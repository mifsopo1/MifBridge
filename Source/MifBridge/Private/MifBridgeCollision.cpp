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
#include "StaticMeshEditorSubsystem.h"
#include "StaticMeshEditorSubsystemHelpers.h"

// list_collision_profiles / set_collision. UCollisionProfile is the ONLY authority on what a profile
// name means in this project - DDS2 defines its own in DefaultEngine.ini, and a name that is not in
// there is accepted by every generic setter and silently means nothing.
#include "Engine/CollisionProfile.h"
#include "Components/PrimitiveComponent.h"
#include "MifBridgeLog.h"
#include "MifBridgeVersion.h"               // MIF_ENGINE_AT_LEAST - SetCustomizedCollision, 5.7-only

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
	namespace
	{
		const TCHAR* CollisionEnabledName(ECollisionEnabled::Type E)
		{
			switch (E)
			{
			case ECollisionEnabled::NoCollision:          return TEXT("NoCollision");
			case ECollisionEnabled::QueryOnly:            return TEXT("QueryOnly");
			case ECollisionEnabled::PhysicsOnly:          return TEXT("PhysicsOnly");
			case ECollisionEnabled::QueryAndPhysics:      return TEXT("QueryAndPhysics");
			case ECollisionEnabled::ProbeOnly:            return TEXT("ProbeOnly");
			case ECollisionEnabled::QueryAndProbe:        return TEXT("QueryAndProbe");
			default:                                      return TEXT("(unknown)");
			}
		}

		const TCHAR* CollisionResponseName(ECollisionResponse R)
		{
			switch (R)
			{
			case ECR_Ignore:  return TEXT("Ignore");
			case ECR_Overlap: return TEXT("Overlap");
			case ECR_Block:   return TEXT("Block");
			default:          return TEXT("(unknown)");
			}
		}

		// The responses a profile RESOLVES to, which is the thing a caller actually cares about and
		// cannot see from the profile name alone.
		TSharedRef<FJsonObject> ResponsesJson(const FCollisionResponseContainer& C)
		{
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			const UEnum* ChannelEnum = StaticEnum<ECollisionChannel>();
			for (int32 i = 0; i < ECC_MAX; ++i)
			{
				const ECollisionChannel Ch = static_cast<ECollisionChannel>(i);
				const FName DisplayName = UCollisionProfile::Get()->ReturnChannelNameFromContainerIndex(i);
				if (DisplayName.IsNone()) { continue; }
				J->SetStringField(DisplayName.ToString(),
					CollisionResponseName(static_cast<ECollisionResponse>(C.EnumArray[i])));
			}
			return J;
		}
	}

	// --- list_collision_profiles ---------------------------------------------
	//   in:  { }
	//   out: { count, profiles:[{name, collisionEnabled, objectType, responses:{...}}] }
	//
	// A caller guessing "BlockAll" when the project defines its own profiles has no way to find out
	// otherwise, and set_property accepts any string. This is the authority.
	void H_list_collision_profiles(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { },
			TEXT("(no parameters)"),
			{ { TEXT("actorPath"), TEXT("this lists the PROJECT's profiles, not one object's - read an object's current profile with get_property on BodyInstance.CollisionProfileName") } }))
		{
			return;
		}

		TArray<TSharedPtr<FName>> Names;
		UCollisionProfile::GetProfileNames(Names);
		TArray<TSharedPtr<FJsonValue>> Arr;
		for (const TSharedPtr<FName>& N : Names)
		{
			if (!N.IsValid()) { continue; }
			FCollisionResponseTemplate T;
			if (!UCollisionProfile::Get()->GetProfileTemplate(*N, T)) { continue; }
			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("name"), T.Name.ToString());
			J->SetStringField(TEXT("collisionEnabled"), CollisionEnabledName(T.CollisionEnabled));
			J->SetStringField(TEXT("objectType"), T.ObjectTypeName.ToString());
			J->SetObjectField(TEXT("responses"), ResponsesJson(T.ResponseToChannels));
			// A NoCollision profile still carries a full response container, so its "responses" read
			// as though it blocks things. It does not - collisionEnabled decides that, and the
			// responses only apply once collision is on. Say so rather than let the table mislead.
			if (T.CollisionEnabled == ECollisionEnabled::NoCollision)
			{
				J->SetBoolField(TEXT("responsesAreMoot"), true);
				J->SetStringField(TEXT("note"),
					TEXT("collisionEnabled is NoCollision, so the responses below never apply - they "
						 "are what this profile WOULD do if collision were enabled"));
			}
			Arr.Add(MakeShared<FJsonValueObject>(J));
		}
		Out->SetNumberField(TEXT("count"), Arr.Num());
		Out->SetArrayField(TEXT("profiles"), Arr);
		Out->SetStringField(TEXT("note"),
			TEXT("these are the profiles THIS project defines (DefaultEngine.ini). set_collision "
				 "validates against exactly this list; set_property does not, and will accept a name "
				 "that means nothing."));
	}

	// --- set_collision -------------------------------------------------------
	//   in:  { objectPath, profile? , collisionEnabled? }
	//   out: { objectPath, profile, collisionEnabled, responses:{...} }
	//
	// The same write set_property can do, with the profile name CHECKED - and the resolved channel
	// responses reported back, because "the profile is set" and "it now blocks what I meant" are
	// different claims.
	void H_set_collision(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("objectPath"), TEXT("component"), TEXT("profile"), TEXT("collisionEnabled") },
			TEXT("objectPath (a component's templatePath from list_components, or a placed actor's "
				 "component path), profile (validated against list_collision_profiles), "
				 "collisionEnabled (NoCollision|QueryOnly|PhysicsOnly|QueryAndPhysics)"),
			{ { TEXT("channel"), TEXT("per-channel responses come from the PROFILE - pick a profile that has the responses you want, and list_collision_profiles shows what each resolves to") },
			  { TEXT("blueprintId"), TEXT("collision lives on a COMPONENT: call list_components, take its templatePath, and pass that as objectPath") } }))
		{
			return;
		}

		const FString ObjPath = JStrAny(In, { TEXT("objectPath"), TEXT("component") });
		if (ObjPath.IsEmpty())
		{
			Fail(Out, TEXT("objectPath is required - a component's templatePath from list_components. "
						   "NOTHING was changed."));
			return;
		}
		UObject* Obj = FindObject<UObject>(nullptr, *ObjPath);
		if (!Obj)
		{
			Obj = LoadAssetLenient(ObjPath);
		}
		UPrimitiveComponent* Prim = Cast<UPrimitiveComponent>(Obj);
		if (!Prim)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is %s, not a PrimitiveComponent - only primitives have collision. "
					 "NOTHING was changed."),
				*ObjPath, Obj ? *FString::Printf(TEXT("a %s"), *Obj->GetClass()->GetName()) : TEXT("not found")));
			return;
		}

		const bool bWantProfile = In->HasField(TEXT("profile"));
		const bool bWantEnabled = In->HasField(TEXT("collisionEnabled"));
		if (!bWantProfile && !bWantEnabled)
		{
			Fail(Out, TEXT("pass profile and/or collisionEnabled. NOTHING was changed."));
			return;
		}

		// BOTH VALUES CHECKED BEFORE EITHER IS WRITTEN. Until 2026-09-04 the profile was applied
		// first and the mode parsed afterwards, so
		//     {"profile":"BlockAll","collisionEnabled":"Nonsense"}
		// moved the component to BlockAll and then refused with "NOTHING was changed." The wrapper
		// half was fixed when this was found - it used to send collisionEnabled by default - but
		// that made the case rarer rather than impossible: any caller passing both keys and getting
		// the second one wrong still hit it.
		//
		// Prim->Modify() was one step earlier still, ahead of BOTH checks, so even the careful
		// unknown-profile refusal below was promising about a component already recorded into the
		// transaction.
		FName ProfileName;
		if (bWantProfile)
		{
			const FString Profile = JStr(In, TEXT("profile"));
			// THE CHECK THAT SET_PROPERTY DOES NOT DO. An unknown name is accepted by the raw setter
			// and reads straight back, leaving the component on whatever it had before - configured
			// in every read path and colliding with the wrong things.
			FCollisionResponseTemplate T;
			if (!UCollisionProfile::Get()->GetProfileTemplate(FName(*Profile), T))
			{
				TArray<TSharedPtr<FName>> Names;
				UCollisionProfile::GetProfileNames(Names);
				TArray<FString> Have;
				for (const TSharedPtr<FName>& N : Names) { if (N.IsValid()) { Have.Add(N->ToString()); } }
				Fail(Out, FString::Printf(
					TEXT("'%s' is not a collision profile in THIS project, and setting it would have "
						 "left the component on its previous collision while reading back as though it "
						 "had changed. Known profiles: %s. NOTHING was changed."),
					*Profile, *FString::Join(Have, TEXT(", "))));
				return;
			}
			ProfileName = FName(*Profile);
		}

		ECollisionEnabled::Type Mode = ECollisionEnabled::QueryAndPhysics;
		if (bWantEnabled)
		{
			const FString E = JStr(In, TEXT("collisionEnabled"));
			if (E == TEXT("NoCollision"))          { Mode = ECollisionEnabled::NoCollision; }
			else if (E == TEXT("QueryOnly"))       { Mode = ECollisionEnabled::QueryOnly; }
			else if (E == TEXT("PhysicsOnly"))     { Mode = ECollisionEnabled::PhysicsOnly; }
			else if (E == TEXT("QueryAndPhysics")) { Mode = ECollisionEnabled::QueryAndPhysics; }
			else
			{
				Fail(Out, FString::Printf(
					TEXT("collisionEnabled '%s' is not one of NoCollision, QueryOnly, PhysicsOnly, "
						 "QueryAndPhysics. NOTHING was changed."), *E));
				return;
			}
		}

		// --- Apply. Nothing below here can refuse. -------------------------------
		Prim->Modify();
		if (bWantProfile) { Prim->SetCollisionProfileName(ProfileName); }
		if (bWantEnabled) { Prim->SetCollisionEnabled(Mode); }

		Prim->MarkPackageDirty();

		// Report what it RESOLVED to, not what was asked for. The profile name alone does not tell a
		// caller whether the thing now blocks the player.
		Out->SetStringField(TEXT("objectPath"), Prim->GetPathName());
		Out->SetStringField(TEXT("profile"), Prim->GetCollisionProfileName().ToString());
		Out->SetStringField(TEXT("collisionEnabled"), CollisionEnabledName(Prim->GetCollisionEnabled()));
		Out->SetStringField(TEXT("objectType"),
			UCollisionProfile::Get()->ReturnChannelNameFromContainerIndex(Prim->GetCollisionObjectType()).ToString());
		Out->SetObjectField(TEXT("responses"), ResponsesJson(Prim->GetCollisionResponseToChannels()));
	}

	/** Shared resolve: /Game/-only, must be a UStaticMesh, must have (or get) a BodySetup. */
	// bAllowAnyMount: /Game/ ONLY for writes, any mount point for reads.
	//
	// The /Game/ restriction is right for the three mutating endpoints this resolver was written for -
	// nothing here should be modifying /Engine/ or a plugin's content, and refusing outright is a
	// better guard than hoping nobody passes one.
	//
	// It is wrong for a READ. get_collision on /Engine/EngineMeshes/Sphere is harmless and is exactly
	// the kind of thing someone does to see what correct collision looks like. Worse, inheriting the
	// guard made the read untestable: the scratch world has no /Game/ StaticMesh at all, so every
	// available mesh was refused and the endpoint could not be exercised against anything.
	//
	// A guard copied from a mutation into a read is a guard that has stopped protecting anything and
	// started costing something.
	static UStaticMesh* ResolveStaticMeshForCollision(const TSharedRef<FJsonObject>& In,
		const TSharedRef<FJsonObject>& Out, const TCHAR* Endpoint, bool bAllowAnyMount = false)
	{
		const FString RawPath = JStrAny(In, { TEXT("path"), TEXT("assetPath"),
											  TEXT("mesh"), TEXT("staticMesh") });
		if (RawPath.IsEmpty())
		{
			Fail(Out, FString::Printf(TEXT("%s: path required"), Endpoint));
			return nullptr;
		}
		if (!bAllowAnyMount && !RawPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, FString::Printf(
				TEXT("%s: path must start with /Game/ - this endpoint MODIFIES the mesh, and engine or "
					 "plugin content is not ours to change. get_collision READS any mount point."),
				Endpoint));
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
		// bCustomizedCollision is UE_DEPRECATED(5.7, "...it will become private soon; use
		// UStaticMesh::GetCustomizedCollision() or UStaticMesh::SetCustomizedCollision()."), but
		// SetCustomizedCollision does not exist at all on 5.3 (confirmed by grep of D:/UE532's
		// StaticMesh.h) - the direct field is the only option there.
#if MIF_ENGINE_AT_LEAST(5, 7)
		Mesh->SetCustomizedCollision(true);
#else
		Mesh->bCustomizedCollision = true;
#endif
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

		// THE CRASH GUARD, and which shapes it actually applies to.
		//
		// box/sphere/capsule fit against MeshDescription. GenerateBoxAsSimpleCollision dereferences
		// it directly with NO null check ("GetMeshDescription(0)->ComputeBoundingBox()"), and the
		// sphere/capsule path hands the same possibly-null pointer into CalcBoundingSphere, which
		// dereferences it on its first line. On a COOKED static mesh that bulk data is stripped and
		// GetMeshDescription(0) returns null - found live 2026-08-28, EXCEPTION_ACCESS_VIOLATION
		// reading address 0x50 inside UnrealEditor-MeshDescription.dll.
		//
		// THE k-DOP SHAPES DO NOT. Corrected 2026-08-30: the original guard refused all eight shapes
		// and its comment claimed "every shape generator here needs it", which is false in both
		// engines. GenerateKDopAsSimpleCollision fits its hull from RENDER data -
		// `StaticMesh->GetRenderData()->LODResources[0]`, then LODResources[0].GetNumVertices() and
		// VertexBuffers.PositionVertexBuffer (GeomFitUtils.cpp:24-29 on 5.3) - which every cooked
		// mesh has, because it is what the mesh is drawn from. So the six k-DOP shapes could never
		// crash on a cooked mesh and were being refused anyway, which took simplified collision away
		// from cooked projects entirely for no reason. They are the more useful shapes on real props
		// besides.
		//
		// Checked against the literal thing about to be dereferenced in each path, not inferred from
		// PKG_Cooked.
		const bool bNeedsMeshDescription =
			Shape == TEXT("box") || Shape == TEXT("sphere") || Shape == TEXT("capsule");

		if (bNeedsMeshDescription && !Mesh->GetMeshDescription(0))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no MeshDescription (editor-only geometry data, stripped on cook), and "
					 "the '%s' generator fits against it - CRASHES the editor with "
					 "EXCEPTION_ACCESS_VIOLATION otherwise (GeomFitUtils.cpp dereferences "
					 "GetMeshDescription(0) with no null check). Refused rather than attempted. "
					 "The k-DOP shapes (10dop-x, 10dop-y, 10dop-z, 18dop, 26dop) DO work on this "
					 "mesh - they fit from render data, which cooking keeps - so try one of those. "
					 "remove_collision also still works (it only touches BodySetup, not geometry)."),
				*JStr(In, TEXT("path")), *Shape));
			return;
		}

		// k-DOP's own precondition. Nothing has been observed to hit this - a StaticMesh with no
		// render data does not draw - but it is the pointer that path dereferences, so it is checked
		// rather than assumed, for the same reason the MeshDescription one is.
		if (!bNeedsMeshDescription && (!Mesh->GetRenderData() || Mesh->GetRenderData()->LODResources.Num() == 0))
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' has no render data (LODResources is empty), which the k-DOP generator fits "
					 "its hull from. NOTHING was changed."),
				*JStr(In, TEXT("path"))));
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
		// bCustomizedCollision is UE_DEPRECATED(5.7, "...it will become private soon; use
		// UStaticMesh::GetCustomizedCollision() or UStaticMesh::SetCustomizedCollision()."), but
		// SetCustomizedCollision does not exist at all on 5.3 (confirmed by grep of D:/UE532's
		// StaticMesh.h) - the direct field is the only option there.
#if MIF_ENGINE_AT_LEAST(5, 7)
		Mesh->SetCustomizedCollision(true);
#else
		Mesh->bCustomizedCollision = true;
#endif
#endif

		UBodySetup* BSAfter = Mesh->GetBodySetup();
		const int32 After = BSAfter ? BSAfter->AggGeom.GetElementCount() : 0;

		Out->SetStringField(TEXT("path"), NormalizePackagePath(JStr(In, TEXT("path"))));
		Out->SetStringField(TEXT("shape"), Shape);
		Out->SetNumberField(TEXT("primitivesBefore"), Before);
		Out->SetNumberField(TEXT("primitivesAfter"), After);
		Out->SetNumberField(TEXT("added"), After - Before);

		// ADDING NOTHING IS NOT ADDING. Same shape as docs/06 issue 18 and 19: the count was correct
		// and ok stayed true beside it, so a caller checking the status rather than the arithmetic saw
		// a collision primitive that does not exist.
		//
		// UStaticMesh's generation calls do not report failure - they either produce geometry or
		// quietly produce none (a degenerate mesh, a shape the generator cannot fit). The count IS the
		// only signal there is, which makes ignoring it worse rather than more forgivable.
		if (After <= Before)
		{
			Fail(Out, FString::Printf(
				TEXT("add_simplified_collision added NOTHING: the mesh had %d collision primitive(s) "
					 "before and %d after. The engine's generator does not report failure - it either "
					 "produces geometry or quietly produces none, usually for a degenerate mesh or a "
					 "shape it cannot fit. Nothing was changed."), Before, After));
			return;
		}
		UE_LOG(LogMifBridge, Log, TEXT("add_simplified_collision: %s shape=%s (%d -> %d primitive(s))"),
			*Mesh->GetPathName(), *Shape, Before, After);
	}

	// --- get_collision --------------------------------------------------------------------------
	//   in:  { path (aliases: assetPath, mesh, staticMesh), lod? = 0 }
	//   out: { simpleCollisionCount, convexCollisionCount, collisionComplexity, hasBodySetup,
	//          sections[ { index, collisionEnabled } ], ... }
	// Bucket: READ. Loads the mesh; changes nothing.
	//
	// WHY THIS WAS MISSING AND WHY THAT MATTERED. The collision family could add, remove and configure
	// - add_simplified_collision, remove_collision, set_collision - and could not SEE. list_collision_
	// profiles sounds like the read half and is not: it lists the project's collision PROFILE names,
	// which is a different question entirely and has nothing to do with any particular mesh.
	//
	// add_simplified_collision already had to count primitives before and after to tell whether the
	// engine's generator did anything, because that generator reports no failure - it either produces
	// geometry or quietly produces none. It was doing that against an internal read no caller could
	// make. So a caller could ask for collision, be told it worked, and have no way to check what they
	// got. That is the exact shape this project keeps finding, one step removed.
	//
	// Verified in BOTH trees before writing:
	//   GetSimpleCollisionCount     5.3 StaticMeshEditorSubsystem.h:226   5.7 :259
	//   GetCollisionComplexity      5.3 :234                              5.7 :267
	//   GetConvexCollisionCount     5.3 :243                              5.7 :276
	//   IsSectionCollisionEnabled   5.3 :328                              5.7 :387
	//
	// The only difference is declaration-side and needs no guard: 5.7 marks the parameter `const` and
	// carries per-member STATICMESHEDITOR_API (the class went UCLASS() -> UCLASS(MinimalAPI)). A
	// non-const UStaticMesh* converts implicitly, so one spelling compiles on both.
	void H_get_collision(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("staticMesh"), TEXT("lod") },
			TEXT("path (aliases: assetPath, mesh, staticMesh) - a StaticMesh asset; lod (default 0) - "
				 "which LOD's sections to report"),
			{ { TEXT("profile"), TEXT("collision PROFILES are a project-wide list - list_collision_profiles reports those. This reads one mesh's own collision.") },
			  { TEXT("actorPath"), TEXT("this reads the MESH ASSET, not a placed actor. A component's collision overrides are a different question - get_property on the component reads those.") } }))
		{
			return;
		}

		// Any mount point: this is a read. See the note on the resolver.
		UStaticMesh* Mesh = ResolveStaticMeshForCollision(In, Out, TEXT("get_collision"),
			/*bAllowAnyMount=*/true);
		if (!Mesh) { return; }

		// STRAIGHT OFF THE BodySetup, not through UStaticMeshEditorSubsystem.
		//
		// The subsystem has GetSimpleCollisionCount / GetConvexCollisionCount / GetCollisionComplexity
		// (5.3 :226/:243/:234, 5.7 :259/:276/:267, differing only by a `const` on the parameter), and
		// using them would mean adding a StaticMeshEditor module dependency this plugin does not
		// currently carry - a new engine-version surface, and a new way for the 5.7 build to break -
		// to reach numbers that are one dereference away.
		//
		// Those subsystem functions ARE these expressions. add_simplified_collision in this same file
		// already counts AggGeom.GetElementCount() before and after for exactly this reason, so the
		// approach is proven here rather than assumed.
		const UBodySetup* BS = Mesh->GetBodySetup();
		const int32 Simple = BS ? BS->AggGeom.GetElementCount() : 0;
		const int32 Convex = BS ? BS->AggGeom.ConvexElems.Num() : 0;
		const TEnumAsByte<ECollisionTraceFlag> Complexity =
			BS ? BS->CollisionTraceFlag : TEnumAsByte<ECollisionTraceFlag>(CTF_UseDefault);

		Out->SetStringField(TEXT("assetPath"), Mesh->GetPathName());
		Out->SetNumberField(TEXT("simpleCollisionCount"), Simple);
		Out->SetNumberField(TEXT("convexCollisionCount"), Convex);

		// The ENUM NAME, not its integer. A caller comparing against set_collision's input needs the
		// name, and an integer here would make them look up a mapping this bridge never published.
		// SPELLED OUT rather than via StaticEnum<ECollisionTraceFlag>(). That template COMPILES here
		// and fails at LINK - unresolved external, because the enum's reflection symbol is not
		// exported to this module. It is the same class of trap docs/02 section 14 records for
		// UCLASS(MinimalAPI) members: nothing is visibly wrong until the linker runs, and the error
		// names a symbol rather than a mistake.
		//
		// Four values, stable since UE4, and a switch cannot fail to link. If the engine ever adds
		// one, the default arm reports the raw number rather than an empty string, so a new value
		// shows up as unrecognised instead of as absent.
		const TCHAR* ComplexityName = TEXT("");
		switch (Complexity.GetValue())
		{
		case CTF_UseDefault:          ComplexityName = TEXT("CTF_UseDefault");          break;
		case CTF_UseSimpleAndComplex: ComplexityName = TEXT("CTF_UseSimpleAndComplex"); break;
		case CTF_UseSimpleAsComplex:  ComplexityName = TEXT("CTF_UseSimpleAsComplex");  break;
		case CTF_UseComplexAsSimple:  ComplexityName = TEXT("CTF_UseComplexAsSimple");  break;
		default:                      ComplexityName = TEXT("");                        break;
		}
		Out->SetStringField(TEXT("collisionComplexity"),
			FString(ComplexityName).IsEmpty()
				? FString::Printf(TEXT("(unrecognised: %d)"), (int32)Complexity.GetValue())
				: FString(ComplexityName));

		// BodySetup is where simple collision actually lives. Reporting its absence separately from a
		// zero count distinguishes "this mesh has no collision" from "this mesh has no collision
		// container at all", which are different problems with different fixes.
		const bool bHasBodySetup = BS != nullptr;
		Out->SetBoolField(TEXT("hasBodySetup"), bHasBodySetup);

		const int32 NumLods = Mesh->GetNumLODs();
		int32 Lod = (int32)JNum(In, TEXT("lod"), 0.0);
		Out->SetNumberField(TEXT("lodCount"), NumLods);
		if (Lod < 0 || Lod >= NumLods)
		{
			// Said rather than clamped. A silently clamped LOD index reports another LOD's sections
			// under the number the caller asked for, which is a wrong answer wearing a right one's
			// clothes.
			Fail(Out, FString::Printf(
				TEXT("lod %d does not exist - this mesh has %d LOD(s), so valid indices are 0..%d. "
					 "The collision counts above are whole-mesh and are correct regardless."),
				Lod, NumLods, NumLods - 1));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Sections;
		int32 EnabledCount = 0;
		const int32 NumSections = Mesh->GetNumSections(Lod);
		for (int32 i = 0; i < NumSections; ++i)
		{
			// FMeshSectionInfoMap is what IsSectionCollisionEnabled reads (5.3 :328, 5.7 :387) -
			// same answer, no extra module.
			const bool bEnabled = Mesh->GetSectionInfoMap().Get(Lod, i).bEnableCollision;
			if (bEnabled) { ++EnabledCount; }
			TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
			S->SetNumberField(TEXT("index"), i);
			S->SetBoolField(TEXT("collisionEnabled"), bEnabled);
			Sections.Add(MakeShared<FJsonValueObject>(S));
		}
		Out->SetNumberField(TEXT("lod"), Lod);
		Out->SetArrayField(TEXT("sections"), Sections);
		Out->SetNumberField(TEXT("sectionsWithCollision"), EnabledCount);

		// The verdict a caller is really asking for, stated once rather than left to be assembled from
		// four fields. Complex-as-simple means the render mesh IS the collision, so a zero primitive
		// count is correct there and alarming anywhere else.
		const bool bUsesComplex = Complexity == ECollisionTraceFlag::CTF_UseComplexAsSimple;
		if (bUsesComplex)
		{
			Out->SetStringField(TEXT("verdict"),
				TEXT("complex-as-simple: the render geometry IS the collision, so simple primitives "
					 "are unused and a count of 0 is expected. This does NOT work for a moving or "
					 "simulating body - those need simple collision."));
		}
		else if (Simple + Convex == 0)
		{
			Out->SetStringField(TEXT("verdict"),
				TEXT("NO COLLISION: no simple primitives, no convex hulls, and the complexity flag is "
					 "not complex-as-simple. Traces and overlaps against this mesh will find nothing. "
					 "add_simplified_collision generates primitives."));
		}
		else
		{
			Out->SetStringField(TEXT("verdict"), FString::Printf(
				TEXT("%d simple primitive(s) and %d convex hull(s)."), Simple, Convex));
		}
	}

	// =======================================================================
	// generate_lods / remove_lods - the ONE LOD capability that was missing
	// =======================================================================
	//
	// SCOPE, CUT AFTER CHECKING. The survey asked for LOD count, per-LOD build settings, per-LOD
	// reduction settings, the LOD group and Nanite settings. Three of those five already work
	// through set_property, and the reason is worth writing down because it is not obvious:
	// UStaticMesh::LODGroup is a public UPROPERTY(EditAnywhere), and PostEditChangeProperty
	// SPECIAL-CASES it (StaticMesh.cpp:3984-3991) by calling SetLODGroup, which resizes the source
	// models to the group default and rewrites every per-LOD reduction setting before building. So
	// set_property already adds, removes and retunes LODs through a group, and ResolvePropertyPathEx
	// reaches the per-LOD structs directly besides.
	//
	// What has NO reflective equivalent is setting an ARBITRARY LOD count with explicit reduction
	// percentages - SetLodsWithNotification drives the mesh reduction interface, which is code
	// rather than data. That is this endpoint, and nothing more.
	//
	// THE BUILD ASSERT APPLIES HERE TOO, and harder. Every one of these calls rebuilds the mesh,
	// and the build path asserts checkf(Owner->IsMeshDescriptionValid(0)) (StaticMesh.cpp:3086) -
	// process termination, not an error. The same condition set_property was guarded against on
	// 2026-08-30 is checked here before the subsystem is touched.
	//
	// PERCENTTRIANGLES IS A FRACTION, NOT A PERCENTAGE, whatever its name says: the struct's own
	// comment is "Ranges from 0.0 to 1.0: 1.0 = no reduction". Passing 50 meaning "50%" would ask
	// for fifty times the triangles and silently get clamped. Values above 1 are refused by name.

	static bool MifLodMeshWouldAssert(UStaticMesh* Mesh, FString& OutReason)
	{
		if (!Mesh) { return false; }
		if (Mesh->GetNumSourceModels() <= 0) { return false; }
		if (Mesh->GetMeshDescription(0) != nullptr) { return false; }
		const UPackage* Pkg = Mesh->GetOutermost();
		OutReason = FString::Printf(
			TEXT("'%s' has %d source model(s) and NO editable MeshDescription%s. Every LOD operation "
				 "rebuilds the mesh, and the build path asserts "
				 "checkf(IsMeshDescriptionValid(0)) - that TERMINATES the editor rather than "
				 "returning an error. NOTHING was changed."),
			*Mesh->GetPathName(), Mesh->GetNumSourceModels(),
			(Pkg && Pkg->HasAnyPackageFlags(PKG_Cooked))
				? TEXT(", because it came from a COOKED package and cook strips that data")
				: TEXT(""));
		return true;
	}

	static UStaticMesh* MifResolveLodMesh(const TSharedRef<FJsonObject>& In,
										  const TSharedRef<FJsonObject>& Out)
	{
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("mesh") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - a StaticMesh asset. NOTHING was changed."));
			return nullptr;
		}
		UObject* Asset = LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: '%s'. NOTHING was changed."), *Path));
			return nullptr;
		}
		UStaticMesh* Mesh = Cast<UStaticMesh>(Asset);
		if (!Mesh)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, not a StaticMesh. NOTHING was changed."),
				*Path, *Asset->GetClass()->GetName()));
			return nullptr;
		}
		FString Reason;
		if (MifLodMeshWouldAssert(Mesh, Reason)) { Fail(Out, Reason); return nullptr; }
		return Mesh;
	}

	static UStaticMeshEditorSubsystem* MifStaticMeshSubsystem(const TSharedRef<FJsonObject>& Out)
	{
		UStaticMeshEditorSubsystem* Sub =
			GEditor ? GEditor->GetEditorSubsystem<UStaticMeshEditorSubsystem>() : nullptr;
		if (!Sub)
		{
			Fail(Out, TEXT("no StaticMeshEditorSubsystem - this is not a running editor."));
		}
		return Sub;
	}

	void H_generate_lods(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("lodCount"),
			  TEXT("reductionPercentages"), TEXT("screenSizes"), TEXT("autoScreenSize"),
			  TEXT("confirm") },
			TEXT("path (aliases assetPath, mesh); lodCount (total LODs including LOD0); ")
			TEXT("reductionPercentages[] - FRACTIONS 0..1, one per LOD, 1.0 = no reduction; ")
			TEXT("screenSizes[] (only with autoScreenSize:false); autoScreenSize (default true); ")
			TEXT("confirm:true"),
			{ { TEXT("lodGroup"), TEXT("already reachable - set_property on LODGroup, which the "
									   "engine special-cases to resize and retune every LOD") },
			  { TEXT("buildSettings"), TEXT("already reachable through set_property on the per-LOD "
											"SourceModels structs") },
			  { TEXT("nanite"), TEXT("already reachable - set_property on NaniteSettings") } }))
		{
			return;
		}
		UStaticMeshEditorSubsystem* Sub = MifStaticMeshSubsystem(Out);
		if (!Sub) { return; }
		UStaticMesh* Mesh = MifResolveLodMesh(In, Out);
		if (!Mesh) { return; }

		const int32 Before = Sub->GetLodCount(Mesh);
		const int32 Want = JInt(In, TEXT("lodCount"), 0);
		if (Want < 1 || Want > MAX_STATIC_MESH_LODS)
		{
			Fail(Out, FString::Printf(
				TEXT("lodCount must be between 1 and %d (it counts LOD0), got %d. NOTHING was "
					 "changed."), (int32)MAX_STATIC_MESH_LODS, Want));
			return;
		}

		// PERCENTTRIANGLES IS A FRACTION. The field's own comment says 0..1 with 1.0 meaning no
		// reduction, so a caller thinking in percent would ask for 50x the triangles and be
		// silently clamped - which looks like the reduction simply not working.
		TArray<float> Percents;
		const TArray<TSharedPtr<FJsonValue>>* PctArr = nullptr;
		if (In->TryGetArrayField(TEXT("reductionPercentages"), PctArr) && PctArr)
		{
			for (const TSharedPtr<FJsonValue>& V : *PctArr)
			{
				double D = 0.0;
				if (!V.IsValid() || !V->TryGetNumber(D))
				{
					Fail(Out, TEXT("reductionPercentages[] holds numbers. NOTHING was changed."));
					return;
				}
				if (D <= 0.0 || D > 1.0)
				{
					Fail(Out, FString::Printf(
						TEXT("reductionPercentages[] values are FRACTIONS from 0 to 1, where 1.0 "
							 "means no reduction - got %g. The engine's own field comment says so, "
							 "and a value meant as a percentage would be clamped and look like the "
							 "reduction silently not working. Use 0.5 for half. NOTHING was "
							 "changed."), D));
					return;
				}
				Percents.Add(static_cast<float>(D));
			}
			if (Percents.Num() != Want)
			{
				Fail(Out, FString::Printf(
					TEXT("reductionPercentages[] has %d entries but lodCount is %d - one per LOD is "
						 "required, including LOD0. NOTHING was changed."), Percents.Num(), Want));
				return;
			}
		}

		const bool bAuto = JBool(In, TEXT("autoScreenSize"), true);
		TArray<float> Screens;
		const TArray<TSharedPtr<FJsonValue>>* ScrArr = nullptr;
		if (In->TryGetArrayField(TEXT("screenSizes"), ScrArr) && ScrArr)
		{
			if (bAuto)
			{
				Fail(Out, TEXT("screenSizes[] was given but autoScreenSize is true, so the engine "
					TEXT("would compute them and discard yours. Pass autoScreenSize:false to use "
						 "them. NOTHING was changed.")));
				return;
			}
			for (const TSharedPtr<FJsonValue>& V : *ScrArr)
			{
				double D = 0.0;
				if (V.IsValid() && V->TryGetNumber(D)) { Screens.Add(static_cast<float>(D)); }
			}
			if (Screens.Num() != Want)
			{
				Fail(Out, FString::Printf(
					TEXT("screenSizes[] has %d entries but lodCount is %d. NOTHING was changed."),
					Screens.Num(), Want));
				return;
			}
		}

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, FString::Printf(
				TEXT("this REPLACES '%s's LOD chain - %d LOD(s) become %d - and rebuilds the mesh. "
					 "Existing per-LOD build settings are regenerated, not preserved. Pass "
					 "confirm:true. NOTHING was changed."),
				*Mesh->GetPathName(), Before, Want));
			return;
		}

		FStaticMeshReductionOptions Options;
		Options.bAutoComputeLODScreenSize = bAuto;
		for (int32 i = 0; i < Want; ++i)
		{
			FStaticMeshReductionSettings S;
			S.PercentTriangles = Percents.IsValidIndex(i) ? Percents[i]
														  : (i == 0 ? 1.0f : 1.0f / (i + 1));
			S.ScreenSize = Screens.IsValidIndex(i) ? Screens[i] : (1.0f / (i + 1));
			Options.ReductionSettings.Add(S);
		}

		const int32 Result = Sub->SetLodsWithNotification(Mesh, Options, /*bApplyChanges*/ true);
		// MEASURED FROM THE MESH, not from the return value. SetLodsWithNotification returns the
		// index it reached, which is not the same claim as "the mesh now has N LODs".
		const int32 After = Sub->GetLodCount(Mesh);

		Out->SetStringField(TEXT("path"), Mesh->GetPathName());
		Out->SetNumberField(TEXT("lodCountBefore"), Before);
		Out->SetNumberField(TEXT("lodCount"), After);
		Out->SetNumberField(TEXT("requested"), Want);
		Out->SetBoolField(TEXT("autoScreenSize"), bAuto);
		Out->SetNumberField(TEXT("subsystemResult"), Result);

		TArray<TSharedPtr<FJsonValue>> Sizes;
		for (float F : Sub->GetLodScreenSizes(Mesh)) { Sizes.Add(MakeShared<FJsonValueNumber>(F)); }
		Out->SetArrayField(TEXT("screenSizes"), Sizes);

		if (After != Want)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("%d LOD(s) were requested and the mesh now reports %d. The count is read back "
					 "from the mesh rather than taken from the call, which returns an index rather "
					 "than a count. A shortfall usually means the reducer could not hit the "
					 "requested triangle budget."), Want, After));
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the mesh is dirty and NOTHING has been saved."));
	}

	void H_remove_lods(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("confirm") },
			TEXT("path (aliases assetPath, mesh); confirm:true - this strips every LOD but LOD0"),
			{ { TEXT("lod"), TEXT("there is no remove-one-LOD operation in the engine - "
								  "RemoveLods strips all of them. generate_lods rebuilds a chain "
								  "of the size you want") } }))
		{
			return;
		}
		UStaticMeshEditorSubsystem* Sub = MifStaticMeshSubsystem(Out);
		if (!Sub) { return; }
		UStaticMesh* Mesh = MifResolveLodMesh(In, Out);
		if (!Mesh) { return; }

		const int32 Before = Sub->GetLodCount(Mesh);
		if (Before <= 1)
		{
			Out->SetStringField(TEXT("path"), Mesh->GetPathName());
			Out->SetNumberField(TEXT("lodCountBefore"), Before);
			Out->SetNumberField(TEXT("lodCount"), Before);
			// A NUMBER, not a bool. The success path reports removed as a count, and a field whose
			// TYPE changes with the branch is worse than a wrong value - a caller doing
			// removed > 0 gets a silent surprise on the idempotent path.
			Out->SetNumberField(TEXT("removed"), 0);
			Out->SetStringField(TEXT("note"),
				TEXT("this mesh has only LOD0, so there is nothing to remove and nothing needed "
					 "removing. removed:0 here means the end state you asked for already holds."));
			return;
		}
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, FString::Printf(
				TEXT("this strips %d LOD(s) from '%s', leaving only LOD0, and rebuilds the mesh. "
					 "Their reduction settings go with them. Pass confirm:true. NOTHING was "
					 "changed."), Before - 1, *Mesh->GetPathName()));
			return;
		}

		const bool bOk = Sub->RemoveLods(Mesh);
		const int32 After = Sub->GetLodCount(Mesh);
		Out->SetStringField(TEXT("path"), Mesh->GetPathName());
		Out->SetNumberField(TEXT("lodCountBefore"), Before);
		Out->SetNumberField(TEXT("lodCount"), After);
		// MEASURED, not taken from the bool - RemoveLods reports whether it ran, not what resulted.
		Out->SetNumberField(TEXT("removed"), Before - After);
		Out->SetBoolField(TEXT("succeeded"), bOk);
		if (!bOk || After != 1)
		{
			Fail(Out, FString::Printf(
				TEXT("RemoveLods returned %s and the mesh now reports %d LOD(s) rather than 1. The "
					 "count is read back from the mesh rather than assumed."),
				bOk ? TEXT("true") : TEXT("false"), After));
			return;
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the mesh is dirty and NOTHING has been saved."));
	}
}
