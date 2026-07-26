// MifBridge — VIEWPORT: driving the editor camera.
//
// This exists because of a question that had no good answer: "how do I see the whole map without
// flying around it?" There was no endpoint to move the editor camera, so the advice was manual
// (Ctrl+A then F, or Alt+J for top ortho) and the first guess at the cause was wrong — it was blamed
// on camera speed when the real complaint was LOD cull distance.
//
// An agent that can capture_camera but cannot MOVE the editor viewport is also stuck describing
// where to look instead of just looking. These endpoints close that: frame the whole level, frame a
// specific actor, drop into top-down ortho, or set an exact transform.
//
// Note the distinction from capture_camera: that spawns a transient scene-capture and writes a PNG,
// affecting nothing the user sees. These change what is on the user's screen. They are read-only in
// the transaction sense — a camera move dirties no asset and must not push an undo entry.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"
#include "Editor/UnrealEdTypes.h"          // ELevelViewportType (LVT_Ortho*)
#include "EditorViewportClient.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "LevelEditorViewport.h"

namespace MifBridge
{
	namespace
	{
		// The "active" viewport, falling back to the first perspective one. GetActiveViewport() can be
		// null when the editor has focus elsewhere, which would otherwise make these endpoints fail
		// for no reason the caller can act on.
		FLevelEditorViewportClient* ActiveLevelViewport()
		{
			if (!GEditor) { return nullptr; }
			const TArray<FLevelEditorViewportClient*>& Clients = GEditor->GetLevelViewportClients();
			FLevelEditorViewportClient* FirstPerspective = nullptr;
			for (FLevelEditorViewportClient* Client : Clients)
			{
				if (!Client) { continue; }
				if (!FirstPerspective && Client->IsPerspective()) { FirstPerspective = Client; }
				if (Client->Viewport && Client->Viewport == GEditor->GetActiveViewport())
				{
					return Client;
				}
			}
			if (FirstPerspective) { return FirstPerspective; }
			return Clients.Num() > 0 ? Clients[0] : nullptr;
		}

		UWorld* VpWorld()
		{
			return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
		}

		void WriteCamera(const TSharedRef<FJsonObject>& Out, FLevelEditorViewportClient* Client)
		{
			if (!Client) { return; }
			const FVector L = Client->GetViewLocation();
			const FRotator R = Client->GetViewRotation();
			TSharedRef<FJsonObject> Loc = MakeShared<FJsonObject>();
			Loc->SetNumberField(TEXT("x"), L.X); Loc->SetNumberField(TEXT("y"), L.Y); Loc->SetNumberField(TEXT("z"), L.Z);
			TSharedRef<FJsonObject> Rot = MakeShared<FJsonObject>();
			Rot->SetNumberField(TEXT("x"), R.Pitch); Rot->SetNumberField(TEXT("y"), R.Yaw); Rot->SetNumberField(TEXT("z"), R.Roll);
			Out->SetObjectField(TEXT("location"), Loc);
			Out->SetObjectField(TEXT("rotation"), Rot);
			Out->SetBoolField(TEXT("perspective"), Client->IsPerspective());
			Out->SetNumberField(TEXT("fov"), Client->ViewFOV);
		}

		AActor* FindVpActor(UWorld* World, const FString& Query)
		{
			if (!World || Query.IsEmpty()) { return nullptr; }
			for (TActorIterator<AActor> It(World); It; ++It)
			{
				AActor* A = *It;
				if (!A || !IsValid(A)) { continue; }
				if (A->GetPathName() == Query || A->GetName() == Query || A->GetActorLabel() == Query)
				{
					return A;
				}
			}
			return nullptr;
		}
	}

	// --- set_viewport_camera ------------------------------------------------
	//   in:  { location?:{x,y,z}, rotation?:{x,y,z}, lookAt?:{x,y,z}, fov?, ortho? ("top"|"front"|
	//          "side"|"perspective"), orthoZoom? }
	//   out: { location, rotation, perspective, fov }
	// lookAt wins over rotation — asking for both is a caller mistake, and aiming at a point is the
	// far more useful of the two.
	void H_set_viewport_camera(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		FLevelEditorViewportClient* Client = ActiveLevelViewport();
		if (!Client) { Fail(Out, TEXT("no level editor viewport available")); return; }

		const FString Ortho = JStr(In, TEXT("ortho")).ToLower();
		if (!Ortho.IsEmpty())
		{
			// Orthographic top-down is the honest answer to "show me the whole map" — no perspective
			// falloff, no far-clip surprises.
			// ELevelViewportType is named by PLANE, not direction: LVT_OrthoXY is "top",
			// LVT_OrthoNegativeXY is "bottom", and so on (Editor/UnrealEdTypes.h). Callers get to say
			// "top" because that is what a human means.
			if (Ortho == TEXT("top"))              { Client->SetViewportType(LVT_OrthoXY); }
			else if (Ortho == TEXT("bottom"))      { Client->SetViewportType(LVT_OrthoNegativeXY); }
			else if (Ortho == TEXT("front"))       { Client->SetViewportType(LVT_OrthoXZ); }
			else if (Ortho == TEXT("back"))        { Client->SetViewportType(LVT_OrthoNegativeXZ); }
			else if (Ortho == TEXT("left"))        { Client->SetViewportType(LVT_OrthoYZ); }
			else if (Ortho == TEXT("right"))       { Client->SetViewportType(LVT_OrthoNegativeYZ); }
			else if (Ortho == TEXT("perspective")) { Client->SetViewportType(LVT_Perspective); }
			else
			{
				Fail(Out, FString::Printf(
					TEXT("unknown ortho '%s' — use top/bottom/front/back/left/right/perspective"), *Ortho));
				return;
			}
		}

		FVector Loc = Client->GetViewLocation();
		const TSharedPtr<FJsonObject>* LocObj = nullptr;
		if (In->TryGetObjectField(TEXT("location"), LocObj) && LocObj)
		{
			const TSharedRef<FJsonObject> O = LocObj->ToSharedRef();
			Loc = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
			Client->SetViewLocation(Loc);
		}

		const TSharedPtr<FJsonObject>* LookObj = nullptr;
		if (In->TryGetObjectField(TEXT("lookAt"), LookObj) && LookObj)
		{
			const TSharedRef<FJsonObject> O = LookObj->ToSharedRef();
			const FVector Target(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
			Client->SetViewRotation((Target - Loc).Rotation());
		}
		else
		{
			const TSharedPtr<FJsonObject>* RotObj = nullptr;
			if (In->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj)
			{
				const TSharedRef<FJsonObject> O = RotObj->ToSharedRef();
				// x/y/z = pitch/yaw/roll, matching every other MifBridge transform.
				Client->SetViewRotation(FRotator(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z"))));
			}
		}

		if (JHasAny(In, { TEXT("fov") }))       { Client->ViewFOV = (float)JNum(In, TEXT("fov"), 90.0); }
		if (JHasAny(In, { TEXT("orthoZoom") })) { Client->SetOrthoZoom((float)JNum(In, TEXT("orthoZoom"), 10000.0)); }

		Client->Invalidate();
		WriteCamera(Out, Client);
	}

	// --- focus_viewport -----------------------------------------------------
	//   in:  { actorPath? | folder? | all? (default), instant? }
	//   out: { framed, actorCount, bounds:{min,max}, location, rotation }
	// The programmatic equivalent of select-all-then-F. Without a target it frames the WHOLE level,
	// which is the "see everything at once" case.
	void H_focus_viewport(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		FLevelEditorViewportClient* Client = ActiveLevelViewport();
		if (!Client) { Fail(Out, TEXT("no level editor viewport available")); return; }
		UWorld* World = VpWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const FString ActorQuery = JStrAny(In, { TEXT("actorPath"), TEXT("actor") });
		const FString Folder = JStr(In, TEXT("folder"));

		FBox Bounds(ForceInit);
		int32 Counted = 0;

		if (!ActorQuery.IsEmpty())
		{
			AActor* A = FindVpActor(World, ActorQuery);
			if (!A) { Fail(Out, FString::Printf(TEXT("actor not found: '%s'"), *ActorQuery)); return; }
			FVector Origin, Extent;
			A->GetActorBounds(false, Origin, Extent);
			Bounds += FBox(Origin - Extent, Origin + Extent);
			Counted = 1;
		}
		else
		{
			for (TActorIterator<AActor> It(World); It; ++It)
			{
				AActor* A = *It;
				if (!A || !IsValid(A)) { continue; }
				if (!Folder.IsEmpty() && !A->GetFolderPath().ToString().StartsWith(Folder)) { continue; }
				FVector Origin, Extent;
				A->GetActorBounds(false, Origin, Extent);
				// Skip actors with no extent (lights, markers) so a single stray marker at the far
				// edge of the map cannot blow the framing out to nothing.
				if (Extent.IsNearlyZero()) { continue; }
				Bounds += FBox(Origin - Extent, Origin + Extent);
				++Counted;
			}
		}

		if (Counted == 0 || !Bounds.IsValid)
		{
			Fail(Out, TEXT("nothing with bounds to frame — check the folder filter, or the level is empty"));
			return;
		}

		Client->FocusViewportOnBox(Bounds, JBool(In, TEXT("instant"), true));
		Client->Invalidate();

		Out->SetBoolField(TEXT("framed"), true);
		Out->SetNumberField(TEXT("actorCount"), Counted);
		TSharedRef<FJsonObject> Mn = MakeShared<FJsonObject>();
		Mn->SetNumberField(TEXT("x"), Bounds.Min.X); Mn->SetNumberField(TEXT("y"), Bounds.Min.Y); Mn->SetNumberField(TEXT("z"), Bounds.Min.Z);
		TSharedRef<FJsonObject> Mx = MakeShared<FJsonObject>();
		Mx->SetNumberField(TEXT("x"), Bounds.Max.X); Mx->SetNumberField(TEXT("y"), Bounds.Max.Y); Mx->SetNumberField(TEXT("z"), Bounds.Max.Z);
		TSharedRef<FJsonObject> B = MakeShared<FJsonObject>();
		B->SetObjectField(TEXT("min"), Mn); B->SetObjectField(TEXT("max"), Mx);
		Out->SetObjectField(TEXT("bounds"), B);
		WriteCamera(Out, Client);
	}

	// --- get_viewport_camera ------------------------------------------------
	//   out: { location, rotation, perspective, fov, viewportCount }
	void H_get_viewport_camera(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		FLevelEditorViewportClient* Client = ActiveLevelViewport();
		if (!Client) { Fail(Out, TEXT("no level editor viewport available")); return; }
		WriteCamera(Out, Client);
		Out->SetNumberField(TEXT("viewportCount"),
			GEditor ? GEditor->GetLevelViewportClients().Num() : 0);
	}
}
