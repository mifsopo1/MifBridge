"""Shared low-level plumbing for the audit_blender_*.py scripts.

One socket helper, kept in one place, because tools/mcp-server/server.py cannot be imported standalone
without pulling in the mcp package (see tools/mcp-server/requirements.txt) and these audits need to run
without that dependency. This is the same wire protocol test_blender_mesh.py's call() implements -
{"endpoint", "token", "params"} framed with a 4-byte big-endian length prefix - kept here rather than a
third copy once a second audit script needed it too.
"""
import json
import os
import socket
import struct

HOST = os.environ.get("MIF_BLENDER_HOST", "127.0.0.1")
try:
    PORT = int(os.environ.get("MIF_BLENDER_PORT", "8792"))
except ValueError:
    PORT = 8792
TOKEN = os.environ.get("MIF_BLENDER_TOKEN", os.environ.get("MIF_BRIDGE_TOKEN", "dev"))


def reachable(timeout=1.5):
    """True if the addon ANSWERS on the port - not merely that something accepted a connection.

    Every Blender suite needs this before its first call, because call() raises rather than
    returning an error dict when nothing is there - so `if not call("ping").get("ok")` never runs
    and the suite dies with a traceback and exit 1 instead of skipping with exit 2. It lived as a
    private copy inside test_blender_ops.py, which is precisely why the two suites written later
    did not have it. Shared here so the next one cannot miss it.

    A REAL PING, NOT A CONNECT, and that distinction cost a false failure on 2026-08-31. This used
    to be a bare socket.connect(), which succeeded against a socket whose owner was already going
    away; the first real call then died with "connection closed reading length header" and
    test_blender_rename_bones reported a FAILURE on a machine that simply had no Blender running.

    run_blender_suites.py had already learned exactly this and says so at the top of itself -
    "READINESS IS A PING, NEVER A CONNECT ... A connection existing is not a server answering" - but
    the lesson reached the runner and never reached here, so every Blender suite stayed one dying
    socket away from the same false failure.

    AND SHARING IT WAS NOT THE END OF IT, 2026-08-31. Three suites - test_blender_ops (the file the
    private copy came FROM), test_blender_rig and test_blender_gen - still carried their own
    bare-connect version months later. Creating the shared helper did not remove them, so the fix
    reached this module and not its callers.

    What that cost, observed rather than argued: test_blender_rig reported PASS 12 FAIL 4 with no
    Blender running at all. A UE editor holds MifBlender's port 8792 on this machine (docs/06 issue
    15), the bare connect succeeded against it, and the suite ran its whole body against a server
    speaking a different protocol. Four FALSE FAILURES where the honest answer was SKIPPED - and a
    false failure is worse than a false pass, because it teaches the reader to ignore the suite.

    The lesson generalises past sockets: EXTRACTING a shared helper only helps once the copies are
    deleted. Until then there are N+1 implementations and the new one is the least used.
    """
    body = json.dumps({"endpoint": "ping", "token": TOKEN, "params": {}}).encode("utf-8")
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        s.sendall(struct.pack(">I", len(body)) + body)
        head = b""
        while len(head) < 4:
            chunk = s.recv(4 - len(head))
            if not chunk:
                return False
            head += chunk
        want = struct.unpack(">I", head)[0]
        buf = b""
        while len(buf) < want:
            chunk = s.recv(min(65536, want - len(buf)))
            if not chunk:
                return False
            buf += chunk
        return json.loads(buf.decode("utf-8")).get("ok") is not False
    except Exception:
        return False
    finally:
        s.close()


def port_is_occupied(timeout=1.0):
    """True if SOMETHING accepts a connection on the port without being a MifBlender.

    Only meaningful once reachable() has said no. The two failures need different fixes and read
    identically otherwise: nothing listening means Blender is not running, something listening that
    will not answer a framed ping means its port is taken. On this machine 8792 is held by a UE
    editor (docs/06 issue 15), and "Blender is not listening" sent me looking for a Blender that had
    failed to start instead of for the process squatting on it.
    """
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


ALLOW_INTERACTIVE_ENV = "MIF_BLENDER_ALLOW_INTERACTIVE"


def headless_verdict(info, allow_interactive=False):
    """(ok, reason) - may a MUTATING tool run against the Blender that returned this scene_info?

    Pure, and separate from the transport, so the refusal path can be tested against known
    responses. The alternative was to prove it by opening a windowed Blender on somebody's desktop,
    which is the thing this guard exists to avoid doing to them.

    FAIL CLOSED WHEN THE ANSWER IS MISSING, which is the opposite of the rule the rest of this repo
    runs on. Elsewhere "could not check" must never be reported as "is wrong" - here the cost of
    guessing wrong is somebody's unsaved scene, not a false line in a report, so an addon too old to
    report `background` gets a refusal and a named override rather than the benefit of the doubt.

    Strict `is True`, not truthiness: bpy.app.background is a real bool and survives JSON as one, so
    anything else - a string, a 1, a None - means this is not the field we think it is.
    """
    if allow_interactive:
        return True, ("%s is set - proceeding against an interactive Blender deliberately"
                      % ALLOW_INTERACTIVE_ENV)
    if not isinstance(info, dict):
        return False, "scene_info did not return an object, so this Blender cannot be identified"
    if "background" not in info:
        return False, ("scene_info does not report `background` - this addon predates the field, "
                       "so whether a person is looking at this Blender cannot be determined")
    if info.get("background") is True:
        return True, "background mode - no window, no unsaved work to lose"
    return False, "this Blender is INTERACTIVE - a person may have unsaved work open in it"


def allow_interactive_requested():
    """Is the override set? Absent, empty, 0 and false all mean no."""
    return os.environ.get(ALLOW_INTERACTIVE_ENV, "").strip() not in ("", "0", "false", "False")


def require_headless(name, call_fn=None):
    """None if it is safe to MUTATE this Blender; an exit code if the caller must stop.

    WHY THIS EXISTS. audit_blender_postconditions, test_blender_mesh and test_blender_rig all open
    by emptying the scene, and every one of them ran against whatever answered the port. Nothing
    asked whether that Blender had a person in front of it. Andre had Blender 5.0 open on
    2026-08-31 while these were being worked on; it listened on 38940 and the default here is 8792,
    so the only thing standing between an audit and somebody's open scene that day was a port
    number.

    The UE half of this repo already got this right - audit_detectors_fire refuses to plant into
    Source/ while an editor holds the project, and says outright that a short window is not a safety
    argument. Same reasoning, other backend.

    Deliberately AFTER the caller's own ping: an unreachable Blender is a SKIP with a diagnosis (see
    skip_banner), and turning that into "cannot determine background" would replace a good message
    with a worse one.
    """
    fn = call_fn or call
    try:
        info = fn("scene_info", {})
    except Exception as exc:                   # noqa: BLE001 - any transport failure is a refusal
        info = {"__transportError__": str(exc)}
    ok, why = headless_verdict(info, allow_interactive_requested())
    if ok:
        return None
    print("")
    print("REFUSED - nothing was verified, and nothing was changed.")
    print("  %s MUTATES the scene (it opens with clear_scene), and" % name)
    print("  %s" % why)
    print("  The Blender answering %s:%d." % (HOST, PORT))
    print("  Start a throwaway one instead:  python tools/run_blender_suites.py")
    print("  Or, if you really do mean this one:  set %s=1" % ALLOW_INTERACTIVE_ENV)
    print("  Exit code 2 means SKIPPED, distinct from 0 (passed) and 1 (failed) on purpose.")
    return 2


def skip_banner(name):
    """The loud skip every Blender suite should print. A skip that looks like a pass is how an
    untested thing gets believed, so it names what was NOT verified and why."""
    occupied = port_is_occupied()
    print("")
    print("SKIPPED - nothing was verified.")
    if occupied:
        # A squatter is not a missing Blender, and saying so saves the next reader the hunt.
        print("  Something IS listening on %s:%d but it is not a MifBlender - it accepted the"
              % (HOST, PORT))
        print("  connection and never answered a framed ping. That is a PORT CONFLICT, not a")
        print("  Blender that failed to start, and starting another one will not fix it.")
        print("  On this machine a UE editor has held that port before - see docs/06 issue 15.")
        print("  Find the holder with:")
        print("    Get-NetTCPConnection -LocalPort %d -State Listen | %%{Get-Process -Id $_.OwningProcess}"
              % PORT)
    else:
        print("  Nothing is listening on %s:%d, so no %s op was exercised." % (HOST, PORT, name))
        print("  Start one with tools/run_blender_suites.py, or run Blender with the MifBlender addon.")
    print("  Exit code 2 means SKIPPED, distinct from 0 (passed) and 1 (failed) on purpose.")
    return 2


def call(op, params=None, timeout=30.0):
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    try:
        msg = json.dumps({"endpoint": op, "token": TOKEN, "params": params or {}}).encode("utf-8")
        s.sendall(struct.pack(">I", len(msg)) + msg)
        hdr = b""
        while len(hdr) < 4:
            chunk = s.recv(4 - len(hdr))
            if not chunk:
                raise RuntimeError("connection closed reading length header")
            hdr += chunk
        n = struct.unpack(">I", hdr)[0]
        body = b""
        while len(body) < n:
            chunk = s.recv(n - len(body))
            if not chunk:
                raise RuntimeError("connection closed reading body")
            body += chunk
        return json.loads(body.decode("utf-8"))
    finally:
        s.close()
