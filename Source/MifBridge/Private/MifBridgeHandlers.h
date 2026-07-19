// MifBridge — endpoint registry + shared graph-edit helpers.
//
// Every endpoint is a free function with the signature (In, Out). Read-only endpoints
// fill Out; mutating endpoints call Modify()/MarkBlueprintAsStructurallyModified inside
// the single transaction opened by RunEndpoint (never their own). The registry is built
// in MifBridgeCommon.cpp from the declarations below.
#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UBlueprint;
class UEdGraph;
class UEdGraphNode;
class UEdGraphPin;
class UEdGraphSchema_K2;
class UClass;
class UScriptStruct;
struct FEdGraphPinType;
enum EEdGraphPinDirection : int;

namespace MifBridge
{
	using FHandlerFn = TFunction<void(const TSharedRef<FJsonObject>& /*In*/, const TSharedRef<FJsonObject>& /*Out*/)>;

	// --- Registry / dispatch ------------------------------------------------
	const TMap<FString, FHandlerFn>& Handlers();
	TArray<FString> GetEndpointNames();
	/** Wrap the named handler in an editor-script guard + one transaction and run it. */
	void RunEndpoint(const FString& Endpoint, const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);

	// --- Result helpers -----------------------------------------------------
	void Fail(const TSharedRef<FJsonObject>& Out, const FString& Message);
	bool IsOk(const TSharedRef<FJsonObject>& Out);

	// --- JSON field accessors (optional reads with defaults) ----------------
	FString JStr(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const FString& Default = FString());
	double JNum(const TSharedRef<FJsonObject>& In, const TCHAR* Field, double Default = 0.0);
	int32 JInt(const TSharedRef<FJsonObject>& In, const TCHAR* Field, int32 Default = 0);
	bool JBool(const TSharedRef<FJsonObject>& In, const TCHAR* Field, bool Default = false);

	// --- Resolution ---------------------------------------------------------
	const UEdGraphSchema_K2* K2();

	UBlueprint* ResolveBlueprint(const FString& Path, FString& OutError);
	/** Reads "blueprintId" (or "path"); on failure writes error into Out and returns null. */
	UBlueprint* ResolveBlueprintField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out);

	void GatherGraphs(UBlueprint* Blueprint, TArray<UEdGraph*>& OutGraphs);
	FString GraphIdOf(UBlueprint* Blueprint, UEdGraph* Graph);
	UEdGraph* ResolveGraph(const FString& GraphId, UBlueprint*& OutBlueprint, FString& OutError);
	/** Reads "graphId"; on failure writes error into Out and returns null. */
	UEdGraph* ResolveGraphField(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out, UBlueprint*& OutBlueprint);

	/** Globally locate a node by its (engine-unique) NodeGuid via TObjectIterator. */
	UEdGraphNode* ResolveNode(const FString& GuidStr, FString& OutError);
	UEdGraphNode* ResolveNodeField(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const TSharedRef<FJsonObject>& Out);

	/** Find a pin by name (case-insensitive, with exec aliases). PreferDir breaks ties. */
	UEdGraphPin* FindPin(UEdGraphNode* Node, const FString& PinName, EEdGraphPinDirection PreferDir, bool bRequireDir);
	/** Follow a knot (reroute) chain to the first non-knot terminal pin. */
	UEdGraphPin* SkipKnots(UEdGraphPin* Pin);

	UClass* ResolveClass(const FString& Name, UBlueprint* ContextBP);
	UScriptStruct* ResolveStruct(const FString& Name);
	bool MakePinType(const FString& TypeStr, const FString& Container, FEdGraphPinType& OutType, FString& OutError);
	bool IsValidIdentifier(const FString& Name);

	// --- Node spawning ------------------------------------------------------
	/** Add to graph, assign GUID, PostPlacedNewNode, position, then AllocateDefaultPins.
	 *  Call any SetFromFunction / SetMacroGraph / VariableReference setup BEFORE this. */
	void PlaceAndInit(UEdGraph* Graph, UEdGraphNode* Node, int32 X, int32 Y);

	// --- Shared mutation helpers (used by node + recipe endpoints) ----------
	void MarkStructural(UBlueprint* Blueprint);
	void EmitNode(const TSharedRef<FJsonObject>& Out, UEdGraphNode* Node);
	/** Resolve pins by name (dir-preferring), tunnel knots, CanCreateConnection, TryCreateConnection.
	 *  Returns false + reason on failure. bBreakFirst clears both pins before wiring. */
	bool ConnectPinsChecked(UEdGraphNode* SrcNode, const FString& SrcPinName,
		UEdGraphNode* DstNode, const FString& DstPinName, bool bBreakFirst, FString& OutError);
	/** First UFunction on Class matching any of the candidate names (for versioned pairs like Greater_*). */
	UFunction* ResolveFunctionByCandidates(UClass* Class, const TArray<FString>& Names);
	/** Create an empty Blueprint function graph (entry + result terminators); set pure via entry ExtraFlags.
	 *  Returns the graph or null+error. Caller adds user-defined pins to the entry/result nodes. */
	UEdGraph* CreateFunctionGraph(UBlueprint* Blueprint, const FString& Name, bool bPure, FString& OutError);

	// --- Compile ------------------------------------------------------------
	/** Compile the blueprint and write {ok,numErrors,numWarnings,messages[]} into Out.
	 *  Shared by the compile/validate endpoints and batch's compileAtEnd. */
	void CompileBlueprintInto(UBlueprint* Blueprint, const TSharedRef<FJsonObject>& Out);

	// --- JSON serializers ---------------------------------------------------
	TSharedRef<FJsonObject> SerializePinType(const FEdGraphPinType& Type);
	TSharedRef<FJsonObject> SerializePin(const UEdGraphPin* Pin);
	TSharedRef<FJsonObject> SerializeNode(const UEdGraphNode* Node, bool bIncludePins);

	// --- Endpoint declarations ---------------------------------------------
#define MIF_DECL(Name) void H_##Name(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)

	// Session / assets
	MIF_DECL(open_blueprint);
	MIF_DECL(list_blueprints);
	MIF_DECL(save_blueprint);
	MIF_DECL(save_package);
	MIF_DECL(backup_blueprint);

	// Introspection
	MIF_DECL(list_graphs);
	MIF_DECL(list_nodes);
	MIF_DECL(get_node);
	MIF_DECL(list_variables);
	MIF_DECL(list_functions);
	MIF_DECL(find_nodes);

	// Variables
	MIF_DECL(add_variable);
	MIF_DECL(rename_variable);
	MIF_DECL(remove_variable);
	MIF_DECL(set_variable_default);

	// Nodes
	MIF_DECL(add_function_call);
	MIF_DECL(add_variable_get);
	MIF_DECL(add_variable_set);
	MIF_DECL(add_branch);
	MIF_DECL(add_macro_instance);
	MIF_DECL(add_get_array_item);
	MIF_DECL(add_override_event);
	MIF_DECL(add_parent_call);
	MIF_DECL(add_cast);
	MIF_DECL(move_node);
	MIF_DECL(remove_node);
	MIF_DECL(refresh_node);

	// Pins / wiring
	MIF_DECL(connect_pins);
	MIF_DECL(disconnect_pin);
	MIF_DECL(reconnect_pin);
	MIF_DECL(set_pin_default);
	MIF_DECL(splice_into_exec);

	// Nodes (phase 3 additions)
	MIF_DECL(add_custom_event);
	MIF_DECL(add_make_struct);
	MIF_DECL(add_break_struct);
	MIF_DECL(add_self);
	MIF_DECL(add_literal);
	MIF_DECL(create_function);
	MIF_DECL(create_blueprint);
	MIF_DECL(resolve_struct);

	// Composite recipes (§10)
	MIF_DECL(recipe_add_debug_print);
	MIF_DECL(recipe_reset_and_loop);
	MIF_DECL(recipe_override_and_call_parent);
	MIF_DECL(recipe_splice_before_parent);
	MIF_DECL(recipe_argmax_over_components);

	// Pipeline hooks
	MIF_DECL(read_modloader_log);
	MIF_DECL(trigger_cook);

	// Phase 3 breadth — graph nodes
	MIF_DECL(add_timeline);
	MIF_DECL(add_class_cast);
	MIF_DECL(add_switch_enum);
	MIF_DECL(add_switch_int);
	MIF_DECL(add_switch_string);
	MIF_DECL(add_enum_literal);
	MIF_DECL(set_pin_type);

	// Phase 3 breadth — event dispatchers (multicast delegates)
	MIF_DECL(add_event_dispatcher);
	MIF_DECL(add_call_dispatcher);
	MIF_DECL(add_bind_dispatcher);
	MIF_DECL(list_dispatchers);

	// Phase 3 breadth — components (SimpleConstructionScript)
	MIF_DECL(add_component);
	MIF_DECL(list_components);
	MIF_DECL(remove_component);
	MIF_DECL(set_component_transform);

	// Phase 3 breadth — interfaces
	MIF_DECL(add_interface);
	MIF_DECL(remove_interface);
	MIF_DECL(list_interfaces);

	// Phase 3 breadth — datatables (read-only)
	MIF_DECL(list_datatables);
	MIF_DECL(read_datatable);
	MIF_DECL(get_datatable_row);

	// Phase 3 completion — functions / interfaces / datatable write
	MIF_DECL(implement_interface_function);
	MIF_DECL(remove_function);
	MIF_DECL(write_datatable_rows);

	// Phase 3 completion — common nodes
	MIF_DECL(add_sequence);
	MIF_DECL(add_spawn_actor);
	MIF_DECL(add_create_widget);
	MIF_DECL(add_get_subsystem);
	MIF_DECL(add_make_array);
	MIF_DECL(add_make_map);
	MIF_DECL(add_format_text);
	MIF_DECL(add_get_data_table_row);
	MIF_DECL(add_comment);

	// UWidgetBlueprint asset endpoints (Is-Variable / bindings / widget tree) + generic property setter
	MIF_DECL(set_widget_is_variable);
	MIF_DECL(add_widget_binding);
	MIF_DECL(remove_widget_binding);
	MIF_DECL(add_tree_widget);
	MIF_DECL(remove_tree_widget);
	MIF_DECL(set_property);

	// Reconstructor unification — engine editable-child (decompile = run_console mif.kr.Reconstruct)
	MIF_DECL(create_editable_child);

	// Compile / diagnostics
	MIF_DECL(compile);
	MIF_DECL(run_console);
	MIF_DECL(validate);

	// Batch
	MIF_DECL(batch);

#undef MIF_DECL
}
