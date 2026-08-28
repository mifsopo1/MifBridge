// MifBridge — MATERIAL GRAPH AUTHORING (the audit's flagship Tier-0 category, Batch D).
//
// The full loop: create_material / create_material_function mint the assets;
// add_material_expression / connect_material_expressions / connect_material_property /
// delete_material_expression edit the graph; list_material_expressions is the numeric read-back;
// layout_material_expressions tidies node positions for humans; recompile_material applies the
// edits to the renderer; shader_compile_status is the async poll half.
//
// Specs: docs/audit/work/D_materials_rendering.md (all ten entries Phase-2 adversarially
// verified; the CORRECTED verdicts on recompile_material are BINDING and implemented below).
//
// THE axis-wide cooked constraint (spec negative #3): UMaterialExpression is
// UCLASS(abstract, Optional, ...) (MaterialExpression.h:183-184) and the expression collection
// lives in UMaterialEditorOnlyData, itself UCLASS(MinimalAPI, Optional) (Material.h:309-310).
// 'Optional' objects are STRIPPED from cooked packages, so a cooked base-game material has NO
// graph — and UMaterial::GetExpressions() derefs GetEditorOnlyData() with no null check
// (Material.cpp:1426-1429), so calling it on a cooked material is a CRASH, not an empty list.
// Every graph endpoint below therefore refuses (or, for the read, degrades honestly) BEFORE
// touching the collection.
//
// Transaction buckets (registered in MifBridgeCommon.cpp):
//   create_material, create_material_function — SELF-MANAGED: new package/UObject creation with
//     explicit AssetCreated + MarkPackageDirty (the create_material_instance precedent,
//     MifBridgeAuthoring.cpp:280-353, runs untransacted for the same reason).
//   recompile_material — SELF-MANAGED: shader-map regeneration must never sit inside a blanket
//     transaction (shader-state teardown on the undo stack is the same crash family as a full
//     Blueprint compile inside an outer transaction).
//   list_material_expressions, shader_compile_status — read-only: pure queries.
//   everything else — transacted (RunEndpoint's blanket transaction; handlers Modify() first).
//
// Module note: this file is the reason "MaterialEditor" joined MifBridge.Build.cs — the first
// new module dependency since the audit began. UMaterialEditingLibrary is class-level
// MATERIALEDITOR_API (MaterialEditingLibrary.h:57), editor-only, engine-core. This plugin is an
// editor-only module (never a runtime dependency of a cooked mod — Build.cs header), which is
// why the WITH_EDITORONLY_DATA members used below need no preprocessor guards.
#include "MifBridgeHandlers.h"
#include "MifBridgeVersion.h"
// FStringOutputDevice MOVED between the two engines this plugin targets:
//   5.3: declared in Containers/UnrealString.h, reached transitively through CoreMinimal
//   5.7: promoted to its own header, Misc/StringOutputDevice.h, and no longer pulled in for free
//
// So the include is REQUIRED on 5.7 and IMPOSSIBLE on 5.3 - that path does not exist there, and an
// unguarded include is a fatal C1083. The Curfew session hit the 5.7 half and could not see the 5.3
// half; building here caught it. A fifth shape for docs/02_GOTCHAS.md section 14: same type, same
// name, different HEADER.
#if MIF_ENGINE_5_7_PLUS
#include "Misc/StringOutputDevice.h"
#endif

// list_material_parameters: FMaterialCachedParameters survives cook, which is the whole reason this
// endpoint is worth having on a shipped game.
#include "MaterialTypes.h"                      // FMaterialParameterInfo / Metadata / Value
#include "Materials/MaterialInstance.h"         // UMaterialInstance - current vs default values
#include "Materials/MaterialInterface.h"        // GetAllParametersOfType
#include "MifBridgeLog.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Editor.h"                              // FEditorDelegates::RefreshEditor (UNREALED_API, Editor.h:197)
#include "EditorSupportDelegates.h"              // FEditorSupportDelegates::RedrawAllViewports (ENGINE_API, :39)
#include "Engine/EngineTypes.h"                  // EBlendMode
#include "Engine/Texture.h"
#include "Factories/MaterialFactoryNew.h"
#include "Factories/MaterialFunctionFactoryNew.h"
#include "MaterialDomain.h"                      // EMaterialDomain (Runtime/Engine/Public/MaterialDomain.h:12-30)
#include "MaterialEditingLibrary.h"              // MATERIALEDITOR_API statics — the NEW module dep
#include "MaterialShared.h"                      // FMaterialUpdateContext (ENGINE_API, MaterialShared.h:2779+)
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"
#include "Materials/MaterialFunction.h"
#include "Materials/MaterialInstance.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"
#include "Particles/ParticleSystemComponent.h"   // bIsViewRelevanceDirty (recompile core replica)
#include "SceneTypes.h"                          // EMaterialProperty (SceneTypes.h:159-200)
#include "ShaderCompiler.h"                      // GShaderCompilingManager (extern ENGINE_API, :928)
#include "UObject/Package.h"
#include "UObject/ObjectRedirector.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UObjectIterator.h"
#include "UObject/UnrealType.h"                  // FProperty::ImportText_Direct / ExportTextItem_Direct

namespace MifBridge
{
	namespace
	{
		const TCHAR* MaterialParamTypeName(EMaterialParameterType T)
		{
			switch (T)
			{
			case EMaterialParameterType::Scalar:               return TEXT("scalar");
			case EMaterialParameterType::Vector:               return TEXT("vector");
			case EMaterialParameterType::DoubleVector:         return TEXT("doubleVector");
			case EMaterialParameterType::Texture:              return TEXT("texture");
			case EMaterialParameterType::Font:                 return TEXT("font");
			case EMaterialParameterType::RuntimeVirtualTexture:return TEXT("runtimeVirtualTexture");
			case EMaterialParameterType::SparseVolumeTexture:  return TEXT("sparseVolumeTexture");
			case EMaterialParameterType::StaticSwitch:         return TEXT("staticSwitch");
			case EMaterialParameterType::StaticComponentMask:  return TEXT("staticComponentMask");
			default:                                           return TEXT("unknown");
			}
		}

		const TCHAR* MaterialAssociationName(EMaterialParameterAssociation A)
		{
			switch (A)
			{
			case LayerParameter: return TEXT("layer");
			case BlendParameter: return TEXT("blend");
			default:             return TEXT("global");
			}
		}

		// SWITCH ON Type, NEVER GUESS. FMaterialParameterValue::AsScalar() and its siblings are
		// check()ed on Type - asking a texture parameter for its scalar TERMINATES the editor rather
		// than returning an error. Every branch below reads only the union member its type owns.
		void WriteParamValue(const TSharedRef<FJsonObject>& J, const TCHAR* Field,
			const FMaterialParameterValue& V)
		{
			switch (V.Type)
			{
			case EMaterialParameterType::Scalar:
				J->SetNumberField(Field, V.AsScalar());
				break;
			case EMaterialParameterType::Vector:
			{
				const FLinearColor C = V.AsLinearColor();
				TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
				O->SetNumberField(TEXT("r"), C.R); O->SetNumberField(TEXT("g"), C.G);
				O->SetNumberField(TEXT("b"), C.B); O->SetNumberField(TEXT("a"), C.A);
				J->SetObjectField(Field, O);
				break;
			}
			case EMaterialParameterType::DoubleVector:
			{
				const FVector4d D = V.AsVector4d();
				TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
				O->SetNumberField(TEXT("x"), D.X); O->SetNumberField(TEXT("y"), D.Y);
				O->SetNumberField(TEXT("z"), D.Z); O->SetNumberField(TEXT("w"), D.W);
				J->SetObjectField(Field, O);
				break;
			}
			case EMaterialParameterType::StaticSwitch:
				J->SetBoolField(Field, V.AsStaticSwitch());
				break;
			case EMaterialParameterType::StaticComponentMask:
			{
				const FStaticComponentMaskValue M = V.AsStaticComponentMask();
				TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
				O->SetBoolField(TEXT("r"), M.R); O->SetBoolField(TEXT("g"), M.G);
				O->SetBoolField(TEXT("b"), M.B); O->SetBoolField(TEXT("a"), M.A);
				J->SetObjectField(Field, O);
				break;
			}
			case EMaterialParameterType::Texture:
			case EMaterialParameterType::RuntimeVirtualTexture:
			case EMaterialParameterType::SparseVolumeTexture:
			case EMaterialParameterType::Font:
			{
				// AsTextureObject covers the object-valued types and returns null rather than
				// asserting, so it is safe for all four.
				UObject* Obj = V.AsTextureObject();
				J->SetStringField(Field, Obj ? *Obj->GetPathName() : TEXT(""));
				break;
			}
			default:
				J->SetStringField(Field, TEXT("(unreadable type)"));
				break;
			}
		}
	}

	// --- list_material_parameters --------------------------------------------
	//   in:  { path, types?:[...], group? }
	//   out: { path, kind, isCooked, count, byType:{...}, parameters:[...] }
	//
	// The only way to ask a COOKED material what it exposes. list_material_expressions is correct to
	// report numExpressions:0 on shipped content - cooking strips the expression graph - and
	// list_object_properties on an instance only ever returns what someone already overrode. The
	// cached parameter table survives cook, so this works where those cannot.
	void H_list_material_parameters(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("assetPath"), TEXT("types"), TEXT("group") },
			TEXT("path (aliases: material, assetPath) of a Material or MaterialInstance; "
				 "types:[scalar|vector|texture|staticSwitch|doubleVector|font|runtimeVirtualTexture|"
				 "sparseVolumeTexture|staticComponentMask] to filter; group to filter by parameter group"),
			{ { TEXT("parameterName"), TEXT("this LISTS parameters - to read one value use get_property on a material instance, and to write one use set_material_parameter") },
			  { TEXT("includeExpressions"), TEXT("that is list_material_expressions, which returns nothing on a COOKED material - this endpoint exists precisely because the cached parameter table survives cook and the expression graph does not") } }))
		{
			return;
		}

		const FString Path = JStrAny(In, { TEXT("path"), TEXT("material"), TEXT("assetPath") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required (a Material or MaterialInstance)"));
			return;
		}
		UObject* Asset = LoadAssetLenient(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s"), *Path));
			return;
		}
		UMaterialInterface* Mat = Cast<UMaterialInterface>(Asset);
		if (!Mat)
		{
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s, not a Material or MaterialInstance."),
				*Path, *Asset->GetClass()->GetName()));
			return;
		}

		// Optional type filter.
		TSet<FString> WantTypes;
		const TArray<TSharedPtr<FJsonValue>>* TypeArr = nullptr;
		if (JArray(In, TEXT("types"), TypeArr) && TypeArr)
		{
			for (int32 i = 0; i < TypeArr->Num(); ++i)
			{
				FString T;
				if (!(*TypeArr)[i].IsValid() || !(*TypeArr)[i]->TryGetString(T) || T.IsEmpty())
				{
					Fail(Out, FString::Printf(TEXT("types[%d] is not a non-empty string."), i));
					return;
				}
				WantTypes.Add(T.ToLower());
			}
		}
		const FString WantGroup = JStr(In, TEXT("group"));

		static const EMaterialParameterType kTypes[] = {
			EMaterialParameterType::Scalar, EMaterialParameterType::Vector,
			EMaterialParameterType::DoubleVector, EMaterialParameterType::Texture,
			EMaterialParameterType::Font, EMaterialParameterType::RuntimeVirtualTexture,
			EMaterialParameterType::SparseVolumeTexture, EMaterialParameterType::StaticSwitch,
			EMaterialParameterType::StaticComponentMask,
		};

		UMaterialInstance* AsInstance = Cast<UMaterialInstance>(Mat);
		TArray<TSharedPtr<FJsonValue>> All;
		TSharedRef<FJsonObject> ByType = MakeShared<FJsonObject>();

		for (EMaterialParameterType T : kTypes)
		{
			const FString TypeName = MaterialParamTypeName(T);
			if (WantTypes.Num() > 0 && !WantTypes.Contains(TypeName.ToLower())) { continue; }

			TMap<FMaterialParameterInfo, FMaterialParameterMetadata> Params;
			Mat->GetAllParametersOfType(T, Params);
			int32 Count = 0;
			for (const TPair<FMaterialParameterInfo, FMaterialParameterMetadata>& P : Params)
			{
				if (!WantGroup.IsEmpty() && P.Value.Group.ToString() != WantGroup) { continue; }

				TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
				J->SetStringField(TEXT("name"), P.Key.Name.ToString());
				J->SetStringField(TEXT("type"), TypeName);
				// ASSOCIATION AND INDEX ARE NOT DECORATION. A LayerParameter reported as a Global
				// makes every later set_material_parameter build the wrong FMaterialParameterInfo,
				// get false back, and lead the caller to conclude the parameter does not exist.
				J->SetStringField(TEXT("association"), MaterialAssociationName(P.Key.Association));
				J->SetNumberField(TEXT("index"), P.Key.Index);
				if (!P.Value.Group.IsNone())
				{
					J->SetStringField(TEXT("group"), P.Value.Group.ToString());
				}
				if (!P.Value.Description.IsEmpty())
				{
					J->SetStringField(TEXT("description"), P.Value.Description);
				}
				J->SetNumberField(TEXT("sortPriority"), P.Value.SortPriority);
				WriteParamValue(J, TEXT("value"), P.Value.Value);

				// On an INSTANCE, GetAllParametersOfType reports the effective value. Whether that
				// value is this instance's own override or inherited from the parent decides whether
				// resetting it does anything, so it is reported rather than left to be guessed.
				if (AsInstance)
				{
					J->SetBoolField(TEXT("overriddenOnThisInstance"), P.Value.bOverride);
				}
				All.Add(MakeShared<FJsonValueObject>(J));
				++Count;
			}
			if (Count > 0) { ByType->SetNumberField(TypeName, Count); }
		}

		Out->SetStringField(TEXT("path"), Mat->GetPathName());
		Out->SetStringField(TEXT("kind"), AsInstance ? TEXT("MaterialInstance") : TEXT("Material"));
		if (AsInstance && AsInstance->Parent)
		{
			Out->SetStringField(TEXT("parent"), AsInstance->Parent->GetPathName());
		}
		// Stated because it is the whole point: this works on cooked content, where
		// list_material_expressions correctly reports nothing.
		Out->SetBoolField(TEXT("survivesCook"), true);
		Out->SetNumberField(TEXT("count"), All.Num());
		Out->SetObjectField(TEXT("byType"), ByType);
		Out->SetArrayField(TEXT("parameters"), All);
		if (All.Num() == 0)
		{
			// "No parameters" and "filtered everything out" look identical otherwise.
			Out->SetStringField(TEXT("note"),
				WantTypes.Num() > 0 || !WantGroup.IsEmpty()
					? TEXT("nothing matched the types/group filter - call again without it to see everything")
					: TEXT("this material genuinely exposes no parameters (it is not a filter artefact)"));
		}
	}

	namespace
	{
		// --- Cooked / container-origin detection --------------------------------
		// PROMOTED in Batch N to MifBridgeCommon.cpp as MifBridge::IsCookedOrContainerPackage
		// (declared in MifBridgeHandlers.h), body unchanged, because edit_container and
		// reset_property_to_default must refuse a cooked target on exactly the same test. A second
		// cooked check under a second name is the PM-005 failure the compiler never reports: the two
		// would have been free to disagree about whether a container-only asset is editable, in the
		// one project where that is the common case. Do NOT re-add a local copy.

		// One shared refusal text so every graph endpoint says exactly the same thing (spec
		// negative #3 requires the error to state the stripping fact AND the two viable routes).
		FString CookedGraphError(const UObject* Asset)
		{
			return FString::Printf(
				TEXT("material '%s' is cooked (expression graph stripped — UMaterialExpression is UCLASS 'Optional', ")
				TEXT("so cooked packages ship NO graph to read or edit). Author a NEW material with create_material, ")
				TEXT("or derive an editable instance from this one with create_material_instance."),
				*Asset->GetPathName());
		}

		// Refuse-and-fail wrapper for the mutating graph endpoints. list_material_expressions
		// deliberately does NOT use it (it degrades honestly instead of refusing).
		bool RefuseIfCookedGraph(const UObject* Asset, const TSharedRef<FJsonObject>& Out)
		{
			if (IsCookedOrContainerPackage(Asset->GetPackage()))
			{
				Fail(Out, CookedGraphError(Asset));
				return true;
			}
			return false;
		}

		// Editor-only data present? Belt-and-braces beside the cooked check: GetExpressions()
		// derefs GetEditorOnlyData() unguarded (Material.cpp:1426-1429), so a null here means
		// "no graph", never "empty graph".
		bool HasGraphData(const UMaterial* Material) { return Material->GetEditorOnlyData() != nullptr; }
		bool HasGraphData(const UMaterialFunction* Function) { return Function->GetEditorOnlyData() != nullptr; }

		// --- Asset resolution ----------------------------------------------------
		// Accepts /Game/Path/M_Foo and /Game/Path/M_Foo.M_Foo (the ResolveBlueprint spelling
		// rule); follows redirectors. Exactly one of OutMaterial/OutFunction is set on success.
		bool ResolveMaterialOrFunction(const FString& InPath, UMaterial*& OutMaterial,
			UMaterialFunction*& OutFunction, FString& OutError)
		{
			OutMaterial = nullptr;
			OutFunction = nullptr;
			FString P = InPath;
			P.TrimStartAndEndInline();
			if (P.IsEmpty())
			{
				OutError = TEXT("path is required (a UMaterial or UMaterialFunction asset path)");
				return false;
			}

			UObject* Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj && !P.Contains(TEXT(".")))
			{
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Obj))
			{
				Obj = Redirector->DestinationObject;
			}
			if (!Obj)
			{
				OutError = FString::Printf(TEXT("asset not found: %s (bare package paths like /Game/A/M_Foo are accepted)"), *P);
				return false;
			}

			OutMaterial = Cast<UMaterial>(Obj);
			OutFunction = Cast<UMaterialFunction>(Obj);
			if (!OutMaterial && !OutFunction)
			{
				// Instances are the #1 wrong-asset case here — steer, don't just refuse.
				const bool bInstance = Obj->IsA<UMaterialInstance>();
				OutError = FString::Printf(
					TEXT("path must be a Material or MaterialFunction, got %s: %s%s"),
					*Obj->GetClass()->GetName(), *Obj->GetPathName(),
					bInstance ? TEXT(" — material instances have no graph; use set_material_parameter, or edit the parent material")
							  : TEXT(""));
				return false;
			}
			return true;
		}

		// ResolveMaterialOrFunction over the standard path aliases + Fail(Out) on error.
		bool ResolveMaterialOrFunctionField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			UMaterial*& OutMaterial, UMaterialFunction*& OutFunction)
		{
			const FString Path = JStrAny(In, { TEXT("path"), TEXT("material"), TEXT("materialPath") });
			FString Error;
			if (!ResolveMaterialOrFunction(Path, OutMaterial, OutFunction, Error))
			{
				Fail(Out, Error);
				return false;
			}
			return true;
		}

		// --- Expression class resolution ------------------------------------------
		// Accepts "ScalarParameter", "MaterialExpressionScalarParameter", or
		// "/Script/Engine.MaterialExpressionScalarParameter" (spec resolution rule). Unknown
		// class => error listing the 10 nearest catalogue matches by edit distance, so a typo'd
		// class costs one round-trip instead of a docs hunt.
		int32 EditDistance(const FString& A, const FString& B)
		{
			const int32 LenA = A.Len();
			const int32 LenB = B.Len();
			TArray<int32> Prev, Curr;
			Prev.SetNum(LenB + 1);
			Curr.SetNum(LenB + 1);
			for (int32 j = 0; j <= LenB; ++j) { Prev[j] = j; }
			for (int32 i = 1; i <= LenA; ++i)
			{
				Curr[0] = i;
				for (int32 j = 1; j <= LenB; ++j)
				{
					const int32 Cost = (FChar::ToLower(A[i - 1]) == FChar::ToLower(B[j - 1])) ? 0 : 1;
					Curr[j] = FMath::Min3(Prev[j] + 1, Curr[j - 1] + 1, Prev[j - 1] + Cost);
				}
				Swap(Prev, Curr);
			}
			return Prev[LenB];
		}

		UClass* ResolveExpressionClass(const FString& InName, FString& OutError)
		{
			FString N = InName;
			N.TrimStartAndEndInline();
			// Path form: keep only the terminal object name ("/Script/Engine.MaterialExpressionX").
			int32 DotIdx = INDEX_NONE;
			if (N.FindLastChar(TEXT('.'), DotIdx))
			{
				N.MidInline(DotIdx + 1);
			}
			if (N.IsEmpty())
			{
				OutError = TEXT("'class' is required and must name a UMaterialExpression subclass (e.g. ScalarParameter, TextureSample, Multiply)");
				return nullptr;
			}
			// Editor-familiar aliases from the axis catalogue (D_materials_rendering.md:49,:59).
			if (N.Equals(TEXT("Lerp"), ESearchCase::IgnoreCase)) { N = TEXT("LinearInterpolate"); }
			if (N.Equals(TEXT("TexCoord"), ESearchCase::IgnoreCase)) { N = TEXT("TextureCoordinate"); }

			const FString Prefixed = N.StartsWith(TEXT("MaterialExpression"), ESearchCase::IgnoreCase)
				? N : (TEXT("MaterialExpression") + N);

			UClass* Match = nullptr;
			TArray<FString> ShortNames;   // catalogue for the nearest-match suggestions
			for (TObjectIterator<UClass> It; It; ++It)
			{
				UClass* Cls = *It;
				if (!Cls->IsChildOf(UMaterialExpression::StaticClass())
					|| Cls->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated | CLASS_NewerVersionExists))
				{
					continue;
				}
				const FString ClsName = Cls->GetName();
				if (ClsName.Equals(Prefixed, ESearchCase::IgnoreCase) || ClsName.Equals(N, ESearchCase::IgnoreCase))
				{
					Match = Cls;
					break;
				}
				FString Short = ClsName;
				Short.RemoveFromStart(TEXT("MaterialExpression"));
				ShortNames.Add(Short);
			}
			if (Match)
			{
				return Match;
			}

			FString Query = N;
			Query.RemoveFromStart(TEXT("MaterialExpression"), ESearchCase::IgnoreCase);
			ShortNames.Sort([&Query](const FString& A, const FString& B)
			{
				return EditDistance(Query, A) < EditDistance(Query, B);
			});
			const int32 NumSuggestions = FMath::Min(10, ShortNames.Num());
			TArray<FString> Nearest;
			for (int32 i = 0; i < NumSuggestions; ++i) { Nearest.Add(ShortNames[i]); }
			OutError = FString::Printf(
				TEXT("unknown expression class '%s' — nearest matches: %s (short names and full MaterialExpression* names are both accepted)"),
				*InName, *FString::Join(Nearest, TEXT(", ")));
			return nullptr;
		}

		// --- Expression addressing (Batch D.1, finding D-1) --------------------------
		// Before D.1 an expression could only be addressed by its UObject name
		// (MaterialExpressionScalarParameter_0) — the handle add_material_expression returns.
		// That is a correct handle and stays rule 1, but live testing showed callers reach first
		// for the spellings a HUMAN uses: the ParameterName they just set ("Tint"), or the class
		// when the graph holds exactly one of them ("Multiply"). The house alias rule
		// (docs/02_GOTCHAS.md §1) says accept those — and accept the hit ONLY if exactly one
		// candidate matches, so a graph that genuinely holds two "Tint"s is never silently
		// redirected to the wrong one.

		// The ParameterName an expression exposes, or NAME_None.
		//
		// Engine-canonical answer first: UMaterialExpression::HasAParameterName / GetParameterName
		// (MaterialExpression.h:551-561 — public, WITH_EDITOR, inline virtuals, so the call is a
		// vtable dispatch needing no exported symbol). The engine's own comment there says these
		// exist precisely because "multiple class have ParameterName but are not
		// UMaterialExpressionParameter due to class hierarchy". Verified overriders in 5.3
		// (grep of Runtime/Engine/Classes/Materials): UMaterialExpressionParameter,
		// UMaterialExpressionTextureSampleParameter, UMaterialExpressionFontSampleParameter,
		// UMaterialExpressionCollectionParameter,
		// UMaterialExpressionRuntimeVirtualTextureSampleParameter and
		// UMaterialExpressionSparseVolumeTextureSample — six families, NOT one base class, which
		// is exactly why a Cast<UMaterialExpressionParameter> would have missed five of them.
		//
		// Reflection fallback second, for classes that carry a ParameterName UPROPERTY WITHOUT
		// overriding the virtual. That is not hypothetical here: the Landscape expressions
		// (MaterialExpressionLandscapeLayerWeight/Switch/Sample.h — 'FName ParameterName' UPROPERTY,
		// no HasAParameterName override) are all over this project's landscape master material.
		// A *static* FName ParameterName (MaterialExpressionLandscapeVisibilityMask.h:22) cannot be
		// a UPROPERTY, so it correctly never appears here.
		FName ExpressionParameterName(const UMaterialExpression* Expression)
		{
			if (!Expression)
			{
				return NAME_None;
			}
			if (Expression->HasAParameterName())
			{
				return Expression->GetParameterName();
			}
			if (const FNameProperty* NameProp = CastField<FNameProperty>(
				Expression->GetClass()->FindPropertyByName(FName(TEXT("ParameterName")))))
			{
				return NameProp->GetPropertyValue_InContainer(Expression);
			}
			return NAME_None;
		}

		// Class short name as rule 3 accepts it: "Multiply" for UMaterialExpressionMultiply.
		FString ExpressionClassShortName(const UMaterialExpression* Expression)
		{
			FString Short = Expression->GetClass()->GetName();
			Short.RemoveFromStart(TEXT("MaterialExpression"));
			return Short;
		}

		// One human-readable catalogue row, teaching every form that would have worked.
		FString DescribeExpression(const UMaterialExpression* Expression)
		{
			const FName Param = ExpressionParameterName(Expression);
			return Param.IsNone()
				? FString::Printf(TEXT("%s (%s)"), *Expression->GetName(), *ExpressionClassShortName(Expression))
				: FString::Printf(TEXT("%s (%s, ParameterName='%s')"), *Expression->GetName(),
					*ExpressionClassShortName(Expression), *Param.ToString());
		}

		// --- Expression lookup: object name -> ParameterName -> unique class ----------
		// Precedence, first rule that produces EXACTLY ONE candidate wins:
		//   1. exact UObject name — never redirected, never ambiguous (names are unique per outer);
		//   2. ParameterName (all six families above + the reflection fallback);
		//   3. class short name, accepted only when the graph holds exactly one node of that class
		//      ("Multiply" -> MaterialExpressionMultiply_0). The same Lerp/TexCoord editor aliases
		//      add_material_expression's `class` accepts are honoured, so a node added as "Lerp" is
		//      addressable as "Lerp".
		// Two candidates under rule 2 or 3 => ERROR listing them, never a coin flip.
		//
		// NO "ClassName#index" form: the UObject name IS the indexed form the engine already
		// guarantees (MaterialExpressionMultiply_0/_1), and a second index over the graph array
		// would disagree with that suffix the moment a node is deleted — two spellings for one
		// slot, one of them wrong.
		// Miss => error listing the first 20 live rows WITH their parameter names, so the caller
		// self-corrects without a second probe.
		UMaterialExpression* FindExpressionByName(UMaterial* Material, UMaterialFunction* Function,
			const FString& Name, FString& OutError)
		{
			TConstArrayView<TObjectPtr<UMaterialExpression>> Expressions =
				Material ? Material->GetExpressions() : Function->GetExpressions();

			FString Query = Name;
			Query.TrimStartAndEndInline();
			if (Query.IsEmpty())
			{
				OutError = TEXT("expression name is required (object name from add_material_expression, a ParameterName, or a unique class short name)");
				return nullptr;
			}

			// Rule 1 — exact object name.
			for (const TObjectPtr<UMaterialExpression>& Expr : Expressions)
			{
				if (Expr && Expr->GetName().Equals(Query, ESearchCase::IgnoreCase))
				{
					return Expr;
				}
			}

			const FString OwnerPath = Material ? Material->GetPathName() : Function->GetPathName();

			// Rule 2 — ParameterName.
			TArray<UMaterialExpression*> ParamHits;
			for (const TObjectPtr<UMaterialExpression>& Expr : Expressions)
			{
				if (!Expr) { continue; }
				const FName Param = ExpressionParameterName(Expr.Get());
				// IsNone() first: a NAME_None parameter must never be addressable as "None".
				if (!Param.IsNone() && Param.ToString().Equals(Query, ESearchCase::IgnoreCase))
				{
					ParamHits.Add(Expr);
				}
			}
			if (ParamHits.Num() == 1)
			{
				return ParamHits[0];
			}
			if (ParamHits.Num() > 1)
			{
				TArray<FString> Candidates;
				for (UMaterialExpression* Expr : ParamHits) { Candidates.Add(DescribeExpression(Expr)); }
				OutError = FString::Printf(
					TEXT("expression '%s' is AMBIGUOUS in %s — %d expressions share that ParameterName: %s. ")
					TEXT("Address one by its exact object name (the value add_material_expression returned)."),
					*Query, *OwnerPath, ParamHits.Num(), *FString::Join(Candidates, TEXT(", ")));
				return nullptr;
			}

			// Rule 3 — unique class short name (same editor aliases as ResolveExpressionClass).
			FString ClassQuery = Query;
			ClassQuery.RemoveFromStart(TEXT("MaterialExpression"), ESearchCase::IgnoreCase);
			if (ClassQuery.Equals(TEXT("Lerp"), ESearchCase::IgnoreCase)) { ClassQuery = TEXT("LinearInterpolate"); }
			if (ClassQuery.Equals(TEXT("TexCoord"), ESearchCase::IgnoreCase)) { ClassQuery = TEXT("TextureCoordinate"); }
			TArray<UMaterialExpression*> ClassHits;
			for (const TObjectPtr<UMaterialExpression>& Expr : Expressions)
			{
				if (Expr && ExpressionClassShortName(Expr.Get()).Equals(ClassQuery, ESearchCase::IgnoreCase))
				{
					ClassHits.Add(Expr);
				}
			}
			if (ClassHits.Num() == 1)
			{
				return ClassHits[0];
			}
			if (ClassHits.Num() > 1)
			{
				TArray<FString> Candidates;
				for (UMaterialExpression* Expr : ClassHits) { Candidates.Add(DescribeExpression(Expr)); }
				OutError = FString::Printf(
					TEXT("expression '%s' is AMBIGUOUS in %s — %d expressions are of class %s, so the class name ")
					TEXT("does not identify one: %s. Address one by its exact object name."),
					*Query, *OwnerPath, ClassHits.Num(), *ClassQuery, *FString::Join(Candidates, TEXT(", ")));
				return nullptr;
			}

			TArray<FString> Valid;
			for (const TObjectPtr<UMaterialExpression>& Expr : Expressions)
			{
				if (Expr)
				{
					Valid.Add(DescribeExpression(Expr.Get()));
					if (Valid.Num() >= 20) { break; }
				}
			}
			OutError = FString::Printf(
				TEXT("expression '%s' not found in %s — accepted forms: the exact object name, a ParameterName, ")
				TEXT("or a class short name when the graph holds exactly ONE node of that class. Valid names%s: %s"),
				*Query, *OwnerPath,
				Expressions.Num() > 20 ? TEXT(" (first 20)") : TEXT(""),
				Valid.Num() > 0 ? *FString::Join(Valid, TEXT(", ")) : TEXT("(graph is empty)"));
			return nullptr;
		}

		// --- Output pin naming -------------------------------------------------------
		// Mirror of the engine's file-local GetExpressionOutputName (MaterialEditingLibrary.cpp:
		// 808-834): named outputs by name, masked outputs as R/G/B/A, unnamed as "".
		FString OutputPinName(const FExpressionOutput& Output)
		{
			if (!Output.OutputName.IsNone())
			{
				return Output.OutputName.ToString();
			}
			if (Output.Mask)
			{
				if (Output.MaskR && !Output.MaskG && !Output.MaskB && !Output.MaskA) { return TEXT("R"); }
				if (!Output.MaskR && Output.MaskG && !Output.MaskB && !Output.MaskA) { return TEXT("G"); }
				if (!Output.MaskR && !Output.MaskG && Output.MaskB && !Output.MaskA) { return TEXT("B"); }
				if (!Output.MaskR && !Output.MaskG && !Output.MaskB && Output.MaskA) { return TEXT("A"); }
			}
			return FString();
		}

		TArray<FString> OutputPinNames(UMaterialExpression* Expression)
		{
			TArray<FString> Names;
			for (const FExpressionOutput& Output : Expression->Outputs)
			{
				const FString Name = OutputPinName(Output);
				Names.Add(Name.IsEmpty() ? TEXT("(unnamed/first)") : Name);
			}
			return Names;
		}

		// --- JSON value -> FProperty import text -------------------------------------
		// The properties{} object rides the same FProperty ImportText machinery as set_property
		// (MifBridgeNodes5.cpp): scratch-copy import so a REJECTED value never wipes the live
		// property (ImportText_Direct parses in place and can clear the destination before
		// deciding the text is bad — the destructive-failed-call bug set_property fixed).
		FString NumberToImportText(double V)
		{
			// Integer-valued numbers print without a decimal point: FIntProperty::ImportText
			// stops at '.', so "4.0" against an int32 UPROPERTY would import as garbage.
			if (V == FMath::RoundToDouble(V) && FMath::Abs(V) < 9.0e15)
			{
				return FString::Printf(TEXT("%lld"), (int64)V);
			}
			return FString::SanitizeFloat(V);
		}

		bool JsonValueToImportText(const TSharedPtr<FJsonValue>& Value, FString& OutText, FString& OutError)
		{
			if (!Value.IsValid())
			{
				OutError = TEXT("null value");
				return false;
			}
			switch (Value->Type)
			{
			case EJson::String:
				OutText = Value->AsString();
				return true;
			case EJson::Boolean:
				// FBoolProperty::ImportText is case-sensitive: True/False, not true/false
				// (the NormalizeBoolLiteral lesson, MifBridgeNodes5.cpp:22-30).
				OutText = Value->AsBool() ? TEXT("True") : TEXT("False");
				return true;
			case EJson::Number:
				OutText = NumberToImportText(Value->AsNumber());
				return true;
			case EJson::Object:
			{
				// Struct value: {"r":1,"g":0.5} => "(r=1,g=0.5)". Struct-member FName lookup is
				// case-insensitive, so lowercase JSON keys match R/G/B/A UPROPERTYs.
				TArray<FString> Parts;
				for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Value->AsObject()->Values)
				{
					FString Inner;
					if (!JsonValueToImportText(Pair.Value, Inner, OutError))
					{
						return false;
					}
					// Quote string leaves that would confuse the (K=V,...) grammar.
					if (Pair.Value->Type == EJson::String
						&& (Inner.Contains(TEXT(",")) || Inner.Contains(TEXT(")")) || Inner.Contains(TEXT("(")) || Inner.Contains(TEXT(" "))))
					{
						Inner = FString::Printf(TEXT("\"%s\""), *Inner);
					}
					Parts.Add(FString::Printf(TEXT("%s=%s"), *Pair.Key, *Inner));
				}
				OutText = FString::Printf(TEXT("(%s)"), *FString::Join(Parts, TEXT(",")));
				return true;
			}
			default:
				OutError = TEXT("array values are not supported here — set container elements via set_property");
				return false;
			}
		}

		// Import one named property onto an expression. Assumes the NAME was already validated
		// (see H_add_material_expression's pre-validation — errors after creation would leave a
		// half-added node inside the already-open blanket transaction).
		bool ImportExpressionProperty(UMaterialExpression* Expression, FProperty* Prop,
			const TSharedPtr<FJsonValue>& Value, FString& OutError)
		{
			FString ImportStr;
			if (!JsonValueToImportText(Value, ImportStr, OutError))
			{
				return false;
			}
			if (CastField<FBoolProperty>(Prop) && Value->Type == EJson::String)
			{
				const FString T = ImportStr.TrimStartAndEnd();
				if (T.Equals(TEXT("true"), ESearchCase::IgnoreCase)) { ImportStr = TEXT("True"); }
				else if (T.Equals(TEXT("false"), ESearchCase::IgnoreCase)) { ImportStr = TEXT("False"); }
			}
			// Batch L, defect 2. This converter is a sibling of MifBridgeNodes5.cpp's — it emits text
			// from the JSON value's shape rather than the destination property's type — so a string
			// value reaches ImportText_Direct unchecked, exactly as override_inherited_component's did
			// when "not-a-float" imported as 0.0 and reported success. The SHARED validator runs here
			// too rather than a fourth copy of the rules (PM-005).
			FString TypeError;
			if (!ValidatePropertyText(Prop, ImportStr, Prop->GetName(), TypeError))
			{
				OutError = TypeError;
				return false;
			}

			void* LeafAddr = Prop->ContainerPtrToValuePtr<void>(Expression);
			FStringOutputDevice ErrText;
			const int32 ValueSize = Prop->GetSize();
			void* Scratch = FMemory::Malloc(FMath::Max(ValueSize, 1), Prop->GetMinAlignment());
			Prop->InitializeValue(Scratch);
			Prop->CopyCompleteValue(Scratch, LeafAddr);   // partial struct imports keep unset members

			const TCHAR* Result = Prop->ImportText_Direct(*ImportStr, Scratch, Expression, PPF_None, &ErrText);
			if (Result != nullptr)
			{
				Prop->CopyCompleteValue(LeafAddr, Scratch);   // publish only on successful parse
			}
			Prop->DestroyValue(Scratch);
			FMemory::Free(Scratch);

			if (Result == nullptr)
			{
				OutError = FString::Printf(TEXT("property '%s' rejected value '%s': %s"),
					*Prop->GetName(), *ImportStr, *ErrText);
				return false;
			}
			return true;
		}

		// --- EMaterialProperty resolution ---------------------------------------------
		// Accepted list per the spec's param table (MP_ prefix optional, case-insensitive);
		// deprecated/meta values get the dedicated "not connectable in 5.3" error instead of
		// "unknown". ClearCoat/ClearCoatRoughness map to the CustomData pins, matching the
		// material editor's display names.
		struct FMaterialPropertyName { const TCHAR* Name; EMaterialProperty Value; };
		const FMaterialPropertyName GConnectableProperties[] =
		{
			{ TEXT("EmissiveColor"),      MP_EmissiveColor },
			{ TEXT("Opacity"),            MP_Opacity },
			{ TEXT("OpacityMask"),        MP_OpacityMask },
			{ TEXT("BaseColor"),          MP_BaseColor },
			{ TEXT("Metallic"),           MP_Metallic },
			{ TEXT("Specular"),           MP_Specular },
			{ TEXT("Roughness"),          MP_Roughness },
			{ TEXT("Anisotropy"),         MP_Anisotropy },
			{ TEXT("Normal"),             MP_Normal },
			{ TEXT("Tangent"),            MP_Tangent },
			{ TEXT("WorldPositionOffset"),MP_WorldPositionOffset },
			{ TEXT("SubsurfaceColor"),    MP_SubsurfaceColor },
			{ TEXT("ClearCoat"),          MP_CustomData0 },
			{ TEXT("CustomData0"),        MP_CustomData0 },
			{ TEXT("ClearCoatRoughness"), MP_CustomData1 },
			{ TEXT("CustomData1"),        MP_CustomData1 },
			{ TEXT("AmbientOcclusion"),   MP_AmbientOcclusion },
			{ TEXT("Refraction"),         MP_Refraction },
			{ TEXT("CustomizedUVs0"),     MP_CustomizedUVs0 },
			{ TEXT("CustomizedUVs1"),     MP_CustomizedUVs1 },
			{ TEXT("CustomizedUVs2"),     MP_CustomizedUVs2 },
			{ TEXT("CustomizedUVs3"),     MP_CustomizedUVs3 },
			{ TEXT("CustomizedUVs4"),     MP_CustomizedUVs4 },
			{ TEXT("CustomizedUVs5"),     MP_CustomizedUVs5 },
			{ TEXT("CustomizedUVs6"),     MP_CustomizedUVs6 },
			{ TEXT("CustomizedUVs7"),     MP_CustomizedUVs7 },
			{ TEXT("PixelDepthOffset"),   MP_PixelDepthOffset },
			{ TEXT("ShadingModel"),       MP_ShadingModel },
			{ TEXT("Displacement"),       MP_Displacement },
		};
		// Names the enum still carries but that no 5.3 graph can drive.
		const TCHAR* GNotConnectableProperties[] =
		{
			TEXT("WorldDisplacement"), TEXT("TessellationMultiplier"), TEXT("MaterialAttributes"),
			TEXT("CustomOutput"), TEXT("MAX"), TEXT("FrontMaterial"), TEXT("SurfaceThickness"),
			TEXT("DiffuseColor"), TEXT("SpecularColor"),
		};

		FString ConnectablePropertyList()
		{
			TArray<FString> Names;
			for (const FMaterialPropertyName& Entry : GConnectableProperties)
			{
				Names.Add(Entry.Name);
			}
			return FString::Join(Names, TEXT(", "));
		}

		bool ResolveMaterialProperty(const FString& InName, EMaterialProperty& OutProperty, FString& OutError)
		{
			FString N = InName;
			N.TrimStartAndEndInline();
			N.RemoveFromStart(TEXT("MP_"), ESearchCase::IgnoreCase);
			if (N.IsEmpty())
			{
				OutError = FString::Printf(TEXT("'property' is required — accepted: %s"), *ConnectablePropertyList());
				return false;
			}
			for (const FMaterialPropertyName& Entry : GConnectableProperties)
			{
				if (N.Equals(Entry.Name, ESearchCase::IgnoreCase))
				{
					OutProperty = Entry.Value;
					return true;
				}
			}
			for (const TCHAR* Dead : GNotConnectableProperties)
			{
				if (N.Equals(Dead, ESearchCase::IgnoreCase))
				{
					OutError = FString::Printf(TEXT("property '%s' is not connectable in 5.3 — accepted: %s"),
						*InName, *ConnectablePropertyList());
					return false;
				}
			}
			OutError = FString::Printf(TEXT("unknown material property '%s' — accepted (MP_ prefix optional): %s"),
				*InName, *ConnectablePropertyList());
			return false;
		}

		// --- Shader-compile telemetry ---------------------------------------------------
		// Shared by recompile_material's response and shader_compile_status itself, so both
		// report identical field names and the poll loop needs no translation.
		void WriteShaderCompileFields(const TSharedRef<FJsonObject>& Out)
		{
			if (!GShaderCompilingManager)
			{
				Out->SetBoolField(TEXT("compiling"), false);
				Out->SetNumberField(TEXT("numRemainingJobs"), 0);
				return;
			}
			// IsCompiling/GetNumRemainingJobs are header-inline (ShaderCompiler.h:770-773,
			// :798-801) and compile into this module; the getters they call are ENGINE_API.
			Out->SetBoolField(TEXT("compiling"), GShaderCompilingManager->IsCompiling());
			Out->SetNumberField(TEXT("numRemainingJobs"), GShaderCompilingManager->GetNumRemainingJobs());
			Out->SetNumberField(TEXT("numOutstandingJobs"), GShaderCompilingManager->GetNumOutstandingJobs());
			Out->SetNumberField(TEXT("numPendingJobs"), GShaderCompilingManager->GetNumPendingJobs());
		}

		// --- New-asset path validation (create_material / create_material_function) ------
		// Returns false + Fail(Out) unless Path is a fresh /Game/ package path. AssetName out.
		// Was ValidateNewAssetPath — the SAME NAME as a different function in MifBridgeUserTypes.cpp
		// with a different signature AND a different failure convention (that one returns an error
		// string; this one writes Fail(Out) itself). Co-located by a unity blob they would merge into
		// one overload set, and a future 3-argument call added to this file would compile and silently
		// run UserTypes' validation policy. Renamed apart rather than merged, because the two really do
		// enforce different rules.
		bool ValidateNewMaterialAssetPath(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
			FString& OutPath, FString& OutAssetName)
		{
			OutPath = JStrAny(In, { TEXT("path"), TEXT("assetPath") });
			OutPath.TrimStartAndEndInline();
			if (OutPath.IsEmpty())
			{
				Fail(Out, TEXT("path is required (a new /Game/... asset path)"));
				return false;
			}
			if (!OutPath.StartsWith(TEXT("/Game/")))
			{
				Fail(Out, TEXT("path must start with /Game/"));
				return false;
			}
			OutAssetName = FPackageName::GetLongPackageAssetName(OutPath);
			if (OutAssetName.IsEmpty() || !FPackageName::IsValidLongPackageName(OutPath))
			{
				Fail(Out, FString::Printf(TEXT("invalid asset path: %s"), *OutPath));
				return false;
			}
			// Existing asset (on disk OR already in memory) — never silently overwrite.
			if (FPackageName::DoesPackageExist(OutPath)
				|| StaticFindObject(UObject::StaticClass(), nullptr, *(OutPath + TEXT(".") + OutAssetName)) != nullptr)
			{
				Fail(Out, FString::Printf(
					TEXT("asset already exists at %s — use a new path or delete_asset first"), *OutPath));
				return false;
			}
			return true;
		}
	}

	// --- create_material -------------------------------------------------------------
	//   in:  { path (assetPath), domain? = Surface, blendMode? = Opaque, initialTexture? }
	//   out: { materialPath, domain, blendMode, numExpressions, compiling, numRemainingJobs }
	// Bucket: SELF-MANAGED — new package/UObject creation + initial shader compile enqueue
	// (create_material_instance precedent: untransacted, explicit AssetCreated + MarkPackageDirty).
	// Spec: D_materials_rendering.md create_material (Phase-2 CONFIRMED). Factory is MinimalAPI
	// with no method-level export (negative #5), so FactoryCreateNew is called through the
	// factory POINTER (virtual dispatch) — a qualified UMaterialFactoryNew::FactoryCreateNew
	// call would need the missing import and fail to link.
	void H_create_material(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("domain"), TEXT("materialDomain"),
			  TEXT("blendMode"), TEXT("initialTexture") },
			TEXT("path (alias: assetPath), domain (alias: materialDomain), blendMode, initialTexture")))
		{
			return;
		}

		FString AssetPath, AssetName;
		if (!ValidateNewMaterialAssetPath(In, Out, AssetPath, AssetName))
		{
			return;
		}

		// Domain / blend mode parsed BEFORE any object is created, so a bad enum string leaves
		// nothing behind. Accepted lists are the non-hidden 5.3.2 values the Phase-2 verdict
		// verified (MaterialDomain.h:12-30; EngineTypes.h:249-263 minus Substrate).
		struct FDomainName { const TCHAR* Name; EMaterialDomain Value; };
		static const FDomainName Domains[] =
		{
			{ TEXT("Surface"), MD_Surface }, { TEXT("DeferredDecal"), MD_DeferredDecal },
			{ TEXT("LightFunction"), MD_LightFunction }, { TEXT("Volume"), MD_Volume },
			{ TEXT("PostProcess"), MD_PostProcess }, { TEXT("UI"), MD_UI },
		};
		struct FBlendName { const TCHAR* Name; EBlendMode Value; };
		static const FBlendName Blends[] =
		{
			{ TEXT("Opaque"), BLEND_Opaque }, { TEXT("Masked"), BLEND_Masked },
			{ TEXT("Translucent"), BLEND_Translucent }, { TEXT("Additive"), BLEND_Additive },
			{ TEXT("Modulate"), BLEND_Modulate }, { TEXT("AlphaComposite"), BLEND_AlphaComposite },
			{ TEXT("AlphaHoldout"), BLEND_AlphaHoldout },
		};

		FString DomainStr = JStrAny(In, { TEXT("domain"), TEXT("materialDomain") }, TEXT("Surface"));
		DomainStr.RemoveFromStart(TEXT("MD_"), ESearchCase::IgnoreCase);
		EMaterialDomain Domain = MD_Surface;
		bool bFound = false;
		for (const FDomainName& Entry : Domains)
		{
			if (DomainStr.Equals(Entry.Name, ESearchCase::IgnoreCase)) { Domain = Entry.Value; bFound = true; break; }
		}
		if (!bFound)
		{
			Fail(Out, FString::Printf(
				TEXT("unknown domain '%s' — accepted: Surface, DeferredDecal, LightFunction, Volume, PostProcess, UI"), *DomainStr));
			return;
		}

		FString BlendStr = JStr(In, TEXT("blendMode"), TEXT("Opaque"));
		BlendStr.RemoveFromStart(TEXT("BLEND_"), ESearchCase::IgnoreCase);
		EBlendMode Blend = BLEND_Opaque;
		bFound = false;
		for (const FBlendName& Entry : Blends)
		{
			if (BlendStr.Equals(Entry.Name, ESearchCase::IgnoreCase)) { Blend = Entry.Value; bFound = true; break; }
		}
		if (!bFound)
		{
			Fail(Out, FString::Printf(
				TEXT("unknown blendMode '%s' — accepted: Opaque, Masked, Translucent, Additive, Modulate, AlphaComposite, AlphaHoldout"), *BlendStr));
			return;
		}

		UTexture* InitialTexture = nullptr;
		const FString TexturePath = JStr(In, TEXT("initialTexture"));
		if (!TexturePath.IsEmpty())
		{
			InitialTexture = LoadObject<UTexture>(nullptr, *TexturePath, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!InitialTexture)
			{
				Fail(Out, FString::Printf(TEXT("initialTexture not found: %s"), *TexturePath));
				return;
			}
		}

		UPackage* Package = CreatePackage(*AssetPath);
		if (!Package)
		{
			Fail(Out, TEXT("failed to create package"));
			return;
		}

		UMaterialFactoryNew* Factory = NewObject<UMaterialFactoryNew>();
		Factory->InitialTexture = InitialTexture;   // factory auto-adds a TextureSample wired to BaseColor/Normal
		UObject* Created = Factory->FactoryCreateNew(
			UMaterial::StaticClass(), Package, FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional, nullptr, GWarn);
		UMaterial* Material = Cast<UMaterial>(Created);
		if (!Material)
		{
			Fail(Out, TEXT("factory returned null"));
			return;
		}

		// Direct UPROPERTY writes (Material.h:449/:453 — public data members), then one
		// PostEditChange to build resources and ENQUEUE the initial shader compile. The enqueue
		// is asynchronous — the response carries the poll fields so callers never block here.
		Material->MaterialDomain = Domain;
		Material->BlendMode = Blend;
		Material->PreEditChange(nullptr);
		Material->PostEditChange();

		FAssetRegistryModule::AssetCreated(Material);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("materialPath"), Material->GetPathName());
		for (const FDomainName& Entry : Domains) { if (Entry.Value == Domain) { Out->SetStringField(TEXT("domain"), Entry.Name); break; } }
		for (const FBlendName& Entry : Blends) { if (Entry.Value == Blend) { Out->SetStringField(TEXT("blendMode"), Entry.Name); break; } }
		Out->SetNumberField(TEXT("numExpressions"), HasGraphData(Material) ? Material->GetExpressions().Num() : 0);
		Out->SetStringField(TEXT("hint"), TEXT("shader compile is asynchronous — poll shader_compile_status"));
		WriteShaderCompileFields(Out);
		UE_LOG(LogMifBridge, Log, TEXT("create_material: %s"), *Material->GetPathName());
	}

	// --- create_material_function -------------------------------------------------------
	//   in:  { path (assetPath), description?, exposeToLibrary? }
	//   out: { functionPath, numExpressions }
	// Bucket: SELF-MANAGED — new package/UObject creation (same precedent as create_material).
	// Spec: D_materials_rendering.md create_material_function (Phase-2 CONFIRMED; Description
	// at MaterialFunction.h:52 and bExposeToLibrary at :60 are plain UPROPERTY data members).
	void H_create_material_function(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("description"), TEXT("exposeToLibrary") },
			TEXT("path (alias: assetPath), description, exposeToLibrary"),
			{ { TEXT("kind"), TEXT("not implemented — nothing in this build authors material layers; layer/layerBlend function kinds are read-only here") } }))
		{
			return;
		}

		FString AssetPath, AssetName;
		if (!ValidateNewMaterialAssetPath(In, Out, AssetPath, AssetName))
		{
			return;
		}

		UPackage* Package = CreatePackage(*AssetPath);
		if (!Package)
		{
			Fail(Out, TEXT("failed to create package"));
			return;
		}

		// MinimalAPI factory, no method-level export (negative #5): call through the pointer.
		UMaterialFunctionFactoryNew* Factory = NewObject<UMaterialFunctionFactoryNew>();
		UObject* Created = Factory->FactoryCreateNew(
			UMaterialFunction::StaticClass(), Package, FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional, nullptr, GWarn);
		UMaterialFunction* Function = Cast<UMaterialFunction>(Created);
		if (!Function)
		{
			Fail(Out, TEXT("factory returned null"));
			return;
		}

		const FString Description = JStr(In, TEXT("description"));
		if (!Description.IsEmpty())
		{
			Function->Description = Description;
		}
		if (JHasAny(In, { TEXT("exposeToLibrary") }))
		{
			Function->bExposeToLibrary = JBool(In, TEXT("exposeToLibrary"), false);
		}
		Function->PostEditChange();

		FAssetRegistryModule::AssetCreated(Function);
		Package->MarkPackageDirty();

		Out->SetStringField(TEXT("functionPath"), Function->GetPathName());
		Out->SetNumberField(TEXT("numExpressions"), HasGraphData(Function) ? Function->GetExpressions().Num() : 0);
		UE_LOG(LogMifBridge, Log, TEXT("create_material_function: %s"), *Function->GetPathName());
	}

	// --- add_material_expression -----------------------------------------------------------
	//   in:  { path (material, materialPath), class (expressionClass, type), x? (nodePosX, posX),
	//          y? (nodePosY, posY), properties? (props) {name: value}, asset? (selectedAsset) }
	//   out: { expressionName, expressionClass, expressionIndex, x, y, propertiesApplied }
	// Bucket: transacted — object creation inside an existing asset; nothing compiles until
	// recompile_material, so a node add is a safe undo step.
	// Spec: add_material_expression (Phase-2 CONFIRMED — CreateMaterialExpressionEx is pure
	// object-model NewObject + AddExpression, no editor window, no dialogs, no waits).
	void H_add_material_expression(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("materialPath"),
			  TEXT("class"), TEXT("expressionClass"), TEXT("type"),
			  TEXT("x"), TEXT("nodePosX"), TEXT("posX"), TEXT("y"), TEXT("nodePosY"), TEXT("posY"),
			  TEXT("properties"), TEXT("props"), TEXT("asset"), TEXT("selectedAsset") },
			TEXT("path (aliases: material, materialPath), class (aliases: expressionClass, type), x (aliases: nodePosX, posX), y (aliases: nodePosY, posY), properties (alias: props), asset (alias: selectedAsset)")))
		{
			return;
		}

		UMaterial* Material = nullptr;
		UMaterialFunction* Function = nullptr;
		if (!ResolveMaterialOrFunctionField(In, Out, Material, Function))
		{
			return;
		}
		UObject* Asset = Material ? (UObject*)Material : (UObject*)Function;
		if (RefuseIfCookedGraph(Asset, Out))
		{
			return;
		}

		FString Error;
		UClass* ExpressionClass = ResolveExpressionClass(
			JStrAny(In, { TEXT("class"), TEXT("expressionClass"), TEXT("type") }), Error);
		if (!ExpressionClass)
		{
			Fail(Out, Error);
			return;
		}

		const int32 X = JIntAny(In, { TEXT("x"), TEXT("nodePosX"), TEXT("posX") }, 0);
		const int32 Y = JIntAny(In, { TEXT("y"), TEXT("nodePosY"), TEXT("posY") }, 0);

		UObject* SelectedAsset = nullptr;
		const FString SelectedAssetPath = JStrAny(In, { TEXT("asset"), TEXT("selectedAsset") });
		if (!SelectedAssetPath.IsEmpty())
		{
			SelectedAsset = StaticLoadObject(UObject::StaticClass(), nullptr, *SelectedAssetPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!SelectedAsset)
			{
				Fail(Out, FString::Printf(TEXT("asset not found: %s"), *SelectedAssetPath));
				return;
			}
		}

		// Pre-validate EVERY properties{} name and value kind BEFORE the expression exists.
		// RunEndpoint's blanket transaction commits even when the handler fails, so an unknown
		// property discovered after creation would leave a half-configured node behind —
		// validation first keeps a failed call non-mutating. Unknown name is a hard error
		// (silent-ignore is the audit's #1 bug class, 03_GAPS_AND_RISKS.md §7.1).
		const TSharedPtr<FJsonObject>* PropsObj = nullptr;
		if (!In->TryGetObjectField(TEXT("properties"), PropsObj) || !PropsObj)
		{
			In->TryGetObjectField(TEXT("props"), PropsObj);
		}
		TArray<TPair<FProperty*, TSharedPtr<FJsonValue>>> ToApply;
		if (PropsObj)
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*PropsObj)->Values)
			{
				FProperty* Prop = ExpressionClass->FindPropertyByName(FName(*Pair.Key));
				if (!Prop)
				{
					Fail(Out, FString::Printf(
						TEXT("unknown property '%s' on %s — property names come from the expression class's UPROPERTYs ")
						TEXT("(e.g. ScalarParameter: ParameterName, DefaultValue, SliderMin, SliderMax)"),
						*Pair.Key, *ExpressionClass->GetName()));
					return;
				}
				if (!Pair.Value.IsValid() || Pair.Value->Type == EJson::Array || Pair.Value->Type == EJson::Null)
				{
					Fail(Out, FString::Printf(
						TEXT("property '%s': array/null values are not supported here — use set_property on the expression subobject for containers"),
						*Pair.Key));
					return;
				}
				ToApply.Emplace(Prop, Pair.Value);
			}
		}

		// Undo capture: the expression array lives in the Optional editor-only-data subobject,
		// which is its own UObject — Modify() both, or Ctrl-Z restores the asset but not the list.
		Asset->Modify();
		if (UObject* EditorOnly = Material ? (UObject*)Material->GetEditorOnlyData() : (UObject*)Function->GetEditorOnlyData())
		{
			EditorOnly->Modify();
		}

		UMaterialExpression* Expression = UMaterialEditingLibrary::CreateMaterialExpressionEx(
			Material, Function, ExpressionClass, SelectedAsset, X, Y);
		if (!Expression)
		{
			Fail(Out, FString::Printf(TEXT("engine refused to create a %s here"), *ExpressionClass->GetName()));
			return;
		}

		int32 Applied = 0;
		for (const TPair<FProperty*, TSharedPtr<FJsonValue>>& Pair : ToApply)
		{
			FString ImportError;
			if (!ImportExpressionProperty(Expression, Pair.Key, Pair.Value, ImportError))
			{
				// Value failed to PARSE (names were pre-validated) — remove the node again so a
				// failed call leaves the graph exactly as it found it.
				if (Material) { UMaterialEditingLibrary::DeleteMaterialExpression(Material, Expression); }
				else { UMaterialEditingLibrary::DeleteMaterialExpressionInFunction(Function, Expression); }
				Fail(Out, FString::Printf(TEXT("%s — expression NOT added"), *ImportError));
				return;
			}
			++Applied;
		}
		if (Applied > 0)
		{
			Expression->PostEditChange();
		}

		int32 Index = INDEX_NONE;
		{
			TConstArrayView<TObjectPtr<UMaterialExpression>> Expressions =
				Material ? Material->GetExpressions() : Function->GetExpressions();
			Index = Expressions.IndexOfByKey(Expression);
		}

		Out->SetStringField(TEXT("expressionName"), Expression->GetName());
		Out->SetStringField(TEXT("expressionClass"), Expression->GetClass()->GetName());
		Out->SetNumberField(TEXT("expressionIndex"), Index);
		Out->SetNumberField(TEXT("x"), X);
		Out->SetNumberField(TEXT("y"), Y);
		Out->SetNumberField(TEXT("propertiesApplied"), Applied);
		UE_LOG(LogMifBridge, Log, TEXT("add_material_expression: %s -> %s"),
			*Expression->GetClass()->GetName(), *Asset->GetPathName());
	}

	// --- connect_material_expressions ---------------------------------------------------------
	//   in:  { path (material), from (fromExpression), fromOutput? (fromOutputName, "" = first;
	//          masked outputs accept R/G/B/A), to (toExpression), toInput? (toInputName, "" = first) }
	//   from/to accept THREE forms (Batch D.1, FindExpressionByName): the exact object name
	//   ("MaterialExpressionMultiply_0", what add_material_expression returns), a ParameterName
	//   ("Tint"), or a class short name when the graph holds exactly one node of that class
	//   ("Multiply"). Two candidates under either alias => error listing them, never a guess.
	//   out: { connected, from, fromOutput, to, toInput } — from/to echo the resolved OBJECT names.
	// Bucket: transacted — pure in-asset pointer rewiring (Input->Connect), undo-safe.
	// Spec: connect_material_expressions (Phase-2 CONFIRMED, impl cpp:677-692 hazard-free).
	void H_connect_material_expressions(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("materialPath"),
			  TEXT("from"), TEXT("fromExpression"), TEXT("fromOutput"), TEXT("fromOutputName"),
			  TEXT("to"), TEXT("toExpression"), TEXT("toInput"), TEXT("toInputName") },
			TEXT("path (aliases: material, materialPath), from (alias: fromExpression), fromOutput (alias: fromOutputName), to (alias: toExpression), toInput (alias: toInputName)")))
		{
			return;
		}

		UMaterial* Material = nullptr;
		UMaterialFunction* Function = nullptr;
		if (!ResolveMaterialOrFunctionField(In, Out, Material, Function))
		{
			return;
		}
		UObject* Asset = Material ? (UObject*)Material : (UObject*)Function;
		if (RefuseIfCookedGraph(Asset, Out))
		{
			return;
		}

		const FString FromName = JStrAny(In, { TEXT("from"), TEXT("fromExpression") });
		const FString ToName = JStrAny(In, { TEXT("to"), TEXT("toExpression") });
		if (FromName.IsEmpty() || ToName.IsEmpty())
		{
			Fail(Out, TEXT("from and to are required — each accepts an expression object name (from add_material_expression / list_material_expressions), a ParameterName, or a class short name that is unique in this graph"));
			return;
		}
		FString Error;
		UMaterialExpression* From = FindExpressionByName(Material, Function, FromName, Error);
		if (!From) { Fail(Out, Error); return; }
		UMaterialExpression* To = FindExpressionByName(Material, Function, ToName, Error);
		if (!To) { Fail(Out, Error); return; }

		const FString FromOutput = JStrAny(In, { TEXT("fromOutput"), TEXT("fromOutputName") });
		const FString ToInput = JStrAny(In, { TEXT("toInput"), TEXT("toInputName") });

		// The connection is stored as an FExpressionInput on the TO node — Modify() it for undo.
		To->Modify();
		Asset->Modify();

		if (!UMaterialEditingLibrary::ConnectMaterialExpressions(From, FromOutput, To, ToInput))
		{
			// Echo BOTH pin lists so the caller can self-correct without a probe round-trip.
			const TArray<FString> InputNames = UMaterialEditingLibrary::GetMaterialExpressionInputNames(To);
			Fail(Out, FString::Printf(
				TEXT("connect failed — check pin names. %s inputs: [%s]; %s outputs: [%s] (empty string = first pin)"),
				*To->GetName(), *FString::Join(InputNames, TEXT(", ")),
				*From->GetName(), *FString::Join(OutputPinNames(From), TEXT(", "))));
			return;
		}

		Out->SetBoolField(TEXT("connected"), true);
		Out->SetStringField(TEXT("from"), From->GetName());
		Out->SetStringField(TEXT("fromOutput"), FromOutput.IsEmpty() ? TEXT("(first)") : *FromOutput);
		Out->SetStringField(TEXT("to"), To->GetName());
		Out->SetStringField(TEXT("toInput"), ToInput.IsEmpty() ? TEXT("(first)") : *ToInput);
	}

	// --- connect_material_property ---------------------------------------------------------------
	//   in:  { path (material), from (fromExpression), fromOutput? (fromOutputName),
	//          property (materialProperty; MP_ prefix optional, case-insensitive) }
	//   from accepts the exact object name, a ParameterName, or a class short name unique in the
	//   graph (Batch D.1 — see FindExpressionByName); ambiguity errors with the candidates.
	//   out: { connected, from, property } — from echoes the resolved OBJECT name.
	// Bucket: transacted — same pointer-rewiring rationale as connect_material_expressions.
	// Spec: connect_material_property (Phase-2 CONFIRMED). The engine impl (cpp:656-676)
	// requires FromExpression->GetOuter() to BE the UMaterial — guaranteed here because the
	// expression is resolved out of that material's own collection.
	void H_connect_material_property(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("materialPath"),
			  TEXT("from"), TEXT("fromExpression"), TEXT("fromOutput"), TEXT("fromOutputName"),
			  TEXT("property"), TEXT("materialProperty") },
			TEXT("path (aliases: material, materialPath), from (alias: fromExpression), fromOutput (alias: fromOutputName), property (alias: materialProperty)")))
		{
			return;
		}

		UMaterial* Material = nullptr;
		UMaterialFunction* Function = nullptr;
		if (!ResolveMaterialOrFunctionField(In, Out, Material, Function))
		{
			return;
		}
		if (Function)
		{
			// Functions have no property pins; their outputs are FunctionOutput EXPRESSIONS.
			Fail(Out, FString::Printf(
				TEXT("%s is a MaterialFunction — functions have no material property pins; add a FunctionOutput ")
				TEXT("expression and connect into it with connect_material_expressions instead"),
				*Function->GetPathName()));
			return;
		}
		if (RefuseIfCookedGraph(Material, Out))
		{
			return;
		}

		EMaterialProperty Property = MP_MAX;
		FString Error;
		if (!ResolveMaterialProperty(JStrAny(In, { TEXT("property"), TEXT("materialProperty") }), Property, Error))
		{
			Fail(Out, Error);
			return;
		}

		const FString FromName = JStrAny(In, { TEXT("from"), TEXT("fromExpression") });
		if (FromName.IsEmpty())
		{
			Fail(Out, TEXT("from is required — an expression object name, a ParameterName, or a class short name that is unique in this graph"));
			return;
		}
		UMaterialExpression* From = FindExpressionByName(Material, nullptr, FromName, Error);
		if (!From)
		{
			Fail(Out, Error);
			return;
		}

		// Property FExpressionInputs live on the Optional editor-only-data object — Modify()
		// it too or the undo restores the material without the binding.
		Material->Modify();
		if (UObject* EditorOnly = Material->GetEditorOnlyData())
		{
			EditorOnly->Modify();
		}

		const FString FromOutput = JStrAny(In, { TEXT("fromOutput"), TEXT("fromOutputName") });
		if (!UMaterialEditingLibrary::ConnectMaterialProperty(From, FromOutput, Property))
		{
			Fail(Out, FString::Printf(
				TEXT("connect failed — check the property is enabled for this material domain/blend mode ")
				TEXT("(e.g. Opacity needs a Translucent blend mode) and the output pin name; %s outputs: [%s]"),
				*From->GetName(), *FString::Join(OutputPinNames(From), TEXT(", "))));
			return;
		}

		Out->SetBoolField(TEXT("connected"), true);
		Out->SetStringField(TEXT("from"), From->GetName());
		Out->SetStringField(TEXT("fromOutput"), FromOutput.IsEmpty() ? TEXT("(first)") : *FromOutput);
		for (const FMaterialPropertyName& Entry : GConnectableProperties)
		{
			if (Entry.Value == Property)
			{
				Out->SetStringField(TEXT("property"), Entry.Name);
				break;
			}
		}
	}

	// --- delete_material_expression ----------------------------------------------------------------
	//   in:  { path (material), expression? (name), all? (deleteAll) — exactly one of the two }
	//   expression accepts the exact object name, a ParameterName, or a class short name unique in
	//   the graph (Batch D.1 — see FindExpressionByName); ambiguity errors with the candidates
	//   rather than deleting a coin-flip node.
	//   out: { deleted, remaining }
	// Bucket: transacted — in-asset object removal; the library handles disconnection.
	// Spec: delete_material_expression (Phase-2 CONFIRMED, all four library signatures verbatim).
	void H_delete_material_expression(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("materialPath"),
			  TEXT("expression"), TEXT("name"), TEXT("all"), TEXT("deleteAll") },
			TEXT("path (aliases: material, materialPath), expression (alias: name), all (alias: deleteAll)")))
		{
			return;
		}

		UMaterial* Material = nullptr;
		UMaterialFunction* Function = nullptr;
		if (!ResolveMaterialOrFunctionField(In, Out, Material, Function))
		{
			return;
		}
		UObject* Asset = Material ? (UObject*)Material : (UObject*)Function;
		if (RefuseIfCookedGraph(Asset, Out))
		{
			return;
		}

		const FString Name = JStrAny(In, { TEXT("expression"), TEXT("name") });
		const bool bAll = JBoolAny(In, { TEXT("all"), TEXT("deleteAll") }, false);
		if (bAll && !Name.IsEmpty())
		{
			Fail(Out, TEXT("pass either expression or all=true, not both (ambiguous)"));
			return;
		}
		if (!bAll && Name.IsEmpty())
		{
			Fail(Out, TEXT("expression is required (object name, ParameterName, or a class short name unique in this graph) — or pass all=true to clear the graph"));
			return;
		}

		// Deleting disconnects every OTHER expression that referenced the victim, so capture
		// them all for undo, plus the editor-only-data object that owns the array.
		Asset->Modify();
		if (UObject* EditorOnly = Material ? (UObject*)Material->GetEditorOnlyData() : (UObject*)Function->GetEditorOnlyData())
		{
			EditorOnly->Modify();
		}
		{
			TConstArrayView<TObjectPtr<UMaterialExpression>> Expressions =
				Material ? Material->GetExpressions() : Function->GetExpressions();
			for (const TObjectPtr<UMaterialExpression>& Expr : Expressions)
			{
				if (Expr) { Expr->Modify(); }
			}
		}

		const int32 Before = (Material ? Material->GetExpressions() : Function->GetExpressions()).Num();
		if (bAll)
		{
			// DO NOT call UMaterialEditingLibrary::DeleteAllMaterialExpressions. It is BROKEN, in both
			// 5.3 and 5.7, and it fails quietly:
			//
			//     for (UMaterialExpression* Expression : Material->GetExpressions())
			//         DeleteMaterialExpression(Material, Expression);
			//
			// GetExpressions() hands back a TConstArrayView over the LIVE array, and
			// DeleteMaterialExpression removes from that same array. So the loop is walking a view
			// whose backing store is shrinking underneath it: each removal shifts the remaining
			// elements down one, the iterator advances past the shifted element, and every other
			// expression is skipped. The result is that SOME survive rather than none, which is far
			// harder to notice than a clean no-op.
			//
			// Reported 2026-08-27 from Curfew on stock 5.7: a clear returned ok and left three
			// expressions behind, and the reporter's guess at the cause - iterating while removing -
			// was exactly this. Deleting the same three BY NAME worked, which is the tell: the
			// per-expression call is fine, only the loop over it is wrong.
			//
			// Snapshot first, then delete from the snapshot. The array may reallocate during the
			// loop and the snapshot does not care.
			TArray<UMaterialExpression*> Doomed;
			{
				TConstArrayView<TObjectPtr<UMaterialExpression>> Live =
					Material ? Material->GetExpressions() : Function->GetExpressions();
				Doomed.Reserve(Live.Num());
				for (const TObjectPtr<UMaterialExpression>& Expr : Live)
				{
					if (Expr) { Doomed.Add(Expr); }
				}
			}
			for (UMaterialExpression* Expr : Doomed)
			{
				if (Material) { UMaterialEditingLibrary::DeleteMaterialExpression(Material, Expr); }
				else { UMaterialEditingLibrary::DeleteMaterialExpressionInFunction(Function, Expr); }
			}
		}
		else
		{
			FString Error;
			UMaterialExpression* Expression = FindExpressionByName(Material, Function, Name, Error);
			if (!Expression)
			{
				Fail(Out, Error);
				return;
			}
			if (Material) { UMaterialEditingLibrary::DeleteMaterialExpression(Material, Expression); }
			else { UMaterialEditingLibrary::DeleteMaterialExpressionInFunction(Function, Expression); }
		}
		const int32 After = (Material ? Material->GetExpressions() : Function->GetExpressions()).Num();

		Out->SetNumberField(TEXT("deleted"), Before - After);
		Out->SetNumberField(TEXT("remaining"), After);

		// A CLEAR THAT CANNOT PROVE IT CLEARED IS WORSE THAN NO CLEAR - the house rule, and this
		// endpoint was breaking it. The counts above were always correct, but ok:true alongside
		// deleted:0 reads as success to anything that checks the status rather than the arithmetic,
		// and that is exactly how the engine bug above went unnoticed.
		//
		// all=true means EMPTY. Anything left is a failure, and it names the survivors so the caller
		// can delete them individually - which works, and is the documented workaround.
		if (bAll && After > 0)
		{
			TArray<FString> Survivors;
			TConstArrayView<TObjectPtr<UMaterialExpression>> Left =
				Material ? Material->GetExpressions() : Function->GetExpressions();
			for (const TObjectPtr<UMaterialExpression>& Expr : Left)
			{
				if (Expr) { Survivors.Add(Expr->GetName()); }
			}
			Fail(Out, FString::Printf(
				TEXT("all=true asked for an EMPTY graph and %d expression(s) survived: %s. %d were "
					 "deleted, so this is a partial clear, not a no-op - the graph is now in a state "
					 "neither you nor it asked for. Delete the survivors by name (that path is "
					 "verified working) and re-read with list_material_expressions."),
				After, *FString::Join(Survivors, TEXT(", ")), Before - After));
			return;
		}
	}

	// --- list_material_expressions -------------------------------------------------------------------
	//   in:  { path (material), includeConnections? = true, includeProperties? = true }
	//   out: { assetType, cooked, numExpressions, expressions[{ name, class, index, x, y,
	//          properties?{}, inputs?[{input, from, fromOutput}] }], connectionCount,
	//          propertyBindings[{property, from, fromOutput}] }
	// Bucket: read-only — THE verification endpoint for every mutation above (house rule:
	// mutations without a read-back are not done).
	// Spec: list_material_expressions (Phase-2 CONFIRMED). Cooked materials are DEGRADED,
	// HONESTLY: numExpressions 0 + cooked:true, without ever touching GetExpressions() (whose
	// impl derefs the stripped editor-only data — a crash, not an empty view).
	void H_list_material_expressions(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("materialPath"),
			  TEXT("includeConnections"), TEXT("includeProperties") },
			TEXT("path (aliases: material, materialPath), includeConnections, includeProperties")))
		{
			return;
		}

		UMaterial* Material = nullptr;
		UMaterialFunction* Function = nullptr;
		if (!ResolveMaterialOrFunctionField(In, Out, Material, Function))
		{
			return;
		}
		UObject* Asset = Material ? (UObject*)Material : (UObject*)Function;
		Out->SetStringField(TEXT("assetType"), Material ? TEXT("material") : TEXT("function"));
		Out->SetStringField(TEXT("path"), Asset->GetPathName());

		const bool bCooked = IsCookedOrContainerPackage(Asset->GetPackage())
			|| (Material ? !HasGraphData(Material) : !HasGraphData(Function));
		Out->SetBoolField(TEXT("cooked"), bCooked);
		if (bCooked)
		{
			// 0 here means "graph stripped at cook", NOT "empty graph" — the flag above is what
			// keeps an agent from mistaking one for the other (spec's Cooked contract).
			Out->SetNumberField(TEXT("numExpressions"), 0);
			Out->SetArrayField(TEXT("expressions"), {});
			Out->SetStringField(TEXT("note"), CookedGraphError(Asset));
			return;
		}

		const bool bConnections = JBool(In, TEXT("includeConnections"), true);
		const bool bProperties = JBool(In, TEXT("includeProperties"), true);

		TConstArrayView<TObjectPtr<UMaterialExpression>> Expressions =
			Material ? Material->GetExpressions() : Function->GetExpressions();

		int32 ConnectionCount = 0;
		TArray<TSharedPtr<FJsonValue>> Rows;
		for (int32 Index = 0; Index < Expressions.Num(); ++Index)
		{
			UMaterialExpression* Expr = Expressions[Index];
			if (!Expr)
			{
				continue;
			}
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), Expr->GetName());
			Row->SetStringField(TEXT("class"), Expr->GetClass()->GetName());
			Row->SetNumberField(TEXT("index"), Index);
			int32 X = 0, Y = 0;
			UMaterialEditingLibrary::GetMaterialExpressionNodePosition(Expr, X, Y);
			Row->SetNumberField(TEXT("x"), X);
			Row->SetNumberField(TEXT("y"), Y);

			if (bProperties)
			{
				// Reflection dump of the expression's OWN configuration (ParameterName,
				// DefaultValue, Texture, ...): properties declared on strict subclasses of
				// UMaterialExpression only — base-class plumbing (positions, GraphNode, Material
				// back-pointer) is either reported above or noise. FExpressionInput-typed struct
				// members are the CONNECTIONS, reported separately below.
				TSharedRef<FJsonObject> Props = MakeShared<FJsonObject>();
				for (TFieldIterator<FProperty> It(Expr->GetClass()); It; ++It)
				{
					FProperty* Prop = *It;
					UStruct* Owner = Prop->GetOwnerStruct();
					if (!Owner || Owner == UMaterialExpression::StaticClass()
						|| !Owner->IsChildOf(UMaterialExpression::StaticClass()))
					{
						continue;
					}
					if (const FStructProperty* SP = CastField<FStructProperty>(Prop))
					{
						const FString StructName = SP->Struct->GetName();
						if (StructName.EndsWith(TEXT("ExpressionInput")) || StructName == TEXT("ExpressionOutput")
							|| StructName == TEXT("ExpressionExecOutput"))
						{
							continue;
						}
					}
					FString ValueStr;
					Prop->ExportTextItem_Direct(ValueStr, Prop->ContainerPtrToValuePtr<void>(Expr), nullptr, Expr, PPF_None);
					Props->SetStringField(Prop->GetName(), ValueStr);
				}
				Row->SetObjectField(TEXT("properties"), Props);
			}

			if (bConnections)
			{
				// GetInput(int32) is an ENGINE_API virtual (MaterialExpression.h:336, "required to
				// return nullptr for invalid input indices") — works uniformly for material AND
				// function graphs (the library's GetInputsForMaterialExpression null-gates on a
				// UMaterial, so it would report nothing for functions). Indices align with
				// GetMaterialExpressionInputNames. NOT GetInputsView(): UE_DEPRECATED(5.5, "Use
				// FExpressionInputIterator instead or GetInput() directly") - FExpressionInputIterator
				// does not exist at all on 5.3 (confirmed by grep of D:/UE532's MaterialExpression.h),
				// but GetInput() is identical and un-deprecated on both, so it needs no version gate.
				const TArray<FString> InputNames = UMaterialEditingLibrary::GetMaterialExpressionInputNames(Expr);
				TArray<TSharedPtr<FJsonValue>> InputRows;
				for (int32 InputIdx = 0; InputIdx < InputNames.Num(); ++InputIdx)
				{
					const FExpressionInput* Input = Expr->GetInput(InputIdx);
					if (!Input || !Input->Expression)
					{
						continue;
					}
					TSharedRef<FJsonObject> InputRow = MakeShared<FJsonObject>();
					InputRow->SetStringField(TEXT("input"),
						InputNames.IsValidIndex(InputIdx) ? InputNames[InputIdx] : FString::FromInt(InputIdx));
					InputRow->SetStringField(TEXT("from"), Input->Expression->GetName());
					if (Input->OutputIndex != INDEX_NONE && Input->Expression->Outputs.IsValidIndex(Input->OutputIndex))
					{
						InputRow->SetStringField(TEXT("fromOutput"),
							OutputPinName(Input->Expression->Outputs[Input->OutputIndex]));
					}
					InputRows.Add(MakeShared<FJsonValueObject>(InputRow));
					++ConnectionCount;
				}
				Row->SetArrayField(TEXT("inputs"), InputRows);
			}
			Rows.Add(MakeShared<FJsonValueObject>(Row));
		}
		Out->SetNumberField(TEXT("numExpressions"), Rows.Num());
		Out->SetArrayField(TEXT("expressions"), Rows);
		Out->SetNumberField(TEXT("connectionCount"), ConnectionCount);

		// Property -> expression bindings (materials only). Deliberately NOT via the library's
		// GetMaterialPropertyInputNode: its impl derefs GetExpressionInputForProperty's return
		// with no null check (MaterialEditingLibrary.cpp:797-806 — the Phase-2 caution), so we
		// call the ENGINE_API accessor (Material.h:1668) directly and null-check ourselves,
		// only over the same connectable set connect_material_property accepts.
		TArray<TSharedPtr<FJsonValue>> Bindings;
		if (Material)
		{
			for (const FMaterialPropertyName& Entry : GConnectableProperties)
			{
				// Skip the duplicate CustomData spellings so each pin is reported once.
				if (FCString::Strcmp(Entry.Name, TEXT("CustomData0")) == 0
					|| FCString::Strcmp(Entry.Name, TEXT("CustomData1")) == 0)
				{
					continue;
				}
				FExpressionInput* Input = Material->GetExpressionInputForProperty(Entry.Value);
				if (!Input || !Input->Expression)
				{
					continue;
				}
				TSharedRef<FJsonObject> Binding = MakeShared<FJsonObject>();
				Binding->SetStringField(TEXT("property"), Entry.Name);
				Binding->SetStringField(TEXT("from"), Input->Expression->GetName());
				if (Input->OutputIndex != INDEX_NONE && Input->Expression->Outputs.IsValidIndex(Input->OutputIndex))
				{
					Binding->SetStringField(TEXT("fromOutput"),
						OutputPinName(Input->Expression->Outputs[Input->OutputIndex]));
				}
				Bindings.Add(MakeShared<FJsonValueObject>(Binding));
			}
		}
		Out->SetArrayField(TEXT("propertyBindings"), Bindings);
	}

	// --- layout_material_expressions ----------------------------------------------------------------------
	//   in:  { path (material) }
	//   out: { numExpressions, note }
	// Bucket: transacted — only moves editor positions, trivially undoable.
	// Spec: layout_material_expressions (Phase-2 CONFIRMED — impl works on
	// MaterialExpressionEditorX/Y directly, no GraphNode/editor window needed; caveat: only
	// nodes REACHABLE from property/function outputs are moved).
	void H_layout_material_expressions(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("materialPath") },
			TEXT("path (aliases: material, materialPath)")))
		{
			return;
		}

		UMaterial* Material = nullptr;
		UMaterialFunction* Function = nullptr;
		if (!ResolveMaterialOrFunctionField(In, Out, Material, Function))
		{
			return;
		}
		UObject* Asset = Material ? (UObject*)Material : (UObject*)Function;
		if (RefuseIfCookedGraph(Asset, Out))
		{
			return;
		}

		// Positions live on the expression objects — Modify() each so undo restores the layout.
		Asset->Modify();
		{
			TConstArrayView<TObjectPtr<UMaterialExpression>> Expressions =
				Material ? Material->GetExpressions() : Function->GetExpressions();
			for (const TObjectPtr<UMaterialExpression>& Expr : Expressions)
			{
				if (Expr) { Expr->Modify(); }
			}
		}

		if (Material) { UMaterialEditingLibrary::LayoutMaterialExpressions(Material); }
		else { UMaterialEditingLibrary::LayoutMaterialFunctionExpressions(Function); }

		Out->SetNumberField(TEXT("numExpressions"),
			(Material ? Material->GetExpressions() : Function->GetExpressions()).Num());
		Out->SetStringField(TEXT("note"),
			TEXT("only nodes reachable from material property inputs (or function inputs/outputs) are laid out — disconnected nodes keep their positions"));
	}

	// --- recompile_material ----------------------------------------------------------------------------------
	//   in:  { path (material, asset) — UMaterial, UMaterialFunction, or UMaterialInstanceConstant }
	//   out: { recompiled, kind, compiling, numRemainingJobs, ... }
	// Bucket: SELF-MANAGED — shader-map regeneration must never ride the blanket transaction.
	//
	// Spec verdict (CORRECTED, BINDING): UMaterialEditingLibrary::RecompileMaterial is NOT
	// "synchronous and cheap" — its tail calls FMaterialEditorUtilities::BuildTextureStreamingData
	// (MaterialEditingLibrary.cpp:731), which (1) runs CollectGarbage TWICE
	// (MaterialEditorUtilities.cpp:789/:814) — a mid-handler GC that kills any unrooted UObject
	// the bridge holds; (2) opens FScopedSlowTask + MakeDialog(true)
	// (MaterialEditorUtilities.cpp:791-792) — modal UI pumped on the same game thread this HTTP
	// server answers on; (3) busy-waits for debug-view-mode shader compiles
	// (DebugViewModeHelpers.cpp:322-356) — an in-handler FlushShaderCompiles-class stall.
	// So for the UMaterial branch we do NOT call the library function: the block below
	// replicates ONLY its non-blocking core (MaterialEditingLibrary.cpp:697-728), which the
	// verdict names explicitly — FMaterialUpdateContext + AddMaterial + PreEditChange(nullptr)/
	// PostEditChange + MarkPackageDirty (all ENGINE_API, MaterialShared.h:2779+), plus the
	// editor-refresh broadcasts and the particle/child-instance refresh loops from the same
	// range. Shader compilation continues in the BACKGROUND; shader_compile_status is the poll.
	// The UpdateMaterialFunction / UpdateMaterialInstance branches were verified clean
	// (cpp:985-1032 / :1187-1202 — enqueue only, no GC, no dialogs, no waits) and ARE called.
	void H_recompile_material(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("material"), TEXT("asset") },
			TEXT("path (aliases: material, asset)")))
		{
			return;
		}

		FString P = JStrAny(In, { TEXT("path"), TEXT("material"), TEXT("asset") });
		P.TrimStartAndEndInline();
		if (P.IsEmpty())
		{
			Fail(Out, TEXT("path is required (Material / MaterialFunction / MaterialInstanceConstant)"));
			return;
		}
		UObject* Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!Obj && !P.Contains(TEXT(".")))
		{
			const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
			Obj = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
		}
		if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Obj))
		{
			Obj = Redirector->DestinationObject;
		}
		if (!Obj)
		{
			Fail(Out, FString::Printf(TEXT("asset not found: %s"), *P));
			return;
		}
		if (IsCookedOrContainerPackage(Obj->GetPackage()))
		{
			Fail(Out, FString::Printf(
				TEXT("cooked material '%s' — shaders ship as fixed permutations, cannot recompile; ")
				TEXT("author a NEW material with create_material or derive one with create_material_instance"),
				*Obj->GetPathName()));
			return;
		}

		if (UMaterial* Material = Cast<UMaterial>(Obj))
		{
			{
				// Leaving this scope updates all dependent material instances (the context's
				// documented contract) — everything inside is async-enqueue only.
				FMaterialUpdateContext UpdateContext;
				UpdateContext.AddMaterial(Material);
				Material->PreEditChange(nullptr);
				Material->PostEditChange();
				Material->MarkPackageDirty();

				// Editor refresh, exactly as the library's non-blocking range does
				// (MaterialEditingLibrary.cpp:709-711).
				FEditorDelegates::RefreshEditor.Broadcast();
				FEditorSupportDelegates::RedrawAllViewports.Broadcast();

				// Particle view relevance + child-instance parameter names (cpp:713-726).
				for (TObjectIterator<UParticleSystemComponent> It; It; ++It)
				{
					It->bIsViewRelevanceDirty = true;
				}
				// The engine's own tail calls the PROTECTED UMaterialInstance::UpdateParameterNames
				// here (MaterialEditingLibrary.cpp:723). That is legal only because UMaterialEditingLibrary
				// is a declared friend (MaterialInstance.h:1064) — MifBridge is not, so the direct call
				// fails to compile (C2248), exported ENGINE_API notwithstanding: exported != accessible.
				// The public route on that same friend class refreshes the parameter names AND the static
				// permutation, which is what a child instance genuinely needs after its parent's graph
				// changed, and is the identical call this endpoint's own MIC branch makes below (:1474).
				for (TObjectIterator<UMaterialInstanceConstant> It; It; ++It)
				{
					if (It->Parent == Material)
					{
						UMaterialEditingLibrary::UpdateMaterialInstance(*It);
					}
				}
			}
			// Deliberately NOT replicated from RecompileMaterial's tail:
			// RebuildMaterialInstanceEditors (open-editor-window UI refresh — no windows in an
			// agent flow) and BuildTextureStreamingData (the GC/dialog/busy-wait hazard above).
			Out->SetStringField(TEXT("kind"), TEXT("material"));
		}
		else if (UMaterialFunctionInterface* FunctionInterface = Cast<UMaterialFunctionInterface>(Obj))
		{
			UMaterialEditingLibrary::UpdateMaterialFunction(FunctionInterface, nullptr);
			Obj->MarkPackageDirty();
			Out->SetStringField(TEXT("kind"), TEXT("function"));
		}
		else if (UMaterialInstanceConstant* Instance = Cast<UMaterialInstanceConstant>(Obj))
		{
			UMaterialEditingLibrary::UpdateMaterialInstance(Instance);
			Obj->MarkPackageDirty();
			Out->SetStringField(TEXT("kind"), TEXT("instance"));
		}
		else
		{
			Fail(Out, FString::Printf(
				TEXT("path must be Material / MaterialFunction / MaterialInstanceConstant, got %s"),
				*Obj->GetClass()->GetName()));
			return;
		}

		Out->SetBoolField(TEXT("recompiled"), true);
		Out->SetStringField(TEXT("path"), Obj->GetPathName());
		Out->SetStringField(TEXT("hint"), TEXT("shader compilation continues in the background — poll shader_compile_status until compiling=false"));
		WriteShaderCompileFields(Out);
		UE_LOG(LogMifBridge, Log, TEXT("recompile_material: %s"), *Obj->GetPathName());
	}

	// --- shader_compile_status -----------------------------------------------------------------------------------
	//   in:  {}
	//   out: { compiling, numRemainingJobs, numOutstandingJobs, numPendingJobs }
	// Bucket: read-only — THE poll endpoint for every material mutation on this axis (and for
	// editor-wide shader churn after level loads). Numbers strictly decrease toward zero over
	// polls; compiling=false && numRemainingJobs==0 is quiescence.
	// Spec: shader_compile_status (Phase-2 CONFIRMED — extern ENGINE_API GShaderCompilingManager
	// ShaderCompiler.h:928; exported getters :746-747; inline IsCompiling/GetNumRemainingJobs
	// :770-773/:798-801 compile into this module).
	void H_shader_compile_status(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, {}, TEXT("(none - this endpoint takes no parameters)")))
		{
			return;
		}
		if (!GShaderCompilingManager)
		{
			// Never null in a real editor session — defensive, per the spec's failure mode.
			Fail(Out, TEXT("shader compiling manager unavailable"));
			return;
		}
		WriteShaderCompileFields(Out);
	}
}
