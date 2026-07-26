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
		UWorld* AuthoringWorld()
		{
			return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
		}

		AActor* FindActor(UWorld* World, const FString& Query)
		{
			if (!World || Query.IsEmpty()) { return nullptr; }
			for (TActorIterator<AActor> It(World); It; ++It)
			{
				AActor* A = *It;
				if (!A || !IsValid(A)) { continue; }
				// Path OR name OR label — the same three spellings every level endpoint should accept.
				// (delete_level_actor historically only matched name/label, so a path from
				// list_level_actors could not be deleted. Do not repeat that here.)
				if (A->GetPathName() == Query || A->GetName() == Query || A->GetActorLabel() == Query)
				{
					return A;
				}
			}
			return nullptr;
		}

		double JNumFrom(const TSharedRef<FJsonObject>& Obj, const TCHAR* Field, double Def)
		{
			double V = Def;
			return Obj->TryGetNumberField(Field, V) ? V : Def;
		}

		bool ReadTransform(const TSharedRef<FJsonObject>& Item, FVector& OutLoc, FRotator& OutRot, FVector& OutScale)
		{
			const TSharedPtr<FJsonObject>* Sub = nullptr;
			OutLoc = FVector(JNumFrom(Item, TEXT("x"), 0), JNumFrom(Item, TEXT("y"), 0), JNumFrom(Item, TEXT("z"), 0));
			if (Item->TryGetObjectField(TEXT("location"), Sub) && Sub)
			{
				const TSharedRef<FJsonObject> L = Sub->ToSharedRef();
				OutLoc = FVector(JNumFrom(L, TEXT("x"), 0), JNumFrom(L, TEXT("y"), 0), JNumFrom(L, TEXT("z"), 0));
			}
			OutRot = FRotator(0, JNumFrom(Item, TEXT("yaw"), 0), 0);
			if (Item->TryGetObjectField(TEXT("rotation"), Sub) && Sub)
			{
				const TSharedRef<FJsonObject> R = Sub->ToSharedRef();
				OutRot = FRotator(JNumFrom(R, TEXT("x"), 0), JNumFrom(R, TEXT("y"), 0), JNumFrom(R, TEXT("z"), 0));
			}
			const double Uniform = JNumFrom(Item, TEXT("scale"), 1.0);
			OutScale = FVector(Uniform);
			if (Item->TryGetObjectField(TEXT("scale"), Sub) && Sub)
			{
				const TSharedRef<FJsonObject> S = Sub->ToSharedRef();
				OutScale = FVector(JNumFrom(S, TEXT("x"), 1), JNumFrom(S, TEXT("y"), 1), JNumFrom(S, TEXT("z"), 1));
			}
			return true;
		}
	}

	// --- spawn_many ---------------------------------------------------------
	//   in:  { actorClass?, mesh?, folder?, items:[{ x,y,z | location:{}, rotation:{}|yaw, scale|scale:{}, label?, mesh?, material? }] }
	//   out: { spawned, failed, actors:[{label, actorPath}] }
	// One call, N actors. Per-item mesh/material override falls back to the top-level default.
	void H_spawn_many(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = AuthoringWorld();
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
			ReadTransform(Item, Loc, Rot, Scale);

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
	//   in:  { actorPaths?:[...], labelPrefix?, offset:{x,y,z}, rotationOffset?:{}, count?, labelSuffix? }
	//   out: { duplicated, actors[] }
	// Copy a whole set (a finished building) N times with an offset — the thing that makes modular
	// authoring practical instead of re-placing every panel.
	void H_duplicate_actors(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = AuthoringWorld();
		if (!World) { Fail(Out, TEXT("no editor world")); return; }

		TArray<AActor*> Sources;
		const TArray<TSharedPtr<FJsonValue>>* Paths = nullptr;
		if (In->TryGetArrayField(TEXT("actorPaths"), Paths) && Paths)
		{
			for (const TSharedPtr<FJsonValue>& V : *Paths)
			{
				FString P;
				if (V.IsValid() && V->TryGetString(P))
				{
					if (AActor* A = FindActor(World, P)) { Sources.Add(A); }
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
		const TSharedPtr<FJsonObject>* OffObj = nullptr;
		if (In->TryGetObjectField(TEXT("offset"), OffObj) && OffObj)
		{
			const TSharedRef<FJsonObject> O = OffObj->ToSharedRef();
			Offset = FVector(JNum(O, TEXT("x")), JNum(O, TEXT("y")), JNum(O, TEXT("z")));
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
				if (!Copy) { continue; }
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
	}

	// --- create_material_instance -------------------------------------------
	//   in:  { parent, path, scalars?:{name:value}, vectors?:{name:{r,g,b,a}}, textures?:{name:path} }
	//   out: { materialPath }
	// Mints a real MaterialInstanceConstant asset. This is what makes UV tiling fixable: derive an
	// instance from a master material and override its tiling scalar, instead of being stuck with
	// whatever the shipped instance happens to expose.
	void H_create_material_instance(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
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

		int32 Applied = 0;
		TArray<TSharedPtr<FJsonValue>> Unknown;
		const TSharedPtr<FJsonObject>* Scalars = nullptr;
		if (In->TryGetObjectField(TEXT("scalars"), Scalars) && Scalars)
		{
			for (const auto& Pair : (*Scalars)->Values)
			{
				double V = 0.0;
				if (Pair.Value.IsValid() && Pair.Value->TryGetNumber(V))
				{
					MIC->SetScalarParameterValueEditorOnly(FMaterialParameterInfo(FName(*Pair.Key)), (float)V);
					++Applied;
				}
			}
		}
		const TSharedPtr<FJsonObject>* Vectors = nullptr;
		if (In->TryGetObjectField(TEXT("vectors"), Vectors) && Vectors)
		{
			for (const auto& Pair : (*Vectors)->Values)
			{
				const TSharedPtr<FJsonObject>* C = nullptr;
				if (Pair.Value.IsValid() && Pair.Value->TryGetObject(C) && C)
				{
					const TSharedRef<FJsonObject> Col = C->ToSharedRef();
					MIC->SetVectorParameterValueEditorOnly(FMaterialParameterInfo(FName(*Pair.Key)),
						FLinearColor(JNum(Col, TEXT("r")), JNum(Col, TEXT("g")), JNum(Col, TEXT("b")), JNum(Col, TEXT("a"), 1.0)));
					++Applied;
				}
			}
		}

		MIC->PostEditChange();
		FAssetRegistryModule::AssetCreated(MIC);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("materialPath"), MIC->GetPathName());
		Out->SetStringField(TEXT("parent"), Parent->GetPathName());
		Out->SetNumberField(TEXT("parametersApplied"), Applied);
	}

	// --- set_material_parameter ---------------------------------------------
	//   in:  { material, scalars?:{}, vectors?:{} }   out: { applied, unknown[] }
	// Edit an EXISTING MaterialInstanceConstant. Reports parameters the parent does not expose,
	// rather than silently accepting a name that will never do anything.
	void H_set_material_parameter(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		const FString MatPath = JStrAny(In, { TEXT("material"), TEXT("materialPath"), TEXT("path") });
		UMaterialInstanceConstant* MIC = LoadObject<UMaterialInstanceConstant>(nullptr, *MatPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!MIC)
		{
			Fail(Out, FString::Printf(
				TEXT("material instance not found (or not a MaterialInstanceConstant): %s — use create_material_instance to derive one you can edit"), *MatPath));
			return;
		}

		int32 Applied = 0;
		TArray<TSharedPtr<FJsonValue>> Unknown;
		const TSharedPtr<FJsonObject>* Scalars = nullptr;
		if (In->TryGetObjectField(TEXT("scalars"), Scalars) && Scalars)
		{
			for (const auto& Pair : (*Scalars)->Values)
			{
				double V = 0.0;
				if (!Pair.Value.IsValid() || !Pair.Value->TryGetNumber(V)) { continue; }
				float Existing = 0.f;
				const FMaterialParameterInfo Info(FName(*Pair.Key));
				if (!MIC->GetScalarParameterValue(Info, Existing))
				{
					Unknown.Add(MakeShared<FJsonValueString>(Pair.Key));
					continue;
				}
				MIC->SetScalarParameterValueEditorOnly(Info, (float)V);
				++Applied;
			}
		}
		const TSharedPtr<FJsonObject>* Vectors = nullptr;
		if (In->TryGetObjectField(TEXT("vectors"), Vectors) && Vectors)
		{
			for (const auto& Pair : (*Vectors)->Values)
			{
				const TSharedPtr<FJsonObject>* C = nullptr;
				if (!Pair.Value.IsValid() || !Pair.Value->TryGetObject(C) || !C) { continue; }
				const TSharedRef<FJsonObject> Col = C->ToSharedRef();
				const FMaterialParameterInfo Info(FName(*Pair.Key));
				FLinearColor Existing;
				if (!MIC->GetVectorParameterValue(Info, Existing))
				{
					Unknown.Add(MakeShared<FJsonValueString>(Pair.Key));
					continue;
				}
				MIC->SetVectorParameterValueEditorOnly(Info,
					FLinearColor(JNum(Col, TEXT("r")), JNum(Col, TEXT("g")), JNum(Col, TEXT("b")), JNum(Col, TEXT("a"), 1.0)));
				++Applied;
			}
		}

		MIC->PostEditChange();
		MIC->MarkPackageDirty();
		Out->SetStringField(TEXT("material"), MIC->GetPathName());
		Out->SetNumberField(TEXT("applied"), Applied);
		Out->SetArrayField(TEXT("unknownParameters"), Unknown);
		if (Unknown.Num() > 0)
		{
			Out->SetStringField(TEXT("note"),
				TEXT("listed parameters are not exposed by the parent material — list_object_properties on the material shows what is"));
		}
	}

	// --- add_foliage_instances ----------------------------------------------
	//   in:  { mesh, label?, folder?, instances:[{x,y,z,yaw?,scale?}] }
	//   out: { actorPath, instanceCount }
	// One actor holding N instanced transforms instead of N actors. This is how foliage is actually
	// done — 90 grass actors is 90 draw setups and 90 outliner rows for something that should be one.
	void H_add_foliage_instances(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		UWorld* World = AuthoringWorld();
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

		int32 Added = 0;
		for (const TSharedPtr<FJsonValue>& V : *Items)
		{
			const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
			if (!V.IsValid() || !V->TryGetObject(ObjPtr) || !ObjPtr) { continue; }
			const TSharedRef<FJsonObject> Item = ObjPtr->ToSharedRef();
			FVector Loc, Scale; FRotator Rot;
			ReadTransform(Item, Loc, Rot, Scale);
			// Instance transforms are LOCAL to the component, and the holder sits at the origin,
			// so world coordinates pass straight through.
			HISM->AddInstance(FTransform(Rot, Loc, Scale));
			++Added;
		}
		HISM->MarkRenderStateDirty();

		Out->SetStringField(TEXT("actorPath"), Holder->GetPathName());
		Out->SetStringField(TEXT("label"), Holder->GetActorLabel());
		Out->SetNumberField(TEXT("instanceCount"), Added);
		UE_LOG(LogMifBridge, Log, TEXT("add_foliage_instances: %d instances of %s"), Added, *Mesh->GetName());
	}
}
