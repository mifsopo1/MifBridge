// MifBridge — level-authoring throughput and material control.
//
// Every endpoint here exists because building a 426-actor town exposed it as a hard blocker:
//
//  spawn_many          : each actor cost TWO HTTP round-trips (spawn, then set mesh). 426 actors was
//                        ~850 calls and several minutes. One call now places hundreds.
//  duplicate_actors     : building five modular structures meant re-spawning every wall panel by
//                        hand. Copying a finished building with an offset is what a human does.
//  create_material_instance / set_material_parameter
//                      : THE blocker for visual quality. The ground read as an obvious grid because
//                        mi_GroundRocks exposes no UV-tiling parameter and there was no way to make
//                        a dynamic instance and override one. Without this, a large flat surface can
//                        never look right — the texture either stretches or tiles visibly.
//  add_foliage_instances: 90 grass clumps were 90 separate actors. Real levels use one instanced
//                        component with N transforms — cheaper to render and to manage.
//
// Batch D.1 (finding D-2, docs/audit/06_IMPLEMENTED.md): every handler in this file predates the
// strict-params rule, and set_material_parameter was live-caught returning ok:true / applied:0 for
// a call whose parameters it never read. All five now run RejectUnknownParams first, and each
// handler's `in:` comment was re-derived FROM THE CODE — two of them documented parameters
// (create_material_instance's `textures`, duplicate_actors' `rotationOffset`) that no line here has
// ever read. Where a silent drop lives deeper than the top-level key set (per-item objects inside
// items[]/instances[], create_material_instance's post-creation apply loop) it is marked
// TODO(audit D.1) on the handler rather than half-fixed.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "Editor.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Components/StaticMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Materials/MaterialInterface.h"
#include "Factories/MaterialInstanceConstantFactoryNew.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"
#include "GameFramework/Actor.h"

namespace MifBridge
{
	namespace
	{
		// AuthoringWorld() was a sixth spelling of "the editor world"; it is MifBridge::EditorWorld()
		// now (declared in MifBridgeHandlers.h, defined once in MifBridgeCommon.cpp). Deliberately the
		// EDITOR world and not the PIE-preferring ActiveWorld(): everything in this file authors
		// persistent level content.

		// The actor finder moved to MifBridgeCommon.cpp as MifBridge::FindActorInWorld (declared in
		// MifBridgeHandlers.h). FIVE byte-identical copies existed under five different names
		// (FindActor, FindNavActor, FindActorByPathOrLabel, FindVpActor, FindWorldActor) — different
		// names are not a build error, which is exactly why they survived, but it meant a fix to the
		// path/name/label matching rule landed in one of five places. Do NOT add a sixth.

		// JNumFrom is GONE. It was a private re-implementation of JNum with the same silent fallback
		// (TryGetNumberField fails -> use the default), which made ReadTransform below the FOURTH copy
		// of Batch L defect 1: an item with {"x":"oops","y":1,"z":2} spawned at (0,1,2) and the
		// response counted it as spawned. Every read here goes through the shared strict readers
		// (MifBridgeHandlers.h / MifBridgeCommon.cpp) now.
		//
		// The grammar is unchanged and deliberately broad, because items[] entries are written by
		// hand: bare x/y/z on the item, or location:{x,y,z}; a bare yaw, or rotation:{x,y,z} /
		// {pitch,yaw,roll}; a scalar scale (uniform), or scale:{x,y,z}. What changed is that a
		// SUPPLIED value the bridge cannot read is now an error naming the item index and the field.
		// ArrayName is the CALLER'S spelling of the array — spawn_many calls it items[],
		// add_foliage_instances calls it instances[]. An error that names the wrong parameter sends
		// the caller looking in the wrong place, which is the whole failure mode being removed here.
		bool ReadTransform(const TSharedRef<FJsonObject>& Item, const TCHAR* ArrayName, int32 Index,
			FVector& OutLoc, FRotator& OutRot, FVector& OutScale, FString& OutError)
		{
			const FString Where = FString::Printf(TEXT("%s[%d]"), ArrayName, Index);
			auto Wrap = [&Where](const FString& Inner) { return FString::Printf(TEXT("%s: %s"), *Where, *Inner); };

			double X = 0.0, Y = 0.0, Z = 0.0;
			FString Err;
			if (ReadNumberField(Item, TEXT("x"), Where + TEXT(".x"), X, Err) == EJsonRead::Invalid
				|| ReadNumberField(Item, TEXT("y"), Where + TEXT(".y"), Y, Err) == EJsonRead::Invalid
				|| ReadNumberField(Item, TEXT("z"), Where + TEXT(".z"), Z, Err) == EJsonRead::Invalid)
			{
				OutError = Wrap(Err);
				return false;
			}
			OutLoc = FVector(X, Y, Z);
			if (ReadVectorField(Item, TEXT("location"), OutLoc, Err) == EJsonRead::Invalid)
			{
				OutError = Wrap(Err);
				return false;
			}

			double Yaw = 0.0;
			if (ReadNumberField(Item, TEXT("yaw"), Where + TEXT(".yaw"), Yaw, Err) == EJsonRead::Invalid)
			{
				OutError = Wrap(Err);
				return false;
			}
			OutRot = FRotator(0, Yaw, 0);
			if (ReadRotatorField(Item, TEXT("rotation"), OutRot, Err) == EJsonRead::Invalid)
			{
				OutError = Wrap(Err);
				return false;
			}

			OutScale = FVector::OneVector;
			if (ReadScaleField(Item, TEXT("scale"), OutScale, Err) == EJsonRead::Invalid)
			{
				OutError = Wrap(Err);
				return false;
			}
			return true;
		}

		// --- Parameter-value coercion (Batch D.1, finding D-2) -------------------
		// ONE place decides what a JSON value MEANS as a material parameter, so the scalars/vectors
		// maps and the singular {parameter, value} sugar can never drift apart.

		// JsonTypeName now lives in MifBridgeCommon.cpp (declared in MifBridgeHandlers.h). The second
		// file the "file-local until" clause was waiting for already existed — MifBridgeNodes5.cpp had
		// its own copy, and the two had DIVERGED: this one said "boolean", that one said "bool", so
		// set_material_parameter and set_property refused the same JSON type in two different words.
		// The shared version keeps "boolean"; no message from this file changes.

		// {r,g,b,a} | {x,y,z,w} | [r,g,b] | [r,g,b,a] -> FLinearColor.
		// Both key spellings are accepted because a vector parameter IS four channels whichever
		// name the caller thinks in; refusing {x,y,z,w} would be the pin-alias trap all over again.
		// Missing channels default to 0, alpha/w to 1 (the create_material_instance convention).
		bool JsonToLinearColor(const TSharedPtr<FJsonValue>& Value, FLinearColor& Out, FString& OutError)
		{
			if (!Value.IsValid() || Value->Type == EJson::Null)
			{
				OutError = TEXT("value is null");
				return false;
			}
			const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
			if (Value->TryGetObject(ObjPtr) && ObjPtr)
			{
				const TSharedRef<FJsonObject> O = ObjPtr->ToSharedRef();
				const bool bRGBA = O->HasField(TEXT("r")) || O->HasField(TEXT("g")) || O->HasField(TEXT("b")) || O->HasField(TEXT("a"));
				const bool bXYZW = O->HasField(TEXT("x")) || O->HasField(TEXT("y")) || O->HasField(TEXT("z")) || O->HasField(TEXT("w"));
				if (!bRGBA && !bXYZW)
				{
					OutError = TEXT("object has none of r/g/b/a or x/y/z/w");
					return false;
				}
				Out = bRGBA
					? FLinearColor(JNum(O, TEXT("r")), JNum(O, TEXT("g")), JNum(O, TEXT("b")), JNum(O, TEXT("a"), 1.0))
					: FLinearColor(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")), JNum(O, TEXT("w"), 1.0));
				return true;
			}
			const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
			if (Value->TryGetArray(Arr) && Arr)
			{
				if (Arr->Num() < 3 || Arr->Num() > 4)
				{
					OutError = FString::Printf(TEXT("array must hold 3 or 4 numbers (got %d)"), Arr->Num());
					return false;
				}
				double C[4] = { 0.0, 0.0, 0.0, 1.0 };
				for (int32 i = 0; i < Arr->Num(); ++i)
				{
					if (!(*Arr)[i].IsValid() || !(*Arr)[i]->TryGetNumber(C[i]))
					{
						OutError = FString::Printf(TEXT("array element %d is not a number"), i);
						return false;
					}
				}
				Out = FLinearColor(C[0], C[1], C[2], C[3]);
				return true;
			}
			OutError = FString::Printf(TEXT("expected {r,g,b,a} (or {x,y,z,w}, or [r,g,b,a]), got %s"),
				JsonTypeName(Value->Type));
			return false;
		}
	}

	// --- spawn_many ---------------------------------------------------------
	//   in:  { actorClass?, mesh?, material?, folder?, labelPrefix?,
	//          items:[{ x,y,z | location:{}, rotation:{}|yaw, scale|scale:{}, label?, mesh?, material? }] }
	//   out: { spawned, failed, actors:[{label, actorPath}] }
	// One call, N actors. Per-item mesh/material override falls back to the top-level default.
	// Batch D.1: unknown-param guard added (D-2 sweep). It guards TOP-LEVEL keys only — the
	// per-item objects inside items[] are still read leniently, see the TODO below.
	// Batch L discharged HALF of the deferred per-item TODO: every transform component in an items[]
	// entry is now type-checked and a bad one is reported as items[N].<field> with the offending
	// value, instead of defaulting to 0 and being counted as spawned. What is still open is
	// UNRECOGNISED keys inside an entry (a typo'd "rot" or "meshPath" is still ignored) and the
	// non-object entry, which is still counted in `failed` with no reason attached — both need the
	// per-item equivalent of RejectUnknownParams, which is a wider change than this batch.
	void H_spawn_many(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("items"), TEXT("actorClass"), TEXT("mesh"), TEXT("material"),
			  TEXT("folder"), TEXT("labelPrefix") },
			TEXT("items[] (required), actorClass, mesh, material, folder, labelPrefix"),
			{ { TEXT("count"), TEXT("spawn_many places one actor per items[] entry — repeat the entry, or use duplicate_actors with count") },
			  { TEXT("actors"), TEXT("the array parameter is called items[]") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
		if (!In->TryGetArrayField(TEXT("items"), Items) || !Items || Items->Num() == 0)
		{
			Fail(Out, TEXT("items[] is required (each entry is one actor)"));
			return;
		}
		if (Items->Num() > 5000)
		{
			Fail(Out, TEXT("items[] capped at 5000 per call"));
			return;
		}

		const FString DefaultMesh = JStr(In, TEXT("mesh"));
		const FString DefaultMat = JStr(In, TEXT("material"));
		const FString ClassName = JStr(In, TEXT("actorClass"), TEXT("StaticMeshActor"));
		const FString Folder = JStr(In, TEXT("folder"));
		// Without this every bulk-spawned actor is "StaticMeshActor_417" — unfindable by label, and
		// useless to anything that filters on one (snap_actors_to_ground's labelContains, for a start).
		const FString LabelPrefix = JStr(In, TEXT("labelPrefix"));

		FString ClassError;
		UClass* SpawnClass = ResolveClassStrict(ClassName, nullptr, TEXT("actorClass"), ClassError);
		if (!SpawnClass) { Fail(Out, ClassError); return; }
		if (!SpawnClass->IsChildOf(AActor::StaticClass()))
		{
			Fail(Out, FString::Printf(TEXT("not an Actor class: '%s'"), *ClassName));
			return;
		}

		// Loading the mesh ONCE outside the loop is most of the speed win — StaticLoadObject per
		// item would dominate the cost for a few hundred actors.
		UStaticMesh* SharedMesh = DefaultMesh.IsEmpty() ? nullptr
			: LoadObject<UStaticMesh>(nullptr, *DefaultMesh, nullptr, LOAD_NoWarn | LOAD_Quiet);
		UMaterialInterface* SharedMat = DefaultMat.IsEmpty() ? nullptr
			: LoadObject<UMaterialInterface>(nullptr, *DefaultMat, nullptr, LOAD_NoWarn | LOAD_Quiet);

		TArray<TSharedPtr<FJsonValue>> Made;
		TArray<TSharedPtr<FJsonValue>> Errors;
		int32 Failed = 0;

		for (int32 Index = 0; Index < Items->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
			if (!(*Items)[Index].IsValid() || !(*Items)[Index]->TryGetObject(ObjPtr) || !ObjPtr) { ++Failed; continue; }
			const TSharedRef<FJsonObject> Item = ObjPtr->ToSharedRef();

			FVector Loc, Scale; FRotator Rot;
			FString ItemError;
			if (!ReadTransform(Item, TEXT("items"), Index, Loc, Rot, Scale, ItemError))
			{
				// Counted AND explained. A transform component the caller supplied and the bridge
				// could not read used to become 0 and the actor was placed there regardless.
				++Failed;
				Errors.Add(MakeShared<FJsonValueString>(ItemError));
				continue;
			}

			FActorSpawnParameters Params;
			Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
			AActor* Actor = World->SpawnActor(SpawnClass, &Loc, &Rot, Params);
			if (!Actor)
			{
				++Failed;
				Errors.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("item %d: spawn failed"), Index)));
				continue;
			}
			Actor->SetActorScale3D(Scale);

			// Per-item label wins; otherwise "<prefix>_<index>" if a prefix was given.
			const FString Label = JStr(Item, TEXT("label"));
			if (!Label.IsEmpty())
			{
				Actor->SetActorLabel(Label);
			}
			else if (!LabelPrefix.IsEmpty())
			{
				Actor->SetActorLabel(FString::Printf(TEXT("%s_%d"), *LabelPrefix, Index));
			}
			if (!Folder.IsEmpty()) { Actor->SetFolderPath(FName(*Folder)); }

			// Mesh / material: per-item override wins, else the shared default.
			if (AStaticMeshActor* SMA = Cast<AStaticMeshActor>(Actor))
			{
				UStaticMesh* Mesh = SharedMesh;
				const FString ItemMesh = JStr(Item, TEXT("mesh"));
				if (!ItemMesh.IsEmpty()) { Mesh = LoadObject<UStaticMesh>(nullptr, *ItemMesh, nullptr, LOAD_NoWarn | LOAD_Quiet); }
				if (Mesh && SMA->GetStaticMeshComponent())
				{
					SMA->GetStaticMeshComponent()->SetStaticMesh(Mesh);
					UMaterialInterface* Mat = SharedMat;
					const FString ItemMat = JStr(Item, TEXT("material"));
					if (!ItemMat.IsEmpty()) { Mat = LoadObject<UMaterialInterface>(nullptr, *ItemMat, nullptr, LOAD_NoWarn | LOAD_Quiet); }
					if (Mat) { SMA->GetStaticMeshComponent()->SetMaterial(0, Mat); }
				}
			}

			TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetStringField(TEXT("label"), Actor->GetActorLabel());
			J->SetStringField(TEXT("actorPath"), Actor->GetPathName());
			Made.Add(MakeShared<FJsonValueObject>(J));
		}

		Out->SetNumberField(TEXT("spawned"), Made.Num());
		Out->SetNumberField(TEXT("failed"), Failed);
		Out->SetArrayField(TEXT("actors"), Made);
		if (Errors.Num() > 0) { Out->SetArrayField(TEXT("errors"), Errors); }
		UE_LOG(LogMifBridge, Log, TEXT("spawn_many: %d spawned, %d failed"), Made.Num(), Failed);
	}

	// --- duplicate_actors ---------------------------------------------------
	//   in:  { actorPaths?:[...], labelPrefix?, offset?:{x,y,z}, yawOffset?, count?, labelSuffix?, folder? }
	//   out: { sourceCount, duplicated, actors[] }
	// Copy a whole set (a finished building) N times with an offset — the thing that makes modular
	// authoring practical instead of re-placing every panel.
	// Batch D.1 (D-2 sweep): unknown-param guard added. This `in:` line previously advertised
	// `rotationOffset?:{}` — a parameter NO code here has ever read. The handler rotates by
	// `yawOffset` (a scalar), so the doc line is corrected rather than the code: a documented
	// parameter that is silently dropped is the same bug as an undocumented one.
	void H_duplicate_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("actorPaths"), TEXT("labelPrefix"), TEXT("offset"), TEXT("yawOffset"),
			  TEXT("count"), TEXT("labelSuffix"), TEXT("folder") },
			TEXT("actorPaths[] and/or labelPrefix (source selection), offset {x,y,z}, yawOffset (degrees), count, labelSuffix, folder"),
			{ { TEXT("rotationOffset"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>") },
			  { TEXT("rotation"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>") },
			  { TEXT("scale"), TEXT("not implemented — copies keep the source actor's scale") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		TArray<AActor*> Sources;
		// Two shortfall channels, both always emitted: an actorPaths[] entry that resolved to nothing,
		// and a copy the engine refused to spawn. Both used to be `continue`d past in silence, so
		// `duplicated` could be short of sourceCount x count with no reason anywhere in the response.
		TArray<TSharedPtr<FJsonValue>> NotFound;
		TArray<TSharedPtr<FJsonValue>> FailedSpawns;
		const TArray<TSharedPtr<FJsonValue>>* Paths = nullptr;
		if (In->TryGetArrayField(TEXT("actorPaths"), Paths) && Paths)
		{
			for (const TSharedPtr<FJsonValue>& V : *Paths)
			{
				FString P;
				if (V.IsValid() && V->TryGetString(P))
				{
					if (AActor* A = FindActorInWorld(World, P)) { Sources.Add(A); }
					else { NotFound.Add(MakeShared<FJsonValueString>(P)); }
				}
			}
		}
		// A prefix is far more usable than listing 41 panel paths by hand.
		const FString Prefix = JStr(In, TEXT("labelPrefix"));
		if (!Prefix.IsEmpty())
		{
			for (TActorIterator<AActor> It(World); It; ++It)
			{
				AActor* A = *It;
				if (A && IsValid(A) && A->GetActorLabel().StartsWith(Prefix)) { Sources.Add(A); }
			}
		}
		if (Sources.Num() == 0) { Fail(Out, TEXT("no source actors — pass actorPaths[] or labelPrefix")); return; }

		FVector Offset(0, 0, 0);
		FString OffsetError;
		if (ReadVectorField(In, TEXT("offset"), Offset, OffsetError) == EJsonRead::Invalid)
		{
			Fail(Out, FString::Printf(TEXT("%s Nothing was duplicated."), *OffsetError));
			return;
		}
		const double YawOffset = JNum(In, TEXT("yawOffset"), 0.0);
		const int32 Count = FMath::Clamp(JInt(In, TEXT("count"), 1), 1, 50);
		const FString Suffix = JStr(In, TEXT("labelSuffix"), TEXT("_copy"));
		const FString Folder = JStr(In, TEXT("folder"));

		TArray<TSharedPtr<FJsonValue>> Made;
		for (int32 N = 1; N <= Count; ++N)
		{
			for (AActor* Src : Sources)
			{
				FActorSpawnParameters Params;
				Params.Template = Src;   // copies component config, incl. the assigned static mesh
				Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
				const FVector NewLoc = Src->GetActorLocation() + Offset * (double)N;
				FRotator NewRot = Src->GetActorRotation();
				NewRot.Yaw += YawOffset * (double)N;

				AActor* Copy = World->SpawnActor(Src->GetClass(), &NewLoc, &NewRot, Params);
				if (!Copy)
				{
					// Silently swallowed, so `duplicated` could be short of sourceCount x count with no
					// reason given anywhere in the response.
					FailedSpawns.Add(MakeShared<FJsonValueString>(FString::Printf(
						TEXT("%s (copy %d): SpawnActor returned null"), *Src->GetActorLabel(), N)));
					continue;
				}
				Copy->SetActorScale3D(Src->GetActorScale3D());
				Copy->SetActorLabel(Src->GetActorLabel() + Suffix + (Count > 1 ? FString::FromInt(N) : FString()));
				Copy->SetFolderPath(FName(*(Folder.IsEmpty() ? Src->GetFolderPath().ToString() : Folder)));

				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("label"), Copy->GetActorLabel());
				J->SetStringField(TEXT("actorPath"), Copy->GetPathName());
				Made.Add(MakeShared<FJsonValueObject>(J));
			}
		}
		Out->SetNumberField(TEXT("sourceCount"), Sources.Num());
		Out->SetNumberField(TEXT("duplicated"), Made.Num());
		Out->SetArrayField(TEXT("actors"), Made);
		// Both shortfall channels are always present, so `duplicated` is checkable rather than trusted:
		// duplicated + failed.length == sourceCount * count, and notFound[] accounts for every
		// actorPaths[] entry that never became a source.
		Out->SetArrayField(TEXT("notFound"), NotFound);
		Out->SetArrayField(TEXT("failed"), FailedSpawns);
		if (FailedSpawns.Num() > 0 || NotFound.Num() > 0)
		{
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("%d requested source path(s) did not resolve and %d copy/copies failed to spawn — see notFound[] and failed[]"),
				NotFound.Num(), FailedSpawns.Num()));
		}
	}

	// --- create_material_instance -------------------------------------------
	//   in:  { parent (parentMaterial), path, scalars?:{name:number}, vectors?:{name:{r,g,b,a}} }
	//   out: { materialPath, parent, parametersApplied }
	// Mints a real MaterialInstanceConstant asset. This is what makes UV tiling fixable: derive an
	// instance from a master material and override its tiling scalar, instead of being stuck with
	// whatever the shipped instance happens to expose.
	// Batch D.1 (D-2 sweep): unknown-param guard added. The `in:` line previously advertised
	// `textures?:{name:path}` — never implemented, never read; a caller passing it got ok:true and
	// an untextured instance. It is now a named refusal (KeyNote) instead of a lie in a comment.
	// Both silent drops the TODO here used to defer are FIXED below, and the objection it raised
	// ("a mid-apply error would leave a half-configured asset behind") is answered by validating
	// EVERYTHING before the first write rather than by tolerating silence. The TODO also asserted
	// this endpoint was in the self-managed bucket with no blanket transaction — it was not; it was
	// transacted. It is self-managed NOW (MifBridgeCommon.cpp), which is what makes the claim true
	// and matches its three asset-creating siblings.
	void H_create_material_instance(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("parent"), TEXT("parentMaterial"), TEXT("path"), TEXT("scalars"), TEXT("vectors") },
			TEXT("parent (alias: parentMaterial), path (must start with /Game/), scalars {name:number}, vectors {name:{r,g,b,a}}"),
			{ { TEXT("textures"), TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property") },
			  { TEXT("texture"),  TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property") },
			  { TEXT("material"), TEXT("the source material parameter is called parent (alias: parentMaterial)") } }))
		{
			return;
		}

		const FString ParentPath = JStrAny(In, { TEXT("parent"), TEXT("parentMaterial") });
		const FString AssetPath = JStr(In, TEXT("path"));
		if (ParentPath.IsEmpty() || AssetPath.IsEmpty())
		{
			Fail(Out, TEXT("parent and path are required (path must start with /Game/)"));
			return;
		}
		if (!AssetPath.StartsWith(TEXT("/Game/")))
		{
			Fail(Out, TEXT("path must start with /Game/"));
			return;
		}
		UMaterialInterface* Parent = LoadObject<UMaterialInterface>(nullptr, *ParentPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Parent) { Fail(Out, FString::Printf(TEXT("parent material not found: %s"), *ParentPath)); return; }

		// --- Gather + VALIDATE, before the PACKAGE and the ASSET exist ------------------
		// BATCH M moved this block ABOVE CreatePackage/FactoryCreateNew. It was already "validate
		// before a single write" — but the asset itself is a write: a bad scalars entry used to fail
		// with a UMaterialInstanceConstant and its UPackage already built in memory. Nothing registers
		// them (AssetCreated/MarkPackageDirty are at the tail), so they never reach the content
		// browser or disk, but the package name is taken for the rest of the session and a retry at
		// the same path then meets an object that is already there. This endpoint is SELF-MANAGED, so
		// there is not even a transaction to cancel. See docs/01_POSTMORTEMS.md PM-007.
		// A scalars entry that was not a number, and a vectors entry that was not an object, used to be
		// skipped in silence and simply not counted in parametersApplied — so {scalars:{"Tiling":"4"}}
		// answered ok:true, parametersApplied:0 and the caller went looking at the material. Same
		// bracket set_material_parameter uses, and the same JsonToLinearColor so {x,y,z,w} and
		// [r,g,b,a] work here too instead of only {r,g,b,a}.
		TArray<TPair<FName, float>> ScalarWrites;
		const TSharedPtr<FJsonObject>* Scalars = nullptr;
		if (In->TryGetObjectField(TEXT("scalars"), Scalars) && Scalars)
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*Scalars)->Values)
			{
				double V = 0.0;
				if (!Pair.Value.IsValid() || !Pair.Value->TryGetNumber(V))
				{
					Fail(Out, FString::Printf(
						TEXT("scalars['%s'] must be a number (got %s) — colour/vector parameters go in vectors:{}"),
						*Pair.Key, Pair.Value.IsValid() ? JsonTypeName(Pair.Value->Type) : TEXT("null")));
					return;
				}
				ScalarWrites.Emplace(FName(*Pair.Key), (float)V);
			}
		}
		TArray<TPair<FName, FLinearColor>> VectorWrites;
		const TSharedPtr<FJsonObject>* Vectors = nullptr;
		if (In->TryGetObjectField(TEXT("vectors"), Vectors) && Vectors)
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*Vectors)->Values)
			{
				FLinearColor Colour = FLinearColor::White;
				FString Error;
				if (!JsonToLinearColor(Pair.Value, Colour, Error))
				{
					Fail(Out, FString::Printf(TEXT("vectors['%s']: %s"), *Pair.Key, *Error));
					return;
				}
				VectorWrites.Emplace(FName(*Pair.Key), Colour);
			}
		}

		const FString AssetName = FPackageName::GetLongPackageAssetName(AssetPath);
		UPackage* Package = CreatePackage(*AssetPath);
		if (!Package) { Fail(Out, TEXT("failed to create package")); return; }

		UMaterialInstanceConstantFactoryNew* Factory = NewObject<UMaterialInstanceConstantFactoryNew>();
		Factory->InitialParent = Parent;
		UObject* Created = Factory->FactoryCreateNew(
			UMaterialInstanceConstant::StaticClass(), Package, FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional, nullptr, GWarn);
		UMaterialInstanceConstant* MIC = Cast<UMaterialInstanceConstant>(Created);
		if (!MIC) { Fail(Out, TEXT("factory returned null")); return; }

		// --- Apply, reporting names the PARENT does not expose --------------------------
		// This never checked the parent exposed the name, so parametersApplied counted writes the
		// material ignores — a number that looks like proof and is not. Its sibling has had
		// unknownParameters[] since Batch D.1; the `Unknown` array declared in this handler was
		// vestigial (declared, never written, never emitted). It is real now.
		int32 Applied = 0;
		TArray<TSharedPtr<FJsonValue>> Unknown;
		for (const TPair<FName, float>& Write : ScalarWrites)
		{
			const FMaterialParameterInfo Info(Write.Key);
			float Existing = 0.f;
			if (!MIC->GetScalarParameterValue(Info, Existing))
			{
				Unknown.Add(MakeShared<FJsonValueString>(Write.Key.ToString()));
				continue;
			}
			MIC->SetScalarParameterValueEditorOnly(Info, Write.Value);
			++Applied;
		}
		for (const TPair<FName, FLinearColor>& Write : VectorWrites)
		{
			const FMaterialParameterInfo Info(Write.Key);
			FLinearColor Existing;
			if (!MIC->GetVectorParameterValue(Info, Existing))
			{
				Unknown.Add(MakeShared<FJsonValueString>(Write.Key.ToString()));
				continue;
			}
			MIC->SetVectorParameterValueEditorOnly(Info, Write.Value);
			++Applied;
		}

		MIC->PostEditChange();
		FAssetRegistryModule::AssetCreated(MIC);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("materialPath"), MIC->GetPathName());
		Out->SetStringField(TEXT("parent"), Parent->GetPathName());
		Out->SetNumberField(TEXT("parametersApplied"), Applied);
		Out->SetArrayField(TEXT("unknownParameters"), Unknown);
		if (Unknown.Num() > 0)
		{
			// The instance WAS created — that is the endpoint's primary product and destroying it over
			// a bad parameter name would be worse — so this is a reported shortfall, not a Fail.
			Out->SetStringField(TEXT("note"), FString::Printf(
				TEXT("the instance was created, but %d parameter name(s) are not exposed by parent '%s' and were not applied ")
				TEXT("(see unknownParameters) — list_material_expressions on the parent shows the real names"),
				Unknown.Num(), *Parent->GetPathName()));
		}
	}

	// --- set_material_parameter ---------------------------------------------
	//   in:  { material (aliases: materialPath, path),
	//          scalars?:{ name: number },
	//          vectors?:{ name: {r,g,b,a} | {x,y,z,w} | [r,g,b,a] },
	//          parameter? (aliases: parameterName, name) + value?  — singular sugar for a
	//            one-entry map; scalar vs vector is INFERRED from value's JSON type
	//            (number/numeric string => scalar, object/array => vector) }
	//   out: { material, applied, scalarsApplied, vectorsApplied, unknownParameters[] }
	// Edit an EXISTING MaterialInstanceConstant. Reports parameters the parent does not expose,
	// rather than silently accepting a name that will never do anything.
	//
	// BEHAVIOUR CHANGE vs the pre-Batch-D.1 implementation, stated here because it is not obvious from
	// the response shape: a MALFORMED entry (scalars value that is not a number, vectors value that is
	// not a colour) now Fails the WHOLE call before any write. The baseline `continue`d past it and
	// applied the rest, so {scalars:{"Tiling":4,"Comment":"x"}} used to return ok:true, applied:1 and
	// now returns ok:false with zero writes. That is deliberate — a partially-applied material edit is
	// indistinguishable from a complete one at the call site — but it IS a change, and it is different
	// from the UNKNOWN-name case, which is still reported in unknownParameters[] and is not fatal
	// unless nothing at all applied. Mirrored in server.py's docstring.
	//
	// BATCH D.1 — finding D-2 (live-found, §7-class silent ignore). This handler used to accept
	// {material, scalars, vectors} and NOTHING else: a caller passing {path, parameter, value} got
	// back ok:true, applied:0 — parameter and value were read by no code at all, and the truthful
	// "you asked for something I do not implement" never reached them. Four defences now, in the
	// order a bad call meets them:
	//   1. RejectUnknownParams (the shared guard, MifBridgeHandlers.h) — an unrecognised key is an
	//      ERROR naming the accepted set, never silence.
	//   2. parameter/value are first-class input, folded into the same maps before anything is
	//      applied, so there is exactly ONE code path that writes to the instance.
	//   3. an empty apply set is an error ("nothing to apply"), never ok:true.
	//   4. a value that parses as neither scalar nor vector is an error NAMING the parameter,
	//      never a skipped map entry (the old `continue` was itself a silent drop).
	// Validation is complete BEFORE the first write, so a rejected call mutates nothing.
	// TODO(audit D.1): this handler never calls MIC->Modify(), so its writes are invisible to the
	// blanket transaction and Ctrl-Z does not restore the previous parameter values. Separate
	// (undo-correctness) bug from D-2; left untouched here so this fix stays one concern.
	void H_set_material_parameter(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("material"), TEXT("materialPath"), TEXT("path"),
			  TEXT("scalars"), TEXT("vectors"),
			  TEXT("parameter"), TEXT("parameterName"), TEXT("name"), TEXT("value") },
			TEXT("material (aliases: materialPath, path), scalars {name:number}, vectors {name:{r,g,b,a}}, ")
			TEXT("and/or the singular pair parameter (aliases: parameterName, name) + value"),
			{ { TEXT("textures"), TEXT("texture parameters are NOT implemented on this endpoint — it applies scalars and vectors only") },
			  { TEXT("texture"),  TEXT("texture parameters are NOT implemented on this endpoint — it applies scalars and vectors only") },
			  { TEXT("switches"), TEXT("static switch parameters are NOT implemented on this endpoint — they need a static-permutation update") } }))
		{
			return;
		}

		const FString MatPath = JStrAny(In, { TEXT("material"), TEXT("materialPath"), TEXT("path") });
		if (MatPath.IsEmpty())
		{
			Fail(Out, TEXT("material is required (aliases: materialPath, path) — the /Game/... path of a MaterialInstanceConstant"));
			return;
		}
		UMaterialInstanceConstant* MIC = LoadObject<UMaterialInstanceConstant>(nullptr, *MatPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!MIC)
		{
			Fail(Out, FString::Printf(
				TEXT("material instance not found (or not a MaterialInstanceConstant): %s — use create_material_instance to derive one you can edit"), *MatPath));
			return;
		}

		// --- Gather: both input shapes collapse into the same two maps ----------
		TSharedRef<FJsonObject> ScalarSet = MakeShared<FJsonObject>();
		TSharedRef<FJsonObject> VectorSet = MakeShared<FJsonObject>();
		const TSharedPtr<FJsonObject>* Src = nullptr;
		if (In->TryGetObjectField(TEXT("scalars"), Src) && Src) { ScalarSet->Values = (*Src)->Values; }
		if (In->TryGetObjectField(TEXT("vectors"), Src) && Src) { VectorSet->Values = (*Src)->Values; }

		const FString Single = JStrAny(In, { TEXT("parameter"), TEXT("parameterName"), TEXT("name") });
		// FJsonObject::HasField is true for an explicit JSON null (JsonObject.h:69-78 only checks
		// the TSharedPtr, and a null parses into a VALID FJsonValueNull), so "present" and
		// "usable" are two different questions and both are asked here.
		const TSharedPtr<FJsonValue> Value = In->TryGetField(TEXT("value"));
		const bool bHasValue = Value.IsValid() && Value->Type != EJson::Null;
		if (!Single.IsEmpty() || In->HasField(TEXT("value")))
		{
			if (Single.IsEmpty())
			{
				Fail(Out, TEXT("value was given without parameter — pass parameter:\"<Name>\" alongside it, or use scalars:{\"<Name>\":number} / vectors:{\"<Name>\":{r,g,b,a}}"));
				return;
			}
			if (!bHasValue)
			{
				Fail(Out, FString::Printf(
					TEXT("parameter '%s' was given without a value — pass value:<number> for a scalar or value:{r,g,b,a} for a vector"), *Single));
				return;
			}
			// Infer the family from the VALUE's JSON type. A numeric string ("0.5") counts as a
			// number because FJsonValueString::TryGetNumber already accepts it (JsonValue.h:134) —
			// anything else is refused BY NAME rather than guessed at.
			double Probe = 0.0;
			if (Value->Type == EJson::Number || (Value->Type == EJson::String && Value->TryGetNumber(Probe)))
			{
				ScalarSet->SetField(Single, Value);
			}
			else if (Value->Type == EJson::Object || Value->Type == EJson::Array)
			{
				VectorSet->SetField(Single, Value);
			}
			else
			{
				Fail(Out, FString::Printf(
					TEXT("cannot tell whether parameter '%s' is a scalar or a vector from a %s value — pass a number (scalar) ")
					TEXT("or {r,g,b,a} / [r,g,b,a] (vector). Booleans (static switches) and asset paths (textures) are not supported here."),
					*Single, JsonTypeName(Value->Type)));
				return;
			}
		}

		if (ScalarSet->Values.Num() == 0 && VectorSet->Values.Num() == 0)
		{
			Fail(Out, TEXT("nothing to apply — pass scalars:{\"<Name>\":number} and/or vectors:{\"<Name>\":{r,g,b,a}}, or the singular parameter:\"<Name>\" + value:<number|{r,g,b,a}>"));
			return;
		}

		// --- Parse: every value validated BEFORE the first write ----------------
		TArray<TPair<FName, float>> ScalarWrites;
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : ScalarSet->Values)
		{
			double V = 0.0;
			if (!Pair.Value.IsValid() || !Pair.Value->TryGetNumber(V))
			{
				Fail(Out, FString::Printf(
					TEXT("scalars['%s'] must be a number (got %s) — colour/vector parameters go in vectors:{}"),
					*Pair.Key, Pair.Value.IsValid() ? JsonTypeName(Pair.Value->Type) : TEXT("null")));
				return;
			}
			ScalarWrites.Emplace(FName(*Pair.Key), (float)V);
		}
		TArray<TPair<FName, FLinearColor>> VectorWrites;
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : VectorSet->Values)
		{
			FLinearColor Colour = FLinearColor::White;
			FString Error;
			if (!JsonToLinearColor(Pair.Value, Colour, Error))
			{
				Fail(Out, FString::Printf(TEXT("vectors['%s']: %s"), *Pair.Key, *Error));
				return;
			}
			VectorWrites.Emplace(FName(*Pair.Key), Colour);
		}

		// --- Apply --------------------------------------------------------------
		// Modify() BEFORE the first write. Without it this handler recorded NOTHING into
		// RunEndpoint's blanket transaction, so UTransBuffer::End saw FTransaction::IsTransient()
		// (EditorTransaction.cpp — "return !bHasChanges"), popped the entry and restored UndoCount.
		// Net effect: the material edit was not undoable AND the next Ctrl-Z silently reverted
		// whatever the user did BEFORE it. undo_transactions then reported success over an edit it
		// had not reverted. One line; the handler is already inside the transaction.
		MIC->Modify();
		int32 ScalarsApplied = 0;
		int32 VectorsApplied = 0;
		TArray<TSharedPtr<FJsonValue>> Unknown;
		for (const TPair<FName, float>& Write : ScalarWrites)
		{
			const FMaterialParameterInfo Info(Write.Key);
			float Existing = 0.f;
			if (!MIC->GetScalarParameterValue(Info, Existing))
			{
				Unknown.Add(MakeShared<FJsonValueString>(Write.Key.ToString()));
				continue;
			}
			MIC->SetScalarParameterValueEditorOnly(Info, Write.Value);
			++ScalarsApplied;
		}
		for (const TPair<FName, FLinearColor>& Write : VectorWrites)
		{
			const FMaterialParameterInfo Info(Write.Key);
			FLinearColor Existing;
			if (!MIC->GetVectorParameterValue(Info, Existing))
			{
				Unknown.Add(MakeShared<FJsonValueString>(Write.Key.ToString()));
				continue;
			}
			MIC->SetVectorParameterValueEditorOnly(Info, Write.Value);
			++VectorsApplied;
		}

		// Applying NOTHING while every name was rejected is a failed call, not a quiet success —
		// the exact ok:true/applied:0 shape finding D-2 caught.
		if (ScalarsApplied == 0 && VectorsApplied == 0)
		{
			TArray<FString> Names;
			for (const TSharedPtr<FJsonValue>& V : Unknown) { Names.Add(V->AsString()); }
			Fail(Out, FString::Printf(
				TEXT("nothing applied — %s exposes none of these parameters: %s. The PARENT material defines what an instance can override; ")
				TEXT("list_object_properties on %s (ScalarParameterValues / VectorParameterValues), or list_material_expressions on the parent, shows the real names."),
				*MIC->GetPathName(), *FString::Join(Names, TEXT(", ")), *MIC->GetPathName()));
			return;
		}

		MIC->PostEditChange();
		MIC->MarkPackageDirty();
		Out->SetStringField(TEXT("material"), MIC->GetPathName());
		Out->SetNumberField(TEXT("applied"), ScalarsApplied + VectorsApplied);
		Out->SetNumberField(TEXT("scalarsApplied"), ScalarsApplied);
		Out->SetNumberField(TEXT("vectorsApplied"), VectorsApplied);
		Out->SetArrayField(TEXT("unknownParameters"), Unknown);
		if (Unknown.Num() > 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("listed parameters are not exposed by the parent material — list_object_properties on the material shows what is"));
		}
	}

	// --- add_foliage_instances ----------------------------------------------
	//   in:  { mesh (staticMesh), label?, folder?, instances:[{x,y,z | location:{}, rotation:{}|yaw, scale|scale:{}}] }
	//   out: { actorPath, label, instanceCount }
	// One actor holding N instanced transforms instead of N actors. This is how foliage is actually
	// done — 90 grass actors is 90 draw setups and 90 outliner rows for something that should be one.
	// Batch D.1 (D-2 sweep): unknown-param guard added (top-level keys only).
	// Batch L: a transform component that cannot be read is now a HARD failure naming
	// instances[N].<field>, rather than a 0 that quietly joined the cluster. Batch M: and the whole
	// array is parsed BEFORE the holder actor is spawned, because the transaction cancel this used to
	// rely on discards the undo entry without rolling the spawn back (PM-007). Still open, as in spawn_many: an entry that is
	// not an object, or one with an unrecognised key, is skipped/ignored without appearing in the
	// response, so instanceCount can still be lower than the array length with no reason given.
	void H_add_foliage_instances(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("mesh"), TEXT("staticMesh"), TEXT("instances"), TEXT("label"), TEXT("folder") },
			TEXT("mesh (alias: staticMesh), instances[] (required), label, folder"),
			{ { TEXT("material"), TEXT("not implemented — the HISM uses the mesh's own materials; override them with set_property on the component afterwards") },
			  { TEXT("transforms"), TEXT("the array parameter is called instances[]") } }))
		{
			return;
		}

		UWorld* World = EditorWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		const FString MeshPath = JStrAny(In, { TEXT("mesh"), TEXT("staticMesh") });
		UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *MeshPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Mesh) { Fail(Out, FString::Printf(TEXT("static mesh not found: %s"), *MeshPath)); return; }

		const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
		if (!In->TryGetArrayField(TEXT("instances"), Items) || !Items || Items->Num() == 0)
		{
			Fail(Out, TEXT("instances[] is required"));
			return;
		}

		// ---- BATCH M: PARSE EVERY INSTANCE BEFORE ANYTHING IS SPAWNED ------------------
		// The per-instance failure below used to happen AFTER the holder actor and its HISM existed,
		// with a comment claiming RunEndpoint's cancel rolled them back. It does not: Cancel discards
		// the undo entry without calling FTransaction::Apply (EditorTransaction.cpp:1387-1437), so a
		// bad instances[7].z left a half-populated foliage actor in the level from a call that said it
		// had failed. Nothing in the parse needs the actor. See docs/01_POSTMORTEMS.md PM-007.
		TArray<FTransform> Xforms;
		Xforms.Reserve(Items->Num());
		{
			int32 ItemIndex = INDEX_NONE;
			for (const TSharedPtr<FJsonValue>& V : *Items)
			{
				++ItemIndex;
				const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
				if (!V.IsValid() || !V->TryGetObject(ObjPtr) || !ObjPtr) { continue; }
				const TSharedRef<FJsonObject> Item = ObjPtr->ToSharedRef();
				FVector Loc, Scale; FRotator Rot;
				FString ItemError;
				if (!ReadTransform(Item, TEXT("instances"), ItemIndex, Loc, Rot, Scale, ItemError))
				{
					// Hard fail, not a skip — and now a hard fail with NOTHING created, rather than one
					// that trusted a rollback the engine never performs.
					Fail(Out, FString::Printf(
						TEXT("%s No foliage was added and no actor was spawned: the whole instances[] array is parsed before anything is created."),
						*ItemError));
					return;
				}
				// Instance transforms are LOCAL to the component, and the holder sits at the origin,
				// so world coordinates pass straight through.
				Xforms.Emplace(Rot, Loc, Scale);
			}
		}

		AActor* Holder = World->SpawnActor<AActor>();
		if (!Holder) { Fail(Out, TEXT("failed to spawn holder actor")); return; }
		Holder->SetActorLabel(JStr(In, TEXT("label"), TEXT("Foliage")));
		const FString Folder = JStr(In, TEXT("folder"));
		if (!Folder.IsEmpty()) { Holder->SetFolderPath(FName(*Folder)); }

		UHierarchicalInstancedStaticMeshComponent* HISM =
			NewObject<UHierarchicalInstancedStaticMeshComponent>(Holder);
		HISM->SetStaticMesh(Mesh);
		HISM->SetMobility(EComponentMobility::Static);
		Holder->SetRootComponent(HISM);
		HISM->RegisterComponent();
		Holder->AddInstanceComponent(HISM);

		// Every transform was validated above, so this loop cannot fail.
		for (const FTransform& Xform : Xforms)
		{
			HISM->AddInstance(Xform);
		}
		const int32 Added = Xforms.Num();
		HISM->MarkRenderStateDirty();

		Out->SetStringField(TEXT("actorPath"), Holder->GetPathName());
		Out->SetStringField(TEXT("label"), Holder->GetActorLabel());
		Out->SetNumberField(TEXT("instanceCount"), Added);
		UE_LOG(LogMifBridge, Log, TEXT("add_foliage_instances: %d instances of %s"), Added, *Mesh->GetName());
	}
}
