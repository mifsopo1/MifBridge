#!/usr/bin/env python3
"""MifBridge MCP server.

Thin FastMCP wrapper over the in-editor MifBridge HTTP plugin. One tool per
plugin endpoint (see docs/05_DESIGN_SPEC.md and docs/00_ARCHITECTURE.md). No
game logic lives here: every tool forwards its arguments as JSON to the local
bridge and returns the bridge's JSON verbatim so Claude sees the real compiler
output.

This file lives at tools/mcp-server/ (renamed from tools/ue5-mcp-bridge/ in
0.3.0). There is a deprecated forwarding shim at the old path -- do NOT add
tools to it; it only runpy's this file.

Designed to front two backends from one tool namespace: Unreal over HTTP
(_post, below) and, when it lands, the MifBlender addon over a local socket
(_blender). Unprefixed tools are Unreal; bl_* would be Blender; mif_* compose
both. As of 0.3.0 only the Unreal backend exists.

TWO BACKENDS, one server. Unprefixed tools reach the UNREAL editor plugin over
HTTP (_post). bl_* tools reach the BLENDER MifBlender addon over a loopback
socket (_blender). mif_* tools are the only ones in this file that contain
logic: they compose both backends. kr_* are UE endpoints registered by a
foreign plugin (see that section's own banner).

The three-way 1:1 registry rule is therefore SCOPED to the UE backend: the
MIF_DECL / MIF_BIND name set must equal the set of endpoint strings passed to
_post(). bl_* and mif_* own no C++ endpoint and are outside that set by
construction - auditing them against MifBridgeHandlers.h would report a dozen
false violations. The checkable form:

    grep -oP '^\\s*MIF_DECL\\(\\K\\w+' Source/MifBridge/Private/MifBridgeHandlers.h | sort -u
    grep -oP '_post\\("\\K[a-z0-9_]+'   tools/mcp-server/server.py              | sort -u

Config (environment):
  MIF_BRIDGE_URL    default http://127.0.0.1:8791/api
  MIF_BRIDGE_TOKEN  default "dev"  (must match the editor's MIF_BRIDGE_TOKEN)
  MIF_BRIDGE_TIMEOUT default 30    (seconds)
  MIF_BLENDER_HOST  default 127.0.0.1
  MIF_BLENDER_PORT  default 8792   (NOT 9876 - that is the third-party
                                    blender-mcp addon, which may run alongside)
  MIF_BLENDER_TOKEN default = MIF_BRIDGE_TOKEN
  MIF_BLENDER_CONNECT_TIMEOUT default 3    (seconds)
  MIF_BLENDER_PROBE_TIMEOUT   default 5    (seconds; bl_status only)
  MIF_BLENDER_TIMEOUT         default 180  (seconds; geometry work)

Run:  python server.py [--debug]
"""

import argparse
import json
import os
import socket
import struct
import sys
import threading
import time
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("MIF_BRIDGE_URL", "http://127.0.0.1:8791/api").rstrip("/")
TOKEN = os.environ.get("MIF_BRIDGE_TOKEN", "dev")
try:
    TIMEOUT = float(os.environ.get("MIF_BRIDGE_TIMEOUT", "30"))
except ValueError:
    TIMEOUT = 30.0


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BLENDER_HOST = os.environ.get("MIF_BLENDER_HOST", "127.0.0.1")
try:
    BLENDER_PORT = int(os.environ.get("MIF_BLENDER_PORT", "8792"))
except ValueError:
    BLENDER_PORT = 8792
BLENDER_TOKEN = os.environ.get("MIF_BLENDER_TOKEN", TOKEN)

# THREE Blender timeouts, deliberately, because "Blender is not running",
# "Blender is wedged" and "Blender is chewing a 200k-tri bevel" are three
# different waits and one number cannot serve all of them:
#   CONNECT (3s)   bounds reaching the listening socket at all. MEASURED on
#                  this box: a closed 127.0.0.1 port returns WSAECONNREFUSED in
#                  ~2.0s, not instantly - Windows retries the SYN first. 3s is
#                  chosen to sit just ABOVE that, so "Blender is not running"
#                  surfaces as the accurate "actively refused" message with the
#                  install steps; drop it to 1s and the same case reports a
#                  misleading connect TIMEOUT instead. Anything longer just adds
#                  dead wait to the commonest failure.
#   PROBE   (5s)   read timeout for bl_status ONLY. A ping does no bpy work, so
#                  5s of silence means the main thread is blocked (modal
#                  operator, file browser, render). This is the number that
#                  makes mif_mesh_roundtrip's step-0 health check cheap instead
#                  of a 180s hang, and it is why that step runs BEFORE Unreal is
#                  asked to write anything.
#   WORK  (180s)   read timeout for every other op. An FBX import/export or a
#                  bevel runs as ONE long main-thread frame in Blender; it
#                  cannot be polled and it must not be cut off half-applied.
BLENDER_CONNECT_TIMEOUT = _envf("MIF_BLENDER_CONNECT_TIMEOUT", 3.0)
BLENDER_PROBE_TIMEOUT = _envf("MIF_BLENDER_PROBE_TIMEOUT", 5.0)
BLENDER_TIMEOUT = _envf("MIF_BLENDER_TIMEOUT", 180.0)

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
    # An endpoint the running DLL does not have never reaches MifBridge's own error handling. Routes
    # are bound one per endpoint (MifBridgeServer.cpp), so an unknown path is answered by Epic's HTTP
    # router with {"errorCode": "...route_handler_not_found", "errorMessage": ""} - no `ok`, and an
    # EMPTY message. A caller checking r["ok"] gets None and one reading r["error"] gets nothing.
    #
    # The realistic way to meet this is not a typo, it is DRIFT: this file gains a tool and the editor
    # is still running a DLL built before it. That deserves to say so, since the fix is a rebuild and
    # nothing in the raw response hints at it.
    if isinstance(data, dict) and "ok" not in data:
        code = str(data.get("errorCode") or "")
        if "route_handler_not_found" in code or response.status_code == 404:
            return {
                "ok": False,
                "error": (
                    f"the running editor has no endpoint named '{endpoint}'. Its MifBridge build is "
                    f"probably older than this MCP server - rebuild the plugin, or call self_audit to "
                    f"see the endpoint list the editor actually has."
                ),
                "errorCode": code or f"HTTP {response.status_code}",
            }
        if code:
            # Any other bare errorCode from the HTTP layer: pass it through in a shape the caller can
            # actually test, rather than a dict with no `ok` in it.
            return {"ok": False, "error": data.get("errorMessage") or code, "errorCode": code}

    _log("<-", endpoint, data)
    return data


# --------------------------------------------------------------------------
# BLENDER BACKEND TRANSPORT - the second choke point, beside _post and
# deliberately NOT merged with it. Every one of the ~224 UE tools names a UE
# endpoint at its call site, so the backend is already implicit there; turning
# _post into a dispatcher would touch every one of them and buy nothing.
#
# Wire protocol (identical framing to the MifBlender addon's framing.py):
#   4-byte BIG-ENDIAN unsigned length prefix + UTF-8 JSON body, 64 MiB cap.
#   request  {"endpoint": <op>, "token": <shared secret>, "params": {...}}
#   response {"ok": true, ...}  |  {"ok": false, "error": "..."}
# Length-prefixed rather than newline- or chunk-delimited because a mesh
# response is megabytes of JSON and a delimiter scan on that is both slow and
# corruptible by any delimiter byte inside a string. Adapted from blender-mcp
# (github.com/MCPBlender/blender-mcp, (c) 2025 Siddharth Ahuja, MIT).
#
# The connection is PERSISTENT and reused across calls, so _BL_LOCK serialises
# send+receive: without it a second op's response can be read as the first's and
# the stream stays desynced until a read timeout fires.
#
# NEVER RAISE and NEVER HANG - same contract as _post. Every failure comes back
# as {"ok": False, "error": ...} naming the fix, bounded by the three timeouts
# above. A tool that hung here would hang the whole MCP client.
# --------------------------------------------------------------------------

_BL_HDR_FMT = ">I"
_BL_HDR_SIZE = 4
_BL_MAX_FRAME = 64 * 1024 * 1024

_BL_SOCK = None
_BL_LOCK = threading.Lock()

# What the lock is currently held FOR, as (op name, time.monotonic() at acquire).
# Written under the lock, read WITHOUT it by a caller that failed to acquire -
# which is the whole point: "another op is in flight and has been for 96s" is
# itself the diagnosis a probe is asking for, and it is unobtainable if the probe
# has to take the lock to learn it. A torn read here is a slightly wrong number
# in an error message, never a wrong answer.
_BL_INFLIGHT = None


def _bl_unreachable(exc) -> dict:
    return {"ok": False, "error": (
        f"Blender backend unreachable at {BLENDER_HOST}:{BLENDER_PORT} ({exc}). "
        "Start Blender, enable the MifBlender addon (Edit > Preferences > Add-ons), and check "
        "View3D > N-panel > MifBridge that the server is listening. Nothing in the UE plugin or "
        "this server can start it for you. Override the address with MIF_BLENDER_HOST / "
        "MIF_BLENDER_PORT if the addon is on a non-default port.")}


def _bl_close():
    """Drop the cached socket so the NEXT call reconnects. Never raises."""
    global _BL_SOCK
    if _BL_SOCK is not None:
        try:
            _BL_SOCK.close()
        except OSError:
            pass
        _BL_SOCK = None


def _bl_connect():
    """Return the cached socket, or open one. Raises OSError on failure."""
    global _BL_SOCK
    if _BL_SOCK is not None:
        return _BL_SOCK
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(BLENDER_CONNECT_TIMEOUT)
    sock.connect((BLENDER_HOST, BLENDER_PORT))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    _BL_SOCK = sock
    return sock


def _bl_recv_exact(sock, count: int) -> bytes:
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ConnectionError("Blender closed the connection mid-frame")
        buf += chunk
    return buf


def _bl_busy_error(op: str, waited: float) -> dict:
    """The answer when the transport lock could not be taken in time.

    Not a timeout dressed up as one: this call never reached Blender, and saying
    WHICH op is holding the line and for HOW LONG is the diagnosis a health probe
    was asking for in the first place.
    """
    held = _BL_INFLIGHT
    if held:
        other, since = held
        detail = (f"'{other}' has held the Blender transport for "
                  f"{time.monotonic() - since:.1f}s")
    else:
        detail = ("another Blender call holds the transport (it finished between the "
                  "failed acquire and this message, so it was only just blocking)")
    return {"ok": False, "error": (
        f"did not send '{op}': {detail}, and this call waited {waited:.1f}s for it. "
        "The connection to Blender is a single serialised socket, so one long op "
        "(an FBX import/export, a bevel on a dense mesh) blocks the rest. Nothing "
        "was sent and nothing was changed. Either wait for it - a geometry op runs "
        f"as ONE main-thread frame and cannot be polled - or, if it has been going "
        "far longer than the work should take, Blender's main thread is wedged on a "
        "modal operator or an open file browser and needs clearing by hand.")}


def _blender(op: str, _timeout: float = None, _lock_timeout: float = None,
             **params) -> dict:
    """Send one framed op to the MifBlender addon and return its JSON verbatim.

    Mirrors _post: unset (None) params are dropped, and every failure is a
    {"ok": False, "error": ...} dict rather than an exception.

    _lock_timeout bounds the wait for the transport lock (NOT the read). Omit it
    and the call queues behind whatever is in flight, which is right for real
    work. Pass it for a PROBE: bl_status exists to answer "is Blender wedged" in
    seconds, and it could not do that while blocking on the same lock for the
    full 180s work timeout - the one tool you reach for when Blender is stuck was
    the one tool made unavailable by Blender being stuck.
    """
    global _BL_INFLIGHT
    body = {k: v for k, v in params.items() if v is not None}
    frame = {"endpoint": op, "token": BLENDER_TOKEN, "params": body}
    read_timeout = BLENDER_TIMEOUT if _timeout is None else _timeout
    _log("bl->", op, body)

    try:
        payload = json.dumps(frame).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"params for Blender op '{op}' are not JSON-serialisable: {exc}"}
    if len(payload) > _BL_MAX_FRAME:
        return {"ok": False, "error": (
            f"request for Blender op '{op}' is {len(payload)} bytes, over the {_BL_MAX_FRAME}-byte "
            "frame cap. Pass a file path rather than inline geometry.")}
    header = struct.pack(_BL_HDR_FMT, len(payload))

    # Bounded acquire ONLY when the caller asked for one. threading.Lock.acquire
    # rejects a timeout together with blocking=False, so the two forms are kept
    # apart rather than collapsed.
    waited = time.monotonic()
    if _lock_timeout is None:
        _BL_LOCK.acquire()
    elif not _BL_LOCK.acquire(timeout=_lock_timeout):
        return _bl_busy_error(op, time.monotonic() - waited)
    _BL_INFLIGHT = (op, time.monotonic())
    try:
        # The retry covers the SEND phase only, and only on a socket we reused.
        # A send that fails on a keep-alive socket means the frame never landed
        # whole, so the op cannot have run: reconnecting and resending is safe.
        # A failure after the send is NEVER retried - the op may already have
        # mutated the scene, and re-running a bevel would bevel twice.
        sock = None
        for attempt in (0, 1):
            reused = _BL_SOCK is not None
            try:
                sock = _bl_connect()
            except OSError as exc:
                _bl_close()
                return _bl_unreachable(exc)
            try:
                sock.settimeout(BLENDER_CONNECT_TIMEOUT)
                sock.sendall(header + payload)
            except OSError as exc:
                _bl_close()
                if reused and attempt == 0:
                    _log("bl--", op, "stale keep-alive socket, reconnecting")
                    continue
                return _bl_unreachable(exc)
            break

        try:
            sock.settimeout(read_timeout)
            (length,) = struct.unpack(_BL_HDR_FMT, _bl_recv_exact(sock, _BL_HDR_SIZE))
            if length > _BL_MAX_FRAME:
                raise ValueError(f"frame header claims {length} bytes, over the {_BL_MAX_FRAME} cap")
            data = json.loads(_bl_recv_exact(sock, length).decode("utf-8"))
        except socket.timeout:
            _bl_close()
            return {"ok": False, "error": (
                f"Blender read timeout after {read_timeout}s on '{op}'. NOT retried: the operation "
                "may have run to completion. Blender's main thread is blocked - a modal operator, "
                "an open file browser or a render will do it. Dismiss it and call bl_status. Raise "
                "MIF_BLENDER_TIMEOUT for a genuinely long geometry op.")}
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            _bl_close()
            return {"ok": False, "error": (
                f"Blender transport error on '{op}': {exc}. The connection was dropped; the next "
                "call reconnects. Check Blender's system console for an addon traceback.")}
    finally:
        _BL_INFLIGHT = None
        _BL_LOCK.release()

    if not isinstance(data, dict):
        return {"ok": False, "error": f"Blender returned a non-object response for '{op}': {data!r:.300}"}
    _log("bl<-", op, data)
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
def get_node(node_guid: str, graph_id: str = "") -> dict:
    "Return full detail (all pins, types, links) for a single node by guid. graph_id scopes the lookup to one graph - the only way to disambiguate two loaded copies of a Blueprint that share NodeGuids, which otherwise reports an ambiguous match."
    return _post("get_node", nodeGuid=node_guid, graphId=graph_id or None)


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
    "Add a variable. name is trimmed+validated and the canonical name is returned. type e.g. int/float/bool/string/Vector/Guid/<Struct>/<Class>. container = array|set|map. For a map, type is the KEY type and value_type is the VALUE type (e.g. type='name', container='map', value_type='int'). scope = member|local (local needs function). REFERENCE TYPES: the class goes INSIDE the type string via a prefix, NOT in a separate parameter - type='object:SceneComponent' gives a variable typed to that class, which will connect to a SceneComponent pin; a bare type='object' gives a plain UObject, which will NOT. The prefixes are object:X (an instance reference), class:X and subclassof:X (a class reference / TSubclassOf), softobject:X and softclass:X (soft pointers). There is no class= / className= / parentClass= / objectClass= parameter and passing one is now a hard error naming this syntax, because it used to be accepted and silently dropped: the call returned ok:true and produced a plain UObject that could not be connected, which read as 'the bridge cannot type object variables'."
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


@mcp.tool()
def set_variable_type(blueprint_id: str, name: str, type: str, container: str = "",
                      value_type: str = "", scope: str = "member", function: str = "") -> dict:
    "Retype an EXISTING variable's DECLARATION (every get/set node of it reconstructs to the new pin type). Same type grammar as add_variable: container = array|set|map, and for a map `type` is the KEY type with value_type the VALUE type. REFERENCE TYPES: the class goes INSIDE the type string - type='object:BP_Foo_C', not a separate class parameter. Prefixes: object:X (instance ref), class:X and subclassof:X (class ref / TSubclassOf), softobject:X and softclass:X (soft pointers). scope = member|local (local needs function). This changes the VARIABLE; to repoint a single NODE at a different declaring class use retarget_variable_node instead."
    return _post("set_variable_type", blueprintId=blueprint_id, name=name, type=type,
                 container=container or None, valueType=value_type or None,
                 scope=scope, function=function or None)


@mcp.tool()
def retarget_variable_node(graph_id: str, node_guid: str, target_class: str = "",
                           to_self: bool = None) -> dict:
    "Repoint one variable get/set NODE at a different declaring class - the node's whole FMemberReference is rewritten and the node reconstructed. Pass to_self=True to point it back at the owning Blueprint instead of a named target_class. The variable NAME is taken from the node you name; there is NO pin argument. This changes WHICH CLASS declares the variable, NOT the pin type - to change the type use set_variable_type. To place a NEW node rather than repoint one, use add_variable_get/add_variable_set with their target class."
    return _post("retarget_variable_node", graphId=graph_id, nodeGuid=node_guid,
                 targetClass=target_class or None, self=to_self)


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

@mcp.tool()
def add_function_call(graph_id: str, function: str, cls: str = "self", x: int = 0, y: int = 0,
                      as_message: bool = False) -> dict:
    "Add a function/library call node. cls is the owning class ('self' for this Blueprint, or e.g. KismetSystemLibrary). Pin types are derived from the reflected UFunction. Automatically picks the correct UK2Node_CallFunction SUBCLASS the way the editor does - CallArrayFunction for UKismetArrayLibrary ops (it OWNS the wildcard-propagation logic; on a plain CallFunction a forced element type silently reverts to wildcard on reload because nothing re-resolves it. Note the type is never PERSISTED even here - AllocateDefaultPins wipes it back to wildcard on every reconstruct and PostReconstructNode re-derives it purely from what is wired in, so it survives only while the connection does), CallDataTableFunction, CommutativeAssociativeBinaryOperator, and Message for interface calls on an external target. The chosen class is returned as nodeClass. as_message forces the interface Message form."
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
def add_widget_animation(blueprint_id: str, name: str, start_time: float = 0.0,
                         end_time: float = 1.0, display_rate: int = 20) -> dict:
    """Create a UMG WidgetAnimation on a Widget Blueprint.

    Times are SECONDS here and are converted to the MovieScene's tick space for you. That conversion
    is the thing to be careful about when reading results back: a MovieScene stores times as ticks
    (typically 60000/1), so a key at 0.95s is tick 57000 and display frame 19. list_widget_animations
    reports the range in BOTH ticks and seconds so a wrong conversion is visible rather than silent.

    Fails if the name is taken or if endTime is not after startTime, and creates nothing in either
    case. The animation is attached to the blueprint before returning - an animation that exists but
    is not in WidgetBlueprint->Animations would compile fine and simply not be there.
    """
    return _post("add_widget_animation", blueprintId=blueprint_id, name=name,
                 startTime=start_time, endTime=end_time, displayRate=display_rate)


@mcp.tool()
def rename_tree_widget(blueprint_id: str, widget_name: str, new_name: str) -> dict:
    """Rename a widget in a Widget Blueprint's tree, carrying the name through everything.

    The rename is the easy part. A widget's name is also recorded in its property bindings, in every
    animation's AnimationBindings, in the MovieScene POSSESSABLE behind each of those, in its
    navigation bindings, and in every graph node that gets or sets it as a variable. Missing any of
    them fails silently - most sharply the possessable, where the animation still compiles, still
    plays, and animates nothing.

    The response reports bindingsUpdated, animationBindingsUpdated and possessablesRenamed so you can
    see the rename carried through rather than assume it.

    Not done, because both need the asset open in the UMG designer: the designer's preview widget and
    DesiredFocusWidget. Compile afterwards for the generated class to pick the rename up.
    """
    return _post("rename_tree_widget", blueprintId=blueprint_id, widgetName=widget_name,
                 newName=new_name)


@mcp.tool()
def list_widget_animations(blueprint_id: str) -> dict:
    """List a Widget Blueprint's UMG animations, with everything needed to verify one.

    Per animation: name and display label, display rate, tick resolution, the playback range in both
    ticks and seconds, track and possessable counts, and the widget bindings (widget name, guid, and
    whether it is the root widget).
    """
    return _post("list_widget_animations", blueprintId=blueprint_id)


@mcp.tool()
def add_widget_animation_track(blueprint_id: str, animation_name: str, widget_name: str,
                               property: str = "RenderTransform.Translation") -> dict:
    """Bind a widget into a UMG animation and give it a property track.

    Three properties are authorable: RenderTransform.Translation (a 2D transform track),
    RenderOpacity (a float track) and ColorAndOpacity (a colour track). Visibility is deliberately
    absent - it is a bool channel and would be half-working. Anything else is refused by name rather
    than silently ignored. Key the track afterwards with set_widget_animation_keys, passing the SAME
    property.

    Creating the binding and the track are both idempotent: call it twice and the second call reports
    createdBinding/createdTrack false.

    The root widget is refused - the engine binds the preview UUserWidget for that case and there is
    no preview widget outside the designer. Animate a child widget.
    """
    return _post("add_widget_animation_track", blueprintId=blueprint_id,
                 animationName=animation_name, widgetName=widget_name, property=property)


@mcp.tool()
def set_widget_animation_keys(blueprint_id: str, animation_name: str, widget_name: str,
                              channel: str = "Y", keys: list = None,
                              replace: bool = True,
                              property: str = "RenderTransform.Translation") -> dict:
    """Key one channel of a widget's animation track.

    property picks WHICH track to key and must match one you created with
    add_widget_animation_track: RenderTransform.Translation (channel X or Y), ColorAndOpacity
    (channel R, G, B or A), or RenderOpacity (leave channel empty - it is a single float).

    keys is [{"time": seconds, "value": number, "interp": "cubic"|"linear"|"constant"}]. Times are
    SECONDS and are converted to the MovieScene's tick space for you; the response reports each key in
    both units so a bad conversion is visible. "cubic" uses the engine's Auto tangent, which is what
    the UMG designer produces.

    The whole batch is validated before anything is written, so a bad key cannot leave a half-keyed
    curve. replace=True (the default) clears the channel first; pass False to append.
    """
    return _post("set_widget_animation_keys", blueprintId=blueprint_id,
                 animationName=animation_name, widgetName=widget_name, channel=channel,
                 keys=keys or [], replace=replace, property=property)


@mcp.tool()
def set_widget_animation_range(blueprint_id: str, animation_name: str,
                               start_time: float = None, end_time: float = None,
                               display_rate: float = None) -> dict:
    """Change an existing UMG animation's playback range or frame rate IN PLACE.

    Exists so that correcting an animation's length no longer needs remove-and-recreate. That
    sequence used to be the only way, and it crashed the editor: removing an animation left the
    UObject alive holding its name, so recreating it renamed on top of a live object. Both halves of
    that are fixed too, but not needing the dance at all is better.

    Times are in SECONDS; give either or both bounds. displayRate is the editor's frame grid.

    NO KEY MOVES. Key times live in the MovieScene's tick resolution, which is independent of
    displayRate, so a longer range does not stretch the motion and a different frame rate does not
    shift a single key. The response says keysUnchanged for exactly this reason - re-key with
    set_widget_animation_keys if you wanted the motion rescaled.

    Reports previousStartTime/previousEndTime alongside the new values, and reads the range back off
    the MovieScene rather than echoing what you asked for.
    """
    # Written out as explicit keywords rather than built into a dict and splatted. _post already
    # drops None, and a **payload call is invisible to tools/param_reach.py, which scans these call
    # sites statically to catch endpoint parameters no MCP tool can send - splatting reported all five
    # of these as unreachable. Do not blind the checker to save four lines.
    return _post("set_widget_animation_range",
                 blueprintId=blueprint_id, animationName=animation_name,
                 startTime=start_time, endTime=end_time, displayRate=display_rate)


@mcp.tool()
def remove_widget_animation(blueprint_id: str, animation_name: str) -> dict:
    """Remove a UMG animation from a Widget Blueprint.

    No confirm flag: this is an undoable blueprint edit, not an asset deletion. Verified by re-finding
    the animation afterwards rather than trusting the removal.
    """
    return _post("remove_widget_animation", blueprintId=blueprint_id, animationName=animation_name)


@mcp.tool()
def remove_widget_animation_track(blueprint_id: str, animation_name: str, widget_name: str,
                                  property: str = "RenderTransform.Translation",
                                  remove_binding: bool = False) -> dict:
    """Remove one property track from a widget's binding in a UMG animation.

    remove_binding=True also drops the widget's possessable AND its AnimationBindings entry - both
    halves together, since removing one and not the other leaves a binding that animates nothing.
    """
    return _post("remove_widget_animation_track", blueprintId=blueprint_id,
                 animationName=animation_name, widgetName=widget_name, property=property,
                 removeBinding=remove_binding)


@mcp.tool()
def add_reroute(graph_id: str, x: int = 0, y: int = 0,
                src_node: str = "", src_pin: str = "",
                dst_node: str = "", dst_pin: str = "") -> dict:
    """Add a reroute (knot) node - the thing that keeps long wires readable.

    Pass all four of src_node/src_pin/dst_node/dst_pin to SPLICE the reroute into a link that already
    exists: the direct wire is replaced by src -> knot -> dst. Splice twice through the same wire and
    you get a chain. Omit all four to place a bare reroute and wire it yourself with connect_pins.

    Every guard runs before anything is created, and the splice is verified afterwards - a reroute
    that failed to take the wire would otherwise leave the graph disconnected under an ok:true.

    Reroutes were readable but not writable until now: list_nodes hide_knots, and the pin resolution
    that tunnels through knot chains, all handle them.
    """
    return _post("add_reroute", graphId=graph_id, x=x, y=y,
                 srcNode=src_node or None, srcPin=src_pin or None,
                 dstNode=dst_node or None, dstPin=dst_pin or None)


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
def add_cast(graph_id: str, target_class: str, pure: bool = False, x: int = 0, y: int = 0) -> dict:
    "Add a Dynamic Cast node to target_class. Impure by default (exec then / Cast Failed pins); pure=True drops the exec pins and exposes a bool success output instead - use it inside a pure function or when the cast feeds a data pin only."
    return _post("add_cast", graphId=graph_id, targetClass=target_class,
                 pure=pure or None, x=x, y=y)


@mcp.tool()
def set_cast_purity(graph_id: str, node_guid: str, pure: bool) -> dict:
    "Flip an existing Dynamic Cast node between pure and impure, REALLOCATING its pins - impure has execute/then/Cast Failed, pure has none of them and just outputs the cast result. Use this rather than writing bIsPureCast with set_property: that flips the flag without reallocating the exec pins, leaving the flag and the pins disagreeing. This only changes purity - to cast to a DIFFERENT class, place a new node with add_cast."
    return _post("set_cast_purity", graphId=graph_id, nodeGuid=node_guid, pure=pure)


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
    "Create a NEW Blueprint function graph. inputs/outputs are lists of {name, type, container?} - the same type grammar as add_variable, so a reference parameter is type='object:SceneComponent'. Inputs land on the entry node, outputs on the result node; compiles so the function is callable immediately. THIS DOES NOT OVERRIDE. Passing a parent's function name used to create a colliding duplicate that only failed later, at compile, with six errors and nothing in the response pointing at the cause; it is now refused up front with nothingModified:true, conflictsWith, parentFunctionIsOverridable and route:'add_override_event'. To override a parent's event or BlueprintNativeEvent call add_override_event {event, parentClass?, callParent?} - it takes parentClass, this does not. To implement an interface member use implement_interface_function. For a new event rather than a function use add_custom_event."
    return _post("create_function", blueprintId=blueprint_id, name=name,
                 inputs=inputs or [], outputs=outputs or [], pure=pure)


@mcp.tool()
def create_blueprint(path: str, parent_class: str = "Actor", blueprint_type: str = "Normal",
                     skeleton: str = None) -> dict:
    "Create a fresh Blueprint asset. path is a /Game/... object path (e.g. /Game/MifTestbed/BP_Foo); parent_class is a name or class path (default Actor). blueprint_type is Normal (default), FunctionLibrary, Interface, MacroLibrary, WidgetBlueprint or AnimBlueprint - an unrecognised value is refused rather than silently producing a plain Blueprint. Compiles it and returns {blueprintId, class, parentClass, eventGraphId}. Fails if one already exists at path: there is NO overwrite (the parameter used to exist here, was read by no line of the handler, and left callers wondering why the flag did nothing) - delete_asset the old one first. AnimBlueprint REQUIRES skeleton=<USkeleton path>: an Animation Blueprint is a UAnimBlueprint carrying a TargetSkeleton, NOT a plain Blueprint parented to UAnimInstance - that variant gets an EventGraph and no AnimGraph, can never play an animation, and is now rejected with a pointer to this parameter."
    return _post("create_blueprint", path=path, parentClass=parent_class, blueprintType=blueprint_type,
                 skeleton=skeleton)


@mcp.tool()
def reparent_blueprint(blueprint_id: str, new_parent_class: str) -> dict:
    "Reparent an existing Blueprint onto a different parent class and recompile. blueprint_id names the Blueprint being REPARENTED; new_parent_class is the class it will now inherit from (a name or a class path). Reparenting is destructive to anything the old parent supplied: nodes calling functions/variables that only existed on the previous parent break at compile, and inherited components from the old hierarchy go away - read the compile result rather than assuming it succeeded cleanly."
    return _post("reparent_blueprint", blueprintId=blueprint_id, newParentClass=new_parent_class)


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
def connect_pins(src_node: str, src_pin: str, dst_node: str, dst_pin: str,
                 graph_id: str = "") -> dict:
    "Wire src_node.src_pin (output) to dst_node.dst_pin (input). Fires the connection notification so wildcards resolve. Returns the schema's reason string if disallowed. graph_id is '<blueprintPath>::<graphName>', exactly as open_blueprint / list_graphs / list_nodes return it, and it SCOPES node resolution: with it both guids are looked up in that one graph's node list, without it they go through a global scan that cannot disambiguate a second loaded copy of the same blueprint carrying identical NodeGuids. Optional and omitted from the request when blank, so the default behaviour is the global scan this tool has always done. (The endpoint also accepts a 'path' key for back-compat - accepted and IGNORED, never a graph selector - which is why it is deliberately NOT exposed here: graph_id is the parameter that actually chooses the graph.)"
    return _post("connect_pins", srcNode=src_node, srcPin=src_pin, dstNode=dst_node, dstPin=dst_pin,
                 graphId=graph_id or None)


@mcp.tool()
def disconnect_pin(node: str, pin: str, graph_id: str = "") -> dict:
    "Break all links on a pin. graph_id is '<blueprintPath>::<graphName>' from open_blueprint / list_graphs / list_nodes; it scopes the node guid lookup to that graph instead of the global scan, which is the only way to disambiguate two loaded copies of a blueprint sharing NodeGuids. Optional, omitted when blank. ('path' is accepted by the endpoint for back-compat and IGNORED - not exposed here; graph_id is the real selector.)"
    return _post("disconnect_pin", node=node, pin=pin, graphId=graph_id or None)


@mcp.tool()
def reconnect_pin(src_node: str, src_pin: str, dst_node: str, dst_pin: str,
                  graph_id: str = "") -> dict:
    "Break both pins then reconnect them — the wildcard-reset combo when a type is stuck. graph_id is '<blueprintPath>::<graphName>' from open_blueprint / list_graphs / list_nodes; it scopes both node guid lookups to that graph instead of the global scan. Optional, omitted when blank. ('path' is accepted by the endpoint for back-compat and IGNORED - not exposed here; graph_id is the real selector.)"
    return _post("reconnect_pin", srcNode=src_node, srcPin=src_pin, dstNode=dst_node, dstPin=dst_pin,
                 graphId=graph_id or None)


@mcp.tool()
def set_pin_default(node: str, pin: str, value: str) -> dict:
    "Set a literal default value on an input pin (schema-formatted)."
    return _post("set_pin_default", node=node, pin=pin, value=value)


@mcp.tool()
def splice_into_exec(after_node: str, insert_node: str, after_pin: str = "then",
                     insert_exec_in: str = "execute", insert_exec_out: str = "then",
                     graph_id: str = "") -> dict:
    "Atomically insert a node into an exec chain: after_node.after_pin -> insert_node, and insert_node -> the old downstream target(s). graph_id scopes both node guid lookups to one graph."
    return _post("splice_into_exec", afterNode=after_node, insertNode=insert_node,
                 afterPin=after_pin, insertExecIn=insert_exec_in, insertExecOut=insert_exec_out,
                 graphId=graph_id or None)


@mcp.tool()
def apply_graph_patch(graph_id: str, operations: list, dry_run: bool = False,
                      stop_on_first_error: bool = True, allow_partial: bool = False) -> dict:
    """Apply MANY dependent graph edits in ONE call, with real rollback.

    Wiring a graph is dominated by connections, not node creation - a single driver graph can be 17
    exec links, 20+ data links and a handful of pin defaults. One call each is slow, and a failure
    partway through leaves the blueprint half-wired with no record of what landed. `batch` does not
    help: it stops at the first failure with every prior op already committed.

    operations[] entries (node = a NodeGuid inside graph_id):
        {"op": "connect_pins",    "srcNode": guid, "srcPin": name, "dstNode": guid, "dstPin": name,
                                  "existingLinkPolicy": "replace"|"preserve"|"reject"}
            existingLinkPolicy decides what happens when the DESTINATION INPUT is already fed by
            something else. Default "replace": the incumbent link is removed so the new source is
            the only one. This is not the same as letting the schema decide - a `self` pin on an
            impure, no-return function is a legal MULTI-TARGET pin, so the engine APPENDS there and
            REPLACES on an otherwise identical pin whose function returns a value. Left to the
            schema, whether a rewire replaces or double-links depends on the callee's signature.
            "preserve" keeps the incumbent (opt in to multi-target); "reject" refuses and names it.
            Exec inputs are never touched by this policy - exec fan-in is legal.
            Each connect result reports sourcesBefore/sourcesAfter, replacedExisting and
            appendedToExisting, and a "replace" that failed to clear the pin is reported FAILED.
        {"op": "disconnect_pin",  "node": guid, "pin": name, "direction": "input"|"output"}
            direction is only needed when one name matches both an input and an output pin;
            leave it off and an ambiguous name is refused rather than guessed at.
        {"op": "set_pin_default", "node": guid, "pin": name, "value": "..."}

    Every operation is resolved and schema-checked BEFORE anything is mutated, so a bad guid or an
    illegal connection is refused with the graph untouched. If an operation still fails during apply,
    the ones already applied are undone by replaying their inverses in reverse order - real rollback,
    not a cancelled transaction (which reverts nothing). Pass allow_partial=True to keep partial work.

    dry_run resolves and validates everything and mutates nothing - use it to check a large patch.
    Node creation is NOT a patch op: create nodes with the add_* tools first, then wire them here.
    """
    return _post("apply_graph_patch", graphId=graph_id, operations=operations, dryRun=dry_run,
                 stopOnFirstError=stop_on_first_error, allowPartial=allow_partial)


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
def set_pin_type(node: str, pin: str, type: str, container: str = "", value_type: str = "",
                 graph_id: str = "") -> dict:
    "Force a pin's type. type supports scalars (float is 32-bit, double/real 64-bit), struct/class names, and prefixes class:X / object:X / softobject:X / softclass:X / interface:X / enum:X. container = array|set|map; for a map, type is the KEY type and value_type is the VALUE type. graph_id is '<blueprintPath>::<graphName>' from open_blueprint / list_graphs / list_nodes; it scopes the node guid lookup to that graph instead of the global scan, which is the only way to disambiguate two loaded copies of a blueprint sharing NodeGuids. Optional, omitted when blank. REFUSES BEFORE MUTATING on an array-function node's target array pin with nothing connected: that node re-derives every pin type from what is wired into it and wipes the pin back to wildcard on load, on reconstruct and during cook - the forced type would not even survive this call - so the response is nothingModified:true with route:'connect_pins'. Wire a typed array to the array pin and the wildcards resolve from it."
    return _post("set_pin_type", node=node, pin=pin, type=type, container=container or None,
                 valueType=value_type or None, graphId=graph_id or None)


# --------------------------------------------------------------------------
# Phase 3 breadth — event dispatchers (multicast delegates)
# --------------------------------------------------------------------------

@mcp.tool()
def add_event_dispatcher(blueprint_id: str, name: str, inputs: list = None) -> dict:
    "Create a Blueprint event dispatcher (multicast delegate) with optional params [{name,type,container?}]. Compiles so it becomes callable/bindable."
    return _post("add_event_dispatcher", blueprintId=blueprint_id, name=name, inputs=inputs or [])


@mcp.tool()
def add_call_dispatcher(graph_id: str, dispatcher: str, target_class: str = "",
                        x: int = 0, y: int = 0) -> dict:
    """Add a Call node for an event dispatcher (broadcasts it). Must already exist + be compiled.

    target_class broadcasts a dispatcher declared on ANOTHER class instead of this Blueprint's own.
    The node then shows a Target pin: place it, then connect_pins the object reference into Target.
    target_class names the CLASS that declares the dispatcher - never the object.
    """
    return _post("add_call_dispatcher", graphId=graph_id, dispatcher=dispatcher,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_bind_dispatcher(graph_id: str, dispatcher: str, target_class: str = "",
                        x: int = 0, y: int = 0) -> dict:
    """Add a Bind (Add) node for an event dispatcher.

    target_class binds a dispatcher declared on ANOTHER class - the equivalent of dragging off an
    object reference in the editor and picking "Bind Event to X". Without it the dispatcher must be
    declared on the Blueprint being edited, and the call fails with
    "event dispatcher 'X' not found on SKEL_<ThisBlueprint>_C".

    target_class names the CLASS that declares the dispatcher, never the object. Wire the object
    itself into the node's Target pin afterwards with connect_pins - e.g. from a Cast To
    DDS2_GameMode node's "As DDS2 Game Mode" output.

    Full external-bind sequence:
        1. add_bind_dispatcher(graph_id, "PlayerLoggedChanged", target_class="DDS2_GameMode")
        2. connect_pins(cast_node, "AsDDS2GameMode", bind_node, "self")   # the Target pin
        3. add_custom_event(...) with the delegate's signature, then connect_pins its
           OutputDelegate into the bind node's Delegate pin
        4. connect_pins the cast's exec into the bind node's exec
    Use describe_class(target_class) to read the delegate's parameter list for step 3.
    """
    return _post("add_bind_dispatcher", graphId=graph_id, dispatcher=dispatcher,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_component_bound_event(blueprint_id: str, component: str, dispatcher: str,
                              event: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a component-bound event node - the red event node you get from a component's Details panel, e.g. OnComponentBeginOverlap on a named collision component. `component` is the component's name on this Blueprint and `dispatcher` is the delegate declared on that component's type; the delegate's owner class is resolved automatically from the component, so no target class is needed. Optional `event` names the generated event node. This ALWAYS lands in the Blueprint's event graph, so pass blueprint_id - there is no graph_id. For a delegate that is NOT declared on a component (a custom event dispatcher, or one on the Blueprint itself) use add_bind_dispatcher instead."
    return _post("add_component_bound_event", blueprintId=blueprint_id, component=component,
                 dispatcher=dispatcher, event=event or None, x=x, y=y)


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
def list_components(blueprint_id: str, component: str = "", include_inherited: bool = True,
                    include_native: bool = True, limit: int = 500) -> dict:
    "List EVERY component reachable from a Blueprint, from all three origins, each row tagged with origin: 'ownSCS' (this Blueprint's own SimpleConstructionScript), 'parentBlueprintSCS' (inherited from a parent BLUEPRINT's SCS, anywhere up the chain) and 'native' (a C++ component on the parent class chain, read off the CDO). It used to walk the child's own SCS only, which is why get_inherited_component - a verb that resolves ONE component BY NAME - had no companion that could tell you the names. Every row carries owningClass, the endpoint to call next (route/endpoint) and a hint. templatePath means one thing everywhere: the objectPath to pass to set_property to change that component's defaults FOR THIS BLUEPRINT. For a NATIVE component that is the child CDO's own subobject, and the subobject name is NOT the property name (Mesh -> CharacterMesh0, CharacterMovement -> CharMoveComp, CapsuleComponent -> CollisionCylinder) - subobjectName carries it, resolved from the object rather than guessed. For an INHERITED component templatePath is present only once an override exists (overrideTemplatePath); until then it is deliberately absent, because the only other template is the PARENT asset's and writing there would change every other child - parentTemplatePath shows it read-only and route says override_inherited_component. Also reports canOverride (exactly what override_inherited_component will accept), editableWhenInherited (the extra editor-side fact), and the ownSCSCount / parentBlueprintSCSCount / nativeCount split. include_inherited and include_native default TRUE and exist only so a caller can ask for the old own-SCS-only shape back. NAME LOOKUP: pass component to ask ONE question - 'does this name exist here, and what can I do with it' - and get it answered in a form that cannot be confused with 'it exists but is not overridable'. canOverride:false is NEVER the discriminator (it is legitimately false for an own-SCS component, for a native one, and for an inherited one whose key or class check fails); exists and origin are. A known name answers requestedComponent + exists:true + origin + owningClass + componentClass + canOverride (+ canOverrideReason when false) + route at the TOP level, with that one row in components[]. An unknown name answers exists:false + origin:'notFound' + route:'none' + availableComponents[] (the names that DO exist, capped at 80) - and ok stays true, because the question was asked and answered. Names are matched as FNames (case-insensitive) and are the Details-panel variable names, not the subobject names. The origin filters are IGNORED for a named lookup, so include_native=False cannot turn a native component into 'no such component'. COOKED / CLASS TARGETS: targetKind is 'blueprint' or 'cookedClass' and is the ONLY discriminator - a generated-class path ('/Game/A/BP_Foo.BP_Foo_C') of an UNCOOKED blueprint resolves through UClass::ClassGeneratedBy to its editable UBlueprint and answers targetKind:'blueprint' with every route live. editableBlueprintExists says whether an editable UBlueprint backs the target and editableBlueprintPath names it - retarget writes at that path rather than minting a duplicate with create_editable_child. Only targetKind:'cookedClass' with editableBlueprintExists:false is genuinely cooked and read-only; there readOnly:true, every row reports canOverride:false with a reason and route:'create_editable_child', cookedClassPath names the class, targetNote explains it, nativeEnumerated:false means the native pass could not run (no constructed CDO, and one is deliberately not created for a read) so nativeCount:0 means 'not looked at' rather than 'none', and classGeneratedByPath appears when the generator exists but is not a UBlueprint. On a cooked target templatePath is ABSENT by design and cookedTemplatePath carries the archetype as a READ-ONLY reference - it must NOT be passed to set_property, because that package is pak-mounted and the write cannot be saved back."
    return _post("list_components", blueprintId=blueprint_id, component=component or None,
                 includeInherited=include_inherited, includeNative=include_native, limit=limit)


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


@mcp.tool()
def remove_event_dispatcher(blueprint_id: str, name: str, confirm: bool = False) -> dict:
    "Delete an event dispatcher. A dispatcher is BOTH a signature graph and a backing delegate variable - this removes both, and refuses rather than leaving half of one behind. Reports orphanedNodeCount: call/bind nodes that referenced it survive and will fail the next compile. Requires confirm=True."
    return _post("remove_event_dispatcher", blueprintId=blueprint_id, name=name, confirm=confirm)


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
    "Read any UObject property by dot-path (e.g. Font.Size). Target is either object_path, or blueprint_id + widget_name for a widget template. THE RESPONSE CARRIES TWO VALUES AND YOU ALMOST ALWAYS WANT THE SECOND. 'value' is the engine's lossless UE export text - round-trip-safe, exact for int64, and it keeps the NSLOCTEXT(...) form of an FText - which means a bool arrives as the STRING 'True'/'False' and an array as one '(\"A\",\"B\")' blob. Those strings are truthy in every scripting language, so an 'is this flag set' test written against 'value' silently passes for both values; that is exactly how a 63-blueprint audit read every disabled flag as enabled. 'typed' is the SAME value as real JSON: bools as booleans, numbers as numbers, arrays/sets as lists, maps/structs as objects (keyed by the reflected member name), enums as the entry NAME string, object refs as path strings (null only when the engine itself says None). Use 'typed' for any test, arithmetic or filter. It is also the shape set_property's value accepts, so get_property.typed -> set_property.value is a closed loop. Two lossy edges, and only two: FText comes back as the display string (the localisation form survives only in 'value'), and an int64 past 2^53 loses precision as a JSON number (again exact in 'value'). property_path takes element accessors - OverrideMaterials[1], FloatCurves[1].Keys[0].Value, ScalarParameterValues[ParameterInfo.Name=Roughness].ParameterValue, SomeMap{Alpha} - and the response says isElement, with elementPath/elementIndex, plus elementOrdering when the index is a POSITION IN ITERATION ORDER (a set or map), which is not stable across a rehash. Read-only."
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
    "Dump an object's top-level properties with type and current value. Each row carries 'value' (UE export text, round-trip-safe, so a bool arrives as the STRING 'True' and a C-array UPROPERTY shows only element 0) and 'typed' (the same value as real JSON: bool/number/array/object, enums as the entry name, object refs as path strings, and a C-array complete) - use 'typed' for any test or arithmetic, and see get_property for the two lossy edges (FText display string, int64 past 2^53). It comes from the SAME emitter get_property and set_property use, so a row's 'typed' is the shape set_property's value accepts: list_object_properties row.typed -> set_property.value is the same closed loop as get_property.typed -> set_property.value. 'typed' is OMITTED, with typedOmitted:true on that row, whenever the row's value exceeded max_value_chars: the typed JSON of a curve or volumetric-cloud struct is no smaller than its export text and usually larger, so emitting it would reproduce the empty-response failure the caps exist to prevent. The response carries typedSupported:true, so a missing 'typed' is never confused with an older build - and a row from a build that has the field always has either 'typed' or typedOmitted:true, never neither. Filter with name_contains; limit caps the returned rows (matched reports the true total, truncated flags the cap). max_value_chars clips long values and sets valueClipped - large Blueprint actors have struct/curve properties tens of KB each, so an unbounded dump returns nothing; raise it (or use get_property, which is unclipped by construction) to get 'typed' for a large struct."
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
                               include_transient: bool = False, deep: bool = True,
                               recursive: bool = False) -> dict:
    "What does this object actually OVERRIDE versus its archetype - the question the Details panel answers with a yellow arrow, and the single most useful read for auditing a Blueprint, a placed actor or a CDO. Computed the way the panel computes it: the archetype with a UClass->CDO hop first, FProperty::Identical with PPF_DeepComparison on instanced-object properties (set deep=False to skip that, and the response says it was skipped), and an ArrayDim loop for C-arrays. Each differing row carries name, path, value, defaultValue, defaultSource ('archetype' or 'constructed' - a property a child Blueprint added does not exist on the archetype at all, and the fallback is stated rather than hidden), the authored specifier, persistence and resettable. Transients are skipped by default because they always differ and drown the signal; include_transient=True keeps them. recursive (alias include_children) defaults to FALSE, which is the top-level-only walk this tool has always done; set it True and a differing STRUCT is OPENED rather than reported, so you get a 'Settings.BloomIntensity' row - a path you can hand straight to reset_property_to_default or set_property - instead of one 'Settings' row whose value is a 4 KB struct literal. Recursion is deliberately narrow: only into an FStructProperty (where one member offset addresses both sides), never into a TArray/TSet/TMap element, never into a C-array member, never through an object pointer - those stay leaves and are compared whole. A struct that compares non-identical but whose members all match (a custom Identical op) falls back to reporting the struct itself. 'path' is the dotted path from the top-level property and is present on every row, recursive or not. The response echoes recursive, reports expanded (structs opened instead of reported) and, when recursive, maxDepth (the depth CAP the walk enforces, currently 4 - not the depth reached). The checkable invariant countsConsistent is now FOUR terms: inspected == differing + matching + skippedTransient + expanded. expanded is always 0 when recursive is off, so it reduces to the old three-term form for every caller that does not ask for it. A walk that hits the 20000-node budget warns and says so: inspected and matching then UNDER-report and an override past that point is not in the response - narrow it with name_contains or turn recursive off. An object whose archetype is itself (the root CDO) is a stated RESULT with differing:0, not an error. Read-only."
    return _post("diff_properties_vs_default", objectPath=object_path or None,
                 blueprintId=blueprint_id or None, widgetName=widget_name or None,
                 nameContains=name_contains or None, limit=limit, maxValueChars=max_value_chars,
                 includeTransient=include_transient, deep=deep, recursive=recursive or None)


@mcp.tool()
def reset_property_to_default(object_path: str = "", property_path: str = "",
                              force: bool = False, override_flag: str = "") -> dict:
    "The Details panel's yellow arrow: put a property back to its archetype default. Reports valueBefore / defaultValue / valueAfter / differedFromDefault / changed / defaultSource / archetype, and ASSERTS the invariant - after a successful reset valueAfter must equal defaultValue byte-for-byte under the same exporter, or the call fails. A property that already equals its default is reported (changed:false), not failed. Applies the two refusals the panel applies and a naive reset does not: CPF_Config properties have NO reset arrow (their value comes from an .ini, not the archetype) and CPF_EditFixedSize containers have none either. force=True waives exactly ONE refusal: CPF_EditConst (the panel greys the row). It has NO effect on a closed meta EditCondition - that is now override_flag's job, so the two meanings are not overloaded onto one boolean. override_flag takes set|refuse|ignore and DEFAULTS TO 'ignore', which is the pre-existing behaviour: the reset proceeds, and the closed gate is reported (overrideFlagUnmet:true plus a warning) rather than silently tolerated. Note the default differs deliberately from set_property's, whose default is 'set'. override_flag='refuse' is the strict path - it fails with nothingModified:true instead of writing behind a closed gate. override_flag='set' is REFUSED on this endpoint by design: writing the companion flag during a RESET would turn a feature ON, which is the opposite of resetting; reset the flag itself with a second call. editCondition / editConditionKind / editConditionMet / editConditionFlag are reported either way. A separate refusal is archetypeShapeMismatch:true with nothingModified:true, when the path resolves on the object and on the archetype to properties of different FField class, ArrayDim or ElementSize - typically a class reinstanced after a live C++/Blueprint change while a stale archetype is still referenced, or a child redeclaring an inherited name with a different type; recompile/reopen the asset, or write the value explicitly with set_property, which never touches the archetype's memory. When the archetype does not carry the property at all - a variable a child Blueprint added - it falls back to a FRESHLY CONSTRUCTED default and says defaultSource:'constructed'. PM-003 safe: the default text is parsed into a scratch buffer before the notification bracket is opened, so a failed reset never touches the live value and never leaves a dangling component re-registration. Element accessors work. Transacted, so Ctrl-Z undoes it. Refuses the widget-template form (use set_property) and refuses a cooked package."
    return _post("reset_property_to_default", objectPath=object_path or None,
                 propertyPath=property_path, force=force or None,
                 overrideFlag=override_flag or None)


@mcp.tool()
def edit_container(object_path: str = "", property_path: str = "", operation: str = "",
                   index: int = None, count: int = None, key: str = "", new_key: str = "",
                   value: Any = None, swap_with: int = None, new_size: int = None,
                   override_flag: str = "") -> dict:
    "The element LIFECYCLE inside a TArray/TSet/TMap - the +, x, insert and clear buttons the Details panel has and set_property does not: operation = add | insert | remove | clear | swap | resize | setKey. (The verb is 'operation', not 'op': 'op' is batch's routing key and is tolerated centrally, so an endpoint using it would be un-diagnosable inside batch.) Element VALUES stay in set_property - address them with the new accessors, e.g. OverrideMaterials[1] or SomeMap{Alpha}. Guards, all applied BEFORE the first mutation because a cancelled transaction reverts nothing: index range checked against the real length and named in the error; CPF_EditFixedSize refuses every size-changing op and names the flag (the panel hides its add/remove buttons for the same reason); a map/set element type with no GetTypeHash is refused BY NAME rather than crashed on; a duplicate map key is REFUSED because FScriptMapHelper::AddPair overwrites silently, which would turn 'add' into 'replace' with no notice, and a duplicate set element likewise (the panel refuses both); every element value is parsed into a scratch buffer first (PM-003); the helper is re-resolved after any structural op, because AddValues/InsertValues reallocate; and the map/set is rehashed after any key or element change, or Find stops seeing entries the container still holds. Reports elementsBefore / elementsAfter / index / rehashed / changed, and treats a structural op that left the count unchanged as a FAILURE rather than a success. Transacted. Refuses the widget-template form and refuses a cooked package. THE OTHER GATE, and the reason an edit here can succeed and still do nothing: when the container's UPROPERTY meta EditCondition is not met the panel greys the container AND its +/x buttons together, and the engine branches on the companion FLAG rather than on the container - so an element you appended is in memory and is read by nothing. override_flag answers that gate and is spelled exactly as set_property spells it: set | refuse | ignore. It DEFAULTS TO 'ignore' where set_property defaults to 'set', deliberately - edit_container has always performed the operation so today's behaviour stays the default and every new behaviour is opt-in, and 'append one element to this array' is not consent to enable the feature that owns the array. 'ignore' performs the operation and is no longer SILENT: editConditionKind / editConditionMet / editConditionFlag are always reported and a closed gate always raises a warning saying the engine will not read the container until the flag is set. 'set' writes the companion flag in the SAME transaction and the same Modify/PreEditChange..PostEditChange bracket, only AFTER the operation has actually mutated (so a refusal on index range, duplicate key or value parse leaves no flag behind), and reports overrideFlagWritten; if the flag cannot be resolved it says overrideFlagUnmet:true rather than silently downgrading to 'ignore'. 'refuse' fails naming the flag and the value it needs, with nothingModified:true. Any other word is refused outright - a string-to-enum dispatch never has a silent default. Omitted from the request when blank, so the wire payload for existing callers is unchanged."
    return _post("edit_container", objectPath=object_path or None, propertyPath=property_path,
                 operation=operation, index=index, count=count, key=key or None,
                 newKey=new_key or None, value=value, swapWith=swap_with, newSize=new_size,
                 overrideFlag=override_flag or None)


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
def list_tree_widgets(blueprint_id: str) -> dict:
    "Dump the ENTIRE WidgetTree of a widget blueprint: name, class, parent, child index, slot class, is-variable flag, is-panel, child count. Read-only. This is what makes the tree addressable - every other tree endpoint takes a widget_name and there was previously no way to discover them. slot_class is the useful field: it tells you which layout properties exist at all (a UCanvasPanelSlot takes x/y, a UVerticalBoxSlot does not, which is exactly why add_tree_widget rejects x/y on a box parent)."
    return _post("list_tree_widgets", blueprintId=blueprint_id)


@mcp.tool()
def duplicate_tree_widget(blueprint_id: str, widget_name: str, parent_name: str = None,
                          index: int = None) -> dict:
    "Clone a widget AND its whole subtree, preserving every property value, by riding the engine's own copy/paste text path (ExportWidgetsToText/ImportWidgetsFromText). parent_name defaults to the source's own parent - 'duplicate beside the original', matching the Designer's Duplicate. The clone's name is assigned by the paste path to stay unique; rename afterwards if you need a specific one. Compile to apply."
    return _post("duplicate_tree_widget", blueprintId=blueprint_id, widgetName=widget_name,
                 parentName=parent_name, index=index)


@mcp.tool()
def wrap_tree_widget(blueprint_id: str, widget_name: str, wrapper_class: str,
                     wrapper_name: str = None) -> dict:
    "The Designer's 'Wrap With': insert a new panel where the widget currently sits, then move the widget inside it. wrapper_class must be a UPanelWidget (CanvasPanel, VerticalBox, HorizontalBox, Overlay, SizeBox, Border...). Sibling order is preserved - the wrapper takes the original's exact child index. Handles the ROOT case, which has no parent slot to inherit. Compile to apply."
    return _post("wrap_tree_widget", blueprintId=blueprint_id, widgetName=widget_name,
                 wrapperClass=wrapper_class, wrapperName=wrapper_name)


@mcp.tool()
def move_tree_widget(blueprint_id: str, widget_name: str, parent_name: str = None,
                     as_root: bool = False, index: int = None,
                     replace_root: bool = False) -> dict:
    "Reparent an EXISTING widget. add_tree_widget creates and remove_tree_widget deletes; without this, rearranging meant delete + recreate, losing every property already set on the widget. Pass parent_name or as_root. Refuses to move a panel into itself or its own descendant (that builds a cycle and the next tree walk never returns). Changes parentage ONLY - set slot layout afterwards with set_property on the widget's Slot. as_root DISPLACES any existing root: the old root and its whole subtree drop out of the hierarchy and stop rendering, so it requires replace_root=True and reports displaced_subtree_size. Compile to apply."
    return _post("move_tree_widget", blueprintId=blueprint_id, widgetName=widget_name,
                 parentName=parent_name, asRoot=as_root, index=index, replaceRoot=replace_root)


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
                      snap_to_ground: bool = False, ground_offset: float = 0.0,
                      skip_post_edit_change: bool = False) -> dict:
    "Author a spline's points - THIS IS WHAT MAKES NPCs WALK. The game routes wandering NPCs along BP_SegmentedPathTaskMarker, whose PathSpline is a USplineComponent. points is [{x,y,z},...] (min 2). point_type: curve|linear|constant|curveClamped|curveCustomTangent. snap_to_ground traces each point down onto the terrain, since a route authored at a flat Z floats or buries itself on uneven ground. Every point is validated BEFORE the existing spline is cleared: a component that is not a number fails the call naming points[N].<field>, rather than silently becoming 0 and bending the route through the origin. skip_post_edit_change=True is REQUIRED on any blueprint whose construction script rebuilds its own spline - BP_CarRoadSpline, BP_SplineSidewalk, BP_QuestNPCWalkPath and BP_SegmentedPathTaskMarker all do. Without it PostEditChange re-runs the construction script, which THROWS AWAY the points just written: the call still returns ok with the right pointCount and a read-back returns 2."
    return _post("set_spline_points", actorPath=actor_path, points=points, component=component,
                 space=space, pointType=point_type, closedLoop=closed_loop,
                 snapToGround=snap_to_ground, groundOffset=ground_offset,
                 skipPostEditChange=skip_post_edit_change)


@mcp.tool()
def get_spline_points(actor_path: str, component: str = None, space: str = "world") -> dict:
    "Read a spline's points, length and closed-loop flag. Read-only; use to verify a patrol route."
    return _post("get_spline_points", actorPath=actor_path, component=component, space=space)


@mcp.tool()
def snap_actors_to_ground(actor_paths: list = None, folder: str = None, label_contains: str = None,
                          all: bool = False, offset: float = 0.0, align_to_normal: bool = False,
                          trace_height: float = 100000.0, ground_actor: str = None,
                          allow_any_hit: bool = False) -> dict:
    "Drop actors onto the terrain, one trace each, with the actor ITSELF excluded from the trace. Doing this from outside is both slow (one HTTP round-trip per actor) and wrong - a trace at a building's own XY hits its roof and 'snaps' it onto itself, climbing every call. Places the BOTTOM of each actor's bounds on the hit, so pivots that are not at the base still sit correctly. Landscapes are skipped. Requires a selector (actor_paths / folder / label_contains / all) - it refuses to guess. By DEFAULT only a Landscape counts as ground, so furniture dropped onto a floor, table or counter finds nothing: pass ground_actor='<label, name or path of the surface>' to nominate that actor as the ground, or allow_any_hit=True to accept the first thing hit."
    return _post("snap_actors_to_ground", actorPaths=actor_paths, folder=folder,
                 labelContains=label_contains, all=all, offset=offset,
                 alignToNormal=align_to_normal, traceHeight=trace_height,
                 groundActor=ground_actor, allowAnyHit=allow_any_hit)


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
def set_material_parameter(material: str, scalars: dict = None, vectors: dict = None,
                           textures: dict = None, switches: dict = None,
                           association: str = "global", index: int = -1) -> dict:
    "Set parameters on an existing MaterialInstanceConstant. scalars is {name: number}, vectors is {name: {r,g,b,a}} (also accepts {x,y,z,w} or [r,g,b,a]). Reports unknownParameters for names the PARENT material does not expose, rather than silently accepting a name that will never do anything - and if NONE of the names exist, or you pass neither scalars nor vectors, the call ERRORS instead of returning ok:true/applied:0. TWO DIFFERENT FAILURE MODES, do not confuse them: an UNKNOWN name is reported in unknownParameters[] and is not fatal on its own, but a MALFORMED value (a scalars entry that is not a number, a vectors entry that is not a colour) aborts the WHOLE call before any write - it used to skip that entry and apply the rest, so {\"Tiling\":4,\"Comment\":\"x\"} that returned ok:true/applied:1 now returns an error with zero writes. Unknown keys are rejected by name (the HTTP endpoint also takes a singular {parameter, value} pair; through this tool use the maps). TEXTURES are {name: \"/Game/path/T_Foo.T_Foo\"} and STATIC SWITCHES are {name: true|false}. A static switch changes the shader PERMUTATION, so UpdateStaticPermutation is run for you - without it the value reads back correctly and the material renders unchanged, which is the most convincing kind of silent failure. association (global|layer|blend) plus index address a LAYER parameter; list_material_parameters reports both for every parameter, and a layer parameter addressed as a global is simply not found."
    return _post("set_material_parameter", material=material, scalars=scalars, vectors=vectors,
                 textures=textures, switches=switches, association=association, index=index)


@mcp.tool()
def add_foliage_instances(instances: list, mesh: str = "", foliage_type: str = "",
                          label: str = "Foliage", folder: str = "") -> dict:
    """Place N instanced transforms in one call instead of N separate actors.

    Pass EITHER mesh OR foliage_type - they build different things, and the response says which via
    the mode field.

    foliage_type places into the level's real AInstancedFoliageActor, the same one Foliage edit mode
    paints into, so the instances inherit that type's cull distance, density, scaling and wind. This
    is the one to use when adding to a level that already has foliage, because it will match rather
    than merely resemble. Find the available types with
    find_assets(class="FoliageType_InstancedStaticMesh"); note they may live outside /Game/. Pass the
    FoliageType asset, not the static mesh it wraps. label and folder do not apply - there is no
    holder actor - and the response says so if you send them.

    mesh builds a standalone actor with a HierarchicalInstancedStaticMeshComponent. Still one draw
    setup and one outliner row instead of 90, but it is NOT in the Foliage system: it will not appear
    in Foliage edit mode and inherits none of a FoliageType's settings.

    instances is a list of {x,y,z,yaw?,scale?}. A transform component that is not a number fails the
    WHOLE call, naming instances[N].<field>, with nothing created - the array is parsed before
    anything is spawned, because the transaction cancel this used to rely on does not roll a spawn
    back (PM-007).

    In foliage_type mode the response reports requested alongside instanceCount, so a placement the
    foliage type itself rejected is visible rather than silently absorbed.
    """
    return _post("add_foliage_instances", instances=instances,
                 mesh=mesh or None, foliageType=foliage_type or None,
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
def trace(start: dict, end: dict = None, direction: dict = None, distance: float = 10000.0,
          shape: str = "line", radius: float = 50.0, half_extent: dict = None,
          half_height: float = 100.0, channel: str = "worldStatic", trace_complex: bool = True,
          multi: bool = False, ignore_actors: list = None, draw: bool = False,
          draw_duration: float = 5.0) -> dict:
    """Trace a ray or sweep a shape through the world.

    trace_ground only fires straight down and takes the first GROUND hit. This answers everything
    else: is there a wall between these two points, what is along this direction, does this doorway
    fit a capsule of this size.

    Give start plus either end, or direction + distance. shape may be line (default), sphere, box or
    capsule - the non-line shapes SWEEP, which is how you ask whether something fits. channel is one
    of worldStatic, worldDynamic, visibility, camera, pawn, physicsBody.

    ignore_actors names actors to exclude. An entry that does not resolve is REFUSED rather than
    skipped, because a trace that silently ignores nothing can return a confident hit against the
    very actor you excluded.

    draw=True leaves the ray in the viewport for draw_duration seconds. The response reports which
    world was traced and whether PIE is running.
    

    COMPONENTS are reachable through object_path, which is not obvious and is the single most
    useful thing to know about this endpoint. Call list_components, take the component's
    templatePath (the ..._GEN_VARIABLE path) and pass it as object_path. That is how you set an
    AudioComponent's Sound, a CharacterMovement's MaxWalkSpeed or JumpZVelocity, a light's
    Intensity, or a mesh's BodyInstance.bSimulatePhysics - there is no separate
    set_component_property because there does not need to be.

    property_path may be NESTED: "BodyInstance.MassScale" and "BodyInstance.bEnableGravity" both
    work.
    """
    return _post("trace", start=start, end=end, direction=direction, distance=distance,
                 shape=shape, radius=radius, halfExtent=half_extent, halfHeight=half_height,
                 channel=channel, traceComplex=trace_complex, multi=multi,
                 ignoreActors=ignore_actors or [], draw=draw, drawDuration=draw_duration)


@mcp.tool()
def capture_viewport(path: str = "") -> dict:
    """Capture the pixels the editor is ACTUALLY drawing right now.

    Different question from capture_camera, which spawns its own transient scene-capture actor with
    its own show flags and view mode. This is the real viewport: the user's camera, the current view
    mode (wireframe stays wireframe), the real show flags. For "does my change look right", this is
    the one you want.

    Synchronous - it reads the viewport backbuffer rather than queuing a screenshot request, so the
    file exists when the call returns.

    Reports realtime and, if every pixel is black, says so explicitly: a minimised or occluded editor
    never draws a frame, and a black PNG would otherwise look like a picture of an empty scene.
    """
    return _post("capture_viewport", path=path)


@mcp.tool()
def audition_sound(path: str = "", stop: bool = False) -> dict:
    """Play a sound through the editor's preview device, or stop the current preview.

    Accepts any USoundBase - SoundWave, SoundCue or MetaSoundSource. With 3771 SoundWaves in the game
    and no way to hear one, picking audio for a mod was guesswork by filename.

    This is a 2D editor preview, audible at the machine running the editor. For a positioned world
    sound use add_function_call with PlaySoundAtLocation instead.
    """
    return _post("audition_sound", path=path, stop=stop)


@mcp.tool()
def nav_project_point(point: dict, extent: dict = None) -> dict:
    """Project a point onto the nav mesh: is this spot walkable, and how far off was it?

    Reports movedBy - the distance from the point you asked about to the nearest navigable one.
    A placement 2cm off the mesh and one 300cm off are different problems, and onNavMesh:true alone
    hides that.

    "No nav mesh in this world" is reported as an error rather than as "not walkable", because they
    call for completely different fixes.
    """
    return _post("nav_project_point", point=point, extent=extent)


@mcp.tool()
def nav_find_path(start: dict, end: dict, draw: bool = False, draw_duration: float = 8.0) -> dict:
    """Can an agent actually get from start to end? Answers without running PIE.

    Reports reachable, partial, pathLength and the path points. PARTIAL IS NOT REACHABLE: a partial
    path stops at the closest reachable point and still looks like a path, so reachable is false
    whenever partial is true and the response says why.

    draw=True leaves the path in the viewport - green if it reaches, orange if partial.
    """
    return _post("nav_find_path", start=start, end=end, draw=draw, drawDuration=draw_duration)


@mcp.tool()
def get_perf_stats() -> dict:
    """Answer "is this mod expensive?" with numbers.

    Returns four groups. The SCENE CENSUS is the one to trust: actors, primitive components, static
    and skeletal mesh components, lights, shadow-casting lights, non-opaque material slots, and a
    LOD0 triangle estimate. Those are properties of the content, reproducible, and are what actually
    decides cost.

    editorTiming and rhi describe the EDITOR drawing its own viewport - UI, gizmos and selection
    outlines included - and are NOT the game's performance. Use them as a relative signal between two
    calls, never as an absolute. The response says so itself.

    memory is process-wide physical usage for the whole editor.
    """
    return _post("get_perf_stats")


@mcp.tool()
def draw_debug(shape: str = "point", start: dict = None, end: dict = None, center: dict = None,
               radius: float = 100.0, extent: dict = None, text: str = "",
               color: str = "green", duration: float = 5.0, thickness: float = 2.0) -> dict:
    """Draw a debug shape in the viewport: line, sphere, box, point, arrow or string.

    capture_camera answers "does this look right" with pixels. This answers "here is what I measured"
    - the trace fired, the bounds compared, the point chosen - drawn next to the geometry it refers
    to, where a human can see it.

    line and arrow take start and end; sphere, box, point and string take center. Colours are named
    (red, green, blue, yellow, cyan, magenta, orange, white, black).

    The response reports which world was drawn into and whether PIE is running, because a shape drawn
    into the editor world is invisible during PIE and vice versa - and the call succeeds either way.
    """
    return _post("draw_debug", shape=shape, start=start, end=end, center=center, radius=radius,
                 extent=extent, text=text, color=color, duration=duration, thickness=thickness)


@mcp.tool()
def trace_ground(x: float = None, y: float = None, location: dict = None,
                 from_z: float = 100000.0, to_z: float = -100000.0,
                 ignore_actor: str = "") -> dict:
    """Line-trace straight down to find ground height.

    Pass either x/y or location={"x":..,"y":..,"z":..} (its z seeds from_z). The response echoes the
    coordinates actually traced - check them. Passing a location the endpoint ignored is how an
    entire terrain investigation once got run at the world origin and concluded the ground was flat
    everywhere.

    Returns hit=false honestly when nothing is hit: editor-world collision is NOT guaranteed for
    imported meshes, and treating a miss as z=0 is how things end up floating.
    """
    return _post("trace_ground", x=x, y=y, location=location, fromZ=from_z, toZ=to_z,
                 ignoreActor=ignore_actor or None)


@mcp.tool()
def capture_camera(location: dict = None, rotation: dict = None, look_at: dict = None,
                   use_viewport_camera: bool = False, fov: float = 0.0,
                   width: int = 1280, height: int = 720,
                   name: str = "MifShot") -> dict:
    "Render the scene from an ARBITRARY viewpoint to a PNG and return its path - does NOT move the user's viewport, so you can inspect while they keep working. THIS IS NOT THE EDITOR VIEWPORT CAMERA: set_viewport_camera / focus_viewport / pilot_actor drive the viewport and do not reach this endpoint. Pass use_viewport_camera=True to shoot from wherever they left the viewport - opt-in, because the default is still (0,0,500) looking down 25 degrees. EVERY response echoes cameraSource = explicit|viewport|default plus locationSource/rotationSource/fovSource: if you expected the viewport and got 'default', that is your answer. Explicit location/rotation/look_at win over the viewport seed. Pass look_at instead of rotation to frame a point. Lit and tonemapped (SCS_FinalColorLDR); the viewport's view mode, show flags and resolution are NOT applied. 'exists' and 'wroteFile' are verified, not assumed - wroteFile false means the PNG is a stale one of the same name. Read the returned path to actually look at it."
    # fov defaults to 0.0, NOT 75.0: _post drops only None, so a non-zero default would send fov on
    # EVERY call, fovSource would read "explicit" 100% of the time and the viewport's own FOV could
    # never reach the capture. 0.0 is a safe sentinel because the C++ hard-refuses fov outside
    # (0,180). 'use_viewport_camera or None' keeps the wire payload byte-identical for old callers.
    return _post("capture_camera", location=location, rotation=rotation, lookAt=look_at,
                 useViewportCamera=use_viewport_camera or None, fov=fov or None,
                 width=width, height=height, name=name)


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
              start_rotation: dict = None, players: int = None, net_mode: str = "",
              one_process: bool = None, width: int = None, height: int = None) -> dict:
    "Start Play-In-Editor. DOES NOT BLOCK: the engine defers the start to its next tick, and this handler runs on the game thread, so waiting here would deadlock the very ticks PIE needs. Poll pie_status until state=='running' before asserting on runtime state. simulate=True runs the world WITHOUT possessing a pawn - better for observing systems tick, since it needs no PlayerStart and cannot fail on a missing GameMode. For replication work: players (1-8) and net_mode (standalone|listen|client, defaulting to listen when players>1) launch a real multiplayer session, one_process (default True) keeps the clients in this process, and width/height size the client windows. Testing anything RepNotify / RunOnServer / NetMulticast needs players>1 - a standalone PIE always has authority and will make authority-gated code look like it works."
    return _post("start_pie", simulate=simulate or None, startLocation=start_location,
                 startRotation=start_rotation, players=players, netMode=net_mode or None,
                 oneProcess=one_process, width=width, height=height)


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
def self_audit(summary_only: bool = False) -> dict:
    "The plugin reporting its OWN invariants from inside the running DLL: live endpoint count and names (the ones actually dispatching, not parsed from a header), each endpoint's transaction bucket (readOnly / selfManaged / transacted / compileHeavy), any policyContradictions (an endpoint in both readOnly and selfManaged - the latter would be silently ignored), healthy, plus buildDate/buildTime so a stale DLL is detectable. Also returns two change-detection signatures, because buildDate/buildTime move on EVERY rebuild including a comment-only one: surfaceSignature (16 hex chars folded over every endpoint's name|bucket|provider - always complete and deterministic, moves only when an endpoint is added/removed/renamed or a bucket/provider changes; check this one first) and paramSignature (folded over the accepted-parameter shapes of the strict-params guards; moves when a key is added to or removed from any accepted list, and NOT for a reorder, a case change or reworded errors). paramSignature is harvested LAZILY - a guard's shape is only seen once that endpoint has actually been called - so paramShapesObserved is returned alongside it and the two builds' paramSignature values are only comparable at equal paramShapesObserved, driven by the same call sequence. summary_only=True returns ONLY the health fields, counts and signatures - the full response carries a row per endpoint plus bucket membership and runs tens of KB, which is an absurd amount to read just to ask whether the bridge is healthy."
    return _post("self_audit", summaryOnly=summary_only or None)


@mcp.tool()
def describe_endpoint(name: str) -> dict:
    "Report what parameters an endpoint accepts, so you stop discovering them by calling endpoints wrong on purpose. Returns status = exactly one of three states, which are never conflated. 'params_declared': the endpoint guards its input - acceptedParams lists every accepted key, aliasGroups pairs each canonical key with its accepted aliases, distinctParams drops the aliases, acceptedSummary is the exact text the guard prints when it refuses, and commonMistakes maps frequently-guessed wrong keys to the right one. 'params_not_declared': there is NO ROW for this endpoint in describe_endpoint's harvested table, so its accepted set cannot be enumerated - acceptedParams is OMITTED, never empty, because an empty list would read as 'takes no parameters'. Read that status narrowly: a missing row has TWO possible causes with OPPOSITE consequences, and this endpoint cannot tell them apart. Either (a) the endpoint has no strict-params guard, in which case it SILENTLY IGNORES any key it does not read - a call can succeed while doing something you did not ask for; or (b) it gained a guard after the table was harvested, in which case it STRICTLY REJECTS unknown keys and an unexpected key is a hard error. Do not assume (a): ten endpoints were in state (b) at one point in this plugin's history and describe_endpoint asserted (a) about all of them. If it matters, read the handler. 'no_such_endpoint': ok:false, with near-miss suggestions. Most endpoints have no row, so expect the middle answer often - it is information, not a failure. The separate positive case of an endpoint that genuinely takes nothing is acceptedParams:[] with acceptsNoParameters:true. Also a superset of a self_audit row: provider, bucket (readOnly/selfManaged/transacted), compileHeavy, and batchable (which mirrors batch's real gate, so 'batch' itself reports false). Key matching is case-insensitive. guard cites the file:line the accepted set was harvested from; coverage reports how much of the surface is describable and flags any table row whose endpoint no longer exists."
    return _post("describe_endpoint", name=name)


# --------------------------------------------------------------------------
# Level / placed-actor editing (the level currently open in the editor)
# --------------------------------------------------------------------------

@mcp.tool()
def get_level_actor(actor_path: str) -> dict:
    """Read one level actor back: transform, label, class, path.

    Takes the actorPath from list_level_actors or spawn_actor_in_level. A label or object name works
    too when it is unique - and because the response echoes the actorPath, you can see which actor a
    label lookup actually resolved to instead of assuming the label was unique.

    Use list_level_actors instead when you want several: that is one call over the whole level, where
    this would be one call each.
    """
    return _post("get_level_actor", actorPath=actor_path)


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
def create_asset(path: str, asset_class: str) -> dict:
    """Instantiate a data-asset class at a /Game path.

    Closes an asymmetry: create_blueprint can author a UDataAsset subclass that nothing was then able
    to instantiate. Pass a concrete class - a native name like "PrimaryDataAsset", or the /Game/...
    path of a Blueprint-authored DataAsset class.

    Refuses abstract classes (an asset of one loads in the editor and fails in the cooked game),
    Actor/Component classes (those are placed, not saved as assets), Blueprint classes (use
    create_blueprint), and a destination that is already taken.

    The asset is created AND registered, then verified by path. Registration is the part that matters:
    an unregistered object answers get_property and set_property perfectly, never appears in
    find_assets, and evaporates on restart. It is still not SAVED - set its properties, then call
    save_dirty_packages.
    """
    return _post("create_asset", path=path, **{"class": asset_class})


@mcp.tool()
def create_datatable(path: str, row_struct: str) -> dict:
    "Create an EMPTY DataTable asset at a /Game/ path with the given row struct, then fill it with write_datatable_rows. row_struct takes a native struct name (RichTextStyleRow, RichImageRow), an F-prefixed name, or a user struct's asset path (/Game/Types/S_MyRow); it must derive from FTableRowBase. This exists because duplicate_asset refuses non-/Game/ sources and import_asset cannot set a CSV import's row struct, so there was previously no way to make a DataTable at all."
    return _post("create_datatable", path=path, rowStruct=row_struct)



@mcp.tool()
def create_struct(path: str, members: list = None) -> dict:
    "Create a Blueprint user-defined struct at a /Game/ path. members is a list of {name, type, container?, valueType?, default?} using the same type grammar as add_variable. A struct must keep at least one member to compile, so the engine's placeholder is only removed once your own members exist."
    return _post("create_struct", path=path, members=members or None)


@mcp.tool()
def list_struct_members(struct: str) -> dict:
    "List a user-defined struct's members: name, friendlyName, guid, type, default, and invalid=true for any member whose type failed to resolve."
    return _post("list_struct_members", struct=struct)


@mcp.tool()
def set_struct_member(struct: str, member: str = "", guid: str = "", new_name: str = "",
                      type: str = "", container: str = "", value_type: str = "",
                      default: str = "") -> dict:
    """Rename, retype or re-default an EXISTING member of a Blueprint struct, in place.

    Without this the only correction is remove + re-add, which mints a new GUID, APPENDS the member at
    the end, reorders the struct, breaks every Make/Break Struct pin, and drops that column from every
    row of every dependent DataTable. Fixing a typo was genuinely expensive.

    Address the member by name or by guid (list_struct_members shows both). Pass at least one of
    new_name, type or default.

    Only works on Blueprint structs. A COOKED struct - which is every base-game DDS2 struct - is
    refused, and that refusal is a safety feature: the engine's struct editing API asserts on a cooked
    struct's stripped editor data rather than returning an error.

    RETYPING IS DESTRUCTIVE DOWNSTREAM. The response reports dependentDataTables and warns when a
    retype has reset that column in every row of every table built on the struct.
    """
    return _post("set_struct_member", struct=struct, member=member, guid=guid, newName=new_name,
                 type=type, container=container, valueType=value_type, default=default)


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
def add_anim_node(graph_id: str, node_class: str, x: int = 0, y: int = 0) -> dict:
    "Add any UAnimGraphNode_* node to an Animation Blueprint's graph - one endpoint for the whole family (SequencePlayer, Slot, StateMachine, BlendSpacePlayer, LayeredBoneBlend...). Works because UAnimGraphNode_Base derives from UK2Node, so anim nodes place and wire exactly like K2 nodes: use connect_pins, move_node, get_node, remove_node on them as normal. Pose DATA lives on the node's Node member, not on pins - set it afterwards with set_property, e.g. propertyPath='Node.Sequence' for a SequencePlayer or 'Node.SlotName' for a Slot."
    return _post("add_anim_node", graphId=graph_id, nodeClass=node_class, x=x, y=y)


@mcp.tool()
def list_animations(filter: str = "", skeleton: str = "", limit: int = 200) -> dict:
    "List animation assets (sequences, montages, blend spaces, composites) from the asset registry WITHOUT loading them. Optional substring filter on path and skeleton. Returns truncated=true if the limit was hit."
    return _post("list_animations", filter=filter or None, skeleton=skeleton or None, limit=limit)


@mcp.tool()
def set_ik_rig_mesh(path: str, mesh: str) -> dict:
    """Assign a SkeletalMesh to an IK Rig - which BUILDS the rig, not just labels it.

    SetSkeletalMesh copies the bone hierarchy, parent indices and reference pose out of the mesh into
    the rig. Assigning PreviewSkeletalMesh directly with set_property stores a pointer and leaves the
    rig skeleton EMPTY, so every later call has no bones to check against. Do this first.

    Refuses if the mesh is missing bones this rig already requires. The engine writes which bones to
    the output log rather than returning them, so the refusal says where to look.
    """
    return _post("set_ik_rig_mesh", path=path, mesh=mesh)


@mcp.tool()
def set_ik_rig_retarget_root(path: str, bone: str) -> dict:
    """Set an IK Rig\'s retarget root - the bone the whole body pose is anchored to, usually pelvis.

    Guarded, because the raw engine call has a silent failure: given a bone that is not in the
    skeleton, SetRetargetRoot sets the root to None and returns TRUE. So asking for a root before a
    mesh is assigned would report success and leave no root. This checks the bone exists first, and
    reads the value back afterwards.
    """
    return _post("set_ik_rig_retarget_root", path=path, bone=bone)


@mcp.tool()
def add_ik_retarget_chain(path: str, name: str, start_bone: str, end_bone: str,
                          goal: str = "") -> dict:
    """Add a retarget chain to an IK Rig: a named span from start_bone down to end_bone.

    Two engine behaviours are surfaced rather than inherited.

    It SILENTLY RENAMES on a name collision - the requested name is run through a uniquifier that
    appends a number - so the response reports `name` (what you got) alongside `requestedName` and a
    `renamed` flag. A mapping written against the name you asked for would otherwise target the wrong
    chain, or nothing.

    It does NOT check the hierarchy: the engine verifies both bones exist and stops there, so a chain
    whose end bone is not a DESCENDANT of its start bone is stored happily and spans nothing. This
    refuses that, which is stricter than the editor and deliberate - there is no correct use for one,
    and it never announces itself afterwards.
    """
    return _post("add_ik_retarget_chain", path=path, name=name, startBone=start_bone,
                 endBone=end_bone, goal=goal or None)


@mcp.tool()
def remove_ik_retarget_chain(path: str, name: str) -> dict:
    """Remove a retarget chain from an IK Rig. Lists the chains it does have if the name is unknown."""
    return _post("remove_ik_retarget_chain", path=path, name=name)


@mcp.tool()
def set_retarget_rigs(path: str, source: str = "", target: str = "") -> dict:
    """Point an IK Retargeter at its source and target IK Rigs.

    SetIKRig is NOT an assignment. It also copies the preview mesh off each rig, rebuilds the chain
    mapping against the TARGET rig\'s chains, and auto-maps chains by fuzzy name match. Writing
    SourceIKRigAsset with set_property does none of that and leaves an unmapped retargeter that reads
    back as fully configured. The response reports the resulting mapping so you can see what happened.

    Both rigs are resolved before either is applied, so a typo in one does not leave the retargeter
    half-wired by the other.
    """
    return _post("set_retarget_rigs", path=path, source=source or None, target=target or None)


@mcp.tool()
def auto_map_retarget_chains(path: str, mode: str = "fuzzy", remap_existing: bool = False) -> dict:
    """Map the source rig\'s chains onto the target rig\'s chains by name.

    mode: "fuzzy" picks the closest name by edit distance, "exact" maps only identical names and sets
    the rest to none, "clear" unmaps everything. remap_existing=False leaves already-mapped chains
    alone, because the engine treats an existing mapping as a deliberate choice - which is why
    re-running it can appear to do nothing. mode="clear" implies it, since clearing only the chains
    that are NOT mapped would be a guaranteed no-op.

    The parameter is deliberately not called "force": that is the conventional name for bypassing a
    destructive-operation guard, and tooling strips it on sight - which silently turned every
    force=True here into False until it was caught. "force" is still accepted if it reaches the
    endpoint.

    Refuses when either rig is unset. The engine\'s implementation sits entirely inside a check for a
    valid target rig, so without one it does nothing at all and reports success.

    Reports the full mapping and, separately, the target chains left UNMAPPED - those parts of the
    body are simply not retargeted at runtime, which nothing else tells you.
    """
    return _post("auto_map_retarget_chains", path=path, mode=mode, remapExisting=remap_existing)


@mcp.tool()
def set_retarget_chain_mapping(path: str, target_chain: str, source_chain: str = "") -> dict:
    """Map one source chain onto one target chain by hand, for what auto-mapping got wrong.

    Pass an empty source_chain to unmap. Both names are checked against their respective rigs BEFORE
    anything is written and the error lists the available chains, because the underlying call returns
    only a bool and you could not otherwise tell which end was wrong.
    """
    return _post("set_retarget_chain_mapping", path=path, targetChain=target_chain,
                 sourceChain=source_chain or None)


@mcp.tool()
def list_retarget_chain_mapping(path: str) -> dict:
    """Read an IK Retargeter\'s chain mapping, and check whether it would actually work.

    Reports each target chain with the source chain driving it, which are unmapped, and a `problems`
    list covering the things that make a retargeter silently do nothing: a missing source or target
    rig, a rig with no skeleton, a rig with no chains, a rig with no retarget root, or source and
    target being the same asset.

    Reads ChainSettings, which is the live mapping. The asset also carries a ChainMapping property -
    that is FRetargetChainMap, deprecated since 5.1 - and a set_property write to it succeeds while
    being read by nothing.
    """
    return _post("list_retarget_chain_mapping", path=path)


@mcp.tool()
def list_ik_solver_types() -> dict:
    """List the IK Rig solver classes this engine build has.

    Needed because the names are NOT guessable: the full-body solver is IKRigFBIKSolver while its
    siblings are IKRig_LimbSolver, IKRig_PoleSolver, IKRig_BodyMover and IKRig_SetTransform.

    Reports class names rather than the friendly labels the IK Rig editor shows. That label comes from
    GetNiceName(), whose base implementation asserts, so a custom solver class that does not override
    it would terminate the editor - it is deliberately never called.
    """
    return _post("list_ik_solver_types")


@mcp.tool()
def add_ik_solver(path: str, solver_class: str) -> dict:
    """Add a solver to an IK Rig. list_ik_solver_types shows the available classes.

    Solvers are addressed by INDEX everywhere else, and indices SHIFT when an earlier solver is
    removed - re-read with list_ik_rig after any remove_ik_solver. Set the solver\'s bone span with
    set_ik_solver, then connect goals to it with set_ik_goal_solver_connection.
    """
    return _post("add_ik_solver", path=path, solverClass=solver_class)


@mcp.tool()
def remove_ik_solver(path: str, index: int) -> dict:
    """Remove a solver from an IK Rig by index.

    Every later solver shifts DOWN by one afterwards, and any goal connected only to this solver
    becomes inert. An out-of-range index is refused with the actual solver count rather than a bare
    "invalid index".
    """
    return _post("remove_ik_solver", path=path, index=index)


@mcp.tool()
def set_ik_solver(path: str, index: int, root_bone: str = "", end_bone: str = "",
                  enabled: bool = None) -> dict:
    """Set a solver\'s root bone, end bone and/or enabled flag.

    Both bones are validated BEFORE either is written, so a bad end_bone cannot leave a solver with a
    new root and its old end. Not every solver type uses every field - a LimbSolver derives its end
    from the goal rather than an explicit end bone - so the response reads the values back off the
    solver and adds refusedNote naming any field the solver declined. The bone names being valid and
    the solver ignoring them are different things.
    """
    return _post("set_ik_solver", path=path, index=index,
                 rootBone=root_bone or None, endBone=end_bone or None, enabled=enabled)


@mcp.tool()
def add_ik_goal(path: str, name: str, bone: str) -> dict:
    """Add an IK goal (an effector target) to a bone on an IK Rig.

    The engine call neither sanitises nor uniquifies the name - unlike retarget chains - and returns
    the same empty answer for "that name is taken" and "no such bone". Both are checked here so the
    refusal says which, and the name is run through the engine\'s own sanitiser first; the response
    reports name, requestedName and a sanitised flag.

    A goal connected to no solver does NOTHING and the rig still initialises - the engine only warns.
    Connect it with set_ik_goal_solver_connection, and list_ik_rig will flag it until you do.
    """
    return _post("add_ik_goal", path=path, name=name, bone=bone)


@mcp.tool()
def remove_ik_goal(path: str, name: str) -> dict:
    """Remove an IK goal. Lists the goals that DO exist if the name is unknown."""
    return _post("remove_ik_goal", path=path, name=name)


@mcp.tool()
def set_ik_goal_bone(path: str, name: str, bone: str) -> dict:
    """Move an existing IK goal to a different bone.

    The underlying call returns the same false for "no such goal" and "no such bone", so both are
    checked first and the error says which. Reports previousBone alongside the new one.
    """
    return _post("set_ik_goal_bone", path=path, name=name, bone=bone)


@mcp.tool()
def set_ik_goal_solver_connection(path: str, name: str, solver_index: int,
                                  connected: bool = True) -> dict:
    """Connect an IK goal to a solver, or disconnect it with connected=False.

    This is the step that makes a goal do anything: an unconnected goal is inert and the engine treats
    that as a warning at most, so nothing else will tell you it was missed. The connection is read
    back after writing rather than trusted from the return value, and the response reports
    connectedToAnySolver so you can see whether the goal now reaches anything at all.
    """
    return _post("set_ik_goal_solver_connection", path=path, name=name,
                 solverIndex=solver_index, connected=connected)


@mcp.tool()
def list_water_bodies(type: str = None, name_contains: str = None) -> dict:
    "List the water bodies in the OPEN level - rivers, lakes, oceans and custom bodies. Reports each body's type, spline point count, world location and which AWaterZone it belongs to. TWO things worth knowing before reading the output. The editor's \"Custom\" body type is spelled Transition in C++, and both spellings are accepted for the type filter and reported side by side as waterBodyType and waterBodyTypeDisplayName. And a body belonging to NO water zone does not render at all since UE 5.1 - it is authored but invisible - so each body reports waterZone and says so explicitly when it is empty. count is what matched the filter and totalInLevel is what exists, reported separately so a filter matching nothing is distinguishable from a level with no water."
    return _post("list_water_bodies", type=type, nameContains=name_contains)


@mcp.tool()
def describe_water_body(path: str, include_spline_points: bool = True) -> dict:
    "Describe ONE water body: everything list_water_bodies reports, plus its water material and every spline point in WORLD space. Resolves by actor PATH, not by label - two bodies can share a label, and list_water_bodies reports actorPath for each. The spline IS the shape of a river or lake, so a body with 0 or 1 spline points is authored-but-empty and renders nothing; likewise a body with no water material assigned renders nothing regardless of its spline, and that case is called out rather than left as an empty string. Spline points are world-space deliberately, so they can be compared against landscape and placed actors without a frame conversion."
    return _post("describe_water_body", path=path, includeSplinePoints=include_spline_points)


@mcp.tool()
def create_water_body(type: str, label: str = None, x: float = 0.0, y: float = 0.0,
                      z: float = 0.0, points: list = None) -> dict:
    "Create a water body in the OPEN level - River, Lake, Ocean or Custom. THE TYPE IS THE CLASS, not a settable property: the four water body types are four different actor classes with four different components, so you pick one here and cannot change it afterwards with set_property. Custom is the editor's name for the C++ 'Transition' and both are accepted. Optionally pass points (an array of {x,y,z} in WORLD space) to set the spline in the same call - a river with no spline is not a river. A spline needs at least 2 points; one point is a degenerate spline that the engine accepts and renders as nothing, so it is refused. Nothing is saved: the actor exists in the open level only. Two things a new body still needs before it renders anything - an AWaterZone covering it, and a water material on its component; the response reports whether it found a zone."
    return _post("create_water_body", type=type, label=label, x=x, y=y, z=z, points=points)


@mcp.tool()
def set_water_body_spline(path: str, points: list) -> dict:
    "Replace a water body's spline - the spline IS the shape of a river or lake. points is an array of {x,y,z} in WORLD space and REPLACES the existing spline entirely; there is no append and no single-point setter, because ResetSpline is the only engine entry point that rebuilds the body's derived data and poking the spline component directly leaves those caches stale (a river the wrong shape, with no error anywhere). Resolves by actor PATH, not label. Needs at least 2 points. The response reads the spline back rather than echoing the request, and reports splineNote if the engine collapsed coincident points so the count differs from what you sent."
    return _post("set_water_body_spline", path=path, points=points)


@mcp.tool()
def create_data_layer(name: str, asset_path: str = None, type: str = "runtime",
                      is_private: bool = False) -> dict:
    "Create a World Partition Data Layer. Without this the family could only operate on layers somebody else authored - list them, change visibility, move actors in and out - which is half a subsystem. Requires a World Partition map and says so by name if the open map is not one (sublevels are the non-partitioned equivalent; see list_sublevels). type is 'runtime' (default) or 'editor'. The DataLayerAsset is created IN MEMORY at /Game/_MifDataLayers/<name> unless you name a path, and NOTHING IS SAVED - the asset and the instance last for the session and an editor restart loses both, which is what makes this usable for tests. The response reads back through the DataLayerManager rather than trusting the returned pointer, so if list_data_layers would not see the layer this call tells you rather than reporting success."
    return _post("create_data_layer", name=name, assetPath=asset_path, type=type,
                 isPrivate=is_private)


@mcp.tool()
def add_actor_to_data_layer(actor_path: str, name: str) -> dict:
    "Put an actor INTO a World Partition Data Layer - the operation Data Layers exist for, and the half this bridge was missing (it could read layers and change how they display, but not what belongs to them). Resolves the actor by PATH, not label. Reports wasAlreadyIn separately from added, because 'already a member' and 'the write failed' both leave the actor in the layer and are otherwise indistinguishable. The verdict comes from a READ-BACK of the actor's layers, not from the engine's return value: AddActorToDataLayer returns false both for a genuine failure and, on some paths, for an actor that was already a member. actorDataLayers lists every layer the actor is in afterwards. Nothing is saved."
    return _post("add_actor_to_data_layer", actorPath=actor_path, name=name)


@mcp.tool()
def remove_actor_from_data_layer(actor_path: str, name: str) -> dict:
    "Remove an actor from a World Partition Data Layer. Resolves by actor PATH, not label. REFUSES if the actor is not in that layer rather than reporting a harmless no-op - naming a layer the actor is not in is a typo or a stale assumption, and the refusal lists the layers it IS in so you can see which. There is deliberately no remove-from-all form. The verdict is a read-back of the actor's layers afterwards, not the engine's return value. Nothing is saved."
    return _post("remove_actor_from_data_layer", actorPath=actor_path, name=name)


@mcp.tool()
def list_foliage_instances(foliage_type: str = None, include_instances: bool = False,
                           limit: int = 200) -> dict:
    "Enumerate the foliage in the open level, by TYPE. This is the read half of add_foliage_instances, which could place foliage while nothing could enumerate it - so a placement could not be verified even in principle. Foliage is not one actor per instance: it lives in the level's AInstancedFoliageActor keyed by foliage type, so filter on foliageType, not on a mesh or an actor path. include_instances adds per-instance transforms (off by default because a painted level has tens of thousands); instanceCount is the TRUE total even when the listing is truncated. THE COOKED CAVEAT MATTERS HERE: placed-instance data is editor-only, so a COOKED level carries its foliage as baked component data and can report types with zero instances while visibly full of foliage - the response says so explicitly rather than leaving a zero to be misread. A level that never had foliage reports no InstancedFoliageActor at all, which is a different state again, and this read will not create one to find out."
    return _post("list_foliage_instances", foliageType=foliage_type,
                 includeInstances=include_instances, limit=limit)


@mcp.tool()
def list_ik_rig(path: str) -> dict:
    """Read an IKRigDefinition AND check whether it would actually work.

    This does not echo the asset's fields back - it validates them. Every field an IK Rig holds can
    be written directly with set_property, and doing so produces an asset that reads back perfectly
    and is broken: a skeleton whose parallel arrays have drifted, a missing reference pose, chains
    naming bones that do not exist, or a chain whose end bone is not a descendant of its start bone so
    there is no chain between them at all. All of those return ok:true when written.

    Reports previewMesh, boneCount, refPoseCount, retargetRoot, every chain with its own valid flag,
    and the rig\'s solvers and goals - including which solvers each goal reaches, since a goal wired to
    none is inert and the engine only warns about it.

    `purpose` says whether this rig is set up for retargeting, for IK, for both, or for nothing yet: a
    rig needs only the half it is used for, and demanding chains from an IK-only rig would call a
    perfectly good one invalid. `valid` and `problems` are judged against that purpose.

    `runtimeInitialized` is the ENGINE\'s own verdict - the rig is actually handed to UIKRigProcessor
    and initialised - with the engine\'s errors and warnings surfaced. It is skipped, with the reason
    given, when the structural checks already failed: handing a structurally inconsistent rig to the
    engine can hit an assert that terminates the editor rather than returning an error.

    IK Rig is UE5-only. On an engine without the plugin this endpoint still exists and refuses with
    that reason, so you can tell "no IK Rig here" from "no such endpoint".
    """
    return _post("list_ik_rig", path=path)


@mcp.tool()
def analyze_skeletal_split(path: str, lod: int = 0) -> dict:
    "What splitting a SkeletalMesh WOULD produce, without splitting it. Reports each render section's vertex/triangle counts and the bones it is skinned to, then per bone which sections it reaches - a bone touching exactly ONE section can be cut cleanly, one spanning several cannot. Section-based rather than per-vertex on purpose: sections are already separate draw calls with their own material, and reading per-vertex weights needs a CPU copy the engine can discard. skinWeightsReadableOnCPU says whether a per-vertex split is possible on THIS asset; measured across 40 DDS2 meshes all 40 kept CPU access, so treat GPU-only as a property of the asset rather than of being cooked. A mesh with one section has no boundary to split on and says so. Bad lod is refused, not clamped."
    return _post("analyze_skeletal_split", path=path, lod=lod)


@mcp.tool()
def list_bones(path: str, name_contains: str = "", root: str = "",
               include_transforms: bool = False) -> dict:
    """List the bones of a Skeleton or SkeletalMesh, with the hierarchy.

    Nothing else in the bridge could name a bone. describe_animation reports curves and notifies but
    no tracks, list_sockets reports sockets (which attach TO bones without enumerating them), and
    reflection cannot help because USkeleton::ReferenceSkeleton is a plain C++ member rather than a
    UPROPERTY - get_property on a Skeleton reaches BoneTree, which holds retargeting modes and no
    names.

    Each bone reports name, index, parent (name AND index), and depth. root limits the listing to one
    bone and its descendants; name_contains filters; include_transforms adds the reference pose, which
    is PARENT-RELATIVE, not world space.

    A mesh and its skeleton can hold DIFFERENT bones - a mesh imported against a skeleton may carry
    fewer - so the response says which one it read in `source`, and when they disagree it reports both
    counts and says so. Which one you read decides whether a bone name will resolve at runtime.
    """
    return _post("list_bones", path=path, nameContains=name_contains or None,
                 root=root or None, includeTransforms=include_transforms)


@mcp.tool()
def list_sockets(path: str) -> dict:
    """List the sockets on a SkeletalMesh or StaticMesh asset.

    This is what a mod attaches props to, and there was previously no way to see what exists. Pass the
    MESH ASSET's path - sockets live on the mesh, not on a blueprint, so resolve the component's
    StaticMesh/SkeletalMesh property first if that is where you are starting.

    Note for skeletal meshes: a USkeleton carries its OWN socket list separately, and a socket defined
    on the skeleton will not appear here. The response says so.
    """
    return _post("list_sockets", path=path)


@mcp.tool()
def describe_behavior_tree(path: str) -> dict:
    """Read a BehaviorTree's structure: root, node tree, and which blackboard it uses.

    Depth-first with depth, name, class, kind (composite/task/root) and decorator count per node. The
    walk is bounded at 2000 nodes and says so if it truncates, rather than returning a partial tree as
    if it were whole.
    """
    return _post("describe_behavior_tree", path=path)


@mcp.tool()
def list_blackboard_keys(path: str) -> dict:
    """List a BlackboardData asset's keys, with type and whether each is inherited from a parent.

    The inherited flag matters: an inherited key is usable but is not editable on this asset, and a
    caller who cannot tell the two apart will try to change one and wonder why nothing happened.
    """
    return _post("list_blackboard_keys", path=path)


@mcp.tool()
def set_blendspace_samples(asset_path: str, samples: list, clear: bool = True) -> dict:
    "Place animation samples in a BlendSpace. samples is [{animation, x, y?}] - each entry names an AnimSequence and its position on the blend axes; y is ignored by a 1D BlendSpace. clear (default true) wipes the existing samples first, so the call is a full replace rather than an append. The AXES themselves are not set here: use set_property with propertyPath=BlendParameters[0].Max (also .Min, .DisplayName, .GridNum). Ported from the UE 5.7 deployment where it was written and used."
    return _post("set_blendspace_samples", assetPath=asset_path, samples=samples, clear=clear)


@mcp.tool()
def set_bone_translation_retargeting(skeleton_path: str, bone_name: str, mode: str,
                                     children_too: bool = False) -> dict:
    "Set how a Skeleton retargets a bone's TRANSLATION. mode is one of Animation, Skeleton, AnimationScaled, AnimationRelative or OrientAndScale. This is what stops a retargeted character sinking through the floor or drifting: the root and pelvis usually want AnimationScaled or OrientAndScale while most bones want Skeleton. children_too applies the same mode down the whole subtree, which is normally what you want for a limb. Ported from the UE 5.7 deployment where it was written and used."
    return _post("set_bone_translation_retargeting", skeletonPath=skeleton_path, boneName=bone_name,
                 mode=mode, childrenToo=children_too)


@mcp.tool()
def describe_animation(asset_path: str) -> dict:
    "Describe an animation asset: skeleton, playLength, notifies (with notify-state windows and branching points), curves. Plus per type - sequence: frameRate/numSampledKeys/additive/syncMarkers; montage: blend times, sections (with nextSection) and slot segments; blendSpace: axes and samples. For an animation BLUEPRINT use list_graphs/list_nodes instead - nested state machines and transition graphs are included."
    return _post("describe_animation", assetPath=asset_path)


# --------------------------------------------------------------------------
# Asset lifecycle (/Game/ only, headless)
# --------------------------------------------------------------------------

@mcp.tool()
def close_asset_editors(path: str, confirm: bool = False) -> dict:
    "Close every open asset editor holding an asset. Deliberately SEPARATE from delete_asset: closing an editor can discard unsaved work in that tab, so the caller opts in rather than a delete doing it silently. Requires confirm=True. Returns had_open_editor, editors_found, editors_closed, still_open and the editor names - closing is a REQUEST a toolkit can decline (an open modal will), so still_open is re-checked after the attempt rather than assumed to be zero. Use this when delete_asset reports blockedBy.openAssetEditors."
    return _post("close_asset_editors", path=path, confirm=confirm)


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
def get_collision(path: str, lod: int = 0) -> dict:
    "Read a StaticMesh's OWN collision - simple primitive count, convex hull count, the collisionComplexity flag by NAME, whether it has a BodySetup at all, and per-section collisionEnabled for the given LOD. This is the read half the collision family was missing: add_simplified_collision and set_collision could change collision and nothing could see it. NOT to be confused with list_collision_profiles, which lists the project's collision PROFILE names and says nothing about any particular mesh. hasBodySetup is reported separately from a zero count because 'no collision' and 'no collision container at all' are different problems. A bad lod index is REFUSED rather than clamped - a clamped index reports another LOD's sections under the number you asked for. verdict states the answer in one line, including the case where complex-as-simple makes a zero primitive count correct rather than alarming."
    return _post("get_collision", path=path, lod=lod)


@mcp.tool()
def remove_collision(path: str, confirm: bool = False) -> dict:
    "Clear ALL simple collision from a StaticMesh - the StaticMeshEditor's 'Remove Collision' button, reachable without opening that editor. Requires confirm=True because it destroys hand-authored convex hulls with no undo across HTTP. Returns removedPrimitives and hadCollision; a mesh that already had none is a success with removedPrimitives=0, not an error. Use this BEFORE add_simplified_collision when you mean to REPLACE collision rather than stack a second primitive on top. Do NOT try to do this with set_property on BodySetup.AggGeom: the property reads back changed but the engine's own path also runs FlushRenderingCommands and RefreshCollisionChange, and without the latter no StaticMeshComponent instanced from the mesh ever picks the change up."
    return _post("remove_collision", path=path, confirm=confirm)


@mcp.tool()
def list_collision_profiles() -> dict:
    """List the collision profiles THIS project defines, with what each resolves to.

    Each entry reports the profile's collisionEnabled mode, its object type, and its per-channel
    responses (Block / Overlap / Ignore) - which is what actually decides whether a mod's prop stops
    the player.

    Worth knowing: set_property will accept ANY string as BodyInstance.CollisionProfileName and read
    it straight back, leaving the component on its previous collision. This is the authority on which
    names mean something, and set_collision validates against it.
    """
    return _post("list_collision_profiles")


@mcp.tool()
def set_collision(object_path: str, profile: str = "", collision_enabled: str = "") -> dict:
    """Set a primitive component's collision profile, with the profile name CHECKED.

    object_path is a component's templatePath from list_components, or a placed actor's component
    path. Pass profile and/or collision_enabled (NoCollision | QueryOnly | PhysicsOnly |
    QueryAndPhysics).

    An unknown profile name is REFUSED with the list of real ones. set_property accepts it silently,
    which leaves the component on its previous collision while reading back as though it changed - a
    prop that looks configured and does not block the player.

    The response reports the channel responses the profile RESOLVED to, because "the profile is set"
    and "it now blocks what I meant" are different claims.
    """
    return _post("set_collision", objectPath=object_path, profile=profile,
                 collisionEnabled=collision_enabled)


@mcp.tool()
def add_simplified_collision(path: str, shape: str) -> dict:
    "Generate one simple collision primitive on a StaticMesh - the StaticMeshEditor's collision toolbar (Add Box/Sphere/Capsule/K-DOP Simplified Collision), reachable without opening that editor. shape: box | sphere | capsule | 10dop-x | 10dop-y | 10dop-z | 18dop | 26dop. Calls the engine's own generators with its own K-DOP direction tables, so the result is identical to the toolbar button. ADDITIVE - it does NOT replace existing collision: the engine's replace-or-cancel prompt is commented out in GeomFitUtils.cpp, so generating over a mesh that already has collision silently leaves you with TWO primitives. Call remove_collision first to replace. Returns primitivesBefore/primitivesAfter/added so you can verify which happened."
    return _post("add_simplified_collision", path=path, shape=shape)


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
def list_niagara_user_parameters(path: str, name_contains: str = "") -> dict:
    """Read a NiagaraSystem's User. parameters, with their VALUES.

    The names alone are already reachable via get_property on
    ExposedParameters.SortedParameterOffsets. The values are not: they live in a flat byte array
    indexed by offset, typed only by an opaque index into a runtime registry that has no reflection
    surface. This is the call that answers "what is User.Spawn Rate actually set to".

    The type is NOT guessed. sizeBytes is exact - it comes from the gap to the next parameter - but a
    four-byte value could be a float, an int32 or a bool, and the store does not say which. So all
    three readings are returned side by side (asFloat, asInt32, asBool), and 2/3/4-float values come
    back as asFloats. typeIndex is passed through untranslated for the same reason; it is stable
    within a build, so it is a usable discriminator once you have learned it.

    Read-only, and the write side is deliberately not implemented. In a cooked-game mod you do not
    edit the asset anyway - you call SetNiagaraVariableFloat/Vec3/Bool on the spawned component from
    Blueprint, and the exact name string this returns is what those take.
    """
    return _post("list_niagara_user_parameters", path=path, nameContains=name_contains or None)


@mcp.tool()
def list_material_parameters(path: str, types: list = None, group: str = "") -> dict:
    """List the parameters a Material or MaterialInstance EXPOSES.

    This is the endpoint to use on SHIPPED content. Cooking strips a material's expression graph, so
    list_material_expressions correctly reports numExpressions:0 on every cooked master material -
    but the cached parameter table survives cook, so this still works.

    Each parameter reports name, type, group, description, sort priority, current/default value, and
    critically its ASSOCIATION (global | layer | blend) and INDEX. Those two are not decoration: a
    layer parameter treated as a global makes set_material_parameter build the wrong
    FMaterialParameterInfo, silently fail, and look as though the parameter does not exist.

    On a MaterialInstance each entry also reports overriddenOnThisInstance, which tells you whether
    the value is this instance's own or inherited from its parent - i.e. whether resetting it would
    do anything.
    """
    return _post("list_material_parameters", path=path, types=types or [], group=group)


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
def list_input_mappings(path: str) -> dict:
    "Read an InputMappingContext: which key is bound to which Input Action, with the triggers and modifiers on each mapping. This answers the question that comes BEFORE add_enhanced_input_action - that places the event node for an action, but nothing could tell you what the action is bound to, or what else shares the key. path takes either the package (/Game/Input/IMC_Default) or the object path (/Game/Input/IMC_Default.IMC_Default). Triggers and modifiers are reported by class name; their settings are ordinary UPROPERTYs, so get_property on the object path reaches them."
    return _post("list_input_mappings", path=path)


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
def trace_start(channels: str = None) -> dict:
    "Start an Unreal Insights trace, writing a .utrace under Saved/MifBridge/Traces. This is the answer to 'which Blueprint is burning frame time' that perf_heavy_actors cannot give: that one reports a static CENSUS (triangles, components, which actors tick), while a trace shows each Tick by name with its real cost. Default channels cpu,frame,bookmark,stats. Tracing costs performance while it runs - do the thing you want to measure, then call trace_stop."
    return _post("trace_start", channels=channels)


@mcp.tool()
def trace_stop() -> dict:
    "Stop the trace started by trace_start and report where the file went and how big it is. The size is the evidence it captured anything - a zero-byte trace means the channels produced no data, which otherwise looks identical to success. Stopping when nothing was started is not an error; it answers stopped:false so the call stays idempotent."
    return _post("trace_stop")


@mcp.tool()
def perf_heavy_actors(limit: int = 40, sort_by: str = None) -> dict:
    "Rank the level's actors by STATIC content cost: LOD0 triangles, primitive components, material slots, and a rough draw estimate (components x material slots). Each row also reports trianglePercent, because a rank is only actionable next to a proportion. sort_by is one of triangles|components|materials|drawEst. IMPORTANT: this is a CENSUS, not a profiler - it cannot see a Blueprint burning milliseconds in Tick, and it is not frame time. get_perf_stats reports editor timing and its own caveat explains why that is the editor drawing its viewport rather than the game's fps. For real frame attribution use Unreal Insights; nothing here replaces it."
    return _post("perf_heavy_actors", limit=limit, sortBy=sort_by)


@mcp.tool()
def blueprint_inheritance_tree(path_prefix: str = "/Game/", root: str = None,
                               max_depth: int = 0) -> dict:
    "The project's Blueprint class hierarchy, built ENTIRELY from asset registry tags - it LOADS NOTHING. Parent comes from FBlueprintTags::ParentClassPath, published per asset, which is why this is safe on a COOKED project where loading Blueprints can kill the editor. Pass root (a blueprint name, its _C class name, or a NATIVE class like 'Actor') to get one subtree; nativeRoots in a no-argument call lists the native classes this project actually derives from. maxDepth 0 is unlimited, and a truncated node reports childrenNotShown rather than looking like a leaf. Always check registryStillScanning: at editor startup a partial tree is indistinguishable from a small project. Blueprint-ness is detected by TAG, not class name, so WidgetBlueprint and AnimBlueprint are included."
    return _post("blueprint_inheritance_tree", pathPrefix=path_prefix, root=root,
                 maxDepth=max_depth)


@mcp.tool()
def project_dependency_graph(path_prefix: str, max_nodes: int = 300,
                             include_external: bool = False) -> dict:
    "The dependency graph under a path prefix: nodes (packages) and edges (A depends on B). Each node reports dependsOn AND referencedBy, because they answer different questions - 'what does this need' versus 'what breaks if I delete it'. path_prefix needs at least two segments (e.g. /Game/Blueprints): GetReferencers runs PER ASSET, so a mount root is a stopped game thread, not a slow call. Capped at max_nodes and reports `truncated` plus `matched` - a truncated graph is a PREFIX of the real one, not a sample, so narrow the prefix rather than raising the cap."
    return _post("project_dependency_graph", pathPrefix=path_prefix, maxNodes=max_nodes,
                 includeExternal=include_external)


@mcp.tool()
def project_asset_distribution(path_prefix: str = None, top_folders: int = 25,
                               top_classes: int = 25) -> dict:
    "Counts of assets by class and by folder under a path prefix (default /Game). Cheap by construction - pure Asset Registry, loads nothing, never touches referencers - which is why this one accepts a bare /Game where project_dependency_graph does not. Reports distinctClasses/distinctFolders alongside the top-N lists so a truncated view is visibly truncated, and registryStillScanning because a low count during a scan is indistinguishable from a low count."
    return _post("project_asset_distribution", pathPrefix=path_prefix,
                 topFolders=top_folders, topClasses=top_classes)


@mcp.tool()
def set_data_layer_visibility(name: str, visible: bool) -> dict:
    "Show or hide a World Partition Data Layer in the editor. Reports before/after/changed plus a separate `verified` flag, because the underlying SetDataLayerVisibility returns VOID and cannot fail loudly - verified:false means the write did not take. Also reports effectiveVisible: a layer can be visible in its own right and still render nothing because a parent layer is hidden. Editor state only; nothing is saved."
    return _post("set_data_layer_visibility", name=name, visible=visible)


@mcp.tool()
def set_data_layer_loaded_in_editor(name: str, loaded: bool,
                                    from_user_change: bool = True) -> dict:
    "Load or unload a World Partition Data Layer's actors in the EDITOR. This is not the same as visibility - an unloaded layer is not in memory at all, where a hidden one is. Reports before/after/changed, a separate `verified` flag read back off the layer, and `engineReturned` (what the engine call itself said), because those are different questions. Editor state only; nothing is saved."
    return _post("set_data_layer_loaded_in_editor", name=name, loaded=loaded,
                 fromUserChange=from_user_change)


@mcp.tool()
def list_game_feature_plugins(name_contains: str = None, active_only: bool = False) -> dict:
    "List the project's Game Feature plugins - how content is added to a shipped game without patching the base game - with their derived state and the raw predicates behind it. IMPORTANT: `state` is DERIVED from installed/registered/loaded/active, because the engine's own GetPluginState exists only on UE 5.7 and not on 5.3; stateFlags carries the raw predicates. gameFeaturePluginCount and totalDiscoveredPlugins are reported so a filtered list never reads as completeness."
    return _post("list_game_feature_plugins", nameContains=name_contains, activeOnly=active_only)


@mcp.tool()
def describe_game_feature_plugin(name: str) -> dict:
    "Describe one Game Feature plugin by NAME (like 'DDS2Casino'), not by asset path: its derived state, descriptor fields, and modules. A plugin that exists but is not a game feature is ANSWERED rather than refused, with isGameFeature false - 'this is not a game feature' is the useful answer to that question. detectedBy says which test matched: the subsystem, the ExplicitlyLoaded descriptor flag, or both."
    return _post("describe_game_feature_plugin", name=name)


@mcp.tool()
def describe_niagara_system(path: str) -> dict:
    "Describe a NiagaraSystem: how many emitters it has and how many are actually ENABLED. A disabled emitter is invisible at runtime and perfectly visible in the editor, which is a common source of 'the effect does nothing', so the enabled and disabled counts are reported separately. If a system reports zero emitters and its package is COOKED, that may mean its editor-only emitter data was stripped rather than that the effect is empty."
    return _post("describe_niagara_system", path=path)


@mcp.tool()
def list_niagara_emitters(path: str, name_contains: str = None,
                          include_disabled: bool = True) -> dict:
    "List a NiagaraSystem's emitters with their index, name, GUID and enabled state. Address an emitter by INDEX rather than name where you can: names are not guaranteed unique within a system. totalEmitters is the unfiltered count, so a filtered list can never be mistaken for the whole thing."
    return _post("list_niagara_emitters", path=path, nameContains=name_contains,
                 includeDisabled=include_disabled)


@mcp.tool()
def list_level_sequences(filter: str = None, limit: int = 0) -> dict:
    "List the project's LevelSequence assets - cutscenes. Pure Asset Registry, so it LOADS NOTHING and cannot trip the cooked-asset hazards that loading an editor asset can. filter is a substring matched against the full object path. Always check registryStillScanning: at editor startup the registry is still discovering assets, so a low or zero count can mean 'not finished looking' rather than 'none exist'. matched is the true total even when limit truncates the list."
    return _post("list_level_sequences", filter=filter, limit=limit)


@mcp.tool()
def describe_level_sequence(path: str) -> dict:
    "Describe one LevelSequence: duration, frame rates, how many things it possesses or spawns, and whether it drives a camera. Sequencer has TWO rates and conflating them is the classic mistake - tickResolution is the internal integer frame space (24000/1 by default) and displayRate is what the UI shows (30/1), so a frame number is meaningless without saying which. Every tick value is also given in seconds. possessables reference actors that must already exist in the level; spawnables carry their own template and are created by the sequence."
    return _post("describe_level_sequence", path=path)


@mcp.tool()
def list_data_layers() -> dict:
    "List the Data Layers of the World Partition map currently open. Data Layers are how a partitioned world is organised, and list_sublevels cannot see them - that answers about streaming levels, a different mechanism which is empty on a partitioned map. Each entry reports name, shortName, fullName, whether it is a RUNTIME layer (only those can be streamed at all), its initial runtime state and its debug colour. On a non-partitioned map it returns count 0 with a note pointing at list_sublevels rather than an error."
    return _post("list_data_layers")


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
def open_asset_editor(path: str) -> dict:
    "Open an asset's default editor (StaticMesh, SkeletalMesh, Material, Animation, ...) programmatically. DOES NOT make that editor's commands reachable by invoke_editor_command - this was built expecting it would, and it was MEASURED FALSE: opening SM_Barrel's StaticMeshEditor left the cached contexts at [LevelViewport, ContentBrowser] with newContexts[] empty, and StaticMeshEditor.RemoveCollision still failed with cachedListsForContext:0. Root cause, verified in engine source: asset editor toolkits NEVER call FInputBindingManager::RegisterCommandList - only five call sites in all of Engine/Source/Editor do (SContentBrowser, LevelEditor, SLevelViewport, MainFrame, Sequencer) - so there is no broadcast to cache, at any time, however often you open it. For an asset-editor command use a DIRECT endpoint that calls the same engine function the button does: remove_collision / add_simplified_collision cover the static-mesh collision toolbar. What this IS good for: getting an editor open for a human to look at, or driving one of the five contexts that do register. newContexts[] is retained as live evidence - if a future engine version starts registering asset-editor lists, it shows up there first. NOT dialog-free: it opens real UI, and an asset that prompts on open can raise a modal, which stalls the game-thread ticker the bridge runs on."
    return _post("open_asset_editor", path=path)


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


# --------------------------------------------------------------------------
# Source media ingest (MifBridgeImport.cpp) - the bridge could author assets
# but never bring BYTES in. import_texture's base64 mode is the only route for
# an icon that was GENERATED: there is no file on disk to point at, and
# reimport_asset cannot help because there is nothing to re-pull.
#
# (This block sits ABOVE main()/the __main__ guard on purpose: a tool defined
#  after mcp.run() starts never registers - the spawn_actor_in_pie lesson.)
#
# The Python arg names deliberately differ from the JSON field names to avoid
# shadowing builtins/stdlib: image_format -> format, base64_data -> base64,
# texture_filter -> filter, export_format -> format, ascii_fbx -> ascii. Each
# _post() call does the mapping, and _post drops None values so every optional
# stays genuinely optional.
#
# export_asset is the READ side of this family and the newest member: the
# bridge could bring bytes in and author assets, but nothing could get a mesh
# back OUT, which is what blocked the Blender round trip (see the bl_* and
# mif_* sections at the bottom of this file).
# --------------------------------------------------------------------------

@mcp.tool()
def import_texture(dest_path: str, source_path: str = "", base64_data: str = "",
                   image_format: str = "", overwrite: bool = None, save: bool = None,
                   compression_settings: str = "", srgb: bool = None,
                   lod_group: str = "", never_stream: bool = None,
                   mip_gen_settings: str = "", texture_filter: str = "") -> dict:
    "Create or REFILL a Texture2D from image bytes. TWO ingest modes - supply exactly one: source_path (a file on disk) or base64_data (the raw image bytes inline; use this when you generated the image and it was never written to a file). PNG, JPEG, BMP and TGA; a data: URI prefix and any newlines/whitespace inside base64_data are stripped for you. HDR/EXR/DDS/TIFF are refused here - use import_asset for those. overwrite:true re-initialises the EXISTING texture object IN PLACE, so everything already referencing it keeps working - this is how you fix a stub icon the UI already points at; without overwrite an existing asset is an error. Saves to disk by default. The response reports sourceDataBytes, sizeX/sizeY, numMips, pixelFormat and fileSizeBytes so you can tell a real texture from a header-only stub. For UI/shop icons pass lod_group='UI', compression_settings='UserInterface2D', mip_gen_settings='NoMipmaps', never_stream=True."
    return _post("import_texture", destPath=dest_path,
                 sourcePath=source_path or None, base64=base64_data or None,
                 format=image_format or None, overwrite=overwrite, save=save,
                 compressionSettings=compression_settings or None, srgb=srgb,
                 lodGroup=lod_group or None, neverStream=never_stream,
                 mipGenSettings=mip_gen_settings or None, filter=texture_filter or None)


@mcp.tool()
def import_asset(file: str, destination: str, name: str = "", factory: str = "",
                 replace_existing: bool = None, replace_existing_settings: bool = None,
                 save: bool = None) -> dict:
    "Import a source media FILE (fbx, wav, psd, obj - anything a loaded editor factory accepts) into a /Game/ folder via UAssetImportTask. destination is a FOLDER, not an asset path; name defaults to the file stem. The factory is auto-resolved from the extension (highest ImportPriority wins) - pass factory to force a specific one; an unsupported extension errors with the full supported list. Always runs bAutomated=true and bAsync=false, so no import-options dialog can appear and nothing spans frames; a large FBX is one long frame. Returns one row per imported object with objectPath/packageName/class, plus dimensions, pixelFormat and sourceDataBytes for anything that came in as a texture. Refuses destinations that collide with container-only packages. For image bytes you hold in memory rather than a file, use import_texture."
    return _post("import_asset", file=file, destination=destination,
                 name=name or None, factory=factory or None,
                 replaceExisting=replace_existing,
                 replaceExistingSettings=replace_existing_settings, save=save)


@mcp.tool()
def export_asset(asset: str, file: str = "", export_format: str = "", overwrite: bool = None,
                 fbx_compatibility: str = "", ascii_fbx: bool = None, vertex_color: bool = None,
                 level_of_detail: bool = None, collision: bool = None,
                 export_source_mesh: bool = None, force_front_x_axis: bool = None) -> dict:
    "Write an asset OUT to a disk file - the read side of round-tripping, and until this existed a mesh could not leave the editor at all. StaticMesh->FBX, Texture->PNG/TGA, SoundWave->WAV, object/level->T3D; the exporter is resolved by UExporter::FindExporter from the class plus the extension, and a class with no exporter is an error listing what does work rather than an empty file. file defaults to <ProjectSaved>/MifBridge/Export/<Name>.<ext> and its directory is created on demand; overwrite defaults true and overwrite:false over an existing file is an ERROR, never a silent no-op. Mutates NO asset (read-only bucket, so it is usable inside batch). FBX is written Z-up / -Y-front / right-handed / centimetres, which is bit-for-bit Blender's own axis system - import it with NO axis arguments and export it back with axis_up='Z', axis_forward='Y' (NOT the Blender operator defaults) and the trip is lossless. force_front_x_axis rotates the scene and is warned about, because it shears anything tiled along a spline. THE RESPONSE IS THE POINT: the engine's export call returns TRUE on three paths that write no file AND deletes the destination on none of them, so a plain stat afterwards cannot tell a fresh file from last run's leftovers - which, on a deterministic default path, is every call after the first. Every expected output file is therefore photographed BEFORE the export (existence, timestamp, size) and counts as written only if it did not exist before or actually moved; a file that did not move is a FAILURE saying the exporter reported success and wrote nothing, not an ok:true over stale bytes. The response carries fileCount, filesWritten, totalFileSizeBytes and files[] with a per-file verdict of written|stale|missing|empty, and file/fileSizeBytes always name a file this call provably wrote. files[] has one entry for StaticMesh->FBX; it has several for the exporters that write more than one file from one name (UDIM/virtual textures as .1001/.L0, surround SoundWave as _fl/_fr/...), which used to be reported as 'produced no usable file'. FBX exports also echo fbxCompatibility - this endpoint defaults to FBX_2020, which is deliberately NOT the engine's own FBX_2013. For a static mesh it also returns numLODs, numVertices, numTriangles, materialSlots[] (ORDER matters - a reimport that reorders them renders the wrong material) and boundsMinUU/boundsMaxUU/boundsSizeUU, which is the pre-image to assert a round trip against. level_of_detail:true emits every LOD into one FBX and will not reimport as a single mesh; export_source_mesh silently disables itself on a cooked asset, which has no MeshDescription."
    return _post("export_asset", asset=asset, file=file or None, format=export_format or None,
                 overwrite=overwrite, fbxCompatibility=fbx_compatibility or None,
                 ascii=ascii_fbx, vertexColor=vertex_color, levelOfDetail=level_of_detail,
                 collision=collision, exportSourceMesh=export_source_mesh,
                 forceFrontXAxis=force_front_x_axis)


@mcp.tool()
def reimport_asset(path: str, source_file: str = "", source_file_index: int = None,
                   force_new_file: bool = None, save: bool = None) -> dict:
    "Re-pull an imported asset from its recorded source file(s). source_file supplies or overrides the path when the original is gone or you want different content. Never opens a file picker: an asset with no recorded source, or whose every recorded source is missing from disk, is an ERROR that names import_texture's base64 mode as the route that actually works - which is the case for generated icons that were never backed by a file. The response lists every recorded source and whether it exists on disk, and for textures reports before/after dimensions plus a `changed` flag, so a reimport of an identical file is visible as such rather than as a silent no-op."
    return _post("reimport_asset", path=path, sourceFile=source_file or None,
                 sourceFileIndex=source_file_index, forceNewFile=force_new_file, save=save)


@mcp.tool()
def set_texture_settings(path: str, compression_settings: str = "", srgb: bool = None,
                         lod_group: str = "", never_stream: bool = None,
                         mip_gen_settings: str = "", texture_filter: str = "",
                         save: bool = None) -> dict:
    "Set a Texture2D's CompressionSettings / SRGB / LODGroup / NeverStream / MipGenSettings / Filter. Enum values accept the short form or the engine spelling ('UserInterface2D' or 'TC_UserInterface2D'; 'UI' or 'TEXTUREGROUP_UI'; 'NoMipmaps' or 'TMGS_NoMipmaps'; 'Nearest' or 'TF_Nearest'); an unknown value errors with the accepted list and the nearest matches. For UI/shop icons: lod_group='UI', compression_settings='UserInterface2D', mip_gen_settings='NoMipmaps', never_stream=True - world-texture defaults give icons DXT banding, unused mips and streaming pop, which reads as a failed import. Every requested field is read back after the rebuild and any value the engine overruled is an ERROR naming requested-vs-applied, never a silent success. Refuses on a texture with no source data (a stub), because settings cannot make an empty texture render - import_texture with overwrite:true is what that needs."
    return _post("set_texture_settings", path=path,
                 compressionSettings=compression_settings or None, srgb=srgb,
                 lodGroup=lod_group or None, neverStream=never_stream,
                 mipGenSettings=mip_gen_settings or None, filter=texture_filter or None,
                 save=save)


# --------------------------------------------------------------------------
# Asset ICON rendering (MifBridgeThumbnail.cpp). ThumbnailTools::RenderThumbnail
# is fully SYNCHRONOUS - there is no job slot and nothing to poll. Only
# write_thumbnail_texture produces an ASSET; the other three render, preview or
# preflight.
#
# (This block sits ABOVE main()/the __main__ guard on purpose: a tool defined
#  after mcp.run() starts never registers - the spawn_actor_in_pie lesson.)
#
# NOTE the srgb / compression / lod_group / generate_mips defaults are None/""
# on purpose, and it is NOT cosmetic: _post drops None keys, and the C++ refill
# path uses JHasAny to decide whether a setting was SUPPLIED. If the wrapper
# always sent srgb=True/compression="EditorIcon", every overwrite:true refill
# would silently reset an existing stub's deliberate texture settings while
# reporting them as if they had always been that way. height likewise defaults
# to 0 -> `height or None` so the C++ default (height = width) survives.
# --------------------------------------------------------------------------

@mcp.tool()
def thumbnail_capabilities(asset: str = "") -> dict:
    "Preflight for the thumbnail endpoints. With no argument: whether this editor can render at all (canRender, canEverRender, rhiInitialized, thumbnailManager) plus size limits. With an asset path: its class, the UThumbnailRenderer the engine would use (renderer/hasRenderer), whether it already has a custom cached thumbnail, and whether it supports orbit camera control. hasRenderer:false means the Content Browser only shows it a generic class icon and render_thumbnail / write_thumbnail_texture will REFUSE rather than bake a black square - ask here first instead of debugging a failed bake. Also reports whether the ThumbnailGenerator plugin is loaded; MifBridge deliberately does not use it."
    return _post("thumbnail_capabilities", asset=asset or None)


@mcp.tool()
def render_thumbnail(asset: str, width: int = 256, height: int = 0,
                     orbit_pitch: float = None, orbit_yaw: float = None, orbit_zoom: float = None,
                     flush_textures: bool = False, alpha: str = "opaque", name: str = "") -> dict:
    "Render an asset's ICON the way the Content Browser does and write it as a PNG under <ProjectSaved>/MifBridge/Thumbnails. Mutates NO asset. Works on static/skeletal meshes, Blueprints, materials, particle systems, textures - anything with a registered thumbnail renderer (check thumbnail_capabilities). height defaults to width (icons are square); size is clamped to 8..2048. orbit_pitch/orbit_yaw/orbit_zoom aim the engine's own orbit camera (the one a human gets by dragging a Content Browser thumbnail) and are RESTORED afterwards, so the asset is not dirtied. alpha: opaque (default, force A=255) or asRendered - the response's alpha{} block reports min/max/transparent-pixel counts as actually rendered, because these renderers clear to opaque black and a cut-out icon is generally NOT available from this path. flush_textures:true forces full asset-compilation and streaming flushes for a sharp final bake and BLOCKS the whole editor (and this bridge) while it runs - expect read timeouts, use once, not in a loop. USE THIS FIRST to check the framing, then write_thumbnail_texture to bake it - this endpoint writes an image file only, not an asset."
    return _post("render_thumbnail", asset=asset, width=width, height=height or None,
                 orbitPitch=orbit_pitch, orbitYaw=orbit_yaw, orbitZoom=orbit_zoom,
                 flushTextures=flush_textures, alpha=alpha, name=name or None)


@mcp.tool()
def write_thumbnail_texture(asset: str, texture_path: str, width: int = 256, height: int = 0,
                            orbit_pitch: float = None, orbit_yaw: float = None, orbit_zoom: float = None,
                            flush_textures: bool = False, alpha: str = "opaque",
                            srgb: bool = None, compression: str = "", lod_group: str = "",
                            generate_mips: bool = None, overwrite: bool = False, save: bool = True) -> dict:
    "Render an asset's icon and WRITE IT AS A UTexture2D ASSET at texture_path - the endpoint that actually fills an empty icon stub, because a PNG cannot be referenced by a widget. Two modes. CREATE: texture_path does not exist -> new package + UTexture2D (compression defaults to EditorIcon/UserInterface2D uncompressed RGBA, lod_group UI, no mips - the right settings for a UI icon). REFILL: texture_path already exists and overwrite:true -> the EXISTING UTexture2D's source is replaced in place, so its object path and every widget/data-table/material already pointing at it keep working; settings you do NOT pass are left exactly as the stub's author set them. Refuses (without writing anything) if the destination exists and overwrite is false, if it is not a UTexture2D, or if it lives in a cooked package (FTextureSource is stripped at cook). The SOURCE asset may be cooked - cooked meshes render fine. save defaults true and the response is only ok after Source.IsValid(), the source dimensions, the object path and the .uasset on disk have all been re-read. SelfManaged, so `batch` refuses it: N icons is N calls, each fully verified. compression: EditorIcon | UserInterface2D | Default | VectorDisplacementmap | Grayscale. lod_group: UI | World | Character | none."
    return _post("write_thumbnail_texture", asset=asset, texturePath=texture_path,
                 width=width, height=height or None,
                 orbitPitch=orbit_pitch, orbitYaw=orbit_yaw, orbitZoom=orbit_zoom,
                 flushTextures=flush_textures, alpha=alpha, srgb=srgb,
                 compression=compression or None, lodGroup=lod_group or None,
                 generateMips=generate_mips, overwrite=overwrite, save=save)


@mcp.tool()
def set_asset_thumbnail(asset: str, width: int = 256, height: int = 0,
                        orbit_pitch: float = None, orbit_yaw: float = None, orbit_zoom: float = None,
                        flush_textures: bool = False, save: bool = False) -> dict:
    "Set an asset's OWN Content Browser icon - the programmatic form of right-click > Capture Thumbnail. This is package METADATA, not an asset: it cannot be referenced by anything, is stripped at cook, and is a different thing from write_thumbnail_texture. Use it to make a folder of generated assets legible to a human. Refuses on cooked packages. save defaults false, so the thumbnail is cached in memory and the package is left dirty until save_package / save_dirty_packages runs - it is lost if the editor closes without one. Verified by reading the package's thumbnail map back after writing."
    return _post("set_asset_thumbnail", asset=asset, width=width, height=height or None,
                 orbitPitch=orbit_pitch, orbitYaw=orbit_yaw, orbitZoom=orbit_zoom,
                 flushTextures=flush_textures, save=save)



# --------------------------------------------------------------------------
# Console / cvars, and variable pin lists.
# exec_console/get_cvar/set_cvar exist so reconstruction flags (mif.kr.Events,
# mif.kr.LatentResume — both ship default-OFF) can be read and flipped without
# leaving the bridge; "is this a tool bug or a tool setting" was untestable before.
# add_node_pin exists because Switch on Int ships with only a Default pin, a full
# Sequence could not be extended, and Make Array could only size at creation.
# --------------------------------------------------------------------------


@mcp.tool()
def exec_console(command: str) -> dict:
    "Run a console command in the EDITOR and return what it printed. handled=false is normal for cvar assignments - verify with get_cvar."
    return _post("exec_console", command=command)


@mcp.tool()
def get_cvar(name: str) -> dict:
    "Read a console variable: its string/int/float/bool value and help text."
    return _post("get_cvar", name=name)


@mcp.tool()
def set_cvar(name: str, value: str) -> dict:
    "Set a console variable (SetByConsole). Returns before/after/changed; warns if the readback disagrees with what was asked for."
    return _post("set_cvar", name=name, value=value)


@mcp.tool()
def add_node_pin(graph_id: str, node: str, count: int = 1) -> dict:
    "Grow a node's variable pin list by `count`: Sequence (then_N), Make Array/Map/Set ([N]), Switch (case pins), Select, commutative maths ops. Returns addedPins[] and the full pins[]."
    return _post("add_node_pin", graphId=graph_id, node=node, count=count)


# --------------------------------------------------------------------------
# BLENDER backend - bl_* tools. These do NOT reach Unreal: they go over the
# loopback socket to the MifBlender addon (tools/blender-addon/), so they are
# outside the MIF_DECL/MIF_BIND parity set - see the module docstring.
#
# Tool name == addon op name for all of these except bl_status, whose op is
# `ping` (the same one deviation compile_blueprint -> compile already has).
# The op table is the addon's, and it is CHECKABLE rather than asserted:
#   ops_scene.OPS  ping, scene_info, list_objects, object_info,
#                  clear_scene, delete_object, run_python
#   ops_mesh.OPS   import_mesh, export_mesh, select_edges, bevel_edges,
#                  extrude_skirt
# `python tools/parity_check.py` diffs the _blender("...") literals below
# against those two dicts and fails on any drift, both directions. It is the
# bl_* half of the MIF_DECL == MIF_BIND discipline: there is no compiler here,
# so the check has to be a script. It also diffs the KEYS each call site sends
# against that op's reject_unknown set, which is the specific thing that used to
# be wrong - three ops were called that did not exist, and the one shared op was
# sent two params (`selector`, `preserveX`) it refuses.
#
# UNITS. Blender works in metres and Unreal in centimetres: 1 uu = 0.01 Blender
# units, so a 1000 uu road is 10.0 long in Blender. The *_uu arguments here are
# UNREAL units and are sent AS UNREAL UNITS: every geometry op takes both an
# `offset`/`depth` (BU) and an `offsetUU`/`depthUU` (uu) and refuses unless
# exactly one is given, so the conversion happens once, in the addon, against
# the addon's own UU_PER_BU. Dividing by 100 here as well is how you build a
# 15-METRE skirt that passes every ok:true check on the way down.
#
# SELECTORS. The addon's selector grammar is FLAT (boundaryOnly, axis, side,
# tolerance, minAngleDeg, maxAngleDeg, edgeIndices, allEdges) and it rejects
# unknown params rather than ignoring them. These tools take the nested
# `selector` dict because it reads better at a call site, and _bl_selector()
# flattens it - once, in one place - before it goes over the wire. It also
# translates the one spelling that differed (`boundary` -> `boundaryOnly`) and
# refuses an unrecognised key HERE, with the accepted list, rather than letting
# the addon refuse it after a round trip.
#
# TIMEOUT LADDER, and which end owns it. The addon's main-thread job timeout
# defaults to 150s (MifBlender/server.py DEFAULT_JOB_TIMEOUT) and the MCP's work
# read timeout to 180s, deliberately in that order: BLENDER gives up first, so
# the socket carries a real error. Inverted - which it was, at 600s vs 180s -
# the MCP abandons the call, drops the socket, and Blender goes on mutating the
# scene for another seven minutes on behalf of a caller already told it failed.
# Raise one and you must raise the other, keeping the addon below.
#
# THE TRANSPORT IS ONE SERIALISED SOCKET, so a long op blocks the rest. Calls
# that are meant to DIAGNOSE that (bl_status, and mif_mesh_roundtrip's step-0
# probe) pass _lock_timeout and come back naming the op that holds the line and
# for how long; real work queues normally.
#
# Blender must ALREADY be running with the addon enabled - nothing here can
# start it. bl_status is the cheap probe (5s read AND 5s lock, not 180s) and is
# what mif_mesh_roundtrip calls first, before Unreal is asked to write anything.
#
# (This block sits ABOVE main()/the __main__ guard on purpose: a tool defined
#  after mcp.run() starts never registers - the spawn_actor_in_pie lesson.)
# --------------------------------------------------------------------------

class _MifToolError(Exception):
    """A composing tool's own refusal, raised so it cannot be ignored and caught
    at the tool boundary so it is never seen by the MCP client as a traceback.
    Every tool that raises it returns {"ok": False, "error": str(exc)}."""


# Boundary edges along Y, both sides - the two long edges of a road/sidewalk
# tile. Defined once so bl_* and mif_mesh_roundtrip cannot drift apart, and
# spelled the way the ADDON spells it (`boundaryOnly`, ops_mesh._BEVEL_KEYS) so
# the flattener is a rename of nothing.
_MIF_DEFAULT_EDGE_SELECTOR = {"boundaryOnly": True, "axis": "Y", "side": "both",
                              "tolerance": 1e-4}

# The addon's flat selector vocabulary, verbatim. Kept as a tuple rather than a
# set because these are also the kwargs order at every call site below, and a
# reader should be able to line the two up.
_BL_SELECTOR_KEYS = ("boundaryOnly", "axis", "side", "tolerance",
                     "minAngleDeg", "maxAngleDeg", "edgeIndices", "allEdges")

# The ONE historical misspelling, translated rather than silently dropped.
_BL_SELECTOR_ALIASES = {"boundary": "boundaryOnly"}

_VEC3_KEYS = ("x", "y", "z")


def _bl_selector(selector: dict) -> dict:
    """Flatten a nested selector into the addon's flat keys. Never returns None.

    Returns every key in _BL_SELECTOR_KEYS, None where unset - _blender() drops
    the Nones, so the addon sees only what the caller actually asked for.
    """
    if selector is None:
        selector = dict(_MIF_DEFAULT_EDGE_SELECTOR)
    if not isinstance(selector, dict):
        raise _MifToolError(
            f"selector must be an object, got {type(selector).__name__}. It is a small "
            f"declarative predicate, not a script: accepted keys are "
            f"{', '.join(_BL_SELECTOR_KEYS)}.")

    out = {key: None for key in _BL_SELECTOR_KEYS}
    unknown = []
    for key, value in selector.items():
        canon = _BL_SELECTOR_ALIASES.get(key, key)
        if canon not in out:
            unknown.append(key)
            continue
        out[canon] = value
    if unknown:
        raise _MifToolError(
            f"selector has unknown key(s) {', '.join(sorted(unknown))}. Accepted: "
            f"{', '.join(_BL_SELECTOR_KEYS)} (plus 'boundary' as an alias for "
            f"'boundaryOnly'). Refused here rather than at the addon, which would "
            f"reject the whole call after a round trip.")
    if all(value is None for value in out.values()):
        raise _MifToolError(
            "selector is empty and the addon refuses to guess which edges to touch. "
            "Give it boundaryOnly / axis+side / minAngleDeg / maxAngleDeg / edgeIndices, "
            "or allEdges:true to really mean every edge. Omit the argument entirely for "
            f"the default road-tile predicate {_MIF_DEFAULT_EDGE_SELECTOR}.")
    return out


def _bl_preserve_axes(preserve_x: bool):
    """preserve_x -> the addon's preserveAxes/assertAxes pair, or None.

    BOTH, never one. Asserting an axis without preserving it is not a
    configuration, it is a guaranteed failure: the assert measures exactly the
    drift the preserve exists to remove.
    """
    return ["X"] if preserve_x else None


def _vec3(value, what: str) -> list:
    """Normalise a 3-vector that may arrive as {"x":..,"y":..,"z":..} or [x,y,z].

    The two backends genuinely disagree on shape and each is right on its own
    side: the UE plugin emits boundsSizeUU through MifExportVectorJson as an
    OBJECT (MifBridgeExport.cpp), the Blender addon emits boundsLocalSizeUU
    through rnd() as a LIST (ops_common.py). Normalise, do not pick a winner.

    RAISES on any other shape, deliberately. The bug this replaces was an
    isinstance(x, list) test that quietly answered False on the dict, skipped
    the comparison, and let the caller append "fidelity_gate" to its completed
    steps - a verification that reported success without ever running.
    """
    if isinstance(value, dict):
        missing = [k for k in _VEC3_KEYS if k not in value]
        if missing:
            raise _MifToolError(
                f"{what} is an object but is missing the key(s) {', '.join(missing)}: "
                f"{value!r:.200}")
        parts = [value[k] for k in _VEC3_KEYS]
    elif isinstance(value, (list, tuple)):
        if len(value) != 3:
            raise _MifToolError(
                f"{what} is a list of {len(value)} where 3 was expected: {value!r:.200}")
        parts = list(value)
    else:
        raise _MifToolError(
            f"{what} must be a 3-element list or an {{x,y,z}} object, got "
            f"{type(value).__name__}: {value!r:.200}")
    try:
        return [float(v) for v in parts]
    except (TypeError, ValueError) as exc:
        raise _MifToolError(f"{what} has a non-numeric component ({exc}): {value!r:.200}")


def _bl_bounds_uu(obj_info: dict, what: str) -> tuple:
    """(min, max, size) in UNREAL units out of an addon object_info block.

    object_info reports the local bbox in BLENDER units as boundsLocalMinBU /
    boundsLocalMaxBU (3-lists, ops_common.object_info) and only the SIZE in uu.
    The min and max are what a pivot comparison needs - the size alone cannot see
    a mesh that kept its dimensions and moved - so they are converted here
    against the same UU_PER_BU=100 the addon uses.

    RAISES if either is absent. A pivot check that could not read the pivot has
    not passed.
    """
    if not isinstance(obj_info, dict):
        raise _MifToolError(f"{what} is {type(obj_info).__name__}, not an object")
    missing = [k for k in ("boundsLocalMinBU", "boundsLocalMaxBU") if k not in obj_info]
    if missing:
        raise _MifToolError(
            f"{what} is missing {', '.join(missing)}, so the PIVOT cannot be checked - and "
            "'the pivot must not move' is half the tiling constraint, not a nicety. Keys "
            f"present: {sorted(obj_info)}")
    lo = [v * 100.0 for v in _vec3(obj_info["boundsLocalMinBU"], f"{what}.boundsLocalMinBU")]
    hi = [v * 100.0 for v in _vec3(obj_info["boundsLocalMaxBU"], f"{what}.boundsLocalMaxBU")]
    return lo, hi, [hi[i] - lo[i] for i in range(3)]


def _pivot_drift(pre_lo, pre_hi, got_lo, got_hi, axes) -> list:
    """[(axisLetter, 'min'|'max', expected, got, delta)] over the named axes.

    The bbox min and max are measured FROM THE ORIGIN, so comparing both is
    exactly a pivot comparison: a mesh re-centred on import keeps its size and
    moves both, and a size-only check - which is all this pipeline used to do -
    sees nothing at all.
    """
    out = []
    for i in axes:
        for label, pre, got in (("min", pre_lo[i], got_lo[i]), ("max", pre_hi[i], got_hi[i])):
            out.append(("XYZ"[i], label, pre, got, got - pre))
    return out


def _fmt_drift(rows) -> str:
    return "; ".join("%s%s %.4f -> %.4f uu (%+.4f)" % (a, l.rjust(4), p, g, d)
                     for a, l, p, g, d in rows)


# The FStaticMaterial field the two sides can be lined up on. export_asset emits
# `slotName` (MifBridgeExport.cpp, Slot.MaterialSlotName); get_property serialises
# the live FStaticMaterial, whose UPROPERTY is MaterialSlotName. Both spellings
# are accepted, and nothing else is guessed at.
_SLOT_NAME_KEYS = ("slotName", "MaterialSlotName", "materialSlotName",
                   "ImportedMaterialSlotName", "importedMaterialSlotName")


def _slot_names(rows):
    """The ORDERED slot-name sequence out of either side's shape, or None.

    None means "this shape is not one I can read" and the caller must SAY SO.
    Returning [] or falling back to a length comparison would turn an unread
    check into a passed one, which is the defect this whole file argues against.
    """
    if not isinstance(rows, list):
        return None
    names = []
    for row in rows:
        if isinstance(row, str):
            names.append(row)
            continue
        if not isinstance(row, dict):
            return None
        for key in _SLOT_NAME_KEYS:
            if key in row and isinstance(row[key], str):
                names.append(row[key])
                break
        else:
            return None
    return names


@mcp.tool()
def bl_status() -> dict:
    "Health probe for the Blender backend, and the FIRST thing to call before any bl_* work. Returns the addon's op table plus bpy.app.version, background (blender -b has no event loop, so the addon runs jobs inline rather than on a timer) and the process pid. BOUNDED AT BOTH ENDS, 5s each, where every other bl_* tool waits 180s. (1) A short READ timeout: a ping does no bpy work, so silence means Blender's main thread is blocked by a modal operator, an open file browser or a render. (2) A short wait for the transport LOCK: the connection to Blender is one serialised socket, so this tool used to block behind an in-flight bevel for the full work timeout - the one thing you reach for when Blender is wedged was the one thing wedged Blender made unavailable. It now gives up and tells you WHICH op is holding the line and for how long, which is itself the diagnosis. A refused connection (Blender shut, or the addon not enabled) comes back effectively instantly with the install steps in the error. Version matters and is reported for a reason: the io_scene_fbx and bmesh.ops defaults this pipeline relies on were read from 4.4 and have moved between releases before. `pid` is worth reading whenever an edit seems to land nowhere: it identifies WHICH Blender owns the port, and the addon's N-panel prints the same number."
    return _blender("ping", _timeout=BLENDER_PROBE_TIMEOUT,
                    _lock_timeout=BLENDER_PROBE_TIMEOUT)


@mcp.tool()
def bl_scene_info(detail: bool = False) -> dict:
    "Summary of the current Blender scene: objectCount, objectsByType, objects[] (names and types; detail:true swaps in a full object_info each), activeObject, selectedObjects, collections, blendFile, and unitSettings. Read-only. Use it to confirm what bl_import_mesh actually landed, or that a previous run's wreckage is still sitting in the scene - bl_import_mesh with clear_scene defaults to wiping it precisely because a failed run leaves its object behind on purpose, as the debugging artifact. It also warns when unitSettings.scaleLength is not 1.0, which is worth reading before any export: MEASURED on 4.4.0, the same 10 BU cube exported at scaleLength 0.01 reimports at 0.1 BU while UnitScaleFactor in the FBX header stays 1.0 either way - so a scene left at a non-default unit scale silently rescales the whole round trip and only the magnitudes give it away."
    return _blender("scene_info", detail=detail or None)


@mcp.tool()
def bl_list_objects(object_type: str = "") -> dict:
    "List objects in the Blender scene with their types. object_type filters to one Blender type ('MESH', 'EMPTY', 'ARMATURE', ...); omit it for everything. Read-only. An FBX that brought in more than the mesh you wanted - LOD children, a collision shape, an armature - shows up here as extra rows, which is the usual reason a round trip refuses to continue."
    return _blender("list_objects", type=object_type or None)


@mcp.tool()
def bl_object_info(object_name: str) -> dict:
    "Measurements for one Blender object, under an 'object' key: boundsLocalMinBU/MaxBU/SizeBU and boundsLocalSizeUU (the local bbox, already converted - use this one, do NOT multiply dimensionsBU yourself, that folds in object scale), dimensionsBU, locationBU, rotationEulerRad, scale, isIdentityTransform, vert/edge/face/tri counts, materialSlots in order, uvLayers, hasCustomSplitNormals. Read-only. This is the tool that answers 'did the FBX survive the trip': compare object.boundsLocalSizeUU against the boundsSizeUU export_asset reported for the source mesh (that one arrives as an {x,y,z} object, this one as a 3-list), and compare materialSlots against its materialSlots. isIdentityTransform must stay true - if it is not, something moved or rotated the object and the pivot is already lost. There is no matrix_world field; isIdentityTransform is the check that replaces reading one. Compare boundsLocalMinBU/MaxBU (x100) against export_asset's boundsMinUU/boundsMaxUU too, not just the size: the size is blind to a mesh that kept its dimensions and moved, and 'the pivot must not move' is half the tiling constraint. mif_mesh_roundtrip now does exactly that automatically."
    return _blender("object_info", object=object_name)


@mcp.tool()
def bl_import_mesh(file: str, clear_scene: bool = True) -> dict:
    "Import an FBX file into Blender and report what arrived. FBX ONLY - the addon hard-refuses every other extension, OBJ included, because FBX is the only format whose axis and unit round trip with Unreal is verified (UE's OBJ exporter swaps Y/Z, de-indexes to three verts per triangle and writes no normals). Pass NO axis settings anywhere: with use_manual_orientation off the importer reads FrontAxis/UpAxis/CoordAxis out of the file, and an FBX written by Unreal declares Z-up / -Y-front / right-handed / cm, which reverse-maps to Blender's own system and applies an identity conversion - the mesh lands unrotated and 1 uu becomes 0.01 Blender units. The created objects are recovered by diffing bpy.data.objects before and after, because the import operators return nothing. clear_scene defaults TRUE so each run starts from a known scene; set it false to import alongside existing objects. Returns imported[] with a full object_info per object - MORE THAN ONE object means the FBX carried LODs, collision or an armature and the source should be re-exported with level_of_detail:false and collision:false."
    return _blender("import_mesh", file=file, clearScene=clear_scene)


@mcp.tool()
def bl_select_edges(object_name: str, selector: dict = None, max_reported: int = 512) -> dict:
    "Resolve an edge selector against a mesh and report what it matches, WITHOUT modifying anything. Always run this before bl_bevel_edges / bl_extrude_skirt: it runs the addon's SAME selector code, so what it reports is exactly what those two would act on, and a selector that matches zero edges makes both of them REFUSE rather than quietly no-op. selector is a small declarative predicate, not a script - {'boundaryOnly': true, 'axis': 'Y', 'side': 'both', 'tolerance': 1e-4} means 'boundary edges (one linked face) whose BOTH vertices sit within tolerance of the object's min-Y or max-Y', which is exactly the two long edges of a road tile. Accepted keys: boundaryOnly (alias boundary), axis, side ('min'|'max'|'both'), tolerance, minAngleDeg, maxAngleDeg, edgeIndices, allEdges - every criterion supplied is ANDed, and an unrecognised key is refused here with the accepted list rather than after a round trip. Omit selector entirely for the Y-boundary road-tile predicate. Returns count plus the boundary/interior/wire breakdown - a non-zero interiorEdges is why bl_extrude_skirt would refuse - and edgeIndices[] capped at max_reported."
    try:
        sel = _bl_selector(selector)
    except _MifToolError as exc:
        return {"ok": False, "error": str(exc)}
    return _blender("select_edges", object=object_name,
                    boundaryOnly=sel["boundaryOnly"], axis=sel["axis"], side=sel["side"],
                    tolerance=sel["tolerance"], minAngleDeg=sel["minAngleDeg"],
                    maxAngleDeg=sel["maxAngleDeg"], edgeIndices=sel["edgeIndices"],
                    allEdges=sel["allEdges"], maxReported=max_reported)


@mcp.tool()
def bl_bevel_edges(object_name: str, selector: dict = None, offset_uu: float = 15.0,
                   segments: int = 3, profile: float = 0.5, preserve_x: bool = True) -> dict:
    "Round or chamfer the selected edges with bmesh.ops.bevel (NOT bpy.ops.mesh.bevel, which needs an EDIT_MESH context and a real VIEW_3D area and therefore cannot run under blender -b). offset_uu is in UNREAL units and is sent as offsetUU - the addon does the one conversion against its own UU_PER_BU, so nothing here divides by 100. segments=1 gives a flat chamfer, >1 a rounded profile. Two bmesh defaults are overridden for you because the defaults are silently wrong here: affect is forced to EDGES (the default is VERTICES) and material to -1 (the default 0 dumps every new face into slot 0). preserve_x defaults TRUE and is the tiling defence - a bevel also drags the vertices at the X extremities inward, which moves the end-cap seam off exactly +/-500 and shears anything extruded along a spline; it is sent as preserveAxes:['X'] AND assertAxes:['X'] together, never one without the other, so verts near the original min/max X are clamped back and X is then asserted, and a surviving drift REFUSES and leaves the mesh unmodified. The assert is TWO checks: the X SIZE, and seam PLANARITY. The second is not a refinement of the first - MEASURED on 4.4.0, a guards-off bevel of the Y edge loops on a 1000x300x50 uu tile left 24 of 32 verts 15 uu inside the X seam and still reported sizeDeltaUU [0,0,0], because the surviving corner verts pin the bounding box. A size check cannot ever see that. selector takes the same keys as bl_select_edges. Returns before/after sizes, offSeamVerts and a per-axis seamPlanarity block (reported for X, Y and Z whatever is guarded - guards decide what FAILS, never what is looked at), plus objectAfter. Prefer bl_extrude_skirt when a skirt is what you actually want: it moves nothing in X or Y by construction rather than by clamping."
    try:
        sel = _bl_selector(selector)
    except _MifToolError as exc:
        return {"ok": False, "error": str(exc)}
    pres = _bl_preserve_axes(preserve_x)
    return _blender("bevel_edges", object=object_name,
                    boundaryOnly=sel["boundaryOnly"], axis=sel["axis"], side=sel["side"],
                    tolerance=sel["tolerance"], minAngleDeg=sel["minAngleDeg"],
                    maxAngleDeg=sel["maxAngleDeg"], edgeIndices=sel["edgeIndices"],
                    allEdges=sel["allEdges"],
                    offsetUU=offset_uu, segments=segments, profile=profile,
                    preserveAxes=pres, assertAxes=pres)


@mcp.tool()
def bl_extrude_skirt(object_name: str, selector: dict = None, depth_uu: float = 15.0,
                     preserve_x: bool = True, direction: str = "down",
                     flip_normals: bool = False) -> dict:
    "Extrude the selected boundary edge loops straight DOWN by depth_uu, forming a skirt - the fix for a flat-edged tile that hovers where the terrain falls away. depth_uu is in UNREAL units and is sent as depthUU; the addon does the one conversion. This is the SAFE edit for anything tiled along a spline: the extrude duplicates the loop IN PLACE and the only follow-up is a translate whose X and Y components are literal zeros, so the seam planes and the tile length are untouched by construction rather than by clamping. Verified on Blender 4.4.0 against a 10x3 BU tile: dX 0.0, dY 0.0, dZ = depth, zero verts moved off the X seam planes. The op REFUSES if the selection contains a non-boundary edge, because extruding an interior edge splits the mesh instead of skirting it - run bl_select_edges first and check interiorEdges. preserve_x still sends preserveAxes+assertAxes as a belt (it should always report 0 snapped verts and offSeamVerts 0 here; anything else means the selection was not a clean boundary loop). The same two-part assert as bl_bevel_edges applies - X size AND seam planarity - and the per-axis seamPlanarity block comes back for X, Y and Z whether or not they are guarded. direction is 'down' or 'up'; there is no sideways option by design. flip_normals inverts the new side faces - the default False gave outward-facing normals on the verified case. NOTE the new skirt faces carry no meaningful UVs, so expect stretched texturing until they are authored; nothing in this pipeline does that for you."
    try:
        sel = _bl_selector(selector)
    except _MifToolError as exc:
        return {"ok": False, "error": str(exc)}
    pres = _bl_preserve_axes(preserve_x)
    return _blender("extrude_skirt", object=object_name,
                    boundaryOnly=sel["boundaryOnly"], axis=sel["axis"], side=sel["side"],
                    tolerance=sel["tolerance"], minAngleDeg=sel["minAngleDeg"],
                    maxAngleDeg=sel["maxAngleDeg"], edgeIndices=sel["edgeIndices"],
                    allEdges=sel["allEdges"],
                    depthUU=depth_uu, direction=direction, flipNormals=flip_normals,
                    preserveAxes=pres, assertAxes=pres)


@mcp.tool()
def bl_export_mesh(object_name: str, file: str) -> dict:
    "Export one Blender object to FBX for reimport into Unreal. The two axis arguments are the whole ballgame and are set for you: axis_up='Z', axis_forward='Y', which are NOT the operator defaults ('Y' / '-Z', the Maya convention) - the defaults produce a mesh that arrives in Unreal rotated. Unit scale is baked (apply_unit_scale with FBX_SCALE_NONE) so the file carries centimetre-magnitude numbers in a cm-declared scene, which is what Unreal reads back whether or not it converts units; bake_space_transform stays OFF (experimental). Only the named object is exported, MESH types only. The response re-stats the path and reports fileExists and fileSizeBytes, so a silent zero-byte write is a failure rather than an ok. NEVER call transform_apply anywhere in this pipeline: one 'Apply All Transforms' bakes the round trip into the mesh and every spline instance shears."
    return _blender("export_mesh", object=object_name, file=file)


@mcp.tool()
def bl_delete_object(object_name: str) -> dict:
    "Delete one object from the Blender scene by name. Use it to clean up a specific import; bl_clear_scene is the whole-scene form. Deleting is not undo-able through this bridge."
    return _blender("delete_object", object=object_name)


@mcp.tool()
def bl_clear_scene() -> dict:
    "Delete every object in the Blender scene. bl_import_mesh already does this by default, so the usual reason to call it directly is to inspect a failed run's leftovers first and then reset. Not undo-able through this bridge."
    return _blender("clear_scene")


@mcp.tool()
def bl_run_python(code: str = "", file: str = "", return_locals: bool = False) -> dict:
    "Execute Python inside Blender, on the main thread, so bpy is safe to touch. The escape hatch for everything MifBlender has no first-class op for - prefer a first-class op whenever one exists, because those have checked parameters and this does not. Pass EITHER code (a string) OR file (a path to a .py that Blender reads) - passing both is an error, as is passing neither. CONTRACT: whatever your code assigns to a module-level name `result` comes back in the response, coerced to JSON-safe values; a script that returns nothing useful is usually one that forgot to assign it. stdout and stderr are captured and returned. bpy, math, bmesh and mathutils are pre-imported into the namespace. An exception comes back as ok:false with the traceback and does NOT kill the connection, so a broken snippet is recoverable. GATED: the addon preference 'allow_run_python' must be ticked in Blender (Edit > Preferences > Add-ons > MifBlender) or every call refuses with instructions - that gate is the safety model, since this runs with Blender's full privileges, has no sandbox and no undo. It also holds the single serialised transport socket for its whole run, so an infinite loop here wedges Blender AND every other bl_* tool; bl_status is bounded at 5s precisely so it can still tell you that. Keep snippets short."
    return _blender("run_python", code=code or None, file=file or None,
                    returnLocals=return_locals or None)


# --------------------------------------------------------------------------
# GENERATION - local text/image -> 3D through ComfyUI, hosted in the addon.
#
# The chain is Flux.1 (prompt -> reference image) -> Hunyuan3D-2 shape (image ->
# untextured mesh) -> Hunyuan3D-2 paint (mesh -> PBR textures baked from
# multiview renders) -> imported into the open Blender scene. Nothing here calls
# a paid API and nothing leaves the machine; it needs a local ComfyUI, by
# default at 127.0.0.1:8188, with those custom nodes installed.
#
# It lives in the addon rather than a mod's tools/ directory because Blender is
# where the result gets inspected and fixed, so the generator belongs on the same
# side of the socket as the mesh ops that clean it up.
#
# ALWAYS call bl_gen_status first. These are long jobs - the defaults run to 600s
# for an image and 3600s for a full asset - and a missing checkpoint should be
# found before an hour of GPU time, not after.
# --------------------------------------------------------------------------


@mcp.tool()
def bl_gen_status(host: str = "") -> dict:
    "Is the local generator usable, and what is installed. FIRST call before any bl_gen_* work: it reports whether ComfyUI is reachable (default 127.0.0.1:8188, override with host) and which checkpoints and custom nodes are present. The generation ops validate their node inputs against ComfyUI's own /object_info rather than hardcoding them, because custom nodes change between commits and a workflow built on stale assumptions fails deep inside the run with a KeyError on a tensor - long after the interesting part started. This tool is how you find a missing Flux or Hunyuan3D checkpoint in one second instead of an hour into a job."
    return _blender("gen_status", host=host or None)


@mcp.tool()
def bl_gen_image(prompt: str, seed: int = 0, variant: str = "schnell", width: int = 1024,
                 height: int = 1024, steps: int = None, host: str = "",
                 timeout: int = 600) -> dict:
    "Prompt -> reference image via Flux.1, left in ComfyUI's output folder. Stage ONE of the chain; the returned image name is what bl_gen_mesh consumes. variant selects the Flux model ('schnell' is the fast default; 'dev' is slower and follows the prompt harder), and steps defaults to whatever suits the variant, so leave it alone unless you are deliberately trading quality for time. seed=0 means random - set it to a fixed number when you want to iterate on the same composition while changing something else. This is the cheap stage: get the reference image right here before spending shape and paint time on it, because every later stage inherits its framing and its mistakes."
    return _blender("gen_image", prompt=prompt, seed=seed or None, variant=variant or None,
                    width=width, height=height, steps=steps, host=host or None,
                    timeout=timeout, _timeout=float(timeout) + 60.0)


@mcp.tool()
def bl_gen_mesh(image: str = "", image_path: str = "", prefix: str = "MifGen/mesh",
                name: str = "", seed: int = 0, steps: int = 30, octree: int = 512,
                guidance: float = 5.0, import_result: bool = True, host: str = "",
                timeout: int = 1800) -> dict:
    "Reference image -> untextured mesh via the Hunyuan3D-2 shape DiT. Stage TWO. Accepts EITHER image (a ComfyUI image ref, normally the name bl_gen_image returned) OR image_path (a local file). octree controls the reconstruction resolution - 512 is the default, higher costs time and memory for detail that a game asset often will not show. import_result defaults true so the mesh lands in the open Blender scene ready for bl_object_info and the mesh ops. NOTE THE OUTPUT IS BARE GEOMETRY: the shape DiT returns no materials, and a bare mesh needs a human to author them before it is worth anything, which is what bl_gen_texture exists to avoid. Long job - default 1800s."
    return _blender("gen_mesh", image=image or None, imagePath=image_path or None,
                    prefix=prefix or None, name=name or None, seed=seed or None,
                    steps=steps, octree=octree, guidance=guidance,
                    importResult=import_result, host=host or None,
                    timeout=timeout, _timeout=float(timeout) + 60.0)


@mcp.tool()
def bl_gen_texture(mesh_path: str, image: str = "", image_path: str = "",
                   prefix: str = "MifGen/mesh", name: str = "", seed: int = 0,
                   steps: int = 15, view_size: int = 512, import_result: bool = True,
                   host: str = "", timeout: int = 2400) -> dict:
    "Existing mesh + reference image -> PBR textures baked on, via the Hunyuan3D-2 paint path (delight -> uv wrap -> multiview render -> sample -> bake). Stage THREE, and the stage that turns a generation into something droppable into a level. mesh_path is REQUIRED and is the mesh to paint; image/image_path supply the appearance reference, normally the same one the shape came from. view_size is the multiview render resolution. Long job - default 2400s, longer than the shape stage because it renders and samples several views before baking."
    return _blender("gen_texture", meshPath=mesh_path, image=image or None,
                    imagePath=image_path or None, prefix=prefix or None, name=name or None,
                    seed=seed or None, steps=steps, viewSize=view_size,
                    importResult=import_result, host=host or None,
                    timeout=timeout, _timeout=float(timeout) + 60.0)


@mcp.tool()
def bl_gen_asset(prompt: str, name: str = "", seed: int = 0, variant: str = "schnell",
                 texture: bool = True, width: int = 1024, height: int = 1024,
                 shape_steps: int = 30, texture_steps: int = 15, octree: int = 512,
                 guidance: float = 5.0, import_result: bool = True, host: str = "",
                 timeout: int = 3600) -> dict:
    "Prompt -> reference image -> mesh -> PBR texture -> imported into the scene. THE ONE CALL that produces something usable: it sequences bl_gen_image, bl_gen_mesh and bl_gen_texture and hands back the finished object. Set texture=false to stop at geometry when you only want the silhouette. name prefixes the ComfyUI outputs so a run's artifacts stay identifiable; seed=0 is random, so fix it to reproduce a result. shape_steps/texture_steps/octree/guidance tune the individual stages and are worth leaving alone until a default disappoints. THIS IS THE LONGEST JOB IN THE TOOLSET - default 3600s, and it holds the single Blender transport socket for the whole run, so every other bl_* tool blocks behind it (bl_status will tell you which op is holding the line and for how long). Call bl_gen_status first."
    return _blender("gen_asset", prompt=prompt, name=name or None, seed=seed or None,
                    variant=variant or None, texture=texture, width=width, height=height,
                    shapeSteps=shape_steps, textureSteps=texture_steps, octree=octree,
                    guidance=guidance, importResult=import_result, host=host or None,
                    timeout=timeout, _timeout=float(timeout) + 60.0)


# --------------------------------------------------------------------------
# CROSS-BACKEND - mif_* tools. The ONLY tools in this file that contain logic:
# every other one is a single _post/_blender passthrough. A mif_* tool owns no
# endpoint on either backend; it sequences calls to both and enforces the
# assertions that make the sequence trustworthy.
#
# (This block sits ABOVE main()/the __main__ guard on purpose: a tool defined
#  after mcp.run() starts never registers - the spawn_actor_in_pie lesson.)
# --------------------------------------------------------------------------

# _MIF_DEFAULT_EDGE_SELECTOR, _bl_selector and _bl_preserve_axes live up in the
# bl_* block, beside the tools that share them - defined once so bl_* and
# mif_mesh_roundtrip cannot drift apart.


@mcp.tool()
def mif_mesh_roundtrip(asset: str, edit: str = "extrude_skirt", destination: str = "",
                       name: str = "", depth_uu: float = 15.0, offset_uu: float = 15.0,
                       segments: int = 3, selector: dict = None, preserve_x: bool = True,
                       assert_bounds: bool = True, tolerance_uu: float = 0.01,
                       dry_run: bool = False, repoint: list = None,
                       repoint_property: str = "SidewalkMesh",
                       keep_intermediates: bool = True) -> dict:
    "Unreal -> Blender -> Unreal in one call: export a mesh, edit it, reimport it as a NEW asset, and optionally repoint the properties that referenced the original. edit = extrude_skirt | bevel_edges | none ('none' is the no-op round trip, which is how you PROVE the FBX axis/scale trip is lossless before trusting any geometry change to it). RUN dry_run:true FIRST - it exports, imports into Blender, measures, and stops, writing nothing and reimporting nothing. Steps, each gated: (0) bl_status, so a shut Blender fails in seconds BEFORE Unreal writes a file; (1) export_asset, keeping its mesh block as the pre-image; (2) the pre-image SHAPE check - export_asset's mesh.boundsSizeUU arrives as an {x,y,z} object and is normalised here, and a missing or mis-shaped one ABORTS rather than being skipped, because every later assert measures against it; (3) bl_import_mesh, which must yield exactly one object; (4) the FIDELITY GATE - the Blender object's boundsLocalSizeUU must match the exported boundsSizeUU, its boundsLocalMin/MaxBU must match the exported boundsMin/MaxUU (that is the PIVOT check: size alone cannot see a mesh that was silently re-centred, because min and max are measured from the origin), and isIdentityTransform must be true. Any mismatch ABORTS - it means the axis, unit or pivot assumption is wrong and everything downstream would be built on it. FAIL-CLOSED: if a measurement is absent it aborts too, and it never appends itself to completed[] without having actually compared numbers; (5) the edit; (7) the tiling assert against the pre-image - X min AND X max, so a tile that kept its length and slid along X fails here where a length-only check passes, plus isIdentityTransform again. This is what stops a sheared spline tile from ever reaching the editor, and it likewise aborts if it cannot measure (pass assert_bounds:false to opt out explicitly - that is recorded as a warning and the step is NOT reported as completed). Y and Z bbox movement is reported and warned on, never asserted: Z growing IS the skirt; (8) import_asset into destination; (9) a material-slot ORDER check comparing the two slotName SEQUENCES, which WARNS rather than aborts (the mesh is valid, the assignment may not be - a human decides) and says plainly when it could not read one of the shapes instead of quietly comparing lengths; (10) set_property per repoint target. Any abort returns ok:false with the step name, what completed, and the artifacts, and it does NOT roll Blender back: the broken object is left in the scene and both FBX files on disk on purpose, as the debugging evidence. depth_uu/offset_uu/tolerance_uu are UNREAL units throughout and are sent as UNREAL units - the addon owns the one conversion. selector takes the flat keys documented on bl_select_edges. repoint takes object paths (e.g. the four BP_SplineSidewalk instances) and writes repoint_property on each; a partial failure there still reports the successful ones, because the asset really was imported."

    steps: list = []
    artifacts: dict = {}
    warnings: list = []

    def _abort(step: str, error: str, **extra) -> dict:
        out = {"ok": False, "step": step, "error": error,
               "completed": steps, "artifacts": artifacts, "warnings": warnings}
        out.update(extra)
        return out

    # ---- validate everything BEFORE the first side effect ----
    if edit not in ("extrude_skirt", "bevel_edges", "none"):
        return _abort("validate", f"edit must be extrude_skirt | bevel_edges | none, got {edit!r}")
    if not dry_run and not destination:
        return _abort("validate", "destination is required (a /Game/... FOLDER, e.g. "
                                  "/Game/MODS/BotanistExpansion_p/Meshes) unless dry_run:true")
    if repoint and dry_run:
        return _abort("validate", "repoint and dry_run are contradictory: a dry run imports nothing, "
                                  "so there is no new asset to point at")
    # Flatten the selector HERE, before the first side effect: an unknown key is
    # a caller mistake, and finding it out after Unreal has written an FBX and
    # Blender has imported it wastes both.
    try:
        sel = _bl_selector(selector)
    except _MifToolError as exc:
        return _abort("validate", str(exc))
    pres = _bl_preserve_axes(preserve_x)

    # ---- 0. Blender health, before Unreal is asked to write anything ----
    # Bounded on the lock as well as the read, for the same reason bl_status is:
    # if another Blender op is already in flight this should say so in seconds,
    # not queue behind it for three minutes and only then start the round trip.
    probe = _blender("ping", _timeout=BLENDER_PROBE_TIMEOUT,
                     _lock_timeout=BLENDER_PROBE_TIMEOUT)
    if not probe.get("ok"):
        return _abort("blender_probe", probe.get("error", "bl_status failed"), probe=probe)
    artifacts["blender"] = {k: probe.get(k) for k in
                            ("blenderVersionString", "blenderVersion", "addonVersion",
                             "background", "pid") if k in probe}
    steps.append("blender_probe")

    # ---- 1. export out of Unreal ----
    exported = _post("export_asset", asset=asset, format="FBX", overwrite=True)
    if not exported.get("ok"):
        return _abort("export_asset", exported.get("error", "export_asset failed"), response=exported)
    src_fbx = exported.get("file")
    if not src_fbx:
        return _abort("export_asset", "export_asset reported ok but no file path", response=exported)
    artifacts["sourceFbx"] = src_fbx
    steps.append("export_asset")

    # ---- 2. the pre-image every later assert is measured against ----
    # FAIL-CLOSED. There is no "measure nothing and carry on" branch here any
    # more: this tool's entire value is the asserts, and an assert that cannot
    # find its input has not passed, it has not run.
    pre = exported.get("mesh") or {}
    pre_slots = pre.get("materialSlots")
    # MIN and MAX, not just size. The size alone cannot see a mesh that kept its
    # dimensions and moved, and "the pivot must not move" is half the tiling
    # constraint. Both ends have always reported these; nothing read them.
    missing = [k for k in ("boundsSizeUU", "boundsMinUU", "boundsMaxUU") if k not in pre]
    if missing:
        return _abort("pre_image",
                      f"export_asset returned no mesh.{', mesh.'.join(missing)}, so the fidelity "
                      "gate, the X-length assert and the pivot check have nothing to measure "
                      "against and this run cannot be verified. That block is emitted for "
                      "UStaticMesh only - if this asset is not a static mesh, mif_mesh_roundtrip "
                      f"is the wrong tool for it. Response mesh block: {pre!r:.400}",
                      response=exported)
    try:
        pre_size = _vec3(pre["boundsSizeUU"], "export_asset mesh.boundsSizeUU")
        pre_min = _vec3(pre["boundsMinUU"], "export_asset mesh.boundsMinUU")
        pre_max = _vec3(pre["boundsMaxUU"], "export_asset mesh.boundsMaxUU")
    except _MifToolError as exc:
        return _abort("pre_image", str(exc), response=exported)

    # UStaticMesh::GetBoundingBox() is the EXTENDED bounds, so on a mesh with a
    # non-zero bounds extension the pre-image describes a box LARGER than the
    # geometry Blender will measure. Recoverable exactly, and the C++ emits what
    # is needed to do it:
    #   VERIFIED, D:/UE532/.../Runtime/Engine/Private/StaticMesh.cpp:5494-5501
    #   (UStaticMesh::CalculateExtendedBounds)  Min -= NegativeBoundsExtension
    #                                           Max += PositiveBoundsExtension
    # so geometry min = reported min + Neg, geometry max = reported max - Pos.
    # Without this the fidelity gate aborts on a true positive with a misleading
    # "the FBX axis or unit assumption is WRONG".
    has_ext = [k for k in ("boundsExtensionPositiveUU", "boundsExtensionNegativeUU") if k in pre]
    if len(has_ext) == 1:
        return _abort("pre_image",
                      f"export_asset reported mesh.{has_ext[0]} without its counterpart. The two "
                      "are emitted together or not at all, so the pre-image cannot be corrected "
                      "for the bounds extension and every assert below it would be measuring an "
                      "inflated box.", response=exported)
    if has_ext:
        try:
            pos_ext = _vec3(pre["boundsExtensionPositiveUU"], "mesh.boundsExtensionPositiveUU")
            neg_ext = _vec3(pre["boundsExtensionNegativeUU"], "mesh.boundsExtensionNegativeUU")
        except _MifToolError as exc:
            return _abort("pre_image", str(exc), response=exported)
        pre_min = [pre_min[i] + neg_ext[i] for i in range(3)]
        pre_max = [pre_max[i] - pos_ext[i] for i in range(3)]
        pre_size = [pre_max[i] - pre_min[i] for i in range(3)]
        warnings.append(
            f"this mesh has a non-zero bounds extension (positive {pos_ext}, negative "
            f"{neg_ext} uu), so export_asset's boundsMin/Max/SizeUU describe a box larger "
            "than the geometry. The pre-image has been corrected back to the geometry box "
            "(StaticMesh.cpp:5494-5501: Min -= Negative, Max += Positive) and every assert "
            "below uses the CORRECTED numbers.")
        artifacts["preImageDeExtended"] = True

    artifacts["preSizeUU"] = pre_size
    artifacts["preMinUU"] = pre_min
    artifacts["preMaxUU"] = pre_max
    steps.append("pre_image")

    # An FBX stores float32 positions, so an absolute 0.01 uu tolerance would
    # false-positive on a 1000 uu mesh. Widen to 1e-4 of the largest dimension.
    tol = max(tolerance_uu, 1e-4 * max(abs(v) for v in pre_size))

    # ---- 3. into Blender ----
    imported = _blender("import_mesh", file=src_fbx, clearScene=True)
    if not imported.get("ok"):
        return _abort("bl_import_mesh", imported.get("error", "import_mesh failed"), response=imported)
    objs = imported.get("imported") or []
    if len(objs) != 1:
        return _abort("bl_import_mesh",
                      f"expected exactly 1 mesh object from {src_fbx}, got {len(objs)}. LODs, a "
                      "collision shape or an armature came along - re-export with "
                      "level_of_detail:false and collision:false.", imported=objs)
    first = objs[0]
    obj_name = first.get("name") if isinstance(first, dict) else first
    if not obj_name:
        return _abort("bl_import_mesh", "imported object has no name", imported=objs)
    artifacts["blenderObject"] = obj_name
    steps.append("bl_import_mesh")

    # ---- 4. FIDELITY GATE - is the axis/scale assumption actually true? ----
    # object_info nests its payload under "object" and the field is
    # boundsLocalSizeUU (already converted, ops_common.py). Reading a
    # top-level "dimensions" that no version of the addon ever emitted is what
    # made this gate a silent no-op that still reported itself completed.
    before = _blender("object_info", object=obj_name)
    if not before.get("ok"):
        return _abort("fidelity_gate", before.get("error", "object_info failed"), response=before)
    artifacts["blenderBeforeEdit"] = before
    before_obj = before.get("object")
    if not isinstance(before_obj, dict) or "boundsLocalSizeUU" not in before_obj:
        return _abort("fidelity_gate",
                      "object_info returned no object.boundsLocalSizeUU for "
                      f"'{obj_name}', so the axis/scale assumption cannot be checked. The gate "
                      "ABORTS rather than skipping: an unmeasured round trip is not a passed one. "
                      f"Keys present: {sorted(before_obj) if isinstance(before_obj, dict) else type(before_obj).__name__}",
                      response=before)
    try:
        got_uu = _vec3(before_obj["boundsLocalSizeUU"], "object_info object.boundsLocalSizeUU")
        got_min, got_max, _ = _bl_bounds_uu(before_obj, "object_info object")
    except _MifToolError as exc:
        return _abort("fidelity_gate", str(exc), response=before)

    drift = [abs(g - p) for g, p in zip(got_uu, pre_size)]
    if max(drift) > tol:
        return _abort("fidelity_gate",
                      f"Blender measures {got_uu} uu where Unreal exported {pre_size} uu "
                      f"(drift {drift}, tolerance {tol}). The FBX axis or unit assumption is "
                      "WRONG, so nothing downstream can be trusted - do not work around this in "
                      "Blender, fix the export settings. bl_scene_info reports the scene unit "
                      "scale, which is one measured way to get exactly this.",
                      blenderUU=got_uu, exportedUU=pre_size)

    # ---- 4b. PIVOT. The other half of the tiling constraint, and until now the
    # half nothing checked. Size survives a mesh that was silently re-centred on
    # import; min and max do not, because they are measured FROM THE ORIGIN.
    #
    # The two sides ARE comparable, which is worth having checked rather than
    # assumed. VERIFIED in D:/UE532: UStaticMesh::CalculateExtendedBounds
    # (StaticMesh.cpp:5478-5490) prefers CachedMeshDescriptionBounds under
    # WITH_EDITOR, and that value is cached for LOD 0 ONLY (:5108-5111) - so in
    # the editor, where export_asset runs, the reported box is LOD0's, which is
    # the LOD the FBX carries and the one Blender measures. If the mesh
    # description is not cached it falls back to GetRenderData()->Bounds, built
    # from the welded render buffer; that is the case where a small unexplained
    # drift here would be the pipeline's fault rather than the mesh's.
    pivot = _pivot_drift(pre_min, pre_max, got_min, got_max, (0, 1, 2))
    off = [row for row in pivot if abs(row[4]) > tol]
    if off:
        return _abort("fidelity_gate",
                      f"the PIVOT moved on the way into Blender: {_fmt_drift(off)} (tolerance "
                      f"{tol} uu). The bounding box is the right SIZE, so a size-only check would "
                      "have passed this - but the geometry has shifted relative to the origin, "
                      "and every instance of a spline tile is placed by its origin. Nothing was "
                      "edited. This is an import-settings problem, not something to nudge back in "
                      "Blender.",
                      exportedMinUU=pre_min, exportedMaxUU=pre_max,
                      blenderMinUU=got_min, blenderMaxUU=got_max)

    # The object TRANSFORM must be identity too: a bbox is local-space, so a
    # rotated or offset object has a perfect local box and a wrong world pivot.
    # object_info has always computed this and nothing has ever read it.
    if before_obj.get("isIdentityTransform") is not True:
        return _abort("fidelity_gate",
                      f"Blender object '{obj_name}' does NOT have an identity transform "
                      f"(location {before_obj.get('locationBU')} BU, rotation "
                      f"{before_obj.get('rotationEulerRad')} rad, scale {before_obj.get('scale')}). "
                      "The local bounding box is measured in the object's own space, so it looks "
                      "correct while the world pivot is not - and the export writes the object "
                      "transform into the FBX. Do NOT fix this with transform_apply: that bakes "
                      "the round trip into the mesh and shears every spline instance. Fix the "
                      "import.", objectInfo=before_obj)

    artifacts["fidelityDriftUU"] = drift
    artifacts["pivotDriftUU"] = [[a, l, round(d, 6)] for a, l, _p, _g, d in pivot]
    steps.append("fidelity_gate")

    if dry_run:
        return {"ok": True, "dryRun": True, "completed": steps, "artifacts": artifacts,
                "warnings": warnings, "preImage": pre, "blenderBeforeEdit": before,
                "note": "stopped after the fidelity gate: nothing was edited, exported or imported."}

    # ---- 5. the edit ----
    # The selector goes over the wire FLAT, one key per kwarg, and the *_uu
    # values go over as *UU. Both are deliberate and both are checked by
    # tools/parity_check.py against the addon's reject_unknown sets.
    if edit == "extrude_skirt":
        edited = _blender("extrude_skirt", object=obj_name,
                          boundaryOnly=sel["boundaryOnly"], axis=sel["axis"], side=sel["side"],
                          tolerance=sel["tolerance"], minAngleDeg=sel["minAngleDeg"],
                          maxAngleDeg=sel["maxAngleDeg"], edgeIndices=sel["edgeIndices"],
                          allEdges=sel["allEdges"],
                          depthUU=depth_uu, preserveAxes=pres, assertAxes=pres)
    elif edit == "bevel_edges":
        edited = _blender("bevel_edges", object=obj_name,
                          boundaryOnly=sel["boundaryOnly"], axis=sel["axis"], side=sel["side"],
                          tolerance=sel["tolerance"], minAngleDeg=sel["minAngleDeg"],
                          maxAngleDeg=sel["maxAngleDeg"], edgeIndices=sel["edgeIndices"],
                          allEdges=sel["allEdges"],
                          offsetUU=offset_uu, segments=segments,
                          preserveAxes=pres, assertAxes=pres)
    else:
        edited = {"ok": True, "skipped": "edit:none"}
    if not edited.get("ok"):
        return _abort(f"bl_{edit}", edited.get("error", f"{edit} failed"), response=edited)
    artifacts["edit"] = edited
    steps.append(f"bl_{edit}")

    # ---- 6. back out of Blender ----
    out_fbx = os.path.splitext(src_fbx)[0] + "_edited.fbx"
    bl_out = _blender("export_mesh", object=obj_name, file=out_fbx)
    if not bl_out.get("ok"):
        return _abort("bl_export_mesh", bl_out.get("error", "export_mesh failed"), response=bl_out)
    if not bl_out.get("fileExists") or not (bl_out.get("fileSizeBytes") or 0) > 0:
        return _abort("bl_export_mesh",
                      f"Blender reported ok but {out_fbx} is missing or empty "
                      f"(exists={bl_out.get('fileExists')}, bytes={bl_out.get('fileSizeBytes')})",
                      response=bl_out)
    artifacts["editedFbx"] = out_fbx
    steps.append("bl_export_mesh")

    # ---- 7. the tiling assert, BEFORE anything reaches the editor ----
    # Two things, not one, because the tiling constraint is two things: the X
    # LENGTH must be unchanged AND the PIVOT must not have moved. Asserting the
    # X min and the X max covers both - they are measured from the origin, so
    # equal min and equal max implies equal length, and a tile that kept its
    # length while sliding along X fails here where a length check passes.
    #
    # Same fail-closed rule as the fidelity gate: if it cannot measure, it
    # aborts, because "could not check" and "checked and it is fine" must never
    # produce the same completed[] entry.
    after = _blender("object_info", object=obj_name)
    artifacts["blenderAfterEdit"] = after
    if not assert_bounds:
        warnings.append("assert_bounds:false - the X-length AND pivot tiling asserts were NOT run. "
                        "Nothing has verified that the tile still spans "
                        f"{pre_min[0]} to {pre_max[0]} uu along X, and a spline instancing it will "
                        "shear if it does not. 'bounds_assert' is deliberately absent from "
                        "completed[].")
    else:
        if not after.get("ok"):
            return _abort("bounds_assert",
                          f"object_info failed after the edit: {after.get('error', 'no error given')}. "
                          "The X length and pivot are therefore UNVERIFIED and the mesh was NOT "
                          f"imported. The edited FBX is kept at {out_fbx} and the object is still "
                          "in Blender.",
                          response=after)
        after_obj = after.get("object")
        if not isinstance(after_obj, dict) or "boundsLocalSizeUU" not in after_obj:
            return _abort("bounds_assert",
                          "object_info returned no object.boundsLocalSizeUU after the edit, so the "
                          "X-length assert has nothing to measure and the mesh was NOT imported. "
                          f"Keys present: {sorted(after_obj) if isinstance(after_obj, dict) else type(after_obj).__name__}",
                          response=after)
        try:
            post_uu = _vec3(after_obj["boundsLocalSizeUU"], "object_info object.boundsLocalSizeUU")
            post_min, post_max, _ = _bl_bounds_uu(after_obj, "object_info object (after the edit)")
        except _MifToolError as exc:
            return _abort("bounds_assert", str(exc), response=after)

        # X only. Y is at the modeller's discretion and Z is the whole point of a
        # skirt, so both are REPORTED and warned on rather than asserted.
        x_off = [row for row in _pivot_drift(pre_min, pre_max, post_min, post_max, (0,))
                 if abs(row[4]) > tol]
        if x_off:
            return _abort("bounds_assert",
                          f"the X seam moved: {_fmt_drift(x_off)} (tolerance {tol} uu). Spline "
                          "tiling would shear, so the mesh was NOT imported. Note this catches a "
                          "tile that kept its LENGTH and slid along X, which a length-only check "
                          f"reports as clean. The edited FBX is kept at {out_fbx} and the object "
                          "is still in Blender for inspection.",
                          exportedMinUU=pre_min, exportedMaxUU=pre_max,
                          blenderMinUU=post_min, blenderMaxUU=post_max)
        if after_obj.get("isIdentityTransform") is not True:
            return _abort("bounds_assert",
                          f"the edit left Blender object '{obj_name}' with a NON-identity transform "
                          f"(location {after_obj.get('locationBU')} BU, rotation "
                          f"{after_obj.get('rotationEulerRad')} rad, scale {after_obj.get('scale')}). "
                          "The local bounding box above is measured in the object's own space, so "
                          "it passed while the pivot did not. The mesh was NOT imported. No op in "
                          "this addon touches the object transform, so something else in the scene "
                          "did.", objectInfo=after_obj)

        yz_off = [row for row in _pivot_drift(pre_min, pre_max, post_min, post_max, (1, 2))
                  if abs(row[4]) > tol]
        if yz_off:
            warnings.append(
                f"the bounding box moved on Y/Z: {_fmt_drift(yz_off)}. For edit='{edit}' that is "
                "EXPECTED on Z (a skirt is exactly a downward extension of the box) and is not "
                "asserted. On Y it means the edit reached geometry it should not have - worth "
                "reading before this ships.")

        artifacts["postEditSizeUU"] = post_uu
        artifacts["postEditMinUU"] = post_min
        artifacts["postEditMaxUU"] = post_max
        steps.append("bounds_assert")

    # ---- 8. back into Unreal as a NEW asset ----
    ue_in = _post("import_asset", file=out_fbx, destination=destination,
                  name=name or None, save=True)
    if not ue_in.get("ok") or not (ue_in.get("numImported") or 0) > 0:
        return _abort("import_asset", ue_in.get("error", "import_asset produced no assets"),
                      response=ue_in)
    rows = ue_in.get("imported") or []
    new_path = rows[0].get("objectPath") if rows and isinstance(rows[0], dict) else None
    artifacts["newAsset"] = new_path
    steps.append("import_asset")

    # ---- 9. material slot ORDER - warn, never abort ----
    # ORDER, not count. A reimport that keeps the count and PERMUTES the slots is
    # the failure this step is named for and the one that puts the wrong material
    # on the wrong face; comparing len() only reads like a comparison.
    if new_path and pre_slots is not None:
        slots = _post("get_property", objectPath=new_path, propertyPath="StaticMaterials")
        typed = slots.get("typed") if isinstance(slots, dict) else None
        pre_names = _slot_names(pre_slots)
        post_names = _slot_names(typed)
        if pre_names is None:
            warnings.append(
                "export_asset's mesh.materialSlots is not in the {slotName: ...} shape this check "
                f"knows how to read ({pre_slots!r:.300}), so slot ORDER is UNVERIFIED. The count "
                "was not compared instead - a length check is not an order check.")
        elif post_names is None:
            warnings.append(
                "could not read a slot-name sequence out of StaticMaterials on the new asset "
                f"(get_property returned {typed!r:.300}), so slot ORDER is UNVERIFIED. Nothing "
                "weaker was substituted; check it by hand before shipping. Source order was "
                f"{pre_names}.")
        elif post_names != pre_names:
            warnings.append(
                f"material slot ORDER or CONTENT changed.\n  before: {pre_names}\n  after:  "
                f"{post_names}\nThe mesh geometry is valid, but material assignment is now wrong "
                "wherever the two sequences differ - slots are bound by INDEX, so a permutation "
                "silently swaps materials between faces. A human decides whether that matters, "
                "which is why this warns rather than aborting.")
        else:
            artifacts["materialSlotOrder"] = pre_names
        steps.append("material_check")

    # ---- 10. repoint the references ----
    repoint_results = []
    partial = False
    for target in (repoint or []):
        res = _post("set_property", objectPath=target, propertyPath=repoint_property, value=new_path)
        ok = bool(res.get("ok"))
        partial = partial or not ok
        repoint_results.append({"target": target, "ok": ok, "changed": res.get("changed"),
                                "error": res.get("error")})
    if repoint:
        steps.append("repoint")

    deleted = []
    if not keep_intermediates:
        for path in (src_fbx, out_fbx):
            try:
                os.remove(path)
                deleted.append(path)
            except OSError as exc:
                warnings.append(f"could not delete intermediate {path}: {exc}")

    return {"ok": not partial, "partial": partial or None, "completed": steps,
            "artifacts": artifacts, "warnings": warnings, "preImage": pre,
            "newAsset": new_path, "importResponse": ue_in,
            "repointResults": repoint_results or None,
            "intermediatesDeleted": deleted or None}


def main():
    global DEBUG
    parser = argparse.ArgumentParser(description="MifBridge MCP server")
    parser.add_argument("--debug", action="store_true", help="log request/response to stderr")
    args = parser.parse_args()
    DEBUG = args.debug or os.environ.get("MIF_BRIDGE_DEBUG", "").lower() in ("1", "true", "yes")
    _log(f"starting; ue={BASE} timeout={TIMEOUT}s token={'set' if TOKEN else 'empty'}")
    # Reported, never dialled: connecting to Blender at startup would block the
    # MCP handshake whenever Blender is closed. bl_* tools connect lazily.
    _log(f"           blender={BLENDER_HOST}:{BLENDER_PORT} connect={BLENDER_CONNECT_TIMEOUT}s "
         f"probe={BLENDER_PROBE_TIMEOUT}s work={BLENDER_TIMEOUT}s (lazy connect)")
    mcp.run()


if __name__ == "__main__":
    main()
