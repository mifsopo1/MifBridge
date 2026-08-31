"""Run every Blender suite against every installed Blender. The Blender half of run_all_suites.py.

WHY THIS EXISTS. run_all_suites.py globs test_*.py and runs it against the LIVE UNREAL EDITOR, so
the two Blender suites there can only ever report SKIPPED - nothing in that runner knows how to
start a Blender. Getting a real answer meant serving one by hand, one version at a time, from a
scratch script. That is not a thing anyone will do twice.

WHAT IT DOES. For each installed Blender: start it headless with the addon serving on a private
port, wait for a PING, run every tools/test_blender_*.py against it, kill it, move on. Then print
one matrix.

SEQUENTIAL, AND NOT AN OPTIMISATION PROBLEM. serve_forever() blocks and owns one port. Four probe
agents were once pointed at 8792 in parallel and spent their time losing a bind race with each
other, which produced a WrongError for every one of them - the addon said so plainly:

    FAILED to bind 127.0.0.1:8792 -- another MifBlender already owns that port

So this runs one Blender at a time, on a port well away from the default so it cannot collide with
a GUI Blender the user has open.

READINESS IS A PING, NEVER A CONNECT. Two readiness checks written during this work used
socket.connect() and both passed against a port whose owner had already exited; the next real call
got ECONNREFUSED. A connection existing is not a server answering.

Usage:
    python tools/run_blender_suites.py                # every installed version
    python tools/run_blender_suites.py --only 5.0     # one
    python tools/run_blender_suites.py --quiet        # exit code only
"""
import argparse
import glob
import io
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(HERE, "blender-addon")
# Deliberately NOT 8792. A GUI Blender with the addon enabled owns that one, and a runner that
# fights the user's own session for a port is a runner that reports nonsense.
PORT = int(os.environ.get("MIF_BLENDER_SUITE_PORT", "8795"))


def installed():
    found = []
    for pattern in (r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
                    r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
                    "/usr/share/blender/*/blender",
                    "/Applications/Blender*.app/Contents/MacOS/Blender"):
        for exe in glob.glob(pattern):
            m = re.search(r"(\d+\.\d+)", exe)
            found.append((m.group(1) if m else "?", exe))
    return sorted(set(found), key=lambda p: [int(x) for x in p[0].split(".")]
                  if p[0] != "?" else [0])


def suites():
    return sorted(glob.glob(os.path.join(HERE, "test_blender_*.py")))


def ping(port, timeout=2.0):
    """A real framed ping. Returns the response dict, or None."""
    body = json.dumps({"endpoint": "ping", "token": "dev", "params": {}}).encode("utf-8")
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        s.sendall(struct.pack(">I", len(body)) + body)
        head = b""
        while len(head) < 4:
            chunk = s.recv(4 - len(head))
            if not chunk:
                return None
            head += chunk
        want = struct.unpack(">I", head)[0]
        buf = b""
        while len(buf) < want:
            chunk = s.recv(min(65536, want - len(buf)))
            if not chunk:
                return None
            buf += chunk
        return json.loads(buf.decode("utf-8"))
    except Exception:
        return None
    finally:
        s.close()


def serve(exe, port):
    expr = ("import sys; sys.path.insert(0, r'%s'); import MifBlender; "
            "MifBlender.serve_forever(port=%d)" % (ADDON_DIR, port))
    return subprocess.Popen([exe, "--background", "--factory-startup", "--python-expr", expr],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


PASSFAIL = re.compile(r"PASS (\d+)\s+FAIL (\d+)")


def run_one(version, exe, quiet):
    """One FRESH Blender per suite, not one per version.

    Serving once per version was the first shape and it produced a false result immediately:
    test_blender_mesh ends with clear_scene - which is the point of its last assertion - and
    test_blender_ops then found an empty scene, had no mesh to adopt, and reported SKIPPED on all
    four versions. A suite that destroys the next suite's fixture is exactly the cross-suite state
    dependence run_all_suites' second pass exists to catch, and here it would have been read as
    "Blender cannot run that suite".

    A headless Blender starts in about two seconds. Isolation is worth far more than that.
    """
    rows = []
    for suite in suites():
        name = os.path.basename(suite)
        if not quiet:
            print("  %-8s %-26s running..." % (version, name))
        proc = serve(exe, PORT)
        try:
            hello = None
            for _ in range(80):
                if proc.poll() is not None:
                    tail = (proc.stdout.read() or "")[-400:]
                    rows.append({"version": version, "reported": "?", "suite": name,
                                 "state": "server died",
                                 "detail": tail.strip().splitlines()[-1] if tail.strip() else ""})
                    hello = None
                    break
                hello = ping(PORT)
                if hello is not None:
                    break
                time.sleep(0.5)
            if hello is None:
                if not rows or rows[-1]["suite"] != name:
                    rows.append({"version": version, "reported": "?", "suite": name,
                                 "state": "no ping", "detail": "port %d" % PORT})
                continue
            reported = hello.get("blenderVersionString", "?")

            env = dict(os.environ, MIF_BLENDER_PORT=str(PORT), MIF_BLENDER_TOKEN="dev")
            try:
                r = subprocess.run([sys.executable, suite], env=env, capture_output=True,
                                   text=True, timeout=900)
                out, err, code = r.stdout, r.stderr, r.returncode
            except subprocess.TimeoutExpired:
                rows.append({"version": version, "reported": reported, "suite": name,
                             "state": "TIMED OUT", "detail": "900s"})
                continue
            m = None
            for line in out.splitlines():
                m = PASSFAIL.search(line) or m
            state = {0: "pass", 1: "FAIL", 2: "skipped"}.get(code, "rc=%d" % code)
            detail = ("%s pass, %s fail" % (m.group(1), m.group(2))) if m else ""
            row = {"version": version, "reported": reported, "suite": name,
                   "state": state, "detail": detail}
            # KEEP THE OUTPUT OF A FAILING SUITE. This used to discard `out` entirely, so a failure
            # reported a bare "FAIL" with no detail and an empty count when the suite had died
            # before printing its summary at all - which is precisely when the output matters most.
            # The UE runner already records a tail for the same reason.
            if code not in (0, 2):
                # AND ITS STDERR. capture_output splits the streams, so a suite that dies
                # mid-run leaves its traceback in stderr while stdout ends on whatever passed
                # last. Keeping stdout alone showed twenty-five PASS lines and no cause -
                # which is the half of the output that cannot contain the reason.
                row["tail"] = "\n".join(out.splitlines()[-25:])
                if (err or "").strip():
                    row["tail"] += ("\n--- stderr ---\n"
                                    + "\n".join(err.splitlines()[-25:]))
            rows.append(row)
            if not quiet:
                print("  %-8s %-26s %-8s %s" % (version, name, state, detail))
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            # The port lingers in TIME_WAIT and the next run needs it. A short pause is cheaper
            # than a bind race that would be reported as a broken Blender.
            time.sleep(1.0)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="just this version, e.g. 5.0")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    versions = installed()
    if a.only:
        versions = [(v, e) for v, e in versions if v == a.only]
    if not versions:
        print("no Blender found (or --only matched nothing)")
        return 2
    if not suites():
        print("no tools/test_blender_*.py suites found")
        return 2

    if not a.quiet:
        print("%d Blender(s), %d suite(s), port %d\n"
              % (len(versions), len(suites()), PORT))

    rows = []
    for version, exe in versions:
        rows.extend(run_one(version, exe, a.quiet))

    bad = [r for r in rows if r["state"] not in ("pass", "skipped")]
    if not a.quiet:
        print("")
        print("%-8s %-12s %-26s %-8s %s" % ("version", "reported", "suite", "state", "detail"))
        for r in rows:
            print("%-8s %-12s %-26s %-8s %s"
                  % (r["version"], r.get("reported", "?"), r["suite"], r["state"], r["detail"]))
        print("")
        # PRINT THE TAIL OF ANYTHING THAT FAILED. A bare "FAIL" with an empty detail column - which
        # is what a suite that died before its summary produces - tells the reader nothing at all.
        for r in bad:
            if r.get("tail"):
                print("  --- %s on %s, last lines ---" % (r["suite"], r["version"]))
                for line in r["tail"].splitlines():
                    print("    " + line[:160])
                print("")
        skipped = [r for r in rows if r["state"] == "skipped"]
        for r in skipped:
            # Named, not counted - the same rule run_all_suites uses. A skip nobody reads is
            # indistinguishable from a pass.
            print("  SKIPPED (verified nothing): %s on %s" % (r["suite"], r["version"]))
        print("%d run(s) across %d Blender(s), %d failed, %d skipped"
              % (len(rows), len(versions), len(bad), len(skipped)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
