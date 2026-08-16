// MifBridge — static-mesh simple collision: remove_collision, add_simplified_collision.
//
// WHY THIS EXISTS. The StaticMeshEditor's collision toolbar is unreachable through
// invoke_editor_command. MifBridge caches FUICommandLists from FInputBindingManager::
// OnRegisterCommandList broadcasts, and an asset editor only broadcasts when it is actually
// OPENED. A pipeline that drives assets purely over HTTP never opens those tabs, so the
// StaticMeshEditor list is never cached and the command resolves but cannot execute
// ("no live FUICommandList maps it", MifBridgeUI.cpp:1075). The two workarounds that error
// suggests — a ToolMenus entry, or send_editor_key with the chord — both ALSO require the
// editor open, so neither helps a headless caller. Reported 2026-08-15.
//
// WHY NOT set_property ON BodySetup.AggGeom. It reads back as changed and is still wrong.
// FStaticMeshEditor::OnRemoveCollision (StaticMeshEditor.cpp:1836) does five things besides
// touching the geometry: FlushRenderingCommands (collision drawing may be reading it), a
// transacted BodySetup->Modify(), RefreshCollisionChange (pushes the new setup out to every
// UStaticMeshComponent instanced from the mesh), MarkPackageDirty, and bCustomizedCollision.
// A raw property write skips all of them, so the asset changes while nothing built from it
// notices. This file mirrors that sequence exactly.
//
// NO MODAL RISK — verified, not assumed. GeomFitUtils' generators open with
// PromptToRemoveExistingCollision, which sounds like a dialog and is the exact hazard
// GOTCHAS section 8 warns about (a modal stalls the game-thread ticker the HTTP server runs
// on). In this engine's source the FMessageDialog::Open call is COMMENTED OUT
// (GeomFitUtils.cpp:27-51), so the function only ever creates a BodySetup when one is missing
// and returns true unconditionally.
//
// THAT COMMENTED-OUT BLOCK IS ALSO WHY add_simplified_collision IS PURELY ADDITIVE. With the
// prompt gone, so is its RemoveSimpleCollision() call — so generating a box on a mesh that
// already has collision ADDS A SECOND BOX rather than replacing the first. Rather than hide a
// silent removal inside the add, the two halves stay separate and mirror what a human does in
// the editor: Remove Collision, then Add Box Simplified Collision.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"                        // GEditor - Begin/EndTransaction
#include "Engine/StaticMesh.h"
#include "PhysicsEngine/BodySetup.h"
#include "RenderingThread.h"               // FlushRenderingCommands
#include "UObject/Package.h"

// UnrealEd/Private — reached via PrivateIncludePaths in MifBridge.Build.cs, the same route
// already used for UMGEditor's private headers. The symbols are UNREALED_API so they link;
// only the header is private. Also supplies the KDopDir* direction tables (header-defined).
#include "GeomFitUtils.h"

namespace MifBridge
{
	/** Shared resolve: /Game/-only, must be a UStaticMesh, must have (or get) a BodySetup. */
	static UStaticMesh* ResolveStaticMeshForCollision(const TSharedRef<FJsonObject>& In,
		const TSharedRef<FJsonObject>& Out, const TCHAR* Endpoint)
	{
		const FString RawPath = JStr(In, TEXT("path"));
		if (RawPath.IsEmpty() || !RawPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, FString::Printf(TEXT("%s: path required, must start with /Game/"), Endpoint));
			return nullptr;
		}
		UObject* Asset = LoadAssetLenient(RawPath);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("%s: asset not found: %s"), Endpoint, *RawPath));
			return nullptr;
		}
		UStaticMesh* Mesh = Cast<UStaticMesh>(Asset);
		if (!Mesh)
		{
			Fail(Out, FString::Printf(
				TEXT("%s: %s is a %s, not a StaticMesh. Simple collision lives on UStaticMesh's "
				     "BodySetup; there is no equivalent for this asset type here."),
				Endpoint, *RawPath, *Asset->GetClass()->GetName()));
			return nullptr;
		}
		return Mesh;
	}

	//   in:  { path: "/Game/.../SM_Foo", confirm: true }
	//   out: { path, removedPrimitives, hadCollision }
	//
	// confirm-gated because hand-authored convex hulls are real work and this destroys them
	// with no undo across an HTTP boundary — same gate as delete_asset / rename_asset.
	void H_remove_collision(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("confirm") },
			TEXT("path (a UStaticMesh), confirm (required true)"),
			{ { TEXT("objectPath"), TEXT("spell it path") },
			  { TEXT("mesh"), TEXT("spell it path") },
			  { TEXT("shape"), TEXT("remove_collision takes no shape - it clears ALL simple collision. Use add_simplified_collision to add one back") } }))
		{
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, TEXT("remove_collision requires confirm=true (it destroys hand-authored collision primitives)"));
			return;
		}

		UStaticMesh* Mesh = ResolveStaticMeshForCollision(In, Out, TEXT("remove_collision"));
		if (!Mesh)
		{
			return;
		}

		UBodySetup* BS = Mesh->GetBodySetup();
		const int32 Before = BS ? BS->AggGeom.GetElementCount() : 0;
		if (!BS || Before == 0)
		{
			// Not an error: the caller's goal ("this mesh has no simple collision") already holds.
			Out->SetStringField(TEXT("path"), NormalizePackagePath(JStr(In, TEXT("path"))));
			Out->SetNumberField(TEXT("removedPrimitives"), 0);
			Out->SetBoolField(TEXT("hadCollision"), false);
			return;
		}

		// Order copied from FStaticMeshEditor::OnRemoveCollision - the flush comes FIRST because
		// collision debug drawing may still be reading the geometry on the render thread.
		FlushRenderingCommands();

		GEditor->BeginTransaction(NSLOCTEXT("MifBridge", "MifBridge_RemoveCollision", "Remove Collision"));
		BS->Modify();
		BS->RemoveSimpleCollision();
		GEditor->EndTransaction();

		RefreshCollisionChange(*Mesh);      // push the change out to every component instanced from this mesh
		Mesh->MarkPackageDirty();
#if WITH_EDITORONLY_DATA
		Mesh->bCustomizedCollision = true;
#endif

		Out->SetStringField(TEXT("path"), NormalizePackagePath(JStr(In, TEXT("path"))));
		Out->SetNumberField(TEXT("removedPrimitives"), Before);
		Out->SetBoolField(TEXT("hadCollision"), true);
		UE_LOG(LogMifBridge, Log, TEXT("remove_collision: %s (%d primitive(s))"), *Mesh->GetPathName(), Before);
	}

	//   in:  { path: "/Game/.../SM_Foo", shape: "box" }
	//   out: { path, shape, primitivesBefore, primitivesAfter, added }
	//
	// ADDITIVE - see the file header. Call remove_collision first to replace rather than stack.
	void H_add_simplified_collision(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("shape") },
			TEXT("path (a UStaticMesh), shape (box|sphere|capsule|10dop-x|10dop-y|10dop-z|18dop|26dop)"),
			{ { TEXT("objectPath"), TEXT("spell it path") },
			  { TEXT("type"), TEXT("spell it shape") },
			  { TEXT("replace"), TEXT("there is no replace - this endpoint is additive. Call remove_collision first (the engine's own replace path is commented out in GeomFitUtils.cpp, so generating over existing collision would silently stack a second primitive)") },
			  { TEXT("sphyl"), TEXT("spell it shape=capsule") } }))
		{
			return;
		}

		UStaticMesh* Mesh = ResolveStaticMeshForCollision(In, Out, TEXT("add_simplified_collision"));
		if (!Mesh)
		{
			return;
		}

		const FString Shape = JStr(In, TEXT("shape")).ToLower();
		if (Shape.IsEmpty())
		{
			Fail(Out, TEXT("add_simplified_collision requires shape: box|sphere|capsule|10dop-x|10dop-y|10dop-z|18dop|26dop"));
			return;
		}

		UBodySetup* BSBefore = Mesh->GetBodySetup();
		const int32 Before = BSBefore ? BSBefore->AggGeom.GetElementCount() : 0;

		FlushRenderingCommands();
		GEditor->BeginTransaction(NSLOCTEXT("MifBridge", "MifBridge_AddCollision", "Add Simplified Collision"));

		// Each generator does its own bs->Modify() inside the transaction we just opened, and
		// returns the new element's index (or INDEX_NONE). The k-DOP direction tables are the
		// engine's own, defined in GeomFitUtils.h - not re-derived here, so a k-DOP from this
		// endpoint is identical to one from the toolbar button.
		int32 Result = INDEX_NONE;
		if (Shape == TEXT("box"))          { Result = GenerateBoxAsSimpleCollision(Mesh); }
		else if (Shape == TEXT("sphere"))  { Result = GenerateSphereAsSimpleCollision(Mesh); }
		else if (Shape == TEXT("capsule")) { Result = GenerateSphylAsSimpleCollision(Mesh); }
		else
		{
			const FVector* Dirs = nullptr;
			int32 DirCount = 0;
			if      (Shape == TEXT("10dop-x")) { Dirs = KDopDir10X; DirCount = 10; }
			else if (Shape == TEXT("10dop-y")) { Dirs = KDopDir10Y; DirCount = 10; }
			else if (Shape == TEXT("10dop-z")) { Dirs = KDopDir10Z; DirCount = 10; }
			else if (Shape == TEXT("18dop"))   { Dirs = KDopDir18;  DirCount = 18; }
			else if (Shape == TEXT("26dop"))   { Dirs = KDopDir26;  DirCount = 26; }

			if (!Dirs)
			{
				GEditor->EndTransaction();
				Fail(Out, FString::Printf(
					TEXT("unknown shape '%s'. Accepted: box, sphere, capsule, 10dop-x, 10dop-y, 10dop-z, 18dop, 26dop"),
					*Shape));
				return;
			}
			TArray<FVector> DirArray;
			DirArray.Append(Dirs, DirCount);
			Result = GenerateKDopAsSimpleCollision(Mesh, DirArray);
		}

		GEditor->EndTransaction();

		if (Result == INDEX_NONE)
		{
			Fail(Out, FString::Printf(
				TEXT("shape '%s' produced no collision primitive on %s (generator returned INDEX_NONE)"),
				*Shape, *Mesh->GetPathName()));
			return;
		}

		RefreshCollisionChange(*Mesh);
		Mesh->MarkPackageDirty();
#if WITH_EDITORONLY_DATA
		Mesh->bCustomizedCollision = true;
#endif

		UBodySetup* BSAfter = Mesh->GetBodySetup();
		const int32 After = BSAfter ? BSAfter->AggGeom.GetElementCount() : 0;

		Out->SetStringField(TEXT("path"), NormalizePackagePath(JStr(In, TEXT("path"))));
		Out->SetStringField(TEXT("shape"), Shape);
		Out->SetNumberField(TEXT("primitivesBefore"), Before);
		Out->SetNumberField(TEXT("primitivesAfter"), After);
		Out->SetNumberField(TEXT("added"), After - Before);
		UE_LOG(LogMifBridge, Log, TEXT("add_simplified_collision: %s shape=%s (%d -> %d primitive(s))"),
			*Mesh->GetPathName(), *Shape, Before, After);
	}
}
