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
import io
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


def bridge_liveness(timeout=8):
    """('alive'|'busy'|'dead') - and the middle one is the whole point of this function.

    A TIMEOUT IS NOT DEATH. Every MifBridge endpoint runs on the GAME THREAD
    (MifBridgeServer.cpp:405-411 takes the IsInGameThread() branch and executes the handler inline),
    and FHttpServerModule is a ticker object, so accepting connections and dispatching requests both
    happen inside FTSTicker::Tick(). Anything that blocks the game thread - PIE startup, a blueprint
    compile, an asset registry scan - stalls both while the listen socket stays open, because
    nothing calls StopListening. The editor is alive and will answer again shortly.

    Collapsing that into a single False is what hung a 288-run sweep: run_all_suites relaunched the
    editor because the bridge "was not responsive", the old editor was merely busy and still running,
    and the two raced for port 8791.

      dead   nothing is listening - the process is gone, relaunching is correct
      busy   listening, not answering in time - WAIT, do not relaunch
      alive  answered
    """
    try:
        raw_post("ping_or_audit_probe__", {}, timeout=timeout)
        return "alive"
    except Timeout:
        return "busy"
    except Dead:
        # A refused connection means the PORT is silent. That is not the same as the process being
        # gone, and conflating them is worse than the bug this function was written to fix: an
        # editor that is still starting has no listener yet, and launch_editor now kills survivors
        # before relaunching - so calling that "dead" would kill a healthy starting editor and do
        # it again on the next slow start. Observed live: a fresh editor read busy, busy, busy,
        # dead, dead, dead while its log was progressing normally (DDC maintenance alone took two
        # minutes).
        #
        # The process list settles it, and this module already had the call.
        if _port_is_listening():
            return "busy"
        return "busy" if _surviving_editor_pids() else "dead"
    except Exception:
        return "alive"       # any JSON answer, including "unknown endpoint", means it is alive


def _port_is_listening(port=None):
    """True if anything holds the bridge port open, regardless of whether it answers."""
    import socket as _socket
    s = _socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port or BRIDGE_PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def bridge_responsive(timeout=8):
    """Back-compat wrapper. Prefer bridge_liveness - 'busy' and 'dead' need different responses."""
    return bridge_liveness(timeout) == "alive"


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


def _all_editor_pids():
    """Every UnrealEditor.exe pid, whatever project it belongs to."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq UnrealEditor.exe", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [c.strip('" ') for c in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("unrealeditor"):
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def _editor_cmdline(pid):
    """The command line of one pid, or '' - used to tell WHOSE editor this is."""
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % pid],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def _surviving_editor_pids():
    """PIDs of UnrealEditor.exe running THIS project. Never anyone else's.

    THIS FILTER IS THE WHOLE POINT. It used to list every UnrealEditor on the machine, and the
    recovery below then ran `taskkill /IM UnrealEditor.exe /F` - a blanket kill. Another session
    runs a Curfew editor on this machine, so that was silently killing someone else's work; it is
    why an editor appeared to keep "coming back up" during a probe run, when in fact the peer was
    relaunching the one just killed out from under them.

    PROJECT_MARKER was already used at the responsiveness check to confirm a pid belongs here. The
    kill path simply did not ask.

    tasklist rather than psutil: this module is imported by every suite and must not need a
    third-party package to do its job.
    """
    mine = []
    for pid in _all_editor_pids():
        cmd = _editor_cmdline(pid)
        # No command line readable (permissions, a race) means NOT PROVEN MINE, so leave it alone.
        # Guessing wrong in this direction kills another project's editor.
        if cmd and PROJECT_MARKER in cmd:
            mine.append(pid)
    return mine


def launch_editor(write_mode=None):
    # LEAVE EXACTLY ONE EDITOR BEHIND. This function is the recovery path, called when
    # wait_for_bridge has already failed, and it used to assume that meant the editor was dead -
    # true when a suite CRASHES it, which is the case it was written for.
    #
    # It is false in the case seen on 2026-08-30: the bridge listener stopped answering on 8791
    # while the editor process stayed alive and responsive, with no stop message anywhere in its
    # log. launch_editor then started a SECOND editor, the two raced for the port, and the sweep
    # hung on the next suite for six minutes. Two editors on one project is also a way to lose
    # work - both hold the same packages open.
    #
    # So a survivor is killed rather than joined, and LOUDLY: an unresponsive editor being
    # terminated is exactly the kind of thing that must not happen quietly in an unattended run.
    survivors = _surviving_editor_pids()
    if survivors:
        others = [p for p in _all_editor_pids() if p not in survivors]
        if others:
            # Reported, never touched. If one of these holds the port that is a conflict to
            # SURFACE, not to resolve by killing another session's editor.
            print("!! launch_editor: %d editor(s) from OTHER projects are running (%s). They are "
                  "left alone." % (len(others), ", ".join(str(x) for x in others)))
        print("!! launch_editor: %d editor(s) for THIS project still running (%s) but the bridge "
              "did not answer." % (len(survivors), ", ".join(str(x) for x in survivors)))
        print("!! Killing them before relaunching - a second editor on the same project races for")
        print("!! port %d and holds the same packages open. Seen hanging a sweep on 2026-08-30."
              % BRIDGE_PORT)
        # PER-PID, never by image name. `taskkill /IM UnrealEditor.exe /F` takes down every editor
        # on the machine - including the Curfew session's, which is not ours to close.
        for pid in survivors:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, timeout=60)
            except Exception as exc:
                print("!! taskkill on pid %d failed (%s) - launching anyway, but expect a port "
                      "race." % (pid, exc))
        deadline = time.time() + 30.0
        while time.time() < deadline and _surviving_editor_pids():
            time.sleep(1.0)
        still = _surviving_editor_pids()
        if still:
            print("!! %d editor(s) SURVIVED the kill (%s). Not launching another - that would make "
                  "the problem worse rather than recover from it."
                  % (len(still), ", ".join(str(x) for x in still)))
            return False
        # A closing editor keeps a file handle briefly after the process list clears.
        time.sleep(5.0)

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

    # THE WRITE MODE IS CHOSEN AT LAUNCH, and that is not a hole in the gate.
    #
    # Andre: "if llm launches the editor it can adjust the flag". The gate's rule is that it is not
    # settable OVER THE BRIDGE - an agent must not be able to unlock the session it is already inside.
    # Choosing the mode when you START the process is a different act, and it is exactly what a human
    # does with setx. Whoever launches a process decides its environment; that has always been true and
    # the gate never claimed otherwise.
    #
    # Worth being blunt about the consequence rather than letting it read as airtight: an agent that
    # can launch editors can therefore choose 'full'. So the gate protects a RUNNING session from
    # itself - a bridge call cannot loosen the rules it is being judged by, and nor can anything that
    # reaches Slate from inside a handler. It does not protect against whoever starts the editor,
    # because that party was never inside the gate.
    #
    # Default None = inherit, which on a machine with MIF_BRIDGE_WRITE_MODE set at User scope means
    # the value Andre chose, and otherwise means 'scratch'. Passing a value is deliberate and logged.
    env = None
    if write_mode:
        env = dict(os.environ)
        env["MIF_BRIDGE_WRITE_MODE"] = str(write_mode)
        print("  launching with MIF_BRIDGE_WRITE_MODE=%s" % write_mode, flush=True)

    subprocess.Popen(
        [EDITOR_EXE, UPROJECT],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, close_fds=True, env=env)


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


# Every suite in this repo names its fixtures the same way, and that convention is what makes the
# check below possible: scratch ACTORS carry a label beginning "Mif" (MifHeightmapFixture,
# MifGuardProbe, MifLayerReg, MifASCTest, ...) and scratch ASSETS live under /Game/_Mif*.
SCRATCH_LABEL_PREFIX = "Mif"
SCRATCH_ASSET_PREFIX = "/Game/_Mif"

# THE EXCEPTION, and it is a real one rather than a hypothetical. /Game/Maps/MifWeaponTest is a
# genuine project map that several suites use ON PURPOSE - it is one of the very few LOOSE (uncooked)
# maps here, so it is the only thing the sublevel family can be tested against. The Mif prefix does
# NOT mean scratch in a package path; only the /Game/_Mif prefix does. Label and path are therefore
# judged separately, and this list exists so the distinction is stated rather than remembered.
NOT_SCRATCH_DESPITE_THE_NAME = ("/Game/Maps/MifWeaponTest",)


def is_scratch_fixture(row):
    """Is this level object somebody else's SCRATCH, rather than the project's own content?

    WHY A SUITE NEEDS TO ASK. Several suites prefer an existing fixture and only build their own if
    the level has none - "use the level's landscape if it has one" is cheaper and exercises real
    content. That is safe exactly as long as no OTHER suite produces something the selector matches.

    It stopped being safe on 2026-09-01. test_landscape_heightmap takes the first landscape with no
    edit layers, which never fired because this project's own landscape has them; then
    test_landscape_layer_register began creating one through create_landscape, which deliberately
    leaves edit layers off. On the second pass of a sweep, heightmap adopted that leftover and
    measured collision against heights it had never set - reporting a 1590uu error against a
    perfectly good endpoint. The suite was right; it was measuring the wrong terrain.

    So the rule is: a suite hunting for something to ADOPT must skip anything that looks like a
    fixture. Takes a row as returned by list_level_actors or landscape_info - anything carrying
    `label` and/or `actorPath`.

    DELIBERATELY CONSERVATIVE. A false positive here costs a suite one candidate and it moves on to
    the next or builds its own; a false negative is the bug above, which reports as a failure in
    unrelated code. When the two are not equally bad, lean toward the cheap one.
    """
    if not isinstance(row, dict):
        return False
    label = str(row.get("label") or "")
    if label.startswith(SCRATCH_LABEL_PREFIX):
        return True
    path = str(row.get("actorPath") or row.get("objectPath") or row.get("path") or "")
    if any(path.startswith(ok) for ok in NOT_SCRATCH_DESPITE_THE_NAME):
        return False
    if SCRATCH_ASSET_PREFIX in path:
        return True
    # WHAT THIS DOES NOT CATCH, said plainly because the first draft of this function claimed it did.
    # An actor spawned from a scratch BLUEPRINT with no label set carries only the class name in its
    # path - ...PersistentLevel.BP_ASCFix46961_C_UAID_... - and those classes are named BP_ASCFix,
    # BP_NoASC, BP_NS_, BP_Probe with no shared prefix, so there is nothing here to match on. Such an
    # actor reads as adoptable. The fix for that is at the other end: a suite spawning a fixture
    # should give it a Mif* label, which spawn_actor_in_level takes and most already pass.
    return False


def pick_adoptable(rows, want=None):
    """First row that is NOT somebody's scratch, or None. The counterpart to is_scratch_fixture.

    `want` is an optional predicate applied on top - so "the first non-scratch landscape with no edit
    layers" is pick_adoptable(rows, lambda r: not r.get("editLayers")).

    Returning None is a normal answer meaning "nothing here is safe to adopt, build your own", which
    is what every caller of this should already do when the level has no candidate at all.
    """
    for row in rows or []:
        if is_scratch_fixture(row):
            continue
        if want is None or want(row):
            return row
    return None


def cleanup_level_actor(actor_path, what="scratch actor"):
    """Delete a level actor a suite spawned. Returns the delete_level_actor response.

    WHY SUITES MUST DO THIS, and why it is here rather than copied again. Endpoints like
    add_nav_volume, create_water_body and create_landscape spawn into the EDITOR world
    (World->SpawnActor against ActiveWorld(), not a PIE-scoped one), so what they create is NOT torn
    down when PIE stops. It persists in the persistent level and is carried into every later PIE
    session, one more accumulating per run.

    That is not hypothetical: an uncleaned NavMeshBoundsVolume silently broke test_pie_family.py's
    T1606, whose precondition is "0 NavMeshBoundsVolume actors -> no navigation coverage". One
    parked a million units away, providing no real coverage anywhere a pawn stands, still made that
    false. A suite leaking state into another suite's preconditions is the worst kind of test bug,
    because the failure lands somewhere else entirely.

    Added to mifaudit 2026-08-30: the cleanup was written for exactly one of eight spawn sites on
    2026-08-29 (958213a), one of the others being thirty lines above it in the same file.

    GOES THROUGH THE GUARD WHEN IT CAN, updated 2026-08-30. If the actor was spawned via
    scratch_confirm.spawn_tracked, that module watched it being created in this process and can prove
    the path is ours, so the delete goes through confirm_call like any other guarded write. If it was
    not - an actor spawned before tracking existed, or one PIE created - this falls back to the old
    deliberate bypass, because the prefix check genuinely cannot speak about a live actor path and
    would refuse it wrongly. The fallback is the exception now rather than the rule.

    The import is inside the function on purpose: scratch_confirm imports this module, so importing
    it at the top would be circular.
    """
    if not actor_path:
        return {"ok": False, "error": "no actorPath to clean up", "skipped": True}
    try:
        import scratch_confirm as _SC
        if _SC.spawned_here(actor_path):
            return _SC.confirm_call("delete_level_actor", {"actorPath": actor_path})
        return raw_post("delete_level_actor", {"actorPath": actor_path, "confirm": True})
    except Timeout:
        return {"ok": False, "error": "delete_level_actor timed out cleaning up %s" % what}


def write_mode():
    """The bridge's current write mode, lowercased, or "" if it cannot be read."""
    try:
        return (call("self_audit", {}).get("writeMode") or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def gated_in_this_mode(endpoint, what=None):
    """True when `endpoint` is on the safety gate's list AND the mode would refuse it.

    The SECTION-level companion to needs_full_write_mode. A whole suite skipping is right when
    everything downstream of a gated call is dead - four PIE suites are in that position. It is
    wrong when only a couple of assertions are affected and the other twenty-nine are perfectly
    good coverage: test_uncovered_reads6 has 29 passing checks and two that need
    run_console_captured, and skipping the file would throw away real verification to avoid two
    false failures.

    So this reports, the caller prints a note and moves on, and the suite still exits 0 having
    honestly covered what it could. What it must NOT do is assert - the gate refusing a gated
    endpoint is the gate working.
    """
    mode = write_mode()
    if mode == "full":
        return False
    print("  NOTE  %s needs '%s', which the safety gate refuses in '%s' mode - not exercised here."
          % (what or endpoint, endpoint, mode or "unknown"))
    print("        Reported rather than asserted: a refusal from the gate is the gate working, and")
    print("        a test that fails when a security control works trains people to ignore it.")
    return True


def needs_full_write_mode(what="this suite"):
    """True when the gate would refuse the work, so the caller should SKIP rather than FAIL.

    WHY THIS EXISTS, found by running the full regression in scratch mode on 2026-08-30. Four
    suites drive PIE, start_pie is on the safety gate's unsafe list, and RefuseIfGated runs in the
    DISPATCHER before the handler is entered. So in scratch or read mode those suites reported
    FAILURES - "start_pie accepted" false, "PIE actually reached state=running" false - while the
    gate was doing exactly its job. A test that fails when a security control works is a test that
    trains people to ignore it.

    SKIP, NOT PASS, and the distinction is this project's own: run_all_suites reports exit 2
    separately from exit 0 precisely because a suite returning success over work it never did
    manufactures confidence. Exit 2 says "not exercised here", which is the truth.

    The caller prints its own reason and returns 2; this only answers the question, so a suite that
    has non-PIE assertions worth running can call it late and still cover them.
    """
    mode = write_mode()
    if mode == "full":
        return False
    print("  SKIP  %s needs write mode 'full' and the bridge is in '%s'." % (what, mode or "unknown"))
    print("        start_pie is on the safety gate's unsafe list and RefuseIfGated runs in the")
    print("        dispatcher, so PIE cannot start and every assertion downstream of it would fail")
    print("        for a reason that is the gate working correctly, not a defect.")
    print("        Relaunch with MIF_BRIDGE_WRITE_MODE=full to exercise this.")
    return True


def wait_for_pie_state(target, timeout=60, poll_timeout=10):
    """Poll pie_status until `state` == target, or the outer budget expires.

    ONE copy, here, since 2026-08-30. There were three - test_pie_family.py, test_game_framework.py
    and test_livelink.py - and only the first was ever fixed, which is the whole reason this lives in
    mifaudit now instead of being copied a fourth time.

    THE BUG THE FIX WAS ABOUT, found live 2026-08-29 chasing a real editor hang during a regression
    sweep: each poll used raw_post's 60s DEFAULT while the outer budget was 30s or 60s, so a single
    slow poll could eat the entire budget by itself and the outer timeout was never really enforced.
    Worse, raw_post RAISES Timeout rather than returning a dict, and none of the loops caught it - so
    "PIE never reached the state" surfaced as an unhandled exception killing the suite instead of a
    clean failure the caller could report. poll_timeout must therefore stay well under timeout.

    Returns the last pie_status dict. On repeated timeouts returns an ok:false dict with state None,
    so callers can assert on it exactly like any other response instead of guarding for exceptions.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = raw_post("pie_status", {}, timeout=poll_timeout)
        except Timeout:
            time.sleep(1)
            continue
        if s.get("state") == target:
            return s
        time.sleep(1)
    try:
        return raw_post("pie_status", {}, timeout=poll_timeout)
    except Timeout:
        return {"ok": False, "error": "pie_status timed out repeatedly - the bridge may be hung",
                "state": None}


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
                    # "compiled" not "built", and the word is doing real work. This is __DATE__ /
                    # __TIME__ from whichever translation unit carries the banner, so an INCREMENTAL
                    # build that did not have to recompile that file leaves the stamp UNCHANGED
                    # while the DLL relinks. On 2026-08-31 this line read 15:37:47 for a DLL
                    # relinked at 19:17 whose new fields were demonstrably live, and it cost real
                    # time before anyone doubted it. For "is my build loaded", ask the DLL's mtime
                    # or check a BEHAVIOUR - never this.
                    print("bridge up on %s - %d endpoints, compiled %s %s (banner: __DATE__ of one "
                          "TU, NOT the link time)"
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


# `confirm` is stripped whatever its value, and the other forbidden keys are stripped only when they
# would AUTHORISE something. The difference is not a nicety - for one shape of parameter the blanket
# strip does the opposite of what this guard is for.
#
# THE HOLE. Three endpoints default `save` to TRUE and say so deliberately: import_texture
# (MifBridgeImport.cpp - "Save is ON by default here, unlike create_material"), set_plugin_enabled
# and write_thumbnail_texture. A suite author writing `M.call("import_texture", {..., "save": False})`
# is asking NOT to touch the disk. The blanket strip removed that key, the handler applied its
# default, and the file was written - so the guard deleted the only thing standing between the suite
# and a disk write. Found 2026-08-31 by sweeping for default-true booleans after `clear` on
# set_blendspace_samples turned out to be one. Latent rather than live: no suite passes save:False
# today, which is exactly why it would have been found by somebody's lost afternoon instead.
#
# `confirm` stays absolute. Passing confirm:false THROUGH would change behaviour that suites already
# rely on - override_inherited_component refuses outright on an explicit confirm:false, where a
# stripped one succeeds - and scratch_confirm.py is the sanctioned route for the confirm-gated
# success paths. This guard is about not authorising; it is not about arguing with a handler.
AUTHORISING_ONLY = FORBIDDEN_KEYS - {"confirm"}


def _authorises(value):
    """Would this value turn the flag ON? Strings are included because JSON is not the only caller."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def guarded_payload(payload):
    out = {}
    for k, v in (payload or {}).items():
        low = k.lower()
        if low in AUTHORISING_ONLY and not _authorises(v):
            out[k] = v          # a false is a REFUSAL to authorise, and must reach the handler
            continue
        if low in FORBIDDEN_KEYS:
            continue
        out[k] = v
    return out


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


DYNAMIC_COVERAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "dynamic_coverage.json")


def record_dynamic_coverage(suite, endpoints):
    """Record the endpoints a suite drove from the LIVE registry rather than by name.

    coverage_gaps.py reads suite SOURCE for literal endpoint strings, so an endpoint reached by
    iterating endpoint_names() is invisible to it and reads as untested. Four names were wrong on
    its list for that reason on 2026-08-31.

    A static declaration in the suite would fix that by LYING. test_node_spawns T330 does not sweep
    every add_* - it sweeps every add_* whose acceptedParams contain graphId and are otherwise a
    subset of the cosmetic set, computed live. A glob would claim endpoints the loop deliberately
    skips, which is worse than the blind spot: coverage_gaps would go quiet about genuinely
    untested surface.

    So what is recorded here is EVIDENCE - what actually ran, stamped with when - and coverage_gaps
    reports the record's age rather than trusting it. Merged per suite so a re-run replaces its
    claim instead of growing a pile nobody prunes.
    """
    import json as _json
    import time as _time
    data = {}
    try:
        with io.open(DYNAMIC_COVERAGE_PATH, encoding="utf-8") as fh:
            data = _json.load(fh)
    except Exception:
        data = {}
    data[str(suite)] = {
        "endpoints": sorted(set(str(e) for e in endpoints)),
        "recordedAt": int(_time.time()),
    }
    tmp = DYNAMIC_COVERAGE_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(_json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, DYNAMIC_COVERAGE_PATH)
    return len(data[str(suite)]["endpoints"])


# --------------------------------------------------------------------------- fixture discovery
def discover_material(require=None, min_params=1, limit=120, cooked=None):
    """A material to test against, FOUND rather than named. Returns (path, params) or (None, []).

    `require` is a parameter kind that must be present - "scalar", "vector", "staticSwitch",
    "texture" - so a caller needing a static switch does not get handed a material without one.
    `min_params` filters out the many engine materials that expose nothing at all.

    `cooked=True` additionally requires a material whose expression graph is EMPTY because it was
    cooked. That is not a nicety - test_material_params exists to prove parameters resolve where
    list_material_expressions is blind, so handing it an uncooked material would leave the suite
    green while testing nothing it was written for. An uncooked project has none, and the caller
    should SKIP rather than weaken the assertion.

    /Engine/ content sorts first ON PURPOSE. It ships with every UE install, so a suite built on it
    runs against a blank project; project content is the fallback, never the assumption. Three
    material suites used to hardcode one DDS2 master material and fail their SETUP everywhere else,
    which reports as an error rather than as "there was nothing here to test".
    """
    # ONE QUERY PER ROOT, IN PREFERENCE ORDER - not one query sorted afterwards. `limit` truncates
    # server-side, so sorting the result only reorders whatever survived: asking for 120 materials
    # and sorting /Game/ first still returned engine content on a project holding 193 /Game/
    # materials, because none of them were in the first 120 rows.
    #
    # /Engine/ is preferred normally - it ships with every install, so a suite built on it runs
    # against a blank project. Inverted for cooked=True, because engine content in an installed
    # editor is never cooked.
    roots = ["/Game/", "/Engine/"] if cooked else ["/Engine/", "/Game/"]
    rows = []
    for root in roots:
        rows += (call("find_assets", {"class": "Material", "pathPrefix": root,
                                      "limit": limit}).get("assets") or [])
    for row in rows:
        path = row.get("path") or row.get("objectPath")
        if not path:
            continue
        params = call("list_material_parameters", {"material": path}).get("parameters") or []
        if len(params) < min_params:
            continue
        if require and not [x for x in params
                            if (x.get("kind") or x.get("type")) == require]:
            continue
        if cooked is not None:
            ex = call("list_material_expressions", {"path": path})
            if (ex.get("cooked") is True) != bool(cooked):
                continue
            if cooked and (ex.get("numExpressions") or 0) != 0:
                continue
        return path, params
    return None, []


def params_of_kind(params, kind):
    """The subset of a discover_material() result with one parameter kind."""
    return [x for x in params if (x.get("kind") or x.get("type")) == kind]


def discover_skeletal_mesh(required_bones=(), limit=200):
    """A SkeletalMesh whose skeleton carries every bone in `required_bones`. (path, bones) or (None, []).

    Selecting on the BONES rather than on the class is the whole point. The IK suites assert against
    real bone names - a goal on foot_r, a chain from spine_01 to spine_05 - and handing them an
    arbitrary skeleton would leave them green while testing nothing those assertions were written
    for. A mesh that lacks the bones is not a substitute fixture, it is a different test.

    /Game/ is searched first here, unlike discover_material: the engine ships only three skeletal
    meshes and none is a full UE5 mannequin, so project content is where a match realistically is.
    """
    want = [b.lower() for b in required_bones]
    for root in ("/Game/", "/Engine/"):
        for row in (call("find_assets", {"class": "SkeletalMesh", "pathPrefix": root,
                                         "limit": limit}).get("assets") or []):
            path = row.get("path") or row.get("objectPath")
            if not path:
                continue
            got = call("list_bones", {"path": path})
            if not got.get("ok"):
                continue
            names = {str(b.get("name") or b).lower() for b in (got.get("bones") or [])}
            if all(w in names for w in want):
                return path, sorted(names)
    return None, []
