"""Is the report loop actually alive and able to do its job? Nothing asked this until today.

WHY THIS EXISTS. On 2026-09-04 Andre asked whether an issue submitted at 4am would be picked up. The
answer was no, and had been no for every report ever filed: the scheduled task ran the bare word
`python`, Task Scheduler does not search PATH, and it failed with 0x80070002 on every single run.
Reports #1, #2 and #3 were all found by hand. The symptom was a number in `LastTaskResult` that
nothing read, and the task showed `State: Ready` throughout - which looks fine.

Every part of that was checkable and nothing checked it.

THE LOG IS NOT A LIVENESS SIGNAL, and this is the trap worth naming first. Idle polls write NOTHING
on purpose - the whole design is that polling costs nothing and says nothing. So a log whose last
line is six hours old is the NORMAL state of a healthy watcher, and "the log is stale" would be a
false alarm every time. Liveness has to come from the process table.

WHAT THIS ASKS, in the order a failure actually bites:

  1. Is a watcher process running at all?
  2. Does the scheduled task exist, and what did its last run really return?
  3. Can it reach GitHub?  (an unauthenticated gh means every poll returns nothing, forever)
  4. Can it spawn an agent?  (a missing `claude` means reports are noticed and nothing happens)
  5. Is there anything open RIGHT NOW that it should have picked up and has not?

Number 5 is the one that would have caught this outright: issue #3 sat open and unseen for hours
while everything else looked ordinary.

Usage:
    python tools/report_watch_health.py            # human-readable, exit 1 if unhealthy
    python tools/report_watch_health.py --quiet    # only the verdict line
Exit: 0 healthy, 1 something is wrong, 2 could not tell (no gh, no PowerShell)
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "report_watch_state.json")
TRUST_FILE = os.path.join(HERE, "report_trust.json")
LABEL = "bridge-report"
TASK = "MifBridge report watcher"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROBLEMS = []
NOTES = []
# Whether a watcher is alive, so the task-result note can say "that was the PREVIOUS run" instead of
# implying the loop is down. Stop-ScheduledTask leaves a refusal code behind on every manual restart.
PROC_RUNNING = [False]


def problem(msg, fix):
    PROBLEMS.append((msg, fix))


def sh(cmd, timeout=60):
    """(ok, stdout). Never raises - a health check that dies is worse than one that says nothing."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL, timeout=timeout,
                           creationflags=NO_WINDOW, cwd=HERE)
        return r.returncode == 0, (r.stdout or "").strip()
    except Exception as exc:                                        # noqa: BLE001
        return False, str(exc)[:200]


def powershell(script, timeout=60):
    return sh(["powershell", "-NoProfile", "-Command", script], timeout=timeout)


# --------------------------------------------------------------------------- 1. the process
def check_process():
    ok, out = powershell(
        "@(Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
        "Where-Object { $_.CommandLine -like '*report_watch.py*' }) | "
        "ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.Name }")
    if not ok:
        NOTES.append("could not read the process table (%s) - liveness unknown" % out[:60])
        return None
    procs = [l for l in out.splitlines() if l.strip()]
    if not procs:
        problem("NO WATCHER PROCESS is running - nothing is polling GitHub",
                "Start-ScheduledTask -TaskName '%s'" % TASK)
        return []
    # MORE THAN ONE IS ALSO WRONG. report_watch.py has no self-lock, so two watchers double every
    # poll and can both escalate the same report - the task's IgnoreNew is the only guard, and this
    # is what says it stopped working.
    PROC_RUNNING[0] = True
    if len(procs) > 1:
        problem("%d watcher processes are running - they will both poll and can both escalate the "
                "same report" % len(procs),
                "stop the extras; check the task's MultipleInstances is IgnoreNew")
    return procs


# --------------------------------------------------------------------------- 2. the task
_RESULTS = {
    0: ("the last run COMPLETED - for a daemon that means it EXITED. It should still be running.",
        "check the tail of report_watch.log for why it stopped"),
    267009: (None, None),          # 0x41301 currently running - the healthy value
    267011: ("the task has never run", "Start-ScheduledTask -TaskName '%s'" % TASK),
    2147942402: ("0x80070002 FILE_NOT_FOUND - the task cannot find what it is told to Execute. "
                 "This is the bug that stopped the loop working for every report ever filed: a "
                 "BARE program name. Task Scheduler does not search PATH.",
                 "set Execute to an ABSOLUTE path to pythonw.exe"),
}


def check_task():
    ok, out = powershell(
        "$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
        "if (-not $t) { 'MISSING' } else { $i = $t | Get-ScheduledTaskInfo; "
        "$t.State.ToString() + '|' + $i.LastTaskResult + '|' + $t.Actions[0].Execute + '|' + "
        "[int]$t.Settings.MultipleInstances }" % TASK)
    if not ok or not out:
        NOTES.append("could not read the scheduled task - it may not be set up")
        return
    if out.strip() == "MISSING":
        problem("the scheduled task '%s' does not exist - nothing restarts the watcher" % TASK,
                "see docs/12_AUTONOMOUS_REPORT_LOOP.md, 'Making it survive a reboot'")
        return
    parts = out.split("|")
    if len(parts) < 4:
        NOTES.append("unexpected task output: %s" % out[:80])
        return
    state, result, execute, multi = parts[0], parts[1], parts[2], parts[3]
    if state.lower() == "disabled":
        problem("the scheduled task is DISABLED", "Enable-ScheduledTask -TaskName '%s'" % TASK)
    try:
        code = int(result)
    except ValueError:
        code = None
    if code is not None and code in _RESULTS:
        msg, fix = _RESULTS[code]
        if msg:
            problem("the task's last result was %s: %s" % (result, msg), fix)
    elif code not in (None, 267009):
        # DECODED, NOT JUST PRINTED. A bare number in this field is precisely what hid the original
        # bug: 2147942402 sat here meaning FILE_NOT_FOUND and nothing translated it. Anything in the
        # 0x8007xxxx range is a wrapped Win32 error and Windows already owns the message table, so
        # growing a lookup here one surprise at a time is the wrong shape.
        NOTES.append("task LastTaskResult is %s (%s)%s"
                     % (result, describe_result(code),
                        " - a watcher is running now, so this is the previous run"
                        if PROC_RUNNING[0] else ""))


    # THE BARE-NAME CHECK, because that is the actual bug that happened and it is invisible from a
    # terminal - the command works fine when pasted into one. It lives INSIDE check_task, which it
    # briefly did not: inserting describe_result above it at column 0 ended this function early and
    # left these two silently unreachable. They were only noticed because a deliberate failure test
    # expected two problems and got one - which is the argument for testing a check by breaking the
    # thing it watches rather than by reading it.
    if execute and not os.path.isabs(execute.strip()):
        problem("the task's Execute is '%s', a BARE NAME. Task Scheduler does not search PATH, so "
                "this fails with 0x80070002 every run." % execute.strip(),
                "use the absolute path to pythonw.exe")
    if multi.strip() not in ("2", "3"):
        problem("MultipleInstances is %s - a repeating trigger can stack a second watcher, and "
                "report_watch.py has no self-lock" % multi.strip(),
                "set it to IgnoreNew")


def describe_result(code):
    """Windows' own words for a task result code, or the hex if it cannot be decoded."""
    hexed = "0x%08X" % (code & 0xFFFFFFFF)
    if (code & 0xFFFF0000) == 0x80070000:
        win32 = code & 0xFFFF
        ok, out = powershell("[ComponentModel.Win32Exception]::new(%d).Message" % win32, timeout=30)
        if ok and out.strip():
            return "%s - %s" % (hexed, out.strip())
    return hexed


# --------------------------------------------------------------------------- 3 & 4. capability
def check_capability():
    if not shutil.which("gh"):
        problem("`gh` is not on PATH - every poll will fail and the loop sees nothing",
                "install the GitHub CLI, or fix PATH for the task's user")
    else:
        ok, out = sh(["gh", "auth", "status"], timeout=60)
        if not ok:
            problem("`gh` is NOT authenticated - every poll returns nothing, silently and forever",
                    "gh auth login")
    if not shutil.which("claude"):
        problem("`claude` is not on PATH - reports would be NOTICED and no agent could be spawned",
                "fix PATH, or the watcher is only a logger")


# --------------------------------------------------------------------------- 5. is it behind?
def check_backlog():
    """Anything open that the watcher should have taken and has not.

    THE CHECK THAT WOULD HAVE CAUGHT IT. Issue #3 sat open and unseen for hours while the process
    table, the task state and the log all looked ordinary.
    """
    if not shutil.which("gh"):
        return
    ok, out = sh(["gh", "issue", "list", "--label", LABEL, "--state", "open",
                  "--json", "number,title,author,comments", "--limit", "20"], timeout=120)
    if not ok:
        NOTES.append("could not list issues - backlog unknown")
        return
    try:
        issues = json.loads(out or "[]")
    except ValueError:
        NOTES.append("unparseable gh output - backlog unknown")
        return
    try:
        state = json.load(io.open(STATE_FILE, encoding="utf-8"))
    except Exception:                                               # noqa: BLE001
        state = {}
    seen = set(state.get("seen") or [])
    seen_comments = set(str(c) for c in (state.get("seenComments") or []))
    try:
        trusted = set(x.lower() for x in
                      (json.load(io.open(TRUST_FILE, encoding="utf-8")).get("trusted") or []))
    except Exception:                                               # noqa: BLE001
        trusted = set()

    unseen = [i for i in issues if i["number"] not in seen]
    for i in unseen:
        problem("issue #%s is OPEN and has never been seen by the watcher - it should have been "
                "picked up" % i["number"],
                "check the watcher is running; it polls every 45s")
    stale_comments = []
    for i in issues:
        if i["number"] not in seen:
            continue
        for c in (i.get("comments") or []):
            who = ((c.get("author") or {}).get("login") or "").lower()
            if str(c.get("id")) not in seen_comments and who in trusted:
                stale_comments.append((i["number"], who))
    for num, who in stale_comments:
        problem("a reply on #%s by %s has not been seen - replies are where the useful information "
                "usually is" % (num, who),
                "check the watcher is running")
    if not unseen and not stale_comments:
        NOTES.append("nothing open is waiting: %d open issue(s), all seen" % len(issues))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quiet", action="store_true", help="only the verdict line")
    a = ap.parse_args()

    procs = check_process()
    check_task()
    check_capability()
    check_backlog()

    if not a.quiet:
        print("report loop health")
        print("  watcher process : %s" % (", ".join(procs) if procs else
                                          ("none" if procs == [] else "unknown")))
        for n in NOTES:
            print("  note            : %s" % n)
        print("")
        # SAID EVERY RUN, not only when something is wrong. "The log is stale" is the first thing
        # somebody reaches for and it is always a false alarm here.
        print("  Idle polls write NOTHING to the log on purpose, so a log line hours old is the")
        print("  NORMAL state of a healthy watcher. Liveness comes from the process, not the log.")
        print("")

    if PROBLEMS:
        print("%d problem(s):" % len(PROBLEMS))
        for msg, fix in PROBLEMS:
            print("  - %s" % msg)
            print("    fix: %s" % fix)
        return 1
    print("OK  the report loop is running and has nothing waiting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
