"""Does the headless guard actually stop a mutating Blender tool, and does it let a real one past?

WHAT IT PROTECTS. audit_blender_postconditions, test_blender_mesh and test_blender_rig all open by
emptying the scene, and until 2026-08-31 every one of them did that to whatever answered the port.
Nothing asked whether a person was sitting in front of that Blender. Andre had Blender 5.0 open
while these were being written; it listened on 38940 and the default here is 8792, so the only thing
between an audit and somebody's unsaved work that day was a port number.

WHY A FAKE BLENDER RATHER THAN A REAL ONE. The refusal path needs a server that reports
background:False, and the honest way to get one is to open a windowed Blender on somebody's desktop
- which is precisely the thing the guard exists to avoid doing to them. So the transport is real
(same 4-byte length prefix, same framing, a real socket, the tools run as real subprocesses) and
only the Blender is not. The tools cannot tell, which is the point: they are not being mocked, they
are being answered.

BOTH DIRECTIONS, because a guard that refuses everything is not a guard. The same fake is flipped to
background:True and each tool must get PAST it - a check that is never reached proves nothing, and
this repo has already shipped one of those.

Usage:
    python tools/test_blender_headless_guard.py

Exit codes:  0 all checks passed   1 a check failed   (never 2 - this suite needs no live process)
"""
import json
import os
import socket
import struct
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ("audit_blender_postconditions.py", "test_blender_mesh.py", "test_blender_rig.py")

PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s" % label)
        if detail:
            print("        %s" % str(detail)[:300])


class FakeBlender(threading.Thread):
    """A MifBlender that answers the framed protocol and nothing else.

    background is settable, because that single field is the whole subject of this suite.
    """

    daemon = True

    def __init__(self, background):
        threading.Thread.__init__(self)
        self.background = background
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.stop = False
        self.seen = []

    def reply(self, endpoint):
        if endpoint == "ping":
            return {"ok": True, "pong": True, "addon": "MifBlender", "protocolVersion": 1,
                    "blenderVersionString": "0.0 (fake)", "blenderVersion": [0, 0, 0]}
        if endpoint == "scene_info":
            body = {"ok": True, "objectCount": 0, "objects": []}
            if self.background is not None:
                body["background"] = self.background
            return body
        # Anything else: a well-formed refusal. A mutating tool that gets this far has already
        # defeated the guard, which is what the assertions below are actually watching for.
        return {"ok": False, "error": "fake Blender: %s not implemented" % endpoint}

    def run(self):
        while not self.stop:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            try:
                head = b""
                while len(head) < 4:
                    c = conn.recv(4 - len(head))
                    if not c:
                        break
                    head += c
                if len(head) < 4:
                    continue
                n = struct.unpack(">I", head)[0]
                buf = b""
                while len(buf) < n:
                    c = conn.recv(min(65536, n - len(buf)))
                    if not c:
                        break
                    buf += c
                req = json.loads(buf.decode("utf-8"))
                self.seen.append(req.get("endpoint"))
                out = json.dumps(self.reply(req.get("endpoint"))).encode("utf-8")
                conn.sendall(struct.pack(">I", len(out)) + out)
            except Exception:
                pass
            finally:
                conn.close()

    def close(self):
        self.stop = True
        try:
            self.srv.close()
        except OSError:
            pass


def run_tool(tool, port, allow=None, timeout=120):
    env = dict(os.environ)
    env["MIF_BLENDER_PORT"] = str(port)
    env["MIF_BLENDER_HOST"] = "127.0.0.1"
    env.pop("MIF_BLENDER_ALLOW_INTERACTIVE", None)
    if allow is not None:
        env["MIF_BLENDER_ALLOW_INTERACTIVE"] = allow
    args = [sys.executable, os.path.join(HERE, tool)]
    if tool == "audit_blender_postconditions.py":
        # It exits 2 on a missing FBX BEFORE it ever pings, which would look exactly like a refusal.
        # Hand it this file: it only has to exist for the tool to reach the guard.
        args += ["--fbx", os.path.abspath(__file__)]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL, timeout=timeout, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    print("headless guard - three mutating tools against a fake Blender")

    import blender_audit_common as B

    # ---------------------------------------------------------------- the pure decision
    print("")
    print("=== G1: headless_verdict, one known response each ===")
    cases = [
        ("background True allows", {"ok": True, "background": True}, True),
        ("background False refuses", {"ok": True, "background": False}, False),
        ("field absent refuses (fail closed)", {"ok": True, "objectCount": 0}, False),
        ("a string 'true' refuses - not the field we think it is",
         {"ok": True, "background": "true"}, False),
        ("background 1 refuses - bpy.app.background is a bool", {"ok": True, "background": 1},
         False),
        ("a transport failure refuses", {"__transportError__": "boom"}, False),
        ("not a dict at all refuses", "background: True", False),
    ]
    for label, info, want in cases:
        got, why = B.headless_verdict(info, allow_interactive=False)
        check("G1 %s" % label, got is want, "got %r - %s" % (got, why))
    got, _ = B.headless_verdict({"ok": True, "background": False}, allow_interactive=True)
    check("G1 the override allows an interactive one deliberately", got is True)

    # ---------------------------------------------------------------- refusal, end to end
    fake = FakeBlender(background=False)
    fake.start()
    print("")
    print("=== G2: an INTERACTIVE Blender - every mutating tool must refuse (port %d) ==="
          % fake.port)
    try:
        for tool in TOOLS:
            rc, out = run_tool(tool, fake.port)
            check("G2 %s exits 2" % tool, rc == 2, "exit=%d" % rc)
            check("G2 %s says REFUSED" % tool, "REFUSED" in out, out[-300:])
            check("G2 %s names the reason" % tool, "INTERACTIVE" in out, out[-300:])
            # THE ASSERTION THAT MATTERS. Anything past scene_info means the guard let a mutating
            # call through, and on a real Blender the next one is clear_scene.
            mutated = [e for e in fake.seen if e not in ("ping", "scene_info")]
            check("G2 %s sent NOTHING but ping/scene_info" % tool, not mutated, mutated)
            fake.seen = []
    finally:
        fake.close()

    # ---------------------------------------------------------------- and it is REACHED
    fake2 = FakeBlender(background=True)
    fake2.start()
    print("")
    print("=== G3: a HEADLESS Blender - the same tools must get PAST the guard (port %d) ==="
          % fake2.port)
    try:
        for tool in TOOLS:
            rc, out = run_tool(tool, fake2.port)
            check("G3 %s does not refuse" % tool, "REFUSED" not in out, out[-300:])
            # It will fail on the fake's canned refusals, and that is fine - what is being asserted
            # is that it got far enough to try. A guard that blocks everything looks identical to a
            # guard that works, right up until it costs somebody a real run.
            tried = [e for e in fake2.seen if e not in ("ping", "scene_info")]
            check("G3 %s went on to call a real op" % tool, bool(tried), fake2.seen)
            fake2.seen = []
    finally:
        fake2.close()

    # ---------------------------------------------------------------- the override, end to end
    fake3 = FakeBlender(background=False)
    fake3.start()
    print("")
    print("=== G4: %s lets a deliberate run through an interactive Blender ==="
          % B.ALLOW_INTERACTIVE_ENV)
    try:
        rc, out = run_tool(TOOLS[0], fake3.port, allow="1")
        check("G4 the override is honoured", "REFUSED" not in out, out[-300:])
        rc, out = run_tool(TOOLS[0], fake3.port, allow="0")
        check("G4 but '0' is not a yes", "REFUSED" in out, out[-300:])
        rc, out = run_tool(TOOLS[0], fake3.port, allow="")
        check("G4 and neither is empty", "REFUSED" in out, out[-300:])
    finally:
        fake3.close()

    print("")
    print("%d PASS  %d FAIL" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main())
