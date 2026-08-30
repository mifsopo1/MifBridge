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
#include "ShowFlags.h"                 // FEngineShowFlags - and its checkNoEntry trap
#include "Engine/EngineBaseTypes.h"    // EViewModeIndex as a UENUM, for the names
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

		// EditorWorld() is MifBridge::EditorWorld() now — the level-editor viewport always shows the editor
		// world, so this one is deliberately NOT the PIE-preferring ActiveWorld().

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

		// The actor finder moved to MifBridgeCommon.cpp as MifBridge::FindActorInWorld (declared in
		// MifBridgeHandlers.h). FIVE byte-identical copies existed under five different names
		// (FindActor, FindNavActor, FindActorByPathOrLabel, FindVpActor, FindWorldActor) — different
		// names are not a build error, which is exactly why they survived, but it meant a fix to the
		// path/name/label matching rule landed in one of five places. Do NOT add a sixth.
	}

	// --- set_viewport_camera ------------------------------------------------
	//   in:  { location?:{x,y,z}, rotation?:{x,y,z}, lookAt?:{x,y,z}, fov?, ortho? ("top"|"front"|
	//          "side"|"perspective"), orthoZoom? }
	//   out: { location, rotation, perspective, fov }
	// lookAt wins over rotation — asking for both is a caller mistake, and aiming at a point is the
	// far more useful of the two.

	// =======================================================================
	// VIEW MODE AND SHOW FLAGS - the rendering-diagnosis surface
	// =======================================================================
	//
	// THE QUESTION THIS ANSWERS is "why is it black" - because the material is broken, because
	// nothing is lit, or because the mesh is not there. Wireframe, Unlit and LightingOnly separate
	// those three in one call each, and an agent had no way to reach any of them. capture_viewport
	// documents the hole in its own error text.
	//
	// GAME VIEW IS IN THE SAME CALL on purpose. It is the single biggest "why does my capture not
	// match what I see" lever - editor-only sprites, billboards and grids vanish under it - and
	// leaving it out would mean an agent could set a view mode and still not know why the picture
	// disagreed with the screen.
	//
	// TWO ENGINE TRAPS, both verified by reading rather than by building and finding out:
	//
	// 1. GetViewModeName(EViewModeIndex) at ShowFlags.h:570 is declared WITHOUT ENGINE_API and
	//    defined in a Private .cpp, so calling it from a plugin is an unresolved external on 5.3
	//    and 5.7 alike. The name comes from StaticEnum<EViewModeIndex>() instead - EViewModeIndex
	//    is a UENUM, so the reflection system has the names and no linkage is involved.
	//
	// 2. FEngineShowFlags::SetSingleFlag's default branch is checkNoEntry() (ShowFlags.cpp:194),
	//    so passing an index FindIndexByName did not recognise ASSERTS - a dead editor, not an
	//    error. Every flag name is therefore resolved and refused BEFORE anything is set, and the
	//    whole request is validated before the first write so a typo in the fifth flag cannot leave
	//    the first four applied.
	//
	// AND THE ORDER MATTERS. SetViewMode internally runs ApplyViewMode, which REWRITES show flags -
	// so a showFlags map has to be applied AFTER the view mode or it is silently undone. That is
	// the kind of thing that looks like the endpoint ignoring the parameter.

	FString MifViewModeName(EViewModeIndex Mode)
	{
		if (const UEnum* E = StaticEnum<EViewModeIndex>())
		{
			FString Name = E->GetNameStringByValue(static_cast<int64>(Mode));
			Name.RemoveFromStart(TEXT("VMI_"));
			return Name.IsEmpty() ? FString::Printf(TEXT("Unknown(%d)"), (int32)Mode) : Name;
		}
		return FString::Printf(TEXT("Unknown(%d)"), (int32)Mode);
	}

	bool MifParseViewMode(const FString& In, EViewModeIndex& Out)
	{
		if (In.IsEmpty()) { return false; }
		const UEnum* E = StaticEnum<EViewModeIndex>();
		if (!E) { return false; }
		for (int32 i = 0; i < E->NumEnums() - 1; ++i)
		{
			FString Name = E->GetNameStringByIndex(i);
			Name.RemoveFromStart(TEXT("VMI_"));
			if (Name.Equals(In, ESearchCase::IgnoreCase))
			{
				Out = static_cast<EViewModeIndex>(E->GetValueByIndex(i));
				return true;
			}
		}
		return false;
	}

	FString MifViewModeList()
	{
		const UEnum* E = StaticEnum<EViewModeIndex>();
		if (!E) { return TEXT("(unavailable)"); }
		TArray<FString> Names;
		for (int32 i = 0; i < E->NumEnums() - 1; ++i)
		{
			FString Name = E->GetNameStringByIndex(i);
			Name.RemoveFromStart(TEXT("VMI_"));
			if (!Name.IsEmpty() && !Name.StartsWith(TEXT("Max")) && !Name.StartsWith(TEXT("Unknown")))
			{
				Names.Add(Name);
			}
		}
		return FString::Join(Names, TEXT(", "));
	}

	/** The flags an agent actually reaches for. "all" dumps every one instead. */
	static const TCHAR* MifCommonShowFlags[] = {
		TEXT("StaticMeshes"), TEXT("SkeletalMeshes"), TEXT("Landscape"), TEXT("Lighting"),
		TEXT("DirectLighting"), TEXT("Fog"), TEXT("VolumetricFog"), TEXT("Atmosphere"),
		TEXT("Bounds"), TEXT("Collision"), TEXT("Grid"), TEXT("Particles"), TEXT("Translucency"),
		TEXT("PostProcessing"), TEXT("Decals"), TEXT("InstancedFoliage"), TEXT("BSP"),
		TEXT("Splines"), TEXT("BillboardSprites"), TEXT("Selection"),
	};

	void MifWriteShowFlags(const TSharedRef<FJsonObject>& Out, FLevelEditorViewportClient* Client,
						   bool bAll)
	{
		TSharedRef<FJsonObject> Flags = MakeShared<FJsonObject>();
		if (bAll)
		{
			// ToString gives the non-default ones; walking the known names is the only way to get a
			// complete picture, so use the engine's own name table.
			// SF_FirstCustom is the boundary between the engine's builtin flags and the ones a
			// plugin registers at runtime - there is no EShowFlag_MAX. Walking to it covers every
			// flag FindIndexByName can resolve by a fixed name, which is what a caller can pass.
			for (int32 Index = 0; Index < static_cast<int32>(FEngineShowFlags::SF_FirstCustom); ++Index)
			{
				const FString Name = FEngineShowFlags::FindNameByIndex(Index);
				if (!Name.IsEmpty())
				{
					Flags->SetBoolField(Name, Client->EngineShowFlags.GetSingleFlag(Index));
				}
			}
		}
		else
		{
			for (const TCHAR* Name : MifCommonShowFlags)
			{
				const int32 Index = FEngineShowFlags::FindIndexByName(Name);
				if (Index != INDEX_NONE)
				{
					Flags->SetBoolField(Name, Client->EngineShowFlags.GetSingleFlag(Index));
				}
			}
		}
		Out->SetObjectField(TEXT("showFlags"), Flags);
	}

	void H_set_viewport_camera(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("location"), TEXT("rotation"), TEXT("lookAt"), TEXT("fov"),
			  TEXT("ortho"), TEXT("orthoZoom"), TEXT("viewMode"), TEXT("showFlags"),
			  TEXT("gameView"), TEXT("realtime") },
			TEXT("location:{x,y,z}, rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z} (wins over ")
			TEXT("rotation), fov, ortho (top/bottom/front/back/left/right/perspective), orthoZoom, ")
			TEXT("viewMode (Lit, Unlit, Wireframe, LightingOnly, ShaderComplexity, ...), showFlags ")
			TEXT("({\"Fog\": false, \"Bounds\": true}), gameView (hides editor-only sprites and ")
			TEXT("grids), realtime"),
			{ { TEXT("x"), TEXT("there is no top-level x/y/z here - pass location:{x,y,z}; rotation and lookAt take the same nested form. capture_camera is the endpoint that also accepts the flat form") },
			  { TEXT("zoom"), TEXT("the key is 'orthoZoom', and it only has an effect on an orthographic view - set ortho first") },
			  { TEXT("orthographic"), TEXT("the key is 'ortho' and it takes a STRING: top/bottom/front/back/left/right/perspective") },
			  { TEXT("actorPath"), TEXT("this endpoint sets an explicit transform - to frame an actor use focus_viewport, which takes actorPath") } }))
		{
			return;
		}

		FLevelEditorViewportClient* Client = ActiveLevelViewport();
		if (Client)
		{
			// VALIDATE EVERY SHOW FLAG NAME BEFORE SETTING ANY OF THEM. SetSingleFlag's default
			// branch is checkNoEntry() (ShowFlags.cpp:194), so an index FindIndexByName did not
			// recognise ASSERTS rather than erroring - a dead editor. And validating up front means
			// a typo in the fifth flag cannot leave the first four applied, which would be a
			// half-done request reported as a failure.
			const TSharedPtr<FJsonObject>* FlagsObj = nullptr;
			if (In->TryGetObjectField(TEXT("showFlags"), FlagsObj) && FlagsObj && FlagsObj->IsValid())
			{
				TArray<FString> Unknown;
				for (const auto& Pair : (*FlagsObj)->Values)
				{
					if (FEngineShowFlags::FindIndexByName(*Pair.Key) == INDEX_NONE)
					{
						Unknown.Add(Pair.Key);
					}
				}
				if (Unknown.Num() > 0)
				{
					Fail(Out, FString::Printf(
						TEXT("unknown show flag(s): %s. FEngineShowFlags::SetSingleFlag ends its ")
						TEXT("default branch in checkNoEntry(), so passing an unrecognised one would ")
						TEXT("ASSERT and take the editor down rather than fail - every name is ")
						TEXT("checked before any is set. get_viewport_camera{showFlags:\"all\"} lists ")
						TEXT("every valid name. NOTHING was changed."),
						*FString::Join(Unknown, TEXT(", "))));
					return;
				}
			}

			// VIEW MODE FIRST, SHOW FLAGS AFTER. SetViewMode runs ApplyViewMode internally, which
			// REWRITES show flags - so applying a showFlags map before it would be silently undone,
			// and would look exactly like the endpoint ignoring the parameter.
			const FString WantMode = JStr(In, TEXT("viewMode"));
			if (!WantMode.IsEmpty())
			{
				EViewModeIndex Mode = VMI_Lit;
				if (!MifParseViewMode(WantMode, Mode))
				{
					Fail(Out, FString::Printf(
						TEXT("unknown viewMode '%s'. Accepted: %s. NOTHING was changed."),
						*WantMode, *MifViewModeList()));
					return;
				}
				Client->SetViewMode(Mode);
			}
			if (FlagsObj && FlagsObj->IsValid())
			{
				for (const auto& Pair : (*FlagsObj)->Values)
				{
					const int32 Index = FEngineShowFlags::FindIndexByName(*Pair.Key);
					bool bValue = false;
					if (Pair.Value.IsValid() && Pair.Value->TryGetBool(bValue))
					{
						Client->EngineShowFlags.SetSingleFlag(Index, bValue);
					}
				}
			}
			if (In->HasField(TEXT("gameView")))
			{
				Client->SetGameView(JBool(In, TEXT("gameView"), false));
			}
			if (In->HasField(TEXT("realtime")))
			{
				Client->SetRealtime(JBool(In, TEXT("realtime"), true));
			}
		}
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
		const TSharedPtr<FJsonObject>* RotObj = nullptr;
		if (In->TryGetObjectField(TEXT("lookAt"), LookObj) && LookObj)
		{
			const TSharedRef<FJsonObject> O = LookObj->ToSharedRef();
			const FVector Target(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
			Client->SetViewRotation((Target - Loc).Rotation());
		}
		else if (In->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj)
		{
			const TSharedRef<FJsonObject> O = RotObj->ToSharedRef();
			// x/y/z = pitch/yaw/roll, matching every other MifBridge transform.
			Client->SetViewRotation(FRotator(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z"))));
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
		// 'all' is accepted and ignored: it is the documented (and actual) DEFAULT — omitting every
		// target frames the whole level — and callers following the header comment already pass it.
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPath"), TEXT("actor"), TEXT("folder"), TEXT("all"), TEXT("instant") },
			TEXT("actorPath (alias: actor) to frame ONE actor, folder to frame a folder subtree, all (or nothing at all) to frame the whole level, instant"),
			{ { TEXT("path"), TEXT("the actor key is 'actorPath' (alias: actor); it accepts an object path, an object name or a label") },
			  { TEXT("name"), TEXT("actorPath already matches on object name and label as well as full path - use it") },
			  { TEXT("bounds"), TEXT("'bounds' is an OUTPUT field - the framing target is actorPath, folder, or nothing for the whole level") } }))
		{
			return;
		}

		FLevelEditorViewportClient* Client = ActiveLevelViewport();
		if (!Client) { Fail(Out, TEXT("no level editor viewport available")); return; }
		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const FString ActorQuery = JStrAny(In, { TEXT("actorPath"), TEXT("actor") });
		const FString Folder = JStr(In, TEXT("folder"));

		FBox Bounds(ForceInit);
		int32 Counted = 0;

		if (!ActorQuery.IsEmpty())
		{
			AActor* A = FindActorInWorld(World, ActorQuery);
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
		if (RejectUnknownParams(In, Out, { TEXT("showFlags") },
			TEXT("showFlags - omit for the ~20 flags an agent usually wants, or pass \"all\" for ")
			TEXT("every one the engine knows"),
			{ { TEXT("viewportIndex"), TEXT("not supported - this always reports the ACTIVE viewport, falling back to the first perspective one; viewportCount in the response says how many exist") } }))
		{
			return;
		}

		FLevelEditorViewportClient* Client = ActiveLevelViewport();
		if (!Client) { Fail(Out, TEXT("no level editor viewport available")); return; }
		WriteCamera(Out, Client);
		Out->SetNumberField(TEXT("viewportCount"),
			GEditor ? GEditor->GetLevelViewportClients().Num() : 0);

		// The rendering state, which is what makes "why is it black" answerable.
		const EViewModeIndex Mode = Client->GetViewMode();
		Out->SetStringField(TEXT("viewMode"), MifViewModeName(Mode));
		Out->SetNumberField(TEXT("viewModeIndex"), static_cast<int32>(Mode));
		Out->SetBoolField(TEXT("gameView"), Client->IsInGameView());
		Out->SetBoolField(TEXT("realtime"), Client->IsRealtime());
		MifWriteShowFlags(Out, Client, JStr(In, TEXT("showFlags")).Equals(TEXT("all"), ESearchCase::IgnoreCase));
	}
}
