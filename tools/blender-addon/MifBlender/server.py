"""MifBlender socket server -- the Blender backend of MifBridge.

THREADING CONTRACT (read this before changing anything in this file)
====================================================================
bpy.data / the DNA-RNA layer is NOT thread-safe, and bpy.ops additionally reads
the global context, pushes undo steps and tags the depsgraph. A write from a
non-main thread while the main thread is mid depsgraph-eval or mid-draw is a
data race on raw C pointers: the failure is a hard process crash or silent heap
corruption with NO Python traceback. You cannot try/except your way out of it.

So the split is absolute:

  socket thread   ->  accept(), recv(), json.loads(), enqueue, sendall()
                      ...and NOTHING else. It must never touch bpy.
  main thread     ->  every single bpy call, run out of a queue by ONE timer.

The marshalling primitive is bpy.app.timers.register, as required. Note WHERE
it is called from: the drain timer is registered ONCE, from the addon's
register() on the main thread (see __init__.py). The socket thread only ever
does _JOBS.put(job).

That differs from the usual pattern (which calls bpy.app.timers.register from
the socket thread, once per command). I could NOT verify from Blender source or
docs that bpy.app.timers.register is safe to call off the main thread, and this
whole design would rest on it. A queue plus one main-thread-registered timer
needs no such assumption. Do not "simplify" it back.

BACKGROUND MODE
===============
Under `blender -b` there is no event loop, so timers NEVER fire. Three places
branch on bpy.app.background, and all three are required or a headless run
hangs forever with no output:
  1. start()          - run the accept loop on the main thread, no server thread
  2. _server_loop()   - handle each client serially, no client thread
  3. _handle_client() - execute the job inline instead of queueing it

Consequently the addon does NOT auto-start in background mode: register() would
never return. Headless callers use MifBlender.serve_forever() explicitly.

Adapted from blender-mcp (github.com/MCPBlender/blender-mcp),
(c) 2025 Siddharth Ahuja, MIT licence.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import socket
import sys
import threading
import time
import traceback

import bpy

from . import framing
from .ops_common import MifOpError, jsonable

# Loopback ONLY. There is deliberately no bind-address preference: the socket
# accepts commands that move files and (optionally) run arbitrary Python, and a
# 0.0.0.0 checkbox is a foot-gun no matter how it is labelled. If you genuinely
# need remote access, tunnel it.
HOST = "127.0.0.1"
DEFAULT_PORT = 8792  # UE plugin is 8791; third-party blender-mcp is 9876. No clash.

PROTOCOL_VERSION = 1

# The MCP server abandons a work request after MIF_BLENDER_TIMEOUT, default 180s
# (tools/mcp-server/server.py). This end must give up FIRST or the ladder is
# inverted: the MCP times out, calls _bl_close(), and Blender carries on mutating
# the scene for another several minutes on behalf of a caller that has already
# been told the op failed. 150s leaves 30s of headroom for the socket round trip.
# If you raise MIF_BLENDER_TIMEOUT, raise this too, and keep it below.
DEFAULT_JOB_TIMEOUT = 150.0

_JOBS: "queue.Queue[_Job]" = queue.Queue()
_TIMER_REGISTERED = False


# ---------------------------------------------------------------------------
# Job: one request, executed on the main thread, awaited by the socket thread
# ---------------------------------------------------------------------------

class _Job:
    __slots__ = ("request", "response", "done", "cancelled")

    def __init__(self, request):
        self.request = request
        self.response = None
        self.done = threading.Event()
        self.cancelled = False


def _drain_timer():
    """Registered once, on the main thread, by the addon's register().

    MUST NOT RAISE. A timer callback that raises is unregistered by Blender, and
    the server would then accept requests forever and answer none of them.
    """
    try:
        try:
            job = _JOBS.get_nowait()
        except queue.Empty:
            return 0.05  # idle poll -- 20 Hz is imperceptible and costs nothing

        if not job.cancelled:
            try:
                job.response = _execute(job.request)
            except BaseException as exc:  # noqa: BLE001 - a timer may not raise
                traceback.print_exc()
                job.response = {"ok": False, "error": "internal: %s" % exc}
        job.done.set()

        # Come straight back if more work is waiting.
        return 0.0 if not _JOBS.empty() else 0.05
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        return 0.25


def ensure_timer():
    """Idempotent. Call from register() on the main thread."""
    global _TIMER_REGISTERED
    if _TIMER_REGISTERED and bpy.app.timers.is_registered(_drain_timer):
        return
    if not bpy.app.timers.is_registered(_drain_timer):
        bpy.app.timers.register(_drain_timer, first_interval=0.0, persistent=True)
    _TIMER_REGISTERED = True


def remove_timer():
    global _TIMER_REGISTERED
    if bpy.app.timers.is_registered(_drain_timer):
        bpy.app.timers.unregister(_drain_timer)
    _TIMER_REGISTERED = False


# ---------------------------------------------------------------------------
# Dispatch  (runs on the MAIN thread only, from _drain_timer or inline in -b)
# ---------------------------------------------------------------------------

def _op_table():
    # Imported here rather than at module scope so a syntax error in an ops
    # module surfaces as a per-request error instead of a dead addon.
    from . import (ops_scene, ops_mesh, ops_gen, ops_rig, ops_create, ops_material,
                   ops_lightcam, ops_anim, ops_render, ops_world,
                   ops_physics, ops_particles, ops_nodes, ops_viewport, ops_file, ops_constraint,
                   ops_collection, ops_viewlayer, ops_io, ops_query)

    table = {}
    table.update(ops_scene.OPS)
    table.update(ops_mesh.OPS)
    table.update(ops_gen.OPS)
    table.update(ops_rig.OPS)
    table.update(ops_create.OPS)
    table.update(ops_material.OPS)
    table.update(ops_lightcam.OPS)
    table.update(ops_anim.OPS)
    table.update(ops_render.OPS)
    table.update(ops_world.OPS)
    table.update(ops_physics.OPS)
    table.update(ops_particles.OPS)
    table.update(ops_nodes.OPS)
    table.update(ops_viewport.OPS)
    table.update(ops_file.OPS)
    table.update(ops_constraint.OPS)
    table.update(ops_collection.OPS)
    table.update(ops_viewlayer.OPS)
    table.update(ops_io.OPS)
    table.update(ops_query.OPS)
    return table


def op_names():
    try:
        return sorted(_op_table().keys())
    except Exception as exc:  # noqa: BLE001
        return ["<op table failed to load: %s>" % exc]


def _execute(request):
    started = time.perf_counter()

    endpoint = request.get("endpoint") or request.get("op") or request.get("type")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return {"ok": False, "error": "params must be a JSON object, got %s"
                                      % type(params).__name__}
    if not endpoint:
        return {"ok": False, "error": "request has no 'endpoint' (aliases: op, type). "
                                      "Call 'ping' for the op list."}

    table = _op_table()
    fn = table.get(endpoint)
    if fn is None:
        return {"ok": False,
                "endpoint": endpoint,
                "error": "unknown endpoint '%s'. Known: %s"
                         % (endpoint, ", ".join(sorted(table)))}

    try:
        result = fn(params)
    except MifOpError as exc:
        # A deliberate, actionable refusal from an op. Not a bug -- do not
        # print a traceback for these, the message IS the diagnosis.
        return {"ok": False, "endpoint": endpoint, "error": str(exc),
                "elapsedMs": round((time.perf_counter() - started) * 1000.0, 2)}
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "endpoint": endpoint,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "traceback": traceback.format_exc(),
                "elapsedMs": round((time.perf_counter() - started) * 1000.0, 2)}

    out = {"ok": True, "endpoint": endpoint}
    if isinstance(result, dict):
        out.update(result)
    elif result is not None:
        out["result"] = result
    out["elapsedMs"] = round((time.perf_counter() - started) * 1000.0, 2)
    # COERCED AT THE DOOR, not on one branch of it. jsonable's own docstring says a response that
    # cannot be serialised is "a silent hang from the caller's point of view" - and it was applied
    # only to the `result` key, which is the RARE path. An op returning a dict, which is nearly all
    # of them, went to json.dumps untouched.
    #
    # What that cost: a NaN anywhere in a response reached the wire as bare `NaN`, which is not
    # valid JSON. Python's json.loads accepts it, so the Python client never noticed; a strict
    # parser rejects the whole frame. jsonable already turns a non-finite float into a string and
    # was simply never asked to.
    return jsonable(out)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class MifBlenderServer:
    def __init__(self, port=DEFAULT_PORT, token="dev", job_timeout=DEFAULT_JOB_TIMEOUT,
                 verbose=True):
        self.host = HOST
        self.port = int(port)
        self.token = token or ""
        self.job_timeout = float(job_timeout)
        self.verbose = bool(verbose)
        self.running = False
        self.socket = None
        self.server_thread = None
        self._clients = 0

    # -- logging -----------------------------------------------------------
    def _log(self, *parts):
        if self.verbose:
            print("[MifBlender]", *parts)

    # -- lifecycle ---------------------------------------------------------
    @staticmethod
    def _set_exclusive(sock):
        """Make a second bind of the same address FAIL, on either platform.

        SO_REUSEADDR does not mean the same thing on Windows as it does on
        POSIX. On Windows it lets a SECOND socket bind an address another socket
        is already listening on -- VERIFIED on this box, Python 3.11/win32: two
        sockets both bound 127.0.0.1 and both listen()ed, no error either time.
        Two Blender windows with the addon auto-starting would therefore BOTH log
        "listening", both show green in the N-panel, and the MCP would reach a
        nondeterministic one. "My edits went to the other Blender" is a whole day.

        SO_EXCLUSIVEADDRUSE is the Windows-only opposite: the second bind fails
        with WSAEADDRINUSE (10048), which the OSError arm in start() already
        handles -- it just never used to fire.

          MEASURED on this box, same session: with SO_EXCLUSIVEADDRUSE the second
          bind raises OSError errno 10048; a plain no-option socket is refused
          too. And the TIME_WAIT worry does NOT bite: closing an accepted
          connection server-side and immediately rebinding the listening port
          with SO_EXCLUSIVEADDRUSE succeeded, so stop-then-start still works.

        On POSIX, SO_REUSEADDR already refuses a second bind of a LISTENING
        address (it only relaxes TIME_WAIT), so the existing behaviour is kept.
        """
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def start(self):
        if self.running:
            self._log("already running on %s:%d" % (self.host, self.port))
            return True

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._set_exclusive(sock)
            sock.bind((self.host, self.port))
            sock.listen(4)
        except OSError as exc:
            self._log("FAILED to bind %s:%d -- %s" % (self.host, self.port, exc))
            self._log("       another MifBlender (or something else) already owns that "
                      "port -- most likely a SECOND Blender window with this addon "
                      "enabled. Close it, or change the port in Preferences > Add-ons > "
                      "MifBlender. The N-panel of whichever Blender DID get the port "
                      "shows its pid; compare it against bl_status's `pid`.")
            try:
                sock.close()
            except Exception:
                pass
            self.socket = None
            self.running = False
            return False

        self.socket = sock
        self.running = True
        if not self.token:
            self._log("WARNING: token is EMPTY -- any loopback process may drive "
                      "this Blender. Set one in the addon preferences.")
        self._log("listening on %s:%d (protocol v%d, pid %d)"
                  % (self.host, self.port, PROTOCOL_VERSION, os.getpid()))

        if bpy.app.background:
            # No event loop in -b, so the drain timer will never fire. Run
            # everything on this (the main) thread. THIS BLOCKS.
            self._log("background mode: blocking accept loop on the main thread")
            self._server_loop()
        else:
            self.server_thread = threading.Thread(
                target=self._server_loop, name="MifBlenderAccept", daemon=True)
            self.server_thread.start()
        return True

    def stop(self):
        self.running = False
        if self.socket is not None:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        if self.server_thread is not None:
            try:
                if self.server_thread.is_alive():
                    # The listening socket has a 1 s timeout, so the loop
                    # notices self.running went False within ~1 s.
                    self.server_thread.join(timeout=2.0)
            except Exception:
                pass
            self.server_thread = None
        self._log("stopped")

    # -- accept loop -------------------------------------------------------
    def _server_loop(self):
        # 1 s timeout on the LISTENING socket is what makes stop() work: without
        # it accept() blocks forever and the thread can never notice `running`.
        try:
            self.socket.settimeout(1.0)
        except Exception:
            return

        while self.running:
            try:
                try:
                    client, addr = self.socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    # socket closed by stop()
                    break

                if addr[0] != "127.0.0.1":
                    # Cannot normally happen (we bind loopback) -- belt only.
                    try:
                        framing.send_json_message(
                            client, {"ok": False, "error": "non-loopback caller rejected"})
                        client.close()
                    except Exception:
                        pass
                    continue

                self._clients += 1
                self._log("client connected from %s:%d" % addr)
                if bpy.app.background:
                    self._handle_client(client)
                else:
                    threading.Thread(target=self._handle_client, args=(client,),
                                     name="MifBlenderClient", daemon=True).start()
            except Exception as exc:  # noqa: BLE001
                if not self.running:
                    break
                self._log("accept loop error: %s" % exc)
                time.sleep(0.5)
        self._log("accept loop exited")

    # -- per-client loop ---------------------------------------------------
    def _handle_client(self, client):
        """SOCKET THREAD. Zero bpy calls below this line -- see the module docstring.

        (bpy.app.background is read, but that is an immutable process-level flag,
        not scene data.)
        """
        background = bpy.app.background
        try:
            client.settimeout(None)  # block; the accept socket owns the stoppability
            while self.running:
                try:
                    request = framing.receive_framed_json(client)
                except json.JSONDecodeError as exc:
                    # Must precede the ValueError arm below -- JSONDecodeError IS a
                    # ValueError, and a mis-encoded body deserves a real answer
                    # rather than a silent hang-up.
                    self._reply(client, {"ok": False, "error": "malformed JSON body: %s" % exc})
                    break
                except (ConnectionError, OSError, ValueError) as exc:
                    self._log("client gone / bad frame: %s" % exc)
                    break

                if not isinstance(request, dict):
                    self._reply(client, {"ok": False,
                                         "error": "frame must be a JSON object"})
                    continue

                if not self._auth_ok(request):
                    self._reply(client, {"ok": False,
                                         "error": "bad or missing token (send it as the "
                                                  "'token' field; it is set in the addon "
                                                  "preferences)"})
                    break

                if background:
                    # Main thread already -- run it here, timers are dead in -b.
                    response = _execute(request)
                else:
                    response = self._run_on_main_thread(request)

                if not self._reply(client, response):
                    break
        except Exception as exc:  # noqa: BLE001
            self._log("client handler error: %s" % exc)
            traceback.print_exc()
        finally:
            try:
                client.close()
            except Exception:
                pass
            self._clients = max(0, self._clients - 1)

    def _run_on_main_thread(self, request):
        """Hand the request to the main thread and WAIT for it.

        Waiting (rather than firing and forgetting) is deliberate: the reply must
        not be sent until the bpy work has actually completed. A fire-and-forget
        'DONE' would tell the caller a file is on disk before Blender has written
        it -- which for the mesh round-trip means Unreal reimports a stale file.
        """
        job = _Job(request)
        _JOBS.put(job)
        if job.done.wait(timeout=self.job_timeout):
            return job.response
        job.cancelled = True  # skipped if it has not started yet
        return {"ok": False,
                "error": "timed out after %.0fs waiting for Blender's main thread. "
                         "Blender is busy (modal operator, long bake, or a dialog is "
                         "open). The job was cancelled if it had not started; if it "
                         "had, it may still complete -- check the scene before "
                         "retrying." % self.job_timeout}

    def _auth_ok(self, request):
        if not self.token:
            return True  # explicitly unauthenticated; warned about at start()
        supplied = request.get("token")
        if not isinstance(supplied, str):
            return False
        return hmac.compare_digest(supplied, self.token)

    def _reply(self, client, payload):
        try:
            framing.send_json_message(client, payload)
            return True
        except (TypeError, ValueError) as exc:
            # Payload could not be framed. Never let that look like success - and say WHICH of the
            # two reasons it was, because they point at completely different fixes.
            #
            # TOO LARGE IS NOT A BUG IN THE OP. The old message said "op produced a
            # non-serialisable response ... the op must return JSON-safe values" for both cases, so
            # a response that serialised perfectly and was merely bigger than the frame sent the
            # caller looking for a serialisation defect in an op that has none. Measured on 5.0.1:
            # list_objects costs about 169 bytes per object, so the 64 MiB cap arrives near 400,000
            # objects - far off, reachable, and worth naming correctly when it happens.
            too_big = "frame too large" in str(exc)
            if too_big:
                message = ("the response is larger than the %d-byte frame limit (%s). The op and "
                           "its values are fine - there is simply too much of it. Narrow the "
                           "request: ask for one object, one collection or one frame range rather "
                           "than the whole scene."
                           % (framing.MAX_MESSAGE_BYTES, exc))
            else:
                message = ("op produced a non-serialisable response (%s). This is a MifBlender bug "
                           "-- the op must return JSON-safe values." % exc)
            try:
                framing.send_json_message(
                    client,
                    {"ok": False,
                     "endpoint": payload.get("endpoint") if isinstance(payload, dict) else None,
                     "responseTooLarge": too_big,
                     "error": message})
                return True
            except Exception:
                return False
        except Exception as exc:  # noqa: BLE001
            self._log("failed to send reply: %s" % exc)
            return False

    # -- introspection -----------------------------------------------------
    def status(self):
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            # The pid is here so a duplicate Blender is VISIBLE. The N-panel
            # prints it and op_ping returns it, so "which Blender did that edit
            # land in" is one comparison instead of a guess.
            "pid": os.getpid(),
            "clients": self._clients,
            "jobTimeout": self.job_timeout,
            "background": bpy.app.background,
            "authRequired": bool(self.token),
            "protocolVersion": PROTOCOL_VERSION,
        }
