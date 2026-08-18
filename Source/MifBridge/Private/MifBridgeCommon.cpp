// MifBridge — shared graph-edit helpers, resolution, serialization, and the dispatch core.
#include "Engine/World.h"       // UWorld / EWorldType for CollectPIEWorlds
#include "Engine/Engine.h"      // GEngine->GetWorldContexts for CollectPIEWorlds
#include "MifBridgeHandlers.h"
#include "MifBridgeEndpointRegistry.h"      // Public/ — the provider registration interface
#include "Dom/JsonObject.h"                 // FJsonObject is only FORWARD-DECLARED in the registry
                                            // header (Json is a PRIVATE dep, MifBridge.Build.cs:39)
#include "Dom/JsonValue.h"                  // EJson + the concrete FJsonValue types the strict
                                            // numeric readers below have to inspect BY TYPE, because
                                            // TryGetNumber's own coercions are what hid defect 1
#include "MifBridgeLog.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"               // GEditor->GetEditorWorldContext() for the shared EditorWorld()
#include "HAL/FileManager.h"      // IFileManager::Copy / COPY_OK for BackupPackage
#include "Misc/OutputDevice.h"            // FOutputDevice — RunEngineExec's tee base
#include "Misc/OutputDeviceRedirector.h"  // GLog->Serialize — the tee forwards, it does not replace
#include "Misc/Paths.h"           // FPaths::FileExists for BackupPackage
#include "UObject/Package.h"      // UPackage::ContainsMap for BackupPackage's .umap branch
#include "UObject/UnrealType.h"    // FStructProperty / FObjectPropertyBase / TFieldIterator for ResolvePropertyPath
#include "UObject/Field.h"         // FField::FindMetaData / HasMetaData for InspectEditCondition
#include "Components/ActorComponent.h"          // UActorComponent::IsEditableWhenInherited
#include "Components/SceneComponent.h"          // AActor::GetRootComponent's return type must be complete
#include "Engine/BlueprintGeneratedClass.h"     // UBlueprintGeneratedClass::SimpleConstructionScript
#include "Engine/InheritableComponentHandler.h" // FComponentKey / UInheritableComponentHandler
#include "Engine/SCS_Node.h"                    // USCS_Node::GetVariableName / ComponentTemplate
#include "Engine/SimpleConstructionScript.h"    // GetAllNodes / GetRootNodes / FindParentNode
#include "WidgetBlueprint.h"                    // ResolvePropertyTarget's widget-template branch
#include "Blueprint/WidgetTree.h"
#include "Components/Widget.h"
#include "EngineUtils.h"           // TActorIterator for FindActorInWorld
#include "GameFramework/Actor.h"   // AActor must be COMPLETE for IsValid()'s UObject* conversion
#include "Kismet2/BlueprintEditorUtils.h"
#include "Engine/Blueprint.h"
#include "K2Node_Knot.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/EngineVersion.h"   // FEngineVersion::Current() — build identity in self_audit
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"     // FPackagePath for IsCookedOrContainerPackage
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
			MIF_BIND(set_variable_type);
			MIF_BIND(retarget_variable_node);
			MIF_BIND(set_variable_default);
			MIF_BIND(set_variable_flags);
			// Nodes
			MIF_BIND(add_function_call);
			MIF_BIND(add_variable_get);
			MIF_BIND(add_variable_set);
			MIF_BIND(add_branch);
			MIF_BIND(add_macro_instance);
			MIF_BIND(add_get_array_item);
			MIF_BIND(add_override_event);
			MIF_BIND(add_component_bound_event);
			MIF_BIND(add_parent_call);
			MIF_BIND(add_cast);
			MIF_BIND(set_cast_purity);
			MIF_BIND(move_node);
			MIF_BIND(remove_node);
			MIF_BIND(refresh_node);
			// Pins / wiring
			MIF_BIND(connect_pins);
			MIF_BIND(disconnect_pin);
			MIF_BIND(reconnect_pin);
			MIF_BIND(set_pin_default);
			MIF_BIND(splice_into_exec);
			MIF_BIND(add_pin);
			MIF_BIND(remove_pin);
			// Nodes (phase 3 additions)
			MIF_BIND(add_custom_event);
			MIF_BIND(add_enhanced_input_action);
			MIF_BIND(add_make_struct);
			MIF_BIND(add_break_struct);
			MIF_BIND(add_self);
			MIF_BIND(add_literal);
			MIF_BIND(create_function);
			MIF_BIND(set_function_flags);
			MIF_BIND(rename_function);
			MIF_BIND(rename_event);
			MIF_BIND(rename_event_dispatcher);
			MIF_BIND(create_blueprint);
			MIF_BIND(reparent_blueprint);
			MIF_BIND(resolve_struct);
			MIF_BIND(describe_class);
			MIF_BIND(list_enum_values);

			MIF_BIND(list_mounted_containers);
			MIF_BIND(find_assets);
			MIF_BIND(describe_package);
			MIF_BIND(diagnose_landscape);
			MIF_BIND(diagnose_landscape_draws);
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
			MIF_BIND(get_inherited_component);
			MIF_BIND(override_inherited_component);
			MIF_BIND(revert_inherited_component);
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
			MIF_BIND(delete_datatable_rows);
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
			// Widget-tree topology
			MIF_BIND(list_tree_widgets);
			MIF_BIND(duplicate_tree_widget);
			MIF_BIND(wrap_tree_widget);
			MIF_BIND(move_tree_widget);
			MIF_BIND(set_property);
			MIF_BIND(get_property);
			MIF_BIND(list_object_properties);
			// Details-panel parity (Batch N) - MifBridgeDetails.cpp
			MIF_BIND(describe_property);
			MIF_BIND(diff_properties_vs_default);
			MIF_BIND(edit_container);
			MIF_BIND(reset_property_to_default);
			MIF_BIND(create_editable_child);
			// Navigation
			MIF_BIND(add_nav_volume);
			MIF_BIND(build_navmesh);
			MIF_BIND(nav_status);
			MIF_BIND(move_actor_to);
			// Level-authoring throughput + materials
			MIF_BIND(spawn_many);
			MIF_BIND(duplicate_actors);
			MIF_BIND(create_material_instance);
			MIF_BIND(set_material_parameter);
			MIF_BIND(add_foliage_instances);
			// Landscape authoring
			MIF_BIND(create_landscape);
			MIF_BIND(sculpt_landscape);
			MIF_BIND(paint_landscape);
			MIF_BIND(bind_landscape_rvt);
			MIF_BIND(landscape_info);
			// World lifecycle + splines + ground snapping
			MIF_BIND(new_level);
			MIF_BIND(save_level_as);
			MIF_BIND(load_level);
			MIF_BIND(set_spline_points);
			MIF_BIND(get_spline_points);
			MIF_BIND(snap_actors_to_ground);
			// Viewport camera control
			MIF_BIND(set_viewport_camera);
			MIF_BIND(focus_viewport);
			MIF_BIND(get_viewport_camera);
			// Spatial awareness + visual feedback
			MIF_BIND(get_actor_bounds);
			MIF_BIND(check_overlaps);
			MIF_BIND(trace_ground);
			MIF_BIND(capture_camera);
			MIF_BIND(scene_report);
			// PIE control + runtime observation
			MIF_BIND(start_pie);
			MIF_BIND(stop_pie);
			MIF_BIND(pie_status);
			MIF_BIND(list_pie_actors);
			MIF_BIND(spawn_actor_in_pie);
			MIF_BIND(run_console_captured);
			// Level / placed-actor editing
			MIF_BIND(list_level_actors);
			MIF_BIND(spawn_actor_in_level);
			MIF_BIND(set_actor_transform);
			MIF_BIND(set_actor_label);
			MIF_BIND(delete_level_actor);
			MIF_BIND(select_level_actors);
			// User-defined struct / enum authoring
			MIF_BIND(create_struct);
			MIF_BIND(list_struct_members);
			MIF_BIND(add_struct_member);
			MIF_BIND(remove_struct_member);
			MIF_BIND(create_enum);
			MIF_BIND(add_enum_value);
			MIF_BIND(remove_enum_value);
			// Animation assets (read-only)
			MIF_BIND(describe_animation);
			MIF_BIND(list_animations);
			// Asset lifecycle
			MIF_BIND(delete_asset);
			MIF_BIND(rename_asset);
			MIF_BIND(duplicate_asset);
			// Static-mesh simple collision (MifBridgeCollision.cpp) — the StaticMeshEditor
			// toolbar equivalent, reachable without opening that editor.
			MIF_BIND(remove_collision);
			MIF_BIND(add_simplified_collision);
			MIF_BIND(get_referencers);
			MIF_BIND(get_dependencies);
			MIF_BIND(audit_unused);
			// Compile / diagnostics
			MIF_BIND(compile);
			MIF_BIND(run_console);
			MIF_BIND(validate);
			MIF_BIND(self_audit);
			MIF_BIND(describe_endpoint);
			// Batch
			MIF_BIND(batch);
			// Undo introspection/rollback + dirty-package flows
			MIF_BIND(list_transactions);
			MIF_BIND(undo_transactions);
			MIF_BIND(redo_transactions);
			MIF_BIND(list_dirty_packages);
			MIF_BIND(save_dirty_packages);
			// Material graph authoring (Batch D)
			MIF_BIND(create_material);
			MIF_BIND(create_material_function);
			MIF_BIND(add_material_expression);
			MIF_BIND(connect_material_expressions);
			MIF_BIND(connect_material_property);
			MIF_BIND(delete_material_expression);
			MIF_BIND(list_material_expressions);
			MIF_BIND(layout_material_expressions);
			MIF_BIND(recompile_material);
			MIF_BIND(shader_compile_status);
			// Level streaming control (Batch I) — editor sublevels + PIE level instances
			MIF_BIND(list_sublevels);
			MIF_BIND(add_sublevel);
			MIF_BIND(remove_sublevel);
			MIF_BIND(set_sublevel_visibility);
			MIF_BIND(set_current_sublevel);
			MIF_BIND(set_sublevel_streaming);
			MIF_BIND(pie_load_level_instance);
			MIF_BIND(pie_unload_level_instance);
			// Editor UI invocation (Batch O) — MifBridgeUI.cpp
			MIF_BIND(list_editor_commands);
			MIF_BIND(invoke_editor_command);
			MIF_BIND(invoke_editor_tab);
			MIF_BIND(send_editor_key);
			MIF_BIND(open_asset_editor);
			// Source media ingest (MifBridgeImport.cpp)
			MIF_BIND(import_texture);
			MIF_BIND(import_asset);
			MIF_BIND(reimport_asset);
			MIF_BIND(set_texture_settings);
			// Source media EGRESS (MifBridgeExport.cpp). No separate endpoint-name list to update:
			// GetEndpointNames() derives from THIS map (and the external registry), and
			// FMifBridgeServer::Start binds one /api/<name> route per entry — so this MIF_BIND is what
			// mints /api/export_asset.
			MIF_BIND(export_asset);
			// Asset icon rendering (MifBridgeThumbnail.cpp)
			MIF_BIND(render_thumbnail);
			MIF_BIND(write_thumbnail_texture);
			MIF_BIND(set_asset_thumbnail);
			MIF_BIND(thumbnail_capabilities);
			// Console / cvar
			MIF_BIND(exec_console);
			MIF_BIND(get_cvar);
			MIF_BIND(set_cvar);
			// Variable pin lists
			MIF_BIND(add_node_pin);
#undef MIF_BIND
		}
		return Map;
	}

	// --- External (provider-registered) endpoints ----------------------------
	// Public/MifBridgeEndpointRegistry.h is the contract. Function-local static: initialised on first
	// use, so a provider whose module loads at "Default" (MifKismetReconstructor.uplugin:17) can
	// populate it before MifBridge's own StartupModule runs at "PostEngineInit" — the OS loader maps
	// this DLL when the provider DLL loads, long before FMifBridgeModule::StartupModule.
	// NOTHING in this block may touch module-startup state (server, routes, menus, token).
	static TMap<FString, FExternalEndpointDesc>& ExternalRegistry()
	{
		static TMap<FString, FExternalEndpointDesc> Map;
		return Map;
	}

	// Flipped by FMifBridgeServer::Start() once the route table is built (MifBridgeServer.cpp:88-108).
	// Routes bind ONCE per name, so an endpoint registered after this point would be dispatchable by
	// RunEndpoint but have no HTTP route — invisible with no error anywhere. Refuse it loudly instead.
	static bool GbRouteTableLive = false;
	void MarkRouteTableLive() { GbRouteTableLive = true; }

	bool RegisterExternalEndpoint(FExternalEndpointDesc Desc, FString* OutError)
	{
		auto Reject = [OutError](const FString& Why) { if (OutError) { *OutError = Why; } return false; };

		if (!IsInGameThread())          { return Reject(TEXT("RegisterExternalEndpoint must be called on the game thread (from your module's StartupModule)")); }
		if (Desc.Name.IsEmpty())        { return Reject(TEXT("endpoint name is empty")); }
		if (!Desc.Handler)              { return Reject(FString::Printf(TEXT("endpoint '%s' has no handler"), *Desc.Name)); }
		if (Desc.Provider.IsEmpty())    { return Reject(FString::Printf(TEXT("endpoint '%s' has no Provider (self_audit attributes every external endpoint to a provider)"), *Desc.Name)); }
		if (GbRouteTableLive)           { return Reject(FString::Printf(TEXT("endpoint '%s': route table already live — register from your module's StartupModule (routes bind once at server start)"), *Desc.Name)); }
		if (Handlers().Contains(Desc.Name)) { return Reject(FString::Printf(TEXT("endpoint '%s' collides with a MifBridge built-in"), *Desc.Name)); }
		if (const FExternalEndpointDesc* Existing = ExternalRegistry().Find(Desc.Name))
		{
			return Reject(FString::Printf(TEXT("endpoint '%s' already registered by provider '%s'"), *Desc.Name, *Existing->Provider));
		}

		const FString Name = Desc.Name;
		ExternalRegistry().Add(Name, MoveTemp(Desc));
		return true;
	}

	int32 UnregisterExternalEndpoints(const FString& Provider)
	{
		TArray<FString> Doomed;
		for (const TPair<FString, FExternalEndpointDesc>& KV : ExternalRegistry())
		{
			if (KV.Value.Provider == Provider) { Doomed.Add(KV.Key); }
		}
		for (const FString& Name : Doomed) { ExternalRegistry().Remove(Name); }
		return Doomed.Num();
	}

	// batch's dispatcher used to consult Handlers() only, so every provider-registered endpoint came
	// back "unknown op" from inside ops[] while self_audit listed it as present. Exposed rather than
	// duplicating the lookup in MifBridgeNodes.cpp: ExternalRegistry() is a file-static here, and a
	// second copy of "how do I find an endpoint" is precisely the drift RunEndpoint/IsReadOnlyEndpoint/
	// IsSelfManagedEndpoint were unified to avoid.
	const FHandlerFn* FindExternalHandler(const FString& Endpoint)
	{
		const FExternalEndpointDesc* Desc = ExternalRegistry().Find(Endpoint);
		// FExternalHandler and FHandlerFn are the same TFunction<void(In,Out)> type, so this hands
		// back the registry's own handler without copying it or leaking FExternalEndpointDesc.
		return (Desc && Desc->Handler) ? &Desc->Handler : nullptr;
	}

	TArray<FString> GetEndpointNames()
	{
		TArray<FString> Names;
		Handlers().GetKeys(Names);
		// Externals are first-class from here down: this single merge is what makes the route-bind
		// loop (MifBridgeServer.cpp:88-108) and self_audit's endpoint list pick them up unchanged.
		for (const TPair<FString, FExternalEndpointDesc>& KV : ExternalRegistry())
		{
			Names.AddUnique(KV.Key);
		}
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
			// Pure discovery: reports a component's origin and any existing override; creates nothing.
			// The two mutating twins stay in the default (transacted) bucket — they modify templates
			// but run no compile, so the blanket transaction gives correct Ctrl-Z.
			TEXT("get_inherited_component"),
			TEXT("list_datatables"), TEXT("read_datatable"), TEXT("get_datatable_row"),
			TEXT("get_property"), TEXT("list_object_properties"),
			// Details-panel parity READS (Batch N). describe_property walks FField metadata and CPF_*
			// flags; diff_properties_vs_default compares against the archetype with FProperty::Identical.
			// Neither calls Modify(), creates anything, or asks for an InheritableComponentHandler with
			// bCreateIfNecessary - so both belong here, or every call pushes an empty entry onto the very
			// undo stack list_transactions exists to report.
			TEXT("describe_property"), TEXT("diff_properties_vs_default"),
			// Per-endpoint parameter introspection (MifBridgeDescribe.cpp). REQUIRED here, not
			// optional: describe_endpoint calls no Modify(), loads nothing and creates nothing — it
			// reads the live registry and a static table harvested from the RejectUnknownParams call
			// sites. Without this entry RunEndpoint gives it the blanket transaction and EVERY call
			// pushes an empty entry onto the undo stack, which is precisely what the describe_property
			// comment directly above exists to prevent. Nothing is mis-reported meanwhile; it just
			// litters undo — and "what parameters does this take?" is asked in a loop.
			TEXT("describe_endpoint"),
			// Pure reflection reads (audit 03_GAPS_AND_RISKS.md §7.6): describe_class walks
			// TFieldIterator over a resolved class, list_enum_values reads UEnum name tables —
			// neither calls Modify() or creates anything persistent. Left out of this set they
			// were transacted, so EVERY call pushed an empty undo entry: exactly the undo-stack
			// pollution this bucket exists to prevent. (describe_class may LoadObject the class;
			// loading is not mutating — find_assets and the list_* endpoints already load here.)
			TEXT("describe_class"), TEXT("list_enum_values"),
			TEXT("describe_animation"), TEXT("list_animations"),
			TEXT("list_tree_widgets"),
			TEXT("list_struct_members"), TEXT("list_level_actors"),
			// PIE start/stop only QUEUE a request — they mutate no asset and must not open a
			// transaction (an undo entry spanning a world teardown is meaningless).
			// Read-only spatial queries. capture_camera spawns a TRANSIENT actor it destroys again,
			// so it dirties nothing and must not open a transaction either.
			TEXT("nav_status"), TEXT("landscape_info"), TEXT("get_spline_points"),
			TEXT("set_viewport_camera"), TEXT("focus_viewport"), TEXT("get_viewport_camera"),
			TEXT("get_actor_bounds"), TEXT("check_overlaps"), TEXT("trace_ground"),
			TEXT("capture_camera"), TEXT("scene_report"),
			// Asset ICON rendering (MifBridgeThumbnail.cpp). Same bucket and same reason as
			// capture_camera immediately above: they render and write an image FILE under
			// <ProjectSaved> and mutate no asset. render_thumbnail additionally saves and RESTORES
			// the asset's ThumbnailInfo around the render (the engine's own renderers clamp its
			// OrbitZoom in place, ThumbnailHelpers.cpp:578-582), so it dirties nothing even for
			// assets that own one — transacting it would push an empty undo entry per icon preview,
			// and previewing in a loop is exactly how you pick a camera angle. Their two ASSET-
			// WRITING siblings are SELF-MANAGED, not here — see IsSelfManagedEndpoint.
			TEXT("render_thumbnail"), TEXT("thumbnail_capabilities"),
			// Asset EXPORT (MifBridgeExport.cpp). Same bucket and the same reason as the two directly
			// above: it writes a FILE under <ProjectSaved> (or a caller-named path) and mutates NO
			// UObject — no Modify(), no MarkPackageDirty, no PostEditChange, no package created. Its
			// ingest siblings import_texture / import_asset are SELF-MANAGED because they create and
			// dirty assets; export creates nothing, so putting it there would be wrong twice over.
			//
			// Two consequences, both deliberate. (1) RunEndpoint opens no FScopedTransaction for it, so
			// exporting in a loop does not push one empty entry per call onto the undo stack — the same
			// cost render_thumbnail is here to avoid, and exporting a batch of meshes to diff them
			// outside the editor is exactly a loop. (2) IsCompileHeavyEndpoint derives from
			// IsSelfManagedEndpoint, so staying READ-ONLY is also what keeps export_asset usable inside
			// `batch`; bucketing it self-managed would have banned it there for no reason.
			TEXT("export_asset"),
			TEXT("start_pie"), TEXT("stop_pie"), TEXT("pie_status"),
			TEXT("list_pie_actors"), TEXT("run_console_captured"),
			// Cooked-content introspection — declared read-only in MifBridgeHandlers.h; without
			// listing them here RunEndpoint wraps each in an FScopedTransaction, so a pure query
			// pushes an empty entry onto the undo stack.
			TEXT("list_mounted_containers"), TEXT("find_assets"), TEXT("describe_package"),
			TEXT("diagnose_landscape"), TEXT("diagnose_landscape_draws"),
			TEXT("self_audit"),
			TEXT("compile"), TEXT("validate"), TEXT("run_console"),
			// Undo-buffer + dirty-package introspection — pure queries of editor-session state.
			// Transacting these would push an empty entry onto the very stack list_transactions
			// exists to report (and list_dirty_packages must not dirty anything to list it).
			TEXT("list_transactions"), TEXT("list_dirty_packages"),
			// Material graph read-back + shader-compile poll (Batch D): pure queries. The poll in
			// particular gets hammered in a loop after every recompile — transacting it would
			// flood the undo stack with one empty entry per poll tick.
			TEXT("list_material_expressions"), TEXT("shader_compile_status"),
			// Level streaming read-back (Batch I). Same reason as shader_compile_status: this is
			// THE poll endpoint for every deferred/async streaming change in MifBridgeStreaming.cpp,
			// so it gets called in a tight loop and must not push an undo entry per poll.
			TEXT("list_sublevels"),
			// Asset-registry dependency queries. IAssetRegistry::GetReferencers / GetDependencies /
			// GetAssets plus JSON serialisation — zero Modify(), zero MarkPackageDirty, zero object
			// creation (full bodies checked). Their own file-neighbours and shape-twins find_assets and
			// describe_package were already here; leaving these three out meant every call pushed an
			// empty "Mif Bridge: get_referencers" entry onto the undo stack, which is the exact defect
			// this bucket exists to prevent. audit_unused is the worst of the three: it is the one you
			// run repeatedly while tuning excludeReferencers.
			//
			// The empty transaction was harmless only ACCIDENTALLY — UTransBuffer::End restores the redo
			// stack when FTransaction::IsTransient() (EditorTransaction.cpp), which is true only while
			// these record nothing. The moment one gains a Modify() the redo stack starts dying
			// silently, and redo_transactions' own docstring tells callers to rely on an A/B undo/redo
			// loop. Being in the right bucket removes that trip-wire instead of documenting it.
			TEXT("get_referencers"), TEXT("get_dependencies"), TEXT("audit_unused"),
			// build_navmesh only QUEUES generation: it validates, calls Nav->Build(), and returns.
			// It Modify()s nothing and navmesh tiles are generated asynchronously over later frames,
			// entirely outside any transaction — so transacting it recorded one empty undo entry per
			// call. Exactly the start_pie/stop_pie precedent two blocks up ("only QUEUE a request").
			TEXT("build_navmesh"),
			// Editor UI DISCOVERY (Batch O). Enumerates binding contexts / commands / console objects
			// and, when asked, one named ToolMenu — and invokes NOTHING. The only third-party code it
			// can reach is a command's FCanExecuteAction predicate, and only under the opt-in
			// includeCanExecute. It lists MENUS via UToolMenus::CollectHierarchy rather than
			// GenerateMenu precisely so that listing has no side effects (GenerateMenu allocates a
			// UToolMenu and runs dynamic-section construct delegates, ToolMenus.cpp:1881-1901).
			// Its three invoking siblings are SELF-MANAGED, not here — see IsSelfManagedEndpoint.
			TEXT("list_editor_commands")
		};
		if (ReadOnly.Contains(Endpoint)) { return true; }
		// External endpoints declare exactly ONE bucket in their descriptor, so this fallback can
		// never put a name in two buckets (policyContradictions stays structurally empty for them).
		if (const FExternalEndpointDesc* Ext = ExternalRegistry().Find(Endpoint))
		{
			return Ext->Bucket == EEndpointBucket::ReadOnly;
		}
		return false;
	}

	// Endpoints that run a full FKismetEditorUtilities::CompileBlueprint (class reinstancing)
	// as part of their work. A full compile must NEVER be captured by an open transaction —
	// reinstancing trashes the old class/CDO and a later Ctrl-Z would restore dead pointers
	// and crash. These handlers therefore open their OWN tight transaction(s) around just the
	// graph mutations and compile outside them, so RunEndpoint must NOT wrap them.
	static bool IsSelfManagedEndpoint(const FString& Endpoint)
	{
		static const TSet<FString> SelfManaged = {
			TEXT("create_function"), TEXT("create_blueprint"), TEXT("reparent_blueprint"), TEXT("recipe_add_debug_print"), TEXT("batch"),
			TEXT("add_event_dispatcher"),
			// Changing a function's NET flags needs a full compile (skeleton regen builds no
			// replication data and leaves call-site bytecode stale), so it opens its own tight
			// transaction around the flag writes and compiles after it closes.
			TEXT("set_function_flags"),
			TEXT("set_property"),          // widget-BP branch calls CompileBlueprint; opens its own tight write transaction
			TEXT("create_editable_child"), // CreateEditableBlueprintCopy compiles + saves an asset
			// Asset-registry-level ops (delete/rename/duplicate a whole package) manage their own
			// GC/undo semantics internally — an outer FScopedTransaction over "the asset stopped
			// existing" isn't meaningful the way it is for a graph edit.
			TEXT("delete_asset"), TEXT("rename_asset"), TEXT("duplicate_asset"),
			// ALandscape::Import builds heightmap/weightmap TEXTURES and registers new components.
			// Undoing that mid-flight leaves components pointing at freed textures — the same class
			// of hazard as compiling a Blueprint inside a transaction.
			TEXT("create_landscape"),
			// Swapping or discarding the entire UWorld invalidates every object an outer
			// transaction recorded — the same hazard class as compiling inside one.
			TEXT("new_level"), TEXT("load_level"), TEXT("save_level_as"),
			// Undo/redo REPLAY prior transactions: beginning one inside RunEndpoint's blanket
			// transaction violates the engine's own invariant (ensure(!GIsTransacting) in
			// UTransBuffer::BeginInternal, TransBuffer.h:74) — and an "undo the undo" entry is
			// nonsense. Being here also makes IsCompileHeavyEndpoint true, which keeps them out
			// of batch's single open transaction for the same reason. They can also trigger
			// Blueprint reinstancing via PostUndo (EditorServer.cpp:1406) — the exact dead-CDO
			// hazard this bucket exists to fence off.
			TEXT("undo_transactions"), TEXT("redo_transactions"),
			// Saving is not undoable, and a wrapping transaction would record package dirty-flag
			// state into the undo stack (FTransaction::FPackageRecord, Transactor.h:240-254) —
			// the asset-lifecycle precedent above applies.
			TEXT("save_dirty_packages"),
			// New-asset creation with explicit AssetCreated + MarkPackageDirty. create_material also
			// enqueues the material's initial shader compile via PostEditChange.
			//
			// create_material_instance is here NOW; it was not before, and two source comments (this
			// one and MifBridgeAuthoring.cpp's) asserted that it was and reasoned from it. It does
			// exactly what its two siblings do — CreatePackage, FactoryCreateNew an RF_Transactional
			// asset, PostEditChange, FAssetRegistryModule::AssetCreated, MarkPackageDirty — and
			// UMaterialInstance::PostEditChangeProperty runs InitResources() + UpdateStaticPermutation()
			// (MaterialInstance.cpp), i.e. the same material-resource/static-permutation rebuild cited
			// as the reason recompile_material is self-managed. MarkPackageDirty inside a transaction
			// also records an FPackageRecord for a package that has never existed on disk. The plugin's
			// own rule (asset-lifecycle ops must not ride the blanket transaction) applies.
			TEXT("create_material"), TEXT("create_material_function"), TEXT("create_material_instance"),
			// SOURCE MEDIA INGEST (MifBridgeImport.cpp). import_texture and import_asset are the
			// create_material / create_material_instance precedent exactly — CreatePackage,
			// NewObject-or-factory an RF_Transactional asset, PostEditChange,
			// FAssetRegistryModule::AssetCreated, MarkPackageDirty — plus a texture/mesh DDC build.
			// MarkPackageDirty inside the blanket transaction records an FPackageRecord for a package
			// that has never existed on disk, which is the same reason the asset creators above are
			// here. reimport_asset replaces an asset's ENTIRE payload through factory code MifBridge
			// did not write and cannot inspect, which may open its own FScopedTransaction — the
			// invoke_editor_command hazard, one bucket up. set_texture_settings runs
			// UTexture::PostEditChange, which tears down and rebuilds the texture resource and runs an
			// FMaterialUpdateContext over every dependent material (Texture.cpp:783-818): resource and
			// shader-state teardown captured by an undo step is the crash family recompile_material is
			// in this set for.
			//
			// Being here also makes all four compile-heavy, so `batch` refuses them. That is correct:
			// running a factory import inside batch's single open transaction is the same hazard.
			TEXT("import_texture"), TEXT("import_asset"), TEXT("reimport_asset"),
			TEXT("set_texture_settings"),
			// Icon BAKING (MifBridgeThumbnail.cpp). write_thumbnail_texture does CreatePackage ->
			// NewObject/FTextureSource::Init -> PostEditChange -> AssetCreated -> MarkPackageDirty,
			// then blocks on FTextureCompilingManager and saves: the create_material precedent for
			// the first half, the save_dirty_packages precedent for the second (saving is not
			// undoable, and MarkPackageDirty inside a transaction records an FPackageRecord for a
			// package that has never existed on disk). set_asset_thumbnail edits the package's
			// thumbnail map and optionally saves — also not something an undo step can revert.
			// Consequence, stated because it is a real cost: this makes both compile-heavy, so
			// `batch` refuses them and filling N icon stubs is N HTTP calls.
			//
			// render_thumbnail and thumbnail_capabilities are NOT here — they write an image file
			// and no asset, so they are read-only. See IsReadOnlyEndpoint.
			TEXT("write_thumbnail_texture"), TEXT("set_asset_thumbnail"),
			// DELIBERATE EXCEPTION, recorded so it does not read as an oversight: create_struct and
			// create_enum also do CreatePackage -> NewObject -> AssetCreated -> MarkPackageDirty, and
			// are NOT in this set. FStructureEditorUtils::AddVariable/RemoveVariable open their OWN
			// FScopedTransaction around the reinstance-and-recompile (StructureEditorUtils.cpp), so
			// user-defined-struct editing inside a transaction is engine-sanctioned and is not the
			// dead-CDO hazard. Only their package-creation half is out of line with the four asset
			// creators above, and moving them would make them compile-heavy and thus unbatchable —
			// a behaviour change with no defect behind it.
			// Regenerates shader maps and updates every dependent instance
			// (FMaterialUpdateContext). Shader-state teardown captured by an undo step is the
			// same crash family as a full Blueprint compile inside an outer transaction — and
			// per the D-axis Phase-2 verdict the engine's own RecompileMaterial tail runs
			// CollectGarbage twice, which must never happen inside an open transaction either.
			TEXT("recompile_material"),
			// Adding or destroying a ULevel in the open world (Batch I). Same hazard class as
			// new_level/load_level above, and remove_sublevel is the worst of the three:
			// RemoveLevelsFromWorld RESETS the transaction buffer itself
			// (EditorLevelUtils.cpp:886-889) — it would destroy RunEndpoint's own transaction under
			// its feet — then runs GEditor->Cleanse, a forced GC (:909), then a stale-reference
			// sweep that is FATAL when the buffer was reset (:929-937). add_sublevel and
			// set_sublevel_streaming both go through AddLevelToWorld's registration cascade
			// (level load + LevelAdded broadcast + SetCurrentLevel), and set_sublevel_streaming
			// REPLACES the ULevelStreaming object outright rather than editing a property, which is
			// not something an undo step can revert. All three defer their engine call to the next
			// tick and report via list_sublevels' ops[].
			TEXT("add_sublevel"), TEXT("remove_sublevel"), TEXT("set_sublevel_streaming"),
			// set_sublevel_visibility and set_current_sublevel (which reaches it via MakeLevelCurrent,
			// EditorLevelUtils.cpp:585) call UEditorLevelUtils::SetLevelVisibility, which opens its OWN
			// FScopedTransaction (EditorLevelUtils.cpp:1198), runs
			// Level->OwningWorld->FlushLevelStreaming() -> FlushAsyncLoading() inside a
			// while (bLevelsPendingVisibility) loop (World.cpp:4533, :4544-4554), and registers or
			// unregisters an entire level's actors via AddToWorld/RemoveFromWorld. That is a strictly
			// larger cascade than the one add_sublevel is deferred for, and it was riding RunEndpoint's
			// blanket transaction: a nested engine transaction plus a blocking async-loading flush
			// captured as one undo step. (The flush still stalls the HTTP ticker for its duration —
			// documented in this file's hazard header and in both endpoint comment blocks. It is
			// bounded in an editor world, unlike audit_unused's unbounded scan.)
			TEXT("set_sublevel_visibility"), TEXT("set_current_sublevel"),
			// PIE level instances (Batch I). The ULevelStreamingDynamic these create is RF_Transient
			// inside a world that gets torn down at stop_pie; recording it in the editor's undo
			// stack is meaningless, and a later Ctrl-Z over it would restore a pointer into a dead
			// PIE world — the same dead-object shape as compiling inside a transaction. (start_pie/
			// stop_pie sit in the read-only bucket for the neighbouring reason: they mutate no
			// asset. These two DO mutate a world, so self-managed is the honest bucket.)
			TEXT("pie_load_level_instance"), TEXT("pie_unload_level_instance"),
			// EDITOR UI INVOCATION (Batch O). These three execute code MifBridge did not write and
			// cannot inspect: a bound FUIAction, a tab spawner, or whatever a keystroke is bound to.
			// Any of it may open its own FScopedTransaction (most editor commands do), run a full
			// FKismetEditorUtilities::CompileBlueprint, or BE undo/redo — and beginning an undo inside
			// an open transaction violates the engine's own ensure(!GIsTransacting)
			// (TransBuffer.h:74), while a compile captured by an undo step restores a dead CDO and
			// crashes. There is no way to know in advance which of those an arbitrary third-party
			// action is, so the only honest bucket is the one that opens NOTHING: the invoked action
			// then behaves exactly as it does when a human clicks it, including owning its own undo
			// step, which is also the undo the user expects to see.
			//
			// Being here additionally makes them compile-heavy (IsCompileHeavyEndpoint derives from
			// this set), so `batch` refuses them. That is correct and deliberate: firing an editor
			// action inside batch's single open transaction is the same hazard with the same cause.
			//
			// list_editor_commands is NOT here — it is read-only and invokes nothing.
			TEXT("invoke_editor_command"), TEXT("invoke_editor_tab"), TEXT("send_editor_key")
		};
		if (SelfManaged.Contains(Endpoint)) { return true; }
		// Mirror of the read-only fallback. IsCompileHeavyEndpoint derives from this function, so an
		// external SelfManaged endpoint is fenced out of batch's open transaction for free.
		if (const FExternalEndpointDesc* Ext = ExternalRegistry().Find(Endpoint))
		{
			return Ext->Bucket == EEndpointBucket::SelfManaged;
		}
		return false;
	}

	// --- self_audit change detection ----------------------------------------
	// buildDate/buildTime (emitted at the end of H_self_audit) come from __DATE__/__TIME__ and move on
	// EVERY rebuild, including a comment-only one. They answer "is this DLL stale?" and nothing else. The
	// two signatures below answer the question a caller actually has — "did the contract I coded against
	// change?" — and deliberately do NOT move for a rebuild that changed no contract.
	//
	// Names are prefixed Mif/GMif because a unity build merges every unnamed namespace and every
	// namespace-scope `static` in a blob into one scope, so a duplicated helper name across two .cpp
	// files in this module is a hard C2084 (see the note in MifBridgeHandlers.h).

	/** FNV-1a/64 over a canonical text rendering, as 16 lowercase hex chars. Not a security hash: it
	 *  exists so "did the surface change?" is one string compare instead of a full list diff. */
	static FString MifSignatureFold(const TArray<FString>& CanonicalLines)
	{
		uint64 Hash = 0xcbf29ce484222325ULL;
		auto Mix = [&Hash](uint8 Byte)
		{
			Hash ^= static_cast<uint64>(Byte);
			Hash *= 0x100000001b3ULL;
		};
		for (const FString& Line : CanonicalLines)
		{
			const int32 Len = Line.Len();
			for (int32 i = 0; i < Len; ++i)
			{
				// Two bytes per code unit so a non-ASCII name can never alias an ASCII one. Every endpoint
				// name and parameter key in this module is ASCII today; this only keeps that from being a
				// silent assumption. Indexed rather than range-for so the null terminator is provably out.
				const uint32 C = static_cast<uint32>(Line[i]);
				Mix(static_cast<uint8>(C & 0xFF));
				Mix(static_cast<uint8>((C >> 8) & 0xFF));
			}
			// Record separator, so {"ab","c"} and {"a","bc"} cannot fold to the same value.
			Mix(static_cast<uint8>('\n'));
		}
		return FString::Printf(TEXT("%016llx"), Hash);
	}

	// Canonicalised accepted-parameter shapes observed this editor session, filled by RejectUnknownParams
	// (further down this file) and read by H_self_audit (just below). Accepted-key lists are
	// initializer_list literals INSIDE handler bodies, so RejectUnknownParams is the only point in the
	// process that ever sees one — there is no way to enumerate them from H_self_audit without executing
	// every handler. Hence a harvest, and hence it is lazy. Game thread only: every handler runs inline
	// on the HTTP server's ticker.
	// Deduped by the canonical shape string itself — see the harvest in RejectUnknownParams for why
	// there is no cheaper per-call-site gate in front of it.
	static TSet<FString> GMifObservedParamShapes;

	// --- self_audit ---------------------------------------------------------
	// The plugin reporting its OWN invariants, from inside the running DLL. This is the piece that
	// makes "is the bridge healthy?" answerable without reading source or trusting a stale doc:
	// the endpoint list here is the one actually dispatching, not one parsed out of a header.
	// Pair it with the MIF_BIND<->@mcp.tool diff in the README to catch wrapper drift.
	void H_self_audit(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		TArray<FString> Names = GetEndpointNames();
		Names.Sort();

		TArray<TSharedPtr<FJsonValue>> All, ReadOnly, SelfManaged, CompileHeavy, Transacted;
		// Parallel object array. The flat `endpoints` string array (All) is deliberately NOT replaced:
		// the README's MIF_BIND<->@mcp.tool diff and every existing consumer parse it. Additive only.
		TArray<TSharedPtr<FJsonValue>> EndpointRows;
		TMap<FString, int32> ProviderCounts;
		// One canonical line per endpoint, folded into surfaceSignature below. Names is already sorted
		// (just above), so the fold input is canonical for free.
		TArray<FString> SurfaceLines;
		SurfaceLines.Reserve(Names.Num());
		for (const FString& Name : Names)
		{
			All.Add(MakeShared<FJsonValueString>(Name));
			const bool bRO = IsReadOnlyEndpoint(Name);
			const bool bSM = IsSelfManagedEndpoint(Name);
			if (bRO) { ReadOnly.Add(MakeShared<FJsonValueString>(Name)); }
			if (bSM) { SelfManaged.Add(MakeShared<FJsonValueString>(Name)); }
			if (IsCompileHeavyEndpoint(Name)) { CompileHeavy.Add(MakeShared<FJsonValueString>(Name)); }
			// Everything else gets RunEndpoint's blanket transaction.
			if (!bRO && !bSM) { Transacted.Add(MakeShared<FJsonValueString>(Name)); }

			// Per-endpoint attribution: a built-in is owned by "MifBridge", an external by the
			// provider that registered it — so endpoint drift is attributable to a plugin, not just
			// noticed. Bucket is reported from the SAME predicates that dispatch uses, never from a
			// second copy of the policy.
			const FExternalEndpointDesc* Ext = ExternalRegistry().Find(Name);
			// Hoisted into locals only so the signature folds the SAME values the response reports —
			// a second copy of these expressions is how the reported bucket and the folded bucket drift.
			const FString Provider = Ext ? Ext->Provider : FString(TEXT("MifBridge"));
			const FString Bucket = bRO ? TEXT("readOnly") : bSM ? TEXT("selfManaged") : TEXT("transacted");
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			Row->SetStringField(TEXT("name"), Name);
			Row->SetStringField(TEXT("provider"), Provider);
			Row->SetStringField(TEXT("bucket"), Bucket);
			if (Ext && !Ext->Summary.IsEmpty()) { Row->SetStringField(TEXT("summary"), Ext->Summary); }
			EndpointRows.Add(MakeShared<FJsonValueObject>(Row));
			if (Ext) { ProviderCounts.FindOrAdd(Ext->Provider)++; }
			SurfaceLines.Add(Name + TEXT("|") + Bucket + TEXT("|") + Provider);
		}

		Out->SetNumberField(TEXT("endpointCount"), Names.Num());
		Out->SetArrayField(TEXT("endpoints"), All);
		Out->SetArrayField(TEXT("endpointDetails"), EndpointRows);
		Out->SetNumberField(TEXT("externalEndpointCount"), ExternalRegistry().Num());

		TArray<TSharedPtr<FJsonValue>> Providers;
		for (const TPair<FString, int32>& KV : ProviderCounts)
		{
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("provider"), KV.Key);
			P->SetNumberField(TEXT("endpointCount"), KV.Value);
			Providers.Add(MakeShared<FJsonValueObject>(P));
		}
		Out->SetArrayField(TEXT("externalProviders"), Providers);

		TSharedRef<FJsonObject> Buckets = MakeShared<FJsonObject>();
		Buckets->SetArrayField(TEXT("readOnly"), ReadOnly);
		Buckets->SetArrayField(TEXT("selfManaged"), SelfManaged);
		Buckets->SetArrayField(TEXT("transacted"), Transacted);
		Buckets->SetArrayField(TEXT("compileHeavy"), CompileHeavy);
		Out->SetObjectField(TEXT("transactionBuckets"), Buckets);

		// An endpoint in BOTH read-only and self-managed is a policy contradiction: RunEndpoint tests
		// read-only first, so the self-managed intent would be silently ignored.
		TArray<TSharedPtr<FJsonValue>> Contradictions;
		for (const FString& Name : Names)
		{
			if (IsReadOnlyEndpoint(Name) && IsSelfManagedEndpoint(Name))
			{
				Contradictions.Add(MakeShared<FJsonValueString>(Name));
			}
		}
		Out->SetArrayField(TEXT("policyContradictions"), Contradictions);
		Out->SetBoolField(TEXT("healthy"), Contradictions.Num() == 0);

		// Build identity, so a stale DLL is detectable rather than mystifying. These move on EVERY
		// rebuild, including a comment-only one — use the two signatures below to detect a CONTRACT
		// change, not these.
		Out->SetStringField(TEXT("buildDate"), ANSI_TO_TCHAR(__DATE__));
		Out->SetStringField(TEXT("buildTime"), ANSI_TO_TCHAR(__TIME__));
		Out->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());

		// surfaceSignature — ALWAYS complete and deterministic. Folded from the endpoint/bucket/provider
		// data this handler just built, with no dependency on what has been called this session, so two
		// DLLs can be compared the instant they load. Moves when an endpoint is added, removed or
		// renamed, when a bucket is reclassified, or when a provider changes. Check this one first.
		Out->SetStringField(TEXT("surfaceSignature"), MifSignatureFold(SurfaceLines));

		// paramSignature — the ACCEPTED-PARAMETER shapes the strict-params guards validate payloads
		// against. This is a PARTIAL-COVERAGE, RUNTIME-OBSERVED value, not a contract hash of the
		// parameter surface. Read all four limits before relying on it:
		//
		//   1. COVERAGE. Only endpoints that call RejectUnknownParams are represented at all: 83 guard
		//      sites against 199 registered endpoints (MIF_DECL/MIF_BIND). Adding, removing or renaming a
		//      parameter on any of the unguarded majority moves NOTHING here.
		//
		//   2. OBSERVATION. Of the guarded ones, only sites that have actually RUN this session are in
		//      the fold. Accepted-key lists are initializer_list literals inside handler bodies, so
		//      RejectUnknownParams is the only code that ever sees one, and only once that guard has
		//      executed. A freshly loaded DLL reports zero shapes and the set grows as endpoints are
		//      exercised. paramShapesObserved is emitted alongside for exactly this reason: comparing
		//      paramSignature between two builds is only well defined at equal coverage — same call
		//      sequence driven against both, paramShapesObserved matching. surfaceSignature carries no
		//      such caveat.
		//
		//   3. GRANULARITY. Shapes are keyed by the shape itself, not by endpoint name, so two endpoints
		//      with identical accepted sets collapse to one entry (83 sites currently yield 79 distinct
		//      shapes). Within the covered-and-observed set a key added or removed anywhere still moves
		//      the value, but it does not name WHICH endpoint moved — attributing a shape to an endpoint
		//      needs the endpoint name plumbed into RejectUnknownParams from both dispatchers (RunEndpoint
		//      and batch, which dispatches straight out of Handlers() without recursing through
		//      RunEndpoint).
		//
		//   4. BUILD CONFIGURATION. No longer a factor, but it was: the harvest used to gate on the
		//      ADDRESS of a guard's AcceptedSummary literal, and MSVC pools byte-identical literals under
		//      /GF, so colliding guard sites lost their shapes in optimised builds and kept them in
		//      unoptimised ones. That gate is gone (see RejectUnknownParams below). If a pointer-identity
		//      gate is ever reintroduced anywhere in this path, this caveat comes back with it.
		//
		// What it proves: if paramSignature MOVES, a covered, observed accepted list changed. It does NOT
		// prove the converse — an unchanged value is not evidence the parameter surface is unchanged. It
		// also ignores key reorders, case changes, reworded error text, and logic-only edits by design.
		TArray<FString> ShapeLines = GMifObservedParamShapes.Array();
		ShapeLines.Sort();
		Out->SetStringField(TEXT("paramSignature"), MifSignatureFold(ShapeLines));
		Out->SetNumberField(TEXT("paramShapesObserved"), ShapeLines.Num());
	}

	bool IsCompileHeavyEndpoint(const FString& Endpoint)
	{
		// Anything that runs a full FKismetEditorUtilities::CompileBlueprint must not execute inside
		// batch's single open transaction — reinstancing captured by an undo step restores a dead CDO
		// and crashes. Derived from IsSelfManagedEndpoint rather than duplicated as a literal list:
		// batch's old hardcoded set had already drifted, silently permitting compile, validate,
		// create_blueprint, set_property, create_editable_child and the asset-lifecycle ops.
		// set_property is the one deliberate subtraction. It is SelfManaged because ONE of its branches
		// (widgetName -> FKismetEditorUtilities::CompileBlueprint, MifBridgeNodes5.cpp) compiles, but
		// the objectPath branch — CDO edits, component templates, node properties, placed actors —
		// compiles nothing. Treating the endpoint as compile-heavy banned ALL of it from batch, while
		// docs/02_GOTCHAS.md §5d tells callers to batch exactly those writes. The widget branch refuses
		// itself when IsBatchTransactionOpen(), so the hazard is fenced at the branch that has it.
		return (IsSelfManagedEndpoint(Endpoint) && Endpoint != TEXT("set_property"))
			|| Endpoint == TEXT("compile")
			|| Endpoint == TEXT("validate");
	}

	// batch-open marker. Game thread only (every handler runs inline on it), and a nested batch is
	// refused before it could ever construct a second scope, so a plain bool is sufficient and a
	// counter would only hide a nesting bug.
	static bool GbBatchTransactionOpen = false;
	bool IsBatchTransactionOpen() { return GbBatchTransactionOpen; }
	FBatchTransactionScope::FBatchTransactionScope()  { GbBatchTransactionOpen = true; }
	FBatchTransactionScope::~FBatchTransactionScope() { GbBatchTransactionOpen = false; }

	// Turn any recorded silent-ignore into a failed response. Declared before RunEndpoint because both
	// of RunEndpoint's exit paths need it; defined here so there is exactly one copy of the wording.
	static void ReportParamTypeViolations(const TSharedRef<FJsonObject>& Out, bool bRolledBack)
	{
		if (NumParamTypeViolations() == 0) { return; }
		const FString Detail = DescribeParamTypeViolations();
		// Preserve a handler's own error if it already failed — its reason is more specific than ours.
		const FString Existing = IsOk(Out) ? FString() : JStr(Out, TEXT("error"));
		Out->SetStringField(TEXT("ignoredParameters"), Detail);
		Fail(Out, FString::Printf(
			TEXT("%s%s. A supplied parameter of the wrong JSON type is REFUSED, never defaulted: acting on a default the ")
			TEXT("caller did not send returns a result they never asked for and the response then echoes it back as if it ")
			TEXT("were intentional. %s"),
			Existing.IsEmpty() ? TEXT("") : *(Existing + TEXT(" — additionally: ")),
			*Detail,
			bRolledBack
				? TEXT("This call's transaction was CANCELLED, which DISCARDS THE UNDO ENTRY - it does not roll the ")
				  TEXT("edit back. UTransBuffer::Cancel pops the transaction off the undo buffer and never calls ")
				  TEXT("FTransaction::Apply (EditorTransaction.cpp:1387-1437), so any write the handler completed ")
				  TEXT("before this check still stands. Re-read the target before retrying.")
				: TEXT("This endpoint manages its own transactions (it compiles, or it is batch), so any write it ")
				  TEXT("completed before this check still stands — re-read the target before retrying.")));
	}

	void RunEndpoint(const FString& Endpoint, const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		Out->SetBoolField(TEXT("ok"), true);

		const FHandlerFn* Fn = Handlers().Find(Endpoint);
		// Built-ins win by construction — RegisterExternalEndpoint refuses a name that collides with
		// one, so this lookup order can never shadow a built-in.
		const FExternalEndpointDesc* Ext = Fn ? nullptr : ExternalRegistry().Find(Endpoint);
		if (!Fn && !Ext)
		{
			Fail(Out, FString::Printf(TEXT("unknown endpoint: %s"), *Endpoint));
			return;
		}

		// Editor-only script paths are allowed inside this scope.
		FEditorScriptExecutionGuard ScriptGuard;

		// Batch L, defect 1: one reset per request, so "a supplied parameter was ignored because its
		// JSON type was wrong" is a property of THIS call and not of a previous one. batch dispatches
		// its ops straight out of Handlers() rather than recursing through here, so one reset covers a
		// whole batch and a violation in any op fails the envelope.
		ResetParamTypeViolations();

		// Read-only endpoints and self-managed (compile-inside) endpoints run without the
		// blanket transaction — the latter open their own scoped transactions internally.
		// IsReadOnly/IsSelfManaged already consult the external registry, so ONE test covers both
		// kinds and an external descriptor's bucket is honoured exactly as a built-in's TSet entry.
		if (IsReadOnlyEndpoint(Endpoint) || IsSelfManagedEndpoint(Endpoint))
		{
			if (Fn) { (*Fn)(In, Out); } else { Ext->Handler(In, Out); }
			// These two buckets have NO transaction to cancel — a self-managed handler opens its own
			// tight ones and they have already committed. The response is still failed, because
			// answering ok:true about a value the caller never sent is the defect; but the honest
			// statement is "this call is not atomic", which the message makes.
			ReportParamTypeViolations(Out, /*bRolledBack*/ false);
			return;
		}

		// Every mutation the handler performs is captured in one transaction so the
		// user can Ctrl-Z the whole bridge action.
		FScopedTransaction Transaction(FText::Format(LOCTEXT("BridgeEditFmt", "Mif Bridge: {0}"), FText::FromString(Endpoint)));
		if (Fn) { (*Fn)(In, Out); } else { Ext->Handler(In, Out); }

		// FAILURE DISCARDS THE UNDO ENTRY. IT DOES NOT ROLL ANYTHING BACK. Batch K added the Cancel()
		// below and this comment used to read "FAILURE ROLLS BACK ... every handler is atomic-on-failure
		// without restructuring any of them". THAT WAS FALSE, and Batch M proved it with the first call
		// that ever mutated and then genuinely failed: override_inherited_component minted an ICH
		// override, failed on the property, cancelled — and the override was still on the asset.
		//
		// UTransBuffer::Cancel (EditorTransaction.cpp:1387-1437) broadcasts TransactionCanceled, calls
		// GUndo->EndOperation(), nulls GUndo and POPS the transaction off UndoBuffer. It never calls
		// FTransaction::Apply(); the only two callers of Apply are UTransBuffer::Undo (:1624) and
		// ::Redo (:1688). The engine's own doc for the virtual says exactly this and no more: "Cancels
		// the current transaction, no longer capture actions to be placed in the undo buffer"
		// (Editor/Transactor.h:514-519). Cancel means STOP RECORDING AND THROW THE RECORD AWAY.
		//
		// WHAT Cancel() IS STILL WORTH DOING. A failed call must not leave a bogus entry on the undo
		// stack: without it, the user's next Ctrl-Z would undo a bridge action that reported failure
		// instead of undoing their own last edit. That is the whole benefit, and it is real.
		//
		// WHAT MAKES A FAILED CALL LEAVE NOTHING BEHIND IS THE HANDLER'S OWN ORDER: validate every
		// input BEFORE the first mutation, or undo what it created on its own failure path. See
		// docs/01_POSTMORTEMS.md PM-007 for the rule and docs/audit/06_IMPLEMENTED.md "Batch M" for
		// the per-handler audit. Do NOT write a new handler that mutates first and validates second on
		// the strength of this line.
		//
		// A parameter the caller supplied and this handler silently ignored fails the call BEFORE the
		// commit decision below. Checked here rather than at each of ~80 read sites: this is the one
		// place every endpoint passes through, which is the whole point of doing it centrally.
		ReportParamTypeViolations(Out, /*bRolledBack*/ true);

		if (!IsOk(Out))
		{
			Transaction.Cancel();
		}
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

	// --- Strict numeric reading + the silent-ignore backstop (Batch L, defect 1) ------------
	// See MifBridgeHandlers.h for the live evidence. Everything below exists because "the field was
	// absent" and "the field was supplied and I could not use it" were the SAME answer, and the
	// second one has to be a hard error naming the field, the value and the expected type.

	namespace
	{
		// Per-request record. The bridge marshals every handler onto the game thread
		// (MifBridgeServer.cpp) and RunEndpoint is the only entry point, so one static is one request.
		// Not thread_local on purpose: a violation recorded on any other thread would be a bug in the
		// marshalling, and hiding it in a second slot would make that bug invisible.
		TArray<FString>& ParamTypeViolations()
		{
			static TArray<FString> Violations;
			return Violations;
		}

		// Short, quotable rendering of what the caller actually sent. Used in every error below, so a
		// refusal shows the offending value rather than only naming the field.
		FString DescribeJsonValue(const TSharedPtr<FJsonValue>& Value)
		{
			if (!Value.IsValid()) { return TEXT("null"); }
			switch (Value->Type)
			{
			case EJson::String:  return FString::Printf(TEXT("the string \"%s\""), *Value->AsString());
			case EJson::Boolean: return Value->AsBool() ? TEXT("the boolean true") : TEXT("the boolean false");
			case EJson::Number:  return FString::Printf(TEXT("the number %s"), *FString::SanitizeFloat(Value->AsNumber()));
			case EJson::Array:   return TEXT("an array");
			case EJson::Object:  return TEXT("an object");
			case EJson::Null:    return TEXT("null");
			default:             return TEXT("nothing");
			}
		}

		void RecordParamTypeViolation(const TCHAR* Field, const TSharedPtr<FJsonValue>& Value, const TCHAR* Expected)
		{
			ParamTypeViolations().Add(FString::Printf(
				TEXT("'%s' was given %s, which is not %s — it was IGNORED and the default was used instead"),
				Field, *DescribeJsonValue(Value), Expected));
		}

		// TryGetField is case-insensitive the same way JStr/JBool/JInt are, so a key that WOULD be
		// honoured is never reported as a violation.
		TSharedPtr<FJsonValue> FieldIfPresent(const TSharedRef<FJsonObject>& In, const TCHAR* Field)
		{
			return In->HasField(Field) ? In->TryGetField(Field) : TSharedPtr<FJsonValue>();
		}
	}

	void ResetParamTypeViolations()
	{
		ParamTypeViolations().Reset();
	}

	int32 NumParamTypeViolations()
	{
		return ParamTypeViolations().Num();
	}

	FString DescribeParamTypeViolations()
	{
		return FString::Join(ParamTypeViolations(), TEXT("; "));
	}

	bool ParseWholeNumber(const FString& Text, double& OutValue)
	{
		const FString T = Text.TrimStartAndEnd();
		if (T.IsEmpty()) { return false; }

		// Scanned by hand on purpose. Core has no strtod-with-end-pointer (FCString exposes Atod, not
		// Strtod), and every parser it DOES expose — Atod, Atoi, LexTryParseString, and UE's own
		// property importers — stops at the first character it cannot read and reports success for
		// the prefix it managed. "12abc" becomes 12 and "not-a-float" becomes 0, with nothing
		// anywhere saying so. That is defect 1 (JSON side) and defect 2 (property-text side) in one
		// sentence, so this is the one function that answers "is the WHOLE thing a number".
		//
		// Grammar: [+-] digits [ . digits ] [ (e|E) [+-] digits ], at least one mantissa digit, and
		// nothing left over. FString::IsNumeric() is not enough — it rejects exponents outright and
		// accepts a lone "." in some versions.
		const TCHAR* P = *T;
		if (*P == TEXT('+') || *P == TEXT('-')) { ++P; }
		int32 MantissaDigits = 0;
		while (FChar::IsDigit(*P)) { ++P; ++MantissaDigits; }
		if (*P == TEXT('.'))
		{
			++P;
			while (FChar::IsDigit(*P)) { ++P; ++MantissaDigits; }
		}
		if (MantissaDigits == 0) { return false; }
		if (*P == TEXT('e') || *P == TEXT('E'))
		{
			++P;
			if (*P == TEXT('+') || *P == TEXT('-')) { ++P; }
			int32 ExponentDigits = 0;
			while (FChar::IsDigit(*P)) { ++P; ++ExponentDigits; }
			if (ExponentDigits == 0) { return false; }
		}
		if (*P != TEXT('\0')) { return false; }   // trailing garbage: "12abc", "1 2", "5%", "0.5f"

		const double Parsed = FCString::Atod(*T);
		if (!FMath::IsFinite(Parsed)) { return false; }
		OutValue = Parsed;
		return true;
	}

	bool JsonValueAsNumber(const TSharedPtr<FJsonValue>& Value, const FString& Where,
		double& OutValue, FString& OutError)
	{
		if (!Value.IsValid() || Value->Type == EJson::None)
		{
			OutError = FString::Printf(TEXT("'%s' has no value — expected a number"), *Where);
			return false;
		}
		if (Value->Type == EJson::Number)
		{
			OutValue = Value->AsNumber();
			return true;
		}
		// A JSON string that is ENTIRELY a number is accepted (callers legitimately send "1.5" from
		// shells and spreadsheets), but only entirely: "1.5deg" is a mistake, not a unit.
		if (Value->Type == EJson::String && ParseWholeNumber(Value->AsString(), OutValue))
		{
			return true;
		}
		OutError = FString::Printf(
			TEXT("'%s' was given %s, which is not a number. Send a JSON number (e.g. %s: 12.5), or a string that is ")
			TEXT("entirely numeric (\"12.5\"). A partly-numeric string like \"12abc\" is refused on purpose: UE's ")
			TEXT("parsers accept the prefix and discard the rest, which is how a bad value becomes a plausible one."),
			*Where, *DescribeJsonValue(Value), *Where);
		return false;
	}

	EJsonRead ReadNumberField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		const FString& Where, double& InOutValue, FString& OutError)
	{
		const TSharedPtr<FJsonValue> Value = FieldIfPresent(In, Field);
		if (!Value.IsValid()) { return EJsonRead::Absent; }
		double Parsed = 0.0;
		if (!JsonValueAsNumber(Value, Where, Parsed, OutError)) { return EJsonRead::Invalid; }
		InOutValue = Parsed;
		return EJsonRead::Read;
	}

	namespace
	{
		// The body shared by ReadVectorField / ReadRotatorField / ReadScaleField. Components are named
		// by the CALLER'S spelling in every error, so "location.x" points at the key that was sent.
		struct FVectorComponentNames
		{
			const TCHAR* A;      // primary spelling  (x / pitch)
			const TCHAR* B;
			const TCHAR* C;
			const TCHAR* AltA;   // alternate spelling accepted for rotators, nullptr otherwise
			const TCHAR* AltB;
			const TCHAR* AltC;
		};

		// The object form, shared by the named-field readers and by ReadVectorObject (a points[] entry
		// is a bare {x,y,z} with no field name of its own).
		bool ReadTripleObject(const TSharedRef<FJsonObject>& Obj, const FString& Where,
			const FVectorComponentNames& Names, double& X, double& Y, double& Z, FString& OutError)
		{
			// An unrecognised component key is a typo, and a typo that is ignored puts the object
			// somewhere the caller did not ask for — the same silent-ignore class, one level down.
			TArray<const TCHAR*> Accepted;
			Accepted.Add(Names.A); Accepted.Add(Names.B); Accepted.Add(Names.C);
			if (Names.AltA) { Accepted.Add(Names.AltA); Accepted.Add(Names.AltB); Accepted.Add(Names.AltC); }
			TArray<FString> Unknown;
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Obj->Values)
			{
				bool bKnown = false;
				for (const TCHAR* Key : Accepted)
				{
					if (Pair.Key.Equals(Key, ESearchCase::IgnoreCase)) { bKnown = true; break; }
				}
				if (!bKnown) { Unknown.Add(Pair.Key); }
			}
			if (Unknown.Num() > 0)
			{
				TArray<FString> AcceptedNames;
				for (const TCHAR* Key : Accepted) { AcceptedNames.Add(Key); }
				OutError = FString::Printf(TEXT("'%s' has unrecognised component(s): %s. Accepts %s."),
					*Where, *FString::Join(Unknown, TEXT(", ")), *FString::Join(AcceptedNames, TEXT(" / ")));
				return false;
			}

			// Per component: absent keeps the incoming value, supplied must be a number. This is the
			// distinction defect 1 collapsed — {"x":"not-a-number","y":123,"z":456} applied y and z,
			// kept x, and echoed the mixture back as if it were the request.
			struct FComp { const TCHAR* Primary; const TCHAR* Alt; double* Slot; };
			const FComp Comps[3] = {
				{ Names.A, Names.AltA, &X }, { Names.B, Names.AltB, &Y }, { Names.C, Names.AltC, &Z } };
			for (const FComp& C : Comps)
			{
				const TCHAR* Present = Obj->HasField(C.Primary) ? C.Primary
					: ((C.Alt && Obj->HasField(C.Alt)) ? C.Alt : nullptr);
				if (!Present) { continue; }
				if (ReadNumberField(Obj, Present, FString::Printf(TEXT("%s.%s"), *Where, Present),
					*C.Slot, OutError) == EJsonRead::Invalid)
				{
					return false;
				}
			}
			return true;
		}

		EJsonRead ReadTripleField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
			const FVectorComponentNames& Names, double& X, double& Y, double& Z,
			bool bAllowBareNumberAsUniform, FString& OutError)
		{
			const TSharedPtr<FJsonValue> Value = FieldIfPresent(In, Field);
			if (!Value.IsValid()) { return EJsonRead::Absent; }

			if (Value->Type == EJson::Object)
			{
				const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
				if (!Value->TryGetObject(ObjPtr) || ObjPtr == nullptr || !ObjPtr->IsValid())
				{
					OutError = FString::Printf(TEXT("'%s' is an object the bridge could not read"), Field);
					return EJsonRead::Invalid;
				}
				return ReadTripleObject(ObjPtr->ToSharedRef(), Field, Names, X, Y, Z, OutError)
					? EJsonRead::Read : EJsonRead::Invalid;
			}

			if (Value->Type == EJson::Array)
			{
				const TArray<TSharedPtr<FJsonValue>>& Arr = Value->AsArray();
				if (Arr.Num() != 3)
				{
					OutError = FString::Printf(TEXT("'%s' as an array must hold exactly 3 numbers [%s,%s,%s] (got %d)"),
						Field, Names.A, Names.B, Names.C, Arr.Num());
					return EJsonRead::Invalid;
				}
				double* Slots[3] = { &X, &Y, &Z };
				const TCHAR* Labels[3] = { Names.A, Names.B, Names.C };
				for (int32 i = 0; i < 3; ++i)
				{
					// AsNumber() used to read these: it returns 0.0 for a string with no way to tell.
					if (!JsonValueAsNumber(Arr[i], FString::Printf(TEXT("%s[%d] (%s)"), Field, i, Labels[i]),
						*Slots[i], OutError))
					{
						return EJsonRead::Invalid;
					}
				}
				return EJsonRead::Read;
			}

			if (bAllowBareNumberAsUniform)
			{
				double Uniform = 0.0;
				if (!JsonValueAsNumber(Value, Field, Uniform, OutError)) { return EJsonRead::Invalid; }
				X = Y = Z = Uniform;
				return EJsonRead::Read;
			}

			OutError = FString::Printf(
				TEXT("'%s' was given %s. Send {\"%s\":..,\"%s\":..,\"%s\":..} or [%s,%s,%s]."),
				Field, *DescribeJsonValue(Value), Names.A, Names.B, Names.C, Names.A, Names.B, Names.C);
			return EJsonRead::Invalid;
		}
	}

	EJsonRead ReadVectorField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		FVector& InOutVec, FString& OutError)
	{
		const FVectorComponentNames Names{ TEXT("x"), TEXT("y"), TEXT("z"), nullptr, nullptr, nullptr };
		double X = InOutVec.X, Y = InOutVec.Y, Z = InOutVec.Z;
		const EJsonRead R = ReadTripleField(In, Field, Names, X, Y, Z, /*bAllowBareNumberAsUniform*/false, OutError);
		if (R == EJsonRead::Read) { InOutVec = FVector(X, Y, Z); }
		return R;
	}

	EJsonRead ReadRotatorField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		FRotator& InOutRot, FString& OutError)
	{
		// x/y/z = pitch/yaw/roll is what every MifBridge transform emits and documents; pitch/yaw/roll
		// is what a caller reading the response back would naturally type. Both, so neither is a trap.
		const FVectorComponentNames Names{ TEXT("x"), TEXT("y"), TEXT("z"), TEXT("pitch"), TEXT("yaw"), TEXT("roll") };
		double P = InOutRot.Pitch, Yw = InOutRot.Yaw, Rl = InOutRot.Roll;
		const EJsonRead R = ReadTripleField(In, Field, Names, P, Yw, Rl, /*bAllowBareNumberAsUniform*/false, OutError);
		if (R == EJsonRead::Read) { InOutRot = FRotator(P, Yw, Rl); }
		return R;
	}

	EJsonRead ReadScaleField(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		FVector& InOutVec, FString& OutError)
	{
		const FVectorComponentNames Names{ TEXT("x"), TEXT("y"), TEXT("z"), nullptr, nullptr, nullptr };
		double X = InOutVec.X, Y = InOutVec.Y, Z = InOutVec.Z;
		const EJsonRead R = ReadTripleField(In, Field, Names, X, Y, Z, /*bAllowBareNumberAsUniform*/true, OutError);
		if (R == EJsonRead::Read) { InOutVec = FVector(X, Y, Z); }
		return R;
	}

	bool ReadVectorObject(const TSharedRef<FJsonObject>& Obj, const FString& Where,
		FVector& InOutVec, FString& OutError)
	{
		const FVectorComponentNames Names{ TEXT("x"), TEXT("y"), TEXT("z"), nullptr, nullptr, nullptr };
		double X = InOutVec.X, Y = InOutVec.Y, Z = InOutVec.Z;
		if (!ReadTripleObject(Obj, Where, Names, X, Y, Z, OutError)) { return false; }
		InOutVec = FVector(X, Y, Z);
		return true;
	}

	// The accessors below keep their "absent -> Default" contract, which is what makes optional
	// parameters work. What changed is the OTHER case: a field that is PRESENT but of the wrong JSON
	// type used to take the same path and be indistinguishable from absence. It now records a
	// violation, and RunEndpoint fails the whole request rather than letting the handler act on a
	// default the caller never asked for.
	double JNum(const TSharedRef<FJsonObject>& In, const TCHAR* Field, double Default)
	{
		const TSharedPtr<FJsonValue> Value = FieldIfPresent(In, Field);
		if (!Value.IsValid()) { return Default; }
		double Parsed = 0.0;
		FString Unused;
		if (JsonValueAsNumber(Value, Field, Parsed, Unused)) { return Parsed; }
		RecordParamTypeViolation(Field, Value, TEXT("a number"));
		return Default;
	}

	int32 JInt(const TSharedRef<FJsonObject>& In, const TCHAR* Field, int32 Default)
	{
		const TSharedPtr<FJsonValue> Value = FieldIfPresent(In, Field);
		if (!Value.IsValid()) { return Default; }
		double Parsed = 0.0;
		FString Unused;
		if (!JsonValueAsNumber(Value, Field, Parsed, Unused))
		{
			// FJsonValueString::TryGetNumber(int32&) ALWAYS returns true and LexFromString gives 0 for
			// garbage (JsonValue.h:135), so the old TryGetNumberField could not fail here at all.
			RecordParamTypeViolation(Field, Value, TEXT("a whole number"));
			return Default;
		}
		if (Parsed != FMath::TruncToDouble(Parsed))
		{
			RecordParamTypeViolation(Field, Value, TEXT("a WHOLE number (it has a fractional part)"));
			return Default;
		}
		return (int32)FMath::Clamp(Parsed, (double)MIN_int32, (double)MAX_int32);
	}

	bool JBool(const TSharedRef<FJsonObject>& In, const TCHAR* Field, bool Default)
	{
		const TSharedPtr<FJsonValue> Value = FieldIfPresent(In, Field);
		if (!Value.IsValid()) { return Default; }
		if (Value->Type == EJson::Boolean) { return Value->AsBool(); }
		// 0/1 and the recognised word spellings stay accepted — they always worked and callers use
		// them. What is refused is FString::ToBool()'s silent verdict on everything else: it answers
		// false for "banana", so {"confirm":"banana"} used to mean "the caller did not confirm".
		if (Value->Type == EJson::Number)
		{
			const double D = Value->AsNumber();
			if (D == 0.0 || D == 1.0) { return D != 0.0; }
			RecordParamTypeViolation(Field, Value, TEXT("a boolean (or the number 0 / 1)"));
			return Default;
		}
		if (Value->Type == EJson::String)
		{
			const FString T = Value->AsString().TrimStartAndEnd();
			if (T.Equals(TEXT("true"), ESearchCase::IgnoreCase) || T.Equals(TEXT("yes"), ESearchCase::IgnoreCase)
				|| T.Equals(TEXT("on"), ESearchCase::IgnoreCase) || T == TEXT("1")) { return true; }
			if (T.Equals(TEXT("false"), ESearchCase::IgnoreCase) || T.Equals(TEXT("no"), ESearchCase::IgnoreCase)
				|| T.Equals(TEXT("off"), ESearchCase::IgnoreCase) || T == TEXT("0")) { return false; }
			RecordParamTypeViolation(Field, Value, TEXT("a boolean (true/false, yes/no, on/off, 1/0)"));
			return Default;
		}
		RecordParamTypeViolation(Field, Value, TEXT("a boolean"));
		return Default;
	}

	FString JStrAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, const FString& Default)
	{
		for (const TCHAR* Field : Fields)
		{
			FString Value;
			if (In->TryGetStringField(Field, Value) && !Value.IsEmpty())
			{
				return Value;
			}
		}
		return Default;
	}

	bool JBoolAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, bool Default)
	{
		// Routes through JBool so the spelling-tolerant form inherits the same type strictness — two
		// implementations of "what counts as a boolean" is exactly the drift PM-005 is about.
		for (const TCHAR* Field : Fields)
		{
			if (In->HasField(Field)) { return JBool(In, Field, Default); }
		}
		return Default;
	}

	int32 JIntAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, int32 Default)
	{
		// Routes through JInt for the same reason JBoolAny routes through JBool.
		for (const TCHAR* Field : Fields)
		{
			if (In->HasField(Field)) { return JInt(In, Field, Default); }
		}
		return Default;
	}

	bool JHasAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields)
	{
		for (const TCHAR* Field : Fields)
		{
			if (In->HasField(Field))
			{
				return true;
			}
		}
		return false;
	}

	// The ONE shared implementation of strict unknown-param rejection (see the header for the
	// find_assets postmortem that motivated it). Promoted from a MifBridgeCooked.cpp file-local
	// in Batch C so new handler files cannot grow divergent copies. Safe against transport noise:
	// MifBridgeServer.cpp deserialises the POST body directly as the param object (the token
	// travels in the X-Mif-Token header) and server.py's _post drops unset (None) kwargs, so
	// In->Values holds only what the caller actually sent.
	bool RejectUnknownParams(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
		std::initializer_list<const TCHAR*> AcceptedKeys, const TCHAR* AcceptedSummary,
		std::initializer_list<TPair<const TCHAR*, const TCHAR*>> KeyNotes)
	{
		// Harvest this guard's accepted-key SHAPE for self_audit's paramSignature. This is the only place
		// in the process that ever sees an accepted-key list. Canonicalised — lowercased and sorted — so a
		// rebuild that merely reorders or re-cases a list does NOT move the signature; the match below is
		// ESearchCase::IgnoreCase, so case genuinely carries no meaning here.
		//
		// There is deliberately NO per-call-site gate. This used to skip the work when the AcceptedSummary
		// POINTER had been seen before, on the assumption that each guard site owns its own string literal.
		// It does not: MSVC pools byte-identical string literals (/GF, on in every optimised configuration),
		// so sites that share a summary share its address. Four pairs in this module do today —
		// MifBridgeAssetOps.cpp:263/:294 ("path"), MifBridgeCooked.cpp:534/:902 ("limit"),
		// MifBridgeNodes.cpp:804/:898 ("graphId, x, y"), and MifBridgeCooked.cpp:120 /
		// MifBridgeMaterials.cpp:1667 ("(none - ...)"). Whichever ran first claimed the address and the
		// other's shape was never harvested. Those four happen to carry IDENTICAL key lists right now, so
		// nothing is lost today — which is precisely the danger: the moment anyone adds an alias to one
		// side of a pair without also rewording its prose summary, that shape stops being harvested, with
		// no compile error, no test failure, and a paramSignature that still looks healthy. A signature
		// that silently misses shapes is worse than no signature, because callers trust it.
		//
		// The dedupe therefore lives where it is provably correct: GMifObservedParamShapes is a TSet keyed
		// on the canonical shape STRING, so re-adding is idempotent and no two sites can alias each other.
		// The cost is a lowercase + sort + join of at most ~21 short keys per guarded call, inside handlers
		// that are about to touch assets or compile blueprints. It does not register.
		{
			TArray<FString> Canonical;
			Canonical.Reserve(static_cast<int32>(AcceptedKeys.size()));
			for (const TCHAR* Key : AcceptedKeys)
			{
				Canonical.Add(FString(Key).ToLower());
			}
			Canonical.Sort();
			GMifObservedParamShapes.Add(FString::Join(Canonical, TEXT(",")));
		}

		TArray<FString> Unrecognised;
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : In->Values)
		{
			// 'op' is the BATCH DISPATCHER's routing key, not a handler parameter: H_batch passes each
			// op object to the handler verbatim, 'op' field included. Without this, every guarded
			// endpoint failed with "unrecognised parameter 'op'" the moment it was called inside batch
			// — a regression the guards themselves introduced. Tolerated centrally so no call site has
			// to remember it, but ONLY while a batch is actually open: an unconditional skip meant
			// find_assets {"op":"typo"} over raw HTTP was silently accepted, and "an ignored parameter
			// is worse than a rejected one" is this codebase's own rule.
			if (Pair.Key.Equals(TEXT("op"), ESearchCase::IgnoreCase) && IsBatchTransactionOpen())
			{
				continue;
			}

			bool bKnown = false;
			for (const TCHAR* Key : AcceptedKeys)
			{
				// Case-insensitive to match how JStr/JBool/JInt find fields (FString keys hash and
				// compare case-insensitively), so a key that WOULD be honoured is never rejected.
				if (Pair.Key.Equals(Key, ESearchCase::IgnoreCase)) { bKnown = true; break; }
			}
			if (bKnown)
			{
				continue;
			}
			const TCHAR* Note = nullptr;
			for (const TPair<const TCHAR*, const TCHAR*>& KeyNote : KeyNotes)
			{
				if (Pair.Key.Equals(KeyNote.Key, ESearchCase::IgnoreCase)) { Note = KeyNote.Value; break; }
			}
			Unrecognised.Add(Note
				? FString::Printf(TEXT("'%s' (%s)"), *Pair.Key, Note)
				: FString::Printf(TEXT("'%s'"), *Pair.Key));
		}
		if (Unrecognised.Num() == 0)
		{
			return false;
		}
		Fail(Out, FString::Printf(TEXT("unrecognised parameter%s %s - accepted: %s"),
			Unrecognised.Num() == 1 ? TEXT("") : TEXT("s"),
			*FString::Join(Unrecognised, TEXT(", ")), AcceptedSummary));
		return true;
	}

	// ONE writer for the asset-identity fields (see the header). Two file-local copies existed —
	// one in MifBridgeCooked.cpp and one in MifBridgeAssetOps.cpp — each with an eviction clause to
	// promote on the next header edit; the unity build settled it by failing with C2084 when a
	// third file joined the blob.
	void EmitAssetIdentity(const TSharedRef<FJsonObject>& Row, const FString& ObjectPath, const FString& PackageName)
	{
		Row->SetStringField(TEXT("objectPath"), ObjectPath);
		Row->SetStringField(TEXT("packageName"), PackageName);
	}

	// Every in-process PIE world (see the header). Lived in MifBridgePIE.cpp's anonymous namespace
	// until MifBridgeStreaming.cpp needed the same selection rule and the unity blob rejected the
	// duplicate: internal linkage per-TU is exactly what unity builds collapse.
	void CollectPIEWorlds(TArray<UWorld*>& OutWorlds)
	{
		if (!GEngine)
		{
			return;
		}
		for (const FWorldContext& Ctx : GEngine->GetWorldContexts())
		{
			if (Ctx.WorldType == EWorldType::PIE && Ctx.World() != nullptr)
			{
				OutWorlds.Add(Ctx.World());
			}
		}
	}

	// --- Shared helpers promoted out of per-file copies ----------------------
	// See MifBridgeHandlers.h for why each of these may only exist once: a unity build merges all
	// unnamed namespaces in a TU into one, and `static` collapses identically, so a same-named
	// file-local helper in two files that share a Module.MifBridge.N.cpp blob is a hard C2084. Blob
	// membership follows file SIZES and shifts with every edit, so it is never a safe fence.

	UWorld* EditorWorld()
	{
		return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
	}

	UWorld* ActiveWorld()
	{
		if (GEditor && GEditor->PlayWorld) { return GEditor->PlayWorld; }
		return EditorWorld();
	}

	// THE one UEngine::Exec call site in this module (Batch O). Both run_console and
	// run_console_captured route through here; a third copy is the PM-005 bug class, and this is also
	// the place the "which Exec overload" trap is recorded once instead of in two comment blocks:
	//
	//   ENGINE_API virtual bool UEngine::Exec(UWorld*, const TCHAR*, FOutputDevice& = *GLog)
	//        Engine.h:2224, under `public:` at :2222   <- THIS ONE
	//   ENGINE_API virtual bool UEngine::Exec_Editor(...)          Engine.h:2229, `protected:` at :2227
	//   UNREALED_API virtual bool UEditorEngine::Exec_Editor(...)  EditorEngine.h:817, `protected:` at :816
	//
	// The two Exec_Editor overloads carry export macros and are STILL inaccessible — the same
	// exported-but-protected shape this project has been bitten by before. Do not reach for them;
	// UEngine::Exec dispatches into them anyway.
	//
	// The tee is what makes capture non-destructive. run_console's documented workflow is "run the
	// command, then tail <Saved>/Logs/*.log" — every mif.kr.* command relies on it. Swapping the
	// default Ar (*GLog) for a plain string device would silently delete that output from the log, so
	// the device forwards every Serialize to GLog exactly as before AND keeps a copy. Pass OutText
	// null for byte-for-byte the old behaviour.
	bool RunEngineExec(UWorld* World, const FString& Command, FString* OutText)
	{
		if (!GEngine) { return false; }
		if (!OutText)
		{
			return GEngine->Exec(World, *Command);
		}

		// Local class: zero chance of colliding with another translation unit in the unity blob,
		// which a file-scope helper of any linkage would have (PM-005, [namespace.unnamed]/1).
		class FMifExecTee : public FOutputDevice
		{
		public:
			explicit FMifExecTee(FString& InSink) : Sink(InSink) {}
			virtual void Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity, const FName& Category) override
			{
				if (Sink.Len() < MaxChars)
				{
					Sink.Append(V);
					Sink.AppendChar(TEXT('\n'));
				}
				else
				{
					bTruncated = true;
				}
				// Forward, so nothing that used to reach the log stops reaching it.
				if (GLog) { GLog->Serialize(V, Verbosity, Category); }
			}
			bool bTruncated = false;
		private:
			// enum, not `static constexpr`: a LOCAL class may not have static data members
			// ([class.local]/4) and MSVC rejects it with C2246 even for constexpr. An unscoped
			// enumerator is a compile-time constant that carries no storage, so it is legal here.
			enum : int32 { MaxChars = 256 * 1024 };
			FString& Sink;
		};

		OutText->Reset();
		FMifExecTee Tee(*OutText);
		const bool bHandled = GEngine->Exec(World, *Command, Tee);
		if (Tee.bTruncated)
		{
			// Never let a cap look like "that was all of it".
			OutText->Append(TEXT("\n[MifBridge] output truncated at 256 KB — tail the editor log for the rest\n"));
		}
		return bHandled;
	}

	AActor* FindActorInWorld(UWorld* World, const FString& Query)
	{
		if (!World || Query.IsEmpty()) { return nullptr; }
		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* A = *It;
			if (!A || !IsValid(A)) { continue; }
			// Path OR name OR label — all three, always. delete_level_actor historically matched only
			// name/label, so a path copied straight out of list_level_actors could not be deleted.
			if (A->GetPathName() == Query || A->GetName() == Query || A->GetActorLabel() == Query)
			{
				return A;
			}
		}
		return nullptr;
	}

	const TCHAR* JsonTypeName(EJson Type)
	{
		switch (Type)
		{
		case EJson::Null:    return TEXT("null");
		case EJson::String:  return TEXT("string");
		case EJson::Number:  return TEXT("number");
		// "boolean", not "bool": the two former copies disagreed here, so set_material_parameter and
		// set_property refused the same JSON type with two different words in text a caller parses.
		case EJson::Boolean: return TEXT("boolean");
		case EJson::Array:   return TEXT("array");
		case EJson::Object:  return TEXT("object");
		default:             return TEXT("none");
		}
	}

	FString NormalizeBoolLiteral(const FString& In)
	{
		const FString T = In.TrimStartAndEnd();
		if (T.Equals(TEXT("true"),  ESearchCase::IgnoreCase)) { return TEXT("True"); }
		if (T.Equals(TEXT("false"), ESearchCase::IgnoreCase)) { return TEXT("False"); }
		return In;
	}

	TSharedRef<FJsonObject> Vec3(double X, double Y, double Z)
	{
		TSharedRef<FJsonObject> V = MakeShared<FJsonObject>();
		V->SetNumberField(TEXT("x"), X);
		V->SetNumberField(TEXT("y"), Y);
		V->SetNumberField(TEXT("z"), Z);
		return V;
	}

	TSharedRef<FJsonObject> Vec3(const FVector& V)
	{
		return Vec3(V.X, V.Y, V.Z);
	}

	// =========================================================================================
	// Element-level property addressing (Batch N, R1 gap G1).
	//
	// The walker used to refuse a container anywhere but the last segment, and had no grammar for
	// addressing one ROW of one. Its own error text was actively misleading -
	// "OverrideMaterials[0] not found" for a property that plainly exists. The grammar is documented
	// on ResolvePropertyPathEx in MifBridgeHandlers.h; everything below is its implementation, and it
	// is the ONLY implementation (ResolvePropertyPath and ResolvePropertyPathChain forward here).
	// =========================================================================================

	namespace
	{
		enum class EMifPathAccessorSyntax : uint8 { Bracket, Brace };

		struct FMifPathAccessor
		{
			EMifPathAccessorSyntax Syntax = EMifPathAccessorSyntax::Bracket;
			FString Text;
		};

		// Whole-string integer, deliberately not FCString::Atoi: Atoi returns 0 for "abc" and for
		// "0abc" alike and cannot report which, which is the PM-006 shape in miniature.
		bool MifParseWholeInt(const FString& In, int32& OutValue)
		{
			const FString T = In.TrimStartAndEnd();
			if (T.IsEmpty()) { return false; }
			int32 Start = 0;
			if (T[0] == TEXT('-') || T[0] == TEXT('+')) { Start = 1; }
			if (T.Len() == Start) { return false; }
			for (int32 i = Start; i < T.Len(); ++i)
			{
				if (!FChar::IsDigit(T[i])) { return false; }
			}
			OutValue = FCString::Atoi(*T);
			return true;
		}

		// Split on '.' at BRACKET DEPTH ZERO only. A map key or a member-find value may legitimately
		// contain a dot (ScalarParameterValues[ParameterInfo.Name=Roughness]), so a plain
		// ParseIntoArray on '.' would cut the path in the wrong place.
		bool MifSplitPathSegments(const FString& Path, TArray<FString>& OutSegs, FString& OutError)
		{
			OutSegs.Reset();
			int32 Depth = 0;
			FString Cur;
			for (int32 i = 0; i < Path.Len(); ++i)
			{
				const TCHAR Ch = Path[i];
				if (Ch == TEXT('[') || Ch == TEXT('{')) { ++Depth; }
				else if (Ch == TEXT(']') || Ch == TEXT('}'))
				{
					--Depth;
					if (Depth < 0)
					{
						OutError = FString::Printf(TEXT("unbalanced '%c' in property path '%s'"), Ch, *Path);
						return false;
					}
				}
				if (Ch == TEXT('.') && Depth == 0)
				{
					if (!Cur.IsEmpty()) { OutSegs.Add(Cur); Cur.Reset(); }
					continue;
				}
				Cur.AppendChar(Ch);
			}
			if (Depth != 0)
			{
				OutError = FString::Printf(TEXT("unterminated '[' or '{' in property path '%s'"), *Path);
				return false;
			}
			if (!Cur.IsEmpty()) { OutSegs.Add(Cur); }
			return true;
		}

		// "Keys[2]" -> name "Keys" + one Bracket accessor "2".
		bool MifParseSegment(const FString& Seg, FString& OutName, TArray<FMifPathAccessor>& OutAccessors, FString& OutError)
		{
			OutAccessors.Reset();
			int32 i = 0;
			while (i < Seg.Len() && Seg[i] != TEXT('[') && Seg[i] != TEXT('{')) { ++i; }
			OutName = Seg.Left(i).TrimStartAndEnd();
			if (OutName.IsEmpty())
			{
				OutError = FString::Printf(TEXT("property path segment '%s' has no property name before its accessor"), *Seg);
				return false;
			}
			while (i < Seg.Len())
			{
				const TCHAR Open = Seg[i];
				const TCHAR Close = (Open == TEXT('[')) ? TEXT(']') : TEXT('}');
				int32 Depth = 0;
				int32 j = i;
				for (; j < Seg.Len(); ++j)
				{
					if (Seg[j] == TEXT('[') || Seg[j] == TEXT('{')) { ++Depth; }
					else if (Seg[j] == TEXT(']') || Seg[j] == TEXT('}'))
					{
						--Depth;
						if (Depth == 0) { break; }
					}
				}
				if (j >= Seg.Len() || Seg[j] != Close)
				{
					OutError = FString::Printf(TEXT("unterminated '%c' in property path segment '%s'"), Open, *Seg);
					return false;
				}
				FMifPathAccessor Acc;
				Acc.Syntax = (Open == TEXT('[')) ? EMifPathAccessorSyntax::Bracket : EMifPathAccessorSyntax::Brace;
				Acc.Text = Seg.Mid(i + 1, j - i - 1).TrimStartAndEnd();
				OutAccessors.Add(Acc);
				i = j + 1;
			}
			return true;
		}

		// A dot path of STRUCT members only, used by the [Member=Value] linear find. No accessors and
		// no object hops: the find runs once per element and must stay cheap and predictable.
		bool MifResolveStructMemberPath(FProperty* ElemProp, void* ElemAddr, const FString& MemberPath,
			FProperty*& OutProp, void*& OutAddr, FString& OutError)
		{
			FStructProperty* SP = CastField<FStructProperty>(ElemProp);
			if (!SP)
			{
				OutError = FString::Printf(TEXT("[Member=Value] needs an array of STRUCTs; the element type here is %s"),
					*ElemProp->GetCPPType());
				return false;
			}
			TArray<FString> Parts;
			MemberPath.ParseIntoArray(Parts, TEXT("."), true);
			UStruct* CurStruct = SP->Struct;
			void*    Cur = ElemAddr;
			for (int32 i = 0; i < Parts.Num(); ++i)
			{
				FProperty* Prop = CurStruct ? CurStruct->FindPropertyByName(FName(*Parts[i])) : nullptr;
				if (!Prop)
				{
					TArray<FString> Available;
					if (CurStruct) { for (TFieldIterator<FProperty> It(CurStruct); It; ++It) { Available.Add(It->GetName()); } }
					OutError = FString::Printf(TEXT("[Member=Value]: '%s' is not a member of '%s'%s"),
						*Parts[i], CurStruct ? *CurStruct->GetName() : TEXT("<null>"),
						*NearMissSuggestion(Available, Parts[i]));
					return false;
				}
				void* Addr = Prop->ContainerPtrToValuePtr<void>(Cur);
				if (i == Parts.Num() - 1) { OutProp = Prop; OutAddr = Addr; return true; }
				FStructProperty* Next = CastField<FStructProperty>(Prop);
				if (!Next)
				{
					OutError = FString::Printf(TEXT("[Member=Value]: cannot descend through '%s' (%s) - only struct members are walkable inside the find"),
						*Parts[i], *Prop->GetClass()->GetName());
					return false;
				}
				CurStruct = Next->Struct;
				Cur = Addr;
			}
			OutError = TEXT("[Member=Value]: empty member path");
			return false;
		}

	}

	FString ExportPropertyTextForMatch(const FProperty* Prop, const void* Addr, UObject* Owner)
	{
		FString S;
		if (Prop && Addr) { Prop->ExportTextItem_Direct(S, Addr, nullptr, Owner, PPF_None); }
		S.TrimStartAndEndInline();
		if (S.Len() >= 2 && S.StartsWith(TEXT("\"")) && S.EndsWith(TEXT("\"")))
		{
			S = S.Mid(1, S.Len() - 2);
		}
		return S;
	}

	int32 FindMapEntryByKeyText(const FMapProperty* MapProp, const void* MapAddr, const FString& KeyText, UObject* Owner)
	{
		if (!MapProp || !MapAddr) { return INDEX_NONE; }
		FScriptMapHelper Helper(MapProp, MapAddr);
		for (int32 i = 0; i < Helper.GetMaxIndex(); ++i)
		{
			if (!Helper.IsValidIndex(i)) { continue; }
			if (ExportPropertyTextForMatch(MapProp->KeyProp, Helper.GetKeyPtr(i), Owner).Equals(KeyText, ESearchCase::IgnoreCase))
			{
				return i;
			}
		}
		return INDEX_NONE;
	}

	FString SampleMapKeyText(const FMapProperty* MapProp, const void* MapAddr, UObject* Owner, int32 Max)
	{
		if (!MapProp || !MapAddr) { return TEXT("(no map)"); }
		FScriptMapHelper Helper(MapProp, MapAddr);
		TArray<FString> Keys;
		for (int32 i = 0; i < Helper.GetMaxIndex() && Keys.Num() < Max; ++i)
		{
			if (!Helper.IsValidIndex(i)) { continue; }
			Keys.Add(ExportPropertyTextForMatch(MapProp->KeyProp, Helper.GetKeyPtr(i), Owner));
		}
		if (Keys.Num() == 0) { return TEXT("(the map is empty)"); }
		FString S = FString::Join(Keys, TEXT(", "));
		if (Helper.Num() > Keys.Num()) { S += FString::Printf(TEXT(", ... (%d total)"), Helper.Num()); }
		return S;
	}

	bool IsCookedOrContainerPackage(const UPackage* Package)
	{
		if (!Package)
		{
			return false;
		}
		if (Package->HasAnyPackageFlags(PKG_Cooked) || Package->bIsCookedForEditor)
		{
			return true;
		}
		const FPackagePath Path = FPackagePath::FromPackageNameUnchecked(Package->GetFName());
		return FPackageName::DoesPackageExistEx(Path, FPackageName::EPackageLocationFilter::FileSystem)
				== FPackageName::EPackageLocationFilter::None
			&& FPackageName::DoesPackageExistEx(Path, FPackageName::EPackageLocationFilter::IoDispatcher)
				!= FPackageName::EPackageLocationFilter::None;
	}

	bool ResolvePropertyPathEx(UObject* Object, const FString& Path,
		FPropertyPathResolution& Out, FString& OutError)
	{
		Out = FPropertyPathResolution();
		if (!Object) { OutError = TEXT("null target object"); return false; }

		TArray<FString> Segs;
		if (!MifSplitPathSegments(Path, Segs, OutError)) { return false; }
		if (Segs.Num() == 0) { OutError = TEXT("property path is empty (expected a dot path, e.g. Font.Size)"); return false; }

		UStruct* CurStruct = Object->GetClass();   // container TYPE
		void*    Container = Object;               // container BASE ptr (UObject* at top level)
		UObject* LeafOwner = Object;               // object PostEditChange fires on

		for (int32 i = 0; i < Segs.Num(); ++i)
		{
			FString SegName;
			TArray<FMifPathAccessor> Accessors;
			if (!MifParseSegment(Segs[i], SegName, Accessors, OutError)) { return false; }

			FProperty* Prop = CurStruct->FindPropertyByName(FName(*SegName));   // direct members only
			if (!Prop)
			{
				// Near misses matter more here than anywhere else in the plugin: the caller is driving
				// the Details panel by name and cannot see the field list, and a UPROPERTY's reflected
				// name often differs from its label ("bHidden" vs "Hidden").
				TArray<FString> Available;
				for (TFieldIterator<FProperty> It(CurStruct); It; ++It) { Available.Add(It->GetName()); }
				OutError = FString::Printf(TEXT("property '%s' not found on '%s'%s - list_object_properties dumps what exists"),
					*SegName, *CurStruct->GetName(), *NearMissSuggestion(Available, SegName));
				return false;
			}

			// VALUE address, not container - Import/ExportText_Direct require this.
			FProperty* EffProp = Prop;
			void*      EffAddr = Prop->ContainerPtrToValuePtr<void>(Container);
			bool       bSegIsElement = false;
			int32      SegCArrayIndex = 0;
			FProperty* SegContainerProp = nullptr;
			void*      SegContainerAddr = nullptr;
			int32      SegElementIndex = INDEX_NONE;
			FString    SegOrdering;
			FString    SegLabel = SegName;

			for (const FMifPathAccessor& Acc : Accessors)
			{
				const bool bBrace = (Acc.Syntax == EMifPathAccessorSyntax::Brace);

				// Fixed-size C-array UPROPERTY (FRichCurve FloatCurves[3]) - NOT a TArray, and the
				// reason UCurveVector::FloatCurves and UBlendSpace::BlendParameters were unreachable
				// for a different cause than the roadmap assumed. Tested BEFORE the dynamic containers
				// because it is a property of the DECLARATION, not of the property class.
				if (!bSegIsElement && EffProp->ArrayDim > 1)
				{
					int32 Index = 0;
					if (bBrace || !MifParseWholeInt(Acc.Text, Index))
					{
						OutError = FString::Printf(
							TEXT("'%s' is a fixed-size C-array UPROPERTY (%s[%d]); address it with [N] where N is an integer 0..%d"),
							*SegLabel, *EffProp->GetCPPType(), EffProp->ArrayDim, EffProp->ArrayDim - 1);
						return false;
					}
					if (Index < 0 || Index >= EffProp->ArrayDim)
					{
						OutError = FString::Printf(
							TEXT("'%s[%d]': index %d is out of range - '%s' is a fixed-size C-array of %d elements (valid 0..%d). ")
							TEXT("Its size is part of the C++ declaration and cannot be changed."),
							*SegLabel, Index, Index, *SegLabel, EffProp->ArrayDim, EffProp->ArrayDim - 1);
						return false;
					}
					SegContainerAddr = EffAddr;
					EffAddr = (uint8*)EffAddr + (SIZE_T)Index * EffProp->ElementSize;
					SegCArrayIndex   = Index;
					SegElementIndex  = Index;
					SegContainerProp = EffProp;
					bSegIsElement    = true;
					SegLabel = FString::Printf(TEXT("%s[%d]"), *SegLabel, Index);
					continue;
				}

				if (FArrayProperty* AP = CastField<FArrayProperty>(EffProp))
				{
					if (bBrace)
					{
						OutError = FString::Printf(
							TEXT("'%s' is a TArray<%s>; use [N] for an index or [Member=Value] for a linear find - {..} addresses a TMap key"),
							*SegLabel, *AP->Inner->GetCPPType());
						return false;
					}
					void* ArrayAddr = EffAddr;   // kept: edit_container and the set/array helpers need the
					                             // CONTAINER's address, not the element's
					FScriptArrayHelper Helper(AP, EffAddr);
					int32 Index = 0;
					if (MifParseWholeInt(Acc.Text, Index))
					{
						if (!Helper.IsValidIndex(Index))
						{
							OutError = FString::Printf(
								TEXT("'%s[%d]': index %d is out of range - the array has %d element%s%s. ")
								TEXT("Use edit_container {operation:\"add\"} to grow it."),
								*SegLabel, Index, Index, Helper.Num(), Helper.Num() == 1 ? TEXT("") : TEXT("s"),
								Helper.Num() > 0 ? *FString::Printf(TEXT(" (valid 0..%d)"), Helper.Num() - 1) : TEXT(""));
							return false;
						}
						EffAddr = Helper.GetElementPtr(Index);
						SegElementIndex = Index;
					}
					else
					{
						int32 EqAt = INDEX_NONE;
						Acc.Text.FindChar(TEXT('='), EqAt);
						if (EqAt == INDEX_NONE)
						{
							OutError = FString::Printf(
								TEXT("'%s[%s]': an array accessor is either an integer index or Member=Value (a linear find on the element's member). '%s' is neither."),
								*SegLabel, *Acc.Text, *Acc.Text);
							return false;
						}
						const FString MemberPath = Acc.Text.Left(EqAt).TrimStartAndEnd();
						const FString Wanted     = Acc.Text.Mid(EqAt + 1).TrimStartAndEnd();
						int32 Found = INDEX_NONE;
						FString FindError;
						for (int32 e = 0; e < Helper.Num(); ++e)
						{
							FProperty* MemberProp = nullptr; void* MemberAddr = nullptr;
							if (!MifResolveStructMemberPath(AP->Inner, Helper.GetElementPtr(e), MemberPath, MemberProp, MemberAddr, FindError))
							{
								OutError = FString::Printf(TEXT("'%s[%s]': %s"), *SegLabel, *Acc.Text, *FindError);
								return false;
							}
							if (ExportPropertyTextForMatch(MemberProp, MemberAddr, LeafOwner).Equals(Wanted, ESearchCase::IgnoreCase))
							{
								Found = e;
								break;
							}
						}
						if (Found == INDEX_NONE)
						{
							OutError = FString::Printf(
								TEXT("'%s[%s]': no element has %s == '%s' (searched %d element%s). ")
								TEXT("The match is a case-insensitive compare of the member's export text; the index form '%s[N]' always works."),
								*SegLabel, *Acc.Text, *MemberPath, *Wanted, Helper.Num(),
								Helper.Num() == 1 ? TEXT("") : TEXT("s"), *SegName);
							return false;
						}
						EffAddr = Helper.GetElementPtr(Found);
						SegElementIndex = Found;
					}
					SegContainerProp = AP;
					SegContainerAddr = ArrayAddr;
					EffProp = AP->Inner;
					bSegIsElement = true;
					SegLabel = FString::Printf(TEXT("%s[%s]"), *SegLabel, *Acc.Text);
					continue;
				}

				if (FSetProperty* SP = CastField<FSetProperty>(EffProp))
				{
					int32 Index = 0;
					if (bBrace || !MifParseWholeInt(Acc.Text, Index))
					{
						OutError = FString::Printf(
							TEXT("'%s' is a TSet<%s>; address an element by its POSITION in iteration order, [N]. A set has no keys, so {..} does not apply."),
							*SegLabel, *SP->ElementProp->GetCPPType());
						return false;
					}
					void* SetAddr = EffAddr;
					FScriptSetHelper Helper(SP, EffAddr);
					if (Index < 0 || Index >= Helper.Num())
					{
						OutError = FString::Printf(
							TEXT("'%s[%d]': index %d is out of range - the set has %d element%s%s."),
							*SegLabel, Index, Index, Helper.Num(), Helper.Num() == 1 ? TEXT("") : TEXT("s"),
							Helper.Num() > 0 ? *FString::Printf(TEXT(" (valid 0..%d)"), Helper.Num() - 1) : TEXT(""));
						return false;
					}
					// Sparse-aware: FindNthElementPtr skips the holes a TSet leaves behind, so [N] is
					// the Nth element in ITERATION order, not the Nth internal slot. That order is not
					// stable across a rehash, which is why the resolution reports it.
					EffAddr = Helper.FindNthElementPtr(Index);
					if (!EffAddr)
					{
						OutError = FString::Printf(TEXT("'%s[%d]': the set reported %d elements but element %d could not be addressed"),
							*SegLabel, Index, Helper.Num(), Index);
						return false;
					}
					SegContainerProp = SP;
					SegContainerAddr = SetAddr;
					SegElementIndex  = Index;
					SegOrdering      = TEXT("iteration");
					EffProp = SP->ElementProp;
					bSegIsElement = true;
					SegLabel = FString::Printf(TEXT("%s[%d]"), *SegLabel, Index);
					continue;
				}

				if (FMapProperty* MP = CastField<FMapProperty>(EffProp))
				{
					// [K] and {K} both mean the KEY on a map - there is no positional access, because
					// a map's internal order is an implementation detail the caller cannot see. The
					// lookup is a compare of exported key text rather than a hash probe, so a key type
					// without GetTypeHash is READABLE here even though edit_container must refuse to
					// add to it.
					void* MapAddr = EffAddr;
					FScriptMapHelper Helper(MP, EffAddr);
					const int32 FoundIndex = FindMapEntryByKeyText(MP, EffAddr, Acc.Text, LeafOwner);
					if (FoundIndex == INDEX_NONE)
					{
						OutError = FString::Printf(
							TEXT("'%s{%s}': no entry with that key in the TMap<%s,%s> (%d entries). Existing keys: %s. ")
							TEXT("Use edit_container {operation:\"add\", key:..., value:...} to create it."),
							*SegLabel, *Acc.Text, *MP->KeyProp->GetCPPType(), *MP->ValueProp->GetCPPType(),
							Helper.Num(), *SampleMapKeyText(MP, EffAddr, LeafOwner, 12));
						return false;
					}
					EffAddr = Helper.GetValuePtr(FoundIndex);
					SegContainerProp = MP;
					SegContainerAddr = MapAddr;
					SegElementIndex  = FoundIndex;
					SegOrdering      = TEXT("iteration");
					EffProp = MP->ValueProp;
					bSegIsElement = true;
					SegLabel = FString::Printf(TEXT("%s{%s}"), *SegLabel, *Acc.Text);
					continue;
				}

				OutError = FString::Printf(
					TEXT("'%s' is a %s (%s), not a container - [] and {} address elements of TArray/TSet/TMap or of a fixed-size C-array UPROPERTY only"),
					*SegLabel, *EffProp->GetClass()->GetName(), *EffProp->GetCPPType());
				return false;
			}

			if (i == Segs.Num() - 1)
			{
				Out.Leaf      = EffProp;
				Out.LeafAddr  = EffAddr;
				Out.LeafOwner = LeafOwner;
				Out.Chain.Add(Prop);
				Out.bLeafIsElement       = bSegIsElement;
				Out.LeafCArrayIndex      = SegCArrayIndex;
				Out.ElementContainerProp = SegContainerProp;
				Out.ElementContainerAddr = SegContainerAddr;
				Out.ElementIndex         = SegElementIndex;
				Out.ElementOrdering      = SegOrdering;
				Out.ElementDescription   = bSegIsElement ? SegLabel : FString();
				if (EffProp == Prop)
				{
					// The leaf is still a DECLARED member of CurStruct - either a plain member, or an
					// element of a C-array member, whose FProperty is the member itself. Only then is a
					// SIBLING lookup (EditCondition's companion bool) well defined; an element of a
					// dynamic container has no declaring container and must leave this null.
					Out.LeafContainerAddr   = Container;
					Out.LeafContainerStruct = CurStruct;
				}
				return true;
			}

			if (FStructProperty* SP = CastField<FStructProperty>(EffProp))
			{
				// Descend the struct in place - same memory, new struct type. The struct member stays
				// in the chain: it is the HEAD the Details panel reports as MemberProperty, and
				// AActor::PostEditChangeProperty switches on MemberProperty (ActorEditor.cpp:134-135).
				Out.Chain.Add(Prop);
				CurStruct = SP->Struct;
				Container = EffAddr;
			}
			else if (FObjectPropertyBase* OP = CastField<FObjectPropertyBase>(EffProp))
			{
				// Cross an object boundary - read the inner UObject, continue on its class with it as
				// the new container AND the new edit owner (the notification must fire on the object
				// that actually holds the leaf).
				UObject* Inner = OP->GetObjectPropertyValue(EffAddr);
				if (!Inner)
				{
					OutError = FString::Printf(
						TEXT("cannot descend through '%s': it is an object reference and it is currently null. ")
						TEXT("Set '%s' to a valid object first, or address the leaf on the object that owns it."),
						*SegLabel, *SegLabel);
					return false;
				}
				CurStruct = Inner->GetClass();
				Container = Inner;
				LeafOwner = Inner;
				// The chain must be relative to the object the notification fires on, so crossing an
				// object boundary RESTARTS it. Keeping the outer segments would build a chain whose
				// head is not a member of LeafOwner's class, and PropagatePostEditChange check()s the
				// active member node (Obj.cpp:660) - a half-built chain asserts rather than degrades.
				Out.Chain.Reset();
			}
			else
			{
				OutError = FString::Printf(
					TEXT("segment '%s' is a %s and is not walkable mid-path on its own. Containers ARE walkable now, but only through an ")
					TEXT("accessor: '%s[0]' for a TArray/TSet element, '%s{Key}' for a TMap value, '%s[Member=Value]' for a linear find."),
					*SegLabel, *EffProp->GetClass()->GetName(), *SegName, *SegName, *SegName);
				return false;
			}
		}
		OutError = TEXT("path traversal fell through");
		return false;
	}

	bool ResolvePropertyPath(UObject* Object, const FString& Path,
		FProperty*& OutLeaf, void*& OutLeafAddr, UObject*& OutLeafOwner, FString& OutError)
	{
		// Forwards: ONE walker. Callers that do not need the chain simply discard it.
		TArray<FProperty*> Ignored;
		return ResolvePropertyPathChain(Object, Path, OutLeaf, OutLeafAddr, OutLeafOwner, Ignored, OutError);
	}

	bool ResolvePropertyPathChain(UObject* Object, const FString& Path,
		FProperty*& OutLeaf, void*& OutLeafAddr, UObject*& OutLeafOwner,
		TArray<FProperty*>& OutChain, FString& OutError)
	{
		FPropertyPathResolution Res;
		const bool bOk = ResolvePropertyPathEx(Object, Path, Res, OutError);
		OutLeaf      = bOk ? Res.Leaf      : nullptr;
		OutLeafAddr  = bOk ? Res.LeafAddr  : nullptr;
		OutLeafOwner = bOk ? Res.LeafOwner : nullptr;
		OutChain.Reset();
		if (bOk) { OutChain = Res.Chain; }
		return bOk;
	}

	// =========================================================================================
	// Component ORIGIN enumeration (Batch N, R3 section 4.1).
	//
	// Batch J shipped the WRITE path for inherited components and no way to discover their names:
	// get_inherited_component resolves ONE component BY NAME, and list_components walked the child's
	// own SCS only, so an agent editing a child Blueprint saw a near-empty list and had no name to
	// pass. This is the enumerator both sides now share.
	// =========================================================================================

	const TCHAR* const kComponentOriginOwnSCS    = TEXT("ownSCS");
	const TCHAR* const kComponentOriginParentSCS = TEXT("parentBlueprintSCS");
	const TCHAR* const kComponentOriginNative    = TEXT("native");
	const TCHAR* const kComponentOriginNotFound  = TEXT("notFound");

	UActorComponent* FindNativeComponentOnCDO(UBlueprint* Blueprint, const FName Name, FString& OutMatchedBy)
	{
		// Two lookups, in the order a caller is likely to have typed the name:
		//   1. the PROPERTY name the Details panel / describe_class show ("Mesh"),
		//   2. the SUBOBJECT name that actually appears in the object path ("CharacterMesh0").
		// The outer test is what makes this "native": a component created by a C++ constructor is a
		// default subobject of the CDO, whereas an SCS component's CDO property is null until the
		// construction script runs (verified live: on a Character-derived child, `Mesh` reports a
		// subobject path while inherited SCS components report None).
		if (!Blueprint) { return nullptr; }
		UClass* GenClass = Blueprint->GeneratedClass;
		if (!GenClass) { return nullptr; }
		UObject* CDO = GenClass->GetDefaultObject(/*bCreateIfNeeded*/ true);
		if (!CDO) { return nullptr; }

		if (FObjectPropertyBase* ObjectProp = CastField<FObjectPropertyBase>(GenClass->FindPropertyByName(Name)))
		{
			UObject* Value = ObjectProp->GetObjectPropertyValue(ObjectProp->ContainerPtrToValuePtr<void>(CDO));
			UActorComponent* Comp = Cast<UActorComponent>(Value);
			if (Comp && Comp->GetOuter() == CDO)
			{
				OutMatchedBy = TEXT("property");
				return Comp;
			}
		}

		if (UObject* Sub = CDO->GetDefaultSubobjectByName(Name))
		{
			if (UActorComponent* Comp = Cast<UActorComponent>(Sub))
			{
				OutMatchedBy = TEXT("subobject");
				return Comp;
			}
		}
		return nullptr;
	}

	FString ComponentCreationMethodString(const UActorComponent* Component)
	{
		if (!Component) { return TEXT("(none)"); }
		switch (Component->CreationMethod)
		{
		case EComponentCreationMethod::Native:                  return TEXT("Native");
		case EComponentCreationMethod::SimpleConstructionScript:return TEXT("SimpleConstructionScript");
		case EComponentCreationMethod::UserConstructionScript:  return TEXT("UserConstructionScript");
		case EComponentCreationMethod::Instance:                return TEXT("Instance");
		default:                                                return TEXT("Unknown");
		}
	}

	namespace
	{
		// The highest ancestor class whose CDO still carries a default subobject of this name - i.e.
		// the class that DECLARES the native component. GetDefaultObject(false) so asking the question
		// never constructs a CDO that did not already exist.
		UClass* MifDeclaringClassOfNativeSubobject(UClass* StartClass, const FName SubobjectName)
		{
			UClass* Best = nullptr;
			for (UClass* C = StartClass; C != nullptr; C = C->GetSuperClass())
			{
				UObject* CDO = C->GetDefaultObject(/*bCreateIfNeeded*/ false);
				if (CDO && CDO->GetDefaultSubobjectByName(SubobjectName)) { Best = C; }
			}
			return Best;
		}
	}

	void EnumerateBlueprintComponents(UBlueprint* Blueprint, TArray<FComponentOriginRow>& OutRows, int32 Cap)
	{
		OutRows.Reset();
		if (!Blueprint) { return; }
		TSet<FName> Seen;
		auto HasRoom = [&OutRows, Cap]() { return Cap <= 0 || OutRows.Num() < Cap; };

		// 1. This blueprint's OWN SCS.
		if (USimpleConstructionScript* OwnSCS = Blueprint->SimpleConstructionScript)
		{
			const TArray<USCS_Node*>& Roots = OwnSCS->GetRootNodes();
			for (USCS_Node* Node : OwnSCS->GetAllNodes())
			{
				if (!Node || !HasRoom()) { continue; }
				const FName N = Node->GetVariableName();
				if (N == NAME_None || Seen.Contains(N)) { continue; }
				Seen.Add(N);
				FComponentOriginRow Row;
				Row.Name             = N;
				Row.Origin           = kComponentOriginOwnSCS;
				Row.ComponentClass   = Node->ComponentClass;
				Row.OwningClass      = Blueprint->GeneratedClass;
				Row.Node             = Node;
				Row.AttachParentNode = OwnSCS->FindParentNode(Node);
				Row.Template         = Node->ComponentTemplate;
				Row.bIsRoot          = Roots.Contains(Node);
				Row.AttachSocket     = Node->AttachToName;
				Row.CannotOverrideReason = TEXT("declared in THIS blueprint's own SimpleConstructionScript - it is not inherited, so there is nothing to delta against");
				OutRows.Add(Row);
			}
		}

		// 2. Every PARENT BLUEPRINT's SCS, up the UBlueprintGeneratedClass chain. Starting at
		//    ParentClass (not GeneratedClass) is what keeps the child's own SCS out of the search;
		//    each level still carries its SimpleConstructionScript even when the class is COOKED and
		//    has no UBlueprint asset behind it - the common case in this project, where mod blueprints
		//    derive from cooked game blueprints.
		//
		//    bCreateIfNecessary=FALSE, deliberately: merely LISTING components must never mint an
		//    InheritableComponentHandler on the asset. The engine's accessor is get-or-CREATE, which
		//    is the whole reason get_inherited_component exists as a separate read verb, and it is
		//    what lets list_components stay in IsReadOnlyEndpoint.
		UInheritableComponentHandler* ICH = Blueprint->GetInheritableComponentHandler(/*bCreateIfNecessary*/ false);
		for (UBlueprintGeneratedClass* BPGC = Cast<UBlueprintGeneratedClass>(Blueprint->ParentClass);
			 BPGC != nullptr;
			 BPGC = Cast<UBlueprintGeneratedClass>(BPGC->GetSuperClass()))
		{
			USimpleConstructionScript* ParentSCS = BPGC->SimpleConstructionScript;
			if (!ParentSCS) { continue; }
			const TArray<USCS_Node*>& ParentRoots = ParentSCS->GetRootNodes();
			for (USCS_Node* Node : ParentSCS->GetAllNodes())
			{
				if (!Node || !HasRoom()) { continue; }
				const FName N = Node->GetVariableName();
				if (N == NAME_None || Seen.Contains(N)) { continue; }
				Seen.Add(N);
				FComponentOriginRow Row;
				Row.Name           = N;
				Row.Origin         = kComponentOriginParentSCS;
				Row.ComponentClass = Node->ComponentClass;
				Row.OwningClass    = BPGC;
				Row.Node           = Node;
				Row.Template       = Node->ComponentTemplate;
				Row.AttachSocket   = Node->AttachToName;
				Row.bIsRoot        = ParentRoots.Contains(Node);
				if (Row.Template) { Row.bEditableWhenInherited = Row.Template->IsEditableWhenInherited(); }

				// The editor's own guard, verbatim (SubobjectData.cpp:152-155).
				const FComponentKey Key(Node);
				const bool bKeyOk   = Key.IsValid();
				const bool bChildOk = Blueprint->ParentClass && Blueprint->ParentClass->IsChildOf(Key.GetComponentOwner());
				// canOverride means exactly one thing: "override_inherited_component will accept this".
				// It is therefore the WRITE path's own two guards and nothing else.
				// bEditableWhenInherited is reported SEPARATELY rather than folded in - it is an extra
				// editor-side fact (the Details panel greys the row) that the bridge's write path does
				// not test, and folding it in would make a read predict a refusal that never happens.
				Row.bCanOverride = bKeyOk && bChildOk;
				if (!bKeyOk)
				{
					Row.CannotOverrideReason = TEXT("component key invalid (the parent SCS node has no valid VariableGuid)");
				}
				else if (!bChildOk)
				{
					Row.CannotOverrideReason = TEXT("this blueprint's ParentClass is not a child of the class that owns the component");
				}
				if (ICH && bKeyOk && bChildOk)
				{
					Row.OverrideTemplate = ICH->GetOverridenComponentTemplate(Key);
				}
				OutRows.Add(Row);
			}
		}

		// 3. NATIVE components on the generated class's CDO. Reported under the PROPERTY name a caller
		//    would type ("Mesh"), with the REAL subobject name carried separately - they differ
		//    (Mesh -> CharacterMesh0, CharacterMovement -> CharMoveComp, CapsuleComponent ->
		//    CollisionCylinder), and the path a caller needs is resolved FROM THE OBJECT, never
		//    composed from the name that was passed in.
		if (UClass* GenClass = Blueprint->GeneratedClass)
		{
			if (UObject* CDO = GenClass->GetDefaultObject(/*bCreateIfNeeded*/ true))
			{
				TMap<UActorComponent*, FProperty*> PropertyForComponent;
				for (TFieldIterator<FObjectPropertyBase> It(GenClass); It; ++It)
				{
					FObjectPropertyBase* OP = *It;
					if (!OP) { continue; }
					UObject* Value = OP->GetObjectPropertyValue(OP->ContainerPtrToValuePtr<void>(CDO));
					UActorComponent* Comp = Cast<UActorComponent>(Value);
					if (Comp && Comp->GetOuter() == CDO && !PropertyForComponent.Contains(Comp))
					{
						PropertyForComponent.Add(Comp, OP);
					}
				}
				const AActor* ActorCDO = Cast<AActor>(CDO);
				TArray<UObject*> Subobjects;
				CDO->GetDefaultSubobjects(Subobjects);
				for (UObject* Sub : Subobjects)
				{
					UActorComponent* Comp = Cast<UActorComponent>(Sub);
					if (!Comp || !HasRoom()) { continue; }
					FProperty** Found = PropertyForComponent.Find(Comp);
					const FName RowName = Found ? (*Found)->GetFName() : Comp->GetFName();
					if (RowName == NAME_None || Seen.Contains(RowName)) { continue; }
					Seen.Add(RowName);
					FComponentOriginRow Row;
					Row.Name           = RowName;
					Row.Origin         = kComponentOriginNative;
					Row.ComponentClass = Comp->GetClass();
					Row.Template       = Comp;
					Row.SubobjectName  = Comp->GetFName();
					Row.OwningClass    = Found ? (*Found)->GetOwnerClass()
					                           : MifDeclaringClassOfNativeSubobject(GenClass, Comp->GetFName());
					Row.bIsRoot        = ActorCDO && ActorCDO->GetRootComponent() == Comp;
					Row.CannotOverrideReason = TEXT("native component: inherited from a C++ parent class, not from a parent Blueprint's SCS. UInheritableComponentHandler does not apply (SubobjectData.cpp:148 excludes it) and this blueprint's CDO already owns its own instance");
					OutRows.Add(Row);
				}
			}
		}
	}

	// =========================================================================================
	// Reflection target resolution + the Details-panel metadata surface (Batch N).
	// =========================================================================================

	UObject* ResolvePropertyTarget(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
		UWidgetBlueprint** OutOwningWidgetBP)
	{
		if (OutOwningWidgetBP) { *OutOwningWidgetBP = nullptr; }

		const FString ObjectPath = JStrAny(In, { TEXT("objectPath"), TEXT("actorPath") });
		const FString WidgetName = JStr(In, TEXT("widgetName"));

		if (!ObjectPath.IsEmpty())
		{
			FString P = ObjectPath; P.TrimStartAndEndInline();
			UObject* Target = StaticLoadObject(UObject::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Target && !P.Contains(TEXT(".")))
			{
				// Accept a bare package path like /Game/Foo/Bar -> /Game/Foo/Bar.Bar
				const FString Full = P + TEXT(".") + FPackageName::GetShortName(P);
				Target = StaticLoadObject(UObject::StaticClass(), nullptr, *Full, nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			if (!Target) { Fail(Out, FString::Printf(TEXT("object not found: %s"), *ObjectPath)); return nullptr; }
			return Target;
		}
		if (!WidgetName.IsEmpty())
		{
			UBlueprint* BP = ResolveBlueprintField(In, Out);   // reads blueprintId/path; writes Fail on miss
			if (!BP) { return nullptr; }
			UWidgetBlueprint* WidgetBP = Cast<UWidgetBlueprint>(BP);
			if (!WidgetBP) { Fail(Out, FString::Printf(TEXT("'%s' is not a Widget Blueprint"), *BP->GetName())); return nullptr; }
			if (!WidgetBP->WidgetTree) { Fail(Out, TEXT("widget blueprint has no WidgetTree")); return nullptr; }
			// The edit target is the UWidget TEMPLATE in the tree (an archetype).
			UObject* Target = WidgetBP->WidgetTree->FindWidget(FName(*WidgetName));
			if (!Target) { Fail(Out, FString::Printf(TEXT("widget '%s' not found in %s"), *WidgetName, *BP->GetName())); return nullptr; }
			if (OutOwningWidgetBP) { *OutOwningWidgetBP = WidgetBP; }
			return Target;
		}
		Fail(Out, TEXT("supply either objectPath (a placed actor's path IS an objectPath) or (blueprintId + widgetName)"));
		return nullptr;
	}

	void InspectEditCondition(const FProperty* Prop, const void* ContainerAddr, FEditConditionInfo& Out)
	{
		Out = FEditConditionInfo();
		if (!Prop) { return; }
#if WITH_EDITORONLY_DATA
		// FName comparison is case-insensitive, which is what makes ONE lookup find both spellings:
		// UStaticMeshComponent::MinLOD authors it as meta=(editcondition="bOverrideMinLOD") while the
		// panel reads TEXT("EditCondition") (PropertyNode.cpp:230, Field.cpp:749-757). static so the
		// name is interned once rather than per call, exactly as the engine does it.
		static const FName NAME_EditCondition(TEXT("EditCondition"));
		static const FName NAME_EditConditionHides(TEXT("EditConditionHides"));
		static const FName NAME_InlineEditConditionToggle(TEXT("InlineEditConditionToggle"));

		Out.bInlineToggle = Prop->HasMetaData(NAME_InlineEditConditionToggle);
		const FString* Cond = Prop->FindMetaData(NAME_EditCondition);
		if (!Cond || Cond->TrimStartAndEnd().IsEmpty())
		{
			Out.Kind = TEXT("none");
			Out.bMet = true;
			return;
		}
		Out.bHasMeta = true;
		Out.MetaText = *Cond;
		Out.bHides   = Prop->HasMetaData(NAME_EditConditionHides);

		FString Expr = Cond->TrimStartAndEnd();
		bool bNegated = false;
		if (Expr.StartsWith(TEXT("!")))
		{
			bNegated = true;
			Expr = Expr.RightChop(1).TrimStartAndEnd();
		}
		// A single identifier and nothing else. Anything carrying == != && || < > ( ) or a function
		// call is REPORTED, never guessed: FEditConditionParser is unexported and lives in
		// Editor/PropertyEditor/Private, and PropertyEditor is not a link dependency of this module
		// (UnrealEd.Build.cs names it only under DynamicallyLoaded/IncludePath, never as a dependency).
		bool bIdentifier = !Expr.IsEmpty();
		for (int32 i = 0; i < Expr.Len() && bIdentifier; ++i)
		{
			const TCHAR Ch = Expr[i];
			bIdentifier = FChar::IsAlpha(Ch) || Ch == TEXT('_') || (i > 0 && FChar::IsDigit(Ch));
		}
		if (!bIdentifier)
		{
			Out.Kind = TEXT("unevaluated");
			Out.bMet = true;   // UNKNOWN. Callers must branch on bEvaluated, never on bMet alone.
			Out.Note = FString::Printf(
				TEXT("EditCondition \"%s\" is not a single bool or its negation, so this bridge does not evaluate it. ")
				TEXT("Measured over Runtime/**.h: 713 of 837 gated properties are a bare or negated identifier (85.2%%); ")
				TEXT("the other 122 are this case."),
				*Out.MetaText);
			return;
		}

		Out.Kind     = bNegated ? TEXT("negatedBool") : TEXT("bool");
		Out.FlagName = Expr;
		Out.bRequiredFlagValue = !bNegated;

		// The companion bool is looked up in the GATED PROPERTY'S OWN owner struct and its supers -
		// FindFProperty uses TFieldIterator with EFieldIterationFlags::Default
		// (EditConditionContext.cpp:55). This is NOT the bOverride_ naming convention: that is only
		// FPostProcessSettings' house style, and UStaticMeshComponent::MinLOD/bOverrideMinLOD is the
		// same mechanism without the prefix.
		UStruct* OwnerStruct = Prop->GetOwnerStruct();
		Out.FlagProp = OwnerStruct ? FindFProperty<FBoolProperty>(OwnerStruct, *Out.FlagName) : nullptr;
		if (!Out.FlagProp)
		{
			Out.Kind = TEXT("unevaluated");
			Out.bMet = true;
			Out.Note = FString::Printf(
				TEXT("EditCondition names '%s' but no FBoolProperty of that name exists on '%s' or any of its supers"),
				*Out.FlagName, OwnerStruct ? *OwnerStruct->GetName() : TEXT("<no owner struct>"));
			return;
		}
		if (!ContainerAddr)
		{
			Out.Note = FString::Printf(
				TEXT("EditCondition '%s' resolved to flag '%s' but the flag could not be READ: the property was addressed as a ")
				TEXT("container element, which has no declaring container to look a sibling up in."),
				*Out.MetaText, *Out.FlagName);
			Out.bMet = true;
			return;
		}
		const bool bFlag = Out.FlagProp->GetPropertyValue(Out.FlagProp->ContainerPtrToValuePtr<void>(ContainerAddr));
		Out.bEvaluated = true;
		Out.bMet = (bFlag == Out.bRequiredFlagValue);
#endif // WITH_EDITORONLY_DATA
	}

	void InspectClamps(const FProperty* Prop, FPropertyClampInfo& Out)
	{
		Out = FPropertyClampInfo();
		if (!Prop) { return; }
		Out.bNumeric = (CastField<FNumericProperty>(Prop) != nullptr);
#if WITH_EDITORONLY_DATA
		static const FName NAME_ClampMin(TEXT("ClampMin"));
		static const FName NAME_ClampMax(TEXT("ClampMax"));
		static const FName NAME_UIMin(TEXT("UIMin"));
		static const FName NAME_UIMax(TEXT("UIMax"));
		static const FName NAME_Multiple(TEXT("Multiple"));
		static const FName NAME_ArrayClamp(TEXT("ArrayClamp"));

		if (const FString* S = Prop->FindMetaData(NAME_ClampMin))
		{
			Out.ClampMinText = *S;
			Out.bHasClampMin = ParseWholeNumber(*S, Out.ClampMin);
		}
		if (const FString* S = Prop->FindMetaData(NAME_ClampMax))
		{
			Out.ClampMaxText = *S;
			Out.bHasClampMax = ParseWholeNumber(*S, Out.ClampMax);
		}
		if (const FString* S = Prop->FindMetaData(NAME_UIMin))     { Out.UIMinText     = *S; Out.bHasUIMin = true; }
		if (const FString* S = Prop->FindMetaData(NAME_UIMax))     { Out.UIMaxText     = *S; Out.bHasUIMax = true; }
		if (const FString* S = Prop->FindMetaData(NAME_Multiple))   { Out.MultipleText   = *S; }
		if (const FString* S = Prop->FindMetaData(NAME_ArrayClamp)) { Out.ArrayClampText = *S; }
#endif // WITH_EDITORONLY_DATA
	}

	bool DescribeClampViolation(const FProperty* Prop, const FString& ValueText, FString& OutMeta, FString& OutLimit)
	{
		FPropertyClampInfo Info;
		InspectClamps(Prop, Info);
		if (!Info.bNumeric) { return false; }
		double Value = 0.0;
		if (!ParseWholeNumber(ValueText, Value)) { return false; }
		if (Info.bHasClampMin && Value < Info.ClampMin) { OutMeta = TEXT("ClampMin"); OutLimit = Info.ClampMinText; return true; }
		if (Info.bHasClampMax && Value > Info.ClampMax) { OutMeta = TEXT("ClampMax"); OutLimit = Info.ClampMaxText; return true; }
		return false;
	}

	void AddWarning(const TSharedRef<FJsonObject>& Out, const FString& Text)
	{
		TArray<TSharedPtr<FJsonValue>> Warnings;
		const TArray<TSharedPtr<FJsonValue>>* Existing = nullptr;
		if (Out->TryGetArrayField(TEXT("warnings"), Existing) && Existing)
		{
			Warnings = *Existing;
		}
		Warnings.Add(MakeShared<FJsonValueString>(Text));
		Out->SetArrayField(TEXT("warnings"), Warnings);
	}

	FString NearMissSuggestion(const TArray<FString>& Available, const FString& Wanted, int32 MaxSuggestions)
	{
		if (Wanted.IsEmpty() || Available.Num() == 0) { return FString(); }

		// Classic Levenshtein, two rolling rows. Bounded by the candidate list, which is one
		// blueprint's variable/function names — tens of entries, not thousands.
		auto Distance = [](const FString& A, const FString& B) -> int32
		{
			const int32 LenA = A.Len();
			const int32 LenB = B.Len();
			TArray<int32> Prev, Cur;
			Prev.SetNumUninitialized(LenB + 1);
			Cur.SetNumUninitialized(LenB + 1);
			for (int32 j = 0; j <= LenB; ++j) { Prev[j] = j; }
			for (int32 i = 1; i <= LenA; ++i)
			{
				Cur[0] = i;
				for (int32 j = 1; j <= LenB; ++j)
				{
					const int32 Cost = (FChar::ToLower(A[i - 1]) == FChar::ToLower(B[j - 1])) ? 0 : 1;
					Cur[j] = FMath::Min3(Cur[j - 1] + 1, Prev[j] + 1, Prev[j - 1] + Cost);
				}
				Prev = Cur;
			}
			return Prev[LenB];
		};

		// Rank 0 = same name, different case. Rank 1 = one contains the other. Rank 2 = close spelling.
		TArray<TPair<int32, FString>> Ranked;
		const int32 Tolerance = FMath::Max(1, Wanted.Len() / 3);
		for (const FString& Candidate : Available)
		{
			if (Candidate.IsEmpty()) { continue; }
			if (Candidate.Equals(Wanted, ESearchCase::CaseSensitive)) { continue; }  // not a near miss
			if (Candidate.Equals(Wanted, ESearchCase::IgnoreCase))
			{
				Ranked.Add(TPair<int32, FString>(0, Candidate));
				continue;
			}
			if (Candidate.Contains(Wanted, ESearchCase::IgnoreCase) || Wanted.Contains(Candidate, ESearchCase::IgnoreCase))
			{
				Ranked.Add(TPair<int32, FString>(1, Candidate));
				continue;
			}
			const int32 Dist = Distance(Candidate, Wanted);
			if (Dist <= Tolerance)
			{
				Ranked.Add(TPair<int32, FString>(2 + Dist, Candidate));
			}
		}
		if (Ranked.Num() == 0) { return FString(); }

		Ranked.Sort([](const TPair<int32, FString>& A, const TPair<int32, FString>& B) { return A.Key < B.Key; });

		TArray<FString> Picked;
		for (const TPair<int32, FString>& Entry : Ranked)
		{
			Picked.Add(FString::Printf(TEXT("'%s'"), *Entry.Value));
			if (Picked.Num() >= FMath::Max(1, MaxSuggestions)) { break; }
		}
		return FString::Printf(TEXT(" (did you mean %s?)"), *FString::Join(Picked, TEXT(", ")));
	}

	bool BackupPackage(UPackage* Package, FString& OutBackupPath, FString& OutError)
	{
		OutBackupPath.Reset();
		if (!Package)
		{
			OutError = TEXT("no package to back up");
			return false;
		}

		// A World is written as .umap, NOT .uasset, and GetAssetPackageExtension() is unconditional.
		// batch's inline copy hardcoded the .uasset spelling, so for a map package the file it tested
		// never existed, FileExists was false, and the branch was skipped IN SILENCE while the caller
		// had asked for a backup before a destructive run. ContainsMap() is the same test the engine's
		// own save path uses.
		const FString FileName = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			Package->ContainsMap() ? FPackageName::GetMapPackageExtension() : FPackageName::GetAssetPackageExtension());

		if (!FPaths::FileExists(FileName))
		{
			OutError = FString::Printf(
				TEXT("'%s' has never been saved to disk (%s does not exist), so there is nothing to back up — ")
				TEXT("call save_blueprint/save_package first"), *Package->GetName(), *FileName);
			return false;
		}

		const FString BackupName = FileName + TEXT(".bak");
		// IFileManager::Copy returns uint32 (FileManager.h:111). Discarding it is how a response could
		// name a .bak that was never written — a safety net that exists only in the JSON.
		if (IFileManager::Get().Copy(*BackupName, *FileName, /*bReplace*/ true, /*bEvenIfReadOnly*/ true) != COPY_OK)
		{
			OutError = FString::Printf(TEXT("failed to write backup '%s' (source '%s' exists; disk full or destination read-only?)"),
				*BackupName, *FileName);
			return false;
		}

		OutBackupPath = BackupName;
		return true;
	}

	bool ParsePinSpecs(const TSharedRef<FJsonObject>& In, const TCHAR* Field,
		TArray<TPair<FName, FEdGraphPinType>>& OutPins, FString& OutError)
	{
		const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
		if (!In->TryGetArrayField(Field, Arr) || Arr == nullptr)
		{
			return true; // absent = no pins, not an error
		}
		int32 Index = INDEX_NONE;
		for (const TSharedPtr<FJsonValue>& Value : *Arr)
		{
			++Index;
			const TSharedPtr<FJsonObject>* ObjPtr = nullptr;
			if (!Value.IsValid() || !Value->TryGetObject(ObjPtr) || ObjPtr == nullptr)
			{
				// Both former copies `continue`d here, so a malformed entry produced a function or
				// dispatcher with fewer parameters than the caller listed and nothing said which.
				OutError = FString::Printf(
					TEXT("%s[%d] is not an object — each entry must be {\"name\":..., \"type\":...}"), Field, Index);
				return false;
			}
			const TSharedRef<FJsonObject> Obj = ObjPtr->ToSharedRef();
			FString PinName = JStr(Obj, TEXT("name"));
			PinName.TrimStartAndEndInline();
			if (!IsValidIdentifier(PinName))
			{
				OutError = FString::Printf(TEXT("invalid name '%s' in %s[%d] (must match ^[A-Za-z_][A-Za-z0-9_]*$)"),
					*PinName, Field, Index);
				return false;
			}
			FEdGraphPinType PinType;
			if (!MakePinType(JStr(Obj, TEXT("type")), JStr(Obj, TEXT("container")), PinType, OutError, JStr(Obj, TEXT("valueType"))))
			{
				OutError = FString::Printf(TEXT("%s[%d] ('%s'): %s"), Field, Index, *PinName, *OutError);
				return false;
			}
			OutPins.Emplace(FName(*PinName), PinType);
		}
		return true;
	}

	// --- Exec-chain splicing + checked pin defaults --------------------------
	// See MifBridgeHandlers.h. The rule both splices implement: NOTHING is broken until EVERY
	// connection the new shape needs has been approved by the schema, and the count reported back is
	// a tally of connections that were actually made — never the size of the list we intended to move.

	// One line of a pin's identity for an error message: "Branch.then".
	static FString DescribePin(const UEdGraphPin* Pin)
	{
		if (!Pin) { return TEXT("<null pin>"); }
		const UEdGraphNode* Owner = Pin->GetOwningNodeUnchecked();
		return FString::Printf(TEXT("%s.%s"),
			Owner ? *Owner->GetName() : TEXT("<orphan>"), *Pin->PinName.ToString());
	}

	bool SpliceExecAfter(UEdGraphPin* SourceOut, UEdGraphPin* InsertIn, UEdGraphPin* InsertOut,
		int32& OutMovedTargets, FString& OutError)
	{
		OutMovedTargets = 0;
		if (!SourceOut || !InsertIn || !InsertOut)
		{
			OutError = TEXT("splice needs all three exec pins (source out, insert in, insert out)");
			return false;
		}

		const UEdGraphSchema_K2* Schema = K2();

		TArray<UEdGraphPin*> OldTargets;
		for (UEdGraphPin* Target : SourceOut->LinkedTo)
		{
			if (Target) { OldTargets.Add(Target); }
		}

		// ---- validate the WHOLE new shape before touching anything ----------------------------
		const FPinConnectionResponse HeadResponse = Schema->CanCreateConnection(SourceOut, InsertIn);
		if (HeadResponse.Response == CONNECT_RESPONSE_DISALLOW)
		{
			OutError = FString::Printf(TEXT("cannot connect %s -> %s: %s. Nothing was changed."),
				*DescribePin(SourceOut), *DescribePin(InsertIn), *HeadResponse.Message.ToString());
			return false;
		}
		for (UEdGraphPin* Target : OldTargets)
		{
			const FPinConnectionResponse TailResponse = Schema->CanCreateConnection(InsertOut, Target);
			if (TailResponse.Response == CONNECT_RESPONSE_DISALLOW)
			{
				OutError = FString::Printf(
					TEXT("cannot re-attach the downstream link %s -> %s after the insert: %s. ")
					TEXT("Nothing was changed (the existing exec chain is intact)."),
					*DescribePin(InsertOut), *DescribePin(Target), *TailResponse.Message.ToString());
				return false;
			}
		}

		// ---- only now is it safe to break ------------------------------------------------------
		Schema->BreakPinLinks(*SourceOut, /*bSendsNodeNotification*/ true);
		if (!Schema->TryCreateConnection(SourceOut, InsertIn))
		{
			// CanCreateConnection approved it a moment ago, so this is a schema-side surprise rather
			// than caller error. Report it as a hard failure: the chain is currently severed, and the
			// caller's transaction (RunEndpoint's, or the recipe's own) is what puts it back.
			OutError = FString::Printf(
				TEXT("%s -> %s was approved by the schema but the connection did not take; the exec chain is severed. ")
				TEXT("Undo this call."), *DescribePin(SourceOut), *DescribePin(InsertIn));
			return false;
		}

		TArray<FString> Unmoved;
		for (UEdGraphPin* Target : OldTargets)
		{
			if (UEdGraphNode* Owner = Target->GetOwningNodeUnchecked()) { Owner->Modify(); }
			if (Schema->TryCreateConnection(InsertOut, Target)) { ++OutMovedTargets; }
			else { Unmoved.Add(DescribePin(Target)); }
		}
		if (Unmoved.Num() > 0)
		{
			OutError = FString::Printf(
				TEXT("%d of %d downstream link(s) could not be re-attached to %s (%s); the exec chain is incomplete. Undo this call."),
				Unmoved.Num(), OldTargets.Num(), *DescribePin(InsertOut), *FString::Join(Unmoved, TEXT(", ")));
			return false;
		}
		return true;
	}

	bool SpliceExecBefore(UEdGraphPin* TargetIn, UEdGraphPin* EntryIn, UEdGraphPin* ExitOut,
		int32& OutMovedUpstreams, FString& OutError)
	{
		OutMovedUpstreams = 0;
		if (!TargetIn || !EntryIn || !ExitOut)
		{
			OutError = TEXT("splice needs all three exec pins (target in, cluster entry in, cluster exit out)");
			return false;
		}

		const UEdGraphSchema_K2* Schema = K2();

		TArray<UEdGraphPin*> Upstreams;
		for (UEdGraphPin* Upstream : TargetIn->LinkedTo)
		{
			if (Upstream) { Upstreams.Add(Upstream); }
		}

		// Validate every link the new shape needs BEFORE breaking the old one.
		for (UEdGraphPin* Upstream : Upstreams)
		{
			const FPinConnectionResponse Response = Schema->CanCreateConnection(Upstream, EntryIn);
			if (Response.Response == CONNECT_RESPONSE_DISALLOW)
			{
				OutError = FString::Printf(
					TEXT("cannot re-point the upstream link %s -> %s at the cluster entry: %s. Nothing was changed."),
					*DescribePin(Upstream), *DescribePin(TargetIn), *Response.Message.ToString());
				return false;
			}
		}
		const FPinConnectionResponse TailResponse = Schema->CanCreateConnection(ExitOut, TargetIn);
		if (TailResponse.Response == CONNECT_RESPONSE_DISALLOW)
		{
			OutError = FString::Printf(TEXT("cannot connect %s -> %s: %s. Nothing was changed."),
				*DescribePin(ExitOut), *DescribePin(TargetIn), *TailResponse.Message.ToString());
			return false;
		}

		Schema->BreakPinLinks(*TargetIn, /*bSendsNodeNotification*/ true);

		TArray<FString> Unmoved;
		for (UEdGraphPin* Upstream : Upstreams)
		{
			if (UEdGraphNode* Owner = Upstream->GetOwningNodeUnchecked()) { Owner->Modify(); }
			if (Schema->TryCreateConnection(Upstream, EntryIn)) { ++OutMovedUpstreams; }
			else { Unmoved.Add(DescribePin(Upstream)); }
		}
		const bool bTailConnected = Schema->TryCreateConnection(ExitOut, TargetIn);

		if (Unmoved.Num() > 0 || !bTailConnected)
		{
			OutError = FString::Printf(
				TEXT("splice-before did not complete: %d of %d upstream link(s) unmoved (%s), cluster exit connected=%s. ")
				TEXT("The exec chain is incomplete — undo this call."),
				Unmoved.Num(), Upstreams.Num(),
				Unmoved.Num() ? *FString::Join(Unmoved, TEXT(", ")) : TEXT("-"),
				bTailConnected ? TEXT("true") : TEXT("false"));
			return false;
		}
		return true;
	}

	bool SetPinDefaultChecked(UEdGraphPin* Pin, const FString& Value,
		FString& OutBefore, FString& OutAfter, bool& bOutChanged, FString& OutError)
	{
		bOutChanged = false;
		if (!Pin) { OutError = TEXT("null pin"); return false; }

		// A pin default lives in one of three slots depending on the pin type (literal text, an object
		// reference, or an FText). Comparing only DefaultValue would miss an object-pin write entirely.
		auto Snapshot = [](const UEdGraphPin* P)
		{
			return FString::Printf(TEXT("%s|%s|%s"),
				*P->DefaultValue,
				P->DefaultObject ? *P->DefaultObject->GetPathName() : TEXT("None"),
				*P->DefaultTextValue.ToString());
		};

		OutBefore = Snapshot(Pin);
		K2()->TrySetDefaultValue(*Pin, Value);   // void; the schema silently refuses what it cannot parse
		OutAfter = Snapshot(Pin);
		bOutChanged = !OutAfter.Equals(OutBefore, ESearchCase::CaseSensitive);

		if (!bOutChanged && !Value.Equals(Pin->DefaultValue, ESearchCase::CaseSensitive))
		{
			// The caller asked for something the pin does not now hold: the schema rejected it. That
			// used to be invisible — TrySetDefaultValue returns nothing and nobody read the pin back.
			OutError = FString::Printf(
				TEXT("the schema refused '%s' as the default for pin '%s' (%s); the pin still holds '%s'. ")
				TEXT("Pin defaults must parse for the pin's type — use list_enum_values for enum pins, and ")
				TEXT("note that a CONNECTED pin ignores its literal default."),
				*Value, *Pin->PinName.ToString(), *Pin->PinType.PinCategory.ToString(), *Pin->DefaultValue);
			return false;
		}
		return true;
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
			OutError = DescribeMissingBlueprint(P);
			return nullptr;
		}
		return Blueprint;
	}

	FString DescribeMissingBlueprint(const FString& Path)
	{
		// "blueprint not found" is the wrong answer for a COOKED asset, and it is the single most
		// misleading error the bridge produced: cooking strips the editor-only UBlueprint entirely and
		// ships only the UBlueprintGeneratedClass, so list_graphs/find_nodes on a cooked BP reported
		// "not found" for an asset that plainly exists. Separate the three cases.
		FString P = Path;
		P.TrimStartAndEndInline();

		// Strip any object suffix to get the package name: /Game/A/BP_Foo.BP_Foo -> /Game/A/BP_Foo
		FString PackageName = P;
		{
			FString Left, Right;
			if (P.Split(TEXT("."), &Left, &Right))
			{
				PackageName = Left;
			}
		}

		// Does a generated class exist at this path? Try the two spellings a cooked BP class takes.
		const FString ShortName = FPackageName::GetShortName(PackageName);
		UClass* GeneratedClass = nullptr;
		for (const FString& Candidate : { PackageName + TEXT(".") + ShortName + TEXT("_C"), P })
		{
			if (UObject* Found = StaticLoadObject(UObject::StaticClass(), nullptr, *Candidate, nullptr, LOAD_NoWarn | LOAD_Quiet))
			{
				if (UClass* AsClass = Cast<UClass>(Found))
				{
					GeneratedClass = AsClass;
					break;
				}
			}
		}

		if (GeneratedClass)
		{
			// ClassGeneratedBy is the editor back-pointer to the UBlueprint; it is null once cooked.
			const bool bCooked = GeneratedClass->ClassGeneratedBy == nullptr;
			return FString::Printf(
				TEXT("'%s' resolves to the generated class '%s' but has no editable UBlueprint%s. ")
				TEXT("Cooked packages strip Blueprint graphs, so list_graphs/list_nodes/find_nodes cannot ")
				TEXT("read them. To READ the logic, decompile it: run_console {\"command\":\"mif.kr.Reconstruct %s\"} ")
				TEXT("(MifKismetReconstructor; see also mif.kr.DumpBP / mif.kr.DumpFull / mif.kr.Events). ")
				TEXT("To EDIT it, mint an editable copy first: create_editable_child {\"sourceAsset\":\"%s\", ")
				TEXT("\"variant\":\"full\"} and point subsequent calls at the returned blueprintId."),
				*P, *GeneratedClass->GetPathName(), bCooked ? TEXT(" (cooked)") : TEXT(""),
				*ShortName, *GeneratedClass->GetPathName());
		}

		if (FPackageName::IsValidLongPackageName(PackageName) && FPackageName::DoesPackageExist(PackageName))
		{
			return FString::Printf(
				TEXT("package '%s' exists but contains no UBlueprint (wrong asset type, or a cooked/stripped package). ")
				TEXT("Use list_blueprints to confirm the path."), *PackageName);
		}

		return FString::Printf(
			TEXT("blueprint not found: %s (no package at '%s' — check the path with list_blueprints; ")
			TEXT("bare package paths like /Game/A/BP_Foo are accepted)"), *P, *PackageName);
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

	// Append Graph and everything nested underneath it.
	//
	// Nested graphs are NOT in the blueprint's four top-level arrays — they hang off the NODES:
	// a collapsed/composite node owns UK2Node_Composite::BoundGraph, an anim state machine node owns
	// the state-machine graph, each state owns its own animation graph, and each transition owns its
	// rule graph. UEdGraphNode::GetSubGraphs() is the virtual that exposes them all uniformly.
	//
	// Without this recursion the bridge could not see inside ANY state machine or collapsed node —
	// list_graphs/list_nodes/find_nodes simply reported the container node and stopped. That is what
	// made animation blueprints look unreadable.
	static void GatherGraphsRecursive(UEdGraph* Graph, TArray<UEdGraph*>& OutGraphs, TSet<UEdGraph*>& Visited)
	{
		if (!Graph || Visited.Contains(Graph))
		{
			return;   // cycle guard — a malformed asset must not hang the editor
		}
		Visited.Add(Graph);
		OutGraphs.Add(Graph);

		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (!Node)
			{
				continue;
			}
			for (UEdGraph* Sub : Node->GetSubGraphs())
			{
				GatherGraphsRecursive(Sub, OutGraphs, Visited);
			}
		}
		// Belt-and-braces: some graph types track children in the array directly.
		for (UEdGraph* Sub : Graph->SubGraphs)
		{
			GatherGraphsRecursive(Sub, OutGraphs, Visited);
		}
	}

	void GatherGraphs(UBlueprint* Blueprint, TArray<UEdGraph*>& OutGraphs)
	{
		if (!Blueprint)
		{
			return;
		}
		TSet<UEdGraph*> Visited;
		// Top-level order preserved (ubergraph, functions, macros, delegates); each root is followed
		// immediately by its own nested graphs, so a caller reading the list top-down sees hierarchy.
		for (UEdGraph* Graph : Blueprint->UbergraphPages)          { GatherGraphsRecursive(Graph, OutGraphs, Visited); }
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)          { GatherGraphsRecursive(Graph, OutGraphs, Visited); }
		for (UEdGraph* Graph : Blueprint->MacroGraphs)             { GatherGraphsRecursive(Graph, OutGraphs, Visited); }
		for (UEdGraph* Graph : Blueprint->DelegateSignatureGraphs) { GatherGraphsRecursive(Graph, OutGraphs, Visited); }
		// Interface functions implemented BY THIS blueprint live in ImplementedInterfaces[].Graphs, NOT in
		// FunctionGraphs — so without this loop they are invisible to list_graphs/list_nodes/add_* and the
		// whole interface is unusable from the bridge. (Overrides of an interface the PARENT implements DO
		// land in FunctionGraphs, which is why GetObjectMeta/CheckFallback always worked and
		// GetRadialOptions/PassRadialChoice did not.)
		for (const FBPInterfaceDescription& Iface : Blueprint->ImplementedInterfaces)
		{
			for (UEdGraph* Graph : Iface.Graphs)                   { GatherGraphsRecursive(Graph, OutGraphs, Visited); }
		}
	}

	FString GraphNamePathOf(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		// Dotted path from the blueprint down to Graph, e.g. "AnimGraph.Locomotion.Idle".
		// Two state machines can each hold a state called "Idle", so a bare name is not a key.
		// The outer chain alternates graph -> owning node -> parent graph, hence the Cast filter.
		TArray<FString> Segments;
		for (UObject* Outer = Graph; Outer && Outer != Blueprint; Outer = Outer->GetOuter())
		{
			if (UEdGraph* AsGraph = Cast<UEdGraph>(Outer))
			{
				Segments.Insert(AsGraph->GetName(), 0);
			}
		}
		// A top-level graph yields exactly one segment, so its id is byte-identical to the pre-nesting
		// format and every previously issued graphId keeps working.
		return Segments.Num() > 0 ? FString::Join(Segments, TEXT(".")) : Graph->GetName();
	}

	FString GraphIdOf(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		return Blueprint->GetPathName() + TEXT("::") + GraphNamePathOf(Blueprint, Graph);
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

		// 1. Exact qualified path ("AnimGraph.Locomotion.Idle") — what GraphIdOf now emits.
		for (UEdGraph* Graph : Graphs)
		{
			if (GraphNamePathOf(OutBlueprint, Graph) == Right)
			{
				return Graph;
			}
		}

		// 2. Bare leaf name — keeps every previously issued graphId working, and lets a caller name a
		//    nested graph directly when it is unambiguous. Refuse to guess when it is not.
		UEdGraph* Match = nullptr;
		int32 MatchCount = 0;
		for (UEdGraph* Graph : Graphs)
		{
			if (Graph->GetName() == Right)
			{
				Match = Graph;
				++MatchCount;
			}
		}
		if (MatchCount == 1)
		{
			return Match;
		}
		if (MatchCount > 1)
		{
			TArray<FString> Candidates;
			for (UEdGraph* Graph : Graphs)
			{
				if (Graph->GetName() == Right)
				{
					Candidates.Add(GraphNamePathOf(OutBlueprint, Graph));
				}
			}
			OutError = FString::Printf(
				TEXT("graph name '%s' is ambiguous in %s — %d graphs share it (nested graphs: anim states, ")
				TEXT("transition rules, collapsed nodes). Use the full dotted path: %s"),
				*Right, *Left, MatchCount, *FString::Join(Candidates, TEXT(" | ")));
			return nullptr;
		}

		OutError = FString::Printf(TEXT("graph '%s' not found in %s (list_graphs shows every graph, including nested ones, by its full dotted path)"), *Right, *Left);
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
		FString GuidStr = JStr(In, Field);
		// The generic single-node field is spelled "nodeGuid" by some endpoints (move_node,
		// remove_node, refresh_node, get_node) and "node" by others (disconnect_pin, set_pin_default,
		// set_pin_type). Accept either — plus "guid"/"nodeId" — so a caller never has to remember which.
		// Endpoints with MULTIPLE node params (srcNode/dstNode, afterNode/insertNode, the recipes)
		// are deliberately excluded: aliasing there would let one node satisfy two distinct roles.
		const bool bGenericField = FCString::Stricmp(Field, TEXT("node")) == 0
			|| FCString::Stricmp(Field, TEXT("nodeGuid")) == 0;
		if (GuidStr.IsEmpty() && bGenericField)
		{
			GuidStr = JStrAny(In, { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId") });
		}
		if (GuidStr.IsEmpty())
		{
			Fail(Out, bGenericField
				? FString::Printf(TEXT("missing %s (accepted spellings: nodeGuid, node, guid, nodeId)"), Field)
				: FString::Printf(TEXT("missing %s"), Field));
			return nullptr;
		}
		// FGuid::Parse accepts BOTH the dashed (36-char) and undashed (32-char) forms, and every guid
		// the bridge emits is FGuid::ToString()'s default EGuidFormats::Digits (undashed). So either
		// spelling works on every endpoint — a "this one wants dashes" mismatch is really the
		// field-NAME mismatch handled above.
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

	// Nodes disagree on what to call their single value output: UK2Node_CallFunction uses
	// "ReturnValue", UK2Node_FormatText uses "Result", UK2Node_MakeArray/MakeMap use "Array"/"Map",
	// GetDataTableRow uses "Out Row". A caller who guesses wrong gets "pin not found" and has to spend
	// a probe on get_node. These groups are interchangeable ONLY as a last resort (see below).
	static const TCHAR* const OutputAliasGroups[][5] = {
		{ TEXT("ReturnValue"), TEXT("Result"),  TEXT("Output"),  TEXT("OutputPin"), nullptr },
		{ TEXT("Array"),       TEXT("OutArray"), nullptr,        nullptr,           nullptr },
		{ TEXT("Out Row"),     TEXT("OutRow"),  TEXT("Row"),     nullptr,           nullptr },
	};

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
		if (AnyMatch)
		{
			return bRequireDir ? nullptr : AnyMatch;
		}

		// LAST RESORT ONLY — nothing matched by the caller's spelling. Try the alias group for the
		// requested name and accept a hit only if it is UNAMBIGUOUS (exactly one pin on the node
		// matches any alias in the group). Running this only after an exact miss means a node that
		// genuinely has the requested pin is never redirected, so this can't silently retarget a
		// working call; it can only rescue one that was already going to fail.
		for (const TCHAR* const (&Group)[5] : OutputAliasGroups)
		{
			bool bNameInGroup = false;
			for (int32 i = 0; i < 5 && Group[i]; ++i)
			{
				if (PinName.Equals(Group[i], ESearchCase::IgnoreCase)) { bNameInGroup = true; break; }
			}
			if (!bNameInGroup)
			{
				continue;
			}

			UEdGraphPin* AliasHit = nullptr;
			int32 AliasCount = 0;
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (!Pin || (bRequireDir && Pin->Direction != PreferDir))
				{
					continue;
				}
				for (int32 i = 0; i < 5 && Group[i]; ++i)
				{
					if (Pin->PinName.ToString().Equals(Group[i], ESearchCase::IgnoreCase))
					{
						AliasHit = Pin;
						++AliasCount;
						break;
					}
				}
			}
			if (AliasCount == 1)
			{
				return AliasHit;
			}
			break; // name belongs to this group and it didn't resolve cleanly; don't try other groups
		}
		return nullptr;
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

	UClass* ResolveClassStrict(const FString& Name, UBlueprint* ContextBP, const TCHAR* ParamName, FString& OutError)
	{
		FString N = Name;
		N.TrimStartAndEndInline();
		if (N.IsEmpty())
		{
			// The whole point of this overload. ResolveClass("") returns ContextBP's OWN class, so a
			// misspelled key (e.g. "class" instead of "targetClass") used to produce a node that
			// silently targeted the blueprint itself — a self-cast that always succeeds, or a
			// SpawnActor of the spawner. Both compile clean and are near-invisible in review.
			OutError = FString::Printf(TEXT("'%s' is required and must name a class (an empty value would silently resolve to this blueprint's own class)"), ParamName);
			return nullptr;
		}
		UClass* Resolved = ResolveClass(N, ContextBP);
		if (!Resolved)
		{
			OutError = FString::Printf(TEXT("%s: class not found: '%s' (try the full path, e.g. /Game/BP/BP_Foo.BP_Foo_C)"), ParamName, *N);
		}
		return Resolved;
	}

	UClass* ResolveClassStrictField(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields,
		UBlueprint* ContextBP, const TSharedRef<FJsonObject>& Out)
	{
		check(Fields.size() > 0);
		const TCHAR* Primary = *Fields.begin();
		FString Error;
		UClass* Resolved = ResolveClassStrict(JStrAny(In, Fields), ContextBP, Primary, Error);
		if (!Resolved)
		{
			Fail(Out, Error);
		}
		return Resolved;
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

	bool MakePinType(const FString& TypeStr, const FString& Container, FEdGraphPinType& OutType, FString& OutError, const FString& ValueTypeStr)
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
		else if (L == TEXT("float") || L == TEXT("float32") || L == TEXT("single"))
		{
			// TRUE 32-bit float. In UE5 the category is always PC_Real and the WIDTH lives in the
			// subcategory (EdGraphSchema_K2.h declares PC_Float and PC_Double as separate FNames).
			// This used to map to PC_Double along with everything else, which made a real float pin
			// unreachable — and a double-returning UFUNCTION fails UMG's delegate signature match for
			// a TAttribute<float> property (PercentDelegate/OpacityDelegate...), so bindings couldn't
			// be authored at all. float and double still interconnect via the schema's autocast.
			OutType.PinCategory = UEdGraphSchema_K2::PC_Real;
			OutType.PinSubCategory = UEdGraphSchema_K2::PC_Float;
		}
		else if (L == TEXT("double") || L == TEXT("float64") || L == TEXT("real"))
		{
			// 64-bit. "real" stays an alias for double: that is what BP shows for an unqualified
			// numeric pin in UE5, and it is what "float" resolved to before the split above.
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
			// The prefix grammar is not guessable from a bare "unknown type" — spell it out, because
			// getting here on an object/class/enum name is almost always a missing prefix, not a typo.
			OutError = FString::Printf(
				TEXT("unknown type: '%s'. Scalars: bool|byte|int|int64|float|double|real|string|name|text ")
				TEXT("(float = 32-bit, double/real = 64-bit). Struct/enum/class names may be given bare, but ")
				TEXT("a REFERENCE needs a prefix: object:<ClassOrPath>, class:<C> (alias subclassof:), ")
				TEXT("softobject:<C>, softclass:<C>, interface:<C>, enum:<E>. ")
				TEXT("Paths work too, e.g. object:/Game/BP/BP_Foo.BP_Foo_C. Containers go in the separate ")
				TEXT("'container' field (array|set)."),
				*T);
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
			// A map needs TWO types. Everything resolved above describes the KEY (PinCategory /
			// PinSubCategory / PinSubCategoryObject); the VALUE lives in the separate PinValueType
			// terminal. Without ValueTypeStr there is nothing to put there, so a map was previously
			// rejected outright — which made TMap unexpressible in variables, function parameters and
			// dispatcher signatures alike, since every typing path routes through here.
			if (ValueTypeStr.IsEmpty())
			{
				OutError = TEXT("map container requires a value type — pass valueType (e.g. type='name', container='map', valueType='int'). The 'type' field is the KEY type.");
				return false;
			}
			FEdGraphPinType ValuePinType;
			FString ValueError;
			if (!MakePinType(ValueTypeStr, FString(), ValuePinType, ValueError))
			{
				OutError = FString::Printf(TEXT("map valueType: %s"), *ValueError);
				return false;
			}
			// Nested containers are not representable — a terminal type has no container field.
			if (ValuePinType.ContainerType != EPinContainerType::None)
			{
				OutError = TEXT("map values cannot themselves be containers (no TMap<K, TArray<V>> in Blueprint) — wrap the value in a struct instead");
				return false;
			}
			OutType.ContainerType = EPinContainerType::Map;
			OutType.PinValueType = FEdGraphTerminalType::FromPinType(ValuePinType);
		}
		else if (!C.IsEmpty() && C != TEXT("none"))
		{
			OutError = FString::Printf(TEXT("unknown container '%s' (expected: array | set | map, or omit for a single value)"), *Container);
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

		// Only allocate if PostPlacedNewNode didn't already. Most K2Nodes leave Pins empty there
		// (engine's own FEdGraphSchemaAction_NewNode::CreateNode does the same two calls back-to-back),
		// but the function TERMINATORS do not: UK2Node_FunctionResult::PostPlacedNewNode calls
		// SyncWithEntryNode(), which sees a signature mismatch on a fresh node and ReconstructNode()s —
		// fully allocating the pins. The follow-up AllocateDefaultPins then runs
		//     CreatePin(EGPD_Input, PC_Exec, PN_Execute)
		// with NO FindPin guard (K2Node_FunctionResult.cpp; contrast UK2Node_EditablePinBase::
		// AllocateDefaultPins, which does check), producing a SECOND "execute" pin on every Return
		// node create_function minted. That duplicate is what raised the permanent compile warning.
		if (Node->Pins.Num() == 0)
		{
			Node->AllocateDefaultPins();
		}
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
		case EPinContainerType::Map:
			Json->SetStringField(TEXT("container"), TEXT("map"));
			{
				// For a map the top-level category describes the KEY; the value is a separate
				// terminal type. Emitting only the key would make TMap<Name,int> read back as
				// indistinguishable from TMap<Name,bool>.
				TSharedRef<FJsonObject> ValueJson = MakeShared<FJsonObject>();
				ValueJson->SetStringField(TEXT("category"), Type.PinValueType.TerminalCategory.ToString());
				if (!Type.PinValueType.TerminalSubCategory.IsNone())
				{
					ValueJson->SetStringField(TEXT("subCategory"), Type.PinValueType.TerminalSubCategory.ToString());
				}
				if (Type.PinValueType.TerminalSubCategoryObject.IsValid())
				{
					ValueJson->SetStringField(TEXT("subObject"), Type.PinValueType.TerminalSubCategoryObject->GetName());
				}
				Json->SetObjectField(TEXT("valueType"), ValueJson);
			}
			break;
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
		// Full object path, so set_property/get_property can target the NODE itself. Details-panel-only
		// node settings (anim transition CrossfadeDuration/BlendMode/PriorityOrder, cast purity, switch
		// defaults) have no dedicated endpoint and are only reachable this way — and without the path
		// emitted here, that route was undiscoverable.
		Json->SetStringField(TEXT("objectPath"), Node->GetPathName());
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
