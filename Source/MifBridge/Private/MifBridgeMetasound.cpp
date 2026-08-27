// MifBridge - METASOUNDS: the interface of a MetaSound, read without touching a Metasound header.
//
// WHY THIS EXISTS. DDS2 ships 185 MetaSoundSource assets, 354 SoundCues and 3771 SoundWaves, and
// MifBridge had exactly ONE audio endpoint - audition_sound, which PLAYS one. Nothing described any
// of it. That is the same inverse gap the spec names for Foliage: a write with no read.
//
// It was also one of NINE plugin dependencies linked in Build.cs that no source file used, which is
// the state MifBridgeWater.cpp describes at the top of itself - build cost, no capability. Metasound
// was picked out of those nine for one reason: 185 real assets means this can be VERIFIED against
// content rather than shipped on a compile alone.
//
// WHY IT IS REFLECTIVE, which is the whole design and not an optimisation. Every direct route into a
// MetaSound's document is either version-specific or unsafe, and all four traps were found by reading
// both engine trees BEFORE writing anything:
//
//   1. THE CONST ACCESSORS WERE RENAMED WHOLESALE.
//        5.3  const FMetasoundFrontendDocument& GetDocumentChecked() const
//        5.7  const FMetasoundFrontendDocument& GetConstDocumentChecked() const
//        5.3  virtual const FMetasoundFrontendDocument& GetDocument() const
//        5.7  virtual const FMetasoundFrontendDocument& GetConstDocument() const
//      Section 14 direction A and B at once: deleted in one tree, added in the other.
//
//   2. GetDocumentChecked() HARD-ASSERTS. Its body is check(nullptr != Document) - so on an asset
//      whose document did not resolve it takes the editor with it rather than returning null. That is
//      the shape that killed the editor once already (PM: analyze_skeletal_split, GetImportedModel).
//
//   3. GetDocumentAccessPtr() IS DEPRECATED ON 5.7. The engine wraps its OWN call to it in
//      PRAGMA_DISABLE_DEPRECATION_WARNINGS. Calling it from here would compile on 5.3 and fail the
//      5.7 probe, which builds at the strictest settings the engine offers.
//
//   4. RootMetasoundDocument IS PROTECTED in both, so there is no direct member read either.
//
// Reflection sidesteps all four. FindPropertyByName does not care about C++ access, cannot assert,
// is not deprecated, and the FIELD NAMES ARE IDENTICAL IN BOTH TREES - verified, not assumed:
//
//   FMetasoundFrontendDocument   RootGraph, Subgraphs, Dependencies    5.3 :1587  5.7 :2061
//   FMetasoundFrontendClass      Metadata, Interface                   5.3 :1495  5.7 :1783
//   FMetasoundFrontendClassInterface  Inputs, Outputs                  5.3 :987   5.7 :1181
//
// The consequence worth stating: this file includes NO Metasound header and needs NO Metasound module,
// so it answers on an engine where the plugin is absent entirely - and it is therefore NOT the reason
// to keep MIF_WITH_METASOUND linked. parity_check still reports that dependency as idle, correctly.
//
// COOKED. DDS2's MetaSounds are cooked, and RootMetasoundDocument is a plain UPROPERTY(EditAnywhere)
// in both trees rather than WITH_EDITORONLY_DATA, so it SURVIVES cooking - checked in the headers
// before relying on it. `cooked` is reported anyway, because the question is asked first here as a
// matter of habit and because a caller comparing two projects will want it.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "JsonObjectConverter.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

namespace MifBridge
{
	namespace
	{
		/** /Game/A/B and /Game/A/B.B both resolve. Local, because the loose loader in
		 *  MifBridgeAnimation.cpp is file-static and copying a whole resolver for one call would be
		 *  the parallel system this project keeps warning about. */
		UObject* LoadMetasoundLoose(const FString& Path)
		{
			if (Path.IsEmpty()) { return nullptr; }
			if (UObject* Direct = StaticLoadObject(UObject::StaticClass(), nullptr, *Path))
			{
				return Direct;
			}
			FString Name;
			if (Path.Split(TEXT("/"), nullptr, &Name, ESearchCase::IgnoreCase, ESearchDir::FromEnd)
				&& !Name.Contains(TEXT(".")))
			{
				return StaticLoadObject(UObject::StaticClass(), nullptr, *(Path + TEXT(".") + Name));
			}
			return nullptr;
		}

		/** Step into a struct-typed field by NAME. Returns the field's address, and its layout through
		 *  OutStruct. Null when the field is absent, which is how a future engine renaming one of these
		 *  will surface - as a named refusal rather than as an empty answer that looks like "no data". */
		const void* StructField(const UStruct* Owner, const void* Addr, const TCHAR* FieldName,
			const UScriptStruct*& OutStruct)
		{
			OutStruct = nullptr;
			if (!Owner || !Addr) { return nullptr; }
			const FStructProperty* Prop = CastField<FStructProperty>(Owner->FindPropertyByName(FName(FieldName)));
			if (!Prop) { return nullptr; }
			OutStruct = Prop->Struct;
			return Prop->ContainerPtrToValuePtr<void>(Addr);
		}

		/** Serialise every element of a struct ARRAY field into JSON, via the engine's own converter.
		 *  Converting each element wholesale rather than picking fields out of it is deliberate: the
		 *  vertex structs carry Name, TypeName, defaults and metadata, and hand-picking two of those
		 *  would silently drop whatever the next engine version adds. */
		int32 StructArrayToJson(const UStruct* Owner, const void* Addr, const TCHAR* FieldName,
			TArray<TSharedPtr<FJsonValue>>& Out)
		{
			if (!Owner || !Addr) { return 0; }
			const FArrayProperty* ArrayProp = CastField<FArrayProperty>(Owner->FindPropertyByName(FName(FieldName)));
			if (!ArrayProp) { return -1; }
			const FStructProperty* Inner = CastField<FStructProperty>(ArrayProp->Inner);
			if (!Inner) { return -1; }
			FScriptArrayHelper Helper(ArrayProp, ArrayProp->ContainerPtrToValuePtr<void>(Addr));
			for (int32 i = 0; i < Helper.Num(); ++i)
			{
				TSharedRef<FJsonObject> Element = MakeShared<FJsonObject>();
				if (FJsonObjectConverter::UStructToJsonObject(Inner->Struct, Helper.GetRawPtr(i), Element, 0, 0))
				{
					Out.Add(MakeShared<FJsonValueObject>(Element));
				}
			}
			return Helper.Num();
		}

		/** How many elements a struct array holds, without serialising any of them. -1 when absent. */
		int32 StructArrayNum(const UStruct* Owner, const void* Addr, const TCHAR* FieldName)
		{
			if (!Owner || !Addr) { return -1; }
			const FArrayProperty* ArrayProp = CastField<FArrayProperty>(Owner->FindPropertyByName(FName(FieldName)));
			if (!ArrayProp) { return -1; }
			FScriptArrayHelper Helper(ArrayProp, ArrayProp->ContainerPtrToValuePtr<void>(Addr));
			return Helper.Num();
		}
	}

	// --- describe_metasound -----------------------------------------------------------------------
	//   in:  { path (aliases: assetPath, metasound) }
	//   out: { path, name, class, cooked, inputs[], outputs[], inputCount, outputCount, ... }
	// Bucket: pure READ. Loads the asset and touches nothing.
	//
	// There is no list_metasounds and that is deliberate: find_assets {class:"MetaSoundSource"}
	// already lists them, and a second endpoint that did the same thing would be tool-count parity -
	// the exact thing the spec says not to chase. What nothing could do is describe ONE.
	void H_describe_metasound(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out,
			{ TEXT("path"), TEXT("assetPath"), TEXT("metasound") },
			TEXT("path (aliases: assetPath, metasound) - a MetaSoundSource or MetaSoundPatch asset"),
			{ { TEXT("name"), TEXT("address it by asset path; find_assets {class:\"MetaSoundSource\"} lists every one with its objectPath") },
			  { TEXT("includeNodes"), TEXT("not implemented - this reports the MetaSound's INTERFACE (its inputs and outputs), which is what you need to drive it. The node graph is reported only as a count") } }))
		{
			return;
		}
		const FString Path = JStrAny(In, { TEXT("path"), TEXT("assetPath"), TEXT("metasound") });
		if (Path.IsEmpty())
		{
			Fail(Out, TEXT("path is required - find_assets {class:\"MetaSoundSource\"} reports objectPath "
						   "for every MetaSound in the project."));
			return;
		}
		UObject* Asset = LoadMetasoundLoose(Path);
		if (!Asset)
		{
			Fail(Out, FString::Printf(
				TEXT("could not load '%s'. Pass the asset path as find_assets reports it."), *Path));
			return;
		}

		// ASKED FIRST, before any accessor. Not because this one is dangerous - the document is a
		// plain UPROPERTY and survives cooking - but because "is this cooked" is the question that
		// has to precede an editor-only read, and answering it here costs nothing.
		const UPackage* Package = Asset->GetPackage();
		const bool bCooked = Package && Package->HasAnyPackageFlags(PKG_Cooked);

		Out->SetStringField(TEXT("path"), Asset->GetPathName());
		Out->SetStringField(TEXT("name"), Asset->GetName());
		Out->SetStringField(TEXT("class"), Asset->GetClass()->GetPathName());
		Out->SetBoolField(TEXT("cooked"), bCooked);

		const UScriptStruct* DocStruct = nullptr;
		const void* Doc = StructField(Asset->GetClass(), Asset, TEXT("RootMetasoundDocument"), DocStruct);
		if (!Doc || !DocStruct)
		{
			// NAMED refusal rather than an empty answer. An asset of the wrong class lands here, and
			// so would a future engine that renames the property - and those need different fixes.
			Fail(Out, FString::Printf(
				TEXT("'%s' is a %s and has no RootMetasoundDocument property, so it is not a MetaSound. "
					 "If it IS one, the engine has renamed that property and this endpoint needs "
					 "updating - nothing was read."),
				*Asset->GetName(), *Asset->GetClass()->GetName()));
			return;
		}

		const UScriptStruct* GraphStruct = nullptr;
		const void* RootGraph = StructField(DocStruct, Doc, TEXT("RootGraph"), GraphStruct);
		if (!RootGraph || !GraphStruct)
		{
			Fail(Out, TEXT("the document has no RootGraph field. The engine's MetaSound document layout "
						   "has changed and this endpoint needs updating - nothing was read."));
			return;
		}

		// The metadata carries the class name, display name and description. Converted wholesale for
		// the same reason as the vertices below.
		const UScriptStruct* MetaStruct = nullptr;
		if (const void* Meta = StructField(GraphStruct, RootGraph, TEXT("Metadata"), MetaStruct))
		{
			TSharedRef<FJsonObject> MetaJson = MakeShared<FJsonObject>();
			if (FJsonObjectConverter::UStructToJsonObject(MetaStruct, Meta, MetaJson, 0, 0))
			{
				Out->SetObjectField(TEXT("metadata"), MetaJson);
			}
		}

		const UScriptStruct* IfaceStruct = nullptr;
		const void* Iface = StructField(GraphStruct, RootGraph, TEXT("Interface"), IfaceStruct);
		if (!Iface || !IfaceStruct)
		{
			Fail(Out, TEXT("the root graph has no Interface field, so this MetaSound's inputs and "
						   "outputs cannot be read. The document layout has changed."));
			return;
		}

		TArray<TSharedPtr<FJsonValue>> Inputs, Outputs;
		const int32 NumIn = StructArrayToJson(IfaceStruct, Iface, TEXT("Inputs"), Inputs);
		const int32 NumOut = StructArrayToJson(IfaceStruct, Iface, TEXT("Outputs"), Outputs);
		Out->SetArrayField(TEXT("inputs"), Inputs);
		Out->SetArrayField(TEXT("outputs"), Outputs);
		// The COUNT the asset holds, beside the number actually serialised. They differ only if the
		// converter refused an element, and a caller reading `inputs` alone would never know.
		Out->SetNumberField(TEXT("inputCount"), NumIn < 0 ? 0 : NumIn);
		Out->SetNumberField(TEXT("outputCount"), NumOut < 0 ? 0 : NumOut);
		if (NumIn >= 0 && Inputs.Num() != NumIn)
		{
			Out->SetStringField(TEXT("inputWarning"), FString::Printf(
				TEXT("the interface holds %d input(s) and only %d could be serialised - the rest are "
					 "missing from inputs[]."), NumIn, Inputs.Num()));
		}
		if (NumOut >= 0 && Outputs.Num() != NumOut)
		{
			Out->SetStringField(TEXT("outputWarning"), FString::Printf(
				TEXT("the interface holds %d output(s) and only %d could be serialised - the rest are "
					 "missing from outputs[]."), NumOut, Outputs.Num()));
		}

		// Counts only, not the graph itself. A MetaSound graph is nodes, edges, literals and GUIDs;
		// dumping it would be a large answer to a question nobody asked, and the INTERFACE above is
		// what you need in order to drive the thing.
		const int32 Subgraphs = StructArrayNum(DocStruct, Doc, TEXT("Subgraphs"));
		const int32 Dependencies = StructArrayNum(DocStruct, Doc, TEXT("Dependencies"));
		if (Subgraphs >= 0) { Out->SetNumberField(TEXT("subgraphCount"), Subgraphs); }
		if (Dependencies >= 0) { Out->SetNumberField(TEXT("dependencyCount"), Dependencies); }

		const UScriptStruct* InnerGraphStruct = nullptr;
		if (const void* Graph = StructField(GraphStruct, RootGraph, TEXT("Graph"), InnerGraphStruct))
		{
			const int32 Nodes = StructArrayNum(InnerGraphStruct, Graph, TEXT("Nodes"));
			const int32 Edges = StructArrayNum(InnerGraphStruct, Graph, TEXT("Edges"));
			if (Nodes >= 0) { Out->SetNumberField(TEXT("nodeCount"), Nodes); }
			if (Edges >= 0) { Out->SetNumberField(TEXT("edgeCount"), Edges); }
		}

		Out->SetStringField(TEXT("note"),
			TEXT("read reflectively - this endpoint includes no Metasound header and works on an engine "
				 "where the plugin is absent. inputs[] and outputs[] are the MetaSound's INTERFACE: the "
				 "parameters you can set on a playing instance. The node graph is reported as a count "
				 "only."));
		UE_LOG(LogMifBridge, Log, TEXT("describe_metasound: %s (%d in, %d out, cooked=%d)"),
			*Asset->GetName(), Inputs.Num(), Outputs.Num(), bCooked ? 1 : 0);
	}
}
