"""One command that answers "did I install this correctly?" for all three pieces.

WHY THIS EXISTS. MifBridge is three installs - the Unreal plugin, the Blender addon, and the MCP
server plus a client config - and the README verifies exactly one of them, with a single curl buried
in section 1. A new user who gets step 3 subtly wrong finds out when an agent says a tool is
unavailable, which reads as "this product does not work" rather than "pip install did not run".

That is the most expensive failure this project has, because it happens to somebody who has just
arrived and has no way to tell a broken install from a broken product.

REQUIRED AND OPTIONAL ARE NOT THE SAME, and conflating them is how a checker gets ignored. The
Blender addon is OPTIONAL - the README says so and means it - so its absence is reported as a NOTE
and never as a failure. A tool that fails because you did not install a thing you were told you did
not need is a tool people stop running.

IT CHECKS WHAT IT CAN REACH, AND SAYS WHAT IT CANNOT. Nothing here starts an editor, a Blender or a
server; those are the user's to run, and a checker that launched things would be surprising in a way
a diagnostic must never be. Anything not running is reported as not running, with the command that
starts it.

Usage:
    python tools/verify_install.py
Exit: 0 everything required is working, 1 something required is not, 2 could not tell
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

UE_PORT = int(os.environ.get("MIF_BRIDGE_PORT", "8791"))
UE_TOKEN = os.environ.get("MIF_BRIDGE_TOKEN", "dev")
BL_PORT = int(os.environ.get("MIF_BLENDER_PORT", "8792"))

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FAILURES = []   # required things that are wrong
NOTES = []      # optional things, and context


def fail(what, why, fix):
    FAILURES.append((what, why, fix))


def ok(what, detail=""):
    print("  OK        %-22s %s" % (what, detail))


def note(what, detail):
    NOTES.append((what, detail))
    print("  optional  %-22s %s" % (what, detail))


# --------------------------------------------------------------------------- 1. the UE plugin
def check_unreal():
    """The one piece that is genuinely required - everything else is a way to talk to it."""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/self_audit" % UE_PORT, data=b"{}",
            headers={"X-Mif-Token": UE_TOKEN, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        ok("Unreal bridge", "port %d, %s endpoints" % (UE_PORT, d.get("endpointCount")))
        return d
    except urllib.error.HTTPError as exc:
        # A 403 IS A DIFFERENT PROBLEM FROM A DEAD PORT, and telling them apart is most of the value
        # here: one means the plugin is fine and the token is wrong, the other means it is not
        # running. Reporting both as "cannot connect" sends people to rebuild a working plugin.
        if exc.code in (401, 403):
            fail("Unreal bridge", "the plugin answered but REFUSED the token (HTTP %d)" % exc.code,
                 "the editor reads MIF_BRIDGE_TOKEN from its own process environment at startup - "
                 "set it before launching the editor, or use the default 'dev'")
        else:
            fail("Unreal bridge", "the plugin answered HTTP %d" % exc.code,
                 "check the editor's Output Log for MifBridge errors")
        return None
    except Exception as exc:                                        # noqa: BLE001
        # Distinguish "nothing listening" from "something listening that is not us", because on this
        # machine 8791 has been held by an unrelated process before now.
        squatter = False
        try:
            s = socket.create_connection(("127.0.0.1", UE_PORT), timeout=1.5)
            s.close()
            squatter = True
        except OSError:
            pass
        if squatter:
            fail("Unreal bridge",
                 "something is listening on %d but it is not answering as MifBridge" % UE_PORT,
                 "another program has the port. Set MIF_BRIDGE_PORT to a free one before starting "
                 "the editor, or stop the other program")
        else:
            fail("Unreal bridge", "nothing is listening on port %d (%s)" % (UE_PORT, str(exc)[:60]),
                 "open the project in Unreal, enable the plugin, then Tools > Mif Bridge: Start")
        return None


# --------------------------------------------------------------------------- 2. the Blender addon
def check_blender():
    """OPTIONAL. Its absence is a note, never a failure - the README promises exactly that."""
    try:
        sys.path.insert(0, HERE)
        import blender_audit_common as B
        B.PORT = BL_PORT
        if B.reachable(timeout=2.0):
            d = B.call("ping", {}, timeout=5.0)
            ok("Blender addon", "port %d, Blender %s"
               % (BL_PORT, d.get("blenderVersionString") or "?"))
            return d
    except Exception:                                               # noqa: BLE001
        pass
    note("Blender addon",
         "not reachable on port %d. This is FINE if you did not install it - the bl_* tools simply "
         "report the backend is unreachable and everything else works. To install: python "
         "tools/blender-addon/build_zip.py, then Blender > Preferences > Add-ons > Install" % BL_PORT)
    return None


# --------------------------------------------------------------------------- 3. the MCP server
def check_mcp():
    """The piece that goes wrong most often, because it is the one with dependencies."""
    server = os.path.join(HERE, "mcp-server", "server.py")
    if not os.path.isfile(server):
        fail("MCP server", "tools/mcp-server/server.py is missing",
             "the download is incomplete - re-extract the plugin")
        return

    missing = []
    for mod in ("mcp", "requests"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        # THE MOST COMMON INSTALL FAILURE, and the one with the least helpful symptom: the agent
        # simply reports the tools as unavailable, which reads as a broken product.
        fail("MCP server", "python packages not installed: %s" % ", ".join(missing),
             "pip install %s  (use the SAME python your MCP client launches, which is often not "
             "the one on your PATH)" % " ".join(missing))
        return
    ok("MCP deps", "mcp and requests are importable by %s" % os.path.basename(sys.executable))

    # Does the server file at least parse under this interpreter? A syntax error from a partial
    # download or a Python that is too old is otherwise discovered by the client, silently.
    r = subprocess.run([sys.executable, "-m", "py_compile", server],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        fail("MCP server", "server.py does not compile under %s" % sys.version.split()[0],
             (r.stderr or "").strip()[:200])
    else:
        ok("MCP server", "compiles under python %s" % sys.version.split()[0])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.parse_args()

    print("MifBridge install check")
    print("  three pieces: the Unreal plugin (required), the MCP server (required to drive it")
    print("  from an agent), and the Blender addon (optional).")
    print("")

    ue = check_unreal()
    check_blender()
    check_mcp()

    print("")
    if FAILURES:
        print("%d problem(s):" % len(FAILURES))
        for what, why, fix in FAILURES:
            print("  - %-16s %s" % (what, why))
            print("    fix: %s" % fix)
        print("")
        print("Nothing above was changed or started - these are yours to fix.")
        return 1

    print("OK  everything required is working%s."
          % (" (%s endpoints)" % ue.get("endpointCount") if ue else ""))
    if NOTES:
        print("    %d optional piece(s) not installed, listed above - that is a choice, not a fault."
              % len(NOTES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
