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

		static const TCHAR* const GMifDescKeys_add_bind_dispatcher[] = {
			TEXT("graphId"), TEXT("dispatcher"), TEXT("targetClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_bind_dispatcher[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("name"), TEXT("the existing dispatcher is named by 'dispatcher'; 'name' is add_event_dispatcher's key for CREATING one"),
			TEXT("dispatcherName"), TEXT("spell it dispatcher"),
			TEXT("blueprintId"), TEXT("graphId already names the blueprint — pass the graph the node lands in"),
			TEXT("target"), TEXT("targetClass names the CLASS that declares the dispatcher; the OBJECT goes into the node's Target/self pin via connect_pins, never here"),
			TEXT("event"), TEXT("the handler is wired into the bind node's Delegate pin — add_custom_event then connect_pins; this endpoint only places the node"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_branch[] = {
			TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_branch[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("condition"), TEXT("the Condition input is a pin — place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_break_struct[] = {
			TEXT("graphId"), TEXT("structName"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_break_struct[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("struct"), TEXT("spell it structName"),
			TEXT("name"), TEXT("the struct is named by structName; resolve_struct is the endpoint whose parameter is called name"),
			TEXT("type"), TEXT("spell it structName"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_call_dispatcher[] = {
			TEXT("graphId"), TEXT("dispatcher"), TEXT("targetClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_call_dispatcher[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("name"), TEXT("the existing dispatcher is named by 'dispatcher'; 'name' is add_event_dispatcher's key for CREATING one"),
			TEXT("dispatcherName"), TEXT("spell it dispatcher"),
			TEXT("blueprintId"), TEXT("graphId already names the blueprint — pass the graph the node lands in"),
			TEXT("target"), TEXT("targetClass names the CLASS that declares the dispatcher; the OBJECT goes into the node's Target/self pin via connect_pins, never here"),
			TEXT("event"), TEXT("the handler is wired into the bind node's Delegate pin — add_custom_event then connect_pins; this endpoint only places the node"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_cast[] = {
			TEXT("graphId"), TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"),
			TEXT("castTo"), TEXT("to"), TEXT("targetType"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_cast[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("pure"), TEXT("add_cast always creates an IMPURE cast so the Cast Failed exec pin exists; there is no pure option here"),
			TEXT("object"), TEXT("the object to cast is a pin — place the node, then connect_pins into its Object pin"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_class_cast[] = {
			TEXT("graphId"), TEXT("targetClass"), TEXT("class"), TEXT("castTo"), TEXT("to"),
			TEXT("targetType"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_class_cast[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("cls"), TEXT("add_cast accepts cls, add_class_cast does not - use targetClass"),
			TEXT("className"), TEXT("add_cast accepts className, add_class_cast does not - use targetClass"),
			TEXT("object"), TEXT("the class value to cast is a pin - place the node, then connect_pins into its input pin"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_comment[] = {
			TEXT("graphId"), TEXT("x"), TEXT("y"), TEXT("width"), TEXT("height"), TEXT("text"), nullptr };
		static const TCHAR* const GMifDescNotes_add_comment[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("comment"), TEXT("spell it text"),
			TEXT("nodeComment"), TEXT("spell it text"),
			TEXT("color"), TEXT("not supported - the box takes the editor's default comment colour"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_component[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("componentClass"), TEXT("class"), TEXT("name"),
			TEXT("parentName"), TEXT("location"), TEXT("rotation"), TEXT("scale"), nullptr };
		static const TCHAR* const GMifDescNotes_add_component[] = {
			TEXT("componentName"), TEXT("spell it name - it is the NEW component's variable name"),
			TEXT("component"), TEXT("spell it name for the new component, or parentName for the existing one to attach it under"),
			TEXT("parent"), TEXT("spell it parentName - the EXISTING component the new one is attached under"),
			TEXT("transform"), TEXT("pass location / rotation / scale as separate keys; there is no combined transform key"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_create_widget[] = {
			TEXT("graphId"), TEXT("widgetClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_create_widget[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("widget"), TEXT("CreateWidget takes the CLASS to create - pass widgetClass (e.g. /Game/UI/W_Foo.W_Foo_C)"),
			TEXT("owningPlayer"), TEXT("Owning Player is a pin - place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_custom_event[] = {
			TEXT("graphId"), TEXT("name"), TEXT("inputs"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_custom_event[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("outputs"), TEXT("a custom event's parameters ARE its output pins - list them under inputs; there is no outputs key here (create_function is the endpoint that has both)"),
			TEXT("params"), TEXT("spell it inputs"),
			TEXT("parameters"), TEXT("spell it inputs"),
			TEXT("eventName"), TEXT("spell it name"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_enhanced_input_action[] = {
			TEXT("graphId"), TEXT("inputAction"), TEXT("action"), TEXT("actionPath"), TEXT("x"), TEXT("y"),
			nullptr };
		static const TCHAR* const GMifDescNotes_add_enhanced_input_action[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("blueprintId"), TEXT("this endpoint places a node in a GRAPH - pass graphId (list_graphs shows every graph by its full dotted path)"),
			TEXT("inputActionPath"), TEXT("spell it inputAction (aliases: action, actionPath)"),
			TEXT("class"), TEXT("pass the UInputAction ASSET path as inputAction, not a class"),
			TEXT("trigger"), TEXT("the Triggered/Started/Ongoing/Canceled/Completed exec pins are generated from the action - place the node, then connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_enum_literal[] = {
			TEXT("graphId"), TEXT("enumName"), TEXT("value"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_enum_literal[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("enum"), TEXT("spell it enumName here - list_enum_values takes either, this endpoint reads only enumName"),
			TEXT("default"), TEXT("spell it value - and it is the enumerator name, not an index; get the exact text from list_enum_values"),
			TEXT("enumerator"), TEXT("spell it value (the enumerator name from list_enum_values)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_enum_value[] = {
			TEXT("enum"), TEXT("enumPath"), TEXT("path"), TEXT("value"), TEXT("name"), TEXT("displayName"),
			nullptr };
		static const TCHAR* const GMifDescNotes_add_enum_value[] = {
			TEXT("values"), TEXT("add_enum_value appends ONE entry; pass value:\"Ready\". The values[] array belongs to create_enum"),
			TEXT("index"), TEXT("the new entry is always appended; its index comes back in the response"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_event_dispatcher[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs"), nullptr };
		static const TCHAR* const GMifDescNotes_add_event_dispatcher[] = {
			TEXT("dispatcher"), TEXT("'dispatcher' names an EXISTING dispatcher on add_call_dispatcher/add_bind_dispatcher; the one being created here is named by 'name'"),
			TEXT("params"), TEXT("spell it inputs (the response reports the count back as 'params')"),
			TEXT("parameters"), TEXT("spell it inputs"),
			TEXT("outputs"), TEXT("a dispatcher signature has inputs only — they surface as OUTPUT pins on the bound event"),
			TEXT("graphId"), TEXT("a dispatcher belongs to the blueprint, not to one graph — pass blueprintId"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_foliage_instances[] = {
			TEXT("mesh"), TEXT("staticMesh"), TEXT("instances"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_add_foliage_instances[] = {
			TEXT("material"), TEXT("not implemented — the HISM uses the mesh's own materials; override them with set_property on the component afterwards"),
			TEXT("transforms"), TEXT("the array parameter is called instances[]"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_format_text[] = {
			TEXT("graphId"), TEXT("format"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_format_text[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("text"), TEXT("spell it format"),
			TEXT("formatText"), TEXT("spell it format"),
			TEXT("args"), TEXT("argument pins come from the {tokens} inside format - place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_function_call[] = {
			TEXT("graphId"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("targetClass"),
			TEXT("ownerClass"), TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method"),
			TEXT("asMessage"), TEXT("message"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_function_call[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("target"), TEXT("the target OBJECT is wired into the node's self/Target pin with connect_pins; 'class' names the class that declares the function"),
			TEXT("args"), TEXT("arguments are pins — place the node, then set_pin_default or connect_pins"),
			TEXT("pure"), TEXT("purity comes from the UFUNCTION itself (BlueprintPure); it is not selectable here"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_get_array_item[] = {
			TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_get_array_item[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("index"), TEXT("the index is a pin — the response names it as indexPin; use set_pin_default or connect_pins"),
			TEXT("array"), TEXT("the array is a pin — the response names it as arrayPin; use connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_get_data_table_row[] = {
			TEXT("graphId"), TEXT("dataTable"), TEXT("rowName"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_get_data_table_row[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("table"), TEXT("spell it dataTable"),
			TEXT("dataTablePath"), TEXT("spell it dataTable"),
			TEXT("row"), TEXT("spell it rowName"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_get_subsystem[] = {
			TEXT("graphId"), TEXT("subsystemClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_get_subsystem[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("subsystem"), TEXT("spell it subsystemClass - it must name a USubsystem-derived CLASS"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_interface[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("interface"), TEXT("interfaceClass"), TEXT("class"),
			nullptr };
		static const TCHAR* const GMifDescNotes_add_interface[] = {
			TEXT("confirm"), TEXT("add_interface is additive and needs no confirm; remove_interface is the one that requires it"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_literal[] = {
			TEXT("graphId"), TEXT("object"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_literal[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("value"), TEXT("add_literal makes an OBJECT-reference literal only - for a scalar (int/float/bool/string/name) place the consuming node and use set_pin_default on its pin instead"),
			TEXT("path"), TEXT("the asset path goes in object"),
			TEXT("objectPath"), TEXT("spell it object"),
			TEXT("asset"), TEXT("spell it object"),
			TEXT("type"), TEXT("the literal's type comes from the resolved object's class; there is nothing to declare"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_macro_instance[] = {
			TEXT("graphId"), TEXT("macroGraph"), TEXT("macro"), TEXT("macroName"), TEXT("name"),
			TEXT("macroPath"), TEXT("macroLibrary"), TEXT("library"), TEXT("path"), TEXT("x"), TEXT("y"),
			nullptr };
		static const TCHAR* const GMifDescNotes_add_macro_instance[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_make_array[] = {
			TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_make_array[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("num"), TEXT("spell it numInputs"),
			TEXT("count"), TEXT("spell it numInputs"),
			TEXT("items"), TEXT("the element values are pins - place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_make_map[] = {
			TEXT("graphId"), TEXT("numInputs"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_make_map[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("numEntries"), TEXT("spell it numInputs - one 'input' is one Key/Value entry"),
			TEXT("entries"), TEXT("spell it numInputs for the COUNT; the keys and values themselves are pins"),
			TEXT("pairs"), TEXT("spell it numInputs for the COUNT; the keys and values themselves are pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_make_struct[] = {
			TEXT("graphId"), TEXT("structName"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_make_struct[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("struct"), TEXT("spell it structName"),
			TEXT("name"), TEXT("the struct is named by structName; resolve_struct is the endpoint whose parameter is called name"),
			TEXT("type"), TEXT("spell it structName"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_material_expression[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("class"), TEXT("expressionClass"),
			TEXT("type"), TEXT("x"), TEXT("nodePosX"), TEXT("posX"), TEXT("y"), TEXT("nodePosY"),
			TEXT("posY"), TEXT("properties"), TEXT("props"), TEXT("asset"), TEXT("selectedAsset"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_nav_volume[] = {
			TEXT("location"), TEXT("size"), TEXT("label"), nullptr };
		static const TCHAR* const GMifDescNotes_add_nav_volume[] = {
			TEXT("scale"), TEXT("pass size in world units - the brush scale (size / 200) is computed for you"),
			TEXT("extent"), TEXT("use size, which is the FULL coverage in world units, not a half-extent"),
			TEXT("name"), TEXT("use label - it becomes the volume's outliner label"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_override_event[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("event"), TEXT("eventName"), TEXT("name"),
			TEXT("function"), TEXT("functionName"), TEXT("interfaceOrParent"), TEXT("class"), TEXT("cls"),
			TEXT("className"), TEXT("parentClass"), TEXT("interface"), TEXT("ownerClass"),
			TEXT("targetClass"), TEXT("callParent"), TEXT("addParentCall"), TEXT("withParentCall"),
			TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_override_event[] = {
			TEXT("graphId"), TEXT("an override always lands in the blueprint's event graph — pass blueprintId instead"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_component_bound_event[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("component"), TEXT("dispatcher"), TEXT("delegate"),
			TEXT("event"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_component_bound_event[] = {
			TEXT("targetClass"), TEXT("not needed here - the delegate's owner class is found automatically from the component's own type"),
			TEXT("graphId"), TEXT("this always lands in the blueprint's event graph - pass blueprintId instead"),
			TEXT("bind"), TEXT("for a delegate that ISN'T declared on a component (a custom event dispatcher, or one on the blueprint itself) use add_bind_dispatcher instead - this endpoint is specifically for per-component delegates like OnComponentBeginOverlap"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_parent_call[] = {
			TEXT("graphId"), TEXT("parentClass"), TEXT("class"), TEXT("cls"), TEXT("className"),
			TEXT("parent"), TEXT("ownerClass"), TEXT("targetClass"), TEXT("function"), TEXT("functionName"),
			TEXT("func"), TEXT("method"), TEXT("name"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_parent_call[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_pin[] = {
			TEXT("name"), TEXT("pin"), TEXT("pinName"), TEXT("type"), TEXT("pinType"), TEXT("container"),
			TEXT("valueType"), TEXT("direction"), TEXT("dir"), TEXT("default"), TEXT("defaultValue"),
			TEXT("value"), TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"),
			TEXT("blueprintId"), TEXT("path"), TEXT("function"), TEXT("functionName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_pin[] = {
			TEXT("confirm"), TEXT("add_pin is additive and needs no confirm; remove_pin is the one that does"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_self[] = {
			TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_self[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("blueprintId"), TEXT("a node is placed in a GRAPH - pass graphId (list_graphs shows every graph of a blueprint); the owning blueprint is inferred from it"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_simplified_collision[] = {
			TEXT("path"), TEXT("shape"), nullptr };
		static const TCHAR* const GMifDescNotes_add_simplified_collision[] = {
			TEXT("objectPath"), TEXT("spell it path"),
			TEXT("type"), TEXT("spell it shape"),
			TEXT("replace"), TEXT("there is no replace - this endpoint is ADDITIVE. Call remove_collision first (the engine's own replace path is commented out in GeomFitUtils.cpp, so generating over existing collision silently stacks a second primitive)"),
			TEXT("sphyl"), TEXT("spell it shape=capsule"),
			nullptr };

		static const TCHAR* const GMifDescKeys_add_sequence[] = {
			TEXT("graphId"), TEXT("x"), TEXT("y"), TEXT("outputs"), nullptr };
		static const TCHAR* const GMifDescNotes_add_sequence[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("numOutputs"), TEXT("spell it outputs (add_make_array/add_make_map use numInputs; Sequence uses outputs)"),
			TEXT("pins"), TEXT("spell it outputs - it is the count of then_N exec pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_spawn_actor[] = {
			TEXT("graphId"), TEXT("actorClass"), TEXT("class"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_spawn_actor[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("actor"), TEXT("SpawnActor takes the CLASS to spawn, not an instance - pass actorClass (e.g. /Game/BP/BP_Foo.BP_Foo_C)"),
			TEXT("transform"), TEXT("SpawnTransform is a pin - place the node, then set_pin_default or connect_pins"),
			TEXT("spawnTransform"), TEXT("SpawnTransform is a pin - place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_struct_member[] = {
			TEXT("struct"), TEXT("structPath"), TEXT("path"), TEXT("name"), TEXT("type"), TEXT("container"),
			TEXT("valueType"), TEXT("default"), nullptr };
		static const TCHAR* const GMifDescNotes_add_struct_member[] = {
			TEXT("class"), TEXT("the class belongs IN the type string, not in its own key: type:\"object:SceneComponent\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X"),
			TEXT("subType"), TEXT("use type:\"object:X\" for the referenced class, or valueType for a map's value type"),
			TEXT("memberName"), TEXT("the member name parameter is called name"),
			TEXT("defaultValue"), TEXT("the parameter is called default"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_sublevel[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class"),
			TEXT("location"), TEXT("rotation"), nullptr };
		static const TCHAR* const GMifDescKeys_add_switch_enum[] = {
			TEXT("graphId"), TEXT("enumName"), TEXT("hasDefault"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_switch_enum[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("enum"), TEXT("spell it enumName here - list_enum_values takes either, this endpoint reads only enumName"),
			TEXT("cases"), TEXT("the case pins come from the enum's own entries; list them with list_enum_values"),
			TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_switch_int[] = {
			TEXT("graphId"), TEXT("cases"), TEXT("startIndex"), TEXT("hasDefault"), TEXT("x"), TEXT("y"),
			nullptr };
		static const TCHAR* const GMifDescNotes_add_switch_int[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("count"), TEXT("spell it cases (the number of case pins to create)"),
			TEXT("caseLabels"), TEXT("an int switch has no labels - pass cases as a count and startIndex as the first value; add_switch_string is the one that takes an array"),
			TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_switch_string[] = {
			TEXT("graphId"), TEXT("cases"), TEXT("caseSensitive"), TEXT("hasDefault"), TEXT("x"), TEXT("y"),
			nullptr };
		static const TCHAR* const GMifDescNotes_add_switch_string[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("caseLabels"), TEXT("spell it cases (an array of label strings)"),
			TEXT("selection"), TEXT("the Selection input is a pin - place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_timeline[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("floatTracks"), TEXT("length"),
			TEXT("autoPlay"), TEXT("loop"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_timeline[] = {
			TEXT("graphId"), TEXT("add_timeline takes a blueprintId, not a graphId - the node is placed in the blueprint's own event graph"),
			TEXT("tracks"), TEXT("spell it floatTracks (an array of non-empty track name strings)"),
			TEXT("timelineName"), TEXT("spell it name; omit it entirely for an auto-generated unique name"),
			TEXT("curve"), TEXT("a UCurveFloat is created per entry in floatTracks; you cannot supply one here"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_tree_widget[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("widgetClass"), TEXT("class"), TEXT("name"),
			TEXT("parentName"), TEXT("asRoot"), TEXT("x"), TEXT("y"), TEXT("autoSize"), nullptr };
		static const TCHAR* const GMifDescNotes_add_tree_widget[] = {
			TEXT("widgetName"), TEXT("the NEW widget's name parameter is called name; widgetName is only a response field"),
			TEXT("className"), TEXT("the class parameter is called widgetClass (alias: class)"),
			TEXT("parent"), TEXT("spell it parentName — the FName of a UPanelWidget already in the tree"),
			TEXT("position"), TEXT("pass the canvas-slot position as separate numbers x and y"),
			TEXT("size"), TEXT("not implemented — the canvas slot is auto-sized; set the slot's Size with set_property after adding"),
			TEXT("slot"), TEXT("slot properties beyond x/y/autoSize are not settable here — use set_property on the created widget's Slot"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_variable[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("type"), TEXT("container"),
			TEXT("valueType"), TEXT("scope"), TEXT("function"), TEXT("default"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable[] = {
			TEXT("class"), TEXT("the class belongs IN the type string, not in its own key: type:\"object:SceneComponent\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X"),
			TEXT("className"), TEXT("use type:\"object:X\" (or class:X / subclassof:X / softobject:X / softclass:X)"),
			TEXT("parentClass"), TEXT("add_variable does not take a parent class. For a typed object variable use type:\"object:X\"; to override a parent's event use add_override_event"),
			TEXT("objectClass"), TEXT("use type:\"object:X\""),
			TEXT("subType"), TEXT("use type:\"object:X\" for the referenced class, or valueType for a map's value type"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_variable_get[] = {
			TEXT("graphId"), TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"), TEXT("property"),
			TEXT("propertyName"), TEXT("member"), TEXT("targetClass"), TEXT("class"), TEXT("cls"),
			TEXT("className"), TEXT("ownerClass"), TEXT("objectClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable_get[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("target"), TEXT("targetClass names the CLASS that owns the property; the OBJECT is wired into the node's Target pin with connect_pins, never passed here"),
			TEXT("value"), TEXT("a Set node takes its value on a pin — place the node, then set_pin_default or connect_pins"),
			TEXT("scope"), TEXT("scope is auto-detected: a variable declared on this function graph resolves as a local, anything else as a member"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_variable_set[] = {
			TEXT("graphId"), TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"), TEXT("property"),
			TEXT("propertyName"), TEXT("member"), TEXT("targetClass"), TEXT("class"), TEXT("cls"),
			TEXT("className"), TEXT("ownerClass"), TEXT("objectClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable_set[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("target"), TEXT("targetClass names the CLASS that owns the property; the OBJECT is wired into the node's Target pin with connect_pins, never passed here"),
			TEXT("value"), TEXT("a Set node takes its value on a pin — place the node, then set_pin_default or connect_pins"),
			TEXT("scope"), TEXT("scope is auto-detected: a variable declared on this function graph resolves as a local, anything else as a member"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_widget_binding[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName"),
			TEXT("functionName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_widget_binding[] = {
			TEXT("property"), TEXT("spell it propertyName (the widget property to drive, e.g. \"Text\")"),
			TEXT("function"), TEXT("spell it functionName (a pure UFUNCTION on the user widget, e.g. \"GetText\")"),
			TEXT("widget"), TEXT("spell it widgetName"),
			TEXT("kind"), TEXT("not settable — this endpoint only writes function bindings (EBindingKind::Function)"),
			TEXT("sourcePath"), TEXT("not settable — SourcePath is deliberately left empty so the runtime binds via BindUFunction(functionName)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_audit_unused[] = {
			TEXT("pathPrefix"), TEXT("class"), TEXT("includeAll"), TEXT("limit"), TEXT("rescan"),
			TEXT("excludeReferencers"), TEXT("excludeReferencer"), TEXT("ignoreReferencers"), nullptr };
		static const TCHAR* const GMifDescKeys_backup_blueprint[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_backup_blueprint[] = {
			TEXT("destination"), TEXT("backup_blueprint picks the backup location itself and reports it as 'backup' in the response; it takes no destination"),
			nullptr };
		static const TCHAR* const GMifDescKeys_batch[] = {
			TEXT("ops"), TEXT("blueprintId"), TEXT("path"), TEXT("backup"), TEXT("compileAtEnd"), nullptr };
		static const TCHAR* const GMifDescNotes_batch[] = {
			TEXT("operations"), TEXT("spell it ops"),
			TEXT("graphId"), TEXT("graphId belongs on each op inside ops, not on the batch envelope"),
			nullptr };
		static const TCHAR* const GMifDescKeys_bind_landscape_rvt[] = {
			TEXT("landscape"), TEXT("actorPath"), TEXT("runtimeVirtualTextures"), TEXT("createVolumes"),
			nullptr };
		static const TCHAR* const GMifDescNotes_bind_landscape_rvt[] = {
			TEXT("runtimeVirtualTexture"), TEXT("the key is PLURAL and takes an array - runtimeVirtualTextures:[assetPath], even for one"),
			TEXT("rvt"), TEXT("use runtimeVirtualTextures:[assetPath,...]"),
			TEXT("createVolume"), TEXT("the key is PLURAL - createVolumes (bool)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_build_navmesh[] = { nullptr };
		static const TCHAR* const GMifDescNotes_build_navmesh[] = {
			TEXT("wait"), TEXT("not supported - generation is asynchronous; poll nav_status until building=false and tiles>0"),
			TEXT("timeout"), TEXT("not supported - this call never blocks; poll nav_status instead"),
			nullptr };
		static const TCHAR* const GMifDescKeys_capture_camera[] = {
			TEXT("x"), TEXT("y"), TEXT("z"), TEXT("location"), TEXT("rotation"), TEXT("lookAt"),
			TEXT("useViewportCamera"), TEXT("useViewport"), TEXT("fromViewport"), TEXT("fov"),
			TEXT("width"), TEXT("height"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_capture_camera[] = {
			TEXT("showFlags"), TEXT("not implemented — capture_camera always renders lit/tonemapped with Atmosphere+Fog on and does NOT read the level viewport's show flags; set_view_mode does not reach this image"),
			TEXT("viewMode"), TEXT("not implemented — same gap as showFlags: the viewport's view mode is not consumed here"),
			TEXT("actorPath"), TEXT("not a parameter of this endpoint — to frame an actor, read get_actor_bounds and pass its origin as lookAt, or focus_viewport the actor and then capture with useViewportCamera:true"),
			nullptr };
		static const TCHAR* const GMifDescKeys_check_overlaps[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("nameContains"), TEXT("ignoreGround"), TEXT("tolerance"),
			nullptr };
		static const TCHAR* const GMifDescNotes_check_overlaps[] = {
			TEXT("path"), TEXT("this endpoint takes actorPath (alias: actor) only — 'path' is accepted by get_actor_bounds, not here"),
			TEXT("name"), TEXT("use nameContains for a substring filter over object names and labels, or actorPath to test a single actor"),
			TEXT("depth"), TEXT("'depth' is an OUTPUT field on each reported pair — the input threshold is 'tolerance' (default 25)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_compile[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_compile[] = {
			TEXT("save"), TEXT("compile does not write to disk - call save_blueprint {blueprintId} afterwards to persist"),
			TEXT("dryRun"), TEXT("compile always commits the compiled class; validate {blueprintId} is the dry-run form and returns the same messages"),
			nullptr };
		static const TCHAR* const GMifDescKeys_connect_material_expressions[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("from"), TEXT("fromExpression"),
			TEXT("fromOutput"), TEXT("fromOutputName"), TEXT("to"), TEXT("toExpression"), TEXT("toInput"),
			TEXT("toInputName"), nullptr };
		static const TCHAR* const GMifDescKeys_connect_material_property[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("from"), TEXT("fromExpression"),
			TEXT("fromOutput"), TEXT("fromOutputName"), TEXT("property"), TEXT("materialProperty"),
			nullptr };
		static const TCHAR* const GMifDescKeys_connect_pins[] = {
			TEXT("srcNode"), TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin"), TEXT("dstNode"),
			TEXT("dstPin"), TEXT("destPin"), TEXT("toPin"), TEXT("graphId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_connect_pins[] = {
			TEXT("from"), TEXT("spell it srcNode"),
			TEXT("fromNode"), TEXT("spell it srcNode"),
			TEXT("sourceNode"), TEXT("spell it srcNode"),
			TEXT("to"), TEXT("spell it dstNode"),
			TEXT("toNode"), TEXT("spell it dstNode"),
			TEXT("destNode"), TEXT("spell it dstNode"),
			TEXT("targetNode"), TEXT("spell it dstNode"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_blueprint[] = {
			TEXT("path"), TEXT("parentClass"), TEXT("blueprintType"),
			TEXT("skeleton"), TEXT("targetSkeleton"), nullptr };
		static const TCHAR* const GMifDescNotes_create_blueprint[] = {
			TEXT("overwrite"), TEXT("NOT supported — this endpoint refuses to clobber an existing asset. delete_asset the old one first, or pick a new path"),
			TEXT("name"), TEXT("the asset name is the last segment of path"),
			TEXT("animBlueprint"), TEXT("spell it blueprintType=AnimBlueprint, and pass skeleton — an Animation Blueprint is a UAnimBlueprint with a TargetSkeleton, not a Blueprint parented to UAnimInstance"),
			TEXT("parent"), TEXT("the base class parameter is called parentClass"),
			nullptr };
		static const TCHAR* const GMifDescKeys_reparent_blueprint[] = {
			TEXT("blueprintId"), TEXT("newParentClass"), nullptr };
		static const TCHAR* const GMifDescNotes_reparent_blueprint[] = {
			TEXT("newParent"), TEXT("spell it newParentClass (alias parentClass)"),
			TEXT("class"), TEXT("the new parent class parameter is called newParentClass"),
			TEXT("path"), TEXT("that names the TARGET blueprint (alias of blueprintId), not the new parent — the new parent is newParentClass"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_editable_child[] = {
			TEXT("sourceAsset"), TEXT("childPath"), TEXT("variant"), nullptr };
		static const TCHAR* const GMifDescNotes_create_editable_child[] = {
			TEXT("blueprintId"), TEXT("spell it sourceAsset - pass the cooked BP's _C class path (/Game/X/BP_Foo.BP_Foo_C) or its asset path"),
			TEXT("path"), TEXT("the SOURCE is sourceAsset; the DESTINATION is childPath"),
			TEXT("source"), TEXT("spell it sourceAsset"),
			TEXT("targetPath"), TEXT("spell it childPath"),
			TEXT("asChild"), TEXT("there is no boolean form - it is variant:\"child\" (the default) vs variant:\"sibling\""),
			TEXT("fullParent"), TEXT("there is no boolean form - it is variant:\"sibling_full\" (alias: \"full\")"),
			TEXT("name"), TEXT("the new asset's name comes from childPath - pass the full destination package path"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_enum[] = {
			TEXT("path"), TEXT("values"), nullptr };
		static const TCHAR* const GMifDescNotes_create_enum[] = {
			TEXT("name"), TEXT("the enum's name comes from the last segment of path - pass path:\"/Game/Types/E_Foo\""),
			TEXT("enum"), TEXT("create_enum MAKES the enum; the new asset location goes in path. To extend an existing enum use add_enum_value"),
			TEXT("enumPath"), TEXT("the new asset location parameter is called path (enumPath is what the response returns)"),
			TEXT("entries"), TEXT("the entry list parameter is called values[]"),
			TEXT("members"), TEXT("members[] is create_struct's parameter; an enum's entries go in values[]"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_function[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs"), TEXT("outputs"), TEXT("pure"),
			nullptr };
		static const TCHAR* const GMifDescNotes_create_function[] = {
			TEXT("override"), TEXT("create_function makes a NEW function; it cannot override. Use add_override_event {event, parentClass?, callParent?} — naming a parent's function here creates a COLLIDING duplicate that fails to compile"),
			TEXT("parentClass"), TEXT("create_function does not take a parent class. add_override_event accepts parentClass (aliases: class, interfaceOrParent, ownerClass, targetClass)"),
			TEXT("interface"), TEXT("to implement an interface function use implement_interface_function; to override a parent event use add_override_event"),
			TEXT("event"), TEXT("events live in the event graph — use add_custom_event for a new one, or add_override_event to override a parent's"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_landscape[] = {
			TEXT("location"), TEXT("scale"), TEXT("componentsX"), TEXT("componentsY"),
			TEXT("quadsPerSection"), TEXT("sectionsPerComponent"), TEXT("material"),
			TEXT("landscapeMaterial"), TEXT("layers"), TEXT("heightMode"), TEXT("amplitude"),
			TEXT("frequency"), TEXT("seed"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_create_landscape[] = {
			TEXT("name"), TEXT("use label - it sets the actor's display label"),
			TEXT("position"), TEXT("use location {x,y,z}"),
			TEXT("layerInfo"), TEXT("layers is an ARRAY of objects - pass layers:[{layerInfo:\"/Game/.../X_LayerInfo\", weight:0..1}]"),
			TEXT("heightmap"), TEXT("importing a heightmap file is not supported - use heightMode (flat|rolling|island) with amplitude, frequency and seed"),
			TEXT("rotation"), TEXT("not supported - the landscape is always spawned axis-aligned"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_material[] = {
			TEXT("path"), TEXT("assetPath"), TEXT("domain"), TEXT("materialDomain"), TEXT("blendMode"),
			TEXT("initialTexture"), nullptr };
		static const TCHAR* const GMifDescKeys_create_material_function[] = {
			TEXT("path"), TEXT("assetPath"), TEXT("description"), TEXT("exposeToLibrary"), nullptr };
		static const TCHAR* const GMifDescNotes_create_material_function[] = {
			TEXT("kind"), TEXT("not implemented — layer/layerBlend function kinds ship with set_material_instance_layers, a later batch"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_material_instance[] = {
			TEXT("parent"), TEXT("parentMaterial"), TEXT("path"), TEXT("scalars"), TEXT("vectors"),
			nullptr };
		static const TCHAR* const GMifDescNotes_create_material_instance[] = {
			TEXT("textures"), TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property"),
			TEXT("texture"), TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property"),
			TEXT("material"), TEXT("the source material parameter is called parent (alias: parentMaterial)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_struct[] = {
			TEXT("path"), TEXT("members"), nullptr };
		static const TCHAR* const GMifDescNotes_create_struct[] = {
			TEXT("name"), TEXT("the struct's name comes from the last segment of path - pass path:\"/Game/Types/S_Foo\""),
			TEXT("struct"), TEXT("create_struct MAKES the struct; the new asset location goes in path. To edit an existing struct use add_struct_member"),
			TEXT("structPath"), TEXT("the new asset location parameter is called path (structPath is what the response returns)"),
			TEXT("fields"), TEXT("the member list parameter is called members[]"),
			nullptr };
		static const TCHAR* const GMifDescKeys_delete_asset[] = {
			TEXT("path"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_delete_asset[] = {
			TEXT("packageName"), TEXT("spell it path - delete_asset takes the package under 'path'; an object path is accepted and reduced to its package"),
			TEXT("objectPath"), TEXT("spell it path - the whole PACKAGE is deleted, not one object inside it"),
			TEXT("force"), TEXT("there is no force - deletion is gated on confirm=true and still fails if the asset is still referenced"),
			nullptr };
		static const TCHAR* const GMifDescKeys_delete_datatable_rows[] = {
			TEXT("path"), TEXT("rowNames"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_delete_datatable_rows[] = {
			TEXT("rows"), TEXT("delete takes row NAMES, not row objects — pass rowNames:[\"A\",\"B\"]"),
			TEXT("rowName"), TEXT("the parameter is the array rowNames[]; pass a single-element array"),
			TEXT("dataTable"), TEXT("the datatable parameter is called path"),
			TEXT("table"), TEXT("the datatable parameter is called path"),
			nullptr };
		static const TCHAR* const GMifDescKeys_delete_level_actor[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_delete_level_actor[] = {
			TEXT("force"), TEXT("the confirmation key is 'confirm' and it must be true"),
			TEXT("actorPaths"), TEXT("this deletes ONE actor — call it once per actor; 'actorPaths' is select_level_actors' key"),
			nullptr };
		static const TCHAR* const GMifDescKeys_delete_material_expression[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("expression"), TEXT("name"),
			TEXT("all"), TEXT("deleteAll"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_animation[] = {
			TEXT("assetPath"), TEXT("path"), TEXT("animation"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescNotes_describe_animation[] = {
			TEXT("name"), TEXT("this endpoint needs an object PATH - assetPath (aliases: path, animation, asset). list_animations returns assetPath values you can paste straight in"),
			TEXT("skeleton"), TEXT("not an input here - the skeleton is REPORTED in the response; to filter a LIST by skeleton use list_animations"),
			TEXT("blueprintId"), TEXT("this reads animation DATA assets (sequence/montage/blend space/composite). For an Animation BLUEPRINT use list_graphs/list_nodes, which recurse into state machines and transition graphs"),
			nullptr };
		static const TCHAR* const GMifDescKeys_describe_class[] = {
			TEXT("class"), TEXT("className"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_package[] = {
			TEXT("package"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_property[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("class"), TEXT("className"), TEXT("propertyPath"), TEXT("property"), TEXT("nameContains"),
			TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"),
			TEXT("includeMetadata"), TEXT("includeDefault"), nullptr };
		static const TCHAR* const GMifDescKeys_diagnose_landscape[] = {
			TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_diagnose_landscape_draws[] = {
			TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_diff_properties_vs_default[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"),
			TEXT("includeTransient"), TEXT("deep"), TEXT("recursive"), TEXT("includeChildren"), nullptr };
		static const TCHAR* const GMifDescKeys_disconnect_pin[] = {
			TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"),
			TEXT("pinName"), TEXT("name"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_duplicate_actors[] = {
			TEXT("actorPaths"), TEXT("labelPrefix"), TEXT("offset"), TEXT("yawOffset"), TEXT("count"),
			TEXT("labelSuffix"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_duplicate_actors[] = {
			TEXT("rotationOffset"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>"),
			TEXT("rotation"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>"),
			TEXT("scale"), TEXT("not implemented — copies keep the source actor's scale"),
			nullptr };
		static const TCHAR* const GMifDescKeys_duplicate_asset[] = {
			TEXT("path"), TEXT("newPath"), nullptr };
		static const TCHAR* const GMifDescNotes_duplicate_asset[] = {
			TEXT("confirm"), TEXT("duplicate_asset needs no confirm - it never overwrites; it fails if newPath is already taken"),
			TEXT("newName"), TEXT("there is no newName - put the whole destination in newPath (e.g. /Game/Foo/CopyName)"),
			TEXT("overwrite"), TEXT("NOT supported - duplicate_asset fails rather than clobbering an existing asset; delete_asset the old one first"),
			nullptr };
		static const TCHAR* const GMifDescKeys_edit_container[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("propertyPath"), TEXT("property"), TEXT("operation"), TEXT("action"), TEXT("index"),
			TEXT("at"), TEXT("count"), TEXT("key"), TEXT("newKey"), TEXT("value"), TEXT("swapWith"),
			TEXT("newSize"), TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"), nullptr };
		static const TCHAR* const GMifDescNotes_edit_container[] = {
			TEXT("op"), TEXT("this endpoint's verb is 'operation' (alias 'action'), NOT 'op' - 'op' is batch's routing key and is tolerated centrally, so an endpoint that used it would be un-diagnosable inside batch"),
			nullptr };
		static const TCHAR* const GMifDescKeys_export_asset[] = {
			TEXT("asset"), TEXT("path"), TEXT("assetPath"), TEXT("objectPath"),
			TEXT("file"), TEXT("filename"), TEXT("outPath"),
			TEXT("format"), TEXT("type"), TEXT("extension"),
			TEXT("overwrite"), TEXT("replaceExisting"), TEXT("fbxCompatibility"),
			TEXT("ascii"), TEXT("vertexColor"), TEXT("levelOfDetail"), TEXT("lod"),
			TEXT("collision"), TEXT("exportSourceMesh"), TEXT("forceFrontXAxis"), nullptr };
		static const TCHAR* const GMifDescNotes_export_asset[] = {
			TEXT("destination"), TEXT("export_asset writes to a DISK path, not a /Game folder — spell it file. (destination means a /Game/... content folder in import_asset, and honouring it here would silently write a .fbx into a path that reads like a package.) Omit it entirely to get <ProjectSaved>/MifBridge/Export/<AssetName>.<ext>."),
			TEXT("async"), TEXT("not implemented and deliberately so — this server runs handlers synchronously inside the HTTP ticker. UExporter has no async export; a large mesh makes one long frame, which is legal, and work that SPANS frames is not."),
			TEXT("selected"), TEXT("not implemented — UAssetExportTask::bSelected filters an ACTOR SELECTION for level/object exports; this endpoint exports one named asset and always sends false."),
			TEXT("options"), TEXT("not implemented as a free-form object — the FBX option fields are exposed individually (fbxCompatibility, ascii, vertexColor, levelOfDetail, collision, exportSourceMesh, forceFrontXAxis). No other exporter's option object is wired, and passing a raw object would defeat the type check that keeps the FBX options MODAL shut (EditorExporters.cpp:2129)."),
			TEXT("base64"), TEXT("not supported — export_asset writes a FILE and reports its path and byte size. Read the bytes off disk at the returned `file`."),
			TEXT("batch"), TEXT("not implemented — call once per asset. The FBX SDK instance is created and destroyed per export (EditorExporters.cpp:96-111), so batching inside one call would save nothing. export_asset IS read-only, so the `batch` ENDPOINT can drive several of these in one request."),
			TEXT("save"), TEXT("not a parameter — export_asset writes a disk file and never touches the asset or its package, so there is nothing to save. (It is read-only for exactly that reason.)"),
			TEXT("lodIndex"), TEXT("not implemented — the FBX exporter takes a bool (levelOfDetail: all LODs, or LOD0 only), not an index. Export with levelOfDetail:false for LOD0."),
			nullptr };
		static const TCHAR* const GMifDescKeys_find_assets[] = {
			TEXT("class"), TEXT("className"), TEXT("type"), TEXT("pathPrefix"), TEXT("nameContains"),
			TEXT("origin"), TEXT("recursiveClasses"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_find_assets[] = {
			TEXT("recursive"), TEXT("not implemented - pathPrefix matching is ALWAYS recursive; recursiveClasses controls class-hierarchy matching"),
			nullptr };
		static const TCHAR* const GMifDescKeys_find_nodes[] = {
			TEXT("graphId"), TEXT("byClass"), TEXT("byTitle"), TEXT("byFunction"), nullptr };
		static const TCHAR* const GMifDescNotes_find_nodes[] = {
			TEXT("class"), TEXT("spell it byClass, e.g. byClass:\"K2Node_CallFunction\""),
			TEXT("title"), TEXT("spell it byTitle"),
			TEXT("function"), TEXT("spell it byFunction"),
			TEXT("name"), TEXT("find_nodes has no 'name': use byTitle for the node's displayed title, or byFunction for the name of the function it calls"),
			TEXT("blueprintId"), TEXT("find_nodes searches ONE graph - pass graphId from open_blueprint/list_graphs"),
			nullptr };
		static const TCHAR* const GMifDescKeys_focus_viewport[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("folder"), TEXT("all"), TEXT("instant"), nullptr };
		static const TCHAR* const GMifDescNotes_focus_viewport[] = {
			TEXT("path"), TEXT("the actor key is 'actorPath' (alias: actor); it accepts an object path, an object name or a label"),
			TEXT("name"), TEXT("actorPath already matches on object name and label as well as full path - use it"),
			TEXT("bounds"), TEXT("'bounds' is an OUTPUT field - the framing target is actorPath, folder, or nothing for the whole level"),
			nullptr };
		static const TCHAR* const GMifDescKeys_get_actor_bounds[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_get_actor_bounds[] = {
			TEXT("assetPath"), TEXT("bounds are read from the PLACED actor, not the mesh asset — the asset's ExtendedBounds ignore the actor's scale. Pass the actor as actorPath"),
			TEXT("label"), TEXT("actorPath already accepts a label, an object name or a full path — use it"),
			TEXT("onlyColliding"), TEXT("not a parameter — bounds always include non-colliding components, because editor-world collision is unreliable for imported props"),
			nullptr };
		static const TCHAR* const GMifDescKeys_get_datatable_row[] = {
			TEXT("path"), TEXT("rowName"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"),
			TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_get_dependencies[] = {
			TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_get_inherited_component[] = {
			TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"),
			TEXT("componentName"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescKeys_get_node[] = {
			TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), nullptr };
		static const TCHAR* const GMifDescNotes_get_node[] = {
			TEXT("pin"), TEXT("get_node already returns EVERY pin on the node; there is no pin filter"),
			TEXT("blueprintId"), TEXT("a node is addressed by its guid, not by its blueprint - pass graphId if you need to disambiguate two loaded copies"),
			nullptr };
		static const TCHAR* const GMifDescKeys_get_property[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("propertyPath"), TEXT("property"), nullptr };
		static const TCHAR* const GMifDescKeys_get_referencers[] = {
			TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_get_spline_points[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("componentName"), TEXT("space"),
			nullptr };
		static const TCHAR* const GMifDescNotes_get_spline_points[] = {
			TEXT("index"), TEXT("not supported - get_spline_points returns EVERY point; index into the returned points[] array"),
			TEXT("points"), TEXT("not a parameter of this endpoint - points[] is what it RETURNS; use set_spline_points to write them"),
			nullptr };
		static const TCHAR* const GMifDescKeys_get_viewport_camera[] = { nullptr };
		static const TCHAR* const GMifDescNotes_get_viewport_camera[] = {
			TEXT("viewportIndex"), TEXT("not supported - this always reports the ACTIVE viewport, falling back to the first perspective one; viewportCount in the response says how many exist"),
			nullptr };
		static const TCHAR* const GMifDescKeys_implement_interface_function[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("function"), nullptr };
		static const TCHAR* const GMifDescNotes_implement_interface_function[] = {
			TEXT("name"), TEXT("the interface function is 'function' here; 'name' is remove_function's key"),
			TEXT("interface"), TEXT("not a parameter - the owning interface is looked up from the function name and REPORTED back as interfaceClass. The interface must already be implemented on the blueprint"),
			TEXT("blueprint"), TEXT("the blueprint key is 'blueprintId' (alias: path)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_import_asset[] = {
			TEXT("file"), TEXT("filename"), TEXT("sourcePath"), TEXT("destination"),
			TEXT("destinationPath"), TEXT("path"), TEXT("name"), TEXT("destinationName"), TEXT("factory"),
			TEXT("replaceExisting"), TEXT("overwrite"), TEXT("replaceExistingSettings"), TEXT("save"),
			nullptr };
		static const TCHAR* const GMifDescNotes_import_asset[] = {
			TEXT("async"), TEXT("not implemented and deliberately so — this server runs handlers synchronously inside the HTTP ticker, and UAssetImportTask::GetObjects() BLOCKS on an async import (AssetImportTask.h:78). Imports here always run bAsync:false, one long frame."),
			TEXT("skeletal"), TEXT("not implemented — forcing static-vs-skeletal FBX needs a UFbxImportUI options object wired into the task; today the FBX factory's own detection decides. Import, then adjust, or pass an explicit factory."),
			TEXT("options"), TEXT("not implemented — per-factory option objects (UFbxImportUI etc.) are not exposed yet"),
			TEXT("base64"), TEXT("not supported here — import_asset imports a FILE through a UFactory. For inline image bytes use import_texture {base64, destPath}."),
			nullptr };
		static const TCHAR* const GMifDescKeys_import_texture[] = {
			TEXT("destPath"), TEXT("path"), TEXT("assetPath"), TEXT("sourcePath"), TEXT("file"),
			TEXT("filename"), TEXT("base64"), TEXT("data"), TEXT("bytes"), TEXT("format"),
			TEXT("overwrite"), TEXT("replaceExisting"), TEXT("save"), TEXT("compressionSettings"),
			TEXT("compression"), TEXT("srgb"), TEXT("sRGB"), TEXT("lodGroup"), TEXT("textureGroup"),
			TEXT("neverStream"), TEXT("mipGenSettings"), TEXT("mipGen"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_import_texture[] = {
			TEXT("width"), TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales"),
			TEXT("height"), TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales"),
			TEXT("textureClass"), TEXT("not implemented — import_texture creates UTexture2D only (cubemaps/volumes/render targets are not source-media imports)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_invoke_editor_command[] = {
			TEXT("context"), TEXT("command"), TEXT("menu"), TEXT("section"), TEXT("entry"), TEXT("dryRun"),
			TEXT("confirm"), TEXT("allowKnownModal"), nullptr };
		static const TCHAR* const GMifDescNotes_invoke_editor_command[] = {
			TEXT("commandList"), TEXT("not a parameter — the list is found automatically (cache), or via menu/section/entry"),
			TEXT("key"), TEXT("sending a keystroke is send_editor_key, not this endpoint"),
			nullptr };
		static const TCHAR* const GMifDescKeys_invoke_editor_tab[] = {
			TEXT("tabId"), TEXT("tab"), TEXT("manager"), TEXT("majorTab"), TEXT("asset"), TEXT("probe"),
			TEXT("probeIds"), TEXT("includeKnownIds"), TEXT("asInactive"), nullptr };
		static const TCHAR* const GMifDescNotes_invoke_editor_tab[] = {
			TEXT("command"), TEXT("invoking a bound command is invoke_editor_command"),
			TEXT("close"), TEXT("closing a tab is not implemented — SDockTab::RequestCloseTab can run a third-party OnCanCloseTab that shows a dialog"),
			nullptr };
		static const TCHAR* const GMifDescKeys_landscape_info[] = { nullptr };
		static const TCHAR* const GMifDescNotes_landscape_info[] = {
			TEXT("landscape"), TEXT("not supported - this endpoint always reports EVERY landscape in the editor world; filter the landscapes[] array by actorPath or label"),
			TEXT("limit"), TEXT("not supported - every landscape is reported"),
			nullptr };
		static const TCHAR* const GMifDescKeys_layout_material_expressions[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), nullptr };
		static const TCHAR* const GMifDescKeys_list_animations[] = {
			TEXT("filter"), TEXT("skeleton"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_animations[] = {
			TEXT("nameContains"), TEXT("the substring filter here is 'filter', and it matches the FULL object path, not just the asset name"),
			TEXT("path"), TEXT("there is no path/root parameter - put the folder in 'filter', e.g. filter:'/Game/Anims/'"),
			TEXT("count"), TEXT("'count' is an OUTPUT field - the cap is 'limit' (default 200, max 5000); read 'truncated' to see whether you hit it"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_blueprints[] = {
			TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_list_blueprints[] = {
			TEXT("path"), TEXT("list_blueprints takes no path - pass the path fragment as filter, e.g. filter:\"/Game/Blueprints/\""),
			TEXT("name"), TEXT("matching runs against the FULL object path, so pass the name fragment as filter, e.g. filter:\"BP_Player\""),
			TEXT("limit"), TEXT("there is no limit parameter - the result is capped at 5000 entries; narrow it with filter"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_components[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("component"), TEXT("componentName"),
			TEXT("includeInherited"), TEXT("includeNative"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_list_datatables[] = {
			TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_list_datatables[] = {
			TEXT("path"), TEXT("list_datatables takes filter, a substring of the object path - read_datatable is the one that takes path"),
			TEXT("name"), TEXT("spell it filter - it is matched against the full object path, so a name substring works"),
			TEXT("search"), TEXT("spell it filter"),
			TEXT("limit"), TEXT("list_datatables is uncapped and takes no limit - narrow the result with filter"),
			TEXT("maxRows"), TEXT("this endpoint lists tables, not rows - maxRows belongs to read_datatable"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_dirty_packages[] = {
			TEXT("kind"), nullptr };
		static const TCHAR* const GMifDescKeys_list_dispatchers[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_dispatchers[] = {
			TEXT("graphId"), TEXT("list_dispatchers is blueprint-scoped — pass blueprintId"),
			TEXT("filter"), TEXT("this endpoint takes no filter; it returns every dispatcher on the blueprint"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_editor_commands[] = {
			TEXT("context"), TEXT("command"), TEXT("filter"), TEXT("includeUnbound"),
			TEXT("includeCanExecute"), TEXT("includeConsole"), TEXT("consolePrefix"), TEXT("menu"),
			TEXT("section"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_editor_commands[] = {
			TEXT("tabId"), TEXT("tabs are a different registry — use invoke_editor_tab {probe:true}"),
			TEXT("entry"), TEXT("pass menu (and optionally section); every entry in it is listed"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_enum_values[] = {
			TEXT("enum"), TEXT("enumName"), nullptr };
		static const TCHAR* const GMifDescKeys_list_functions[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_functions[] = {
			TEXT("filter"), TEXT("list_functions has no filter; it returns every function graph"),
			TEXT("class"), TEXT("list_functions reads a BLUEPRINT's own function graphs - to reflect over any class's BlueprintCallable functions use describe_class {class, filter}"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_graphs[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_graphs[] = {
			TEXT("graphId"), TEXT("list_graphs RETURNS graphIds, it does not take one - to read a single graph use list_nodes {graphId}"),
			TEXT("filter"), TEXT("list_graphs has no filter; it returns every graph. find_nodes {graphId, byTitle} searches inside one graph."),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_interfaces[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("includeInherited"), nullptr };
		static const TCHAR* const GMifDescNotes_list_interfaces[] = {
			TEXT("inherited"), TEXT("spell it includeInherited"),
			TEXT("limit"), TEXT("not supported - list_interfaces always returns every implemented interface"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_level_actors[] = {
			TEXT("classFilter"), TEXT("nameContains"), TEXT("folder"), TEXT("selectedOnly"), TEXT("limit"),
			nullptr };
		static const TCHAR* const GMifDescNotes_list_level_actors[] = {
			TEXT("class"), TEXT("the filter key here is 'classFilter' — a substring matched against the whole ancestry, not an exact class path"),
			TEXT("labelContains"), TEXT("use nameContains — it matches the object name AND the Outliner label ('labelContains' is snap_actors_to_ground's key)"),
			TEXT("filter"), TEXT("use nameContains ('filter'/'nameFilter' are the property-listing endpoints' aliases, not this one's)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_material_expressions[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("includeConnections"),
			TEXT("includeProperties"), nullptr };
		static const TCHAR* const GMifDescKeys_list_mounted_containers[] = { nullptr };
		static const TCHAR* const GMifDescKeys_list_nodes[] = {
			TEXT("graphId"), TEXT("hideKnots"), nullptr };
		static const TCHAR* const GMifDescNotes_list_nodes[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("blueprintId"), TEXT("list_nodes reads ONE graph - pass graphId from open_blueprint/list_graphs, not a blueprint path"),
			TEXT("path"), TEXT("this endpoint selects a GRAPH, so pass graphId ('<blueprintPath>::<graphName>'); a bare blueprint path does not name a graph"),
			TEXT("hideReroute"), TEXT("spell it hideKnots (a reroute node is a UK2Node_Knot)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_object_properties[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"),
			nullptr };
		static const TCHAR* const GMifDescNotes_list_object_properties[] = {
			TEXT("propertyPath"), TEXT("list_object_properties dumps ALL top-level properties; get_property reads ONE by dot path, and describe_property reports its flags/metadata/EditCondition"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_pie_actors[] = {
			TEXT("classFilter"), TEXT("nameContains"), TEXT("limit"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescNotes_list_pie_actors[] = {
			TEXT("class"), TEXT("use classFilter — a SUBSTRING matched against the actor's class and every super, not an exact class path"),
			TEXT("world"), TEXT("use netMode (server|client|any) to pick which PIE world answers; the returned 'worlds' array shows what is running"),
			TEXT("actorClass"), TEXT("use classFilter (substring match)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_struct_members[] = {
			TEXT("struct"), TEXT("structPath"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_list_sublevels[] = {
			TEXT("world"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescKeys_list_transactions[] = {
			TEXT("limit"), TEXT("count"), TEXT("max"), TEXT("offset"), TEXT("start"),
			TEXT("includeObjects"), TEXT("include_objects"), nullptr };
		static const TCHAR* const GMifDescKeys_list_variables[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_list_variables[] = {
			TEXT("filter"), TEXT("list_variables has no filter; it returns every member variable"),
			TEXT("scope"), TEXT("list_variables reports member variables only (scope is always \"member\" in the response); a local variable lives on its function graph and is not listed here"),
			TEXT("name"), TEXT("list_variables lists them all - there is no single-variable lookup; read the entry you want out of variables[]"),
			nullptr };
		static const TCHAR* const GMifDescKeys_load_level[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_load_level[] = {
			TEXT("level"), TEXT("use path - 'level' is the sublevel selector on the streaming endpoints; load_level opens a whole map"),
			TEXT("filename"), TEXT("use path with a package path like \"/Game/Maps/MyLevel\" - the .umap filename is derived from it and is never passed in"),
			nullptr };
		static const TCHAR* const GMifDescKeys_move_actor_to[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("location"), nullptr };
		static const TCHAR* const GMifDescNotes_move_actor_to[] = {
			TEXT("path"), TEXT("use actorPath (alias: actor) - this endpoint does not accept the bare 'path' spelling other actor endpoints allow"),
			TEXT("destination"), TEXT("the goal goes in location {x,y,z}"),
			TEXT("acceptanceRadius"), TEXT("not supported - SimpleMoveToLocation uses the engine's default acceptance radius"),
			nullptr };
		static const TCHAR* const GMifDescKeys_move_node[] = {
			TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("x"),
			TEXT("y"), nullptr };
		static const TCHAR* const GMifDescKeys_nav_status[] = { nullptr };
		static const TCHAR* const GMifDescNotes_nav_status[] = {
			TEXT("world"), TEXT("not supported - nav_status always reports the active world (the PIE world while PIE is running); the world it used is echoed back as 'world'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_new_level[] = {
			TEXT("partitioned"), nullptr };
		static const TCHAR* const GMifDescNotes_new_level[] = {
			TEXT("path"), TEXT("new_level does not take a path - it creates an unsaved transient map; pass path to save_level_as afterwards"),
			TEXT("name"), TEXT("new_level does not name the map - the name comes from the path you give save_level_as"),
			nullptr };
		static const TCHAR* const GMifDescKeys_open_asset_editor[] = {
			TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_open_asset_editor[] = {
			TEXT("blueprintId"), TEXT("spell it path"),
			TEXT("asset"), TEXT("spell it path"),
			TEXT("focus"), TEXT("there is no focus - OpenEditorForAsset already brings the editor forward; alreadyOpen in the response says whether it was open before this call"),
			nullptr };

		static const TCHAR* const GMifDescKeys_open_blueprint[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_open_blueprint[] = {
			TEXT("name"), TEXT("open_blueprint addresses the asset by path, e.g. path:\"/Game/Foo/BP_Bar\"; list_blueprints {filter} finds one by a name fragment first"),
			TEXT("graphId"), TEXT("open_blueprint opens a whole blueprint and RETURNS its graphIds; to read one graph use list_nodes {graphId}"),
			nullptr };
		static const TCHAR* const GMifDescKeys_override_inherited_component[] = {
			TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"),
			TEXT("componentName"), TEXT("name"), TEXT("properties"), TEXT("props"), TEXT("confirm"),
			nullptr };
		static const TCHAR* const GMifDescNotes_override_inherited_component[] = {
			TEXT("propertyPath"), TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single dot-path write against the returned overrideTemplatePath"),
			TEXT("value"), TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single named write"),
			nullptr };
		static const TCHAR* const GMifDescKeys_paint_landscape[] = {
			TEXT("landscape"), TEXT("actorPath"), TEXT("layerInfo"), TEXT("layer"), TEXT("info"),
			TEXT("center"), TEXT("radius"), TEXT("weight"), TEXT("falloff"), nullptr };
		static const TCHAR* const GMifDescNotes_paint_landscape[] = {
			TEXT("layerName"), TEXT("pass the LandscapeLayerInfoObject asset path as layerInfo - landscape_info lists the legal ones"),
			TEXT("strength"), TEXT("use weight (0..1)"),
			TEXT("alpha"), TEXT("use weight (0..1)"),
			TEXT("brushSize"), TEXT("use radius (world units)"),
			TEXT("erase"), TEXT("there is no erase mode - weights normalise across layers, so paint a DIFFERENT layer up to push this one down"),
			nullptr };
		static const TCHAR* const GMifDescKeys_pie_load_level_instance[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("location"), TEXT("rotation"),
			TEXT("visible"), TEXT("netMode"), TEXT("nameOverride"), TEXT("tempPackage"), nullptr };
		static const TCHAR* const GMifDescKeys_pie_status[] = { nullptr };
		static const TCHAR* const GMifDescNotes_pie_status[] = {
			TEXT("netMode"), TEXT("this endpoint always reports GEditor->PlayWorld; use list_pie_actors {netMode:server|client|any} to address a specific PIE world"),
			TEXT("waitFor"), TEXT("not supported: nothing can block here without stalling the ticks PIE needs. Call pie_status repeatedly instead"),
			nullptr };
		static const TCHAR* const GMifDescKeys_pie_unload_level_instance[] = {
			TEXT("instanceName"), TEXT("name"), TEXT("path"), TEXT("packagePath"), TEXT("level"),
			TEXT("objectPath"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescKeys_read_datatable[] = {
			TEXT("path"), TEXT("maxRows"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"),
			TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_read_modloader_log[] = {
			TEXT("path"), TEXT("lines"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_read_modloader_log[] = {
			TEXT("logPath"), TEXT("spell it path - or omit it entirely to tail the live DDS2 UE4SS.log"),
			TEXT("file"), TEXT("spell it path"),
			TEXT("maxLines"), TEXT("spell it lines - it is the tail size, clamped to 1-5000"),
			TEXT("limit"), TEXT("spell it lines - it is the tail size, clamped to 1-5000"),
			TEXT("tail"), TEXT("spell it lines - it is the tail size, clamped to 1-5000"),
			TEXT("contains"), TEXT("spell it filter - a plain substring match, not a regex"),
			TEXT("search"), TEXT("spell it filter - a plain substring match, not a regex"),
			nullptr };
		static const TCHAR* const GMifDescKeys_recipe_add_debug_print[] = {
			TEXT("graphId"), TEXT("message"), TEXT("functionName"), TEXT("messageParam"), TEXT("afterNode"),
			TEXT("afterPin"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_add_debug_print[] = {
			TEXT("blueprintId"), TEXT("the print node lands in ONE graph - pass graphId from list_graphs, not the blueprint path"),
			TEXT("text"), TEXT("the printed string is 'message'"),
			TEXT("nodeGuid"), TEXT("the splice anchor is 'afterNode' - this endpoint creates its own node, it does not edit one"),
			nullptr };
		static const TCHAR* const GMifDescKeys_recipe_argmax_over_components[] = {
			TEXT("graphId"), TEXT("loopBodyNode"), TEXT("loopBodyPin"), TEXT("scoreNode"), TEXT("scorePin"),
			TEXT("indexNode"), TEXT("indexPin"), TEXT("bestScoreVar"), TEXT("bestIndexVar"), TEXT("x"),
			TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_argmax_over_components[] = {
			TEXT("node"), TEXT("three DISTINCT nodes are required here - loopBodyNode, scoreNode, indexNode - so there is no generic 'node' alias"),
			TEXT("forEachNode"), TEXT("spelled 'loopBodyNode' here (recipe_reset_and_loop returns that guid as forEachNode)"),
			TEXT("blueprintId"), TEXT("this recipe builds nodes in ONE graph - pass graphId from list_graphs, not the blueprint path"),
			nullptr };
		static const TCHAR* const GMifDescKeys_recipe_reset_and_loop[] = {
			TEXT("graphId"), TEXT("arrayVar"), TEXT("indexVar"), TEXT("scoreVar"), TEXT("indexInit"),
			TEXT("scoreInit"), TEXT("afterNode"), TEXT("afterPin"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_reset_and_loop[] = {
			TEXT("blueprintId"), TEXT("this recipe builds nodes in ONE graph - pass graphId from list_graphs, not the blueprint path"),
			TEXT("array"), TEXT("the array variable NAME is 'arrayVar'"),
			TEXT("index"), TEXT("'indexVar' names the variable; 'indexInit' is the value it is reset to"),
			nullptr };
		static const TCHAR* const GMifDescKeys_recipe_splice_before_parent[] = {
			TEXT("graphId"), TEXT("parentNode"), TEXT("clusterEntry"), TEXT("clusterExit"),
			TEXT("clusterEntryExecIn"), TEXT("clusterExitExecOut"), nullptr };
		static const TCHAR* const GMifDescNotes_recipe_splice_before_parent[] = {
			TEXT("node"), TEXT("three DISTINCT nodes are required here - parentNode, clusterEntry, clusterExit - so there is no generic 'node' alias"),
			TEXT("parentNodeGuid"), TEXT("spelled 'parentNode' on this endpoint (add_override_event RETURNS it as parentNodeGuid)"),
			TEXT("entryNode"), TEXT("spelled 'clusterEntry'"),
			TEXT("exitNode"), TEXT("spelled 'clusterExit'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_recompile_material[] = {
			TEXT("path"), TEXT("material"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescKeys_reconnect_pin[] = {
			TEXT("srcNode"), TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin"), TEXT("dstNode"),
			TEXT("dstPin"), TEXT("destPin"), TEXT("toPin"), TEXT("graphId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_reconnect_pin[] = {
			TEXT("from"), TEXT("spell it srcNode"),
			TEXT("fromNode"), TEXT("spell it srcNode"),
			TEXT("sourceNode"), TEXT("spell it srcNode"),
			TEXT("to"), TEXT("spell it dstNode"),
			TEXT("toNode"), TEXT("spell it dstNode"),
			TEXT("destNode"), TEXT("spell it dstNode"),
			TEXT("targetNode"), TEXT("spell it dstNode"),
			nullptr };
		static const TCHAR* const GMifDescKeys_redo_transactions[] = {
			TEXT("count"), TEXT("n"), TEXT("steps"), TEXT("toIndex"), TEXT("to_index"), nullptr };
		static const TCHAR* const GMifDescKeys_refresh_node[] = {
			TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), nullptr };
		static const TCHAR* const GMifDescKeys_reimport_asset[] = {
			TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("sourceFile"), TEXT("file"),
			TEXT("newFile"), TEXT("sourceFileIndex"), TEXT("forceNewFile"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_reimport_asset[] = {
			TEXT("askForNewFileIfMissing"), TEXT("not settable — it would open a file-picker MODAL, which freezes the editor and this bridge with it. Pass sourceFile instead."),
			TEXT("showNotification"), TEXT("not settable — always false; the response IS the notification"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_collision[] = {
			TEXT("path"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_collision[] = {
			TEXT("objectPath"), TEXT("spell it path"),
			TEXT("mesh"), TEXT("spell it path"),
			TEXT("shape"), TEXT("remove_collision takes no shape - it clears ALL simple collision. Use add_simplified_collision to add one back"),
			nullptr };

		static const TCHAR* const GMifDescKeys_remove_component[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_component[] = {
			TEXT("component"), TEXT("spell it name here - list_components takes 'component', remove_component takes 'name'"),
			TEXT("componentName"), TEXT("spell it name"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_enum_value[] = {
			TEXT("enum"), TEXT("enumPath"), TEXT("path"), TEXT("index"), TEXT("value"), TEXT("name"),
			TEXT("displayName"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_enum_value[] = {
			TEXT("guid"), TEXT("enum entries are addressed by index or by value/display name, never by guid - guid is remove_struct_member's parameter"),
			TEXT("values"), TEXT("remove_enum_value removes ONE entry; pass value:\"Ready\" or index:2"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_function[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_function[] = {
			TEXT("function"), TEXT("this endpoint's key is 'name'; 'function' is implement_interface_function's key"),
			TEXT("force"), TEXT("the required acknowledgement is confirm:true"),
			TEXT("graphId"), TEXT("remove_function matches the function graph by NAME on the given blueprint - it does not take a graphId"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_interface[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("interface"), TEXT("interfaceClass"), TEXT("class"),
			TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_interface[] = {
			TEXT("preserveFunctions"), TEXT("not supported - remove_interface always removes the interface's functions with it (bPreserveFunctions=false)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_node[] = {
			TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("confirm"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_pin[] = {
			TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"),
			TEXT("pinName"), TEXT("name"), TEXT("direction"), TEXT("dir"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_struct_member[] = {
			TEXT("struct"), TEXT("structPath"), TEXT("path"), TEXT("name"), TEXT("guid"), TEXT("confirm"),
			nullptr };
		static const TCHAR* const GMifDescNotes_remove_struct_member[] = {
			TEXT("member"), TEXT("the member is addressed by name or by guid"),
			TEXT("memberName"), TEXT("the member name parameter is called name"),
			TEXT("index"), TEXT("struct members are addressed by name or guid, never by index - index is remove_enum_value's parameter"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_sublevel[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("discardUnsaved"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_tree_widget[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_tree_widget[] = {
			TEXT("name"), TEXT("the widget parameter is called widgetName"),
			TEXT("widget"), TEXT("spell it widgetName"),
			TEXT("recursive"), TEXT("not a parameter — RemoveWidget always takes the widget's whole subtree with it"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_variable[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_widget_binding[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyName"), nullptr };
		static const TCHAR* const GMifDescNotes_remove_widget_binding[] = {
			TEXT("functionName"), TEXT("not part of the identity — a binding is removed by widgetName + propertyName alone, whatever function it points at"),
			TEXT("property"), TEXT("spell it propertyName"),
			TEXT("widget"), TEXT("spell it widgetName"),
			nullptr };
		static const TCHAR* const GMifDescKeys_rename_asset[] = {
			TEXT("path"), TEXT("newPath"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_asset[] = {
			TEXT("newName"), TEXT("there is no newName - put the whole destination in newPath (e.g. /Game/Foo/NewName); its last segment becomes the new asset name"),
			TEXT("newPackageName"), TEXT("spell it newPath - newPackageName is a RESPONSE field only"),
			TEXT("destination"), TEXT("spell it newPath"),
			nullptr };
		static const TCHAR* const GMifDescKeys_rename_event[] = {
			TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("newName"),
			TEXT("name"), TEXT("to"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_event[] = {
			TEXT("oldName"), TEXT("rename_event addresses the event by nodeGuid, not by its current name - only the new name is passed (newName, aliases: name, to)"),
			TEXT("from"), TEXT("rename_event addresses the event by nodeGuid; the destination is newName (aliases: name, to)"),
			TEXT("event"), TEXT("address the custom event by nodeGuid (aliases: node, guid, nodeId) - find_nodes locates it"),
			TEXT("blueprintId"), TEXT("the owning blueprint is inferred from the node; pass graphId if the same guid exists in more than one loaded copy"),
			nullptr };
		static const TCHAR* const GMifDescKeys_rename_event_dispatcher[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("name"), TEXT("dispatcher"),
			TEXT("newName"), TEXT("to"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_event_dispatcher[] = {
			TEXT("from"), TEXT("the current name is oldName (aliases: name, dispatcher) - only the destination has a short spelling ('to' = newName)"),
			TEXT("graphId"), TEXT("a dispatcher is a signature GRAPH plus a backing delegate VARIABLE - it is addressed by blueprintId + oldName so both halves can be renamed together"),
			nullptr };
		static const TCHAR* const GMifDescKeys_rename_function[] = {
			TEXT("graphId"), TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("function"),
			TEXT("name"), TEXT("newName"), TEXT("to"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_function[] = {
			TEXT("from"), TEXT("the current name is oldName (aliases: function, name) - only the destination has a short spelling ('to' = newName)"),
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("newFunctionName"), TEXT("spell it newName (alias: to)"),
			TEXT("dispatcher"), TEXT("an event dispatcher is a signature graph PLUS a backing delegate variable - use rename_event_dispatcher, which renames both"),
			nullptr };
		static const TCHAR* const GMifDescKeys_rename_variable[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("newName"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_variable[] = {
			TEXT("name"), TEXT("rename_variable needs BOTH oldName and newName; there is no single 'name'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_render_thumbnail[] = {
			TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"),
			TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"), TEXT("flushTextures"), TEXT("alpha"),
			TEXT("name"), nullptr };
		static const TCHAR* const GMifDescKeys_reset_property_to_default[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("propertyPath"), TEXT("property"), TEXT("force"), TEXT("allowEditConst"),
			TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"), nullptr };
		static const TCHAR* const GMifDescKeys_resolve_struct[] = {
			TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_resolve_struct[] = {
			TEXT("structName"), TEXT("resolve_struct spells it name; structName is what add_make_struct/add_break_struct use"),
			TEXT("struct"), TEXT("spell it name"),
			TEXT("path"), TEXT("pass the path as the VALUE of name - name accepts a bare name or a full struct path in the same field"),
			nullptr };
		static const TCHAR* const GMifDescKeys_revert_inherited_component[] = {
			TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"),
			TEXT("componentName"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_run_console[] = {
			TEXT("command"), TEXT("cmd"), TEXT("world"), TEXT("captureOutput"), nullptr };
		static const TCHAR* const GMifDescNotes_run_console[] = {
			TEXT("filter"), TEXT("log-line filtering belongs to run_console_captured, which brackets GLog; this endpoint returns the command's own output device text"),
			nullptr };
		static const TCHAR* const GMifDescKeys_run_console_captured[] = {
			TEXT("command"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_run_console_captured[] = {
			TEXT("cmd"), TEXT("use command — the 'cmd' alias exists only on run_console, not here"),
			TEXT("world"), TEXT("not selectable here: this endpoint runs against the PIE world when playing and the editor world otherwise. Use run_console {world:editor|pie|active} to choose"),
			TEXT("captureOutput"), TEXT("capture is unconditional here — that is what this endpoint is for; run_console has the toggle"),
			nullptr };
		static const TCHAR* const GMifDescKeys_save_blueprint[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_save_blueprint[] = {
			TEXT("savePath"), TEXT("save_blueprint has no save-as: it rewrites the blueprint's OWN package. To save a different asset use save_package {path}."),
			TEXT("compile"), TEXT("save_blueprint does not compile - call compile {blueprintId} first if the blueprint has pending structural changes"),
			nullptr };
		static const TCHAR* const GMifDescKeys_save_dirty_packages[] = {
			TEXT("maps"), TEXT("saveMaps"), TEXT("save_maps"), TEXT("content"), TEXT("saveContent"),
			TEXT("save_content"), TEXT("dryRun"), TEXT("dry_run"), nullptr };
		static const TCHAR* const GMifDescKeys_save_level_as[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("assetPath"), nullptr };
		static const TCHAR* const GMifDescNotes_save_level_as[] = {
			TEXT("level"), TEXT("use path - 'level' is the sublevel selector on the streaming endpoints; save_level_as always saves the OPEN persistent level"),
			TEXT("filename"), TEXT("use path with a package path like \"/Game/Maps/MyLevel\" - the .umap filename is derived from it and is never passed in"),
			nullptr };
		static const TCHAR* const GMifDescKeys_save_package[] = {
			TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_save_package[] = {
			TEXT("blueprintId"), TEXT("save_package addresses any asset by its /Game/ object path, so pass it as path. For a Blueprint, save_blueprint {blueprintId} does the same thing."),
			TEXT("package"), TEXT("pass the ASSET's object path as path (e.g. /Game/Data/DT_Items) - the owning package is derived from it"),
			TEXT("assetPath"), TEXT("spell it path"),
			nullptr };
		static const TCHAR* const GMifDescKeys_scene_report[] = {
			TEXT("groundZ"), TEXT("floatTolerance"), TEXT("tallWarnZ"), nullptr };
		static const TCHAR* const GMifDescNotes_scene_report[] = {
			TEXT("tolerance"), TEXT("the float/sunken threshold here is 'floatTolerance' (default 30) — 'tolerance' is check_overlaps' overlap-depth threshold"),
			TEXT("nameContains"), TEXT("not supported — scene_report always scans the whole world; filter its floating/sunken/tooTall arrays caller-side, or use check_overlaps which does take nameContains"),
			TEXT("actorPath"), TEXT("scene_report is whole-scene by design; for one actor use get_actor_bounds, or check_overlaps with actorPath"),
			nullptr };
		static const TCHAR* const GMifDescKeys_sculpt_landscape[] = {
			TEXT("landscape"), TEXT("actorPath"), TEXT("center"), TEXT("radius"), TEXT("mode"),
			TEXT("amount"), TEXT("falloff"), TEXT("targetZ"), nullptr };
		static const TCHAR* const GMifDescNotes_sculpt_landscape[] = {
			TEXT("strength"), TEXT("use amount (world units) with mode raise/lower"),
			TEXT("height"), TEXT("use targetZ (a world Z) with mode flatten, or amount with mode raise/lower"),
			TEXT("brushSize"), TEXT("use radius (world units)"),
			TEXT("target"), TEXT("use targetZ - it is a world Z, not a vertex height"),
			TEXT("z"), TEXT("center is an object - pass center:{x,y}; a flatten target is targetZ"),
			nullptr };
		static const TCHAR* const GMifDescKeys_select_level_actors[] = {
			TEXT("actorPaths"), TEXT("clear"), nullptr };
		static const TCHAR* const GMifDescNotes_select_level_actors[] = {
			TEXT("actorPath"), TEXT("the key here is the PLURAL 'actorPaths' and it takes an array — pass [path] for a single actor"),
			TEXT("actors"), TEXT("the key is 'actorPaths'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_send_editor_key[] = {
			TEXT("key"), TEXT("confirm"), TEXT("dryRun"), TEXT("modifiers"), TEXT("userIndex"),
			TEXT("isRepeat"), TEXT("characterCode"), TEXT("keyCode"), TEXT("sendKeyUp"), nullptr };
		static const TCHAR* const GMifDescNotes_send_editor_key[] = {
			TEXT("text"), TEXT("typing a string is not implemented — ProcessKeyCharEvent per character goes into whatever currently has focus, which is unbounded; see the Batch O notes in docs/audit/06_IMPLEMENTED.md"),
			TEXT("ctrl"), TEXT("modifiers go in the modifiers object: modifiers:{ctrl:true}"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_actor_label[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_set_actor_label[] = {
			TEXT("name"), TEXT("the World Outliner display name is 'label'; the object name is engine-assigned and is not renamed here"),
			TEXT("newLabel"), TEXT("the key is 'label'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_actor_transform[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("location"), TEXT("rotation"),
			TEXT("scale"), TEXT("relative"), nullptr };
		static const TCHAR* const GMifDescNotes_set_actor_transform[] = {
			TEXT("transform"), TEXT("pass location / rotation / scale as separate keys"),
			TEXT("yaw"), TEXT("rotation accepts {pitch,yaw,roll} or {x,y,z} — there is no bare yaw here"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_asset_thumbnail[] = {
			TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"),
			TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"), TEXT("flushTextures"), TEXT("save"),
			nullptr };
		static const TCHAR* const GMifDescNotes_set_asset_thumbnail[] = {
			TEXT("texturePath"), TEXT("this endpoint sets the asset's own Content Browser icon and writes no texture asset — use write_thumbnail_texture for that"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_component_transform[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("location"), TEXT("rotation"),
			TEXT("scale"), nullptr };
		static const TCHAR* const GMifDescNotes_set_component_transform[] = {
			TEXT("component"), TEXT("spell it name here - list_components takes 'component', set_component_transform takes 'name'"),
			TEXT("componentName"), TEXT("spell it name"),
			TEXT("relativeLocation"), TEXT("spell it location - the transform written here is already the RELATIVE one"),
			TEXT("transform"), TEXT("pass location / rotation / scale as separate keys; there is no combined transform key"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_current_sublevel[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), nullptr };
		static const TCHAR* const GMifDescKeys_set_function_flags[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("graphId"), TEXT("function"), TEXT("functionName"),
			TEXT("name"), TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("replicates"),
			TEXT("reliable"), TEXT("access"), TEXT("pure"), TEXT("const"), TEXT("isConst"),
			TEXT("callInEditor"), TEXT("category"), TEXT("tooltip"), TEXT("keywords"), nullptr };
		static const TCHAR* const GMifDescNotes_set_function_flags[] = {
			TEXT("replication"), TEXT("spell it replicates (none | multicast | server | client)"),
			TEXT("net"), TEXT("read-only in the response - set the mode with replicates; FUNC_Net is derived from it and cannot be set on its own"),
			TEXT("static"), TEXT("read-only in the response - a Blueprint function's static-ness is not editable here"),
			TEXT("authorityOnly"), TEXT("read-only in the response - not settable through this endpoint"),
			TEXT("flags"), TEXT("pass each flag as a TOP-LEVEL key (replicates, reliable, access, pure, const, callInEditor, category, tooltip, keywords); the response's 'flags' object is read-back only"),
			TEXT("event"), TEXT("address a custom event by nodeGuid (aliases: node, guid, nodeId); a function graph by graphId or blueprintId + function"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_material_parameter[] = {
			TEXT("material"), TEXT("materialPath"), TEXT("path"), TEXT("scalars"), TEXT("vectors"),
			TEXT("parameter"), TEXT("parameterName"), TEXT("name"), TEXT("value"), nullptr };
		static const TCHAR* const GMifDescNotes_set_material_parameter[] = {
			TEXT("textures"), TEXT("texture parameters are NOT implemented on this endpoint — it applies scalars and vectors only"),
			TEXT("texture"), TEXT("texture parameters are NOT implemented on this endpoint — it applies scalars and vectors only"),
			TEXT("switches"), TEXT("static switch parameters are NOT implemented on this endpoint — they need a static-permutation update"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_pin_default[] = {
			TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"),
			TEXT("pinName"), TEXT("name"), TEXT("value"), TEXT("default"), TEXT("defaultValue"), nullptr };
		static const TCHAR* const GMifDescKeys_set_pin_type[] = {
			TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("pin"),
			TEXT("pinName"), TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"), nullptr };
		static const TCHAR* const GMifDescKeys_set_property[] = {
			TEXT("objectPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyPath"),
			TEXT("value"), TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"),
			TEXT("enforceClamps"), TEXT("clamp"), TEXT("respectClamps"), nullptr };
		static const TCHAR* const GMifDescNotes_set_property[] = {
			TEXT("actorPath"), TEXT("use objectPath - a placed actor's path IS an objectPath"),
			TEXT("format"), TEXT("no output format switch here; the response always carries BOTH valueAfter (export text) and typed (typed JSON)"),
			TEXT("verify"), TEXT("not optional - every write is verified by re-export, which is what makes ok:true mean written"),
			TEXT("operation"), TEXT("set_property writes a VALUE; add/insert/remove/clear/swap/resize/setKey on a container are edit_container"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_spline_points[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("component"), TEXT("componentName"), TEXT("points"),
			TEXT("space"), TEXT("pointType"), TEXT("closedLoop"), TEXT("closed"), TEXT("loop"),
			TEXT("snapToGround"), TEXT("groundOffset"), nullptr };
		static const TCHAR* const GMifDescNotes_set_spline_points[] = {
			TEXT("offset"), TEXT("use groundOffset - 'offset' is snap_actors_to_ground's name for the same idea"),
			TEXT("type"), TEXT("use pointType - it sets the interpolation type of every point written by this call"),
			TEXT("tangents"), TEXT("not implemented - set_spline_points writes point LOCATIONS only; pointType:\"curveCustomTangent\" is accepted but the tangents themselves cannot be supplied here"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_sublevel_streaming[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_sublevel_visibility[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("visible"), TEXT("editorVisible"),
			TEXT("shouldBeLoaded"), TEXT("shouldBeVisible"), TEXT("lightingScenario"), nullptr };
		static const TCHAR* const GMifDescKeys_set_texture_settings[] = {
			TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("texturePath"),
			TEXT("compressionSettings"), TEXT("compression"), TEXT("srgb"), TEXT("sRGB"), TEXT("lodGroup"),
			TEXT("textureGroup"), TEXT("neverStream"), TEXT("mipGenSettings"), TEXT("mipGen"),
			TEXT("filter"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_set_texture_settings[] = {
			TEXT("addressX"), TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set"),
			TEXT("addressY"), TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set"),
			TEXT("maxTextureSize"), TEXT("not implemented — use set_property on MaxTextureSize"),
			TEXT("lodBias"), TEXT("not implemented — use set_property on LODBias"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_variable_default[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("value"), TEXT("default"),
			TEXT("defaultValue"), nullptr };
		static const TCHAR* const GMifDescKeys_set_cast_purity[] = {
			TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("pure"), nullptr };
		static const TCHAR* const GMifDescNotes_set_cast_purity[] = {
			TEXT("bIsPureCast"), TEXT("pass pure:true|false - writing bIsPureCast with set_property flips the flag but does NOT reallocate the exec pins, leaving flag and pins disagreeing"),
			TEXT("impure"), TEXT("spell it pure:false"),
			TEXT("targetClass"), TEXT("this endpoint only changes purity; to cast to a different class place a new node with add_cast"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_variable_type[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("type"), TEXT("container"),
			TEXT("valueType"), TEXT("scope"), TEXT("function"), nullptr };
		static const TCHAR* const GMifDescNotes_set_variable_type[] = {
			TEXT("class"), TEXT("the class belongs IN the type string: type:\"object:BP_Foo_C\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X"),
			TEXT("newType"), TEXT("spell it type"),
			TEXT("targetClass"), TEXT("that is retarget_variable_node's key (repoint a NODE at another declaring class); to change the TYPE use type:\"object:X\""),
			TEXT("node"), TEXT("set_variable_type retypes the VARIABLE declaration; to repoint one node use retarget_variable_node"),
			nullptr };
		static const TCHAR* const GMifDescKeys_retarget_variable_node[] = {
			TEXT("graphId"), TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"),
			TEXT("targetClass"), TEXT("class"), TEXT("self"), nullptr };
		static const TCHAR* const GMifDescNotes_retarget_variable_node[] = {
			TEXT("type"), TEXT("this changes WHICH CLASS declares the variable, not the pin type - use set_variable_type for the type"),
			TEXT("var"), TEXT("the variable name comes from the node you name; to place a NEW node use add_variable_get/add_variable_set with targetClass"),
			TEXT("pin"), TEXT("there is no pin argument - the whole node's FMemberReference is repointed and the node is reconstructed"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_variable_flags[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("var"), TEXT("variable"),
			TEXT("replicated"), TEXT("repNotify"), TEXT("repNotifyFunction"), TEXT("replicationCondition"),
			TEXT("saveGame"), TEXT("transient"), TEXT("config"), TEXT("instanceEditable"),
			TEXT("blueprintReadOnly"), TEXT("exposeOnSpawn"), TEXT("advancedDisplay"), TEXT("interp"),
			TEXT("deprecated"), TEXT("category"), TEXT("tooltip"), nullptr };
		static const TCHAR* const GMifDescNotes_set_variable_flags[] = {
			TEXT("variableName"), TEXT("spell it name (aliases: var, variable)"),
			TEXT("replicate"), TEXT("spell it replicated - and repNotify:true already implies it"),
			TEXT("editable"), TEXT("spell it instanceEditable (the Details-panel \"Instance Editable\" checkbox)"),
			TEXT("readOnly"), TEXT("spell it blueprintReadOnly"),
			TEXT("condition"), TEXT("spell it replicationCondition - an ELifetimeCondition such as COND_OwnerOnly; the COND_ prefix is optional"),
			TEXT("onRep"), TEXT("spell it repNotifyFunction; omit it and repNotify:true mints OnRep_<Name> for you"),
			TEXT("default"), TEXT("set_variable_flags only sets flags - use set_variable_default {blueprintId, name, value} to change a variable's default"),
			TEXT("type"), TEXT("set_variable_flags cannot retype a variable; the type is fixed at add_variable {type:\"object:X\"} time"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_viewport_camera[] = {
			TEXT("location"), TEXT("rotation"), TEXT("lookAt"), TEXT("fov"), TEXT("ortho"),
			TEXT("orthoZoom"), nullptr };
		static const TCHAR* const GMifDescNotes_set_viewport_camera[] = {
			TEXT("x"), TEXT("there is no top-level x/y/z here - pass location:{x,y,z}; rotation and lookAt take the same nested form. capture_camera is the endpoint that also accepts the flat form"),
			TEXT("zoom"), TEXT("the key is 'orthoZoom', and it only has an effect on an orthographic view - set ortho first"),
			TEXT("orthographic"), TEXT("the key is 'ortho' and it takes a STRING: top/bottom/front/back/left/right/perspective"),
			TEXT("actorPath"), TEXT("this endpoint sets an explicit transform - to frame an actor use focus_viewport, which takes actorPath"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_widget_is_variable[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("isVariable"), nullptr };
		static const TCHAR* const GMifDescNotes_set_widget_is_variable[] = {
			TEXT("name"), TEXT("the widget parameter is called widgetName — the widget's FName in the tree, not its display label"),
			TEXT("widget"), TEXT("spell it widgetName"),
			TEXT("variableName"), TEXT("not settable here — the generated member variable is ALWAYS named after the widget itself; rename the widget to rename the variable"),
			nullptr };
		static const TCHAR* const GMifDescKeys_shader_compile_status[] = { nullptr };
		static const TCHAR* const GMifDescKeys_snap_actors_to_ground[] = {
			TEXT("actorPaths"), TEXT("folder"), TEXT("labelContains"), TEXT("all"), TEXT("offset"),
			TEXT("traceHeight"), TEXT("alignToNormal"), TEXT("groundActor"), TEXT("ground"),
			TEXT("allowAnyHit"), nullptr };
		static const TCHAR* const GMifDescNotes_snap_actors_to_ground[] = {
			TEXT("actorPath"), TEXT("use actorPaths:[...] - this endpoint snaps a SET, so the parameter is plural even for a single actor"),
			TEXT("groundOffset"), TEXT("use offset - 'groundOffset' is set_spline_points' name for the same idea"),
			TEXT("snapToGround"), TEXT("not a parameter - snapping IS what this endpoint does; choose the actors with actorPaths[], folder, labelContains or all:true"),
			nullptr };
		static const TCHAR* const GMifDescKeys_spawn_actor_in_level[] = {
			TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"),
			TEXT("mesh"), TEXT("staticMesh"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_actor_in_level[] = {
			TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials"),
			TEXT("name"), TEXT("an actor's display name is 'label'; its object name is assigned by the engine"),
			nullptr };
		static const TCHAR* const GMifDescKeys_spawn_actor_in_pie[] = {
			TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"),
			TEXT("mesh"), TEXT("staticMesh"), TEXT("label"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_actor_in_pie[] = {
			TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials"),
			TEXT("folder"), TEXT("folders are an editor-outliner concept; a PIE-spawned actor has none"),
			nullptr };
		static const TCHAR* const GMifDescKeys_spawn_many[] = {
			TEXT("items"), TEXT("actorClass"), TEXT("mesh"), TEXT("material"), TEXT("folder"),
			TEXT("labelPrefix"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_many[] = {
			TEXT("count"), TEXT("spawn_many places one actor per items[] entry — repeat the entry, or use duplicate_actors with count"),
			TEXT("actors"), TEXT("the array parameter is called items[]"),
			nullptr };
		static const TCHAR* const GMifDescKeys_splice_into_exec[] = {
			TEXT("afterNode"), TEXT("insertNode"), TEXT("graphId"), TEXT("afterPin"), TEXT("afterExecOut"),
			TEXT("insertExecIn"), TEXT("insertIn"), TEXT("execIn"), TEXT("insertExecOut"),
			TEXT("insertOut"), TEXT("execOut"), nullptr };
		static const TCHAR* const GMifDescNotes_splice_into_exec[] = {
			TEXT("beforeNode"), TEXT("splice_into_exec inserts AFTER a node — pass afterNode"),
			TEXT("node"), TEXT("this endpoint needs BOTH afterNode and insertNode; there is no single 'node'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_start_pie[] = {
			TEXT("simulate"), TEXT("startLocation"), TEXT("startRotation"), TEXT("players"),
			TEXT("netMode"), TEXT("oneProcess"), TEXT("width"), TEXT("height"), nullptr };
		static const TCHAR* const GMifDescNotes_start_pie[] = {
			TEXT("location"), TEXT("use startLocation — and note startRotation is only read when startLocation is supplied too"),
			TEXT("rotation"), TEXT("use startRotation; it is only read when startLocation is supplied as well"),
			TEXT("clients"), TEXT("use players (clamped to 1-8)"),
			TEXT("level"), TEXT("PIE plays whatever level is already open — call load_level first, then start_pie"),
			TEXT("map"), TEXT("PIE plays whatever level is already open — call load_level first, then start_pie"),
			TEXT("wait"), TEXT("not supported: this handler runs on the game thread, so waiting for PIE would deadlock the ticks that start it. Poll pie_status until state=='running'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_stop_pie[] = { nullptr };
		static const TCHAR* const GMifDescNotes_stop_pie[] = {
			TEXT("wait"), TEXT("not supported: the stop is deferred to the next editor tick and this handler holds the game thread. Poll pie_status until state=='stopped'"),
			TEXT("force"), TEXT("not supported: RequestEndPlayMap is the only safe teardown from inside the ticker; EndPlayMap here would tear the world down under this callstack"),
			nullptr };
		static const TCHAR* const GMifDescKeys_thumbnail_capabilities[] = {
			TEXT("asset"), TEXT("assetPath"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_trace_ground[] = {
			TEXT("x"), TEXT("y"), TEXT("fromZ"), TEXT("toZ"), TEXT("location"), TEXT("ignoreActor"),
			TEXT("actorPath"), nullptr };
		static const TCHAR* const GMifDescNotes_trace_ground[] = {
			TEXT("z"), TEXT("there is no top-level z — the trace START is 'fromZ' (default 100000) and the END is 'toZ' (default -100000). location:{x,y,z} also seeds fromZ from its z"),
			TEXT("ignore"), TEXT("the key is 'ignoreActor' (alias: actorPath); it accepts an object path, object name or label"),
			TEXT("channel"), TEXT("not a parameter — this always traces ECC_WorldStatic with complex collision"),
			nullptr };
		static const TCHAR* const GMifDescKeys_trigger_cook[] = {
			TEXT("mod"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescNotes_trigger_cook[] = {
			TEXT("modName"), TEXT("spell it mod"),
			TEXT("assetPath"), TEXT("spell it asset - it is substituted into the retoc --filter argument"),
			TEXT("path"), TEXT("spell it asset - it is substituted into the retoc --filter argument"),
			TEXT("confirm"), TEXT("trigger_cook is plan-only and runs nothing, so there is nothing to confirm"),
			TEXT("execute"), TEXT("trigger_cook is plan-only by design - run the returned plan yourself, out-of-editor"),
			nullptr };
		static const TCHAR* const GMifDescKeys_undo_transactions[] = {
			TEXT("count"), TEXT("n"), TEXT("steps"), TEXT("toIndex"), TEXT("to_index"), TEXT("allowRedo"),
			TEXT("allow_redo"), TEXT("canRedo"), nullptr };
		static const TCHAR* const GMifDescKeys_validate[] = {
			TEXT("blueprintId"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescNotes_validate[] = {
			TEXT("dryRun"), TEXT("validate is ALWAYS a dry run and reports dryRun:true in the response; it is not an input"),
			TEXT("save"), TEXT("validate never writes to disk - run save_blueprint {blueprintId} once the compile is clean"),
			nullptr };
		static const TCHAR* const GMifDescKeys_write_datatable_rows[] = {
			TEXT("path"), TEXT("rows"), TEXT("replace"), TEXT("confirm"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_write_thumbnail_texture[] = {
			TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("texturePath"), TEXT("outputPath"),
			TEXT("width"), TEXT("height"), TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"),
			TEXT("flushTextures"), TEXT("alpha"), TEXT("srgb"), TEXT("compression"), TEXT("lodGroup"),
			TEXT("generateMips"), TEXT("overwrite"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_write_thumbnail_texture[] = {
			TEXT("name"), TEXT("render_thumbnail names a PNG file; this endpoint names an ASSET — use texturePath"),
			nullptr };

		static const FMifDescribeRow GMifDescribeRows[] = {
			{ TEXT("describe_endpoint"), GMifDescKeys_describe_endpoint, GMifDescNotes_describe_endpoint,
			  MIF_DESCRIBE_OWN_SUMMARY,
			  TEXT("MifBridgeDescribe.cpp"), 0, nullptr },   // line 0 = "this file, see H_describe_endpoint"
			{ TEXT("add_bind_dispatcher"), GMifDescKeys_add_bind_dispatcher, GMifDescNotes_add_bind_dispatcher,
			  TEXT("graphId, dispatcher, targetClass (optional — bind/call a dispatcher declared on that EXTERNAL class instead of this blueprint's own), x, y"),
			  TEXT("MifBridgeDelegates.cpp"), 43, TEXT("SpawnDelegateNode") },
			{ TEXT("add_branch"), GMifDescKeys_add_branch, GMifDescNotes_add_branch,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes.cpp"), 804, nullptr },
			{ TEXT("add_break_struct"), GMifDescKeys_add_break_struct, GMifDescNotes_add_break_struct,
			  TEXT("graphId, structName, x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 245, nullptr },
			{ TEXT("add_call_dispatcher"), GMifDescKeys_add_call_dispatcher, GMifDescNotes_add_call_dispatcher,
			  TEXT("graphId, dispatcher, targetClass (optional — bind/call a dispatcher declared on that EXTERNAL class instead of this blueprint's own), x, y"),
			  TEXT("MifBridgeDelegates.cpp"), 43, TEXT("SpawnDelegateNode") },
			{ TEXT("add_cast"), GMifDescKeys_add_cast, GMifDescNotes_add_cast,
			  TEXT("graphId, targetClass (aliases: class, cls, className, castTo, to, targetType), pure? (default false - true makes a data-only cast with no exec pins), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1099, nullptr },
			{ TEXT("set_cast_purity"), GMifDescKeys_set_cast_purity, GMifDescNotes_set_cast_purity,
			  TEXT("graphId?, node (aliases: nodeGuid, guid, nodeId), pure - converts an EXISTING cast between pure and impure, reallocating its exec pins"),
			  TEXT("MifBridgeNodes.cpp"), 1250, nullptr },
			{ TEXT("add_class_cast"), GMifDescKeys_add_class_cast, GMifDescNotes_add_class_cast,
			  TEXT("graphId, targetClass (aliases: class, castTo, to, targetType), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 232, nullptr },
			{ TEXT("add_comment"), GMifDescKeys_add_comment, GMifDescNotes_add_comment,
			  TEXT("graphId, x, y, width (default 400, min 32), height (default 150, min 32), text (the comment body)"),
			  TEXT("MifBridgeNodes4.cpp"), 402, nullptr },
			{ TEXT("add_component"), GMifDescKeys_add_component, GMifDescNotes_add_component,
			  TEXT("blueprintId (alias: path), componentClass (alias: class), name (optional - the new component's variable name), parentName (an EXISTING component to attach under), location, rotation, scale"),
			  TEXT("MifBridgeComponents.cpp"), 280, nullptr },
			{ TEXT("add_create_widget"), GMifDescKeys_add_create_widget, GMifDescNotes_add_create_widget,
			  TEXT("graphId, widgetClass (alias: class), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 134, nullptr },
			{ TEXT("add_custom_event"), GMifDescKeys_add_custom_event, GMifDescNotes_add_custom_event,
			  TEXT("graphId, name, inputs? ([{name, type, container?, valueType?}] - the event's parameters), x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 142, nullptr },
			{ TEXT("add_enhanced_input_action"), GMifDescKeys_add_enhanced_input_action, GMifDescNotes_add_enhanced_input_action,
			  TEXT("graphId, inputAction (aliases: action, actionPath) - the UInputAction asset path, x, y"),
			  TEXT("MifBridgeNodes7.cpp"), 22, nullptr },
			{ TEXT("add_enum_literal"), GMifDescKeys_add_enum_literal, GMifDescNotes_add_enum_literal,
			  TEXT("graphId, enumName, value? (the enumerator NAME, e.g. \"NewEnumerator0\"), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 466, nullptr },
			{ TEXT("add_enum_value"), GMifDescKeys_add_enum_value, GMifDescNotes_add_enum_value,
			  TEXT("enum (aliases: enumPath, path), value (aliases: name, displayName) - the display name of the one new entry"),
			  TEXT("MifBridgeUserTypes.cpp"), 574, nullptr },
			{ TEXT("add_event_dispatcher"), GMifDescKeys_add_event_dispatcher, GMifDescNotes_add_event_dispatcher,
			  TEXT("blueprintId (alias: path), name, inputs (array of {name, type, container?, valueType?} — the dispatcher's signature parameters)"),
			  TEXT("MifBridgeDelegates.cpp"), 111, nullptr },
			{ TEXT("add_foliage_instances"), GMifDescKeys_add_foliage_instances, GMifDescNotes_add_foliage_instances,
			  TEXT("mesh (alias: staticMesh), instances[] (required), label, folder"),
			  TEXT("MifBridgeAuthoring.cpp"), 806, nullptr },
			{ TEXT("add_format_text"), GMifDescKeys_add_format_text, GMifDescNotes_add_format_text,
			  TEXT("graphId, format (the literal Format text - its {tokens} create the argument pins), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 304, nullptr },
			{ TEXT("add_function_call"), GMifDescKeys_add_function_call, GMifDescNotes_add_function_call,
			  TEXT("graphId, class (aliases: cls, className, targetClass, ownerClass; default \"self\"), function (aliases: functionName, func, method), asMessage (alias: message), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 650, nullptr },
			{ TEXT("add_get_array_item"), GMifDescKeys_add_get_array_item, GMifDescNotes_add_get_array_item,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes.cpp"), 898, nullptr },
			{ TEXT("add_get_data_table_row"), GMifDescKeys_add_get_data_table_row, GMifDescNotes_add_get_data_table_row,
			  TEXT("graphId, dataTable (object path of the UDataTable), rowName, x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 346, nullptr },
			{ TEXT("add_get_subsystem"), GMifDescKeys_add_get_subsystem, GMifDescNotes_add_get_subsystem,
			  TEXT("graphId, subsystemClass (alias: class), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 197, nullptr },
			{ TEXT("add_interface"), GMifDescKeys_add_interface, GMifDescNotes_add_interface,
			  TEXT("blueprintId (alias: path), interface (aliases: interfaceClass, class)"),
			  TEXT("MifBridgeInterfaces.cpp"), 24, nullptr },
			{ TEXT("add_literal"), GMifDescKeys_add_literal, GMifDescNotes_add_literal,
			  TEXT("graphId, object (an asset OBJECT PATH; object-reference literals only), x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 287, nullptr },
			{ TEXT("add_macro_instance"), GMifDescKeys_add_macro_instance, GMifDescNotes_add_macro_instance,
			  TEXT("graphId, macroGraph (aliases: macro, macroName, name), macroPath (aliases: macroLibrary, library, path), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 829, nullptr },
			{ TEXT("add_make_array"), GMifDescKeys_add_make_array, GMifDescNotes_add_make_array,
			  TEXT("graphId, numInputs (element pin count, 1-64, default 1), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 238, nullptr },
			{ TEXT("add_make_map"), GMifDescKeys_add_make_map, GMifDescNotes_add_make_map,
			  TEXT("graphId, numInputs (entry count - each entry is one Key + Value pin pair, 1-64, default 1), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 272, nullptr },
			{ TEXT("add_make_struct"), GMifDescKeys_add_make_struct, GMifDescNotes_add_make_struct,
			  TEXT("graphId, structName, x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 201, nullptr },
			{ TEXT("add_material_expression"), GMifDescKeys_add_material_expression, nullptr,
			  TEXT("path (aliases: material, materialPath), class (aliases: expressionClass, type), x (aliases: nodePosX, posX), y (aliases: nodePosY, posY), properties (alias: props), asset (alias: selectedAsset)"),
			  TEXT("MifBridgeMaterials.cpp"), 927, nullptr },
			{ TEXT("add_nav_volume"), GMifDescKeys_add_nav_volume, GMifDescNotes_add_nav_volume,
			  TEXT("location {x,y,z}, size {x,y,z} (coverage in WORLD UNITS), label"),
			  TEXT("MifBridgeNavigation.cpp"), 47, nullptr },
			{ TEXT("add_override_event"), GMifDescKeys_add_override_event, GMifDescNotes_add_override_event,
			  TEXT("blueprintId (alias: path), event (aliases: eventName, name, function, functionName), interfaceOrParent (aliases: class, cls, className, parentClass, interface, ownerClass, targetClass), callParent (aliases: addParentCall, withParentCall), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 944, nullptr },
			{ TEXT("add_component_bound_event"), GMifDescKeys_add_component_bound_event, GMifDescNotes_add_component_bound_event,
			  TEXT("blueprintId (alias: path), component (the SCS/native component variable name), dispatcher (aliases: delegate, event), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1058, nullptr },
			{ TEXT("add_parent_call"), GMifDescKeys_add_parent_call, GMifDescNotes_add_parent_call,
			  TEXT("graphId, parentClass (aliases: class, cls, className, parent, ownerClass, targetClass; default = this blueprint's parent), function (aliases: functionName, func, method, name), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1044, nullptr },
			{ TEXT("add_pin"), GMifDescKeys_add_pin, GMifDescNotes_add_pin,
			  TEXT("name (aliases: pin, pinName), type (alias: pinType), container, valueType, direction (alias: dir; input|output), default (aliases: defaultValue, value), and ONE target: nodeGuid (aliases: node, guid, nodeId) | graphId | blueprintId + function"),
			  TEXT("MifBridgeNodes.cpp"), 1217, nullptr },
			{ TEXT("add_self"), GMifDescKeys_add_self, GMifDescNotes_add_self,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes2.cpp"), 117, nullptr },
			{ TEXT("add_sequence"), GMifDescKeys_add_sequence, GMifDescNotes_add_sequence,
			  TEXT("graphId, x, y, outputs (then_N exec pin count, 2-64, default 2)"),
			  TEXT("MifBridgeNodes4.cpp"), 33, nullptr },
			{ TEXT("add_simplified_collision"), GMifDescKeys_add_simplified_collision, GMifDescNotes_add_simplified_collision,
			  TEXT("path (a UStaticMesh), shape (box|sphere|capsule|10dop-x|10dop-y|10dop-z|18dop|26dop)"),
			  TEXT("MifBridgeCollision.cpp"), 143, nullptr },
			{ TEXT("add_spawn_actor"), GMifDescKeys_add_spawn_actor, GMifDescNotes_add_spawn_actor,
			  TEXT("graphId, actorClass (alias: class), x, y"),
			  TEXT("MifBridgeNodes4.cpp"), 69, nullptr },
			{ TEXT("add_struct_member"), GMifDescKeys_add_struct_member, GMifDescNotes_add_struct_member,
			  TEXT("struct (aliases: structPath, path), name, type, container?, valueType?, default?"),
			  TEXT("MifBridgeUserTypes.cpp"), 344, nullptr },
			{ TEXT("add_sublevel"), GMifDescKeys_add_sublevel, nullptr,
			  TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\"), location {x,y,z}, rotation {x,y,z}"),
			  TEXT("MifBridgeStreaming.cpp"), 569, nullptr },
			{ TEXT("add_switch_enum"), GMifDescKeys_add_switch_enum, GMifDescNotes_add_switch_enum,
			  TEXT("graphId, enumName, hasDefault? (default false), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 321, nullptr },
			{ TEXT("add_switch_int"), GMifDescKeys_add_switch_int, GMifDescNotes_add_switch_int,
			  TEXT("graphId, cases? (NUMBER of case pins, clamped 0-256), startIndex? (default 0), hasDefault? (default true), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 361, nullptr },
			{ TEXT("add_switch_string"), GMifDescKeys_add_switch_string, GMifDescNotes_add_switch_string,
			  TEXT("graphId, cases? (ARRAY of non-empty, non-duplicate label strings), caseSensitive? (default false), hasDefault? (default true), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 400, nullptr },
			{ TEXT("add_timeline"), GMifDescKeys_add_timeline, GMifDescNotes_add_timeline,
			  TEXT("blueprintId (alias: path), name?, floatTracks? (array of track name strings), length?, autoPlay? (default false), loop? (default false), x, y"),
			  TEXT("MifBridgeNodes3.cpp"), 74, nullptr },
			{ TEXT("add_tree_widget"), GMifDescKeys_add_tree_widget, GMifDescNotes_add_tree_widget,
			  TEXT("blueprintId (alias: path), widgetClass (alias: class), name (optional, uniquified on collision), parentName or asRoot, and canvas-slot placement x, y, autoSize (default true)"),
			  TEXT("MifBridgeWidgets.cpp"), 206, nullptr },
			{ TEXT("add_variable"), GMifDescKeys_add_variable, GMifDescNotes_add_variable,
			  TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), function? (required when scope=local), default?"),
			  TEXT("MifBridgeIntrospect.cpp"), 920, nullptr },
			{ TEXT("add_variable_get"), GMifDescKeys_add_variable_get, GMifDescNotes_add_variable_get,
			  TEXT("graphId, var (aliases: name, variable, varName, property, propertyName, member), targetClass (aliases: class, cls, className, ownerClass, objectClass), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 369, TEXT("DoAddVariableNode") },
			{ TEXT("add_variable_set"), GMifDescKeys_add_variable_set, GMifDescNotes_add_variable_set,
			  TEXT("graphId, var (aliases: name, variable, varName, property, propertyName, member), targetClass (aliases: class, cls, className, ownerClass, objectClass), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 369, TEXT("DoAddVariableNode") },
			{ TEXT("add_widget_binding"), GMifDescKeys_add_widget_binding, GMifDescNotes_add_widget_binding,
			  TEXT("blueprintId (alias: path), widgetName, propertyName, functionName - all four required"),
			  TEXT("MifBridgeWidgets.cpp"), 97, nullptr },
			{ TEXT("audit_unused"), GMifDescKeys_audit_unused, nullptr,
			  TEXT("pathPrefix, class, includeAll, limit, rescan, excludeReferencers (aliases: excludeReferencer, ignoreReferencers)"),
			  TEXT("MifBridgeAssetOps.cpp"), 481, nullptr },
			{ TEXT("backup_blueprint"), GMifDescKeys_backup_blueprint, GMifDescNotes_backup_blueprint,
			  TEXT("blueprintId (alias: path) - copies the blueprint's package file on disk to a backup, returned as 'backup'"),
			  TEXT("MifBridgeIntrospect.cpp"), 198, nullptr },
			{ TEXT("batch"), GMifDescKeys_batch, GMifDescNotes_batch,
			  TEXT("ops (array), blueprintId (alias: path), backup, compileAtEnd (default true)"),
			  TEXT("MifBridgeNodes.cpp"), 1824, nullptr },
			{ TEXT("bind_landscape_rvt"), GMifDescKeys_bind_landscape_rvt, GMifDescNotes_bind_landscape_rvt,
			  TEXT("landscape (alias: actorPath; omit when there is only one), runtimeVirtualTextures [assetPath,...], createVolumes (bool, default true)"),
			  TEXT("MifBridgeLandscape.cpp"), 704, nullptr },
			{ TEXT("build_navmesh"), GMifDescKeys_build_navmesh, GMifDescNotes_build_navmesh,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeNavigation.cpp"), 107, nullptr },
			{ TEXT("capture_camera"), GMifDescKeys_capture_camera, GMifDescNotes_capture_camera,
			  TEXT("x, y, z (or location:{x,y,z}), rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z}, useViewportCamera (aliases: useViewport, fromViewport), fov, width, height, name"),
			  TEXT("MifBridgeSpatial.cpp"), 330, nullptr },
			{ TEXT("check_overlaps"), GMifDescKeys_check_overlaps, GMifDescNotes_check_overlaps,
			  TEXT("actorPath (alias: actor) to test ONE actor, or omit both for a whole-scene audit; nameContains, ignoreGround, tolerance"),
			  TEXT("MifBridgeSpatial.cpp"), 155, nullptr },
			{ TEXT("compile"), GMifDescKeys_compile, GMifDescNotes_compile,
			  TEXT("blueprintId (alias: path) - compiles the blueprint and returns {ok, numErrors, numWarnings, messages[{severity,text,nodeGuid,pinName}]}"),
			  TEXT("MifBridgeIntrospect.cpp"), 1492, nullptr },
			{ TEXT("connect_material_expressions"), GMifDescKeys_connect_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath), from (alias: fromExpression), fromOutput (alias: fromOutputName), to (alias: toExpression), toInput (alias: toInputName)"),
			  TEXT("MifBridgeMaterials.cpp"), 1073, nullptr },
			{ TEXT("connect_material_property"), GMifDescKeys_connect_material_property, nullptr,
			  TEXT("path (aliases: material, materialPath), from (alias: fromExpression), fromOutput (alias: fromOutputName), property (alias: materialProperty)"),
			  TEXT("MifBridgeMaterials.cpp"), 1144, nullptr },
			{ TEXT("connect_pins"), GMifDescKeys_connect_pins, GMifDescNotes_connect_pins,
			  TEXT("srcNode, srcPin (aliases: sourcePin, fromPin), dstNode, dstPin (aliases: destPin, toPin), graphId, path (back-compat only — accepted and ignored; graphId already names the blueprint)"),
			  TEXT("MifBridgeNodes.cpp"), 554, TEXT("DoConnect") },
			{ TEXT("create_blueprint"), GMifDescKeys_create_blueprint, GMifDescNotes_create_blueprint,
			  TEXT("path (must start with /Game/), parentClass (default \"Actor\"), blueprintType (Normal | FunctionLibrary | Interface | MacroLibrary | WidgetBlueprint | AnimBlueprint), skeleton (alias targetSkeleton - REQUIRED for AnimBlueprint)"),
			  TEXT("MifBridgeNodes2.cpp"), 1209, nullptr },
			{ TEXT("reparent_blueprint"), GMifDescKeys_reparent_blueprint, GMifDescNotes_reparent_blueprint,
			  TEXT("blueprintId (alias: path), newParentClass (alias: parentClass)"),
			  TEXT("MifBridgeNodes2.cpp"), 1385, nullptr },
			{ TEXT("create_editable_child"), GMifDescKeys_create_editable_child, GMifDescNotes_create_editable_child,
			  TEXT("sourceAsset (the cooked BP - its _C class path or its asset path), childPath (destination; defaults to /Game/Mif/<Name>_Child or _Editable), variant: child | sibling | uncooked | sibling_full | full"),
			  TEXT("MifBridgeReconstruct.cpp"), 23, nullptr },
			{ TEXT("create_enum"), GMifDescKeys_create_enum, GMifDescNotes_create_enum,
			  TEXT("path (must start with /Game/ - the enum is named after the last segment), values[] (entry display names, in order)"),
			  TEXT("MifBridgeUserTypes.cpp"), 475, nullptr },
			{ TEXT("create_function"), GMifDescKeys_create_function, GMifDescNotes_create_function,
			  TEXT("blueprintId (alias: path), name, inputs?, outputs?, pure?"),
			  TEXT("MifBridgeNodes2.cpp"), 344, nullptr },
			{ TEXT("create_landscape"), GMifDescKeys_create_landscape, GMifDescNotes_create_landscape,
			  TEXT("location {x,y,z}, scale {x,y,z}, componentsX, componentsY, quadsPerSection (7|15|31|63|127|255), sectionsPerComponent (1|2), material (alias: landscapeMaterial), layers [{layerInfo (aliases: info, path), weight}], heightMode (\"flat\"|\"rolling\"|\"island\"), amplitude, frequency, seed, label, folder"),
			  TEXT("MifBridgeLandscape.cpp"), 112, nullptr },
			{ TEXT("create_material"), GMifDescKeys_create_material, nullptr,
			  TEXT("path (alias: assetPath), domain (alias: materialDomain), blendMode, initialTexture"),
			  TEXT("MifBridgeMaterials.cpp"), 743, nullptr },
			{ TEXT("create_material_function"), GMifDescKeys_create_material_function, GMifDescNotes_create_material_function,
			  TEXT("path (alias: assetPath), description, exposeToLibrary"),
			  TEXT("MifBridgeMaterials.cpp"), 865, nullptr },
			{ TEXT("create_material_instance"), GMifDescKeys_create_material_instance, GMifDescNotes_create_material_instance,
			  TEXT("parent (alias: parentMaterial), path (must start with /Game/), scalars {name:number}, vectors {name:{r,g,b,a}}"),
			  TEXT("MifBridgeAuthoring.cpp"), 448, nullptr },
			{ TEXT("create_struct"), GMifDescKeys_create_struct, GMifDescNotes_create_struct,
			  TEXT("path (must start with /Game/ - the struct is named after the last segment), members[] (each: name, type, container?, valueType?, default?)"),
			  TEXT("MifBridgeUserTypes.cpp"), 194, nullptr },
			{ TEXT("delete_asset"), GMifDescKeys_delete_asset, GMifDescNotes_delete_asset,
			  TEXT("path (a /Game/ package or object path), confirm (required true)"),
			  TEXT("MifBridgeAssetOps.cpp"), 78, nullptr },
			{ TEXT("delete_datatable_rows"), GMifDescKeys_delete_datatable_rows, GMifDescNotes_delete_datatable_rows,
			  TEXT("path, rowNames[], confirm=true"),
			  TEXT("MifBridgeDataTables.cpp"), 675, nullptr },
			{ TEXT("delete_level_actor"), GMifDescKeys_delete_level_actor, GMifDescNotes_delete_level_actor,
			  TEXT("actorPath (aliases: actor, path), confirm (must be true)"),
			  TEXT("MifBridgeLevel.cpp"), 459, nullptr },
			{ TEXT("delete_material_expression"), GMifDescKeys_delete_material_expression, nullptr,
			  TEXT("path (aliases: material, materialPath), expression (alias: name), all (alias: deleteAll)"),
			  TEXT("MifBridgeMaterials.cpp"), 1235, nullptr },
			{ TEXT("describe_animation"), GMifDescKeys_describe_animation, GMifDescNotes_describe_animation,
			  TEXT("assetPath (aliases: path, animation, asset) - the animation asset to describe, e.g. /Game/Anims/AS_Run"),
			  TEXT("MifBridgeAnimation.cpp"), 103, nullptr },
			{ TEXT("describe_class"), GMifDescKeys_describe_class, nullptr,
			  TEXT("class (alias: className), filter (optional substring match)"),
			  TEXT("MifBridgeIntrospect.cpp"), 405, nullptr },
			{ TEXT("describe_package"), GMifDescKeys_describe_package, nullptr,
			  TEXT("package (alias: path)"),
			  TEXT("MifBridgeCooked.cpp"), 354, nullptr },
			{ TEXT("describe_property"), GMifDescKeys_describe_property, nullptr,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName | class (alias className); then propertyPath (alias property) OR nameContains (aliases filter, nameFilter); limit, maxValueChars, includeMetadata, includeDefault"),
			  TEXT("MifBridgeDetails.cpp"), 733, nullptr },
			{ TEXT("diagnose_landscape"), GMifDescKeys_diagnose_landscape, nullptr,
			  TEXT("limit"),
			  TEXT("MifBridgeCooked.cpp"), 534, nullptr },
			{ TEXT("diagnose_landscape_draws"), GMifDescKeys_diagnose_landscape_draws, nullptr,
			  TEXT("limit"),
			  TEXT("MifBridgeCooked.cpp"), 902, nullptr },
			{ TEXT("diff_properties_vs_default"), GMifDescKeys_diff_properties_vs_default, nullptr,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars, includeTransient, deep, recursive (alias includeChildren)"),
			  TEXT("MifBridgeDetails.cpp"), 885, nullptr },
			{ TEXT("disconnect_pin"), GMifDescKeys_disconnect_pin, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), path (back-compat only — accepted and ignored; graphId already names the blueprint)"),
			  TEXT("MifBridgeNodes.cpp"), 1674, nullptr },
			{ TEXT("duplicate_actors"), GMifDescKeys_duplicate_actors, GMifDescNotes_duplicate_actors,
			  TEXT("actorPaths[] and/or labelPrefix (source selection), offset {x,y,z}, yawOffset (degrees), count, labelSuffix, folder"),
			  TEXT("MifBridgeAuthoring.cpp"), 327, nullptr },
			{ TEXT("duplicate_asset"), GMifDescKeys_duplicate_asset, GMifDescNotes_duplicate_asset,
			  TEXT("path (the source asset), newPath (the destination - its last segment is BOTH the destination folder and the new asset name)"),
			  TEXT("MifBridgeAssetOps.cpp"), 207, nullptr },
			{ TEXT("edit_container"), GMifDescKeys_edit_container, GMifDescNotes_edit_container,
			  TEXT("objectPath (alias actorPath), propertyPath (alias property), operation (alias action) = add|insert|remove|clear|swap|resize|setKey, index (alias at), count, key, newKey, value, swapWith, newSize, overrideFlag (set|refuse|ignore)"),
			  TEXT("MifBridgeDetails.cpp"), 1651, nullptr },
			{ TEXT("export_asset"), GMifDescKeys_export_asset, GMifDescNotes_export_asset,
			  TEXT("asset (aliases: path, assetPath, objectPath), file (aliases: filename, outPath), format (aliases: type, extension), overwrite (alias: replaceExisting), fbxCompatibility, ascii, vertexColor, levelOfDetail (alias: lod), collision, exportSourceMesh, forceFrontXAxis"),
			  TEXT("MifBridgeExport.cpp"), 303, nullptr },
			{ TEXT("find_assets"), GMifDescKeys_find_assets, GMifDescNotes_find_assets,
			  TEXT("class (aliases: className, type), pathPrefix, nameContains, origin, recursiveClasses, limit"),
			  TEXT("MifBridgeCooked.cpp"), 243, nullptr },
			{ TEXT("find_nodes"), GMifDescKeys_find_nodes, GMifDescNotes_find_nodes,
			  TEXT("graphId, byClass (substring of the node's C++ class name), byTitle (substring of the node title), byFunction (substring of the called function name) - every filter is optional and they are ANDed"),
			  TEXT("MifBridgeIntrospect.cpp"), 516, nullptr },
			{ TEXT("focus_viewport"), GMifDescKeys_focus_viewport, GMifDescNotes_focus_viewport,
			  TEXT("actorPath (alias: actor) to frame ONE actor, folder to frame a folder subtree, all (or nothing at all) to frame the whole level, instant"),
			  TEXT("MifBridgeViewport.cpp"), 165, nullptr },
			{ TEXT("get_actor_bounds"), GMifDescKeys_get_actor_bounds, GMifDescNotes_get_actor_bounds,
			  TEXT("actorPath (aliases: actor, path) — the PLACED actor to measure, given as an object path, object name or label"),
			  TEXT("MifBridgeSpatial.cpp"), 122, nullptr },
			{ TEXT("get_datatable_row"), GMifDescKeys_get_datatable_row, nullptr,
			  TEXT("path, rowName, textFormat (aliases: textMode, simpleText:true)"),
			  TEXT("MifBridgeDataTables.cpp"), 436, nullptr },
			{ TEXT("get_dependencies"), GMifDescKeys_get_dependencies, nullptr,
			  TEXT("path"),
			  TEXT("MifBridgeAssetOps.cpp"), 324, nullptr },
			{ TEXT("get_inherited_component"), GMifDescKeys_get_inherited_component, nullptr,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name)"),
			  TEXT("MifBridgeInherited.cpp"), 622, nullptr },
			{ TEXT("get_node"), GMifDescKeys_get_node, GMifDescNotes_get_node,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional - scopes the guid lookup to that one graph, the only way to disambiguate two loaded copies of a blueprint sharing NodeGuids)"),
			  TEXT("MifBridgeIntrospect.cpp"), 300, nullptr },
			{ TEXT("get_property"), GMifDescKeys_get_property, nullptr,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, propertyPath (alias property)"),
			  TEXT("MifBridgeNodes6.cpp"), 43, nullptr },
			{ TEXT("get_referencers"), GMifDescKeys_get_referencers, nullptr,
			  TEXT("path"),
			  TEXT("MifBridgeAssetOps.cpp"), 293, nullptr },
			{ TEXT("get_spline_points"), GMifDescKeys_get_spline_points, GMifDescNotes_get_spline_points,
			  TEXT("actorPath (alias: actor), component (alias: componentName), space (\"world\"|\"local\", default world)"),
			  TEXT("MifBridgeWorld.cpp"), 380, nullptr },
			{ TEXT("get_viewport_camera"), GMifDescKeys_get_viewport_camera, GMifDescNotes_get_viewport_camera,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeViewport.cpp"), 237, nullptr },
			{ TEXT("implement_interface_function"), GMifDescKeys_implement_interface_function, GMifDescNotes_implement_interface_function,
			  TEXT("blueprintId (alias: path), function - the interface function name to add an implementation graph for"),
			  TEXT("MifBridgeFunctions.cpp"), 21, nullptr },
			{ TEXT("import_asset"), GMifDescKeys_import_asset, GMifDescNotes_import_asset,
			  TEXT("file (aliases: filename, sourcePath), destination (aliases: destinationPath, path), name (alias: destinationName), factory, replaceExisting (alias: overwrite), replaceExistingSettings, save"),
			  TEXT("MifBridgeImport.cpp"), 1132, nullptr },
			{ TEXT("import_texture"), GMifDescKeys_import_texture, GMifDescNotes_import_texture,
			  TEXT("destPath (aliases: path, assetPath), sourcePath (aliases: file, filename) OR base64 (aliases: data, bytes), format, overwrite (alias: replaceExisting), save, compressionSettings (alias: compression), srgb, lodGroup (alias: textureGroup), neverStream, mipGenSettings (alias: mipGen), filter"),
			  TEXT("MifBridgeImport.cpp"), 802, nullptr },
			{ TEXT("invoke_editor_command"), GMifDescKeys_invoke_editor_command, GMifDescNotes_invoke_editor_command,
			  TEXT("context, command, menu, section, entry, dryRun, confirm, allowKnownModal"),
			  TEXT("MifBridgeUI.cpp"), 871, nullptr },
			{ TEXT("invoke_editor_tab"), GMifDescKeys_invoke_editor_tab, GMifDescNotes_invoke_editor_tab,
			  TEXT("tabId (alias: tab), manager (global|majorTab|assetEditor; default global), majorTab, asset, probe, probeIds[], includeKnownIds (default true), asInactive"),
			  TEXT("MifBridgeUI.cpp"), 1165, nullptr },
			{ TEXT("landscape_info"), GMifDescKeys_landscape_info, GMifDescNotes_landscape_info,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeLandscape.cpp"), 804, nullptr },
			{ TEXT("layout_material_expressions"), GMifDescKeys_layout_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath)"),
			  TEXT("MifBridgeMaterials.cpp"), 1488, nullptr },
			{ TEXT("list_animations"), GMifDescKeys_list_animations, GMifDescNotes_list_animations,
			  TEXT("filter (substring matched against the full object path), skeleton (substring matched against the registry's Skeleton tag), limit (default 200, max 5000)"),
			  TEXT("MifBridgeAnimation.cpp"), 284, nullptr },
			{ TEXT("list_blueprints"), GMifDescKeys_list_blueprints, GMifDescNotes_list_blueprints,
			  TEXT("filter (optional; substring matched against each blueprint's full object path - omit to list every blueprint, capped at 5000)"),
			  TEXT("MifBridgeIntrospect.cpp"), 81, nullptr },
			{ TEXT("list_components"), GMifDescKeys_list_components, nullptr,
			  TEXT("blueprintId (alias: path), component (alias: componentName; optional - omit for the whole list), includeInherited (default true), includeNative (default true), limit (default 500)"),
			  TEXT("MifBridgeComponents.cpp"), 505, nullptr },
			{ TEXT("list_datatables"), GMifDescKeys_list_datatables, GMifDescNotes_list_datatables,
			  TEXT("filter (optional substring matched against the full object path; omit to list every DataTable)"),
			  TEXT("MifBridgeDataTables.cpp"), 332, nullptr },
			{ TEXT("list_dirty_packages"), GMifDescKeys_list_dirty_packages, nullptr,
			  TEXT("kind (content|world|all)"),
			  TEXT("MifBridgeUndo.cpp"), 467, nullptr },
			{ TEXT("list_dispatchers"), GMifDescKeys_list_dispatchers, GMifDescNotes_list_dispatchers,
			  TEXT("blueprintId (alias: path)"),
			  TEXT("MifBridgeDelegates.cpp"), 230, nullptr },
			{ TEXT("list_editor_commands"), GMifDescKeys_list_editor_commands, GMifDescNotes_list_editor_commands,
			  TEXT("context, command, filter, includeUnbound (default true), includeCanExecute (default false), includeConsole (default false), consolePrefix, menu, section, limit (default 400)"),
			  TEXT("MifBridgeUI.cpp"), 567, nullptr },
			{ TEXT("list_enum_values"), GMifDescKeys_list_enum_values, nullptr,
			  TEXT("enum (alias: enumName)"),
			  TEXT("MifBridgeNodes3.cpp"), 281, nullptr },
			{ TEXT("list_functions"), GMifDescKeys_list_functions, GMifDescNotes_list_functions,
			  TEXT("blueprintId (alias: path) - lists the blueprint's own function graphs with name and graphId"),
			  TEXT("MifBridgeIntrospect.cpp"), 363, nullptr },
			{ TEXT("list_graphs"), GMifDescKeys_list_graphs, GMifDescNotes_list_graphs,
			  TEXT("blueprintId (alias: path) - lists every graph in the blueprint, nested ones included, with its graphId"),
			  TEXT("MifBridgeIntrospect.cpp"), 229, nullptr },
			{ TEXT("list_interfaces"), GMifDescKeys_list_interfaces, GMifDescNotes_list_interfaces,
			  TEXT("blueprintId (alias: path), includeInherited (default false)"),
			  TEXT("MifBridgeInterfaces.cpp"), 116, nullptr },
			{ TEXT("list_level_actors"), GMifDescKeys_list_level_actors, GMifDescNotes_list_level_actors,
			  TEXT("classFilter, nameContains, folder, selectedOnly, limit"),
			  TEXT("MifBridgeLevel.cpp"), 126, nullptr },
			{ TEXT("list_material_expressions"), GMifDescKeys_list_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath), includeConnections, includeProperties"),
			  TEXT("MifBridgeMaterials.cpp"), 1320, nullptr },
			{ TEXT("list_mounted_containers"), GMifDescKeys_list_mounted_containers, nullptr,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeCooked.cpp"), 120, nullptr },
			{ TEXT("list_nodes"), GMifDescKeys_list_nodes, GMifDescNotes_list_nodes,
			  TEXT("graphId ('<blueprintPath>::<graphName>', exactly as open_blueprint/list_graphs return it), hideKnots (default false; true skips reroute nodes)"),
			  TEXT("MifBridgeIntrospect.cpp"), 259, nullptr },
			{ TEXT("list_object_properties"), GMifDescKeys_list_object_properties, GMifDescNotes_list_object_properties,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars"),
			  TEXT("MifBridgeNodes6.cpp"), 110, nullptr },
			{ TEXT("list_pie_actors"), GMifDescKeys_list_pie_actors, GMifDescNotes_list_pie_actors,
			  TEXT("classFilter, nameContains, limit (1-5000, default 200), netMode (server|client|any; default server)"),
			  TEXT("MifBridgePIE.cpp"), 394, nullptr },
			{ TEXT("list_struct_members"), GMifDescKeys_list_struct_members, nullptr,
			  TEXT("struct (aliases: structPath, path) - asset path of a Blueprint user-defined struct"),
			  TEXT("MifBridgeUserTypes.cpp"), 315, nullptr },
			{ TEXT("list_sublevels"), GMifDescKeys_list_sublevels, nullptr,
			  TEXT("world (\"editor\"|\"pie\"), netMode (\"server\"|\"client\"|\"any\", only meaningful with world:\"pie\")"),
			  TEXT("MifBridgeStreaming.cpp"), 463, nullptr },
			{ TEXT("list_transactions"), GMifDescKeys_list_transactions, nullptr,
			  TEXT("limit (aliases: count, max), offset (alias: start), includeObjects (alias: include_objects)"),
			  TEXT("MifBridgeUndo.cpp"), 120, nullptr },
			{ TEXT("list_variables"), GMifDescKeys_list_variables, GMifDescNotes_list_variables,
			  TEXT("blueprintId (alias: path) - lists the blueprint's MEMBER variables with name, type, default, flags and a suspiciousName marker"),
			  TEXT("MifBridgeIntrospect.cpp"), 318, nullptr },
			{ TEXT("load_level"), GMifDescKeys_load_level, GMifDescNotes_load_level,
			  TEXT("path (aliases: packagePath, assetPath) - the package path of the map to open, e.g. \"/Game/Maps/MyLevel\""),
			  TEXT("MifBridgeWorld.cpp"), 198, nullptr },
			{ TEXT("move_actor_to"), GMifDescKeys_move_actor_to, GMifDescNotes_move_actor_to,
			  TEXT("actorPath (alias: actor) - the pawn to move; location {x,y,z} - the goal"),
			  TEXT("MifBridgeNavigation.cpp"), 191, nullptr },
			{ TEXT("move_node"), GMifDescKeys_move_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1143, nullptr },
			{ TEXT("nav_status"), GMifDescKeys_nav_status, GMifDescNotes_nav_status,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeNavigation.cpp"), 146, nullptr },
			{ TEXT("new_level"), GMifDescKeys_new_level, GMifDescNotes_new_level,
			  TEXT("partitioned (bool, default false) - the only parameter; new_level takes no path"),
			  TEXT("MifBridgeWorld.cpp"), 124, nullptr },
			{ TEXT("open_asset_editor"), GMifDescKeys_open_asset_editor, GMifDescNotes_open_asset_editor,
			  TEXT("path - the asset whose default editor to open. NOTE: this does NOT make that editor's commands reachable by invoke_editor_command; asset editor toolkits never register a command list (measured 2026-08-15). Use a direct endpoint instead, e.g. remove_collision / add_simplified_collision"),
			  TEXT("MifBridgeUI.cpp"), 1508, nullptr },
			{ TEXT("open_blueprint"), GMifDescKeys_open_blueprint, GMifDescNotes_open_blueprint,
			  TEXT("blueprintId (alias: path) - the blueprint asset to open; returns blueprintId, name, class, parentClass and graphs"),
			  TEXT("MifBridgeIntrospect.cpp"), 39, nullptr },
			{ TEXT("override_inherited_component"), GMifDescKeys_override_inherited_component, GMifDescNotes_override_inherited_component,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), properties (alias: props), confirm"),
			  TEXT("MifBridgeInherited.cpp"), 773, nullptr },
			{ TEXT("paint_landscape"), GMifDescKeys_paint_landscape, GMifDescNotes_paint_landscape,
			  TEXT("landscape (alias: actorPath; omit when there is only one), layerInfo (aliases: layer, info) - a LandscapeLayerInfoObject ASSET PATH, center {x,y} in WORLD units, radius (world units), weight (0..1), falloff (0..1 of the radius that is feathered)"),
			  TEXT("MifBridgeLandscape.cpp"), 558, nullptr },
			{ TEXT("pie_load_level_instance"), GMifDescKeys_pie_load_level_instance, nullptr,
			  TEXT("path (packagePath, level), location {x,y,z}, rotation {x,y,z}, visible (bool), netMode (\"server\"|\"client\"|\"any\"), nameOverride (string), tempPackage (bool)"),
			  TEXT("MifBridgeStreaming.cpp"), 1262, nullptr },
			{ TEXT("pie_status"), GMifDescKeys_pie_status, GMifDescNotes_pie_status,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgePIE.cpp"), 362, nullptr },
			{ TEXT("pie_unload_level_instance"), GMifDescKeys_pie_unload_level_instance, nullptr,
			  TEXT("instanceName (name) from pie_load_level_instance, or objectPath, or path (packagePath, level) naming the SOURCE map; netMode (\"server\"|\"client\"|\"any\")"),
			  TEXT("MifBridgeStreaming.cpp"), 1398, nullptr },
			{ TEXT("read_datatable"), GMifDescKeys_read_datatable, nullptr,
			  TEXT("path, maxRows, textFormat (aliases: textMode, simpleText:true)"),
			  TEXT("MifBridgeDataTables.cpp"), 374, nullptr },
			{ TEXT("read_modloader_log"), GMifDescKeys_read_modloader_log, GMifDescNotes_read_modloader_log,
			  TEXT("path (optional - defaults to the live DDS2 UE4SS.log), lines (tail size, 1-5000, default 80), filter (plain substring)"),
			  TEXT("MifBridgePipeline.cpp"), 31, nullptr },
			{ TEXT("recipe_add_debug_print"), GMifDescKeys_recipe_add_debug_print, GMifDescNotes_recipe_add_debug_print,
			  TEXT("graphId, message, functionName (default PrintToModLoader), messageParam (default Message), afterNode, afterPin (default then), x, y"),
			  TEXT("MifBridgeRecipes.cpp"), 66, nullptr },
			{ TEXT("recipe_argmax_over_components"), GMifDescKeys_recipe_argmax_over_components, GMifDescNotes_recipe_argmax_over_components,
			  TEXT("graphId, loopBodyNode, loopBodyPin (default 'Loop Body'), scoreNode, scorePin, indexNode, indexPin, bestScoreVar, bestIndexVar, x, y"),
			  TEXT("MifBridgeRecipes.cpp"), 441, nullptr },
			{ TEXT("recipe_reset_and_loop"), GMifDescKeys_recipe_reset_and_loop, GMifDescNotes_recipe_reset_and_loop,
			  TEXT("graphId, arrayVar, indexVar, scoreVar (omit to skip the score SET), indexInit (default -1), scoreInit (default -2.0), afterNode, afterPin (default then), x, y"),
			  TEXT("MifBridgeRecipes.cpp"), 204, nullptr },
			{ TEXT("recipe_splice_before_parent"), GMifDescKeys_recipe_splice_before_parent, GMifDescNotes_recipe_splice_before_parent,
			  TEXT("graphId, parentNode, clusterEntry, clusterExit, clusterEntryExecIn (default execute), clusterExitExecOut (default then)"),
			  TEXT("MifBridgeRecipes.cpp"), 364, nullptr },
			{ TEXT("recompile_material"), GMifDescKeys_recompile_material, nullptr,
			  TEXT("path (aliases: material, asset)"),
			  TEXT("MifBridgeMaterials.cpp"), 1550, nullptr },
			{ TEXT("reconnect_pin"), GMifDescKeys_reconnect_pin, GMifDescNotes_reconnect_pin,
			  TEXT("srcNode, srcPin (aliases: sourcePin, fromPin), dstNode, dstPin (aliases: destPin, toPin), graphId, path (back-compat only — accepted and ignored; graphId already names the blueprint)"),
			  TEXT("MifBridgeNodes.cpp"), 554, TEXT("DoConnect") },
			{ TEXT("redo_transactions"), GMifDescKeys_redo_transactions, nullptr,
			  TEXT("count (aliases: n, steps), toIndex (alias: to_index)"),
			  TEXT("MifBridgeUndo.cpp"), 340, nullptr },
			{ TEXT("refresh_node"), GMifDescKeys_refresh_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid)"),
			  TEXT("MifBridgeNodes.cpp"), 1641, nullptr },
			{ TEXT("reimport_asset"), GMifDescKeys_reimport_asset, GMifDescNotes_reimport_asset,
			  TEXT("path (aliases: assetPath, objectPath), sourceFile (aliases: file, newFile), sourceFileIndex, forceNewFile, save"),
			  TEXT("MifBridgeImport.cpp"), 1390, nullptr },
			{ TEXT("remove_collision"), GMifDescKeys_remove_collision, GMifDescNotes_remove_collision,
			  TEXT("path (a UStaticMesh), confirm (required true)"),
			  TEXT("MifBridgeCollision.cpp"), 84, nullptr },
			{ TEXT("remove_component"), GMifDescKeys_remove_component, GMifDescNotes_remove_component,
			  TEXT("blueprintId (alias: path), name (the component's variable name), confirm (required true)"),
			  TEXT("MifBridgeComponents.cpp"), 889, nullptr },
			{ TEXT("remove_enum_value"), GMifDescKeys_remove_enum_value, GMifDescNotes_remove_enum_value,
			  TEXT("enum (aliases: enumPath, path), index or value (aliases: name, displayName), confirm=true"),
			  TEXT("MifBridgeUserTypes.cpp"), 620, nullptr },
			{ TEXT("remove_function"), GMifDescKeys_remove_function, GMifDescNotes_remove_function,
			  TEXT("blueprintId (alias: path), name - the function graph to delete, confirm - must be true"),
			  TEXT("MifBridgeFunctions.cpp"), 90, nullptr },
			{ TEXT("remove_interface"), GMifDescKeys_remove_interface, GMifDescNotes_remove_interface,
			  TEXT("blueprintId (alias: path), interface (aliases: interfaceClass, class), confirm (required true)"),
			  TEXT("MifBridgeInterfaces.cpp"), 65, nullptr },
			{ TEXT("remove_node"), GMifDescKeys_remove_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes.cpp"), 1167, nullptr },
			{ TEXT("remove_pin"), GMifDescKeys_remove_pin, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), direction (alias: dir; input|output), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes.cpp"), 1506, nullptr },
			{ TEXT("remove_struct_member"), GMifDescKeys_remove_struct_member, GMifDescNotes_remove_struct_member,
			  TEXT("struct (aliases: structPath, path), name or guid, confirm=true"),
			  TEXT("MifBridgeUserTypes.cpp"), 398, nullptr },
			{ TEXT("remove_sublevel"), GMifDescKeys_remove_sublevel, nullptr,
			  TEXT("path (packagePath, level), discardUnsaved (bool)"),
			  TEXT("MifBridgeStreaming.cpp"), 711, nullptr },
			{ TEXT("remove_tree_widget"), GMifDescKeys_remove_tree_widget, GMifDescNotes_remove_tree_widget,
			  TEXT("blueprintId (alias: path), widgetName"),
			  TEXT("MifBridgeWidgets.cpp"), 356, nullptr },
			{ TEXT("remove_variable"), GMifDescKeys_remove_variable, nullptr,
			  TEXT("blueprintId (alias: path), name, confirm=true"),
			  TEXT("MifBridgeIntrospect.cpp"), 1192, nullptr },
			{ TEXT("remove_widget_binding"), GMifDescKeys_remove_widget_binding, GMifDescNotes_remove_widget_binding,
			  TEXT("blueprintId (alias: path), widgetName, propertyName - both required"),
			  TEXT("MifBridgeWidgets.cpp"), 162, nullptr },
			{ TEXT("rename_asset"), GMifDescKeys_rename_asset, GMifDescNotes_rename_asset,
			  TEXT("path, newPath (the destination - its last segment is BOTH the destination folder and the new asset name), confirm (required true)"),
			  TEXT("MifBridgeAssetOps.cpp"), 134, nullptr },
			{ TEXT("rename_event"), GMifDescKeys_rename_event, GMifDescNotes_rename_event,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), newName (aliases: name, to), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes2.cpp"), 621, nullptr },
			{ TEXT("rename_event_dispatcher"), GMifDescKeys_rename_event_dispatcher, GMifDescNotes_rename_event_dispatcher,
			  TEXT("blueprintId (alias: path), oldName (aliases: name, dispatcher), newName (alias: to), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes2.cpp"), 679, nullptr },
			{ TEXT("rename_function"), GMifDescKeys_rename_function, GMifDescNotes_rename_function,
			  TEXT("graphId, OR blueprintId (alias: path) + oldName (aliases: function, name); plus newName (alias: to), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes2.cpp"), 529, nullptr },
			{ TEXT("rename_variable"), GMifDescKeys_rename_variable, GMifDescNotes_rename_variable,
			  TEXT("blueprintId (alias: path), oldName, newName, confirm=true"),
			  TEXT("MifBridgeIntrospect.cpp"), 1061, nullptr },
			{ TEXT("render_thumbnail"), GMifDescKeys_render_thumbnail, nullptr,
			  TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, flushTextures, alpha, name"),
			  TEXT("MifBridgeThumbnail.cpp"), 792, nullptr },
			{ TEXT("reset_property_to_default"), GMifDescKeys_reset_property_to_default, nullptr,
			  TEXT("objectPath (alias actorPath), propertyPath (alias property), force (alias allowEditConst), overrideFlag (set|refuse|ignore)"),
			  TEXT("MifBridgeDetails.cpp"), 1073, nullptr },
			{ TEXT("resolve_struct"), GMifDescKeys_resolve_struct, GMifDescNotes_resolve_struct,
			  TEXT("name (bare name, C++ name or full path - e.g. Vector, FGuid, /Script/CoreUObject.Transform)"),
			  TEXT("MifBridgeNodes2.cpp"), 86, nullptr },
			{ TEXT("revert_inherited_component"), GMifDescKeys_revert_inherited_component, nullptr,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), confirm"),
			  TEXT("MifBridgeInherited.cpp"), 1133, nullptr },
			{ TEXT("run_console"), GMifDescKeys_run_console, GMifDescNotes_run_console,
			  TEXT("command (alias: cmd), world (editor|pie|active; default editor), captureOutput (default true)"),
			  TEXT("MifBridgeIntrospect.cpp"), 1540, nullptr },
			{ TEXT("run_console_captured"), GMifDescKeys_run_console_captured, GMifDescNotes_run_console_captured,
			  TEXT("command, filter (substring; only log lines containing it are returned)"),
			  TEXT("MifBridgePIE.cpp"), 504, nullptr },
			{ TEXT("save_blueprint"), GMifDescKeys_save_blueprint, GMifDescNotes_save_blueprint,
			  TEXT("blueprintId (alias: path) - writes the package that owns this blueprint back to disk, in place"),
			  TEXT("MifBridgeIntrospect.cpp"), 122, nullptr },
			{ TEXT("save_dirty_packages"), GMifDescKeys_save_dirty_packages, nullptr,
			  TEXT("maps (aliases: saveMaps, save_maps), content (aliases: saveContent, save_content), dryRun (alias: dry_run)"),
			  TEXT("MifBridgeUndo.cpp"), 561, nullptr },
			{ TEXT("save_level_as"), GMifDescKeys_save_level_as, GMifDescNotes_save_level_as,
			  TEXT("path (aliases: packagePath, assetPath) - the package path to save the open level to, e.g. \"/Game/Maps/MyLevel\""),
			  TEXT("MifBridgeWorld.cpp"), 158, nullptr },
			{ TEXT("save_package"), GMifDescKeys_save_package, GMifDescNotes_save_package,
			  TEXT("path - the /Game/ object path of ANY asset; the package that owns it is marked dirty and written to disk"),
			  TEXT("MifBridgeIntrospect.cpp"), 166, nullptr },
			{ TEXT("scene_report"), GMifDescKeys_scene_report, GMifDescNotes_scene_report,
			  TEXT("groundZ, floatTolerance, tallWarnZ — all optional; the scan itself always covers every actor in the active world"),
			  TEXT("MifBridgeSpatial.cpp"), 619, nullptr },
			{ TEXT("sculpt_landscape"), GMifDescKeys_sculpt_landscape, GMifDescNotes_sculpt_landscape,
			  TEXT("landscape (alias: actorPath; omit when there is only one), center {x,y} in WORLD units, radius (world units), mode (\"raise\"|\"lower\"|\"flatten\"|\"smooth\"), amount (world units, raise/lower ONLY), targetZ (a world Z, flatten ONLY), falloff (0..1 of the radius that is feathered)"),
			  TEXT("MifBridgeLandscape.cpp"), 340, nullptr },
			{ TEXT("select_level_actors"), GMifDescKeys_select_level_actors, GMifDescNotes_select_level_actors,
			  TEXT("actorPaths (array of full actor paths), clear"),
			  TEXT("MifBridgeLevel.cpp"), 500, nullptr },
			{ TEXT("send_editor_key"), GMifDescKeys_send_editor_key, GMifDescNotes_send_editor_key,
			  TEXT("key, confirm, dryRun, modifiers{ctrl,alt,shift,cmd}, userIndex (default 0), isRepeat, characterCode, keyCode, sendKeyUp (default true)"),
			  TEXT("MifBridgeUI.cpp"), 1325, nullptr },
			{ TEXT("set_actor_label"), GMifDescKeys_set_actor_label, GMifDescNotes_set_actor_label,
			  TEXT("actorPath (aliases: actor, path), label, folder"),
			  TEXT("MifBridgeLevel.cpp"), 424, nullptr },
			{ TEXT("set_actor_transform"), GMifDescKeys_set_actor_transform, GMifDescNotes_set_actor_transform,
			  TEXT("actorPath (aliases: actor, path), location, rotation, scale, relative"),
			  TEXT("MifBridgeLevel.cpp"), 338, nullptr },
			{ TEXT("set_asset_thumbnail"), GMifDescKeys_set_asset_thumbnail, GMifDescNotes_set_asset_thumbnail,
			  TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, flushTextures, save"),
			  TEXT("MifBridgeThumbnail.cpp"), 1171, nullptr },
			{ TEXT("set_component_transform"), GMifDescKeys_set_component_transform, GMifDescNotes_set_component_transform,
			  TEXT("blueprintId (alias: path), name (the component's variable name), location, rotation, scale - each {x,y,z} or [x,y,z]"),
			  TEXT("MifBridgeComponents.cpp"), 932, nullptr },
			{ TEXT("set_current_sublevel"), GMifDescKeys_set_current_sublevel, nullptr,
			  TEXT("path (packagePath, level) — a package path, or the literal \"persistent\""),
			  TEXT("MifBridgeStreaming.cpp"), 1043, nullptr },
			{ TEXT("set_function_flags"), GMifDescKeys_set_function_flags, GMifDescNotes_set_function_flags,
			  TEXT("target by nodeGuid (aliases: node, guid, nodeId), OR graphId, OR blueprintId (alias: path) + function (aliases: functionName, name); flags: replicates (none|multicast|server|client), reliable, access (public|protected|private), pure, const (alias: isConst), callInEditor, category, tooltip, keywords"),
			  TEXT("MifBridgeNodes2.cpp"), 874, nullptr },
			{ TEXT("set_material_parameter"), GMifDescKeys_set_material_parameter, GMifDescNotes_set_material_parameter,
			  TEXT("material (aliases: materialPath, path), scalars {name:number}, vectors {name:{r,g,b,a}}, and/or the singular pair parameter (aliases: parameterName, name) + value"),
			  TEXT("MifBridgeAuthoring.cpp"), 621, nullptr },
			{ TEXT("set_pin_default"), GMifDescKeys_set_pin_default, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), value (aliases: default, defaultValue)"),
			  TEXT("MifBridgeNodes.cpp"), 1704, nullptr },
			{ TEXT("set_pin_type"), GMifDescKeys_set_pin_type, nullptr,
			  TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), pin (aliases: pinName, name), type, container?, valueType?"),
			  TEXT("MifBridgeNodes3.cpp"), 520, nullptr },
			{ TEXT("set_property"), GMifDescKeys_set_property, GMifDescNotes_set_property,
			  TEXT("objectPath | (blueprintId or path) + widgetName, propertyPath, value, overrideFlag (set|refuse|ignore), enforceClamps"),
			  TEXT("MifBridgeNodes5.cpp"), 966, nullptr },
			{ TEXT("set_spline_points"), GMifDescKeys_set_spline_points, GMifDescNotes_set_spline_points,
			  TEXT("actorPath (alias: actor), component (alias: componentName), points:[{x,y,z},...] (at least 2), space (\"world\"|\"local\"), pointType (\"curve\"|\"linear\"|\"constant\"|\"curveClamped\"|\"curveCustomTangent\"), closedLoop (aliases: closed, loop), snapToGround (bool, needs space:\"world\"), groundOffset (number)"),
			  TEXT("MifBridgeWorld.cpp"), 240, nullptr },
			{ TEXT("set_sublevel_streaming"), GMifDescKeys_set_sublevel_streaming, nullptr,
			  TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\")"),
			  TEXT("MifBridgeStreaming.cpp"), 1142, nullptr },
			{ TEXT("set_sublevel_visibility"), GMifDescKeys_set_sublevel_visibility, nullptr,
			  TEXT("path (packagePath, level), visible (editorVisible), shouldBeLoaded, shouldBeVisible, lightingScenario"),
			  TEXT("MifBridgeStreaming.cpp"), 858, nullptr },
			{ TEXT("set_texture_settings"), GMifDescKeys_set_texture_settings, GMifDescNotes_set_texture_settings,
			  TEXT("path (aliases: assetPath, objectPath, texturePath), compressionSettings (alias: compression), srgb, lodGroup (alias: textureGroup), neverStream, mipGenSettings (alias: mipGen), filter, save"),
			  TEXT("MifBridgeImport.cpp"), 1604, nullptr },
			{ TEXT("set_variable_default"), GMifDescKeys_set_variable_default, nullptr,
			  TEXT("blueprintId (alias: path), name, value (aliases: default, defaultValue)"),
			  TEXT("MifBridgeIntrospect.cpp"), 1276, nullptr },
			{ TEXT("set_variable_type"), GMifDescKeys_set_variable_type, GMifDescNotes_set_variable_type,
			  TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), function? (required when scope=local) - retypes an EXISTING variable in place, keeping its Get/Set nodes"),
			  TEXT("MifBridgeIntrospect.cpp"), 1300, nullptr },
			{ TEXT("retarget_variable_node"), GMifDescKeys_retarget_variable_node, GMifDescNotes_retarget_variable_node,
			  TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), targetClass (alias: class) OR self:true - repoints one variable Get/Set node's FMemberReference at a different declaring class"),
			  TEXT("MifBridgeIntrospect.cpp"), 1450, nullptr },
			{ TEXT("set_variable_flags"), GMifDescKeys_set_variable_flags, GMifDescNotes_set_variable_flags,
			  TEXT("blueprintId (alias: path), name (aliases: var, variable), then any of replicated, repNotify, repNotifyFunction, replicationCondition, saveGame, transient, config, instanceEditable, blueprintReadOnly, exposeOnSpawn, advancedDisplay, interp, deprecated, category, tooltip - PARTIAL UPDATE: only the keys actually present are applied, the rest are left alone"),
			  TEXT("MifBridgeIntrospect.cpp"), 867, nullptr },
			{ TEXT("set_viewport_camera"), GMifDescKeys_set_viewport_camera, GMifDescNotes_set_viewport_camera,
			  TEXT("location:{x,y,z}, rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z} (wins over rotation), fov, ortho (top/bottom/front/back/left/right/perspective), orthoZoom"),
			  TEXT("MifBridgeViewport.cpp"), 84, nullptr },
			{ TEXT("set_widget_is_variable"), GMifDescKeys_set_widget_is_variable, GMifDescNotes_set_widget_is_variable,
			  TEXT("blueprintId (alias: path), widgetName, isVariable (default true)"),
			  TEXT("MifBridgeWidgets.cpp"), 54, nullptr },
			{ TEXT("shader_compile_status"), GMifDescKeys_shader_compile_status, nullptr,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeMaterials.cpp"), 1667, nullptr },
			{ TEXT("snap_actors_to_ground"), GMifDescKeys_snap_actors_to_ground, GMifDescNotes_snap_actors_to_ground,
			  TEXT("actorPaths:[...], folder, labelContains, all (bool), offset (number), traceHeight (number), alignToNormal (bool), groundActor (alias: ground), allowAnyHit (bool)"),
			  TEXT("MifBridgeWorld.cpp"), 428, nullptr },
			{ TEXT("spawn_actor_in_level"), GMifDescKeys_spawn_actor_in_level, GMifDescNotes_spawn_actor_in_level,
			  TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, folder"),
			  TEXT("MifBridgeLevel.cpp"), 213, nullptr },
			{ TEXT("spawn_actor_in_pie"), GMifDescKeys_spawn_actor_in_pie, GMifDescNotes_spawn_actor_in_pie,
			  TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, netMode (server|client|any; default server)"),
			  TEXT("MifBridgePIE.cpp"), 564, nullptr },
			{ TEXT("spawn_many"), GMifDescKeys_spawn_many, GMifDescNotes_spawn_many,
			  TEXT("items[] (required), actorClass, mesh, material, folder, labelPrefix"),
			  TEXT("MifBridgeAuthoring.cpp"), 194, nullptr },
			{ TEXT("splice_into_exec"), GMifDescKeys_splice_into_exec, GMifDescNotes_splice_into_exec,
			  TEXT("afterNode, insertNode, graphId (optional), afterPin (alias: afterExecOut; default \"then\"), insertExecIn (aliases: insertIn, execIn; default \"execute\"), insertExecOut (aliases: insertOut, execOut; default \"then\")"),
			  TEXT("MifBridgeNodes.cpp"), 1749, nullptr },
			{ TEXT("start_pie"), GMifDescKeys_start_pie, GMifDescNotes_start_pie,
			  TEXT("simulate, startLocation {x,y,z}, startRotation {x,y,z}, players (1-8), netMode (standalone|listen|client; default listen when players>1), oneProcess (default true), width, height (client window size, multiplayer only)"),
			  TEXT("MifBridgePIE.cpp"), 205, nullptr },
			{ TEXT("stop_pie"), GMifDescKeys_stop_pie, GMifDescNotes_stop_pie,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgePIE.cpp"), 330, nullptr },
			{ TEXT("thumbnail_capabilities"), GMifDescKeys_thumbnail_capabilities, nullptr,
			  TEXT("asset (aliases: assetPath, path) — optional; omit for editor-wide capability only"),
			  TEXT("MifBridgeThumbnail.cpp"), 695, nullptr },
			{ TEXT("trace_ground"), GMifDescKeys_trace_ground, GMifDescNotes_trace_ground,
			  TEXT("x, y (or location:{x,y,z}, whose z seeds fromZ), fromZ, toZ, ignoreActor (alias: actorPath)"),
			  TEXT("MifBridgeSpatial.cpp"), 225, nullptr },
			{ TEXT("trigger_cook"), GMifDescKeys_trigger_cook, GMifDescNotes_trigger_cook,
			  TEXT("mod, asset - both optional, and both only fill placeholders in the returned command plan (this endpoint executes nothing)"),
			  TEXT("MifBridgePipeline.cpp"), 112, nullptr },
			{ TEXT("undo_transactions"), GMifDescKeys_undo_transactions, nullptr,
			  TEXT("count (aliases: n, steps), toIndex (alias: to_index), allowRedo (aliases: allow_redo, canRedo)"),
			  TEXT("MifBridgeUndo.cpp"), 207, nullptr },
			{ TEXT("validate"), GMifDescKeys_validate, GMifDescNotes_validate,
			  TEXT("blueprintId (alias: path) - compiles WITHOUT saving and returns the same {ok, numErrors, numWarnings, messages[]} as compile, plus dryRun:true"),
			  TEXT("MifBridgeIntrospect.cpp"), 1612, nullptr },
			{ TEXT("write_datatable_rows"), GMifDescKeys_write_datatable_rows, nullptr,
			  TEXT("path, rows, replace, confirm"),
			  TEXT("MifBridgeDataTables.cpp"), 507, nullptr },
			{ TEXT("write_thumbnail_texture"), GMifDescKeys_write_thumbnail_texture, GMifDescNotes_write_thumbnail_texture,
			  TEXT("asset (aliases: assetPath, path), texturePath (alias: outputPath), width, height, orbitPitch, orbitYaw, orbitZoom, flushTextures, alpha, srgb, compression, lodGroup, generateMips, overwrite, save"),
			  TEXT("MifBridgeThumbnail.cpp"), 879, nullptr },
		};
		const FMifDescribeRow* MifDescribeFindRow(const FString& Endpoint)
		{
			// 206 case-insensitive compares. Linear beats a sorted-array search's maintenance risk here:
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
