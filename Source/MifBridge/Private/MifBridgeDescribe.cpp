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
// RejectUnknownParams inside a handler body, and only a minority of handlers call it: 93 guard sites
// outside this file, covering 95 of 208 MIF_DECL'd endpoints (two guards sit in shared helper bodies
// serving two endpoints each - see HARVEST below). The table below therefore has 96 rows: those 95,
// plus describe_endpoint's own.
//
// THESE THREE NUMBERS ARE A SNAPSHOT AND THEY HAVE ALREADY GONE STALE ONCE. They read 83/85/86 while
// ten endpoints - capture_camera, set_pin_type, import_texture, import_asset, reimport_asset,
// set_texture_settings, thumbnail_capabilities, render_thumbnail, write_thumbnail_texture,
// set_asset_thumbnail - had acquired guards in the same wave this table was harvested in, and every
// one of them was answered "does not call RejectUnknownParams" when it in fact rejects unknown keys
// outright. Re-derive before trusting them: `grep -rc "RejectUnknownParams(" Private/*.cpp` (minus
// this file's own guard) for the sites, and `grep -c "^\s*MIF_DECL(" Private/MifBridgeHandlers.h`
// for the denominator. Nothing in the DLL can check them for you - see COVERAGE below for why.
//
// Counting note, because "84 of 199" was once the figure in circulation and this file disagreed with
// it: counting GUARD SITES and counting ENDPOINTS THAT REACH A GUARD differ by two. The gap is the
// two shared bodies - DoAddVariableNode (add_variable_get, add_variable_set) and DoConnect
// (connect_pins, reconnect_pin) - where one guard site serves two endpoints each. Attributing a
// guard to the nearest preceding H_ function, which is the obvious way to scan for this, gets all
// four of those wrong AND silently misattributes any guard written inside a helper that happens to
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

		static const TCHAR* const GMifDescKeys_add_branch[] = {
			TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_branch[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("condition"), TEXT("the Condition input is a pin — place the node, then set_pin_default or connect_pins"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_cast[] = {
			TEXT("graphId"), TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("castTo"), TEXT("to"),
			TEXT("targetType"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_cast[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("pure"), TEXT("add_cast always creates an IMPURE cast so the Cast Failed exec pin exists; there is no pure option here"),
			TEXT("object"), TEXT("the object to cast is a pin — place the node, then connect_pins into its Object pin"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_foliage_instances[] = {
			TEXT("mesh"), TEXT("staticMesh"), TEXT("instances"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_add_foliage_instances[] = {
			TEXT("material"), TEXT("not implemented — the HISM uses the mesh's own materials; override them with set_property on the component afterwards"),
			TEXT("transforms"), TEXT("the array parameter is called instances[]"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_function_call[] = {
			TEXT("graphId"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("targetClass"), TEXT("ownerClass"),
			TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method"), TEXT("asMessage"), TEXT("message"),
			TEXT("x"), TEXT("y"), nullptr };
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
		static const TCHAR* const GMifDescKeys_add_macro_instance[] = {
			TEXT("graphId"), TEXT("macroGraph"), TEXT("macro"), TEXT("macroName"), TEXT("name"), TEXT("macroPath"),
			TEXT("macroLibrary"), TEXT("library"), TEXT("path"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_macro_instance[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_material_expression[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("class"), TEXT("expressionClass"), TEXT("type"),
			TEXT("x"), TEXT("nodePosX"), TEXT("posX"), TEXT("y"), TEXT("nodePosY"), TEXT("posY"), TEXT("properties"),
			TEXT("props"), TEXT("asset"), TEXT("selectedAsset"), nullptr };
		static const TCHAR* const GMifDescKeys_add_override_event[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("event"), TEXT("eventName"), TEXT("name"), TEXT("function"),
			TEXT("functionName"), TEXT("interfaceOrParent"), TEXT("class"), TEXT("cls"), TEXT("className"),
			TEXT("parentClass"), TEXT("interface"), TEXT("ownerClass"), TEXT("targetClass"), TEXT("callParent"),
			TEXT("addParentCall"), TEXT("withParentCall"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_override_event[] = {
			TEXT("graphId"), TEXT("an override always lands in the blueprint's event graph — pass blueprintId instead"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_parent_call[] = {
			TEXT("graphId"), TEXT("parentClass"), TEXT("class"), TEXT("cls"), TEXT("className"), TEXT("parent"),
			TEXT("ownerClass"), TEXT("targetClass"), TEXT("function"), TEXT("functionName"), TEXT("func"), TEXT("method"),
			TEXT("name"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_parent_call[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_pin[] = {
			TEXT("name"), TEXT("pin"), TEXT("pinName"), TEXT("type"), TEXT("pinType"), TEXT("container"), TEXT("valueType"),
			TEXT("direction"), TEXT("dir"), TEXT("default"), TEXT("defaultValue"), TEXT("value"), TEXT("nodeGuid"),
			TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("blueprintId"), TEXT("path"), TEXT("function"),
			TEXT("functionName"), nullptr };
		static const TCHAR* const GMifDescNotes_add_pin[] = {
			TEXT("confirm"), TEXT("add_pin is additive and needs no confirm; remove_pin is the one that does"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_sublevel[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class"), TEXT("location"),
			TEXT("rotation"), nullptr };
		static const TCHAR* const GMifDescKeys_add_variable[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("type"), TEXT("container"), TEXT("valueType"),
			TEXT("scope"), TEXT("function"), TEXT("default"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable[] = {
			TEXT("class"),       TEXT("the class belongs IN the type string, not in its own key: type:\"object:SceneComponent\". Prefixes: object:X, class:X, subclassof:X, softobject:X, softclass:X"),
			TEXT("className"),   TEXT("use type:\"object:X\" (or class:X / subclassof:X / softobject:X / softclass:X)"),
			TEXT("parentClass"), TEXT("add_variable does not take a parent class. For a typed object variable use type:\"object:X\"; to override a parent's event use add_override_event"),
			TEXT("objectClass"), TEXT("use type:\"object:X\""),
			TEXT("subType"),     TEXT("use type:\"object:X\" for the referenced class, or valueType for a map's value type"),
			nullptr };
		static const TCHAR* const GMifDescKeys_create_function[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("inputs"), TEXT("outputs"), TEXT("pure"), nullptr };
		static const TCHAR* const GMifDescNotes_create_function[] = {
			TEXT("override"),    TEXT("create_function makes a NEW function; it cannot override. Use add_override_event {event, parentClass?, callParent?}"),
			TEXT("parentClass"), TEXT("create_function does not take a parent class. add_override_event accepts parentClass (aliases: class, interfaceOrParent, ownerClass, targetClass)"),
			TEXT("interface"),   TEXT("to implement an interface function use implement_interface_function; to override a parent event use add_override_event"),
			TEXT("event"),       TEXT("events live in the event graph - use add_custom_event for a new one, or add_override_event to override a parent's"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_variable_get[] = {
			TEXT("graphId"), TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"), TEXT("property"),
			TEXT("propertyName"), TEXT("member"), TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"),
			TEXT("ownerClass"), TEXT("objectClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable_get[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("target"), TEXT("targetClass names the CLASS that owns the property; the OBJECT is wired into the node's Target pin with connect_pins, never passed here"),
			TEXT("value"), TEXT("a Set node takes its value on a pin — place the node, then set_pin_default or connect_pins"),
			TEXT("scope"), TEXT("scope is auto-detected: a variable declared on this function graph resolves as a local, anything else as a member"),
			nullptr };
		static const TCHAR* const GMifDescKeys_add_variable_set[] = {
			TEXT("graphId"), TEXT("var"), TEXT("name"), TEXT("variable"), TEXT("varName"), TEXT("property"),
			TEXT("propertyName"), TEXT("member"), TEXT("targetClass"), TEXT("class"), TEXT("cls"), TEXT("className"),
			TEXT("ownerClass"), TEXT("objectClass"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescNotes_add_variable_set[] = {
			TEXT("graph"), TEXT("spell it graphId"),
			TEXT("target"), TEXT("targetClass names the CLASS that owns the property; the OBJECT is wired into the node's Target pin with connect_pins, never passed here"),
			TEXT("value"), TEXT("a Set node takes its value on a pin — place the node, then set_pin_default or connect_pins"),
			TEXT("scope"), TEXT("scope is auto-detected: a variable declared on this function graph resolves as a local, anything else as a member"),
			nullptr };
		static const TCHAR* const GMifDescKeys_audit_unused[] = {
			TEXT("pathPrefix"), TEXT("class"), TEXT("includeAll"), TEXT("limit"), TEXT("rescan"), TEXT("excludeReferencers"),
			TEXT("excludeReferencer"), TEXT("ignoreReferencers"), nullptr };
		static const TCHAR* const GMifDescKeys_batch[] = {
			TEXT("ops"), TEXT("blueprintId"), TEXT("path"), TEXT("backup"), TEXT("compileAtEnd"), nullptr };
		static const TCHAR* const GMifDescNotes_batch[] = {
			TEXT("operations"), TEXT("spell it ops"),
			TEXT("graphId"), TEXT("graphId belongs on each op inside ops, not on the batch envelope"),
			nullptr };
		static const TCHAR* const GMifDescKeys_capture_camera[] = {
			TEXT("x"), TEXT("y"), TEXT("z"), TEXT("location"), TEXT("rotation"), TEXT("lookAt"),
			TEXT("useViewportCamera"), TEXT("useViewport"), TEXT("fromViewport"),
			TEXT("fov"), TEXT("width"), TEXT("height"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescNotes_capture_camera[] = {
			TEXT("showFlags"), TEXT("not implemented — capture_camera always renders lit/tonemapped with Atmosphere+Fog on and does NOT read the level viewport's show flags; set_view_mode does not reach this image"),
			TEXT("viewMode"), TEXT("not implemented — same gap as showFlags: the viewport's view mode is not consumed here"),
			TEXT("actorPath"), TEXT("not a parameter of this endpoint — to frame an actor, read get_actor_bounds and pass its origin as lookAt, or focus_viewport the actor and then capture with useViewportCamera:true"),
			nullptr };
		static const TCHAR* const GMifDescKeys_connect_material_expressions[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("from"), TEXT("fromExpression"), TEXT("fromOutput"),
			TEXT("fromOutputName"), TEXT("to"), TEXT("toExpression"), TEXT("toInput"), TEXT("toInputName"), nullptr };
		static const TCHAR* const GMifDescKeys_connect_material_property[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("from"), TEXT("fromExpression"), TEXT("fromOutput"),
			TEXT("fromOutputName"), TEXT("property"), TEXT("materialProperty"), nullptr };
		static const TCHAR* const GMifDescKeys_connect_pins[] = {
			TEXT("srcNode"), TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin"), TEXT("dstNode"), TEXT("dstPin"),
			TEXT("destPin"), TEXT("toPin"), TEXT("graphId"), TEXT("path"), nullptr };
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
			TEXT("path"), TEXT("parentClass"), TEXT("blueprintType"), nullptr };
		static const TCHAR* const GMifDescNotes_create_blueprint[] = {
			TEXT("overwrite"), TEXT("NOT supported — this endpoint refuses to clobber an existing asset. delete_asset the old one first, or pick a new path"),
			TEXT("name"), TEXT("the asset name is the last segment of path"),
			TEXT("parent"), TEXT("the base class parameter is called parentClass"),
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
			TEXT("parent"), TEXT("parentMaterial"), TEXT("path"), TEXT("scalars"), TEXT("vectors"), nullptr };
		static const TCHAR* const GMifDescNotes_create_material_instance[] = {
			TEXT("textures"), TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property"),
			TEXT("texture"), TEXT("texture parameter overrides are NOT implemented — create the instance, then set TextureParameterValues with set_property"),
			TEXT("material"), TEXT("the source material parameter is called parent (alias: parentMaterial)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_delete_datatable_rows[] = {
			TEXT("path"), TEXT("rowNames"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_delete_datatable_rows[] = {
			TEXT("rows"), TEXT("delete takes row NAMES, not row objects — pass rowNames:[\"A\",\"B\"]"),
			TEXT("rowName"), TEXT("the parameter is the array rowNames[]; pass a single-element array"),
			TEXT("dataTable"), TEXT("the datatable parameter is called path"),
			TEXT("table"), TEXT("the datatable parameter is called path"),
			nullptr };
		static const TCHAR* const GMifDescKeys_delete_material_expression[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("expression"), TEXT("name"), TEXT("all"),
			TEXT("deleteAll"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_class[] = {
			TEXT("class"), TEXT("className"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_package[] = {
			TEXT("package"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_describe_property[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("class"),
			TEXT("className"), TEXT("propertyPath"), TEXT("property"), TEXT("nameContains"), TEXT("filter"),
			TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"), TEXT("includeMetadata"), TEXT("includeDefault"), nullptr };
		static const TCHAR* const GMifDescKeys_diagnose_landscape[] = {
			TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_diagnose_landscape_draws[] = {
			TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_diff_properties_vs_default[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"),
			TEXT("includeTransient"), TEXT("deep"), TEXT("recursive"), TEXT("includeChildren"), nullptr };
		static const TCHAR* const GMifDescKeys_disconnect_pin[] = {
			TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"), TEXT("pinName"),
			TEXT("name"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_duplicate_actors[] = {
			TEXT("actorPaths"), TEXT("labelPrefix"), TEXT("offset"), TEXT("yawOffset"), TEXT("count"), TEXT("labelSuffix"),
			TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_duplicate_actors[] = {
			TEXT("rotationOffset"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>"),
			TEXT("rotation"), TEXT("not implemented — duplicate_actors rotates about Z only: pass yawOffset:<degrees>"),
			TEXT("scale"), TEXT("not implemented — copies keep the source actor's scale"),
			nullptr };
		static const TCHAR* const GMifDescKeys_edit_container[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("propertyPath"), TEXT("property"), TEXT("operation"), TEXT("action"), TEXT("index"), TEXT("at"),
			TEXT("count"), TEXT("key"), TEXT("newKey"), TEXT("value"), TEXT("swapWith"), TEXT("newSize"),
			TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"), nullptr };
		static const TCHAR* const GMifDescNotes_edit_container[] = {
			TEXT("op"), TEXT("this endpoint's verb is 'operation' (alias 'action'), NOT 'op' - 'op' is batch's routing key and is tolerated centrally, so an endpoint that used it would be un-diagnosable inside batch"),
			nullptr };
		static const TCHAR* const GMifDescKeys_find_assets[] = {
			TEXT("class"), TEXT("className"), TEXT("type"), TEXT("pathPrefix"), TEXT("nameContains"), TEXT("origin"),
			TEXT("recursiveClasses"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_find_assets[] = {
			TEXT("recursive"), TEXT("not implemented - pathPrefix matching is ALWAYS recursive; recursiveClasses controls class-hierarchy matching"),
			nullptr };
		static const TCHAR* const GMifDescKeys_get_datatable_row[] = {
			TEXT("path"), TEXT("rowName"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_get_dependencies[] = {
			TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_get_inherited_component[] = {
			TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"), TEXT("componentName"),
			TEXT("name"), nullptr };
		static const TCHAR* const GMifDescKeys_get_property[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("propertyPath"), TEXT("property"), nullptr };
		static const TCHAR* const GMifDescKeys_get_referencers[] = {
			TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_import_asset[] = {
			TEXT("file"), TEXT("filename"), TEXT("sourcePath"),
			TEXT("destination"), TEXT("destinationPath"), TEXT("path"),
			TEXT("name"), TEXT("destinationName"), TEXT("factory"),
			TEXT("replaceExisting"), TEXT("overwrite"), TEXT("replaceExistingSettings"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_import_asset[] = {
			TEXT("async"), TEXT("not implemented and deliberately so — this server runs handlers synchronously inside the HTTP ticker, and UAssetImportTask::GetObjects() BLOCKS on an async import (AssetImportTask.h:78). Imports here always run bAsync:false, one long frame."),
			TEXT("skeletal"), TEXT("not implemented — forcing static-vs-skeletal FBX needs a UFbxImportUI options object wired into the task; today the FBX factory's own detection decides. Import, then adjust, or pass an explicit factory."),
			TEXT("options"), TEXT("not implemented — per-factory option objects (UFbxImportUI etc.) are not exposed yet"),
			TEXT("base64"), TEXT("not supported here — import_asset imports a FILE through a UFactory. For inline image bytes use import_texture {base64, destPath}."),
			nullptr };
		static const TCHAR* const GMifDescKeys_import_texture[] = {
			TEXT("destPath"), TEXT("path"), TEXT("assetPath"),
			TEXT("sourcePath"), TEXT("file"), TEXT("filename"),
			TEXT("base64"), TEXT("data"), TEXT("bytes"),
			TEXT("format"), TEXT("overwrite"), TEXT("replaceExisting"), TEXT("save"),
			TEXT("compressionSettings"), TEXT("compression"), TEXT("srgb"), TEXT("sRGB"),
			TEXT("lodGroup"), TEXT("textureGroup"), TEXT("neverStream"),
			TEXT("mipGenSettings"), TEXT("mipGen"), TEXT("filter"), nullptr };
		static const TCHAR* const GMifDescNotes_import_texture[] = {
			TEXT("width"), TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales"),
			TEXT("height"), TEXT("not a parameter — dimensions come from the image itself; import_texture never rescales"),
			TEXT("textureClass"), TEXT("not implemented — import_texture creates UTexture2D only (cubemaps/volumes/render targets are not source-media imports)"),
			nullptr };
		static const TCHAR* const GMifDescKeys_invoke_editor_command[] = {
			TEXT("context"), TEXT("command"), TEXT("menu"), TEXT("section"), TEXT("entry"), TEXT("dryRun"), TEXT("confirm"),
			TEXT("allowKnownModal"), nullptr };
		static const TCHAR* const GMifDescNotes_invoke_editor_command[] = {
			TEXT("commandList"), TEXT("not a parameter — the list is found automatically (cache), or via menu/section/entry"),
			TEXT("key"), TEXT("sending a keystroke is send_editor_key, not this endpoint"),
			nullptr };
		static const TCHAR* const GMifDescKeys_invoke_editor_tab[] = {
			TEXT("tabId"), TEXT("tab"), TEXT("manager"), TEXT("majorTab"), TEXT("asset"), TEXT("probe"), TEXT("probeIds"),
			TEXT("includeKnownIds"), TEXT("asInactive"), nullptr };
		static const TCHAR* const GMifDescNotes_invoke_editor_tab[] = {
			TEXT("command"), TEXT("invoking a bound command is invoke_editor_command"),
			TEXT("close"), TEXT("closing a tab is not implemented — SDockTab::RequestCloseTab can run a third-party OnCanCloseTab that shows a dialog"),
			nullptr };
		static const TCHAR* const GMifDescKeys_layout_material_expressions[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), nullptr };
		static const TCHAR* const GMifDescKeys_list_components[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("component"), TEXT("componentName"), TEXT("includeInherited"),
			TEXT("includeNative"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescKeys_list_dirty_packages[] = {
			TEXT("kind"), nullptr };
		static const TCHAR* const GMifDescKeys_list_editor_commands[] = {
			TEXT("context"), TEXT("command"), TEXT("filter"), TEXT("includeUnbound"), TEXT("includeCanExecute"),
			TEXT("includeConsole"), TEXT("consolePrefix"), TEXT("menu"), TEXT("section"), TEXT("limit"), nullptr };
		static const TCHAR* const GMifDescNotes_list_editor_commands[] = {
			TEXT("tabId"), TEXT("tabs are a different registry — use invoke_editor_tab {probe:true}"),
			TEXT("entry"), TEXT("pass menu (and optionally section); every entry in it is listed"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_enum_values[] = {
			TEXT("enum"), TEXT("enumName"), nullptr };
		static const TCHAR* const GMifDescKeys_list_material_expressions[] = {
			TEXT("path"), TEXT("material"), TEXT("materialPath"), TEXT("includeConnections"), TEXT("includeProperties"), nullptr };
		static const TCHAR* const GMifDescKeys_list_mounted_containers[] = { nullptr };
		static const TCHAR* const GMifDescKeys_list_object_properties[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("nameContains"), TEXT("filter"), TEXT("nameFilter"), TEXT("limit"), TEXT("maxValueChars"), nullptr };
		static const TCHAR* const GMifDescNotes_list_object_properties[] = {
			TEXT("propertyPath"), TEXT("list_object_properties dumps ALL top-level properties; get_property reads ONE by dot path, and describe_property reports its flags/metadata/EditCondition"),
			nullptr };
		static const TCHAR* const GMifDescKeys_list_sublevels[] = {
			TEXT("world"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescKeys_list_transactions[] = {
			TEXT("limit"), TEXT("count"), TEXT("max"), TEXT("offset"), TEXT("start"), TEXT("includeObjects"),
			TEXT("include_objects"), nullptr };
		static const TCHAR* const GMifDescKeys_move_node[] = {
			TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("x"), TEXT("y"), nullptr };
		static const TCHAR* const GMifDescKeys_override_inherited_component[] = {
			TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"), TEXT("componentName"),
			TEXT("name"), TEXT("properties"), TEXT("props"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_override_inherited_component[] = {
			TEXT("propertyPath"), TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single dot-path write against the returned overrideTemplatePath"),
			TEXT("value"), TEXT("this endpoint takes a 'properties' OBJECT (name -> value); use set_property for a single named write"),
			nullptr };
		static const TCHAR* const GMifDescKeys_pie_load_level_instance[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("location"), TEXT("rotation"), TEXT("visible"),
			TEXT("netMode"), TEXT("nameOverride"), TEXT("tempPackage"), nullptr };
		static const TCHAR* const GMifDescKeys_pie_unload_level_instance[] = {
			TEXT("instanceName"), TEXT("name"), TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("objectPath"),
			TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescKeys_read_datatable[] = {
			TEXT("path"), TEXT("maxRows"), TEXT("textFormat"), TEXT("textMode"), TEXT("simpleText"), TEXT("op"), nullptr };
		static const TCHAR* const GMifDescKeys_recompile_material[] = {
			TEXT("path"), TEXT("material"), TEXT("asset"), nullptr };
		static const TCHAR* const GMifDescKeys_reconnect_pin[] = {
			TEXT("srcNode"), TEXT("srcPin"), TEXT("sourcePin"), TEXT("fromPin"), TEXT("dstNode"), TEXT("dstPin"),
			TEXT("destPin"), TEXT("toPin"), TEXT("graphId"), TEXT("path"), nullptr };
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
			TEXT("path"), TEXT("assetPath"), TEXT("objectPath"),
			TEXT("sourceFile"), TEXT("file"), TEXT("newFile"),
			TEXT("sourceFileIndex"), TEXT("forceNewFile"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_reimport_asset[] = {
			TEXT("askForNewFileIfMissing"), TEXT("not settable — it would open a file-picker MODAL, which freezes the editor and this bridge with it. Pass sourceFile instead."),
			TEXT("showNotification"), TEXT("not settable — always false; the response IS the notification"),
			nullptr };
		static const TCHAR* const GMifDescKeys_remove_node[] = {
			TEXT("nodeGuid"), TEXT("node"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_pin[] = {
			TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"), TEXT("pinName"),
			TEXT("name"), TEXT("direction"), TEXT("dir"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_sublevel[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("discardUnsaved"), nullptr };
		static const TCHAR* const GMifDescKeys_remove_variable[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_rename_variable[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("oldName"), TEXT("newName"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescNotes_rename_variable[] = {
			TEXT("name"), TEXT("rename_variable needs BOTH oldName and newName; there is no single 'name'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_render_thumbnail[] = {
			TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"),
			TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"),
			TEXT("flushTextures"), TEXT("alpha"), TEXT("name"), nullptr };
		static const TCHAR* const GMifDescKeys_reset_property_to_default[] = {
			TEXT("objectPath"), TEXT("actorPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"),
			TEXT("propertyPath"), TEXT("property"), TEXT("force"), TEXT("allowEditConst"), nullptr };
		static const TCHAR* const GMifDescKeys_revert_inherited_component[] = {
			TEXT("blueprint"), TEXT("blueprintId"), TEXT("path"), TEXT("asset"), TEXT("component"), TEXT("componentName"),
			TEXT("name"), TEXT("confirm"), nullptr };
		static const TCHAR* const GMifDescKeys_run_console[] = {
			TEXT("command"), TEXT("cmd"), TEXT("world"), TEXT("captureOutput"), nullptr };
		static const TCHAR* const GMifDescNotes_run_console[] = {
			TEXT("filter"), TEXT("log-line filtering belongs to run_console_captured, which brackets GLog; this endpoint returns the command's own output device text"),
			nullptr };
		static const TCHAR* const GMifDescKeys_save_dirty_packages[] = {
			TEXT("maps"), TEXT("saveMaps"), TEXT("save_maps"), TEXT("content"), TEXT("saveContent"), TEXT("save_content"),
			TEXT("dryRun"), TEXT("dry_run"), nullptr };
		static const TCHAR* const GMifDescKeys_send_editor_key[] = {
			TEXT("key"), TEXT("confirm"), TEXT("dryRun"), TEXT("modifiers"), TEXT("userIndex"), TEXT("isRepeat"),
			TEXT("characterCode"), TEXT("keyCode"), TEXT("sendKeyUp"), nullptr };
		static const TCHAR* const GMifDescNotes_send_editor_key[] = {
			TEXT("text"), TEXT("typing a string is not implemented — ProcessKeyCharEvent per character goes into whatever currently has focus, which is unbounded; see the Batch O notes in docs/audit/06_IMPLEMENTED.md"),
			TEXT("ctrl"), TEXT("modifiers go in the modifiers object: modifiers:{ctrl:true}"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_actor_transform[] = {
			TEXT("actorPath"), TEXT("actor"), TEXT("path"), TEXT("location"), TEXT("rotation"), TEXT("scale"),
			TEXT("relative"), nullptr };
		static const TCHAR* const GMifDescNotes_set_actor_transform[] = {
			TEXT("transform"), TEXT("pass location / rotation / scale as separate keys"),
			TEXT("yaw"), TEXT("rotation accepts {pitch,yaw,roll} or {x,y,z} — there is no bare yaw here"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_asset_thumbnail[] = {
			TEXT("asset"), TEXT("assetPath"), TEXT("path"), TEXT("width"), TEXT("height"),
			TEXT("orbitPitch"), TEXT("orbitYaw"), TEXT("orbitZoom"), TEXT("flushTextures"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_set_asset_thumbnail[] = {
			TEXT("texturePath"), TEXT("this endpoint sets the asset's own Content Browser icon and writes no texture asset — use write_thumbnail_texture for that"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_current_sublevel[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), nullptr };
		static const TCHAR* const GMifDescKeys_set_material_parameter[] = {
			TEXT("material"), TEXT("materialPath"), TEXT("path"), TEXT("scalars"), TEXT("vectors"), TEXT("parameter"),
			TEXT("parameterName"), TEXT("name"), TEXT("value"), nullptr };
		static const TCHAR* const GMifDescNotes_set_material_parameter[] = {
			TEXT("textures"), TEXT("texture parameters are NOT implemented on this endpoint — it applies scalars and vectors only"),
			TEXT("texture"), TEXT("texture parameters are NOT implemented on this endpoint — it applies scalars and vectors only"),
			TEXT("switches"), TEXT("static switch parameters are NOT implemented on this endpoint — they need a static-permutation update"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_pin_default[] = {
			TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"), TEXT("graphId"), TEXT("pin"), TEXT("pinName"),
			TEXT("name"), TEXT("value"), TEXT("default"), TEXT("defaultValue"), nullptr };
		static const TCHAR* const GMifDescKeys_set_pin_type[] = {
			TEXT("graphId"),
			TEXT("node"), TEXT("nodeGuid"), TEXT("guid"), TEXT("nodeId"),
			TEXT("pin"), TEXT("pinName"), TEXT("name"),
			TEXT("type"), TEXT("container"), TEXT("valueType"), nullptr };
		static const TCHAR* const GMifDescKeys_set_property[] = {
			TEXT("objectPath"), TEXT("blueprintId"), TEXT("path"), TEXT("widgetName"), TEXT("propertyPath"), TEXT("value"),
			TEXT("overrideFlag"), TEXT("editCondition"), TEXT("override"), TEXT("enforceClamps"), TEXT("clamp"),
			TEXT("respectClamps"), nullptr };
		static const TCHAR* const GMifDescNotes_set_property[] = {
			TEXT("actorPath"), TEXT("use objectPath - a placed actor's path IS an objectPath"),
			TEXT("format"), TEXT("no output format switch here; the response always carries BOTH valueAfter (export text) and typed (typed JSON)"),
			TEXT("verify"), TEXT("not optional - every write is verified by re-export, which is what makes ok:true mean written"),
			TEXT("operation"), TEXT("set_property writes a VALUE; add/insert/remove/clear/swap/resize/setKey on a container are edit_container"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_sublevel_streaming[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("streamingClass"), TEXT("class"), nullptr };
		static const TCHAR* const GMifDescKeys_set_sublevel_visibility[] = {
			TEXT("path"), TEXT("packagePath"), TEXT("level"), TEXT("visible"), TEXT("editorVisible"), TEXT("shouldBeLoaded"),
			TEXT("shouldBeVisible"), TEXT("lightingScenario"), nullptr };
		static const TCHAR* const GMifDescKeys_set_texture_settings[] = {
			TEXT("path"), TEXT("assetPath"), TEXT("objectPath"), TEXT("texturePath"),
			TEXT("compressionSettings"), TEXT("compression"), TEXT("srgb"), TEXT("sRGB"),
			TEXT("lodGroup"), TEXT("textureGroup"), TEXT("neverStream"),
			TEXT("mipGenSettings"), TEXT("mipGen"), TEXT("filter"), TEXT("save"), nullptr };
		static const TCHAR* const GMifDescNotes_set_texture_settings[] = {
			TEXT("addressX"), TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set"),
			TEXT("addressY"), TEXT("not implemented — tiling/address modes are a separate concern from this endpoint's compression/streaming set"),
			TEXT("maxTextureSize"), TEXT("not implemented — use set_property on MaxTextureSize"),
			TEXT("lodBias"), TEXT("not implemented — use set_property on LODBias"),
			nullptr };
		static const TCHAR* const GMifDescKeys_set_variable_default[] = {
			TEXT("blueprintId"), TEXT("path"), TEXT("name"), TEXT("value"), TEXT("default"), TEXT("defaultValue"), nullptr };
		static const TCHAR* const GMifDescKeys_shader_compile_status[] = { nullptr };
		static const TCHAR* const GMifDescKeys_spawn_actor_in_level[] = {
			TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"), TEXT("mesh"),
			TEXT("staticMesh"), TEXT("label"), TEXT("folder"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_actor_in_level[] = {
			TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials"),
			TEXT("name"), TEXT("an actor's display name is 'label'; its object name is assigned by the engine"),
			nullptr };
		static const TCHAR* const GMifDescKeys_spawn_actor_in_pie[] = {
			TEXT("actorClass"), TEXT("class"), TEXT("location"), TEXT("rotation"), TEXT("scale"), TEXT("mesh"),
			TEXT("staticMesh"), TEXT("label"), TEXT("netMode"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_actor_in_pie[] = {
			TEXT("material"), TEXT("not supported here — spawn the actor, then set_property on the mesh component's OverrideMaterials"),
			TEXT("folder"), TEXT("folders are an editor-outliner concept; a PIE-spawned actor has none"),
			nullptr };
		static const TCHAR* const GMifDescKeys_spawn_many[] = {
			TEXT("items"), TEXT("actorClass"), TEXT("mesh"), TEXT("material"), TEXT("folder"), TEXT("labelPrefix"), nullptr };
		static const TCHAR* const GMifDescNotes_spawn_many[] = {
			TEXT("count"), TEXT("spawn_many places one actor per items[] entry — repeat the entry, or use duplicate_actors with count"),
			TEXT("actors"), TEXT("the array parameter is called items[]"),
			nullptr };
		static const TCHAR* const GMifDescKeys_splice_into_exec[] = {
			TEXT("afterNode"), TEXT("insertNode"), TEXT("graphId"), TEXT("afterPin"), TEXT("afterExecOut"),
			TEXT("insertExecIn"), TEXT("insertIn"), TEXT("execIn"), TEXT("insertExecOut"), TEXT("insertOut"), TEXT("execOut"), nullptr };
		static const TCHAR* const GMifDescNotes_splice_into_exec[] = {
			TEXT("beforeNode"), TEXT("splice_into_exec inserts AFTER a node — pass afterNode"),
			TEXT("node"), TEXT("this endpoint needs BOTH afterNode and insertNode; there is no single 'node'"),
			nullptr };
		static const TCHAR* const GMifDescKeys_thumbnail_capabilities[] = {
			TEXT("asset"), TEXT("assetPath"), TEXT("path"), nullptr };
		static const TCHAR* const GMifDescKeys_undo_transactions[] = {
			TEXT("count"), TEXT("n"), TEXT("steps"), TEXT("toIndex"), TEXT("to_index"), TEXT("allowRedo"), TEXT("allow_redo"),
			TEXT("canRedo"), nullptr };
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
			{ TEXT("add_branch"), GMifDescKeys_add_branch, GMifDescNotes_add_branch,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes.cpp"), 804, nullptr },
			{ TEXT("add_cast"), GMifDescKeys_add_cast, GMifDescNotes_add_cast,
			  TEXT("graphId, targetClass (aliases: class, cls, className, castTo, to, targetType), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1099, nullptr },
			{ TEXT("add_foliage_instances"), GMifDescKeys_add_foliage_instances, GMifDescNotes_add_foliage_instances,
			  TEXT("mesh (alias: staticMesh), instances[] (required), label, folder"),
			  TEXT("MifBridgeAuthoring.cpp"), 806, nullptr },
			{ TEXT("add_function_call"), GMifDescKeys_add_function_call, GMifDescNotes_add_function_call,
			  TEXT("graphId, class (aliases: cls, className, targetClass, ownerClass; default \"self\"), function (aliases: functionName, func, method), asMessage (alias: message), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 650, nullptr },
			{ TEXT("add_get_array_item"), GMifDescKeys_add_get_array_item, GMifDescNotes_add_get_array_item,
			  TEXT("graphId, x, y"),
			  TEXT("MifBridgeNodes.cpp"), 898, nullptr },
			{ TEXT("add_macro_instance"), GMifDescKeys_add_macro_instance, GMifDescNotes_add_macro_instance,
			  TEXT("graphId, macroGraph (aliases: macro, macroName, name), macroPath (aliases: macroLibrary, library, path), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 829, nullptr },
			{ TEXT("add_material_expression"), GMifDescKeys_add_material_expression, nullptr,
			  TEXT("path (aliases: material, materialPath), class (aliases: expressionClass, type), x (aliases: nodePosX, posX), y (aliases: nodePosY, posY), properties (alias: props), asset (alias: selectedAsset)"),
			  TEXT("MifBridgeMaterials.cpp"), 927, nullptr },
			{ TEXT("add_override_event"), GMifDescKeys_add_override_event, GMifDescNotes_add_override_event,
			  TEXT("blueprintId (alias: path), event (aliases: eventName, name, function, functionName), interfaceOrParent (aliases: class, cls, className, parentClass, interface, ownerClass, targetClass), callParent (aliases: addParentCall, withParentCall), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 944, nullptr },
			{ TEXT("add_parent_call"), GMifDescKeys_add_parent_call, GMifDescNotes_add_parent_call,
			  TEXT("graphId, parentClass (aliases: class, cls, className, parent, ownerClass, targetClass; default = this blueprint's parent), function (aliases: functionName, func, method, name), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1044, nullptr },
			{ TEXT("add_pin"), GMifDescKeys_add_pin, GMifDescNotes_add_pin,
			  TEXT("name (aliases: pin, pinName), type (alias: pinType), container, valueType, direction (alias: dir; input|output), default (aliases: defaultValue, value), and ONE target: nodeGuid (aliases: node, guid, nodeId) | graphId | blueprintId + function"),
			  TEXT("MifBridgeNodes.cpp"), 1217, nullptr },
			{ TEXT("add_sublevel"), GMifDescKeys_add_sublevel, nullptr,
			  TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\"), location {x,y,z}, rotation {x,y,z}"),
			  TEXT("MifBridgeStreaming.cpp"), 569, nullptr },
			{ TEXT("add_variable"), GMifDescKeys_add_variable, GMifDescNotes_add_variable,
			  TEXT("blueprintId (alias: path), name, type, container?, valueType?, scope? (member|local), function? (required when scope=local), default?"),
			  TEXT("MifBridgeIntrospect.cpp"), 785, nullptr },
			{ TEXT("add_variable_get"), GMifDescKeys_add_variable_get, GMifDescNotes_add_variable_get,
			  TEXT("graphId, var (aliases: name, variable, varName, property, propertyName, member), targetClass (aliases: class, cls, className, ownerClass, objectClass), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 369, TEXT("DoAddVariableNode") },
			{ TEXT("add_variable_set"), GMifDescKeys_add_variable_set, GMifDescNotes_add_variable_set,
			  TEXT("graphId, var (aliases: name, variable, varName, property, propertyName, member), targetClass (aliases: class, cls, className, ownerClass, objectClass), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 369, TEXT("DoAddVariableNode") },
			{ TEXT("audit_unused"), GMifDescKeys_audit_unused, nullptr,
			  TEXT("pathPrefix, class, includeAll, limit, rescan, excludeReferencers (aliases: excludeReferencer, ignoreReferencers)"),
			  TEXT("MifBridgeAssetOps.cpp"), 451, nullptr },
			{ TEXT("batch"), GMifDescKeys_batch, GMifDescNotes_batch,
			  TEXT("ops (array), blueprintId (alias: path), backup, compileAtEnd (default true)"),
			  TEXT("MifBridgeNodes.cpp"), 1824, nullptr },
			{ TEXT("capture_camera"), GMifDescKeys_capture_camera, GMifDescNotes_capture_camera,
			  TEXT("x, y, z (or location:{x,y,z}), rotation:{x,y,z} = pitch/yaw/roll, lookAt:{x,y,z}, useViewportCamera (aliases: useViewport, fromViewport), fov, width, height, name"),
			  TEXT("MifBridgeSpatial.cpp"), 299, nullptr },
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
			  TEXT("path (must start with /Game/), parentClass (default \"Actor\"), blueprintType (Normal | FunctionLibrary | Interface | MacroLibrary | WidgetBlueprint)"),
			  TEXT("MifBridgeNodes2.cpp"), 1041, nullptr },
			{ TEXT("create_function"), GMifDescKeys_create_function, GMifDescNotes_create_function,
			  TEXT("blueprintId (alias: path), name, inputs?, outputs?, pure?"),
			  TEXT("MifBridgeNodes2.cpp"), 285, nullptr },
			{ TEXT("create_material"), GMifDescKeys_create_material, nullptr,
			  TEXT("path (alias: assetPath), domain (alias: materialDomain), blendMode, initialTexture"),
			  TEXT("MifBridgeMaterials.cpp"), 743, nullptr },
			{ TEXT("create_material_function"), GMifDescKeys_create_material_function, GMifDescNotes_create_material_function,
			  TEXT("path (alias: assetPath), description, exposeToLibrary"),
			  TEXT("MifBridgeMaterials.cpp"), 865, nullptr },
			{ TEXT("create_material_instance"), GMifDescKeys_create_material_instance, GMifDescNotes_create_material_instance,
			  TEXT("parent (alias: parentMaterial), path (must start with /Game/), scalars {name:number}, vectors {name:{r,g,b,a}}"),
			  TEXT("MifBridgeAuthoring.cpp"), 448, nullptr },
			{ TEXT("delete_datatable_rows"), GMifDescKeys_delete_datatable_rows, GMifDescNotes_delete_datatable_rows,
			  TEXT("path, rowNames[], confirm=true"),
			  TEXT("MifBridgeDataTables.cpp"), 663, nullptr },
			{ TEXT("delete_material_expression"), GMifDescKeys_delete_material_expression, nullptr,
			  TEXT("path (aliases: material, materialPath), expression (alias: name), all (alias: deleteAll)"),
			  TEXT("MifBridgeMaterials.cpp"), 1235, nullptr },
			{ TEXT("describe_class"), GMifDescKeys_describe_class, nullptr,
			  TEXT("class (alias: className), filter (optional substring match)"),
			  TEXT("MifBridgeIntrospect.cpp"), 311, nullptr },
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
			{ TEXT("edit_container"), GMifDescKeys_edit_container, GMifDescNotes_edit_container,
			  TEXT("objectPath (alias actorPath), propertyPath (alias property), operation (alias action) = add|insert|remove|clear|swap|resize|setKey, index (alias at), count, key, newKey, value, swapWith, newSize, overrideFlag (set|refuse|ignore)"),
			  TEXT("MifBridgeDetails.cpp"), 1571, nullptr },
			{ TEXT("find_assets"), GMifDescKeys_find_assets, GMifDescNotes_find_assets,
			  TEXT("class (aliases: className, type), pathPrefix, nameContains, origin, recursiveClasses, limit"),
			  TEXT("MifBridgeCooked.cpp"), 243, nullptr },
			{ TEXT("get_datatable_row"), GMifDescKeys_get_datatable_row, nullptr,
			  TEXT("path, rowName, textFormat (aliases: textMode, simpleText:true)"),
			  TEXT("MifBridgeDataTables.cpp"), 424, nullptr },
			{ TEXT("get_dependencies"), GMifDescKeys_get_dependencies, nullptr,
			  TEXT("path"),
			  TEXT("MifBridgeAssetOps.cpp"), 294, nullptr },
			{ TEXT("get_inherited_component"), GMifDescKeys_get_inherited_component, nullptr,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name)"),
			  TEXT("MifBridgeInherited.cpp"), 622, nullptr },
			{ TEXT("get_property"), GMifDescKeys_get_property, nullptr,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, propertyPath (alias property)"),
			  TEXT("MifBridgeNodes6.cpp"), 43, nullptr },
			{ TEXT("get_referencers"), GMifDescKeys_get_referencers, nullptr,
			  TEXT("path"),
			  TEXT("MifBridgeAssetOps.cpp"), 263, nullptr },
			{ TEXT("import_asset"), GMifDescKeys_import_asset, GMifDescNotes_import_asset,
			  TEXT("file (aliases: filename, sourcePath), destination (aliases: destinationPath, path), name (alias: destinationName), factory, replaceExisting (alias: overwrite), replaceExistingSettings, save"),
			  TEXT("MifBridgeImport.cpp"), 1112, nullptr },
			{ TEXT("import_texture"), GMifDescKeys_import_texture, GMifDescNotes_import_texture,
			  TEXT("destPath (aliases: path, assetPath), sourcePath (aliases: file, filename) OR base64 (aliases: data, bytes), format, overwrite (alias: replaceExisting), save, compressionSettings (alias: compression), srgb, lodGroup (alias: textureGroup), neverStream, mipGenSettings (alias: mipGen), filter"),
			  TEXT("MifBridgeImport.cpp"), 782, nullptr },
			{ TEXT("invoke_editor_command"), GMifDescKeys_invoke_editor_command, GMifDescNotes_invoke_editor_command,
			  TEXT("context, command, menu, section, entry, dryRun, confirm, allowKnownModal"),
			  TEXT("MifBridgeUI.cpp"), 871, nullptr },
			{ TEXT("invoke_editor_tab"), GMifDescKeys_invoke_editor_tab, GMifDescNotes_invoke_editor_tab,
			  TEXT("tabId (alias: tab), manager (global|majorTab|assetEditor; default global), majorTab, asset, probe, probeIds[], includeKnownIds (default true), asInactive"),
			  TEXT("MifBridgeUI.cpp"), 1165, nullptr },
			{ TEXT("layout_material_expressions"), GMifDescKeys_layout_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath)"),
			  TEXT("MifBridgeMaterials.cpp"), 1488, nullptr },
			{ TEXT("list_components"), GMifDescKeys_list_components, nullptr,
			  TEXT("blueprintId (alias: path), component (alias: componentName; optional - omit for the whole list), includeInherited (default true), includeNative (default true), limit (default 500)"),
			  TEXT("MifBridgeComponents.cpp"), 493, nullptr },
			{ TEXT("list_dirty_packages"), GMifDescKeys_list_dirty_packages, nullptr,
			  TEXT("kind (content|world|all)"),
			  TEXT("MifBridgeUndo.cpp"), 467, nullptr },
			{ TEXT("list_editor_commands"), GMifDescKeys_list_editor_commands, GMifDescNotes_list_editor_commands,
			  TEXT("context, command, filter, includeUnbound (default true), includeCanExecute (default false), includeConsole (default false), consolePrefix, menu, section, limit (default 400)"),
			  TEXT("MifBridgeUI.cpp"), 567, nullptr },
			{ TEXT("list_enum_values"), GMifDescKeys_list_enum_values, nullptr,
			  TEXT("enum (alias: enumName)"),
			  TEXT("MifBridgeNodes3.cpp"), 247, nullptr },
			{ TEXT("list_material_expressions"), GMifDescKeys_list_material_expressions, nullptr,
			  TEXT("path (aliases: material, materialPath), includeConnections, includeProperties"),
			  TEXT("MifBridgeMaterials.cpp"), 1320, nullptr },
			{ TEXT("list_mounted_containers"), GMifDescKeys_list_mounted_containers, nullptr,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeCooked.cpp"), 120, nullptr },
			{ TEXT("list_object_properties"), GMifDescKeys_list_object_properties, GMifDescNotes_list_object_properties,
			  TEXT("objectPath (alias actorPath) | (blueprintId or path) + widgetName, nameContains (aliases filter, nameFilter), limit, maxValueChars"),
			  TEXT("MifBridgeNodes6.cpp"), 110, nullptr },
			{ TEXT("list_sublevels"), GMifDescKeys_list_sublevels, nullptr,
			  TEXT("world (\"editor\"|\"pie\"), netMode (\"server\"|\"client\"|\"any\", only meaningful with world:\"pie\")"),
			  TEXT("MifBridgeStreaming.cpp"), 463, nullptr },
			{ TEXT("list_transactions"), GMifDescKeys_list_transactions, nullptr,
			  TEXT("limit (aliases: count, max), offset (alias: start), includeObjects (alias: include_objects)"),
			  TEXT("MifBridgeUndo.cpp"), 120, nullptr },
			{ TEXT("move_node"), GMifDescKeys_move_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), x, y"),
			  TEXT("MifBridgeNodes.cpp"), 1143, nullptr },
			{ TEXT("override_inherited_component"), GMifDescKeys_override_inherited_component, GMifDescNotes_override_inherited_component,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), properties (alias: props), confirm"),
			  TEXT("MifBridgeInherited.cpp"), 773, nullptr },
			{ TEXT("pie_load_level_instance"), GMifDescKeys_pie_load_level_instance, nullptr,
			  TEXT("path (packagePath, level), location {x,y,z}, rotation {x,y,z}, visible (bool), netMode (\"server\"|\"client\"|\"any\"), nameOverride (string), tempPackage (bool)"),
			  TEXT("MifBridgeStreaming.cpp"), 1262, nullptr },
			{ TEXT("pie_unload_level_instance"), GMifDescKeys_pie_unload_level_instance, nullptr,
			  TEXT("instanceName (name) from pie_load_level_instance, or objectPath, or path (packagePath, level) naming the SOURCE map; netMode (\"server\"|\"client\"|\"any\")"),
			  TEXT("MifBridgeStreaming.cpp"), 1398, nullptr },
			{ TEXT("read_datatable"), GMifDescKeys_read_datatable, nullptr,
			  TEXT("path, maxRows, textFormat (aliases: textMode, simpleText:true)"),
			  TEXT("MifBridgeDataTables.cpp"), 362, nullptr },
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
			  TEXT("MifBridgeImport.cpp"), 1370, nullptr },
			{ TEXT("remove_node"), GMifDescKeys_remove_node, nullptr,
			  TEXT("nodeGuid (aliases: node, guid, nodeId), graphId (optional, disambiguates a reused guid), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes.cpp"), 1167, nullptr },
			{ TEXT("remove_pin"), GMifDescKeys_remove_pin, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), direction (alias: dir; input|output), confirm (required, must be true)"),
			  TEXT("MifBridgeNodes.cpp"), 1506, nullptr },
			{ TEXT("remove_sublevel"), GMifDescKeys_remove_sublevel, nullptr,
			  TEXT("path (packagePath, level), discardUnsaved (bool)"),
			  TEXT("MifBridgeStreaming.cpp"), 711, nullptr },
			{ TEXT("remove_variable"), GMifDescKeys_remove_variable, nullptr,
			  TEXT("blueprintId (alias: path), name, confirm=true"),
			  TEXT("MifBridgeIntrospect.cpp"), 1036, nullptr },
			{ TEXT("rename_variable"), GMifDescKeys_rename_variable, GMifDescNotes_rename_variable,
			  TEXT("blueprintId (alias: path), oldName, newName, confirm=true"),
			  TEXT("MifBridgeIntrospect.cpp"), 905, nullptr },
			{ TEXT("render_thumbnail"), GMifDescKeys_render_thumbnail, nullptr,
			  TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, flushTextures, alpha, name"),
			  TEXT("MifBridgeThumbnail.cpp"), 792, nullptr },
			{ TEXT("reset_property_to_default"), GMifDescKeys_reset_property_to_default, nullptr,
			  TEXT("objectPath (alias actorPath), propertyPath (alias property), force (alias allowEditConst)"),
			  TEXT("MifBridgeDetails.cpp"), 1059, nullptr },
			{ TEXT("revert_inherited_component"), GMifDescKeys_revert_inherited_component, nullptr,
			  TEXT("blueprint (aliases: blueprintId, path, asset), component (aliases: componentName, name), confirm"),
			  TEXT("MifBridgeInherited.cpp"), 1133, nullptr },
			{ TEXT("run_console"), GMifDescKeys_run_console, GMifDescNotes_run_console,
			  TEXT("command (alias: cmd), world (editor|pie|active; default editor), captureOutput (default true)"),
			  TEXT("MifBridgeIntrospect.cpp"), 1375, nullptr },
			{ TEXT("save_dirty_packages"), GMifDescKeys_save_dirty_packages, nullptr,
			  TEXT("maps (aliases: saveMaps, save_maps), content (aliases: saveContent, save_content), dryRun (alias: dry_run)"),
			  TEXT("MifBridgeUndo.cpp"), 561, nullptr },
			{ TEXT("send_editor_key"), GMifDescKeys_send_editor_key, GMifDescNotes_send_editor_key,
			  TEXT("key, confirm, dryRun, modifiers{ctrl,alt,shift,cmd}, userIndex (default 0), isRepeat, characterCode, keyCode, sendKeyUp (default true)"),
			  TEXT("MifBridgeUI.cpp"), 1325, nullptr },
			{ TEXT("set_actor_transform"), GMifDescKeys_set_actor_transform, GMifDescNotes_set_actor_transform,
			  TEXT("actorPath (aliases: actor, path), location, rotation, scale, relative"),
			  TEXT("MifBridgeLevel.cpp"), 329, nullptr },
			{ TEXT("set_asset_thumbnail"), GMifDescKeys_set_asset_thumbnail, GMifDescNotes_set_asset_thumbnail,
			  TEXT("asset (aliases: assetPath, path), width, height, orbitPitch, orbitYaw, orbitZoom, flushTextures, save"),
			  TEXT("MifBridgeThumbnail.cpp"), 1171, nullptr },
			{ TEXT("set_current_sublevel"), GMifDescKeys_set_current_sublevel, nullptr,
			  TEXT("path (packagePath, level) — a package path, or the literal \"persistent\""),
			  TEXT("MifBridgeStreaming.cpp"), 1043, nullptr },
			{ TEXT("set_material_parameter"), GMifDescKeys_set_material_parameter, GMifDescNotes_set_material_parameter,
			  TEXT("material (aliases: materialPath, path), scalars {name:number}, vectors {name:{r,g,b,a}}, and/or the singular pair parameter (aliases: parameterName, name) + value"),
			  TEXT("MifBridgeAuthoring.cpp"), 621, nullptr },
			{ TEXT("set_pin_default"), GMifDescKeys_set_pin_default, nullptr,
			  TEXT("node (aliases: nodeGuid, guid, nodeId), graphId (optional), pin (aliases: pinName, name), value (aliases: default, defaultValue)"),
			  TEXT("MifBridgeNodes.cpp"), 1704, nullptr },
			{ TEXT("set_pin_type"), GMifDescKeys_set_pin_type, nullptr,
			  TEXT("graphId, node (aliases: nodeGuid, guid, nodeId), pin (aliases: pinName, name), type, container?, valueType?"),
			  TEXT("MifBridgeNodes3.cpp"), 443, nullptr },
			{ TEXT("set_property"), GMifDescKeys_set_property, GMifDescNotes_set_property,
			  TEXT("objectPath | (blueprintId or path) + widgetName, propertyPath, value, overrideFlag (set|refuse|ignore), enforceClamps"),
			  TEXT("MifBridgeNodes5.cpp"), 966, nullptr },
			{ TEXT("set_sublevel_streaming"), GMifDescKeys_set_sublevel_streaming, nullptr,
			  TEXT("path (packagePath, level), streamingClass (class: \"alwaysloaded\"|\"dynamic\")"),
			  TEXT("MifBridgeStreaming.cpp"), 1142, nullptr },
			{ TEXT("set_sublevel_visibility"), GMifDescKeys_set_sublevel_visibility, nullptr,
			  TEXT("path (packagePath, level), visible (editorVisible), shouldBeLoaded, shouldBeVisible, lightingScenario"),
			  TEXT("MifBridgeStreaming.cpp"), 858, nullptr },
			{ TEXT("set_texture_settings"), GMifDescKeys_set_texture_settings, GMifDescNotes_set_texture_settings,
			  TEXT("path (aliases: assetPath, objectPath, texturePath), compressionSettings (alias: compression), srgb, lodGroup (alias: textureGroup), neverStream, mipGenSettings (alias: mipGen), filter, save"),
			  TEXT("MifBridgeImport.cpp"), 1584, nullptr },
			{ TEXT("set_variable_default"), GMifDescKeys_set_variable_default, nullptr,
			  TEXT("blueprintId (alias: path), name, value (aliases: default, defaultValue)"),
			  TEXT("MifBridgeIntrospect.cpp"), 1120, nullptr },
			{ TEXT("shader_compile_status"), GMifDescKeys_shader_compile_status, nullptr,
			  TEXT("(none - this endpoint takes no parameters)"),
			  TEXT("MifBridgeMaterials.cpp"), 1667, nullptr },
			{ TEXT("spawn_actor_in_level"), GMifDescKeys_spawn_actor_in_level, GMifDescNotes_spawn_actor_in_level,
			  TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, folder"),
			  TEXT("MifBridgeLevel.cpp"), 204, nullptr },
			{ TEXT("spawn_actor_in_pie"), GMifDescKeys_spawn_actor_in_pie, GMifDescNotes_spawn_actor_in_pie,
			  TEXT("actorClass (alias: class), location, rotation, scale, mesh (alias: staticMesh), label, netMode (server|client|any; default server)"),
			  TEXT("MifBridgePIE.cpp"), 514, nullptr },
			{ TEXT("spawn_many"), GMifDescKeys_spawn_many, GMifDescNotes_spawn_many,
			  TEXT("items[] (required), actorClass, mesh, material, folder, labelPrefix"),
			  TEXT("MifBridgeAuthoring.cpp"), 194, nullptr },
			{ TEXT("splice_into_exec"), GMifDescKeys_splice_into_exec, GMifDescNotes_splice_into_exec,
			  TEXT("afterNode, insertNode, graphId (optional), afterPin (alias: afterExecOut; default \"then\"), insertExecIn (aliases: insertIn, execIn; default \"execute\"), insertExecOut (aliases: insertOut, execOut; default \"then\")"),
			  TEXT("MifBridgeNodes.cpp"), 1749, nullptr },
			{ TEXT("thumbnail_capabilities"), GMifDescKeys_thumbnail_capabilities, nullptr,
			  TEXT("asset (aliases: assetPath, path) — optional; omit for editor-wide capability only"),
			  TEXT("MifBridgeThumbnail.cpp"), 695, nullptr },
			{ TEXT("undo_transactions"), GMifDescKeys_undo_transactions, nullptr,
			  TEXT("count (aliases: n, steps), toIndex (alias: to_index), allowRedo (aliases: allow_redo, canRedo)"),
			  TEXT("MifBridgeUndo.cpp"), 207, nullptr },
			{ TEXT("write_datatable_rows"), GMifDescKeys_write_datatable_rows, nullptr,
			  TEXT("path, rows, replace, confirm"),
			  TEXT("MifBridgeDataTables.cpp"), 495, nullptr },
			{ TEXT("write_thumbnail_texture"), GMifDescKeys_write_thumbnail_texture, GMifDescNotes_write_thumbnail_texture,
			  TEXT("asset (aliases: assetPath, path), texturePath (alias: outputPath), width, height, orbitPitch, orbitYaw, orbitZoom, flushTextures, alpha, srgb, compression, lodGroup, generateMips, overwrite, save"),
			  TEXT("MifBridgeThumbnail.cpp"), 879, nullptr },
		};
		const FMifDescribeRow* MifDescribeFindRow(const FString& Endpoint)
		{
			// 96 case-insensitive compares. Linear beats a sorted-array search's maintenance risk here:
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

#undef MIF_DESCRIBE_OWN_KEYS
#undef MIF_DESCRIBE_OWN_SUMMARY
}   // namespace MifBridge
