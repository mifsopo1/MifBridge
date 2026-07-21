// MifBridge — shared graph-edit helpers, resolution, serialization, and the dispatch core.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Engine/Blueprint.h"
#include "K2Node_Knot.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "UObject/Class.h"
#include "UObject/ObjectRedirector.h"
#include "UObject/Script.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UObjectIterator.h"

#define LOCTEXT_NAMESPACE "MifBridge"

namespace MifBridge
{
	// --- Registry / dispatch ------------------------------------------------

	const TMap<FString, FHandlerFn>& Handlers()
	{
		static TMap<FString, FHandlerFn> Map;
		if (Map.Num() == 0)
		{
#define MIF_BIND(Name) Map.Add(TEXT(#Name), &H_##Name)
			// Session / assets
			MIF_BIND(open_blueprint);
			MIF_BIND(list_blueprints);
			MIF_BIND(save_blueprint);
			MIF_BIND(save_package);
			MIF_BIND(backup_blueprint);
			// Introspection
			MIF_BIND(list_graphs);
			MIF_BIND(list_nodes);
			MIF_BIND(get_node);
			MIF_BIND(list_variables);
			MIF_BIND(list_functions);
			MIF_BIND(find_nodes);
			// Variables
			MIF_BIND(add_variable);
			MIF_BIND(rename_variable);
			MIF_BIND(remove_variable);
			MIF_BIND(set_variable_default);
			// Nodes
			MIF_BIND(add_function_call);
			MIF_BIND(add_variable_get);
			MIF_BIND(add_variable_set);
			MIF_BIND(add_branch);
			MIF_BIND(add_macro_instance);
			MIF_BIND(add_get_array_item);
			MIF_BIND(add_override_event);
			MIF_BIND(add_parent_call);
			MIF_BIND(add_cast);
			MIF_BIND(move_node);
			MIF_BIND(remove_node);
			MIF_BIND(refresh_node);
			// Pins / wiring
			MIF_BIND(connect_pins);
			MIF_BIND(disconnect_pin);
			MIF_BIND(reconnect_pin);
			MIF_BIND(set_pin_default);
			MIF_BIND(splice_into_exec);
			// Nodes (phase 3 additions)
			MIF_BIND(add_custom_event);
			MIF_BIND(add_make_struct);
			MIF_BIND(add_break_struct);
			MIF_BIND(add_self);
			MIF_BIND(add_literal);
			MIF_BIND(create_function);
			MIF_BIND(create_blueprint);
			MIF_BIND(resolve_struct);
			MIF_BIND(describe_class);
			MIF_BIND(list_enum_values);
			// Composite recipes
			MIF_BIND(recipe_add_debug_print);
			MIF_BIND(recipe_reset_and_loop);
			MIF_BIND(recipe_override_and_call_parent);
			MIF_BIND(recipe_splice_before_parent);
			MIF_BIND(recipe_argmax_over_components);
			// Pipeline hooks
			MIF_BIND(read_modloader_log);
			MIF_BIND(trigger_cook);
			// Phase 3 breadth — graph nodes
			MIF_BIND(add_timeline);
			MIF_BIND(add_class_cast);
			MIF_BIND(add_switch_enum);
			MIF_BIND(add_switch_int);
			MIF_BIND(add_switch_string);
			MIF_BIND(add_enum_literal);
			MIF_BIND(set_pin_type);
			// Phase 3 breadth — event dispatchers
			MIF_BIND(add_event_dispatcher);
			MIF_BIND(add_call_dispatcher);
			MIF_BIND(add_bind_dispatcher);
			MIF_BIND(list_dispatchers);
			// Phase 3 breadth — components (SCS)
			MIF_BIND(add_component);
			MIF_BIND(list_components);
			MIF_BIND(remove_component);
			MIF_BIND(set_component_transform);
			// Phase 3 breadth — interfaces
			MIF_BIND(add_interface);
			MIF_BIND(remove_interface);
			MIF_BIND(list_interfaces);
			// Phase 3 breadth — datatables
			MIF_BIND(list_datatables);
			MIF_BIND(read_datatable);
			MIF_BIND(get_datatable_row);
			// Phase 3 completion — functions / interfaces / datatable write
			MIF_BIND(implement_interface_function);
			MIF_BIND(remove_function);
			MIF_BIND(write_datatable_rows);
			// Phase 3 completion — common nodes
			MIF_BIND(add_sequence);
			MIF_BIND(add_spawn_actor);
			MIF_BIND(add_create_widget);
			MIF_BIND(add_get_subsystem);
			MIF_BIND(add_make_array);
			MIF_BIND(add_make_map);
			MIF_BIND(add_format_text);
			MIF_BIND(add_get_data_table_row);
			MIF_BIND(add_comment);
			// UWidgetBlueprint asset endpoints + generic property setter
			MIF_BIND(set_widget_is_variable);
			MIF_BIND(add_widget_binding);
			MIF_BIND(remove_widget_binding);
			MIF_BIND(add_tree_widget);
			MIF_BIND(remove_tree_widget);
			MIF_BIND(set_property);
			MIF_BIND(get_property);
			MIF_BIND(list_object_properties);
			MIF_BIND(create_editable_child);
			// Asset lifecycle
			MIF_BIND(delete_asset);
			MIF_BIND(rename_asset);
			MIF_BIND(duplicate_asset);
			// Compile / diagnostics
			MIF_BIND(compile);
			MIF_BIND(run_console);
			MIF_BIND(validate);
			// Batch
			MIF_BIND(batch);
#undef MIF_BIND
		}
		return Map;
	}

	TArray<FString> GetEndpointNames()
	{
		TArray<FString> Names;
		Handlers().GetKeys(Names);
		return Names;
	}

	// Endpoints that never mutate assets — run without a transaction so the undo stack
	// stays clean (compile/validate/save touch the object but must not be an undo step).
	static bool IsReadOnlyEndpoint(const FString& Endpoint)
	{
		static const TSet<FString> ReadOnly = {
			TEXT("open_blueprint"), TEXT("list_blueprints"), TEXT("save_blueprint"), TEXT("save_package"), TEXT("backup_blueprint"),
			TEXT("list_graphs"), TEXT("list_nodes"), TEXT("get_node"),
			TEXT("list_variables"), TEXT("list_functions"), TEXT("find_nodes"),
			TEXT("resolve_struct"), TEXT("read_modloader_log"), TEXT("trigger_cook"),
			TEXT("list_dispatchers"), TEXT("list_components"), TEXT("list_interfaces"),
			TEXT("list_datatables"), TEXT("read_datatable"), TEXT("get_datatable_row"),
			TEXT("get_property"), TEXT("list_object_properties"),
			TEXT("compile"), TEXT("validate"), TEXT("run_console")
		};
		return ReadOnly.Contains(Endpoint);
	}

	// Endpoints that run a full FKismetEditorUtilities::CompileBlueprint (class reinstancing)
	// as part of their work. A full compile must NEVER be captured by an open transaction —
	// reinstancing trashes the old class/CDO and a later Ctrl-Z would restore dead pointers
	// and crash. These handlers therefore open their OWN tight transaction(s) around just the
	// graph mutations and compile outside them, so RunEndpoint must NOT wrap them.
	static bool IsSelfManagedEndpoint(const FString& Endpoint)
	{
		static const TSet<FString> SelfManaged = {
			TEXT("create_function"), TEXT("create_blueprint"), TEXT("recipe_add_debug_print"), TEXT("batch"),
			TEXT("add_event_dispatcher"),
			TEXT("set_property"),          // widget-BP branch calls CompileBlueprint; opens its own tight write transaction
			TEXT("create_editable_child"), // CreateEditableBlueprintCopy compiles + saves an asset
			// Asset-registry-level ops (delete/rename/duplicate a whole package) manage their own
			// GC/undo semantics internally — an outer FScopedTransaction over "the asset stopped
			// existing" isn't meaningful the way it is for a graph edit.
			TEXT("delete_asset"), TEXT("rename_asset"), TEXT("duplicate_asset")
		};
		return SelfManaged.Contains(Endpoint);
	}

	void RunEndpoint(const FString& Endpoint, const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		Out->SetBoolField(TEXT("ok"), true);

		const FHandlerFn* Fn = Handlers().Find(Endpoint);
		if (!Fn)
		{
			Fail(Out, FString::Printf(TEXT("unknown endpoint: %s"), *Endpoint));
			return;
		}

		// Editor-only script paths are allowed inside this scope.
		FEditorScriptExecutionGuard ScriptGuard;

		// Read-only endpoints and self-managed (compile-inside) endpoints run without the
		// blanket transaction — the latter open their own scoped transactions internally.
		if (IsReadOnlyEndpoint(Endpoint) || IsSelfManagedEndpoint(Endpoint))
		{
			(*Fn)(In, Out);
			return;
		}

		// Every mutation the handler performs is captured in one transaction so the
		// user can Ctrl-Z the whole bridge action.
		FScopedTransaction Transaction(FText::Format(LOCTEXT("BridgeEditFmt", "Mif Bridge: {0}"), FText::FromString(Endpoint)));
		(*Fn)(In, Out);
	}

	// --- Result / JSON accessors -------------------------------------------

	void Fail(const TSharedRef<FJsonObject>& Out, const FString& Message)
	{
		Out->SetBoolField(TEXT("ok"), false);
		Out->SetStringField(TEXT("error"), Message);
		UE_LOG(LogMifBridge, Verbose, TEXT("endpoint error: %s"), *Message);
	}

	bool IsOk(const TSharedRef<FJsonObject>& Out)
	{
		bool bOk = true;
		Out->TryGetBoolField(TEXT("ok"), bOk);
		return bOk;
	}

	FString JStr(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const FString& Default)
	{
		FString Value;
		return In->TryGetStringField(Field, Value) ? Value : Default;
	}

	double JNum(const TSharedRef<FJsonObject>& In, const TCHAR* Field, double Default)
	{
		double Value = Default;
		return In->TryGetNumberField(Field, Value) ? Value : Default;
	}

	int32 JInt(const TSharedRef<FJsonObject>& In, const TCHAR* Field, int32 Default)
	{
		int32 Value = Default;
		return In->TryGetNumberField(Field, Value) ? Value : Default;
	}

	bool JBool(const TSharedRef<FJsonObject>& In, const TCHAR* Field, bool Default)
	{
		bool Value = Default;
		return In->TryGetBoolField(Field, Value) ? Value : Default;
	}

	// --- Resolution ---------------------------------------------------------

	const UEdGraphSchema_K2* K2()
	{
		return GetDefault<UEdGraphSchema_K2>();
	}

	UBlueprint* ResolveBlueprint(const FString& Path, FString& OutError)
	{
		FString P = Path;
		P.TrimStartAndEndInline();
		if (P.IsEmpty())
		{
			OutError = TEXT("missing blueprint path/blueprintId");
			return nullptr;
		}

		UObject* Obj = StaticLoadObject(UBlueprint::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn);
		if (!Obj && !P.Contains(TEXT(".")))
		{
			// Accept a bare package path like /Game/Foo/BP_Bar → /Game/Foo/BP_Bar.BP_Bar
			const FString Short = FPackageName::GetShortName(P);
			const FString Full = P + TEXT(".") + Short;
			Obj = StaticLoadObject(UBlueprint::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn);
		}

		if (UObjectRedirector* Redirector = Cast<UObjectRedirector>(Obj))
		{
			Obj = Redirector->DestinationObject;
		}

		UBlueprint* Blueprint = Cast<UBlueprint>(Obj);
		if (!Blueprint)
		{
			OutError = FString::Printf(TEXT("blueprint not found: %s"), *P);
			return nullptr;
		}
		return Blueprint;
	}

	UBlueprint* ResolveBlueprintField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		FString Path = JStr(In, TEXT("blueprintId"));
		if (Path.IsEmpty())
		{
			Path = JStr(In, TEXT("path"));
		}
		FString Error;
		UBlueprint* Blueprint = ResolveBlueprint(Path, Error);
		if (!Blueprint)
		{
			Fail(Out, Error);
		}
		return Blueprint;
	}

	void GatherGraphs(UBlueprint* Blueprint, TArray<UEdGraph*>& OutGraphs)
	{
		if (!Blueprint)
		{
			return;
		}
		for (UEdGraph* Graph : Blueprint->UbergraphPages) { if (Graph) OutGraphs.Add(Graph); }
		for (UEdGraph* Graph : Blueprint->FunctionGraphs) { if (Graph) OutGraphs.Add(Graph); }
		for (UEdGraph* Graph : Blueprint->MacroGraphs) { if (Graph) OutGraphs.Add(Graph); }
		for (UEdGraph* Graph : Blueprint->DelegateSignatureGraphs) { if (Graph) OutGraphs.Add(Graph); }
	}

	FString GraphIdOf(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		return Blueprint->GetPathName() + TEXT("::") + Graph->GetName();
	}

	UEdGraph* ResolveGraph(const FString& GraphId, UBlueprint*& OutBlueprint, FString& OutError)
	{
		FString Left, Right;
		if (!GraphId.Split(TEXT("::"), &Left, &Right, ESearchCase::CaseSensitive, ESearchDir::FromEnd))
		{
			OutError = TEXT("graphId must be '<blueprintPath>::<graphName>' (from open_blueprint/list_graphs)");
			return nullptr;
		}

		OutBlueprint = ResolveBlueprint(Left, OutError);
		if (!OutBlueprint)
		{
			return nullptr;
		}

		TArray<UEdGraph*> Graphs;
		GatherGraphs(OutBlueprint, Graphs);
		for (UEdGraph* Graph : Graphs)
		{
			if (Graph->GetName() == Right)
			{
				return Graph;
			}
		}

		OutError = FString::Printf(TEXT("graph '%s' not found in %s"), *Right, *Left);
		return nullptr;
	}

	UEdGraph* ResolveGraphField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out, UBlueprint*& OutBlueprint)
	{
		const FString GraphId = JStr(In, TEXT("graphId"));
		if (GraphId.IsEmpty())
		{
			Fail(Out, TEXT("missing graphId"));
			return nullptr;
		}
		FString Error;
		UEdGraph* Graph = ResolveGraph(GraphId, OutBlueprint, Error);
		if (!Graph)
		{
			Fail(Out, Error);
		}
		return Graph;
	}

	UEdGraphNode* ResolveNode(const FString& GuidStr, FString& OutError)
	{
		FGuid Guid;
		if (!FGuid::Parse(GuidStr, Guid))
		{
			OutError = FString::Printf(TEXT("bad node guid: %s"), *GuidStr);
			return nullptr;
		}

		// NodeGuid is unique per fresh CreateNewGuid(), but it is NOT globally unique:
		//  - content-browser DuplicateObject copies NodeGuid verbatim (only paste regenerates it), and
		//  - CompileBlueprint clones source nodes into the transient consolidated event graph,
		//    retaining the source NodeGuid, and those clones linger until GC.
		// So we skip transient-package nodes (compiler clones / REINST leftovers), require a
		// real owning blueprint, and refuse to guess when two live assets collide.
		UEdGraphNode* Match = nullptr;
		int32 MatchCount = 0;
		UPackage* TransientPackage = GetTransientPackage();
		for (TObjectIterator<UEdGraphNode> It; It; ++It)
		{
			UEdGraphNode* Node = *It;
			if (!Node || !IsValid(Node) || Node->NodeGuid != Guid)
			{
				continue;
			}
			if (Cast<UEdGraph>(Node->GetOuter()) == nullptr || Node->GetPackage() == TransientPackage)
			{
				continue; // orphan or compiler-clone in the transient package
			}
			if (FBlueprintEditorUtils::FindBlueprintForNode(Node) == nullptr)
			{
				continue; // not part of a real blueprint asset
			}
			Match = Node;
			++MatchCount;
		}
		if (MatchCount == 1)
		{
			return Match;
		}
		if (MatchCount > 1)
		{
			OutError = FString::Printf(TEXT("ambiguous node guid %s matches %d loaded nodes (duplicate blueprints loaded?) — reopen the target blueprint or address it via its graph"), *GuidStr, MatchCount);
			return nullptr;
		}

		OutError = FString::Printf(TEXT("node not found: %s"), *GuidStr);
		return nullptr;
	}

	UEdGraphNode* ResolveNodeField(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const TSharedRef<FJsonObject>& Out)
	{
		const FString GuidStr = JStr(In, Field);
		if (GuidStr.IsEmpty())
		{
			Fail(Out, FString::Printf(TEXT("missing %s"), Field));
			return nullptr;
		}
		// If a graphId is supplied, scope the node lookup to that graph's nodes. ResolveGraph
		// picks the primary (editable) blueprint at the path, so this disambiguates the case
		// where a second copy of the blueprint is loaded carrying the same NodeGuids.
		const FString GraphId = JStr(In, TEXT("graphId"));
		if (!GraphId.IsEmpty())
		{
			FGuid Guid;
			if (!FGuid::Parse(GuidStr, Guid))
			{
				Fail(Out, FString::Printf(TEXT("bad node guid: %s"), *GuidStr));
				return nullptr;
			}
			FString GErr;
			UBlueprint* GBP = nullptr;
			UEdGraph* Graph = ResolveGraph(GraphId, GBP, GErr);
			if (!Graph)
			{
				Fail(Out, GErr);
				return nullptr;
			}
			for (UEdGraphNode* N : Graph->Nodes)
			{
				if (N && N->NodeGuid == Guid)
				{
					return N;
				}
			}
			Fail(Out, FString::Printf(TEXT("node %s not found in graph %s"), *GuidStr, *GraphId));
			return nullptr;
		}
		FString Error;
		UEdGraphNode* Node = ResolveNode(GuidStr, Error);
		if (!Node)
		{
			Fail(Out, Error);
		}
		return Node;
	}

	UEdGraphPin* FindPin(UEdGraphNode* Node, const FString& PinName, EEdGraphPinDirection PreferDir, bool bRequireDir)
	{
		if (!Node)
		{
			return nullptr;
		}

		UEdGraphPin* DirMatch = nullptr;
		UEdGraphPin* AnyMatch = nullptr;

		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (!Pin)
			{
				continue;
			}
			const FString PinStr = Pin->PinName.ToString();
			bool bNameMatch = PinStr.Equals(PinName, ESearchCase::IgnoreCase);

			if (!bNameMatch && Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec)
			{
				// Friendly exec aliases so callers need not know the exact pin name.
				if ((PinName.Equals(TEXT("exec"), ESearchCase::IgnoreCase) || PinName.Equals(TEXT("execute"), ESearchCase::IgnoreCase))
					&& Pin->Direction == EGPD_Input)
				{
					bNameMatch = true;
				}
			}

			if (!bNameMatch)
			{
				continue;
			}

			if (Pin->Direction == PreferDir)
			{
				DirMatch = Pin;
				if (bRequireDir)
				{
					return DirMatch;
				}
			}
			if (!AnyMatch)
			{
				AnyMatch = Pin;
			}
		}

		if (DirMatch)
		{
			return DirMatch;
		}
		return bRequireDir ? nullptr : AnyMatch;
	}

	UEdGraphPin* SkipKnots(UEdGraphPin* Pin)
	{
		int32 Guard = 0;
		while (Pin && Cast<UK2Node_Knot>(Pin->GetOwningNodeUnchecked()) && Guard++ < 50)
		{
			UK2Node_Knot* Knot = Cast<UK2Node_Knot>(Pin->GetOwningNode());
			UEdGraphPin* Bridge = (Pin->Direction == EGPD_Output) ? Knot->GetInputPin() : Knot->GetOutputPin();
			if (Bridge && Bridge->LinkedTo.Num() == 1)
			{
				Pin = Bridge->LinkedTo[0];
			}
			else
			{
				break;
			}
		}
		return Pin;
	}

	UClass* ResolveClass(const FString& Name, UBlueprint* ContextBP)
	{
		FString N = Name;
		N.TrimStartAndEndInline();

		if (N.IsEmpty() || N.Equals(TEXT("self"), ESearchCase::IgnoreCase))
		{
			if (ContextBP)
			{
				return ContextBP->SkeletonGeneratedClass ? ContextBP->SkeletonGeneratedClass : ContextBP->GeneratedClass;
			}
			return nullptr;
		}

		if (N.Contains(TEXT("/")) || N.Contains(TEXT(".")))
		{
			if (UClass* Loaded = LoadClass<UObject>(nullptr, *N, nullptr, LOAD_NoWarn))
			{
				return Loaded;
			}
			if (UObject* Obj = StaticLoadObject(UClass::StaticClass(), nullptr, *N, nullptr, LOAD_NoWarn))
			{
				return Cast<UClass>(Obj);
			}
		}

		if (UClass* Found = FindFirstObject<UClass>(*N, EFindFirstObjectOptions::None))
		{
			return Found;
		}
		if (!N.EndsWith(TEXT("_C")))
		{
			if (UClass* Found = FindFirstObject<UClass>(*(N + TEXT("_C")), EFindFirstObjectOptions::None))
			{
				return Found;
			}
		}
		return nullptr;
	}

	UScriptStruct* ResolveStruct(const FString& Name)
	{
		FString N = Name;
		N.TrimStartAndEndInline();

		if (N == TEXT("Vector") || N == TEXT("FVector")) return TBaseStructure<FVector>::Get();
		if (N == TEXT("Vector2D") || N == TEXT("FVector2D")) return TBaseStructure<FVector2D>::Get();
		if (N == TEXT("Vector4") || N == TEXT("FVector4")) return TBaseStructure<FVector4>::Get();
		if (N == TEXT("Rotator") || N == TEXT("FRotator")) return TBaseStructure<FRotator>::Get();
		if (N == TEXT("Transform") || N == TEXT("FTransform")) return TBaseStructure<FTransform>::Get();
		if (N == TEXT("Quat") || N == TEXT("FQuat")) return TBaseStructure<FQuat>::Get();
		if (N == TEXT("Guid") || N == TEXT("FGuid")) return TBaseStructure<FGuid>::Get();
		if (N == TEXT("LinearColor") || N == TEXT("FLinearColor")) return TBaseStructure<FLinearColor>::Get();
		if (N == TEXT("Color") || N == TEXT("FColor")) return TBaseStructure<FColor>::Get();
		if (N == TEXT("IntPoint") || N == TEXT("FIntPoint")) return TBaseStructure<FIntPoint>::Get();
		if (N == TEXT("IntVector") || N == TEXT("FIntVector")) return TBaseStructure<FIntVector>::Get();

		if (UScriptStruct* Found = FindFirstObject<UScriptStruct>(*N, EFindFirstObjectOptions::None))
		{
			return Found;
		}
		if (!N.StartsWith(TEXT("F")))
		{
			if (UScriptStruct* Found = FindFirstObject<UScriptStruct>(*(TEXT("F") + N), EFindFirstObjectOptions::None))
			{
				return Found;
			}
		}
		return nullptr;
	}

	bool MakePinType(const FString& TypeStr, const FString& Container, FEdGraphPinType& OutType, FString& OutError)
	{
		FString T = TypeStr;
		T.TrimStartAndEndInline();
		const FString L = T.ToLower();

		OutType = FEdGraphPinType();

		// Explicit ref prefixes: class:X / subclassof:X, softclass:X, object:X, softobject:X,
		// interface:X, enum:X — resolve the inner name to a UClass/UEnum and pick the category.
		bool bHandled = false;
		{
			FString Prefix, Inner;
			if (T.Split(TEXT(":"), &Prefix, &Inner) && !Prefix.IsEmpty())
			{
				const FString PfxL = Prefix.ToLower();
				Inner.TrimStartAndEndInline();
				if (PfxL == TEXT("class") || PfxL == TEXT("subclassof") || PfxL == TEXT("softclass") ||
					PfxL == TEXT("object") || PfxL == TEXT("softobject") || PfxL == TEXT("interface"))
				{
					UClass* Cls = ResolveClass(Inner, nullptr);
					if (!Cls)
					{
						OutError = FString::Printf(TEXT("class not found for '%s'"), *T);
						return false;
					}
					if (PfxL == TEXT("class") || PfxL == TEXT("subclassof")) OutType.PinCategory = UEdGraphSchema_K2::PC_Class;
					else if (PfxL == TEXT("softclass")) OutType.PinCategory = UEdGraphSchema_K2::PC_SoftClass;
					else if (PfxL == TEXT("softobject")) OutType.PinCategory = UEdGraphSchema_K2::PC_SoftObject;
					else if (PfxL == TEXT("interface")) OutType.PinCategory = UEdGraphSchema_K2::PC_Interface;
					else OutType.PinCategory = UEdGraphSchema_K2::PC_Object;
					OutType.PinSubCategoryObject = Cls;
					bHandled = true;
				}
				else if (PfxL == TEXT("enum"))
				{
					UEnum* PrefEnum = FindFirstObject<UEnum>(*Inner, EFindFirstObjectOptions::None);
					if (!PrefEnum)
					{
						OutError = FString::Printf(TEXT("enum not found for '%s'"), *T);
						return false;
					}
					OutType.PinCategory = UEdGraphSchema_K2::PC_Byte;
					OutType.PinSubCategoryObject = PrefEnum;
					bHandled = true;
				}
			}
		}

		if (bHandled)
		{
			// fall through to container handling below
		}
		else if (L == TEXT("bool") || L == TEXT("boolean"))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Boolean;
		}
		else if (L == TEXT("int") || L == TEXT("int32") || L == TEXT("integer"))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Int;
		}
		else if (L == TEXT("int64"))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Int64;
		}
		else if (L == TEXT("byte"))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Byte;
		}
		else if (L == TEXT("float") || L == TEXT("double") || L == TEXT("real"))
		{
			// UE5 unified float→double: PC_Real + PC_Double subcategory.
			OutType.PinCategory = UEdGraphSchema_K2::PC_Real;
			OutType.PinSubCategory = UEdGraphSchema_K2::PC_Double;
		}
		else if (L == TEXT("string"))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_String;
		}
		else if (L == TEXT("name"))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Name;
		}
		else if (L == TEXT("text"))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Text;
		}
		else if (UScriptStruct* Struct = ResolveStruct(T))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Struct;
			OutType.PinSubCategoryObject = Struct;
		}
		else if (UEnum* Enum = FindFirstObject<UEnum>(*T, EFindFirstObjectOptions::None))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Byte;
			OutType.PinSubCategoryObject = Enum;
		}
		else if (UClass* Class = ResolveClass(T, nullptr))
		{
			OutType.PinCategory = UEdGraphSchema_K2::PC_Object;
			OutType.PinSubCategoryObject = Class;
		}
		else
		{
			OutError = FString::Printf(TEXT("unknown type: '%s'"), *T);
			return false;
		}

		const FString C = Container.ToLower();
		if (C == TEXT("array"))
		{
			OutType.ContainerType = EPinContainerType::Array;
		}
		else if (C == TEXT("set"))
		{
			OutType.ContainerType = EPinContainerType::Set;
		}
		else if (C == TEXT("map"))
		{
			OutError = TEXT("map container is not supported (needs a value type); use array/set/none");
			return false;
		}

		return true;
	}

	bool IsValidIdentifier(const FString& Name)
	{
		if (Name.IsEmpty())
		{
			return false;
		}
		const TCHAR First = Name[0];
		const bool bFirstOk = (First >= 'A' && First <= 'Z') || (First >= 'a' && First <= 'z') || First == '_';
		if (!bFirstOk)
		{
			return false;
		}
		for (int32 Index = 1; Index < Name.Len(); ++Index)
		{
			const TCHAR Ch = Name[Index];
			const bool bOk = (Ch >= 'A' && Ch <= 'Z') || (Ch >= 'a' && Ch <= 'z') || (Ch >= '0' && Ch <= '9') || Ch == '_';
			if (!bOk)
			{
				return false;
			}
		}
		return true;
	}

	// --- Node spawning ------------------------------------------------------

	void PlaceAndInit(UEdGraph* Graph, UEdGraphNode* Node, int32 X, int32 Y)
	{
		Node->SetFlags(RF_Transactional);
		Graph->AddNode(Node, /*bFromUI*/ false, /*bSelectNewNode*/ false);
		Node->CreateNewGuid();
		Node->PostPlacedNewNode();
		Node->NodePosX = X;
		Node->NodePosY = Y;
		Node->AllocateDefaultPins();
	}

	// --- JSON serializers ---------------------------------------------------

	TSharedRef<FJsonObject> SerializePinType(const FEdGraphPinType& Type)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("category"), Type.PinCategory.ToString());
		if (!Type.PinSubCategory.IsNone())
		{
			Json->SetStringField(TEXT("subCategory"), Type.PinSubCategory.ToString());
		}
		if (Type.PinSubCategoryObject.IsValid())
		{
			Json->SetStringField(TEXT("subObject"), Type.PinSubCategoryObject->GetName());
		}
		switch (Type.ContainerType)
		{
		case EPinContainerType::Array: Json->SetStringField(TEXT("container"), TEXT("array")); break;
		case EPinContainerType::Set:   Json->SetStringField(TEXT("container"), TEXT("set")); break;
		case EPinContainerType::Map:   Json->SetStringField(TEXT("container"), TEXT("map")); break;
		default: break;
		}
		if (Type.bIsReference)
		{
			Json->SetBoolField(TEXT("isReference"), true);
		}
		return Json;
	}

	TSharedRef<FJsonObject> SerializePin(const UEdGraphPin* Pin)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("name"), Pin->PinName.ToString());
		Json->SetStringField(TEXT("direction"), Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
		Json->SetObjectField(TEXT("type"), SerializePinType(Pin->PinType));

		if (Pin->bHidden)
		{
			// Distinguishes "intentionally hidden + auto-defaulted (e.g. a WorldContext/self pin
			// the compiler wires implicitly)" from "visible and genuinely unwired" — without this,
			// an empty linkedTo on a hidden pin looks identical to a real bug from the JSON alone.
			Json->SetBoolField(TEXT("hidden"), true);
		}

		if (!Pin->DefaultValue.IsEmpty())
		{
			Json->SetStringField(TEXT("default"), Pin->DefaultValue);
		}
		// FText-typed pins (PC_Text) store their literal in DefaultTextValue, not DefaultValue —
		// without this, every Text pin with a real literal looks empty/unset over the API.
		if (!Pin->DefaultTextValue.IsEmpty())
		{
			Json->SetStringField(TEXT("default"), Pin->DefaultTextValue.ToString());
		}
		if (Pin->DefaultObject)
		{
			Json->SetStringField(TEXT("defaultObject"), Pin->DefaultObject->GetPathName());
		}

		TArray<TSharedPtr<FJsonValue>> Links;
		for (UEdGraphPin* Linked : Pin->LinkedTo)
		{
			if (!Linked)
			{
				continue;
			}
			TSharedRef<FJsonObject> LinkJson = MakeShared<FJsonObject>();
			if (UEdGraphNode* Owner = Linked->GetOwningNodeUnchecked())
			{
				LinkJson->SetStringField(TEXT("node"), Owner->NodeGuid.ToString());
			}
			LinkJson->SetStringField(TEXT("pin"), Linked->PinName.ToString());
			Links.Add(MakeShared<FJsonValueObject>(LinkJson));
		}
		Json->SetArrayField(TEXT("linkedTo"), Links);
		return Json;
	}

	TSharedRef<FJsonObject> SerializeNode(const UEdGraphNode* Node, bool bIncludePins)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("guid"), Node->NodeGuid.ToString());
		Json->SetStringField(TEXT("class"), Node->GetClass()->GetName());
		Json->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::ListView).ToString());
		Json->SetNumberField(TEXT("x"), Node->NodePosX);
		Json->SetNumberField(TEXT("y"), Node->NodePosY);

		if (bIncludePins)
		{
			TArray<TSharedPtr<FJsonValue>> Pins;
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin)
				{
					Pins.Add(MakeShared<FJsonValueObject>(SerializePin(Pin)));
				}
			}
			Json->SetArrayField(TEXT("pins"), Pins);
		}
		return Json;
	}

	// --- Shared mutation helpers -------------------------------------------

	void MarkStructural(UBlueprint* Blueprint)
	{
		if (Blueprint)
		{
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		}
	}

	void EmitNode(const TSharedRef<FJsonObject>& Out, UEdGraphNode* Node)
	{
		Out->SetStringField(TEXT("nodeGuid"), Node->NodeGuid.ToString());
		Out->SetObjectField(TEXT("node"), SerializeNode(Node, /*bIncludePins*/ true));
	}

	bool ConnectPinsChecked(UEdGraphNode* SrcNode, const FString& SrcPinName,
		UEdGraphNode* DstNode, const FString& DstPinName, bool bBreakFirst, FString& OutError)
	{
		if (!SrcNode || !DstNode)
		{
			OutError = TEXT("null node in connect");
			return false;
		}
		UEdGraphPin* OutPin = FindPin(SrcNode, SrcPinName, EGPD_Output, /*bRequireDir*/ false);
		UEdGraphPin* InPin = FindPin(DstNode, DstPinName, EGPD_Input, /*bRequireDir*/ false);
		if (!OutPin)
		{
			OutError = FString::Printf(TEXT("src pin not found: '%s'"), *SrcPinName);
			return false;
		}
		if (!InPin)
		{
			OutError = FString::Printf(TEXT("dst pin not found: '%s'"), *DstPinName);
			return false;
		}

		OutPin = SkipKnots(OutPin);
		InPin = SkipKnots(InPin);

		const UEdGraphSchema_K2* Schema = K2();
		if (UEdGraphNode* OutOwner = OutPin->GetOwningNodeUnchecked())
		{
			OutOwner->Modify();
		}
		if (UEdGraphNode* InOwner = InPin->GetOwningNodeUnchecked())
		{
			InOwner->Modify();
		}
		if (bBreakFirst)
		{
			Schema->BreakPinLinks(*OutPin, true);
			Schema->BreakPinLinks(*InPin, true);
		}

		const FPinConnectionResponse Response = Schema->CanCreateConnection(OutPin, InPin);
		if (Response.Response == CONNECT_RESPONSE_DISALLOW)
		{
			OutError = Response.Message.ToString();
			return false;
		}
		return Schema->TryCreateConnection(OutPin, InPin);
	}

	UFunction* ResolveFunctionByCandidates(UClass* Class, const TArray<FString>& Names)
	{
		if (!Class)
		{
			return nullptr;
		}
		for (const FString& Name : Names)
		{
			if (UFunction* Function = Class->FindFunctionByName(FName(*Name)))
			{
				return Function;
			}
		}
		return nullptr;
	}
}

#undef LOCTEXT_NAMESPACE
