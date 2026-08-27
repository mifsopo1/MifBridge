#!/usr/bin/env python3
"""Read the crash journal, and keep the editor alive.

    python mifwatch.py                 -> what happened last? (reads the journal, changes nothing)
    python mifwatch.py --watch         -> keep the editor up, relaunch on death, name what killed it
    python mifwatch.py --watch --once  -> relaunch at most once, then report

WHY THIS EXISTS. On 2026-08-26 `add_anim_node` crash-killed the editor (PM-013). There was no
in-editor signal and no record of which call did it, so the culprit had to be reconstructed from what
had recently been attempted - which took far longer than the fix did.

The plugin now writes `Saved/MifBridge/journal.jsonl`, flushing each record BEFORE the handler runs.
This reads it. The whole diagnostic is an ABSENCE:

  * a `start` with no matching `end`  -> that endpoint was running when the process stopped
  * a `session` with no `shutdown`    -> that editor died rather than being closed

Neither can be recovered from a log that flushes lazily, which is why UE_LOG was not enough:
FOutputDeviceFile hands lines to a background ring buffer and loses exactly the tail you need.

WHAT THIS DELIBERATELY DOES NOT REIMPLEMENT. mifaudit.py already contains a hard-won launcher - the
port-owner identity check that distinguishes THIS project's editor from another's, a detached launch
with the Restore-Packages modal pre-cleared, modal-versus-loading discrimination, and a sweep lock.
Every one of those was learned from a failure. This calls into them; it does not re-derive them.
"""

import argparse
import io
import json
import os
import sys
import time

import mifaudit as M

# The plugin writes to <ProjectSaved>/MifBridge/ (same convention as the thumbnail writer). Resolved
# from mifaudit's location rather than from __file__, so this still works when run from elsewhere.
JOURNAL = os.path.normpath(os.path.join(os.path.dirname(M.__file__), "..", "..", "..",
                                        "Saved", "MifBridge", "journal.jsonl"))


def read_records(path=None):
    """Parse the journal. Tolerates a truncated final line - the process may have died mid-write."""
    p = path or JOURNAL
    if not os.path.isfile(p):
        return None
    out = []
    with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                # A half-written record IS evidence: the process stopped mid-flush. Keep it as a
                # marker rather than discarding it silently.
                out.append({"ev": "truncated", "raw": line[:120]})
    return out


def analyse(records):
    """Walk the journal and pair up what opened with what closed.

    Deliberately linear rather than clever: the journal is append-only across runs and several editors
    can share a project, so PIDs interleave. Sessions are tracked by pid.
    """
    sessions = []      # {pid, port, engine, started, ended, unfinished:[ep], calls:int}
    cur = None
    open_call = None

    for r in records:
        ev = r.get("ev")
        if ev == "session":
            if cur is not None:
                # A new session header with the previous one still open means the previous editor
                # never wrote a shutdown - it died.
                cur["unfinished"].append(open_call) if open_call else None
                sessions.append(cur)
            cur = {"pid": r.get("pid"), "port": r.get("port"), "engine": r.get("engine"),
                   "started": r.get("t"), "ended": None, "unfinished": [], "calls": 0,
                   "slowest": ("", 0.0)}
            open_call = None
        elif ev == "start":
            if cur is None:
                continue
            if open_call:
                # Two starts with no end between them: the first never returned. This is the
                # in-session form of the same absence.
                cur["unfinished"].append(open_call)
            open_call = r.get("ep")
            cur["calls"] += 1
        elif ev == "end":
            if cur is None:
                continue
            ms = float(r.get("ms") or 0.0)
            if ms > cur["slowest"][1]:
                cur["slowest"] = (r.get("ep") or "?", ms)
            open_call = None
        elif ev == "shutdown":
            if cur is not None:
                cur["ended"] = r.get("t")
                if open_call:
                    cur["unfinished"].append(open_call)
                sessions.append(cur)
                cur, open_call = None, None
        elif ev == "truncated":
            if cur is not None:
                cur["unfinished"].append((open_call or "?") + " (record truncated mid-write)")

    if cur is not None:
        if open_call:
            cur["unfinished"].append(open_call)
        sessions.append(cur)
    return sessions


def report(records):
    if records is None:
        print("no journal at %s" % JOURNAL)
        print("")
        print("The plugin writes it on the first StartServer. If the editor has run since the journal")
        print("shipped and this is still missing, check the mif.BridgeJournal CVar - it defaults to on.")
        return 1

    sessions = analyse(records)
    if not sessions:
        print("journal exists but records no sessions (%d lines)" % len(records))
        return 0

    print("journal: %s" % JOURNAL)
    print("%d record(s), %d session(s)" % (len(records), len(sessions)))
    print("")

    bad = 0
    dirty_deaths = 0
    for s in sessions[-6:]:
        clean = s["ended"] is not None and not s["unfinished"]
        if s["ended"] is None:
            # A session with no shutdown record died. Counted SEPARATELY from unfinished calls,
            # because an editor can die between calls - every call completed, and the process still
            # vanished. The first version of this summary counted only unfinished calls and therefore
            # printed "every session shut down cleanly" directly beneath a session marked DIED.
            dirty_deaths += 1
        mark = "  ok  " if clean else " DIED "
        print("%s pid %-7s port %-6s engine %-5s  %d call(s)"
              % (mark, s["pid"], s["port"], s["engine"], s["calls"]))
        print("        started  %s" % s["started"])
        print("        shutdown %s" % (s["ended"] or "NONE - the process did not shut down cleanly"))
        if s["slowest"][1] > 0:
            print("        slowest  %s (%.0f ms)" % s["slowest"])
        for ep in s["unfinished"]:
            bad += 1
            # THE payoff. This line is the whole reason the journal flushes before dispatch.
            print("        >>> LAST CALL, NEVER RETURNED: %s" % ep)
        print("")

    if bad or dirty_deaths:
        print("=" * 70)
        if bad:
            print("%d call(s) started and never finished." % bad)
            print("On a hard death that names the endpoint the editor was inside when it stopped -")
            print("which is exactly what PM-013 had to reconstruct by hand.")
        if dirty_deaths:
            print("%d session(s) ended with no shutdown record: the editor DIED rather than being"
                  % dirty_deaths)
            print("closed. If no call is named above, it died between calls rather than inside one -")
            print("which is itself information: the cause was not a handler.")
        print("=" * 70)
        return 2
    print("every session shut down cleanly and every call that started also finished.")
    return 0


def watch(once=False):
    """Keep the editor up. Reports what died, using the journal rather than guessing."""
    owner = M.sweep_owner()
    if owner:
        # Another sweep holds the lock. Relaunching underneath it is how two processes end up
        # fighting over one editor.
        print("a sweep holds %s (owner %s) - not touching the editor" % (M.SWEEP_LOCK, owner))
        return 1

    relaunches = 0
    print("watching. ctrl-c to stop.")
    while True:
        if not M.bridge_responsive(timeout=10):
            print("")
            print("bridge not answering at %s" % time.strftime("%H:%M:%S"))
            recs = read_records()
            if recs:
                for s in analyse(recs)[-1:]:
                    for ep in s["unfinished"]:
                        print("  the journal says the last call was: %s" % ep)
            if once and relaunches >= 1:
                print("  --once given and already relaunched - stopping here.")
                return 2
            print("  relaunching...")
            # ensure_editor carries the port-owner identity check and the detached launch. Do not
            # replace it with a bare Popen: that is the bug that hung a whole regression for 17
            # minutes when the editor inherited a pipe.
            M.ensure_editor(max_relaunch=1)
            relaunches += 1
            print("  back up: %s" % M.bridge_responsive(timeout=300))
        time.sleep(15)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--watch", action="store_true", help="keep the editor alive, relaunch on death")
    ap.add_argument("--once", action="store_true", help="with --watch: relaunch at most once")
    ap.add_argument("--journal", help="read a specific journal file instead of the default")
    args = ap.parse_args()

    if args.watch:
        try:
            return watch(once=args.once)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0
    return report(read_records(args.journal))


if __name__ == "__main__":
    sys.exit(main())
