"""Shared harness for long unattended audit runs against the SDK editor.

Three jobs:

  1. TALK TO THE RIGHT EDITOR. More than one Unreal editor is usually running on this machine
     (the DDS2 SDK on D:/UE532, and unrelated projects on stock engines). Only the SDK editor loads
     MifBridge, but "the bridge answered" is not proof of which process answered. Before any call,
     `require_sdk_bridge()` resolves the PID listening on the bridge port and refuses unless its
     command line names DrugDealerSimulator2.uproject.

  2. SURVIVE CRASHES. Fuzzing an editor plugin crashes the editor - that is the point. The runner
     detects a dead bridge, relaunches, and refuses to retry a call that has already killed the
     editor once, so one bad endpoint cannot eat the whole run in a relaunch loop.

  3. NOT LOSE FINDINGS. Everything goes to a JSONL file as it happens, never held only in memory.
     A run that dies at hour four still leaves everything it learned on disk.

SAFETY RULES BAKED IN, because this runs unattended:
  * Scratch assets live under /Game/_MifAudit* and nothing else is touched.
  * `confirm:true` is NEVER sent. Destructive endpoints are exercised only to check that they
    REFUSE without it - which is a real test, and a safe one.
  * Nothing is saved. save_* endpoints are on the deny list.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

BRIDGE_PORT = 8791
BASE = "http://127.0.0.1:%d/api" % BRIDGE_PORT
TOKEN = "dev"
UPROJECT = r"D:\DDS2SDK\Game\DrugDealerSimulator2.uproject"
EDITOR_EXE = r"D:\UE532\Engine\Binaries\Win64\UnrealEditor.exe"
PROJECT_MARKER = "DrugDealerSimulator2.uproject"

HERE = os.path.dirname(os.path.abspath(__file__))
FINDINGS = os.path.join(HERE, "audit_findings.jsonl")

# Endpoints this harness must never call unattended.
DENY = {
    # would end or restart the session the harness is driving
    "quit_editor", "restart_editor", "shutdown",
    # STARTS A PROFILER and leaves it running. Every sweep here (fuzz_endpoints, cooked_sweep,
    # audit_read_purity) enumerates endpoint_names() and filters on this set, so without the entry a
    # sweep would call trace_start, begin writing a .utrace, and never call trace_stop - degrading
    # performance for the remainder of the run and every run after it in the same session. Tracing is
    # a deliberate act with a matching stop, not something to fire blindly at 300 endpoints.
    # trace_stop is deliberately NOT denied: it is harmless, and leaving it callable means a stray
    # trace can be stopped.
    "trace_start",
    # writes to disk - the standing rule for this project is that audits save nothing
    "save_blueprint", "save_level", "save_level_as", "save_dirty_packages", "save_all",
    "save_asset", "save_package",
    # discards unsaved work in the open map without asking
    "new_level", "load_level", "open_level",
    # long-running or blocking; PIE in particular defers to the game thread
    "start_pie", "stop_pie", "pie_load_level_instance", "pie_unload_level_instance",
    "cook_content", "build_lighting", "build_navigation", "recompile_all",
    # drives an external process
    "run_console",
}

# Never send these keys, whatever the fuzz strategy says.
FORBIDDEN_KEYS = {"confirm", "force", "discardunsaved", "overwrite", "replaceexisting", "save"}



# Every PowerShell spawn below passes stdin=subprocess.DEVNULL. This is HARDENING, not a fix for
# anything proven: a child that inherits our stdin and reads it blocks forever, and combined with
# capture_output's pipes that is the documented shape of a subprocess hang on Windows - the direct
# child can be killed on timeout while a grandchild still holds the pipe handles, so the read never
# reaches EOF.
#
# Written down because the obvious suspicion was checked and was WRONG: when test_transactions wedged
# for 568s there were ten PowerShell processes alive, which looked like a leak from these calls. They
# all shared one unrelated parent and the oldest was five days old. None of them came from here. The
# stall remains unexplained; this closes a real hole next to it rather than pretending to be the cause.
# --------------------------------------------------------------------------- editor identity
# Set when the LAST bridge_pid() call failed to run its probe at all, as opposed to running it and
# finding no listener. Those are opposite situations and this function used to return None for both:
# "the editor is not there" versus "I could not look". Conflating them is how a suite could sit for
# 568 seconds reporting nothing - wait_for_bridge saw False, believed the editor was absent, and
# waited for an editor that had been answering the whole time.
_probe_failed = [None]


def bridge_pid():
    """PID listening on the bridge port, or None.

    None means TWO different things and the caller usually needs to know which, so the reason is left
    in _probe_failed: None when the probe ran and found nothing, or a string when the probe itself
    could not run (PowerShell refused to spawn, timed out under load, and so on).
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue"
             " | Select-Object -First 1).OwningProcess" % BRIDGE_PORT],
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=30).stdout.strip()
        _probe_failed[0] = None
        return int(out) if out.isdigit() else None
    except Exception as exc:
        _probe_failed[0] = "%s: %s" % (type(exc).__name__, exc)
        return None


def process_cmdline(pid):
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter 'ProcessId = %d').CommandLine" % pid],
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=30).stdout.strip()
    except Exception:
        return ""


# Identity is resolved by spawning PowerShell twice, which costs about a second. Doing that once
# per endpoint made the harness slower than the thing it was testing, so the verified PID is cached
# and only re-resolved when the port owner CHANGES - which is exactly when identity could have moved
# to a different editor. The cheap HTTP liveness check runs in between.
_verified_pid = [None]


def require_sdk_bridge(force=False):
    """(ok, message). Refuses when the port is owned by anything but the SDK editor.

    "The bridge answered" does not identify WHICH editor answered. Several are usually running,
    and driving a fuzz run at the wrong one would be both useless and destructive.
    """
    pid = bridge_pid()
    if pid is None:
        _verified_pid[0] = None
        if _probe_failed[0]:
            # Say which of the two it is. "I could not look" is not evidence the editor is gone, and
            # treating it as such is what turns a transient PowerShell hiccup into a silent wait.
            return False, ("could not probe port %d - the check itself failed (%s). This is NOT "
                           "evidence the editor is down." % (BRIDGE_PORT, _probe_failed[0]))
        return False, "nothing is listening on port %d" % BRIDGE_PORT
    if not force and pid == _verified_pid[0]:
        return True, "pid %d (cached)" % pid
    cmd = process_cmdline(pid)
    if PROJECT_MARKER not in cmd:
        _verified_pid[0] = None
        return False, ("port %d is owned by pid %d, which is NOT the SDK editor: %s"
                       % (BRIDGE_PORT, pid, cmd[:160]))
    _verified_pid[0] = pid
    return True, "pid %d (%s)" % (pid, PROJECT_MARKER)


def bridge_responsive(timeout=8):
    """Cheap liveness probe - no process spawning. Says nothing about identity."""
    try:
        raw_post("ping_or_audit_probe__", {}, timeout=timeout)
        return True
    except (Dead, Timeout):
        return False
    except Exception:
        return True          # any JSON answer, including "unknown endpoint", means it is alive


def sdk_editor_pid():
    """PID of the SDK editor process, running or not yet serving."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name = 'UnrealEditor.exe'\""
             " | Where-Object { $_.CommandLine -like '*%s*' }"
             " | Select-Object -First 1).ProcessId" % PROJECT_MARKER],
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=30).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


def launch_editor():
    # Clear the "Restore Packages" prompt FIRST, when it is safe to. An unattended run leaves unsaved
    # scratch packages behind, and after a kill the next launch opens a modal offering to restore them
    # - which goes up BEFORE the bridge starts serving, so the relaunch this function exists to perform
    # would wait out its whole timeout against an editor that is never going to answer. Recovering from
    # a crash is exactly when that must not happen.
    #
    # clear_scratch_restore refuses unless every entry is a scratch path, so a genuine recovery offer
    # for real work is left alone. Failure to clear is never fatal here: the launch still proceeds and
    # wait_for_bridge reports the blocked window by name.
    try:
        import clear_scratch_restore
        clear_scratch_restore.clear(quiet=True)
    except Exception:
        pass

    # LAUNCH DETACHED, WITH NO INHERITED STDIO. This used to be
    #     subprocess.run(['powershell', '-NoProfile', '-Command', "Start-Process ..."],
    #                    capture_output=True, ...)
    # and that hung a whole regression on 2026-08-26. Start-Process with no redirection hands the
    # editor PowerShell's inherited handles, and PowerShell's stdout is a PIPE created by that
    # capture_output=True. The editor then holds a pipe open for its entire lifetime - hours - while
    # subprocess.run in run_all_suites waits for the pipe to close rather than for the child to exit.
    # The symptom is brutal to diagnose: test_ik_rig sat at 'running...' for 17 minutes against a 3.2
    # second norm, its child process was already GONE, the editor was healthy and answering, and the
    # runner's own 900s timeout never fired because it was blocked in the post-kill read.
    #
    # Launching the exe directly removes both the PowerShell layer and the inheritance. DEVNULL on all
    # three streams means there is no pipe to hold; DETACHED_PROCESS plus CREATE_NEW_PROCESS_GROUP
    # means the editor does not die with, or signal, whatever launched it.
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [EDITOR_EXE, UPROJECT],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, close_fds=True)


def editor_window_title(pid):
    """The editor's main window title, or None. A title that is not the usual one is the tell."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Id %d -ErrorAction SilentlyContinue).MainWindowTitle" % pid],
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=20).stdout.strip()
        return out or None
    except Exception:
        return None



# --------------------------------------------------------------------------- sweep interlock
# A full sweep OWNS the editor. Suites share global editor state - most importantly the undo buffer,
# which is a single stack for the whole editor - so a second process driving the same editor does not
# merely produce its own wrong answers, it corrupts the sweep's.
#
# Demonstrated rather than theorised, on 2026-08-26: a manual test_transactions run started while a
# sweep was in flight issued undo_transactions against the global stack, reverted work belonging to
# whichever suite the sweep was mid-way through, and turned test_idempotence red in the sweep's own
# results. Neither run's failures named the cause.
#
# run_all_suites writes SWEEP_LOCK and puts its pid in MIF_SWEEP for the suites it launches, so its
# own children are exempt by construction. Anything else starting while the lock is held gets told.
SWEEP_LOCK = os.path.join(HERE, ".sweep-lock")


def sweep_owner():
    """Pid of a running sweep that this process is NOT part of, or None."""
    if os.environ.get("MIF_SWEEP"):
        return None                      # launched BY the sweep; the lock is ours
    try:
        pid = int(open(SWEEP_LOCK).read().strip())
    except Exception:
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Id %d -ErrorAction SilentlyContinue) -ne $null" % pid],
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=20).stdout.strip()
        return pid if out.lower().startswith("true") else None
    except Exception:
        return None                      # cannot tell - do not cry wolf


def warn_if_sweep_running():
    """Loudly, once. Not a refusal: a human who means it should still be able to run one suite."""
    pid = sweep_owner()
    if pid is None:
        return False
    print("")
    print("  !! A FULL SWEEP IS RUNNING (pid %d) AND OWNS THIS EDITOR." % pid)
    print("  !! Suites share global editor state - the undo buffer above all - so running this now")
    print("  !! will corrupt BOTH this run's results and the sweep's, and neither set of failures")
    print("  !! will say why. Wait for it, or stop it first.")
    print("")
    return True


def wait_for_bridge(timeout=900, quiet=False):
    """Block until the SDK editor is serving. Returns True/False.

    Distinguishes LOADING from BLOCKED. Both look the same from here - the port is bound and requests
    time out - but they need opposite responses: one wants patience, the other will never resolve on
    its own. A modal Slate window spins its own loop, so the ticker that serves this bridge stops
    running while the socket keeps accepting (02_GOTCHAS.md section 8). That happened today behind a
    project window titled "BA Welcome Screen", and waiting would have burned the full timeout.
    """
    warn_if_sweep_running()
    start = time.time()
    warned = False
    identity_warned = False
    last_why = ""
    while time.time() - start < timeout:
        ok, why = require_sdk_bridge()
        # THE SILENT PATH. Everything below reports when the port is bound but not ANSWERING; nothing
        # reported when require_sdk_bridge kept saying no. That path sleeps 5s and loops, so a caller
        # got no output whatsoever for the full 900s - a quarter-hour stall indistinguishable from a
        # hang, which is exactly what it looked like when test_transactions wedged for 568s during a
        # full run while the editor sat idle and answered everything else.
        #
        # Whatever the reason, it is IN `why` already; the loop simply never printed it. Said once,
        # after a grace period long enough for an honest relaunch, and again only if the reason
        # CHANGES - a message every five seconds would be its own kind of useless.
        if not ok:
            grace = time.time() - start > 60
            if grace and (not identity_warned or why != last_why):
                identity_warned = True
                last_why = why
                print("  [waiting %ds - the bridge is not usable yet: %s]" % (int(time.time() - start), why))
                print("  [this is the identity check, not a slow editor. If it does not change, the")
                print("   port is owned by another editor or nothing is listening at all.]")
        if ok:
            # An editor mid-load binds the port before it can answer, so the first probes time out.
            # Waiting is the whole point of this function - a timeout here is not an error.
            try:
                r = raw_post("self_audit", {"summaryOnly": True}, timeout=60)
            except (Dead, Timeout):
                # After a grace period long enough for any honest cold start, a bound-but-silent port
                # is worth naming rather than waiting out. Reported once, not every five seconds.
                if not warned and time.time() - start > 240:
                    warned = True
                    pid = bridge_pid()
                    title = editor_window_title(pid) if pid else None
                    print("  [bridge port is bound but not answering after %ds]" % int(time.time() - start))
                    if title:
                        print("  [editor window title: %r]" % title)
                        print("  [a title that is not the normal editor one means a MODAL is spinning")
                        print("   its own loop, which stops the ticker that serves this bridge. That")
                        print("   does not resolve on its own - see 02_GOTCHAS.md section 8.]")
                time.sleep(5)
                continue
            if isinstance(r, dict) and r.get("ok"):
                if not quiet:
                    print("bridge up on %s - %d endpoints, built %s %s"
                          % (why, r.get("endpointCount", -1), r.get("buildDate"), r.get("buildTime")))
                return True
        time.sleep(5)
    return False


def ensure_editor(max_relaunch=1):
    """Bring the SDK editor back if it died. Returns True when serving.

    Fast path first: if the bridge answers at all, and the port owner has not changed, there is
    nothing to do and no process needs spawning.
    """
    if _verified_pid[0] is not None and bridge_responsive():
        return True
    ok, _ = require_sdk_bridge()
    if ok:
        return True
    for _ in range(max_relaunch):
        if sdk_editor_pid() is None:
            print("  [editor gone - relaunching]")
            launch_editor()
        if wait_for_bridge(timeout=900, quiet=True):
            print("  [editor back]")
            return True
    return False


# --------------------------------------------------------------------------- calls
class Timeout(Exception):
    pass


class Dead(Exception):
    pass


def raw_post(endpoint, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + "/" + endpoint, data=body,
                                 headers={"X-Mif-Token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code, "_httpError": True}
    except urllib.error.URLError as e:
        raise Dead(str(e))
    except Exception as e:
        raise Timeout(str(e))
    try:
        return json.loads(raw)
    except Exception:
        # A non-JSON body from a JSON API is itself a finding.
        return {"ok": False, "error": "non-JSON response", "_raw": raw[:400], "_badJson": True}


def guarded_payload(payload):
    return {k: v for k, v in (payload or {}).items() if k.lower() not in FORBIDDEN_KEYS}


def call(endpoint, payload=None, timeout=60):
    """Post, refusing forbidden keys and denied endpoints. Raises Dead/Timeout."""
    if endpoint in DENY:
        return {"ok": False, "error": "denied by harness", "_denied": True}
    return raw_post(endpoint, guarded_payload(payload), timeout=timeout)


# --------------------------------------------------------------------------- findings
# One id per PROCESS, stamped on every finding. Without it the findings file - which is cumulative,
# append-only and untracked - cannot tell this run's results from last week's. That cost real time
# once: seven findings were triaged as current, and establishing that they WERE current came down to
# noticing that one endpoint named in them had not existed a few hours earlier. A timestamp answers it
# in a second.
RUN_ID = "%s-%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid())


def record(kind, endpoint, detail, severity="medium", **extra):
    """Append one finding. Written immediately - a run that dies keeps what it learned."""
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "runId": RUN_ID,
           "kind": kind, "endpoint": endpoint, "severity": severity, "detail": detail}
    row.update(extra)
    with open(FINDINGS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def load_findings():
    if not os.path.exists(FINDINGS):
        return []
    out = []
    for ln in open(FINDINGS, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def endpoint_names():
    r = raw_post("self_audit", {}, timeout=120)
    return sorted(r.get("endpoints") or []) if isinstance(r, dict) else []
