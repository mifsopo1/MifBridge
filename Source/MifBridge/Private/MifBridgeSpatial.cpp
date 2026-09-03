// MifBridge — SPATIAL AWARENESS and VISUAL FEEDBACK for level building.
//
// Written after a 70-actor scene was built blind and came out wrong in every avoidable way:
// cliff-sized rocks swallowing buildings, thigh-high grass, palms inside walls, ground planes
// visibly floating. Every one of those is detectable from data — none of them needed a human eye.
//
// The lesson encoded here: NUMBERS FOR CORRECTNESS, PIXELS FOR TASTE.
//   get_actor_bounds / check_overlaps / trace_ground answer "is this wrong?" in milliseconds and
//   are scriptable in a placement loop. capture_camera answers "does this look good?", which is the
//   only question that actually needs rendering.
//
// A note on why bounds are read from the PLACED ACTOR and not the mesh asset: the asset's
// ExtendedBounds ignores the actor's scale. Reading SM_Large_Rock_01's asset bounds says 1312u tall;
// placed at scale 2.4 it is 3150u, and it was that gap that buried a building. AActor::GetActorBounds
// returns the real world-space extent, scale included.
//
// A note on capture_camera and the USER'S viewport: they are, and always were, two different cameras.
// set_viewport_camera (MifBridgeViewport.cpp) moves what the user sees; capture_camera spawns its own
// transient ASceneCapture2D. Nothing was wired between them, so "point the camera, then capture" shot
// from (0,0,500) while both endpoints answered ok:true - docs/06_OPEN_ISSUES_FROM_USE.md #7. There is now
// an OPT-IN useViewportCamera:true, and cameraSource is echoed on EVERY capture so the split is visible
// in the JSON instead of only in the picture.
#include "MifBridgeHandlers.h"
#include "GameFramework/PlayerController.h"   // PC->MyHUD / PC->Player - the draw_debug
#include "GameFramework/HUD.h"                // 'string' shape can only draw through a HUD
#include "MifBridgeLog.h"

#include "Components/LightComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "DrawDebugHelpers.h"
#include "ImageUtils.h"              // PNGCompressImageArray - same path capture_camera/thumbnails use
#include "Misc/FileHelper.h"
#include "UnrealClient.h"            // FViewport::ReadPixels - the real backbuffer
#include "RenderingThread.h"        // FlushRenderingCommands - the forced redraw must land before ReadPixels
#include "NavigationSystem.h"        // nav queries: project a point, find a path, raycast
#include "NavigationPath.h"          // UNavigationPath returned by FindPathToLocationSynchronously
#include "Sound/SoundBase.h"         // audition_sound
#include "HAL/PlatformMemory.h"     // FPlatformMemory::GetStats - process memory
#include "Misc/App.h"               // FApp::GetDeltaTime
#include "RHIStats.h"               // GNumDrawCallsRHI / GNumPrimitivesDrawnRHI      // DrawDebugLine/Sphere/Box/Point/DirectionalArrow/String
#include "Editor.h"
#include "EditorViewportClient.h"   // FEditorViewportClient - GetViewLocation/GetViewRotation/ViewFOV
#include "Engine/Engine.h"
#include "Engine/SceneCapture2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Components/SceneCaptureComponent2D.h"
#include "GameFramework/Actor.h"
#include "Kismet/KismetRenderingLibrary.h"
#include "LevelEditorViewport.h"     // FLevelEditorViewportClient - the useViewportCamera seed
#include "CollisionQueryParams.h"
#include "Misc/Paths.h"
#include "HAL/PlatformFileManager.h"

namespace MifBridge
{
	namespace
	{
		// ActiveWorld() is MifBridge::ActiveWorld() now — same policy (prefer the PIE world when
		// playing so queries match what is actually running, fall back to the editor world for
		// level-building), one definition instead of five near-copies with two different answers.

		// The actor finder moved to MifBridgeCommon.cpp as MifBridge::FindActorInWorld (declared in
		// MifBridgeHandlers.h). FIVE byte-identical copies existed under five different names
		// (FindActor, FindNavActor, FindActorByPathOrLabel, FindVpActor, FindWorldActor) — different
		// names are not a build error, which is exactly why they survived, but it meant a fix to the
		// path/name/label matching rule landed in one of five places. Do NOT add a sixth.

		// Vec3 moved to MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). This file's FVector form
		// and MifBridgeStreaming.cpp's 3-double form were BOTH in unity blob 2 already and compiled
		// only because their arities differed — so they shared one cross-file overload set and any
		// signature change on either side was an instant C2084. Both overloads now exist once.

		// World-space AABB of a placed actor. bOnlyColliding=false so meshes WITHOUT collision still
		// report their visual size — editor-world collision is unreliable for imported props, and a
		// silent zero-extent box would defeat the whole point of overlap checking.
		bool ActorBox(AActor* A, FBox& OutBox)
		{
			if (!A) { return false; }
			FVector Origin, Extent;
			A->GetActorBounds(/*bOnlyCollidingComponents*/ false, Origin, Extent, /*bIncludeFromChildActors*/ true);
			if (Extent.IsNearlyZero()) { return false; }
			OutBox = FBox(Origin - Extent, Origin + Extent);
			return true;
		}

		// The active level viewport, falling back to the first PERSPECTIVE one, then to index 0.
		// DELIBERATE NEAR-COPY of MifBridgeViewport.cpp's ActiveLevelViewport (same policy, same
		// fallback order) under a distinctly prefixed name: a unity build merges every unnamed
		// namespace in a translation unit, so sharing the spelling is a hard C2084 the moment the two
		// files land in one blob — and this file may not edit MifBridgeViewport.cpp or the shared
		// header, which is where the one true copy belongs.
		//
		// EVICTION CLAUSE. This is now the SECOND copy, which is exactly the promotion trigger this
		// codebase already applies (EmitAssetIdentity, CollectPIEWorlds, FindActorInWorld). Promote it
		// to MifBridge::ActiveLevelViewport in MifBridgeHandlers.h / MifBridgeCommon.cpp on the next
		// integrator pass and delete BOTH file-local copies. Until then the two MUST agree: if they
		// ever pick different clients, set_viewport_camera and capture_camera are back to sharing no
		// state, which is the precise defect capture_camera was changed to close. That is why the
		// chosen client's INDEX is echoed in the response — a caller can diff it against
		// get_viewport_camera instead of trusting that the two copies still match.
		FLevelEditorViewportClient* MifSpatialActiveLevelViewport(int32& OutIndex, int32& OutCount)
		{
			OutIndex = INDEX_NONE;
			OutCount = 0;
			if (!GEditor) { return nullptr; }
			const TArray<FLevelEditorViewportClient*>& Clients = GEditor->GetLevelViewportClients();
			OutCount = Clients.Num();
			FLevelEditorViewportClient* FirstPerspective = nullptr;
			int32 FirstPerspectiveIndex = INDEX_NONE;
			for (int32 i = 0; i < Clients.Num(); ++i)
			{
				FLevelEditorViewportClient* Client = Clients[i];
				if (!Client) { continue; }
				if (!FirstPerspective && Client->IsPerspective())
				{
					FirstPerspective = Client;
					FirstPerspectiveIndex = i;
				}
				if (Client->Viewport && Client->Viewport == GEditor->GetActiveViewport())
				{
					OutIndex = i;
					return Client;
				}
			}
			if (FirstPerspective) { OutIndex = FirstPerspectiveIndex; return FirstPerspective; }
			if (Clients.Num() > 0 && Clients[0]) { OutIndex = 0; return Clients[0]; }
			return nullptr;
		}
	}

	// --- get_actor_bounds ---------------------------------------------------
	//   in:  { actorPath }   out: { origin, extent, size, min, max, hasBounds }
	void H_get_actor_bounds(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("path") },
			TEXT("actorPath (aliases: actor, path) — the PLACED actor to measure, given as an object path, object name or label"),
			{ { TEXT("assetPath"), TEXT("bounds are read from the PLACED actor, not the mesh asset — the asset's ExtendedBounds ignore the actor's scale. Pass the actor as actorPath") },
			  { TEXT("label"), TEXT("actorPath already accepts a label, an object name or a full path — use it") },
			  { TEXT("onlyColliding"), TEXT("not a parameter — bounds always include non-colliding components, because editor-world collision is unreliable for imported props") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		AActor* A = FindActorInWorld(World, JStrAny(In, { TEXT("actorPath"), TEXT("actor"), TEXT("path") }));
		if (!A) { Fail(Out, TEXT("actor not found (accepts actorPath, name or label)")); return; }

		FVector Origin, Extent;
		A->GetActorBounds(false, Origin, Extent, true);
		Out->SetStringField(TEXT("actorPath"), A->GetPathName());
		Out->SetObjectField(TEXT("origin"), Vec3(Origin));
		Out->SetObjectField(TEXT("extent"), Vec3(Extent));
		Out->SetObjectField(TEXT("size"), Vec3(Extent * 2.0));   // what you actually compare against
		Out->SetObjectField(TEXT("min"), Vec3(Origin - Extent));
		Out->SetObjectField(TEXT("max"), Vec3(Origin + Extent));
		Out->SetBoolField(TEXT("hasBounds"), !Extent.IsNearlyZero());
	}

	// --- check_overlaps -----------------------------------------------------
	//   in:  { actorPath? , nameContains?, ignoreGround?, tolerance? }
	//   out: { pairs:[{a,b,overlapVolume}], count }
	// With no actorPath this is a WHOLE-SCENE audit — the "what did I get wrong" endpoint.
	// Pure AABB math on cached bounds, no world collision queries, so it works on meshes that have
	// no collision at all (which is most imported props in an editor world).
	void H_check_overlaps(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("nameContains"), TEXT("ignoreGround"), TEXT("tolerance") },
			TEXT("actorPath (alias: actor) to test ONE actor, or omit both for a whole-scene audit; nameContains, ignoreGround, tolerance"),
			{ { TEXT("path"), TEXT("this endpoint takes actorPath (alias: actor) only — 'path' is accepted by get_actor_bounds, not here") },
			  { TEXT("name"), TEXT("use nameContains for a substring filter over object names and labels, or actorPath to test a single actor") },
			  { TEXT("depth"), TEXT("'depth' is an OUTPUT field on each reported pair — the input threshold is 'tolerance' (default 25)") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		const FString Single = JStrAny(In, { TEXT("actorPath"), TEXT("actor") });
		const FString NameFilter = JStr(In, TEXT("nameContains"));
		// Small overlaps are normal and desirable — foliage should touch the ground, props should
		// touch walls. Only report intersections deeper than this on the smallest axis.
		const double Tolerance = JNum(In, TEXT("tolerance"), 25.0);

		struct FEntry { AActor* Actor; FBox Box; };
		TArray<FEntry> Entries;
		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* A = *It;
			if (!A || !IsValid(A)) { continue; }
			if (!NameFilter.IsEmpty() && !A->GetName().Contains(NameFilter) && !A->GetActorLabel().Contains(NameFilter)) { continue; }
			// The ground plane overlaps everything by design; excluding it is the default.
			if (JBool(In, TEXT("ignoreGround"), true) && A->GetActorLabel().Contains(TEXT("Ground"))) { continue; }
			FBox B;
			if (ActorBox(A, B)) { Entries.Add({ A, B }); }
		}

		// A NOT-FOUND actor must not degrade into a whole-scene audit. FindActorInWorld returns null
		// both for "you did not ask for one" and for "the one you asked for is not here", and the
		// filter below is skipped on null - so a mistyped actorPath silently widened the question from
		// "does THIS actor overlap" to "list every overlap in the level", and answered ok:true with
		// 108 pairs that had nothing to do with the request.
		AActor* Target = nullptr;
		if (!Single.IsEmpty())
		{
			Target = FindActorInWorld(World, Single);
			if (!Target)
			{
				Fail(Out, FString::Printf(
					TEXT("actor not found: '%s'. Nothing was tested. Omit actorPath entirely for a "
						 "whole-scene audit - leaving it in with a name that does not resolve would "
						 "otherwise return every overlap in the level as though it were this actor's."),
					*Single));
				return;
			}
		}
		TArray<TSharedPtr<FJsonValue>> Pairs;
		for (int32 i = 0; i < Entries.Num(); ++i)
		{
			if (Target && Entries[i].Actor != Target) { continue; }
			for (int32 j = 0; j < Entries.Num(); ++j)
			{
				if (i == j) { continue; }
				if (!Target && j <= i) { continue; }   // each unordered pair once
				const FBox Inter = Entries[i].Box.Overlap(Entries[j].Box);
				if (!Inter.IsValid) { continue; }
				const FVector S = Inter.GetSize();
				// Depth on the SHALLOWEST axis — two boxes that merely touch have a near-zero axis.
				const double MinAxis = FMath::Min3(S.X, S.Y, S.Z);
				if (MinAxis < Tolerance) { continue; }

				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("a"), Entries[i].Actor->GetActorLabel());
				P->SetStringField(TEXT("b"), Entries[j].Actor->GetActorLabel());
				P->SetStringField(TEXT("aPath"), Entries[i].Actor->GetPathName());
				P->SetStringField(TEXT("bPath"), Entries[j].Actor->GetPathName());
				P->SetNumberField(TEXT("depth"), MinAxis);
				Pairs.Add(MakeShared<FJsonValueObject>(P));
			}
		}

		Out->SetNumberField(TEXT("actorsTested"), Entries.Num());
		Out->SetNumberField(TEXT("count"), Pairs.Num());
		Out->SetArrayField(TEXT("pairs"), Pairs);
	}

	// --- trace_ground -------------------------------------------------------
	//   in:  { x, y, fromZ?, ignoreActor? }   out: { hit, z, actorPath?, normal? }
	// Editor-world collision is NOT guaranteed for imported meshes, so a miss is reported honestly
	// rather than silently returning 0 — a caller that treats "no hit" as "ground at z=0" is exactly
	// how things end up floating.
	namespace
	{
		// Named channels, because ECollisionChannel is an enum a caller cannot guess and getting it
		// wrong produces a confident miss rather than an error.
		bool ResolveTraceChannel(const FString& Name, ECollisionChannel& Out)
		{
			const FString N = Name.ToLower();
			if (N.IsEmpty() || N == TEXT("worldstatic"))  { Out = ECC_WorldStatic;  return true; }
			if (N == TEXT("worlddynamic"))                { Out = ECC_WorldDynamic; return true; }
			if (N == TEXT("visibility"))                  { Out = ECC_Visibility;   return true; }
			if (N == TEXT("camera"))                      { Out = ECC_Camera;       return true; }
			if (N == TEXT("pawn"))                        { Out = ECC_Pawn;         return true; }
			if (N == TEXT("physicsbody"))                 { Out = ECC_PhysicsBody;  return true; }
			return false;
		}

		const TCHAR* kChannelList =
			TEXT("worldStatic, worldDynamic, visibility, camera, pawn, physicsBody");

		FColor ResolveDebugColor(const FString& Name)
		{
			const FString N = Name.ToLower();
			if (N == TEXT("red"))     { return FColor::Red; }
			if (N == TEXT("green"))   { return FColor::Green; }
			if (N == TEXT("blue"))    { return FColor::Blue; }
			if (N == TEXT("yellow"))  { return FColor::Yellow; }
			if (N == TEXT("cyan"))    { return FColor::Cyan; }
			if (N == TEXT("magenta")) { return FColor::Magenta; }
			if (N == TEXT("orange"))  { return FColor::Orange; }
			if (N == TEXT("white"))   { return FColor::White; }
			if (N == TEXT("black"))   { return FColor::Black; }
			return FColor::Green;
		}

		// One hit, described the way a caller can act on: what was hit, where, and how far along.
		TSharedRef<FJsonObject> SerializeHit(const FHitResult& Hit, const FVector& Start)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			if (AActor* A = Hit.GetActor())
			{
				J->SetStringField(TEXT("actorPath"), A->GetPathName());
				J->SetStringField(TEXT("label"), A->GetActorLabel());
				J->SetStringField(TEXT("class"), A->GetClass()->GetName());
			}
			if (Hit.GetComponent())
			{
				J->SetStringField(TEXT("component"), Hit.GetComponent()->GetName());
			}
			J->SetObjectField(TEXT("impactPoint"), Vec3(Hit.ImpactPoint));
			J->SetObjectField(TEXT("normal"), Vec3(Hit.ImpactNormal));
			J->SetNumberField(TEXT("distance"), FVector::Dist(Start, Hit.ImpactPoint));
			if (Hit.BoneName != NAME_None)
			{
				J->SetStringField(TEXT("bone"), Hit.BoneName.ToString());
			}
			J->SetBoolField(TEXT("blockingHit"), Hit.bBlockingHit);
			return J;
		}

		// The world debug shapes and traces actually go to, plus whether PIE is up. A shape drawn into
		// the editor world is invisible during PIE and vice versa, and the call succeeds either way -
		// so the answer has to say which world it used, or "ok:true and nothing visible" is
		// undiagnosable.
		UWorld* SpatialWorld(bool& bOutPie)
		{
			bOutPie = false;
			if (GEditor)
			{
				if (UWorld* Pie = GEditor->PlayWorld)
				{
					bOutPie = true;
					return Pie;
				}
			}
			return EditorWorld();
		}
	}

	// --- capture_viewport ----------------------------------------------------
	//   in:  { path? (aliases: name, file) }
	// This line previously advertised `viewport?`, which the handler REJECTS - a caller following the
	// documentation got "unrecognised parameter 'viewport'" and no capture. There is no viewport
	// selection here: it captures whichever viewport the editor is currently drawing, which is what
	// `viewportType` in the out: block REPORTS rather than something you choose. Same resolution as the
	// duplicate_actors `rotationOffset?` line - the doc is corrected, not the code, because a documented
	// parameter that no code has ever read is a lie in the comment rather than a missing feature.
	//   out: { file, width, height, bytes, realtime, allBlack?, viewportType }
	//
	// The pixels the editor is ACTUALLY drawing, as distinct from capture_camera's transient
	// ASceneCapture2D - a different camera with its own show flags and view mode. That split is
	// documented at the top of this file and has misled someone before.
	void H_capture_viewport(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("name"), TEXT("file") },
			TEXT("path (alias: name, file) - where to write the PNG; defaults to "
				 "Saved/MifBridge/Viewport.png"),
			{ { TEXT("location"), TEXT("this captures the CURRENT viewport - move it first with set_viewport_camera, or use capture_camera to shoot from an arbitrary point without disturbing the user's view") },
			  { TEXT("resolution"), TEXT("the capture is the viewport's own size; resize the editor window to change it") },
			  { TEXT("showUI"), TEXT("not supported - this reads the 3D viewport's backbuffer, which never contains the editor's surrounding UI") } }))
		{
			return;
		}
		if (!GEditor)
		{
			Fail(Out, TEXT("no editor"));
			return;
		}
		FViewport* Viewport = GEditor->GetActiveViewport();
		if (!Viewport)
		{
			Fail(Out, TEXT("no active editor viewport - the editor has no focused level viewport to "
						   "read. Click a viewport, or use capture_camera, which does not need one."));
			return;
		}

		const FIntPoint Size = Viewport->GetSizeXY();
		if (Size.X <= 0 || Size.Y <= 0)
		{
			Fail(Out, FString::Printf(
				TEXT("the active viewport reports a %dx%d size, so there is nothing to read - it is "
					 "probably minimised."), Size.X, Size.Y));
			return;
		}

		// FORCE A REDRAW FIRST. This is not belt-and-braces, it is the whole correctness of the
		// endpoint. A viewport that is not realtime - which is any level viewport once the editor
		// loses focus - does not redraw on its own, so the backbuffer still holds whatever was last
		// drawn. Without this, moving the camera with set_viewport_camera and capturing returned a
		// BYTE-IDENTICAL image while cameraLocation below dutifully reported the NEW position: a
		// frame from one camera, labelled with another. Caught by test_capture_viewport T194.
		Viewport->Invalidate();
		Viewport->Draw();
		FlushRenderingCommands();   // the draw is queued to the render thread; ReadPixels must not race it

		TArray<FColor> Pixels;
		if (!Viewport->ReadPixels(Pixels) || Pixels.Num() == 0)
		{
			Fail(Out, TEXT("reading the viewport's pixels failed - the backbuffer was not available. "
						   "This happens when the editor window is minimised or fully occluded."));
			return;
		}

		// ReadPixels returns the BACKBUFFER: whatever was last drawn. An idle, occluded or minimised
		// editor has not redrawn, so a capture can be stale or blank while every other field says
		// success.
		//
		// The first version of this checked for ALL BLACK. Then the first real capture came back
		// almost entirely WHITE - a blank viewport with nothing but the axis gizmo - and sailed
		// straight past the check. Blank is blank whatever colour it is, so this measures UNIFORMITY
		// instead: what fraction of the frame is the single most common colour.
		// FORCE ALPHA OPAQUE - and this one is not cosmetic. ReadPixels returns the backbuffer's
		// alpha channel, which in the editor is not coverage: it is whatever the renderer left there,
		// and it is ~0 almost everywhere. PNGCompressImageArray writes it out verbatim, so the file
		// was a FULLY TRANSPARENT PNG - 343523 of 343620 pixels at alpha 0 when this was found. Every
		// field said success, the RGB really was a correct render of the scene, and any viewer that
		// honours alpha showed a blank page. It was mistaken for an empty scene twice before the
		// alpha channel was actually looked at.
		int32 Distinct = 0;
		TMap<uint32, int32> Histogram;
		for (FColor& Px : Pixels)
		{
			Px.A = 255;
			const uint32 Key = (uint32(Px.R) << 16) | (uint32(Px.G) << 8) | uint32(Px.B);
			int32& N = Histogram.FindOrAdd(Key);
			++N;
		}
		Distinct = Histogram.Num();
		int32 TopCount = 0;
		uint32 TopColour = 0;
		for (const TPair<uint32, int32>& Pair : Histogram)
		{
			if (Pair.Value > TopCount) { TopCount = Pair.Value; TopColour = Pair.Key; }
		}
		const double Uniformity = Pixels.Num() > 0 ? double(TopCount) / double(Pixels.Num()) : 1.0;

		FString Name = JStrAny(In, { TEXT("path"), TEXT("name"), TEXT("file") });
		if (Name.IsEmpty()) { Name = TEXT("Viewport"); }
		const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge");
		FPlatformFileManager::Get().GetPlatformFile().CreateDirectoryTree(*Dir);
		const FString FullPath = FPaths::ConvertRelativePathToFull(
			Dir / (FPaths::MakeValidFileName(FPaths::GetBaseFilename(Name)) + TEXT(".png")));

		TArray64<uint8> Png;
		FImageUtils::PNGCompressImageArray(Size.X, Size.Y,
			TArrayView64<const FColor>(Pixels.GetData(), Pixels.Num()), Png);
		if (Png.Num() == 0)
		{
			Fail(Out, FString::Printf(TEXT("PNG compression produced no data for %dx%d"), Size.X, Size.Y));
			return;
		}
		if (!FFileHelper::SaveArrayToFile(Png, *FullPath))
		{
			Fail(Out, FString::Printf(TEXT("failed to write %s (check disk space and that the path is "
										   "writable)"), *FullPath));
			return;
		}

		Out->SetStringField(TEXT("file"), FullPath);
		Out->SetNumberField(TEXT("width"), Size.X);
		Out->SetNumberField(TEXT("height"), Size.Y);
		Out->SetNumberField(TEXT("bytes"), Png.Num());
		// Provenance, echoed for the same reason capture_camera echoes cameraSource: so that "which
		// camera is this?" is answerable from the JSON rather than only from the picture.
		Out->SetStringField(TEXT("source"), TEXT("editor viewport backbuffer"));
		// Stated so a caller knows the frame was drawn for THIS call rather than found lying around.
		Out->SetBoolField(TEXT("forcedRedraw"), true);
		if (FEditorViewportClient* Client = static_cast<FEditorViewportClient*>(Viewport->GetClient()))
		{
			Out->SetBoolField(TEXT("realtime"), Client->IsRealtime());
			Out->SetObjectField(TEXT("cameraLocation"), Vec3(Client->GetViewLocation()));
			const FRotator R = Client->GetViewRotation();
			Out->SetObjectField(TEXT("cameraRotation"), Vec3(FVector(R.Pitch, R.Yaw, R.Roll)));
			if (!Client->IsRealtime())
			{
				Out->SetStringField(TEXT("realtimeNote"),
					TEXT("this viewport is NOT realtime, so it does not redraw on its own. This capture "
						 "forced a redraw before reading, so the pixels DO match the camera reported "
						 "here - but anything that animates only while ticking (particles, sequences, "
						 "scrolling materials) will look frozen."));
			}
		}
		// Reported ALWAYS, not only when it looks wrong, so a caller can judge for itself rather than
		// trust a threshold someone picked.
		Out->SetNumberField(TEXT("distinctColours"), Distinct);
		Out->SetNumberField(TEXT("uniformity"), Uniformity);
		Out->SetStringField(TEXT("dominantColour"), FString::Printf(TEXT("#%06X"), TopColour));
		if (Uniformity > 0.98)
		{
			Out->SetBoolField(TEXT("looksBlank"), true);
			Out->SetStringField(TEXT("blankNote"), FString::Printf(
				TEXT("%.1f%% of this frame is the single colour %s, so it is almost certainly BLANK "
					 "rather than a picture of the scene - a minimised or occluded editor never draws, "
					 "and an empty or unlit view looks the same. The file was still written."),
				Uniformity * 100.0, *FString::Printf(TEXT("#%06X"), TopColour)));
		}
	}

	// --- audition_sound ------------------------------------------------------
	//   in:  { path }   out: { playing, sound, duration, class }
	//
	// 3771 SoundWaves and no way to hear one: picking audio for a mod was guesswork by filename.
	// Same path the Content Browser's preview button uses.
	void H_audition_sound(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("sound"), TEXT("assetPath"), TEXT("stop") },
			TEXT("path (aliases: sound, assetPath) of any USoundBase - SoundWave, SoundCue or "
				 "MetaSoundSource; or stop:true to silence the current preview"),
			{ { TEXT("volume"), TEXT("the editor preview plays at the asset's own volume - set the asset's Volume property to change it") },
			  { TEXT("location"), TEXT("this is a 2D editor PREVIEW, not a world sound; for a positioned sound use add_function_call with PlaySoundAtLocation") } }))
		{
			return;
		}
		if (!GEditor)
		{
			Fail(Out, TEXT("no editor"));
			return;
		}

		if (JBool(In, TEXT("stop"), false))
		{
			GEditor->ResetPreviewAudioComponent();
			Out->SetBoolField(TEXT("playing"), false);
			Out->SetBoolField(TEXT("stopped"), true);
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("sound"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (any USoundBase), or pass stop:true"));
			return;
		}
		UObject* Asset = LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s"), *Path));
			return;
		}
		USoundBase* Sound = Cast<USoundBase>(Asset);
		if (!Sound)
		{
			// SoundWave, SoundCue and MetaSoundSource are all USoundBase; anything else is a mistake
			// worth naming rather than silently doing nothing.
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, not a USoundBase - pass a SoundWave, SoundCue or MetaSoundSource."),
				*Path, *Asset->GetClass()->GetName()));
			return;
		}

		UAudioComponent* Comp = GEditor->PlayPreviewSound(Sound);
		// PlayPreviewSound returns the component it used. A null here means the editor has no audio
		// device (a -nosound session, or no device at all), which is silence that would otherwise be
		// indistinguishable from a quiet asset.
		if (!Comp)
		{
			Fail(Out, TEXT("the editor produced no preview audio component - this session probably has "
						   "no audio device (-nosound). The asset is fine; nothing can be heard."));
			return;
		}

		Out->SetBoolField(TEXT("playing"), true);
		Out->SetStringField(TEXT("sound"), Sound->GetPathName());
		Out->SetStringField(TEXT("class"), Sound->GetClass()->GetName());
		Out->SetNumberField(TEXT("duration"), Sound->GetDuration());
		Out->SetStringField(TEXT("note"),
			TEXT("playing through the EDITOR's preview device - audible at the machine running the "
				 "editor, and stopped with stop:true"));
	}

	// --- nav_project_point ---------------------------------------------------
	//   in:  { point:{x,y,z}, extent? }   out: { onNavMesh, projected:{...}, movedBy }
	void H_nav_project_point(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("point"), TEXT("extent") },
			TEXT("point:{x,y,z}, extent:{x,y,z} (search box, default 100/100/200)"),
			{ { TEXT("actor"), TEXT("pass a point - read an actor's location with get_level_actor first") } }))
		{
			return;
		}
		bool bPie = false;
		UWorld* World = SpatialWorld(bPie);
		if (!World) { Fail(Out, TEXT("no world")); return; }
		UNavigationSystemV1* Nav = FNavigationSystem::GetCurrent<UNavigationSystemV1>(World);
		if (!Nav)
		{
			// "No navmesh" and "off the mesh" are different answers and must not look alike.
			Fail(Out, TEXT("this world has no navigation system - there is no nav mesh to project onto. "
						   "Add a nav volume with add_nav_volume and build it with build_navmesh."));
			return;
		}

		FString VErr;
		FVector Point = FVector::ZeroVector;
		const EJsonRead R = ReadVectorField(In, TEXT("point"), Point, VErr);
		if (R == EJsonRead::Invalid) { Fail(Out, VErr); return; }
		if (R == EJsonRead::Absent) { Fail(Out, TEXT("point:{x,y,z} is required")); return; }
		FVector Extent(100.0, 100.0, 200.0);
		if (ReadVectorField(In, TEXT("extent"), Extent, VErr) == EJsonRead::Invalid)
		{
			Fail(Out, VErr);
			return;
		}

		FNavLocation Projected;
		const bool bOn = Nav->ProjectPointToNavigation(Point, Projected, Extent);
		Out->SetBoolField(TEXT("onNavMesh"), bOn);
		Out->SetObjectField(TEXT("queried"), Vec3(Point));
		if (bOn)
		{
			Out->SetObjectField(TEXT("projected"), Vec3(Projected.Location));
			// How far it moved is the useful number: a placement 2cm off the mesh and one 300cm off
			// are different problems, and "onNavMesh: true" hides that.
			Out->SetNumberField(TEXT("movedBy"), FVector::Dist(Point, Projected.Location));
		}
		else
		{
			Out->SetStringField(TEXT("note"),
				TEXT("no navigable point within the search extent - either nothing walkable is near, "
					 "or the nav mesh has not been built here (build_navmesh reports tile counts)"));
		}
		Out->SetBoolField(TEXT("pieRunning"), bPie);
	}

	// --- nav_find_path -------------------------------------------------------
	//   in:  { start:{x,y,z}, end:{x,y,z} }
	//   out: { reachable, partial, pathLength, pointCount, points:[...] }
	void H_nav_find_path(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("start"), TEXT("end"), TEXT("draw"), TEXT("drawDuration") },
			TEXT("start:{x,y,z}, end:{x,y,z}, draw (leave the path in the viewport), drawDuration"),
			{ { TEXT("actor"), TEXT("pass coordinates - read an actor's location with get_level_actor") } }))
		{
			return;
		}
		bool bPie = false;
		UWorld* World = SpatialWorld(bPie);
		if (!World) { Fail(Out, TEXT("no world")); return; }
		UNavigationSystemV1* Nav = FNavigationSystem::GetCurrent<UNavigationSystemV1>(World);
		if (!Nav)
		{
			Fail(Out, TEXT("this world has no navigation system - there is nothing to path through. "
						   "Add a nav volume with add_nav_volume and build it with build_navmesh."));
			return;
		}

		FString VErr;
		FVector Start = FVector::ZeroVector, End = FVector::ZeroVector;
		if (ReadVectorField(In, TEXT("start"), Start, VErr) != EJsonRead::Read)
		{
			Fail(Out, VErr.IsEmpty() ? TEXT("start:{x,y,z} is required") : *VErr);
			return;
		}
		if (ReadVectorField(In, TEXT("end"), End, VErr) != EJsonRead::Read)
		{
			Fail(Out, VErr.IsEmpty() ? TEXT("end:{x,y,z} is required") : *VErr);
			return;
		}

		UNavigationPath* Path = UNavigationSystemV1::FindPathToLocationSynchronously(World, Start, End);
		if (!Path)
		{
			Fail(Out, TEXT("the navigation system returned no path object at all - that is a query "
						   "failure rather than an unreachable destination."));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Pts;
		for (const FVector& P : Path->PathPoints)
		{
			Pts.Add(MakeShared<FJsonValueObject>(Vec3(P)));
		}
		// PARTIAL IS NOT REACHABLE. A partial path stops at the closest reachable point and still
		// looks like a path - reporting it as success is how "the NPC can get there" becomes a lie.
		const bool bPartial = Path->IsPartial();
		Out->SetBoolField(TEXT("reachable"), Path->IsValid() && !bPartial);
		Out->SetBoolField(TEXT("partial"), bPartial);
		Out->SetBoolField(TEXT("valid"), Path->IsValid());
		Out->SetNumberField(TEXT("pathLength"), Path->GetPathLength());
		Out->SetNumberField(TEXT("pointCount"), Pts.Num());
		Out->SetArrayField(TEXT("points"), Pts);
		if (bPartial)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("PARTIAL path: it stops at the closest reachable point, so the destination is NOT "
					 "reachable even though a path came back"));
		}
		if (JBool(In, TEXT("draw"), false))
		{
			const float Dur = (float)JNum(In, TEXT("drawDuration"), 8.0);
			for (int32 i = 1; i < Path->PathPoints.Num(); ++i)
			{
				DrawDebugLine(World, Path->PathPoints[i - 1], Path->PathPoints[i],
					bPartial ? FColor::Orange : FColor::Green, false, Dur, 0, 4.0f);
			}
		}
		Out->SetBoolField(TEXT("pieRunning"), bPie);
	}

	// --- get_perf_stats ------------------------------------------------------
	//   in:  { }
	//   out: { editorTiming:{...}, rhi:{...}, memory:{...}, scene:{...}, caveat }
	//
	// "Is this mod expensive?" had no answer from the bridge except for landscape. This gives one,
	// while being explicit about which half of it means anything.
	void H_get_perf_stats(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { },
			TEXT("(no parameters)"),
			{ { TEXT("world"), TEXT("this always measures the world the editor is currently showing; start_pie first if you want PIE numbers, and check pieRunning in the response") },
			  { TEXT("reset"), TEXT("the RHI counters are the engine's own and are not resettable from here - compare two calls instead") } }))
		{
			return;
		}

		bool bPie = false;
		UWorld* World = SpatialWorld(bPie);
		if (!World) { Fail(Out, TEXT("no world")); return; }

		// --- timing. Reported, but fenced with a caveat, because these are EDITOR frames.
		TSharedRef<FJsonObject> Timing = MakeShared<FJsonObject>();
		const double Delta = FApp::GetDeltaTime();
		Timing->SetNumberField(TEXT("lastFrameMs"), Delta * 1000.0);
		Timing->SetNumberField(TEXT("impliedFps"), Delta > 0.0 ? 1.0 / Delta : 0.0);
		Out->SetObjectField(TEXT("editorTiming"), Timing);

		// --- RHI counters. Per-GPU arrays; index 0 is the one that matters on a single-GPU box.
		TSharedRef<FJsonObject> Rhi = MakeShared<FJsonObject>();
		Rhi->SetNumberField(TEXT("drawCalls"), GNumDrawCallsRHI[0]);
		Rhi->SetNumberField(TEXT("primitivesDrawn"), GNumPrimitivesDrawnRHI[0]);
		Out->SetObjectField(TEXT("rhi"), Rhi);

		// --- process memory.
		const FPlatformMemoryStats Mem = FPlatformMemory::GetStats();
		TSharedRef<FJsonObject> MemJ = MakeShared<FJsonObject>();
		MemJ->SetNumberField(TEXT("usedPhysicalMB"), (double)Mem.UsedPhysical / (1024.0 * 1024.0));
		MemJ->SetNumberField(TEXT("peakUsedPhysicalMB"), (double)Mem.PeakUsedPhysical / (1024.0 * 1024.0));
		MemJ->SetNumberField(TEXT("availablePhysicalMB"), (double)Mem.AvailablePhysical / (1024.0 * 1024.0));
		Out->SetObjectField(TEXT("memory"), MemJ);

		// --- THE HONEST HALF. A census of what is in the level is a property of the CONTENT, not of
		// who is looking at it or what the editor happens to be drawing this frame. These are the
		// numbers that actually decide whether a mod is expensive, and they are reproducible.
		int32 Actors = 0, StaticMeshes = 0, SkeletalMeshes = 0, Lights = 0, Primitives = 0;
		int32 ShadowCastingLights = 0, TranslucentOrMasked = 0;
		int64 TriangleEstimate = 0;
		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* A = *It;
			if (!A || !IsValid(A)) { continue; }
			++Actors;
			TArray<UActorComponent*> Comps;
			A->GetComponents(Comps);
			for (UActorComponent* Comp : Comps)
			{
				if (UStaticMeshComponent* SMC = Cast<UStaticMeshComponent>(Comp))
				{
					++StaticMeshes;
					if (UStaticMesh* Mesh = SMC->GetStaticMesh())
					{
						if (Mesh->GetRenderData() && Mesh->GetRenderData()->LODResources.Num() > 0)
						{
							TriangleEstimate += Mesh->GetRenderData()->LODResources[0].GetNumTriangles();
						}
					}
				}
				else if (Cast<USkeletalMeshComponent>(Comp)) { ++SkeletalMeshes; }
				if (ULightComponent* LC = Cast<ULightComponent>(Comp))
				{
					++Lights;
					if (LC->CastShadows) { ++ShadowCastingLights; }
				}
				if (UPrimitiveComponent* PC = Cast<UPrimitiveComponent>(Comp))
				{
					++Primitives;
					for (int32 i = 0; i < PC->GetNumMaterials(); ++i)
					{
						if (UMaterialInterface* MI = PC->GetMaterial(i))
						{
							const EBlendMode Blend = MI->GetBlendMode();
							if (Blend != BLEND_Opaque) { ++TranslucentOrMasked; }
						}
					}
				}
			}
		}
		TSharedRef<FJsonObject> Scene = MakeShared<FJsonObject>();
		Scene->SetNumberField(TEXT("actors"), Actors);
		Scene->SetNumberField(TEXT("primitiveComponents"), Primitives);
		Scene->SetNumberField(TEXT("staticMeshComponents"), StaticMeshes);
		Scene->SetNumberField(TEXT("skeletalMeshComponents"), SkeletalMeshes);
		Scene->SetNumberField(TEXT("lights"), Lights);
		Scene->SetNumberField(TEXT("shadowCastingLights"), ShadowCastingLights);
		Scene->SetNumberField(TEXT("nonOpaqueMaterialSlots"), TranslucentOrMasked);
		Scene->SetNumberField(TEXT("lod0TriangleEstimate"), (double)TriangleEstimate);
		Out->SetObjectField(TEXT("scene"), Scene);

		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetBoolField(TEXT("pieRunning"), bPie);
		// Say plainly which numbers are worth anything. Reporting editor frame times as if they were
		// the game's would be worse than reporting nothing.
		Out->SetStringField(TEXT("caveat"),
			TEXT("editorTiming and rhi describe THE EDITOR rendering its own viewport - UI, gizmos and "
				 "selection outlines included - and they are not the game's performance. Treat them as "
				 "a relative signal between two calls, never as an absolute. The 'scene' census is the "
				 "reliable half: it is a property of the content and is reproducible."));
		Out->SetStringField(TEXT("sceneNote"),
			TEXT("lod0TriangleEstimate sums LOD0 of every static mesh COMPONENT, so an instanced or "
				 "repeated mesh is counted once per placement, which is what draw cost cares about. It "
				 "ignores skeletal meshes, foliage instances and Nanite fallbacks."));
	}

	// --- trace ---------------------------------------------------------------
	//   in:  { start:{x,y,z}, end:{x,y,z} | direction:{x,y,z} + distance,
	//          shape? (line|sphere|box|capsule), radius?, halfExtent?, halfHeight?,
	//          channel?, traceComplex?, multi?, ignoreActors?:[...], draw?, drawDuration? }
	//   out: { hit, hitCount, hits:[...], traced:{start,end}, channel, world, pieRunning }
	//
	// trace_ground fires straight down and takes the first GROUND hit, which answers exactly one
	// question. This answers the rest: what is between these two points, on which channel, ignoring
	// what, and optionally leaving the ray visible in the viewport.
	void H_trace(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("start"), TEXT("end"), TEXT("direction"), TEXT("distance"), TEXT("shape"),
			  TEXT("radius"), TEXT("halfExtent"), TEXT("halfHeight"), TEXT("channel"),
			  TEXT("traceComplex"), TEXT("multi"), TEXT("ignoreActors"), TEXT("draw"),
			  TEXT("drawDuration") },
			TEXT("start:{x,y,z} plus either end:{x,y,z} or direction:{x,y,z} + distance; "
				 "shape (line|sphere|box|capsule, default line), radius (sphere/capsule), "
				 "halfExtent:{x,y,z} (box), halfHeight (capsule), channel (default worldStatic), "
				 "traceComplex (default true), multi (default false), ignoreActors:[names or paths], "
				 "draw (bool - leave the ray in the viewport), drawDuration (seconds, default 5)"),
			{ { TEXT("from"), TEXT("the parameter is 'start' (trace_ground uses fromZ/toZ because it is Z-only; this one takes full vectors)") },
			  { TEXT("to"), TEXT("the parameter is 'end'") },
			  { TEXT("ignoreActor"), TEXT("this one takes ignoreActors:[...] - a list, since a general trace usually needs to exclude several") } }))
		{
			return;
		}

		bool bPie = false;
		UWorld* World = SpatialWorld(bPie);
		if (!World) { Fail(Out, TEXT("no world")); return; }

		// EJsonRead, not a bool: Absent and Invalid are different answers. A supplied-but-malformed
		// vector must be REPORTED, not defaulted - defaulting it would fire the ray from somewhere
		// the caller never asked for and report a confident hit.
		FString VErr;
		FVector Start = FVector::ZeroVector;
		const EJsonRead StartRead = ReadVectorField(In, TEXT("start"), Start, VErr);
		if (StartRead == EJsonRead::Invalid) { Fail(Out, VErr); return; }
		if (StartRead == EJsonRead::Absent)
		{
			Fail(Out, TEXT("start:{x,y,z} is required. NOTHING was traced."));
			return;
		}

		FVector End = FVector::ZeroVector;
		const EJsonRead EndRead = ReadVectorField(In, TEXT("end"), End, VErr);
		if (EndRead == EJsonRead::Invalid) { Fail(Out, VErr); return; }
		if (EndRead == EJsonRead::Absent)
		{
			FVector Dir = FVector::ZeroVector;
			const EJsonRead DirRead = ReadVectorField(In, TEXT("direction"), Dir, VErr);
			if (DirRead == EJsonRead::Invalid) { Fail(Out, VErr); return; }
			if (DirRead == EJsonRead::Absent)
			{
				Fail(Out, TEXT("give either end:{x,y,z} or direction:{x,y,z} + distance. "
							   "NOTHING was traced."));
				return;
			}
			if (Dir.IsNearlyZero())
			{
				Fail(Out, TEXT("direction is zero-length, so there is no ray to fire. NOTHING was traced."));
				return;
			}
			const double Distance = JNum(In, TEXT("distance"), 10000.0);
			End = Start + Dir.GetSafeNormal() * Distance;
		}

		ECollisionChannel Channel = ECC_WorldStatic;
		const FString ChannelName = JStr(In, TEXT("channel"));
		if (!ResolveTraceChannel(ChannelName, Channel))
		{
			Fail(Out, FString::Printf(
				TEXT("unknown channel '%s' - use one of: %s. NOTHING was traced."),
				*ChannelName, kChannelList));
			return;
		}

		FCollisionQueryParams Params(SCENE_QUERY_STAT(MifBridgeTrace),
			JBool(In, TEXT("traceComplex"), true));

		// An ignore that does not resolve is REFUSED, not skipped. trace_ground shipped with the
		// skip-silently version and returned confident hits against the very actors a caller had
		// excluded; there is no reason to repeat that here.
		const TArray<TSharedPtr<FJsonValue>>* Ignores = nullptr;
		if (JArray(In, TEXT("ignoreActors"), Ignores) && Ignores)
		{
			for (int32 i = 0; i < Ignores->Num(); ++i)
			{
				FString Name;
				if (!(*Ignores)[i].IsValid() || !(*Ignores)[i]->TryGetString(Name) || Name.IsEmpty())
				{
					Fail(Out, FString::Printf(
						TEXT("ignoreActors[%d] is not a non-empty string. NOTHING was traced."), i));
					return;
				}
				AActor* A = FindActorInWorld(World, Name);
				if (!A)
				{
					Fail(Out, FString::Printf(
						TEXT("ignoreActors[%d] '%s' does not resolve to an actor in this world, so the "
							 "trace would have run WITHOUT ignoring it and could have hit the very "
							 "thing you excluded. NOTHING was traced."), i, *Name));
					return;
				}
				Params.AddIgnoredActor(A);
			}
		}

		const FString Shape = JStr(In, TEXT("shape"), TEXT("line")).ToLower();
		const bool bMulti = JBool(In, TEXT("multi"), false);
		FCollisionShape Sweep;
		bool bIsSweep = true;
		if (Shape == TEXT("line"))
		{
			bIsSweep = false;
		}
		else if (Shape == TEXT("sphere"))
		{
			Sweep = FCollisionShape::MakeSphere((float)JNum(In, TEXT("radius"), 50.0));
		}
		else if (Shape == TEXT("capsule"))
		{
			Sweep = FCollisionShape::MakeCapsule((float)JNum(In, TEXT("radius"), 50.0),
												 (float)JNum(In, TEXT("halfHeight"), 100.0));
		}
		else if (Shape == TEXT("box"))
		{
			FVector Half(50.0, 50.0, 50.0);
			if (ReadVectorField(In, TEXT("halfExtent"), Half, VErr) == EJsonRead::Invalid)
			{
				Fail(Out, VErr);
				return;
			}
			Sweep = FCollisionShape::MakeBox(FVector3f(Half));
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("unknown shape '%s' - use line, sphere, box or capsule. NOTHING was traced."),
				*Shape));
			return;
		}

		// REFUSE A PARAMETER THIS SHAPE WOULD IGNORE, while nothing has been traced yet.
		//
		// MODE-PARAMS-OK: radius/halfExtent/halfHeight are refused from the table below
		//
		// audit_mode_params reads refusal message literals and this refusal builds the name at
		// runtime, so the marker above is how it knows this was dealt with - same as
		// create_procedural_mesh.
		//
		// THE DEFAULT IS WHAT MAKES THIS BITE. `shape` defaults to LINE, so
		// {"start":..., "end":..., "radius":100} - no shape at all - fires a line trace and drops the
		// radius. The caller asked to sweep a 100-unit sphere and got a ray, which finds far fewer
		// hits, and the response said ok:true with shape:"line". That is the invoke_editor_tab shape
		// exactly: forget the mode, get the default mode, lose the parameter in silence. It is worse
		// here than in create_procedural_mesh, because the answer is query results a caller ACTS on
		// rather than an asset they can look at.
		//
		// drawDuration is on a different axis - it is read only inside `if (draw)` - so it is checked
		// separately below rather than bent into a shape table.
		{
			struct FShapeParam { const TCHAR* Name; const TCHAR* Shapes; };
			static const FShapeParam kShapeOnly[] = {
				{ TEXT("radius"),     TEXT("sphere, capsule") },
				{ TEXT("halfExtent"), TEXT("box") },
				{ TEXT("halfHeight"), TEXT("capsule") },
			};
			for (const FShapeParam& P : kShapeOnly)
			{
				if (!In->HasField(P.Name) || FString(P.Shapes).Contains(Shape))
				{
					continue;
				}
				Fail(Out, FString::Printf(
					TEXT("%s is only read by shape %s; shape '%s' would have ignored it and traced ")
					TEXT("a %s anyway under ok:true. Set shape, or drop %s. NOTHING was traced."),
					P.Name, P.Shapes, *Shape, *Shape, P.Name));
				return;
			}
			// Same defect, different mode parameter: drawDuration is read only when draw is true, so
			// passing it alone asks for a duration on a line nobody will see.
			if (In->HasField(TEXT("drawDuration")) && !JBool(In, TEXT("draw"), false))
			{
				Fail(Out, TEXT("drawDuration is only read when draw is true; without it nothing is "
							   "drawn and the duration would have been ignored. Pass draw:true, or "
							   "drop drawDuration. NOTHING was traced."));
				return;
			}
		}

		TArray<FHitResult> Hits;
		bool bAnyHit = false;
		if (!bIsSweep)
		{
			if (bMulti) { bAnyHit = World->LineTraceMultiByChannel(Hits, Start, End, Channel, Params); }
			else
			{
				FHitResult One;
				bAnyHit = World->LineTraceSingleByChannel(One, Start, End, Channel, Params);
				if (bAnyHit) { Hits.Add(One); }
			}
		}
		else
		{
			if (bMulti)
			{
				bAnyHit = World->SweepMultiByChannel(Hits, Start, End, FQuat::Identity, Channel, Sweep, Params);
			}
			else
			{
				FHitResult One;
				bAnyHit = World->SweepSingleByChannel(One, Start, End, FQuat::Identity, Channel, Sweep, Params);
				if (bAnyHit) { Hits.Add(One); }
			}
		}

		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const FHitResult& H : Hits)
		{
			Arr.Add(MakeShared<FJsonValueObject>(SerializeHit(H, Start)));
		}

		if (JBool(In, TEXT("draw"), false))
		{
			const float Dur = (float)JNum(In, TEXT("drawDuration"), 5.0);
			DrawDebugLine(World, Start, End, bAnyHit ? FColor::Red : FColor::Green,
						  /*bPersistent*/ false, Dur, 0, 2.0f);
			for (const FHitResult& H : Hits)
			{
				DrawDebugPoint(World, H.ImpactPoint, 12.0f, FColor::Yellow, false, Dur);
			}
		}

		Out->SetBoolField(TEXT("hit"), bAnyHit);
		Out->SetNumberField(TEXT("hitCount"), Arr.Num());
		Out->SetArrayField(TEXT("hits"), Arr);
		TSharedRef<FJsonObject> Traced = MakeShared<FJsonObject>();
		Traced->SetObjectField(TEXT("start"), Vec3(Start));
		Traced->SetObjectField(TEXT("end"), Vec3(End));
		Out->SetObjectField(TEXT("traced"), Traced);
		Out->SetStringField(TEXT("shape"), Shape);
		Out->SetStringField(TEXT("channel"), ChannelName.IsEmpty() ? TEXT("worldStatic") : *ChannelName);
		// Which world answered. A trace against the editor world while PIE is running is a different
		// question from the one the caller probably meant, and this is the only way to notice.
		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetBoolField(TEXT("pieRunning"), bPie);
	}

	// --- draw_debug ----------------------------------------------------------
	//   in:  { shape (line|sphere|box|point|arrow|string), start|center, end?, radius?,
	//          extent?, text?, color?, duration?, thickness? }
	//   out: { drawn, shape, world, pieRunning, duration }
	//
	// capture_camera answers "does this look right" with pixels. This answers "here is what I
	// measured" - the trace I fired, the bounds I compared, the point I chose - drawn where a human
	// can see it next to the geometry it refers to.
	void H_draw_debug(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("shape"), TEXT("start"), TEXT("end"), TEXT("center"), TEXT("radius"),
			  TEXT("extent"), TEXT("text"), TEXT("color"), TEXT("duration"), TEXT("thickness") },
			TEXT("shape (line|sphere|box|point|arrow|string), start:{x,y,z} and end:{x,y,z} for "
				 "line/arrow, center:{x,y,z} for sphere/box/point/string, radius (sphere), "
				 "extent:{x,y,z} (box), text (string), color (red|green|blue|yellow|cyan|magenta|"
				 "orange|white|black, default green), duration (seconds, default 5), thickness"),
			{ { TEXT("position"), TEXT("use 'center' for sphere/box/point/string, or 'start' + 'end' for line/arrow") },
			  { TEXT("size"), TEXT("use 'radius' for a sphere or 'extent':{x,y,z} for a box") },
			  { TEXT("persistent"), TEXT("not supported on purpose - a persistent debug shape survives until the level reloads and there is no endpoint to clear it. Use a long duration instead.") } }))
		{
			return;
		}

		bool bPie = false;
		UWorld* World = SpatialWorld(bPie);
		if (!World) { Fail(Out, TEXT("no world")); return; }

		const FString Shape = JStr(In, TEXT("shape"), TEXT("point")).ToLower();
		const FColor Color = ResolveDebugColor(JStr(In, TEXT("color")));
		const float Duration = (float)JNum(In, TEXT("duration"), 5.0);
		const float Thickness = (float)JNum(In, TEXT("thickness"), 2.0);

		if (Duration <= 0.0f)
		{
			Fail(Out, TEXT("duration must be greater than zero - a shape drawn for zero seconds is "
						   "invisible, which would look exactly like a bug. NOTHING was drawn."));
			return;
		}

		FString VErr;
		FVector Start = FVector::ZeroVector, End = FVector::ZeroVector, Center = FVector::ZeroVector;
		const EJsonRead StartRead = ReadVectorField(In, TEXT("start"), Start, VErr);
		if (StartRead == EJsonRead::Invalid) { Fail(Out, VErr); return; }
		const EJsonRead EndRead = ReadVectorField(In, TEXT("end"), End, VErr);
		if (EndRead == EJsonRead::Invalid) { Fail(Out, VErr); return; }
		const EJsonRead CenterRead = ReadVectorField(In, TEXT("center"), Center, VErr);
		if (CenterRead == EJsonRead::Invalid) { Fail(Out, VErr); return; }
		const bool bHasStart = (StartRead == EJsonRead::Read);
		const bool bHasEnd = (EndRead == EJsonRead::Read);
		const bool bHasCenter = (CenterRead == EJsonRead::Read);

		if (Shape == TEXT("line") || Shape == TEXT("arrow"))
		{
			if (!bHasStart || !bHasEnd)
			{
				Fail(Out, FString::Printf(
					TEXT("shape '%s' needs both start:{x,y,z} and end:{x,y,z}. NOTHING was drawn."),
					*Shape));
				return;
			}
			if (Shape == TEXT("line"))
			{
				DrawDebugLine(World, Start, End, Color, false, Duration, 0, Thickness);
			}
			else
			{
				DrawDebugDirectionalArrow(World, Start, End, 120.0f, Color, false, Duration, 0, Thickness);
			}
		}
		else
		{
			if (!bHasCenter)
			{
				Fail(Out, FString::Printf(
					TEXT("shape '%s' needs center:{x,y,z}. NOTHING was drawn."), *Shape));
				return;
			}
			if (Shape == TEXT("sphere"))
			{
				DrawDebugSphere(World, Center, (float)JNum(In, TEXT("radius"), 100.0), 16, Color,
								false, Duration, 0, Thickness);
			}
			else if (Shape == TEXT("box"))
			{
				FVector Extent(100.0, 100.0, 100.0);
				if (ReadVectorField(In, TEXT("extent"), Extent, VErr) == EJsonRead::Invalid)
				{
					Fail(Out, VErr);
					return;
				}
				DrawDebugBox(World, Center, Extent, Color, false, Duration, 0, Thickness);
			}
			else if (Shape == TEXT("point"))
			{
				DrawDebugPoint(World, Center, FMath::Max(1.0f, Thickness * 6.0f), Color, false, Duration);
			}
			else if (Shape == TEXT("string"))
			{
				const FString Text = JStr(In, TEXT("text"));
				if (Text.IsEmpty())
				{
					Fail(Out, TEXT("shape 'string' needs text. NOTHING was drawn."));
					return;
				}
				// 'string' IS THE ONE SHAPE THAT CANNOT DRAW IN AN EDITOR WORLD. Every other shape here
				// goes through the world's line batcher and renders in the editor viewport. DrawDebugString
				// does not: it walks GetPlayerControllerIterator and only draws where a controller has
				// BOTH MyHUD and Player (DrawDebugHelpers.cpp:613-630). An editor world has no such
				// controller, so the loop body never runs, the void function reports nothing, and this
				// handler used to answer drawn:true having drawn absolutely nothing.
				//
				// Checked rather than assumed, because during PIE the same call works perfectly well.
				bool bHasHudTarget = false;
				for (FConstPlayerControllerIterator It = World->GetPlayerControllerIterator(); It; ++It)
				{
					APlayerController* PC = It->Get();
					if (PC && PC->MyHUD && PC->Player)
					{
						bHasHudTarget = true;
						break;
					}
				}
				if (!bHasHudTarget)
				{
					Fail(Out, TEXT("shape 'string' draws through a player controller's HUD, and this world has "
						"no controller with one - which is the normal state of an EDITOR world, so the text "
						"would not appear anywhere. Nothing was drawn. Start PIE if you want on-screen text, "
						"or use line/sphere/box/point/arrow, which render in the editor viewport."));
					return;
				}
				DrawDebugString(World, Center, Text, nullptr, Color, Duration);
			}
			else
			{
				Fail(Out, FString::Printf(
					TEXT("unknown shape '%s' - use line, sphere, box, point, arrow or string. "
						 "NOTHING was drawn."), *Shape));
				return;
			}
		}

		Out->SetBoolField(TEXT("drawn"), true);
		Out->SetStringField(TEXT("shape"), Shape);
		Out->SetNumberField(TEXT("duration"), Duration);
		// THE FIELD THAT MAKES AN INVISIBLE DRAW DIAGNOSABLE. Debug shapes drawn into the editor
		// world do not appear during PIE and vice versa, and the call succeeds either way. Without
		// this, "ok:true and nothing on screen" has no explanation.
		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetBoolField(TEXT("pieRunning"), bPie);
		if (bPie)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("drawn into the PIE world because PIE is running - it will not be visible in the "
					 "editor viewport, and it disappears when PIE stops"));
		}
	}

	void H_trace_ground(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("x"), TEXT("y"), TEXT("fromZ"), TEXT("toZ"), TEXT("location"),
			  TEXT("ignoreActor"), TEXT("actorPath") },
			TEXT("x, y (or location:{x,y,z}, whose z seeds fromZ), fromZ, toZ, ignoreActor (alias: actorPath)"),
			{ { TEXT("z"), TEXT("there is no top-level z — the trace START is 'fromZ' (default 100000) and the END is 'toZ' (default -100000). location:{x,y,z} also seeds fromZ from its z") },
			  { TEXT("ignore"), TEXT("the key is 'ignoreActor' (alias: actorPath); it accepts an object path, object name or label") },
			  { TEXT("channel"), TEXT("not a parameter — this always traces ECC_WorldStatic with complex collision") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		// Accept BOTH {x,y} and {location:{x,y,z}}. Every other endpoint in this bridge takes a
		// location object, so callers naturally send one here too — and when this handler read only
		// the top-level keys it silently traced at the world ORIGIN for every request, returning a
		// confident hit for a point nowhere near the one asked about. Same silent-ignore class as the
		// naming traps: honour the spelling the caller reasonably used.
		double X = JNum(In, TEXT("x"));
		double Y = JNum(In, TEXT("y"));
		double FromZ = JNum(In, TEXT("fromZ"), 100000.0);
		const double ToZ = JNum(In, TEXT("toZ"), -100000.0);

		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), LocObj) && LocObj)
		{
			const TSharedRef<FJsonObject> L = LocObj->ToSharedRef();
			X = JNum(L, TEXT("x"));
			Y = JNum(L, TEXT("y"));
			// A location's Z is where to trace FROM, unless fromZ was given explicitly.
			if (!JHasAny(In, { TEXT("fromZ") }) && L->HasField(TEXT("z")))
			{
				FromZ = JNum(L, TEXT("z"));
			}
		}

		FCollisionQueryParams Params(SCENE_QUERY_STAT(MifBridgeTraceGround), /*bTraceComplex*/ true);

		// AN ignoreActor THAT DOES NOT RESOLVE IS A REFUSAL, NOT A SHRUG.
		//
		// This used to be `if (AActor* Ignore = FindActorInWorld(...)) { AddIgnoredActor(Ignore); }`.
		// When the name resolved to nothing the `if` simply did not fire, the trace ran WITHOUT
		// ignoring anything, and the caller got a confident hit:true - quite possibly against the very
		// actor they asked to exclude, which is the one answer they had ruled out.
		//
		// Same silent-ignore class as invoke_editor_tab's mode-dependent 'asset', and found the same
		// way: the endpoint sweep's ghost probe handed it a path that does not exist and it answered
		// ok:true. Note the XY case a few lines above already had this reasoning applied to it; the
		// ignore path did not.
		const FString IgnoreName = JStrAny(In, { TEXT("ignoreActor"), TEXT("actorPath") });
		if (!IgnoreName.IsEmpty())
		{
			AActor* Ignore = FindActorInWorld(World, IgnoreName);
			if (!Ignore)
			{
				Fail(Out, FString::Printf(
					TEXT("ignoreActor '%s' does not resolve to an actor in this world, so the trace "
						 "would have run WITHOUT ignoring it and could have hit the very actor you "
						 "asked to exclude. NOTHING was traced. Check the name with "
						 "list_level_actors, or omit ignoreActor."), *IgnoreName));
				return;
			}
			Params.AddIgnoredActor(Ignore);
			Out->SetStringField(TEXT("ignoredActor"), Ignore->GetPathName());
		}

		FHitResult Hit;
		const bool bHit = World->LineTraceSingleByChannel(
			Hit, FVector(X, Y, FromZ), FVector(X, Y, ToZ), ECC_WorldStatic, Params);

		// WHICH WORLD THIS ANSWER CAME FROM. trace_ground uses ActiveWorld(), which prefers the PIE world
		// while PIE runs; list_level_actors uses EditorWorld() and always reports the editor one. During
		// PIE on a World Partition map with no cells resident, that pair reads as catastrophic - ground
		// exists, zero actors, spawns return null - when nothing is wrong at all. It cost a real session
		// the time to work that out, so both endpoints now say which world they used.
		//
		// The NAME alone does not settle it: a PIE world is a duplicate and carries the same name as the
		// editor world it came from. worldType is the field that actually distinguishes them.
		Out->SetStringField(TEXT("world"), World->GetName());
		Out->SetStringField(TEXT("worldType"), World->IsPlayInEditor() ? TEXT("pie") : TEXT("editor"));
		Out->SetBoolField(TEXT("hit"), bHit);
		if (bHit)
		{
			Out->SetNumberField(TEXT("z"), Hit.ImpactPoint.Z);
			// Echo the point actually traced, so a caller can never again believe a result belongs to
			// an XY it did not sample.
			Out->SetObjectField(TEXT("traced"), Vec3(FVector(X, Y, FromZ)));
			Out->SetObjectField(TEXT("normal"), Vec3(Hit.ImpactNormal));
			if (AActor* HitActor = Hit.GetActor())
			{
				Out->SetStringField(TEXT("actorPath"), HitActor->GetPathName());
				Out->SetStringField(TEXT("label"), HitActor->GetActorLabel());
			}
		}
		else
		{
			Out->SetStringField(TEXT("note"),
				TEXT("no hit — the ground mesh may have no collision in the editor world. Do NOT assume z=0; place from known bounds instead."));
		}
	}

	// --- capture_camera -----------------------------------------------------
	//   in:  { location:{x,y,z} (or top-level x,y,z), rotation:{x,y,z}, lookAt?:{x,y,z},
	//          useViewportCamera? (aliases: useViewport, fromViewport),
	//          fov?, width?, height?, name? }
	//   out: { path, exists, wroteFile, width, height, location, rotation, fov,
	//          cameraSource, locationSource, rotationSource, fovSource, viewport? }
	//   bucket: READ-ONLY (listed in MifBridgeCommon.cpp) — the ASceneCapture2D is RF_Transient and is
	//           destroyed inside the call, so nothing is dirtied and no transaction is opened.
	//
	// Renders from an ARBITRARY viewpoint via a temporary SceneCapture2D — deliberately NOT the
	// user's viewport, so inspecting a building doesn't yank their camera around while they work.
	//
	// THE COMPOSITION GAP THIS CLOSES (docs/06_OPEN_ISSUES_FROM_USE.md §7, task #21).
	// set_viewport_camera drives FLevelEditorViewportClient (MifBridgeViewport.cpp:84-141) and answers
	// ok:true. capture_camera spawned its OWN ASceneCapture2D at a Loc/Rot taken solely from its own
	// location/rotation params — defaulting to (0,0,500) and (-25,0,0) — and never read the viewport.
	// Both endpoints were individually truthful; the caller's reasonable model, "point the camera, then
	// capture", spanned two endpoints that shared no state, and nothing in either response said so.
	//   * useViewportCamera:true seeds Loc/Rot/FOV from the active level viewport client. OPT-IN, never
	//     the default: the (0,0,500)/(-25,0,0) defaults are load-bearing for existing callers, and
	//     silently re-aiming them would be the SAME breakage class being fixed here.
	//   * cameraSource is echoed ALWAYS — "explicit" | "viewport" | "default" — including when
	//     useViewportCamera is absent. That is the point: a caller who never learned about the opt-in
	//     reads cameraSource:"default" in the JSON instead of discovering it in the pixels.
	// locationSource / rotationSource / fovSource are echoed alongside it because the three can
	// legitimately disagree (useViewportCamera:true with an explicit fov, say) and one summary word
	// would then have to lie about one of them.
	//
	// What is still NOT shared, stated rather than implied: the viewport's VIEW MODE, SHOW FLAGS and
	// RESOLUTION. This always renders lit + tonemapped (SCS_FinalColorLDR) with Atmosphere and Fog on,
	// at width/height, so set_view_mode wireframe/unlit does NOT reach this image. See the KeyNotes on
	// the guard below, which say so to any caller who tries to pass showFlags/viewMode.
	//
	// CaptureScene() is synchronous (it enqueues and flushes), and ExportRenderTarget writes the file
	// before returning, so the path handed back already exists — no poll-then-fetch dance.
	void H_capture_camera(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("x"), TEXT("y"), TEXT("z"), TEXT("location"), TEXT("rotation"), TEXT("lookAt"),
			  TEXT("useViewportCamera"), TEXT("useViewport"), TEXT("fromViewport"),
			  TEXT("fov"), TEXT("width"), TEXT("height"), TEXT("name") },
			TEXT("x, y, z (or location:{x,y,z}), rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z}, useViewportCamera (aliases: useViewport, fromViewport), fov, width, height, name"),
			{ { TEXT("showFlags"), TEXT("not implemented — capture_camera always renders lit/tonemapped with Atmosphere+Fog on and does NOT read the level viewport's show flags, and no endpoint in this build sets a view mode") },
			  { TEXT("viewMode"),  TEXT("not implemented — same gap as showFlags: the viewport's view mode is not consumed here") },
			  { TEXT("actorPath"), TEXT("not a parameter of this endpoint — to frame an actor, read get_actor_bounds and pass its origin as lookAt, or focus_viewport the actor and then capture with useViewportCamera:true") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		// Presence is tested on the OBJECT, not with JHasAny: FJsonObject::HasField answers true for an
		// explicit JSON null, and a null location reported as an "explicit" camera would be a brand new
		// lie in the very field added to stop lying about where the shot came from.
		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		const bool bLocObj = In->TryGetObjectField(TEXT("location"), LocObj) && LocObj;
		const bool bLocXYZ = JHasAny(In, { TEXT("x"), TEXT("y"), TEXT("z") });
		const TSharedPtr<FJsonObject>* RotObj = nullptr;
		const bool bRotObj = In->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj;
		const TSharedPtr<FJsonObject>* LookObj = nullptr;
		const bool bLookObj = In->TryGetObjectField(TEXT("lookAt"), LookObj) && LookObj;
		const bool bFovGiven = JHasAny(In, { TEXT("fov") });
		const bool bWantViewport = JBoolAny(In,
			{ TEXT("useViewportCamera"), TEXT("useViewport"), TEXT("fromViewport") }, false);

		// Seed order is default -> viewport -> explicit, and every stage records WHICH source it was,
		// so the labels reported below are produced by the same reads that produced the numbers rather
		// than re-derived afterwards from a second look at the payload.
		FVector  Loc(0.0, 0.0, 500.0);
		FRotator Rot(-25.0, 0.0, 0.0);
		double   Fov = 75.0;
		const TCHAR* LocSource = TEXT("default");
		const TCHAR* RotSource = TEXT("default");
		const TCHAR* FovSource = TEXT("default");

		if (bWantViewport)
		{
			int32 VpIndex = INDEX_NONE, VpCount = 0;
			FLevelEditorViewportClient* Client = MifSpatialActiveLevelViewport(VpIndex, VpCount);
			if (!Client)
			{
				// The caller asked for the viewport BY NAME. Quietly falling back to (0,0,500)/(-25,0,0)
				// would render a confident picture of somewhere nobody asked about — the exact failure
				// this parameter exists to close, reintroduced one level down.
				Fail(Out, TEXT("useViewportCamera was requested but no level editor viewport is available ")
					TEXT("(get_viewport_camera answers the same question and will fail the same way). Open a level ")
					TEXT("viewport, or pass location/rotation explicitly."));
				return;
			}
			Loc = Client->GetViewLocation();
			Rot = Client->GetViewRotation();
			Fov = Client->ViewFOV;
			LocSource = TEXT("viewport");
			RotSource = TEXT("viewport");
			FovSource = TEXT("viewport");

			// Echo the client that answered, INDEX included, so a caller can diff this against
			// get_viewport_camera rather than trust that two file-local copies of "which viewport is
			// active" still agree (see the eviction clause on MifSpatialActiveLevelViewport).
			TSharedRef<FJsonObject> Vp = MakeShared<FJsonObject>();
			Vp->SetObjectField(TEXT("location"), Vec3(Loc));
			Vp->SetObjectField(TEXT("rotation"), Vec3(FVector(Rot.Pitch, Rot.Yaw, Rot.Roll)));
			Vp->SetNumberField(TEXT("fov"), Client->ViewFOV);
			Vp->SetBoolField(TEXT("perspective"), Client->IsPerspective());
			Vp->SetNumberField(TEXT("index"), VpIndex);
			Vp->SetNumberField(TEXT("count"), VpCount);
			Out->SetObjectField(TEXT("viewport"), Vp);

			if (!Client->IsPerspective())
			{
				// An orthographic client's view location is a projection origin, not an eye position,
				// and a SceneCapture2D renders PERSPECTIVE. The seed is still honoured — the caller
				// asked for it — but an image that quietly answers a different question than the screen
				// is the same defect in a different medium, so it is named.
				AddWarning(Out, TEXT("the active level viewport is ORTHOGRAPHIC: its location/rotation were used, but ")
					TEXT("this capture is a PERSPECTIVE render and will not match what is on screen. Switch it with ")
					TEXT("set_viewport_camera {ortho:\"perspective\"} first, or pass location/rotation explicitly."));
			}
			if (World != EditorWorld())
			{
				AddWarning(Out, TEXT("seeded from the level editor viewport, which always shows the EDITOR world, while ")
					TEXT("capturing the PIE world — the pose was COPIED across two different worlds, not shared with them."));
			}
		}
		// True only past the failure above, so everything downstream can ask "did the viewport seed us?"
		// without re-testing the parameter.
		const bool bSeeded = bWantViewport;

		if (bLocObj || bLocXYZ)
		{
			// A PARTIAL location keeps the BASELINE for the components it omits. Baseline is the
			// viewport pose when one seeded us, and otherwise the historical default: location:{x:1}
			// has always meant (1,0,0) and top-level x:1 has always meant (1,0,500). Changing either
			// would silently re-aim existing callers, which is the thing being fixed, not repeated.
			if (bLocObj)
			{
				const FVector Base = bSeeded ? Loc : FVector::ZeroVector;
				const TSharedRef<FJsonObject> L = LocObj->ToSharedRef();
				Loc = FVector(JNum(L, TEXT("x"), Base.X), JNum(L, TEXT("y"), Base.Y), JNum(L, TEXT("z"), Base.Z));
			}
			else
			{
				const FVector Base = bSeeded ? Loc : FVector(0.0, 0.0, 500.0);
				Loc = FVector(JNum(In, TEXT("x"), Base.X), JNum(In, TEXT("y"), Base.Y), JNum(In, TEXT("z"), Base.Z));
			}
			LocSource = TEXT("explicit");
		}

		if (bRotObj)
		{
			const FRotator Base = bSeeded ? Rot : FRotator::ZeroRotator;
			const TSharedRef<FJsonObject> R = RotObj->ToSharedRef();
			Rot = FRotator(JNum(R, TEXT("x"), Base.Pitch), JNum(R, TEXT("y"), Base.Yaw), JNum(R, TEXT("z"), Base.Roll));
			RotSource = TEXT("explicit");
		}

		// lookAt is far easier to drive from a script than pitch/yaw — "frame that building" is a
		// point, not an angle. Applied AFTER the location so it aims from wherever we actually ended
		// up, viewport-seeded location included.
		if (bLookObj)
		{
			const TSharedRef<FJsonObject> L = LookObj->ToSharedRef();
			const FVector Target(JNum(L, TEXT("x")), JNum(L, TEXT("y")), JNum(L, TEXT("z")));
			if (Target.Equals(Loc, 0.01))
			{
				// (Target - Loc).Rotation() on a zero vector is ZeroRotator, i.e. "looking down +X" —
				// a confident answer to an unanswerable question.
				Fail(Out, FString::Printf(
					TEXT("lookAt (%s) is the camera location itself, so there is no direction to look. Move one of them."),
					*Target.ToString()));
				return;
			}
			if (bRotObj)
			{
				AddWarning(Out, TEXT("both rotation and lookAt were supplied; lookAt wins (aiming at a point is the more useful of the two)."));
			}
			Rot = (Target - Loc).Rotation();
			RotSource = TEXT("explicit");
		}

		if (bFovGiven)
		{
			Fov = JNum(In, TEXT("fov"), Fov);
			FovSource = TEXT("explicit");
		}
		if (!(Fov > 0.0 && Fov < 180.0))
		{
			// Reachable from the viewport seed as well as from the caller, which is why the source is
			// quoted: a degenerate FOV renders a black or smeared frame and used to answer ok:true.
			Fail(Out, FString::Printf(
				TEXT("fov %.3f is out of range (source: %s) — a perspective field of view must be greater than 0 and less than 180 degrees."),
				Fov, FovSource));
			return;
		}

		// ALWAYS reported, even with no viewport involved: this single word is what makes the
		// composition gap visible in JSON. "explicit" whenever the caller named any part of the
		// transform, because those win over a viewport seed.
		const bool bAnyExplicit = (bLocObj || bLocXYZ || bRotObj || bLookObj);
		const TCHAR* CameraSource = bAnyExplicit ? TEXT("explicit")
			: (bSeeded ? TEXT("viewport") : TEXT("default"));
		if (bAnyExplicit && bSeeded)
		{
			AddWarning(Out, TEXT("useViewportCamera:true AND an explicit location/rotation/lookAt were both supplied — ")
				TEXT("the explicit values win. locationSource/rotationSource/fovSource say which each one came from."));
		}

		const int32 W  = FMath::Clamp(JInt(In, TEXT("width"), 1280), 64, 4096);
		const int32 Ht = FMath::Clamp(JInt(In, TEXT("height"), 720), 64, 4096);

		UTextureRenderTarget2D* RT = NewObject<UTextureRenderTarget2D>(GetTransientPackage());
		RT->RenderTargetFormat = RTF_RGBA8;
		RT->ClearColor = FLinearColor::Black;
		RT->bAutoGenerateMips = false;
		RT->InitAutoFormat(W, Ht);
		RT->UpdateResourceImmediate(true);

		FActorSpawnParameters SpawnParams;
		SpawnParams.ObjectFlags |= RF_Transient;   // never dirties the level
		ASceneCapture2D* Cap = World->SpawnActor<ASceneCapture2D>(Loc, Rot, SpawnParams);
		if (!Cap) { Fail(Out, TEXT("failed to spawn capture camera")); return; }

		// Verify the pose BEFORE rendering. An image is only as truthful as the transform it was shot
		// from, and echoing the REQUESTED numbers beside a picture taken from somewhere else is exactly
		// the class of silent success this endpoint was reopened to kill.
		//
		// The ORIENTATION is compared as quaternions, not component-wise as FRotator::Equals would do.
		// An actor stores an FQuat, so GetActorRotation() is a round trip, and at gimbal lock that round
		// trip returns a DIFFERENT Euler triple for the SAME orientation - (-90, 45, 0) comes back as
		// (-90, 0, -45). A component-wise check would therefore reject the straight-down shot, which is
		// the single most common lookAt in this bridge (top-down framing). |dot| handles q and -q being
		// the same rotation; 0.9999 is ~0.8 degrees.
		const FVector  ActualLoc = Cap->GetActorLocation();
		const FRotator ActualRot = Cap->GetActorRotation();
		const double   AimDot    = FMath::Abs(ActualRot.Quaternion() | Rot.Quaternion());
		if (!ActualLoc.Equals(Loc, 0.1) || AimDot < 0.9999)
		{
			const FString Detail = FString::Printf(
				TEXT("capture camera did not land on the requested transform: asked loc %s rot %s, got loc %s rot %s. No image was written."),
				*Loc.ToString(), *Rot.ToString(), *ActualLoc.ToString(), *ActualRot.ToString());
			Cap->Destroy();
			Fail(Out, Detail);
			return;
		}

		USceneCaptureComponent2D* Comp = Cap->GetCaptureComponent2D();
		if (!Comp) { Cap->Destroy(); Fail(Out, TEXT("capture component missing")); return; }
		Comp->TextureTarget = RT;
		Comp->FOVAngle = (float)Fov;
		Comp->CaptureSource = SCS_FinalColorLDR;         // lit, tonemapped — what the eye would see
		Comp->bCaptureEveryFrame = false;
		Comp->bCaptureOnMovement = false;
		Comp->ShowFlags.SetAtmosphere(true);
		Comp->ShowFlags.SetFog(true);
		Comp->CaptureScene();
		// Read back off the component rather than echoing the request: same rule as the transform above.
		const double ActualFov = Comp->FOVAngle;

		FString Name = JStr(In, TEXT("name"), TEXT("MifShot"));
		Name = FPaths::MakeValidFileName(Name);
		const FString Dir = FPaths::ProjectSavedDir() / TEXT("MifBridge");
		IPlatformFile& PF = FPlatformFileManager::Get().GetPlatformFile();
		PF.CreateDirectoryTree(*Dir);
		const FString FullPath = FPaths::ConvertRelativePathToFull(Dir / (Name + TEXT(".png")));

		// ExportRenderTarget returns void, so a bare FileExists() afterwards answers "yes" for an export
		// that did nothing whenever an EARLIER capture left a file of the same name behind — a stale PNG
		// reported as this call's output. Snapshot stamp+size first and compare.
		const bool     bExistedBefore = PF.FileExists(*FullPath);
		const FDateTime BeforeStamp   = bExistedBefore ? PF.GetTimeStamp(*FullPath) : FDateTime::MinValue();
		const int64     BeforeSize    = bExistedBefore ? PF.FileSize(*FullPath) : -1;

		UKismetRenderingLibrary::ExportRenderTarget(World, RT, Dir, Name + TEXT(".png"));
		Cap->Destroy();

		const bool bExists = PF.FileExists(*FullPath);
		const bool bFresh  = bExists && (!bExistedBefore
			|| PF.GetTimeStamp(*FullPath) != BeforeStamp
			|| PF.FileSize(*FullPath) != BeforeSize);

		Out->SetStringField(TEXT("path"), FullPath);
		Out->SetBoolField(TEXT("exists"), bExists);       // verified, not assumed
		Out->SetBoolField(TEXT("wroteFile"), bFresh);     // and verified to be THIS call's file
		Out->SetNumberField(TEXT("width"), W);
		Out->SetNumberField(TEXT("height"), Ht);
		Out->SetObjectField(TEXT("location"), Vec3(ActualLoc));
		Out->SetObjectField(TEXT("rotation"), Vec3(FVector(ActualRot.Pitch, ActualRot.Yaw, ActualRot.Roll)));
		Out->SetNumberField(TEXT("fov"), ActualFov);
		Out->SetStringField(TEXT("cameraSource"), CameraSource);
		Out->SetStringField(TEXT("locationSource"), LocSource);
		Out->SetStringField(TEXT("rotationSource"), RotSource);
		Out->SetStringField(TEXT("fovSource"), FovSource);
		Out->SetBoolField(TEXT("useViewportCamera"), bWantViewport);

		if (!bExists)
		{
			Fail(Out, FString::Printf(
				TEXT("render target export wrote no file at %s — ExportRenderTarget reports nothing, so check that '%s' is writable and that the render target initialised."),
				*FullPath, *Dir));
			return;
		}
		if (!bFresh)
		{
			AddWarning(Out, FString::Printf(
				TEXT("%s already existed and its timestamp and size are unchanged — this response may be describing an EARLIER capture of the same name. Pass a distinct 'name' per shot."),
				*FullPath));
		}
		if (FCString::Strcmp(CameraSource, TEXT("default")) == 0)
		{
			// The discoverability half of the fix: the caller who hit docs/06_OPEN_ISSUES_FROM_USE.md §7
			// got ok:true and a picture of nowhere. Now they get told, in the response, where it came
			// from and how to shoot from the viewport they just moved.
			AddWarning(Out, TEXT("cameraSource is \"default\": no location/rotation/lookAt was given and useViewportCamera was not set, ")
				TEXT("so this image is from (0,0,500) looking down 25 degrees — NOT from the editor viewport, which set_viewport_camera ")
				TEXT("and focus_viewport drive independently of this endpoint. Pass useViewportCamera:true to shoot from where they left it."));
		}

		UE_LOG(LogMifBridge, Log, TEXT("capture_camera (%s) -> %s"), CameraSource, *FullPath);
	}

	// --- scene_report -------------------------------------------------------
	//   out: { actorCount, bounds, overlaps, floating[], sunken[], scaleOutliers[] }
	// The single call that would have caught every mistake in the blind build. Run it after placing.
	void H_scene_report(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("groundZ"), TEXT("floatTolerance"), TEXT("tallWarnZ") },
			TEXT("groundZ, floatTolerance, tallWarnZ — all optional; the scan itself always covers every actor in the active world"),
			{ { TEXT("tolerance"), TEXT("the float/sunken threshold here is 'floatTolerance' (default 30) — 'tolerance' is check_overlaps' overlap-depth threshold") },
			  { TEXT("nameContains"), TEXT("not supported — scene_report always scans the whole world; filter its floating/sunken/tooTall arrays caller-side, or use check_overlaps which does take nameContains") },
			  { TEXT("actorPath"), TEXT("scene_report is whole-scene by design; for one actor use get_actor_bounds, or check_overlaps with actorPath") } }))
		{
			return;
		}

		UWorld* World = ActiveWorld();
		if (!World) { Fail(Out, TEXT("no world")); return; }

		const double GroundZ = JNum(In, TEXT("groundZ"), 0.0);
		const double FloatTol = JNum(In, TEXT("floatTolerance"), 30.0);

		int32 Count = 0;
		FBox Total(ForceInit);
		TArray<TSharedPtr<FJsonValue>> Floating, Sunken, Big;
		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* A = *It;
			if (!A || !IsValid(A)) { continue; }
			FBox B;
			if (!ActorBox(A, B)) { continue; }
			++Count;
			Total += B;

			const FString Label = A->GetActorLabel();
			if (Label.Contains(TEXT("Ground")) || Label.Contains(TEXT("Sky"))) { continue; }

			// Floating: the actor's underside sits clearly above the ground plane.
			if (B.Min.Z > GroundZ + FloatTol)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("label"), Label);
				J->SetNumberField(TEXT("gap"), B.Min.Z - GroundZ);
				Floating.Add(MakeShared<FJsonValueObject>(J));
			}
			// Sunken: more than half the actor is below ground.
			const double Height = B.Max.Z - B.Min.Z;
			if (Height > 1.0 && B.Max.Z < GroundZ + Height * 0.5)
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("label"), Label);
				J->SetNumberField(TEXT("buriedTo"), B.Max.Z);
				Sunken.Add(MakeShared<FJsonValueObject>(J));
			}
			// Scale outlier: anything taller than a 3-storey building is probably a mistake in a town.
			if (Height > JNum(In, TEXT("tallWarnZ"), 1500.0))
			{
				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("label"), Label);
				J->SetNumberField(TEXT("height"), Height);
				J->SetObjectField(TEXT("scale"), Vec3(A->GetActorScale3D()));
				Big.Add(MakeShared<FJsonValueObject>(J));
			}
		}

		Out->SetNumberField(TEXT("actorCount"), Count);
		if (Total.IsValid)
		{
			Out->SetObjectField(TEXT("sceneMin"), Vec3(Total.Min));
			Out->SetObjectField(TEXT("sceneMax"), Vec3(Total.Max));
			Out->SetObjectField(TEXT("sceneSize"), Vec3(Total.GetSize()));
		}
		Out->SetArrayField(TEXT("floating"), Floating);
		Out->SetArrayField(TEXT("sunken"), Sunken);
		Out->SetArrayField(TEXT("tooTall"), Big);
		Out->SetNumberField(TEXT("floatingCount"), Floating.Num());
		Out->SetNumberField(TEXT("sunkenCount"), Sunken.Num());
		Out->SetNumberField(TEXT("tooTallCount"), Big.Num());
	}
}
