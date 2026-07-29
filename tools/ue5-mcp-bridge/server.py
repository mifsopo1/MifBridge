#!/usr/bin/env python3
"""MifBridge MCP server.

Thin FastMCP wrapper over the in-editor MifBridge HTTP plugin. One tool per
plugin endpoint (see docs/13_UE5_MCP_BRIDGE_PLUGIN.md section 9). No game logic
lives here: every tool forwards its arguments as JSON to the local bridge and
returns the bridge's JSON verbatim so Claude sees the real compiler output.

Config (environment):
  MIF_BRIDGE_URL    default http://127.0.0.1:8791/api
  MIF_BRIDGE_TOKEN  default "dev"  (must match the editor's MIF_BRIDGE_TOKEN)
  MIF_BRIDGE_TIMEOUT default 30    (seconds)

Run:  python server.py [--debug]
"""

import argparse
import os
import sys
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("MIF_BRIDGE_URL", "http://127.0.0.1:8791/api").rstrip("/")
TOKEN = os.environ.get("MIF_BRIDGE_TOKEN", "dev")
try:
    TIMEOUT = float(os.environ.get("MIF_BRIDGE_TIMEOUT", "30"))
except ValueError:
    TIMEOUT = 30.0

DEBUG = False

mcp = FastMCP("mif-ue5-bridge")


def _log(*args):
    # NEVER write to stdout: it carries the MCP stdio JSON-RPC stream.
    if DEBUG:
        print("[mif-bridge]", *args, file=sys.stderr, flush=True)


def _post(endpoint: str, **payload) -> dict:
    """POST a JSON body to the bridge and return the parsed JSON response.

    Errors are surfaced as {"ok": False, "error": "..."} dicts rather than raised,
    so a dead editor reports "bridge unreachable" instead of hanging the tool call.
    """
    # Drop unset optional args so the plugin sees only what the caller provided.
    body = {k: v for k, v in payload.items() if v is not None}
    url = f"{BASE}/{endpoint}"
    _log("->", endpoint, body)
    try:
        response = requests.post(
            url,
            json=body,
            headers={"X-Mif-Token": TOKEN, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectTimeout:
        return {"ok": False, "error": f"bridge connect timeout after {TIMEOUT}s at {url}"}
    except requests.exceptions.ReadTimeout:
        return {"ok": False, "error": f"bridge read timeout after {TIMEOUT}s (editor busy compiling?)"}
    except requests.exceptions.ConnectionError as exc:
        return {"ok": False, "error": f"bridge unreachable at {url} — is the editor open with MifBridge started? ({exc})"}
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "error": f"request failed: {exc}"}

    if response.status_code == 403:
        return {"ok": False, "error": "bridge rejected request (bad X-Mif-Token or non-loopback caller)"}

    try:
        data = response.json()
    except ValueError:
        return {
            "ok": False,
            "error": f"non-JSON response (HTTP {response.status_code})",
            "body": response.text[:2000],
        }
    _log("<-", endpoint, data)
    return data


# --------------------------------------------------------------------------
# Session / assets
# --------------------------------------------------------------------------

@mcp.tool()
def open_blueprint(path: str) -> dict:
    "Open a Blueprint by asset path (e.g. /Game/Foo/BP_Bar). Returns blueprintId, class, parentClass, and graphs. Resolves redirectors."
    return _post("open_blueprint", path=path)


@mcp.tool()
def list_blueprints(filter: str = "") -> dict:
    "List Blueprint assets in the project. Optional substring filter on the object path."
    return _post("list_blueprints", filter=filter or None)


@mcp.tool()
def save_blueprint(blueprint_id: str) -> dict:
    "Save the Blueprint's package to disk."
    return _post("save_blueprint", blueprintId=blueprint_id)


@mcp.tool()
def save_package(path: str) -> dict:
    "Save ANY asset package to disk by /Game/ path (DataTables etc.). An asset loaded from a mounted pak saves as a loose Content override for cooking."
    return _post("save_package", path=path)


@mcp.tool()
def backup_blueprint(blueprint_id: str) -> dict:
    "Write a .bak copy of the Blueprint's .uasset on disk."
    return _post("backup_blueprint", blueprintId=blueprint_id)


# --------------------------------------------------------------------------
# Introspection (read-back)
# --------------------------------------------------------------------------

@mcp.tool()
def list_graphs(blueprint_id: str) -> dict:
    "List every graph (event/function/macro/delegate) in a Blueprint with its graphId."
    return _post("list_graphs", blueprintId=blueprint_id)


@mcp.tool()
def list_nodes(graph_id: str, hide_knots: bool = False) -> dict:
    "List all nodes in a graph with guid, class, title, position and pins. Set hide_knots to skip reroute nodes."
    return _post("list_nodes", graphId=graph_id, hideKnots=hide_knots)


@mcp.tool()
def get_node(node_guid: str) -> dict:
    "Return full detail (all pins, types, links) for a single node by guid."
    return _post("get_node", nodeGuid=node_guid)


@mcp.tool()
def list_variables(blueprint_id: str) -> dict:
    "List member variables with name, type, and default. Flags names with trailing whitespace or invalid characters."
    return _post("list_variables", blueprintId=blueprint_id)


@mcp.tool()
def list_functions(blueprint_id: str) -> dict:
    "List the Blueprint's function graphs."
    return _post("list_functions", blueprintId=blueprint_id)


@mcp.tool()
def find_nodes(graph_id: str, by_class: str = "", by_title: str = "", by_function: str = "") -> dict:
    "Find nodes in a graph by (substring) class name, title, or called-function name."
    return _post("find_nodes", graphId=graph_id, byClass=by_class or None,
                 byTitle=by_title or None, byFunction=by_function or None)


# --------------------------------------------------------------------------
# Variables
# --------------------------------------------------------------------------

@mcp.tool()
def add_variable(blueprint_id: str, name: str, type: str, container: str = "", value_type: str = "",
                 scope: str = "member", function: str = "", default: str = "") -> dict:
    "Add a variable. name is trimmed+validated and the canonical name is returned. type e.g. int/float/bool/string/Vector/Guid/<Struct>/<Class>. container = array|set|map. For a map, type is the KEY type and value_type is the VALUE type (e.g. type='name', container='map', value_type='int'). scope = member|local (local needs function)."
    return _post("add_variable", blueprintId=blueprint_id, name=name, type=type,
                 container=container or None, valueType=value_type or None, scope=scope, function=function or None,
                 default=default or None)


@mcp.tool()
def rename_variable(blueprint_id: str, old_name: str, new_name: str, confirm: bool = False) -> dict:
    # NOTE: refuses when old_name does not exist (it used to report ok:true for a rename that never
    # happened), when new_name equals old_name, and when the variable has a RepNotify function - the
    # engine's rename path opens a modal dialog for that case, which would hang the whole bridge.
    # Clear it first with set_variable_flags(rep_notify=False), rename, then set it again.
    "Rename a member variable. Requires confirm=True."
    return _post("rename_variable", blueprintId=blueprint_id, oldName=old_name,
                 newName=new_name, confirm=confirm)


@mcp.tool()
def remove_variable(blueprint_id: str, name: str, confirm: bool = False) -> dict:
    "Remove a member variable. Requires confirm=True."
    return _post("remove_variable", blueprintId=blueprint_id, name=name, confirm=confirm)


@mcp.tool()
def set_variable_default(blueprint_id: str, name: str, value) -> dict:
    "Set a member variable's default value (applied on next compile). value is REQUIRED and may be a string (UE export text) or typed JSON - a list for an array variable, an object for a struct, a number/bool for the matching type; it is converted against the variable's real property type and REFUSED if it cannot convert (an int variable rejects \"banana\" instead of storing it). Pass value=None to clear the default deliberately. Omitting value used to WIPE the existing default and report ok:true - that is now an error. Returns valueBefore/valueAfter/changed/typeValidated, all read back from the variable rather than echoed from the request."
    return _post("set_variable_default", blueprintId=blueprint_id, name=name, value=value)


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

@mcp.tool()
def add_function_call(graph_id: str, function: str, cls: str = "self", x: int = 0, y: int = 0,
                      as_message: bool = False) -> dict:
    "Add a function/library call node. cls is the owning class ('self' for this Blueprint, or e.g. KismetSystemLibrary). Pin types are derived from the reflected UFunction. Automatically picks the correct UK2Node_CallFunction SUBCLASS the way the editor does - CallArrayFunction for UKismetArrayLibrary ops (so array wildcards resolve durably instead of reverting on reload), CallDataTableFunction, CommutativeAssociativeBinaryOperator, and Message for interface calls on an external target. The chosen class is returned as nodeClass. as_message forces the interface Message form."
    return _post("add_function_call", graphId=graph_id, function=function, **{"class": cls}, x=x, y=y,
                 asMessage=as_message or None)


@mcp.tool()
def add_variable_get(graph_id: str, var: str, target_class: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a 'get variable' node. With no target_class the scope is auto-detected (a variable declared on this function graph resolves as a LOCAL, anything else as a self member). target_class reads a property on ANOTHER object - a spawned actor's variable, or a NATIVE UPROPERTY like ChildActorComponent.ChildActorClass - and the node gets a visible Target pin to wire the object reference into. Pass the UObject class name WITHOUT its C++ prefix (ChildActorComponent, not UChildActorComponent) or a full Blueprint class path. Returns scope (self|local|external), access, hasTargetPin/targetPin, memberClass and native. READABILITY IS CHECKED HERE, the same way add_variable_set checks writability: an inherited property without CPF_BlueprintVisible, or one carrying meta=(BlueprintPrivate) on a parent Blueprint, is refused at placement rather than accepted and then failing at compile."
    return _post("add_variable_get", graphId=graph_id, var=var,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_variable_set(graph_id: str, var: str, target_class: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a 'set variable' node. Same scope rules and target_class semantics as add_variable_get. WRITABILITY IS CHECKED HERE: a BlueprintReadOnly property is refused at placement instead of accepted and then failing at compile - and it only failed at compile once the node was WIRED, because the compiler prunes isolated nodes before validation, so an unwired bad node reported 0 errors."
    return _post("add_variable_set", graphId=graph_id, var=var,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_branch(graph_id: str, x: int = 0, y: int = 0) -> dict:
    "Add a Branch (if/then/else) node."
    return _post("add_branch", graphId=graph_id, x=x, y=y)


@mcp.tool()
def add_macro_instance(graph_id: str, macro_graph: str,
                       macro_path: str = "/Engine/EditorBlueprintResources/StandardMacros.StandardMacros",
                       x: int = 0, y: int = 0) -> dict:
    "Add a macro instance (e.g. macro_graph='ForEachLoop'). Spawned fresh + AllocateDefaultPins so wildcards resolve on connect — never pasted."
    return _post("add_macro_instance", graphId=graph_id, macroGraph=macro_graph,
                 macroPath=macro_path, x=x, y=y)


@mcp.tool()
def add_get_array_item(graph_id: str, x: int = 0, y: int = 0) -> dict:
    "Add a Get (array item) node. Returns the real pin names (arrayPin/indexPin/outPin)."
    return _post("add_get_array_item", graphId=graph_id, x=x, y=y)


@mcp.tool()
def add_override_event(blueprint_id: str, event: str, interface_or_parent: str = "",
                       call_parent: bool = False, x: int = 0, y: int = 0) -> dict:
    "Add an override event node (interface/parent event) into the event graph. Set call_parent to also add and wire a Parent call. interface_or_parent defaults to the parent class."
    return _post("add_override_event", blueprintId=blueprint_id, event=event,
                 interfaceOrParent=interface_or_parent or None, callParent=call_parent, x=x, y=y)


@mcp.tool()
def add_parent_call(graph_id: str, function: str, parent_class: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a Parent (super) call node. parent_class defaults to the Blueprint's parent."
    return _post("add_parent_call", graphId=graph_id, function=function,
                 parentClass=parent_class or None, x=x, y=y)


@mcp.tool()
def add_cast(graph_id: str, target_class: str, x: int = 0, y: int = 0) -> dict:
    "Add a Dynamic Cast node to target_class (impure: exposes exec then / Cast Failed)."
    return _post("add_cast", graphId=graph_id, targetClass=target_class, x=x, y=y)


@mcp.tool()
def add_custom_event(graph_id: str, name: str, inputs: list = None, x: int = 0, y: int = 0) -> dict:
    "Add a custom event node (name validated). Optional inputs are event params: [{name,type,container?}]."
    return _post("add_custom_event", graphId=graph_id, name=name, inputs=inputs or [], x=x, y=y)


@mcp.tool()
def add_make_struct(graph_id: str, struct_name: str, x: int = 0, y: int = 0) -> dict:
    "Add a Make Struct node for a struct (e.g. Vector, Transform, Guid, or a named UScriptStruct)."
    return _post("add_make_struct", graphId=graph_id, structName=struct_name, x=x, y=y)


@mcp.tool()
def add_break_struct(graph_id: str, struct_name: str, x: int = 0, y: int = 0) -> dict:
    "Add a Break Struct node for a struct."
    return _post("add_break_struct", graphId=graph_id, structName=struct_name, x=x, y=y)


@mcp.tool()
def add_self(graph_id: str, x: int = 0, y: int = 0) -> dict:
    "Add a 'self' reference node (outputs the owning Blueprint instance)."
    return _post("add_self", graphId=graph_id, x=x, y=y)


@mcp.tool()
def add_literal(graph_id: str, object: str = "", x: int = 0, y: int = 0) -> dict:
    "Add an OBJECT-reference literal node bound to `object` (an asset path). For scalar literals use set_pin_default on the consuming pin instead."
    return _post("add_literal", graphId=graph_id, object=object or None, x=x, y=y)


@mcp.tool()
def create_function(blueprint_id: str, name: str, inputs: list = None, outputs: list = None, pure: bool = False) -> dict:
    "Create a Blueprint function graph. inputs/outputs are lists of {name, type, container?}. Inputs land on the entry node, outputs on the result node; compiles so the function is callable immediately."
    return _post("create_function", blueprintId=blueprint_id, name=name,
                 inputs=inputs or [], outputs=outputs or [], pure=pure)


@mcp.tool()
def create_blueprint(path: str, parent_class: str = "Actor", blueprint_type: str = "Normal") -> dict:
    "Create a fresh Blueprint asset. path is a /Game/... object path (e.g. /Game/MifTestbed/BP_Foo); parent_class is a name or class path (default Actor). blueprint_type is Normal (default), FunctionLibrary, Interface, MacroLibrary or WidgetBlueprint - an unrecognised value is refused rather than silently producing a plain Blueprint. Compiles it and returns {blueprintId, class, parentClass, eventGraphId}. Fails if one already exists at path: there is NO overwrite (the parameter used to exist here, was read by no line of the handler, and left callers wondering why the flag did nothing) - delete_asset the old one first."
    return _post("create_blueprint", path=path, parentClass=parent_class, blueprintType=blueprint_type)


@mcp.tool()
def resolve_struct(name: str) -> dict:
    "Resolve a struct name (e.g. Vector, Guid, or a mod struct) to its UScriptStruct path. Returns {found, name, path}."
    return _post("resolve_struct", name=name)


@mcp.tool()
def move_node(node_guid: str, x: int, y: int) -> dict:
    "Move a node to a new position."
    return _post("move_node", nodeGuid=node_guid, x=x, y=y)


@mcp.tool()
def remove_node(node_guid: str, confirm: bool = False) -> dict:
    "Remove a node. Requires confirm=True."
    return _post("remove_node", nodeGuid=node_guid, confirm=confirm)


@mcp.tool()
def refresh_node(node_guid: str) -> dict:
    "Reconstruct a node (ReconstructNode) — re-reads its function/variable/pins."
    return _post("refresh_node", nodeGuid=node_guid)


# --------------------------------------------------------------------------
# Pins / wiring
# --------------------------------------------------------------------------

@mcp.tool()
def connect_pins(src_node: str, src_pin: str, dst_node: str, dst_pin: str) -> dict:
    "Wire src_node.src_pin (output) to dst_node.dst_pin (input). Fires the connection notification so wildcards resolve. Returns the schema's reason string if disallowed."
    return _post("connect_pins", srcNode=src_node, srcPin=src_pin, dstNode=dst_node, dstPin=dst_pin)


@mcp.tool()
def disconnect_pin(node: str, pin: str) -> dict:
    "Break all links on a pin."
    return _post("disconnect_pin", node=node, pin=pin)


@mcp.tool()
def reconnect_pin(src_node: str, src_pin: str, dst_node: str, dst_pin: str) -> dict:
    "Break both pins then reconnect them — the wildcard-reset combo when a type is stuck."
    return _post("reconnect_pin", srcNode=src_node, srcPin=src_pin, dstNode=dst_node, dstPin=dst_pin)


@mcp.tool()
def set_pin_default(node: str, pin: str, value: str) -> dict:
    "Set a literal default value on an input pin (schema-formatted)."
    return _post("set_pin_default", node=node, pin=pin, value=value)


@mcp.tool()
def splice_into_exec(after_node: str, insert_node: str, after_pin: str = "then",
                     insert_exec_in: str = "execute", insert_exec_out: str = "then") -> dict:
    "Atomically insert a node into an exec chain: after_node.after_pin -> insert_node, and insert_node -> the old downstream target(s)."
    return _post("splice_into_exec", afterNode=after_node, insertNode=insert_node,
                 afterPin=after_pin, insertExecIn=insert_exec_in, insertExecOut=insert_exec_out)


# --------------------------------------------------------------------------
# Compile / diagnostics
# --------------------------------------------------------------------------

@mcp.tool()
def compile_blueprint(blueprint_id: str) -> dict:
    "Compile a Blueprint and return structured messages: {ok, numErrors, numWarnings, messages:[{severity,text,nodeGuid,pinName}]}."
    return _post("compile", blueprintId=blueprint_id)


@mcp.tool()
def validate_blueprint(blueprint_id: str) -> dict:
    "Compile without saving (dry-run) and return the same structured messages as compile."
    return _post("validate", blueprintId=blueprint_id)


@mcp.tool()
def run_console(command: str, world: str = "editor", capture_output: bool = True) -> dict:
    "Execute an editor console command (e.g. a mif.kr.* cvar-command) on the game thread and return {ok, command, executed, world, execOutput, execOutputLines}. executed=false means NO handler claimed the command - it is not a claim about success. execOutput is what the command wrote to its OWN output device, and it was ALSO forwarded to the editor log (the capture tees, it does not replace GLog) - a command that reports via UE_LOG instead, which most mif.kr.* commands do, writes nothing there: use run_console_captured, which brackets GLog. world = editor (default) | pie (refused when not playing) | active (PIE if playing, else editor). There is deliberately no separate run_editor_exec: it would have been a third copy of the same UEngine::Exec call and everything it was meant to add is folded in here. HAZARD: an exec command is arbitrary registered code - if it opens a dialog or blocks, it stops the game-thread ticker this bridge runs on and THIS CALL NEVER RETURNS. list_editor_commands {includeConsole:true, consolePrefix:...} shows what a prefix offers before you run it."
    return _post("run_console", command=command, world=world, captureOutput=capture_output)


# --------------------------------------------------------------------------
# Composite recipes
# --------------------------------------------------------------------------

@mcp.tool()
def recipe_add_debug_print(graph_id: str, message: str, after_node: str = "",
                           after_pin: str = "then", function_name: str = "PrintToModLoader",
                           message_param: str = "Message", x: int = 0, y: int = 0) -> dict:
    "Add a DEBUG log node calling a self-local PrintToModLoader(Message) — created if missing — and splice it after after_node. Uses PrintToModLoader (not PrintString, which is stripped in the shipped game)."
    return _post("recipe_add_debug_print", graphId=graph_id, message=message,
                 afterNode=after_node or None, afterPin=after_pin,
                 functionName=function_name, messageParam=message_param, x=x, y=y)


@mcp.tool()
def recipe_reset_and_loop(graph_id: str, array_var: str, index_var: str, score_var: str = "",
                          index_init: str = "-1", score_init: str = "-2.0",
                          after_node: str = "", after_pin: str = "then", x: int = 0, y: int = 0) -> dict:
    "Build SET index(=-1) -> [SET score(=-2.0)] -> ForEachLoop over array_var, wiring the array wildcard correctly. Returns the ForEach node + its pins (Loop Body / Array Element / Array Index / Completed) to build the body."
    return _post("recipe_reset_and_loop", graphId=graph_id, arrayVar=array_var, indexVar=index_var,
                 scoreVar=score_var or None, indexInit=index_init, scoreInit=score_init,
                 afterNode=after_node or None, afterPin=after_pin, x=x, y=y)


@mcp.tool()
def recipe_override_and_call_parent(blueprint_id: str, event: str, interface_or_parent: str = "",
                                    x: int = 0, y: int = 0) -> dict:
    "Add an interface/parent override event with the Parent call pre-wired (MainInteraction shape)."
    return _post("recipe_override_and_call_parent", blueprintId=blueprint_id, event=event,
                 interfaceOrParent=interface_or_parent or None, x=x, y=y)


@mcp.tool()
def recipe_splice_before_parent(graph_id: str, parent_node: str, cluster_entry: str, cluster_exit: str,
                                cluster_entry_exec_in: str = "execute", cluster_exit_exec_out: str = "then") -> dict:
    "Insert a cluster (cluster_entry..cluster_exit) between whatever feeds parent_node's exec and parent_node — the SteelRack 'cluster before the Parent call' move."
    return _post("recipe_splice_before_parent", graphId=graph_id, parentNode=parent_node,
                 clusterEntry=cluster_entry, clusterExit=cluster_exit,
                 clusterEntryExecIn=cluster_entry_exec_in, clusterExitExecOut=cluster_exit_exec_out)


@mcp.tool()
def recipe_argmax_over_components(graph_id: str, loop_body_node: str, score_node: str, score_pin: str,
                                  index_node: str, index_pin: str, best_score_var: str, best_index_var: str,
                                  loop_body_pin: str = "Loop Body", x: int = 0, y: int = 0) -> dict:
    "Inside a loop body, build: if (score > bestScore) { bestScore = score; bestIndex = index; }. Supply the score output pin and index output pin sources."
    return _post("recipe_argmax_over_components", graphId=graph_id, loopBodyNode=loop_body_node,
                 loopBodyPin=loop_body_pin, scoreNode=score_node, scorePin=score_pin,
                 indexNode=index_node, indexPin=index_pin,
                 bestScoreVar=best_score_var, bestIndexVar=best_index_var, x=x, y=y)


# --------------------------------------------------------------------------
# Pipeline hooks
# --------------------------------------------------------------------------

@mcp.tool()
def read_modloader_log(lines: int = 80, filter: str = "", path: str = "") -> dict:
    "Tail the UE4SS.log where Lua print() and Blueprint PrintToModLoader output land (closes the runtime loop). Optional substring filter and path override."
    return _post("read_modloader_log", lines=lines, filter=filter or None, path=path or None)


@mcp.tool()
def trigger_cook(mod: str = "", asset: str = "") -> dict:
    "PLAN ONLY: returns the verified retoc extract->patch->repack->parity->deploy command sequence with paths pinned. Executes nothing (the pipeline runs out-of-editor on live paks)."
    return _post("trigger_cook", mod=mod or None, asset=asset or None)


# --------------------------------------------------------------------------
# Batch
# --------------------------------------------------------------------------

@mcp.tool()
def batch(ops: list, compile_at_end: bool = True, blueprint_id: str = "", backup: bool = False) -> dict:
    "Run many ops in ONE transaction + one compile. Each op is a dict {op:'<endpoint>', ...params}. Optionally back up blueprint_id first and compile touched blueprints at the end."
    return _post("batch", ops=ops, compileAtEnd=compile_at_end,
                 blueprintId=blueprint_id or None, backup=backup)


# --------------------------------------------------------------------------
# Phase 3 breadth — graph nodes
# --------------------------------------------------------------------------

@mcp.tool()
def add_timeline(blueprint_id: str, name: str = "", float_tracks: list = None,
                 length: float = 0.0, auto_play: bool = False, loop: bool = False,
                 x: int = 0, y: int = 0) -> dict:
    "Add a Timeline (node + template) to an Actor Blueprint. Optional float_tracks (names), length, auto_play, loop."
    return _post("add_timeline", blueprintId=blueprint_id, name=name or None,
                 floatTracks=float_tracks or None, length=length, autoPlay=auto_play, loop=loop, x=x, y=y)


@mcp.tool()
def add_class_cast(graph_id: str, target_class: str, x: int = 0, y: int = 0) -> dict:
    "Add a Cast-to-Class (UClass) node to target_class."
    return _post("add_class_cast", graphId=graph_id, targetClass=target_class, x=x, y=y)


@mcp.tool()
def add_switch_enum(graph_id: str, enum_name: str, has_default: bool = False, x: int = 0, y: int = 0) -> dict:
    "Add a Switch-on-Enum node (case pins derived from the enum)."
    return _post("add_switch_enum", graphId=graph_id, enumName=enum_name, hasDefault=has_default, x=x, y=y)


@mcp.tool()
def add_switch_int(graph_id: str, cases: int = 0, start_index: int = 0,
                   has_default: bool = True, x: int = 0, y: int = 0) -> dict:
    "Add a Switch-on-Int node with `cases` case pins starting at start_index."
    return _post("add_switch_int", graphId=graph_id, cases=cases, startIndex=start_index,
                 hasDefault=has_default, x=x, y=y)


@mcp.tool()
def add_switch_string(graph_id: str, cases: list = None, case_sensitive: bool = False,
                      has_default: bool = True, x: int = 0, y: int = 0) -> dict:
    "Add a Switch-on-String node with a case pin per label in `cases`."
    return _post("add_switch_string", graphId=graph_id, cases=cases or [],
                 caseSensitive=case_sensitive, hasDefault=has_default, x=x, y=y)


@mcp.tool()
def add_enum_literal(graph_id: str, enum_name: str, value: str = "", x: int = 0, y: int = 0) -> dict:
    "Add an enum literal node; value is the enumerator name (e.g. 'NewEnumerator0')."
    return _post("add_enum_literal", graphId=graph_id, enumName=enum_name, value=value or None, x=x, y=y)


@mcp.tool()
def set_pin_type(node: str, pin: str, type: str, container: str = "", value_type: str = "") -> dict:
    "Force a pin's type. type supports scalars (float is 32-bit, double/real 64-bit), struct/class names, and prefixes class:X / object:X / softobject:X / softclass:X / interface:X / enum:X. container = array|set|map; for a map, type is the KEY type and value_type is the VALUE type."
    return _post("set_pin_type", node=node, pin=pin, type=type, container=container or None,
                 valueType=value_type or None)


# --------------------------------------------------------------------------
# Phase 3 breadth — event dispatchers (multicast delegates)
# --------------------------------------------------------------------------

@mcp.tool()
def add_event_dispatcher(blueprint_id: str, name: str, inputs: list = None) -> dict:
    "Create a Blueprint event dispatcher (multicast delegate) with optional params [{name,type,container?}]. Compiles so it becomes callable/bindable."
    return _post("add_event_dispatcher", blueprintId=blueprint_id, name=name, inputs=inputs or [])


@mcp.tool()
def add_call_dispatcher(graph_id: str, dispatcher: str, x: int = 0, y: int = 0) -> dict:
    "Add a Call node for an event dispatcher (broadcasts it). The dispatcher must already exist + be compiled."
    return _post("add_call_dispatcher", graphId=graph_id, dispatcher=dispatcher, x=x, y=y)


@mcp.tool()
def add_bind_dispatcher(graph_id: str, dispatcher: str, x: int = 0, y: int = 0) -> dict:
    "Add a Bind (Add) node for an event dispatcher."
    return _post("add_bind_dispatcher", graphId=graph_id, dispatcher=dispatcher, x=x, y=y)


@mcp.tool()
def list_dispatchers(blueprint_id: str) -> dict:
    "List the Blueprint's event dispatchers."
    return _post("list_dispatchers", blueprintId=blueprint_id)


# --------------------------------------------------------------------------
# Phase 3 breadth — components (SimpleConstructionScript)
# --------------------------------------------------------------------------

@mcp.tool()
def add_component(blueprint_id: str, component_class: str, name: str = "", parent_name: str = "",
                  location: list = None, rotation: list = None, scale: list = None) -> dict:
    "Add a component to an Actor Blueprint's SCS tree. Optional parent_name (attach under), and location/rotation([pitch,yaw,roll])/scale as [x,y,z] or {x,y,z}. EVERY numeric field is strict now: a value you SUPPLY that is not a number is a hard error naming the field, the value and the expected type. It is never defaulted - location={\"x\":\"not-a-number\",\"y\":123,\"z\":456} used to return ok:true having applied y and z, kept the old x, and echoed the mixture back as if you had asked for it. A transform that cannot be read fails the call and the component is rolled back with it."
    return _post("add_component", blueprintId=blueprint_id, componentClass=component_class,
                 name=name or None, parentName=parent_name or None,
                 location=location or None, rotation=rotation or None, scale=scale or None)


@mcp.tool()
def list_components(blueprint_id: str, include_inherited: bool = True,
                    include_native: bool = True, limit: int = 500) -> dict:
    "List EVERY component reachable from a Blueprint, from all three origins, each row tagged with origin: 'ownSCS' (this Blueprint's own SimpleConstructionScript), 'parentBlueprintSCS' (inherited from a parent BLUEPRINT's SCS, anywhere up the chain) and 'native' (a C++ component on the parent class chain, read off the CDO). It used to walk the child's own SCS only, which is why get_inherited_component - a verb that resolves ONE component BY NAME - had no companion that could tell you the names. Every row carries owningClass, the endpoint to call next (route/endpoint) and a hint. templatePath means one thing everywhere: the objectPath to pass to set_property to change that component's defaults FOR THIS BLUEPRINT. For a NATIVE component that is the child CDO's own subobject, and the subobject name is NOT the property name (Mesh -> CharacterMesh0, CharacterMovement -> CharMoveComp, CapsuleComponent -> CollisionCylinder) - subobjectName carries it, resolved from the object rather than guessed. For an INHERITED component templatePath is present only once an override exists (overrideTemplatePath); until then it is deliberately absent, because the only other template is the PARENT asset's and writing there would change every other child - parentTemplatePath shows it read-only and route says override_inherited_component. Also reports canOverride (exactly what override_inherited_component will accept), editableWhenInherited (the extra editor-side fact), and the ownSCSCount / parentBlueprintSCSCount / nativeCount split. include_inherited and include_native default TRUE and exist only so a caller can ask for the old own-SCS-only shape back."
    return _post("list_components", blueprintId=blueprint_id, includeInherited=include_inherited,
                 includeNative=include_native, limit=limit)


@mcp.tool()
def remove_component(blueprint_id: str, name: str, confirm: bool = False) -> dict:
    "Remove a component from the SCS tree (children promoted). Requires confirm=True."
    return _post("remove_component", blueprintId=blueprint_id, name=name, confirm=confirm)


@mcp.tool()
def get_inherited_component(blueprint: str, component: str) -> dict:
    "Discovery verb for an INHERITED component: reports origin (parentBlueprintSCS | native | ownSCS | notFound), whether an override template already exists, its objectPath, and the parent's original template. Creates nothing. For a NATIVE inherited component (e.g. a Character's Mesh) ICH does not apply - it returns the CDO-subobject path to use with set_property instead, because the property name and the subobject name differ (Mesh -> CharacterMesh0)."
    return _post("get_inherited_component", blueprint=blueprint, component=component)


@mcp.tool()
def override_inherited_component(blueprint: str, component: str, properties: dict = None,
                                 confirm: bool = False) -> dict:
    "Create (or reuse) the per-child override template for a component inherited from a parent BLUEPRINT's SCS - the same delta the Details panel writes - and optionally apply properties to it. Only the properties you set are stored; everything else keeps inheriting. Returns overrideTemplatePath, usable as set_property's objectPath. Refuses native inherited components and names the CDO-subobject path instead. Each property reports applied/changed separately, so writing an identical value is applied:true, changed:false rather than a false failure - and typeValidated separately again, because those are different questions. A value is checked against the destination property's TYPE before the import: {\"SphereRadius\":\"not-a-float\"} used to answer ok:true, applied:true, wanted:\"0.000000\" - UE's float importer parsed the garbage as 0.0 and reported success, and the post-write check then compared 0 against 0 and passed. It is a hard error naming the property, the value and the expected form now. EVERY property is validated BEFORE the override template is minted, so a rejected call creates nothing at all: it answers created:false, nothingModified:true, outcome:\"preflight-rejected-nothing-created\" and the blueprint is untouched. (It used to mint the override first and validate second - a FAILED call permanently added an override to your blueprint, and the cancelled transaction did not remove it, because UTransBuffer::Cancel discards the undo entry rather than rolling anything back.) If a value still fails at write time for a reason no type check can predict - an engine clamp, a PostEditChangeProperty rejection - the override is removed again, but ONLY when this call created it: a pre-existing override is never deleted, and outcome/overrideRemovedOnFailure say which path was taken."
    return _post("override_inherited_component", blueprint=blueprint, component=component,
                 properties=properties or None, confirm=confirm)


@mcp.tool()
def revert_inherited_component(blueprint: str, component: str, confirm: bool = False) -> dict:
    "Remove the child's override template so the component falls back to the parent's values. Requires confirm=True (it discards the overrides)."
    return _post("revert_inherited_component", blueprint=blueprint, component=component, confirm=confirm)


@mcp.tool()
def set_component_transform(blueprint_id: str, name: str, location: list = None,
                            rotation: list = None, scale: list = None) -> dict:
    "Set a scene component's relative transform. location/rotation([pitch,yaw,roll])/scale as [x,y,z] or {x,y,z} (rotation also takes {pitch,yaw,roll}). EVERY numeric field is strict now: a value you SUPPLY that is not a number is a hard error naming the field, the value and the expected type. It is never defaulted - location={\"x\":\"not-a-number\",\"y\":123,\"z\":456} used to return ok:true having applied y and z, kept the old x, and echoed the mixture back as if you had asked for it. The array form used to be read with a JSON accessor that returns 0.0 for a string and cannot report that it did, so [\"oops\",1,2] became (0,1,2)."
    return _post("set_component_transform", blueprintId=blueprint_id, name=name,
                 location=location or None, rotation=rotation or None, scale=scale or None)


# --------------------------------------------------------------------------
# Phase 3 breadth — interfaces
# --------------------------------------------------------------------------

@mcp.tool()
def add_interface(blueprint_id: str, interface: str) -> dict:
    "Implement an interface on the Blueprint (by class name or path)."
    return _post("add_interface", blueprintId=blueprint_id, interface=interface)


@mcp.tool()
def remove_interface(blueprint_id: str, interface: str, confirm: bool = False) -> dict:
    "Remove an implemented interface. Requires confirm=True."
    return _post("remove_interface", blueprintId=blueprint_id, interface=interface, confirm=confirm)


@mcp.tool()
def list_interfaces(blueprint_id: str, include_inherited: bool = False) -> dict:
    "List interfaces implemented by the Blueprint."
    return _post("list_interfaces", blueprintId=blueprint_id, includeInherited=include_inherited)


# --------------------------------------------------------------------------
# Phase 3 breadth — datatables (read-only)
# --------------------------------------------------------------------------

@mcp.tool()
def list_datatables(filter: str = "") -> dict:
    "List DataTable assets (optional substring filter on the object path)."
    return _post("list_datatables", filter=filter or None)


@mcp.tool()
def read_datatable(path: str, max_rows: int = 500, text_format: str = "export") -> dict:
    "Read a DataTable: row struct, row names, and rows as JSON (capped at max_rows). text_format controls FText rendering: 'export' (default) is the engine's lossless NSLOCTEXT(\"ns\",\"key\",\"source\") form - round-trip safe, write it back through write_datatable_rows merge mode verbatim; 'simple' returns the plain display string (lossy - drops namespace/key, do not write it back expecting the same ids). Any other value is an error. The effective value is echoed back as textFormat, and an export-mode response that actually contains NSLOCTEXT carries a textNote explaining it is not corruption."
    return _post("read_datatable", path=path, maxRows=max_rows, textFormat=text_format or None)


@mcp.tool()
def get_datatable_row(path: str, row_name: str, text_format: str = "export") -> dict:
    "Read a single DataTable row by name as JSON. text_format: 'export' (default, lossless NSLOCTEXT form) | 'simple' (plain display string, lossy). Echoed back as textFormat; export-mode rows containing NSLOCTEXT carry a textNote."
    return _post("get_datatable_row", path=path, rowName=row_name, textFormat=text_format or None)


# --------------------------------------------------------------------------
# Phase 3 completion — functions / interface functions / datatable write
# --------------------------------------------------------------------------

@mcp.tool()
def implement_interface_function(blueprint_id: str, function: str) -> dict:
    "Add the implementation graph for a return-valued interface function (event-style ones use add_override_event). Returns its graphId."
    return _post("implement_interface_function", blueprintId=blueprint_id, function=function)


@mcp.tool()
def remove_function(blueprint_id: str, name: str, confirm: bool = False) -> dict:
    "Remove a Blueprint function graph. Requires confirm=True."
    return _post("remove_function", blueprintId=blueprint_id, name=name, confirm=confirm)


@mcp.tool()
def write_datatable_rows(path: str, rows: list, replace: bool = False, confirm: bool = False) -> dict:
    "Write DataTable rows (each a dict with a 'Name' field + row-struct fields). replace=True overwrites the whole table; otherwise rows are added/updated in place. Requires confirm=True. FText asymmetry: merge mode (replace=False, the default) parses values through FJsonObjectConverter and accepts read_datatable's NSLOCTEXT export form verbatim; replace mode assigns a GENERATED localization id (namespace '<Table> [guid]', key '<Row>_<Column>') to any plain FText string, so those fields read back as NSLOCTEXT(...) - a successful replace on a row struct with FText returns textLocalizationNote saying so. Prefer merge unless you intend a full-table overwrite."
    return _post("write_datatable_rows", path=path, rows=rows, replace=replace, confirm=confirm)


@mcp.tool()
def delete_datatable_rows(path: str, row_names: list, confirm: bool = False) -> dict:
    "Delete rows from a DataTable by name. row_names is a list of row-name strings. Requires confirm=True. Returns deleted (count), rowCount (rows left), and notFound (any names that were not present) - names that do not exist are skipped, not an error. Use this to RENAME a row: write_datatable_rows the row under its new name, then delete the old one. Prefer that over write_datatable_rows(replace=True), which rebuilds the whole table via CreateTableFromJSONString - it empties the table first and skips the FText repair path, so it is the more destructive option."
    return _post("delete_datatable_rows", path=path, rowNames=row_names, confirm=confirm)


# --------------------------------------------------------------------------
# Phase 3 completion — common nodes
# --------------------------------------------------------------------------

@mcp.tool()
def add_sequence(graph_id: str, outputs: int = 2, x: int = 0, y: int = 0) -> dict:
    "Add a Sequence node with `outputs` exec-out pins (then_0..then_N)."
    return _post("add_sequence", graphId=graph_id, outputs=outputs, x=x, y=y)


@mcp.tool()
def add_spawn_actor(graph_id: str, actor_class: str, x: int = 0, y: int = 0) -> dict:
    "Add a SpawnActorFromClass node bound to actor_class (exposes its spawn-var pins)."
    return _post("add_spawn_actor", graphId=graph_id, actorClass=actor_class, x=x, y=y)


@mcp.tool()
def add_get_subsystem(graph_id: str, subsystem_class: str, x: int = 0, y: int = 0) -> dict:
    "Add a Get Subsystem node typed to subsystem_class (GameInstance/World subsystem)."
    return _post("add_get_subsystem", graphId=graph_id, subsystemClass=subsystem_class, x=x, y=y)


@mcp.tool()
def add_make_array(graph_id: str, num_inputs: int = 1, x: int = 0, y: int = 0) -> dict:
    "Add a Make Array node with num_inputs element pins (the element type resolves on connect)."
    return _post("add_make_array", graphId=graph_id, numInputs=num_inputs, x=x, y=y)


@mcp.tool()
def add_format_text(graph_id: str, format: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a Format Text node; the {tokens} in `format` become argument pins."
    return _post("add_format_text", graphId=graph_id, format=format or None, x=x, y=y)


@mcp.tool()
def add_get_data_table_row(graph_id: str, data_table: str = "", row_name: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a Get Data Table Row node. Optional data_table (asset path) types the result struct; optional row_name sets the row pin."
    return _post("add_get_data_table_row", graphId=graph_id, dataTable=data_table or None,
                 rowName=row_name or None, x=x, y=y)


@mcp.tool()
def add_comment(graph_id: str, text: str = "", x: int = 0, y: int = 0, width: int = 400, height: int = 150) -> dict:
    "Add a comment box to the graph (readability). text/width/height/position."
    return _post("add_comment", graphId=graph_id, text=text, x=x, y=y, width=width, height=height)


@mcp.tool()
def add_create_widget(graph_id: str, widget_class: str, x: int = 0, y: int = 0) -> dict:
    "Add a Create Widget node. widget_class must be a UUserWidget subclass; an empty value is rejected (it would resolve to this blueprint's own class)."
    return _post("add_create_widget", graphId=graph_id, widgetClass=widget_class, x=x, y=y)


@mcp.tool()
def add_make_map(graph_id: str, num_inputs: int = 1, x: int = 0, y: int = 0) -> dict:
    "Add a Make Map node with num_inputs key/value pin pairs (1-64)."
    return _post("add_make_map", graphId=graph_id, numInputs=num_inputs, x=x, y=y)


# --------------------------------------------------------------------------
# Pins (removal)
# --------------------------------------------------------------------------

@mcp.tool()
def add_pin(name: str, type: str, graph_id: str = "", blueprint_id: str = "", function: str = "",
            node_guid: str = "", container: str = "", value_type: str = "", direction: str = "input",
            default: str = "") -> dict:
    "Add a parameter to an EXISTING function or custom event - no more rebuilding the function to change its signature. Target by graph_id, blueprint_id + function, or node_guid (custom event). direction = input|output; a custom event has no outputs. For an output on a function with no Return node, one is created and wired from the entry's exec. Outputs are mirrored onto EVERY sibling Return node in the graph with the same name (they must match or it will not compile). Returns the final pin name, which may be uniquified if the name was taken."
    return _post("add_pin", name=name, type=type, graphId=graph_id or None,
                 blueprintId=blueprint_id or None, function=function or None,
                 nodeGuid=node_guid or None, container=container or None,
                 valueType=value_type or None, direction=direction, default=default or None)


@mcp.tool()
def remove_pin(node_guid: str, pin: str, graph_id: str = "", direction: str = "",
               confirm: bool = False) -> dict:
    "Remove a pin. Handles user-defined pins (function/event/tunnel parameters, syncing sibling Return nodes) and duplicate pins (keeps the wired copy). Engine-allocated pins are refused - AllocateDefaultPins would recreate them. direction = input|output disambiguates same-named pins. Requires confirm=True."
    return _post("remove_pin", nodeGuid=node_guid, pin=pin, graphId=graph_id or None,
                 direction=direction or None, confirm=confirm)


# --------------------------------------------------------------------------
# Variable flags (replication / SaveGame / editability)
# --------------------------------------------------------------------------

@mcp.tool()
def set_variable_flags(blueprint_id: str, name: str,
                       replicated: bool = None, rep_notify: bool = None,
                       rep_notify_function: str = "", replication_condition: str = "",
                       save_game: bool = None, transient: bool = None, config: bool = None,
                       instance_editable: bool = None, blueprint_read_only: bool = None,
                       expose_on_spawn: bool = None, advanced_display: bool = None,
                       interp: bool = None, deprecated: bool = None,
                       category: str = "", tooltip: str = "") -> dict:
    "Set Details-panel flags on a MEMBER variable (locals are rejected - they are never replicated or saved). PARTIAL UPDATE: omitted flags are left alone. rep_notify creates the OnRep_<Name> function graph if missing and implies replicated. replication_condition takes an ELifetimeCondition (COND_None, COND_OwnerOnly, ...; the COND_ prefix is optional). Returns the resulting flags, plus replicationWarning if the owning Actor has bReplicates=false."
    return _post("set_variable_flags", blueprintId=blueprint_id, name=name,
                 replicated=replicated, repNotify=rep_notify,
                 repNotifyFunction=rep_notify_function or None,
                 replicationCondition=replication_condition or None,
                 saveGame=save_game, transient=transient, config=config,
                 instanceEditable=instance_editable, blueprintReadOnly=blueprint_read_only,
                 exposeOnSpawn=expose_on_spawn, advancedDisplay=advanced_display,
                 interp=interp, deprecated=deprecated,
                 category=category or None, tooltip=tooltip or None)


# --------------------------------------------------------------------------
# Renaming functions, events and dispatchers
# --------------------------------------------------------------------------

@mcp.tool()
def rename_function(new_name: str, graph_id: str = "", blueprint_id: str = "", old_name: str = "",
                    confirm: bool = False) -> dict:
    "Rename a Blueprint function graph. Repoints the entry/result terminators and any override graphs in CHILD blueprints; call sites in OTHER blueprints resolve by name and must be recompiled. Refuses a dispatcher signature graph (use rename_event_dispatcher). Requires confirm=True."
    return _post("rename_function", newName=new_name, graphId=graph_id or None,
                 blueprintId=blueprint_id or None, oldName=old_name or None, confirm=confirm)


@mcp.tool()
def rename_event(node_guid: str, new_name: str, confirm: bool = False) -> dict:
    "Rename a Custom Event by node guid. Refuses an OVERRIDE event (its name is fixed by the parent declaration). Requires confirm=True."
    return _post("rename_event", nodeGuid=node_guid, newName=new_name, confirm=confirm)


@mcp.tool()
def rename_event_dispatcher(blueprint_id: str, old_name: str, new_name: str,
                            confirm: bool = False) -> dict:
    "Rename an event dispatcher. A dispatcher is BOTH a signature graph and a backing delegate variable - this renames both, which is why rename_variable refuses to touch one. Requires confirm=True."
    return _post("rename_event_dispatcher", blueprintId=blueprint_id, oldName=old_name,
                 newName=new_name, confirm=confirm)


# --------------------------------------------------------------------------
# Function / event flags (RPC replication, access, purity)
# --------------------------------------------------------------------------

@mcp.tool()
def set_function_flags(blueprint_id: str = "", graph_id: str = "", function: str = "",
                       node_guid: str = "",
                       replicates: str = "", reliable: bool = None, access: str = "",
                       pure: bool = None, is_const: bool = None, call_in_editor: bool = None,
                       category: str = "", tooltip: str = "", keywords: str = "") -> dict:
    "Set RPC/replication and access flags on a FUNCTION or a CUSTOM EVENT - the Details-panel Replicates dropdown, Reliable checkbox, access specifier, Pure/Const/CallInEditor, plus category/tooltip/keywords. Target it by node_guid (custom event), graph_id, or blueprint_id + function. replicates = none|multicast|server|client (aliases runOnServer/runOnClient/owningClient). PARTIAL UPDATE: omitted flags are left alone. REFUSES: a custom event that OVERRIDES a parent event (its flags come from the parent), pure/const on a custom event, and a node that is neither a custom event nor a function entry. Changing replicates runs a FULL COMPILE and returns it under 'compile'; other blueprints CALLING the function must be recompiled too before their call sites route over the network. Warns on RPC-with-bReplicates-false, reliable-without-replicates, and pure+RPC."
    return _post("set_function_flags", blueprintId=blueprint_id or None, graphId=graph_id or None,
                 function=function or None, nodeGuid=node_guid or None,
                 replicates=replicates or None, reliable=reliable, access=access or None,
                 pure=pure, isConst=is_const, callInEditor=call_in_editor,
                 category=category or None, tooltip=tooltip or None, keywords=keywords or None)


# --------------------------------------------------------------------------
# Reflection (any UObject, not just Blueprints)
# --------------------------------------------------------------------------

@mcp.tool()
def get_property(object_path: str = "", blueprint_id: str = "", widget_name: str = "",
                 property_path: str = "") -> dict:
    "Read any UObject property by dot-path (e.g. Font.Size). Target is either object_path, or blueprint_id + widget_name for a widget template."
    return _post("get_property", objectPath=object_path or None, blueprintId=blueprint_id or None,
                 widgetName=widget_name or None, propertyPath=property_path)


@mcp.tool()
def set_property(object_path: str = "", blueprint_id: str = "", widget_name: str = "",
                 property_path: str = "", value: Any = "", override_flag: str = "",
                 enforce_clamps: bool = False) -> dict:
    "Write any UObject property by dot-path, the way the Details panel does. Target is either object_path, or blueprint_id + widget_name for a widget template (which recompiles). value takes TWO forms: UE export text as a STRING (the original path, byte-for-byte unchanged), or TYPED JSON - a list for an array/set, a dict for a map/struct, a real number, a bool. Pass the typed form for containers: a JSON array used to be read as an empty string, and an empty buffer means 'EMPTY THE ARRAY' to the engine's array importer, which reported applied:true after WIPING it. Same shape fixed for JSON floats (the float import path has no 'nothing consumed' guard, so 0.5 wrote 0.0 and said ok) and for unresolvable object paths (imported 'successfully' as null). Returns valueForm, importText (the export text actually imported), valueBefore/valueAfter, typed (typed JSON read-back), changed, and elementsBefore/elementsAfter for containers. A value that fails to parse leaves the property UNCHANGED. TWO further guarantees: (1) typeValidated - the value is checked against the DESTINATION property's type BEFORE the import, because verifying that a write landed does not verify that the value was understood; \"not-a-float\" on a float used to import as 0.0, report success, and pass the post-write check by comparing 0 against 0. Numbers must parse WHOLE (no \"12abc\", no exponent form), bools must be a recognised literal, enums must be a real entry (the valid ones are listed in the error). Where a type cannot be pre-checked the response says so in typeValidationNote instead of implying it was. (2) verifiedOn/reconstructed/retargetedTo - writing to a PLACED actor's component reruns that actor's construction scripts, which destroys the component and renames it TRASH_*; the read-back is now taken from the RE-RESOLVED object, and if it cannot be re-resolved the call fails as UNVERIFIED rather than reporting verified:true about a dead object. notification/memberProperty/chainDepth report the edit notification, which is now PostEditChangeChainProperty - a strict superset of the old PostEditChangeProperty, and the only one that reaches archetype instances. THREE more: (3) EDITCONDITION - many engine properties are GATED, and writing one behind an unset flag is SILENTLY IGNORED by the engine (UStaticMeshComponent::MinLOD without bOverrideMinLOD is never read: StaticMeshRender.cpp:248; FPostProcessSettings has 423 more). The write lands in memory and the capability does not, which the post-write verification cannot see because the value genuinely changed. The companion flag is detected via meta=(EditCondition=...) - NOT the bOverride_ naming convention - and override_flag decides what happens: 'set' (the default) writes the flag alongside the value in the same transaction and REPORTS it in overrideFlagWritten{name,valueBefore,valueAfter} (valueAfter is a measured readback); 'refuse' fails naming the flag and its current value; 'ignore' writes anyway and warns. editCondition/editConditionKind/editConditionMet are always emitted, including as null when there is no gate. A condition this bridge cannot evaluate (anything beyond a single bool or its negation - 122 of 837 in Runtime/**.h) is reported as unevaluated, never guessed. (4) ELEMENT ADDRESSING - property_path now takes accessors: OverrideMaterials[1], FloatCurves[1].Keys[0].Value (a C-array UPROPERTY, not a TArray), ScalarParameterValues[ParameterInfo.Name=Roughness].ParameterValue (a linear find on a member), SomeMap{Alpha}.Threshold. Out-of-range names the index AND the actual length. A set index is a POSITION IN ITERATION ORDER and the response says so. Editing a set element checks for duplicates and rehashes. (5) CLAMPS - ClampMin/ClampMax are enforced ONLY by the panel's typed numeric setters, never by ImportText, so this endpoint can write a value the panel would refuse: it reports clampViolation by default and coerces (setting coerced:true) when enforce_clamps=True. UIMin/UIMax are slider bounds and are reported, never acted on."
    return _post("set_property", objectPath=object_path or None, blueprintId=blueprint_id or None,
                 widgetName=widget_name or None, propertyPath=property_path, value=value,
                 overrideFlag=override_flag or None, enforceClamps=enforce_clamps or None)


@mcp.tool()
def list_object_properties(object_path: str = "", blueprint_id: str = "", widget_name: str = "",
                           name_contains: str = "", limit: int = 200,
                           max_value_chars: int = 200) -> dict:
    "Dump an object's top-level properties with type and current value. Filter with name_contains; limit caps the returned rows (matched reports the true total, truncated flags the cap). max_value_chars clips long values and sets valueClipped - large Blueprint actors have struct/curve properties tens of KB each, so an unbounded dump returns nothing. Use get_property for the full value of a single named property."
    return _post("list_object_properties", objectPath=object_path or None,
                 blueprintId=blueprint_id or None, widgetName=widget_name or None,
                 nameContains=name_contains or None, limit=limit,
                 maxValueChars=max_value_chars)


# --------------------------------------------------------------------------
# Details-panel parity (Batch N)
# --------------------------------------------------------------------------

@mcp.tool()
def describe_property(object_path: str = "", blueprint_id: str = "", widget_name: str = "",
                      class_name: str = "", property_path: str = "", name_contains: str = "",
                      limit: int = 200, max_value_chars: int = 200,
                      include_metadata: bool = True, include_default: bool = True) -> dict:
    "THE DISCOVERY LAYER for everything else on this axis: what the Details panel knows about a property and set_property could not tell you. Reports the authored specifier (EditAnywhere / EditDefaultsOnly / VisibleAnywhere / ... recovered from the CPF_* flags, so you can see that VisibleAnywhere is exactly CPF_Edit|CPF_EditConst - a property a human CANNOT edit and this bridge will happily write), the raw flags, every metadata key, Category/DisplayName/ToolTip, EditCondition plus its resolved companion flag and current met/unmet state, ClampMin/ClampMax/UIMin/UIMax/Multiple/ArrayClamp, EditFixedSize, Instanced + AllowedClasses/DisallowedClasses, GetOptions, Units, BitmaskEnum, ArrayDim, the container shape (kind, inner/key/value type, element count, key hashability), persistence ('saved' | 'transient' | 'duplicateTransient' | 'notSerialized' - three DIFFERENT lies: gone on reload, gone on copy/paste, not undoable), editableByHuman (the panel's own predicate recomputed, with notEditableReason), and differsFromDefault + defaultValue + defaultSource. Three forms: property_path for one property in full detail (element accessors work: OverrideMaterials[1], SomeMap{Alpha}); name_contains for a filtered survey; class_name to describe a TYPE with no instance (values then come from the CDO). On a COOKED package GetMetaDataMap() is null, so metadataAvailable comes back false and every meta field is ABSENT rather than an empty string - 'unknown' and 'no clamp' are different answers. Read-only."
    return _post("describe_property", objectPath=object_path or None, blueprintId=blueprint_id or None,
                 widgetName=widget_name or None, className=class_name or None,
                 propertyPath=property_path or None, nameContains=name_contains or None,
                 limit=limit, maxValueChars=max_value_chars,
                 includeMetadata=include_metadata, includeDefault=include_default)


@mcp.tool()
def diff_properties_vs_default(object_path: str = "", blueprint_id: str = "", widget_name: str = "",
                               name_contains: str = "", limit: int = 200, max_value_chars: int = 200,
                               include_transient: bool = False, deep: bool = True) -> dict:
    "What does this object actually OVERRIDE versus its archetype - the question the Details panel answers with a yellow arrow, and the single most useful read for auditing a Blueprint, a placed actor or a CDO. Computed the way the panel computes it: the archetype with a UClass->CDO hop first, FProperty::Identical with PPF_DeepComparison on instanced-object properties (set deep=False to skip that, and the response says it was skipped), and an ArrayDim loop for C-arrays. Each differing row carries value, defaultValue, defaultSource ('archetype' or 'constructed' - a property a child Blueprint added does not exist on the archetype at all, and the fallback is stated rather than hidden), the authored specifier, persistence and resettable. Transients are skipped by default because they always differ and drown the signal; include_transient=True keeps them. Emits the checkable invariant inspected == differing + matching + skippedTransient as countsConsistent. An object whose archetype is itself (the root CDO) is a stated RESULT with differing:0, not an error. Read-only."
    return _post("diff_properties_vs_default", objectPath=object_path or None,
                 blueprintId=blueprint_id or None, widgetName=widget_name or None,
                 nameContains=name_contains or None, limit=limit, maxValueChars=max_value_chars,
                 includeTransient=include_transient, deep=deep)


@mcp.tool()
def reset_property_to_default(object_path: str = "", property_path: str = "",
                              force: bool = False) -> dict:
    "The Details panel's yellow arrow: put a property back to its archetype default. Reports valueBefore / defaultValue / valueAfter / differedFromDefault / changed / defaultSource / archetype, and ASSERTS the invariant - after a successful reset valueAfter must equal defaultValue byte-for-byte under the same exporter, or the call fails. A property that already equals its default is reported (changed:false), not failed. Applies the two refusals the panel applies and a naive reset does not: CPF_Config properties have NO reset arrow (their value comes from an .ini, not the archetype) and CPF_EditFixedSize containers have none either. CPF_EditConst needs force=True. When the archetype does not carry the property at all - a variable a child Blueprint added - it falls back to a FRESHLY CONSTRUCTED default and says defaultSource:'constructed'. PM-003 safe: the default text is parsed into a scratch buffer before the notification bracket is opened, so a failed reset never touches the live value and never leaves a dangling component re-registration. Element accessors work. Transacted, so Ctrl-Z undoes it. Refuses the widget-template form (use set_property) and refuses a cooked package."
    return _post("reset_property_to_default", objectPath=object_path or None,
                 propertyPath=property_path, force=force or None)


@mcp.tool()
def edit_container(object_path: str = "", property_path: str = "", operation: str = "",
                   index: int = None, count: int = None, key: str = "", new_key: str = "",
                   value: Any = None, swap_with: int = None, new_size: int = None) -> dict:
    "The element LIFECYCLE inside a TArray/TSet/TMap - the +, x, insert and clear buttons the Details panel has and set_property does not: operation = add | insert | remove | clear | swap | resize | setKey. (The verb is 'operation', not 'op': 'op' is batch's routing key and is tolerated centrally, so an endpoint using it would be un-diagnosable inside batch.) Element VALUES stay in set_property - address them with the new accessors, e.g. OverrideMaterials[1] or SomeMap{Alpha}. Guards, all applied BEFORE the first mutation because a cancelled transaction reverts nothing: index range checked against the real length and named in the error; CPF_EditFixedSize refuses every size-changing op and names the flag (the panel hides its add/remove buttons for the same reason); a map/set element type with no GetTypeHash is refused BY NAME rather than crashed on; a duplicate map key is REFUSED because FScriptMapHelper::AddPair overwrites silently, which would turn 'add' into 'replace' with no notice, and a duplicate set element likewise (the panel refuses both); every element value is parsed into a scratch buffer first (PM-003); the helper is re-resolved after any structural op, because AddValues/InsertValues reallocate; and the map/set is rehashed after any key or element change, or Find stops seeing entries the container still holds. Reports elementsBefore / elementsAfter / index / rehashed / changed, and treats a structural op that left the count unchanged as a FAILURE rather than a success. Transacted. Refuses the widget-template form and refuses a cooked package."
    return _post("edit_container", objectPath=object_path or None, propertyPath=property_path,
                 operation=operation, index=index, count=count, key=key or None,
                 newKey=new_key or None, value=value, swapWith=swap_with, newSize=new_size)


@mcp.tool()
def describe_class(class_name: str) -> dict:
    "List a class's callable functions (with signatures), Blueprint-visible properties, and event dispatchers."
    return _post("describe_class", className=class_name)


@mcp.tool()
def list_enum_values(enum_name: str) -> dict:
    "List an enum's entries (name, display name, value)."
    return _post("list_enum_values", enumName=enum_name)


# --------------------------------------------------------------------------
# Widget Blueprints
# --------------------------------------------------------------------------

@mcp.tool()
def set_widget_is_variable(blueprint_id: str, widget_name: str, is_variable: bool = True) -> dict:
    "Toggle a widget's Is Variable flag. The generated member is named after the widget's FName, not its display label."
    return _post("set_widget_is_variable", blueprintId=blueprint_id, widgetName=widget_name,
                 isVariable=is_variable)


@mcp.tool()
def add_widget_binding(blueprint_id: str, widget_name: str, property_name: str,
                       function_name: str) -> dict:
    "Bind a widget property (e.g. Text, Percent) to a pure UFUNCTION on the UserWidget. Replaces any existing bind on that property. Takes effect at the next full compile."
    return _post("add_widget_binding", blueprintId=blueprint_id, widgetName=widget_name,
                 propertyName=property_name, functionName=function_name)


@mcp.tool()
def remove_widget_binding(blueprint_id: str, widget_name: str, property_name: str) -> dict:
    "Remove a widget property binding by (widget, property)."
    return _post("remove_widget_binding", blueprintId=blueprint_id, widgetName=widget_name,
                 propertyName=property_name)


@mcp.tool()
def add_tree_widget(blueprint_id: str, widget_class: str, name: str = "", parent_name: str = "",
                    as_root: bool = False, x: float = 0, y: float = 0, auto_size: bool = True) -> dict:
    "Add a widget to a Widget Blueprint's tree, either as the root or as a child of parent_name (which must be a panel). widget_class must be a UWidget subclass; an empty value is rejected."
    return _post("add_tree_widget", blueprintId=blueprint_id, widgetClass=widget_class,
                 name=name or None, parentName=parent_name or None, asRoot=as_root,
                 x=x, y=y, autoSize=auto_size)


@mcp.tool()
def remove_tree_widget(blueprint_id: str, widget_name: str) -> dict:
    "Remove a widget from a Widget Blueprint's tree (handles child, root and named-slot cases)."
    return _post("remove_tree_widget", blueprintId=blueprint_id, widgetName=widget_name)


# --------------------------------------------------------------------------
# Cooked / mounted-container introspection (read-only)
# --------------------------------------------------------------------------

@mcp.tool()
def list_mounted_containers() -> dict:
    "List the mounted pak/utoc containers and the resolved game install dir. Use this to see what cooked content is actually visible to the editor. containers[] rows carry filePath (the FILESYSTEM path of the .utoc) alongside the older path key, which holds the same value - unlike every other endpoint, where path/objectPath/packageName mean /Game/ paths."
    return _post("list_mounted_containers")


@mcp.tool()
def find_assets(cls: str = "", path_prefix: str = "", name_contains: str = "",
                origin: str = "any", recursive_classes: bool = True, limit: int = 100) -> dict:
    "Search the asset registry across loose AND cooked/mounted content. cls filters by class name, path_prefix by /Game/... prefix, name_contains by substring. origin = any|loose|cooked. Returns at most limit results. Every row carries objectPath (/Game/X/Foo.Foo_C) and packageName (/Game/X/Foo) with those exact meanings plugin-wide - feed packageName to describe_package / get_referencers / audit_unused.exclude_referencers, objectPath to anything that loads the asset. The older path/package keys are still emitted with the same values."
    return _post("find_assets", **{"class": cls or None}, pathPrefix=path_prefix or None,
                 nameContains=name_contains or None, origin=origin,
                 recursiveClasses=recursive_classes, limit=limit)


@mcp.tool()
def describe_package(package: str) -> dict:
    "Describe a package by /Game/ path: the objects it contains, their classes, and whether it is cooked. Works on cooked packages whose Blueprint graphs are stripped. Emits packageName at the top level; registryAssets[] rows are now shaped identically to a find_assets row (objectPath, packageName, package, origin, name, class, loaded) and exports[] rows carry objectPath (GetPathName, so a subobject keeps its :Subobject suffix) + packageName."
    return _post("describe_package", package=package)


@mcp.tool()
def diagnose_landscape(limit: int = 40) -> dict:
    "Report landscape proxies/components in the current editor world (diagnostics). limit caps the components listed, 1-1000."
    return _post("diagnose_landscape", limit=limit)


@mcp.tool()
def diagnose_landscape_draws(limit: int = 40) -> dict:
    "Render-thread follow-up to diagnose_landscape: per-component cached mesh-draw-command counts (base pass vs depth pass) plus LOD screen sizes, for landscape components that pass every game-thread check yet never draw. Briefly blocks on a rendering-thread flush. limit caps the proxies listed, 1-1000."
    return _post("diagnose_landscape_draws", limit=limit)


# --------------------------------------------------------------------------
# Navigation (nav mesh + nav-driven movement)
# --------------------------------------------------------------------------

@mcp.tool()
def add_nav_volume(location: dict = None, size: dict = None, label: str = "NavBounds") -> dict:
    "Place a NavMeshBoundsVolume defining where the nav mesh will generate. size is in WORLD UNITS (converted to brush scale internally - a volume's size comes from its brush, not a size property, which is the usual way this silently covers nothing). Call build_navmesh next."
    return _post("add_nav_volume", location=location, size=size, label=label)


@mcp.tool()
def build_navmesh() -> dict:
    "Start nav mesh generation. DOES NOT BLOCK - tiles are cooked over subsequent frames and this handler runs on the game thread. Poll nav_status until building=false and tiles>0. Fails clearly if there is no bounds volume."
    return _post("build_navmesh")


@mcp.tool()
def nav_status() -> dict:
    "Nav mesh state: hasNavSystem, boundsVolumes, navMeshActors, TILES, building, ready. Reports the tile count rather than just success - a mis-sized bounds volume builds 'successfully' with zero tiles and every later pathing call then fails for no visible reason. Warns explicitly in that case."
    return _post("nav_status")


@mcp.tool()
def move_actor_to(actor_path: str, location: dict) -> dict:
    "Issue a nav-driven move order to a pawn. Requires a running PIE session (AI controllers only exist at runtime) and a built nav mesh - both failure modes are reported distinctly."
    return _post("move_actor_to", actorPath=actor_path, location=location)


# --------------------------------------------------------------------------
# Viewport camera (what the USER sees, not a scene capture)
# --------------------------------------------------------------------------

@mcp.tool()
def set_viewport_camera(location: dict = None, rotation: dict = None, look_at: dict = None,
                        fov: float = None, ortho: str = None, ortho_zoom: float = None) -> dict:
    "Move the editor viewport camera the user is looking through. Distinct from capture_camera, which spawns a transient scene-capture and changes nothing on screen. look_at wins over rotation. ortho: top|bottom|front|back|left|right|perspective - orthographic top is the honest answer to 'show me the whole map', with no perspective falloff or far-clip surprises. rotation is x/y/z = pitch/yaw/roll like every other MifBridge transform."
    return _post("set_viewport_camera", location=location, rotation=rotation, lookAt=look_at,
                 fov=fov, ortho=ortho, orthoZoom=ortho_zoom)


@mcp.tool()
def focus_viewport(actor_path: str = None, folder: str = None, instant: bool = True) -> dict:
    "Frame the viewport on an actor, a folder, or (with no target) the WHOLE level - the programmatic equivalent of select-all-then-F. Actors with zero extent (lights, markers) are skipped so one stray marker at the map edge cannot blow the framing out. Returns the bounds it framed."
    return _post("focus_viewport", actorPath=actor_path, folder=folder, instant=instant)


@mcp.tool()
def get_viewport_camera() -> dict:
    "Read the editor viewport camera: location, rotation, fov, whether it is perspective, and how many viewports exist. Read-only."
    return _post("get_viewport_camera")


# --------------------------------------------------------------------------
# World lifecycle, splines, ground snapping
# --------------------------------------------------------------------------

@mcp.tool()
def new_level(partitioned: bool = False) -> dict:
    "Create a fresh empty level. Forces bPromptUserToSave=FALSE - a modal 'save your changes?' dialog blocks the game thread, which is the same thread this bridge runs on, so a prompt would deadlock an unattended run. The new level is transient: call save_level_as before restarting the editor or it is lost."
    return _post("new_level", partitioned=partitioned)


@mcp.tool()
def save_level_as(path: str) -> dict:
    "Save the current level to a package path like '/Game/Maps/MyLevel'. Without this a level built over an hour evaporates when the editor restarts."
    return _post("save_level_as", path=path)


@mcp.tool()
def load_level(path: str) -> dict:
    "Load a level by package path ('/Game/Maps/MyLevel'). Discards unsaved changes without prompting, for the same deadlock reason as new_level."
    return _post("load_level", path=path)


@mcp.tool()
# NOTE: every point is parsed BEFORE the existing spline is cleared, and a malformed entry is an
# error naming its index - points=[[0,0,0],[100,0,0]] (bare arrays instead of {x,y,z}) used to return
# ok:true/pointCount:0 having DESTROYED the existing route. snap_to_ground requires space="world"
# (the ground trace is a world-space line trace); in local space it was silently ignored.
def set_spline_points(actor_path: str, points: list, component: str = None, space: str = "world",
                      point_type: str = "curve", closed_loop: bool = False,
                      snap_to_ground: bool = False, ground_offset: float = 0.0) -> dict:
    "Author a spline's points - THIS IS WHAT MAKES NPCs WALK. The game routes wandering NPCs along BP_SegmentedPathTaskMarker, whose PathSpline is a USplineComponent. points is [{x,y,z},...] (min 2). point_type: curve|linear|constant|curveClamped|curveCustomTangent. snap_to_ground traces each point down onto the terrain, since a route authored at a flat Z floats or buries itself on uneven ground. Every point is validated BEFORE the existing spline is cleared: a component that is not a number fails the call naming points[N].<field>, rather than silently becoming 0 and bending the route through the origin."
    return _post("set_spline_points", actorPath=actor_path, points=points, component=component,
                 space=space, pointType=point_type, closedLoop=closed_loop,
                 snapToGround=snap_to_ground, groundOffset=ground_offset)


@mcp.tool()
def get_spline_points(actor_path: str, component: str = None, space: str = "world") -> dict:
    "Read a spline's points, length and closed-loop flag. Read-only; use to verify a patrol route."
    return _post("get_spline_points", actorPath=actor_path, component=component, space=space)


@mcp.tool()
def snap_actors_to_ground(actor_paths: list = None, folder: str = None, label_contains: str = None,
                          all: bool = False, offset: float = 0.0, align_to_normal: bool = False,
                          trace_height: float = 100000.0) -> dict:
    "Drop actors onto the terrain, one trace each, with the actor ITSELF excluded from the trace. Doing this from outside is both slow (one HTTP round-trip per actor) and wrong - a trace at a building's own XY hits its roof and 'snaps' it onto itself, climbing every call. Places the BOTTOM of each actor's bounds on the hit, so pivots that are not at the base still sit correctly. Landscapes are skipped. Requires a selector (actor_paths / folder / label_contains / all) - it refuses to guess."
    return _post("snap_actors_to_ground", actorPaths=actor_paths, folder=folder,
                 labelContains=label_contains, all=all, offset=offset,
                 alignToNormal=align_to_normal, traceHeight=trace_height)


# --------------------------------------------------------------------------
# Landscape authoring (real terrain, not a stretched plane)
# --------------------------------------------------------------------------

@mcp.tool()
def create_landscape(location: dict = None, scale: dict = None, components_x: int = 8, components_y: int = 8,
                     quads_per_section: int = 63, sections_per_component: int = 1,
                     material: str = None, layers: list = None, height_mode: str = "flat",
                     amplitude: float = 0.0, frequency: float = 2.0, seed: float = 0.0,
                     label: str = "Landscape", folder: str = None) -> dict:
    "Create a real ALandscape. This is the correct answer for ground - a stretched /Engine/BasicShapes/Plane smears one UV set over the whole surface (blurred corners) and a grid of tiles reads as a checkerboard. height_mode: flat|rolling|island; amplitude is in WORLD UNITS of relief. quads_per_section must be 7/15/31/63/127/255 or sections crack. layers is [{layerInfo: '/Game/.../X_LayerInfo', weight: 0..1}] - a layered landscape material with NOTHING painted renders as its fallback (usually black), so pass at least one layer."
    return _post("create_landscape", location=location, scale=scale, componentsX=components_x,
                 componentsY=components_y, quadsPerSection=quads_per_section,
                 sectionsPerComponent=sections_per_component, material=material, layers=layers,
                 heightMode=height_mode, amplitude=amplitude, frequency=frequency, seed=seed,
                 label=label, folder=folder)


@mcp.tool()
def sculpt_landscape(center: dict, radius: float, mode: str = "flatten", amount: float = 0.0,
                     falloff: float = 0.5, target_z: float = None, landscape: str = None) -> dict:
    "Sculpt terrain in WORLD units. mode: raise|lower|flatten|smooth. center/radius/amount/target_z are all world units - the vertex-space conversion happens inside. falloff is the fraction of the radius that is feathered (0 = hard edge = a mesa with vertical walls, so it defaults to 0.5). flatten with no target_z levels to whatever height is under the brush centre. Use this to carve a building pad or a road corridor. amount applies to raise/lower ONLY and target_z to flatten ONLY: passing one to the wrong mode is now an error rather than being silently ignored, and an unknown mode is rejected up front (it used to be checked inside the per-vertex loop, so a brush smaller than one quad never reached the check and returned ok:true/verticesTouched:0)."
    return _post("sculpt_landscape", center=center, radius=radius, mode=mode, amount=amount,
                 falloff=falloff, targetZ=target_z, landscape=landscape)


@mcp.tool()
def paint_landscape(layer_info: str, center: dict, radius: float, weight: float = 1.0,
                    falloff: float = 0.5, landscape: str = None) -> dict:
    "Paint a landscape weight layer in WORLD units - this is what makes a road corridor read as dirt while the verge stays grass. layer_info is a LandscapeLayerInfoObject asset path and must be one of the layers the landscape's material declares - that requirement is now ENFORCED (it was only ever promised in the error text). Painting an unregistered layer does not no-op: it allocates a stray weightmap channel, the weight normalisation dims the layers you WERE using, and a later fixup deletes the allocation - so the paint appeared, damaged the real layers, and then vanished, all under ok:true. landscape_info lists the legal layers. Weights normalise across layers, so painting one up pushes the others down (which is why there is no erase mode)."
    return _post("paint_landscape", layerInfo=layer_info, center=center, radius=radius,
                 weight=weight, falloff=falloff, landscape=landscape)


@mcp.tool()
def bind_landscape_rvt(runtime_virtual_textures: list, landscape: str = None, create_volumes: bool = True) -> dict:
    "Bind runtime virtual textures to a landscape AND create the bounding volumes. A landscape material that samples an RVT renders its base colour BLACK unless both exist: the RVT in the landscape's array (what to draw into) and an ARuntimeVirtualTextureVolume in the level (where it applies). The editor's 'Create Volumes' button is pure UI - the bSetCreateRuntimeVirtualTextureVolumes property is a transient placeholder that does nothing when set. Verify with landscape_info."
    return _post("bind_landscape_rvt", runtimeVirtualTextures=runtime_virtual_textures,
                 landscape=landscape, createVolumes=create_volumes)


@mcp.tool()
def landscape_info() -> dict:
    "Report every landscape in the editor world: world bounds, vertex resolution, scale, material, painted layers, materialLayers (what the MATERIAL declares - painting a layer not in this list succeeds and changes nothing), runtimeVirtualTextures (empty here means a black terrain if the material samples an RVT) and componentsWithoutWeightmap (non-zero means painted layer data never landed). Read-only. Call this before sculpt_landscape/paint_landscape - every world-space argument they take only makes sense against these bounds."
    return _post("landscape_info")


# --------------------------------------------------------------------------
# Level-authoring throughput + material control
# --------------------------------------------------------------------------

@mcp.tool()
def spawn_many(items: list, actor_class: str = "StaticMeshActor", mesh: str = "",
               material: str = "", folder: str = "", label_prefix: str = "") -> dict:
    "Spawn MANY actors in ONE call. items is a list of {x,y,z or location:{}, rotation:{} or yaw, scale (number or {}), label?, mesh?, material?}. Top-level mesh/material are the defaults; per-item values override. label_prefix names them '<prefix>_<index>' - without it every actor is 'StaticMeshActor_417', unfindable by label and invisible to anything that filters on one (snap_actors_to_ground's label_contains). Replaces the 2-HTTP-calls-per-actor pattern - a few hundred actors goes from minutes to seconds. Capped at 5000 per call; returns spawned/failed counts. A transform component that is not a number now fails THAT item with a reason naming items[N].<field> and counts it in failed[], instead of defaulting to 0 and placing the actor at an address you did not give. Unrecognised keys inside an entry are still ignored - only the transform values are checked."
    return _post("spawn_many", items=items, actorClass=actor_class, mesh=mesh or None,
                 material=material or None, folder=folder or None,
                 labelPrefix=label_prefix or None)


@mcp.tool()
def duplicate_actors(actor_paths: list = None, label_prefix: str = "", offset: dict = None,
                     yaw_offset: float = 0.0, count: int = 1, label_suffix: str = "_copy",
                     folder: str = "") -> dict:
    "Duplicate a SET of actors with a positional offset - copy a whole finished building instead of re-placing every panel. Select sources by actor_paths[] or by label_prefix (e.g. 'B5_' grabs every piece of that building). count>1 makes a row, each offset by N*offset. offset is strict: a component that is not a number fails the call instead of silently becoming 0, which would stack every copy on top of the original."
    return _post("duplicate_actors", actorPaths=actor_paths, labelPrefix=label_prefix or None,
                 offset=offset, yawOffset=yaw_offset, count=count,
                 labelSuffix=label_suffix, folder=folder or None)


@mcp.tool()
def create_material_instance(parent: str, path: str, scalars: dict = None,
                             vectors: dict = None) -> dict:
    "Create a MaterialInstanceConstant asset from a parent material, with parameter overrides. This is how you fix UV tiling on large surfaces: derive an instance from the master material and override its tiling scalar, rather than being stuck with whatever the shipped instance happens to expose. scalars is {name: number}, vectors is {name: {r,g,b,a}}."
    return _post("create_material_instance", parent=parent, path=path,
                 scalars=scalars, vectors=vectors)


@mcp.tool()
def set_material_parameter(material: str, scalars: dict = None, vectors: dict = None) -> dict:
    "Set parameters on an existing MaterialInstanceConstant. scalars is {name: number}, vectors is {name: {r,g,b,a}} (also accepts {x,y,z,w} or [r,g,b,a]). Reports unknownParameters for names the PARENT material does not expose, rather than silently accepting a name that will never do anything - and if NONE of the names exist, or you pass neither scalars nor vectors, the call ERRORS instead of returning ok:true/applied:0. TWO DIFFERENT FAILURE MODES, do not confuse them: an UNKNOWN name is reported in unknownParameters[] and is not fatal on its own, but a MALFORMED value (a scalars entry that is not a number, a vectors entry that is not a colour) aborts the WHOLE call before any write - it used to skip that entry and apply the rest, so {\"Tiling\":4,\"Comment\":\"x\"} that returned ok:true/applied:1 now returns an error with zero writes. Unknown keys are rejected by name (the HTTP endpoint also takes a singular {parameter, value} pair; through this tool use the maps). Texture and static-switch parameters are not supported here."
    return _post("set_material_parameter", material=material, scalars=scalars, vectors=vectors)


@mcp.tool()
def add_foliage_instances(mesh: str, instances: list, label: str = "Foliage",
                          folder: str = "") -> dict:
    "Create ONE actor holding N instanced transforms of a mesh (HierarchicalInstancedStaticMesh) instead of N separate actors. This is how foliage is actually done - 90 grass actors is 90 draw setups and 90 outliner rows for something that should be one. instances is a list of {x,y,z,yaw?,scale?}. A transform component that is not a number fails the whole call naming instances[N].<field> and the transaction is cancelled, so no partial cluster is left behind reported as complete."
    return _post("add_foliage_instances", mesh=mesh, instances=instances,
                 label=label, folder=folder or None)


# --------------------------------------------------------------------------
# Spatial awareness + visual feedback (numbers for correctness, pixels for taste)
# --------------------------------------------------------------------------

@mcp.tool()
def get_actor_bounds(actor_path: str) -> dict:
    "World-space AABB of a PLACED actor: origin, extent, size, min, max. This accounts for the actor's SCALE, unlike the mesh asset's ExtendedBounds - a 1312u rock placed at scale 2.4 is 3150u, and that gap is how things end up swallowing buildings. Accepts actorPath, name or label."
    return _post("get_actor_bounds", actorPath=actor_path)


@mcp.tool()
def check_overlaps(actor_path: str = "", name_contains: str = "", ignore_ground: bool = True,
                   tolerance: float = 25.0) -> dict:
    "Find actors intersecting each other. With no actor_path this is a WHOLE-SCENE audit - the 'what did I get wrong' call. Pure AABB math on bounds, no collision queries, so it works on meshes with no collision (most imported props in an editor world). tolerance ignores shallow touching, which is normal for foliage on ground."
    return _post("check_overlaps", actorPath=actor_path or None, nameContains=name_contains or None,
                 ignoreGround=ignore_ground, tolerance=tolerance)


@mcp.tool()
def trace_ground(x: float, y: float, from_z: float = 100000.0, ignore_actor: str = "") -> dict:
    "Line-trace straight down at (x,y) to find ground height. Returns hit=false honestly when nothing is hit - editor-world collision is NOT guaranteed for imported meshes, and treating a miss as z=0 is how things end up floating."
    return _post("trace_ground", x=x, y=y, fromZ=from_z, ignoreActor=ignore_actor or None)


@mcp.tool()
def capture_camera(location: dict = None, rotation: dict = None, look_at: dict = None,
                   fov: float = 75.0, width: int = 1280, height: int = 720,
                   name: str = "MifShot") -> dict:
    "Render the scene from an ARBITRARY viewpoint to a PNG and return its path - does NOT move the user's viewport, so you can inspect while they keep working. Pass look_at instead of rotation to frame a point. Lit and tonemapped (SCS_FinalColorLDR). The file exists by the time this returns; 'exists' is verified, not assumed. Read the returned path to actually look at it."
    return _post("capture_camera", location=location, rotation=rotation, lookAt=look_at,
                 fov=fov, width=width, height=height, name=name)


@mcp.tool()
def scene_report(ground_z: float = 0.0, float_tolerance: float = 30.0,
                 tall_warn_z: float = 1500.0) -> dict:
    "One-call scene audit: actor count, total bounds, plus actors that are FLOATING above ground, SUNKEN below it, or suspiciously TALL (scale outliers). This is the call that catches the whole class of blind-placement mistakes before anyone has to look at a screenshot."
    return _post("scene_report", groundZ=ground_z, floatTolerance=float_tolerance,
                 tallWarnZ=tall_warn_z)


# --------------------------------------------------------------------------
# Play-In-Editor control and runtime observation
# --------------------------------------------------------------------------

@mcp.tool()
def start_pie(simulate: bool = False, start_location: dict = None,
              start_rotation: dict = None) -> dict:
    "Start Play-In-Editor. DOES NOT BLOCK: the engine defers the start to its next tick, and this handler runs on the game thread, so waiting here would deadlock the very ticks PIE needs. Poll pie_status until state=='running' before asserting on runtime state. simulate=True runs the world WITHOUT possessing a pawn - better for observing systems tick, since it needs no PlayerStart and cannot fail on a missing GameMode."
    return _post("start_pie", simulate=simulate or None, startLocation=start_location,
                 startRotation=start_rotation)


@mcp.tool()
def stop_pie() -> dict:
    "End the Play-In-Editor session. Also deferred - poll pie_status until state=='stopped'."
    return _post("stop_pie")


@mcp.tool()
def pie_status() -> dict:
    "PIE state: state (stopped|starting|running) where running means the world EXISTS and BeginPlay has happened (not merely that a session was requested - sessionActive reports that separately), running/startPending/stopPending/simulating, the PIE world name, elapsed timeSeconds, live actor count, and the possessed pawn + PlayerController when there is one. Also reports editorWorld alongside pieWorld, because during PIE there are TWO worlds and level endpoints see the editor one."
    return _post("pie_status")


@mcp.tool()
def list_pie_actors(class_filter: str = "", name_contains: str = "", limit: int = 200, net_mode: str = "server") -> dict:
    "List actors in the RUNNING PIE world (list_level_actors sees the editor world instead - during PIE they are different worlds with different actor paths). The returned actorPath is a LIVE object, so get_property against it reads the running value: that is how you assert on runtime state."
    return _post("list_pie_actors", classFilter=class_filter or None,
                 nameContains=name_contains or None, limit=limit, netMode=net_mode)


@mcp.tool()
def run_console_captured(command: str, filter: str = "") -> dict:
    "Run an editor/game console command AND capture its log output. run_console returns only whether a handler claimed the command; mif.kr.* commands log rather than writing to the Exec archive, so this brackets GLog for the duration of the call. Runs against the PIE world when playing, otherwise the editor world. Only output logged SYNCHRONOUSLY during the call is captured - async work reports nothing here."
    return _post("run_console_captured", command=command, filter=filter or None)


@mcp.tool()
def self_audit() -> dict:
    "The plugin reporting its OWN invariants from inside the running DLL: live endpoint count and names (the ones actually dispatching, not parsed from a header), each endpoint's transaction bucket (readOnly / selfManaged / transacted / compileHeavy), any policyContradictions (an endpoint in both readOnly and selfManaged - the latter would be silently ignored), healthy, plus buildDate/buildTime so a stale DLL is detectable."
    return _post("self_audit")


# --------------------------------------------------------------------------
# Level / placed-actor editing (the level currently open in the editor)
# --------------------------------------------------------------------------

@mcp.tool()
def list_level_actors(class_filter: str = "", name_contains: str = "", folder: str = "",
                      selected_only: bool = False, limit: int = 200) -> dict:
    "List actors placed in the CURRENT level with actorPath, name, label, class, folder and transform. class_filter matches any class in the ancestry by substring, so 'StaticMeshActor' finds subclasses. Returns matched (the true total) alongside count, and truncated=true if limit was hit. actorPath is the handle every other level endpoint takes - and set_property accepts it as objectPath to edit per-instance properties."
    return _post("list_level_actors", classFilter=class_filter or None,
                 nameContains=name_contains or None, folder=folder or None,
                 selectedOnly=selected_only or None, limit=limit)


@mcp.tool()
def spawn_actor_in_level(actor_class: str, location: dict = None, rotation: dict = None,
                         scale: dict = None, label: str = "", folder: str = "",
                         mesh: str = None) -> dict:
    "Spawn an actor into the current level. actor_class may be a native class or a Blueprint class path (/Game/BP/BP_Foo.BP_Foo_C). location/rotation/scale take {x,y,z} (rotation also accepts {pitch,yaw,roll}, and scale accepts a bare number for uniform); rotation is pitch/yaw/roll. mesh assigns a static mesh (spawn a StaticMeshActor for it) - it used to be accepted and silently dropped, producing an EMPTY actor that reported ok. Transforms are validated BEFORE the spawn, so a bad component fails without leaving an actor behind. EVERY numeric field is strict now: a value you SUPPLY that is not a number is a hard error naming the field, the value and the expected type. It is never defaulted - location={\"x\":\"not-a-number\",\"y\":123,\"z\":456} used to return ok:true having applied y and z, kept the old x, and echoed the mixture back as if you had asked for it. Returns the new actorPath. The level is left DIRTY - call save_package on the map path to persist."
    return _post("spawn_actor_in_level", actorClass=actor_class, location=location,
                 rotation=rotation, scale=scale, label=label or None, folder=folder or None,
                 mesh=mesh)


@mcp.tool()
def set_actor_transform(actor_path: str, location: dict = None, rotation: dict = None,
                        scale: dict = None, relative: bool = False) -> dict:
    "Move/rotate/scale a placed actor. Omitted components keep their current value, so this doubles as move-only; rotation accepts {x,y,z} or {pitch,yaw,roll}, and any of the three may also be [x,y,z]. relative=True treats location and rotation as DELTAS instead of absolutes - and REFUSES if scale is also passed, because there is no unambiguous relative scale. EVERY numeric field is strict now: a value you SUPPLY that is not a number is a hard error naming the field, the value and the expected type. It is never defaulted - location={\"x\":\"not-a-number\",\"y\":123,\"z\":456} used to return ok:true having applied y and z, kept the old x, and echoed the mixture back as if you had asked for it. A relative call now seeds its deltas from ZERO, so an omitted component means 'no delta'; it used to seed from the current transform and then add the current transform again, doubling every component you did NOT send. The response reports locationApplied/rotationApplied/scaleApplied so the echoed transform can never be misread as 'all three applied as requested'. Unknown parameters are rejected by name."
    return _post("set_actor_transform", actorPath=actor_path, location=location,
                 rotation=rotation, scale=scale, relative=relative or None)


@mcp.tool()
def set_actor_label(actor_path: str, label: str = "", folder: str = "") -> dict:
    "Set a placed actor's World Outliner display label and/or its outliner folder. The label is not the object name; changing it is safe and breaks no references."
    return _post("set_actor_label", actorPath=actor_path, label=label or None, folder=folder or None)


@mcp.tool()
def delete_level_actor(actor_path: str, confirm: bool = False) -> dict:
    "Delete a placed actor from the current level. Requires confirm=True."
    return _post("delete_level_actor", actorPath=actor_path, confirm=confirm)


@mcp.tool()
def select_level_actors(actor_paths: list = None, clear: bool = False) -> dict:
    "Set the editor's actor selection (clear=True empties it first). Useful for handing off to a human mid-task - the selected actors get gizmos and drive the editor's own tooling. Returns the resulting selection."
    return _post("select_level_actors", actorPaths=actor_paths, clear=clear or None)


# --------------------------------------------------------------------------
# User-defined structs and enums (Blueprint types, not native C++)
# --------------------------------------------------------------------------

@mcp.tool()
def create_struct(path: str, members: list = None) -> dict:
    "Create a Blueprint user-defined struct at a /Game/ path. members is a list of {name, type, container?, valueType?, default?} using the same type grammar as add_variable. A struct must keep at least one member to compile, so the engine's placeholder is only removed once your own members exist."
    return _post("create_struct", path=path, members=members or None)


@mcp.tool()
def list_struct_members(struct: str) -> dict:
    "List a user-defined struct's members: name, friendlyName, guid, type, default, and invalid=true for any member whose type failed to resolve."
    return _post("list_struct_members", struct=struct)


@mcp.tool()
def add_struct_member(struct: str, name: str, type: str, container: str = "",
                      value_type: str = "", default: str = "") -> dict:
    "Add a member to an existing user-defined struct. Same type grammar as add_variable (container = array|set|map, value_type for maps)."
    return _post("add_struct_member", struct=struct, name=name, type=type,
                 container=container or None, valueType=value_type or None,
                 default=default or None)


@mcp.tool()
def remove_struct_member(struct: str, name: str = "", guid: str = "", confirm: bool = False) -> dict:
    "Remove a member from a user-defined struct, by name or guid. Refuses to remove the last member (an empty struct will not compile). Requires confirm=True."
    return _post("remove_struct_member", struct=struct, name=name or None,
                 guid=guid or None, confirm=confirm)


@mcp.tool()
def create_enum(path: str, values: list = None) -> dict:
    "Create a Blueprint user-defined enum at a /Game/ path. values is a list of display-name strings. The underlying FNames are engine-generated; the strings you pass become the display names, which is what Blueprint shows and what list_enum_values reports."
    return _post("create_enum", path=path, values=values or None)


@mcp.tool()
def add_enum_value(enum: str, value: str) -> dict:
    "Append an entry to a user-defined enum. value is the display name. Returns its index."
    return _post("add_enum_value", enum=enum, value=value)


@mcp.tool()
def remove_enum_value(enum: str, value: str = "", index: int = None, confirm: bool = False) -> dict:
    "Remove an entry from a user-defined enum, by display name or index. Refuses to remove the last one. WARNING: removing a non-final entry shifts every later index down, silently re-pointing anything that stored the enum by index - the response warns when this happens. Requires confirm=True."
    return _post("remove_enum_value", enum=enum, value=value or None, index=index, confirm=confirm)


# --------------------------------------------------------------------------
# Animation assets (read-only)
# --------------------------------------------------------------------------

@mcp.tool()
def list_animations(filter: str = "", skeleton: str = "", limit: int = 200) -> dict:
    "List animation assets (sequences, montages, blend spaces, composites) from the asset registry WITHOUT loading them. Optional substring filter on path and skeleton. Returns truncated=true if the limit was hit."
    return _post("list_animations", filter=filter or None, skeleton=skeleton or None, limit=limit)


@mcp.tool()
def describe_animation(asset_path: str) -> dict:
    "Describe an animation asset: skeleton, playLength, notifies (with notify-state windows and branching points), curves. Plus per type - sequence: frameRate/numSampledKeys/additive/syncMarkers; montage: blend times, sections (with nextSection) and slot segments; blendSpace: axes and samples. For an animation BLUEPRINT use list_graphs/list_nodes instead - nested state machines and transition graphs are included."
    return _post("describe_animation", assetPath=asset_path)


# --------------------------------------------------------------------------
# Asset lifecycle (/Game/ only, headless)
# --------------------------------------------------------------------------

@mcp.tool()
def delete_asset(path: str, confirm: bool = False) -> dict:
    "Delete a /Game/ asset package. Requires confirm=True."
    return _post("delete_asset", path=path, confirm=confirm)


@mcp.tool()
def rename_asset(path: str, new_path: str, confirm: bool = False) -> dict:
    "Rename/move a /Game/ asset package, leaving a redirector. Requires confirm=True."
    return _post("rename_asset", path=path, newPath=new_path, confirm=confirm)


@mcp.tool()
def duplicate_asset(path: str, new_path: str) -> dict:
    "Duplicate a /Game/ asset to a new path. No confirm needed - it never destroys or overwrites."
    return _post("duplicate_asset", path=path, newPath=new_path)


@mcp.tool()
def get_referencers(path: str) -> dict:
    "Which packages reference this asset. Authoritative - reads the asset registry's dependency graph, so it is immune to the FName trap where a trailing _<digits> is stored as a separate number and a literal name search misses real references. Note it sees hard refs and soft object/class paths, but NOT a path stored as a plain string in a DataTable cell. package == packageName == the PACKAGE path you asked about (an object path is accepted and reduced), and every referencers[] entry is a PACKAGE path too - the registry graph is package-to-package, so there is no objectPath to give. Feed them straight into audit_unused's exclude_referencers."
    return _post("get_referencers", path=path)


@mcp.tool()
def get_dependencies(path: str) -> dict:
    "Which packages this asset references (the inverse of get_referencers). Same shape: package == packageName, and every dependencies[] entry is a PACKAGE path."
    return _post("get_dependencies", path=path)


@mcp.tool()
def audit_unused(path_prefix: str, cls: str = "", include_all: bool = False,
                 limit: int = 4000, rescan: bool = False,
                 exclude_referencers: list = None) -> dict:
    "Find unused assets under a folder in one call. Returns, per asset, objectPath + packageName (same spelling as find_assets), refs (total referencing packages) and extRefs (those outside its own folder). extRefs is the telling number: a cluster that only references itself has refs>0 but extRefs==0 and is just as unshipped as something with no references. include_all returns every asset rather than only the unreferenced; rescan forces a re-scan first so freshly-created assets are not reported dead. exclude_referencers names referencers whose reference DOES NOT COUNT toward 'used' - one dev-only test level that references everything otherwise makes every asset look alive. Each entry is an exact package path (/Game/DevTest/L_Scratch) or a folder prefix (/Game/DevTest/, trailing slash optional, matches everything beneath); an object path is reduced to its package, so a value pasted out of find_assets works unedited. A malformed entry is an ERROR naming it, never a silent skip."
    # THREE REFUSALS EXIST TO KEEP THE BRIDGE ANSWERING (every handler runs inline on the game
    # thread, so an unbounded scan takes the whole HTTP server offline for its duration):
    #   - rescan=True is refused for a path_prefix of fewer than two segments (/Game, /) - forcing a
    #     synchronous re-scan of a mount root is minutes of frozen editor;
    #   - the call errors instead of waiting when the asset registry is still scanning (it used to
    #     call WaitForCompletion unconditionally, even when rescan was not asked for);
    #   - a path_prefix matching more than 20000 assets is refused, because referencers are queried
    #     per asset regardless of `limit` (limit caps the reported rows, not the work).
    return _post("audit_unused", pathPrefix=path_prefix, **{"class": cls or None},
                 includeAll=include_all, limit=limit, rescan=rescan,
                 excludeReferencers=exclude_referencers or None)


# --------------------------------------------------------------------------
# Cooked-Blueprint reconstruction
# --------------------------------------------------------------------------

@mcp.tool()
def create_editable_child(source_asset: str, child_path: str = "", variant: str = "child") -> dict:
    "Mint a persistent EDITABLE copy of a cooked Blueprint (whose graphs are stripped and cannot be read directly). source_asset is the cooked BP's _C class path or asset path. variant: child (inherits source) | sibling/uncooked (parent-class copy) | full/sibling_full (also reconstructs the whole Blueprint-parent chain into editable siblings). Graphs are filled with decompiled nodes only if MifKismetReconstructor is loaded; otherwise they are signature-only stubs."
    return _post("create_editable_child", sourceAsset=source_asset,
                 childPath=child_path or None, variant=variant)


# --------------------------------------------------------------------------
# Spawn into a RUNNING PIE world (not the editor world)
# (Relocated in Batch C: this block used to sit AFTER the __main__ guard, where
#  mcp.run() blocks before it executes - the tool never registered at runtime.)
# --------------------------------------------------------------------------

@mcp.tool()
def spawn_actor_in_pie(actor_class: str, location: dict = None, rotation: dict = None,
                       scale: dict = None, label: str = None, net_mode: str = "server",
                       mesh: str = None) -> dict:
    "Spawn an actor into the RUNNING PIE world. spawn_actor_in_level cannot do this - it goes through UEditorActorSubsystem, which serves the EDITOR world. Needed because a mod whose bootstrap is UE4SS (which does not run in the editor) otherwise never spawns under PIE, and placing the actor in the map does not survive a world travel. net_mode picks which PIE world when running multi-client: server (default - a replicated actor spawned here reaches every client), client, or any. Returns hasAuthority/replicates on the spawned actor plus a worlds array of every PIE world, so a wrong-role spawn is visible rather than silent. BeginPlay fires immediately; the actor is not saved to any map and dies with PIE. rotation is x/y/z = pitch/yaw/roll like every other MifBridge transform. mesh assigns a static mesh (spawn a StaticMeshActor for it) - the same parameter spawn_actor_in_level takes, ported here because this endpoint was the unfixed sibling that still accepted it and silently dropped it, producing an EMPTY actor that reported ok."
    return _post("spawn_actor_in_pie", actorClass=actor_class, location=location,
                 rotation=rotation, scale=scale, label=label, netMode=net_mode, mesh=mesh)


# --------------------------------------------------------------------------
# Undo introspection / rollback + dirty-package flows
# --------------------------------------------------------------------------

@mcp.tool()
def list_transactions(limit: int = 20, offset: int = 0, include_objects: bool = False) -> dict:
    "Inspect the editor undo buffer, newest first (offset 0 = newest). Returns queueLength, undoCount, currentIndex (= queueLength - undoCount - 1, the entry the next undo removes), canUndo/canRedo, undoBarrier, nextUndoTitle, and transactions[{index, id, title, context, primaryObject, recordCount, dataSizeBytes}]. include_objects=True adds every affected object path per entry (can be large). Use it to verify what a bridge mutation actually did and to pick a toIndex for undo_transactions."
    return _post("list_transactions", limit=limit, offset=offset,
                 includeObjects=include_objects or None)


@mcp.tool()
def undo_transactions(count: int = 1, to_index: int = None, allow_redo: bool = True) -> dict:
    "Undo the last N editor transactions (count 1..50), or pass to_index to undo down until currentIndex == to_index (-1 = undo everything; capped at 50 steps per call - call again to continue). count and to_index are mutually exclusive. Returns undone, titlesUndone, stoppedEarly(+reason), and the new queueLength/undoCount/currentIndex. WARNING: undoing a Blueprint-touching transaction reinstances classes - RE-RESOLVE any cached object paths afterwards. allow_redo=False makes the undone steps unredoable."
    return _post("undo_transactions",
                 count=None if to_index is not None else count,
                 toIndex=to_index, allowRedo=allow_redo)


@mcp.tool()
def redo_transactions(count: int = 1, to_index: int = None) -> dict:
    "Redo the last N undone transactions (count 1..50), or pass to_index to redo up until currentIndex == to_index (capped at 50 steps per call). Returns redone, titlesRedone, stoppedEarly(+reason), and the new queue position. WARNING: the redo stack is fragile - ANY mutating bridge call between undo and redo wipes it (the engine discards redoable entries when a new transaction begins). The measure -> undo -> re-measure -> redo A/B loop only works if the middle steps are pure reads."
    return _post("redo_transactions",
                 count=None if to_index is not None else count,
                 toIndex=to_index)


@mcp.tool()
def list_dirty_packages(kind: str = "all") -> dict:
    "List every unsaved (dirty) package - what a crash would lose and what save_dirty_packages will touch. kind: content | world | all. Returns count, counts{world, content}, packages[{name, kind, origin(loose|container|new), saveable, assetClass?}]. origin=container means the dirty package lives only in a mounted game pak and can NEVER be saved (saveable=false - the red flag this endpoint exists to raise). World rows include each dirty map's MapBuildData package."
    return _post("list_dirty_packages", kind=kind)


@mcp.tool()
def save_dirty_packages(maps: bool = True, content: bool = True, dry_run: bool = False) -> dict:
    "Save EVERY dirty package in one prompt-free, checkout-free call (per-package saves; deliberately avoids the engine bulk path, whose failure dialog would deadlock the bridge's game thread). Returns neededSaving plus per-package results: saved[] (or wouldSave[] when dry_run), failed[{package, reason}] (e.g. read-only files), skipped[{package, reason}] (e.g. untitled maps - things the engine would drop silently), skippedCookedOrigin[] (dirty pak-only packages that can never be saved). Errors during PIE when maps=True - stop_pie first or pass maps=False."
    return _post("save_dirty_packages", maps=maps, content=content,
                 dryRun=dry_run or None)


# --------------------------------------------------------------------------
# Material graph authoring (Batch D) - create/edit/read/apply/poll.
# NOTE: cooked base-game materials have NO expression graph (stripped at cook);
# graph tools refuse on them - create_material / create_material_instance are
# the routes that work against cooked content.
# (This block sits ABOVE main()/the __main__ guard on purpose: a tool defined
#  after mcp.run() starts never registers - the spawn_actor_in_pie lesson.)
# --------------------------------------------------------------------------

@mcp.tool()
def create_material(path: str, domain: str = "Surface", blend_mode: str = "Opaque",
                    initial_texture: str = "") -> dict:
    "Create a NEW master UMaterial asset at a /Game/ path. domain: Surface | DeferredDecal | LightFunction | Volume | PostProcess | UI. blend_mode: Opaque | Masked | Translucent | Additive | Modulate | AlphaComposite | AlphaHoldout. initial_texture (optional asset path) auto-adds a TextureSample wired to BaseColor (or Normal for normal maps). The initial shader compile is ASYNC - poll shader_compile_status. Errors if the path already exists."
    return _post("create_material", path=path, domain=domain, blendMode=blend_mode,
                 initialTexture=initial_texture or None)


@mcp.tool()
def create_material_function(path: str, description: str = "", expose_to_library: bool = None) -> dict:
    "Create a NEW UMaterialFunction asset at a /Game/ path (reusable graph fragment; call it from materials via add_material_expression class=MaterialFunctionCall). Add FunctionInput/FunctionOutput expressions to define its interface. expose_to_library lists it in the material editor's function library."
    return _post("create_material_function", path=path, description=description or None,
                 exposeToLibrary=expose_to_library)


@mcp.tool()
def add_material_expression(path: str, expression_class: str, x: int = 0, y: int = 0,
                            properties: dict = None, asset: str = "") -> dict:
    "Add a node to a Material or MaterialFunction graph. expression_class accepts short names (ScalarParameter, VectorParameter, TextureSample, Multiply, Add, Lerp, TexCoord, Fresnel, FunctionInput, ...) or full MaterialExpression* names; unknown class errors with the 10 nearest matches. properties is a {name: value} object applied by reflection (e.g. {'ParameterName': 'Roughness', 'DefaultValue': 0.35} or {'Texture': '/Game/T_Rock'}); unknown property name = error, and a failed call adds NOTHING. asset (optional path) auto-wires TextureSample/MaterialFunctionCall/CollectionParameter nodes. Returns expressionName - the handle for connect/delete. Refuses on cooked materials (graph stripped at cook)."
    return _post("add_material_expression", path=path, expressionClass=expression_class,
                 x=x, y=y, properties=properties, asset=asset or None)


@mcp.tool()
def connect_material_expressions(path: str, from_expression: str, to_expression: str,
                                 from_output: str = "", to_input: str = "") -> dict:
    "Wire one expression's output into another expression's input inside a material/function graph. from_expression/to_expression each accept THREE forms: the exact object name (from add_material_expression/list_material_expressions), a ParameterName ('Tint'), or a class short name when the graph holds exactly ONE node of that class ('Multiply'). Two candidates under either alias = error listing them, never a guess; the response echoes the resolved OBJECT names. Empty pin names mean 'first pin'; masked outputs accept R/G/B/A. A failed connect echoes the target's input pins and the source's output pins so the next call can self-correct."
    return _post("connect_material_expressions", path=path, fromExpression=from_expression,
                 fromOutput=from_output or None, toExpression=to_expression,
                 toInput=to_input or None)


@mcp.tool()
def connect_material_property(path: str, from_expression: str, material_property: str,
                              from_output: str = "") -> dict:
    "Wire an expression output into a MATERIAL OUTPUT pin - without this the graph never affects pixels. from_expression accepts the exact object name, a ParameterName, or a class short name unique in the graph (ambiguity errors with the candidates). material_property (MP_ prefix optional, case-insensitive): BaseColor, Roughness, Metallic, Specular, Normal, EmissiveColor, Opacity, OpacityMask, Anisotropy, Tangent, WorldPositionOffset, SubsurfaceColor, ClearCoat, ClearCoatRoughness, AmbientOcclusion, Refraction, CustomizedUVs0-7, PixelDepthOffset, ShadingModel, Displacement. Materials only (functions use FunctionOutput expressions). 'connect failed' usually means the property is disabled for the material's domain/blend mode (e.g. Opacity needs Translucent)."
    return _post("connect_material_property", path=path, fromExpression=from_expression,
                 fromOutput=from_output or None, materialProperty=material_property)


@mcp.tool()
def delete_material_expression(path: str, expression: str = "", delete_all: bool = False) -> dict:
    "Remove one node (expression=<name>) or every node (delete_all=True) from a material/function graph; the engine disconnects it from everything first. Exactly one of the two must be given. expression accepts the exact object name, a ParameterName, or a class short name unique in the graph - an ambiguous alias ERRORS with the candidates rather than deleting a coin-flip node. Returns deleted + remaining counts."
    return _post("delete_material_expression", path=path, expression=expression or None,
                 deleteAll=delete_all or None)


@mcp.tool()
def list_material_expressions(path: str, include_connections: bool = True,
                              include_properties: bool = True) -> dict:
    "Read back a material/function graph: expressions[{name, class, index, x, y, properties{}, inputs[{input, from, fromOutput}]}], connectionCount, and (materials) propertyBindings[{property, from, fromOutput}] - the verification read for every graph mutation. On cooked materials returns numExpressions:0 with cooked:true (the graph is STRIPPED at cook, not empty - do not confuse the two)."
    return _post("list_material_expressions", path=path,
                 includeConnections=include_connections, includeProperties=include_properties)


@mcp.tool()
def layout_material_expressions(path: str) -> dict:
    "Auto-arrange a material/function graph's nodes in a grid so a human opening the asset sees something readable. Only nodes REACHABLE from material property inputs (or function inputs/outputs) are moved - disconnected nodes stay put."
    return _post("layout_material_expressions", path=path)


@mcp.tool()
def recompile_material(path: str) -> dict:
    "Apply graph/parameter edits to the renderer - REQUIRED after add/connect/delete for the changes to reach pixels. Dispatches on asset class: UMaterial (non-blocking recompile core; deliberately avoids the engine library call, whose hidden tail runs garbage collection twice, opens a modal progress dialog, and busy-waits on debug shaders - each one lethal mid-HTTP-handler), UMaterialFunction (updates every material using it), or UMaterialInstanceConstant. Returns immediately with {compiling, numRemainingJobs}; shader compilation continues in the BACKGROUND - poll shader_compile_status until compiling=false. Refuses on cooked materials (shaders ship as fixed permutations)."
    return _post("recompile_material", path=path)


@mcp.tool()
def shader_compile_status() -> dict:
    "Poll the editor-wide shader compiler (GShaderCompilingManager): {compiling, numRemainingJobs, numOutstandingJobs, numPendingJobs}. THE poll half for recompile_material / create_material (and level-load shader churn). Numbers decrease toward zero; compiling=false with numRemainingJobs=0 means quiescent - safe to read get_material_stats-style numbers or capture pixels."
    return _post("shader_compile_status")


# --------------------------------------------------------------------------
# Enhanced Input authoring
# (Registry drift: this endpoint has had MIF_DECL + MIF_BIND since Nodes7 landed
#  but never got an @mcp.tool, so it was unreachable over MCP.)
# --------------------------------------------------------------------------

@mcp.tool()
def add_enhanced_input_action(graph_id: str, input_action: str, x: int = 0, y: int = 0) -> dict:
    "Add a UK2Node_EnhancedInputAction event node (the 'IA_Foo' node you normally get by right-clicking the graph and searching for the action asset) - the one node class the bridge could not author, which forced every Enhanced Input binding to be finished by hand. input_action is a UInputAction object path (/Game/X/IA_Foo.IA_Foo) or its package path (/Game/X/IA_Foo). Pins (Triggered/Started/Ongoing/Canceled/Completed plus a value pin typed by the action's ValueType) are generated FROM the action, so an unresolvable path is an error rather than a pin-less node."
    return _post("add_enhanced_input_action", graphId=graph_id, inputAction=input_action, x=x, y=y)


# --------------------------------------------------------------------------
# Level streaming (Batch I) - sublevels in the editor world, level instances
# in the running PIE world.
#
# list_sublevels is the READ half AND the poll endpoint for every mutation
# here: streaming state changes land across frames, so the deferred verbs
# return an opId and nothing blocks. Poll until pending clears / ready=true.
# (This block sits ABOVE main()/the __main__ guard on purpose: a tool defined
#  after mcp.run() starts never registers - the spawn_actor_in_pie lesson.)
# --------------------------------------------------------------------------

@mcp.tool()
def list_sublevels(world: str = "editor", net_mode: str = "server") -> dict:
    "List the sublevels of a world: persistent{}, sublevels[{packagePath, objectPath, streamingClass, loaded, visible, editorVisible, pending, ...}], count/loadedCount/visibleCount/pendingCount, currentLevel, isPartitioned, ready, and ops[] (the deferred add/remove/streaming jobs and their state). world = editor|pie - during PIE there are TWO worlds and the editor verbs see the editor one; net_mode picks which PIE world when running multi-client and is only meaningful with world='pie'. THIS IS THE POLL ENDPOINT for add_sublevel / remove_sublevel / set_sublevel_streaming / set_sublevel_visibility / pie_load_level_instance / pie_unload_level_instance."
    return _post("list_sublevels", world=world, netMode=net_mode)


@mcp.tool()
def add_sublevel(path: str, streaming_class: str = "alwaysloaded",
                 location: dict = None, rotation: dict = None) -> dict:
    "Add an existing map as a sublevel of the open world. path is a package path (/Game/Maps/TownDistrict). streaming_class = alwaysloaded | dynamic. location/rotation take {x,y,z}, rotation as pitch/yaw/roll like every other MifBridge transform. DEFERRED: returns requested/deferred/opId immediately and the engine work runs off a next-tick timer, because AddLevelToWorld flushes level streaming and re-registers a ULevel - a cascade that must not ride an undo transaction. Poll list_sublevels. Refuses BEFORE calling the engine in the two cases where the engine would open a MODAL dialog and deadlock the bridge: the path IS the persistent level, or it is already a sublevel (that one answers alreadyPresent:true, changed:false). A cooked .pak-only map has no loose .umap to add and is refused by name."
    return _post("add_sublevel", path=path, streamingClass=streaming_class,
                 location=location, rotation=rotation)


@mcp.tool()
def remove_sublevel(path: str, discard_unsaved: bool = False) -> dict:
    "Remove a sublevel from the open world. DEFERRED (opId, poll list_sublevels) for a stronger reason than add_sublevel: RemoveLevelsFromWorld RESETS the transaction buffer, then forces a GC, then runs a stale-reference sweep that is FATAL when the buffer was reset - none of which may happen with the bridge's HTTP call frame on the stack. Expect undoBufferReset:true in the response: your undo history is gone afterwards, by the engine's design, not the bridge's. Refuses the persistent level (use load_level / new_level to change the open map). discard_unsaved drops unsaved changes in that sublevel instead of refusing."
    return _post("remove_sublevel", path=path, discardUnsaved=discard_unsaved)


@mcp.tool()
def set_sublevel_visibility(path: str, visible: bool = None, should_be_loaded: bool = None,
                            should_be_visible: bool = None, lighting_scenario: bool = None) -> dict:
    "Flip a sublevel's flags: visible (EDITOR viewport visibility), should_be_loaded / should_be_visible (RUNTIME streaming intent), lighting_scenario. PARTIAL UPDATE - omitted flags are left alone, and a call that passes none of them is an error. EVERY write is READ BACK and compared: setters that do nothing are reported in ignored[{field, requested, actual, reason}] rather than echoed as success, and a call where NOTHING took is an ERROR. That is not belt-and-braces - ULevelStreamingAlwaysLoaded::ShouldBeLoaded() is hardcoded to return true, so should_be_loaded=False on an always-loaded sublevel (which is what add_sublevel creates by default) does nothing at all. Inline, not deferred, but the load/unload itself lands over later frames: check pending, then poll list_sublevels."
    return _post("set_sublevel_visibility", path=path, visible=visible,
                 shouldBeLoaded=should_be_loaded, shouldBeVisible=should_be_visible,
                 lightingScenario=lighting_scenario)


@mcp.tool()
def set_current_sublevel(path: str) -> dict:
    "Set which level new actors are spawned into. path is a sublevel's package path, or the literal 'persistent'. Without this sublevels are decoration: spawn_actor_in_level, spawn_many and duplicate_actors always land in whatever level is current. Returns currentLevel, previousLevel, changed."
    return _post("set_current_sublevel", path=path)


@mcp.tool()
def set_sublevel_streaming(path: str, streaming_class: str) -> dict:
    "Change a sublevel's streaming class: alwaysloaded | dynamic. DEFERRED (opId, poll list_sublevels) because SetStreamingClassForLevel does not edit a property - it REMOVES the ULevelStreaming and re-adds the level, returning a NEW object, and an object-identity swap mid-array is not an undoable property revert. Refuses when the sublevel is not loaded: the engine asserts (check(Level)) and takes the editor down rather than returning an error - load it with set_sublevel_visibility {should_be_loaded: True} first. Returns fromClass/toClass, and changed:false with no engine call when it is already that class."
    return _post("set_sublevel_streaming", path=path, streamingClass=streaming_class)


@mcp.tool()
def pie_load_level_instance(path: str, location: dict = None, rotation: dict = None,
                            visible: bool = True, net_mode: str = "server",
                            name_override: str = "", temp_package: bool = False) -> dict:
    "Stream a level into the RUNNING PIE world as an instance - test setup without a Lua command, and the counterpart to spawn_actor_in_pie. path is the SOURCE map's package path; location/rotation ({x,y,z}, pitch/yaw/roll) place the instance; net_mode picks which PIE world when running multi-client. name_override names the instance (otherwise one is generated); temp_package loads it into a transient package. The request runs INLINE and hands back the real handle (instanceName, objectPath) because the engine's LoadLevelInstance never blocks and never dialogs - but the STREAMING is async, so poll list_sublevels {world:'pie'} until it reports loaded/visible. The instance is not saved to any map and dies with PIE."
    return _post("pie_load_level_instance", path=path, location=location, rotation=rotation,
                 visible=visible, netMode=net_mode, nameOverride=name_override or None,
                 tempPackage=temp_package)


@mcp.tool()
def pie_unload_level_instance(instance_name: str = "", object_path: str = "", path: str = "",
                              net_mode: str = "server") -> dict:
    "Unload a level instance from the running PIE world. Identify it by instance_name (what pie_load_level_instance returned), object_path, or path naming the SOURCE map - one of the three is required. Requests the unload and returns; the teardown happens over the following frames via the streaming update, so poll list_sublevels {world:'pie'}. An instance already being unloaded answers changed:false rather than erroring."
    return _post("pie_unload_level_instance", instanceName=instance_name or None,
                 objectPath=object_path or None, path=path or None, netMode=net_mode)


# --------------------------------------------------------------------------
# Cooked-Blueprint reconstruction (Batch R) - kr_* endpoints.
# These are NOT MifBridge built-ins: they are registered into the bridge at
# editor startup by the MifKismetReconstructor plugin (self_audit reports them
# with provider "MifKismetReconstructor"). If that plugin is absent every kr_*
# call returns "unknown endpoint" and the rest of the bridge is unaffected.
# They are the route to cooked Blueprint LOGIC, which list_graphs / list_nodes
# structurally cannot read (cooking strips the editor-only UBlueprint).
# (This block sits ABOVE main()/the __main__ guard on purpose: a tool defined
#  after mcp.run() starts never registers - the spawn_actor_in_pie lesson.)
# --------------------------------------------------------------------------

@mcp.tool()
def kr_list_cooked_blueprints(path_contains: str = "/Game/", cooked_only: bool = True,
                              include_widgets: bool = True, offset: int = 0,
                              limit: int = 200) -> dict:
    "Census of cooked Blueprint packages straight from the asset registry - the way to size and page the reconstructable corpus before touching it. path_contains is a SUBSTRING of the package name ('*' = every mounted root incl. DLC). Returns total (every Blueprint package before filtering), matched (after), returned, truncated, and blueprints[{objectPath, packageName, name, class, cooked, loaded, generatedClass}] sorted by package so consecutive pages neither overlap nor skip. Loads NOTHING - 'loaded' reports current editor state, not reachability. Rows prefer the *_C generated-class path, which is what every other kr_* tool takes. limit>2000 is refused, not clamped."
    return _post("kr_list_cooked_blueprints", pathContains=path_contains,
                 cookedOnly=cooked_only, includeWidgets=include_widgets,
                 offset=offset, limit=limit)


@mcp.tool()
def kr_dump_blueprint(asset: str, function_filter: str = "", include_bytecode: bool = False,
                      max_statements_per_function: int = 500, include_histogram: bool = True,
                      include_properties: bool = True, include_events: bool = True,
                      offset: int = 0, limit: int = 100) -> dict:
    "Structure of a cooked BlueprintGeneratedClass as JSON: own functions (name, scriptBytes, numParams, flags), own properties, event thunks, the parent chain, and counts. asset = the objectPath, ideally the .<Name>_C class path. OUTPUT SIZE IS THE CONSTRAINT: by default NOTHING is disassembled (one cheap reflection pass); include_bytecode=True disassembles ONLY the functions on the returned page, and with no function_filter it force-caps limit at 10 and says so in note/effectiveLimit. max_statements_per_function truncates each array while still reporting totalStatements. opcodeHistogram keys are hex; each statement's 'Inst' field carries the readable name. Works on loose/uncooked Blueprints too (cooked:false says which you got). Use kr_disassemble_function for one function in full."
    return _post("kr_dump_blueprint", asset=asset, functionFilter=function_filter or None,
                 includeBytecode=include_bytecode,
                 maxStatementsPerFunction=max_statements_per_function,
                 includeHistogram=include_histogram, includeProperties=include_properties,
                 includeEvents=include_events, offset=offset, limit=limit)


@mcp.tool()
def kr_disassemble_function(asset: str, function: str, statement_offset: int = 0,
                            statement_limit: int = 2000, include_raw: bool = True) -> dict:
    "THE tool for reading cooked Blueprint logic: one function's Kismet bytecode as a structured JSON statement stream. asset = the .<Name>_C class path, function = an exact OWN function name (inherited functions error with the parent class to call instead; near-miss names are listed). statement_offset/statement_limit page the STATEMENT ARRAY - each statement's StatementIndex field is a BYTE OFFSET into Script (that is what jump targets reference), so do not confuse the two. totalStatements is returned whether or not paginated, so pages concatenate exactly. include_raw=False strips each statement to {Inst, StatementIndex} for cheap control-flow views. An unknown opcode is a DEGRADE not an error: disassemblyFailed/failedOpcode/failedAtIndex plus every statement decoded before the abort."
    return _post("kr_disassemble_function", asset=asset, function=function,
                 statementOffset=statement_offset, statementLimit=statement_limit,
                 includeRaw=include_raw)


@mcp.tool()
def kr_list_events(asset: str, kind: str = "all", include_frame_param_map: bool = True) -> dict:
    "Event census of a cooked class: every event thunk with its kind, its RECOVERED ubergraph entry offset, param count, and the authoritative frame->param map (read out of the thunk's own bytecode - the generated frame property name must never be reconstructed by hand). kind filters all | event | bndEvt | inpActEvt | sequenceEvent. A Blueprint with no event graph returns ok:true, events:[], status:'NO_UBERGRAPH' - an EMPTY LIST, never an error. counts.realFunctions is own functions that call no ubergraph (ordinary functions, not failed events); rawPointerHits vs identityCalls exposes the gap between the byte-scan prefilter and confirmed calls. Events that fail recovery keep their row with recovered:false and a status reason."
    return _post("kr_list_events", asset=asset, kind=kind,
                 includeFrameParamMap=include_frame_param_map)


@mcp.tool()
def kr_analyze_ubergraph(asset: str, include_per_event: bool = True,
                         include_offsets: bool = False) -> dict:
    "Ubergraph slice analysis for ONE cooked Blueprint: prologue shape, per-event reachability, and the shared/unreached statement counts. Read-only - builds no graphs, mints nothing, compiles nothing. The number that matters is counts.sharedLatent: a latent statement (Delay/timeline) reached by more than one event cannot be split into per-event graphs faithfully, because Delay dedupes on CallbackTarget+UUID. Numbers are self-checkable: analysedStmts == reached1 + shared + unreached (echoed in the 'invariant' field); prologue statements and EndOfScript are excluded from analysedStmts on purpose, which is what keeps 'unreached' meaningful. walkCapHit=true means the counts are a LOWER BOUND; disasmAborted=true means they are PARTIAL. include_offsets adds the raw sharedLatent/unreached byte offsets."
    return _post("kr_analyze_ubergraph", asset=asset, includePerEvent=include_per_event,
                 includeOffsets=include_offsets)


@mcp.tool()
def kr_pin_type_from_property(class_path: str, property: str, self_scope: str = "") -> dict:
    "Turn any class property into the exact type string add_variable / add_pin / create_function / set_pin_type accept - instead of guessing category/subcategory spellings. class_path takes a _C class path, a plain asset path, or a native class path (/Script/Engine.Actor). Returns TWO forms: pinType (the reconstructor's lossless FEdGraphPinType JSON) and bridgeType (the short grammar), plus bridgeContainer/bridgeValueType and a ready-to-paste addVariableExample. bridgeTypeUsable=false with bridgeTypeNote when a pin has no grammar spelling at all (delegates, wildcards, field paths) - never a plausible-looking string that would be rejected. Object/class refs are emitted as FULL PATHS, enums with the explicit 'enum:' prefix, and float vs double is read from the pin subcategory (getting that wrong breaks UMG TAttribute<float> bindings). Works on cooked assets: FProperty reflection survives cooking."
    # "class" is a Python keyword, so the bridge key cannot be a named parameter here.
    return _post("kr_pin_type_from_property",
                 **{"class": class_path, "property": property, "selfScope": self_scope or None})


@mcp.tool()
def kr_reconstruct_request(source_asset: str, mode: str = "copy", variant: str = "",
                           function: str = "", target_path: str = "") -> dict:
    "Start the single kr job: decompile a cooked Blueprint's bytecode into editable K2 graphs. mode='copy' mints a whole persistent editable Blueprint (variant: child | sibling | uncooked | sibling_full | full) and SAVES it; mode='function' reconstructs ONE function into a scratch Blueprint under /Game/Reconstructed and leaves it dirty (save with save_dirty_packages). variant is copy-only and function is function-only - passing the wrong one is an ERROR, never ignored. Requesting the ubergraph in function mode is refused with the reason. Returns a jobId IMMEDIATELY: the work is deferred one tick and is ATOMIC (the HTTP listener is a game-thread ticker, so nothing is read off the socket while it runs - mid-job progress is impossible, not merely unimplemented). Poll kr_reconstruct_status. ONE job slot, no queue: a second request while one runs is REFUSED naming the running jobId."
    return _post("kr_reconstruct_request", sourceAsset=source_asset, mode=mode,
                 variant=variant or None, function=function or None,
                 targetPath=target_path or None)


@mcp.tool()
def kr_reconstruct_status(job_id: str = "") -> dict:
    "Poll the single kr job slot - THE poll half for EVERY kr job kind (reconstruct, verify, classify, census, batch); there is no per-kind status endpoint. Omit job_id for the retained job. Returns kind, state (queued|running|done|failed), phase, elapsedMs, functionsTotalEstimate vs functionsDone/functionsReconstructed/functionsDegraded, eventsDone/eventsReconstructed, nodesCreated, compile{measured, errors, warnings, firstError} and the kind-specific result{}. compile.measured=false means nothing measured it - errors:0 there does NOT mean a clean compile; call validate on result.blueprintId for authoritative numbers. progressObservable says whether the counters can move mid-job: FALSE for the single-Blueprint kinds (reconstruct/verify/classify), which are ATOMIC because the HTTP listener is a game-thread ticker and reads nothing off the socket while they run; TRUE for the SLICED kinds (census/batch), which process one Blueprint per tick so result.bpDone genuinely advances across polls. Exactly ONE record is retained, so poll-after-done works but is lost once the next request is accepted; an unknown job_id answers found:false naming the id that IS retained. Job records are in-memory only and do not survive an editor restart."
    return _post("kr_reconstruct_status", jobId=job_id or None)


# --------------------------------------------------------------------------
# Wave 3 - the kr_* verify family. Turns "it compiled" into "it provably
# behaves like the original": reconstruct a THROWAWAY TRANSIENT copy, compile
# it, and diff the recompiled bytecode against the cooked original.
#
# All four mint nothing persistent - no asset is saved, registered or opened;
# only the census/batch CSV under <ProjectSaved>/MifKr/ is written. All four
# are deferred and poll through kr_reconstruct_status. There is ONE job slot
# and no queue: a second request while one runs is REFUSED naming the runner.
# --------------------------------------------------------------------------

@mcp.tool()
def kr_verify_fidelity(source_asset: str, classify_intentional: bool = True,
                       allow_anim: bool = False) -> dict:
    "Reconstruct a throwaway transient CHILD of a cooked Blueprint, compile it, and diff every reconstructed function's recompiled bytecode against the cooked original - the whole-Blueprint fidelity aggregate. source_asset is the .<Name>_C class path, the asset path, or the exact name. CHILD-ONLY BY DESIGN: there is no mode/variant parameter, because a sibling copy mints its components into the transient package, so every component reference differs by object path and reports systematic FALSE drift - a number that measures the mode, not the decompiler. A loose/authored Blueprint is deliberately accepted (author with the bridge, reconstruct, diff against what you built). Anim Blueprints are refused unless allow_anim, because the engine mints an anim source as a plain UBlueprint with no AnimGraph and every number would describe that degraded copy. Returns a jobId immediately; poll kr_reconstruct_status. result.fidelity.score is null (never 1.000) when nothing was scored, and the whole fidelity block is ABSENT when the copy failed to compile."
    return _post("kr_verify_fidelity", sourceAsset=source_asset,
                 classifyIntentional=classify_intentional, allowAnim=allow_anim)


@mcp.tool()
def kr_classify_drift(source_asset: str, function: str = "", classify_intentional: bool = True,
                      allow_anim: bool = False) -> dict:
    "kr_verify_fidelity decomposed PER FUNCTION: result.functions[{name, verdict, reasons[], detail}] with verdict in identical/equivalent/intentional/drift/missing/uncomparable, plus verdictCounts, reasonTally and consistent. For real drift it reports the ROOT-CAUSE edit rather than the first raw stream difference - one inserted statement re-ordinalises every later jump, so the first raw difference is usually a cascade artefact. `function` FILTERS THE REPORT ONLY; it does not narrow the work, because the pipeline is per-Blueprint and the whole verify runs regardless. This kind costs roughly TWICE kr_verify_fidelity (the verifier runs once for the aggregate and once per function), which is exactly what makes result.consistent an independent cross-check rather than a tautology. Same CHILD-ONLY rule and same anim gate as kr_verify_fidelity. Returns a jobId; poll kr_reconstruct_status."
    return _post("kr_classify_drift", sourceAsset=source_asset, function=function or None,
                 classifyIntentional=classify_intentional, allowAnim=allow_anim)


@mcp.tool()
def kr_drift_census(path_filter: str = "/Game/", start_index: int = 0, max_count: int = 50,
                    classify_intentional: bool = True) -> dict:
    "Fidelity verify across a path-filtered SET of cooked Blueprints with the classifier's census instrument (mif.kr.DriftCensus) forced on for the job, producing running corpus totals over HTTP plus the on-disk CSV of every UNCLAIMED drift edit - the data a rule author needs to decide which drift classes actually dominate, without babysitting a console for an hour. path_filter is a SUBSTRING of the package name ('*' = every mounted root). max_count defaults to 50 so an accidental whole-corpus run must be opt-in: pass 0 to ask for the entire filtered corpus explicitly. start_index is the crash-resume cursor - result.resumeHint is the value to pass if the editor dies mid-sweep. CHILD MODE ALWAYS, VERIFY ALWAYS, COOKED-ONLY (loose Blueprints are exactly what a corpus fidelity number must not dilute; anim Blueprints are excluded by the same rule as kr_verify_fidelity). SLICED one Blueprint per tick, so the bridge keeps answering and result.bpDone genuinely advances across polls of kr_reconstruct_status. Returns corpusFidelity/corpusAdjusted (null when nothing scored), skipTaxonomy{resolve,parent,mint} and censusCsvPath."
    return _post("kr_drift_census", pathFilter=path_filter, startIndex=start_index,
                 maxCount=max_count, classifyIntentional=classify_intentional)


@mcp.tool()
def kr_batch_reconstruct(path_filter: str = "/Game/", mode: str = "sibling", verify: bool = False,
                         start_index: int = 0, max_blueprints: int = 0,
                         classify_intentional: bool = True) -> dict:
    "The regression sweep: reconstruct every matching cooked Blueprint into a throwaway copy, compile it, tally PASS/FAIL/SKIP with the three-way skip taxonomy, and write the engine harness's exact CSV. This is mif.kr.ReconstructAll over HTTP - except the console command blocks the editor for the whole run and this one is SLICED one Blueprint per tick, so the bridge keeps answering and progress is observable. path_filter is a substring of the package name ('*' = all); max_blueprints 0 means every match. mode = sibling (parent-class copy, the default and what the console sweep does) | child (IS-A the cooked class, the only mode fidelity is measurable in). verify REQUIRES mode='child' and is refused otherwise, loudly: a sibling copy mints its components into the transient package, so verify would emit systematic FALSE drift on every Blueprint and read as a decompiler regression. Nothing is ever saved - use kr_reconstruct_request mode='copy' for a persistent asset. Returns a jobId; poll kr_reconstruct_status."
    return _post("kr_batch_reconstruct", pathFilter=path_filter, mode=mode, verify=verify,
                 startIndex=start_index, maxBlueprints=max_blueprints,
                 classifyIntentional=classify_intentional)


# --------------------------------------------------------------------------
# Batch O - EDITOR UI INVOCATION: invoke the ACTION, never the pixel.
#
# Reaching an editor affordance with no callable API - a third-party plugin's
# toolbar button, a custom editor window, a Details-panel row nobody exposed.
# Pixel clicking through the AutomationDriver is NOT implemented and will not
# be added casually: it deadlocks the bridge if driven from a handler, warps
# the user's real OS mouse pointer, steals window focus, dies when the editor
# is minimised, and cannot address an engine Details row by identity at all
# (no engine editor widget carries a driver id). The full decision, the
# guardrails a future implementation would need, and the list of what cannot
# be made safe are in docs/audit/06_IMPLEMENTED.md "Batch O".
#
# EVERY INVOKING TOOL BELOW CAN OPEN A MODAL. The bridge's HTTP server is a
# game-thread ticker; a modal spins its own loop, the tick stops, the socket
# stops being read, and the call NEVER RETURNS. A hang IS the symptom.
# Diagnose from outside the process:
#   powershell -NoProfile -Command "Get-Process UnrealEditor | Select-Object Id,MainWindowTitle"
# --------------------------------------------------------------------------

@mcp.tool()
def list_editor_commands(context: str = "", command: str = "", filter: str = "",
                         include_unbound: bool = True, include_can_execute: bool = False,
                         include_console: bool = False, console_prefix: str = "",
                         menu: str = "", section: str = "", limit: int = 400) -> dict:
    "DISCOVERY for invoke_editor_command / send_editor_key. Three halves, each honest about what it can see. (a) BINDING CONTEXTS are genuinely ENUMERABLE: every FUICommandInfo in every registered TCommands<> context, with label, description, chord and whether a live command list maps it - this reaches third-party plugins with ZERO coupling (BlueprintAssist's ~150 commands list under context 'BlueprintAssistCommands' without the bridge linking against it). (b) CONSOLE OBJECTS, opt-in via include_console + console_prefix. (c) ONE NAMED MENU, opt-in via menu: its sections and entries with an invokeKind of command/submenu/decoration/unreachableOrToolUIAction - probe-only, because menu NAMES cannot be listed (UToolMenus keeps its registry in a private member). Read commandListSource.contextsWithLists: a context absent from it has no invokable list and invoke_editor_command will say so. include_can_execute runs each command's CanExecute predicate (third-party code) and is off by default; canExecute is null when unknown, never guessed. Invokes NOTHING."
    return _post("list_editor_commands", context=context or None, command=command or None,
                 filter=filter or None, includeUnbound=include_unbound,
                 includeCanExecute=include_can_execute or None,
                 includeConsole=include_console or None, consolePrefix=console_prefix or None,
                 menu=menu or None, section=section or None, limit=limit)


@mcp.tool()
def invoke_editor_command(context: str, command: str, menu: str = "", section: str = "",
                          entry: str = "", dry_run: bool = False, confirm: bool = False,
                          allow_known_modal: bool = False) -> dict:
    "Execute the FUIAction a menu entry or toolbar button is bound to - the same delegate a mouse click ends in, minus hit-testing, minus focus change, minus cursor. START WITH dry_run=True: it resolves the command, finds a live FUICommandList and reports CanExecute without firing anything. confirm=True is REQUIRED to actually execute; without it (and without dry_run) the call FAILS naming the parameter rather than answering ok:true having done nothing. A command whose CanExecute is false is refused, not invoked. A small VERIFIED deny-list of commands whose engine implementation opens a modal unconditionally is refused unless allow_known_modal=True. If the default route reports no live command list, pass menu/section/entry to take the action off a ToolMenus entry instead, or use send_editor_key with the command's chord (that is the only route to commands a plugin dispatches from its own IInputProcessor, which is how BlueprintAssist actually runs). HAZARD: the action is arbitrary third-party code and may open a modal, which stops the bridge until a human clicks - there is no way to prevent that from inside the process."
    return _post("invoke_editor_command", context=context, command=command,
                 menu=menu or None, section=section or None, entry=entry or None,
                 dryRun=dry_run or None, confirm=confirm or None,
                 allowKnownModal=allow_known_modal or None)


@mcp.tool()
def invoke_editor_tab(tab_id: str = "", manager: str = "global", major_tab: str = "",
                      asset: str = "", probe: bool = False, probe_ids: list = None,
                      include_known_ids: bool = True, as_inactive: bool = False) -> dict:
    "Open an editor tab by id via FTabManager::TryInvokeTab - the route BlueprintAssist itself uses to open its own windows. 'Open a custom editor window' is one public call, no pixels. Call with NO tab_id (or probe=True) for DISCOVERY: it probes a curated seed of well-known ids plus anything in probe_ids plus a partial walk of the manager's workspace menu, and reports which ids this editor can actually spawn (probes[].hasSpawner) and which are already open. Tab ids CANNOT be enumerated - the registry and its lookup are both protected in the engine despite carrying the export macro - so probing is the honest primitive and every hasSpawner is a LIVE answer, not a claim from the seed list. manager selects which tab manager: 'global' (nomad/global tabs - OutputLog, ReferenceViewer, BADebugMenu...), 'majorTab' with major_tab='LevelEditor' (level-editor minor tabs such as LevelEditorSelectionDetails - the level Details panel), or 'assetEditor' with asset=<path of an OPEN asset> (Blueprint-editor tabs such as Inspector / MyBlueprint / Palette - the Blueprint Details panel). An unknown id is refused with near misses before anything is constructed. HAZARD: a tab spawner is third-party code and could show a dialog while building its widget - that is exactly how a BlueprintAssist popup took this bridge down once."
    return _post("invoke_editor_tab", tabId=tab_id or None, manager=manager,
                 majorTab=major_tab or None, asset=asset or None, probe=probe or None,
                 probeIds=probe_ids or None, includeKnownIds=include_known_ids,
                 asInactive=as_inactive or None)


@mcp.tool()
def send_editor_key(key: str, confirm: bool = False, dry_run: bool = False,
                    modifiers: dict = None, user_index: int = 0, is_repeat: bool = False,
                    character_code: int = 0, key_code: int = 0, send_key_up: bool = True) -> dict:
    "Inject a key event through FSlateApplication::ProcessKeyDownEvent, which reaches registered IInputProcessors FIRST - the only route to commands a plugin dispatches from its own input processor rather than from a reachable FUICommandList. This is how BlueprintAssist's ~150 commands actually run. key is an FKey name ('Tab', 'F5', 'H', 'SpaceBar'), exactly the spelling list_editor_commands reports as chord.key; an unknown name is refused with near misses. confirm=True is REQUIRED (dry_run=True validates the key, the modifier reality and the current focus without sending). MODIFIED CHORDS ARE REFUSED, NOT FAKED: FSlateApplication::GetModifierKeys() reads the REAL platform keyboard, so a synthetic Ctrl+H is evaluated by any consumer written like BlueprintAssist's as bare H and would fire the wrong command silently - if you ask for modifiers the human is not physically holding, this fails and tells you to use invoke_editor_command instead. Down and up are sent together so a stalled call cannot strand a key down. HAZARD: the key runs whatever is bound to it, which the request does not name, and that can open a modal."
    return _post("send_editor_key", key=key, confirm=confirm or None, dryRun=dry_run or None,
                 modifiers=modifiers or None, userIndex=user_index or None,
                 isRepeat=is_repeat or None, characterCode=character_code or None,
                 keyCode=key_code or None, sendKeyUp=send_key_up)


def main():
    global DEBUG
    parser = argparse.ArgumentParser(description="MifBridge MCP server")
    parser.add_argument("--debug", action="store_true", help="log request/response to stderr")
    args = parser.parse_args()
    DEBUG = args.debug or os.environ.get("MIF_BRIDGE_DEBUG", "").lower() in ("1", "true", "yes")
    _log(f"starting; base={BASE} timeout={TIMEOUT}s token={'set' if TOKEN else 'empty'}")
    mcp.run()


if __name__ == "__main__":
    main()
