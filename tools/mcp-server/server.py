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
# Self-reported caller tag for the Activity panel (X-Mif-Agent header) - set MIF_AGENT before
# launching this server so the panel can tell "claude", "gpt", "gemini", etc. apart when several
# agents share one editor. Unset by default: an absent tag beats a guessed one.
AGENT = os.environ.get("MIF_AGENT", "")
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

mcp = FastMCP(
    "mif-ue5-bridge",
    instructions=(
        "MifBridge drives a LIVE Unreal Editor over HTTP. Two things to know before you start.\n"
        "\n"
        "1. EVERY TOOL'S DESCRIPTION HERE IS A SUMMARY. The traps, engine citations and failure "
        "modes were moved out of the tool descriptions on 2026-08-30 because 450 of them cost "
        "about 72,000 tokens of context on every single turn. Call mif_help('<tool_name>') to get "
        "the full text for any tool BEFORE you use one you have not used before - several of these "
        "endpoints guard engine asserts that would terminate the editor, and the reason is in the "
        "help rather than the summary.\n"
        "\n"
        "2. FAILURE IS THE PRESENCE OF `error`, NEVER THE ABSENCE OF `ok`. Check for an `error` "
        "key. A response can carry warnings, notes and partial results alongside ok:true, and "
        "several endpoints deliberately report a measured zero rather than failing.\n"
        "\n"
        "self_audit tells you what mode the bridge is in and what it will refuse. "
        "describe_endpoint('<name>') reports an endpoint's real accepted parameters, aliases and "
        "common mistakes from the LIVE editor, which is the authority when this server and the "
        "plugin disagree."
    ),
)

# ---------------------------------------------------------------------------
# Tool help, moved out of the tool descriptions themselves.
#
# WHY THIS EXISTS. Every MCP tool's name, description and parameter schema sit in the model's
# context on EVERY turn, whether the tool is called or not. At 450 tools those descriptions came to
# 289,944 characters - roughly 72,000 tokens - spent before any work began. The detail was worth
# writing and is not worth re-reading 450 times a turn, so the lead sentence stays inline and the
# rest is served from tool_help.json on demand. Nothing was deleted: the sidecar holds the FULL
# original text, and the extraction asserted the surviving lead still matched it.
# ---------------------------------------------------------------------------
_TOOL_HELP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_help.json")
_TOOL_HELP_CACHE = None


def _tool_help():
    global _TOOL_HELP_CACHE
    if _TOOL_HELP_CACHE is None:
        try:
            with open(_TOOL_HELP_PATH, "r", encoding="utf-8") as fh:
                _TOOL_HELP_CACHE = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            _TOOL_HELP_CACHE = {"__error__": str(exc)}
    return _TOOL_HELP_CACHE


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
        headers = {"X-Mif-Token": TOKEN, "Content-Type": "application/json"}
        if AGENT:
            headers["X-Mif-Agent"] = AGENT
        response = requests.post(
            url,
            json=body,
            headers=headers,
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
                 scope: str = "member", function: str = "", default: str = "",
                 replicated: bool = None, rep_notify: bool = None, rep_notify_function: str = "",
                 replication_condition: str = "", save_game: bool = None, transient: bool = None,
                 config: bool = None, instance_editable: bool = None, blueprint_read_only: bool = None,
                 expose_on_spawn: bool = None, advanced_display: bool = None, interp: bool = None,
                 deprecated: bool = None, category: str = "", tooltip: str = "",
                 field_notify: bool = None) -> dict:
    "Add a variable. name is trimmed+validated and the canonical name is returned. type e.g. int/float/bool/string/Vector/Guid/<Struct>/<Class>. container = array|set|map. For a map, type is the KEY type and value_type is the VALUE type (e.g."
    return _post("add_variable", blueprintId=blueprint_id, name=name, type=type,
                 container=container or None, valueType=value_type or None, scope=scope, function=function or None,
                 default=default or None,
                 replicated=replicated, repNotify=rep_notify, repNotifyFunction=rep_notify_function or None,
                 replicationCondition=replication_condition or None, saveGame=save_game, transient=transient,
                 config=config, instanceEditable=instance_editable, blueprintReadOnly=blueprint_read_only,
                 exposeOnSpawn=expose_on_spawn, advancedDisplay=advanced_display, interp=interp,
                 deprecated=deprecated, category=category or None, tooltip=tooltip or None,
                 fieldNotify=field_notify)


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
    "Set a member variable's default value (applied on next compile). value is REQUIRED and may be a string (UE export text) or typed JSON - a list for an array variable, an object for a struct, a number/bool for the matching type; it is"
    return _post("set_variable_default", blueprintId=blueprint_id, name=name, value=value)


@mcp.tool()
def set_variable_type(blueprint_id: str, name: str, type: str, container: str = "",
                      value_type: str = "", scope: str = "member", function: str = "") -> dict:
    "Retype an EXISTING variable's DECLARATION (every get/set node of it reconstructs to the new pin type). Same type grammar as add_variable: container = array|set|map, and for a map `type` is the KEY type with value_type the VALUE type."
    return _post("set_variable_type", blueprintId=blueprint_id, name=name, type=type,
                 container=container or None, valueType=value_type or None,
                 scope=scope, function=function or None)


@mcp.tool()
def retarget_variable_node(graph_id: str, node_guid: str, target_class: str = "",
                           to_self: bool = None) -> dict:
    "Repoint one variable get/set NODE at a different declaring class - the node's whole FMemberReference is rewritten and the node reconstructed. Pass to_self=True to point it back at the owning Blueprint instead of a named target_class."
    return _post("retarget_variable_node", graphId=graph_id, nodeGuid=node_guid,
                 targetClass=target_class or None, self=to_self)


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

@mcp.tool()
def add_function_call(graph_id: str, function: str, cls: str = "self", x: int = 0, y: int = 0,
                      as_message: bool = False) -> dict:
    "Add a function/library call node. cls is the owning class ('self' for this Blueprint, or e.g. KismetSystemLibrary). Pin types are derived from the reflected UFunction."
    return _post("add_function_call", graphId=graph_id, function=function, **{"class": cls}, x=x, y=y,
                 asMessage=as_message or None)


@mcp.tool()
def add_variable_get(graph_id: str, var: str, target_class: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a 'get variable' node. With no target_class the scope is auto-detected (a variable declared on this function graph resolves as a LOCAL, anything else as a self member)."
    return _post("add_variable_get", graphId=graph_id, var=var,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_variable_set(graph_id: str, var: str, target_class: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a 'set variable' node. Same scope rules and target_class semantics as add_variable_get."
    return _post("add_variable_set", graphId=graph_id, var=var,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_widget_animation(blueprint_id: str, name: str, start_time: float = 0.0,
                         end_time: float = 1.0, display_rate: int = 20) -> dict:
    "Create a UMG WidgetAnimation on a Widget Blueprint."
    return _post("add_widget_animation", blueprintId=blueprint_id, name=name,
                 startTime=start_time, endTime=end_time, displayRate=display_rate)


@mcp.tool()
def rename_tree_widget(blueprint_id: str, widget_name: str, new_name: str) -> dict:
    "Rename a widget in a Widget Blueprint's tree, carrying the name through everything."
    return _post("rename_tree_widget", blueprintId=blueprint_id, widgetName=widget_name,
                 newName=new_name)


@mcp.tool()
def list_widget_animations(blueprint_id: str) -> dict:
    "List a Widget Blueprint's UMG animations, with everything needed to verify one."
    return _post("list_widget_animations", blueprintId=blueprint_id)


@mcp.tool()
def add_widget_animation_track(blueprint_id: str, animation_name: str, widget_name: str,
                               property: str = "RenderTransform.Translation") -> dict:
    "Bind a widget into a UMG animation and give it a property track."
    return _post("add_widget_animation_track", blueprintId=blueprint_id,
                 animationName=animation_name, widgetName=widget_name, property=property)


@mcp.tool()
def set_widget_animation_keys(blueprint_id: str, animation_name: str, widget_name: str,
                              channel: str = "Y", keys: list = None,
                              replace: bool = True,
                              property: str = "RenderTransform.Translation") -> dict:
    "Key one channel of a widget's animation track."
    return _post("set_widget_animation_keys", blueprintId=blueprint_id,
                 animationName=animation_name, widgetName=widget_name, channel=channel,
                 keys=keys or [], replace=replace, property=property)


@mcp.tool()
def set_widget_animation_range(blueprint_id: str, animation_name: str,
                               start_time: float = None, end_time: float = None,
                               display_rate: float = None) -> dict:
    "Change an existing UMG animation's playback range or frame rate IN PLACE."
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
    "Remove one property track from a widget's binding in a UMG animation."
    return _post("remove_widget_animation_track", blueprintId=blueprint_id,
                 animationName=animation_name, widgetName=widget_name, property=property,
                 removeBinding=remove_binding)


@mcp.tool()
def add_reroute(graph_id: str, x: int = 0, y: int = 0,
                src_node: str = "", src_pin: str = "",
                dst_node: str = "", dst_pin: str = "") -> dict:
    "Add a reroute (knot) node - the thing that keeps long wires readable."
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
    "Flip an existing Dynamic Cast node between pure and impure, REALLOCATING its pins - impure has execute/then/Cast Failed, pure has none of them and just outputs the cast result."
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
    "Create a NEW Blueprint function graph. inputs/outputs are lists of {name, type, container?} - the same type grammar as add_variable, so a reference parameter is type='object:SceneComponent'."
    return _post("create_function", blueprintId=blueprint_id, name=name,
                 inputs=inputs or [], outputs=outputs or [], pure=pure)


@mcp.tool()
def create_blueprint(path: str, parent_class: str = "Actor", blueprint_type: str = "Normal",
                     skeleton: str = None) -> dict:
    "Create a fresh Blueprint asset. path is a /Game/... object path (e.g. /Game/MifTestbed/BP_Foo); parent_class is a name or class path (default Actor)."
    return _post("create_blueprint", path=path, parentClass=parent_class, blueprintType=blueprint_type,
                 skeleton=skeleton)


@mcp.tool()
def reparent_blueprint(blueprint_id: str, new_parent_class: str) -> dict:
    "Reparent an existing Blueprint onto a different parent class and recompile. blueprint_id names the Blueprint being REPARENTED; new_parent_class is the class it will now inherit from (a name or a class path)."
    return _post("reparent_blueprint", blueprintId=blueprint_id, newParentClass=new_parent_class)


@mcp.tool()
def resolve_struct(name: str) -> dict:
    "Resolve a struct name (e.g. Vector, Guid, or a mod struct) to its UScriptStruct path. Returns {found, name, path}."
    return _post("resolve_struct", name=name)


@mcp.tool()
def move_node(node_guid: str, x: int, y: int, graph_id: str = None) -> dict:
    "Move a node to a new position. graph_id is optional and only matters if the SAME node guid exists in more than one loaded copy of a Blueprint (e.g."
    return _post("move_node", nodeGuid=node_guid, x=x, y=y, graphId=graph_id)


@mcp.tool()
def remove_node(node_guid: str, confirm: bool = False, graph_id: str = None) -> dict:
    "Remove a node. Requires confirm=True. graph_id is optional and only matters if the SAME node guid exists in more than one loaded copy of a Blueprint (e.g."
    return _post("remove_node", nodeGuid=node_guid, confirm=confirm, graphId=graph_id)


@mcp.tool()
def refresh_node(node_guid: str, graph_id: str = None) -> dict:
    "Reconstruct a node (ReconstructNode) — re-reads its function/variable/pins. graph_id is optional and only matters if the SAME node guid exists in more than one loaded copy of a Blueprint (e.g."
    return _post("refresh_node", nodeGuid=node_guid, graphId=graph_id)


# --------------------------------------------------------------------------
# Pins / wiring
# --------------------------------------------------------------------------

@mcp.tool()
def connect_pins(src_node: str, src_pin: str, dst_node: str, dst_pin: str,
                 graph_id: str = "") -> dict:
    "Wire src_node.src_pin (output) to dst_node.dst_pin (input). Fires the connection notification so wildcards resolve. Returns the schema's reason string if disallowed."
    return _post("connect_pins", srcNode=src_node, srcPin=src_pin, dstNode=dst_node, dstPin=dst_pin,
                 graphId=graph_id or None)


@mcp.tool()
def disconnect_pin(node: str, pin: str, graph_id: str = "") -> dict:
    "Break all links on a pin. graph_id is '<blueprintPath>::<graphName>' from open_blueprint / list_graphs / list_nodes; it scopes the node guid lookup to that graph instead of the global scan, which is the only way to disambiguate two loaded"
    return _post("disconnect_pin", node=node, pin=pin, graphId=graph_id or None)


@mcp.tool()
def reconnect_pin(src_node: str, src_pin: str, dst_node: str, dst_pin: str,
                  graph_id: str = "") -> dict:
    "Break both pins then reconnect them — the wildcard-reset combo when a type is stuck."
    return _post("reconnect_pin", srcNode=src_node, srcPin=src_pin, dstNode=dst_node, dstPin=dst_pin,
                 graphId=graph_id or None)


@mcp.tool()
def set_pin_default(node: str, pin: str, value: str, graph_id: str = "") -> dict:
    "Set a literal default value on an input pin (schema-formatted). graph_id is '<blueprintPath>::<graphName>' from open_blueprint / list_graphs / list_nodes; it scopes the node guid lookup to that graph instead of the global scan, which is"
    return _post("set_pin_default", node=node, pin=pin, value=value, graphId=graph_id or None)


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
    "Apply MANY dependent graph edits in ONE call, with real rollback."
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
    "Execute an editor console command (e.g. a mif.kr.* cvar-command) on the game thread and return {ok, command, executed, world, execOutput, execOutputLines}."
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
def read_engine_log(lines: int = 200, filter: str = "") -> dict:
    "Tail THIS editor process's own Output Log (Saved/Logs/<Project>.log) - every UE_LOG call anywhere in the engine or project, including FMessageLog warnings (they mirror here by default)."
    return _post("read_engine_log", lines=lines, filter=filter or None)


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
    "Force a pin's type. type supports scalars (float is 32-bit, double/real 64-bit), struct/class names, and prefixes class:X / object:X / softobject:X / softclass:X / interface:X / enum:X."
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
    "Add a Call node for an event dispatcher (broadcasts it). Must already exist + be compiled."
    return _post("add_call_dispatcher", graphId=graph_id, dispatcher=dispatcher,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_bind_dispatcher(graph_id: str, dispatcher: str, target_class: str = "",
                        x: int = 0, y: int = 0) -> dict:
    "Add a Bind (Add) node for an event dispatcher."
    return _post("add_bind_dispatcher", graphId=graph_id, dispatcher=dispatcher,
                 targetClass=target_class or None, x=x, y=y)


@mcp.tool()
def add_component_bound_event(blueprint_id: str, component: str, dispatcher: str,
                              event: str = "", x: int = 0, y: int = 0) -> dict:
    "Add a component-bound event node - the red event node you get from a component's Details panel, e.g. OnComponentBeginOverlap on a named collision component."
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
def add_component(blueprint_id: str = "", component_class: str = "", name: str = "", parent_name: str = "",
                  location: list = None, rotation: list = None, scale: list = None, actor_path: str = "") -> dict:
    "Add a component to an Actor Blueprint's SCS tree. Optional parent_name (attach under), and location/rotation([pitch,yaw,roll])/scale as [x,y,z] or {x,y,z}."
    return _post("add_component", blueprintId=blueprint_id, componentClass=component_class,
                 name=name or None, parentName=parent_name or None,
                 location=location or None, rotation=rotation or None, scale=scale or None, actorPath=actor_path)


@mcp.tool()
def list_components(blueprint_id: str = "", component: str = "", include_inherited: bool = True,
                    include_native: bool = True, limit: int = 500, actor_path: str = "") -> dict:
    "List EVERY component reachable from a Blueprint, from all three origins, each row tagged with origin: 'ownSCS' (this Blueprint's own SimpleConstructionScript), 'parentBlueprintSCS' (inherited from a parent BLUEPRINT's SCS, anywhere up the"
    return _post("list_components", blueprintId=blueprint_id, component=component or None,
                 includeInherited=include_inherited, includeNative=include_native, limit=limit, actorPath=actor_path)


@mcp.tool()
def remove_component(blueprint_id: str = "", name: str = "", confirm: bool = False, actor_path: str = "") -> dict:
    "Remove a component from the SCS tree (children promoted). Requires confirm=True. ON A PLACED ACTOR: pass actor_path to remove an INSTANCE component from that one actor."
    return _post("remove_component", blueprintId=blueprint_id, name=name, confirm=confirm, actorPath=actor_path)


@mcp.tool()
def get_inherited_component(blueprint: str, component: str) -> dict:
    "Discovery verb for an INHERITED component: reports origin (parentBlueprintSCS | native | ownSCS | notFound), whether an override template already exists, its objectPath, and the parent's original template. Creates nothing."
    return _post("get_inherited_component", blueprint=blueprint, component=component)


@mcp.tool()
def override_inherited_component(blueprint: str, component: str, properties: dict = None,
                                 confirm: bool = False) -> dict:
    "Create (or reuse) the per-child override template for a component inherited from a parent BLUEPRINT's SCS - the same delta the Details panel writes - and optionally apply properties to it."
    return _post("override_inherited_component", blueprint=blueprint, component=component,
                 properties=properties or None, confirm=confirm)


@mcp.tool()
def revert_inherited_component(blueprint: str, component: str, confirm: bool = False) -> dict:
    "Remove the child's override template so the component falls back to the parent's values. Requires confirm=True (it discards the overrides)."
    return _post("revert_inherited_component", blueprint=blueprint, component=component, confirm=confirm)


@mcp.tool()
def set_component_transform(blueprint_id: str = "", name: str = "", location: list = None,
                            rotation: list = None, scale: list = None, actor_path: str = "") -> dict:
    "Set a scene component's relative transform. location/rotation([pitch,yaw,roll])/scale as [x,y,z] or {x,y,z} (rotation also takes {pitch,yaw,roll})."
    return _post("set_component_transform", blueprintId=blueprint_id, name=name,
                 location=location or None, rotation=rotation or None, scale=scale or None, actorPath=actor_path)


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
    "Read a DataTable: row struct, row names, and rows as JSON (capped at max_rows). text_format controls FText rendering: 'export' (default) is the engine's lossless NSLOCTEXT(\"ns\",\"key\",\"source\") form - round-trip safe, write it back through"
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
    "Write DataTable rows (each a dict with a 'Name' field + row-struct fields). replace=True overwrites the whole table; otherwise rows are added/updated in place. Requires confirm=True."
    return _post("write_datatable_rows", path=path, rows=rows, replace=replace, confirm=confirm)


@mcp.tool()
def delete_datatable_rows(path: str, row_names: list, confirm: bool = False) -> dict:
    "Delete rows from a DataTable by name. row_names is a list of row-name strings. Requires confirm=True."
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
    "Add a parameter to an EXISTING function or custom event - no more rebuilding the function to change its signature. Target by graph_id, blueprint_id + function, or node_guid (custom event)."
    return _post("add_pin", name=name, type=type, graphId=graph_id or None,
                 blueprintId=blueprint_id or None, function=function or None,
                 nodeGuid=node_guid or None, container=container or None,
                 valueType=value_type or None, direction=direction, default=default or None)


@mcp.tool()
def remove_pin(node_guid: str, pin: str, graph_id: str = "", direction: str = "",
               confirm: bool = False) -> dict:
    "Remove a pin. Handles user-defined pins (function/event/tunnel parameters, syncing sibling Return nodes) and duplicate pins (keeps the wired copy). Engine-allocated pins are refused - AllocateDefaultPins would recreate them."
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
                       category: str = "", tooltip: str = "", field_notify: bool = None) -> dict:
    "Set Details-panel flags on a MEMBER variable (locals are rejected - they are never replicated or saved). PARTIAL UPDATE: omitted flags are left alone. rep_notify creates the OnRep_<Name> function graph if missing and implies replicated."
    return _post("set_variable_flags", blueprintId=blueprint_id, name=name,
                 replicated=replicated, repNotify=rep_notify,
                 repNotifyFunction=rep_notify_function or None,
                 replicationCondition=replication_condition or None,
                 saveGame=save_game, transient=transient, config=config,
                 instanceEditable=instance_editable, blueprintReadOnly=blueprint_read_only,
                 exposeOnSpawn=expose_on_spawn, advancedDisplay=advanced_display,
                 interp=interp, deprecated=deprecated,
                 category=category or None, tooltip=tooltip or None, fieldNotify=field_notify)


# --------------------------------------------------------------------------
# Renaming functions, events and dispatchers
# --------------------------------------------------------------------------

@mcp.tool()
def rename_function(new_name: str, graph_id: str = "", blueprint_id: str = "", old_name: str = "",
                    confirm: bool = False) -> dict:
    "Rename a Blueprint function graph. Repoints the entry/result terminators and any override graphs in CHILD blueprints; call sites in OTHER blueprints resolve by name and must be recompiled."
    return _post("rename_function", newName=new_name, graphId=graph_id or None,
                 blueprintId=blueprint_id or None, oldName=old_name or None, confirm=confirm)


@mcp.tool()
def rename_event(node_guid: str, new_name: str, confirm: bool = False, graph_id: str = None) -> dict:
    "Rename a Custom Event by node guid. Refuses an OVERRIDE event (its name is fixed by the parent declaration). Requires confirm=True."
    return _post("rename_event", nodeGuid=node_guid, newName=new_name, confirm=confirm, graphId=graph_id)


@mcp.tool()
def rename_event_dispatcher(blueprint_id: str, old_name: str, new_name: str,
                            confirm: bool = False) -> dict:
    "Rename an event dispatcher. A dispatcher is BOTH a signature graph and a backing delegate variable - this renames both, which is why rename_variable refuses to touch one. Requires confirm=True."
    return _post("rename_event_dispatcher", blueprintId=blueprint_id, oldName=old_name,
                 newName=new_name, confirm=confirm)


@mcp.tool()
def remove_event_dispatcher(blueprint_id: str, name: str, confirm: bool = False) -> dict:
    "Delete an event dispatcher. A dispatcher is BOTH a signature graph and a backing delegate variable - this removes both, and refuses rather than leaving half of one behind."
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
    "Set RPC/replication and access flags on a FUNCTION or a CUSTOM EVENT - the Details-panel Replicates dropdown, Reliable checkbox, access specifier, Pure/Const/CallInEditor, plus category/tooltip/keywords."
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
    "Read any UObject property by dot-path (e.g. Font.Size). Target is either object_path, or blueprint_id + widget_name for a widget template. THE RESPONSE CARRIES TWO VALUES AND YOU ALMOST ALWAYS WANT THE SECOND."
    return _post("get_property", objectPath=object_path or None, blueprintId=blueprint_id or None,
                 widgetName=widget_name or None, propertyPath=property_path)


@mcp.tool()
def set_property(object_path: str = "", blueprint_id: str = "", widget_name: str = "",
                 property_path: str = "", value: Any = "", override_flag: str = "",
                 enforce_clamps: bool = False, save_config: str = "none") -> dict:
    "Write any UObject property by dot-path, the way the Details panel does. Target is either object_path, or blueprint_id + widget_name for a widget template (which recompiles)."
    return _post("set_property", objectPath=object_path or None, blueprintId=blueprint_id or None,
                 widgetName=widget_name or None, propertyPath=property_path, value=value,
                 overrideFlag=override_flag or None, enforceClamps=enforce_clamps or None, saveConfig=save_config)


@mcp.tool()
def list_object_properties(object_path: str = "", blueprint_id: str = "", widget_name: str = "",
                           name_contains: str = "", limit: int = 200,
                           max_value_chars: int = 200) -> dict:
    "Dump an object's top-level properties with type and current value. Each row carries 'value' (UE export text, round-trip-safe, so a bool arrives as the STRING 'True' and a C-array UPROPERTY shows only element 0) and 'typed' (the same value"
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
    "THE DISCOVERY LAYER for everything else on this axis: what the Details panel knows about a property and set_property could not tell you. Reports the authored specifier (EditAnywhere / EditDefaultsOnly / VisibleAnywhere / ..."
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
    "What does this object actually OVERRIDE versus its archetype - the question the Details panel answers with a yellow arrow, and the single most useful read for auditing a Blueprint, a placed actor or a CDO."
    return _post("diff_properties_vs_default", objectPath=object_path or None,
                 blueprintId=blueprint_id or None, widgetName=widget_name or None,
                 nameContains=name_contains or None, limit=limit, maxValueChars=max_value_chars,
                 includeTransient=include_transient, deep=deep, recursive=recursive or None)


@mcp.tool()
def reset_property_to_default(object_path: str = "", property_path: str = "",
                              force: bool = False, override_flag: str = "") -> dict:
    "The Details panel's yellow arrow: put a property back to its archetype default. Reports valueBefore / defaultValue / valueAfter / differedFromDefault / changed / defaultSource / archetype, and ASSERTS the invariant - after a successful"
    return _post("reset_property_to_default", objectPath=object_path or None,
                 propertyPath=property_path, force=force or None,
                 overrideFlag=override_flag or None)


@mcp.tool()
def edit_container(object_path: str = "", property_path: str = "", operation: str = "",
                   index: int = None, count: int = None, key: str = "", new_key: str = "",
                   value: Any = None, swap_with: int = None, new_size: int = None,
                   override_flag: str = "") -> dict:
    "The element LIFECYCLE inside a TArray/TSet/TMap - the +, x, insert and clear buttons the Details panel has and set_property does not: operation = add | insert | remove | clear | swap | resize | setKey."
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
    "Dump the ENTIRE WidgetTree of a widget blueprint: name, class, parent, child index, slot class, is-variable flag, is-panel, child count. Read-only."
    return _post("list_tree_widgets", blueprintId=blueprint_id)


@mcp.tool()
def duplicate_tree_widget(blueprint_id: str, widget_name: str, parent_name: str = None,
                          index: int = None) -> dict:
    "Clone a widget AND its whole subtree, preserving every property value, by riding the engine's own copy/paste text path (ExportWidgetsToText/ImportWidgetsFromText)."
    return _post("duplicate_tree_widget", blueprintId=blueprint_id, widgetName=widget_name,
                 parentName=parent_name, index=index)


@mcp.tool()
def wrap_tree_widget(blueprint_id: str, widget_name: str, wrapper_class: str,
                     wrapper_name: str = None) -> dict:
    "The Designer's 'Wrap With': insert a new panel where the widget currently sits, then move the widget inside it. wrapper_class must be a UPanelWidget (CanvasPanel, VerticalBox, HorizontalBox, Overlay, SizeBox, Border...)."
    return _post("wrap_tree_widget", blueprintId=blueprint_id, widgetName=widget_name,
                 wrapperClass=wrapper_class, wrapperName=wrapper_name)


@mcp.tool()
def move_tree_widget(blueprint_id: str, widget_name: str, parent_name: str = None,
                     as_root: bool = False, index: int = None,
                     replace_root: bool = False) -> dict:
    "Reparent an EXISTING widget. add_tree_widget creates and remove_tree_widget deletes; without this, rearranging meant delete + recreate, losing every property already set on the widget. Pass parent_name or as_root."
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
def remove_tree_widget(blueprint_id: str, widget_name: str, confirm: bool = False) -> dict:
    "Remove a widget from a Widget Blueprint's tree (handles child, root and named-slot cases). Removes the widget's WHOLE SUBTREE in one call - requires confirm=True."
    return _post("remove_tree_widget", blueprintId=blueprint_id, widgetName=widget_name, confirm=confirm)


# --------------------------------------------------------------------------
# Cooked / mounted-container introspection (read-only)
# --------------------------------------------------------------------------

@mcp.tool()
def list_mounted_containers() -> dict:
    "List the mounted pak/utoc containers and the resolved game install dir. Use this to see what cooked content is actually visible to the editor."
    return _post("list_mounted_containers")


@mcp.tool()
def find_assets(cls: str = "", path_prefix: str = "", name_contains: str = "",
                origin: str = "any", recursive_classes: bool = True, limit: int = 100) -> dict:
    "Search the asset registry across loose AND cooked/mounted content. cls filters by class name, path_prefix by /Game/... prefix, name_contains by substring. origin = any|loose|cooked. Returns at most limit results."
    return _post("find_assets", **{"class": cls or None}, pathPrefix=path_prefix or None,
                 nameContains=name_contains or None, origin=origin,
                 recursiveClasses=recursive_classes, limit=limit)


@mcp.tool()
def describe_package(package: str) -> dict:
    "Describe a package by /Game/ path: the objects it contains, their classes, and whether it is cooked. Works on cooked packages whose Blueprint graphs are stripped."
    return _post("describe_package", package=package)


@mcp.tool()
def diagnose_landscape(limit: int = 40) -> dict:
    "Report landscape proxies/components in the current editor world (diagnostics). limit caps the components listed, 1-1000."
    return _post("diagnose_landscape", limit=limit)


@mcp.tool()
def diagnose_landscape_draws(limit: int = 40) -> dict:
    "Render-thread follow-up to diagnose_landscape: per-component cached mesh-draw-command counts (base pass vs depth pass) plus LOD screen sizes, for landscape components that pass every game-thread check yet never draw."
    return _post("diagnose_landscape_draws", limit=limit)


# --------------------------------------------------------------------------
# Navigation (nav mesh + nav-driven movement)
# --------------------------------------------------------------------------

@mcp.tool()
def add_nav_volume(location: dict = None, size: dict = None, label: str = "NavBounds") -> dict:
    "Place a NavMeshBoundsVolume defining where the nav mesh will generate. size is in WORLD UNITS (converted to brush scale internally - a volume's size comes from its brush, not a size property, which is the usual way this silently covers"
    return _post("add_nav_volume", location=location, size=size, label=label)


@mcp.tool()
def build_navmesh() -> dict:
    "Start nav mesh generation. DOES NOT BLOCK - tiles are cooked over subsequent frames and this handler runs on the game thread. Poll nav_status until building=false and tiles>0. Fails clearly if there is no bounds volume."
    return _post("build_navmesh")


@mcp.tool()
def nav_status() -> dict:
    "Nav mesh state: hasNavSystem, boundsVolumes, navMeshActors, TILES, building, ready."
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
                        fov: float = None, ortho: str = None, ortho_zoom: float = None, view_mode: str = "", show_flags: dict = None, game_view: bool = None, realtime: bool = None) -> dict:
    "Move the editor viewport camera the user is looking through. Distinct from capture_camera, which spawns a transient scene-capture and changes nothing on screen. look_at wins over rotation."
    return _post("set_viewport_camera", location=location, rotation=rotation, lookAt=look_at,
                 fov=fov, ortho=ortho, orthoZoom=ortho_zoom, viewMode=view_mode, showFlags=show_flags, gameView=game_view, realtime=realtime)


@mcp.tool()
def focus_viewport(actor_path: str = None, folder: str = None, instant: bool = True) -> dict:
    "Frame the viewport on an actor, a folder, or (with no target) the WHOLE level - the programmatic equivalent of select-all-then-F."
    return _post("focus_viewport", actorPath=actor_path, folder=folder, instant=instant)


@mcp.tool()
def get_viewport_camera(show_flags: str = "") -> dict:
    "Read the editor viewport camera: location, rotation, fov, whether it is perspective, and how many viewports exist. Read-only."
    return _post("get_viewport_camera", showFlags=show_flags)


# --------------------------------------------------------------------------
# World lifecycle, splines, ground snapping
# --------------------------------------------------------------------------

@mcp.tool()
def new_level(partitioned: bool = False) -> dict:
    "Create a fresh empty level. Forces bPromptUserToSave=FALSE - a modal 'save your changes?' dialog blocks the game thread, which is the same thread this bridge runs on, so a prompt would deadlock an unattended run."
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
    "Author a spline's points - THIS IS WHAT MAKES NPCs WALK. The game routes wandering NPCs along BP_SegmentedPathTaskMarker, whose PathSpline is a USplineComponent. points is [{x,y,z},...] (min 2)."
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
    "Drop actors onto the terrain, one trace each, with the actor ITSELF excluded from the trace."
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
    "Create a real ALandscape. This is the correct answer for ground - a stretched /Engine/BasicShapes/Plane smears one UV set over the whole surface (blurred corners) and a grid of tiles reads as a checkerboard."
    return _post("create_landscape", location=location, scale=scale, componentsX=components_x,
                 componentsY=components_y, quadsPerSection=quads_per_section,
                 sectionsPerComponent=sections_per_component, material=material, layers=layers,
                 heightMode=height_mode, amplitude=amplitude, frequency=frequency, seed=seed,
                 label=label, folder=folder)


@mcp.tool()
def sculpt_landscape(center: dict, radius: float, mode: str = "flatten", amount: float = 0.0,
                     falloff: float = 0.5, target_z: float = None, landscape: str = None) -> dict:
    "Sculpt terrain in WORLD units. mode: raise|lower|flatten|smooth. center/radius/amount/target_z are all world units - the vertex-space conversion happens inside."
    return _post("sculpt_landscape", center=center, radius=radius, mode=mode, amount=amount,
                 falloff=falloff, targetZ=target_z, landscape=landscape)


@mcp.tool()
def apply_spline_to_landscape(spline_actor: str, landscape: str = "", component: str = "",
                              start_width: float = 200.0, end_width: float = 200.0,
                              start_side_falloff: float = 200.0, end_side_falloff: float = 200.0,
                              start_roll: float = 0.0, end_roll: float = 0.0,
                              subdivisions: int = 20, raise_heights: bool = True,
                              lower_heights: bool = True, paint_layer: str = "",
                              edit_layer: str = "") -> dict:
    "Carve and paint a landscape along a spline - the road / riverbed / path operation, in one call."
    return _post("apply_spline_to_landscape", splineActor=spline_actor, landscape=landscape,
                 component=component, startWidth=start_width, endWidth=end_width,
                 startSideFalloff=start_side_falloff, endSideFalloff=end_side_falloff,
                 startRoll=start_roll, endRoll=end_roll, subdivisions=subdivisions,
                 raiseHeights=raise_heights, lowerHeights=lower_heights,
                 paintLayer=paint_layer, editLayer=edit_layer)


@mcp.tool()
def paint_landscape(layer_info: str, center: dict, radius: float, weight: float = 1.0,
                    falloff: float = 0.5, landscape: str = None) -> dict:
    "Paint a landscape weight layer in WORLD units - this is what makes a road corridor read as dirt while the verge stays grass."
    return _post("paint_landscape", layerInfo=layer_info, center=center, radius=radius,
                 weight=weight, falloff=falloff, landscape=landscape)


@mcp.tool()
def bind_landscape_rvt(runtime_virtual_textures: list, landscape: str = None, create_volumes: bool = True) -> dict:
    "Bind runtime virtual textures to a landscape AND create the bounding volumes. A landscape material that samples an RVT renders its base colour BLACK unless both exist: the RVT in the landscape's array (what to draw into) and an"
    return _post("bind_landscape_rvt", runtimeVirtualTextures=runtime_virtual_textures,
                 landscape=landscape, createVolumes=create_volumes)


@mcp.tool()
def landscape_info() -> dict:
    "Report every landscape in the editor world: world bounds, vertex resolution, scale, material, painted layers, materialLayers (what the MATERIAL declares - painting a layer not in this list succeeds and changes nothing),"
    return _post("landscape_info")


# --------------------------------------------------------------------------
# Level-authoring throughput + material control
# --------------------------------------------------------------------------

@mcp.tool()
def spawn_many(items: list, actor_class: str = "StaticMeshActor", mesh: str = "",
               material: str = "", folder: str = "", label_prefix: str = "") -> dict:
    "Spawn MANY actors in ONE call. items is a list of {x,y,z or location:{}, rotation:{} or yaw, scale (number or {}), label?, mesh?, material?}. Top-level mesh/material are the defaults; per-item values override."
    return _post("spawn_many", items=items, actorClass=actor_class, mesh=mesh or None,
                 material=material or None, folder=folder or None,
                 labelPrefix=label_prefix or None)


@mcp.tool()
def duplicate_actors(actor_paths: list = None, label_prefix: str = "", offset: dict = None,
                     yaw_offset: float = 0.0, count: int = 1, label_suffix: str = "_copy",
                     folder: str = "") -> dict:
    "Duplicate a SET of actors with a positional offset - copy a whole finished building instead of re-placing every panel. Select sources by actor_paths[] or by label_prefix (e.g. 'B5_' grabs every piece of that building)."
    return _post("duplicate_actors", actorPaths=actor_paths, labelPrefix=label_prefix or None,
                 offset=offset, yawOffset=yaw_offset, count=count,
                 labelSuffix=label_suffix, folder=folder or None)


@mcp.tool()
def create_material_instance(parent: str, path: str, scalars: dict = None,
                             vectors: dict = None) -> dict:
    "Create a MaterialInstanceConstant asset from a parent material, with parameter overrides."
    return _post("create_material_instance", parent=parent, path=path,
                 scalars=scalars, vectors=vectors)


@mcp.tool()
def set_material_parameter(material: str, scalars: dict = None, vectors: dict = None,
                           textures: dict = None, switches: dict = None,
                           association: str = "global", index: int = -1) -> dict:
    "Set parameters on an existing MaterialInstanceConstant. scalars is {name: number}, vectors is {name: {r,g,b,a}} (also accepts {x,y,z,w} or [r,g,b,a])."
    return _post("set_material_parameter", material=material, scalars=scalars, vectors=vectors,
                 textures=textures, switches=switches, association=association, index=index)


@mcp.tool()
def add_foliage_instances(instances: list, mesh: str = "", foliage_type: str = "",
                          label: str = "Foliage", folder: str = "") -> dict:
    "Place N instanced transforms in one call instead of N separate actors."
    return _post("add_foliage_instances", instances=instances,
                 mesh=mesh or None, foliageType=foliage_type or None,
                 label=label, folder=folder or None)


# --------------------------------------------------------------------------
# Spatial awareness + visual feedback (numbers for correctness, pixels for taste)
# --------------------------------------------------------------------------

@mcp.tool()
def get_actor_bounds(actor_path: str) -> dict:
    "World-space AABB of a PLACED actor: origin, extent, size, min, max. This accounts for the actor's SCALE, unlike the mesh asset's ExtendedBounds - a 1312u rock placed at scale 2.4 is 3150u, and that gap is how things end up swallowing"
    return _post("get_actor_bounds", actorPath=actor_path)


@mcp.tool()
def check_overlaps(actor_path: str = "", name_contains: str = "", ignore_ground: bool = True,
                   tolerance: float = 25.0) -> dict:
    "Find actors intersecting each other. With no actor_path this is a WHOLE-SCENE audit - the 'what did I get wrong' call."
    return _post("check_overlaps", actorPath=actor_path or None, nameContains=name_contains or None,
                 ignoreGround=ignore_ground, tolerance=tolerance)


@mcp.tool()
def trace(start: dict, end: dict = None, direction: dict = None, distance: float = 10000.0,
          shape: str = "line", radius: float = 50.0, half_extent: dict = None,
          half_height: float = 100.0, channel: str = "worldStatic", trace_complex: bool = True,
          multi: bool = False, ignore_actors: list = None, draw: bool = False,
          draw_duration: float = 5.0) -> dict:
    "Trace a ray or sweep a shape through the world."
    return _post("trace", start=start, end=end, direction=direction, distance=distance,
                 shape=shape, radius=radius, halfExtent=half_extent, halfHeight=half_height,
                 channel=channel, traceComplex=trace_complex, multi=multi,
                 ignoreActors=ignore_actors or [], draw=draw, drawDuration=draw_duration)


@mcp.tool()
def capture_viewport(path: str = "") -> dict:
    "Capture the pixels the editor is ACTUALLY drawing right now."
    return _post("capture_viewport", path=path)


@mcp.tool()
def audition_sound(path: str = "", stop: bool = False) -> dict:
    "Play a sound through the editor's preview device, or stop the current preview."
    return _post("audition_sound", path=path, stop=stop)


@mcp.tool()
def nav_project_point(point: dict, extent: dict = None) -> dict:
    "Project a point onto the nav mesh: is this spot walkable, and how far off was it?"
    return _post("nav_project_point", point=point, extent=extent)


@mcp.tool()
def nav_find_path(start: dict, end: dict, draw: bool = False, draw_duration: float = 8.0) -> dict:
    "Can an agent actually get from start to end? Answers without running PIE."
    return _post("nav_find_path", start=start, end=end, draw=draw, drawDuration=draw_duration)


@mcp.tool()
def get_perf_stats() -> dict:
    "Answer \"is this mod expensive?\" with numbers."
    return _post("get_perf_stats")


@mcp.tool()
def draw_debug(shape: str = "point", start: dict = None, end: dict = None, center: dict = None,
               radius: float = 100.0, extent: dict = None, text: str = "",
               color: str = "green", duration: float = 5.0, thickness: float = 2.0) -> dict:
    "Draw a debug shape in the viewport: line, sphere, box, point, arrow or string."
    return _post("draw_debug", shape=shape, start=start, end=end, center=center, radius=radius,
                 extent=extent, text=text, color=color, duration=duration, thickness=thickness)


@mcp.tool()
def trace_ground(x: float = None, y: float = None, location: dict = None,
                 from_z: float = 100000.0, to_z: float = -100000.0,
                 ignore_actor: str = "") -> dict:
    "Line-trace straight down to find ground height."
    return _post("trace_ground", x=x, y=y, location=location, fromZ=from_z, toZ=to_z,
                 ignoreActor=ignore_actor or None)


@mcp.tool()
def capture_camera(location: dict = None, rotation: dict = None, look_at: dict = None,
                   use_viewport_camera: bool = False, fov: float = 0.0,
                   width: int = 1280, height: int = 720,
                   name: str = "MifShot") -> dict:
    "Render the scene from an ARBITRARY viewpoint to a PNG and return its path - does NOT move the user's viewport, so you can inspect while they keep working."
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
    "One-call scene audit: actor count, total bounds, plus actors that are FLOATING above ground, SUNKEN below it, or suspiciously TALL (scale outliers)."
    return _post("scene_report", groundZ=ground_z, floatTolerance=float_tolerance,
                 tallWarnZ=tall_warn_z)


# --------------------------------------------------------------------------
# Play-In-Editor control and runtime observation
# --------------------------------------------------------------------------

@mcp.tool()
def start_pie(simulate: bool = False, start_location: dict = None,
              start_rotation: dict = None, players: int = None, net_mode: str = "",
              one_process: bool = None, width: int = None, height: int = None) -> dict:
    "Start Play-In-Editor. DOES NOT BLOCK: the engine defers the start to its next tick, and this handler runs on the game thread, so waiting here would deadlock the very ticks PIE needs."
    return _post("start_pie", simulate=simulate or None, startLocation=start_location,
                 startRotation=start_rotation, players=players, netMode=net_mode or None,
                 oneProcess=one_process, width=width, height=height)


@mcp.tool()
def stop_pie() -> dict:
    "End the Play-In-Editor session. Also deferred - poll pie_status until state=='stopped'."
    return _post("stop_pie")


@mcp.tool()
def pie_status() -> dict:
    "PIE state: state (stopped|starting|running) where running means the world EXISTS and BeginPlay has happened (not merely that a session was requested - sessionActive reports that separately), running/startPending/stopPending/simulating, the"
    return _post("pie_status")


@mcp.tool()
def list_pie_actors(class_filter: str = "", name_contains: str = "", limit: int = 200, net_mode: str = "server") -> dict:
    "List actors in the RUNNING PIE world (list_level_actors sees the editor world instead - during PIE they are different worlds with different actor paths)."
    return _post("list_pie_actors", classFilter=class_filter or None,
                 nameContains=name_contains or None, limit=limit, netMode=net_mode)


@mcp.tool()
def run_console_captured(command: str, filter: str = "") -> dict:
    "Run an editor/game console command AND capture its log output. run_console returns only whether a handler claimed the command; mif.kr.* commands log rather than writing to the Exec archive, so this brackets GLog for the duration of the"
    return _post("run_console_captured", command=command, filter=filter or None)


@mcp.tool()
def self_audit(summary_only: bool = False, include_endpoint_details: bool = None,
               include_endpoints: bool = None) -> dict:
    "The plugin reporting its OWN invariants from inside the running DLL: live endpoint count and names (the ones actually dispatching, not parsed from a header), each endpoint's transaction bucket (readOnly / selfManaged / transacted /"
    return _post("self_audit", summaryOnly=summary_only or None,
                 includeEndpointDetails=include_endpoint_details, includeEndpoints=include_endpoints)


@mcp.tool()
def describe_endpoint(name: str) -> dict:
    "Report what parameters an endpoint accepts, so you stop discovering them by calling endpoints wrong on purpose. Returns status = exactly one of three states, which are never conflated."
    return _post("describe_endpoint", name=name)


@mcp.tool()
def find_tools(keyword: str, limit: int = 15) -> dict:
    "Search this MCP server's own tool names and descriptions for a keyword, so you can find the right tool among all of them without reading through the whole list or guessing a name."
    q = (keyword or "").strip().lower()
    if not q:
        return {"ok": False, "error": "keyword is required - a substring to search tool names/descriptions for."}
    try:
        tools = mcp._tool_manager.list_tools()
    except Exception as e:
        return {"ok": False, "error": "could not read this server's own tool registry: %s" % e}

    name_hits, desc_hits = [], []
    for t in tools:
        name = t.name or ""
        desc = t.description or ""
        if t.name == "find_tools":
            continue   # searching for the search tool itself is never the point
        hit_name = q in name.lower()
        hit_desc = (not hit_name) and q in desc.lower()
        if not (hit_name or hit_desc):
            continue
        try:
            params = list((t.parameters or {}).get("properties", {}).keys())
        except Exception:
            params = []
        # Collapsed whitespace: a multi-line docstring's indentation and newlines are real content
        # for a human reading source, but noise in a 200-char summary meant to be judged at a glance.
        flat = " ".join(desc.split())
        row = {
            "name": name,
            "summary": (flat[:200] + "...") if len(flat) > 200 else flat,
            "params": params,
        }
        (name_hits if hit_name else desc_hits).append(row)

    name_hits.sort(key=lambda r: r["name"])
    desc_hits.sort(key=lambda r: r["name"])
    results = (name_hits + desc_hits)[:max(1, limit)]
    truncated = len(name_hits) + len(desc_hits) > len(results)
    out = {
        "ok": True,
        "keyword": keyword,
        "count": len(results),
        "matched": len(name_hits) + len(desc_hits),
        "results": results,
    }
    if truncated:
        out["truncated"] = True
        out["note"] = ("%d total matches, showing %d - narrow the keyword or raise limit to see the rest."
                        % (len(name_hits) + len(desc_hits), len(results)))
    if not results:
        out["note"] = ("no tool name or description contains %r. Try a different word for the same "
                        "concept, or self_audit for the full endpoint list this server wraps." % keyword)
    return out


# --------------------------------------------------------------------------
# Level / placed-actor editing (the level currently open in the editor)
# --------------------------------------------------------------------------

@mcp.tool()
def get_level_actor(actor_path: str) -> dict:
    "Read one level actor back: transform, label, class, path."
    return _post("get_level_actor", actorPath=actor_path)


@mcp.tool()
def attach_actor(child: str, parent: str, socket: str = "",
                 keep_world_transform: bool = True) -> dict:
    "Parent one placed actor to another - what dragging in the World Outliner does, addressed by path."
    return _post("attach_actor", child=child, parent=parent, socket=socket,
                 keepWorldTransform=keep_world_transform)


@mcp.tool()
def detach_actor(actor_path: str, keep_world_transform: bool = True) -> dict:
    "Detach a placed actor from whatever it is attached to. Takes only the CHILD - it detaches from whatever parent it currently has, which you can read from attachParent on any actor response."
    return _post("detach_actor", actorPath=actor_path,
                 keepWorldTransform=keep_world_transform)


@mcp.tool()
def list_level_actors(class_filter: str = "", name_contains: str = "", folder: str = "",
                      selected_only: bool = False, limit: int = 200) -> dict:
    "List actors placed in the CURRENT level with actorPath, name, label, class, folder and transform. class_filter matches any class in the ancestry by substring, so 'StaticMeshActor' finds subclasses."
    return _post("list_level_actors", classFilter=class_filter or None,
                 nameContains=name_contains or None, folder=folder or None,
                 selectedOnly=selected_only or None, limit=limit)


@mcp.tool()
def spawn_actor_in_level(actor_class: str, location: dict = None, rotation: dict = None,
                         scale: dict = None, label: str = "", folder: str = "",
                         mesh: str = None) -> dict:
    "Spawn an actor into the current level. actor_class may be a native class or a Blueprint class path (/Game/BP/BP_Foo.BP_Foo_C)."
    return _post("spawn_actor_in_level", actorClass=actor_class, location=location,
                 rotation=rotation, scale=scale, label=label or None, folder=folder or None,
                 mesh=mesh)


@mcp.tool()
def set_actor_transform(actor_path: str, location: dict = None, rotation: dict = None,
                        scale: dict = None, relative: bool = False) -> dict:
    "Move/rotate/scale a placed actor. Omitted components keep their current value, so this doubles as move-only; rotation accepts {x,y,z} or {pitch,yaw,roll}, and any of the three may also be [x,y,z]."
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
    "Instantiate a data-asset class at a /Game path."
    return _post("create_asset", path=path, **{"class": asset_class})


@mcp.tool()
def create_datatable(path: str, row_struct: str) -> dict:
    "Create an EMPTY DataTable asset at a /Game/ path with the given row struct, then fill it with write_datatable_rows."
    return _post("create_datatable", path=path, rowStruct=row_struct)



@mcp.tool()
def create_struct(path: str, members: list = None) -> dict:
    "Create a Blueprint user-defined struct at a /Game/ path. members is a list of {name, type, container?, valueType?, default?} using the same type grammar as add_variable."
    return _post("create_struct", path=path, members=members or None)


@mcp.tool()
def list_struct_members(struct: str) -> dict:
    "List a user-defined struct's members: name, friendlyName, guid, type, default, and invalid=true for any member whose type failed to resolve."
    return _post("list_struct_members", struct=struct)


@mcp.tool()
def set_struct_member(struct: str, member: str = "", guid: str = "", new_name: str = "",
                      type: str = "", container: str = "", value_type: str = "",
                      default: str = "") -> dict:
    "Rename, retype or re-default an EXISTING member of a Blueprint struct, in place."
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
    "Create a Blueprint user-defined enum at a /Game/ path. values is a list of display-name strings."
    return _post("create_enum", path=path, values=values or None)


@mcp.tool()
def add_enum_value(enum: str, value: str) -> dict:
    "Append an entry to a user-defined enum. value is the display name. Returns its index."
    return _post("add_enum_value", enum=enum, value=value)


@mcp.tool()
def remove_enum_value(enum: str, value: str = "", index: int = None, confirm: bool = False) -> dict:
    "Remove an entry from a user-defined enum, by display name or index. Refuses to remove the last one."
    return _post("remove_enum_value", enum=enum, value=value or None, index=index, confirm=confirm)


# --------------------------------------------------------------------------
# Animation assets (read-only)
# --------------------------------------------------------------------------

@mcp.tool()
def add_anim_node(graph_id: str, node_class: str, x: int = 0, y: int = 0) -> dict:
    "Add any UAnimGraphNode_* node to an Animation Blueprint's graph - one endpoint for the whole family (SequencePlayer, Slot, StateMachine, BlendSpacePlayer, LayeredBoneBlend...)."
    return _post("add_anim_node", graphId=graph_id, nodeClass=node_class, x=x, y=y)


@mcp.tool()
def add_anim_state(blueprint_id: str, graph_id: str, name: str,
                   x: int = 0, y: int = 0) -> dict:
    "Add a STATE to an Animation Blueprint's state machine - the one missing constructor call that was blocking all of it."
    return _post("add_anim_state", blueprintId=blueprint_id, graphId=graph_id, name=name, x=x, y=y)


@mcp.tool()
def list_animations(filter: str = "", skeleton: str = "", limit: int = 200) -> dict:
    "List animation assets (sequences, montages, blend spaces, composites) from the asset registry WITHOUT loading them. Optional substring filter on path and skeleton. Returns truncated=true if the limit was hit."
    return _post("list_animations", filter=filter or None, skeleton=skeleton or None, limit=limit)


@mcp.tool()
def set_ik_rig_mesh(path: str, mesh: str) -> dict:
    "Assign a SkeletalMesh to an IK Rig - which BUILDS the rig, not just labels it."
    return _post("set_ik_rig_mesh", path=path, mesh=mesh)


@mcp.tool()
def set_ik_rig_retarget_root(path: str, bone: str) -> dict:
    "Set an IK Rig's retarget root - the bone the whole body pose is anchored to, usually pelvis."
    return _post("set_ik_rig_retarget_root", path=path, bone=bone)


@mcp.tool()
def add_ik_retarget_chain(path: str, name: str, start_bone: str, end_bone: str,
                          goal: str = "") -> dict:
    "Add a retarget chain to an IK Rig: a named span from start_bone down to end_bone."
    return _post("add_ik_retarget_chain", path=path, name=name, startBone=start_bone,
                 endBone=end_bone, goal=goal or None)


@mcp.tool()
def remove_ik_retarget_chain(path: str, name: str) -> dict:
    """Remove a retarget chain from an IK Rig. Lists the chains it does have if the name is unknown."""
    return _post("remove_ik_retarget_chain", path=path, name=name)


@mcp.tool()
def set_retarget_rigs(path: str, source: str = "", target: str = "") -> dict:
    "Point an IK Retargeter at its source and target IK Rigs."
    return _post("set_retarget_rigs", path=path, source=source or None, target=target or None)


@mcp.tool()
def auto_map_retarget_chains(path: str, mode: str = "fuzzy", remap_existing: bool = False) -> dict:
    "Map the source rig's chains onto the target rig's chains by name."
    return _post("auto_map_retarget_chains", path=path, mode=mode, remapExisting=remap_existing)


@mcp.tool()
def set_retarget_chain_mapping(path: str, target_chain: str, source_chain: str = "") -> dict:
    "Map one source chain onto one target chain by hand, for what auto-mapping got wrong."
    return _post("set_retarget_chain_mapping", path=path, targetChain=target_chain,
                 sourceChain=source_chain or None)


@mcp.tool()
def list_retarget_chain_mapping(path: str) -> dict:
    "Read an IK Retargeter's chain mapping, and check whether it would actually work."
    return _post("list_retarget_chain_mapping", path=path)


@mcp.tool()
def list_ik_solver_types() -> dict:
    "List the IK Rig solver classes this engine build has."
    return _post("list_ik_solver_types")


@mcp.tool()
def add_ik_solver(path: str, solver_class: str) -> dict:
    "Add a solver to an IK Rig. list_ik_solver_types shows the available classes."
    return _post("add_ik_solver", path=path, solverClass=solver_class)


@mcp.tool()
def remove_ik_solver(path: str, index: int) -> dict:
    "Remove a solver from an IK Rig by index."
    return _post("remove_ik_solver", path=path, index=index)


@mcp.tool()
def set_ik_solver(path: str, index: int, root_bone: str = "", end_bone: str = "",
                  enabled: bool = None) -> dict:
    "Set a solver's root bone, end bone and/or enabled flag."
    return _post("set_ik_solver", path=path, index=index,
                 rootBone=root_bone or None, endBone=end_bone or None, enabled=enabled)


@mcp.tool()
def add_ik_goal(path: str, name: str, bone: str) -> dict:
    "Add an IK goal (an effector target) to a bone on an IK Rig."
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
    "Connect an IK goal to a solver, or disconnect it with connected=False."
    return _post("set_ik_goal_solver_connection", path=path, name=name,
                 solverIndex=solver_index, connected=connected)


@mcp.tool()
def list_water_bodies(type: str = None, name_contains: str = None) -> dict:
    "List the water bodies in the OPEN level - rivers, lakes, oceans and custom bodies. Reports each body's type, spline point count, world location and which AWaterZone it belongs to. TWO things worth knowing before reading the output."
    return _post("list_water_bodies", type=type, nameContains=name_contains)


@mcp.tool()
def describe_water_body(path: str, include_spline_points: bool = True) -> dict:
    "Describe ONE water body: everything list_water_bodies reports, plus its water material and every spline point in WORLD space."
    return _post("describe_water_body", path=path, includeSplinePoints=include_spline_points)


@mcp.tool()
def create_water_body(type: str, label: str = None, x: float = 0.0, y: float = 0.0,
                      z: float = 0.0, points: list = None) -> dict:
    "Create a water body in the OPEN level - River, Lake, Ocean or Custom. THE TYPE IS THE CLASS, not a settable property: the four water body types are four different actor classes with four different components, so you pick one here and"
    return _post("create_water_body", type=type, label=label, x=x, y=y, z=z, points=points)


@mcp.tool()
def describe_metasound(path: str) -> dict:
    "Describe ONE MetaSound's INTERFACE - the inputs and outputs you set to drive it - plus counts for its node graph."
    return _post("describe_metasound", path=path)


@mcp.tool()
def create_water_zone(x: float = 0.0, y: float = 0.0, z: float = 0.0,
                      extent_x: float = None, extent_y: float = None,
                      label: str = None) -> dict:
    "Create an AWaterZone in the OPEN level - the thing that makes water bodies RENDER."
    return _post("create_water_zone", x=x, y=y, z=z,
                 extentX=extent_x, extentY=extent_y, label=label)


@mcp.tool()
def set_water_body_spline(path: str, points: list) -> dict:
    "Replace a water body's spline - the spline IS the shape of a river or lake. points is an array of {x,y,z} in WORLD space and REPLACES the existing spline entirely; there is no append and no single-point setter, because ResetSpline is the"
    return _post("set_water_body_spline", path=path, points=points)


@mcp.tool()
def create_data_layer(name: str, asset_path: str = None, type: str = "runtime",
                      is_private: bool = False) -> dict:
    "Create a World Partition Data Layer. Without this the family could only operate on layers somebody else authored - list them, change visibility, move actors in and out - which is half a subsystem."
    return _post("create_data_layer", name=name, assetPath=asset_path, type=type,
                 isPrivate=is_private)


@mcp.tool()
def add_actor_to_data_layer(actor_path: str, name: str) -> dict:
    "Put an actor INTO a World Partition Data Layer - the operation Data Layers exist for, and the half this bridge was missing (it could read layers and change how they display, but not what belongs to them)."
    return _post("add_actor_to_data_layer", actorPath=actor_path, name=name)


@mcp.tool()
def remove_actor_from_data_layer(actor_path: str, name: str) -> dict:
    "Remove an actor from a World Partition Data Layer. Resolves by actor PATH, not label."
    return _post("remove_actor_from_data_layer", actorPath=actor_path, name=name)


@mcp.tool()
def list_foliage_instances(foliage_type: str = None, include_instances: bool = False,
                           limit: int = 200) -> dict:
    "Enumerate the foliage in the open level, by TYPE. This is the read half of add_foliage_instances, which could place foliage while nothing could enumerate it - so a placement could not be verified even in principle."
    return _post("list_foliage_instances", foliageType=foliage_type,
                 includeInstances=include_instances, limit=limit)


@mcp.tool()
def list_ik_rig(path: str) -> dict:
    "Read an IKRigDefinition AND check whether it would actually work."
    return _post("list_ik_rig", path=path)


@mcp.tool()
def analyze_skeletal_split(path: str, lod: int = 0) -> dict:
    "What splitting a SkeletalMesh WOULD produce, without splitting it. Reports each render section's vertex/triangle counts and the bones it is skinned to, then per bone which sections it reaches - a bone touching exactly ONE section can be"
    return _post("analyze_skeletal_split", path=path, lod=lod)


@mcp.tool()
def list_bones(path: str, name_contains: str = "", root: str = "",
               include_transforms: bool = False) -> dict:
    "List the bones of a Skeleton or SkeletalMesh, with the hierarchy."
    return _post("list_bones", path=path, nameContains=name_contains or None,
                 root=root or None, includeTransforms=include_transforms)


@mcp.tool()
def list_virtual_bones(path: str) -> dict:
    "List virtual bones on a Skeleton - links a rigger added BETWEEN two real bones, baked into every animation on that skeleton at playback time."
    return _post("list_virtual_bones", path=path)


@mcp.tool()
def list_morph_targets(path: str, lod: int = 0) -> dict:
    "List morph target names on a SkeletalMesh, with per-LOD data presence."
    return _post("list_morph_targets", path=path, lod=lod)


@mcp.tool()
def list_sockets(path: str) -> dict:
    "List the sockets on a SkeletalMesh or StaticMesh asset."
    return _post("list_sockets", path=path)


@mcp.tool()
def set_niagara_component_parameter(actor_path: str, name: str, value, type: str = None,
                                    component: str = None, confirm: bool = False) -> dict:
    "Override a Niagara user parameter on a PLACED COMPONENT in the open level. Deliberately not on the system asset: editing the asset changes every instance, and modifying a COOKED UNiagaraSystem is a known fatal editor crash, so this never"
    return _post("set_niagara_component_parameter", actorPath=actor_path, name=name, value=value,
                 type=type, component=component, confirm=confirm)


@mcp.tool()
def list_sequence_bindings(path: str) -> dict:
    "What a LevelSequence actually binds - guid, name, kind (possessable/spawnable), class, and the tracks on each."
    return _post("list_sequence_bindings", path=path)


@mcp.tool()
def add_sequence_possessable(path: str, actor_path: str, confirm: bool = False) -> dict:
    "Bind an actor from the OPEN level into a LevelSequence. Requires confirm=True - it modifies a shared asset."
    return _post("add_sequence_possessable", path=path, actorPath=actor_path, confirm=confirm)


@mcp.tool()
def add_sequence_track(path: str, guid: str, track_class: str, confirm: bool = False, root: bool = False, camera_cut: bool = False, time: float = 0.0) -> dict:
    "Add a track to a binding. guid comes from list_sequence_bindings; track_class is a UMovieSceneTrack class PATH such as /Script/MovieSceneTracks.MovieScene3DTransformTrack. Requires confirm=True."
    return _post("add_sequence_track", path=path, guid=guid, trackClass=track_class,
                 confirm=confirm, root=root, cameraCut=camera_cut, time=time)


@mcp.tool()
def add_sequence_section(path: str, guid: str, start_time: float, end_time: float,
                         track_class: str = "", track_index: int = -1,
                         row_index: int = 0, confirm: bool = False) -> dict:
    "Create a SECTION on a LevelSequence track and give it a time range - the step without which the rest of the sequencer write chain animates nothing. add_sequence_track's own response says the track it makes is EMPTY; this is what fills it."
    return _post("add_sequence_section", path=path, guid=guid, startTime=start_time,
                 endTime=end_time, trackClass=track_class, trackIndex=track_index,
                 rowIndex=row_index, confirm=confirm)


@mcp.tool()
def set_sequence_keys(path: str, guid: str, channel: str, keys: list,
                      track_class: str = "", track_index: int = -1, section_index: int = 0,
                      replace: bool = False, confirm: bool = False) -> dict:
    "Write keyframes onto a section's channel - the last step, and the one that makes a LevelSequence actually animate."
    return _post("set_sequence_keys", path=path, guid=guid, channel=channel, keys=keys,
                 trackClass=track_class, trackIndex=track_index, sectionIndex=section_index,
                 replace=replace, confirm=confirm)


@mcp.tool()
def list_state_trees(path_prefix: str = "/Game/") -> dict:
    "List the project's StateTree assets - the modern UE5 alternative to Behavior Trees. Asset Registry only, LOADS NOTHING. Check registryStillScanning."
    return _post("list_state_trees", pathPrefix=path_prefix)


@mcp.tool()
def describe_state_tree(path: str) -> dict:
    "Describe a StateTree: its states with name, type, parent index and expanded child indices, plus the SCHEMA, which decides what the tree can be run against (an actor, a component, a mass entity) - a tree attached to the wrong thing is"
    return _post("describe_state_tree", path=path)


@mcp.tool()
def list_gameplay_tags(filter: str = None, only_explicit: bool = True, limit: int = 0) -> dict:
    "Every gameplay tag REGISTERED IN THE RUNNING EDITOR. This is not the same as reading DefaultGameplayTags.ini: the tag table is assembled at runtime from ini files, other config, and native UE_DEFINE_GAMEPLAY_TAG registration in C++ and"
    return _post("list_gameplay_tags", filter=filter, onlyExplicit=only_explicit, limit=limit)


@mcp.tool()
def describe_gameplay_tag(tag: str) -> dict:
    "One gameplay tag: whether it exists, its parent chain, its direct children. A tag that does NOT exist returns ok:true with exists:false rather than an error - 'does this tag exist' answered with 'no' is a successful call, and it does not"
    return _post("describe_gameplay_tag", tag=tag)


@mcp.tool()
def add_gameplay_tag(tag: str, comment: str = "", source: str = "", transient: bool = False) -> dict:
    "Author a gameplay tag. TWO MODES and the difference is where the tag lives. transient=True registers it for THIS EDITOR SESSION only - writes nothing to disk, allowed in every write mode, gone on restart; that is the one you usually want"
    return _post("add_gameplay_tag", tag=tag, comment=comment, source=source, transient=transient)


@mcp.tool()
def live_coding_status() -> dict:
    "Whether Live Coding is running in this editor, and CRUCIALLY whether it is holding the editor's DLLs."
    return _post("live_coding_status")


@mcp.tool()
def live_coding_compile(confirm: bool = False) -> dict:
    "Start a Live Coding compile - it patches newly compiled C++ into the RUNNING editor. Requires confirm=True, because a bad patch can destabilise the process holding unsaved work."
    return _post("live_coding_compile", confirm=confirm)


@mcp.tool()
def list_pcg_graphs(path_prefix: str = "/Game/") -> dict:
    "List the project's PCGGraph assets. Asset Registry only - LOADS NOTHING. Check registryStillScanning: at startup a low count can mean 'not finished looking'."
    return _post("list_pcg_graphs", pathPrefix=path_prefix)


@mcp.tool()
def describe_pcg_graph(path: str) -> dict:
    "Describe a PCGGraph: its nodes, each with the SETTINGS CLASS that identifies what the node actually is, plus input/output pin counts."
    return _post("describe_pcg_graph", path=path)


@mcp.tool()
def add_pcg_node(graph: str, settings_class: str, x: int = 0, y: int = 0) -> dict:
    "Add a node to a PCG graph. settings_class is a UPCGSettings subclass name such as PCGSurfaceSamplerSettings or PCGStaticMeshSpawnerSettings; an unknown one is refused with near matches rather than creating anything."
    return _post("add_pcg_node", graph=graph, settingsClass=settings_class, x=x, y=y)


@mcp.tool()
def connect_pcg_nodes(graph: str, from_node: str, from_pin: str, to_node: str,
                      to_pin: str) -> dict:
    "Wire one PCG node's OUTPUT pin to another's INPUT pin. Pins are addressed by LABEL - describe_pcg_graph reports every node's inputPinNames and outputPinNames."
    return _post("connect_pcg_nodes", graph=graph, fromNode=from_node, fromPin=from_pin,
                 toNode=to_node, toPin=to_pin)


@mcp.tool()
def disconnect_pcg_nodes(graph: str, from_node: str, from_pin: str, to_node: str,
                         to_pin: str) -> dict:
    "Remove one edge from a PCG graph, named by the same four values that created it. `removed` is the measured change in the pin's edge count, and is cross-checked against what UPCGGraph::RemoveEdge claimed - a disagreement between the two is"
    return _post("disconnect_pcg_nodes", graph=graph, fromNode=from_node, fromPin=from_pin,
                 toNode=to_node, toPin=to_pin)


@mcp.tool()
def remove_pcg_node(graph: str, node: str, confirm: bool = False) -> dict:
    "Remove a node from a PCG graph by its NAME (from describe_pcg_graph - a settings class is a node TYPE and a graph can hold many of one type)."
    return _post("remove_pcg_node", graph=graph, node=node, confirm=confirm)


@mcp.tool()
def describe_physics_asset(asset_path: str) -> dict:
    "Read a PhysicsAsset's bodies, constraints and - the reason this endpoint exists - its body-pair collision-disable table."
    return _post("describe_physics_asset", assetPath=asset_path)


@mcp.tool()
def add_physics_body(asset_path: str, bone_name: str, geom_type: str = "sphyl",
                     min_bone_size: float = 20.0) -> dict:
    "Create a physics body for one bone in a PhysicsAsset. geom_type is sphyl (alias capsule), sphere, box or taperedCapsule; the convex and level-set types are not offered because they need render geometry this call does not fit against."
    return _post("add_physics_body", assetPath=asset_path, boneName=bone_name, geomType=geom_type,
                 minBoneSize=min_bone_size)


@mcp.tool()
def remove_physics_body(asset_path: str, bone_name: str = "", index: int = -1,
                        confirm: bool = False) -> dict:
    "Remove a physics body, by bone_name (preferred) or index. Requires confirm=True: removal RENUMBERS every body after it, so any index you are holding becomes wrong, and it drops that body's collision-disable pairs."
    return _post("remove_physics_body", assetPath=asset_path, confirm=confirm,
                 boneName=bone_name or None, index=None if bone_name else index)


@mcp.tool()
def add_physics_constraint(asset_path: str, bone1: str, bone2: str, name: str = "") -> dict:
    "Create a constraint joining two physics bodies in a PhysicsAsset - the joints that make a ragdoll hang together rather than fall apart."
    return _post("add_physics_constraint", assetPath=asset_path, bone1=bone1, bone2=bone2,
                 name=name or None)


@mcp.tool()
def remove_physics_constraint(asset_path: str, index: int = -1, joint_name: str = "",
                              confirm: bool = False) -> dict:
    "Remove a constraint from a PhysicsAsset, by joint_name (preferred) or index. Requires confirm=True, since removal renumbers every constraint after it."
    # Explicit, not a **payload dict - see set_physics_body_collision for why.
    return _post("remove_physics_constraint", assetPath=asset_path, confirm=confirm,
                 jointName=joint_name or None, index=None if joint_name else index)


@mcp.tool()
def set_physics_body_collision(asset_path: str, enabled: bool, bone_a: str = "", bone_b: str = "",
                               index_a: int = -1, index_b: int = -1) -> dict:
    "Enable or disable collision between two bodies in a PhysicsAsset - the table that stops a ragdoll's neighbouring limbs from fighting each other. Address the pair by bone_a/bone_b (preferred) or index_a/index_b."
    # Passed explicitly rather than through a **payload dict so param_reach can SEE them - a
    # dict-built call is invisible to the static check, which would report these as unreachable
    # and hide a real gap behind a baseline entry. The handler prefers a bone name whenever one
    # is given, so sending the unused index alongside is harmless.
    return _post("set_physics_body_collision", assetPath=asset_path, enabled=enabled,
                 boneA=bone_a or None, boneB=bone_b or None,
                 indexA=None if bone_a else index_a, indexB=None if bone_b else index_b)


@mcp.tool()
def set_physics_primitive_collision(asset_path: str, primitive_type: str, primitive_index: int,
                                    collision_enabled: str, bone_name: str = "",
                                    index: int = -1) -> dict:
    "Set collision on ONE collision primitive of one physics body - how you stop a single capsule on a body colliding while the rest still do."
    return _post("set_physics_primitive_collision", assetPath=asset_path,
                 primitiveType=primitive_type, primitiveIndex=primitive_index,
                 collisionEnabled=collision_enabled, boneName=bone_name or None,
                 index=None if bone_name else index)


@mcp.tool()
def add_socket(path: str, name: str, bone: str, location: dict = None, rotation: dict = None,
               scale: dict = None, target: str = "") -> dict:
    "Create a socket on a SkeletalMesh or Skeleton - the attach point a weapon, prop, VFX emitter or camera boom hangs off, and the one socket verb that had no equivalent."
    return _post("add_socket", path=path, name=name, bone=bone, location=location,
                 rotation=rotation, scale=scale, target=target or None)


@mcp.tool()
def run_retarget(retargeter: str, animations: list, source_mesh: str = "", target_mesh: str = "",
                 prefix: str = "", suffix: str = "", search: str = "", replace: str = "",
                 remap_referenced_assets: bool = False, confirm: bool = False) -> dict:
    "Run a configured IK Retargeter over animation assets, producing retargeted duplicates on the target skeleton."
    return _post("run_retarget", retargeter=retargeter, animations=animations,
                 sourceMesh=source_mesh, targetMesh=target_mesh, prefix=prefix, suffix=suffix,
                 search=search, replace=replace, remapReferencedAssets=remap_referenced_assets,
                 confirm=confirm)


@mcp.tool()
def add_virtual_bone(skeleton: str, source: str, target: str, name: str = "") -> dict:
    "Create a virtual bone on a USkeleton - the synthetic bones (hand-relative-to-hip, foot-relative-to-root) that IK and retargeting chains are usually built against."
    return _post("add_virtual_bone", skeleton=skeleton, source=source, target=target,
                 name=name or None)


@mcp.tool()
def remove_virtual_bone(skeleton: str, name: str = "", names: list = None,
                        confirm: bool = False) -> dict:
    "Remove one or more virtual bones from a USkeleton. Requires confirm=True because removal REPARENTS other bones: USkeleton::RemoveVirtualBones rewires every virtual bone whose source was a removed one to point at that bone's own source, so"
    return _post("remove_virtual_bone", skeleton=skeleton, name=name or None,
                 names=names, confirm=confirm)


@mcp.tool()
def rename_virtual_bone(skeleton: str, name: str, new_name: str) -> dict:
    "Rename a virtual bone on a USkeleton. The original is verified to exist first because USkeleton::RenameVirtualBone returns VOID and does nothing quietly when the name matches nothing - a typo would otherwise look like success."
    return _post("rename_virtual_bone", skeleton=skeleton, name=name, newName=new_name)


@mcp.tool()
def add_anim_curve(asset_path: str, name: str, type: str = "float") -> dict:
    "Declare a curve on an AnimSequence or AnimMontage - the per-frame scalar tracks that drive material parameters, IK alpha, morph weights and curve-driven gameplay. type is 'float' or 'transform'."
    return _post("add_anim_curve", assetPath=asset_path, name=name, type=type)


@mcp.tool()
def set_anim_curve_keys(asset_path: str, name: str, keys: list, append: bool = False,
                        type: str = "float") -> dict:
    "Write keys onto an existing float curve. keys is [{time, value, interp?}] where interp is linear (default), constant or cubic. REPLACES the curve's keys by default; pass append=True to add to what is there."
    return _post("set_anim_curve_keys", assetPath=asset_path, name=name, keys=keys,
                 append=append, type=type)


@mcp.tool()
def remove_anim_curve(asset_path: str, name: str, type: str = "float",
                      confirm: bool = False) -> dict:
    "Remove a curve and its keys from an AnimSequence or AnimMontage. Requires confirm=True, and the refusal states how many keys would be destroyed. Uncooked only, same checkf guard as add_anim_curve."
    return _post("remove_anim_curve", assetPath=asset_path, name=name, type=type, confirm=confirm)


@mcp.tool()
def lighting_build_status() -> dict:
    "Report the OPEN level's static-lighting state: whether a Lightmass build is running, how many objects and reflection captures are still unbuilt, and whether the level is actually built."
    return _post("lighting_build_status")


@mcp.tool()
def move_actors_to_level(actor_paths: list, level: str, all_or_fail: bool = True,
                         confirm: bool = False) -> dict:
    "Move already-placed actors from one level into another - persistent to sublevel, or between sublevels. `level` is a sublevel package path or 'persistent'."
    return _post("move_actors_to_level", actorPaths=actor_paths, level=level,
                 allOrFail=all_or_fail, confirm=confirm)


@mcp.tool()
def list_level_instances(include_actors: bool = False, limit: int = 500) -> dict:
    "List the Level Instance actors placed in the open world - UE5's prefab - with the level asset each points at, whether it is loaded or being edited, its bounds and how many actors it contains."
    return _post("list_level_instances", includeActors=include_actors, limit=limit)


@mcp.tool()
def set_level_instance_loaded(actor_path: str, loaded: bool) -> dict:
    "Load or unload one placed Level Instance in the editor. Loading is not visibility: an unloaded instance has no actors at all."
    return _post("set_level_instance_loaded", actorPath=actor_path, loaded=loaded)


@mcp.tool()
def edit_level_instance(actor_path: str, action: str, discard_edits: bool = False) -> dict:
    "Open, commit or discard an edit session on a Level Instance. action is 'edit', 'commit' or 'discard'. Changes affect EVERY placement of that level asset, and a commit WRITES its package."
    return _post("edit_level_instance", actorPath=actor_path, action=action,
                 discardEdits=discard_edits)


@mcp.tool()
def break_level_instance(actor_path: str, levels: int = 1, confirm: bool = False) -> dict:
    "Break a Level Instance into loose actors. DESTRUCTIVE and one-way: the link to the level asset is gone, so later changes to that asset will no longer reach these actors. Requires confirm=True."
    return _post("break_level_instance", actorPath=actor_path, levels=levels, confirm=confirm)


@mcp.tool()
def remove_foliage_instances(foliage_type: str, indices: list = None, sphere: dict = None,
                             box: dict = None, all: bool = False,
                             confirm: bool = False) -> dict:
    "Delete painted foliage instances - by index, or by a world-space sphere or box ('clear the trees where the road goes'). The erase half of add_foliage_instances. Pass EXACTLY one selector, plus confirm=True."
    return _post("remove_foliage_instances", foliageType=foliage_type, indices=indices,
                 sphere=sphere, box=box, all=all, confirm=confirm)


@mcp.tool()
def source_control(path: str = "") -> dict:
    "Report whether revision control is configured, and what state a file is in (checked out, checked out by someone else, added, not at head, read-only on disk). Omit path for the provider status alone. READ ONLY - source_control_checkout is the write half."
    return _post("source_control", path=path)


@mcp.tool()
def source_control_checkout(path: str, action: str = "checkout",
                            confirm: bool = False) -> dict:
    "Check out, mark for add, or REVERT a file in revision control. action is checkout, add, checkoutOrAdd or revert; revert requires confirm=True because it discards local changes. Checking IN is deliberately not offered."
    return _post("source_control_checkout", path=path, action=action, confirm=confirm)



@mcp.tool()
def list_pcg_components() -> dict:
    "Every PCG component in the OPEN level, with its owning actor, its graph, and whether it is generated and activated. Reports every component on an actor rather than the first, since one actor can carry several."
    return _post("list_pcg_components")


@mcp.tool()
def pcg_generate(actor_path: str, confirm: bool = False) -> dict:
    "Run a PCG component's graph, spawning its output into the OPEN level. Requires confirm=True: this can spawn THOUSANDS of actors - that is what it is for - and there is no single undo."
    return _post("pcg_generate", actorPath=actor_path, confirm=confirm)


@mcp.tool()
def pcg_cleanup(actor_path: str, confirm: bool = False) -> dict:
    "Remove the actors a PCG component generated. Requires confirm=True - it destroys generated content. Asynchronous like generation. Nothing is saved."
    return _post("pcg_cleanup", actorPath=actor_path, confirm=confirm)


@mcp.tool()
def add_blackboard_key(path: str, name: str, type: str, instance_synced: bool = False,
                       category: str = None, confirm: bool = False) -> dict:
    "Add a key to a BlackboardData asset. type is one of Bool, Int, Float, String, Name, Vector, Rotator, Object, Class, Enum - an unknown type is REFUSED rather than creating a key with a null KeyType, which the asset accepts and nothing can"
    return _post("add_blackboard_key", path=path, name=name, type=type,
                 instanceSynced=instance_synced, category=category, confirm=confirm)


@mcp.tool()
def describe_behavior_tree(path: str) -> dict:
    "Read a BehaviorTree's structure: root, node tree, and which blackboard it uses."
    return _post("describe_behavior_tree", path=path)


@mcp.tool()
def list_blackboard_keys(path: str) -> dict:
    "List a BlackboardData asset's keys, with type and whether each is inherited from a parent."
    return _post("list_blackboard_keys", path=path)


@mcp.tool()
def set_blendspace_samples(asset_path: str, samples: list, clear: bool = True) -> dict:
    "Place animation samples in a BlendSpace. samples is [{animation, x, y?}] - each entry names an AnimSequence and its position on the blend axes; y is ignored by a 1D BlendSpace."
    return _post("set_blendspace_samples", assetPath=asset_path, samples=samples, clear=clear)


@mcp.tool()
def set_bone_translation_retargeting(skeleton_path: str, bone_name: str, mode: str,
                                     children_too: bool = False) -> dict:
    "Set how a Skeleton retargets a bone's TRANSLATION. mode is one of Animation, Skeleton, AnimationScaled, AnimationRelative or OrientAndScale."
    return _post("set_bone_translation_retargeting", skeletonPath=skeleton_path, boneName=bone_name,
                 mode=mode, childrenToo=children_too)


@mcp.tool()
def describe_animation(asset_path: str) -> dict:
    "Describe an animation asset: skeleton, playLength, notifies (with notify-state windows and branching points), curves."
    return _post("describe_animation", assetPath=asset_path)


@mcp.tool()
def add_anim_notify(asset_path: str, time: float, track: str = "", name: str = "",
                    notify_class: str = "", notify_state_class: str = "",
                    duration: float = 0.1) -> dict:
    "Place a notify on an AnimSequence, AnimMontage or AnimComposite - the write half of what describe_animation has always been able to READ."
    return _post("add_anim_notify", assetPath=asset_path, time=time, track=track, name=name,
                 notifyClass=notify_class, notifyStateClass=notify_state_class, duration=duration)


@mcp.tool()
def remove_anim_notify(asset_path: str, name: str = "", track: str = "",
                       confirm: bool = False) -> dict:
    "Remove notifies from an animation, either every one with a given name or every one on a given track. Exactly one of name or track - passing neither would mean removing everything, so it is refused rather than guessed at."
    return _post("remove_anim_notify", assetPath=asset_path, name=name, track=track,
                 confirm=confirm)


@mcp.tool()
def add_anim_notify_track(asset_path: str, track: str) -> dict:
    "Create a named notify track on an animation - the row notifies sit on in the notify panel. Adding a track that already exists is created:false with a note rather than an error."
    return _post("add_anim_notify_track", assetPath=asset_path, track=track)


@mcp.tool()
def remove_anim_notify_track(asset_path: str, track: str, confirm: bool = False) -> dict:
    "Remove a notify track, and every notify on it - hence confirm=True, with the refusal naming the count first."
    return _post("remove_anim_notify_track", assetPath=asset_path, track=track, confirm=confirm)


# --------------------------------------------------------------------------
# Asset lifecycle (/Game/ only, headless)
# --------------------------------------------------------------------------

@mcp.tool()
def close_asset_editors(path: str, confirm: bool = False) -> dict:
    "Close every open asset editor holding an asset. Deliberately SEPARATE from delete_asset: closing an editor can discard unsaved work in that tab, so the caller opts in rather than a delete doing it silently. Requires confirm=True."
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
    "Read a StaticMesh's OWN collision - simple primitive count, convex hull count, the collisionComplexity flag by NAME, whether it has a BodySetup at all, and per-section collisionEnabled for the given LOD."
    return _post("get_collision", path=path, lod=lod)


@mcp.tool()
def remove_collision(path: str, confirm: bool = False) -> dict:
    "Clear ALL simple collision from a StaticMesh - the StaticMeshEditor's 'Remove Collision' button, reachable without opening that editor. Requires confirm=True because it destroys hand-authored convex hulls with no undo across HTTP."
    return _post("remove_collision", path=path, confirm=confirm)


@mcp.tool()
def list_collision_profiles() -> dict:
    "List the collision profiles THIS project defines, with what each resolves to."
    return _post("list_collision_profiles")


@mcp.tool()
def set_collision(object_path: str, profile: str = "", collision_enabled: str = "") -> dict:
    "Set a primitive component's collision profile, with the profile name CHECKED."
    return _post("set_collision", objectPath=object_path, profile=profile,
                 collisionEnabled=collision_enabled)


@mcp.tool()
def add_simplified_collision(path: str, shape: str) -> dict:
    "Generate one simple collision primitive on a StaticMesh - the StaticMeshEditor's collision toolbar (Add Box/Sphere/Capsule/K-DOP Simplified Collision), reachable without opening that editor."
    return _post("add_simplified_collision", path=path, shape=shape)


@mcp.tool()
def get_referencers(path: str) -> dict:
    "Which packages reference this asset. Authoritative - reads the asset registry's dependency graph, so it is immune to the FName trap where a trailing _<digits> is stored as a separate number and a literal name search misses real references."
    return _post("get_referencers", path=path)


@mcp.tool()
def get_dependencies(path: str) -> dict:
    "Which packages this asset references (the inverse of get_referencers). Same shape: package == packageName, and every dependencies[] entry is a PACKAGE path."
    return _post("get_dependencies", path=path)


@mcp.tool()
def audit_unused(path_prefix: str, cls: str = "", include_all: bool = False,
                 limit: int = 4000, rescan: bool = False,
                 exclude_referencers: list = None) -> dict:
    "Find unused assets under a folder in one call. Returns, per asset, objectPath + packageName (same spelling as find_assets), refs (total referencing packages) and extRefs (those outside its own folder)."
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
    "Mint a persistent EDITABLE copy of a cooked Blueprint (whose graphs are stripped and cannot be read directly). source_asset is the cooked BP's _C class path or asset path."
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
    "Spawn an actor into the RUNNING PIE world. spawn_actor_in_level cannot do this - it goes through UEditorActorSubsystem, which serves the EDITOR world."
    return _post("spawn_actor_in_pie", actorClass=actor_class, location=location,
                 rotation=rotation, scale=scale, label=label, netMode=net_mode, mesh=mesh)


# --------------------------------------------------------------------------
# Undo introspection / rollback + dirty-package flows
# --------------------------------------------------------------------------

@mcp.tool()
def list_transactions(limit: int = 20, offset: int = 0, include_objects: bool = False) -> dict:
    "Inspect the editor undo buffer, newest first (offset 0 = newest). Returns queueLength, undoCount, currentIndex (= queueLength - undoCount - 1, the entry the next undo removes), canUndo/canRedo, undoBarrier, nextUndoTitle, and"
    return _post("list_transactions", limit=limit, offset=offset,
                 includeObjects=include_objects or None)


@mcp.tool()
def undo_transactions(count: int = 1, to_index: int = None, allow_redo: bool = True) -> dict:
    "Undo the last N editor transactions (count 1..50), or pass to_index to undo down until currentIndex == to_index (-1 = undo everything; capped at 50 steps per call - call again to continue). count and to_index are mutually exclusive."
    return _post("undo_transactions",
                 count=None if to_index is not None else count,
                 toIndex=to_index, allowRedo=allow_redo)


@mcp.tool()
def redo_transactions(count: int = 1, to_index: int = None) -> dict:
    "Redo the last N undone transactions (count 1..50), or pass to_index to redo up until currentIndex == to_index (capped at 50 steps per call). Returns redone, titlesRedone, stoppedEarly(+reason), and the new queue position."
    return _post("redo_transactions",
                 count=None if to_index is not None else count,
                 toIndex=to_index)


@mcp.tool()
def list_dirty_packages(kind: str = "all") -> dict:
    "List every unsaved (dirty) package - what a crash would lose and what save_dirty_packages will touch. kind: content | world | all."
    return _post("list_dirty_packages", kind=kind)


@mcp.tool()
def save_dirty_packages(maps: bool = True, content: bool = True, dry_run: bool = False) -> dict:
    "Save EVERY dirty package in one prompt-free, checkout-free call (per-package saves; deliberately avoids the engine bulk path, whose failure dialog would deadlock the bridge's game thread)."
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
    "Create a NEW master UMaterial asset at a /Game/ path. domain: Surface | DeferredDecal | LightFunction | Volume | PostProcess | UI. blend_mode: Opaque | Masked | Translucent | Additive | Modulate | AlphaComposite | AlphaHoldout."
    return _post("create_material", path=path, domain=domain, blendMode=blend_mode,
                 initialTexture=initial_texture or None)


@mcp.tool()
def create_material_function(path: str, description: str = "", expose_to_library: bool = None) -> dict:
    "Create a NEW UMaterialFunction asset at a /Game/ path (reusable graph fragment; call it from materials via add_material_expression class=MaterialFunctionCall). Add FunctionInput/FunctionOutput expressions to define its interface."
    return _post("create_material_function", path=path, description=description or None,
                 exposeToLibrary=expose_to_library)


@mcp.tool()
def add_material_expression(path: str, expression_class: str, x: int = 0, y: int = 0,
                            properties: dict = None, asset: str = "") -> dict:
    "Add a node to a Material or MaterialFunction graph. expression_class accepts short names (ScalarParameter, VectorParameter, TextureSample, Multiply, Add, Lerp, TexCoord, Fresnel, FunctionInput, ...) or full MaterialExpression* names;"
    return _post("add_material_expression", path=path, expressionClass=expression_class,
                 x=x, y=y, properties=properties, asset=asset or None)


@mcp.tool()
def connect_material_expressions(path: str, from_expression: str, to_expression: str,
                                 from_output: str = "", to_input: str = "") -> dict:
    "Wire one expression's output into another expression's input inside a material/function graph."
    return _post("connect_material_expressions", path=path, fromExpression=from_expression,
                 fromOutput=from_output or None, toExpression=to_expression,
                 toInput=to_input or None)


@mcp.tool()
def connect_material_property(path: str, from_expression: str, material_property: str,
                              from_output: str = "") -> dict:
    "Wire an expression output into a MATERIAL OUTPUT pin - without this the graph never affects pixels."
    return _post("connect_material_property", path=path, fromExpression=from_expression,
                 fromOutput=from_output or None, materialProperty=material_property)


@mcp.tool()
def delete_material_expression(path: str, expression: str = "", delete_all: bool = False) -> dict:
    "Remove one node (expression=<name>) or every node (delete_all=True) from a material/function graph; the engine disconnects it from everything first. Exactly one of the two must be given."
    return _post("delete_material_expression", path=path, expression=expression or None,
                 deleteAll=delete_all or None)


@mcp.tool()
def list_niagara_user_parameters(path: str, name_contains: str = "") -> dict:
    "Read a NiagaraSystem's User. parameters, with their VALUES."
    return _post("list_niagara_user_parameters", path=path, nameContains=name_contains or None)


@mcp.tool()
def list_material_parameters(path: str, types: list = None, group: str = "") -> dict:
    "List the parameters a Material or MaterialInstance EXPOSES."
    return _post("list_material_parameters", path=path, types=types or [], group=group)


@mcp.tool()
def list_material_expressions(path: str, include_connections: bool = True,
                              include_properties: bool = True) -> dict:
    "Read back a material/function graph: expressions[{name, class, index, x, y, properties{}, inputs[{input, from, fromOutput}]}], connectionCount, and (materials) propertyBindings[{property, from, fromOutput}] - the verification read for"
    return _post("list_material_expressions", path=path,
                 includeConnections=include_connections, includeProperties=include_properties)


@mcp.tool()
def layout_material_expressions(path: str) -> dict:
    "Auto-arrange a material/function graph's nodes in a grid so a human opening the asset sees something readable. Only nodes REACHABLE from material property inputs (or function inputs/outputs) are moved - disconnected nodes stay put."
    return _post("layout_material_expressions", path=path)


@mcp.tool()
def recompile_material(path: str) -> dict:
    "Apply graph/parameter edits to the renderer - REQUIRED after add/connect/delete for the changes to reach pixels."
    return _post("recompile_material", path=path)


@mcp.tool()
def shader_compile_status() -> dict:
    "Poll the editor-wide shader compiler (GShaderCompilingManager): {compiling, numRemainingJobs, numOutstandingJobs, numPendingJobs}. THE poll half for recompile_material / create_material (and level-load shader churn)."
    return _post("shader_compile_status")


# --------------------------------------------------------------------------
# Enhanced Input authoring
# (Registry drift: this endpoint has had MIF_DECL + MIF_BIND since Nodes7 landed
#  but never got an @mcp.tool, so it was unreachable over MCP.)
# --------------------------------------------------------------------------

@mcp.tool()
def list_input_mappings(path: str) -> dict:
    "Read an InputMappingContext: which key is bound to which Input Action, with the triggers and modifiers on each mapping."
    return _post("list_input_mappings", path=path)


@mcp.tool()
def map_input_key(context: str, action: str, key: str) -> dict:
    "Bind a key to an Input Action inside an InputMappingContext - the write half of list_input_mappings, and the step that connects the two ends the bridge could already build separately (create_asset makes the context,"
    return _post("map_input_key", context=context, action=action, key=key)


@mcp.tool()
def unmap_input_key(context: str, action: str = "", key: str = "", all: bool = False,
                    confirm: bool = False) -> dict:
    "Remove key bindings from an InputMappingContext. With action and key, unbinds that one pair; with action alone, unbinds EVERY key from that one action; with all=True AND confirm=True, clears the entire context."
    return _post("unmap_input_key", context=context, action=action, key=key, all=all,
                 confirm=confirm)


@mcp.tool()
def list_legacy_input_mappings(name: str = "") -> dict:
    "Read the project's LEGACY (pre-Enhanced) input bindings from UInputSettings - action mappings and axis mappings, which are separate families with different fields."
    return _post("list_legacy_input_mappings", name=name)


@mcp.tool()
def map_legacy_input(name: str, key: str, axis: bool = False, scale: float = 1.0,
                     shift: bool = False, ctrl: bool = False, alt: bool = False,
                     cmd: bool = False) -> dict:
    "Add a LEGACY (pre-Enhanced) input binding to UInputSettings. name is a bare action or axis name, not an asset path; key is an FKey name such as SpaceBar."
    return _post("map_legacy_input", name=name, key=key, axis=axis, scale=scale, shift=shift,
                 ctrl=ctrl, alt=alt, cmd=cmd)


@mcp.tool()
def unmap_legacy_input(name: str, key: str, axis: bool = False, scale: float = 1.0,
                       shift: bool = False, ctrl: bool = False, alt: bool = False,
                       cmd: bool = False) -> dict:
    "Remove a LEGACY input binding from UInputSettings. A legacy mapping matches on name, key AND every modifier, so removing Ctrl+S needs ctrl=True - without it you are asking to remove a different binding and nothing will match."
    return _post("unmap_legacy_input", name=name, key=key, axis=axis, scale=scale, shift=shift,
                 ctrl=ctrl, alt=alt, cmd=cmd)


@mcp.tool()
def save_input_settings(confirm: bool = False) -> dict:
    "Persist the legacy input mappings to Config/DefaultInput.ini via UInputSettings::SaveKeyMappings."
    return _post("save_input_settings", confirm=confirm)


@mcp.tool()
def list_settings(container: str = "", category: str = "", name_contains: str = "",
                  limit: int = 500) -> dict:
    "Enumerate the project's settings sections - Project Settings, Editor Preferences and every plugin's settings page, which are all UDeveloperSettings CDOs."
    return _post("list_settings", container=container, category=category,
                 nameContains=name_contains, limit=limit)


@mcp.tool()
def add_enhanced_input_action(graph_id: str, input_action: str, x: int = 0, y: int = 0) -> dict:
    "Add a UK2Node_EnhancedInputAction event node (the 'IA_Foo' node you normally get by right-clicking the graph and searching for the action asset) - the one node class the bridge could not author, which forced every Enhanced Input binding to"
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
    "Start an Unreal Insights trace, writing a .utrace under Saved/MifBridge/Traces. This is the answer to 'which Blueprint is burning frame time' that perf_heavy_actors cannot give: that one reports a static CENSUS (triangles, components,"
    return _post("trace_start", channels=channels)


@mcp.tool()
def trace_stop() -> dict:
    "Stop the trace started by trace_start and report where the file went and how big it is. The size is the evidence it captured anything - a zero-byte trace means the channels produced no data, which otherwise looks identical to success."
    return _post("trace_stop")


@mcp.tool()
def perf_heavy_actors(limit: int = 40, sort_by: str = None) -> dict:
    "Rank the level's actors by STATIC content cost: LOD0 triangles, primitive components, material slots, and a rough draw estimate (components x material slots)."
    return _post("perf_heavy_actors", limit=limit, sortBy=sort_by)


@mcp.tool()
def blueprint_inheritance_tree(path_prefix: str = "/Game/", root: str = None,
                               max_depth: int = 0) -> dict:
    "The project's Blueprint class hierarchy, built ENTIRELY from asset registry tags - it LOADS NOTHING."
    return _post("blueprint_inheritance_tree", pathPrefix=path_prefix, root=root,
                 maxDepth=max_depth)


@mcp.tool()
def project_dependency_graph(path_prefix: str, max_nodes: int = 300,
                             include_external: bool = False, mermaid: bool = False) -> dict:
    "The dependency graph under a path prefix: nodes (packages) and edges (A depends on B). Each node reports dependsOn AND referencedBy, because they answer different questions - 'what does this need' versus 'what breaks if I delete it'."
    return _post("project_dependency_graph", pathPrefix=path_prefix, maxNodes=max_nodes,
                 includeExternal=include_external, mermaid=mermaid)


@mcp.tool()
def project_asset_distribution(path_prefix: str = None, top_folders: int = 25,
                               top_classes: int = 25) -> dict:
    "Counts of assets by class and by folder under a path prefix (default /Game). Cheap by construction - pure Asset Registry, loads nothing, never touches referencers - which is why this one accepts a bare /Game where project_dependency_graph"
    return _post("project_asset_distribution", pathPrefix=path_prefix,
                 topFolders=top_folders, topClasses=top_classes)


@mcp.tool()
def set_data_layer_visibility(name: str, visible: bool) -> dict:
    "Show or hide a World Partition Data Layer in the editor. Reports before/after/changed plus a separate `verified` flag, because the underlying SetDataLayerVisibility returns VOID and cannot fail loudly - verified:false means the write did"
    return _post("set_data_layer_visibility", name=name, visible=visible)


@mcp.tool()
def set_data_layer_loaded_in_editor(name: str, loaded: bool,
                                    from_user_change: bool = True) -> dict:
    "Load or unload a World Partition Data Layer's actors in the EDITOR. This is not the same as visibility - an unloaded layer is not in memory at all, where a hidden one is."
    return _post("set_data_layer_loaded_in_editor", name=name, loaded=loaded,
                 fromUserChange=from_user_change)


@mcp.tool()
def list_game_feature_plugins(name_contains: str = None, active_only: bool = False) -> dict:
    "List the project's Game Feature plugins - how content is added to a shipped game without patching the base game - with their derived state and the raw predicates behind it."
    return _post("list_game_feature_plugins", nameContains=name_contains, activeOnly=active_only)


@mcp.tool()
def describe_game_feature_plugin(name: str) -> dict:
    "Describe one Game Feature plugin by NAME (like 'DDS2Casino'), not by asset path: its derived state, descriptor fields, and modules."
    return _post("describe_game_feature_plugin", name=name)


@mcp.tool()
def describe_niagara_system(path: str) -> dict:
    "Describe a NiagaraSystem: how many emitters it has and how many are actually ENABLED."
    return _post("describe_niagara_system", path=path)


@mcp.tool()
def create_procedural_mesh(path: str, shape: str,
                           dimension_x: float = None, dimension_y: float = None, dimension_z: float = None,
                           steps: int = None, radius: float = None,
                           steps_phi: int = None, steps_theta: int = None,
                           height: float = None, radial_steps: int = None, height_steps: int = None,
                           capped: bool = None, base_radius: float = None, top_radius: float = None,
                           major_radius: float = None, minor_radius: float = None,
                           major_steps: int = None, minor_steps: int = None) -> dict:
    "Generate a procedural StaticMesh from GeometryScript and create it fresh at `path` (must not already exist - this never overwrites). shape is one of box, sphere, cylinder, cone, torus."
    return _post("create_procedural_mesh", path=path, shape=shape,
                 dimensionX=dimension_x, dimensionY=dimension_y, dimensionZ=dimension_z,
                 steps=steps, radius=radius, stepsPhi=steps_phi, stepsTheta=steps_theta,
                 height=height, radialSteps=radial_steps, heightSteps=height_steps, capped=capped,
                 baseRadius=base_radius, topRadius=top_radius,
                 majorRadius=major_radius, minorRadius=minor_radius,
                 majorSteps=major_steps, minorSteps=minor_steps)


@mcp.tool()
def create_level_snapshot(path: str, name: str = None, description: str = None) -> dict:
    "Capture the CURRENT editor world's state (every actor's properties) into a new LevelSnapshot asset at `path` (must not already exist). This is a rollback point - use apply_level_snapshot to restore it later."
    return _post("create_level_snapshot", path=path, name=name, description=description)


@mcp.tool()
def describe_level_snapshot(path: str) -> dict:
    "Read-only summary of an existing LevelSnapshot asset: numSavedActors, mapPath (the level it was captured in), captureTime, snapshotName, description."
    return _post("describe_level_snapshot", path=path)


@mcp.tool()
def apply_level_snapshot(path: str) -> dict:
    "Restore every captured property from a LevelSnapshot asset back onto the CURRENT editor world - the actual rollback."
    return _post("apply_level_snapshot", path=path)


@mcp.tool()
def push_livelink_transform(subject_name: str,
                            location_x: float = None, location_y: float = None, location_z: float = None,
                            rotation_pitch: float = None, rotation_yaw: float = None, rotation_roll: float = None,
                            scale_x: float = None, scale_y: float = None, scale_z: float = None) -> dict:
    "Push ONE synthetic Transform-role LiveLink frame under subject_name through a scratch, session-local source - no PIE, no capture hardware, no Blueprint virtual subject needed."
    return _post("push_livelink_transform", subjectName=subject_name,
                 locationX=location_x, locationY=location_y, locationZ=location_z,
                 rotationPitch=rotation_pitch, rotationYaw=rotation_yaw, rotationRoll=rotation_roll,
                 scaleX=scale_x, scaleY=scale_y, scaleZ=scale_z)


@mcp.tool()
def describe_livelink_subject(subject_name: str) -> dict:
    "Read-only: evaluates the CURRENT frame for a LiveLink subject through the same ILiveLinkClient path a real Blueprint/component consumer would use - not limited to subjects push_livelink_transform itself created."
    return _post("describe_livelink_subject", subjectName=subject_name)


@mcp.tool()
def add_game_framework_receiver(actor_path: str) -> dict:
    "Register ONE actor as a UGameFrameworkComponentManager receiver, opting it into add_game_framework_component_request's auto-attach system."
    return _post("add_game_framework_receiver", actorPath=actor_path)


@mcp.tool()
def add_game_framework_component_request(receiver_class: str, component_class: str, request_id: str = None) -> dict:
    "Request that every CURRENT and FUTURE receiver actor of receiver_class (registered via add_game_framework_receiver) get an instance of component_class, live. PIE only."
    return _post("add_game_framework_component_request", receiverClass=receiver_class,
                 componentClass=component_class, requestId=request_id)


@mcp.tool()
def remove_game_framework_component_request(request_id: str) -> dict:
    "Release a component request created by add_game_framework_component_request. Every current receiver actor of that request's class immediately loses the component - the manager's own documented behavior, not something this endpoint does by"
    return _post("remove_game_framework_component_request", requestId=request_id)


@mcp.tool()
def add_mvvm_viewmodel(widget_blueprint_path: str, view_model_class: str) -> dict:
    "Add a viewmodel instance to a Widget Blueprint's MVVM view (creating the view if it doesn't have one yet). Returns viewModelName and viewModelId - you need the NAME for add_mvvm_binding's sourceViewModelName."
    return _post("add_mvvm_viewmodel", widgetBlueprintPath=widget_blueprint_path, viewModelClass=view_model_class)


@mcp.tool()
def add_mvvm_binding(widget_blueprint_path: str, source_view_model_name: str, source_property_name: str,
                     destination_widget_name: str, destination_property_name: str, binding_mode: str = None) -> dict:
    "Bind a viewmodel property to a widget property - the actual MVVM connection add_mvvm_viewmodel's FieldNotify groundwork makes possible."
    return _post("add_mvvm_binding", widgetBlueprintPath=widget_blueprint_path,
                 sourceViewModelName=source_view_model_name, sourcePropertyName=source_property_name,
                 destinationWidgetName=destination_widget_name, destinationPropertyName=destination_property_name,
                 bindingMode=binding_mode)


@mcp.tool()
def describe_mvvm_view(widget_blueprint_path: str) -> dict:
    "Read-only: lists every viewmodel and binding on a Widget Blueprint's MVVM view. hasView:false (with empty arrays) means the Blueprint has no MVVM view at all yet - this never CREATES one, unlike add_mvvm_viewmodel/add_mvvm_binding which do"
    return _post("describe_mvvm_view", widgetBlueprintPath=widget_blueprint_path)


@mcp.tool()
def remove_mvvm_viewmodel(widget_blueprint_path: str, view_model_name: str) -> dict:
    "Remove a viewmodel from a Widget Blueprint's MVVM view (view_model_name from add_mvvm_viewmodel or describe_mvvm_view)."
    return _post("remove_mvvm_viewmodel", widgetBlueprintPath=widget_blueprint_path, viewModelName=view_model_name)


@mcp.tool()
def remove_mvvm_binding(widget_blueprint_path: str, binding_id: str) -> dict:
    "Remove a binding from a Widget Blueprint's MVVM view (binding_id from add_mvvm_binding or describe_mvvm_view). Refuses if no binding with that id exists."
    return _post("remove_mvvm_binding", widgetBlueprintPath=widget_blueprint_path, bindingId=binding_id)


@mcp.tool()
def create_mesh_boolean(target_path: str, tool_path: str, operation: str, output_path: str,
                        tool_offset_x: float = None, tool_offset_y: float = None, tool_offset_z: float = None) -> dict:
    "Combine two EXISTING StaticMesh assets (union, intersection, or subtract) into a THIRD, new StaticMesh at output_path (must not already exist)."
    return _post("create_mesh_boolean", targetPath=target_path, toolPath=tool_path, operation=operation,
                 outputPath=output_path, toolOffsetX=tool_offset_x, toolOffsetY=tool_offset_y, toolOffsetZ=tool_offset_z)


@mcp.tool()
def describe_dynamic_mesh(path: str, lod: int = None) -> dict:
    "Read-only geometry stats for a StaticMesh asset via GeometryScript: vertexCount, triangleCount, isClosed, and bounds, converted from the given LOD (default 0). Nothing is written to the source asset."
    return _post("describe_dynamic_mesh", path=path, lod=lod)


@mcp.tool()
def list_niagara_emitters(path: str, name_contains: str = None,
                          include_disabled: bool = True) -> dict:
    "List a NiagaraSystem's emitters with their index, name, GUID and enabled state. Address an emitter by INDEX rather than name where you can: names are not guaranteed unique within a system."
    return _post("list_niagara_emitters", path=path, nameContains=name_contains,
                 includeDisabled=include_disabled)


@mcp.tool()
def list_level_sequences(filter: str = None, limit: int = 0) -> dict:
    "List the project's LevelSequence assets - cutscenes. Pure Asset Registry, so it LOADS NOTHING and cannot trip the cooked-asset hazards that loading an editor asset can. filter is a substring matched against the full object path."
    return _post("list_level_sequences", filter=filter, limit=limit)


@mcp.tool()
def describe_level_sequence(path: str) -> dict:
    "Describe one LevelSequence: duration, frame rates, how many things it possesses or spawns, and whether it drives a camera."
    return _post("describe_level_sequence", path=path)


@mcp.tool()
def list_data_layers() -> dict:
    "List the Data Layers of the World Partition map currently open. Data Layers are how a partitioned world is organised, and list_sublevels cannot see them - that answers about streaming levels, a different mechanism which is empty on a"
    return _post("list_data_layers")


@mcp.tool()
def list_layers(include_actors: bool = False, limit: int = 200) -> dict:
    "The CLASSIC Layers system - editor-time organisation and visibility, NOT World Partition Data Layers (list_data_layers is those; the two are unrelated systems with confusingly similar names)."
    return _post("list_layers", includeActors=include_actors, limit=limit)


@mcp.tool()
def list_partition_actors(class_filter: str = "", name_contains: str = "", data_layer: str = "",
                          loaded_only: bool = False, limit: int = 200) -> dict:
    "Every actor in a World Partition map, LOADED OR NOT, read from the actor descriptors."
    return _post("list_partition_actors", classFilter=class_filter, nameContains=name_contains,
                 dataLayer=data_layer, loadedOnly=loaded_only, limit=limit)


@mcp.tool()
def modify_actor_layers(operation: str, layer: str = "", layers: list = None,
                        actor_paths: list = None, confirm: bool = False) -> dict:
    "Create, delete, populate or select a classic Layer. operation is add | remove | create | delete | select."
    return _post("modify_actor_layers", operation=operation, layer=layer, layers=layers,
                 actorPaths=actor_paths, confirm=confirm)


@mcp.tool()
def set_layer_visibility(visible: bool, layer: str = "", layers: list = None) -> dict:
    "Hide or show a whole classic Layer - the 'hide all the vegetation while I work on the buildings' operation. Takes one name (layer) or several (layers)."
    return _post("set_layer_visibility", layer=layer, layers=layers, visible=visible)


@mcp.tool()
def list_sublevels(world: str = "editor", net_mode: str = "server") -> dict:
    "List the sublevels of a world: persistent{}, sublevels[{packagePath, objectPath, streamingClass, loaded, visible, editorVisible, pending, ...}], count/loadedCount/visibleCount/pendingCount, currentLevel, isPartitioned, ready, and ops[]"
    return _post("list_sublevels", world=world, netMode=net_mode)


@mcp.tool()
def add_sublevel(path: str, streaming_class: str = "alwaysloaded",
                 location: dict = None, rotation: dict = None) -> dict:
    "Add an existing map as a sublevel of the open world. path is a package path (/Game/Maps/TownDistrict). streaming_class = alwaysloaded | dynamic."
    return _post("add_sublevel", path=path, streamingClass=streaming_class,
                 location=location, rotation=rotation)


@mcp.tool()
def remove_sublevel(path: str, discard_unsaved: bool = False) -> dict:
    "Remove a sublevel from the open world. DEFERRED (opId, poll list_sublevels) for a stronger reason than add_sublevel: RemoveLevelsFromWorld RESETS the transaction buffer, then forces a GC, then runs a stale-reference sweep that is FATAL"
    return _post("remove_sublevel", path=path, discardUnsaved=discard_unsaved)


@mcp.tool()
def set_sublevel_visibility(path: str, visible: bool = None, should_be_loaded: bool = None,
                            should_be_visible: bool = None, lighting_scenario: bool = None) -> dict:
    "Flip a sublevel's flags: visible (EDITOR viewport visibility), should_be_loaded / should_be_visible (RUNTIME streaming intent), lighting_scenario."
    return _post("set_sublevel_visibility", path=path, visible=visible,
                 shouldBeLoaded=should_be_loaded, shouldBeVisible=should_be_visible,
                 lightingScenario=lighting_scenario)


@mcp.tool()
def set_current_sublevel(path: str) -> dict:
    "Set which level new actors are spawned into. path is a sublevel's package path, or the literal 'persistent'."
    return _post("set_current_sublevel", path=path)


@mcp.tool()
def set_sublevel_streaming(path: str, streaming_class: str) -> dict:
    "Change a sublevel's streaming class: alwaysloaded | dynamic. DEFERRED (opId, poll list_sublevels) because SetStreamingClassForLevel does not edit a property - it REMOVES the ULevelStreaming and re-adds the level, returning a NEW object,"
    return _post("set_sublevel_streaming", path=path, streamingClass=streaming_class)


@mcp.tool()
def pie_load_level_instance(path: str, location: dict = None, rotation: dict = None,
                            visible: bool = True, net_mode: str = "server",
                            name_override: str = "", temp_package: bool = False) -> dict:
    "Stream a level into the RUNNING PIE world as an instance - test setup without a Lua command, and the counterpart to spawn_actor_in_pie."
    return _post("pie_load_level_instance", path=path, location=location, rotation=rotation,
                 visible=visible, netMode=net_mode, nameOverride=name_override or None,
                 tempPackage=temp_package)


@mcp.tool()
def pie_unload_level_instance(instance_name: str = "", object_path: str = "", path: str = "",
                              net_mode: str = "server") -> dict:
    "Unload a level instance from the running PIE world. Identify it by instance_name (what pie_load_level_instance returned), object_path, or path naming the SOURCE map - one of the three is required."
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
    "Census of cooked Blueprint packages straight from the asset registry - the way to size and page the reconstructable corpus before touching it. path_contains is a SUBSTRING of the package name ('*' = every mounted root incl. DLC)."
    return _post("kr_list_cooked_blueprints", pathContains=path_contains,
                 cookedOnly=cooked_only, includeWidgets=include_widgets,
                 offset=offset, limit=limit)


@mcp.tool()
def kr_dump_blueprint(asset: str, function_filter: str = "", include_bytecode: bool = False,
                      max_statements_per_function: int = 500, include_histogram: bool = True,
                      include_properties: bool = True, include_events: bool = True,
                      offset: int = 0, limit: int = 100) -> dict:
    "Structure of a cooked BlueprintGeneratedClass as JSON: own functions (name, scriptBytes, numParams, flags), own properties, event thunks, the parent chain, and counts. asset = the objectPath, ideally the .<Name>_C class path."
    return _post("kr_dump_blueprint", asset=asset, functionFilter=function_filter or None,
                 includeBytecode=include_bytecode,
                 maxStatementsPerFunction=max_statements_per_function,
                 includeHistogram=include_histogram, includeProperties=include_properties,
                 includeEvents=include_events, offset=offset, limit=limit)


@mcp.tool()
def kr_disassemble_function(asset: str, function: str, statement_offset: int = 0,
                            statement_limit: int = 2000, include_raw: bool = True) -> dict:
    "THE tool for reading cooked Blueprint logic: one function's Kismet bytecode as a structured JSON statement stream."
    return _post("kr_disassemble_function", asset=asset, function=function,
                 statementOffset=statement_offset, statementLimit=statement_limit,
                 includeRaw=include_raw)


@mcp.tool()
def kr_list_events(asset: str, kind: str = "all", include_frame_param_map: bool = True) -> dict:
    "Event census of a cooked class: every event thunk with its kind, its RECOVERED ubergraph entry offset, param count, and the authoritative frame->param map (read out of the thunk's own bytecode - the generated frame property name must never"
    return _post("kr_list_events", asset=asset, kind=kind,
                 includeFrameParamMap=include_frame_param_map)


@mcp.tool()
def kr_analyze_ubergraph(asset: str, include_per_event: bool = True,
                         include_offsets: bool = False) -> dict:
    "Ubergraph slice analysis for ONE cooked Blueprint: prologue shape, per-event reachability, and the shared/unreached statement counts. Read-only - builds no graphs, mints nothing, compiles nothing."
    return _post("kr_analyze_ubergraph", asset=asset, includePerEvent=include_per_event,
                 includeOffsets=include_offsets)


@mcp.tool()
def kr_pin_type_from_property(class_path: str, property: str, self_scope: str = "") -> dict:
    "Turn any class property into the exact type string add_variable / add_pin / create_function / set_pin_type accept - instead of guessing category/subcategory spellings."
    # "class" is a Python keyword, so the bridge key cannot be a named parameter here.
    return _post("kr_pin_type_from_property",
                 **{"class": class_path, "property": property, "selfScope": self_scope or None})


@mcp.tool()
def kr_reconstruct_request(source_asset: str, mode: str = "copy", variant: str = "",
                           function: str = "", target_path: str = "") -> dict:
    "Start the single kr job: decompile a cooked Blueprint's bytecode into editable K2 graphs."
    return _post("kr_reconstruct_request", sourceAsset=source_asset, mode=mode,
                 variant=variant or None, function=function or None,
                 targetPath=target_path or None)


@mcp.tool()
def kr_reconstruct_status(job_id: str = "") -> dict:
    "Poll the single kr job slot - THE poll half for EVERY kr job kind (reconstruct, verify, classify, census, batch); there is no per-kind status endpoint. Omit job_id for the retained job."
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
    "Reconstruct a throwaway transient CHILD of a cooked Blueprint, compile it, and diff every reconstructed function's recompiled bytecode against the cooked original - the whole-Blueprint fidelity aggregate."
    return _post("kr_verify_fidelity", sourceAsset=source_asset,
                 classifyIntentional=classify_intentional, allowAnim=allow_anim)


@mcp.tool()
def kr_classify_drift(source_asset: str, function: str = "", classify_intentional: bool = True,
                      allow_anim: bool = False) -> dict:
    "kr_verify_fidelity decomposed PER FUNCTION: result.functions[{name, verdict, reasons[], detail}] with verdict in identical/equivalent/intentional/drift/missing/uncomparable, plus verdictCounts, reasonTally and consistent."
    return _post("kr_classify_drift", sourceAsset=source_asset, function=function or None,
                 classifyIntentional=classify_intentional, allowAnim=allow_anim)


@mcp.tool()
def kr_drift_census(path_filter: str = "/Game/", start_index: int = 0, max_count: int = 50,
                    classify_intentional: bool = True) -> dict:
    "Fidelity verify across a path-filtered SET of cooked Blueprints with the classifier's census instrument (mif.kr.DriftCensus) forced on for the job, producing running corpus totals over HTTP plus the on-disk CSV of every UNCLAIMED drift"
    return _post("kr_drift_census", pathFilter=path_filter, startIndex=start_index,
                 maxCount=max_count, classifyIntentional=classify_intentional)


@mcp.tool()
def kr_batch_reconstruct(path_filter: str = "/Game/", mode: str = "sibling", verify: bool = False,
                         start_index: int = 0, max_blueprints: int = 0,
                         classify_intentional: bool = True) -> dict:
    "The regression sweep: reconstruct every matching cooked Blueprint into a throwaway copy, compile it, tally PASS/FAIL/SKIP with the three-way skip taxonomy, and write the engine harness's exact CSV."
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
    "DISCOVERY for invoke_editor_command / send_editor_key. Three halves, each honest about what it can see."
    return _post("list_editor_commands", context=context or None, command=command or None,
                 filter=filter or None, includeUnbound=include_unbound,
                 includeCanExecute=include_can_execute or None,
                 includeConsole=include_console or None, consolePrefix=console_prefix or None,
                 menu=menu or None, section=section or None, limit=limit)


@mcp.tool()
def open_asset_editor(path: str) -> dict:
    "Open an asset's default editor (StaticMesh, SkeletalMesh, Material, Animation, ...) programmatically."
    return _post("open_asset_editor", path=path)


@mcp.tool()
def invoke_editor_command(context: str, command: str, menu: str = "", section: str = "",
                          entry: str = "", dry_run: bool = False, confirm: bool = False,
                          allow_known_modal: bool = False) -> dict:
    "Execute the FUIAction a menu entry or toolbar button is bound to - the same delegate a mouse click ends in, minus hit-testing, minus focus change, minus cursor."
    return _post("invoke_editor_command", context=context, command=command,
                 menu=menu or None, section=section or None, entry=entry or None,
                 dryRun=dry_run or None, confirm=confirm or None,
                 allowKnownModal=allow_known_modal or None)


@mcp.tool()
def invoke_editor_tab(tab_id: str = "", manager: str = "global", major_tab: str = "",
                      asset: str = "", probe: bool = False, probe_ids: list = None,
                      include_known_ids: bool = True, as_inactive: bool = False) -> dict:
    "Open an editor tab by id via FTabManager::TryInvokeTab - the route BlueprintAssist itself uses to open its own windows. 'Open a custom editor window' is one public call, no pixels."
    return _post("invoke_editor_tab", tabId=tab_id or None, manager=manager,
                 majorTab=major_tab or None, asset=asset or None, probe=probe or None,
                 probeIds=probe_ids or None, includeKnownIds=include_known_ids,
                 asInactive=as_inactive or None)


@mcp.tool()
def send_editor_key(key: str, confirm: bool = False, dry_run: bool = False,
                    modifiers: dict = None, user_index: int = 0, is_repeat: bool = False,
                    character_code: int = 0, key_code: int = 0, send_key_up: bool = True) -> dict:
    "Inject a key event through FSlateApplication::ProcessKeyDownEvent, which reaches registered IInputProcessors FIRST - the only route to commands a plugin dispatches from its own input processor rather than from a reachable FUICommandList."
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
    "Create or REFILL a Texture2D from image bytes. TWO ingest modes - supply exactly one: source_path (a file on disk) or base64_data (the raw image bytes inline; use this when you generated the image and it was never written to a file)."
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
    "Import a source media FILE (fbx, wav, psd, obj - anything a loaded editor factory accepts) into a /Game/ folder via UAssetImportTask. destination is a FOLDER, not an asset path; name defaults to the file stem."
    return _post("import_asset", file=file, destination=destination,
                 name=name or None, factory=factory or None,
                 replaceExisting=replace_existing,
                 replaceExistingSettings=replace_existing_settings, save=save)


@mcp.tool()
def export_asset(asset: str, file: str = "", export_format: str = "", overwrite: bool = None,
                 fbx_compatibility: str = "", ascii_fbx: bool = None, vertex_color: bool = None,
                 level_of_detail: bool = None, collision: bool = None,
                 export_source_mesh: bool = None, force_front_x_axis: bool = None) -> dict:
    "Write an asset OUT to a disk file - the read side of round-tripping, and until this existed a mesh could not leave the editor at all."
    return _post("export_asset", asset=asset, file=file or None, format=export_format or None,
                 overwrite=overwrite, fbxCompatibility=fbx_compatibility or None,
                 ascii=ascii_fbx, vertexColor=vertex_color, levelOfDetail=level_of_detail,
                 collision=collision, exportSourceMesh=export_source_mesh,
                 forceFrontXAxis=force_front_x_axis)


@mcp.tool()
def reimport_asset(path: str, source_file: str = "", source_file_index: int = None,
                   force_new_file: bool = None, save: bool = None) -> dict:
    "Re-pull an imported asset from its recorded source file(s). source_file supplies or overrides the path when the original is gone or you want different content."
    return _post("reimport_asset", path=path, sourceFile=source_file or None,
                 sourceFileIndex=source_file_index, forceNewFile=force_new_file, save=save)


@mcp.tool()
def set_texture_settings(path: str, compression_settings: str = "", srgb: bool = None,
                         lod_group: str = "", never_stream: bool = None,
                         mip_gen_settings: str = "", texture_filter: str = "",
                         save: bool = None) -> dict:
    "Set a Texture2D's CompressionSettings / SRGB / LODGroup / NeverStream / MipGenSettings / Filter."
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
    "Preflight for the thumbnail endpoints. With no argument: whether this editor can render at all (canRender, canEverRender, rhiInitialized, thumbnailManager) plus size limits."
    return _post("thumbnail_capabilities", asset=asset or None)


@mcp.tool()
def render_thumbnail(asset: str, width: int = 256, height: int = 0,
                     orbit_pitch: float = None, orbit_yaw: float = None, orbit_zoom: float = None,
                     flush_textures: bool = False, alpha: str = "opaque", name: str = "") -> dict:
    "Render an asset's ICON the way the Content Browser does and write it as a PNG under <ProjectSaved>/MifBridge/Thumbnails. Mutates NO asset."
    return _post("render_thumbnail", asset=asset, width=width, height=height or None,
                 orbitPitch=orbit_pitch, orbitYaw=orbit_yaw, orbitZoom=orbit_zoom,
                 flushTextures=flush_textures, alpha=alpha, name=name or None)


@mcp.tool()
def write_thumbnail_texture(asset: str, texture_path: str, width: int = 256, height: int = 0,
                            orbit_pitch: float = None, orbit_yaw: float = None, orbit_zoom: float = None,
                            flush_textures: bool = False, alpha: str = "opaque",
                            srgb: bool = None, compression: str = "", lod_group: str = "",
                            generate_mips: bool = None, overwrite: bool = False, save: bool = True) -> dict:
    "Render an asset's icon and WRITE IT AS A UTexture2D ASSET at texture_path - the endpoint that actually fills an empty icon stub, because a PNG cannot be referenced by a widget. Two modes."
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
    "Set an asset's OWN Content Browser icon - the programmatic form of right-click > Capture Thumbnail."
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


@mcp.tool()
def create_metahuman_character(path: str) -> dict:
    "Create a new UMetaHumanCharacter asset at `path` (/Game/... - must not already exist), with default/archetype identity."
    return _post("create_metahuman_character", path=path)


@mcp.tool()
def spawn_metahuman_actor(character_path: str) -> dict:
    "Spawn a live preview actor in the open level bound to the UMetaHumanCharacter asset at `character_path`."
    return _post("spawn_metahuman_actor", characterPath=character_path)


@mcp.tool()
def add_gameplay_effect_modifier(object_path: str, attribute_set_class: str, attribute_name: str,
                                 operation: str, magnitude: float = 0.0) -> dict:
    "Add a modifier (attribute + operation + flat magnitude) to a GameplayEffect Blueprint's Modifiers array."
    return _post("add_gameplay_effect_modifier", objectPath=object_path,
                 attributeSetClass=attribute_set_class, attributeName=attribute_name,
                 operation=operation, magnitude=magnitude)


@mcp.tool()
def list_live_widgets(net_mode: str = "server", top_level_only: bool = True, class_filter: str = "") -> dict:
    "List LIVE UUserWidget instances actually on screen right now - not a Widget Blueprint asset's design-time tree (use list_tree_widgets for that)."
    return _post("list_live_widgets", netMode=net_mode, topLevelOnly=top_level_only,
                 classFilter=class_filter or None)


@mcp.tool()
def describe_live_widget(path: str, max_depth: int = 12) -> dict:
    "Read the full LIVE geometry tree for one widget instance (path from list_live_widgets) - position, size, visibility and slot info for it and every descendant, recursing through UMG panel children AND through any nested UUserWidget's own"
    return _post("describe_live_widget", path=path, maxDepth=max_depth)


@mcp.tool()
def preview_widget(widget_class: str, width: int = 512, height: int = 512, dpi_scale: float = 1.0,
                   background: str = "transparent", name: str = "") -> dict:
    "Render ONE Widget Blueprint class to a PNG, isolated - no PIE, no game world, no parent composition. Good for checking one widget's own layout (brushes, fonts, colors, local hierarchy) fast, without packaging or opening the game."
    return _post("preview_widget", widgetClass=widget_class, width=width, height=height,
                 dpiScale=dpi_scale, background=background, name=name or None)


@mcp.tool()
def preview_composite_widget(root_class: str, children: list = None, width: int = 512,
                             height: int = 512, dpi_scale: float = 1.0,
                             background: str = "transparent", name: str = "") -> dict:
    "Assemble a root Widget Blueprint plus N children into named containers, transiently, and render the RESULT - reproducing a runtime-composed screen (a vanilla parent with a child injected into a named panel, e.g."
    return _post("preview_composite_widget", rootClass=root_class, children=children or [],
                 width=width, height=height, dpiScale=dpi_scale, background=background,
                 name=name or None)


@mcp.tool()
def ui_scenario_start(target_actor_path: str, player_location: dict, player_rotation: dict = None,
                      net_mode: str = "server", player_index: int = 0, confirm: bool = False) -> dict:
    "Start an interaction-faithful UI scenario: positions the LOCAL PLAYER PAWN at player_location/player_rotation (explicit - no automatic interaction-radius calculation, since that's game-specific logic this bridge can't know generically) in"
    return _post("ui_scenario_start", targetActorPath=target_actor_path, playerLocation=player_location,
                 playerRotation=player_rotation, netMode=net_mode, playerIndex=player_index, confirm=confirm)


@mcp.tool()
def ui_scenario_activate(activation_key: str = "F", expected_widget_classes: list = None,
                         timeout_seconds: float = 10.0, stable_frames: int = 3,
                         confirm: bool = False) -> dict:
    "THE hazardous step of the scenario runner: delivers activation_key through UGameViewportClient::InputKey - the actual entry point real input takes into the game's own PlayerController/input stack, not a generic focused-widget guess."
    return _post("ui_scenario_activate", activationKey=activation_key,
                 expectedWidgetClasses=expected_widget_classes or [], timeoutSeconds=timeout_seconds,
                 stableFrames=stable_frames, confirm=confirm)


@mcp.tool()
def ui_scenario_status() -> dict:
    "Poll the active UI scenario's state: IDLE (none active) | POSITIONED (after ui_scenario_start) | WAITING_FOR_STABLE_UI (after ui_scenario_activate, still ticking) | READY (capture-able) | TIMED_OUT | FAILED | STOPPED."
    return _post("ui_scenario_status")


@mcp.tool()
def ui_scenario_capture(name: str = "") -> dict:
    "Capture the scenario's result: the GAME viewport (not the editor's own active viewport - a different capture entirely from capture_viewport) as a PNG, plus every top-level live widget's path/class/geometry."
    return _post("ui_scenario_capture", name=name or None)


@mcp.tool()
def ui_scenario_stop() -> dict:
    "Stop the active UI scenario (if any) and return this bridge to idle - unregisters the internal ticker and clears state. Safe to call even if nothing is active (wasActive:false)."
    return _post("ui_scenario_stop")


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


def _bl_scale(obj_info: dict, what: str) -> list:
    """The object's own scale, required and fail-closed like everything else here.

    object_info's boundsLocal*BU/UU fields are deliberately LOCAL space -
    ops_common.local_bounds() reads raw vertex coordinates so a cached,
    stale bound_box can never mask a real edit. That is correct for its own
    purpose but means those fields do NOT fold in object scale, and
    import_mesh deliberately leaves the imported object at a uniform
    non-1 scale (MifBlender/ops_mesh.py: Blender's FBX importer represents the
    cm-file/BU unit conversion as an object-scale, not a mesh rescale -
    VERIFIED empirically 2026-08-27: a barrel exported from UE at
    boundsSizeUU (56.08, 55.72, 1.08) reads back from Blender with
    boundsLocalSizeBU == THE SAME NUMBERS and object scale [0.01, 0.01, 0.01]).
    Every comparison against Unreal's world-space bounds below must multiply
    by this scale first, or it is comparing local Blender numbers to world
    Unreal ones and is wrong by exactly the scale factor, always.
    """
    if not isinstance(obj_info, dict) or "scale" not in obj_info:
        raise _MifToolError(
            f"{what} has no .scale, so local-space bounds cannot be converted to world space "
            f"and nothing below can be trusted. Keys present: "
            f"{sorted(obj_info) if isinstance(obj_info, dict) else type(obj_info).__name__}")
    return _vec3(obj_info["scale"], f"{what}.scale")


def _bl_shape_ok(obj_info: dict, scale: list, what: str):
    """Is this object's transform safe to fold into a world-space comparison?

    NOT the same question as object_info's own isIdentityTransform, which
    additionally demands scale == 1 on every axis - a freshly imported mesh
    NEVER satisfies that (see _bl_scale above), so that field would fail every
    real round trip even after the size/pivot math is corrected for scale.
    What this pipeline actually needs is narrower: no offset, no rotation, and
    a scale that is the SAME on every axis and not mirrored. A uniform non-1
    scale is expected and is exactly what the multiplication by `scale`
    already corrects for; a skewed or negative scale is the real danger (it
    would distort geometry on export) and is what this still catches.

    Returns None on success, or a reason string identifying what is wrong.
    """
    loc = _vec3(obj_info.get("locationBU", [0.0, 0.0, 0.0]), f"{what}.locationBU")
    rot = _vec3(obj_info.get("rotationEulerRad", [0.0, 0.0, 0.0]), f"{what}.rotationEulerRad")
    if any(abs(v) > 1e-4 for v in loc):
        return f"non-zero location {loc} BU"
    if any(abs(v) > 1e-4 for v in rot):
        return f"non-zero rotation {rot} rad"
    if any(s <= 0 for s in scale):
        return f"non-positive scale {scale}"
    if max(scale) - min(scale) > 1e-4 * max(scale):
        return f"non-uniform scale {scale} (would skew geometry, not just resize it)"
    return None


def _bl_bounds_uu(obj_info: dict, what: str, scale: list) -> tuple:
    """(min, max, size) in UNREAL WORLD units out of an addon object_info block.

    object_info reports the LOCAL bbox in BLENDER units as boundsLocalMinBU /
    boundsLocalMaxBU (3-lists, ops_common.object_info) and only the SIZE in uu.
    The min and max are what a pivot comparison needs - the size alone cannot see
    a mesh that kept its dimensions and moved - so they are converted here
    against the same UU_PER_BU=100 the addon uses, AFTER folding in the
    object's own scale (see _bl_scale) to get world space, not local space.

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
    raw_lo = _vec3(obj_info["boundsLocalMinBU"], f"{what}.boundsLocalMinBU")
    raw_hi = _vec3(obj_info["boundsLocalMaxBU"], f"{what}.boundsLocalMaxBU")
    lo = [raw_lo[i] * scale[i] * 100.0 for i in range(3)]
    hi = [raw_hi[i] * scale[i] * 100.0 for i in range(3)]
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
    "Health probe for the Blender backend, and the FIRST thing to call before any bl_* work."
    return _blender("ping", _timeout=BLENDER_PROBE_TIMEOUT,
                    _lock_timeout=BLENDER_PROBE_TIMEOUT)


@mcp.tool()
def bl_scene_info(detail: bool = False) -> dict:
    "Summary of the current Blender scene: objectCount, objectsByType, objects[] (names and types; detail:true swaps in a full object_info each), activeObject, selectedObjects, collections, blendFile, and unitSettings. Read-only."
    return _blender("scene_info", detail=detail or None)


@mcp.tool()
def bl_list_objects(object_type: str = "") -> dict:
    "List objects in the Blender scene with their types. object_type filters to one Blender type ('MESH', 'EMPTY', 'ARMATURE', ...); omit it for everything. Read-only."
    return _blender("list_objects", type=object_type or None)


@mcp.tool()
def bl_object_info(object_name: str) -> dict:
    "Measurements for one Blender object, under an 'object' key: boundsLocalMinBU/MaxBU/SizeBU and boundsLocalSizeUU (the local bbox, already converted - use this one, do NOT multiply dimensionsBU yourself, that folds in object scale),"
    return _blender("object_info", object=object_name)


@mcp.tool()
def bl_list_bones(object_name: str, name_contains: str = "") -> dict:
    "The REST-POSE bone hierarchy of a Blender ARMATURE object - the same question UE's list_bones answers for a Skeleton's ReferenceSkeleton, asked on the authoring side instead."
    return _blender("list_bones", object=object_name, nameContains=name_contains or None)


@mcp.tool()
def bl_list_shape_keys(object_name: str) -> dict:
    "Shape keys on a Blender mesh object - Blender's name for what Unreal calls morph targets (compare against UE's list_morph_targets on the same character)."
    return _blender("list_shape_keys", object=object_name)


@mcp.tool()
def bl_list_vertex_groups(object_name: str) -> dict:
    "Vertex groups on a Blender mesh object - the bone-weight assignment groups a skinned mesh needs one per deforming bone, named to match the armature's bone names by Blender convention."
    return _blender("list_vertex_groups", object=object_name)


@mcp.tool()
def bl_list_modifiers(object_name: str) -> dict:
    "The modifier stack on a Blender mesh object, in EVALUATION ORDER (top to bottom in the Modifier Properties panel, which is also application order) - answers 'what will bl_export_mesh actually produce' before spending an export to find out."
    return _blender("list_modifiers", object=object_name)


@mcp.tool()
def bl_import_mesh(file: str, clear_scene: bool = True) -> dict:
    "Import an FBX file into Blender and report what arrived. FBX ONLY - the addon hard-refuses every other extension, OBJ included, because FBX is the only format whose axis and unit round trip with Unreal is verified (UE's OBJ exporter swaps"
    return _blender("import_mesh", file=file, clearScene=clear_scene)


@mcp.tool()
def bl_select_edges(object_name: str, selector: dict = None, max_reported: int = 512) -> dict:
    "Resolve an edge selector against a mesh and report what it matches, WITHOUT modifying anything."
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
def bl_uv_unwrap(object_name: str, method: str = "SMART", uv_layer: str = None,
                 angle_limit_deg: float = None, island_margin: float = 0.02,
                 replace: bool = False, dry_run: bool = False) -> dict:
    "Generate a UV layer on a Blender mesh. Closes a gap the addon could already SEE: object_info and gen_status both report uvLayers, and the quality check says outright 'no UVs - texturing and lightmaps will both fail until it is unwrapped',"
    return _blender("uv_unwrap", object=object_name, method=method, uvLayer=uv_layer,
                    angleLimitDeg=angle_limit_deg, islandMargin=island_margin,
                    replace=replace, dryRun=dry_run)


@mcp.tool()
def bl_decimate_mesh(object_name: str, ratio: float = None, target_tris: int = None,
                     mode: str = "COLLAPSE", angle_limit: float = None,
                     iterations: int = None, dry_run: bool = False) -> dict:
    "Reduce a mesh's triangle count in Blender - the LOD edit a game pipeline wants most, and where analyze_skeletal_split's triangle counts finally have somewhere to go."
    return _blender("decimate_mesh", object=object_name, ratio=ratio, targetTris=target_tris,
                    mode=mode, angleLimit=angle_limit, iterations=iterations, dryRun=dry_run)


@mcp.tool()
def bl_apply_transform(object_name: str, location: bool = True, rotation: bool = True,
                       scale: bool = True, fix_normals: bool = True) -> dict:
    "Bake an object's loc/rot/scale into its MESH DATA in Blender, restoring the identity transform."
    return _blender("apply_transform", object=object_name, location=location,
                    rotation=rotation, scale=scale, fixNormals=fix_normals)


@mcp.tool()
def bl_set_origin(object_name: str, mode: str = "geometry", location: list = None) -> dict:
    "Move a Blender object's ORIGIN without moving its geometry in the world. The origin is what Unreal rotates and places the mesh around, it is baked into the FBX, and it CANNOT be fixed on the Unreal side - so it has to be right before"
    return _blender("set_origin", object=object_name, mode=mode, location=location)


@mcp.tool()
def bl_clean_mesh(object_name: str, merge_distance: float = None, remove_loose: bool = False,
                  dissolve_degenerate: bool = False, triangulate: bool = False,
                  recalc_normals: bool = False, force: bool = False) -> dict:
    "The cleanup pass an imported or edited mesh needs before it goes back to Unreal. FIVE INDEPENDENT STEPS, run in the only correct order: merge first (so loose/degenerate detection sees merged topology), then remove loose, then dissolve"
    return _blender("clean_mesh", object=object_name, mergeDistance=merge_distance,
                    removeLoose=remove_loose, dissolveDegenerate=dissolve_degenerate,
                    triangulate=triangulate, recalcNormals=recalc_normals, force=force)


@mcp.tool()
def bl_normalize_weights(object_name: str, max_influences: int = None,
                         normalize: bool = True, groups: list = None) -> dict:
    "Make every vertex's bone weights sum to 1 in Blender, and cap how many bones influence one vertex."
    return _blender("normalize_weights", object=object_name, maxInfluences=max_influences,
                    normalize=normalize, groups=groups)


@mcp.tool()
def bl_transfer_weights(source: str, destination: str,
                        mapping: str = "POLYINTERP_NEAREST") -> dict:
    "Copy vertex weights from one Blender mesh onto another by proximity - the op a retopology or LOD pass needs."
    return _blender("transfer_weights", source=source, destination=destination, mapping=mapping)


@mcp.tool()
def bl_add_modifier(object_name: str, type: str, modifier: str = None,
                    settings: dict = None, index: int = None) -> dict:
    "Add a modifier to a Blender mesh object's stack - the write half of bl_list_modifiers."
    return _blender("add_modifier", object=object_name, type=type, modifier=modifier,
                    settings=settings, index=index)


@mcp.tool()
def bl_apply_modifier(object_name: str, modifier: str, dry_run: bool = False) -> dict:
    "Bake a modifier into a Blender mesh's data. Destructive, and it reports what that cost."
    return _blender("apply_modifier", object=object_name, modifier=modifier, dryRun=dry_run)


@mcp.tool()
def bl_remove_modifier(object_name: str, modifier: str) -> dict:
    "Remove a modifier from a Blender object's stack WITHOUT applying it - the mesh data is untouched, and meshUnchanged in the response is measured rather than asserted."
    return _blender("remove_modifier", object=object_name, modifier=modifier)


@mcp.tool()
def bl_uv_info(object_name: str, layer: str = None, max_reported_islands: int = 64) -> dict:
    "Read a Blender mesh's UVs per layer - the verification half of bl_uv_unwrap. bl_uv_unwrap can CREATE a channel and bl_object_info reports only that channels EXIST, so 'did the unwrap produce something Unreal can bake a lightmap into' had"
    return _blender("uv_info", object=object_name, layer=layer,
                    maxReportedIslands=max_reported_islands)


@mcp.tool()
def bl_bevel_edges(object_name: str, selector: dict = None, offset_uu: float = 15.0,
                   segments: int = 3, profile: float = 0.5, preserve_x: bool = True) -> dict:
    "Round or chamfer the selected edges with bmesh.ops.bevel (NOT bpy.ops.mesh.bevel, which needs an EDIT_MESH context and a real VIEW_3D area and therefore cannot run under blender -b)."
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
    "Extrude the selected boundary edge loops straight DOWN by depth_uu, forming a skirt - the fix for a flat-edged tile that hovers where the terrain falls away."
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
def bl_set_material_slots(object: str, slots: list, allow_resize: bool = False) -> dict:
    "Set a Blender object's material slot NAMES, in ORDER. Slot order is what decides which Unreal material lands on which face, so a reordered list renders the wrong material on an otherwise perfect mesh."
    return _blender("set_material_slots", object=object, slots=slots, allowResize=allow_resize)


@mcp.tool()
def bl_create_primitive(kind: str, name: str = "", size: float = None, radius: float = None,
                        location: list = None, rotation: list = None, segments: int = None,
                        ring_count: int = None, subdivisions: int = None, vertices: int = None,
                        depth: float = None, x_subdivisions: int = None,
                        y_subdivisions: int = None) -> dict:
    "Create a primitive mesh object in Blender: cube, sphere (alias uvsphere), icosphere, cylinder, cone, torus, plane, grid, circle or monkey."
    return _blender("create_primitive", kind=kind, name=name or None, size=size, radius=radius,
                    location=location, rotation=rotation, segments=segments,
                    ringCount=ring_count, subdivisions=subdivisions, vertices=vertices,
                    depth=depth, xSubdivisions=x_subdivisions, ySubdivisions=y_subdivisions)


@mcp.tool()
def bl_transform_object(object: str, location: list = None, rotation: list = None,
                        scale: list = None, relative: bool = False) -> dict:
    "Move, rotate or scale a Blender object WITHOUT baking the transform into its mesh data."
    return _blender("transform_object", object=object, location=location, rotation=rotation,
                    scale=scale, relative=relative)


@mcp.tool()
def bl_join_objects(target: str, objects: list) -> dict:
    "Join Blender mesh objects into one. DESTRUCTIVE AND ASYMMETRIC: the sources are DELETED and everything lands in target."
    return _blender("join_objects", target=target, objects=objects)


@mcp.tool()
def bl_separate_mesh(object: str, mode: str = "loose") -> dict:
    "Split a Blender mesh into separate objects - mode 'loose' (each disconnected island becomes its own object) or 'material' (one object per material slot in use). The counterpart to bl_join_objects."
    return _blender("separate_mesh", object=object, mode=mode)


@mcp.tool()
def bl_create_material(name: str, reuse: bool = False, base_color: list = None,
                       metallic: float = None, roughness: float = None) -> dict:
    "Create a Blender material with a Principled BSDF. Before this the addon could assign material slot NAMES and could not create a material or set a single shading value."
    return _blender("create_material", name=name, reuse=reuse, baseColor=base_color,
                    metallic=metallic, roughness=roughness)


@mcp.tool()
def bl_set_material_properties(material: str, base_color: list = None, metallic: float = None,
                               roughness: float = None, specular: float = None,
                               ior: float = None, alpha: float = None, emissive: list = None,
                               emissive_strength: float = None, transmission: float = None,
                               sheen: float = None, clearcoat: float = None,
                               anisotropic: float = None) -> dict:
    "Write Principled BSDF values on a Blender material. THE VERSION SPREAD IS THE WHOLE DIFFICULTY: Blender RENAMED these inputs between 3.6 and 4.0 - 'Specular' became 'Specular IOR Level', 'Emission' became 'Emission Color', 'Transmission'"
    return _blender("set_material_properties", material=material, baseColor=base_color,
                    metallic=metallic, roughness=roughness, specular=specular, ior=ior,
                    alpha=alpha, emissive=emissive, emissiveStrength=emissive_strength,
                    transmission=transmission, sheen=sheen, clearcoat=clearcoat,
                    anisotropic=anisotropic)


@mcp.tool()
def bl_list_materials(name_contains: str = "", used_only: bool = False) -> dict:
    "List every material in the Blender file with its user count. Reports `unused` - materials with zero users - because a material with no users is NOT written to an FBX at all, so one created and never assigned silently does not arrive in"
    return _blender("list_materials", nameContains=name_contains or None, usedOnly=used_only)


@mcp.tool()
def bl_describe_material(material: str, links: bool = False) -> dict:
    "Read one Blender material in full: its Principled BSDF values, the node tree shape, and every image texture with its FILE PATH."
    return _blender("describe_material", material=material, links=links)


@mcp.tool()
def bl_assign_material_to_faces(object: str, slot: int, faces: list = None) -> dict:
    "Point a range of polygons at one of a Blender object's material SLOTS. bl_set_material_slots decides which materials a mesh has and in what order; this decides which faces use which, and only the first existed."
    return _blender("assign_material_to_faces", object=object, slot=slot, faces=faces)


@mcp.tool()
def bl_export_mesh(object_name: str, file: str, object_types: list = None,
                   add_leaf_bones: bool = None, armature_deform_only: bool = None,
                   primary_bone_axis: str = None, secondary_bone_axis: str = None,
                   bake_anim: bool = None) -> dict:
    "Export a Blender object to FBX for reimport into Unreal. The two axis arguments are the whole ballgame and are set for you: axis_up='Z', axis_forward='Y', which are NOT the operator defaults ('Y' / '-Z', the Maya convention) - the defaults"
    return _blender("export_mesh", object=object_name, file=file, objectTypes=object_types,
                    addLeafBones=add_leaf_bones, armatureDeformOnly=armature_deform_only,
                    primaryBoneAxis=primary_bone_axis, secondaryBoneAxis=secondary_bone_axis,
                    bakeAnim=bake_anim)


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
    "Execute Python inside Blender, on the main thread, so bpy is safe to touch. The escape hatch for everything MifBlender has no first-class op for - prefer a first-class op whenever one exists, because those have checked parameters and this"
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
    "Is the local generator usable, and what is installed. FIRST call before any bl_gen_* work: it reports whether ComfyUI is reachable (default 127.0.0.1:8188, override with host) and which checkpoints and custom nodes are present."
    return _blender("gen_status", host=host or None)


@mcp.tool()
def bl_gen_image(prompt: str, seed: int = 0, variant: str = "schnell", width: int = 1024,
                 height: int = 1024, steps: int = None, host: str = "",
                 timeout: int = 600) -> dict:
    "Prompt -> reference image via Flux.1, left in ComfyUI's output folder. Stage ONE of the chain; the returned image name is what bl_gen_mesh consumes."
    return _blender("gen_image", prompt=prompt, seed=seed or None, variant=variant or None,
                    width=width, height=height, steps=steps, host=host or None,
                    timeout=timeout, _timeout=float(timeout) + 60.0)


@mcp.tool()
def bl_gen_mesh(image: str = "", image_path: str = "", prefix: str = "MifGen/mesh",
                name: str = "", seed: int = 0, steps: int = 30, octree: int = 512,
                guidance: float = 5.0, import_result: bool = True, host: str = "",
                timeout: int = 1800) -> dict:
    "Reference image -> untextured mesh via the Hunyuan3D-2 shape DiT. Stage TWO. Accepts EITHER image (a ComfyUI image ref, normally the name bl_gen_image returned) OR image_path (a local file)."
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
    "Existing mesh + reference image -> PBR textures baked on, via the Hunyuan3D-2 paint path (delight -> uv wrap -> multiview render -> sample -> bake). Stage THREE, and the stage that turns a generation into something droppable into a level."
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
    "Prompt -> reference image -> mesh -> PBR texture -> imported into the scene. THE ONE CALL that produces something usable: it sequences bl_gen_image, bl_gen_mesh and bl_gen_texture and hands back the finished object."
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
    "Unreal -> Blender -> Unreal in one call: export a mesh, edit it, reimport it as a NEW asset, and optionally repoint the properties that referenced the original."

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
        before_scale = _bl_scale(before_obj, "object_info object")
        local_uu = _vec3(before_obj["boundsLocalSizeUU"], "object_info object.boundsLocalSizeUU")
        got_uu = [local_uu[i] * abs(before_scale[i]) for i in range(3)]
        got_min, got_max, _ = _bl_bounds_uu(before_obj, "object_info object", before_scale)
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

    # The object TRANSFORM must be safe to fold into a world-space comparison
    # too: a bbox is local-space, so a rotated or offset object has a perfect
    # local box and a wrong world pivot. NOT the same test as
    # isIdentityTransform (scale == 1 on every axis) - import_mesh always
    # leaves a uniform non-1 scale, by design, and that is already corrected
    # for above via before_scale. What must actually be identity is location
    # and rotation; what must actually be UNIFORM (not 1) is scale.
    bad_shape = _bl_shape_ok(before_obj, before_scale, "object_info object")
    if bad_shape:
        return _abort("fidelity_gate",
                      f"Blender object '{obj_name}' has a transform that cannot be trusted: "
                      f"{bad_shape}. The local bounding box is measured in the object's own "
                      "space, so it can look correct while the world pivot is not - and the "
                      "export writes the object transform into the FBX. Do NOT fix this with "
                      "transform_apply: that bakes the round trip into the mesh and shears every "
                      "spline instance. Fix the import.", objectInfo=before_obj)

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
            after_scale = _bl_scale(after_obj, "object_info object (after the edit)")
            post_local_uu = _vec3(after_obj["boundsLocalSizeUU"], "object_info object.boundsLocalSizeUU")
            post_uu = [post_local_uu[i] * abs(after_scale[i]) for i in range(3)]
            post_min, post_max, _ = _bl_bounds_uu(after_obj, "object_info object (after the edit)",
                                                  after_scale)
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
        bad_shape = _bl_shape_ok(after_obj, after_scale, "object_info object (after the edit)")
        if bad_shape:
            return _abort("bounds_assert",
                          f"the edit left Blender object '{obj_name}' with a transform that cannot "
                          f"be trusted: {bad_shape}. The local bounding box above is measured in "
                          "the object's own space, so it can pass while the pivot did not. The mesh "
                          "was NOT imported. No edit op in this addon touches location or rotation, "
                          "and scale should only ever be the uniform value import_mesh set - if it "
                          "moved or skewed, something else in the scene did.", objectInfo=after_obj)

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


@mcp.tool()
def mif_help(tool: str = "") -> dict:
    "Get the FULL documentation for a MifBridge tool - the traps, engine citations and failure modes that were moved out of the tool descriptions to keep them out of every turn's context. Call this BEFORE using a tool you have not used before: several endpoints guard engine asserts that would terminate the editor, and the reason lives here rather than in the one-line summary. Pass no argument to list every tool that has extended help. For an endpoint's real accepted parameters as the LIVE editor sees them, use describe_endpoint instead - that reads the running plugin and is the authority when this server and the plugin disagree."
    store = _tool_help()
    if store.get("__error__"):
        return {"error": "tool help unavailable: %s" % store["__error__"],
                "path": _TOOL_HELP_PATH}
    if not tool:
        return {"tools": sorted(k for k in store if not k.startswith("__")),
                "count": len([k for k in store if not k.startswith("__")]),
                "note": "pass one of these as `tool` for its full documentation."}
    name = tool.strip()
    if name in store:
        return {"tool": name, "help": store[name]}
    near = sorted(k for k in store if name.lower() in k.lower())[:12]
    return {"error": "no extended help for %r. Either the tool name is wrong, or its description "
                     "was already short enough to keep inline - in which case what you see in the "
                     "tool list IS the whole of it." % name,
            "didYouMean": near}
