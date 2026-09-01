// MifBridge - ENDPOINT SELF-DESCRIPTION (describe_endpoint).
//
// One endpoint:
//   describe_endpoint - READ-ONLY. Reports the accepted parameter set, the alias groups, the
//     common-mistake hints and the self_audit row for a named endpoint. It resolves names,
//     reads tables and builds JSON; it calls no Modify(), loads no asset and creates nothing.
//
// TRANSACTION BUCKET - ACTION REQUIRED BY THE INTEGRATOR. This endpoint belongs in
// IsReadOnlyEndpoint's TSet in MifBridgeCommon.cpp. That file is owned by the integrator, so the
// entry is REPORTED rather than added here. Until it lands, RunEndpoint gives describe_endpoint the
// blanket transaction and EVERY call pushes an empty entry onto the undo stack - the exact pollution
// the comment above describe_property in that same TSet exists to prevent. Nothing is mis-reported
// meanwhile: the endpoint answers correctly, it just litters undo.
//
// Verify-after-write does not apply: this endpoint performs no write. The equivalent obligation for
// a pure reader is that it must not claim knowledge it does not have, which is the whole subject of
// the next two sections.
//
// ---------------------------------------------------------------------------------------------
// THE THREE STATES, AND WHY THIS ENDPOINT EXISTS
// ---------------------------------------------------------------------------------------------
// Accepted-key lists in this plugin exist ONLY as the inline initialiser-list argument to
// RejectUnknownParams inside a handler body, and by now MOST handlers call it: 202 guard sites
// outside this file, covering 205 of 208 MIF_DECL'd endpoints (three guards sit in shared helper
// bodies serving two endpoints each - see HARVEST below). The table below therefore has 206 rows:
// those 205, plus describe_endpoint's own. The 2 registered endpoints with NO row are
// recipe_override_and_call_parent and self_audit; both were unguarded at this harvest.
//
// THESE THREE NUMBERS ARE A STALE ANNOTATION, NOT A FACT TO QUOTE. They are a note about the source
// tree at one instant, they are not derived from anything the DLL can see, and NOTHING recomputes
// them when a guard is added - so treat any figure in this comment block as wrong until re-derived.
// They have now gone stale TWICE. They read 93/95/96 at the previous harvest and were already wrong
// by 2 rows before this one began. Before that they read 83/85/86 while
// ten endpoints - capture_camera, set_pin_type, import_texture, import_asset, reimport_asset,
// set_texture_settings, thumbnail_capabilities, render_thumbnail, write_thumbnail_texture,
// set_asset_thumbnail - had acquired guards in the same wave this table was harvested in, and every
// one of them was answered "does not call RejectUnknownParams" when it in fact rejects unknown keys
// outright. Re-derive before trusting them: `grep -rc "RejectUnknownParams(" Private/*.cpp` (minus
// this file's own guard) for the sites, and `grep -c "^\s*MIF_DECL(" Private/MifBridgeHandlers.h`
// for the denominator. Nothing in the DLL can check them for you - see COVERAGE below for why.
//
// Counting note, because "84 of 199" was once the figure in circulation and this file disagreed with
// it: counting GUARD SITES and counting ENDPOINTS THAT REACH A GUARD differ by three. The gap is the
// three shared bodies - DoAddVariableNode (add_variable_get, add_variable_set), DoConnect
// (connect_pins, reconnect_pin) and SpawnDelegateNode (add_call_dispatcher, add_bind_dispatcher) -
// where one guard site serves two endpoints each. Attributing a
// guard to the nearest preceding H_ function, which is the obvious way to scan for this, gets all
// six of those wrong AND silently misattributes any guard written inside a helper that happens to
// sit between two handlers. Resolution here was done by brace-matched enclosing scope plus one hop
// through the call graph.
//
// describe_endpoint therefore reports THREE states and never collapses them:
//
//   "params_declared"     - the endpoint guards its input and here is the accepted set. Note this
//                           includes a DECLARED-EMPTY set: list_mounted_containers and
//                           shader_compile_status call RejectUnknownParams with {} and the summary
//                           "(none - this endpoint takes no parameters)". That is a real, positive
//                           statement and is flagged with acceptsNoParameters:true.
//   "params_not_declared" - THE TABLE HAS NO ROW for this endpoint, so its accepted set cannot be
//                           enumerated here. Read the status name as "not declared IN THIS TABLE":
//                           it does NOT establish that the handler is unguarded. An unharvested guard
//                           is indistinguishable from no guard from inside the DLL, and the two
//                           behave OPPOSITELY - silently ignore the unknown key, or reject the whole
//                           call - so the response names both and asserts neither.
//                           acceptedParams is OMITTED ENTIRELY from the response, not emitted empty.
//   "no_such_endpoint"    - the name is not registered. ok:false, with near-miss suggestions.
//
// Emitting an empty acceptedParams for the middle case would read as "takes no parameters" - and
// would be indistinguishable from the two endpoints for which that is literally true. That is the
// confidently-wrong-answer defect class this plugin keeps shipping, so the middle case omits the
// field and says why in `note`.
//
// ---------------------------------------------------------------------------------------------
// HARVEST MECHANISM - what was already there, what was chosen, and why
// ---------------------------------------------------------------------------------------------
// FIRST, THE PRIOR ART, because the brief asked whether MifBridgeCommon.cpp already harvests this.
// IT DOES, BUT NOT USABLY FOR THIS ENDPOINT, and it must not be mistaken for a table:
// RejectUnknownParams canonicalises each accepted-key list it sees (lowercase, sort, comma-join) and
// adds the resulting STRING to a file-static TSet<FString> GMifObservedParamShapes, which
// H_self_audit folds into `paramSignature`. Read its own caveat block: that set is
//   - keyed by the SHAPE, so the endpoint name is thrown away, and two endpoints with identical
//     accepted sets collapse into one entry (self_audit's own comment records 83 sites yielding 79
//     distinct shapes);
//   - lazy, so it holds only what has actually run this editor session - zero entries on a fresh
//     DLL load;
//   - lossy, keeping neither the AcceptedSummary prose (which is where the ALIAS grouping lives) nor
//     the KeyNotes hints.
// It answers "did the parameter surface move?", never "what does endpoint X accept?". Its own
// limitation 3 states the gap exactly: attributing a shape to an endpoint needs the endpoint name
// plumbed into RejectUnknownParams. So this file EXTENDS nothing and DUPLICATES nothing - it is not
// a second copy of that set, it is the different question that set cannot answer.
//
// OPTION (a), a runtime side-table filled as RejectUnknownParams runs, was rejected twice over.
// It requires editing RejectUnknownParams in MifBridgeCommon.cpp, which this file's author does not
// own. And it is lazy by construction: describe_endpoint would answer "params_not_declared" for a
// guarded endpoint purely because nobody had called it yet this session, which is precisely the
// moment an agent needs it. A discovery endpoint that is empty right after editor start is useless.
//
// OPTION (b), the authored table below, was chosen: populated for every guarded endpoint KNOWN AT
// HARVEST TIME from the instant the DLL loads, session-independent, and it carries the summary prose
// and KeyNotes that the runtime set discards. Note the qualifier - option (b) trades option (a)'s
// laziness for a completeness it can never verify, and that is the trade that bit (see COVERAGE).
//
// ITS COST IS STALENESS - the table is a copy, and a copy can drift from the literals it was taken
// from. Three things hold it honest, and the third is the important one:
//   1. Every row cites the exact <file>:<line> of the guard it came from, emitted as `guard` in the
//      response. Verification is one jump, not a search.
//   2. A row whose endpoint is no longer registered is DETECTED AT RUNTIME, on every call, by
//      checking each row against the live merged registry. Renames and removals - one drift
//      direction - self-report as `coverage.staleTableRows` and flip `coverage.noStaleTableRows`
//      to false. That boolean is deliberately narrow; see COVERAGE below for what it must not be
//      read as.
//   3. The alias groups are NOT a second data copy. They are parsed at runtime out of the same
//      AcceptedSummary string the guard itself prints, then CROSS-CHECKED against the harvested key
//      list. A key named in the prose but absent from the list (or the reverse) is reported as
//      `summaryInconsistencies` / `keysNotInSummary` rather than smoothed over - so this endpoint
//      surfaces guard-vs-prose drift in the SOURCE as a side effect of answering.
// What is NOT runtime-detectable: (i) a guard whose key list changed while its endpoint name stayed
// put, and (ii) a guard that exists in the source but has NO ROW here at all. Both are stated in the
// response - (i) in the `harvest` block, (ii) in `coverage.completenessNote` and in the note the
// no-row branch emits - rather than left for the caller to discover.
//
// ---------------------------------------------------------------------------------------------
// COVERAGE - THE CLAIM THIS FILE IS NOT ALLOWED TO MAKE
// ---------------------------------------------------------------------------------------------
// A missing row is invisible from inside the DLL. An accepted-key list is an inline initialiser-list
// argument that exists only while its handler runs, so nothing here can enumerate the guards that
// EXIST; it can only enumerate the guards that were WRITTEN DOWN. Coverage therefore reports:
//   * staleTableRows / noStaleTableRows - rows whose endpoint vanished. Provable, and reported.
//   * endpointsWithTableRow / endpointsWithoutTableRow - counts OF THE TABLE. The second is an UPPER
//     BOUND on the endpoints that silently ignore unknown keys, never a count of them.
//   * completenessVerifiable:false - said out loud, because the previous `tableHealthy:true` was read
//     as "the table is complete" when all it ever meant was "no row has gone stale". It stayed true
//     through the exact failure it was supposed to catch: ten guarded endpoints with no row, each
//     confidently described as unguarded. A boolean that is green during the defect it names is worse
//     than no boolean.
// The rule for anything added here: emit what the code can establish, and name the field after that.
//
// THE REAL FIX, for whoever owns MifBridgeCommon.cpp: plumb the endpoint name into
// RejectUnknownParams and have it record name -> {keys, summary, notes}. describe_endpoint then
// prefers the live record and falls back to this table for endpoints not yet called, which removes
// both the staleness and the laziness. The response shape below already accommodates that: `source`
// is per-response, so it can become "runtime" for observed endpoints without any caller change.
//
// Unity-build note: every free function and file-scope object here is prefixed MifDescribe/GMifDesc.
// A unity blob merges unnamed namespaces, so internal linkage does not prevent C2084.
#include "MifBridgeHandlers.h"
#include "MifBridgeLog.h"

namespace MifBridge
{
	namespace
	{
		// One harvested guard. Keys and NotePairs are nullptr-terminated static arrays; Keys may
		// legitimately be empty (declared to take no parameters at all).
		struct FMifDescribeRow
		{
			const TCHAR*        Endpoint;
			const TCHAR* const* Keys;
			const TCHAR* const* NotePairs;   // flat {key, hint} pairs, or nullptr when the guard has none
			const TCHAR*        Summary;     // verbatim AcceptedSummary - the text the guard prints on refusal
			const TCHAR*        SourceFile;
			int32               SourceLine;
			const TCHAR*        ViaHelper;   // non-null when the guard is in a shared body, not in H_<name>
		};

		// describe_endpoint's OWN accepted keys. Defined once and used BOTH by the guard in the
		// handler and by this file's table row, so the endpoint that reports parameter sets cannot
		// misreport its own. (The generated rows below get the same guarantee from regeneration.)
#define MIF_DESCRIBE_OWN_KEYS TEXT("name"), TEXT("endpoint"), TEXT("endpointName")
#define MIF_DESCRIBE_OWN_SUMMARY TEXT("name (aliases: endpoint, endpointName)")
		static const TCHAR* const GMifDescKeys_describe_endpoint[] = { MIF_DESCRIBE_OWN_KEYS, nullptr };
		static const TCHAR* const GMifDescNotes_describe_endpoint[] = {
			TEXT("tool"), TEXT("spell it name"),
			TEXT("endpoint_name"), TEXT("spell it name (this bridge uses camelCase parameters)"),
			TEXT("names"), TEXT("one endpoint per call; self_audit lists them all"),
			nullptr };

		// ---------------------------------------------------------------------------------------------
		// GENERATED DATA - accepted-key sets harvested from the RejectUnknownParams call sites.
		// Regenerate rather than hand-edit; see the HARVEST MECHANISM note in the header block above.
		// Source of truth is the initialiser-list literal at <file>:<line> cited in each row.
		// ---------------------------------------------------------------------------------------------

		// >>> MIF_HARVEST_BEGIN - generated by tools/harvest_param_table.py, do not hand-edit
		// 451 endpoints, harvested from the RejectUnknownParams call site that guards each.
		// Regenerate with tools/harvest_param_table.py; every string below is the VERBATIM
		// source text of the guard, copied rather than re-encoded.

		static const TCHAR* const GMifDescKeys_open_blueprint[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_open_blueprint[] = { TEXT("name"), TEXT("open_blueprint addresses the asset by path, e.g. path:\"/Game/Foo/BP_Bar\"; list_blueprints {filter} finds one by a name fragment first") ,  TEXT("graphId"), TEXT("open_blueprint opens a whole blueprint and RETURNS its graphIds; to read one graph use list_nodes {graphId}"), nullptr };
		static const TCHAR* const GMifDescKeys_list_blueprints[] = { TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_list_blueprints[] = { TEXT("path"), TEXT("list_blueprints takes no path - pass the path fragment as filter, e.g. filter:\"/Game/Blueprints/\"") ,  TEXT("name"), TEXT("matching runs against the FULL object path, so pass the name fragment as filter, e.g. filter:\"BP_Player\"") ,  TEXT("limit"), TEXT("there is no limit parameter - the result is capped at 5000 entries; narrow it with filter"), nullptr };
		static const TCHAR* const GMifDescKeys_save_blueprint[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_save_blueprint[] = { TEXT("savePath"), TEXT("save_blueprint has no save-as: it rewrites the blueprint's OWN package. To save a different asset use save_package {path}.") ,  TEXT("compile"), TEXT("save_blueprint does not compile - call compile {blueprintId} first if the blueprint has pending structural changes"), nullptr };
		static const TCHAR* const GMifDescKeys_save_package[] = { TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_save_package[] = { TEXT("blueprintId"), TEXT("save_package addresses any asset by its /Game/ object path, so pass it as path. For a Blueprint, save_blueprint {blueprintId} does the same thing.") ,  TEXT("package"), TEXT("pass the ASSET's object path as path (e.g. /Game/Data/DT_Items) - the owning package is derived from it") ,  TEXT("assetPath"), TEXT("spell it path"), nullptr };
		static const TCHAR* const GMifDescKeys_list_automation_tests[] = { TEXT("filter"), TEXT("limit"), TEXT("offset"), nullptr };
		static const TCHAR* const GMifDescNotes_list_automation_tests[] = { TEXT("run"), TEXT("this endpoint only LISTS - it never runs a test. Running one is a separate concern and is not offered here.") ,  TEXT("name"), TEXT("spell it filter - it is a substring match, not an exact name"), nullptr };
		static const TCHAR* const GMifDescKeys_backup_blueprint[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_backup_blueprint[] = { TEXT("destination"), TEXT("backup_blueprint picks the backup location itself and reports it as 'backup' in the response; it takes no destination"), nullptr };
		static const TCHAR* const GMifDescKeys_list_graphs[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_graphs[] = { TEXT("graphId"), TEXT("list_graphs RETURNS graphIds, it does not take one - to read a single graph use list_nodes {graphId}") ,  TEXT("filter"), TEXT("list_graphs has no filter; it returns every graph. find_nodes {graphId, byTitle} searches inside one graph."), nullptr };
		static const TCHAR* const GMifDescKeys_list_nodes[] = { TEXT("graphId"), TEXT("hideKnots"), nullptr };
		static const TCHAR* const GMifDescNotes_list_nodes[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("blueprintId"), TEXT("list_nodes reads ONE graph - pass graphId from open_blueprint/list_graphs, not a blueprint path") ,  TEXT("path"), TEXT("this endpoint selects a GRAPH, so pass graphId ('<blueprintPath>::<graphName>'); a bare blueprint path does not name a graph") ,  TEXT("hideReroute"), TEXT("spell it hideKnots (a reroute node is a UK2Node_Knot)"), nullptr };
		static const TCHAR* const GMifDescKeys_get_node[] = { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), nullptr };
		static const TCHAR* const GMifDescNotes_get_node[] = { TEXT("pin"), TEXT("get_node already returns EVERY pin on the node; there is no pin filter") ,  TEXT("blueprintId"), TEXT("a node is addressed by its guid, not by its blueprint - pass graphId if you need to disambiguate two loaded copies"), nullptr };
		static const TCHAR* const GMifDescKeys_list_variables[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_variables[] = { TEXT("filter"), TEXT("list_variables has no filter; it returns every member variable") ,  TEXT("scope"), TEXT("list_variables reports member variables only (scope is always \"member\" in the response); a local variable lives on its function graph and is not listed here") ,  TEXT("name"), TEXT("list_variables lists them all - there is no single-variable lookup; read the entry you want out of variables[]"), nullptr };
		static const TCHAR* const GMifDescKeys_list_functions[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_functions[] = { TEXT("filter"), TEXT("list_functions has no filter; it returns every function graph") ,  TEXT("class"), TEXT("list_functions reads a BLUEPRINT's own function graphs - to reflect over any class's BlueprintCallable functions use describe_class {class, filter}"), nullptr };
		static const TCHAR* const GMifDescKeys_find_nodes[] = { TEXT("graphId"), TEXT("byClass"), TEXT("byTitle"), TEXT("byFunction"), nullptr };
		static const TCHAR* const GMifDescNotes_find_nodes[] = { TEXT("class"), TEXT("spell it byClass, e.g. byClass:\"K2Node_CallFunction\"") ,  TEXT("title"), TEXT("spell it byTitle") ,  TEXT("function"), TEXT("spell it byFunction") ,  TEXT("name"), TEXT("find_nodes has no 'name': use byTitle for the node's displayed title, or byFunction for the name of the function it calls") ,  TEXT("blueprintId"), TEXT("find_nodes searches ONE graph - pass graphId from open_blueprint/list_graphs"), nullptr };
		static const TCHAR* const GMifDescKeys_add_variable[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"), TEXT("scope"), TEXT("function"), TEXT("default"), TEXT("replicated"), TEXT("repNotify"), TEXT("repNotifyFunction"), TEXT("replicationCondition"), TEXT("saveGame"), TEXT("transient"), TEXT("config"), TEXT("instanceEditable"), TEXT("blueprintReadOnly"), TEXT("exposeOnSpawn"), TEXT("advancedDisplay"), TEXT("interp"), TEXT("deprecated"), TEXT("category"), TEXT("tooltip"), TEXT("fieldNotify"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable[] = { TEXT("class"), TEXT("the class belongs IN the type string, not in its own key: type:\"object:SceneComponent\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X") ,  TEXT("className"), TEXT("use type:\"object:X\" (or class:X / subclassof:X / softobject:X / softclass:X)") ,  TEXT("parentClass"), TEXT("add_variable does not take a parent class. For a typed object variable use type:\"object:X\"; to override a parent's event use add_override_event") ,  TEXT("objectClass"), TEXT("use type:\"object:X\"") ,  TEXT("subType"), TEXT("use type:\"object:X\" for the referenced class, or valueType for a map's value type"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_variable[] = { TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("newName"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_variable[] = { TEXT("name"), TEXT("rename_variable needs BOTH oldName and newName; there is no single 'name'"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_variable[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_set_variable_type[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"), TEXT("scope"), TEXT("function"), nullptr };
		static const TCHAR* const GMifDescNotes_set_variable_type[] = { TEXT("class"), TEXT("the class belongs IN the type string: type:\"object:BP_Foo_C\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X") ,  TEXT("newType"), TEXT("spell it type") ,  TEXT("targetClass"), TEXT("use type:\"object:X\" — targetClass is retarget_variable_node's key, for repointing a NODE at another class"), nullptr };
		static const TCHAR* const GMifDescKeys_retarget_variable_node[] = { TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("targetClass"), TEXT("class"), TEXT("self"), nullptr };
		static const TCHAR* const GMifDescNotes_retarget_variable_node[] = { TEXT("type"), TEXT("retarget_variable_node changes WHICH CLASS declares the variable, not the pin type — use set_variable_type for the type") ,  TEXT("var"), TEXT("the variable is taken from the node you name; to place a NEW node use add_variable_get/add_variable_set with targetClass"), nullptr };
		static const TCHAR* const GMifDescKeys_set_variable_default[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("value"), TEXT("default"), TEXT("defaultValue"), nullptr };
		static const TCHAR* const GMifDescKeys_set_variable_flags[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("var"), TEXT("variable"), TEXT("replicated"), TEXT("repNotify"), TEXT("repNotifyFunction"), TEXT("replicationCondition"), TEXT("saveGame"), TEXT("transient"), TEXT("config"), TEXT("instanceEditable"), TEXT("blueprintReadOnly"), TEXT("exposeOnSpawn"), TEXT("advancedDisplay"), TEXT("interp"), TEXT("deprecated"), TEXT("category"), TEXT("tooltip"), TEXT("fieldNotify"), nullptr };
		static const TCHAR* const GMifDescNotes_set_variable_flags[] = { TEXT("variableName"), TEXT("spell it name (aliases: var, variable)") ,  TEXT("replicate"), TEXT("spell it replicated - and repNotify:true already implies it") ,  TEXT("editable"), TEXT("spell it instanceEditable (the Details-panel \"Instance Editable\" checkbox)") ,  TEXT("readOnly"), TEXT("spell it blueprintReadOnly") ,  TEXT("condition"), TEXT("spell it replicationCondition - an ELifetimeCondition such as COND_OwnerOnly; the COND_ prefix is optional") ,  TEXT("onRep"), TEXT("spell it repNotifyFunction; omit it and repNotify:true mints OnRep_<Name> for you") ,  TEXT("default"), TEXT("set_variable_flags only sets flags - use set_variable_default {blueprintId, name, value} to change a variable's default") ,  TEXT("type"), TEXT("set_variable_flags cannot retype a variable; the type is fixed at add_variable {type:\"object:X\"} time"), nullptr };
		static const TCHAR* const GMifDescKeys_add_function_call[] = { TEXT("graphId"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("targetClass"), TEXT("ownerClass"), TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method"), TEXT("asMessage"), TEXT("message"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_function_call[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("target"), TEXT("the target OBJECT is wired into the node's self/Target pin with connect_pins; 'class' names the class that declares the function") ,  TEXT("args"), TEXT("arguments are pins — place the node, then set_pin_default or connect_pins") ,  TEXT("pure"), TEXT("purity comes from the UFUNCTION itself (BlueprintPure); it is not selectable here"), nullptr };
		static const TCHAR* const GMifDescKeys_add_variable_get[] = { TEXT("graphId"), TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"), TEXT("property"), TEXT("propertyName"), TEXT("member"), TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("ownerClass"), TEXT("objectClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable_get[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("target"), TEXT("targetClass names the CLASS that owns the property; the OBJECT is wired into the node's Target pin with connect_pins, never passed here") ,  TEXT("value"), TEXT("a Set node takes its value on a pin — place the node, then set_pin_default or connect_pins") ,  TEXT("scope"), TEXT("scope is auto-detected: a variable declared on this function graph resolves as a local, anything else as a member"), nullptr };
		static const TCHAR* const GMifDescKeys_add_variable_set[] = { TEXT("graphId"), TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"), TEXT("property"), TEXT("propertyName"), TEXT("member"), TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("ownerClass"), TEXT("objectClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable_set[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("target"), TEXT("targetClass names the CLASS that owns the property; the OBJECT is wired into the node's Target pin with connect_pins, never passed here") ,  TEXT("value"), TEXT("a Set node takes its value on a pin — place the node, then set_pin_default or connect_pins") ,  TEXT("scope"), TEXT("scope is auto-detected: a variable declared on this function graph resolves as a local, anything else as a member"), nullptr };
		static const TCHAR* const GMifDescKeys_add_branch[] = { TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_branch[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("condition"), TEXT("the Condition input is a pin — place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_macro_instance[] = { TEXT("graphId"), TEXT("macroGraph"), TEXT("macro"), TEXT("macroName"), TEXT("name"), TEXT("macroPath"), TEXT("macroLibrary"), TEXT("library"), TEXT("path"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_macro_instance[] = { TEXT("graph"), TEXT("spell it graphId"), nullptr };
		static const TCHAR* const GMifDescKeys_add_get_array_item[] = { TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_get_array_item[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("index"), TEXT("the index is a pin — the response names it as indexPin; use set_pin_default or connect_pins") ,  TEXT("array"), TEXT("the array is a pin — the response names it as arrayPin; use connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_override_event[] = { TEXT("blueprintId"), TEXT("path"), TEXT("event"), TEXT("eventName"), TEXT("name"), TEXT("function"), TEXT("functionName"), TEXT("interfaceOrParent"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("parentClass"), TEXT("interface"), TEXT("ownerClass"), TEXT("targetClass"), TEXT("callParent"), TEXT("addParentCall"), TEXT("withParentCall"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_override_event[] = { TEXT("graphId"), TEXT("an override always lands in the blueprint's event graph — pass blueprintId instead"), nullptr };
		static const TCHAR* const GMifDescKeys_add_component_bound_event[] = { TEXT("blueprintId"), TEXT("path"), TEXT("component"), TEXT("dispatcher"), TEXT("delegate"), TEXT("event"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_component_bound_event[] = { TEXT("targetClass"), TEXT("not needed here - the delegate's owner class is found automatically from the component's own type") ,  TEXT("graphId"), TEXT("this always lands in the blueprint's event graph - pass blueprintId instead"), nullptr };
		static const TCHAR* const GMifDescKeys_add_parent_call[] = { TEXT("graphId"), TEXT("parentClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("parent"), TEXT("ownerClass"), TEXT("targetClass"), TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method"), TEXT("name"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_parent_call[] = { TEXT("graph"), TEXT("spell it graphId"), nullptr };
		static const TCHAR* const GMifDescKeys_add_cast[] = { TEXT("graphId"), TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("castTo"), TEXT("to"), TEXT("targetType"), TEXT("pure"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_cast[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("object"), TEXT("the object to cast is a pin — place the node, then connect_pins into its Object pin"), nullptr };
		static const TCHAR* const GMifDescKeys_set_cast_purity[] = { TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("pure"), nullptr };
		static const TCHAR* const GMifDescNotes_set_cast_purity[] = { TEXT("bIsPureCast"), TEXT("pass pure:true|false — writing bIsPureCast directly with set_property changes the flag but does NOT reallocate the exec pins") ,  TEXT("impure"), TEXT("spell it pure:false"), nullptr };
		static const TCHAR* const GMifDescKeys_move_node[] = { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_node[] = { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_refresh_node[] = { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), nullptr };
		static const TCHAR* const GMifDescKeys_blueprint_breakpoint[] = { TEXT("op"), TEXT("graphId"), TEXT("blueprintId"), TEXT("path"), TEXT("nodeGuid"), TEXT("nodeId"), nullptr };
		static const TCHAR* const GMifDescNotes_blueprint_breakpoint[] = { TEXT("line"), TEXT("Blueprint breakpoints sit on a NODE, not a line - pass the " "node's guid, which list_nodes reports") ,  TEXT("condition"), TEXT("conditional breakpoints are not part of the Blueprint " "debugger's model; there is nothing to attach a condition " "to") ,  TEXT("enabled"), TEXT("use op:enable or op:disable - a boolean that silently means " "'create it too' is how a typo becomes a new breakpoint"), nullptr };
		static const TCHAR* const GMifDescKeys_blueprint_watch[] = { TEXT("op"), TEXT("graphId"), TEXT("blueprintId"), TEXT("path"), TEXT("nodeGuid"), TEXT("nodeId"), TEXT("pin"), nullptr };
		static const TCHAR* const GMifDescNotes_blueprint_watch[] = { TEXT("value"), TEXT("a watch READS - it never sets. set_property writes a pin's " "default") ,  TEXT("pinId"), TEXT("pins are addressed by NAME here, which is what list_nodes " "reports for them"), nullptr };
		static const TCHAR* const GMifDescKeys_connect_pins[] = { TEXT("srcNode"), TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin"), TEXT("dstNode"), TEXT("dstPin"), TEXT("destPin"), TEXT("toPin"), TEXT("graphId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_connect_pins[] = { TEXT("from"), TEXT("spell it srcNode") ,  TEXT("fromNode"), TEXT("spell it srcNode") ,  TEXT("sourceNode"), TEXT("spell it srcNode") ,  TEXT("to"), TEXT("spell it dstNode") ,  TEXT("toNode"), TEXT("spell it dstNode") ,  TEXT("destNode"), TEXT("spell it dstNode") ,  TEXT("targetNode"), TEXT("spell it dstNode"), nullptr };
		static const TCHAR* const GMifDescKeys_disconnect_pin[] = { TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"), TEXT("pinName"), TEXT("name"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_reconnect_pin[] = { TEXT("srcNode"), TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin"), TEXT("dstNode"), TEXT("dstPin"), TEXT("destPin"), TEXT("toPin"), TEXT("graphId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_reconnect_pin[] = { TEXT("from"), TEXT("spell it srcNode") ,  TEXT("fromNode"), TEXT("spell it srcNode") ,  TEXT("sourceNode"), TEXT("spell it srcNode") ,  TEXT("to"), TEXT("spell it dstNode") ,  TEXT("toNode"), TEXT("spell it dstNode") ,  TEXT("destNode"), TEXT("spell it dstNode") ,  TEXT("targetNode"), TEXT("spell it dstNode"), nullptr };
		static const TCHAR* const GMifDescKeys_set_pin_default[] = { TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"), TEXT("pinName"), TEXT("name"), TEXT("value"), TEXT("default"), TEXT("defaultValue"), nullptr };
		static const TCHAR* const GMifDescKeys_splice_into_exec[] = { TEXT("afterNode"), TEXT("insertNode"), TEXT("graphId"), TEXT("afterPin"), TEXT("afterExecOut"), TEXT("insertExecIn"), TEXT("insertIn"), TEXT("execIn"), TEXT("insertExecOut"), TEXT("insertOut"), TEXT("execOut"), nullptr };
		static const TCHAR* const GMifDescNotes_splice_into_exec[] = { TEXT("beforeNode"), TEXT("splice_into_exec inserts AFTER a node — pass afterNode") ,  TEXT("node"), TEXT("this endpoint needs BOTH afterNode and insertNode; there is no single 'node'"), nullptr };
		static const TCHAR* const GMifDescKeys_apply_graph_patch[] = { TEXT("graphId"), TEXT("operations"), TEXT("ops"), TEXT("dryRun"), TEXT("stopOnFirstError"), TEXT("allowPartial"), nullptr };
		static const TCHAR* const GMifDescNotes_apply_graph_patch[] = { TEXT("blueprintId"), TEXT("pass graphId - a patch applies to ONE graph, and node guids are resolved inside it") ,  TEXT("compileAfter"), TEXT("not offered here: compiling inside a multi-edit transaction is the reinstancing crash this codebase guards against. Call compile afterwards.") ,  TEXT("addNode"), TEXT("node creation is not a patch op - create nodes with add_* first, then wire them here. See the file header for why."), nullptr };
		static const TCHAR* const GMifDescKeys_add_pin[] = { TEXT("name"), TEXT("pin"), TEXT("pinName"), TEXT("type"), TEXT("pinType"), TEXT("container"), TEXT("valueType"), TEXT("direction"), TEXT("dir"), TEXT("default"), TEXT("defaultValue"), TEXT("value"), TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("blueprintId"), TEXT("path"), TEXT("function"), TEXT("functionName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_pin[] = { TEXT("confirm"), TEXT("add_pin is additive and needs no confirm; remove_pin is the one that does"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_pin[] = { TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"), TEXT("pinName"), TEXT("name"), TEXT("direction"), TEXT("dir"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_add_custom_event[] = { TEXT("graphId"), TEXT("name"), TEXT("inputs"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_custom_event[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("outputs"), TEXT("a custom event's parameters ARE its output pins - list them under inputs; there is no outputs key here (create_function is the endpoint that has both)") ,  TEXT("params"), TEXT("spell it inputs") ,  TEXT("parameters"), TEXT("spell it inputs") ,  TEXT("eventName"), TEXT("spell it name"), nullptr };
		static const TCHAR* const GMifDescKeys_add_enhanced_input_action[] = { TEXT("graphId"), TEXT("inputAction"), TEXT("action"), TEXT("actionPath"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_enhanced_input_action[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("blueprintId"), TEXT("this endpoint places a node in a GRAPH - pass graphId (list_graphs shows every graph by its full dotted path)") ,  TEXT("inputActionPath"), TEXT("spell it inputAction (aliases: action, actionPath)") ,  TEXT("class"), TEXT("pass the UInputAction ASSET path as inputAction, not a class") ,  TEXT("trigger"), TEXT("the Triggered/Started/Ongoing/Canceled/Completed exec pins are generated from the action - place the node, then connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_list_input_mappings[] = { TEXT("path"), TEXT("context"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_list_input_mappings[] = { TEXT("action"), TEXT("this lists a CONTEXT's mappings; to find one action's bindings, read them all and filter on the action field") ,  TEXT("player"), TEXT("this reads the ASSET, not a live player's applied contexts - those exist only during PIE"), nullptr };
		static const TCHAR* const GMifDescKeys_map_input_key[] = { TEXT("context"), TEXT("path"), TEXT("assetPath"), TEXT("action"), TEXT("key"), nullptr };
		static const TCHAR* const GMifDescNotes_map_input_key[] = { TEXT("triggers"), TEXT("not accepted yet - trigger and modifier classes are a ") TEXT("second pass; this maps action to key") ,  TEXT("modifiers"), TEXT("not accepted yet - see triggers") ,  TEXT("rebuild"), TEXT("not a parameter - the rebuild is ALWAYS issued after the ") TEXT("mapping, because MapKey issues its own BEFORE adding and so ") TEXT("misses the new mapping entirely"), nullptr };
		static const TCHAR* const GMifDescKeys_unmap_input_key[] = { TEXT("context"), TEXT("path"), TEXT("assetPath"), TEXT("action"), TEXT("key"), TEXT("all"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_unmap_input_key[] = { TEXT("clear"), TEXT("spell it all:true, and it needs confirm:true as well"), nullptr };
		static const TCHAR* const GMifDescKeys_list_legacy_input_mappings[] = { TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_list_legacy_input_mappings[] = { TEXT("context"), TEXT("legacy input has no contexts - that is Enhanced Input. Use ") TEXT("list_input_mappings for an InputMappingContext."), nullptr };
		static const TCHAR* const GMifDescKeys_map_legacy_input[] = { TEXT("name"), TEXT("key"), TEXT("axis"), TEXT("scale"), TEXT("shift"), TEXT("ctrl"), TEXT("alt"), TEXT("cmd"), nullptr };
		static const TCHAR* const GMifDescNotes_map_legacy_input[] = { TEXT("context"), TEXT("legacy input has no contexts - use map_input_key for Enhanced ") TEXT("Input") ,  TEXT("action"), TEXT("spell it `name`, and it is a bare name here, not an asset path") ,  TEXT("save"), TEXT("this only edits memory. Persisting to Config/DefaultInput.ini is ") TEXT("save_input_settings, which is separate because it writes to disk"), nullptr };
		static const TCHAR* const GMifDescKeys_unmap_legacy_input[] = { TEXT("name"), TEXT("key"), TEXT("axis"), TEXT("scale"), TEXT("shift"), TEXT("ctrl"), TEXT("alt"), TEXT("cmd"), nullptr };
		static const TCHAR* const GMifDescNotes_unmap_legacy_input[] = { TEXT("all"), TEXT("not supported - legacy mappings are project-wide settings, not a ") TEXT("scratch container, so there is no bulk clear here on purpose"), nullptr };
		static const TCHAR* const GMifDescKeys_save_input_settings[] = { TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_save_input_settings[] = { TEXT("path"), TEXT("not selectable - SaveKeyMappings writes the project's own " "DefaultInput.ini and takes no path"), nullptr };
		static const TCHAR* const GMifDescKeys_list_settings[] = { TEXT("container"), TEXT("category"), TEXT("nameContains"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_settings[] = { TEXT("section"), TEXT("that is an OUTPUT field - filter with nameContains or category " "and read the section off each row") ,  TEXT("value"), TEXT("this lists settings CLASSES; read a value with get_property " "{objectPath: <the cdoPath from a row>, propertyPath: ...}"), nullptr };
		static const TCHAR* const GMifDescKeys_add_pcg_node[] = { TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("settingsClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_pcg_node[] = { TEXT("title"), TEXT("a node's title is display text derived from its settings; the " "settings CLASS is its stable identity and is what this takes") ,  TEXT("node"), TEXT("that is an OUTPUT - the new node's name is returned to you"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_pcg_node[] = { TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("node"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_pcg_node[] = { TEXT("settingsClass"), TEXT("that identifies a node TYPE, and a graph can hold many " "of one type - address the one you mean by node name"), nullptr };
		static const TCHAR* const GMifDescKeys_connect_pcg_nodes[] = { TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("fromNode"), TEXT("fromPin"), TEXT("toNode"), TEXT("toPin"), nullptr };
		static const TCHAR* const GMifDescNotes_connect_pcg_nodes[] = { TEXT("index"), TEXT("pins are addressed by LABEL, not position - describe_pcg_graph " "reports the labels"), nullptr };
		static const TCHAR* const GMifDescKeys_disconnect_pcg_nodes[] = { TEXT("graph"), TEXT("path"), TEXT("assetPath"), TEXT("fromNode"), TEXT("fromPin"), TEXT("toNode"), TEXT("toPin"), nullptr };
		static const TCHAR* const GMifDescNotes_disconnect_pcg_nodes[] = { TEXT("all"), TEXT("not supported - name the edge. Removing every edge on a node is " "what remove_pcg_node does, and it says how many it will destroy"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_physics_asset[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_physics_asset[] = { TEXT("boneName"), TEXT("this describes the whole asset; every body is listed with its " "bone name and index"), nullptr };
		static const TCHAR* const GMifDescKeys_add_physics_body[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("boneName"), TEXT("geomType"), TEXT("minBoneSize"), nullptr };
		static const TCHAR* const GMifDescNotes_add_physics_body[] = { TEXT("autoFit"), TEXT("not offered - FPhysicsAssetUtils::CreateFromSkeletalMesh puts " "up an FScopedSlowTask MakeDialog, and a modal deadlocks the " "bridge because handlers run inline on the ticker that would " "have to service it"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_physics_body[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("boneName"), TEXT("index"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_add_physics_constraint[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("bone1"), TEXT("bone2"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_add_physics_constraint[] = { TEXT("limits"), TEXT("the swing/twist limits are ordinary UPROPERTYs on the " "constraint's DefaultInstance - create it here, then tune it " "with set_property"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_physics_constraint[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("index"), TEXT("jointName"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_set_physics_body_collision[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("boneA"), TEXT("boneB"), TEXT("indexA"), TEXT("indexB"), TEXT("enabled"), nullptr };
		static const TCHAR* const GMifDescNotes_set_physics_body_collision[] = { TEXT("primitiveIndex"), TEXT("the per-PRIMITIVE variant is not offered: " "UPhysicsAsset::SetPrimitiveCollision's own ensure " "compares a per-type index against the TOTAL element " "count, so a valid-looking call can index past the end " "of a per-type array. This endpoint is the body-PAIR " "table, which has no such defect"), nullptr };
		static const TCHAR* const GMifDescKeys_set_physics_primitive_collision[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("boneName"), TEXT("index"), TEXT("primitiveType"), TEXT("primitiveIndex"), TEXT("collisionEnabled"), nullptr };
		static const TCHAR* const GMifDescNotes_set_physics_primitive_collision[] = { TEXT("enabled"), TEXT("collision here is four-valued, not a bool - pass " "collisionEnabled:\"NoCollision\" or \"QueryAndPhysics\". The " "boolean body-PAIR table is set_physics_body_collision"), nullptr };
		static const TCHAR* const GMifDescKeys_add_socket[] = { TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("name"), TEXT("bone"), TEXT("boneName"), TEXT("location"), TEXT("rotation"), TEXT("scale"), TEXT("target"), nullptr };
		static const TCHAR* const GMifDescNotes_add_socket[] = { TEXT("index"), TEXT("that is an OUTPUT - list_sockets reports each socket's index, " "and set_property/edit_container use it to move or delete one") ,  TEXT("parent"), TEXT("spell it `bone` - a socket attaches to a BONE, not to another " "socket"), nullptr };
		static const TCHAR* const GMifDescKeys_run_retarget[] = { TEXT("retargeter"), TEXT("path"), TEXT("assetPath"), TEXT("animations"), TEXT("sourceMesh"), TEXT("targetMesh"), TEXT("prefix"), TEXT("suffix"), TEXT("search"), TEXT("replace"), TEXT("remapReferencedAssets"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_run_retarget[] = { TEXT("destination"), TEXT("not offered, because DuplicateAndRetarget cannot honour " "it - it hard-codes the destination to the TARGET MESH's " "package (IKRetargetBatchOperation.cpp:107). Accepting the " "parameter and writing somewhere else would be worse than " "not having it") ,  TEXT("overwrite"), TEXT("not offered - 5.3's DuplicateAndRetarget has no overwrite " "concept at all (the parameter only exists from 5.7), so the " "two engines would behave differently on a name collision"), nullptr };
		static const TCHAR* const GMifDescKeys_add_virtual_bone[] = { TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("source"), TEXT("sourceBone"), TEXT("target"), TEXT("targetBone"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_add_virtual_bone[] = { TEXT("parent"), TEXT("spell it `source` - a virtual bone is defined by the bone it is " "measured FROM and the bone it is measured TO"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_virtual_bone[] = { TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("name"), TEXT("names"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_virtual_bone[] = { TEXT("all"), TEXT("not supported - name them. Removing every virtual bone would " "silently rewire the whole set through the reparenting rule"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_virtual_bone[] = { TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("name"), TEXT("newName"), nullptr };
		static const TCHAR* const GMifDescKeys_add_anim_curve[] = { TEXT("assetPath"), TEXT("path"), TEXT("animation"), TEXT("name"), TEXT("type"), nullptr };
		static const TCHAR* const GMifDescNotes_add_anim_curve[] = { TEXT("keys"), TEXT("this DECLARES the curve; set_anim_curve_keys puts keys in it") ,  TEXT("value"), TEXT("same - a curve is a track, not a single value"), nullptr };
		static const TCHAR* const GMifDescKeys_set_anim_curve_keys[] = { TEXT("assetPath"), TEXT("path"), TEXT("animation"), TEXT("name"), TEXT("type"), TEXT("keys"), TEXT("append"), nullptr };
		static const TCHAR* const GMifDescNotes_set_anim_curve_keys[] = { TEXT("clear"), TEXT("inverted and renamed: this REPLACES by default, so pass " "append:true to add to what is there instead") ,  TEXT("times"), TEXT("pass keys:[{time,value}] - parallel arrays get out of step and " "there is no way to notice"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_anim_curve[] = { TEXT("assetPath"), TEXT("path"), TEXT("animation"), TEXT("name"), TEXT("type"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_anim_curve[] = { TEXT("all"), TEXT("not supported - name the curve. Removing every curve at once is " "not something this should make easy"), nullptr };
		static const TCHAR* const GMifDescKeys_lighting_build_status[] = { nullptr };
		static const TCHAR* const GMifDescNotes_lighting_build_status[] = { TEXT("build"), TEXT("this endpoint only READS. Start a build with " "invoke_editor_command {context:\"LevelEditor\", " "command:\"BuildLightingOnly\"} - it already exists, and a " "second way to do it would be one too many") ,  TEXT("wait"), TEXT("not offered - a Lightmass build takes minutes and blocking the " "bridge on it would stall every other call. Poll this instead"), nullptr };
		static const TCHAR* const GMifDescKeys_move_actors_to_level[] = { TEXT("actorPaths"), TEXT("actors"), TEXT("level"), TEXT("sublevel"), TEXT("allOrFail"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_move_actors_to_level[] = { TEXT("folder"), TEXT("not a selector here - list_level_actors filters, and its " "actorPath values are what this takes") ,  TEXT("copy"), TEXT("this MOVES. CopyOrMoveActorsToLevel's copy half is a separate " "verb and is not offered yet"), nullptr };
		static const TCHAR* const GMifDescKeys_list_level_instances[] = { TEXT("includeActors"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_level_instances[] = { TEXT("worldAsset"), TEXT("that is an OUTPUT - every row reports which level asset " "the placement points at"), nullptr };
		static const TCHAR* const GMifDescKeys_set_level_instance_loaded[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("loaded"), nullptr };
		static const TCHAR* const GMifDescNotes_set_level_instance_loaded[] = { TEXT("visible"), TEXT("loading is not visibility - an unloaded instance has no " "actors at all. set_property on the actor for visibility"), nullptr };
		static const TCHAR* const GMifDescKeys_edit_level_instance[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("action"), TEXT("discardEdits"), nullptr };
		static const TCHAR* const GMifDescNotes_edit_level_instance[] = { TEXT("save"), TEXT("committing already writes the level instance's package. There " "is no separate save here, and this endpoint will not add one"), nullptr };
		static const TCHAR* const GMifDescKeys_break_level_instance[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("levels"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_break_level_instance[] = { TEXT("keep"), TEXT("breaking always consumes the level instance actor - there is no " "variant that keeps it"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_foliage_instances[] = { TEXT("foliageType"), TEXT("type"), TEXT("indices"), TEXT("sphere"), TEXT("box"), TEXT("all"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_foliage_instances[] = { TEXT("mesh"), TEXT("foliage is keyed by TYPE, not by mesh - list_foliage_instances " "reports the type path to pass here") ,  TEXT("actorPath"), TEXT("instances are not actors. If you placed a standalone HISM " "holder with add_foliage_instances{mesh}, that is an ACTOR " "and delete_level_actor removes it - this endpoint is for painted " "foliage in the level's InstancedFoliageActor"), nullptr };
		static const TCHAR* const GMifDescKeys_source_control[] = { TEXT("path"), TEXT("packagePath"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_source_control[] = { TEXT("paths"), TEXT("one path at a time. QueryFileState blocks the game thread on a " "server round-trip, and the plural QueryFileStates does not " "exist before 5.6 - so a batch here would be a loop of blocking " "calls, which is exactly what should not be offered") ,  TEXT("checkout"), TEXT("this endpoint only READS. source_control_checkout is the " "write half"), nullptr };
		static const TCHAR* const GMifDescKeys_source_control_checkout[] = { TEXT("path"), TEXT("packagePath"), TEXT("assetPath"), TEXT("action"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_source_control_checkout[] = { TEXT("submit"), TEXT("checking in is deliberately not offered - it publishes work to " "everyone on the team, which is a person's decision") ,  TEXT("paths"), TEXT("one path at a time here; a batch would be a loop of blocking " "server calls on the game thread"), nullptr };
		static const TCHAR* const GMifDescKeys_list_redirectors[] = { TEXT("pathPrefix"), TEXT("path"), TEXT("paths"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_redirectors[] = { TEXT("dryRun"), TEXT("this endpoint IS the dry run - it only reads. " "fixup_redirectors is the half that acts") ,  TEXT("confirm"), TEXT("nothing here changes anything, so there is nothing to " "confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_fixup_redirectors[] = { TEXT("pathPrefix"), TEXT("path"), TEXT("paths"), TEXT("keepRedirectors"), TEXT("confirm"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_fixup_redirectors[] = { TEXT("dryRun"), TEXT("split off into list_redirectors, which is not gated - so the " "dry run stays available in every write mode while this half " "does not") ,  TEXT("deleteRedirectors"), TEXT("inverted and renamed: deleting fixed-up redirectors " "is the DEFAULT, so pass keepRedirectors:true to " "leave them") ,  TEXT("recursive"), TEXT("pathPrefix is always recursive - it is a prefix, not a " "folder listing"), nullptr };
		static const TCHAR* const GMifDescKeys_get_asset_tags[] = { TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), nullptr };
		static const TCHAR* const GMifDescNotes_get_asset_tags[] = { TEXT("load"), TEXT("nothing is loaded, deliberately - that is the whole point of " "reading the registry rather than the asset. get_property loads " "and reads real values") ,  TEXT("tags"), TEXT("that is an OUTPUT here. To FILTER on tags, pass tags:{...} to " "find_assets"), nullptr };
		static const TCHAR* const GMifDescKeys_check_consolidate_assets[] = { TEXT("target"), TEXT("targetPath"), TEXT("sources"), nullptr };
		static const TCHAR* const GMifDescNotes_check_consolidate_assets[] = { TEXT("confirm"), TEXT("nothing here changes anything, so there is nothing to " "confirm. consolidate_assets is the half that acts") ,  TEXT("dryRun"), TEXT("this endpoint IS the dry run") ,  TEXT("deleteSources"), TEXT("that is consolidate_assets' parameter - this only " "reports what would happen"), nullptr };
		static const TCHAR* const GMifDescKeys_consolidate_assets[] = { TEXT("target"), TEXT("targetPath"), TEXT("sources"), TEXT("deleteSources"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_consolidate_assets[] = { TEXT("dryRun"), TEXT("split off into check_consolidate_assets, which is not gated - " "so the preview stays available in every write mode while this " "half does not"), nullptr };
		static const TCHAR* const GMifDescKeys_generate_lods[] = { TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("lodCount"), TEXT("reductionPercentages"), TEXT("screenSizes"), TEXT("autoScreenSize"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_generate_lods[] = { TEXT("lodGroup"), TEXT("already reachable - set_property on LODGroup, which the " "engine special-cases to resize and retune every LOD") ,  TEXT("buildSettings"), TEXT("already reachable through set_property on the per-LOD " "SourceModels structs") ,  TEXT("nanite"), TEXT("already reachable - set_property on NaniteSettings"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_lods[] = { TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_lods[] = { TEXT("lod"), TEXT("there is no remove-one-LOD operation in the engine - " "RemoveLods strips all of them. generate_lods rebuilds a chain " "of the size you want"), nullptr };
		static const TCHAR* const GMifDescKeys_list_collections[] = { TEXT("shareType"), nullptr };
		static const TCHAR* const GMifDescNotes_list_collections[] = { TEXT("name"), TEXT("that is describe_collection, which lists a collection's assets"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_collection[] = { TEXT("name"), TEXT("shareType"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_collection[] = { TEXT("limit"), TEXT("a collection is a hand-made set - if one is large enough to " "need paging, that is worth knowing rather than hiding"), nullptr };
		static const TCHAR* const GMifDescKeys_create_collection[] = { TEXT("name"), TEXT("shareType"), TEXT("paths"), TEXT("assets"), nullptr };
		static const TCHAR* const GMifDescNotes_create_collection[] = { TEXT("confirm"), TEXT("creating an empty named set destroys nothing, so it is not " "gated on confirm. destroy_collection is"), nullptr };
		static const TCHAR* const GMifDescKeys_add_to_collection[] = { TEXT("name"), TEXT("shareType"), TEXT("paths"), TEXT("assets"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_from_collection[] = { TEXT("name"), TEXT("shareType"), TEXT("paths"), TEXT("assets"), nullptr };
		static const TCHAR* const GMifDescKeys_destroy_collection[] = { TEXT("name"), TEXT("shareType"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_get_level_blueprint[] = { TEXT("level"), TEXT("sublevel"), TEXT("create"), nullptr };
		static const TCHAR* const GMifDescNotes_get_level_blueprint[] = { TEXT("blueprintId"), TEXT("that is the OUTPUT - this endpoint exists to tell you " "what it is, because nothing else emits it") ,  TEXT("graph"), TEXT("use the returned blueprintId with list_graphs; every blueprint " "endpoint already works on a Level Blueprint unchanged"), nullptr };
		static const TCHAR* const GMifDescKeys_create_macro[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs"), TEXT("outputs"), nullptr };
		static const TCHAR* const GMifDescNotes_create_macro[] = { TEXT("pure"), TEXT("macros have no pure/impure distinction - that is create_function. " "A macro inlines wherever it is used") ,  TEXT("category"), TEXT("not set here; set_property on the graph's metadata after " "creation if you need one") ,  TEXT("override"), TEXT("a macro cannot override anything - use add_override_event " "for a parent event, or create_function"), nullptr };
		static const TCHAR* const GMifDescKeys_add_k2_node[] = { TEXT("graphId"), TEXT("nodeClass"), TEXT("class"), TEXT("x"), TEXT("y"), TEXT("proxyFactoryFunction"), TEXT("proxyFactoryClass"), TEXT("proxyClass"), TEXT("properties"), nullptr };
		static const TCHAR* const GMifDescNotes_add_k2_node[] = { TEXT("function"), TEXT("for an ordinary function call use add_function_call - it " "resolves overloads and self-context, which this does not") ,  TEXT("pins"), TEXT("pins are allocated by the node itself from its configuration. " "Wire them afterwards with connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_create_event[] = { TEXT("graphId"), TEXT("function"), TEXT("bindNode"), TEXT("bindPin"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_create_event[] = { TEXT("scopeClass"), TEXT("there is no setter for the scope - GetScopeClass derives " "it entirely from what is wired into the Self pin, so a " "scopeClass parameter would be silently ignored. Wire an " "object of that type into Self instead") ,  TEXT("event"), TEXT("spell it function - this wraps a function OR a custom event by " "name, and the parameter covers both") ,  TEXT("delegate"), TEXT("the dispatcher is named on the BIND node; this endpoint only " "needs that node's guid"), nullptr };
		static const TCHAR* const GMifDescKeys_set_enum_value[] = { TEXT("enum"), TEXT("enumPath"), TEXT("path"), TEXT("index"), TEXT("value"), TEXT("displayName"), TEXT("newName"), TEXT("moveTo"), TEXT("bitflags"), nullptr };
		static const TCHAR* const GMifDescNotes_set_enum_value[] = { TEXT("add"), TEXT("add_enum_value creates an entry; this changes an existing one") ,  TEXT("remove"), TEXT("remove_enum_value deletes one") ,  TEXT("order"), TEXT("spell it moveTo - the target INDEX, not an ordering"), nullptr };
		static const TCHAR* const GMifDescKeys_set_niagara_emitter[] = { TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("emitter"), TEXT("enabled"), TEXT("recompile"), nullptr };
		static const TCHAR* const GMifDescNotes_set_niagara_emitter[] = { TEXT("add"), TEXT("adding an emitter is not offered here. AddEmitterHandle reaches " "UNGUARDED dereferences of GraphSource and ParentScratchPads " "(NiagaraEmitter.cpp:1119-1120), both editor-only fields that are " "NULL on any emitter from a cooked project - so it needs a " "source-emitter precondition of its own, plus a version guard, " "because 5.6 and 5.7 renamed the branch field this depends on") ,  TEXT("remove"), TEXT("same - and RemoveEmitterHandle vs RemoveEmitterHandlesById " "differ in whether system parameters are cleaned up") ,  TEXT("index"), TEXT("emitters are addressed by NAME here, because an index shifts " "when anything is added or removed"), nullptr };
		static const TCHAR* const GMifDescKeys_add_niagara_emitter[] = { TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("emitter"), TEXT("name"), TEXT("enabled"), nullptr };
		static const TCHAR* const GMifDescNotes_add_niagara_emitter[] = { TEXT("index"), TEXT("a new emitter is appended; handles are addressed by NAME") ,  TEXT("version"), TEXT("the source's exposed version is used - GetEmitterData returns " "null SILENTLY for an unknown version guid, so accepting one " "would mean accepting a wrong-data outcome"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_niagara_emitter[] = { TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("emitter"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_niagara_emitter[] = { TEXT("index"), TEXT("an index shifts when anything is added or removed; remove by " "NAME") ,  TEXT("confirm"), TEXT("this is an undoable asset edit, not a deletion - it needs no " "confirm, and nothing is saved"), nullptr };
		static const TCHAR* const GMifDescKeys_add_make_struct[] = { TEXT("graphId"), TEXT("structName"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_make_struct[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("struct"), TEXT("spell it structName") ,  TEXT("name"), TEXT("the struct is named by structName; resolve_struct is the endpoint whose parameter is called name") ,  TEXT("type"), TEXT("spell it structName"), nullptr };
		static const TCHAR* const GMifDescKeys_add_break_struct[] = { TEXT("graphId"), TEXT("structName"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_break_struct[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("struct"), TEXT("spell it structName") ,  TEXT("name"), TEXT("the struct is named by structName; resolve_struct is the endpoint whose parameter is called name") ,  TEXT("type"), TEXT("spell it structName"), nullptr };
		static const TCHAR* const GMifDescKeys_add_self[] = { TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_self[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("blueprintId"), TEXT("a node is placed in a GRAPH - pass graphId (list_graphs shows every graph of a blueprint); the owning blueprint is inferred from it"), nullptr };
		static const TCHAR* const GMifDescKeys_add_literal[] = { TEXT("graphId"), TEXT("object"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_literal[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("value"), TEXT("add_literal makes an OBJECT-reference literal only - for a scalar (int/float/bool/string/name) place the consuming node and use set_pin_default on its pin instead") ,  TEXT("path"), TEXT("the asset path goes in object") ,  TEXT("objectPath"), TEXT("spell it object") ,  TEXT("asset"), TEXT("spell it object") ,  TEXT("type"), TEXT("the literal's type comes from the resolved object's class; there is nothing to declare"), nullptr };
		static const TCHAR* const GMifDescKeys_create_function[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs"), TEXT("outputs"), TEXT("pure"), nullptr };
		static const TCHAR* const GMifDescNotes_create_function[] = { TEXT("override"), TEXT("create_function makes a NEW function; it cannot override. Use add_override_event {event, parentClass?, callParent?} — naming a parent's function here creates a COLLIDING duplicate that fails to compile") ,  TEXT("parentClass"), TEXT("create_function does not take a parent class. add_override_event accepts parentClass (aliases: class, interfaceOrParent, ownerClass, targetClass)") ,  TEXT("interface"), TEXT("to implement an interface function use implement_interface_function; to override a parent event use add_override_event") ,  TEXT("event"), TEXT("events live in the event graph — use add_custom_event for a new one, or add_override_event to override a parent's"), nullptr };
		static const TCHAR* const GMifDescKeys_set_function_flags[] = { TEXT("blueprintId"), TEXT("path"), TEXT("graphId"), TEXT("function"), TEXT("functionName"), TEXT("name"), TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("replicates"), TEXT("reliable"), TEXT("access"), TEXT("pure"), TEXT("const"), TEXT("isConst"), TEXT("callInEditor"), TEXT("category"), TEXT("tooltip"), TEXT("keywords"), nullptr };
		static const TCHAR* const GMifDescNotes_set_function_flags[] = { TEXT("replication"), TEXT("spell it replicates (none | multicast | server | client)") ,  TEXT("net"), TEXT("read-only in the response - set the mode with replicates; FUNC_Net is derived from it and cannot be set on its own") ,  TEXT("static"), TEXT("read-only in the response - a Blueprint function's static-ness is not editable here") ,  TEXT("authorityOnly"), TEXT("read-only in the response - not settable through this endpoint") ,  TEXT("flags"), TEXT("pass each flag as a TOP-LEVEL key (replicates, reliable, access, pure, const, callInEditor, category, tooltip, keywords); the response's 'flags' object is read-back only") ,  TEXT("event"), TEXT("address a custom event by nodeGuid (aliases: node, guid, nodeId); a function graph by graphId or blueprintId + function"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_function[] = { TEXT("graphId"), TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("function"), TEXT("name"), TEXT("newName"), TEXT("to"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_function[] = { TEXT("from"), TEXT("the current name is oldName (aliases: function, name) - only the destination has a short spelling ('to' = newName)") ,  TEXT("graph"), TEXT("spell it graphId") ,  TEXT("newFunctionName"), TEXT("spell it newName (alias: to)") ,  TEXT("dispatcher"), TEXT("an event dispatcher is a signature graph PLUS a backing delegate variable - use rename_event_dispatcher, which renames both"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_event[] = { TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("newName"), TEXT("name"), TEXT("to"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_event[] = { TEXT("oldName"), TEXT("rename_event addresses the event by nodeGuid, not by its current name - only the new name is passed (newName, aliases: name, to)") ,  TEXT("from"), TEXT("rename_event addresses the event by nodeGuid; the destination is newName (aliases: name, to)") ,  TEXT("event"), TEXT("address the custom event by nodeGuid (aliases: node, guid, nodeId) - find_nodes locates it") ,  TEXT("blueprintId"), TEXT("the owning blueprint is inferred from the node; pass graphId if the same guid exists in more than one loaded copy"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_event_dispatcher[] = { TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("name"), TEXT("dispatcher"), TEXT("newName"), TEXT("to"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_event_dispatcher[] = { TEXT("from"), TEXT("the current name is oldName (aliases: name, dispatcher) - only the destination has a short spelling ('to' = newName)") ,  TEXT("graphId"), TEXT("a dispatcher is a signature GRAPH plus a backing delegate VARIABLE - it is addressed by blueprintId + oldName so both halves can be renamed together"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_event_dispatcher[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_event_dispatcher[] = { TEXT("dispatcher"), TEXT("this endpoint's key is 'name'") ,  TEXT("force"), TEXT("the required acknowledgement is confirm:true") ,  TEXT("graphId"), TEXT("remove_event_dispatcher matches by NAME on the given blueprint - it does not take a graphId"), nullptr };
		static const TCHAR* const GMifDescKeys_create_blueprint[] = { TEXT("path"), TEXT("parentClass"), TEXT("blueprintType"), TEXT("skeleton"), TEXT("targetSkeleton"), nullptr };
		static const TCHAR* const GMifDescNotes_create_blueprint[] = { TEXT("overwrite"), TEXT("NOT supported — this endpoint refuses to clobber an existing asset. delete_asset the old one first, or pick a new path") ,  TEXT("name"), TEXT("the asset name is the last segment of path") ,  TEXT("parent"), TEXT("the base class parameter is called parentClass"), nullptr };
		static const TCHAR* const GMifDescKeys_reparent_blueprint[] = { TEXT("blueprintId"), TEXT("path"), TEXT("newParentClass"), TEXT("parentClass"), nullptr };
		static const TCHAR* const GMifDescNotes_reparent_blueprint[] = { TEXT("newParent"), TEXT("spell it newParentClass (alias parentClass)") ,  TEXT("class"), TEXT("the new parent class parameter is called newParentClass"), nullptr };
		static const TCHAR* const GMifDescKeys_resolve_struct[] = { TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_resolve_struct[] = { TEXT("structName"), TEXT("resolve_struct spells it name; structName is what add_make_struct/add_break_struct use") ,  TEXT("struct"), TEXT("spell it name") ,  TEXT("path"), TEXT("pass the path as the VALUE of name - name accepts a bare name or a full struct path in the same field"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_class[] = { TEXT("class"), TEXT("className"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescKeys_list_enum_values[] = { TEXT("enum"), TEXT("enumName"), nullptr };
		static const TCHAR* const GMifDescKeys_list_mounted_containers[] = { nullptr };
		static const TCHAR* const GMifDescKeys_find_assets[] = { TEXT("class"), TEXT("className"), TEXT("type"), TEXT("pathPrefix"), TEXT("nameContains"), TEXT("origin"), TEXT("recursiveClasses"), TEXT("limit"), TEXT("tags"), TEXT("includeTags"), nullptr };
		static const TCHAR* const GMifDescNotes_find_assets[] = { TEXT("recursive"), TEXT("not implemented - pathPrefix matching is ALWAYS recursive; recursiveClasses controls class-hierarchy matching") ,  TEXT("tag"), TEXT("spell it tags:{...} - a map, because filtering on one tag and reading another is the common case") ,  TEXT("minWidth"), TEXT("registry tag matching is exact STRING equality, so numeric comparisons are not a filter axis. Texture2D's Dimensions tag is a formatted \"1024x1024\" string - pass includeTags:true and compare in the caller"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_package[] = { TEXT("package"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_diagnose_landscape[] = { TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_diagnose_landscape_draws[] = { TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_recipe_add_debug_print[] = { TEXT("graphId"), TEXT("message"), TEXT("functionName"), TEXT("messageParam"), TEXT("afterNode"), TEXT("afterPin"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_add_debug_print[] = { TEXT("blueprintId"), TEXT("the print node lands in ONE graph - pass graphId from list_graphs, not the blueprint path") ,  TEXT("text"), TEXT("the printed string is 'message'") ,  TEXT("nodeGuid"), TEXT("the splice anchor is 'afterNode' - this endpoint creates its own node, it does not edit one"), nullptr };
		static const TCHAR* const GMifDescKeys_recipe_reset_and_loop[] = { TEXT("graphId"), TEXT("arrayVar"), TEXT("indexVar"), TEXT("scoreVar"), TEXT("indexInit"), TEXT("scoreInit"), TEXT("afterNode"), TEXT("afterPin"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_reset_and_loop[] = { TEXT("blueprintId"), TEXT("this recipe builds nodes in ONE graph - pass graphId from list_graphs, not the blueprint path") ,  TEXT("array"), TEXT("the array variable NAME is 'arrayVar'") ,  TEXT("index"), TEXT("'indexVar' names the variable; 'indexInit' is the value it is reset to"), nullptr };
		static const TCHAR* const GMifDescKeys_recipe_splice_before_parent[] = { TEXT("graphId"), TEXT("parentNode"), TEXT("clusterEntry"), TEXT("clusterExit"), TEXT("clusterEntryExecIn"), TEXT("clusterExitExecOut"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_splice_before_parent[] = { TEXT("node"), TEXT("three DISTINCT nodes are required here - parentNode, clusterEntry, clusterExit - so there is no generic 'node' alias") ,  TEXT("parentNodeGuid"), TEXT("spelled 'parentNode' on this endpoint (add_override_event RETURNS it as parentNodeGuid)") ,  TEXT("entryNode"), TEXT("spelled 'clusterEntry'") ,  TEXT("exitNode"), TEXT("spelled 'clusterExit'"), nullptr };
		static const TCHAR* const GMifDescKeys_recipe_argmax_over_components[] = { TEXT("graphId"), TEXT("loopBodyNode"), TEXT("loopBodyPin"), TEXT("scoreNode"), TEXT("scorePin"), TEXT("indexNode"), TEXT("indexPin"), TEXT("bestScoreVar"), TEXT("bestIndexVar"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_argmax_over_components[] = { TEXT("node"), TEXT("three DISTINCT nodes are required here - loopBodyNode, scoreNode, indexNode - so there is no generic 'node' alias") ,  TEXT("forEachNode"), TEXT("spelled 'loopBodyNode' here (recipe_reset_and_loop returns that guid as forEachNode)") ,  TEXT("blueprintId"), TEXT("this recipe builds nodes in ONE graph - pass graphId from list_graphs, not the blueprint path"), nullptr };
		static const TCHAR* const GMifDescKeys_read_modloader_log[] = { TEXT("path"), TEXT("lines"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_read_modloader_log[] = { TEXT("logPath"), TEXT("spell it path - or omit it entirely to tail the live DDS2 UE4SS.log") ,  TEXT("file"), TEXT("spell it path") ,  TEXT("maxLines"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") ,  TEXT("limit"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") ,  TEXT("tail"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") ,  TEXT("contains"), TEXT("spell it filter - a plain substring match, not a regex") ,  TEXT("search"), TEXT("spell it filter - a plain substring match, not a regex"), nullptr };
		static const TCHAR* const GMifDescKeys_read_engine_log[] = { TEXT("lines"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_read_engine_log[] = { TEXT("path"), TEXT("not accepted here - this always reads the current process's own ") TEXT("Output Log. Use read_modloader_log if you need to read a DIFFERENT log file by path") ,  TEXT("maxLines"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") ,  TEXT("limit"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") ,  TEXT("tail"), TEXT("spell it lines - it is the tail size, clamped to 1-5000") ,  TEXT("contains"), TEXT("spell it filter - a plain substring match, not a regex") ,  TEXT("search"), TEXT("spell it filter - a plain substring match, not a regex"), nullptr };
		static const TCHAR* const GMifDescKeys_trigger_cook[] = { TEXT("mod"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescNotes_trigger_cook[] = { TEXT("modName"), TEXT("spell it mod") ,  TEXT("assetPath"), TEXT("spell it asset - it is substituted into the retoc --filter argument") ,  TEXT("path"), TEXT("spell it asset - it is substituted into the retoc --filter argument") ,  TEXT("confirm"), TEXT("trigger_cook is plan-only and runs nothing, so there is nothing to confirm") ,  TEXT("execute"), TEXT("trigger_cook is plan-only by design - run the returned plan yourself, out-of-editor"), nullptr };
		static const TCHAR* const GMifDescKeys_add_timeline[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("floatTracks"), TEXT("length"), TEXT("autoPlay"), TEXT("loop"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_timeline[] = { TEXT("graphId"), TEXT("add_timeline takes a blueprintId, not a graphId - the node is placed in the blueprint's own event graph") ,  TEXT("tracks"), TEXT("spell it floatTracks (an array of non-empty track name strings)") ,  TEXT("timelineName"), TEXT("spell it name; omit it entirely for an auto-generated unique name") ,  TEXT("curve"), TEXT("a UCurveFloat is created per entry in floatTracks; you cannot supply one here"), nullptr };
		static const TCHAR* const GMifDescKeys_add_reroute[] = { TEXT("graphId"), TEXT("x"), TEXT("y"), TEXT("srcNode"), TEXT("srcPin"), TEXT("dstNode"), TEXT("dstPin"), nullptr };
		static const TCHAR* const GMifDescNotes_add_reroute[] = { TEXT("knot"), TEXT("a reroute IS a knot - this endpoint makes one; there is no separate 'knot' parameter") ,  TEXT("between"), TEXT("name the link explicitly with srcNode + srcPin + dstNode + dstPin"), nullptr };
		static const TCHAR* const GMifDescKeys_add_widget_animation[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("startTime"), TEXT("endTime"), TEXT("displayRate"), nullptr };
		static const TCHAR* const GMifDescNotes_add_widget_animation[] = { TEXT("fps"), TEXT("the parameter is displayRate, in frames per second") ,  TEXT("duration"), TEXT("give endTime instead — the range is start..end, not a length") ,  TEXT("tickResolution"), TEXT("not settable here; the engine's default is used and list_widget_animations reports it, because keys are authored in TICK space"), nullptr };
		static const TCHAR* const GMifDescKeys_list_widget_animations[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_widget_animations[] = { TEXT("animationName"), TEXT("this lists them all — there is no single-animation read; the listing carries the full detail for each"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_tree_widget[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("name"), TEXT("newName"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_tree_widget[] = { TEXT("oldName"), TEXT("the widget to rename is 'widgetName'; 'newName' is what to call it") ,  TEXT("rename"), TEXT("the parameter is newName"), nullptr };
		static const TCHAR* const GMifDescKeys_add_widget_animation_track[] = { TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), TEXT("widgetName"), TEXT("property"), nullptr };
		static const TCHAR* const GMifDescNotes_add_widget_animation_track[] = { TEXT("propertyPath"), TEXT("the parameter is 'property'") ,  TEXT("channel"), TEXT("a track carries BOTH translation channels; pick X or Y when you key it, in set_widget_animation_keys") ,  TEXT("widgetGuid"), TEXT("widgets are addressed by name here — list_tree_widgets shows them"), nullptr };
		static const TCHAR* const GMifDescKeys_set_widget_animation_keys[] = { TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), TEXT("widgetName"), TEXT("property"), TEXT("channel"), TEXT("keys"), TEXT("replace"), nullptr };
		static const TCHAR* const GMifDescNotes_set_widget_animation_keys[] = { TEXT("time"), TEXT("times go inside keys[], one per key, in seconds") ,  TEXT("tangent"), TEXT("interp:\"cubic\" uses the engine's Auto tangent, which is what the UMG designer produces") ,  TEXT("frame"), TEXT("keys are given in SECONDS and converted to tick space for you; list_widget_animations reports both"), nullptr };
		static const TCHAR* const GMifDescKeys_set_widget_animation_range[] = { TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), TEXT("startTime"), TEXT("endTime"), TEXT("displayRate"), nullptr };
		static const TCHAR* const GMifDescNotes_set_widget_animation_range[] = { TEXT("length"), TEXT("give endTime; the range is absolute, not a duration") ,  TEXT("keys"), TEXT("this changes the RANGE only - key times are untouched. Use set_widget_animation_keys to move keys"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_widget_animation[] = { TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_widget_animation[] = { TEXT("confirm"), TEXT("not needed — this is an undoable blueprint edit, not an asset deletion") ,  TEXT("name"), TEXT("the parameter is animationName, to match add_widget_animation_track and set_widget_animation_keys"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_widget_animation_track[] = { TEXT("blueprintId"), TEXT("path"), TEXT("animationName"), TEXT("widgetName"), TEXT("property"), TEXT("removeBinding"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_widget_animation_track[] = { TEXT("channel"), TEXT("a track carries all of a property's channels; there is no per-channel removal — key it empty instead"), nullptr };
		static const TCHAR* const GMifDescKeys_add_class_cast[] = { TEXT("graphId"), TEXT("targetClass"), TEXT("class"), TEXT("castTo"), TEXT("to"), TEXT("targetType"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_class_cast[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("cls"), TEXT("add_cast accepts cls, add_class_cast does not - use targetClass") ,  TEXT("className"), TEXT("add_cast accepts className, add_class_cast does not - use targetClass") ,  TEXT("object"), TEXT("the class value to cast is a pin - place the node, then connect_pins into its input pin"), nullptr };
		static const TCHAR* const GMifDescKeys_add_switch_enum[] = { TEXT("graphId"), TEXT("enumName"), TEXT("hasDefault"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_switch_enum[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("enum"), TEXT("spell it enumName here - list_enum_values takes either, this endpoint reads only enumName") ,  TEXT("cases"), TEXT("the case pins come from the enum's own entries; list them with list_enum_values") ,  TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_switch_int[] = { TEXT("graphId"), TEXT("cases"), TEXT("startIndex"), TEXT("hasDefault"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_switch_int[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("count"), TEXT("spell it cases (the number of case pins to create)") ,  TEXT("caseLabels"), TEXT("an int switch has no labels - pass cases as a count and startIndex as the first value; add_switch_string is the one that takes an array") ,  TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_switch_string[] = { TEXT("graphId"), TEXT("cases"), TEXT("caseSensitive"), TEXT("hasDefault"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_switch_string[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("caseLabels"), TEXT("spell it cases (an array of label strings)") ,  TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_switch_name[] = { TEXT("graphId"), TEXT("cases"), TEXT("hasDefault"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_switch_name[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("caseLabels"), TEXT("spell it cases (an array of label strings)") ,  TEXT("caseSensitive"), TEXT("not settable on a Switch on Name - FName comparison is case-insensitive by construction and UK2Node_SwitchName has no bIsCaseSensitive. add_switch_string does, because FString comparison can be either.") ,  TEXT("type"), TEXT("not a parameter - add_switch_int, add_switch_enum, add_switch_string and add_switch_name are separate endpoints, one per switch type") ,  TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_enum_literal[] = { TEXT("graphId"), TEXT("enumName"), TEXT("value"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_enum_literal[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("enum"), TEXT("spell it enumName here - list_enum_values takes either, this endpoint reads only enumName") ,  TEXT("default"), TEXT("spell it value - and it is the enumerator name, not an index; get the exact text from list_enum_values") ,  TEXT("enumerator"), TEXT("spell it value (the enumerator name from list_enum_values)"), nullptr };
		static const TCHAR* const GMifDescKeys_set_pin_type[] = { TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("pin"), TEXT("pinName"), TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"), nullptr };
		static const TCHAR* const GMifDescKeys_add_event_dispatcher[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs"), nullptr };
		static const TCHAR* const GMifDescNotes_add_event_dispatcher[] = { TEXT("dispatcher"), TEXT("'dispatcher' names an EXISTING dispatcher on add_call_dispatcher/add_bind_dispatcher; the one being created here is named by 'name'") ,  TEXT("params"), TEXT("spell it inputs (the response reports the count back as 'params')") ,  TEXT("parameters"), TEXT("spell it inputs") ,  TEXT("outputs"), TEXT("a dispatcher signature has inputs only — they surface as OUTPUT pins on the bound event") ,  TEXT("graphId"), TEXT("a dispatcher belongs to the blueprint, not to one graph — pass blueprintId"), nullptr };
		static const TCHAR* const GMifDescKeys_add_call_dispatcher[] = { TEXT("graphId"), TEXT("dispatcher"), TEXT("targetClass"), TEXT("x"), TEXT("y"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescNotes_add_call_dispatcher[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("name"), TEXT("the existing dispatcher is named by 'dispatcher'; 'name' is add_event_dispatcher's key for CREATING one") ,  TEXT("dispatcherName"), TEXT("spell it dispatcher") ,  TEXT("blueprintId"), TEXT("graphId already names the blueprint — pass the graph the node lands in") ,  TEXT("target"), TEXT("targetClass names the CLASS that declares the dispatcher; the OBJECT goes into the node's Target/self pin via connect_pins, never here") ,  TEXT("event"), TEXT("the handler is wired into the bind node's Delegate pin — add_custom_event then connect_pins; this endpoint only places the node"), nullptr };
		static const TCHAR* const GMifDescKeys_add_bind_dispatcher[] = { TEXT("graphId"), TEXT("dispatcher"), TEXT("targetClass"), TEXT("x"), TEXT("y"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescNotes_add_bind_dispatcher[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("name"), TEXT("the existing dispatcher is named by 'dispatcher'; 'name' is add_event_dispatcher's key for CREATING one") ,  TEXT("dispatcherName"), TEXT("spell it dispatcher") ,  TEXT("blueprintId"), TEXT("graphId already names the blueprint — pass the graph the node lands in") ,  TEXT("target"), TEXT("targetClass names the CLASS that declares the dispatcher; the OBJECT goes into the node's Target/self pin via connect_pins, never here") ,  TEXT("event"), TEXT("the handler is wired into the bind node's Delegate pin — add_custom_event then connect_pins; this endpoint only places the node"), nullptr };
		static const TCHAR* const GMifDescKeys_list_dispatchers[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_dispatchers[] = { TEXT("graphId"), TEXT("list_dispatchers is blueprint-scoped — pass blueprintId") ,  TEXT("filter"), TEXT("this endpoint takes no filter; it returns every dispatcher on the blueprint"), nullptr };
		static const TCHAR* const GMifDescKeys_add_component[] = { TEXT("actorPath"), TEXT("actor"), TEXT("blueprintId"), TEXT("path"), TEXT("componentClass"), TEXT("class"), TEXT("name"), TEXT("parentName"), TEXT("location"), TEXT("rotation"), TEXT("scale"), nullptr };
		static const TCHAR* const GMifDescNotes_add_component[] = { TEXT("componentName"), TEXT("spell it name - it is the NEW component's variable name") ,  TEXT("component"), TEXT("spell it name for the new component, or parentName for the existing one to attach it under") ,  TEXT("parent"), TEXT("spell it parentName - the EXISTING component the new one is attached under") ,  TEXT("transform"), TEXT("pass location / rotation / scale as separate keys; there is no combined transform key"), nullptr };
		static const TCHAR* const GMifDescKeys_list_components[] = { TEXT("actorPath"), TEXT("actor"), TEXT("blueprintId"), TEXT("path"), TEXT("component"), TEXT("componentName"), TEXT("includeInherited"), TEXT("includeNative"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_component[] = { TEXT("actorPath"), TEXT("actor"), TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_component[] = { TEXT("component"), TEXT("spell it name here - list_components takes 'component', remove_component takes 'name'") ,  TEXT("componentName"), TEXT("spell it name"), nullptr };
		static const TCHAR* const GMifDescKeys_get_inherited_component[] = { TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"), TEXT("componentName"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescKeys_override_inherited_component[] = { TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"), TEXT("componentName"), TEXT("name"), TEXT("properties"), TEXT("props"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_override_inherited_component[] = { TEXT("propertyPath"), TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single dot-path write against the returned overrideTemplatePath") ,  TEXT("value"), TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single named write"), nullptr };
		static const TCHAR* const GMifDescKeys_revert_inherited_component[] = { TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"), TEXT("componentName"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_set_component_transform[] = { TEXT("actorPath"), TEXT("actor"), TEXT("name"), TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("location"), TEXT("rotation"), TEXT("scale"), nullptr };
		static const TCHAR* const GMifDescNotes_set_component_transform[] = { TEXT("component"), TEXT("spell it name here - list_components takes 'component', set_component_transform takes 'name'") ,  TEXT("componentName"), TEXT("spell it name") ,  TEXT("relativeLocation"), TEXT("spell it location - the transform written here is already the RELATIVE one") ,  TEXT("transform"), TEXT("pass location / rotation / scale as separate keys; there is no combined transform key"), nullptr };
		static const TCHAR* const GMifDescKeys_add_interface[] = { TEXT("blueprintId"), TEXT("path"), TEXT("interface"), TEXT("interfaceClass"), TEXT("class"), nullptr };
		static const TCHAR* const GMifDescNotes_add_interface[] = { TEXT("confirm"), TEXT("add_interface is additive and needs no confirm; remove_interface is the one that requires it"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_interface[] = { TEXT("blueprintId"), TEXT("path"), TEXT("interface"), TEXT("interfaceClass"), TEXT("class"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_interface[] = { TEXT("preserveFunctions"), TEXT("not supported - remove_interface always removes the interface's functions with it (bPreserveFunctions=false)"), nullptr };
		static const TCHAR* const GMifDescKeys_list_interfaces[] = { TEXT("blueprintId"), TEXT("path"), TEXT("includeInherited"), nullptr };
		static const TCHAR* const GMifDescNotes_list_interfaces[] = { TEXT("inherited"), TEXT("spell it includeInherited") ,  TEXT("limit"), TEXT("not supported - list_interfaces always returns every implemented interface"), nullptr };
		static const TCHAR* const GMifDescKeys_list_datatables[] = { TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_list_datatables[] = { TEXT("path"), TEXT("list_datatables takes filter, a substring of the object path - read_datatable is the one that takes path") ,  TEXT("name"), TEXT("spell it filter - it is matched against the full object path, so a name substring works") ,  TEXT("search"), TEXT("spell it filter") ,  TEXT("limit"), TEXT("list_datatables is uncapped and takes no limit - narrow the result with filter") ,  TEXT("maxRows"), TEXT("this endpoint lists tables, not rows - maxRows belongs to read_datatable"), nullptr };
		static const TCHAR* const GMifDescKeys_read_datatable[] = { TEXT("path"), TEXT("maxRows"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_get_datatable_row[] = { TEXT("path"), TEXT("rowName"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_implement_interface_function[] = { TEXT("blueprintId"), TEXT("path"), TEXT("function"), nullptr };
		static const TCHAR* const GMifDescNotes_implement_interface_function[] = { TEXT("name"), TEXT("the interface function is 'function' here; 'name' is remove_function's key") ,  TEXT("interface"), TEXT("not a parameter - the owning interface is looked up from the function name and REPORTED back as interfaceClass. The interface must already be implemented on the blueprint") ,  TEXT("blueprint"), TEXT("the blueprint key is 'blueprintId' (alias: path)"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_function[] = { TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_function[] = { TEXT("function"), TEXT("this endpoint's key is 'name'; 'function' is implement_interface_function's key") ,  TEXT("force"), TEXT("the required acknowledgement is confirm:true") ,  TEXT("graphId"), TEXT("remove_function matches the function graph by NAME on the given blueprint - it does not take a graphId"), nullptr };
		static const TCHAR* const GMifDescKeys_write_datatable_rows[] = { TEXT("path"), TEXT("rows"), TEXT("replace"), TEXT("confirm"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_delete_datatable_rows[] = { TEXT("path"), TEXT("rowNames"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_delete_datatable_rows[] = { TEXT("rows"), TEXT("delete takes row NAMES, not row objects — pass rowNames:[\"A\",\"B\"]") ,  TEXT("rowName"), TEXT("the parameter is the array rowNames[]; pass a single-element array") ,  TEXT("dataTable"), TEXT("the datatable parameter is called path") ,  TEXT("table"), TEXT("the datatable parameter is called path"), nullptr };
		static const TCHAR* const GMifDescKeys_add_sequence[] = { TEXT("graphId"), TEXT("x"), TEXT("y"), TEXT("outputs"), nullptr };
		static const TCHAR* const GMifDescNotes_add_sequence[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("numOutputs"), TEXT("spell it outputs (add_make_array/add_make_map use numInputs; Sequence uses outputs)") ,  TEXT("pins"), TEXT("spell it outputs - it is the count of then_N exec pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_spawn_actor[] = { TEXT("graphId"), TEXT("actorClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_spawn_actor[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("actor"), TEXT("SpawnActor takes the CLASS to spawn, not an instance - pass actorClass (e.g. /Game/BP/BP_Foo.BP_Foo_C)") ,  TEXT("transform"), TEXT("SpawnTransform is a pin - place the node, then set_pin_default or connect_pins") ,  TEXT("spawnTransform"), TEXT("SpawnTransform is a pin - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_create_widget[] = { TEXT("graphId"), TEXT("widgetClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_create_widget[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("widget"), TEXT("CreateWidget takes the CLASS to create - pass widgetClass (e.g. /Game/UI/W_Foo.W_Foo_C)") ,  TEXT("owningPlayer"), TEXT("Owning Player is a pin - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_get_subsystem[] = { TEXT("graphId"), TEXT("subsystemClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_get_subsystem[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("subsystem"), TEXT("spell it subsystemClass - it must name a USubsystem-derived CLASS"), nullptr };
		static const TCHAR* const GMifDescKeys_add_make_array[] = { TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_make_array[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("num"), TEXT("spell it numInputs") ,  TEXT("count"), TEXT("spell it numInputs") ,  TEXT("items"), TEXT("the element values are pins - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_make_map[] = { TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_make_map[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("numEntries"), TEXT("spell it numInputs - one 'input' is one Key/Value entry") ,  TEXT("entries"), TEXT("spell it numInputs for the COUNT; the keys and values themselves are pins") ,  TEXT("pairs"), TEXT("spell it numInputs for the COUNT; the keys and values themselves are pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_make_set[] = { TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_make_set[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("num"), TEXT("spell it numInputs") ,  TEXT("count"), TEXT("spell it numInputs") ,  TEXT("container"), TEXT("not a parameter - add_make_array, add_make_map and add_make_set are separate endpoints, one per node type") ,  TEXT("items"), TEXT("the element values are pins - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_format_text[] = { TEXT("graphId"), TEXT("format"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_format_text[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("text"), TEXT("spell it format") ,  TEXT("formatText"), TEXT("spell it format") ,  TEXT("args"), TEXT("argument pins come from the {tokens} inside format - place the node, then set_pin_default or connect_pins"), nullptr };
		static const TCHAR* const GMifDescKeys_add_get_data_table_row[] = { TEXT("graphId"), TEXT("dataTable"), TEXT("rowName"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_get_data_table_row[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("table"), TEXT("spell it dataTable") ,  TEXT("dataTablePath"), TEXT("spell it dataTable") ,  TEXT("row"), TEXT("spell it rowName"), nullptr };
		static const TCHAR* const GMifDescKeys_add_comment[] = { TEXT("graphId"), TEXT("x"), TEXT("y"), TEXT("width"), TEXT("height"), TEXT("text"), nullptr };
		static const TCHAR* const GMifDescNotes_add_comment[] = { TEXT("graph"), TEXT("spell it graphId") ,  TEXT("comment"), TEXT("spell it text") ,  TEXT("nodeComment"), TEXT("spell it text") ,  TEXT("color"), TEXT("not supported - the box takes the editor's default comment colour"), nullptr };
		static const TCHAR* const GMifDescKeys_set_node_state[] = { TEXT("node"), TEXT("enabled"), TEXT("state"), TEXT("comment"), TEXT("commentBubble"), nullptr };
		static const TCHAR* const GMifDescNotes_set_node_state[] = { TEXT("text"), TEXT("that is add_comment's key - this sets the comment ON an existing node, not a comment BOX") ,  TEXT("enable"), TEXT("spell it `enabled`, and it takes a STATE not a bool - developmentOnly is a third value a bool cannot express"), nullptr };
		static const TCHAR* const GMifDescKeys_list_blend_profiles[] = { TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("profile"), nullptr };
		static const TCHAR* const GMifDescNotes_list_blend_profiles[] = { TEXT("bone"), TEXT("this lists profiles and every bone in them - filter on the client, or read one profile with `profile`"), nullptr };
		static const TCHAR* const GMifDescKeys_create_blend_profile[] = { TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_create_blend_profile[] = { TEXT("mode"), TEXT("UBlendProfile::Mode is a private UPROPERTY with no setter - a new profile is TimeFactor and this endpoint reports which mode it got rather than pretending to choose") ,  TEXT("bones"), TEXT("create makes an EMPTY profile; set_blend_profile_bone adds bones to it one at a time"), nullptr };
		static const TCHAR* const GMifDescKeys_set_blend_profile_bone[] = { TEXT("skeleton"), TEXT("path"), TEXT("assetPath"), TEXT("profile"), TEXT("bone"), TEXT("scale"), TEXT("recurse"), nullptr };
		static const TCHAR* const GMifDescNotes_set_blend_profile_bone[] = { TEXT("create"), TEXT("the entry is always created if the bone has none - the engine's bCreate defaults to FALSE and then writes nothing at all, which is not a behaviour worth offering") ,  TEXT("weight"), TEXT("spell it scale - and what it means depends on the profile's mode, which every response reports") ,  TEXT("bones"), TEXT("one bone per call; recurse:true covers a whole limb from its root"), nullptr };
		static const TCHAR* const GMifDescKeys_group_actors[] = { TEXT("actorPaths"), TEXT("actors"), TEXT("enableGrouping"), nullptr };
		static const TCHAR* const GMifDescNotes_group_actors[] = { TEXT("name"), TEXT("an AGroupActor is not named at creation - group first, then set_actor_label on the group this returns") ,  TEXT("group"), TEXT("that is ungroup_actors' key; this endpoint CREATES a group out of actorPaths[]") ,  TEXT("parent"), TEXT("grouping is not attachment - attach_actor is the parent/child verb and survives a cook, a group is a flat editor-only selection aid") ,  TEXT("folder"), TEXT("a folder is an Outliner tree path, not a group - the two are independent, and there is no endpoint that SETS one today; list_level_actors filters by folder but nothing assigns it"), nullptr };
		static const TCHAR* const GMifDescKeys_ungroup_actors[] = { TEXT("actorPaths"), TEXT("actors"), TEXT("group"), nullptr };
		static const TCHAR* const GMifDescNotes_ungroup_actors[] = { TEXT("recursive"), TEXT("UngroupActors disbands the root group it finds for each actor; there is no partial-depth ungroup to ask for") ,  TEXT("delete"), TEXT("ungrouping never deletes members - it removes the AGroupActor and leaves every actor where it is. delete_level_actor is the destructive verb"), nullptr };
		static const TCHAR* const GMifDescKeys_set_widget_is_variable[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("isVariable"), nullptr };
		static const TCHAR* const GMifDescNotes_set_widget_is_variable[] = { TEXT("name"), TEXT("the widget parameter is called widgetName — the widget's FName in the tree, not its display label") ,  TEXT("widget"), TEXT("spell it widgetName") ,  TEXT("variableName"), TEXT("not settable here — the generated member variable is ALWAYS named after the widget itself; rename the widget to rename the variable"), nullptr };
		static const TCHAR* const GMifDescKeys_add_widget_binding[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName"), TEXT("functionName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_widget_binding[] = { TEXT("property"), TEXT("spell it propertyName (the widget property to drive, e.g. \"Text\")") ,  TEXT("function"), TEXT("spell it functionName (a pure UFUNCTION on the user widget, e.g. \"GetText\")") ,  TEXT("widget"), TEXT("spell it widgetName") ,  TEXT("kind"), TEXT("not settable — this endpoint only writes function bindings (EBindingKind::Function)") ,  TEXT("sourcePath"), TEXT("not settable — SourcePath is deliberately left empty so the runtime binds via BindUFunction(functionName)"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_widget_binding[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_widget_binding[] = { TEXT("functionName"), TEXT("not part of the identity — a binding is removed by widgetName + propertyName alone, whatever function it points at") ,  TEXT("property"), TEXT("spell it propertyName") ,  TEXT("widget"), TEXT("spell it widgetName"), nullptr };
		static const TCHAR* const GMifDescKeys_list_widget_bindings[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName"), nullptr };
		static const TCHAR* const GMifDescNotes_list_widget_bindings[] = { TEXT("widget"), TEXT("spell it widgetName") ,  TEXT("property"), TEXT("spell it propertyName") ,  TEXT("functionName"), TEXT("not a filter - a binding is identified by widgetName + propertyName; the function is what it POINTS AT and is reported per row"), nullptr };
		static const TCHAR* const GMifDescKeys_add_tree_widget[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetClass"), TEXT("class"), TEXT("name"), TEXT("parentName"), TEXT("asRoot"), TEXT("x"), TEXT("y"), TEXT("autoSize"), nullptr };
		static const TCHAR* const GMifDescNotes_add_tree_widget[] = { TEXT("widgetName"), TEXT("the NEW widget's name parameter is called name; widgetName is only a response field") ,  TEXT("className"), TEXT("the class parameter is called widgetClass (alias: class)") ,  TEXT("parent"), TEXT("spell it parentName — the FName of a UPanelWidget already in the tree") ,  TEXT("position"), TEXT("pass the canvas-slot position as separate numbers x and y") ,  TEXT("size"), TEXT("not implemented — the canvas slot is auto-sized; set the slot's Size with set_property after adding") ,  TEXT("slot"), TEXT("slot properties beyond x/y/autoSize are not settable here — use set_property on the created widget's Slot"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_tree_widget[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_tree_widget[] = { TEXT("name"), TEXT("the widget parameter is called widgetName") ,  TEXT("widget"), TEXT("spell it widgetName") ,  TEXT("recursive"), TEXT("not a parameter — RemoveWidget always takes the widget's whole subtree with it"), nullptr };
		static const TCHAR* const GMifDescKeys_list_tree_widgets[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_tree_widgets[] = { TEXT("widgetName"), TEXT("this endpoint lists the WHOLE tree; there is no per-widget filter"), nullptr };
		static const TCHAR* const GMifDescKeys_duplicate_tree_widget[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("parentName"), TEXT("index"), nullptr };
		static const TCHAR* const GMifDescNotes_duplicate_tree_widget[] = { TEXT("newName"), TEXT("the clone name is assigned by the engine paste path to keep it unique; rename afterwards if you need a specific one") ,  TEXT("widget"), TEXT("spell it widgetName"), nullptr };
		static const TCHAR* const GMifDescKeys_wrap_tree_widget[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("wrapperClass"), TEXT("wrapperName"), nullptr };
		static const TCHAR* const GMifDescNotes_wrap_tree_widget[] = { TEXT("class"), TEXT("spell it wrapperClass - the PANEL to wrap with, not the widget being wrapped") ,  TEXT("panelClass"), TEXT("spell it wrapperClass"), nullptr };
		static const TCHAR* const GMifDescKeys_move_tree_widget[] = { TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("parentName"), TEXT("asRoot"), TEXT("index"), TEXT("replaceRoot"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_move_tree_widget[] = { TEXT("newParent"), TEXT("spell it parentName") ,  TEXT("x"), TEXT("move changes PARENTAGE only; set slot layout afterwards with set_property on the widget Slot") ,  TEXT("y"), TEXT("move changes PARENTAGE only; set slot layout afterwards with set_property on the widget Slot"), nullptr };
		static const TCHAR* const GMifDescKeys_set_property[] = { TEXT("objectPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyPath"), TEXT("value"), TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"), TEXT("enforceClamps"), TEXT("clamp"), TEXT("respectClamps"), TEXT("saveConfig"), nullptr };
		static const TCHAR* const GMifDescNotes_set_property[] = { TEXT("actorPath"), TEXT("use objectPath - a placed actor's path IS an objectPath") ,  TEXT("componentName"), TEXT("components ARE supported, just not by name here: call list_components, take the component's templatePath (the ..._GEN_VARIABLE one) and pass it as objectPath. That is how you set an AudioComponent's Sound, a CharacterMovement's MaxWalkSpeed, or BodyInstance.bSimulatePhysics on a mesh") ,  TEXT("component"), TEXT("same as componentName - pass the component's templatePath from list_components as objectPath") ,  TEXT("format"), TEXT("no output format switch here; the response always carries BOTH valueAfter (export text) and typed (typed JSON)") ,  TEXT("verify"), TEXT("not optional - every write is verified by re-export, which is what makes ok:true mean written") ,  TEXT("operation"), TEXT("set_property writes a VALUE; add/insert/remove/clear/swap/resize/setKey on a container are edit_container") ,  TEXT("save"), TEXT("spell it saveConfig, and it takes none|default|user rather than a bool - \"default\" writes the project-wide Config/Default*.ini, \"user\" writes the per-user config, and they are different files with different blast radii") ,  TEXT("configFile"), TEXT("not selectable - TryUpdateDefaultConfigFile picks the file from the class and the response reports which one it used. Letting a caller name the file would make set_property an arbitrary-file writer"), nullptr };
		static const TCHAR* const GMifDescKeys_get_property[] = { TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyPath"), TEXT("property"), nullptr };
		static const TCHAR* const GMifDescKeys_list_object_properties[] = { TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"), nullptr };
		static const TCHAR* const GMifDescNotes_list_object_properties[] = { TEXT("propertyPath"), TEXT("list_object_properties dumps ALL top-level properties; get_property reads ONE by dot path, and describe_property reports its flags/metadata/EditCondition"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_property[] = { TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("class"), TEXT("className"), TEXT("propertyPath"), TEXT("property"), TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"), TEXT("includeMetadata"), TEXT("includeDefault"), nullptr };
		static const TCHAR* const GMifDescKeys_diff_properties_vs_default[] = { TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"), TEXT("includeTransient"), TEXT("deep"), TEXT("recursive"), TEXT("includeChildren"), nullptr };
		static const TCHAR* const GMifDescKeys_edit_container[] = { TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyPath"), TEXT("property"), TEXT("operation"), TEXT("action"), TEXT("index"), TEXT("at"), TEXT("count"), TEXT("key"), TEXT("newKey"), TEXT("value"), TEXT("swapWith"), TEXT("newSize"), TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"), nullptr };
		static const TCHAR* const GMifDescNotes_edit_container[] = { TEXT("op"), TEXT("this endpoint's verb is 'operation' (alias 'action'), NOT 'op' - 'op' is batch's routing key and is tolerated centrally, so an endpoint that used it would be un-diagnosable inside batch"), nullptr };
		static const TCHAR* const GMifDescKeys_reset_property_to_default[] = { TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyPath"), TEXT("property"), TEXT("force"), TEXT("allowEditConst"), TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"), nullptr };
		static const TCHAR* const GMifDescKeys_add_nav_volume[] = { TEXT("location"), TEXT("size"), TEXT("label"), nullptr };
		static const TCHAR* const GMifDescNotes_add_nav_volume[] = { TEXT("scale"), TEXT("pass size in world units - the brush scale (size / 200) is computed for you") ,  TEXT("extent"), TEXT("use size, which is the FULL coverage in world units, not a half-extent") ,  TEXT("name"), TEXT("use label - it becomes the volume's outliner label"), nullptr };
		static const TCHAR* const GMifDescKeys_build_navmesh[] = { nullptr };
		static const TCHAR* const GMifDescNotes_build_navmesh[] = { TEXT("wait"), TEXT("not supported - generation is asynchronous; poll nav_status until building=false and tiles>0") ,  TEXT("timeout"), TEXT("not supported - this call never blocks; poll nav_status instead"), nullptr };
		static const TCHAR* const GMifDescKeys_nav_status[] = { nullptr };
		static const TCHAR* const GMifDescNotes_nav_status[] = { TEXT("world"), TEXT("not supported - nav_status always reports the active world (the PIE world while PIE is running); the world it used is echoed back as 'world'"), nullptr };
		static const TCHAR* const GMifDescKeys_move_actor_to[] = { TEXT("actorPath"), TEXT("actor"), TEXT("location"), nullptr };
		static const TCHAR* const GMifDescNotes_move_actor_to[] = { TEXT("path"), TEXT("use actorPath (alias: actor) - this endpoint does not accept the bare 'path' spelling other actor endpoints allow") ,  TEXT("destination"), TEXT("the goal goes in location {x,y,z}") ,  TEXT("acceptanceRadius"), TEXT("not supported - SimpleMoveToLocation uses the engine's default acceptance radius"), nullptr };
		static const TCHAR* const GMifDescKeys_spawn_many[] = { TEXT("items"), TEXT("actorClass"), TEXT("mesh"), TEXT("material"), TEXT("folder"), TEXT("labelPrefix"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_many[] = { TEXT("count"), TEXT("spawn_many places one actor per items[] entry — repeat the entry, or use duplicate_actors with count") ,  TEXT("actors"), TEXT("the array parameter is called items[]"), nullptr };
		static const TCHAR* const GMifDescKeys_duplicate_actors[] = { TEXT("actorPaths"), TEXT("labelPrefix"), TEXT("offset"), TEXT("yawOffset"), TEXT("count"), TEXT("labelSuffix"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_duplicate_actors[] = { TEXT("rotationOffset"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>") ,  TEXT("rotation"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>") ,  TEXT("scale"), TEXT("not implemented — copies keep the source actor's scale"), nullptr };
		static const TCHAR* const GMifDescKeys_create_material_instance[] = { TEXT("parent"), TEXT("parentMaterial"), TEXT("path"), TEXT("scalars"), TEXT("vectors"), nullptr };
		static const TCHAR* const GMifDescNotes_create_material_instance[] = { TEXT("textures"), TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property") ,  TEXT("texture"), TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property") ,  TEXT("material"), TEXT("the source material parameter is called parent (alias: parentMaterial)"), nullptr };
		static const TCHAR* const GMifDescKeys_set_material_parameter[] = { TEXT("material"), TEXT("materialPath"), TEXT("path"), TEXT("scalars"), TEXT("vectors"), TEXT("textures"), TEXT("switches"), TEXT("parameter"), TEXT("parameterName"), TEXT("name"), TEXT("value"), TEXT("association"), TEXT("index"), nullptr };
		static const TCHAR* const GMifDescNotes_set_material_parameter[] = { TEXT("texture"), TEXT("the plural key is 'textures': {\"ParamName\": \"/Game/path/T_Foo.T_Foo\"}") ,  TEXT("switch"), TEXT("the plural key is 'switches': {\"ParamName\": true}") ,  TEXT("staticSwitches"), TEXT("the key is 'switches'"), nullptr };
		static const TCHAR* const GMifDescKeys_add_foliage_instances[] = { TEXT("mesh"), TEXT("staticMesh"), TEXT("foliageType"), TEXT("type"), TEXT("instances"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_add_foliage_instances[] = { TEXT("material"), TEXT("not implemented — the HISM uses the mesh's own materials; override them with set_property on the component afterwards") ,  TEXT("transforms"), TEXT("the array parameter is called instances[]"), nullptr };
		static const TCHAR* const GMifDescKeys_list_foliage_instances[] = { TEXT("foliageType"), TEXT("type"), TEXT("includeInstances"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_foliage_instances[] = { TEXT("actorPath"), TEXT("foliage is not an actor per instance - it lives in the level's AInstancedFoliageActor, keyed by foliage TYPE") ,  TEXT("mesh"), TEXT("filter on foliageType; the mesh is reported for each type but is not the key"), nullptr };
		static const TCHAR* const GMifDescKeys_create_landscape[] = { TEXT("location"), TEXT("scale"), TEXT("componentsX"), TEXT("componentsY"), TEXT("quadsPerSection"), TEXT("sectionsPerComponent"), TEXT("material"), TEXT("landscapeMaterial"), TEXT("layers"), TEXT("heightMode"), TEXT("amplitude"), TEXT("frequency"), TEXT("seed"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_create_landscape[] = { TEXT("name"), TEXT("use label - it sets the actor's display label") ,  TEXT("position"), TEXT("use location {x,y,z}") ,  TEXT("layerInfo"), TEXT("layers is an ARRAY of objects - pass layers:[{layerInfo:\"/Game/.../X_LayerInfo\", weight:0..1}]") ,  TEXT("heightmap"), TEXT("importing a heightmap file is not supported - use heightMode (flat|rolling|island) with amplitude, frequency and seed") ,  TEXT("rotation"), TEXT("not supported - the landscape is always spawned axis-aligned"), nullptr };
		static const TCHAR* const GMifDescKeys_sculpt_landscape[] = { TEXT("landscape"), TEXT("actorPath"), TEXT("center"), TEXT("radius"), TEXT("mode"), TEXT("amount"), TEXT("falloff"), TEXT("targetZ"), nullptr };
		static const TCHAR* const GMifDescNotes_sculpt_landscape[] = { TEXT("strength"), TEXT("use amount (world units) with mode raise/lower") ,  TEXT("height"), TEXT("use targetZ (a world Z) with mode flatten, or amount with mode raise/lower") ,  TEXT("brushSize"), TEXT("use radius (world units)") ,  TEXT("target"), TEXT("use targetZ - it is a world Z, not a vertex height") ,  TEXT("z"), TEXT("center is an object - pass center:{x,y}; a flatten target is targetZ"), nullptr };
		static const TCHAR* const GMifDescKeys_import_landscape_heightmap[] = { TEXT("landscape"), TEXT("actorPath"), TEXT("file"), TEXT("data"), TEXT("width"), TEXT("height"), TEXT("x0"), TEXT("y0"), TEXT("minZ"), TEXT("maxZ"), nullptr };
		static const TCHAR* const GMifDescNotes_import_landscape_heightmap[] = { TEXT("layer"), TEXT("edit layers are not supported here. Writing to a named layer " "needs FScopedSetLandscapeEditingLayer around the edit, and " "without it the write silently lands on the merged result " "instead - a wrong answer that looks like a right one. Sculpt " "the base layer, or ask for this as its own item") ,  TEXT("heights"), TEXT("a JSON array of floats is deliberately not accepted - " "1450x1450 is 2.1M values and about 25 MB of request body. " "Use file, or data as base64 uint16") ,  TEXT("format"), TEXT("the format is taken from the file extension - .png or .r16"), nullptr };
		static const TCHAR* const GMifDescKeys_export_landscape_heightmap[] = { TEXT("landscape"), TEXT("actorPath"), TEXT("file"), TEXT("x0"), TEXT("y0"), TEXT("width"), TEXT("height"), TEXT("asData"), nullptr };
		static const TCHAR* const GMifDescNotes_export_landscape_heightmap[] = { TEXT("format"), TEXT("the format is taken from the file extension - .png or .r16") ,  TEXT("minZ"), TEXT("an export is the raw uint16 the landscape stores, so there is " "nothing to remap. The response reports the world Z that 0 and " "65535 correspond to, which is what you would remap WITH"), nullptr };
		static const TCHAR* const GMifDescKeys_paint_landscape[] = { TEXT("landscape"), TEXT("actorPath"), TEXT("layerInfo"), TEXT("layer"), TEXT("info"), TEXT("center"), TEXT("radius"), TEXT("weight"), TEXT("falloff"), nullptr };
		static const TCHAR* const GMifDescNotes_paint_landscape[] = { TEXT("layerName"), TEXT("pass the LandscapeLayerInfoObject asset path as layerInfo - landscape_info lists the legal ones") ,  TEXT("strength"), TEXT("use weight (0..1)") ,  TEXT("alpha"), TEXT("use weight (0..1)") ,  TEXT("brushSize"), TEXT("use radius (world units)") ,  TEXT("erase"), TEXT("there is no erase mode - weights normalise across layers, so paint a DIFFERENT layer up to push this one down"), nullptr };
		static const TCHAR* const GMifDescKeys_register_landscape_layer[] = { TEXT("landscape"), TEXT("actorPath"), TEXT("layerName"), TEXT("layer"), TEXT("layerInfo"), TEXT("template"), nullptr };
		static const TCHAR* const GMifDescNotes_register_landscape_layer[] = { TEXT("weight"), TEXT("registration does not paint - register the layer, then paint_landscape applies weight") ,  TEXT("create"), TEXT("creating is the default; pass layerInfo to assign an existing asset instead") ,  TEXT("material"), TEXT("this cannot add a layer to the material - the material must already declare the name, and set_material_parameter is not that verb either"), nullptr };
		static const TCHAR* const GMifDescKeys_bind_landscape_rvt[] = { TEXT("landscape"), TEXT("actorPath"), TEXT("runtimeVirtualTextures"), TEXT("createVolumes"), nullptr };
		static const TCHAR* const GMifDescNotes_bind_landscape_rvt[] = { TEXT("runtimeVirtualTexture"), TEXT("the key is PLURAL and takes an array - runtimeVirtualTextures:[assetPath], even for one") ,  TEXT("rvt"), TEXT("use runtimeVirtualTextures:[assetPath,...]") ,  TEXT("createVolume"), TEXT("the key is PLURAL - createVolumes (bool)"), nullptr };
		static const TCHAR* const GMifDescKeys_landscape_info[] = { nullptr };
		static const TCHAR* const GMifDescNotes_landscape_info[] = { TEXT("landscape"), TEXT("not supported - this endpoint always reports EVERY landscape in the editor world; filter the landscapes[] array by actorPath or label") ,  TEXT("limit"), TEXT("not supported - every landscape is reported"), nullptr };
		static const TCHAR* const GMifDescKeys_new_level[] = { TEXT("partitioned"), nullptr };
		static const TCHAR* const GMifDescNotes_new_level[] = { TEXT("path"), TEXT("new_level does not take a path - it creates an unsaved transient map; pass path to save_level_as afterwards") ,  TEXT("name"), TEXT("new_level does not name the map - the name comes from the path you give save_level_as"), nullptr };
		static const TCHAR* const GMifDescKeys_save_level_as[] = { TEXT("path"), TEXT("packagePath"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_save_level_as[] = { TEXT("level"), TEXT("use path - 'level' is the sublevel selector on the streaming endpoints; save_level_as always saves the OPEN persistent level") ,  TEXT("filename"), TEXT("use path with a package path like \"/Game/Maps/MyLevel\" - the .umap filename is derived from it and is never passed in"), nullptr };
		static const TCHAR* const GMifDescKeys_load_level[] = { TEXT("path"), TEXT("packagePath"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_load_level[] = { TEXT("level"), TEXT("use path - 'level' is the sublevel selector on the streaming endpoints; load_level opens a whole map") ,  TEXT("filename"), TEXT("use path with a package path like \"/Game/Maps/MyLevel\" - the .umap filename is derived from it and is never passed in"), nullptr };
		static const TCHAR* const GMifDescKeys_set_spline_points[] = { TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("componentName"), TEXT("points"), TEXT("space"), TEXT("pointType"), TEXT("closedLoop"), TEXT("closed"), TEXT("loop"), TEXT("snapToGround"), TEXT("groundOffset"), TEXT("skipPostEditChange"), nullptr };
		static const TCHAR* const GMifDescNotes_set_spline_points[] = { TEXT("offset"), TEXT("use groundOffset - 'offset' is snap_actors_to_ground's name for the same idea") ,  TEXT("type"), TEXT("use pointType - it sets the interpolation type of every point written by this call") ,  TEXT("tangents"), TEXT("not implemented - set_spline_points writes point LOCATIONS only; pointType:\"curveCustomTangent\" is accepted but the tangents themselves cannot be supplied here"), nullptr };
		static const TCHAR* const GMifDescKeys_get_spline_points[] = { TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("componentName"), TEXT("space"), nullptr };
		static const TCHAR* const GMifDescNotes_get_spline_points[] = { TEXT("index"), TEXT("not supported - get_spline_points returns EVERY point; index into the returned points[] array") ,  TEXT("points"), TEXT("not a parameter of this endpoint - points[] is what it RETURNS; use set_spline_points to write them"), nullptr };
		static const TCHAR* const GMifDescKeys_snap_actors_to_ground[] = { TEXT("actorPaths"), TEXT("folder"), TEXT("labelContains"), TEXT("all"), TEXT("offset"), TEXT("traceHeight"), TEXT("alignToNormal"), TEXT("groundActor"), TEXT("ground"), TEXT("allowAnyHit"), nullptr };
		static const TCHAR* const GMifDescNotes_snap_actors_to_ground[] = { TEXT("actorPath"), TEXT("use actorPaths:[...] - this endpoint snaps a SET, so the parameter is plural even for a single actor") ,  TEXT("groundOffset"), TEXT("use offset - 'groundOffset' is set_spline_points' name for the same idea") ,  TEXT("snapToGround"), TEXT("not a parameter - snapping IS what this endpoint does; choose the actors with actorPaths[], folder, labelContains or all:true"), nullptr };
		static const TCHAR* const GMifDescKeys_set_viewport_camera[] = { TEXT("location"), TEXT("rotation"), TEXT("lookAt"), TEXT("fov"), TEXT("ortho"), TEXT("orthoZoom"), TEXT("viewMode"), TEXT("showFlags"), TEXT("gameView"), TEXT("realtime"), nullptr };
		static const TCHAR* const GMifDescNotes_set_viewport_camera[] = { TEXT("x"), TEXT("there is no top-level x/y/z here - pass location:{x,y,z}; rotation and lookAt take the same nested form. capture_camera is the endpoint that also accepts the flat form") ,  TEXT("zoom"), TEXT("the key is 'orthoZoom', and it only has an effect on an orthographic view - set ortho first") ,  TEXT("orthographic"), TEXT("the key is 'ortho' and it takes a STRING: top/bottom/front/back/left/right/perspective") ,  TEXT("actorPath"), TEXT("this endpoint sets an explicit transform - to frame an actor use focus_viewport, which takes actorPath"), nullptr };
		static const TCHAR* const GMifDescKeys_list_viewport_bookmarks[] = { nullptr };
		static const TCHAR* const GMifDescNotes_list_viewport_bookmarks[] = { TEXT("index"), TEXT("this lists them all; describe one by reading the entry with that index") ,  TEXT("viewport"), TEXT("bookmarks live on AWorldSettings, not on a viewport - every viewport in the editor shares one set"), nullptr };
		static const TCHAR* const GMifDescKeys_set_viewport_bookmark[] = { TEXT("index"), TEXT("slot"), nullptr };
		static const TCHAR* const GMifDescNotes_set_viewport_bookmark[] = { TEXT("location"), TEXT("a bookmark cannot be written for a place the camera is not - CreateOrSetBookmark reads the viewport. Move there with set_viewport_camera first, then set the bookmark") ,  TEXT("rotation"), TEXT("same as location - set_viewport_camera first, then bookmark where you are") ,  TEXT("name"), TEXT("bookmarks are numbered, not named - there is no label to set"), nullptr };
		static const TCHAR* const GMifDescKeys_jump_viewport_bookmark[] = { TEXT("index"), TEXT("slot"), nullptr };
		static const TCHAR* const GMifDescNotes_jump_viewport_bookmark[] = { TEXT("speed"), TEXT("the jump is immediate; there is no interpolation setting on this endpoint") ,  TEXT("create"), TEXT("jumping never creates - set_viewport_bookmark writes a slot"), nullptr };
		static const TCHAR* const GMifDescKeys_clear_viewport_bookmark[] = { TEXT("index"), TEXT("slot"), TEXT("all"), nullptr };
		static const TCHAR* const GMifDescNotes_clear_viewport_bookmark[] = { TEXT("confirm"), TEXT("clearing a bookmark destroys no asset and no actor - it is a camera slot on the level, and undo covers it"), nullptr };
		static const TCHAR* const GMifDescKeys_focus_viewport[] = { TEXT("actorPath"), TEXT("actor"), TEXT("folder"), TEXT("all"), TEXT("instant"), nullptr };
		static const TCHAR* const GMifDescNotes_focus_viewport[] = { TEXT("path"), TEXT("the actor key is 'actorPath' (alias: actor); it accepts an object path, an object name or a label") ,  TEXT("name"), TEXT("actorPath already matches on object name and label as well as full path - use it") ,  TEXT("bounds"), TEXT("'bounds' is an OUTPUT field - the framing target is actorPath, folder, or nothing for the whole level"), nullptr };
		static const TCHAR* const GMifDescKeys_get_viewport_camera[] = { TEXT("showFlags"), nullptr };
		static const TCHAR* const GMifDescNotes_get_viewport_camera[] = { TEXT("viewportIndex"), TEXT("not supported - this always reports the ACTIVE viewport, falling back to the first perspective one; viewportCount in the response says how many exist"), nullptr };
		static const TCHAR* const GMifDescKeys_get_actor_bounds[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_get_actor_bounds[] = { TEXT("assetPath"), TEXT("bounds are read from the PLACED actor, not the mesh asset — the asset's ExtendedBounds ignore the actor's scale. Pass the actor as actorPath") ,  TEXT("label"), TEXT("actorPath already accepts a label, an object name or a full path — use it") ,  TEXT("onlyColliding"), TEXT("not a parameter — bounds always include non-colliding components, because editor-world collision is unreliable for imported props"), nullptr };
		static const TCHAR* const GMifDescKeys_check_overlaps[] = { TEXT("actorPath"), TEXT("actor"), TEXT("nameContains"), TEXT("ignoreGround"), TEXT("tolerance"), nullptr };
		static const TCHAR* const GMifDescNotes_check_overlaps[] = { TEXT("path"), TEXT("this endpoint takes actorPath (alias: actor) only — 'path' is accepted by get_actor_bounds, not here") ,  TEXT("name"), TEXT("use nameContains for a substring filter over object names and labels, or actorPath to test a single actor") ,  TEXT("depth"), TEXT("'depth' is an OUTPUT field on each reported pair — the input threshold is 'tolerance' (default 25)"), nullptr };
		static const TCHAR* const GMifDescKeys_trace_ground[] = { TEXT("x"), TEXT("y"), TEXT("fromZ"), TEXT("toZ"), TEXT("location"), TEXT("ignoreActor"), TEXT("actorPath"), nullptr };
		static const TCHAR* const GMifDescNotes_trace_ground[] = { TEXT("z"), TEXT("there is no top-level z — the trace START is 'fromZ' (default 100000) and the END is 'toZ' (default -100000). location:{x,y,z} also seeds fromZ from its z") ,  TEXT("ignore"), TEXT("the key is 'ignoreActor' (alias: actorPath); it accepts an object path, object name or label") ,  TEXT("channel"), TEXT("not a parameter — this always traces ECC_WorldStatic with complex collision"), nullptr };
		static const TCHAR* const GMifDescKeys_trace[] = { TEXT("start"), TEXT("end"), TEXT("direction"), TEXT("distance"), TEXT("shape"), TEXT("radius"), TEXT("halfExtent"), TEXT("halfHeight"), TEXT("channel"), TEXT("traceComplex"), TEXT("multi"), TEXT("ignoreActors"), TEXT("draw"), TEXT("drawDuration"), nullptr };
		static const TCHAR* const GMifDescNotes_trace[] = { TEXT("from"), TEXT("the parameter is 'start' (trace_ground uses fromZ/toZ because it is Z-only; this one takes full vectors)") ,  TEXT("to"), TEXT("the parameter is 'end'") ,  TEXT("ignoreActor"), TEXT("this one takes ignoreActors:[...] - a list, since a general trace usually needs to exclude several"), nullptr };
		static const TCHAR* const GMifDescKeys_draw_debug[] = { TEXT("shape"), TEXT("start"), TEXT("end"), TEXT("center"), TEXT("radius"), TEXT("extent"), TEXT("text"), TEXT("color"), TEXT("duration"), TEXT("thickness"), nullptr };
		static const TCHAR* const GMifDescNotes_draw_debug[] = { TEXT("position"), TEXT("use 'center' for sphere/box/point/string, or 'start' + 'end' for line/arrow") ,  TEXT("size"), TEXT("use 'radius' for a sphere or 'extent':{x,y,z} for a box") ,  TEXT("persistent"), TEXT("not supported on purpose - a persistent debug shape survives until the level reloads and there is no endpoint to clear it. Use a long duration instead."), nullptr };
		static const TCHAR* const GMifDescKeys_get_perf_stats[] = { nullptr };
		static const TCHAR* const GMifDescNotes_get_perf_stats[] = { TEXT("world"), TEXT("this always measures the world the editor is currently showing; start_pie first if you want PIE numbers, and check pieRunning in the response") ,  TEXT("reset"), TEXT("the RHI counters are the engine's own and are not resettable from here - compare two calls instead"), nullptr };
		static const TCHAR* const GMifDescKeys_capture_viewport[] = { TEXT("path"), TEXT("name"), TEXT("file"), nullptr };
		static const TCHAR* const GMifDescNotes_capture_viewport[] = { TEXT("location"), TEXT("this captures the CURRENT viewport - move it first with set_viewport_camera, or use capture_camera to shoot from an arbitrary point without disturbing the user's view") ,  TEXT("resolution"), TEXT("the capture is the viewport's own size; resize the editor window to change it") ,  TEXT("showUI"), TEXT("not supported - this reads the 3D viewport's backbuffer, which never contains the editor's surrounding UI"), nullptr };
		static const TCHAR* const GMifDescKeys_audition_sound[] = { TEXT("path"), TEXT("sound"), TEXT("assetPath"), TEXT("stop"), nullptr };
		static const TCHAR* const GMifDescNotes_audition_sound[] = { TEXT("volume"), TEXT("the editor preview plays at the asset's own volume - set the asset's Volume property to change it") ,  TEXT("location"), TEXT("this is a 2D editor PREVIEW, not a world sound; for a positioned sound use add_function_call with PlaySoundAtLocation"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_metasound[] = { TEXT("path"), TEXT("assetPath"), TEXT("metasound"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_metasound[] = { TEXT("name"), TEXT("address it by asset path; find_assets {class:\"MetaSoundSource\"} lists every one with its objectPath") ,  TEXT("includeNodes"), TEXT("not implemented - this reports the MetaSound's INTERFACE (its inputs and outputs), which is what you need to drive it. The node graph is reported only as a count"), nullptr };
		static const TCHAR* const GMifDescKeys_nav_project_point[] = { TEXT("point"), TEXT("extent"), nullptr };
		static const TCHAR* const GMifDescNotes_nav_project_point[] = { TEXT("actor"), TEXT("pass a point - read an actor's location with get_level_actor first"), nullptr };
		static const TCHAR* const GMifDescKeys_nav_find_path[] = { TEXT("start"), TEXT("end"), TEXT("draw"), TEXT("drawDuration"), nullptr };
		static const TCHAR* const GMifDescNotes_nav_find_path[] = { TEXT("actor"), TEXT("pass coordinates - read an actor's location with get_level_actor"), nullptr };
		static const TCHAR* const GMifDescKeys_capture_camera[] = { TEXT("x"), TEXT("y"), TEXT("z"), TEXT("location"), TEXT("rotation"), TEXT("lookAt"), TEXT("useViewportCamera"), TEXT("useViewport"), TEXT("fromViewport"), TEXT("fov"), TEXT("width"), TEXT("height"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_capture_camera[] = { TEXT("showFlags"), TEXT("not implemented — capture_camera always renders lit/tonemapped with Atmosphere+Fog on and does NOT read the level viewport's show flags, and no endpoint in this build sets a view mode") ,  TEXT("viewMode"), TEXT("not implemented — same gap as showFlags: the viewport's view mode is not consumed here") ,  TEXT("actorPath"), TEXT("not a parameter of this endpoint — to frame an actor, read get_actor_bounds and pass its origin as lookAt, or focus_viewport the actor and then capture with useViewportCamera:true"), nullptr };
		static const TCHAR* const GMifDescKeys_scene_report[] = { TEXT("groundZ"), TEXT("floatTolerance"), TEXT("tallWarnZ"), nullptr };
		static const TCHAR* const GMifDescNotes_scene_report[] = { TEXT("tolerance"), TEXT("the float/sunken threshold here is 'floatTolerance' (default 30) — 'tolerance' is check_overlaps' overlap-depth threshold") ,  TEXT("nameContains"), TEXT("not supported — scene_report always scans the whole world; filter its floating/sunken/tooTall arrays caller-side, or use check_overlaps which does take nameContains") ,  TEXT("actorPath"), TEXT("scene_report is whole-scene by design; for one actor use get_actor_bounds, or check_overlaps with actorPath"), nullptr };
		static const TCHAR* const GMifDescKeys_start_pie[] = { TEXT("simulate"), TEXT("startLocation"), TEXT("startRotation"), TEXT("players"), TEXT("netMode"), TEXT("oneProcess"), TEXT("width"), TEXT("height"), nullptr };
		static const TCHAR* const GMifDescNotes_start_pie[] = { TEXT("location"), TEXT("use startLocation — and note startRotation is only read when startLocation is supplied too") ,  TEXT("rotation"), TEXT("use startRotation; it is only read when startLocation is supplied as well") ,  TEXT("clients"), TEXT("use players (clamped to 1-8)") ,  TEXT("level"), TEXT("PIE plays whatever level is already open — call load_level first, then start_pie") ,  TEXT("map"), TEXT("PIE plays whatever level is already open — call load_level first, then start_pie") ,  TEXT("wait"), TEXT("not supported: this handler runs on the game thread, so waiting for PIE would deadlock the ticks that start it. Poll pie_status until state=='running'"), nullptr };
		static const TCHAR* const GMifDescKeys_stop_pie[] = { nullptr };
		static const TCHAR* const GMifDescNotes_stop_pie[] = { TEXT("wait"), TEXT("not supported: the stop is deferred to the next editor tick and this handler holds the game thread. Poll pie_status until state=='stopped'") ,  TEXT("force"), TEXT("not supported: RequestEndPlayMap is the only safe teardown from inside the ticker; EndPlayMap here would tear the world down under this callstack"), nullptr };
		static const TCHAR* const GMifDescKeys_pie_status[] = { nullptr };
		static const TCHAR* const GMifDescNotes_pie_status[] = { TEXT("netMode"), TEXT("this endpoint always reports GEditor->PlayWorld; use list_pie_actors {netMode:server|client|any} to address a specific PIE world") ,  TEXT("waitFor"), TEXT("not supported: nothing can block here without stalling the ticks PIE needs. Call pie_status repeatedly instead"), nullptr };
		static const TCHAR* const GMifDescKeys_list_pie_actors[] = { TEXT("classFilter"), TEXT("nameContains"), TEXT("limit"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescNotes_list_pie_actors[] = { TEXT("class"), TEXT("use classFilter — a SUBSTRING matched against the actor's class and every super, not an exact class path") ,  TEXT("world"), TEXT("use netMode (server|client|any) to pick which PIE world answers; the returned 'worlds' array shows what is running") ,  TEXT("actorClass"), TEXT("use classFilter (substring match)"), nullptr };
		static const TCHAR* const GMifDescKeys_spawn_actor_in_pie[] = { TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"), TEXT("mesh"), TEXT("staticMesh"), TEXT("label"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_actor_in_pie[] = { TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials") ,  TEXT("folder"), TEXT("folders are an editor-outliner concept; a PIE-spawned actor has none"), nullptr };
		static const TCHAR* const GMifDescKeys_run_console_captured[] = { TEXT("command"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_run_console_captured[] = { TEXT("cmd"), TEXT("use command — the 'cmd' alias exists only on run_console, not here") ,  TEXT("world"), TEXT("not selectable here: this endpoint runs against the PIE world when playing and the editor world otherwise. Use run_console {world:editor|pie|active} to choose") ,  TEXT("captureOutput"), TEXT("capture is unconditional here — that is what this endpoint is for; run_console has the toggle"), nullptr };
		static const TCHAR* const GMifDescKeys_list_level_actors[] = { TEXT("classFilter"), TEXT("nameContains"), TEXT("folder"), TEXT("selectedOnly"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_level_actors[] = { TEXT("class"), TEXT("the filter key here is 'classFilter' — a substring matched against the whole ancestry, not an exact class path") ,  TEXT("labelContains"), TEXT("use nameContains — it matches the object name AND the Outliner label ('labelContains' is snap_actors_to_ground's key)") ,  TEXT("filter"), TEXT("use nameContains ('filter'/'nameFilter' are the property-listing endpoints' aliases, not this one's)"), nullptr };
		static const TCHAR* const GMifDescKeys_get_level_actor[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_get_level_actor[] = { TEXT("actorPaths"), TEXT("this reads ONE actor — for several, use list_level_actors, which is a single call over the whole level") ,  TEXT("nameContains"), TEXT("that is list_level_actors' filter; this endpoint takes one exact handle (a label or object name is accepted too, if unique)"), nullptr };
		static const TCHAR* const GMifDescKeys_attach_actor[] = { TEXT("child"), TEXT("parent"), TEXT("socket"), TEXT("keepWorldTransform"), nullptr };
		static const TCHAR* const GMifDescNotes_attach_actor[] = { TEXT("actorPath"), TEXT("this endpoint takes TWO actors - spell them child and parent") ,  TEXT("attachTo"), TEXT("spell it parent") ,  TEXT("target"), TEXT("spell it parent"), nullptr };
		static const TCHAR* const GMifDescKeys_detach_actor[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("keepWorldTransform"), nullptr };
		static const TCHAR* const GMifDescNotes_detach_actor[] = { TEXT("child"), TEXT("spell it actorPath - detach takes only the child") ,  TEXT("parent"), TEXT("not accepted - detach_actor detaches the named actor from ") TEXT("whatever it is attached to"), nullptr };
		static const TCHAR* const GMifDescKeys_spawn_actor_in_level[] = { TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"), TEXT("mesh"), TEXT("staticMesh"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_actor_in_level[] = { TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials") ,  TEXT("name"), TEXT("an actor's display name is 'label'; its object name is assigned by the engine"), nullptr };
		static const TCHAR* const GMifDescKeys_set_actor_transform[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("location"), TEXT("rotation"), TEXT("scale"), TEXT("relative"), nullptr };
		static const TCHAR* const GMifDescNotes_set_actor_transform[] = { TEXT("transform"), TEXT("pass location / rotation / scale as separate keys") ,  TEXT("yaw"), TEXT("rotation accepts {pitch,yaw,roll} or {x,y,z} — there is no bare yaw here"), nullptr };
		static const TCHAR* const GMifDescKeys_set_actor_label[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_set_actor_label[] = { TEXT("name"), TEXT("the World Outliner display name is 'label'; the object name is engine-assigned and is not renamed here") ,  TEXT("newLabel"), TEXT("the key is 'label'"), nullptr };
		static const TCHAR* const GMifDescKeys_delete_level_actor[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_delete_level_actor[] = { TEXT("force"), TEXT("the confirmation key is 'confirm' and it must be true") ,  TEXT("actorPaths"), TEXT("this deletes ONE actor — call it once per actor; 'actorPaths' is select_level_actors' key"), nullptr };
		static const TCHAR* const GMifDescKeys_select_level_actors[] = { TEXT("actorPaths"), TEXT("clear"), nullptr };
		static const TCHAR* const GMifDescNotes_select_level_actors[] = { TEXT("actorPath"), TEXT("the key here is the PLURAL 'actorPaths' and it takes an array — pass [path] for a single actor") ,  TEXT("actors"), TEXT("the key is 'actorPaths'"), nullptr };
		static const TCHAR* const GMifDescKeys_create_struct[] = { TEXT("path"), TEXT("members"), nullptr };
		static const TCHAR* const GMifDescNotes_create_struct[] = { TEXT("name"), TEXT("the struct's name comes from the last segment of path - pass path:\"/Game/Types/S_Foo\"") ,  TEXT("struct"), TEXT("create_struct MAKES the struct; the new asset location goes in path. To edit an existing struct use add_struct_member") ,  TEXT("structPath"), TEXT("the new asset location parameter is called path (structPath is what the response returns)") ,  TEXT("fields"), TEXT("the member list parameter is called members[]"), nullptr };
		static const TCHAR* const GMifDescKeys_create_datatable[] = { TEXT("path"), TEXT("rowStruct"), TEXT("struct"), nullptr };
		static const TCHAR* const GMifDescNotes_create_datatable[] = { TEXT("name"), TEXT("the table's name comes from the last segment of path - pass path:\"/Game/Foo/DT_Bar\"") ,  TEXT("rows"), TEXT("create_datatable only MAKES the empty table; fill it with write_datatable_rows") ,  TEXT("rowType"), TEXT("spell it rowStruct") ,  TEXT("structPath"),TEXT("pass the row struct as rowStruct; path is the NEW table's location"), nullptr };
		static const TCHAR* const GMifDescKeys_create_asset[] = { TEXT("path"), TEXT("class"), TEXT("assetClass"), TEXT("className"), TEXT("properties"), nullptr };
		static const TCHAR* const GMifDescNotes_create_asset[] = { TEXT("parentClass"), TEXT("that is create_blueprint's key - this endpoint instantiates an existing class rather than authoring a new one") ,  TEXT("blueprintType"), TEXT("create_asset makes a DATA asset, not a blueprint - use create_blueprint for those") ,  TEXT("rowStruct"), TEXT("that is create_datatable's key"), nullptr };
		static const TCHAR* const GMifDescKeys_set_struct_member[] = { TEXT("struct"), TEXT("structPath"), TEXT("path"), TEXT("member"), TEXT("memberName"), TEXT("guid"), TEXT("newName"), TEXT("type"), TEXT("container"), TEXT("valueType"), TEXT("default"), nullptr };
		static const TCHAR* const GMifDescNotes_set_struct_member[] = { TEXT("name"), TEXT("ambiguous here - 'member' names the member to change and 'newName' is what to call it") ,  TEXT("index"), TEXT("members are addressed by NAME or GUID, not position; reordering is not supported (it would change every Make/Break Struct pin order)") ,  TEXT("rename"), TEXT("the parameter is newName"), nullptr };
		static const TCHAR* const GMifDescKeys_list_struct_members[] = { TEXT("struct"), TEXT("structPath"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_add_struct_member[] = { TEXT("struct"), TEXT("structPath"), TEXT("path"), TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"), TEXT("default"), nullptr };
		static const TCHAR* const GMifDescNotes_add_struct_member[] = { TEXT("class"), TEXT("the class belongs IN the type string, not in its own key: type:\"object:SceneComponent\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X") ,  TEXT("subType"), TEXT("use type:\"object:X\" for the referenced class, or valueType for a map's value type") ,  TEXT("memberName"), TEXT("the member name parameter is called name") ,  TEXT("defaultValue"), TEXT("the parameter is called default"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_struct_member[] = { TEXT("struct"), TEXT("structPath"), TEXT("path"), TEXT("name"), TEXT("guid"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_struct_member[] = { TEXT("member"), TEXT("the member is addressed by name or by guid") ,  TEXT("memberName"), TEXT("the member name parameter is called name") ,  TEXT("index"), TEXT("struct members are addressed by name or guid, never by index - index is remove_enum_value's parameter"), nullptr };
		static const TCHAR* const GMifDescKeys_create_enum[] = { TEXT("path"), TEXT("values"), nullptr };
		static const TCHAR* const GMifDescNotes_create_enum[] = { TEXT("name"), TEXT("the enum's name comes from the last segment of path - pass path:\"/Game/Types/E_Foo\"") ,  TEXT("enum"), TEXT("create_enum MAKES the enum; the new asset location goes in path. To extend an existing enum use add_enum_value") ,  TEXT("enumPath"), TEXT("the new asset location parameter is called path (enumPath is what the response returns)") ,  TEXT("entries"), TEXT("the entry list parameter is called values[]") ,  TEXT("members"), TEXT("members[] is create_struct's parameter; an enum's entries go in values[]"), nullptr };
		static const TCHAR* const GMifDescKeys_add_enum_value[] = { TEXT("enum"), TEXT("enumPath"), TEXT("path"), TEXT("value"), TEXT("name"), TEXT("displayName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_enum_value[] = { TEXT("values"), TEXT("add_enum_value appends ONE entry; pass value:\"Ready\". The values[] array belongs to create_enum") ,  TEXT("index"), TEXT("the new entry is always appended; its index comes back in the response"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_enum_value[] = { TEXT("enum"), TEXT("enumPath"), TEXT("path"), TEXT("index"), TEXT("value"), TEXT("name"), TEXT("displayName"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_enum_value[] = { TEXT("guid"), TEXT("enum entries are addressed by index or by value/display name, never by guid - guid is remove_struct_member's parameter") ,  TEXT("values"), TEXT("remove_enum_value removes ONE entry; pass value:\"Ready\" or index:2"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_animation[] = { TEXT("assetPath"), TEXT("path"), TEXT("animation"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_animation[] = { TEXT("name"), TEXT("this endpoint needs an object PATH - assetPath (aliases: path, animation, asset). list_animations returns assetPath values you can paste straight in") ,  TEXT("skeleton"), TEXT("not an input here - the skeleton is REPORTED in the response; to filter a LIST by skeleton use list_animations") ,  TEXT("blueprintId"), TEXT("this reads animation DATA assets (sequence/montage/blend space/composite). For an Animation BLUEPRINT use list_graphs/list_nodes, which recurse into state machines and transition graphs"), nullptr };
		static const TCHAR* const GMifDescKeys_add_anim_state[] = { TEXT("blueprintId"), TEXT("path"), TEXT("graphId"), TEXT("graph"), TEXT("name"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_anim_state[] = { TEXT("stateName"), TEXT("spell it name") ,  TEXT("nodeClass"), TEXT("not accepted - this endpoint makes a UAnimStateNode. Use ") TEXT("connect_pins between two states to make a transition; the ") TEXT("state machine schema creates the transition node itself") ,  TEXT("fromState"), TEXT("transitions are made by connect_pins between two states, ") TEXT("not here"), nullptr };
		static const TCHAR* const GMifDescKeys_add_anim_notify[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("track"), TEXT("time"), TEXT("notifyClass"), TEXT("notifyStateClass"), TEXT("duration"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_add_anim_notify[] = { TEXT("triggerTime"), TEXT("spell it time") ,  TEXT("class"), TEXT("spell it notifyClass, or notifyStateClass for a state"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_anim_notify[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("name"), TEXT("track"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_anim_notify[] = { TEXT("notifyName"), TEXT("spell it name"), nullptr };
		static const TCHAR* const GMifDescKeys_add_sync_marker[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("name"), TEXT("marker"), TEXT("time"), TEXT("trackIndex"), nullptr };
		static const TCHAR* const GMifDescNotes_add_sync_marker[] = { TEXT("track"), TEXT("spell it trackIndex - this takes the INDEX, not the track name") ,  TEXT("notify"), TEXT("a sync marker is not a notify - add_anim_notify places those") ,  TEXT("montage"), TEXT("sync markers live on an AnimSequence; a montage has none"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_sync_marker[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("name"), TEXT("marker"), TEXT("time"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_sync_marker[] = { TEXT("index"), TEXT("markers are addressed by NAME (and optionally time); an array index would shift under you as soon as one is removed") ,  TEXT("all"), TEXT("omitting time already removes every marker with that name"), nullptr };
		static const TCHAR* const GMifDescKeys_add_anim_notify_track[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("track"), nullptr };
		static const TCHAR* const GMifDescNotes_add_anim_notify_track[] = { TEXT("trackName"), TEXT("spell it track") ,  TEXT("index"), TEXT("tracks are addressed by NAME here, not index"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_anim_notify_track[] = { TEXT("assetPath"), TEXT("path"), TEXT("asset"), TEXT("track"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_anim_notify_track[] = { TEXT("trackName"), TEXT("spell it track"), nullptr };
		static const TCHAR* const GMifDescKeys_list_sockets[] = { TEXT("path"), TEXT("assetPath"), TEXT("mesh"), nullptr };
		static const TCHAR* const GMifDescNotes_list_sockets[] = { TEXT("blueprintId"), TEXT("sockets live on the MESH ASSET, not on a blueprint - take the mesh path from the component's StaticMesh/SkeletalMesh property, or from find_assets") ,  TEXT("componentName"), TEXT("same: resolve the component's mesh asset first, then pass that path here"), nullptr };
		static const TCHAR* const GMifDescKeys_list_bones[] = { TEXT("path"), TEXT("assetPath"), TEXT("skeleton"), TEXT("mesh"), TEXT("nameContains"), TEXT("includeTransforms"), TEXT("root"), nullptr };
		static const TCHAR* const GMifDescNotes_list_bones[] = { TEXT("socket"), TEXT("sockets are list_sockets - this lists BONES") ,  TEXT("depth"), TEXT("depth is reported per bone; there is no depth limit parameter"), nullptr };
		static const TCHAR* const GMifDescKeys_list_virtual_bones[] = { TEXT("path"), TEXT("assetPath"), TEXT("skeleton"), TEXT("mesh"), nullptr };
		static const TCHAR* const GMifDescNotes_list_virtual_bones[] = { TEXT("bone"), TEXT("this lists ALL virtual bones - filter the result rather than the query"), nullptr };
		static const TCHAR* const GMifDescKeys_list_morph_targets[] = { TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("skeletalMesh"), TEXT("lod"), nullptr };
		static const TCHAR* const GMifDescNotes_list_morph_targets[] = { TEXT("name"), TEXT("this lists ALL morph targets - filter the result rather than the query"), nullptr };
		static const TCHAR* const GMifDescKeys_analyze_skeletal_split[] = { TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("skeletalMesh"), TEXT("lod"), nullptr };
		static const TCHAR* const GMifDescNotes_analyze_skeletal_split[] = { TEXT("bone"), TEXT("this reports EVERY bone and which sections use it - filter the result rather than the query") ,  TEXT("split"), TEXT("this only ANALYSES. Splitting creates assets, which this bridge does not do."), nullptr };
		static const TCHAR* const GMifDescKeys_set_blendspace_samples[] = { TEXT("assetPath"), TEXT("path"), TEXT("blendSpace"), TEXT("samples"), TEXT("clear"), nullptr };
		static const TCHAR* const GMifDescNotes_set_blendspace_samples[] = { TEXT("axis"), TEXT("set the axis with set_property propertyPath=BlendParameters[0].Max (also .Min, .DisplayName, .GridNum)") ,  TEXT("animation"), TEXT("samples is an ARRAY of objects, each with its own animation and x"), nullptr };
		static const TCHAR* const GMifDescKeys_set_bone_translation_retargeting[] = { TEXT("skeletonPath"), TEXT("path"), TEXT("boneName"), TEXT("bone"), TEXT("mode"), TEXT("childrenToo"), nullptr };
		static const TCHAR* const GMifDescKeys_list_water_bodies[] = { TEXT("type"), TEXT("waterBodyType"), TEXT("nameContains"), nullptr };
		static const TCHAR* const GMifDescNotes_list_water_bodies[] = { TEXT("path"), TEXT("this lists every water body in the OPEN level; describe_water_body takes a path") ,  TEXT("zone"), TEXT("filtering by water zone is not supported - each body reports its waterZone and you can filter on that"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_water_body[] = { TEXT("path"), TEXT("actorPath"), TEXT("includeSplinePoints"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_water_body[] = { TEXT("name"), TEXT("pass the actor PATH - list_water_bodies reports actorPath for each"), nullptr };
		static const TCHAR* const GMifDescKeys_create_water_body[] = { TEXT("type"), TEXT("waterBodyType"), TEXT("label"), TEXT("x"), TEXT("y"), TEXT("z"), TEXT("points"), nullptr };
		static const TCHAR* const GMifDescNotes_create_water_body[] = { TEXT("spline"), TEXT("spell it points - an array of {x,y,z} in WORLD space") ,  TEXT("class"), TEXT("pass type instead - the actor class is derived from it, because the four water body classes are not interchangeable") ,  TEXT("zone"), TEXT("a body finds its own AWaterZone by overlap; create the zone separately with create_water_zone"), nullptr };
		static const TCHAR* const GMifDescKeys_create_water_zone[] = { TEXT("x"), TEXT("y"), TEXT("z"), TEXT("extentX"), TEXT("extentY"), TEXT("label"), nullptr };
		static const TCHAR* const GMifDescNotes_create_water_zone[] = { TEXT("extent"), TEXT("pass extentX and extentY - a zone's extent is a 2D size, and one number would have to guess whether you meant a square or a diameter") ,  TEXT("bodies"), TEXT("a zone does not take a body list - each AWaterBody finds its zone by OVERLAP, so place the zone over them and the response reports which ones it picked up") ,  TEXT("resolution"), TEXT("render target resolution comes from the engine's Water editor settings through the actor factory; there is no override here"), nullptr };
		static const TCHAR* const GMifDescKeys_set_water_body_spline[] = { TEXT("path"), TEXT("actorPath"), TEXT("points"), nullptr };
		static const TCHAR* const GMifDescNotes_set_water_body_spline[] = { TEXT("index"), TEXT("this replaces the WHOLE spline; there is no single-point setter, because ResetSpline is the only engine entry point that rebuilds the body's derived data") ,  TEXT("add"), TEXT("there is no append - pass the full point list you want"), nullptr };
		static const TCHAR* const GMifDescKeys_list_ik_rig[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), nullptr };
		static const TCHAR* const GMifDescNotes_list_ik_rig[] = { TEXT("retargeter"), TEXT("an IKRetargeter is a different asset - read it with list_retarget_chain_mapping") ,  TEXT("mesh"), TEXT("the mesh is reported, not selected; set it with set_ik_rig_mesh"), nullptr };
		static const TCHAR* const GMifDescKeys_list_ik_solver_types[] = { nullptr };
		static const TCHAR* const GMifDescNotes_list_ik_solver_types[] = { TEXT("path"), TEXT("this lists solver CLASSES available in the engine, not the solvers on a particular rig - list_ik_rig reports those"), nullptr };
		static const TCHAR* const GMifDescKeys_add_ik_solver[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("solverClass"), TEXT("solver"), nullptr };
		static const TCHAR* const GMifDescNotes_add_ik_solver[] = { TEXT("goal"), TEXT("a solver is added first, then a goal is connected to it with set_ik_goal_solver_connection"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_ik_solver[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("index"), TEXT("solverIndex"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_ik_solver[] = { TEXT("solverClass"), TEXT("solvers are removed by INDEX - a rig may hold several of one class"), nullptr };
		static const TCHAR* const GMifDescKeys_set_ik_solver[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("index"), TEXT("solverIndex"), TEXT("rootBone"), TEXT("endBone"), TEXT("enabled"), nullptr };
		static const TCHAR* const GMifDescNotes_set_ik_solver[] = { TEXT("goal"), TEXT("goals attach to a solver via set_ik_goal_solver_connection, not here"), nullptr };
		static const TCHAR* const GMifDescKeys_add_ik_goal[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName"), TEXT("bone"), TEXT("boneName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_ik_goal[] = { TEXT("transform"), TEXT("a goal's transform is a preview pose, not authoring, and is deliberately not settable here - the engine call asserts on an unknown goal name") ,  TEXT("solver"), TEXT("connect the goal to a solver afterwards with set_ik_goal_solver_connection"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_ik_goal[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName"), nullptr };
		static const TCHAR* const GMifDescKeys_set_ik_goal_bone[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName"), TEXT("bone"), TEXT("boneName"), nullptr };
		static const TCHAR* const GMifDescKeys_set_ik_goal_solver_connection[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("goalName"), TEXT("solverIndex"), TEXT("index"), TEXT("connected"), nullptr };
		static const TCHAR* const GMifDescKeys_set_ik_rig_mesh[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("mesh"), TEXT("skeletalMesh"), nullptr };
		static const TCHAR* const GMifDescNotes_set_ik_rig_mesh[] = { TEXT("skeleton"), TEXT("an IK Rig is built from a SKELETAL MESH, not a Skeleton asset - pass the mesh") ,  TEXT("previewMesh"), TEXT("the parameter is 'mesh'; it becomes the preview mesh AND builds the rig's skeleton"), nullptr };
		static const TCHAR* const GMifDescKeys_set_ik_rig_retarget_root[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("bone"), TEXT("boneName"), TEXT("root"), nullptr };
		static const TCHAR* const GMifDescNotes_set_ik_rig_retarget_root[] = { TEXT("chain"), TEXT("the retarget ROOT is a single bone, not a chain"), nullptr };
		static const TCHAR* const GMifDescKeys_add_ik_retarget_chain[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("chainName"), TEXT("startBone"), TEXT("endBone"), TEXT("goal"), TEXT("goalName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_ik_retarget_chain[] = { TEXT("bones"), TEXT("a chain is defined by its two ENDS: startBone and endBone. The bones between them are implied by the hierarchy"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_ik_retarget_chain[] = { TEXT("path"), TEXT("assetPath"), TEXT("rig"), TEXT("name"), TEXT("chainName"), nullptr };
		static const TCHAR* const GMifDescKeys_set_retarget_rigs[] = { TEXT("path"), TEXT("assetPath"), TEXT("retargeter"), TEXT("source"), TEXT("sourceRig"), TEXT("target"), TEXT("targetRig"), nullptr };
		static const TCHAR* const GMifDescNotes_set_retarget_rigs[] = { TEXT("mesh"), TEXT("the preview meshes come from the rigs themselves - set them on the rigs with set_ik_rig_mesh"), nullptr };
		static const TCHAR* const GMifDescKeys_auto_map_retarget_chains[] = { TEXT("path"), TEXT("assetPath"), TEXT("retargeter"), TEXT("mode"), TEXT("remapExisting"), TEXT("force"), nullptr };
		static const TCHAR* const GMifDescNotes_auto_map_retarget_chains[] = { TEXT("sourceChain"), TEXT("this maps ALL chains automatically; set one by hand with set_retarget_chain_mapping"), nullptr };
		static const TCHAR* const GMifDescKeys_set_retarget_chain_mapping[] = { TEXT("path"), TEXT("assetPath"), TEXT("retargeter"), TEXT("targetChain"), TEXT("sourceChain"), nullptr };
		static const TCHAR* const GMifDescNotes_set_retarget_chain_mapping[] = { TEXT("chain"), TEXT("a mapping has two ends: targetChain and sourceChain"), nullptr };
		static const TCHAR* const GMifDescKeys_list_retarget_chain_mapping[] = { TEXT("path"), TEXT("assetPath"), TEXT("retargeter"), nullptr };
		static const TCHAR* const GMifDescNotes_list_retarget_chain_mapping[] = { TEXT("rig"), TEXT("an IKRigDefinition is a different asset - read it with list_ik_rig"), nullptr };
		static const TCHAR* const GMifDescKeys_set_niagara_component_parameter[] = { TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("name"), TEXT("parameter"), TEXT("type"), TEXT("value"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_set_niagara_component_parameter[] = { TEXT("system"), TEXT("this sets an override on a PLACED COMPONENT, not on the system asset - editing the asset would change every instance, and on a COOKED system it is a known editor crash (docs/02 section 6c)") ,  TEXT("path"), TEXT("spell it actorPath - this addresses a placed actor, not an asset"), nullptr };
		static const TCHAR* const GMifDescKeys_list_sequence_bindings[] = { TEXT("path"), TEXT("assetPath"), TEXT("sequence"), nullptr };
		static const TCHAR* const GMifDescNotes_list_sequence_bindings[] = { TEXT("binding"), TEXT("this lists ALL bindings; filter the result"), nullptr };
		static const TCHAR* const GMifDescKeys_add_sequence_possessable[] = { TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("actorPath"), TEXT("actor"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_add_sequence_possessable[] = { TEXT("class"), TEXT("the class is taken from the actor - bind the actor you mean") ,  TEXT("name"), TEXT("the name is taken from the actor's label, so the sequence matches the outliner"), nullptr };
		static const TCHAR* const GMifDescKeys_add_sequence_track[] = { TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("guid"), TEXT("binding"), TEXT("trackClass"), TEXT("confirm"), TEXT("root"), TEXT("cameraCut"), TEXT("time"), nullptr };
		static const TCHAR* const GMifDescNotes_add_sequence_track[] = { TEXT("actorPath"), TEXT("bind the actor first with add_sequence_possessable, then pass its guid here") ,  TEXT("master"), TEXT("spell it root - AddMasterTrack was deprecated in 5.2 and is gone entirely from 5.7; AddTrack is the replacement"), nullptr };
		static const TCHAR* const GMifDescKeys_add_sequence_section[] = { TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("guid"), TEXT("binding"), TEXT("trackClass"), TEXT("trackIndex"), TEXT("startTime"), TEXT("endTime"), TEXT("rowIndex"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_add_sequence_section[] = { TEXT("startFrame"), TEXT("times here are SECONDS - the tick conversion is done for ") TEXT("you from the sequence's own tick resolution") ,  TEXT("duration"), TEXT("pass startTime and endTime, not a duration"), nullptr };
		static const TCHAR* const GMifDescKeys_set_sequence_keys[] = { TEXT("path"), TEXT("assetPath"), TEXT("sequence"), TEXT("guid"), TEXT("binding"), TEXT("trackClass"), TEXT("trackIndex"), TEXT("sectionIndex"), TEXT("channel"), TEXT("keys"), TEXT("replace"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_set_sequence_keys[] = { TEXT("frame"), TEXT("key times are SECONDS - the tick conversion is done for you") ,  TEXT("channelName"), TEXT("spell it channel"), nullptr };
		static const TCHAR* const GMifDescKeys_list_state_trees[] = { TEXT("pathPrefix"), TEXT("prefix"), nullptr };
		static const TCHAR* const GMifDescNotes_list_state_trees[] = { TEXT("tree"), TEXT("this LISTS them; describe_state_tree takes one") ,  TEXT("behaviorTree"), TEXT("different system - list via find_assets, and describe_behavior_tree reads one"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_state_tree[] = { TEXT("path"), TEXT("assetPath"), TEXT("tree"), nullptr };
		static const TCHAR* const GMifDescKeys_list_gameplay_tags[] = { TEXT("filter"), TEXT("search"), TEXT("onlyExplicit"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_gameplay_tags[] = { TEXT("tag"), TEXT("this LISTS tags; describe_gameplay_tag takes one") ,  TEXT("category"), TEXT("gameplay tags have no categories - the hierarchy IS the grouping, so filter on a prefix like 'Ability.'"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_gameplay_tag[] = { TEXT("tag"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_gameplay_tag[] = { TEXT("filter"), TEXT("that is list_gameplay_tags; this describes ONE tag"), nullptr };
		static const TCHAR* const GMifDescKeys_add_gameplay_tag[] = { TEXT("tag"), TEXT("comment"), TEXT("source"), TEXT("transient"), nullptr };
		static const TCHAR* const GMifDescNotes_add_gameplay_tag[] = { TEXT("name"), TEXT("spell it tag - the full dotted tag name") ,  TEXT("tagName"), TEXT("spell it tag - the full dotted tag name") ,  TEXT("temporary"), TEXT("spell it transient - session-only, nothing written to disk"), nullptr };
		static const TCHAR* const GMifDescKeys_live_coding_status[] = { nullptr };
		static const TCHAR* const GMifDescNotes_live_coding_status[] = { TEXT("enable"), TEXT("this only READS the state. Enabling Live Coding mid-session changes how the editor holds its DLLs and is a decision for a person at the keyboard."), nullptr };
		static const TCHAR* const GMifDescKeys_live_coding_compile[] = { TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_live_coding_compile[] = { TEXT("wait"), TEXT("there is deliberately NO wait option - blocking here takes the whole bridge off the air for the length of a C++ compile. Poll live_coding_status instead.") ,  TEXT("target"), TEXT("Live Coding compiles whatever changed; it does not take a target"), nullptr };
		static const TCHAR* const GMifDescKeys_list_pcg_graphs[] = { TEXT("pathPrefix"), TEXT("prefix"), nullptr };
		static const TCHAR* const GMifDescNotes_list_pcg_graphs[] = { TEXT("graph"), TEXT("this LISTS graphs; describe_pcg_graph takes one"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_pcg_graph[] = { TEXT("path"), TEXT("assetPath"), TEXT("graph"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_pcg_graph[] = { TEXT("component"), TEXT("that is a placed component, not the graph asset - list_pcg_components reports those"), nullptr };
		static const TCHAR* const GMifDescKeys_list_pcg_components[] = { nullptr };
		static const TCHAR* const GMifDescNotes_list_pcg_components[] = { TEXT("path"), TEXT("this reads the open LEVEL, not an asset. list_pcg_graphs takes a path."), nullptr };
		static const TCHAR* const GMifDescKeys_pcg_generate[] = { TEXT("actorPath"), TEXT("actor"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_pcg_generate[] = { TEXT("graph"), TEXT("generation runs a COMPONENT in the level, not a graph asset - list_pcg_components reports the components"), nullptr };
		static const TCHAR* const GMifDescKeys_pcg_cleanup[] = { TEXT("actorPath"), TEXT("actor"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_behavior_tree[] = { TEXT("path"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_behavior_tree[] = { TEXT("blueprintId"), TEXT("a BehaviorTree is its own asset, not a blueprint - find one with find_assets {class: BehaviorTree}"), nullptr };
		static const TCHAR* const GMifDescKeys_list_blackboard_keys[] = { TEXT("path"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_list_blackboard_keys[] = { TEXT("behaviorTree"), TEXT("pass the BLACKBOARD's path; describe_behavior_tree reports which blackboard a tree uses"), nullptr };
		static const TCHAR* const GMifDescKeys_add_blackboard_key[] = { TEXT("path"), TEXT("assetPath"), TEXT("blackboard"), TEXT("name"), TEXT("key"), TEXT("type"), TEXT("keyType"), TEXT("instanceSynced"), TEXT("category"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_add_blackboard_key[] = { TEXT("behaviorTree"), TEXT("keys live on the BLACKBOARD asset, not on the tree - describe_behavior_tree reports which blackboard a tree uses") ,  TEXT("value"), TEXT("a blackboard key has no value at author time - values exist per running instance"), nullptr };
		static const TCHAR* const GMifDescKeys_list_animations[] = { TEXT("filter"), TEXT("skeleton"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_animations[] = { TEXT("nameContains"), TEXT("the substring filter here is 'filter', and it matches the FULL object path, not just the asset name") ,  TEXT("path"), TEXT("there is no path/root parameter - put the folder in 'filter', e.g. filter:'/Game/Anims/'") ,  TEXT("count"), TEXT("'count' is an OUTPUT field - the cap is 'limit' (default 200, max 5000); read 'truncated' to see whether you hit it"), nullptr };
		static const TCHAR* const GMifDescKeys_add_anim_node[] = { TEXT("graphId"), TEXT("nodeClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_anim_node[] = { TEXT("sequence"), TEXT("set the animation afterwards with set_property propertyPath=Node.Sequence on the returned node - the field differs per node type") ,  TEXT("slotName"), TEXT("set it afterwards with set_property propertyPath=Node.SlotName on the returned node") ,  TEXT("blueprintId"), TEXT("a node is added to a GRAPH; pass graphId (list_graphs shows them, e.g. \"AnimGraph\")"), nullptr };
		static const TCHAR* const GMifDescKeys_delete_asset[] = { TEXT("path"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_delete_asset[] = { TEXT("packageName"), TEXT("spell it path - delete_asset takes the package under 'path'; an object path is accepted and reduced to its package") ,  TEXT("objectPath"), TEXT("spell it path - the whole PACKAGE is deleted, not one object inside it") ,  TEXT("force"), TEXT("there is no force - deletion is gated on confirm=true and still fails if the asset is still referenced"), nullptr };
		static const TCHAR* const GMifDescKeys_close_asset_editors[] = { TEXT("path"), TEXT("objectPath"), TEXT("assetPath"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_close_asset_editors[] = { TEXT("all"), TEXT("closing EVERY asset editor is not offered - name the asset you mean") ,  TEXT("force"), TEXT("there is no force; this finds an open editor or reports that there is none"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_asset[] = { TEXT("path"), TEXT("newPath"), TEXT("renames"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_asset[] = { TEXT("newName"), TEXT("there is no newName - put the whole destination in newPath (e.g. /Game/Foo/NewName); its last segment becomes the new asset name") ,  TEXT("newPackageName"), TEXT("spell it newPath - newPackageName is a RESPONSE field only") ,  TEXT("destination"), TEXT("spell it newPath") ,  TEXT("assets"), TEXT("the array parameter is called renames[], and each entry is an OBJECT {path, newPath} rather than a bare path - a rename needs both halves") ,  TEXT("paths"), TEXT("the array parameter is called renames[], and each entry is {path, newPath}"), nullptr };
		static const TCHAR* const GMifDescKeys_fix_up_redirectors[] = { TEXT("path"), TEXT("confirm"), TEXT("dryRun"), TEXT("keepRedirectors"), TEXT("recursive"), nullptr };
		static const TCHAR* const GMifDescNotes_fix_up_redirectors[] = { TEXT("folder"), TEXT("spell it path") ,  TEXT("deleteRedirectors"), TEXT("inverted - pass keepRedirectors:true to KEEP them; deleting is the default because leaving them is what created the problem"), nullptr };
		static const TCHAR* const GMifDescKeys_duplicate_asset[] = { TEXT("path"), TEXT("newPath"), nullptr };
		static const TCHAR* const GMifDescNotes_duplicate_asset[] = { TEXT("confirm"), TEXT("duplicate_asset needs no confirm - it never overwrites; it fails if newPath is already taken") ,  TEXT("newName"), TEXT("there is no newName - put the whole destination in newPath (e.g. /Game/Foo/CopyName)") ,  TEXT("overwrite"), TEXT("NOT supported - duplicate_asset fails rather than clobbering an existing asset; delete_asset the old one first"), nullptr };
		static const TCHAR* const GMifDescKeys_get_collision[] = { TEXT("path"), TEXT("assetPath"), TEXT("mesh"), TEXT("staticMesh"), TEXT("lod"), nullptr };
		static const TCHAR* const GMifDescNotes_get_collision[] = { TEXT("profile"), TEXT("collision PROFILES are a project-wide list - list_collision_profiles reports those. This reads one mesh's own collision.") ,  TEXT("actorPath"), TEXT("this reads the MESH ASSET, not a placed actor. A component's collision overrides are a different question - get_property on the component reads those."), nullptr };
		static const TCHAR* const GMifDescKeys_remove_collision[] = { TEXT("path"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_collision[] = { TEXT("objectPath"), TEXT("spell it path") ,  TEXT("mesh"), TEXT("spell it path") ,  TEXT("shape"), TEXT("remove_collision takes no shape - it clears ALL simple collision. Use add_simplified_collision to add one back"), nullptr };
		static const TCHAR* const GMifDescKeys_add_simplified_collision[] = { TEXT("path"), TEXT("shape"), nullptr };
		static const TCHAR* const GMifDescNotes_add_simplified_collision[] = { TEXT("objectPath"), TEXT("spell it path") ,  TEXT("type"), TEXT("spell it shape") ,  TEXT("replace"), TEXT("there is no replace - this endpoint is additive. Call remove_collision first (the engine's own replace path is commented out in GeomFitUtils.cpp, so generating over existing collision would silently stack a second primitive)") ,  TEXT("sphyl"), TEXT("spell it shape=capsule"), nullptr };
		static const TCHAR* const GMifDescKeys_list_collision_profiles[] = { nullptr };
		static const TCHAR* const GMifDescNotes_list_collision_profiles[] = { TEXT("actorPath"), TEXT("this lists the PROJECT's profiles, not one object's - read an object's current profile with get_property on BodyInstance.CollisionProfileName"), nullptr };
		static const TCHAR* const GMifDescKeys_set_collision[] = { TEXT("objectPath"), TEXT("component"), TEXT("profile"), TEXT("collisionEnabled"), nullptr };
		static const TCHAR* const GMifDescNotes_set_collision[] = { TEXT("channel"), TEXT("per-channel responses come from the PROFILE - pick a profile that has the responses you want, and list_collision_profiles shows what each resolves to") ,  TEXT("blueprintId"), TEXT("collision lives on a COMPONENT: call list_components, take its templatePath, and pass that as objectPath"), nullptr };
		static const TCHAR* const GMifDescKeys_get_referencers[] = { TEXT("path"), TEXT("category"), TEXT("hard"), TEXT("includeEditorOnly"), TEXT("includeProperties"), nullptr };
		static const TCHAR* const GMifDescNotes_get_referencers[] = { TEXT("soft"), TEXT("spell it hard:false - one parameter with two states, rather than " "two that can disagree") ,  TEXT("recursive"), TEXT("this is one hop. project_dependency_graph walks the graph"), nullptr };
		static const TCHAR* const GMifDescKeys_get_dependencies[] = { TEXT("path"), TEXT("category"), TEXT("hard"), TEXT("includeEditorOnly"), TEXT("includeProperties"), nullptr };
		static const TCHAR* const GMifDescNotes_get_dependencies[] = { TEXT("soft"), TEXT("spell it hard:false - one parameter with two states, rather than " "two that can disagree") ,  TEXT("recursive"), TEXT("this is one hop. project_dependency_graph walks the graph"), nullptr };
		static const TCHAR* const GMifDescKeys_audit_unused[] = { TEXT("pathPrefix"), TEXT("class"), TEXT("includeAll"), TEXT("limit"), TEXT("rescan"), TEXT("excludeReferencers"), TEXT("excludeReferencer"), TEXT("ignoreReferencers"), nullptr };
		static const TCHAR* const GMifDescKeys_create_editable_child[] = { TEXT("sourceAsset"), TEXT("childPath"), TEXT("variant"), nullptr };
		static const TCHAR* const GMifDescNotes_create_editable_child[] = { TEXT("blueprintId"), TEXT("spell it sourceAsset - pass the cooked BP's _C class path (/Game/X/BP_Foo.BP_Foo_C) or its asset path") ,  TEXT("path"), TEXT("the SOURCE is sourceAsset; the DESTINATION is childPath") ,  TEXT("source"), TEXT("spell it sourceAsset") ,  TEXT("targetPath"), TEXT("spell it childPath") ,  TEXT("asChild"), TEXT("there is no boolean form - it is variant:\"child\" (the default) vs variant:\"sibling\"") ,  TEXT("fullParent"), TEXT("there is no boolean form - it is variant:\"sibling_full\" (alias: \"full\")") ,  TEXT("name"), TEXT("the new asset's name comes from childPath - pass the full destination package path"), nullptr };
		static const TCHAR* const GMifDescKeys_compile[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_compile[] = { TEXT("save"), TEXT("compile does not write to disk - call save_blueprint {blueprintId} afterwards to persist") ,  TEXT("dryRun"), TEXT("compile always commits the compiled class; validate {blueprintId} is the dry-run form and returns the same messages"), nullptr };
		static const TCHAR* const GMifDescKeys_run_console[] = { TEXT("command"), TEXT("cmd"), TEXT("world"), TEXT("captureOutput"), nullptr };
		static const TCHAR* const GMifDescNotes_run_console[] = { TEXT("filter"), TEXT("log-line filtering belongs to run_console_captured, which brackets GLog; this endpoint returns the command's own output device text"), nullptr };
		static const TCHAR* const GMifDescKeys_validate[] = { TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_validate[] = { TEXT("dryRun"), TEXT("validate is ALWAYS a dry run and reports dryRun:true in the response; it is not an input") ,  TEXT("save"), TEXT("validate never writes to disk - run save_blueprint {blueprintId} once the compile is clean"), nullptr };
		static const TCHAR* const GMifDescKeys_self_audit[] = { TEXT("summaryOnly"), TEXT("compact"), TEXT("includeEndpointDetails"), TEXT("includeEndpoints"), nullptr };
		static const TCHAR* const GMifDescNotes_self_audit[] = { TEXT("verbose"), TEXT("the FULL response is the default; pass summaryOnly:true for the compact form") ,  TEXT("endpoint"), TEXT("self_audit describes the whole surface - use describe_endpoint {endpoint} for one"), nullptr };
		static const TCHAR* const GMifDescKeys_batch[] = { TEXT("ops"), TEXT("blueprintId"), TEXT("path"), TEXT("backup"), TEXT("compileAtEnd"), nullptr };
		static const TCHAR* const GMifDescNotes_batch[] = { TEXT("operations"), TEXT("spell it ops") ,  TEXT("graphId"), TEXT("graphId belongs on each op inside ops, not on the batch envelope"), nullptr };
		static const TCHAR* const GMifDescKeys_list_transactions[] = { TEXT("limit"), TEXT("count"), TEXT("max"), TEXT("offset"), TEXT("start"), TEXT("includeObjects"), TEXT("include_objects"), nullptr };
		static const TCHAR* const GMifDescKeys_undo_transactions[] = { TEXT("count"), TEXT("n"), TEXT("steps"), TEXT("toIndex"), TEXT("to_index"), TEXT("allowRedo"), TEXT("allow_redo"), TEXT("canRedo"), nullptr };
		static const TCHAR* const GMifDescKeys_redo_transactions[] = { TEXT("count"), TEXT("n"), TEXT("steps"), TEXT("toIndex"), TEXT("to_index"), nullptr };
		static const TCHAR* const GMifDescKeys_project_paths[] = { nullptr };
		static const TCHAR* const GMifDescNotes_project_paths[] = { TEXT("project"), TEXT("not supported - this reports the paths of the RUNNING editor's own project; there is no way to ask it about a different one") ,  TEXT("plugin"), TEXT("not supported - pluginsDir is the project's Plugins folder; a specific plugin's own directory is not reported"), nullptr };
		static const TCHAR* const GMifDescKeys_list_dirty_packages[] = { TEXT("kind"), nullptr };
		static const TCHAR* const GMifDescKeys_save_dirty_packages[] = { TEXT("maps"), TEXT("saveMaps"), TEXT("save_maps"), TEXT("content"), TEXT("saveContent"), TEXT("save_content"), TEXT("dryRun"), TEXT("dry_run"), nullptr };
		static const TCHAR* const GMifDescKeys_create_material[] = { TEXT("path"), TEXT("assetPath"), TEXT("domain"), TEXT("materialDomain"), TEXT("blendMode"), TEXT("initialTexture"), nullptr };
		static const TCHAR* const GMifDescKeys_create_material_function[] = { TEXT("path"), TEXT("assetPath"), TEXT("description"), TEXT("exposeToLibrary"), nullptr };
		static const TCHAR* const GMifDescNotes_create_material_function[] = { TEXT("kind"), TEXT("not implemented — nothing in this build authors material layers; layer/layerBlend function kinds are read-only here"), nullptr };
		static const TCHAR* const GMifDescKeys_add_material_expression[] = { TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("class"), TEXT("expressionClass"), TEXT("type"), TEXT("x"), TEXT("nodePosX"), TEXT("posX"), TEXT("y"), TEXT("nodePosY"), TEXT("posY"), TEXT("properties"), TEXT("props"), TEXT("asset"), TEXT("selectedAsset"), nullptr };
		static const TCHAR* const GMifDescKeys_connect_material_expressions[] = { TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("from"), TEXT("fromExpression"), TEXT("fromOutput"), TEXT("fromOutputName"), TEXT("to"), TEXT("toExpression"), TEXT("toInput"), TEXT("toInputName"), nullptr };
		static const TCHAR* const GMifDescKeys_connect_material_property[] = { TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("from"), TEXT("fromExpression"), TEXT("fromOutput"), TEXT("fromOutputName"), TEXT("property"), TEXT("materialProperty"), nullptr };
		static const TCHAR* const GMifDescKeys_delete_material_expression[] = { TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("expression"), TEXT("name"), TEXT("all"), TEXT("deleteAll"), nullptr };
		static const TCHAR* const GMifDescKeys_list_material_expressions[] = { TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("includeConnections"), TEXT("includeProperties"), nullptr };
		static const TCHAR* const GMifDescKeys_list_material_parameters[] = { TEXT("path"), TEXT("material"), TEXT("assetPath"), TEXT("types"), TEXT("group"), TEXT("layers"), nullptr };
		static const TCHAR* const GMifDescNotes_list_material_parameters[] = { TEXT("parameterName"), TEXT("this LISTS parameters - to read one value use get_property on a material instance, and to write one use set_material_parameter") ,  TEXT("includeExpressions"), TEXT("that is list_material_expressions, which returns nothing on a COOKED material - this endpoint exists precisely because the cached parameter table survives cook and the expression graph does not"), nullptr };
		static const TCHAR* const GMifDescKeys_set_material_layers[] = { TEXT("path"), TEXT("material"), TEXT("assetPath"), TEXT("layers"), nullptr };
		static const TCHAR* const GMifDescNotes_set_material_layers[] = { TEXT("blends"), TEXT("blends are not a separate array here - each layer carries its own `blend`, which is what keeps the two in step; the base layer takes none") ,  TEXT("parameter"), TEXT("that is set_material_parameter - this replaces the LAYER STACK, not a value inside it") ,  TEXT("append"), TEXT("this sets the whole stack; read the current one with list_material_parameters {layers:true} and send it back with your addition"), nullptr };
		static const TCHAR* const GMifDescKeys_list_niagara_user_parameters[] = { TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("nameContains"), nullptr };
		static const TCHAR* const GMifDescNotes_list_niagara_user_parameters[] = { TEXT("component"), TEXT("this reads the ASSET's user parameters. A spawned component's overrides are a different question and are not read here") ,  TEXT("value"), TEXT("this endpoint is read-only - writing Niagara user parameters is deliberately not implemented") ,  TEXT("emitter"), TEXT("emitter-scope parameters are not user parameters; only the User. namespace is exposed by a system"), nullptr };
		static const TCHAR* const GMifDescKeys_set_niagara_user_parameter[] = { TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("name"), TEXT("value"), nullptr };
		static const TCHAR* const GMifDescNotes_set_niagara_user_parameter[] = { TEXT("add"), TEXT("this sets an EXISTING parameter. Adding one is not offered: a user " "parameter no emitter reads is invisible in the editor and does " "nothing, so creating one by typo is worse than being told the " "name is unknown") ,  TEXT("component"), TEXT("this writes the ASSET's default. To override on one placed " "component, use set_niagara_component_parameter") ,  TEXT("type"), TEXT("the type is the one the system already records for that " "parameter - it is not something a caller chooses, and writing a " "mismatched type would terminate the editor"), nullptr };
		static const TCHAR* const GMifDescKeys_layout_material_expressions[] = { TEXT("path"), TEXT("material"), TEXT("materialPath"), nullptr };
		static const TCHAR* const GMifDescKeys_recompile_material[] = { TEXT("path"), TEXT("material"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescKeys_material_statistics[] = { TEXT("path"), TEXT("assetPath"), TEXT("material"), TEXT("compile"), nullptr };
		static const TCHAR* const GMifDescNotes_material_statistics[] = { TEXT("featureLevel"), TEXT("statistics come from GMaxRHIFeatureLevel, the level this " "editor is running - a per-level query is not offered " "because the other levels have no shader map here") ,  TEXT("quality"), TEXT("same - the quality level is the editor's own") ,  TEXT("recompile"), TEXT("the parameter is 'compile', and it WAITS for a compile " "rather than forcing a fresh one - recompile_material is " "the endpoint that rebuilds"), nullptr };
		static const TCHAR* const GMifDescKeys_shader_compile_status[] = { nullptr };
		static const TCHAR* const GMifDescKeys_list_sublevels[] = { TEXT("world"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescKeys_list_data_layers[] = { nullptr };
		static const TCHAR* const GMifDescNotes_list_data_layers[] = { TEXT("world"), TEXT("this always reads the EDITOR world - stop_pie if you want its state to settle") ,  TEXT("level"), TEXT("Data Layers belong to the World Partition map, not to a sublevel - use list_sublevels for those"), nullptr };
		static const TCHAR* const GMifDescKeys_apply_spline_to_landscape[] = { TEXT("landscape"), TEXT("actorPath"), TEXT("splineActor"), TEXT("spline"), TEXT("component"), TEXT("startWidth"), TEXT("endWidth"), TEXT("startSideFalloff"), TEXT("endSideFalloff"), TEXT("startRoll"), TEXT("endRoll"), TEXT("subdivisions"), TEXT("raiseHeights"), TEXT("lowerHeights"), TEXT("paintLayer"), TEXT("editLayer"), nullptr };
		static const TCHAR* const GMifDescNotes_apply_spline_to_landscape[] = { TEXT("width"), TEXT("spell it startWidth and endWidth - a spline can taper") ,  TEXT("falloff"), TEXT("spell it startSideFalloff and endSideFalloff"), nullptr };
		static const TCHAR* const GMifDescKeys_list_partition_actors[] = { TEXT("classFilter"), TEXT("class"), TEXT("nameContains"), TEXT("dataLayer"), TEXT("loadedOnly"), TEXT("limit"), TEXT("bounds"), nullptr };
		static const TCHAR* const GMifDescNotes_list_partition_actors[] = { TEXT("box"), TEXT("spell it bounds - {min:{x,y,z}, max:{x,y,z}}") ,  TEXT("radius"), TEXT("spatial filtering here is a BOX, not a sphere - pass bounds") ,  TEXT("pathPrefix"), TEXT("descriptors are addressed by class/name/data layer, not " "by content path - use classFilter or nameContains"), nullptr };
		static const TCHAR* const GMifDescKeys_load_partition_actors[] = { TEXT("guids"), TEXT("guid"), TEXT("bounds"), TEXT("unpin"), nullptr };
		static const TCHAR* const GMifDescNotes_load_partition_actors[] = { TEXT("actorPath"), TEXT("an unloaded actor has no path yet - that is the point. Pass the guid list_partition_actors reports") ,  TEXT("load"), TEXT("this endpoint loads by default; pass unpin:true to release") ,  TEXT("all"), TEXT("there is no load-everything switch - a partitioned map is partitioned because loading all of it does not fit. Use bounds"), nullptr };
		static const TCHAR* const GMifDescKeys_list_layers[] = { TEXT("includeActors"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_layers[] = { TEXT("withActors"), TEXT("spell it includeActors") ,  TEXT("dataLayers"), TEXT("different system - use list_data_layers for World ") TEXT("Partition Data Layers"), nullptr };
		static const TCHAR* const GMifDescKeys_modify_actor_layers[] = { TEXT("actorPaths"), TEXT("actors"), TEXT("layer"), TEXT("layers"), TEXT("operation"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_modify_actor_layers[] = { TEXT("op"), TEXT("spell it operation") ,  TEXT("actorPath"), TEXT("spell it actorPaths - this endpoint takes an array"), nullptr };
		static const TCHAR* const GMifDescKeys_set_layer_visibility[] = { TEXT("layer"), TEXT("layers"), TEXT("visible"), nullptr };
		static const TCHAR* const GMifDescNotes_set_layer_visibility[] = { TEXT("hidden"), TEXT("spell it visible, inverted - visible:false hides the layer") ,  TEXT("name"), TEXT("spell it layer"), nullptr };
		static const TCHAR* const GMifDescKeys_list_level_sequences[] = { TEXT("filter"), TEXT("search"), TEXT("name"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_level_sequences[] = { TEXT("path"), TEXT("list_level_sequences takes filter, a substring of the object path - describe_level_sequence is the one that takes path") ,  TEXT("class"), TEXT("this endpoint is ULevelSequence-only; find_assets is the one that takes a class"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_level_sequence[] = { TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("sequencePath"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_level_sequence[] = { TEXT("filter"), TEXT("describe_level_sequence takes one path - list_level_sequences is the one that takes filter") ,  TEXT("time"), TEXT("this reports the whole playback range; evaluating a sequence at a time needs a live player, which the bridge does not drive"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_niagara_system[] = { TEXT("path"), TEXT("assetPath"), TEXT("system"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_niagara_system[] = { TEXT("emitter"), TEXT("this describes the whole system; list_niagara_emitters is the one that takes an emitter") ,  TEXT("component"), TEXT("this reads the ASSET; a placed component's overrides are a different question"), nullptr };
		static const TCHAR* const GMifDescKeys_list_niagara_emitters[] = { TEXT("path"), TEXT("assetPath"), TEXT("system"), TEXT("nameContains"), TEXT("includeDisabled"), nullptr };
		static const TCHAR* const GMifDescNotes_list_niagara_emitters[] = { TEXT("index"), TEXT("this lists them all with their index - filter with nameContains, or read the index off the result"), nullptr };
		static const TCHAR* const GMifDescKeys_list_game_feature_plugins[] = { TEXT("nameContains"), TEXT("activeOnly"), nullptr };
		static const TCHAR* const GMifDescNotes_list_game_feature_plugins[] = { TEXT("name"), TEXT("this lists them all - describe_game_feature_plugin is the one that takes a single name") ,  TEXT("path"), TEXT("game feature plugins are addressed by NAME, not asset path") ,  TEXT("activate"), TEXT("this endpoint is read-only; activating a game feature changes what is loaded in the running editor and the bridge does not do that"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_game_feature_plugin[] = { TEXT("name"), TEXT("plugin"), TEXT("pluginName"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_game_feature_plugin[] = { TEXT("nameContains"), TEXT("describe takes one exact name - list_game_feature_plugins is the one that filters") ,  TEXT("url"), TEXT("this takes the plugin NAME; the file-protocol URL is derived for you and returned"), nullptr };
		static const TCHAR* const GMifDescKeys_create_procedural_mesh[] = { TEXT("path"), TEXT("assetPath"), TEXT("shape"), TEXT("dimensionX"), TEXT("dimensionY"), TEXT("dimensionZ"), TEXT("steps"), TEXT("radius"), TEXT("stepsPhi"), TEXT("stepsTheta"), TEXT("height"), TEXT("radialSteps"), TEXT("heightSteps"), TEXT("capped"), TEXT("baseRadius"), TEXT("topRadius"), TEXT("majorRadius"), TEXT("minorRadius"), TEXT("majorSteps"), TEXT("minorSteps"), nullptr };
		static const TCHAR* const GMifDescNotes_create_procedural_mesh[] = { TEXT("class"), TEXT("this always creates a StaticMesh - there is no other class here") ,  TEXT("size"), TEXT("box takes dimensionX/dimensionY/dimensionZ, not a single size"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_dynamic_mesh[] = { TEXT("path"), TEXT("assetPath"), TEXT("lod"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_dynamic_mesh[] = { TEXT("mesh"), TEXT("the parameter is path/assetPath"), nullptr };
		static const TCHAR* const GMifDescKeys_create_mesh_boolean[] = { TEXT("targetPath"), TEXT("path"), TEXT("toolPath"), TEXT("operation"), TEXT("outputPath"), TEXT("toolOffsetX"), TEXT("toolOffsetY"), TEXT("toolOffsetZ"), nullptr };
		static const TCHAR* const GMifDescNotes_create_mesh_boolean[] = { TEXT("newPath"), TEXT("the parameter is outputPath") ,  TEXT("output"), TEXT("the parameter is outputPath"), nullptr };
		static const TCHAR* const GMifDescKeys_create_level_snapshot[] = { TEXT("path"), TEXT("assetPath"), TEXT("name"), TEXT("description"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_level_snapshot[] = { TEXT("path"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescKeys_apply_level_snapshot[] = { TEXT("path"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescKeys_push_livelink_transform[] = { TEXT("subjectName"), TEXT("locationX"), TEXT("locationY"), TEXT("locationZ"), TEXT("rotationPitch"), TEXT("rotationYaw"), TEXT("rotationRoll"), TEXT("scaleX"), TEXT("scaleY"), TEXT("scaleZ"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_livelink_subject[] = { TEXT("subjectName"), nullptr };
		static const TCHAR* const GMifDescKeys_add_game_framework_receiver[] = { TEXT("actorPath"), TEXT("actor"), nullptr };
		static const TCHAR* const GMifDescKeys_add_game_framework_component_request[] = { TEXT("receiverClass"), TEXT("componentClass"), TEXT("requestId"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_game_framework_component_request[] = { TEXT("requestId"), nullptr };
		static const TCHAR* const GMifDescKeys_list_game_framework_component_requests[] = { nullptr };
		static const TCHAR* const GMifDescNotes_list_game_framework_component_requests[] = { TEXT("requestId"), TEXT("not a filter - the list is small by construction and a filter that returned one row would just be remove_game_framework_component_request's error message"), nullptr };
		static const TCHAR* const GMifDescKeys_add_mvvm_viewmodel[] = { TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), TEXT("viewModelClass"), nullptr };
		static const TCHAR* const GMifDescKeys_add_mvvm_binding[] = { TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), TEXT("sourceViewModelName"), TEXT("sourcePropertyName"), TEXT("destinationWidgetName"), TEXT("destinationPropertyName"), TEXT("bindingMode"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_mvvm_view[] = { TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_mvvm_viewmodel[] = { TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), TEXT("viewModelName"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_mvvm_binding[] = { TEXT("widgetBlueprintPath"), TEXT("path"), TEXT("blueprintId"), TEXT("bindingId"), nullptr };
		static const TCHAR* const GMifDescKeys_set_data_layer_visibility[] = { TEXT("name"), TEXT("dataLayer"), TEXT("layer"), TEXT("visible"), nullptr };
		static const TCHAR* const GMifDescNotes_set_data_layer_visibility[] = { TEXT("loaded"), TEXT("that is set_data_layer_loaded_in_editor - an UNLOADED layer is not in memory at all, which is not the same as hidden") ,  TEXT("level"), TEXT("Data Layers belong to the World Partition map, not a sublevel - use the sublevel endpoints for those"), nullptr };
		static const TCHAR* const GMifDescKeys_set_data_layer_loaded_in_editor[] = { TEXT("name"), TEXT("dataLayer"), TEXT("layer"), TEXT("loaded"), TEXT("fromUserChange"), nullptr };
		static const TCHAR* const GMifDescNotes_set_data_layer_loaded_in_editor[] = { TEXT("visible"), TEXT("that is set_data_layer_visibility - loading and visibility are different things"), nullptr };
		static const TCHAR* const GMifDescKeys_create_data_layer[] = { TEXT("name"), TEXT("assetPath"), TEXT("type"), TEXT("dataLayerType"), TEXT("isPrivate"), nullptr };
		static const TCHAR* const GMifDescNotes_create_data_layer[] = { TEXT("visible"), TEXT("a new layer is visible by default; set_data_layer_visibility changes it afterwards") ,  TEXT("parent"), TEXT("nesting is not supported here - create the layer, then use the editor's Data Layers panel to reparent it"), nullptr };
		static const TCHAR* const GMifDescKeys_add_actor_to_data_layer[] = { TEXT("actorPath"), TEXT("actor"), TEXT("name"), TEXT("dataLayer"), TEXT("layer"), nullptr };
		static const TCHAR* const GMifDescNotes_add_actor_to_data_layer[] = { TEXT("actors"), TEXT("one actor per call - there is no plural form, so a partial failure across a list cannot be reported as success") ,  TEXT("visible"), TEXT("membership and visibility are different questions - set_data_layer_visibility is the other one"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_actor_from_data_layer[] = { TEXT("actorPath"), TEXT("actor"), TEXT("name"), TEXT("dataLayer"), TEXT("layer"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_actor_from_data_layer[] = { TEXT("all"), TEXT("there is no remove-from-every-layer form - name the layer, because removing an actor from layers you did not know it was in is not an operation anyone means to perform"), nullptr };
		static const TCHAR* const GMifDescKeys_blueprint_inheritance_tree[] = { TEXT("pathPrefix"), TEXT("prefix"), TEXT("root"), TEXT("maxDepth"), nullptr };
		static const TCHAR* const GMifDescNotes_blueprint_inheritance_tree[] = { TEXT("blueprintId"), TEXT("this reads the WHOLE project's tree from the asset registry; pass root to narrow it") ,  TEXT("class"), TEXT("spell it root - and it accepts a native class name like Actor as well as a blueprint"), nullptr };
		static const TCHAR* const GMifDescKeys_project_dependency_graph[] = { TEXT("pathPrefix"), TEXT("path"), TEXT("maxNodes"), TEXT("includeExternal"), TEXT("mermaid"), nullptr };
		static const TCHAR* const GMifDescNotes_project_dependency_graph[] = { TEXT("depth"), TEXT("this returns the whole dependency set under the prefix in one pass; there is no recursion depth to set") ,  TEXT("limit"), TEXT("maxNodes is the cap here - and it is reported as `truncated` rather than applied silently") ,  TEXT("format"), TEXT("spell it mermaid - this is a boolean add-on, not an output-format switch; nodes/edges are always returned too"), nullptr };
		static const TCHAR* const GMifDescKeys_set_plugin_enabled[] = { TEXT("name"), TEXT("plugin"), TEXT("pluginName"), TEXT("enabled"), TEXT("dryRun"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_set_plugin_enabled[] = { TEXT("path"), TEXT("this takes a plugin NAME like 'Water', not a path - list_game_feature_plugins enumerates them") ,  TEXT("restart"), TEXT("the bridge cannot restart the editor; the response says a restart is required and you must do it") ,  TEXT("load"), TEXT("enabling does not load a plugin into THIS session - nothing can, short of a restart"), nullptr };
		static const TCHAR* const GMifDescKeys_project_asset_distribution[] = { TEXT("pathPrefix"), TEXT("path"), TEXT("topFolders"), TEXT("topClasses"), nullptr };
		static const TCHAR* const GMifDescNotes_project_asset_distribution[] = { TEXT("class"), TEXT("this reports the distribution ACROSS classes - find_assets is the one that filters to a class"), nullptr };
		static const TCHAR* const GMifDescKeys_perf_heavy_actors[] = { TEXT("limit"), TEXT("sortBy"), nullptr };
		static const TCHAR* const GMifDescNotes_perf_heavy_actors[] = { TEXT("fps"), TEXT("this measures STATIC content cost, not frame time - get_perf_stats reports editor timing, and its caveat explains why that is not the game's fps") ,  TEXT("profile"), TEXT("this is a census of the level, not a profiler. Unreal Insights is the profiler; nothing here replaces it"), nullptr };
		static const TCHAR* const GMifDescKeys_trace_start[] = { TEXT("channels"), nullptr };
		static const TCHAR* const GMifDescNotes_trace_start[] = { TEXT("duration"), TEXT("there is no duration - tracing runs until trace_stop, because a fixed window almost never contains the thing you were trying to catch") ,  TEXT("path"), TEXT("the destination is chosen for you under Saved/MifBridge/Traces and returned; a caller-supplied path is a way to write outside the project"), nullptr };
		static const TCHAR* const GMifDescKeys_trace_stop[] = { nullptr };
		static const TCHAR* const GMifDescNotes_trace_stop[] = { TEXT("path"), TEXT("the path is remembered from trace_start and returned here"), nullptr };
		static const TCHAR* const GMifDescKeys_add_sublevel[] = { TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_sublevel[] = { TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("discardUnsaved"), nullptr };
		static const TCHAR* const GMifDescKeys_set_sublevel_visibility[] = { TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("visible"), TEXT("editorVisible"), TEXT("shouldBeLoaded"), TEXT("shouldBeVisible"), TEXT("lightingScenario"), nullptr };
		static const TCHAR* const GMifDescKeys_set_current_sublevel[] = { TEXT("path"), TEXT("packagePath"), TEXT("level"), nullptr };
		static const TCHAR* const GMifDescKeys_set_sublevel_streaming[] = { TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class"), nullptr };
		static const TCHAR* const GMifDescKeys_pie_load_level_instance[] = { TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("location"), TEXT("rotation"), TEXT("visible"), TEXT("netMode"), TEXT("nameOverride"), TEXT("tempPackage"), nullptr };
		static const TCHAR* const GMifDescKeys_pie_unload_level_instance[] = { TEXT("instanceName"), TEXT("name"), TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("objectPath"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescKeys_list_editor_commands[] = { TEXT("context"), TEXT("command"), TEXT("filter"), TEXT("includeUnbound"), TEXT("includeCanExecute"), TEXT("includeConsole"), TEXT("consolePrefix"), TEXT("menu"), TEXT("section"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_editor_commands[] = { TEXT("tabId"), TEXT("tabs are a different registry — use invoke_editor_tab {probe:true}") ,  TEXT("entry"), TEXT("pass menu (and optionally section); every entry in it is listed"), nullptr };
		static const TCHAR* const GMifDescKeys_invoke_editor_command[] = { TEXT("context"), TEXT("command"), TEXT("menu"), TEXT("section"), TEXT("entry"), TEXT("dryRun"), TEXT("confirm"), TEXT("allowKnownModal"), nullptr };
		static const TCHAR* const GMifDescNotes_invoke_editor_command[] = { TEXT("commandList"), TEXT("not a parameter — the list is found automatically (cache), or via menu/section/entry") ,  TEXT("key"), TEXT("sending a keystroke is send_editor_key, not this endpoint"), nullptr };
		static const TCHAR* const GMifDescKeys_invoke_editor_tab[] = { TEXT("tabId"), TEXT("tab"), TEXT("manager"), TEXT("majorTab"), TEXT("asset"), TEXT("probe"), TEXT("probeIds"), TEXT("includeKnownIds"), TEXT("asInactive"), nullptr };
		static const TCHAR* const GMifDescNotes_invoke_editor_tab[] = { TEXT("command"), TEXT("invoking a bound command is invoke_editor_command") ,  TEXT("close"), TEXT("closing a tab is not implemented — SDockTab::RequestCloseTab can run a third-party OnCanCloseTab that shows a dialog"), nullptr };
		static const TCHAR* const GMifDescKeys_send_editor_key[] = { TEXT("key"), TEXT("confirm"), TEXT("dryRun"), TEXT("modifiers"), TEXT("userIndex"), TEXT("isRepeat"), TEXT("characterCode"), TEXT("keyCode"), TEXT("sendKeyUp"), nullptr };
		static const TCHAR* const GMifDescNotes_send_editor_key[] = { TEXT("text"), TEXT("typing a string is not implemented — ProcessKeyCharEvent per character goes into whatever currently has focus, which is unbounded; see the Batch O notes in docs/audit/06_IMPLEMENTED.md") ,  TEXT("ctrl"), TEXT("modifiers go in the modifiers object: modifiers:{ctrl:true}"), nullptr };
		static const TCHAR* const GMifDescKeys_open_asset_editor[] = { TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_open_asset_editor[] = { TEXT("blueprintId"), TEXT("spell it path") ,  TEXT("asset"), TEXT("spell it path") ,  TEXT("focus"), TEXT("there is no focus - OpenEditorForAsset already brings the editor forward; alreadyOpen in the response says whether it was open before this call"), nullptr };
		static const TCHAR* const GMifDescKeys_import_texture[] = { TEXT("destPath"), TEXT("path"), TEXT("assetPath"), TEXT("sourcePath"), TEXT("file"), TEXT("filename"), TEXT("base64"), TEXT("data"), TEXT("bytes"), TEXT("format"), TEXT("overwrite"), TEXT("replaceExisting"), TEXT("save"), TEXT("compressionSettings"), TEXT("compression"), TEXT("srgb"), TEXT("sRGB"), TEXT("lodGroup"), TEXT("textureGroup"), TEXT("neverStream"), TEXT("mipGenSettings"), TEXT("mipGen"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_import_texture[] = { TEXT("width"), TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales") ,  TEXT("height"), TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales") ,  TEXT("textureClass"), TEXT("not implemented — import_texture creates UTexture2D only (cubemaps/volumes/render targets are not source-media imports)"), nullptr };
		static const TCHAR* const GMifDescKeys_import_asset[] = { TEXT("file"), TEXT("filename"), TEXT("sourcePath"), TEXT("destination"), TEXT("destinationPath"), TEXT("path"), TEXT("name"), TEXT("destinationName"), TEXT("factory"), TEXT("replaceExisting"), TEXT("overwrite"), TEXT("replaceExistingSettings"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_import_asset[] = { TEXT("async"), TEXT("not implemented and deliberately so — this server runs handlers synchronously inside the HTTP ticker, and UAssetImportTask::GetObjects() BLOCKS on an async import (AssetImportTask.h:78). Imports here always run bAsync:false, one long frame.") ,  TEXT("skeletal"), TEXT("not implemented — forcing static-vs-skeletal FBX needs a UFbxImportUI options object wired into the task; today the FBX factory's own detection decides. Import, then adjust, or pass an explicit factory.") ,  TEXT("options"), TEXT("not implemented — per-factory option objects (UFbxImportUI etc.) are not exposed yet") ,  TEXT("base64"), TEXT("not supported here — import_asset imports a FILE through a UFactory. For inline image bytes use import_texture {base64, destPath}."), nullptr };
		static const TCHAR* const GMifDescKeys_reimport_asset[] = { TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("sourceFile"), TEXT("file"), TEXT("newFile"), TEXT("sourceFileIndex"), TEXT("forceNewFile"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_reimport_asset[] = { TEXT("askForNewFileIfMissing"), TEXT("not settable — it would open a file-picker MODAL, which freezes the editor and this bridge with it. Pass sourceFile instead.") ,  TEXT("showNotification"), TEXT("not settable — always false; the response IS the notification"), nullptr };
		static const TCHAR* const GMifDescKeys_set_texture_settings[] = { TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("texturePath"), TEXT("compressionSettings"), TEXT("compression"), TEXT("srgb"), TEXT("sRGB"), TEXT("lodGroup"), TEXT("textureGroup"), TEXT("neverStream"), TEXT("mipGenSettings"), TEXT("mipGen"), TEXT("filter"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_set_texture_settings[] = { TEXT("addressX"), TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set") ,  TEXT("addressY"), TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set") ,  TEXT("maxTextureSize"), TEXT("not implemented — use set_property on MaxTextureSize") ,  TEXT("lodBias"), TEXT("not implemented — use set_property on LODBias"), nullptr };
		static const TCHAR* const GMifDescKeys_export_asset[] = { TEXT("asset"), TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("file"), TEXT("filename"), TEXT("outPath"), TEXT("format"), TEXT("type"), TEXT("extension"), TEXT("overwrite"), TEXT("replaceExisting"), TEXT("fbxCompatibility"), TEXT("ascii"), TEXT("vertexColor"), TEXT("levelOfDetail"), TEXT("lod"), TEXT("collision"), TEXT("exportSourceMesh"), TEXT("forceFrontXAxis"), nullptr };
		static const TCHAR* const GMifDescNotes_export_asset[] = { TEXT("destination"), TEXT("export_asset writes to a DISK path, not a /Game folder — spell it file. (destination means a /Game/... content folder in import_asset, and honouring it here would silently write a .fbx into a path that reads like a package.) Omit it entirely to get <ProjectSaved>/MifBridge/Export/<AssetName>.<ext>.") ,  TEXT("async"), TEXT("not implemented and deliberately so — this server runs handlers synchronously inside the HTTP ticker. UExporter has no async export; a large mesh makes one long frame, which is legal, and work that SPANS frames is not.") ,  TEXT("selected"), TEXT("not implemented — UAssetExportTask::bSelected filters an ACTOR SELECTION for level/object exports; this endpoint exports one named asset and always sends false.") ,  TEXT("options"), TEXT("not implemented as a free-form object — the FBX option fields are exposed individually (fbxCompatibility, ascii, vertexColor, levelOfDetail, collision, exportSourceMesh, forceFrontXAxis). No other exporter's option object is wired, and passing a raw object would defeat the type check that keeps the FBX options MODAL shut (EditorExporters.cpp:2129).") ,  TEXT("base64"), TEXT("not supported — export_asset writes a FILE and reports its path and byte size. Read the bytes off disk at the returned `file`.") ,  TEXT("batch"), TEXT("not implemented — call once per asset. The FBX SDK instance is created and destroyed per export (EditorExporters.cpp:96-111), so batching inside one call would save nothing. export_asset IS read-only, so the `batch` ENDPOINT can drive several of these in one request.") ,  TEXT("save"), TEXT("not a parameter — export_asset writes a disk file and never touches the asset or its package, so there is nothing to save. (It is read-only for exactly that reason.)") ,  TEXT("lodIndex"), TEXT("not implemented — the FBX exporter takes a bool (levelOfDetail: all LODs, or LOD0 only), not an index. Export with levelOfDetail:false for LOD0."), nullptr };
		static const TCHAR* const GMifDescKeys_render_thumbnail[] = { TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"), TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"), TEXT("flushTextures"), TEXT("alpha"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescKeys_write_thumbnail_texture[] = { TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("texturePath"), TEXT("outputPath"), TEXT("width"), TEXT("height"), TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"), TEXT("flushTextures"), TEXT("alpha"), TEXT("srgb"), TEXT("compression"), TEXT("lodGroup"), TEXT("generateMips"), TEXT("overwrite"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_write_thumbnail_texture[] = { TEXT("name"), TEXT("render_thumbnail names a PNG file; this endpoint names an ASSET — use texturePath"), nullptr };
		static const TCHAR* const GMifDescKeys_set_asset_thumbnail[] = { TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"), TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"), TEXT("flushTextures"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_set_asset_thumbnail[] = { TEXT("texturePath"), TEXT("this endpoint sets the asset's own Content Browser icon and writes no texture asset — use write_thumbnail_texture for that"), nullptr };
		static const TCHAR* const GMifDescKeys_thumbnail_capabilities[] = { TEXT("asset"), TEXT("assetPath"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_exec_console[] = { TEXT("command"), nullptr };
		static const TCHAR* const GMifDescNotes_exec_console[] = { TEXT("cmd"), TEXT("spell it command") ,  TEXT("cvar"), TEXT("to READ a cvar use get_cvar {name}; to SET one use set_cvar {name, value}") ,  TEXT("console"), TEXT("spell it command"), nullptr };
		static const TCHAR* const GMifDescKeys_get_cvar[] = { TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_get_cvar[] = { TEXT("cvar"), TEXT("spell it name") ,  TEXT("var"), TEXT("spell it name") ,  TEXT("value"), TEXT("get_cvar only reads; use set_cvar {name, value} to write"), nullptr };
		static const TCHAR* const GMifDescKeys_set_cvar[] = { TEXT("name"), TEXT("value"), nullptr };
		static const TCHAR* const GMifDescNotes_set_cvar[] = { TEXT("cvar"), TEXT("spell it name") ,  TEXT("var"), TEXT("spell it name"), nullptr };
		static const TCHAR* const GMifDescKeys_add_node_pin[] = { TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("count"), nullptr };
		static const TCHAR* const GMifDescNotes_add_node_pin[] = { TEXT("pin"), TEXT("add_node_pin adds the NEXT pin in the node's own sequence - you cannot name it. To set a value on the new pin use set_pin_default") ,  TEXT("pinName"), TEXT("the new pin's name is chosen by the node (then_N, [N], Case_N); read it back from the returned pins[]") ,  TEXT("value"), TEXT("add the pin first, then set_pin_default on the name returned in addedPins[]") ,  TEXT("index"), TEXT("pins are appended in order; there is no insert-at-index"), nullptr };
		static const TCHAR* const GMifDescKeys_create_metahuman_character[] = { TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_create_metahuman_character[] = { TEXT("name"), TEXT("the asset name comes from the last segment of path"), nullptr };
		static const TCHAR* const GMifDescKeys_spawn_metahuman_actor[] = { TEXT("characterPath"), TEXT("path"), TEXT("character"), nullptr };
		static const TCHAR* const GMifDescKeys_add_gameplay_effect_modifier[] = { TEXT("objectPath"), TEXT("attributeSetClass"), TEXT("attributeName"), TEXT("operation"), TEXT("magnitude"), nullptr };
		static const TCHAR* const GMifDescNotes_add_gameplay_effect_modifier[] = { TEXT("attribute"), TEXT("split into attributeSetClass + attributeName - a " "FGameplayAttribute is resolved from a real class property, not a bare string") ,  TEXT("value"), TEXT("this endpoint's numeric key is magnitude, to match GAS's own " "terminology - set_property's generic 'value' key does not apply here"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_ability_system[] = { TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("objectPath"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_ability_system[] = { TEXT("blueprintId"), TEXT("this reads a LIVE component's runtime state; a Blueprint asset has none. Spawn the actor first") ,  TEXT("ability"), TEXT("this reports every granted ability - there is nothing to filter by yet"), nullptr };
		static const TCHAR* const GMifDescKeys_list_live_widgets[] = { TEXT("netMode"), TEXT("topLevelOnly"), TEXT("classFilter"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_live_widget[] = { TEXT("path"), TEXT("maxDepth"), nullptr };
		static const TCHAR* const GMifDescKeys_preview_widget[] = { TEXT("widgetClass"), TEXT("width"), TEXT("height"), TEXT("dpiScale"), TEXT("background"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_preview_widget[] = { TEXT("dpiMode"), TEXT("not implemented - pass dpiScale explicitly; the response's dpiScaleAtThisSize reports what dpiMode:project would have used"), nullptr };
		static const TCHAR* const GMifDescKeys_preview_composite_widget[] = { TEXT("rootClass"), TEXT("children"), TEXT("width"), TEXT("height"), TEXT("dpiScale"), TEXT("background"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_preview_composite_widget[] = { TEXT("recipe"), TEXT("the field is called children[], not recipe"), nullptr };
		static const TCHAR* const GMifDescKeys_ui_scenario_start[] = { TEXT("targetActorPath"), TEXT("netMode"), TEXT("playerLocation"), TEXT("playerRotation"), TEXT("playerIndex"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_ui_scenario_start[] = { TEXT("activationKey"), TEXT("belongs to ui_scenario_activate, not start") ,  TEXT("expectedWidgetClasses"), TEXT("belongs to ui_scenario_activate"), nullptr };
		static const TCHAR* const GMifDescKeys_ui_scenario_activate[] = { TEXT("activationKey"), TEXT("expectedWidgetClasses"), TEXT("timeoutSeconds"), TEXT("stableFrames"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_ui_scenario_status[] = { nullptr };
		static const TCHAR* const GMifDescKeys_ui_scenario_capture[] = { TEXT("name"), nullptr };
		static const TCHAR* const GMifDescKeys_ui_scenario_stop[] = { nullptr };

		static const FMifDescribeRow GMifDescribeRows[] = {
			{ TEXT("describe_endpoint"), GMifDescKeys_describe_endpoint, GMifDescNotes_describe_endpoint,
			  MIF_DESCRIBE_OWN_SUMMARY,
			  TEXT("MifBridgeDescribe.cpp"), 0, nullptr },
			{ TEXT("open_blueprint"), GMifDescKeys_open_blueprint, GMifDescNotes_open_blueprint,
			  TEXT("blueprintId (alias: path) - the blueprint asset to open; returns blueprintId, name, class, parentClass and graphs"),
			  TEXT("MifBridgeIntrospect.cpp"), 61, nullptr },
			{ TEXT("list_blueprints"), GMifDescKeys_list_blueprints, GMifDescNotes_list_blueprints,
			  TEXT("filter (optional; substring matched against each blueprint's full object path - omit to list every blueprint, capped at 5000)"),
			  TEXT("MifBridgeIntrospect.cpp"), 103, nullptr },
			{ TEXT("save_blueprint"), GMifDescKeys_save_blueprint, GMifDescNotes_save_blueprint,
			  TEXT("blueprintId (alias: path) - writes the package that owns this blueprint back to disk, in place"),
			  TEXT("MifBridgeIntrospect.cpp"), 210, nullptr },
			{ TEXT("save_package"), GMifDescKeys_save_package, GMifDescNotes_save_package,
			  TEXT("path - the /Game/ object path of ANY asset; the package that owns it is marked dirty and written to disk"),
			  TEXT("MifBridgeIntrospect.cpp"), 378, nullptr },
			{ TEXT("list_automation_tests"), GMifDescKeys_list_automation_tests, GMifDescNotes_list_automation_tests,
			  TEXT("filter? (case-insensitive substring of the full test path), limit? (default 200), ") TEXT("offset?"),
			  TEXT("MifBridgeIntrospect.cpp"), 267, nullptr },
			{ TEXT("backup_blueprint"), GMifDescKeys_backup_blueprint, GMifDescNotes_backup_blueprint,
			  TEXT("blueprintId (alias: path) - copies the blueprint's package file on disk to a backup, returned as 'backup'"),
			  TEXT("MifBridgeIntrospect.cpp"), 471, nullptr },
			{ TEXT("list_graphs"), GMifDescKeys_list_graphs, GMifDescNotes_list_graphs,
			  TEXT("blueprintId (alias: path) - lists every graph in the blueprint, nested ones included, each with its graphId and kind (ubergraph | function | macro | delegateSignature | interface | nested)"),
			  TEXT("MifBridgeIntrospect.cpp"), 502, nullptr },
			{ TEXT("list_nodes"), GMifDescKeys_list_nodes, GMifDescNotes_list_nodes,
			  TEXT("graphId ('<blueprintPath>::<graphName>', exactly as open_blueprint/list_graphs return it), hideKnots (default false; true skips reroute nodes)"),
			  TEXT("MifBridgeIntrospect.cpp"), 559, nullptr },
			{ TEXT("get_node"), GMifDescKeys_get_node, GMifDescNotes_get_node,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional - scopes the guid lookup to that one graph, the only way to disambiguate two loaded copies of a blueprint sharing NodeGuids)"),
			  TEXT("MifBridgeIntrospect.cpp"), 622, nullptr },
			{ TEXT("list_variables"), GMifDescKeys_list_variables, GMifDescNotes_list_variables,
			  TEXT("blueprintId (alias: path) - lists the blueprint's MEMBER variables with name, type, default, flags and a suspiciousName marker"),
			  TEXT("MifBridgeIntrospect.cpp"), 640, nullptr },
			{ TEXT("list_functions"), GMifDescKeys_list_functions, GMifDescNotes_list_functions,
			  TEXT("blueprintId (alias: path) - lists the blueprint's own function graphs with name and graphId"),
			  TEXT("MifBridgeIntrospect.cpp"), 685, nullptr },
			{ TEXT("find_nodes"), GMifDescKeys_find_nodes, GMifDescNotes_find_nodes,
			  TEXT("graphId, byClass (substring of the node's C++ class name), byTitle (substring of the node title), byFunction (substring of the called function name) - every filter is optional and they are ANDed"),
			  TEXT("MifBridgeIntrospect.cpp"), 838, nullptr },
			{ TEXT("add_variable"), GMifDescKeys_add_variable, GMifDescNotes_add_variable,
			  TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), ") TEXT("function? (required when scope=local), default?, and optionally any set_variable_flags ") TEXT("flag (replicated, repNotify, repNotifyFunction, replicationCondition, saveGame, transient, ") TEXT("config, instanceEditable, blueprintReadOnly, exposeOnSpawn, advancedDisplay, interp, ") TEXT("deprecated, category, tooltip, fieldNotify) to set at creation time - member scope only"),
			  TEXT("MifBridgeIntrospect.cpp"), 1320, nullptr },
			{ TEXT("rename_variable"), GMifDescKeys_rename_variable, GMifDescNotes_rename_variable,
			  TEXT("blueprintId (alias: path), oldName, newName, confirm=true"),
			  TEXT("MifBridgeIntrospect.cpp"), 1479, nullptr },
			{ TEXT("remove_variable"), GMifDescKeys_remove_variable, nullptr,
			  TEXT("blueprintId (alias: path), name, confirm=true"),
			  TEXT("MifBridgeIntrospect.cpp"), 1613, nullptr },
			{ TEXT("set_variable_type"), GMifDescKeys_set_variable_type, GMifDescNotes_set_variable_type,
			  TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), ") TEXT("function? (required when scope=local)"),
			  TEXT("MifBridgeIntrospect.cpp"), 1716, nullptr },
			{ TEXT("retarget_variable_node"), GMifDescKeys_retarget_variable_node, GMifDescNotes_retarget_variable_node,
			  TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), targetClass (alias: class) OR self:true"),
			  TEXT("MifBridgeIntrospect.cpp"), 2003, nullptr },
			{ TEXT("set_variable_default"), GMifDescKeys_set_variable_default, nullptr,
			  TEXT("blueprintId (alias: path), name, value (aliases: default, defaultValue)"),
			  TEXT("MifBridgeIntrospect.cpp"), 2114, nullptr },
			{ TEXT("set_variable_flags"), GMifDescKeys_set_variable_flags, GMifDescNotes_set_variable_flags,
			  TEXT("blueprintId (alias: path), name (aliases: var, variable), then any of replicated, repNotify, ") TEXT("repNotifyFunction, replicationCondition, saveGame, transient, config, instanceEditable, ") TEXT("blueprintReadOnly, exposeOnSpawn, advancedDisplay, interp, deprecated, category, tooltip, ") TEXT("fieldNotify (the MVVM \"broadcasts on change\" flag, meaningful only on a class ") TEXT("implementing INotifyFieldValueChanged such as an MVVM ViewModel Blueprint) ") TEXT("- PARTIAL UPDATE: only the keys actually present are applied, the rest are left alone"),
			  TEXT("MifBridgeIntrospect.cpp"), 1228, nullptr },
			{ TEXT("add_function_call"), GMifDescKeys_add_function_call, GMifDescNotes_add_function_call,
			  TEXT("graphId, class (aliases: cls, className, targetClass, ownerClass; default \"self\"), ") TEXT("function (aliases: functionName, func, method), asMessage (alias: message), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 716, nullptr },
			{ TEXT("add_variable_get"), GMifDescKeys_add_variable_get, GMifDescNotes_add_variable_get,
			  TEXT("graphId, var (aliases: name, variable, varName, property, propertyName, member), ") TEXT("targetClass (aliases: class, cls, className, ownerClass, objectClass), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 381, TEXT("DoAddVariableNode") },
			{ TEXT("add_variable_set"), GMifDescKeys_add_variable_set, GMifDescNotes_add_variable_set,
			  TEXT("graphId, var (aliases: name, variable, varName, property, propertyName, member), ") TEXT("targetClass (aliases: class, cls, className, ownerClass, objectClass), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 381, TEXT("DoAddVariableNode") },
			{ TEXT("add_branch"), GMifDescKeys_add_branch, GMifDescNotes_add_branch,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes.cpp"), 870, nullptr },
			{ TEXT("add_macro_instance"), GMifDescKeys_add_macro_instance, GMifDescNotes_add_macro_instance,
			  TEXT("graphId, macroGraph (aliases: macro, macroName, name), ") TEXT("macroPath (aliases: macroLibrary, library, path), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 895, nullptr },
			{ TEXT("add_get_array_item"), GMifDescKeys_add_get_array_item, GMifDescNotes_add_get_array_item,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1155, nullptr },
			{ TEXT("add_override_event"), GMifDescKeys_add_override_event, GMifDescNotes_add_override_event,
			  TEXT("blueprintId (alias: path), event (aliases: eventName, name, function, functionName), ") TEXT("interfaceOrParent (aliases: class, cls, className, parentClass, interface, ownerClass, targetClass), ") TEXT("callParent (aliases: addParentCall, withParentCall), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1201, nullptr },
			{ TEXT("add_component_bound_event"), GMifDescKeys_add_component_bound_event, GMifDescNotes_add_component_bound_event,
			  TEXT("blueprintId (alias: path), component (the SCS/native component variable name), ") TEXT("dispatcher (aliases: delegate, event), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1320, nullptr },
			{ TEXT("add_parent_call"), GMifDescKeys_add_parent_call, GMifDescNotes_add_parent_call,
			  TEXT("graphId, parentClass (aliases: class, cls, className, parent, ownerClass, targetClass; ") TEXT("default = this blueprint's parent), function (aliases: functionName, func, method, name), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1423, nullptr },
			{ TEXT("add_cast"), GMifDescKeys_add_cast, GMifDescNotes_add_cast,
			  TEXT("graphId, targetClass (aliases: class, cls, className, castTo, to, targetType), pure? (default false), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1484, nullptr },
			{ TEXT("set_cast_purity"), GMifDescKeys_set_cast_purity, GMifDescNotes_set_cast_purity,
			  TEXT("graphId?, node (aliases: nodeGuid, guid, nodeId), pure"),
			  TEXT("MifBridgeNodes.cpp"), 1546, nullptr },
			{ TEXT("move_node"), GMifDescKeys_move_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1622, nullptr },
			{ TEXT("remove_node"), GMifDescKeys_remove_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes.cpp"), 1646, nullptr },
			{ TEXT("refresh_node"), GMifDescKeys_refresh_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid)"),
			  TEXT("MifBridgeNodes.cpp"), 2304, nullptr },
			{ TEXT("blueprint_breakpoint"), GMifDescKeys_blueprint_breakpoint, GMifDescNotes_blueprint_breakpoint,
			  TEXT("op: add | remove | enable | disable | list | clear. add/remove/enable/disable need " "nodeGuid (alias nodeId) and its graphId; list and clear take the blueprint " "(blueprintId, alias path) or any graphId in it"),
			  TEXT("MifBridgeNodes.cpp"), 2823, nullptr },
			{ TEXT("blueprint_watch"), GMifDescKeys_blueprint_watch, GMifDescNotes_blueprint_watch,
			  TEXT("op: add | remove | list | clear | read. add/remove/read need nodeGuid (alias " "nodeId), pin (the pin NAME) and graphId; list and clear take the blueprint " "(blueprintId, alias path) or any graphId in it"),
			  TEXT("MifBridgeNodes.cpp"), 3026, nullptr },
			{ TEXT("connect_pins"), GMifDescKeys_connect_pins, GMifDescNotes_connect_pins,
			  TEXT("srcNode, srcPin (aliases: sourcePin, fromPin), dstNode, dstPin (aliases: destPin, toPin), ") TEXT("graphId, path (back-compat only — accepted and ignored; graphId already names the blueprint)"),
			  TEXT("MifBridgeNodes.cpp"), 566, TEXT("DoConnect") },
			{ TEXT("disconnect_pin"), GMifDescKeys_disconnect_pin, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), ") TEXT("path (back-compat only — accepted and ignored; graphId already names the blueprint)"),
			  TEXT("MifBridgeNodes.cpp"), 2337, nullptr },
			{ TEXT("reconnect_pin"), GMifDescKeys_reconnect_pin, GMifDescNotes_reconnect_pin,
			  TEXT("srcNode, srcPin (aliases: sourcePin, fromPin), dstNode, dstPin (aliases: destPin, toPin), ") TEXT("graphId, path (back-compat only — accepted and ignored; graphId already names the blueprint)"),
			  TEXT("MifBridgeNodes.cpp"), 566, TEXT("DoConnect") },
			{ TEXT("set_pin_default"), GMifDescKeys_set_pin_default, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), ") TEXT("value (aliases: default, defaultValue)"),
			  TEXT("MifBridgeNodes.cpp"), 2367, nullptr },
			{ TEXT("splice_into_exec"), GMifDescKeys_splice_into_exec, GMifDescNotes_splice_into_exec,
			  TEXT("afterNode, insertNode, graphId (optional), afterPin (alias: afterExecOut; default \"then\"), ") TEXT("insertExecIn (aliases: insertIn, execIn; default \"execute\"), ") TEXT("insertExecOut (aliases: insertOut, execOut; default \"then\")"),
			  TEXT("MifBridgeNodes.cpp"), 2412, nullptr },
			{ TEXT("apply_graph_patch"), GMifDescKeys_apply_graph_patch, GMifDescNotes_apply_graph_patch,
			  TEXT("graphId, operations (alias: ops) - array of {op:connect_pins|disconnect_pin|set_pin_default, ...}, " "dryRun (default false; resolve and validate everything, mutate nothing), " "stopOnFirstError (default true), allowPartial (default false; when false a failure rolls back everything already applied)"),
			  TEXT("MifBridgeGraphPatch.cpp"), 353, nullptr },
			{ TEXT("add_pin"), GMifDescKeys_add_pin, GMifDescNotes_add_pin,
			  TEXT("name (aliases: pin, pinName), type (alias: pinType), container, valueType, ") TEXT("direction (alias: dir; input|output), default (aliases: defaultValue, value), ") TEXT("and ONE target: nodeGuid (aliases: node, guid, nodeId) | graphId | blueprintId + function"),
			  TEXT("MifBridgeNodes.cpp"), 1730, nullptr },
			{ TEXT("remove_pin"), GMifDescKeys_remove_pin, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), ") TEXT("direction (alias: dir; input|output), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes.cpp"), 2089, nullptr },
			{ TEXT("add_custom_event"), GMifDescKeys_add_custom_event, GMifDescNotes_add_custom_event,
			  TEXT("graphId, name, inputs? ([{name, type, container?, valueType?}] - the event's parameters), x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 151, nullptr },
			{ TEXT("add_enhanced_input_action"), GMifDescKeys_add_enhanced_input_action, GMifDescNotes_add_enhanced_input_action,
			  TEXT("graphId, inputAction (aliases: action, actionPath) - the UInputAction asset path, x, y"),
			  TEXT("MifBridgeNodes7.cpp"), 33, nullptr },
			{ TEXT("list_input_mappings"), GMifDescKeys_list_input_mappings, GMifDescNotes_list_input_mappings,
			  TEXT("path (aliases: context, assetPath) - the /Game/... path of an InputMappingContext"),
			  TEXT("MifBridgeNodes7.cpp"), 108, nullptr },
			{ TEXT("map_input_key"), GMifDescKeys_map_input_key, GMifDescNotes_map_input_key,
			  TEXT("context (aliases: path, assetPath) - an InputMappingContext; action - an ") TEXT("InputAction asset path; key - an FKey NAME such as SpaceBar, LeftMouseButton, ") TEXT("Gamepad_FaceButton_Bottom"),
			  TEXT("MifBridgeNodes7.cpp"), 256, nullptr },
			{ TEXT("unmap_input_key"), GMifDescKeys_unmap_input_key, GMifDescNotes_unmap_input_key,
			  TEXT("context (aliases: path, assetPath); action - unbind this InputAction; key - ") TEXT("optional, unbinds only that one key (omit to unbind EVERY key from the action); ") TEXT("all:true with confirm:true clears the ENTIRE context"),
			  TEXT("MifBridgeNodes7.cpp"), 394, nullptr },
			{ TEXT("list_legacy_input_mappings"), GMifDescKeys_list_legacy_input_mappings, GMifDescNotes_list_legacy_input_mappings,
			  TEXT("name - optional, report only mappings with this action or axis name"),
			  TEXT("MifBridgeNodes7.cpp"), 626, nullptr },
			{ TEXT("map_legacy_input"), GMifDescKeys_map_legacy_input, GMifDescNotes_map_legacy_input,
			  TEXT("name - the action or axis name; key - an FKey name; axis:true for an axis mapping ") TEXT("(then scale, default 1.0); shift/ctrl/alt/cmd for an action mapping's modifiers"),
			  TEXT("MifBridgeNodes7.cpp"), 683, nullptr },
			{ TEXT("unmap_legacy_input"), GMifDescKeys_unmap_legacy_input, GMifDescNotes_unmap_legacy_input,
			  TEXT("name - the action or axis name; key - the FKey name to unbind; axis:true for an ") TEXT("axis mapping. Modifiers must match the mapping being removed."),
			  TEXT("MifBridgeNodes7.cpp"), 812, nullptr },
			{ TEXT("save_input_settings"), GMifDescKeys_save_input_settings, GMifDescNotes_save_input_settings,
			  TEXT("confirm:true - this WRITES Config/DefaultInput.ini in the project"),
			  TEXT("MifBridgeNodes7.cpp"), 887, nullptr },
			{ TEXT("list_settings"), GMifDescKeys_list_settings, GMifDescNotes_list_settings,
			  TEXT("container (Project|Editor, default both), category, nameContains, limit"),
			  TEXT("MifBridgeNodes5.cpp"), 999, nullptr },
			{ TEXT("add_pcg_node"), GMifDescKeys_add_pcg_node, GMifDescNotes_add_pcg_node,
			  TEXT("graph (aliases: path, assetPath); settingsClass (alias: class) - a UPCGSettings ") TEXT("subclass such as PCGSurfaceSamplerSettings; x, y - optional editor position"),
			  TEXT("MifBridgePCG.cpp"), 537, nullptr },
			{ TEXT("remove_pcg_node"), GMifDescKeys_remove_pcg_node, GMifDescNotes_remove_pcg_node,
			  TEXT("graph (aliases: path, assetPath); node - the node NAME from describe_pcg_graph; ") TEXT("confirm:true - removing a node also destroys every edge attached to it"),
			  TEXT("MifBridgePCG.cpp"), 624, nullptr },
			{ TEXT("connect_pcg_nodes"), GMifDescKeys_connect_pcg_nodes, GMifDescNotes_connect_pcg_nodes,
			  TEXT("graph (aliases: path, assetPath); fromNode + fromPin (an OUTPUT pin label); ") TEXT("toNode + toPin (an INPUT pin label). describe_pcg_graph reports every node's ") TEXT("inputPinNames and outputPinNames."),
			  TEXT("MifBridgePCG.cpp"), 703, nullptr },
			{ TEXT("disconnect_pcg_nodes"), GMifDescKeys_disconnect_pcg_nodes, GMifDescNotes_disconnect_pcg_nodes,
			  TEXT("graph (aliases: path, assetPath); fromNode + fromPin, toNode + toPin - the same ") TEXT("four that named the edge when it was created"),
			  TEXT("MifBridgePCG.cpp"), 821, nullptr },
			{ TEXT("describe_physics_asset"), GMifDescKeys_describe_physics_asset, GMifDescNotes_describe_physics_asset,
			  TEXT("assetPath (aliases: path, asset) - a PhysicsAsset"),
			  TEXT("MifBridgePhysicsAsset.cpp"), 288, nullptr },
			{ TEXT("add_physics_body"), GMifDescKeys_add_physics_body, GMifDescNotes_add_physics_body,
			  TEXT("assetPath (aliases: path, asset); boneName - the bone to create a body for; ") TEXT("geomType (sphyl|sphere|box|taperedCapsule, default sphyl); minBoneSize"),
			  TEXT("MifBridgePhysicsAsset.cpp"), 374, nullptr },
			{ TEXT("remove_physics_body"), GMifDescKeys_remove_physics_body, nullptr,
			  TEXT("assetPath (aliases: path, asset); boneName OR index; confirm:true - removing a ") TEXT("body also renumbers every body after it and drops its collision-disable pairs"),
			  TEXT("MifBridgePhysicsAsset.cpp"), 468, nullptr },
			{ TEXT("add_physics_constraint"), GMifDescKeys_add_physics_constraint, GMifDescNotes_add_physics_constraint,
			  TEXT("assetPath (aliases: path, asset); bone1 and bone2 - the two bones to constrain; ") TEXT("name - optional joint name, defaults to bone1"),
			  TEXT("MifBridgePhysicsAsset.cpp"), 524, nullptr },
			{ TEXT("remove_physics_constraint"), GMifDescKeys_remove_physics_constraint, nullptr,
			  TEXT("assetPath (aliases: path, asset); index OR jointName; confirm:true"),
			  TEXT("MifBridgePhysicsAsset.cpp"), 623, nullptr },
			{ TEXT("set_physics_body_collision"), GMifDescKeys_set_physics_body_collision, GMifDescNotes_set_physics_body_collision,
			  TEXT("assetPath (aliases: path, asset); boneA + boneB (or indexA + indexB); ") TEXT("enabled:true|false - whether the two bodies collide with each other"),
			  TEXT("MifBridgePhysicsAsset.cpp"), 714, nullptr },
			{ TEXT("set_physics_primitive_collision"), GMifDescKeys_set_physics_primitive_collision, GMifDescNotes_set_physics_primitive_collision,
			  TEXT("assetPath (aliases: path, asset); boneName or index - which body; primitiveType ") TEXT("(sphere|box|capsule|convex); primitiveIndex - the index WITHIN that type's array; ") TEXT("collisionEnabled (NoCollision|QueryOnly|PhysicsOnly|QueryAndPhysics)"),
			  TEXT("MifBridgePhysicsAsset.cpp"), 816, nullptr },
			{ TEXT("add_socket"), GMifDescKeys_add_socket, GMifDescNotes_add_socket,
			  TEXT("path (aliases: assetPath, mesh) - a SkeletalMesh or Skeleton; name; bone (alias ") TEXT("boneName); location/rotation/scale {x,y,z}; target (mesh|skeleton|both)"),
			  TEXT("MifBridgeAnimation.cpp"), 2172, nullptr },
			{ TEXT("run_retarget"), GMifDescKeys_run_retarget, GMifDescNotes_run_retarget,
			  TEXT("retargeter (aliases: path, assetPath); animations[] - AnimSequence/Montage paths; ") TEXT("sourceMesh/targetMesh override the rigs' preview meshes; prefix, suffix, search, ") TEXT("replace name the outputs; remapReferencedAssets (default FALSE); confirm:true"),
			  TEXT("MifBridgeIKRig.cpp"), 2424, nullptr },
			{ TEXT("add_virtual_bone"), GMifDescKeys_add_virtual_bone, GMifDescNotes_add_virtual_bone,
			  TEXT("skeleton (aliases: path, assetPath); source (alias sourceBone); target (alias ") TEXT("targetBone); name - optional, the engine names it \"VB <source>_<target>\" otherwise"),
			  TEXT("MifBridgeSkeleton.cpp"), 803, nullptr },
			{ TEXT("remove_virtual_bone"), GMifDescKeys_remove_virtual_bone, GMifDescNotes_remove_virtual_bone,
			  TEXT("skeleton (aliases: path, assetPath); name or names[]; confirm:true - removing a ") TEXT("virtual bone REPARENTS any virtual bone that used it as a source"),
			  TEXT("MifBridgeSkeleton.cpp"), 903, nullptr },
			{ TEXT("rename_virtual_bone"), GMifDescKeys_rename_virtual_bone, nullptr,
			  TEXT("skeleton (aliases: path, assetPath); name - the existing virtual bone; newName"),
			  TEXT("MifBridgeSkeleton.cpp"), 1010, nullptr },
			{ TEXT("add_anim_curve"), GMifDescKeys_add_anim_curve, GMifDescNotes_add_anim_curve,
			  TEXT("assetPath (aliases: path, animation); name - the curve name; type (float|transform, ") TEXT("default float)"),
			  TEXT("MifBridgeAnimation.cpp"), 2536, nullptr },
			{ TEXT("set_anim_curve_keys"), GMifDescKeys_set_anim_curve_keys, GMifDescNotes_set_anim_curve_keys,
			  TEXT("assetPath (aliases: path, animation); name; type (float, default); ") TEXT("keys:[{time, value, interp?}] - REPLACES the curve's keys unless append:true"),
			  TEXT("MifBridgeAnimation.cpp"), 2641, nullptr },
			{ TEXT("remove_anim_curve"), GMifDescKeys_remove_anim_curve, GMifDescNotes_remove_anim_curve,
			  TEXT("assetPath (aliases: path, animation); name; type (float|transform, default ") TEXT("float); confirm:true - the curve's keys go with it"),
			  TEXT("MifBridgeAnimation.cpp"), 2784, nullptr },
			{ TEXT("lighting_build_status"), GMifDescKeys_lighting_build_status, GMifDescNotes_lighting_build_status,
			  TEXT("(none - this reports the OPEN level's lighting build state)"),
			  TEXT("MifBridgeViewport.cpp"), 486, nullptr },
			{ TEXT("move_actors_to_level"), GMifDescKeys_move_actors_to_level, GMifDescNotes_move_actors_to_level,
			  TEXT("actorPaths[] (alias actors) - the actors to move; level (alias sublevel) - the ") TEXT("destination sublevel package path, or \"persistent\"; allOrFail (default true); ") TEXT("confirm:true - moving an actor CHANGES ITS PATH"),
			  TEXT("MifBridgeStreaming.cpp"), 3397, nullptr },
			{ TEXT("list_level_instances"), GMifDescKeys_list_level_instances, GMifDescNotes_list_level_instances,
			  TEXT("includeActors (list each loaded instance's contained actor paths), limit"),
			  TEXT("MifBridgeStreaming.cpp"), 3799, nullptr },
			{ TEXT("set_level_instance_loaded"), GMifDescKeys_set_level_instance_loaded, GMifDescNotes_set_level_instance_loaded,
			  TEXT("actorPath - a placed Level Instance; loaded:true|false"),
			  TEXT("MifBridgeStreaming.cpp"), 3846, nullptr },
			{ TEXT("edit_level_instance"), GMifDescKeys_edit_level_instance, GMifDescNotes_edit_level_instance,
			  TEXT("actorPath; action (edit|commit|discard); discardEdits - only with commit"),
			  TEXT("MifBridgeStreaming.cpp"), 3902, nullptr },
			{ TEXT("break_level_instance"), GMifDescKeys_break_level_instance, GMifDescNotes_break_level_instance,
			  TEXT("actorPath; levels (how many nesting levels to break, default 1); confirm:true"),
			  TEXT("MifBridgeStreaming.cpp"), 4021, nullptr },
			{ TEXT("remove_foliage_instances"), GMifDescKeys_remove_foliage_instances, GMifDescNotes_remove_foliage_instances,
			  TEXT("foliageType (alias type) - the EXACT foliage type path, not a substring; then ") TEXT("exactly one of indices:[int], sphere:{center:{x,y,z},radius}, ") TEXT("box:{min:{x,y,z},max:{x,y,z}} or all:true; confirm:true"),
			  TEXT("MifBridgeAuthoring.cpp"), 1621, nullptr },
			{ TEXT("source_control"), GMifDescKeys_source_control, GMifDescNotes_source_control,
			  TEXT("path (aliases packagePath, assetPath) - optional; omit it to report only whether " "revision control is configured"),
			  TEXT("MifBridgeIntrospect.cpp"), 2595, nullptr },
			{ TEXT("source_control_checkout"), GMifDescKeys_source_control_checkout, GMifDescNotes_source_control_checkout,
			  TEXT("path (aliases packagePath, assetPath); action (checkout|add|checkoutOrAdd|revert, " "default checkout); confirm:true - required for revert only"),
			  TEXT("MifBridgeIntrospect.cpp"), 2684, nullptr },
			{ TEXT("list_redirectors"), GMifDescKeys_list_redirectors, GMifDescNotes_list_redirectors,
			  TEXT("pathPrefix (alias path) or paths[]; limit"),
			  TEXT("MifBridgeCooked.cpp"), 1476, nullptr },
			{ TEXT("fixup_redirectors"), GMifDescKeys_fixup_redirectors, GMifDescNotes_fixup_redirectors,
			  TEXT("pathPrefix (alias path) or paths[]; keepRedirectors (fix references but leave the ") TEXT("redirector); confirm:true; limit"),
			  TEXT("MifBridgeCooked.cpp"), 1499, nullptr },
			{ TEXT("get_asset_tags"), GMifDescKeys_get_asset_tags, GMifDescNotes_get_asset_tags,
			  TEXT("path (aliases assetPath, objectPath) - the asset to read registry tags for"),
			  TEXT("MifBridgeCooked.cpp"), 1615, nullptr },
			{ TEXT("check_consolidate_assets"), GMifDescKeys_check_consolidate_assets, GMifDescNotes_check_consolidate_assets,
			  TEXT("target - the asset every reference will point at; sources[] - the assets to " "repoint away from"),
			  TEXT("MifBridgeAssetOps.cpp"), 1759, nullptr },
			{ TEXT("consolidate_assets"), GMifDescKeys_consolidate_assets, GMifDescNotes_consolidate_assets,
			  TEXT("target; sources[]; deleteSources (default false); confirm:true"),
			  TEXT("MifBridgeAssetOps.cpp"), 1783, nullptr },
			{ TEXT("generate_lods"), GMifDescKeys_generate_lods, GMifDescNotes_generate_lods,
			  TEXT("path (aliases assetPath, mesh); lodCount (total LODs including LOD0); ") TEXT("reductionPercentages[] - FRACTIONS 0..1, one per LOD, 1.0 = no reduction; ") TEXT("screenSizes[] (only with autoScreenSize:false); autoScreenSize (default true); ") TEXT("confirm:true"),
			  TEXT("MifBridgeCollision.cpp"), 782, nullptr },
			{ TEXT("remove_lods"), GMifDescKeys_remove_lods, GMifDescNotes_remove_lods,
			  TEXT("path (aliases assetPath, mesh); confirm:true - this strips every LOD but LOD0"),
			  TEXT("MifBridgeCollision.cpp"), 926, nullptr },
			{ TEXT("list_collections"), GMifDescKeys_list_collections, GMifDescNotes_list_collections,
			  TEXT("shareType (local|private|shared) - omit for all three"),
			  TEXT("MifBridgeCooked.cpp"), 1764, nullptr },
			{ TEXT("describe_collection"), GMifDescKeys_describe_collection, GMifDescNotes_describe_collection,
			  TEXT("name; shareType (local|private|shared, default local)"),
			  TEXT("MifBridgeCooked.cpp"), 1809, nullptr },
			{ TEXT("create_collection"), GMifDescKeys_create_collection, GMifDescNotes_create_collection,
			  TEXT("name; shareType (local|private|shared, default local); paths[] - optional assets " "to put in it immediately"),
			  TEXT("MifBridgeCooked.cpp"), 1856, nullptr },
			{ TEXT("add_to_collection"), GMifDescKeys_add_to_collection, nullptr,
			  TEXT("name; shareType (default local); paths[] (alias assets)"),
			  TEXT("MifBridgeCooked.cpp"), 2013, nullptr },
			{ TEXT("remove_from_collection"), GMifDescKeys_remove_from_collection, nullptr,
			  TEXT("name; shareType (default local); paths[] (alias assets)"),
			  TEXT("MifBridgeCooked.cpp"), 2025, nullptr },
			{ TEXT("destroy_collection"), GMifDescKeys_destroy_collection, nullptr,
			  TEXT("name; shareType (default local); confirm:true"),
			  TEXT("MifBridgeCooked.cpp"), 2037, nullptr },
			{ TEXT("get_level_blueprint"), GMifDescKeys_get_level_blueprint, GMifDescNotes_get_level_blueprint,
			  TEXT("level (a sublevel package path, or \"persistent\" / omitted for the persistent ") TEXT("level); create (default FALSE - minting a Level Blueprint dirties the map)"),
			  TEXT("MifBridgeStreaming.cpp"), 4132, nullptr },
			{ TEXT("create_macro"), GMifDescKeys_create_macro, GMifDescNotes_create_macro,
			  TEXT("blueprintId (alias: path) - a Blueprint or a Blueprint Macro Library; name; ") TEXT("inputs?[{name,type,...}]; outputs?"),
			  TEXT("MifBridgeNodes2.cpp"), 1927, nullptr },
			{ TEXT("add_k2_node"), GMifDescKeys_add_k2_node, GMifDescNotes_add_k2_node,
			  TEXT("graphId; nodeClass (alias class) - a UK2Node subclass, e.g. ") TEXT("\"K2Node_AsyncAction\" or \"K2Node_Select\"; x, y; for the async family ") TEXT("proxyFactoryFunction + proxyFactoryClass (+ proxyClass, inferred from the ") TEXT("function's return when omitted); properties{} - reflective writes applied BEFORE ") TEXT("pins are allocated"),
			  TEXT("MifBridgeNodes7.cpp"), 971, nullptr },
			{ TEXT("add_create_event"), GMifDescKeys_add_create_event, GMifDescNotes_add_create_event,
			  TEXT("graphId; function - the function or custom event to wrap; bindNode - the guid of ") TEXT("the bind node whose Delegate pin this feeds; bindPin (default \"Delegate\"); x, y"),
			  TEXT("MifBridgeDelegates.cpp"), 370, nullptr },
			{ TEXT("set_enum_value"), GMifDescKeys_set_enum_value, GMifDescNotes_set_enum_value,
			  TEXT("enum (aliases enumPath, path); then EITHER bitflags (enum-scoped) OR an entry ") TEXT("addressed by index or value (alias displayName) plus newName and/or moveTo"),
			  TEXT("MifBridgeUserTypes.cpp"), 1728, nullptr },
			{ TEXT("set_niagara_emitter"), GMifDescKeys_set_niagara_emitter, GMifDescNotes_set_niagara_emitter,
			  TEXT("path (aliases assetPath, system) - a NiagaraSystem; emitter - the handle name; ") TEXT("enabled:true|false; recompile (default FALSE - compiling from an HTTP handler is ") TEXT("opt-in)"),
			  TEXT("MifBridgeNiagara2.cpp"), 516, nullptr },
			{ TEXT("add_niagara_emitter"), GMifDescKeys_add_niagara_emitter, GMifDescNotes_add_niagara_emitter,
			  TEXT("path (aliases assetPath, system) - the NiagaraSystem to add to; emitter - the " "SOURCE UNiagaraEmitter asset to add a copy of; name (optional) - the handle name, " "defaults to the source's name; enabled (default true)"),
			  TEXT("MifBridgeNiagara2.cpp"), 694, nullptr },
			{ TEXT("remove_niagara_emitter"), GMifDescKeys_remove_niagara_emitter, GMifDescNotes_remove_niagara_emitter,
			  TEXT("path (aliases assetPath, system) - the NiagaraSystem; emitter - the handle NAME " "to remove (list_niagara_emitters reports them)"),
			  TEXT("MifBridgeNiagara2.cpp"), 882, nullptr },
			{ TEXT("add_make_struct"), GMifDescKeys_add_make_struct, GMifDescNotes_add_make_struct,
			  TEXT("graphId, structName, x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 210, nullptr },
			{ TEXT("add_break_struct"), GMifDescKeys_add_break_struct, GMifDescNotes_add_break_struct,
			  TEXT("graphId, structName, x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 254, nullptr },
			{ TEXT("add_self"), GMifDescKeys_add_self, GMifDescNotes_add_self,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 126, nullptr },
			{ TEXT("add_literal"), GMifDescKeys_add_literal, GMifDescNotes_add_literal,
			  TEXT("graphId, object (an asset OBJECT PATH; object-reference literals only), x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 296, nullptr },
			{ TEXT("create_function"), GMifDescKeys_create_function, GMifDescNotes_create_function,
			  TEXT("blueprintId (alias: path), name, inputs?, outputs?, pure?"),
			  TEXT("MifBridgeNodes2.cpp"), 360, nullptr },
			{ TEXT("set_function_flags"), GMifDescKeys_set_function_flags, GMifDescNotes_set_function_flags,
			  TEXT("target by nodeGuid (aliases: node, guid, nodeId), OR graphId, OR blueprintId (alias: path) + function (aliases: functionName, name); ") TEXT("flags: replicates (none|multicast|server|client), reliable, access (public|protected|private), pure, const (alias: isConst), callInEditor, category, tooltip, keywords"),
			  TEXT("MifBridgeNodes2.cpp"), 1143, nullptr },
			{ TEXT("rename_function"), GMifDescKeys_rename_function, GMifDescNotes_rename_function,
			  TEXT("graphId, OR blueprintId (alias: path) + oldName (aliases: function, name); plus newName (alias: to), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes2.cpp"), 570, nullptr },
			{ TEXT("rename_event"), GMifDescKeys_rename_event, GMifDescNotes_rename_event,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), newName (aliases: name, to), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes2.cpp"), 662, nullptr },
			{ TEXT("rename_event_dispatcher"), GMifDescKeys_rename_event_dispatcher, GMifDescNotes_rename_event_dispatcher,
			  TEXT("blueprintId (alias: path), oldName (aliases: name, dispatcher), newName (alias: to), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes2.cpp"), 738, nullptr },
			{ TEXT("remove_event_dispatcher"), GMifDescKeys_remove_event_dispatcher, GMifDescNotes_remove_event_dispatcher,
			  TEXT("blueprintId (alias: path), name - the dispatcher to delete, confirm - must be true"),
			  TEXT("MifBridgeNodes2.cpp"), 861, nullptr },
			{ TEXT("create_blueprint"), GMifDescKeys_create_blueprint, GMifDescNotes_create_blueprint,
			  TEXT("path (must start with /Game/), parentClass (default \"Actor\"), blueprintType ") TEXT("(Normal | FunctionLibrary | Interface | MacroLibrary | WidgetBlueprint | AnimBlueprint), ") TEXT("skeleton (alias targetSkeleton) - REQUIRED for AnimBlueprint"),
			  TEXT("MifBridgeNodes2.cpp"), 1492, nullptr },
			{ TEXT("reparent_blueprint"), GMifDescKeys_reparent_blueprint, GMifDescNotes_reparent_blueprint,
			  TEXT("blueprintId (alias: path), newParentClass (alias: parentClass)"),
			  TEXT("MifBridgeNodes2.cpp"), 1763, nullptr },
			{ TEXT("resolve_struct"), GMifDescKeys_resolve_struct, GMifDescNotes_resolve_struct,
			  TEXT("name (bare name, C++ name or full path - e.g. Vector, FGuid, /Script/CoreUObject.Transform)"),
			  TEXT("MifBridgeNodes2.cpp"), 95, nullptr },
			{ TEXT("describe_class"), GMifDescKeys_describe_class, nullptr,
			  TEXT("class (alias: className), filter (optional substring match)"),
			  TEXT("MifBridgeIntrospect.cpp"), 727, nullptr },
			{ TEXT("list_enum_values"), GMifDescKeys_list_enum_values, nullptr,
			  TEXT("enum (alias: enumName)"),
			  TEXT("MifBridgeNodes3.cpp"), 442, nullptr },
			{ TEXT("list_mounted_containers"), GMifDescKeys_list_mounted_containers, nullptr,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeCooked.cpp"), 136, nullptr },
			{ TEXT("find_assets"), GMifDescKeys_find_assets, GMifDescNotes_find_assets,
			  TEXT("class (aliases: className, type), pathPrefix, nameContains, origin, recursiveClasses, ") TEXT("limit, tags ({\"Name\":\"value\"} exact match, or {\"Name\":null} for present-with-") TEXT("any-value), includeTags (add each row's registry tags to the response)"),
			  TEXT("MifBridgeCooked.cpp"), 259, nullptr },
			{ TEXT("describe_package"), GMifDescKeys_describe_package, nullptr,
			  TEXT("package (alias: path)"),
			  TEXT("MifBridgeCooked.cpp"), 519, nullptr },
			{ TEXT("diagnose_landscape"), GMifDescKeys_diagnose_landscape, nullptr,
			  TEXT("limit"),
			  TEXT("MifBridgeCooked.cpp"), 716, nullptr },
			{ TEXT("diagnose_landscape_draws"), GMifDescKeys_diagnose_landscape_draws, nullptr,
			  TEXT("limit"),
			  TEXT("MifBridgeCooked.cpp"), 1084, nullptr },
			{ TEXT("recipe_add_debug_print"), GMifDescKeys_recipe_add_debug_print, GMifDescNotes_recipe_add_debug_print,
			  TEXT("graphId, message, functionName (default PrintToModLoader), messageParam (default Message), ") TEXT("afterNode, afterPin (default then), x, y"),
			  TEXT("MifBridgeRecipes.cpp"), 66, nullptr },
			{ TEXT("recipe_reset_and_loop"), GMifDescKeys_recipe_reset_and_loop, GMifDescNotes_recipe_reset_and_loop,
			  TEXT("graphId, arrayVar, indexVar, scoreVar (omit to skip the score SET), indexInit (default -1), ") TEXT("scoreInit (default -2.0), afterNode, afterPin (default then), x, y"),
			  TEXT("MifBridgeRecipes.cpp"), 204, nullptr },
			{ TEXT("recipe_splice_before_parent"), GMifDescKeys_recipe_splice_before_parent, GMifDescNotes_recipe_splice_before_parent,
			  TEXT("graphId, parentNode, clusterEntry, clusterExit, clusterEntryExecIn (default execute), ") TEXT("clusterExitExecOut (default then)"),
			  TEXT("MifBridgeRecipes.cpp"), 396, nullptr },
			{ TEXT("recipe_argmax_over_components"), GMifDescKeys_recipe_argmax_over_components, GMifDescNotes_recipe_argmax_over_components,
			  TEXT("graphId, loopBodyNode, loopBodyPin (default 'Loop Body'), scoreNode, scorePin, indexNode, ") TEXT("indexPin, bestScoreVar, bestIndexVar, x, y"),
			  TEXT("MifBridgeRecipes.cpp"), 473, nullptr },
			{ TEXT("read_modloader_log"), GMifDescKeys_read_modloader_log, GMifDescNotes_read_modloader_log,
			  TEXT("path (optional - defaults to the live DDS2 UE4SS.log), lines (tail size, 1-5000, default 80), filter (plain substring)"),
			  TEXT("MifBridgePipeline.cpp"), 123, nullptr },
			{ TEXT("read_engine_log"), GMifDescKeys_read_engine_log, GMifDescNotes_read_engine_log,
			  TEXT("lines (tail size, 1-5000, default 200), filter (plain substring) - always reads THIS ") TEXT("editor process's own Output Log (Saved/Logs/<Project>.log); there is no path override, ") TEXT("unlike read_modloader_log, because there is only ever one such log for a running process"),
			  TEXT("MifBridgePipeline.cpp"), 219, nullptr },
			{ TEXT("trigger_cook"), GMifDescKeys_trigger_cook, GMifDescNotes_trigger_cook,
			  TEXT("mod, asset - both optional, and both only fill placeholders in the returned command plan (this endpoint executes nothing)"),
			  TEXT("MifBridgePipeline.cpp"), 316, nullptr },
			{ TEXT("add_timeline"), GMifDescKeys_add_timeline, GMifDescNotes_add_timeline,
			  TEXT("blueprintId (alias: path), name?, floatTracks? (array of track name strings), length?, ") TEXT("autoPlay? (default false), loop? (default false), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 212, nullptr },
			{ TEXT("add_reroute"), GMifDescKeys_add_reroute, GMifDescNotes_add_reroute,
			  TEXT("graphId, x, y, and optionally srcNode + srcPin + dstNode + dstPin to SPLICE the " "reroute into that existing link (src -> knot -> dst) instead of placing a bare one"),
			  TEXT("MifBridgeNodes3.cpp"), 97, nullptr },
			{ TEXT("add_widget_animation"), GMifDescKeys_add_widget_animation, GMifDescNotes_add_widget_animation,
			  TEXT("blueprintId (alias: path), name, startTime (seconds, default 0), endTime (seconds, " "default 1), displayRate (fps, default 20)"),
			  TEXT("MifBridgeWidgets.cpp"), 1343, nullptr },
			{ TEXT("list_widget_animations"), GMifDescKeys_list_widget_animations, GMifDescNotes_list_widget_animations,
			  TEXT("blueprintId (alias: path) of a Widget Blueprint"),
			  TEXT("MifBridgeWidgets.cpp"), 1316, nullptr },
			{ TEXT("rename_tree_widget"), GMifDescKeys_rename_tree_widget, GMifDescNotes_rename_tree_widget,
			  TEXT("blueprintId (alias: path), widgetName (alias: name) — the widget to rename, newName"),
			  TEXT("MifBridgeWidgets.cpp"), 1184, nullptr },
			{ TEXT("add_widget_animation_track"), GMifDescKeys_add_widget_animation_track, GMifDescNotes_add_widget_animation_track,
			  TEXT("blueprintId (alias: path), animationName, widgetName, property " "(RenderTransform.Translation | RenderTransform.Scale | RenderTransform.Angle | " "RenderTransform.Shear | RenderOpacity | ColorAndOpacity; default " "RenderTransform.Translation). The four RenderTransform families share ONE track, " "so asking for a second of them on the same widget reports createdTrack:false - " "the track is already there and carries all seven channels"),
			  TEXT("MifBridgeWidgets.cpp"), 369, nullptr },
			{ TEXT("set_widget_animation_keys"), GMifDescKeys_set_widget_animation_keys, GMifDescNotes_set_widget_animation_keys,
			  TEXT("blueprintId (alias: path), animationName, widgetName, property " "(default RenderTransform.Translation), channel (X/Y for translation, scale and " "shear; omit or 'value' for RenderTransform.Angle and RenderOpacity; R/G/B/A for " "ColorAndOpacity), keys:[{time (SECONDS), value, interp: cubic|linear|constant}], " "replace (bool, default true — clears first). NOTE that X on RenderTransform.Scale " "and X on RenderTransform.Translation are different curves on the same section, so " "the property is what disambiguates them"),
			  TEXT("MifBridgeWidgets.cpp"), 519, nullptr },
			{ TEXT("set_widget_animation_range"), GMifDescKeys_set_widget_animation_range, GMifDescNotes_set_widget_animation_range,
			  TEXT("blueprintId (alias: path), animationName, startTime and/or endTime in SECONDS, " "displayRate in frames per second"),
			  TEXT("MifBridgeWidgets.cpp"), 834, nullptr },
			{ TEXT("remove_widget_animation"), GMifDescKeys_remove_widget_animation, GMifDescNotes_remove_widget_animation,
			  TEXT("blueprintId (alias: path), animationName"),
			  TEXT("MifBridgeWidgets.cpp"), 945, nullptr },
			{ TEXT("remove_widget_animation_track"), GMifDescKeys_remove_widget_animation_track, GMifDescNotes_remove_widget_animation_track,
			  TEXT("blueprintId (alias: path), animationName, widgetName, property (default " "RenderTransform.Translation), removeBinding (bool, default false — also drops the " "widget's possessable and AnimationBindings entry)"),
			  TEXT("MifBridgeWidgets.cpp"), 1016, nullptr },
			{ TEXT("add_class_cast"), GMifDescKeys_add_class_cast, GMifDescNotes_add_class_cast,
			  TEXT("graphId, targetClass (aliases: class, castTo, to, targetType), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 393, nullptr },
			{ TEXT("add_switch_enum"), GMifDescKeys_add_switch_enum, GMifDescNotes_add_switch_enum,
			  TEXT("graphId, enumName, hasDefault? (default false), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 518, nullptr },
			{ TEXT("add_switch_int"), GMifDescKeys_add_switch_int, GMifDescNotes_add_switch_int,
			  TEXT("graphId, cases? (NUMBER of case pins, clamped 0-256), startIndex? (default 0), ") TEXT("hasDefault? (default true), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 558, nullptr },
			{ TEXT("add_switch_string"), GMifDescKeys_add_switch_string, GMifDescNotes_add_switch_string,
			  TEXT("graphId, cases? (ARRAY of non-empty, non-duplicate label strings), caseSensitive? (default false), ") TEXT("hasDefault? (default true), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 672, nullptr },
			{ TEXT("add_switch_name"), GMifDescKeys_add_switch_name, GMifDescNotes_add_switch_name,
			  TEXT("graphId, cases? (ARRAY of non-empty, non-duplicate label strings), hasDefault? ") TEXT("(default true), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 606, nullptr },
			{ TEXT("add_enum_literal"), GMifDescKeys_add_enum_literal, GMifDescNotes_add_enum_literal,
			  TEXT("graphId, enumName, value? (the enumerator NAME, e.g. \"NewEnumerator0\"), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 738, nullptr },
			{ TEXT("set_pin_type"), GMifDescKeys_set_pin_type, nullptr,
			  TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), pin (aliases: pinName, name), ") TEXT("type, container?, valueType?"),
			  TEXT("MifBridgeNodes3.cpp"), 815, nullptr },
			{ TEXT("add_event_dispatcher"), GMifDescKeys_add_event_dispatcher, GMifDescNotes_add_event_dispatcher,
			  TEXT("blueprintId (alias: path), name, inputs (array of {name, type, container?, valueType?} — ") TEXT("the dispatcher's signature parameters)"),
			  TEXT("MifBridgeDelegates.cpp"), 117, nullptr },
			{ TEXT("add_call_dispatcher"), GMifDescKeys_add_call_dispatcher, GMifDescNotes_add_call_dispatcher,
			  TEXT("graphId, dispatcher, targetClass (optional — bind/call a dispatcher declared on that ") TEXT("EXTERNAL class instead of this blueprint's own), x, y, op (bind|unbind|unbindAll ") TEXT("on add_bind_dispatcher; add_call_dispatcher is the call spelling)"),
			  TEXT("MifBridgeDelegates.cpp"), 47, TEXT("SpawnDelegateNode") },
			{ TEXT("add_bind_dispatcher"), GMifDescKeys_add_bind_dispatcher, GMifDescNotes_add_bind_dispatcher,
			  TEXT("graphId, dispatcher, targetClass (optional — bind/call a dispatcher declared on that ") TEXT("EXTERNAL class instead of this blueprint's own), x, y, op (bind|unbind|unbindAll ") TEXT("on add_bind_dispatcher; add_call_dispatcher is the call spelling)"),
			  TEXT("MifBridgeDelegates.cpp"), 47, TEXT("SpawnDelegateNode") },
			{ TEXT("list_dispatchers"), GMifDescKeys_list_dispatchers, GMifDescNotes_list_dispatchers,
			  TEXT("blueprintId (alias: path)"),
			  TEXT("MifBridgeDelegates.cpp"), 300, nullptr },
			{ TEXT("add_component"), GMifDescKeys_add_component, GMifDescNotes_add_component,
			  TEXT("blueprintId (alias: path), componentClass (alias: class), name (optional - the new component's variable name), parentName (an EXISTING component to attach under), location, rotation, scale"),
			  TEXT("MifBridgeComponents.cpp"), 747, nullptr },
			{ TEXT("list_components"), GMifDescKeys_list_components, nullptr,
			  TEXT("EITHER blueprintId (alias: path) for a Blueprint's SCS, OR actorPath (alias: actor) for the ") TEXT("components a PLACED actor actually has - including instance components that exist on that ") TEXT("one actor only. component (alias: componentName; optional - omit for the whole list), ") TEXT("includeInherited (default true), includeNative (default true), limit (default 500)"),
			  TEXT("MifBridgeComponents.cpp"), 1021, nullptr },
			{ TEXT("remove_component"), GMifDescKeys_remove_component, GMifDescNotes_remove_component,
			  TEXT("EITHER blueprintId (alias: path) to remove from the CLASS - which changes every placed copy ") TEXT("- OR actorPath (alias: actor) to remove an INSTANCE component from one placed actor. ") TEXT("name (the component's name), confirm (required true)"),
			  TEXT("MifBridgeComponents.cpp"), 1521, nullptr },
			{ TEXT("get_inherited_component"), GMifDescKeys_get_inherited_component, nullptr,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name)"),
			  TEXT("MifBridgeInherited.cpp"), 655, nullptr },
			{ TEXT("override_inherited_component"), GMifDescKeys_override_inherited_component, GMifDescNotes_override_inherited_component,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), properties (alias: props), confirm"),
			  TEXT("MifBridgeInherited.cpp"), 806, nullptr },
			{ TEXT("revert_inherited_component"), GMifDescKeys_revert_inherited_component, nullptr,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), confirm"),
			  TEXT("MifBridgeInherited.cpp"), 1166, nullptr },
			{ TEXT("set_component_transform"), GMifDescKeys_set_component_transform, GMifDescNotes_set_component_transform,
			  TEXT("blueprintId (alias: path), name (the component's variable name), location, rotation, scale - each {x,y,z} or [x,y,z]"),
			  TEXT("MifBridgeComponents.cpp"), 1618, nullptr },
			{ TEXT("add_interface"), GMifDescKeys_add_interface, GMifDescNotes_add_interface,
			  TEXT("blueprintId (alias: path), interface (aliases: interfaceClass, class)"),
			  TEXT("MifBridgeInterfaces.cpp"), 24, nullptr },
			{ TEXT("remove_interface"), GMifDescKeys_remove_interface, GMifDescNotes_remove_interface,
			  TEXT("blueprintId (alias: path), interface (aliases: interfaceClass, class), confirm (required true)"),
			  TEXT("MifBridgeInterfaces.cpp"), 65, nullptr },
			{ TEXT("list_interfaces"), GMifDescKeys_list_interfaces, GMifDescNotes_list_interfaces,
			  TEXT("blueprintId (alias: path), includeInherited (default false)"),
			  TEXT("MifBridgeInterfaces.cpp"), 116, nullptr },
			{ TEXT("list_datatables"), GMifDescKeys_list_datatables, GMifDescNotes_list_datatables,
			  TEXT("filter (optional substring matched against the full object path; omit to list every DataTable)"),
			  TEXT("MifBridgeDataTables.cpp"), 332, nullptr },
			{ TEXT("read_datatable"), GMifDescKeys_read_datatable, nullptr,
			  TEXT("path, maxRows, textFormat (aliases: textMode, simpleText:true)"),
			  TEXT("MifBridgeDataTables.cpp"), 374, nullptr },
			{ TEXT("get_datatable_row"), GMifDescKeys_get_datatable_row, nullptr,
			  TEXT("path, rowName, textFormat (aliases: textMode, simpleText:true)"),
			  TEXT("MifBridgeDataTables.cpp"), 436, nullptr },
			{ TEXT("implement_interface_function"), GMifDescKeys_implement_interface_function, GMifDescNotes_implement_interface_function,
			  TEXT("blueprintId (alias: path), function - the interface function name to add an implementation graph for"),
			  TEXT("MifBridgeFunctions.cpp"), 21, nullptr },
			{ TEXT("remove_function"), GMifDescKeys_remove_function, GMifDescNotes_remove_function,
			  TEXT("blueprintId (alias: path), name - the function graph to delete, confirm - must be true"),
			  TEXT("MifBridgeFunctions.cpp"), 90, nullptr },
			{ TEXT("write_datatable_rows"), GMifDescKeys_write_datatable_rows, nullptr,
			  TEXT("path, rows, replace, confirm"),
			  TEXT("MifBridgeDataTables.cpp"), 507, nullptr },
			{ TEXT("delete_datatable_rows"), GMifDescKeys_delete_datatable_rows, GMifDescNotes_delete_datatable_rows,
			  TEXT("path, rowNames[], confirm=true"),
			  TEXT("MifBridgeDataTables.cpp"), 683, nullptr },
			{ TEXT("add_sequence"), GMifDescKeys_add_sequence, GMifDescNotes_add_sequence,
			  TEXT("graphId, x, y, outputs (then_N exec pin count, 2-64, default 2)"),
			  TEXT("MifBridgeNodes4.cpp"), 34, nullptr },
			{ TEXT("add_spawn_actor"), GMifDescKeys_add_spawn_actor, GMifDescNotes_add_spawn_actor,
			  TEXT("graphId, actorClass (alias: class), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 70, nullptr },
			{ TEXT("add_create_widget"), GMifDescKeys_add_create_widget, GMifDescNotes_add_create_widget,
			  TEXT("graphId, widgetClass (alias: class), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 135, nullptr },
			{ TEXT("add_get_subsystem"), GMifDescKeys_add_get_subsystem, GMifDescNotes_add_get_subsystem,
			  TEXT("graphId, subsystemClass (alias: class), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 198, nullptr },
			{ TEXT("add_make_array"), GMifDescKeys_add_make_array, GMifDescNotes_add_make_array,
			  TEXT("graphId, numInputs (element pin count, 1-64, default 1), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 239, nullptr },
			{ TEXT("add_make_map"), GMifDescKeys_add_make_map, GMifDescNotes_add_make_map,
			  TEXT("graphId, numInputs (entry count - each entry is one Key + Value pin pair, 1-64, default 1), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 316, nullptr },
			{ TEXT("add_make_set"), GMifDescKeys_add_make_set, GMifDescNotes_add_make_set,
			  TEXT("graphId, numInputs (element pin count, 1-64, default 1), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 281, nullptr },
			{ TEXT("add_format_text"), GMifDescKeys_add_format_text, GMifDescNotes_add_format_text,
			  TEXT("graphId, format (the literal Format text - its {tokens} create the argument pins), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 348, nullptr },
			{ TEXT("add_get_data_table_row"), GMifDescKeys_add_get_data_table_row, GMifDescNotes_add_get_data_table_row,
			  TEXT("graphId, dataTable (object path of the UDataTable), rowName, x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 390, nullptr },
			{ TEXT("add_comment"), GMifDescKeys_add_comment, GMifDescNotes_add_comment,
			  TEXT("graphId, x, y, width (default 400, min 32), height (default 150, min 32), text (the comment body)"),
			  TEXT("MifBridgeNodes4.cpp"), 611, nullptr },
			{ TEXT("set_node_state"), GMifDescKeys_set_node_state, GMifDescNotes_set_node_state,
			  TEXT("node (guid or objectPath); enabled (alias state): enabled | disabled | " "developmentOnly; comment (the note shown on the node); commentBubble (bool, " "whether that note is pinned open)"),
			  TEXT("MifBridgeNodes4.cpp"), 485, nullptr },
			{ TEXT("list_blend_profiles"), GMifDescKeys_list_blend_profiles, GMifDescNotes_list_blend_profiles,
			  TEXT("skeleton (aliases path, assetPath) - a USkeleton, or a SkeletalMesh whose " "skeleton to read; profile (optional) to report just that one"),
			  TEXT("MifBridgeAnimation.cpp"), 3271, nullptr },
			{ TEXT("create_blend_profile"), GMifDescKeys_create_blend_profile, GMifDescNotes_create_blend_profile,
			  TEXT("skeleton (aliases path, assetPath) - a USkeleton, or a SkeletalMesh whose " "skeleton to add to; name - the profile's name"),
			  TEXT("MifBridgeAnimation.cpp"), 3310, nullptr },
			{ TEXT("set_blend_profile_bone"), GMifDescKeys_set_blend_profile_bone, GMifDescNotes_set_blend_profile_bone,
			  TEXT("skeleton (aliases path, assetPath), profile - the blend profile's name, " "bone - a bone on that skeleton, scale - the per-bone factor, " "recurse (default false) to apply it to every child bone too"),
			  TEXT("MifBridgeAnimation.cpp"), 3373, nullptr },
			{ TEXT("group_actors"), GMifDescKeys_group_actors, GMifDescNotes_group_actors,
			  TEXT("actorPaths[] (alias actors) - two or more actors in the SAME level; ") TEXT("enableGrouping:true - switch the editor's grouping mode on if it is off. That is ") TEXT("a persistent editor setting, so it is never changed implicitly"),
			  TEXT("MifBridgeLevel.cpp"), 979, nullptr },
			{ TEXT("ungroup_actors"), GMifDescKeys_ungroup_actors, GMifDescNotes_ungroup_actors,
			  TEXT("actorPaths[] (aliases actors, group) - the group to disband, or any actor in it"),
			  TEXT("MifBridgeLevel.cpp"), 1161, nullptr },
			{ TEXT("set_widget_is_variable"), GMifDescKeys_set_widget_is_variable, GMifDescNotes_set_widget_is_variable,
			  TEXT("blueprintId (alias: path), widgetName, isVariable (default true)"),
			  TEXT("MifBridgeWidgets.cpp"), 1449, nullptr },
			{ TEXT("add_widget_binding"), GMifDescKeys_add_widget_binding, GMifDescNotes_add_widget_binding,
			  TEXT("blueprintId (alias: path), widgetName, propertyName, functionName - all four required"),
			  TEXT("MifBridgeWidgets.cpp"), 1492, nullptr },
			{ TEXT("remove_widget_binding"), GMifDescKeys_remove_widget_binding, GMifDescNotes_remove_widget_binding,
			  TEXT("blueprintId (alias: path), widgetName, propertyName - both required"),
			  TEXT("MifBridgeWidgets.cpp"), 1638, nullptr },
			{ TEXT("list_widget_bindings"), GMifDescKeys_list_widget_bindings, GMifDescNotes_list_widget_bindings,
			  TEXT("blueprintId (alias: path) - required; widgetName and propertyName narrow the list"),
			  TEXT("MifBridgeWidgets.cpp"), 1567, nullptr },
			{ TEXT("add_tree_widget"), GMifDescKeys_add_tree_widget, GMifDescNotes_add_tree_widget,
			  TEXT("blueprintId (alias: path), widgetClass (alias: class), name (optional, uniquified on collision), ") TEXT("parentName or asRoot, and canvas-slot placement x, y, autoSize (default true)"),
			  TEXT("MifBridgeWidgets.cpp"), 1701, nullptr },
			{ TEXT("remove_tree_widget"), GMifDescKeys_remove_tree_widget, GMifDescNotes_remove_tree_widget,
			  TEXT("blueprintId (alias: path), widgetName, confirm=true - required because this removes ") TEXT("the widget's WHOLE SUBTREE in one call, same as every other remove_* endpoint's gate"),
			  TEXT("MifBridgeWidgets.cpp"), 1875, nullptr },
			{ TEXT("list_tree_widgets"), GMifDescKeys_list_tree_widgets, GMifDescNotes_list_tree_widgets,
			  TEXT("blueprintId (alias: path)"),
			  TEXT("MifBridgeWidgets.cpp"), 2014, nullptr },
			{ TEXT("duplicate_tree_widget"), GMifDescKeys_duplicate_tree_widget, GMifDescNotes_duplicate_tree_widget,
			  TEXT("blueprintId (alias: path), widgetName, parentName (optional - defaults to the source own parent), index (optional insert position)"),
			  TEXT("MifBridgeWidgets.cpp"), 2119, nullptr },
			{ TEXT("wrap_tree_widget"), GMifDescKeys_wrap_tree_widget, GMifDescNotes_wrap_tree_widget,
			  TEXT("blueprintId (alias: path), widgetName, wrapperClass (a UPanelWidget class), wrapperName (optional)"),
			  TEXT("MifBridgeWidgets.cpp"), 2232, nullptr },
			{ TEXT("move_tree_widget"), GMifDescKeys_move_tree_widget, GMifDescNotes_move_tree_widget,
			  TEXT("blueprintId (alias: path), widgetName, parentName (the new parent panel) OR asRoot:true (+ replaceRoot:true if a root already exists), index (optional position within the new parent)"),
			  TEXT("MifBridgeWidgets.cpp"), 2323, nullptr },
			{ TEXT("set_property"), GMifDescKeys_set_property, GMifDescNotes_set_property,
			  TEXT("objectPath | (blueprintId or path) + widgetName, propertyPath, value, overrideFlag (set|refuse|ignore), enforceClamps. " "objectPath also reaches a blueprint's COMPONENTS: take the component's templatePath " "from list_components (the ..._GEN_VARIABLE path) and pass it as objectPath. " "propertyPath may be NESTED - 'BodyInstance.bSimulatePhysics' works. " "saveConfig (none|default|user) persists a config-backed setting; the response " "always reports configBacked so a session-only write is never silent."),
			  TEXT("MifBridgeNodes5.cpp"), 1156, nullptr },
			{ TEXT("get_property"), GMifDescKeys_get_property, nullptr,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, propertyPath (alias property)"),
			  TEXT("MifBridgeNodes6.cpp"), 43, nullptr },
			{ TEXT("list_object_properties"), GMifDescKeys_list_object_properties, GMifDescNotes_list_object_properties,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars"),
			  TEXT("MifBridgeNodes6.cpp"), 110, nullptr },
			{ TEXT("describe_property"), GMifDescKeys_describe_property, nullptr,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName | class (alias className); then propertyPath (alias property) OR nameContains (aliases filter, nameFilter); limit, maxValueChars, includeMetadata, includeDefault"),
			  TEXT("MifBridgeDetails.cpp"), 733, nullptr },
			{ TEXT("diff_properties_vs_default"), GMifDescKeys_diff_properties_vs_default, nullptr,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars, includeTransient, deep, recursive (alias includeChildren)"),
			  TEXT("MifBridgeDetails.cpp"), 885, nullptr },
			{ TEXT("edit_container"), GMifDescKeys_edit_container, GMifDescNotes_edit_container,
			  TEXT("objectPath (alias actorPath), propertyPath (alias property), operation (alias action) = add|insert|remove|clear|swap|resize|setKey, index (alias at), count, key, newKey, value, swapWith, newSize, overrideFlag (set|refuse|ignore)"),
			  TEXT("MifBridgeDetails.cpp"), 1651, nullptr },
			{ TEXT("reset_property_to_default"), GMifDescKeys_reset_property_to_default, nullptr,
			  TEXT("objectPath (alias actorPath), propertyPath (alias property), force (alias allowEditConst), overrideFlag (set|refuse|ignore)"),
			  TEXT("MifBridgeDetails.cpp"), 1073, nullptr },
			{ TEXT("add_nav_volume"), GMifDescKeys_add_nav_volume, GMifDescNotes_add_nav_volume,
			  TEXT("location {x,y,z}, size {x,y,z} (coverage in WORLD UNITS), label"),
			  TEXT("MifBridgeNavigation.cpp"), 47, nullptr },
			{ TEXT("build_navmesh"), GMifDescKeys_build_navmesh, GMifDescNotes_build_navmesh,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeNavigation.cpp"), 112, nullptr },
			{ TEXT("nav_status"), GMifDescKeys_nav_status, GMifDescNotes_nav_status,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeNavigation.cpp"), 151, nullptr },
			{ TEXT("move_actor_to"), GMifDescKeys_move_actor_to, GMifDescNotes_move_actor_to,
			  TEXT("actorPath (alias: actor) - the pawn to move; location {x,y,z} - the goal"),
			  TEXT("MifBridgeNavigation.cpp"), 196, nullptr },
			{ TEXT("spawn_many"), GMifDescKeys_spawn_many, GMifDescNotes_spawn_many,
			  TEXT("items[] (required), actorClass, mesh, material, folder, labelPrefix"),
			  TEXT("MifBridgeAuthoring.cpp"), 206, nullptr },
			{ TEXT("duplicate_actors"), GMifDescKeys_duplicate_actors, GMifDescNotes_duplicate_actors,
			  TEXT("actorPaths[] and/or labelPrefix (source selection), offset {x,y,z}, yawOffset (degrees), count, labelSuffix, folder"),
			  TEXT("MifBridgeAuthoring.cpp"), 465, nullptr },
			{ TEXT("create_material_instance"), GMifDescKeys_create_material_instance, GMifDescNotes_create_material_instance,
			  TEXT("parent (alias: parentMaterial), path (must start with /Game/), scalars {name:number}, vectors {name:{r,g,b,a}}"),
			  TEXT("MifBridgeAuthoring.cpp"), 601, nullptr },
			{ TEXT("set_material_parameter"), GMifDescKeys_set_material_parameter, GMifDescNotes_set_material_parameter,
			  TEXT("material (aliases: materialPath, path), scalars {name:number}, vectors {name:{r,g,b,a}}, ") TEXT("textures {name:\"/Game/...\"}, switches {name:true|false}, ") TEXT("and/or the singular pair parameter (aliases: parameterName, name) + value. ") TEXT("association (global|layer|blend) + index address a LAYER parameter — list_material_parameters ") TEXT("reports both, and a layer parameter addressed as a global is simply not found"),
			  TEXT("MifBridgeAuthoring.cpp"), 779, nullptr },
			{ TEXT("add_foliage_instances"), GMifDescKeys_add_foliage_instances, GMifDescNotes_add_foliage_instances,
			  TEXT("EITHER mesh (alias: staticMesh) for a standalone instanced-mesh actor, OR foliageType " "(alias: type) to place into the level's real Foliage system; instances[] (required), " "label and folder (mesh mode only)"),
			  TEXT("MifBridgeAuthoring.cpp"), 1101, nullptr },
			{ TEXT("list_foliage_instances"), GMifDescKeys_list_foliage_instances, GMifDescNotes_list_foliage_instances,
			  TEXT("foliageType (alias: type) - substring matched against the foliage type path; " "includeInstances (default false - counts only); limit (default 200, per type)"),
			  TEXT("MifBridgeAuthoring.cpp"), 1412, nullptr },
			{ TEXT("create_landscape"), GMifDescKeys_create_landscape, GMifDescNotes_create_landscape,
			  TEXT("location {x,y,z}, scale {x,y,z}, componentsX, componentsY, quadsPerSection (7|15|31|63|127|255), ") TEXT("sectionsPerComponent (1|2), material (alias: landscapeMaterial), ") TEXT("layers [{layerInfo (aliases: info, path), weight}], heightMode (\"flat\"|\"rolling\"|\"island\"), ") TEXT("amplitude, frequency, seed, label, folder"),
			  TEXT("MifBridgeLandscape.cpp"), 261, nullptr },
			{ TEXT("sculpt_landscape"), GMifDescKeys_sculpt_landscape, GMifDescNotes_sculpt_landscape,
			  TEXT("landscape (alias: actorPath; omit when there is only one), center {x,y} in WORLD units, ") TEXT("radius (world units), mode (\"raise\"|\"lower\"|\"flatten\"|\"smooth\"), ") TEXT("amount (world units, raise/lower ONLY), targetZ (a world Z, flatten ONLY), ") TEXT("falloff (0..1 of the radius that is feathered)"),
			  TEXT("MifBridgeLandscape.cpp"), 554, nullptr },
			{ TEXT("import_landscape_heightmap"), GMifDescKeys_import_landscape_heightmap, GMifDescNotes_import_landscape_heightmap,
			  TEXT("landscape (alias actorPath); file - a 16-bit greyscale PNG or raw .r16 - OR data, " "base64 little-endian uint16; width/height REQUIRED with data; x0/y0 for a region " "write (default: the landscape's own origin); minZ/maxZ to map 0..65535 onto a " "world Z range (both or neither - default is a straight copy, since the native " "storage is already uint16)"),
			  TEXT("MifBridgeLandscape.cpp"), 1838, nullptr },
			{ TEXT("export_landscape_heightmap"), GMifDescKeys_export_landscape_heightmap, GMifDescNotes_export_landscape_heightmap,
			  TEXT("landscape (alias actorPath); file - .png (16-bit greyscale) or .r16, default " "<ProjectSaved>/MifBridge/Export/<Landscape>.r16; x0/y0/width/height for a region; " "asData:true to also return base64 little-endian uint16 instead of only a path"),
			  TEXT("MifBridgeLandscape.cpp"), 2047, nullptr },
			{ TEXT("paint_landscape"), GMifDescKeys_paint_landscape, GMifDescNotes_paint_landscape,
			  TEXT("landscape (alias: actorPath; omit when there is only one), ") TEXT("layerInfo (aliases: layer, info) - a LandscapeLayerInfoObject ASSET PATH, ") TEXT("center {x,y} in WORLD units, radius (world units), weight (0..1), ") TEXT("falloff (0..1 of the radius that is feathered)"),
			  TEXT("MifBridgeLandscape.cpp"), 971, nullptr },
			{ TEXT("register_landscape_layer"), GMifDescKeys_register_landscape_layer, GMifDescNotes_register_landscape_layer,
			  TEXT("landscape (alias actorPath; omit when there is only one), ") TEXT("layerName (alias layer) - a layer the landscape MATERIAL declares, ") TEXT("layerInfo - assign an EXISTING LandscapeLayerInfoObject asset path instead of ") TEXT("creating one, template - clone another LayerInfo's settings when creating"),
			  TEXT("MifBridgeLandscape.cpp"), 805, nullptr },
			{ TEXT("bind_landscape_rvt"), GMifDescKeys_bind_landscape_rvt, GMifDescNotes_bind_landscape_rvt,
			  TEXT("landscape (alias: actorPath; omit when there is only one), ") TEXT("runtimeVirtualTextures [assetPath,...], createVolumes (bool, default true)"),
			  TEXT("MifBridgeLandscape.cpp"), 1117, nullptr },
			{ TEXT("landscape_info"), GMifDescKeys_landscape_info, GMifDescNotes_landscape_info,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeLandscape.cpp"), 1217, nullptr },
			{ TEXT("new_level"), GMifDescKeys_new_level, GMifDescNotes_new_level,
			  TEXT("partitioned (bool, default false) - the only parameter; new_level takes no path"),
			  TEXT("MifBridgeWorld.cpp"), 124, nullptr },
			{ TEXT("save_level_as"), GMifDescKeys_save_level_as, GMifDescNotes_save_level_as,
			  TEXT("path (aliases: packagePath, assetPath) - the package path to save the open level to, e.g. \"/Game/Maps/MyLevel\""),
			  TEXT("MifBridgeWorld.cpp"), 158, nullptr },
			{ TEXT("load_level"), GMifDescKeys_load_level, GMifDescNotes_load_level,
			  TEXT("path (aliases: packagePath, assetPath) - the package path of the map to open, e.g. \"/Game/Maps/MyLevel\""),
			  TEXT("MifBridgeWorld.cpp"), 198, nullptr },
			{ TEXT("set_spline_points"), GMifDescKeys_set_spline_points, GMifDescNotes_set_spline_points,
			  TEXT("actorPath (alias: actor), component (alias: componentName), points:[{x,y,z},...] (at least 2), space (\"world\"|\"local\"), pointType (\"curve\"|\"linear\"|\"constant\"|\"curveClamped\"|\"curveCustomTangent\"), closedLoop (aliases: closed, loop), snapToGround (bool, needs space:\"world\"), groundOffset (number), skipPostEditChange (bool - do NOT re-run the owning actor's construction script; REQUIRED on blueprints that rebuild their own spline)"),
			  TEXT("MifBridgeWorld.cpp"), 240, nullptr },
			{ TEXT("get_spline_points"), GMifDescKeys_get_spline_points, GMifDescNotes_get_spline_points,
			  TEXT("actorPath (alias: actor), component (alias: componentName), space (\"world\"|\"local\", default world)"),
			  TEXT("MifBridgeWorld.cpp"), 392, nullptr },
			{ TEXT("snap_actors_to_ground"), GMifDescKeys_snap_actors_to_ground, GMifDescNotes_snap_actors_to_ground,
			  TEXT("actorPaths:[...], folder, labelContains, all (bool), offset (number), traceHeight (number), alignToNormal (bool), groundActor (alias: ground), allowAnyHit (bool)"),
			  TEXT("MifBridgeWorld.cpp"), 440, nullptr },
			{ TEXT("set_viewport_camera"), GMifDescKeys_set_viewport_camera, GMifDescNotes_set_viewport_camera,
			  TEXT("location:{x,y,z}, rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z} (wins over ") TEXT("rotation), fov, ortho (top/bottom/front/back/left/right/perspective), orthoZoom, ") TEXT("viewMode (Lit, Unlit, Wireframe, LightingOnly, ShaderComplexity, ...), showFlags ") TEXT("({\"Fog\": false, \"Bounds\": true}), gameView (hides editor-only sprites and ") TEXT("grids), realtime"),
			  TEXT("MifBridgeViewport.cpp"), 210, nullptr },
			{ TEXT("list_viewport_bookmarks"), GMifDescKeys_list_viewport_bookmarks, GMifDescNotes_list_viewport_bookmarks,
			  TEXT("(no parameters) - lists every bookmark slot on the CURRENT LEVEL"),
			  TEXT("MifBridgeViewport.cpp"), 617, nullptr },
			{ TEXT("set_viewport_bookmark"), GMifDescKeys_set_viewport_bookmark, GMifDescNotes_set_viewport_bookmark,
			  TEXT("index (alias slot) - which numbered slot to capture the CURRENT camera into"),
			  TEXT("MifBridgeViewport.cpp"), 652, nullptr },
			{ TEXT("jump_viewport_bookmark"), GMifDescKeys_jump_viewport_bookmark, GMifDescNotes_jump_viewport_bookmark,
			  TEXT("index (alias slot) - the numbered slot to move the camera to"),
			  TEXT("MifBridgeViewport.cpp"), 713, nullptr },
			{ TEXT("clear_viewport_bookmark"), GMifDescKeys_clear_viewport_bookmark, GMifDescNotes_clear_viewport_bookmark,
			  TEXT("index (alias slot) - the slot to clear; OR all:true to clear every slot"),
			  TEXT("MifBridgeViewport.cpp"), 789, nullptr },
			{ TEXT("focus_viewport"), GMifDescKeys_focus_viewport, GMifDescNotes_focus_viewport,
			  TEXT("actorPath (alias: actor) to frame ONE actor, folder to frame a folder subtree, all (or nothing at all) to frame the whole level, instant"),
			  TEXT("MifBridgeViewport.cpp"), 361, nullptr },
			{ TEXT("get_viewport_camera"), GMifDescKeys_get_viewport_camera, GMifDescNotes_get_viewport_camera,
			  TEXT("showFlags - omit for the ~20 flags an agent usually wants, or pass \"all\" for ") TEXT("every one the engine knows"),
			  TEXT("MifBridgeViewport.cpp"), 433, nullptr },
			{ TEXT("get_actor_bounds"), GMifDescKeys_get_actor_bounds, GMifDescNotes_get_actor_bounds,
			  TEXT("actorPath (aliases: actor, path) — the PLACED actor to measure, given as an object path, object name or label"),
			  TEXT("MifBridgeSpatial.cpp"), 139, nullptr },
			{ TEXT("check_overlaps"), GMifDescKeys_check_overlaps, GMifDescNotes_check_overlaps,
			  TEXT("actorPath (alias: actor) to test ONE actor, or omit both for a whole-scene audit; nameContains, ignoreGround, tolerance"),
			  TEXT("MifBridgeSpatial.cpp"), 172, nullptr },
			{ TEXT("trace_ground"), GMifDescKeys_trace_ground, GMifDescNotes_trace_ground,
			  TEXT("x, y (or location:{x,y,z}, whose z seeds fromZ), fromZ, toZ, ignoreActor (alias: actorPath)"),
			  TEXT("MifBridgeSpatial.cpp"), 1175, nullptr },
			{ TEXT("trace"), GMifDescKeys_trace, GMifDescNotes_trace,
			  TEXT("start:{x,y,z} plus either end:{x,y,z} or direction:{x,y,z} + distance; " "shape (line|sphere|box|capsule, default line), radius (sphere/capsule), " "halfExtent:{x,y,z} (box), halfHeight (capsule), channel (default worldStatic), " "traceComplex (default true), multi (default false), ignoreActors:[names or paths], " "draw (bool - leave the ray in the viewport), drawDuration (seconds, default 5)"),
			  TEXT("MifBridgeSpatial.cpp"), 824, nullptr },
			{ TEXT("draw_debug"), GMifDescKeys_draw_debug, GMifDescNotes_draw_debug,
			  TEXT("shape (line|sphere|box|point|arrow|string), start:{x,y,z} and end:{x,y,z} for " "line/arrow, center:{x,y,z} for sphere/box/point/string, radius (sphere), " "extent:{x,y,z} (box), text (string), color (red|green|blue|yellow|cyan|magenta|" "orange|white|black, default green), duration (seconds, default 5), thickness"),
			  TEXT("MifBridgeSpatial.cpp"), 1025, nullptr },
			{ TEXT("get_perf_stats"), GMifDescKeys_get_perf_stats, GMifDescNotes_get_perf_stats,
			  TEXT("(no parameters)"),
			  TEXT("MifBridgeSpatial.cpp"), 708, nullptr },
			{ TEXT("capture_viewport"), GMifDescKeys_capture_viewport, GMifDescNotes_capture_viewport,
			  TEXT("path (alias: name, file) - where to write the PNG; defaults to " "Saved/MifBridge/Viewport.png"),
			  TEXT("MifBridgeSpatial.cpp"), 351, nullptr },
			{ TEXT("audition_sound"), GMifDescKeys_audition_sound, GMifDescNotes_audition_sound,
			  TEXT("path (aliases: sound, assetPath) of any USoundBase - SoundWave, SoundCue or " "MetaSoundSource; or stop:true to silence the current preview"),
			  TEXT("MifBridgeSpatial.cpp"), 503, nullptr },
			{ TEXT("describe_metasound"), GMifDescKeys_describe_metasound, GMifDescNotes_describe_metasound,
			  TEXT("path (aliases: assetPath, metasound) - a MetaSoundSource or MetaSoundPatch asset"),
			  TEXT("MifBridgeMetasound.cpp"), 137, nullptr },
			{ TEXT("nav_project_point"), GMifDescKeys_nav_project_point, GMifDescNotes_nav_project_point,
			  TEXT("point:{x,y,z}, extent:{x,y,z} (search box, default 100/100/200)"),
			  TEXT("MifBridgeSpatial.cpp"), 573, nullptr },
			{ TEXT("nav_find_path"), GMifDescKeys_nav_find_path, GMifDescNotes_nav_find_path,
			  TEXT("start:{x,y,z}, end:{x,y,z}, draw (leave the path in the viewport), drawDuration"),
			  TEXT("MifBridgeSpatial.cpp"), 629, nullptr },
			{ TEXT("capture_camera"), GMifDescKeys_capture_camera, GMifDescNotes_capture_camera,
			  TEXT("x, y, z (or location:{x,y,z}), rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z}, useViewportCamera (aliases: useViewport, fromViewport), fov, width, height, name"),
			  TEXT("MifBridgeSpatial.cpp"), 1314, nullptr },
			{ TEXT("scene_report"), GMifDescKeys_scene_report, GMifDescNotes_scene_report,
			  TEXT("groundZ, floatTolerance, tallWarnZ — all optional; the scan itself always covers every actor in the active world"),
			  TEXT("MifBridgeSpatial.cpp"), 1603, nullptr },
			{ TEXT("start_pie"), GMifDescKeys_start_pie, GMifDescNotes_start_pie,
			  TEXT("simulate, startLocation {x,y,z}, startRotation {x,y,z}, players (1-8), ") TEXT("netMode (standalone|listen|client; default listen when players>1), oneProcess (default true), ") TEXT("width, height (client window size, multiplayer only)"),
			  TEXT("MifBridgePIE.cpp"), 205, nullptr },
			{ TEXT("stop_pie"), GMifDescKeys_stop_pie, GMifDescNotes_stop_pie,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgePIE.cpp"), 330, nullptr },
			{ TEXT("pie_status"), GMifDescKeys_pie_status, GMifDescNotes_pie_status,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgePIE.cpp"), 362, nullptr },
			{ TEXT("list_pie_actors"), GMifDescKeys_list_pie_actors, GMifDescNotes_list_pie_actors,
			  TEXT("classFilter, nameContains, limit (1-5000, default 200), netMode (server|client|any; default server)"),
			  TEXT("MifBridgePIE.cpp"), 394, nullptr },
			{ TEXT("spawn_actor_in_pie"), GMifDescKeys_spawn_actor_in_pie, GMifDescNotes_spawn_actor_in_pie,
			  TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, ") TEXT("netMode (server|client|any; default server)"),
			  TEXT("MifBridgePIE.cpp"), 564, nullptr },
			{ TEXT("run_console_captured"), GMifDescKeys_run_console_captured, GMifDescNotes_run_console_captured,
			  TEXT("command, filter (substring; only log lines containing it are returned)"),
			  TEXT("MifBridgePIE.cpp"), 504, nullptr },
			{ TEXT("list_level_actors"), GMifDescKeys_list_level_actors, GMifDescNotes_list_level_actors,
			  TEXT("classFilter, nameContains, folder, selectedOnly, limit"),
			  TEXT("MifBridgeLevel.cpp"), 222, nullptr },
			{ TEXT("get_level_actor"), GMifDescKeys_get_level_actor, GMifDescNotes_get_level_actor,
			  TEXT("actorPath (aliases: actor, path)"),
			  TEXT("MifBridgeLevel.cpp"), 193, nullptr },
			{ TEXT("attach_actor"), GMifDescKeys_attach_actor, GMifDescNotes_attach_actor,
			  TEXT("child (actorPath of the actor to be parented), parent (actorPath to parent it TO), ") TEXT("socket (optional socket or bone name on the parent), keepWorldTransform (default ") TEXT("true - the child stays where it is on screen; false snaps it onto the parent)"),
			  TEXT("MifBridgeLevel.cpp"), 732, nullptr },
			{ TEXT("detach_actor"), GMifDescKeys_detach_actor, GMifDescNotes_detach_actor,
			  TEXT("actorPath (aliases: actor, path) of the CHILD to detach; keepWorldTransform ") TEXT("(default true - it stays where it is on screen rather than snapping back)"),
			  TEXT("MifBridgeLevel.cpp"), 882, nullptr },
			{ TEXT("spawn_actor_in_level"), GMifDescKeys_spawn_actor_in_level, GMifDescNotes_spawn_actor_in_level,
			  TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, folder"),
			  TEXT("MifBridgeLevel.cpp"), 314, nullptr },
			{ TEXT("set_actor_transform"), GMifDescKeys_set_actor_transform, GMifDescNotes_set_actor_transform,
			  TEXT("actorPath (aliases: actor, path), location, rotation, scale, relative"),
			  TEXT("MifBridgeLevel.cpp"), 466, nullptr },
			{ TEXT("set_actor_label"), GMifDescKeys_set_actor_label, GMifDescNotes_set_actor_label,
			  TEXT("actorPath (aliases: actor, path), label, folder"),
			  TEXT("MifBridgeLevel.cpp"), 552, nullptr },
			{ TEXT("delete_level_actor"), GMifDescKeys_delete_level_actor, GMifDescNotes_delete_level_actor,
			  TEXT("actorPath (aliases: actor, path), confirm (must be true)"),
			  TEXT("MifBridgeLevel.cpp"), 607, nullptr },
			{ TEXT("select_level_actors"), GMifDescKeys_select_level_actors, GMifDescNotes_select_level_actors,
			  TEXT("actorPaths (array of full actor paths), clear"),
			  TEXT("MifBridgeLevel.cpp"), 648, nullptr },
			{ TEXT("create_struct"), GMifDescKeys_create_struct, GMifDescNotes_create_struct,
			  TEXT("path (must start with /Game/ - the struct is named after the last segment), ") TEXT("members[] (each: name, type, container?, valueType?, default?)"),
			  TEXT("MifBridgeUserTypes.cpp"), 310, nullptr },
			{ TEXT("create_datatable"), GMifDescKeys_create_datatable, GMifDescNotes_create_datatable,
			  TEXT("path (must start with /Game/ - the table is named after the last segment), ") TEXT("rowStruct (alias: struct) - a struct deriving from FTableRowBase, by name or object path"),
			  TEXT("MifBridgeUserTypes.cpp"), 445, nullptr },
			{ TEXT("create_asset"), GMifDescKeys_create_asset, GMifDescNotes_create_asset,
			  TEXT("path (/Game/...), class (alias: assetClass, className) - a concrete UObject class, " "typically a UDataAsset or UPrimaryDataAsset subclass. properties is an OPTIONAL " "{propertyPath: value} map applied before the asset is registered, so nothing " "watching the registry sees it in its default state"),
			  TEXT("MifBridgeUserTypes.cpp"), 740, nullptr },
			{ TEXT("set_struct_member"), GMifDescKeys_set_struct_member, GMifDescNotes_set_struct_member,
			  TEXT("struct (aliases: structPath, path), member (alias: memberName, or guid), and at least " "one of newName, type (+container/valueType), default"),
			  TEXT("MifBridgeUserTypes.cpp"), 548, nullptr },
			{ TEXT("list_struct_members"), GMifDescKeys_list_struct_members, nullptr,
			  TEXT("struct (aliases: structPath, path) - asset path of a Blueprint user-defined struct"),
			  TEXT("MifBridgeUserTypes.cpp"), 1285, nullptr },
			{ TEXT("add_struct_member"), GMifDescKeys_add_struct_member, GMifDescNotes_add_struct_member,
			  TEXT("struct (aliases: structPath, path), name, type, container?, valueType?, default?"),
			  TEXT("MifBridgeUserTypes.cpp"), 1314, nullptr },
			{ TEXT("remove_struct_member"), GMifDescKeys_remove_struct_member, GMifDescNotes_remove_struct_member,
			  TEXT("struct (aliases: structPath, path), name or guid, confirm=true"),
			  TEXT("MifBridgeUserTypes.cpp"), 1368, nullptr },
			{ TEXT("create_enum"), GMifDescKeys_create_enum, GMifDescNotes_create_enum,
			  TEXT("path (must start with /Game/ - the enum is named after the last segment), ") TEXT("values[] (entry display names, in order)"),
			  TEXT("MifBridgeUserTypes.cpp"), 1445, nullptr },
			{ TEXT("add_enum_value"), GMifDescKeys_add_enum_value, GMifDescNotes_add_enum_value,
			  TEXT("enum (aliases: enumPath, path), value (aliases: name, displayName) - the display name of the one new entry"),
			  TEXT("MifBridgeUserTypes.cpp"), 1561, nullptr },
			{ TEXT("remove_enum_value"), GMifDescKeys_remove_enum_value, GMifDescNotes_remove_enum_value,
			  TEXT("enum (aliases: enumPath, path), index or value (aliases: name, displayName), confirm=true"),
			  TEXT("MifBridgeUserTypes.cpp"), 1636, nullptr },
			{ TEXT("describe_animation"), GMifDescKeys_describe_animation, GMifDescNotes_describe_animation,
			  TEXT("assetPath (aliases: path, animation, asset) - the animation asset to describe, e.g. /Game/Anims/AS_Run"),
			  TEXT("MifBridgeAnimation.cpp"), 435, nullptr },
			{ TEXT("add_anim_state"), GMifDescKeys_add_anim_state, GMifDescNotes_add_anim_state,
			  TEXT("blueprintId (the Animation Blueprint); graphId - the STATE MACHINE's inner graph, ") TEXT("from list_graphs; name (the state's name, which is also its bound graph's name); ") TEXT("x, y (graph position)"),
			  TEXT("MifBridgeAnimation.cpp"), 2010, nullptr },
			{ TEXT("add_anim_notify"), GMifDescKeys_add_anim_notify, GMifDescNotes_add_anim_notify,
			  TEXT("assetPath (aliases: path, asset); time (seconds into the sequence); track (name, " "default the first existing track); ONE of notifyClass / notifyStateClass / name - " "name alone makes a skeleton notify (the AnimNotify_<Name> event kind); duration " "(states only, seconds)"),
			  TEXT("MifBridgeAnimation.cpp"), 1751, nullptr },
			{ TEXT("remove_anim_notify"), GMifDescKeys_remove_anim_notify, GMifDescNotes_remove_anim_notify,
			  TEXT("assetPath (aliases: path, asset); name (remove every notify with this name) OR " "track (remove every notify on this track); confirm:true"),
			  TEXT("MifBridgeAnimation.cpp"), 1900, nullptr },
			{ TEXT("add_sync_marker"), GMifDescKeys_add_sync_marker, GMifDescNotes_add_sync_marker,
			  TEXT("assetPath (aliases: path, asset) - an AnimSequence; name (alias: marker) - the " "marker name, which is what a sync group matches on; time (seconds into the " "sequence); trackIndex (default 0 - which notify track it sits on)"),
			  TEXT("MifBridgeAnimation.cpp"), 2965, nullptr },
			{ TEXT("remove_sync_marker"), GMifDescKeys_remove_sync_marker, GMifDescNotes_remove_sync_marker,
			  TEXT("assetPath (aliases: path, asset) - an AnimSequence; name (alias: marker) - every " "marker with this name is removed unless time is given; time (optional, seconds) - " "remove only the one at this time"),
			  TEXT("MifBridgeAnimation.cpp"), 3081, nullptr },
			{ TEXT("add_anim_notify_track"), GMifDescKeys_add_anim_notify_track, GMifDescNotes_add_anim_notify_track,
			  TEXT("assetPath (aliases: path, asset); track - the NAME of the track to create"),
			  TEXT("MifBridgeAnimation.cpp"), 1627, nullptr },
			{ TEXT("remove_anim_notify_track"), GMifDescKeys_remove_anim_notify_track, GMifDescNotes_remove_anim_notify_track,
			  TEXT("assetPath (aliases: path, asset); track - the NAME of the track to remove; " "confirm:true, because removing a track removes every notify on it"),
			  TEXT("MifBridgeAnimation.cpp"), 1682, nullptr },
			{ TEXT("list_sockets"), GMifDescKeys_list_sockets, GMifDescNotes_list_sockets,
			  TEXT("path (alias: assetPath, mesh) of a SkeletalMesh or StaticMesh asset"),
			  TEXT("MifBridgeAnimation.cpp"), 124, nullptr },
			{ TEXT("list_bones"), GMifDescKeys_list_bones, GMifDescNotes_list_bones,
			  TEXT("path (aliases: assetPath, skeleton, mesh) of a Skeleton or SkeletalMesh; " "nameContains to filter; root to list only one bone and its descendants; " "includeTransforms for the reference pose"),
			  TEXT("MifBridgeSkeleton.cpp"), 66, nullptr },
			{ TEXT("list_virtual_bones"), GMifDescKeys_list_virtual_bones, GMifDescNotes_list_virtual_bones,
			  TEXT("path (aliases: assetPath, skeleton, mesh) - a Skeleton, or a SkeletalMesh whose " "assigned Skeleton will be read"),
			  TEXT("MifBridgeSkeleton.cpp"), 265, nullptr },
			{ TEXT("list_morph_targets"), GMifDescKeys_list_morph_targets, GMifDescNotes_list_morph_targets,
			  TEXT("path (aliases: assetPath, mesh, skeletalMesh) - a SkeletalMesh asset; lod (default 0) " "- which LOD's data presence to report per target"),
			  TEXT("MifBridgeSkeleton.cpp"), 359, nullptr },
			{ TEXT("analyze_skeletal_split"), GMifDescKeys_analyze_skeletal_split, GMifDescNotes_analyze_skeletal_split,
			  TEXT("path (aliases: assetPath, mesh, skeletalMesh) - a SkeletalMesh asset; lod (default 0)"),
			  TEXT("MifBridgeSkeleton.cpp"), 465, nullptr },
			{ TEXT("set_blendspace_samples"), GMifDescKeys_set_blendspace_samples, GMifDescNotes_set_blendspace_samples,
			  TEXT("assetPath (aliases: path, blendSpace), samples[] of { animation, x, y? }, clear (default true)"),
			  TEXT("MifBridgeAnimation.cpp"), 859, nullptr },
			{ TEXT("set_bone_translation_retargeting"), GMifDescKeys_set_bone_translation_retargeting, nullptr,
			  TEXT("skeletonPath (alias: path), boneName (alias: bone), mode {Animation|Skeleton|AnimationScaled|AnimationRelative|OrientAndScale}, childrenToo (default false)"),
			  TEXT("MifBridgeAnimation.cpp"), 1282, nullptr },
			{ TEXT("list_water_bodies"), GMifDescKeys_list_water_bodies, GMifDescNotes_list_water_bodies,
			  TEXT("type (alias: waterBodyType) - River, Lake, Ocean, or Transition (aka Custom); " "nameContains (substring filter on the actor label)"),
			  TEXT("MifBridgeWater.cpp"), 235, nullptr },
			{ TEXT("describe_water_body"), GMifDescKeys_describe_water_body, GMifDescNotes_describe_water_body,
			  TEXT("path (alias: actorPath) - a water body actor; includeSplinePoints (default true)"),
			  TEXT("MifBridgeWater.cpp"), 300, nullptr },
			{ TEXT("create_water_body"), GMifDescKeys_create_water_body, GMifDescNotes_create_water_body,
			  TEXT("type (alias: waterBodyType) - River, Lake, Ocean, or Custom (aka Transition); " "label; x, y, z (the actor's location); points (optional spline, world space)"),
			  TEXT("MifBridgeWater.cpp"), 375, nullptr },
			{ TEXT("create_water_zone"), GMifDescKeys_create_water_zone, GMifDescNotes_create_water_zone,
			  TEXT("x, y, z (the zone's location); extentX, extentY (its size in world units - both or neither); label"),
			  TEXT("MifBridgeWater.cpp"), 530, nullptr },
			{ TEXT("set_water_body_spline"), GMifDescKeys_set_water_body_spline, GMifDescNotes_set_water_body_spline,
			  TEXT("path (alias: actorPath) - a water body actor; points - an array of {x,y,z} in WORLD " "space, REPLACING the existing spline"),
			  TEXT("MifBridgeWater.cpp"), 714, nullptr },
			{ TEXT("list_ik_rig"), GMifDescKeys_list_ik_rig, GMifDescNotes_list_ik_rig,
			  TEXT("path (aliases: assetPath, rig) of an IKRigDefinition asset"),
			  TEXT("MifBridgeIKRig.cpp"), 764, nullptr },
			{ TEXT("list_ik_solver_types"), GMifDescKeys_list_ik_solver_types, GMifDescNotes_list_ik_solver_types,
			  TEXT("no parameters - this lists the solver classes this engine build has"),
			  TEXT("MifBridgeIKRig.cpp"), 1855, nullptr },
			{ TEXT("add_ik_solver"), GMifDescKeys_add_ik_solver, GMifDescNotes_add_ik_solver,
			  TEXT("path (aliases: assetPath, rig), solverClass (alias: solver) - " "list_ik_solver_types shows the available ones"),
			  TEXT("MifBridgeIKRig.cpp"), 1891, nullptr },
			{ TEXT("remove_ik_solver"), GMifDescKeys_remove_ik_solver, GMifDescNotes_remove_ik_solver,
			  TEXT("path (aliases: assetPath, rig), index (alias: solverIndex) from list_ik_rig"),
			  TEXT("MifBridgeIKRig.cpp"), 1949, nullptr },
			{ TEXT("set_ik_solver"), GMifDescKeys_set_ik_solver, GMifDescNotes_set_ik_solver,
			  TEXT("path (aliases: assetPath, rig), index (alias: solverIndex), and any of rootBone, " "endBone, enabled"),
			  TEXT("MifBridgeIKRig.cpp"), 2000, nullptr },
			{ TEXT("add_ik_goal"), GMifDescKeys_add_ik_goal, GMifDescNotes_add_ik_goal,
			  TEXT("path (aliases: assetPath, rig), name (alias: goalName), bone (alias: boneName)"),
			  TEXT("MifBridgeIKRig.cpp"), 2117, nullptr },
			{ TEXT("remove_ik_goal"), GMifDescKeys_remove_ik_goal, nullptr,
			  TEXT("path (aliases: assetPath, rig), name (alias: goalName)"),
			  TEXT("MifBridgeIKRig.cpp"), 2199, nullptr },
			{ TEXT("set_ik_goal_bone"), GMifDescKeys_set_ik_goal_bone, nullptr,
			  TEXT("path (aliases: assetPath, rig), name (alias: goalName), bone (alias: boneName)"),
			  TEXT("MifBridgeIKRig.cpp"), 2245, nullptr },
			{ TEXT("set_ik_goal_solver_connection"), GMifDescKeys_set_ik_goal_solver_connection, nullptr,
			  TEXT("path (aliases: assetPath, rig), name (alias: goalName), solverIndex (alias: index), " "connected (bool, default true - false disconnects)"),
			  TEXT("MifBridgeIKRig.cpp"), 2306, nullptr },
			{ TEXT("set_ik_rig_mesh"), GMifDescKeys_set_ik_rig_mesh, GMifDescNotes_set_ik_rig_mesh,
			  TEXT("path (aliases: assetPath, rig) of an IKRigDefinition, mesh (alias: skeletalMesh)"),
			  TEXT("MifBridgeIKRig.cpp"), 1084, nullptr },
			{ TEXT("set_ik_rig_retarget_root"), GMifDescKeys_set_ik_rig_retarget_root, GMifDescNotes_set_ik_rig_retarget_root,
			  TEXT("path (aliases: assetPath, rig) of an IKRigDefinition, bone (aliases: boneName, root)"),
			  TEXT("MifBridgeIKRig.cpp"), 1177, nullptr },
			{ TEXT("add_ik_retarget_chain"), GMifDescKeys_add_ik_retarget_chain, GMifDescNotes_add_ik_retarget_chain,
			  TEXT("path (aliases: assetPath, rig), name (alias: chainName), startBone, endBone, " "goal (alias: goalName, optional)"),
			  TEXT("MifBridgeIKRig.cpp"), 1243, nullptr },
			{ TEXT("remove_ik_retarget_chain"), GMifDescKeys_remove_ik_retarget_chain, nullptr,
			  TEXT("path (aliases: assetPath, rig), name (alias: chainName)"),
			  TEXT("MifBridgeIKRig.cpp"), 1366, nullptr },
			{ TEXT("set_retarget_rigs"), GMifDescKeys_set_retarget_rigs, GMifDescNotes_set_retarget_rigs,
			  TEXT("path (aliases: assetPath, retargeter) of an IKRetargeter; source (alias: sourceRig) " "and/or target (alias: targetRig), each an IKRigDefinition path"),
			  TEXT("MifBridgeIKRig.cpp"), 1416, nullptr },
			{ TEXT("auto_map_retarget_chains"), GMifDescKeys_auto_map_retarget_chains, GMifDescNotes_auto_map_retarget_chains,
			  TEXT("path (aliases: assetPath, retargeter), mode (exact|fuzzy|clear, default fuzzy), " "remapExisting (bool, default false - also remap chains that already have a source)"),
			  TEXT("MifBridgeIKRig.cpp"), 1507, nullptr },
			{ TEXT("set_retarget_chain_mapping"), GMifDescKeys_set_retarget_chain_mapping, GMifDescNotes_set_retarget_chain_mapping,
			  TEXT("path (aliases: assetPath, retargeter), targetChain (the chain ON THE TARGET rig), " "sourceChain (the chain on the SOURCE rig to drive it, or empty to unmap)"),
			  TEXT("MifBridgeIKRig.cpp"), 1603, nullptr },
			{ TEXT("list_retarget_chain_mapping"), GMifDescKeys_list_retarget_chain_mapping, GMifDescNotes_list_retarget_chain_mapping,
			  TEXT("path (aliases: assetPath, retargeter) of an IKRetargeter asset"),
			  TEXT("MifBridgeIKRig.cpp"), 1676, nullptr },
			{ TEXT("set_niagara_component_parameter"), GMifDescKeys_set_niagara_component_parameter, GMifDescNotes_set_niagara_component_parameter,
			  TEXT("actorPath (an actor with a NiagaraComponent); name (alias: parameter) - the user " "parameter; type - float|int|bool|vector|color (inferred when unambiguous); value; " "confirm:true"),
			  TEXT("MifBridgeNiagara2.cpp"), 268, nullptr },
			{ TEXT("list_sequence_bindings"), GMifDescKeys_list_sequence_bindings, GMifDescNotes_list_sequence_bindings,
			  TEXT("path (aliases: assetPath, sequence) - a LevelSequence asset"),
			  TEXT("MifBridgeSequencerWrite.cpp"), 97, nullptr },
			{ TEXT("add_sequence_possessable"), GMifDescKeys_add_sequence_possessable, GMifDescNotes_add_sequence_possessable,
			  TEXT("path (the LevelSequence); actorPath (an actor in the OPEN level); confirm:true"),
			  TEXT("MifBridgeSequencerWrite.cpp"), 185, nullptr },
			{ TEXT("add_sequence_track"), GMifDescKeys_add_sequence_track, GMifDescNotes_add_sequence_track,
			  TEXT("path (the LevelSequence); trackClass - a UMovieSceneTrack class path such as ") TEXT("/Script/MovieSceneTracks.MovieScene3DTransformTrack; confirm:true. THREE SCOPES: ") TEXT("by default the track hangs off an object binding and needs guid (alias: binding) ") TEXT("from list_sequence_bindings; root:true adds a track to the SEQUENCE itself (Audio, ") TEXT("Fade, LevelVisibility, Subsequence) and takes no guid; cameraCut:true adds a camera ") TEXT("cut pointing at the camera bound to guid, at time (seconds)"),
			  TEXT("MifBridgeSequencerWrite.cpp"), 273, nullptr },
			{ TEXT("add_sequence_section"), GMifDescKeys_add_sequence_section, GMifDescNotes_add_sequence_section,
			  TEXT("path (the LevelSequence); guid (alias: binding) from list_sequence_bindings; ") TEXT("trackClass OR trackIndex to pick the track on that binding; startTime and endTime ") TEXT("in SECONDS; rowIndex (default 0); confirm:true"),
			  TEXT("MifBridgeSequencerWrite.cpp"), 596, nullptr },
			{ TEXT("set_sequence_keys"), GMifDescKeys_set_sequence_keys, GMifDescNotes_set_sequence_keys,
			  TEXT("path; guid (alias: binding); trackClass or trackIndex; sectionIndex (from ") TEXT("add_sequence_section); channel - the channel NAME from that response, e.g. ") TEXT("'Location.X'; keys - [{time (SECONDS), value, interp: cubic|linear|constant}]; ") TEXT("replace (default false - true clears the channel first); confirm:true"),
			  TEXT("MifBridgeSequencerWrite.cpp"), 704, nullptr },
			{ TEXT("list_state_trees"), GMifDescKeys_list_state_trees, GMifDescNotes_list_state_trees,
			  TEXT("pathPrefix (alias: prefix, default /Game/)"),
			  TEXT("MifBridgeStateTree.cpp"), 77, nullptr },
			{ TEXT("describe_state_tree"), GMifDescKeys_describe_state_tree, nullptr,
			  TEXT("path (aliases: assetPath, tree) - a StateTree asset"),
			  TEXT("MifBridgeStateTree.cpp"), 124, nullptr },
			{ TEXT("list_gameplay_tags"), GMifDescKeys_list_gameplay_tags, GMifDescNotes_list_gameplay_tags,
			  TEXT("filter (alias: search) - substring match on the tag string; onlyExplicit (default " "true) - exclude tags that exist only as implied parents; limit (0 = all)"),
			  TEXT("MifBridgeGameplayTags.cpp"), 43, nullptr },
			{ TEXT("describe_gameplay_tag"), GMifDescKeys_describe_gameplay_tag, GMifDescNotes_describe_gameplay_tag,
			  TEXT("tag (alias: name) - a full tag string such as 'Ability.Melee.Heavy'"),
			  TEXT("MifBridgeGameplayTags.cpp"), 109, nullptr },
			{ TEXT("add_gameplay_tag"), GMifDescKeys_add_gameplay_tag, GMifDescNotes_add_gameplay_tag,
			  TEXT("tag (required, e.g. 'Ability.Melee.Heavy'); comment (developer comment stored beside ") TEXT("it); source (which .ini owns it - default DefaultGameplayTags.ini); transient (bool, ") TEXT("default false - true registers for THIS EDITOR SESSION only and writes nothing to disk)"),
			  TEXT("MifBridgeGameplayTags.cpp"), 226, nullptr },
			{ TEXT("live_coding_status"), GMifDescKeys_live_coding_status, GMifDescNotes_live_coding_status,
			  TEXT("no parameters"),
			  TEXT("MifBridgeLiveCoding.cpp"), 81, nullptr },
			{ TEXT("live_coding_compile"), GMifDescKeys_live_coding_compile, GMifDescNotes_live_coding_compile,
			  TEXT("confirm:true"),
			  TEXT("MifBridgeLiveCoding.cpp"), 137, nullptr },
			{ TEXT("list_pcg_graphs"), GMifDescKeys_list_pcg_graphs, GMifDescNotes_list_pcg_graphs,
			  TEXT("pathPrefix (alias: prefix, default /Game/)"),
			  TEXT("MifBridgePCG.cpp"), 104, nullptr },
			{ TEXT("describe_pcg_graph"), GMifDescKeys_describe_pcg_graph, GMifDescNotes_describe_pcg_graph,
			  TEXT("path (aliases: assetPath, graph) - a PCGGraph asset"),
			  TEXT("MifBridgePCG.cpp"), 152, nullptr },
			{ TEXT("list_pcg_components"), GMifDescKeys_list_pcg_components, GMifDescNotes_list_pcg_components,
			  TEXT("no parameters - this lists every PCG component in the OPEN level"),
			  TEXT("MifBridgePCG.cpp"), 265, nullptr },
			{ TEXT("pcg_generate"), GMifDescKeys_pcg_generate, GMifDescNotes_pcg_generate,
			  TEXT("actorPath (an actor with a PCG component); confirm:true"),
			  TEXT("MifBridgePCG.cpp"), 306, nullptr },
			{ TEXT("pcg_cleanup"), GMifDescKeys_pcg_cleanup, nullptr,
			  TEXT("actorPath (an actor with a PCG component); confirm:true"),
			  TEXT("MifBridgePCG.cpp"), 377, nullptr },
			{ TEXT("describe_behavior_tree"), GMifDescKeys_describe_behavior_tree, GMifDescNotes_describe_behavior_tree,
			  TEXT("path (alias: assetPath) of a BehaviorTree asset"),
			  TEXT("MifBridgeAnimation.cpp"), 242, nullptr },
			{ TEXT("list_blackboard_keys"), GMifDescKeys_list_blackboard_keys, GMifDescNotes_list_blackboard_keys,
			  TEXT("path (alias: assetPath) of a BlackboardData asset"),
			  TEXT("MifBridgeAnimation.cpp"), 310, nullptr },
			{ TEXT("add_blackboard_key"), GMifDescKeys_add_blackboard_key, GMifDescNotes_add_blackboard_key,
			  TEXT("path (a BlackboardData asset); name (alias: key); type (alias: keyType) - Bool, Int, " "Float, String, Name, Vector, Rotator, Object, Class, Enum; instanceSynced (default " "false); category; confirm:true"),
			  TEXT("MifBridgeAnimation.cpp"), 1398, nullptr },
			{ TEXT("list_animations"), GMifDescKeys_list_animations, GMifDescNotes_list_animations,
			  TEXT("filter (substring matched against the full object path), skeleton (substring matched against the registry's Skeleton tag), limit (default 200, max 5000)"),
			  TEXT("MifBridgeAnimation.cpp"), 680, nullptr },
			{ TEXT("add_anim_node"), GMifDescKeys_add_anim_node, GMifDescNotes_add_anim_node,
			  TEXT("graphId (the AnimGraph or a state/transition graph inside it), nodeClass (alias: class) - any UAnimGraphNode_* class, x/y (optional layout)"),
			  TEXT("MifBridgeAnimation.cpp"), 753, nullptr },
			{ TEXT("delete_asset"), GMifDescKeys_delete_asset, GMifDescNotes_delete_asset,
			  TEXT("path (a /Game/ package or object path), confirm (required true)"),
			  TEXT("MifBridgeAssetOps.cpp"), 91, nullptr },
			{ TEXT("close_asset_editors"), GMifDescKeys_close_asset_editors, GMifDescNotes_close_asset_editors,
			  TEXT("path (aliases: objectPath, assetPath) - a /Game/ package or object path; confirm (required true)"),
			  TEXT("MifBridgeAssetOps.cpp"), 219, nullptr },
			{ TEXT("rename_asset"), GMifDescKeys_rename_asset, GMifDescNotes_rename_asset,
			  TEXT("path, newPath (the destination - its last segment is BOTH the destination folder and the new asset name), confirm (required true); ") TEXT("OR renames[] of {path, newPath} to move many in ONE IAssetTools pass"),
			  TEXT("MifBridgeAssetOps.cpp"), 450, nullptr },
			{ TEXT("fix_up_redirectors"), GMifDescKeys_fix_up_redirectors, GMifDescNotes_fix_up_redirectors,
			  TEXT("path (a /Game folder), confirm (required true unless dryRun), dryRun? (survey only, ") TEXT("no confirm needed), keepRedirectors? (fix the references but leave the redirector ") TEXT("packages), recursive? (default true)"),
			  TEXT("MifBridgeAssetOps.cpp"), 328, nullptr },
			{ TEXT("duplicate_asset"), GMifDescKeys_duplicate_asset, GMifDescNotes_duplicate_asset,
			  TEXT("path (the source asset), newPath (the destination - its last segment is BOTH the destination folder and the new asset name)"),
			  TEXT("MifBridgeAssetOps.cpp"), 689, nullptr },
			{ TEXT("get_collision"), GMifDescKeys_get_collision, GMifDescNotes_get_collision,
			  TEXT("path (aliases: assetPath, mesh, staticMesh) - a StaticMesh asset; lod (default 0) - " "which LOD's sections to report"),
			  TEXT("MifBridgeCollision.cpp"), 572, nullptr },
			{ TEXT("remove_collision"), GMifDescKeys_remove_collision, GMifDescNotes_remove_collision,
			  TEXT("path (a UStaticMesh), confirm (required true)"),
			  TEXT("MifBridgeCollision.cpp"), 313, nullptr },
			{ TEXT("add_simplified_collision"), GMifDescKeys_add_simplified_collision, GMifDescNotes_add_simplified_collision,
			  TEXT("path (a UStaticMesh), shape (box|sphere|capsule|10dop-x|10dop-y|10dop-z|18dop|26dop)"),
			  TEXT("MifBridgeCollision.cpp"), 380, nullptr },
			{ TEXT("list_collision_profiles"), GMifDescKeys_list_collision_profiles, GMifDescNotes_list_collision_profiles,
			  TEXT("(no parameters)"),
			  TEXT("MifBridgeCollision.cpp"), 111, nullptr },
			{ TEXT("set_collision"), GMifDescKeys_set_collision, GMifDescNotes_set_collision,
			  TEXT("objectPath (a component's templatePath from list_components, or a placed actor's " "component path), profile (validated against list_collision_profiles), " "collisionEnabled (NoCollision|QueryOnly|PhysicsOnly|QueryAndPhysics)"),
			  TEXT("MifBridgeCollision.cpp"), 160, nullptr },
			{ TEXT("get_referencers"), GMifDescKeys_get_referencers, GMifDescNotes_get_referencers,
			  TEXT("path; category (package|manage|searchableName|all, default package); hard (true = ") TEXT("hard only, false = SOFT only, omit for both); includeEditorOnly (default true); ") TEXT("includeProperties (per-edge hard/game/build detail)"),
			  TEXT("MifBridgeAssetOps.cpp"), 1131, nullptr },
			{ TEXT("get_dependencies"), GMifDescKeys_get_dependencies, GMifDescNotes_get_dependencies,
			  TEXT("path; category (package|manage|searchableName|all, default package); hard (true = ") TEXT("hard only, false = SOFT only, omit for both); includeEditorOnly (default true); ") TEXT("includeProperties (per-edge hard/game/build detail)"),
			  TEXT("MifBridgeAssetOps.cpp"), 1159, nullptr },
			{ TEXT("audit_unused"), GMifDescKeys_audit_unused, nullptr,
			  TEXT("pathPrefix, class, includeAll, limit, rescan, excludeReferencers (aliases: excludeReferencer, ignoreReferencers)"),
			  TEXT("MifBridgeAssetOps.cpp"), 1313, nullptr },
			{ TEXT("create_editable_child"), GMifDescKeys_create_editable_child, GMifDescNotes_create_editable_child,
			  TEXT("sourceAsset (the cooked BP - its _C class path or its asset path), childPath (destination; defaults to /Game/Mif/<Name>_Child or _Editable), variant: child | sibling | uncooked | sibling_full | full"),
			  TEXT("MifBridgeReconstruct.cpp"), 46, nullptr },
			{ TEXT("compile"), GMifDescKeys_compile, GMifDescNotes_compile,
			  TEXT("blueprintId (alias: path) - compiles the blueprint and returns {ok, numErrors, numWarnings, messages[{severity,text,nodeGuid,pinName}]}"),
			  TEXT("MifBridgeIntrospect.cpp"), 2330, nullptr },
			{ TEXT("run_console"), GMifDescKeys_run_console, GMifDescNotes_run_console,
			  TEXT("command (alias: cmd), world (editor|pie|active; default editor), captureOutput (default true)"),
			  TEXT("MifBridgeIntrospect.cpp"), 2445, nullptr },
			{ TEXT("validate"), GMifDescKeys_validate, GMifDescNotes_validate,
			  TEXT("blueprintId (alias: path) - compiles WITHOUT saving and returns the same {ok, numErrors, numWarnings, messages[]} as compile, plus dryRun:true"),
			  TEXT("MifBridgeIntrospect.cpp"), 2517, nullptr },
			{ TEXT("self_audit"), GMifDescKeys_self_audit, GMifDescNotes_self_audit,
			  TEXT("summaryOnly (alias: compact; default false) - health fields, counts and signatures only; " "includeEndpointDetails / includeEndpoints override it individually"),
			  TEXT("MifBridgeCommon.cpp"), 1025, nullptr },
			{ TEXT("batch"), GMifDescKeys_batch, GMifDescNotes_batch,
			  TEXT("ops (array), blueprintId (alias: path), backup, compileAtEnd (default true)"),
			  TEXT("MifBridgeNodes.cpp"), 2525, nullptr },
			{ TEXT("list_transactions"), GMifDescKeys_list_transactions, nullptr,
			  TEXT("limit (aliases: count, max), offset (alias: start), includeObjects (alias: include_objects)"),
			  TEXT("MifBridgeUndo.cpp"), 121, nullptr },
			{ TEXT("undo_transactions"), GMifDescKeys_undo_transactions, nullptr,
			  TEXT("count (aliases: n, steps), toIndex (alias: to_index), allowRedo (aliases: allow_redo, canRedo)"),
			  TEXT("MifBridgeUndo.cpp"), 208, nullptr },
			{ TEXT("redo_transactions"), GMifDescKeys_redo_transactions, nullptr,
			  TEXT("count (aliases: n, steps), toIndex (alias: to_index)"),
			  TEXT("MifBridgeUndo.cpp"), 341, nullptr },
			{ TEXT("project_paths"), GMifDescKeys_project_paths, GMifDescNotes_project_paths,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeCommon.cpp"), 1272, nullptr },
			{ TEXT("list_dirty_packages"), GMifDescKeys_list_dirty_packages, nullptr,
			  TEXT("kind (content|world|all)"),
			  TEXT("MifBridgeUndo.cpp"), 468, nullptr },
			{ TEXT("save_dirty_packages"), GMifDescKeys_save_dirty_packages, nullptr,
			  TEXT("maps (aliases: saveMaps, save_maps), content (aliases: saveContent, save_content), dryRun (alias: dry_run)"),
			  TEXT("MifBridgeUndo.cpp"), 562, nullptr },
			{ TEXT("create_material"), GMifDescKeys_create_material, nullptr,
			  TEXT("path (alias: assetPath), domain (alias: materialDomain), blendMode, initialTexture"),
			  TEXT("MifBridgeMaterials.cpp"), 1062, nullptr },
			{ TEXT("create_material_function"), GMifDescKeys_create_material_function, GMifDescNotes_create_material_function,
			  TEXT("path (alias: assetPath), description, exposeToLibrary"),
			  TEXT("MifBridgeMaterials.cpp"), 1184, nullptr },
			{ TEXT("add_material_expression"), GMifDescKeys_add_material_expression, nullptr,
			  TEXT("path (aliases: material, materialPath), class (aliases: expressionClass, type), x (aliases: nodePosX, posX), y (aliases: nodePosY, posY), properties (alias: props), asset (alias: selectedAsset)"),
			  TEXT("MifBridgeMaterials.cpp"), 1246, nullptr },
			{ TEXT("connect_material_expressions"), GMifDescKeys_connect_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath), from (alias: fromExpression), fromOutput (alias: fromOutputName), to (alias: toExpression), toInput (alias: toInputName)"),
			  TEXT("MifBridgeMaterials.cpp"), 1392, nullptr },
			{ TEXT("connect_material_property"), GMifDescKeys_connect_material_property, nullptr,
			  TEXT("path (aliases: material, materialPath), from (alias: fromExpression), fromOutput (alias: fromOutputName), property (alias: materialProperty)"),
			  TEXT("MifBridgeMaterials.cpp"), 1463, nullptr },
			{ TEXT("delete_material_expression"), GMifDescKeys_delete_material_expression, nullptr,
			  TEXT("path (aliases: material, materialPath), expression (alias: name), all (alias: deleteAll)"),
			  TEXT("MifBridgeMaterials.cpp"), 1554, nullptr },
			{ TEXT("list_material_expressions"), GMifDescKeys_list_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath), includeConnections, includeProperties"),
			  TEXT("MifBridgeMaterials.cpp"), 1697, nullptr },
			{ TEXT("list_material_parameters"), GMifDescKeys_list_material_parameters, GMifDescNotes_list_material_parameters,
			  TEXT("path (aliases: material, assetPath) of a Material or MaterialInstance; " "types:[scalar|vector|texture|staticSwitch|doubleVector|font|runtimeVirtualTexture|" "sparseVolumeTexture|staticComponentMask] to filter; group to filter by parameter group; " "layers:true to also report the material LAYER STACK"),
			  TEXT("MifBridgeMaterials.cpp"), 186, nullptr },
			{ TEXT("set_material_layers"), GMifDescKeys_set_material_layers, GMifDescNotes_set_material_layers,
			  TEXT("path (aliases: material, assetPath) of a MaterialInstance; " "layers[] of { function (a UMaterialFunction path), blend (required on every " "entry except the first), name, enabled }"),
			  TEXT("MifBridgeMaterials.cpp"), 2249, nullptr },
			{ TEXT("list_niagara_user_parameters"), GMifDescKeys_list_niagara_user_parameters, GMifDescNotes_list_niagara_user_parameters,
			  TEXT("path (aliases: assetPath, system) of a NiagaraSystem; nameContains to filter"),
			  TEXT("MifBridgeNiagara.cpp"), 167, nullptr },
			{ TEXT("set_niagara_user_parameter"), GMifDescKeys_set_niagara_user_parameter, GMifDescNotes_set_niagara_user_parameter,
			  TEXT("path (aliases assetPath, system) - a NiagaraSystem; name - the User. parameter " "(list_niagara_user_parameters reports them, with types); value - a number for " "float/int, true/false for bool, or an array for vec2/vec3/vec4/quat/color/position"),
			  TEXT("MifBridgeNiagara.cpp"), 585, nullptr },
			{ TEXT("layout_material_expressions"), GMifDescKeys_layout_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath)"),
			  TEXT("MifBridgeMaterials.cpp"), 1868, nullptr },
			{ TEXT("recompile_material"), GMifDescKeys_recompile_material, nullptr,
			  TEXT("path (aliases: material, asset)"),
			  TEXT("MifBridgeMaterials.cpp"), 1930, nullptr },
			{ TEXT("material_statistics"), GMifDescKeys_material_statistics, GMifDescNotes_material_statistics,
			  TEXT("path (aliases assetPath, material) - a UMaterial or UMaterialInstance; " "compile (default FALSE - when the shader map is not already built, opting in " "STALLS the editor until it compiles, which can take minutes)"),
			  TEXT("MifBridgeMaterials.cpp"), 2103, nullptr },
			{ TEXT("shader_compile_status"), GMifDescKeys_shader_compile_status, nullptr,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeMaterials.cpp"), 2047, nullptr },
			{ TEXT("list_sublevels"), GMifDescKeys_list_sublevels, nullptr,
			  TEXT("world (\"editor\"|\"pie\"), netMode (\"server\"|\"client\"|\"any\", only meaningful with world:\"pie\")"),
			  TEXT("MifBridgeStreaming.cpp"), 499, nullptr },
			{ TEXT("list_data_layers"), GMifDescKeys_list_data_layers, GMifDescNotes_list_data_layers,
			  TEXT("(none - this endpoint takes no parameters; it reports the Data Layers of the world the " "editor currently has open)"),
			  TEXT("MifBridgeStreaming.cpp"), 1553, nullptr },
			{ TEXT("apply_spline_to_landscape"), GMifDescKeys_apply_spline_to_landscape, GMifDescNotes_apply_spline_to_landscape,
			  TEXT("splineActor (alias: spline) - an actor with a USplineComponent; landscape (alias: ") TEXT("actorPath, omit when the level has one); component - which spline component if the ") TEXT("actor has several; startWidth/endWidth (default 200uu); startSideFalloff/") TEXT("endSideFalloff (default 200uu); startRoll/endRoll (degrees, default 0); ") TEXT("subdivisions (default 20); raiseHeights/lowerHeights (default true); paintLayer - ") TEXT("a LandscapeLayerInfoObject path; editLayer - REQUIRED on a landscape with edit ") TEXT("layers"),
			  TEXT("MifBridgeLandscape.cpp"), 1452, nullptr },
			{ TEXT("list_partition_actors"), GMifDescKeys_list_partition_actors, GMifDescNotes_list_partition_actors,
			  TEXT("classFilter (alias: class) - a native actor class path; nameContains - substring " "match on label or name; dataLayer - only actors in this Data Layer; loadedOnly " "(default false) - only actors currently in memory; limit (default 200); " "bounds {min:{x,y,z}, max:{x,y,z}} - only actors whose editor bounds intersect " "this box"),
			  TEXT("MifBridgeStreaming.cpp"), 3137, nullptr },
			{ TEXT("load_partition_actors"), GMifDescKeys_load_partition_actors, GMifDescNotes_load_partition_actors,
			  TEXT("guids (alias: guid) - actor guids from list_partition_actors; bounds {min:{x,y,z}," " max:{x,y,z}} - load every actor intersecting this box; unpin (default false) - " "release the given guids instead of pinning them"),
			  TEXT("MifBridgeStreaming.cpp"), 2870, nullptr },
			{ TEXT("list_layers"), GMifDescKeys_list_layers, GMifDescNotes_list_layers,
			  TEXT("includeActors (default false - list each layer's member actorPaths, which is the ") TEXT("expensive part), limit (max layers reported, default 200)"),
			  TEXT("MifBridgeStreaming.cpp"), 2228, nullptr },
			{ TEXT("modify_actor_layers"), GMifDescKeys_modify_actor_layers, GMifDescNotes_modify_actor_layers,
			  TEXT("operation: add | remove | create | delete | select. add/remove/select need ") TEXT("actorPaths (aliases: actors); create/delete need only the layer name; delete needs ") TEXT("confirm:true. layer (one) or layers (array)"),
			  TEXT("MifBridgeStreaming.cpp"), 2426, nullptr },
			{ TEXT("set_layer_visibility"), GMifDescKeys_set_layer_visibility, GMifDescNotes_set_layer_visibility,
			  TEXT("layer (one name) or layers (array of names); visible (bool, required)"),
			  TEXT("MifBridgeStreaming.cpp"), 2324, nullptr },
			{ TEXT("list_level_sequences"), GMifDescKeys_list_level_sequences, GMifDescNotes_list_level_sequences,
			  TEXT("filter (aliases: search, name) - substring matched against the full object path; " "limit (default 0 = uncapped)"),
			  TEXT("MifBridgeSequencer.cpp"), 47, nullptr },
			{ TEXT("describe_level_sequence"), GMifDescKeys_describe_level_sequence, GMifDescNotes_describe_level_sequence,
			  TEXT("path (aliases: assetPath, objectPath, sequencePath) - a LevelSequence asset"),
			  TEXT("MifBridgeSequencer.cpp"), 132, nullptr },
			{ TEXT("describe_niagara_system"), GMifDescKeys_describe_niagara_system, GMifDescNotes_describe_niagara_system,
			  TEXT("path (aliases: assetPath, system) - a NiagaraSystem asset"),
			  TEXT("MifBridgeNiagara2.cpp"), 98, nullptr },
			{ TEXT("list_niagara_emitters"), GMifDescKeys_list_niagara_emitters, GMifDescNotes_list_niagara_emitters,
			  TEXT("path (aliases: assetPath, system) - a NiagaraSystem asset; nameContains (substring " "filter); includeDisabled (default true)"),
			  TEXT("MifBridgeNiagara2.cpp"), 184, nullptr },
			{ TEXT("list_game_feature_plugins"), GMifDescKeys_list_game_feature_plugins, GMifDescNotes_list_game_feature_plugins,
			  TEXT("nameContains (substring filter on the plugin name); activeOnly (default false)"),
			  TEXT("MifBridgeGameFeatures.cpp"), 166, nullptr },
			{ TEXT("describe_game_feature_plugin"), GMifDescKeys_describe_game_feature_plugin, GMifDescNotes_describe_game_feature_plugin,
			  TEXT("name (aliases: plugin, pluginName) - a plugin name like 'DDS2Casino'"),
			  TEXT("MifBridgeGameFeatures.cpp"), 253, nullptr },
			{ TEXT("create_procedural_mesh"), GMifDescKeys_create_procedural_mesh, GMifDescNotes_create_procedural_mesh,
			  TEXT("path (alias: assetPath) - where to create the new StaticMesh; ") TEXT("shape (box|sphere|cylinder|cone|torus); ") TEXT("box: dimensionX/Y/Z (default 100 each), steps (subdivision on all three axes, default 0); ") TEXT("sphere: radius (default 50), stepsPhi/stepsTheta (default 10/16); ") TEXT("cylinder: radius (default 50), height (default 100), radialSteps (default 12), ") TEXT("heightSteps (default 0), capped (default true); ") TEXT("cone: baseRadius (default 50), topRadius (default 5), height (default 100), ") TEXT("radialSteps (default 12), heightSteps (default 4), capped (default true); ") TEXT("torus: majorRadius (default 50), minorRadius (default 25), majorSteps (default 16), ") TEXT("minorSteps (default 8)"),
			  TEXT("MifBridgeGeometryScript.cpp"), 174, nullptr },
			{ TEXT("describe_dynamic_mesh"), GMifDescKeys_describe_dynamic_mesh, GMifDescNotes_describe_dynamic_mesh,
			  TEXT("path (alias: assetPath) - a StaticMesh asset; lod (default 0)"),
			  TEXT("MifBridgeGeometryScript.cpp"), 410, nullptr },
			{ TEXT("create_mesh_boolean"), GMifDescKeys_create_mesh_boolean, GMifDescNotes_create_mesh_boolean,
			  TEXT("targetPath (alias: path) and toolPath - two existing StaticMesh assets; operation ") TEXT("(union|intersection|subtract); outputPath - where to create the result (must not ") TEXT("already exist); toolOffsetX/Y/Z (optional, default 0 - moves toolPath before the ") TEXT("operation so it actually overlaps targetPath)"),
			  TEXT("MifBridgeGeometryScript.cpp"), 488, nullptr },
			{ TEXT("create_level_snapshot"), GMifDescKeys_create_level_snapshot, nullptr,
			  TEXT("path (alias: assetPath) - where to create the snapshot asset; name (optional, ") TEXT("defaults to the asset name); description (optional)"),
			  TEXT("MifBridgeLevelSnapshots.cpp"), 127, nullptr },
			{ TEXT("describe_level_snapshot"), GMifDescKeys_describe_level_snapshot, nullptr,
			  TEXT("path (alias: assetPath) - a LevelSnapshot asset"),
			  TEXT("MifBridgeLevelSnapshots.cpp"), 194, nullptr },
			{ TEXT("apply_level_snapshot"), GMifDescKeys_apply_level_snapshot, nullptr,
			  TEXT("path (alias: assetPath) - a LevelSnapshot asset to restore into the CURRENT editor world"),
			  TEXT("MifBridgeLevelSnapshots.cpp"), 226, nullptr },
			{ TEXT("push_livelink_transform"), GMifDescKeys_push_livelink_transform, nullptr,
			  TEXT("subjectName - the LiveLink subject to create or update; locationX/Y/Z, ") TEXT("rotationPitch/Yaw/Roll, scaleX/Y/Z (all optional, default identity - location 0, ") TEXT("rotation 0, scale 1)"),
			  TEXT("MifBridgeLiveLink.cpp"), 128, nullptr },
			{ TEXT("describe_livelink_subject"), GMifDescKeys_describe_livelink_subject, nullptr,
			  TEXT("subjectName - the LiveLink subject to read"),
			  TEXT("MifBridgeLiveLink.cpp"), 207, nullptr },
			{ TEXT("add_game_framework_receiver"), GMifDescKeys_add_game_framework_receiver, nullptr,
			  TEXT("actorPath (alias: actor) - the actor to register as a component-request receiver"),
			  TEXT("MifBridgeGameFramework.cpp"), 116, nullptr },
			{ TEXT("add_game_framework_component_request"), GMifDescKeys_add_game_framework_component_request, nullptr,
			  TEXT("receiverClass - an Actor subclass; componentClass - an ActorComponent subclass; ") TEXT("requestId (optional, auto-generated if omitted) - use it with ") TEXT("remove_game_framework_component_request later"),
			  TEXT("MifBridgeGameFramework.cpp"), 158, nullptr },
			{ TEXT("remove_game_framework_component_request"), GMifDescKeys_remove_game_framework_component_request, nullptr,
			  TEXT("requestId - the id returned by add_game_framework_component_request"),
			  TEXT("MifBridgeGameFramework.cpp"), 236, nullptr },
			{ TEXT("list_game_framework_component_requests"), GMifDescKeys_list_game_framework_component_requests, GMifDescNotes_list_game_framework_component_requests,
			  TEXT("no parameters - it reports every live component request this editor session made"),
			  TEXT("MifBridgeGameFramework.cpp"), 278, nullptr },
			{ TEXT("add_mvvm_viewmodel"), GMifDescKeys_add_mvvm_viewmodel, nullptr,
			  TEXT("widgetBlueprintPath (aliases: path, blueprintId) - a Widget Blueprint; viewModelClass ") TEXT("- the class to add as a viewmodel"),
			  TEXT("MifBridgeMVVM.cpp"), 168, nullptr },
			{ TEXT("add_mvvm_binding"), GMifDescKeys_add_mvvm_binding, nullptr,
			  TEXT("widgetBlueprintPath (aliases: path, blueprintId); sourceViewModelName + ") TEXT("sourcePropertyName - a property already added via add_mvvm_viewmodel; ") TEXT("destinationWidgetName + destinationPropertyName - a named widget in the tree and a ") TEXT("property on it; bindingMode (optional: oneWayToDestination default, ") TEXT("oneTimeToDestination, twoWay, oneWayToSource)"),
			  TEXT("MifBridgeMVVM.cpp"), 247, nullptr },
			{ TEXT("describe_mvvm_view"), GMifDescKeys_describe_mvvm_view, nullptr,
			  TEXT("widgetBlueprintPath (aliases: path, blueprintId) - a Widget Blueprint"),
			  TEXT("MifBridgeMVVM.cpp"), 416, nullptr },
			{ TEXT("remove_mvvm_viewmodel"), GMifDescKeys_remove_mvvm_viewmodel, nullptr,
			  TEXT("widgetBlueprintPath (aliases: path, blueprintId); viewModelName - from ") TEXT("add_mvvm_viewmodel or describe_mvvm_view"),
			  TEXT("MifBridgeMVVM.cpp"), 492, nullptr },
			{ TEXT("remove_mvvm_binding"), GMifDescKeys_remove_mvvm_binding, nullptr,
			  TEXT("widgetBlueprintPath (aliases: path, blueprintId); bindingId - from add_mvvm_binding ") TEXT("or describe_mvvm_view"),
			  TEXT("MifBridgeMVVM.cpp"), 572, nullptr },
			{ TEXT("set_data_layer_visibility"), GMifDescKeys_set_data_layer_visibility, GMifDescNotes_set_data_layer_visibility,
			  TEXT("name (aliases: dataLayer, layer) - a Data Layer short name; visible (bool, required)"),
			  TEXT("MifBridgeStreaming.cpp"), 1700, nullptr },
			{ TEXT("set_data_layer_loaded_in_editor"), GMifDescKeys_set_data_layer_loaded_in_editor, GMifDescNotes_set_data_layer_loaded_in_editor,
			  TEXT("name (aliases: dataLayer, layer); loaded (bool, required); fromUserChange (default " "true - mirrors what the Outliner does, and the engine records the distinction)"),
			  TEXT("MifBridgeStreaming.cpp"), 1762, nullptr },
			{ TEXT("create_data_layer"), GMifDescKeys_create_data_layer, GMifDescNotes_create_data_layer,
			  TEXT("name (the layer's short name); assetPath (defaults to /Game/_MifDataLayers/<name>); " "type (alias: dataLayerType) - runtime (default) or editor; isPrivate (default false)"),
			  TEXT("MifBridgeStreaming.cpp"), 2042, nullptr },
			{ TEXT("add_actor_to_data_layer"), GMifDescKeys_add_actor_to_data_layer, GMifDescNotes_add_actor_to_data_layer,
			  TEXT("actorPath (alias: actor); name (aliases: dataLayer, layer) - a Data Layer short name"),
			  TEXT("MifBridgeStreaming.cpp"), 1882, nullptr },
			{ TEXT("remove_actor_from_data_layer"), GMifDescKeys_remove_actor_from_data_layer, GMifDescNotes_remove_actor_from_data_layer,
			  TEXT("actorPath (alias: actor); name (aliases: dataLayer, layer) - a Data Layer short name"),
			  TEXT("MifBridgeStreaming.cpp"), 1950, nullptr },
			{ TEXT("blueprint_inheritance_tree"), GMifDescKeys_blueprint_inheritance_tree, GMifDescNotes_blueprint_inheritance_tree,
			  TEXT("pathPrefix (alias: prefix, default /Game/); root (a class or blueprint name to " "subtree from); maxDepth (0 = unlimited)"),
			  TEXT("MifBridgeProject.cpp"), 399, nullptr },
			{ TEXT("project_dependency_graph"), GMifDescKeys_project_dependency_graph, GMifDescNotes_project_dependency_graph,
			  TEXT("pathPrefix (alias: path) - at least two segments, e.g. /Game/Blueprints; " "maxNodes (default 300); includeExternal (default false - keep edges that leave the " "prefix); mermaid (default false - also return a `mermaid` flowchart-TD text field, " "capped at the same maxNodes)"),
			  TEXT("MifBridgeProject.cpp"), 142, nullptr },
			{ TEXT("set_plugin_enabled"), GMifDescKeys_set_plugin_enabled, GMifDescNotes_set_plugin_enabled,
			  TEXT("name (aliases: plugin, pluginName) - a discovered plugin name; enabled (REQUIRED, " "no default); dryRun (default false - report what would change and write nothing, " "allowed in every write mode); save (default true - persist to the .uproject)"),
			  TEXT("MifBridgeProject.cpp"), 696, nullptr },
			{ TEXT("project_asset_distribution"), GMifDescKeys_project_asset_distribution, GMifDescNotes_project_asset_distribution,
			  TEXT("pathPrefix (alias: path, default /Game); topFolders (default 25); topClasses " "(default 25)"),
			  TEXT("MifBridgeProject.cpp"), 311, nullptr },
			{ TEXT("perf_heavy_actors"), GMifDescKeys_perf_heavy_actors, GMifDescNotes_perf_heavy_actors,
			  TEXT("limit (default 40); sortBy one of triangles|components|materials|drawEst " "(default triangles)"),
			  TEXT("MifBridgePerfView.cpp"), 86, nullptr },
			{ TEXT("trace_start"), GMifDescKeys_trace_start, GMifDescNotes_trace_start,
			  TEXT("channels (default \"cpu,frame,bookmark,stats\" - the set that answers 'what is " "burning frame time')"),
			  TEXT("MifBridgeTrace.cpp"), 50, nullptr },
			{ TEXT("trace_stop"), GMifDescKeys_trace_stop, GMifDescNotes_trace_stop,
			  TEXT("(no parameters)"),
			  TEXT("MifBridgeTrace.cpp"), 104, nullptr },
			{ TEXT("add_sublevel"), GMifDescKeys_add_sublevel, nullptr,
			  TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\"), location {x,y,z}, rotation {x,y,z}"),
			  TEXT("MifBridgeStreaming.cpp"), 605, nullptr },
			{ TEXT("remove_sublevel"), GMifDescKeys_remove_sublevel, nullptr,
			  TEXT("path (packagePath, level), discardUnsaved (bool)"),
			  TEXT("MifBridgeStreaming.cpp"), 747, nullptr },
			{ TEXT("set_sublevel_visibility"), GMifDescKeys_set_sublevel_visibility, nullptr,
			  TEXT("path (packagePath, level), visible (editorVisible), shouldBeLoaded, shouldBeVisible, lightingScenario"),
			  TEXT("MifBridgeStreaming.cpp"), 894, nullptr },
			{ TEXT("set_current_sublevel"), GMifDescKeys_set_current_sublevel, nullptr,
			  TEXT("path (packagePath, level) — a package path, or the literal \"persistent\""),
			  TEXT("MifBridgeStreaming.cpp"), 1079, nullptr },
			{ TEXT("set_sublevel_streaming"), GMifDescKeys_set_sublevel_streaming, nullptr,
			  TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\")"),
			  TEXT("MifBridgeStreaming.cpp"), 1178, nullptr },
			{ TEXT("pie_load_level_instance"), GMifDescKeys_pie_load_level_instance, nullptr,
			  TEXT("path (packagePath, level), location {x,y,z}, rotation {x,y,z}, visible (bool), " "netMode (\"server\"|\"client\"|\"any\"), nameOverride (string), tempPackage (bool)"),
			  TEXT("MifBridgeStreaming.cpp"), 1298, nullptr },
			{ TEXT("pie_unload_level_instance"), GMifDescKeys_pie_unload_level_instance, nullptr,
			  TEXT("instanceName (name) from pie_load_level_instance, or objectPath, or path (packagePath, level) " "naming the SOURCE map; netMode (\"server\"|\"client\"|\"any\")"),
			  TEXT("MifBridgeStreaming.cpp"), 1434, nullptr },
			{ TEXT("list_editor_commands"), GMifDescKeys_list_editor_commands, GMifDescNotes_list_editor_commands,
			  TEXT("context, command, filter, includeUnbound (default true), includeCanExecute (default false), ") TEXT("includeConsole (default false), consolePrefix, menu, section, limit (default 400)"),
			  TEXT("MifBridgeUI.cpp"), 569, nullptr },
			{ TEXT("invoke_editor_command"), GMifDescKeys_invoke_editor_command, GMifDescNotes_invoke_editor_command,
			  TEXT("context, command, menu, section, entry, dryRun, confirm, allowKnownModal"),
			  TEXT("MifBridgeUI.cpp"), 873, nullptr },
			{ TEXT("invoke_editor_tab"), GMifDescKeys_invoke_editor_tab, GMifDescNotes_invoke_editor_tab,
			  TEXT("tabId (alias: tab), manager (global|majorTab|assetEditor; default global), majorTab, ") TEXT("asset, probe, probeIds[], includeKnownIds (default true), asInactive"),
			  TEXT("MifBridgeUI.cpp"), 1167, nullptr },
			{ TEXT("send_editor_key"), GMifDescKeys_send_editor_key, GMifDescNotes_send_editor_key,
			  TEXT("key, confirm, dryRun, modifiers{ctrl,alt,shift,cmd}, userIndex (default 0), ") TEXT("isRepeat, characterCode, keyCode, sendKeyUp (default true)"),
			  TEXT("MifBridgeUI.cpp"), 1349, nullptr },
			{ TEXT("open_asset_editor"), GMifDescKeys_open_asset_editor, GMifDescNotes_open_asset_editor,
			  TEXT("path - the asset whose default editor to open (warms its FUICommandList so invoke_editor_command can reach that editor's commands)"),
			  TEXT("MifBridgeUI.cpp"), 1541, nullptr },
			{ TEXT("import_texture"), GMifDescKeys_import_texture, GMifDescNotes_import_texture,
			  TEXT("destPath (aliases: path, assetPath), sourcePath (aliases: file, filename) OR base64 ") TEXT("(aliases: data, bytes), format, overwrite (alias: replaceExisting), save, ") TEXT("compressionSettings (alias: compression), srgb, lodGroup (alias: textureGroup), ") TEXT("neverStream, mipGenSettings (alias: mipGen), filter"),
			  TEXT("MifBridgeImport.cpp"), 846, nullptr },
			{ TEXT("import_asset"), GMifDescKeys_import_asset, GMifDescNotes_import_asset,
			  TEXT("file (aliases: filename, sourcePath), destination (aliases: destinationPath, path), ") TEXT("name (alias: destinationName), factory, replaceExisting (alias: overwrite), ") TEXT("replaceExistingSettings, save"),
			  TEXT("MifBridgeImport.cpp"), 1176, nullptr },
			{ TEXT("reimport_asset"), GMifDescKeys_reimport_asset, GMifDescNotes_reimport_asset,
			  TEXT("path (aliases: assetPath, objectPath), sourceFile (aliases: file, newFile), ") TEXT("sourceFileIndex, forceNewFile, save"),
			  TEXT("MifBridgeImport.cpp"), 1434, nullptr },
			{ TEXT("set_texture_settings"), GMifDescKeys_set_texture_settings, GMifDescNotes_set_texture_settings,
			  TEXT("path (aliases: assetPath, objectPath, texturePath), compressionSettings (alias: compression), ") TEXT("srgb, lodGroup (alias: textureGroup), neverStream, mipGenSettings (alias: mipGen), filter, save"),
			  TEXT("MifBridgeImport.cpp"), 1648, nullptr },
			{ TEXT("export_asset"), GMifDescKeys_export_asset, GMifDescNotes_export_asset,
			  TEXT("asset (aliases: path, assetPath, objectPath), file (aliases: filename, outPath), ") TEXT("format (aliases: type, extension), overwrite (alias: replaceExisting), ") TEXT("fbxCompatibility, ascii, vertexColor, levelOfDetail (alias: lod), collision, ") TEXT("exportSourceMesh, forceFrontXAxis"),
			  TEXT("MifBridgeExport.cpp"), 437, nullptr },
			{ TEXT("render_thumbnail"), GMifDescKeys_render_thumbnail, nullptr,
			  TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, ") TEXT("flushTextures, alpha, name"),
			  TEXT("MifBridgeThumbnail.cpp"), 792, nullptr },
			{ TEXT("write_thumbnail_texture"), GMifDescKeys_write_thumbnail_texture, GMifDescNotes_write_thumbnail_texture,
			  TEXT("asset (aliases: assetPath, path), texturePath (alias: outputPath), width, height, ") TEXT("orbitPitch, orbitYaw, orbitZoom, flushTextures, alpha, srgb, compression, lodGroup, ") TEXT("generateMips, overwrite, save"),
			  TEXT("MifBridgeThumbnail.cpp"), 879, nullptr },
			{ TEXT("set_asset_thumbnail"), GMifDescKeys_set_asset_thumbnail, GMifDescNotes_set_asset_thumbnail,
			  TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, ") TEXT("flushTextures, save"),
			  TEXT("MifBridgeThumbnail.cpp"), 1171, nullptr },
			{ TEXT("thumbnail_capabilities"), GMifDescKeys_thumbnail_capabilities, nullptr,
			  TEXT("asset (aliases: assetPath, path) — optional; omit for editor-wide capability only"),
			  TEXT("MifBridgeThumbnail.cpp"), 695, nullptr },
			{ TEXT("exec_console"), GMifDescKeys_exec_console, GMifDescNotes_exec_console,
			  TEXT("command - the console command to run in the editor, e.g. \"mif.kr.Events 1\" or \"stat unit\""),
			  TEXT("MifBridgeConsole.cpp"), 59, nullptr },
			{ TEXT("get_cvar"), GMifDescKeys_get_cvar, GMifDescNotes_get_cvar,
			  TEXT("name - the console variable to read, e.g. \"mif.kr.Events\""),
			  TEXT("MifBridgeConsole.cpp"), 107, nullptr },
			{ TEXT("set_cvar"), GMifDescKeys_set_cvar, GMifDescNotes_set_cvar,
			  TEXT("name, value - sets a console variable, e.g. {name:\"mif.kr.Events\", value:\"1\"}"),
			  TEXT("MifBridgeConsole.cpp"), 150, nullptr },
			{ TEXT("add_node_pin"), GMifDescKeys_add_node_pin, GMifDescNotes_add_node_pin,
			  TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), count (how many pins to add, 1-32, default 1)"),
			  TEXT("MifBridgeNodePins.cpp"), 51, nullptr },
			{ TEXT("create_metahuman_character"), GMifDescKeys_create_metahuman_character, GMifDescNotes_create_metahuman_character,
			  TEXT("path (/Game/... - must not already exist)"),
			  TEXT("MifBridgeMetaHuman.cpp"), 139, nullptr },
			{ TEXT("spawn_metahuman_actor"), GMifDescKeys_spawn_metahuman_actor, nullptr,
			  TEXT("characterPath (aliases: path, character) - a UMetaHumanCharacter asset"),
			  TEXT("MifBridgeMetaHuman.cpp"), 219, nullptr },
			{ TEXT("add_gameplay_effect_modifier"), GMifDescKeys_add_gameplay_effect_modifier, GMifDescNotes_add_gameplay_effect_modifier,
			  TEXT("objectPath (a GameplayEffect Blueprint's CDO, e.g. .../GE_Foo.Default__GE_Foo_C), ") TEXT("attributeSetClass, attributeName, operation (Add|Multiply|Divide|Override), magnitude ") TEXT("(flat float - curve-table magnitudes are not covered by this endpoint)"),
			  TEXT("MifBridgeGAS.cpp"), 307, nullptr },
			{ TEXT("describe_ability_system"), GMifDescKeys_describe_ability_system, GMifDescNotes_describe_ability_system,
			  TEXT("actorPath (aliases: actor, path, objectPath) - a live actor carrying an " "AbilitySystemComponent, or the component itself"),
			  TEXT("MifBridgeGAS.cpp"), 139, nullptr },
			{ TEXT("list_live_widgets"), GMifDescKeys_list_live_widgets, nullptr,
			  TEXT("netMode? (server|client|any, default server - only meaningful with >1 PIE world), ") TEXT("topLevelOnly? (default true - widgets added directly to a viewport/player screen, ") TEXT("not every nested child), classFilter? (substring match on class name)"),
			  TEXT("MifBridgeLiveWidgets.cpp"), 212, nullptr },
			{ TEXT("describe_live_widget"), GMifDescKeys_describe_live_widget, nullptr,
			  TEXT("path (a live widget instance's path, from list_live_widgets), maxDepth? (default 12)"),
			  TEXT("MifBridgeLiveWidgets.cpp"), 257, nullptr },
			{ TEXT("preview_widget"), GMifDescKeys_preview_widget, GMifDescNotes_preview_widget,
			  TEXT("widgetClass (a UserWidget-derived class, e.g. /Game/UI/WBP_Foo.WBP_Foo_C), ") TEXT("width/height? (64-4096, default 512), dpiScale? (default 1.0 - see ") TEXT("dpiScaleAtThisSize in the response for the project's own curve at this size, not ") TEXT("applied automatically), background? (transparent|black|white, default transparent), name?"),
			  TEXT("MifBridgeWidgetPreview.cpp"), 68, nullptr },
			{ TEXT("preview_composite_widget"), GMifDescKeys_preview_composite_widget, GMifDescNotes_preview_composite_widget,
			  TEXT("rootClass (a UserWidget class), children[] (each: class, insertInto - a named ") TEXT("panel/slot variable on the ROOT, name? - a label for this response only), width/height? ") TEXT("(64-4096, default 512), dpiScale? (default 1.0), background? (transparent|black|white), name?"),
			  TEXT("MifBridgeCompositePreview.cpp"), 149, nullptr },
			{ TEXT("ui_scenario_start"), GMifDescKeys_ui_scenario_start, GMifDescNotes_ui_scenario_start,
			  TEXT("targetActorPath (a live PIE actor's path, from list_pie_actors), netMode? ") TEXT("(server|client|any, default server), playerLocation {x,y,z} (required - explicit, ") TEXT("no automatic interaction-radius calculation), playerRotation? {pitch,yaw,roll}, ") TEXT("playerIndex? (default 0), confirm (required true - this moves the player pawn)"),
			  TEXT("MifBridgeUIScenario.cpp"), 313, nullptr },
			{ TEXT("ui_scenario_activate"), GMifDescKeys_ui_scenario_activate, nullptr,
			  TEXT("activationKey? (default F), expectedWidgetClasses? [class paths to wait for], ") TEXT("timeoutSeconds? (default 10), stableFrames? (default 3), confirm (required true - ") TEXT("this delivers real input and runs gameplay code synchronously)"),
			  TEXT("MifBridgeUIScenario.cpp"), 430, nullptr },
			{ TEXT("ui_scenario_status"), GMifDescKeys_ui_scenario_status, nullptr,
			  TEXT("no parameters"),
			  TEXT("MifBridgeUIScenario.cpp"), 539, nullptr },
			{ TEXT("ui_scenario_capture"), GMifDescKeys_ui_scenario_capture, nullptr,
			  TEXT("name? (output filename)"),
			  TEXT("MifBridgeUIScenario.cpp"), 554, nullptr },
			{ TEXT("ui_scenario_stop"), GMifDescKeys_ui_scenario_stop, nullptr,
			  TEXT("no parameters"),
			  TEXT("MifBridgeUIScenario.cpp"), 648, nullptr },
		};
// <<< MIF_HARVEST_END
		const FMifDescribeRow* MifDescribeFindRow(const FString& Endpoint)
		{
			// One case-insensitive compare per row. Linear beats a sorted-array search's maintenance risk here:
			// a binary search silently returns nothing if a regeneration ever emits out of order.
			for (const FMifDescribeRow& Row : GMifDescribeRows)
			{
				if (Endpoint.Equals(Row.Endpoint, ESearchCase::IgnoreCase))
				{
					return &Row;
				}
			}
			return nullptr;
		}

		bool MifDescribeIsIdentChar(TCHAR C)
		{
			return FChar::IsAlnum(C) || C == TEXT('_');
		}

		/** Leading identifier of Text: "simpleText:true" -> "simpleText", "ownerClass; default" ->
		 *  "ownerClass". The summary prose annotates aliases with type/default hints after a ':' or
		 *  ';', and those annotations are documentation, not part of the key. */
		FString MifDescribeLeadingIdent(const FString& Text)
		{
			const FString Trimmed = Text.TrimStartAndEnd();
			int32 End = 0;
			while (End < Trimmed.Len() && MifDescribeIsIdentChar(Trimmed[End]))
			{
				++End;
			}
			return Trimmed.Left(End);
		}

		struct FMifDescribeAliasGroup
		{
			FString         Canonical;
			TArray<FString> Aliases;
		};

		/** Recover "canonical (aliases: a, b, c)" groups from a guard's AcceptedSummary.
		 *  Parsed rather than tabulated on purpose: the summary is the string the guard actually
		 *  prints, so grouping derived from it cannot disagree with the error a caller sees. Every
		 *  identifier this yields is cross-checked against the harvested key list by the caller. */
		void MifDescribeParseAliasGroups(const FString& Summary, TArray<FMifDescribeAliasGroup>& Out)
		{
			int32 Cursor = 0;
			while (Cursor < Summary.Len())
			{
				const int32 Open = Summary.Find(TEXT("(alias"), ESearchCase::IgnoreCase,
					ESearchDir::FromStart, Cursor);
				if (Open == INDEX_NONE)
				{
					return;
				}
				const int32 Close = Summary.Find(TEXT(")"), ESearchCase::CaseSensitive,
					ESearchDir::FromStart, Open);
				const int32 Colon = Summary.Find(TEXT(":"), ESearchCase::CaseSensitive,
					ESearchDir::FromStart, Open);
				if (Close == INDEX_NONE || Colon == INDEX_NONE || Colon > Close)
				{
					// Malformed or an unrelated "(alias" mention - skip past it rather than guessing.
					Cursor = Open + 6;
					continue;
				}

				FMifDescribeAliasGroup Group;
				// The canonical key is the identifier immediately preceding the '(' .
				int32 End = Open - 1;
				while (End >= 0 && FChar::IsWhitespace(Summary[End]))
				{
					--End;
				}
				int32 Start = End;
				while (Start >= 0 && MifDescribeIsIdentChar(Summary[Start]))
				{
					--Start;
				}
				if (End > Start)
				{
					Group.Canonical = Summary.Mid(Start + 1, End - Start);
				}

				TArray<FString> Parts;
				Summary.Mid(Colon + 1, Close - Colon - 1).ParseIntoArray(Parts, TEXT(","), true);
				for (const FString& Part : Parts)
				{
					const FString Id = MifDescribeLeadingIdent(Part);
					if (!Id.IsEmpty())
					{
						Group.Aliases.Add(Id);
					}
				}
				if (!Group.Canonical.IsEmpty() && Group.Aliases.Num() > 0)
				{
					Out.Add(MoveTemp(Group));
				}
				Cursor = Close + 1;
			}
		}

		/** Levenshtein, case-insensitive, two rows. Only ever run over the ~211 registered names on
		 *  the not-found path, so clarity beats cleverness. */
		int32 MifDescribeEditDistance(const FString& A, const FString& B)
		{
			const int32 LenA = A.Len();
			const int32 LenB = B.Len();
			if (LenA == 0) { return LenB; }
			if (LenB == 0) { return LenA; }

			TArray<int32> Prev, Cur;
			Prev.SetNumUninitialized(LenB + 1);
			Cur.SetNumUninitialized(LenB + 1);
			for (int32 j = 0; j <= LenB; ++j)
			{
				Prev[j] = j;
			}
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
		}

		/** Bucket/provider for one endpoint, taken from self_audit's OWN output.
		 *
		 *  IsReadOnlyEndpoint and IsSelfManagedEndpoint are file-static in MifBridgeCommon.cpp and
		 *  deliberately unexported, so the only honest way to report a transaction bucket is to ask
		 *  the endpoint that already owns the policy. Re-deriving it here from a copied TSet is the
		 *  duplicate-source bug this codebase has paid for repeatedly (batch's hardcoded
		 *  compile-heavy list, which drifted). H_self_audit is read-only and side-effect free, so
		 *  calling it inline is safe even inside RunEndpoint's transaction.
		 *
		 *  Returns false when the endpoint has no row, which for a registered name would mean
		 *  self_audit and the live registry disagree - reported, never silently defaulted. */
		bool MifDescribeLookupAuditRow(const FString& Endpoint, FString& OutProvider,
			FString& OutBucket, FString& OutExternalSummary)
		{
			TSharedRef<FJsonObject> AuditIn  = MakeShared<FJsonObject>();
			TSharedRef<FJsonObject> AuditOut = MakeShared<FJsonObject>();
			H_self_audit(AuditIn, AuditOut);

			const TArray<TSharedPtr<FJsonValue>>* Rows = nullptr;
			if (!AuditOut->TryGetArrayField(TEXT("endpointDetails"), Rows) || Rows == nullptr)
			{
				return false;
			}
			for (const TSharedPtr<FJsonValue>& Value : *Rows)
			{
				const TSharedPtr<FJsonObject>* Row = nullptr;
				if (!Value.IsValid() || !Value->TryGetObject(Row) || Row == nullptr || !Row->IsValid())
				{
					continue;
				}
				FString RowName;
				if (!(*Row)->TryGetStringField(TEXT("name"), RowName)
					|| !RowName.Equals(Endpoint, ESearchCase::IgnoreCase))
				{
					continue;
				}
				(*Row)->TryGetStringField(TEXT("provider"), OutProvider);
				(*Row)->TryGetStringField(TEXT("bucket"), OutBucket);
				(*Row)->TryGetStringField(TEXT("summary"), OutExternalSummary);
				return true;
			}
			return false;
		}
	}   // anonymous namespace

	void H_describe_endpoint(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out)
	{
		if (RejectUnknownParams(In, Out, { MIF_DESCRIBE_OWN_KEYS }, MIF_DESCRIBE_OWN_SUMMARY,
			{ { TEXT("tool"), TEXT("spell it name") },
			  { TEXT("endpoint_name"), TEXT("spell it name (this bridge uses camelCase parameters)") },
			  { TEXT("names"), TEXT("one endpoint per call; self_audit lists them all") } }))
		{
			return;
		}

		const FString Requested = JStrAny(In, { MIF_DESCRIBE_OWN_KEYS }).TrimStartAndEnd();

		// --- live registry, and the table's integrity against it -------------------------------
		// Done before anything else so even the not-found path reports coverage: a caller whose name
		// is wrong still learns how much of the surface is describable at all.
		const TArray<FString> Registered = GetEndpointNames();
		TSet<FString> RegisteredSet;
		RegisteredSet.Reserve(Registered.Num());
		for (const FString& Name : Registered)
		{
			RegisteredSet.Add(Name);
		}

		TArray<TSharedPtr<FJsonValue>> StaleRows;
		// Counts ROWS THAT MATCH A LIVE ENDPOINT. Deliberately not called "declared": whether an
		// endpoint declares an accepted set is a property of its handler body, and this loop only
		// ever sees the table.
		int32 RowsForRegisteredEndpoints = 0;
		for (const FMifDescribeRow& Row : GMifDescribeRows)
		{
			if (RegisteredSet.Contains(FString(Row.Endpoint)))
			{
				++RowsForRegisteredEndpoints;
			}
			else
			{
				// A row for an endpoint that no longer exists: renamed or removed since the harvest.
				// This is the drift case that IS catchable at runtime, so it is never left silent.
				StaleRows.Add(MakeShared<FJsonValueString>(FString(Row.Endpoint)));
			}
		}

		TSharedRef<FJsonObject> Coverage = MakeShared<FJsonObject>();
		Coverage->SetNumberField(TEXT("registeredEndpoints"), Registered.Num());
		Coverage->SetNumberField(TEXT("tableRows"), static_cast<int32>(UE_ARRAY_COUNT(GMifDescribeRows)));
		// Named for what they COUNT - table rows - not for what the table was once assumed to prove.
		// The old names (endpointsWith/WithoutDeclaredParams) asserted a property of the HANDLERS that
		// a static table cannot establish in either direction.
		Coverage->SetNumberField(TEXT("endpointsWithTableRow"), RowsForRegisteredEndpoints);
		Coverage->SetNumberField(TEXT("endpointsWithoutTableRow"),
			Registered.Num() - RowsForRegisteredEndpoints);
		Coverage->SetArrayField(TEXT("staleTableRows"), StaleRows);
		// The narrow claim that IS provable here, under a name that claims only that. `tableHealthy`
		// used to sit on this line and it over-claimed: it flipped false ONLY when a row's endpoint had
		// vanished, so it read green while ten guarded endpoints had no row at all and were being
		// described as unguarded. A MISSING row is not detectable from inside the DLL - an accepted-key
		// list is an inline initialiser-list literal that leaves no trace until its handler runs - so
		// completeness is reported as unverifiable instead of being implied by a healthy-looking bool.
		Coverage->SetBoolField(TEXT("noStaleTableRows"), StaleRows.Num() == 0);
		Coverage->SetBoolField(TEXT("completenessVerifiable"), false);
		Coverage->SetStringField(TEXT("completenessNote"),
			TEXT("These counts describe the TABLE, not the handlers. staleTableRows detects ONE drift ")
			TEXT("direction only - a row whose endpoint is no longer registered. The opposite drift, a ")
			TEXT("guard that exists in the source but has no row here, leaves no runtime trace and is ")
			TEXT("NOT detectable from inside the DLL, so endpointsWithoutTableRow is an UPPER BOUND on ")
			TEXT("the endpoints that accept anything silently - never a count of them."));
		Out->SetObjectField(TEXT("coverage"), Coverage);

		if (Requested.IsEmpty())
		{
			Fail(Out, TEXT("describe_endpoint requires 'name' (aliases: endpoint, endpointName) - the ")
				TEXT("endpoint to describe, e.g. {\"name\":\"add_branch\"}. Call self_audit for the full ")
				TEXT("list of registered endpoint names."));
			return;
		}

		// --- STATE 3: no such endpoint ---------------------------------------------------------
		// ONE lookup, and it is already case-insensitive: FString hashes and compares without regard
		// to case in UE, which is the same property RejectUnknownParams relies on to match parameter
		// keys and Handlers() relies on to dispatch. Find returns the STORED element, so *Canonical is
		// the REGISTERED spelling even when the caller's case differed - which is what makes the
		// requestedAs/nameNote pair below possible without a second pass. (An explicit case-insensitive
		// fallback loop lived here and was dead code for exactly this reason.)
		const FString* Canonical = RegisteredSet.Find(Requested);
		const FString Resolved = Canonical ? *Canonical : FString();

		if (Resolved.IsEmpty())
		{
			struct FMifDescribeNearMiss
			{
				FString Name;
				int32   Distance;
			};
			TArray<FMifDescribeNearMiss> NearMisses;
			for (const FString& Name : Registered)
			{
				const int32 Distance = MifDescribeEditDistance(Requested, Name);
				// Either close by edit distance, or one name contains the other (a caller who
				// remembered "material_expression" but not "add_material_expression").
				const bool bSubstring = Name.Contains(Requested, ESearchCase::IgnoreCase)
					|| Requested.Contains(Name, ESearchCase::IgnoreCase);
				if (Distance <= 3 || bSubstring)
				{
					NearMisses.Add({ Name, bSubstring ? FMath::Min(Distance, 2) : Distance });
				}
			}
			NearMisses.Sort([](const FMifDescribeNearMiss& A, const FMifDescribeNearMiss& B)
			{
				return A.Distance != B.Distance ? A.Distance < B.Distance : A.Name < B.Name;
			});

			TArray<TSharedPtr<FJsonValue>> SuggestionValues;
			TArray<FString> SuggestionNames;
			for (const FMifDescribeNearMiss& Miss : NearMisses)
			{
				if (SuggestionValues.Num() >= 8)
				{
					break;
				}
				SuggestionValues.Add(MakeShared<FJsonValueString>(Miss.Name));
				SuggestionNames.Add(Miss.Name);
			}
			Out->SetStringField(TEXT("status"), TEXT("no_such_endpoint"));
			Out->SetStringField(TEXT("name"), Requested);
			Out->SetBoolField(TEXT("registered"), false);
			Out->SetArrayField(TEXT("suggestions"), SuggestionValues);
			Fail(Out, FString::Printf(
				TEXT("no such endpoint '%s' - it is not in the live registry of %d endpoints.%s ")
				TEXT("Call self_audit for the full list."),
				*Requested, Registered.Num(),
				SuggestionNames.Num() > 0
					? *FString::Printf(TEXT(" Did you mean: %s?"), *FString::Join(SuggestionNames, TEXT(", ")))
					: TEXT("")));
			return;
		}

		Out->SetStringField(TEXT("name"), Resolved);
		Out->SetBoolField(TEXT("registered"), true);
		if (!Resolved.Equals(Requested, ESearchCase::CaseSensitive))
		{
			// Deliberately NOT claiming the caller's spelling would have failed - that depends on
			// FHttpRouter's path matching, which is engine internals this file has not verified.
			// What IS verified: FMifBridgeServer::Start binds the route as "/api/<Name>" using the
			// registered string, so that spelling is the one known to work.
			Out->SetStringField(TEXT("requestedAs"), Requested);
			Out->SetStringField(TEXT("nameNote"), FString::Printf(
				TEXT("matched case-insensitively; the registered spelling is '%s' and its HTTP route ")
				TEXT("is bound as '/api/%s' - prefer that exact spelling"), *Resolved, *Resolved));
		}

		// --- the self_audit row, superset-ed ---------------------------------------------------
		FString Provider, Bucket, ExternalSummary;
		const bool bHasAuditRow = MifDescribeLookupAuditRow(Resolved, Provider, Bucket, ExternalSummary);
		if (bHasAuditRow)
		{
			Out->SetStringField(TEXT("provider"), Provider);
			Out->SetStringField(TEXT("bucket"), Bucket);
			Out->SetBoolField(TEXT("readOnly"), Bucket == TEXT("readOnly"));
			Out->SetBoolField(TEXT("selfManaged"), Bucket == TEXT("selfManaged"));
			Out->SetBoolField(TEXT("transacted"), Bucket == TEXT("transacted"));
			if (!ExternalSummary.IsEmpty())
			{
				Out->SetStringField(TEXT("summary"), ExternalSummary);
			}
		}
		else
		{
			// Registered but absent from self_audit's rows: the two views of the surface disagree.
			// Reported, because a describe_endpoint that quietly omitted the bucket would hide it.
			Out->SetStringField(TEXT("auditRowWarning"), FString::Printf(
				TEXT("'%s' is in the live registry but self_audit emitted no endpointDetails row for ")
				TEXT("it - bucket and provider are UNKNOWN for this call, not defaulted. This is a ")
				TEXT("bridge defect; report it."), *Resolved));
		}
		// IsCompileHeavyEndpoint is exported, so this is the real predicate, not a copy of it.
		const bool bCompileHeavy = IsCompileHeavyEndpoint(Resolved);
		Out->SetBoolField(TEXT("compileHeavy"), bCompileHeavy);
		// Mirrors H_batch's ACTUAL op gate (MifBridgeNodes.cpp: `OpName == "batch" ||
		// IsCompileHeavyEndpoint(OpName)`) rather than approximating it with the compile-heavy half
		// alone. Reporting batchable:true for 'batch' would have been a confidently wrong answer about
		// the one op batch is guaranteed to refuse - a nested batch is rejected before its scope is
		// ever constructed.
		const bool bIsBatchItself = Resolved.Equals(TEXT("batch"), ESearchCase::IgnoreCase);
		Out->SetBoolField(TEXT("batchable"), !bCompileHeavy && !bIsBatchItself);
		if (bCompileHeavy || bIsBatchItself)
		{
			Out->SetStringField(TEXT("batchableNote"), bIsBatchItself
				? TEXT("batch refuses to nest inside itself")
				: TEXT("compile-heavy: refused inside batch's single transaction, because reinstancing ")
				  TEXT("captured by an undo step restores a dead CDO and crashes"));
		}

		// --- STATE 1 / STATE 2: parameters -----------------------------------------------------
		const FMifDescribeRow* Row = MifDescribeFindRow(Resolved);
		if (!Row)
		{
			// STATE 2. acceptedParams is OMITTED. See the header block: an empty array here would be
			// a claim ("takes nothing") that this endpoint has no evidence for.
			//
			// WHAT THIS BRANCH MAY AND MAY NOT SAY. The ONLY fact established here is that the table
			// carries no row for this name. It said more than that once - it asserted the endpoint
			// "does not call RejectUnknownParams" and "will SILENTLY IGNORE" unknown keys - and ten
			// endpoints (capture_camera, set_pin_type, the four import ones, the four thumbnail ones)
			// whose guards were written after the harvest were told the EXACT OPPOSITE of the truth:
			// they reject unknown keys outright. A missing row cannot distinguish "no guard" from
			// "guard added since the harvest", and the two behave oppositely, so this branch now names
			// both possibilities and asserts neither.
			// STATE 2a - OBSERVED AT RUNTIME. Before falling back to "unknown", ask whether this
			// endpoint's guard has actually RUN this session. RejectUnknownParams records its accepted
			// key list against the dispatching endpoint, which is direct evidence the handler guards
			// its input - strictly better than the hand-harvested table, because it cannot go stale.
			// This is what closes the reported gap: close_asset_editors and add_node_pin DO guard, and
			// used to report "unknown" purely because nobody had regenerated the table.
			{
				TArray<FString> ObservedKeys;
				if (MifDescribeObservedParams(Resolved, &ObservedKeys))
				{
					TArray<TSharedPtr<FJsonValue>> KeyArr;
					for (const FString& K : ObservedKeys) { KeyArr.Add(MakeShared<FJsonValueString>(K)); }
					Out->SetStringField(TEXT("status"), TEXT("params_observed"));
					Out->SetStringField(TEXT("paramsSource"), TEXT("runtime"));
					Out->SetArrayField(TEXT("acceptedParams"), KeyArr);
					Out->SetNumberField(TEXT("acceptedParamCount"), KeyArr.Num());
					Out->SetStringField(TEXT("note"), FString::Printf(
						TEXT("'%s' has no row in the harvested table, but its RejectUnknownParams guard ran this ")
						TEXT("session and these are the keys it accepted - so the endpoint DOES reject unknown ")
						TEXT("parameters. Keys are reported lowercased and sorted (the guard matches case-insensitively). ")
						TEXT("This is observed evidence, not a table lookup, and cannot go stale."),
						*Resolved));
					return;
				}
			}

			Out->SetStringField(TEXT("status"), TEXT("params_not_declared"));
			Out->SetStringField(TEXT("paramsSource"), TEXT("none"));
			const bool bExternal = bHasAuditRow && !Provider.IsEmpty()
				&& !Provider.Equals(TEXT("MifBridge"), ESearchCase::IgnoreCase);
			Out->SetStringField(TEXT("note"), bExternal
				? FString::Printf(
					TEXT("'%s' is an EXTERNAL endpoint registered by provider '%s'. Its parameter guard, ")
					TEXT("if it has one, lives in that plugin's own source; MifBridge cannot enumerate ")
					TEXT("it. acceptedParams is omitted rather than empty - this is 'unknown', NOT ")
					TEXT("'takes no parameters'. Consult the provider, or its @mcp.tool signature in ")
					TEXT("server.py."), *Resolved, *Provider)
				: FString::Printf(
					TEXT("'%s' has NO ROW in describe_endpoint's harvested table, so its accepted ")
					TEXT("parameter set is UNKNOWN HERE. acceptedParams is omitted rather than empty - ")
					TEXT("this is 'unknown', NOT 'takes no parameters'. WHAT THIS DOES NOT TELL YOU: ")
					TEXT("whether the handler guards its input. A missing row has TWO possible causes ")
					TEXT("and this endpoint cannot tell them apart - (a) the handler calls no ")
					TEXT("RejectUnknownParams, in which case it SILENTLY IGNORES any key it does not ")
					TEXT("read, including a typo'd one; or (b) it does guard, and its guard was written ")
					TEXT("after this table was harvested, in which case an unknown key is REJECTED and ")
					TEXT("the whole call fails. Those outcomes are opposite, so do not assume either: ")
					TEXT("read the handler body, or use the @mcp.tool signature in server.py as the ")
					TEXT("nearest available contract. %d of %d registered endpoints have no table row."),
					*Resolved, Registered.Num() - RowsForRegisteredEndpoints, Registered.Num()));
			return;
		}

		// STATE 1.
		Out->SetStringField(TEXT("status"), TEXT("params_declared"));
		Out->SetStringField(TEXT("paramsSource"), TEXT("table"));

		TArray<FString> Keys;
		for (const TCHAR* const* Cursor = Row->Keys; *Cursor != nullptr; ++Cursor)
		{
			Keys.Add(FString(*Cursor));
		}

		TArray<TSharedPtr<FJsonValue>> KeyValues;
		for (const FString& Key : Keys)
		{
			KeyValues.Add(MakeShared<FJsonValueString>(Key));
		}
		Out->SetArrayField(TEXT("acceptedParams"), KeyValues);
		Out->SetNumberField(TEXT("acceptedParamCount"), Keys.Num());
		Out->SetStringField(TEXT("acceptedSummary"), FString(Row->Summary));
		// The one case where an empty acceptedParams is a real answer, said out loud so it can never
		// be confused with the omitted-field case above.
		Out->SetBoolField(TEXT("acceptsNoParameters"), Keys.Num() == 0);

		// Matching is case-insensitive in the guard itself (RejectUnknownParams compares with
		// ESearchCase::IgnoreCase, as do JStr/JBool/JInt), so say so rather than letting a caller
		// infer that the listed casing is mandatory.
		Out->SetBoolField(TEXT("caseInsensitiveKeys"), true);

		// --- alias groups, parsed from the summary and cross-checked against the key list ------
		TArray<FMifDescribeAliasGroup> Groups;
		MifDescribeParseAliasGroups(FString(Row->Summary), Groups);

		auto KeysContain = [&Keys](const FString& Candidate)
		{
			for (const FString& Key : Keys)
			{
				if (Key.Equals(Candidate, ESearchCase::IgnoreCase))
				{
					return true;
				}
			}
			return false;
		};

		TArray<TSharedPtr<FJsonValue>> GroupValues;
		TArray<TSharedPtr<FJsonValue>> Inconsistencies;
		TSet<FString> Mentioned;
		for (const FMifDescribeAliasGroup& Group : Groups)
		{
			if (!KeysContain(Group.Canonical))
			{
				Inconsistencies.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("summary names '%s' as a canonical parameter but it is not in the guard's ")
					TEXT("accepted-key list - the prose and the guard disagree"), *Group.Canonical)));
				continue;
			}
			Mentioned.Add(Group.Canonical.ToLower());

			TArray<TSharedPtr<FJsonValue>> AliasValues;
			for (const FString& Alias : Group.Aliases)
			{
				if (!KeysContain(Alias))
				{
					Inconsistencies.Add(MakeShared<FJsonValueString>(FString::Printf(
						TEXT("summary offers '%s' as an alias of '%s' but the guard does not accept ")
						TEXT("it - sending it would be REJECTED"), *Alias, *Group.Canonical)));
					continue;
				}
				Mentioned.Add(Alias.ToLower());
				AliasValues.Add(MakeShared<FJsonValueString>(Alias));
			}

			TSharedRef<FJsonObject> GroupObject = MakeShared<FJsonObject>();
			GroupObject->SetStringField(TEXT("canonical"), Group.Canonical);
			GroupObject->SetArrayField(TEXT("aliases"), AliasValues);
			GroupValues.Add(MakeShared<FJsonValueObject>(GroupObject));
		}
		Out->SetArrayField(TEXT("aliasGroups"), GroupValues);

		// Keys that no alias group claims: the distinct parameters, one entry per real concept.
		TArray<TSharedPtr<FJsonValue>> DistinctValues;
		for (const FString& Key : Keys)
		{
			bool bIsAlias = false;
			for (const FMifDescribeAliasGroup& Group : Groups)
			{
				for (const FString& Alias : Group.Aliases)
				{
					if (Alias.Equals(Key, ESearchCase::IgnoreCase))
					{
						bIsAlias = true;
						break;
					}
				}
				if (bIsAlias)
				{
					break;
				}
			}
			if (!bIsAlias)
			{
				DistinctValues.Add(MakeShared<FJsonValueString>(Key));
			}
		}
		Out->SetArrayField(TEXT("distinctParams"), DistinctValues);

		if (Inconsistencies.Num() > 0)
		{
			Out->SetArrayField(TEXT("summaryInconsistencies"), Inconsistencies);
		}

		// Accepted keys the prose never mentions. Not an error - but it is exactly why callers were
		// reduced to guessing: these are accepted, and reading the guard's own error message would
		// never reveal them.
		TArray<TSharedPtr<FJsonValue>> Undocumented;
		const FString SummaryText = FString(Row->Summary);
		for (const FString& Key : Keys)
		{
			if (Mentioned.Contains(Key.ToLower()))
			{
				continue;
			}
			if (SummaryText.Contains(Key, ESearchCase::IgnoreCase))
			{
				continue;
			}
			Undocumented.Add(MakeShared<FJsonValueString>(Key));
		}
		if (Undocumented.Num() > 0)
		{
			Out->SetArrayField(TEXT("keysNotInSummary"), Undocumented);
			Out->SetStringField(TEXT("keysNotInSummaryNote"),
				TEXT("these keys ARE accepted by the guard but appear nowhere in its summary text, so ")
				TEXT("the endpoint's own rejection message would never reveal them"));
		}

		// --- common mistakes (the guard's KeyNotes) --------------------------------------------
		if (Row->NotePairs)
		{
			TArray<TSharedPtr<FJsonValue>> NoteValues;
			for (const TCHAR* const* Cursor = Row->NotePairs; *Cursor != nullptr && *(Cursor + 1) != nullptr;
				Cursor += 2)
			{
				TSharedRef<FJsonObject> Note = MakeShared<FJsonObject>();
				Note->SetStringField(TEXT("key"), FString(*Cursor));
				Note->SetStringField(TEXT("hint"), FString(*(Cursor + 1)));
				NoteValues.Add(MakeShared<FJsonValueObject>(Note));
			}
			Out->SetArrayField(TEXT("commonMistakes"), NoteValues);
		}

		// --- provenance ------------------------------------------------------------------------
		TSharedRef<FJsonObject> Guard = MakeShared<FJsonObject>();
		Guard->SetStringField(TEXT("file"), FString(Row->SourceFile));
		if (Row->SourceLine > 0)
		{
			Guard->SetNumberField(TEXT("line"), Row->SourceLine);
		}
		else
		{
			// describe_endpoint's own row. A hardcoded self-citation would need updating on every
			// edit made ABOVE the guard in this file, and a line citation that drifts is worse than
			// none - it sends the next reader to the wrong place, which is the documented mechanism
			// behind this module's duplicate-helper bugs (see MifBridgeEndpointRegistry.h's note on
			// why it stopped citing line numbers). The function name does not drift, so cite that.
			Guard->SetStringField(TEXT("function"), TEXT("H_describe_endpoint"));
		}
		if (Row->ViaHelper)
		{
			Guard->SetStringField(TEXT("viaHelper"), FString(Row->ViaHelper));
			Guard->SetStringField(TEXT("viaHelperNote"), FString::Printf(
				TEXT("the guard is not in H_%s but in the shared body %s(), which serves more than one ")
				TEXT("endpoint - edit it and every caller's accepted set moves"), *Resolved, Row->ViaHelper));
		}
		Out->SetObjectField(TEXT("guard"), Guard);

		TSharedRef<FJsonObject> Harvest = MakeShared<FJsonObject>();
		Harvest->SetStringField(TEXT("mechanism"), TEXT("static table harvested from RejectUnknownParams call sites"));
		Harvest->SetStringField(TEXT("limitation"),
			TEXT("This set was copied from the initialiser-list literal cited in 'guard' at build time. ")
			TEXT("A rename or removal of the ENDPOINT is detected at runtime (coverage.staleTableRows); ")
			TEXT("a change to the KEY LIST of a still-existing endpoint is NOT detectable from inside ")
			TEXT("the DLL - verify against the cited file:line if that matters to you."));
		Out->SetObjectField(TEXT("harvest"), Harvest);
	}


	// Exposed so self_audit can report table COVERAGE without duplicating the table. Named for the
	// TABLE, never for a guard: a missing row means the harvester found no RejectUnknownParams call
	// site, and that has two causes with opposite consequences (no guard at all, or a guard added
	// after the harvest). Ten endpoints were in the second case while an earlier revision confidently
	// reported the first. Callers must not upgrade "no row" into "accepts anything".
	bool MifDescribeHasParamRow(const FString& Endpoint, int32* OutParamCount)
	{
		const FMifDescribeRow* Row = MifDescribeFindRow(Endpoint);
		if (!Row) { return false; }
		if (OutParamCount)
		{
			int32 N = 0;
			for (const TCHAR* const* K = Row->Keys; K && *K; ++K) { ++N; }
			*OutParamCount = N;
		}
		return true;
	}

	int32 MifDescribeParamRowCount()
	{
		return (int32)UE_ARRAY_COUNT(GMifDescribeRows);
	}

#undef MIF_DESCRIBE_OWN_KEYS
#undef MIF_DESCRIBE_OWN_SUMMARY
}   // namespace MifBridge
