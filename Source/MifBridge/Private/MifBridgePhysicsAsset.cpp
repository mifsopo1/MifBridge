// MifBridge - PhysicsAsset authoring: bodies, constraints, and the body-pair collision table.
//
// SCOPE, AND WHY IT IS NARROWER THAN IT LOOKS. This does NOT try to be a reader for PhysicsAssets.
// Almost everything about one is already reachable: SkeletalBodySetups, ConstraintSetup and every
// FKAggregateGeom inside them are ordinary UPROPERTYs, and ResolvePropertyPathEx crosses object
// pointers, so `get_property {propertyPath:"SkeletalBodySetups"}` walks the lot today. Building a
// second way to read the same fields is the parallel-system mistake this spec has declined before
// (FEATURE_PARITY_SPEC.md:2686, the PhysicsAsset/LODInfo entry).
//
// describe_physics_asset therefore earns its place on exactly two things reflection CANNOT give:
//
//   1. CollisionDisableTable (PhysicsAsset.h:245) is a bare TMap<FRigidBodyIndexPair,bool> with NO
//      UPROPERTY on it. Reflection cannot see it at all. It is also the single most confusing part
//      of a ragdoll - which bodies ignore each other - and it is invisible from every other endpoint.
//   2. The INDEX numbering. Every write verb here addresses bodies and constraints by index, and
//      those indices SHIFT when anything is removed (DestroyBody RemoveAts and then rebuilds the
//      index map). A caller needs them from the same source that the writes consume.
//
// Everything else in the response is a convenience beside those two, and the response says so rather
// than implying this is the only way to read a PhysicsAsset.
//
// ============================================================================================
// THREE UNGUARDED ENGINE CALLS. All three were verified by reading, not assumed.
// ============================================================================================
//
// 1. DestroyConstraint (PhysicsAssetUtils.cpp:1189) is `check(PhysAsset)` and then a bare
//    `ConstraintSetup.RemoveAt(ConstraintIndex)`. The check validates the ASSET POINTER, not the
//    index. An out-of-range index from a caller is a crash, not an error return.
//
// 2. DestroyBody (PhysicsAssetUtils.cpp:1229) ends in the same bare
//    `SkeletalBodySetups.RemoveAt(bodyIndex)`.
//
//    So every index this file passes to either is bounds-checked HERE first. That is the whole
//    reason the remove verbs accept a boneName as well: an index a caller derived from a stale read
//    is the likeliest way to hand one of these a bad number.
//
// 3. NOT OFFERED, deliberately: the PER-PRIMITIVE collision variant.
//    UPhysicsAsset::SetPrimitiveCollision (PhysicsAsset.cpp:305) carries
//        check(SkeletalBodySetups.IsValidIndex(BodyIndex));       // hard check - crash
//        ensure(PrimitiveIndex < AggGeom->GetElementCount());     // and this one is WRONG
//    GetElementCount() is the TOTAL across spheres, boxes, capsules and convex hulls, while
//    PrimitiveIndex is per-TYPE. So PrimitiveType=Box with PrimitiveIndex=3, on a body holding 5
//    elements of which 1 is a box, passes the ensure and then indexes BoxElems[3] out of range.
//    Guarding that correctly means validating against the per-type array the engine failed to
//    check, which is its own piece of work; it is filed rather than half-done here. The BODY-PAIR
//    table (DisableCollision/EnableCollision) has no such defect and is offered.

#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/BodySetup.h"
// USkeletalBodySetup is declared inside PhysicsAsset.h itself (:421), not in a header of its own.
#include "PhysicsEngine/PhysicsConstraintTemplate.h"
#include "PhysicsAssetUtils.h"
#include "Engine/SkeletalMesh.h"
#include "ScopedTransaction.h"

namespace MifBridge
{
	static UPhysicsAsset* ResolvePhysicsAsset(const TSharedRef<FJsonObject>& In,
											  const TSharedRef<FJsonObject>& Out)
	{
		const FString Path = JStrAny(In, { TEXT("assetPath"), TEXT("path"), TEXT("asset") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("assetPath is required (aliases: path, asset) - a PhysicsAsset. ")
				TEXT("find_assets {class:\"PhysicsAsset\"} lists them. NOTHING was changed."));
			return nullptr;
		}
		UPhysicsAsset* Asset = LoadObject<UPhysicsAsset>(nullptr, *Path, nullptr,
														LOAD_NoWarn | LOAD_Quiet);
		if (!Asset)
		{
			const FString Name = FPaths::GetBaseFilename(Path);
			Asset = LoadObject<UPhysicsAsset>(nullptr, *(Path + TEXT(".") + Name), nullptr,
											  LOAD_NoWarn | LOAD_Quiet);
		}
		if (!Asset)
		{
			Fail(Out, FString::Printf(
				TEXT("no PhysicsAsset at '%s'. find_assets {class:\"PhysicsAsset\"} lists them. ")
				TEXT("NOTHING was changed."), *Path));
		}
		return Asset;
	}

	/** Bodies are addressed by index OR bone name; a stale index is the likeliest bad input. */
	static bool ResolveBodyIndex(UPhysicsAsset* Asset, const TSharedRef<FJsonObject>& In,
								 int32& OutIndex, const TSharedRef<FJsonObject>& Out)
	{
		const FString BoneName = JStr(In, TEXT("boneName"));
		if (!BoneName.IsEmpty())
		{
			for (int32 i = 0; i < Asset->SkeletalBodySetups.Num(); ++i)
			{
				const USkeletalBodySetup* B = Asset->SkeletalBodySetups[i];
				if (B && B->BoneName == FName(*BoneName)) { OutIndex = i; return true; }
			}
			TArray<FString> Names;
			for (const USkeletalBodySetup* B : Asset->SkeletalBodySetups)
			{
				if (B && Names.Num() < 30) { Names.Add(B->BoneName.ToString()); }
			}
			Fail(Out, FString::Printf(
				TEXT("no body for bone '%s' in this PhysicsAsset. It has bodies for: %s. ")
				TEXT("NOTHING was changed."),
				*BoneName, Names.Num() ? *FString::Join(Names, TEXT(", ")) : TEXT("(none)")));
			return false;
		}
		if (!In->HasField(TEXT("index")))
		{
			Fail(Out, TEXT("boneName or index is required to identify a body. describe_physics_asset ")
				TEXT("lists both. NOTHING was changed."));
			return false;
		}
		const int32 Index = static_cast<int32>(JNum(In, TEXT("index"), -1.0));
		// THE BOUNDS CHECK THE ENGINE DOES NOT DO. DestroyBody ends in a bare RemoveAt, so an
		// out-of-range index here is an editor crash rather than an error.
		if (!Asset->SkeletalBodySetups.IsValidIndex(Index))
		{
			Fail(Out, FString::Printf(
				TEXT("body index %d is out of range - this PhysicsAsset has %d body/bodies (0..%d). ")
				TEXT("FPhysicsAssetUtils::DestroyBody ends in an unguarded RemoveAt, so this is ")
				TEXT("checked here rather than crashing the editor. Indices SHIFT after any removal; ")
				TEXT("re-read describe_physics_asset, or address the body by boneName instead. ")
				TEXT("NOTHING was changed."),
				Index, Asset->SkeletalBodySetups.Num(), Asset->SkeletalBodySetups.Num() - 1));
			return false;
		}
		OutIndex = Index;
		return true;
	}

	static void WriteBodyPairTable(UPhysicsAsset* Asset, const TSharedRef<FJsonObject>& Out)
	{
		// The one thing reflection cannot reach: CollisionDisableTable has no UPROPERTY.
		TArray<TSharedPtr<FJsonValue>> Pairs;
		for (int32 i = 0; i < Asset->SkeletalBodySetups.Num(); ++i)
		{
			for (int32 j = i + 1; j < Asset->SkeletalBodySetups.Num(); ++j)
			{
				if (!Asset->IsCollisionEnabled(i, j))
				{
					const USkeletalBodySetup* A = Asset->SkeletalBodySetups[i];
					const USkeletalBodySetup* B = Asset->SkeletalBodySetups[j];
					TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
					R->SetNumberField(TEXT("indexA"), i);
					R->SetNumberField(TEXT("indexB"), j);
					R->SetStringField(TEXT("boneA"), A ? A->BoneName.ToString() : FString());
					R->SetStringField(TEXT("boneB"), B ? B->BoneName.ToString() : FString());
					Pairs.Add(MakeShared<FJsonValueObject>(R));
				}
			}
		}
		Out->SetArrayField(TEXT("disabledPairs"), Pairs);
		Out->SetNumberField(TEXT("disabledPairCount"), Pairs.Num());
	}

	// --- describe_physics_asset ---------------------------------------------
	void H_describe_physics_asset(const TSharedRef<FJsonObject>& In,
								  const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset") },
			TEXT("assetPath (aliases: path, asset) - a PhysicsAsset"),
			{ { TEXT("boneName"), TEXT("this describes the whole asset; every body is listed with its "
									   "bone name and index") } }))
		{
			return;
		}
		UPhysicsAsset* Asset = ResolvePhysicsAsset(In, Out);
		if (!Asset) { return; }

		TArray<TSharedPtr<FJsonValue>> Bodies;
		for (int32 i = 0; i < Asset->SkeletalBodySetups.Num(); ++i)
		{
			const USkeletalBodySetup* B = Asset->SkeletalBodySetups[i];
			if (!B) { continue; }
			TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
			// THE INDEX IS THE POINT of this row - every write verb consumes it, and it shifts
			// whenever anything is removed.
			R->SetNumberField(TEXT("index"), i);
			R->SetStringField(TEXT("boneName"), B->BoneName.ToString());
			// Primitive COUNTS, not their contents: the contents are plain UPROPERTYs that
			// get_property already returns in full, and duplicating them here would be a second
			// reader for the same data.
			R->SetNumberField(TEXT("sphereCount"), B->AggGeom.SphereElems.Num());
			R->SetNumberField(TEXT("boxCount"), B->AggGeom.BoxElems.Num());
			R->SetNumberField(TEXT("capsuleCount"), B->AggGeom.SphylElems.Num());
			R->SetNumberField(TEXT("convexCount"), B->AggGeom.ConvexElems.Num());
			R->SetNumberField(TEXT("primitiveCount"), B->AggGeom.GetElementCount());
			Bodies.Add(MakeShared<FJsonValueObject>(R));
		}

		TArray<TSharedPtr<FJsonValue>> Constraints;
		for (int32 i = 0; i < Asset->ConstraintSetup.Num(); ++i)
		{
			const UPhysicsConstraintTemplate* C = Asset->ConstraintSetup[i];
			if (!C) { continue; }
			TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
			R->SetNumberField(TEXT("index"), i);
			R->SetStringField(TEXT("jointName"), C->DefaultInstance.JointName.ToString());
			R->SetStringField(TEXT("bone1"), C->DefaultInstance.ConstraintBone1.ToString());
			R->SetStringField(TEXT("bone2"), C->DefaultInstance.ConstraintBone2.ToString());
			Constraints.Add(MakeShared<FJsonValueObject>(R));
		}

		Out->SetStringField(TEXT("assetPath"), Asset->GetPathName());
		Out->SetArrayField(TEXT("bodies"), Bodies);
		Out->SetNumberField(TEXT("bodyCount"), Bodies.Num());
		Out->SetArrayField(TEXT("constraints"), Constraints);
		Out->SetNumberField(TEXT("constraintCount"), Constraints.Num());
		WriteBodyPairTable(Asset, Out);
		Out->SetStringField(TEXT("note"),
			TEXT("this endpoint exists for the two things reflection cannot give you: disabledPairs "
				 "(CollisionDisableTable has no UPROPERTY, so no get_property call can reach it) and "
				 "the body/constraint INDEX numbering that every write verb here consumes. Everything "
				 "else about a PhysicsAsset is an ordinary UPROPERTY - get_property {propertyPath:"
				 "\"SkeletalBodySetups\"} returns the primitives in full, including their transforms "
				 "and radii, and set_property tunes them."));
	}

	// --- add_physics_body ---------------------------------------------------
	void H_add_physics_body(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("boneName"), TEXT("geomType"),
			  TEXT("minBoneSize") },
			TEXT("assetPath (aliases: path, asset); boneName - the bone to create a body for; ")
			TEXT("geomType (sphyl|sphere|box|taperedCapsule, default sphyl); minBoneSize"),
			{ { TEXT("autoFit"), TEXT("not offered - FPhysicsAssetUtils::CreateFromSkeletalMesh puts "
									  "up an FScopedSlowTask MakeDialog, and a modal deadlocks the "
									  "bridge because handlers run inline on the ticker that would "
									  "have to service it") } }))
		{
			return;
		}
		UPhysicsAsset* Asset = ResolvePhysicsAsset(In, Out);
		if (!Asset) { return; }

		const FString BoneName = JStr(In, TEXT("boneName"));
		if (BoneName.IsEmpty())
		{
			Fail(Out, TEXT("boneName is required - the bone to create a body for. NOTHING was changed."));
			return;
		}
		// Already has one? Two bodies on one bone is not a state the editor produces, and creating a
		// second silently would be a mess to unpick.
		for (const USkeletalBodySetup* B : Asset->SkeletalBodySetups)
		{
			if (B && B->BoneName == FName(*BoneName))
			{
				Fail(Out, FString::Printf(
					TEXT("this PhysicsAsset already has a body for bone '%s'. Remove it first, or "
						 "tune the existing one with set_property. NOTHING was changed."), *BoneName));
				return;
			}
		}

		FPhysAssetCreateParams Params;
		const FString Geom = JStr(In, TEXT("geomType"), TEXT("sphyl")).ToLower();
		if (Geom == TEXT("sphyl") || Geom == TEXT("capsule")) { Params.GeomType = EFG_Sphyl; }
		else if (Geom == TEXT("sphere")) { Params.GeomType = EFG_Sphere; }
		else if (Geom == TEXT("box")) { Params.GeomType = EFG_Box; }
		else if (Geom == TEXT("taperedcapsule")) { Params.GeomType = EFG_TaperedCapsule; }
		else
		{
			Fail(Out, FString::Printf(
				TEXT("unknown geomType '%s' - accepted: sphyl (alias capsule), sphere, box, ")
				TEXT("taperedCapsule. The convex and level-set types are not offered here because ")
				TEXT("they need render geometry this call does not fit against. NOTHING was changed."),
				*Geom));
			return;
		}
		if (In->HasField(TEXT("minBoneSize")))
		{
			Params.MinBoneSize = static_cast<float>(JNum(In, TEXT("minBoneSize"), 20.0));
		}

		const int32 Before = Asset->SkeletalBodySetups.Num();
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_AddPhysBody", "Add Physics Body"));
		Asset->Modify();
		const int32 Index = FPhysicsAssetUtils::CreateNewBody(Asset, FName(*BoneName), Params);
		if (!Asset->SkeletalBodySetups.IsValidIndex(Index))
		{
			Fail(Out, FString::Printf(
				TEXT("CreateNewBody returned index %d, which the asset does not contain. NOTHING ")
				TEXT("usable was produced."), Index));
			return;
		}
		Asset->UpdateBodySetupIndexMap();
		Asset->MarkPackageDirty();

		Out->SetStringField(TEXT("assetPath"), Asset->GetPathName());
		Out->SetStringField(TEXT("boneName"), BoneName);
		Out->SetNumberField(TEXT("bodyIndex"), Index);
		Out->SetNumberField(TEXT("bodyCountBefore"), Before);
		Out->SetNumberField(TEXT("bodyCount"), Asset->SkeletalBodySetups.Num());
		// CreateNewBody makes the body; it does not FIT geometry to the bone. Saying so here is the
		// difference between a caller thinking they have a working ragdoll and knowing they do not.
		const USkeletalBodySetup* NewBody = Asset->SkeletalBodySetups[Index];
		const int32 Prims = NewBody ? NewBody->AggGeom.GetElementCount() : 0;
		Out->SetNumberField(TEXT("primitiveCount"), Prims);
		if (Prims == 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("the body exists but has NO collision primitives - CreateNewBody does not fit "
					 "geometry to the bone, it only creates the setup. Add primitives with "
					 "edit_container on the body's AggGeom.SphylElems (or SphereElems / BoxElems), "
					 "addressing the body via its objectPath from get_property, or it will collide "
					 "with nothing."));
		}
		Out->SetStringField(TEXT("assetNote"),
			TEXT("the asset is dirty and NOTHING has been saved."));
	}

	// --- remove_physics_body ------------------------------------------------
	void H_remove_physics_body(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("boneName"), TEXT("index"),
			  TEXT("confirm") },
			TEXT("assetPath (aliases: path, asset); boneName OR index; confirm:true - removing a ")
			TEXT("body also renumbers every body after it and drops its collision-disable pairs"),
			{}))
		{
			return;
		}
		UPhysicsAsset* Asset = ResolvePhysicsAsset(In, Out);
		if (!Asset) { return; }
		int32 Index = INDEX_NONE;
		if (!ResolveBodyIndex(Asset, In, Index, Out)) { return; }

		const USkeletalBodySetup* Body = Asset->SkeletalBodySetups[Index];
		const FString BoneName = Body ? Body->BoneName.ToString() : FString();
		const int32 Before = Asset->SkeletalBodySetups.Num();

		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, FString::Printf(
				TEXT("removing body %d ('%s') RENUMBERS every body after it, so any index you are ")
				TEXT("holding becomes wrong, and it drops that body's collision-disable pairs. Pass ")
				TEXT("confirm:true. NOTHING was changed."), Index, *BoneName));
			return;
		}

		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_RemovePhysBody", "Remove Physics Body"));
		Asset->Modify();
		FPhysicsAssetUtils::DestroyBody(Asset, Index);
		const int32 After = Asset->SkeletalBodySetups.Num();
		if (After >= Before)
		{
			Fail(Out, FString::Printf(
				TEXT("DestroyBody ran and the asset still holds %d body/bodies. NOTHING was removed."),
				After));
			return;
		}
		Asset->MarkPackageDirty();

		Out->SetStringField(TEXT("assetPath"), Asset->GetPathName());
		Out->SetStringField(TEXT("boneName"), BoneName);
		Out->SetNumberField(TEXT("removedIndex"), Index);
		Out->SetNumberField(TEXT("bodyCountBefore"), Before);
		Out->SetNumberField(TEXT("bodyCount"), After);
		Out->SetStringField(TEXT("renumberNote"),
			TEXT("every body after the removed one has shifted down by one. Any index held from an "
				 "earlier describe_physics_asset is now wrong - re-read before the next call, or "
				 "address bodies by boneName."));
		Out->SetStringField(TEXT("assetNote"), TEXT("the asset is dirty and NOTHING has been saved."));
	}

	// --- add_physics_constraint ---------------------------------------------
	void H_add_physics_constraint(const TSharedRef<FJsonObject>& In,
								  const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("bone1"), TEXT("bone2"),
			  TEXT("name") },
			TEXT("assetPath (aliases: path, asset); bone1 and bone2 - the two bones to constrain; ")
			TEXT("name - optional joint name, defaults to bone1"),
			{ { TEXT("limits"), TEXT("the swing/twist limits are ordinary UPROPERTYs on the "
									 "constraint's DefaultInstance - create it here, then tune it "
									 "with set_property") } }))
		{
			return;
		}
		UPhysicsAsset* Asset = ResolvePhysicsAsset(In, Out);
		if (!Asset) { return; }

		const FString Bone1 = JStr(In, TEXT("bone1"));
		const FString Bone2 = JStr(In, TEXT("bone2"));
		if (Bone1.IsEmpty() || Bone2.IsEmpty())
		{
			Fail(Out, TEXT("bone1 and bone2 are both required. NOTHING was changed."));
			return;
		}
		if (Bone1 == Bone2)
		{
			Fail(Out, TEXT("bone1 and bone2 are the same bone - a constraint joins two DIFFERENT ")
				TEXT("bodies. NOTHING was changed."));
			return;
		}
		// Both bones need bodies, or the constraint refers to nothing. Checked here because nothing
		// downstream complains.
		auto HasBody = [Asset](const FString& Bone)
		{
			for (const USkeletalBodySetup* B : Asset->SkeletalBodySetups)
			{
				if (B && B->BoneName == FName(*Bone)) { return true; }
			}
			return false;
		};
		for (const FString& Bone : { Bone1, Bone2 })
		{
			if (!HasBody(Bone))
			{
				Fail(Out, FString::Printf(
					TEXT("no physics body exists for bone '%s', so a constraint on it would join ")
					TEXT("nothing. add_physics_body first. NOTHING was changed."), *Bone));
				return;
			}
		}

		const FString JointName = JStr(In, TEXT("name"), Bone1);
		const int32 Before = Asset->ConstraintSetup.Num();
		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_AddPhysConstraint",
										"Add Physics Constraint"));
		Asset->Modify();
		const int32 Index = FPhysicsAssetUtils::CreateNewConstraint(Asset, FName(*JointName));
		if (!Asset->ConstraintSetup.IsValidIndex(Index))
		{
			Fail(Out, FString::Printf(
				TEXT("CreateNewConstraint returned index %d, which the asset does not contain. ")
				TEXT("NOTHING usable was produced."), Index));
			return;
		}
		// CreateNewConstraint makes an EMPTY template - it does not know which bones it joins. Wiring
		// the two bone names is what turns it into a constraint rather than a placeholder, and
		// leaving that to the caller would mean handing back something that does nothing.
		UPhysicsConstraintTemplate* C = Asset->ConstraintSetup[Index];
		C->Modify();
		C->DefaultInstance.ConstraintBone1 = FName(*Bone1);
		C->DefaultInstance.ConstraintBone2 = FName(*Bone2);
		C->DefaultInstance.JointName = FName(*JointName);
		Asset->MarkPackageDirty();

		// Read back from the asset, not from the pointer we just wrote through.
		const UPhysicsConstraintTemplate* Check = Asset->ConstraintSetup[Index];
		if (!Check || Check->DefaultInstance.ConstraintBone1 != FName(*Bone1))
		{
			Fail(Out, TEXT("the constraint was created and does not report its bones on read-back. ")
				TEXT("NOTHING usable was produced."));
			return;
		}

		Out->SetStringField(TEXT("assetPath"), Asset->GetPathName());
		Out->SetNumberField(TEXT("constraintIndex"), Index);
		Out->SetStringField(TEXT("jointName"), JointName);
		Out->SetStringField(TEXT("bone1"), Bone1);
		Out->SetStringField(TEXT("bone2"), Bone2);
		Out->SetNumberField(TEXT("constraintCountBefore"), Before);
		Out->SetNumberField(TEXT("constraintCount"), Asset->ConstraintSetup.Num());
		Out->SetStringField(TEXT("note"),
			TEXT("the constraint is created with the engine's DEFAULT limits (free swing and twist). "
				 "Tune them with set_property on the constraint's DefaultInstance.ProfileInstance - "
				 "they are ordinary UPROPERTYs and this endpoint deliberately does not duplicate "
				 "that surface."));
		Out->SetStringField(TEXT("assetNote"), TEXT("the asset is dirty and NOTHING has been saved."));
	}

	// --- remove_physics_constraint ------------------------------------------
	void H_remove_physics_constraint(const TSharedRef<FJsonObject>& In,
									 const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("index"), TEXT("jointName"),
			  TEXT("confirm") },
			TEXT("assetPath (aliases: path, asset); index OR jointName; confirm:true"),
			{}))
		{
			return;
		}
		UPhysicsAsset* Asset = ResolvePhysicsAsset(In, Out);
		if (!Asset) { return; }

		int32 Index = INDEX_NONE;
		const FString JointName = JStr(In, TEXT("jointName"));
		if (!JointName.IsEmpty())
		{
			for (int32 i = 0; i < Asset->ConstraintSetup.Num(); ++i)
			{
				const UPhysicsConstraintTemplate* C = Asset->ConstraintSetup[i];
				if (C && C->DefaultInstance.JointName == FName(*JointName)) { Index = i; break; }
			}
			if (Index == INDEX_NONE)
			{
				Fail(Out, FString::Printf(
					TEXT("no constraint named '%s'. describe_physics_asset lists them with their ")
					TEXT("indices. NOTHING was changed."), *JointName));
				return;
			}
		}
		else
		{
			if (!In->HasField(TEXT("index")))
			{
				Fail(Out, TEXT("index or jointName is required. NOTHING was changed."));
				return;
			}
			Index = static_cast<int32>(JNum(In, TEXT("index"), -1.0));
			// THE BOUNDS CHECK THE ENGINE DOES NOT DO. DestroyConstraint is check(PhysAsset) and
			// then a bare ConstraintSetup.RemoveAt(ConstraintIndex) - the check validates the ASSET
			// pointer, never the index, so an out-of-range value crashes the editor.
			if (!Asset->ConstraintSetup.IsValidIndex(Index))
			{
				Fail(Out, FString::Printf(
					TEXT("constraint index %d is out of range - this asset has %d (0..%d). ")
					TEXT("FPhysicsAssetUtils::DestroyConstraint validates the ASSET pointer and not ")
					TEXT("the index, ending in an unguarded RemoveAt, so this is checked here rather ")
					TEXT("than crashing the editor. NOTHING was changed."),
					Index, Asset->ConstraintSetup.Num(), Asset->ConstraintSetup.Num() - 1));
				return;
			}
		}

		const UPhysicsConstraintTemplate* C = Asset->ConstraintSetup[Index];
		const FString Name = C ? C->DefaultInstance.JointName.ToString() : FString();
		const int32 Before = Asset->ConstraintSetup.Num();
		if (!JBool(In, TEXT("confirm"), false))
		{
			Fail(Out, FString::Printf(
				TEXT("removing constraint %d ('%s') renumbers every constraint after it. Pass ")
				TEXT("confirm:true. NOTHING was changed."), Index, *Name));
			return;
		}

		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_RemovePhysConstraint",
										"Remove Physics Constraint"));
		Asset->Modify();
		FPhysicsAssetUtils::DestroyConstraint(Asset, Index);
		const int32 After = Asset->ConstraintSetup.Num();
		if (After >= Before)
		{
			Fail(Out, FString::Printf(
				TEXT("DestroyConstraint ran and the asset still holds %d. NOTHING was removed."),
				After));
			return;
		}
		Asset->MarkPackageDirty();

		Out->SetStringField(TEXT("assetPath"), Asset->GetPathName());
		Out->SetNumberField(TEXT("removedIndex"), Index);
		Out->SetStringField(TEXT("jointName"), Name);
		Out->SetNumberField(TEXT("constraintCountBefore"), Before);
		Out->SetNumberField(TEXT("constraintCount"), After);
		Out->SetStringField(TEXT("renumberNote"),
			TEXT("every constraint after the removed one has shifted down by one - re-read "
				 "describe_physics_asset before using an index again."));
		Out->SetStringField(TEXT("assetNote"), TEXT("the asset is dirty and NOTHING has been saved."));
	}

	// --- set_physics_body_collision -----------------------------------------
	void H_set_physics_body_collision(const TSharedRef<FJsonObject>& In,
									  const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("boneA"), TEXT("boneB"),
			  TEXT("indexA"), TEXT("indexB"), TEXT("enabled") },
			TEXT("assetPath (aliases: path, asset); boneA + boneB (or indexA + indexB); ")
			TEXT("enabled:true|false - whether the two bodies collide with each other"),
			{ { TEXT("primitiveIndex"), TEXT("the per-PRIMITIVE variant is not offered: "
											 "UPhysicsAsset::SetPrimitiveCollision's own ensure "
											 "compares a per-type index against the TOTAL element "
											 "count, so a valid-looking call can index past the end "
											 "of a per-type array. This endpoint is the body-PAIR "
											 "table, which has no such defect") } }))
		{
			return;
		}
		UPhysicsAsset* Asset = ResolvePhysicsAsset(In, Out);
		if (!Asset) { return; }

		auto Resolve = [Asset, &Out](const FString& BoneKey, const FString& IndexKey,
									 const TSharedRef<FJsonObject>& In2, int32& OutIdx)
		{
			const FString Bone = JStr(In2, *BoneKey);
			if (!Bone.IsEmpty())
			{
				for (int32 i = 0; i < Asset->SkeletalBodySetups.Num(); ++i)
				{
					const USkeletalBodySetup* B = Asset->SkeletalBodySetups[i];
					if (B && B->BoneName == FName(*Bone)) { OutIdx = i; return true; }
				}
				Fail(Out, FString::Printf(
					TEXT("no body for bone '%s'. NOTHING was changed."), *Bone));
				return false;
			}
			if (!In2->HasField(*IndexKey))
			{
				Fail(Out, FString::Printf(
					TEXT("%s or %s is required. NOTHING was changed."), *BoneKey, *IndexKey));
				return false;
			}
			OutIdx = static_cast<int32>(JNum(In2, *IndexKey, -1.0));
			if (!Asset->SkeletalBodySetups.IsValidIndex(OutIdx))
			{
				Fail(Out, FString::Printf(
					TEXT("%s %d is out of range - this asset has %d body/bodies. NOTHING was changed."),
					*IndexKey, OutIdx, Asset->SkeletalBodySetups.Num()));
				return false;
			}
			return true;
		};

		int32 A = INDEX_NONE, B = INDEX_NONE;
		if (!Resolve(TEXT("boneA"), TEXT("indexA"), In, A)) { return; }
		if (!Resolve(TEXT("boneB"), TEXT("indexB"), In, B)) { return; }
		if (A == B)
		{
			Fail(Out, TEXT("boneA and boneB resolve to the same body - a body cannot collide with ")
				TEXT("itself, so there is no pair to set. NOTHING was changed."));
			return;
		}
		if (!In->HasField(TEXT("enabled")))
		{
			Fail(Out, TEXT("enabled:true|false is required - say which way to set the pair rather ")
				TEXT("than having this guess or toggle. NOTHING was changed."));
			return;
		}
		const bool bEnabled = JBool(In, TEXT("enabled"), true);
		const bool bWas = Asset->IsCollisionEnabled(A, B);

		FScopedTransaction Tx(NSLOCTEXT("MifBridge", "MifBridge_SetPhysPairCollision",
										"Set Physics Body Collision"));
		Asset->Modify();
		if (bEnabled) { Asset->EnableCollision(A, B); }
		else          { Asset->DisableCollision(A, B); }

		// Verified by reading the table back - both calls are void.
		const bool bNow = Asset->IsCollisionEnabled(A, B);
		if (bNow != bEnabled)
		{
			Fail(Out, FString::Printf(
				TEXT("the pair still reports collision %s after asking for %s. NOTHING usable was ")
				TEXT("produced."), bNow ? TEXT("enabled") : TEXT("disabled"),
				bEnabled ? TEXT("enabled") : TEXT("disabled")));
			return;
		}
		Asset->MarkPackageDirty();

		const USkeletalBodySetup* BA = Asset->SkeletalBodySetups[A];
		const USkeletalBodySetup* BB = Asset->SkeletalBodySetups[B];
		Out->SetStringField(TEXT("assetPath"), Asset->GetPathName());
		Out->SetStringField(TEXT("boneA"), BA ? BA->BoneName.ToString() : FString());
		Out->SetStringField(TEXT("boneB"), BB ? BB->BoneName.ToString() : FString());
		Out->SetNumberField(TEXT("indexA"), A);
		Out->SetNumberField(TEXT("indexB"), B);
		Out->SetBoolField(TEXT("enabled"), bNow);
		Out->SetBoolField(TEXT("changed"), bWas != bNow);
		WriteBodyPairTable(Asset, Out);
		Out->SetStringField(TEXT("assetNote"), TEXT("the asset is dirty and NOTHING has been saved."));
	}
}
