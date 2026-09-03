"""Replay the sanitised report queue against the live editor and record what actually happened.

This is the only part of the loop that TOUCHES the editor, so it is the only part that can do damage,
and it is written to assume the queue is already safe rather than to re-derive that. report_intake did
the containment: every path is scratch, the endpoint is registered and not on the DENY list, and no
prose reached this file. What is left here is operational care rather than security:

  * mifaudit's own guard still strips confirm/save/force/overwrite/discardUnsaved/replaceExisting. This
    module deliberately does not reach for scratch_confirm. A report that only reproduces WITH confirm
    is recorded as needing a human, because auto-running destructive verbs on a schedule is exactly the
    thing the guard exists to prevent, and "the payload was scratch" is not a reason to weaken it.
  * The bridge is checked for life after every call. A handler that opens a modal hangs the whole
    ticker, and from outside that is indistinguishable from a crash (PM-011). If the bridge stops
    answering, the run STOPS - continuing would queue more calls against an editor that cannot serve
    them, and the report that killed it would be lost among the timeouts.
  * The interlock is honoured. A sweep running in another process shares one undo stack with this one,
    and a replay landing in the middle corrupts THAT run's results as well as its own.

WHAT A RESULT MEANS. `reproduced` is deliberately NOT decided here. This module records the response
and whether the call survived; deciding whether that response constitutes the reported bug requires
reading the reporter's prose, which is a judgement, and judgements belong to the agent or the human
working the queue - not to a script that would have to parse prose to make them.

Usage:
    python tools/report_repro.py            # replay everything in report_queue.json
    python tools/report_repro.py --number 7 # replay one report
"""
import json
import os
import sys
import time

import mifaudit as M

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(HERE, "report_queue.json")
RESULT_FILE = os.path.join(HERE, "report_results.json")

CALL_TIMEOUT = 90


def load_queue():
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("queue") or []
    except Exception as exc:
        print("no queue to work (%s)" % exc)
        return []


def main():
    only = None
    if "--number" in sys.argv:
        try:
            only = int(sys.argv[sys.argv.index("--number") + 1])
        except Exception:
            print("--number needs an issue number")
            return 2

    # One editor, one undo stack. See mifaudit.warn_if_sweep_running.
    owner = M.sweep_owner()
    if owner:
        print("a sweep is running (pid %s). Replaying now would corrupt both runs - refusing." % owner)
        return 2

    queue = load_queue()
    if only is not None:
        queue = [q for q in queue if q.get("number") == only]
    if not queue:
        print("nothing to replay")
        return 0

    # DO NOT WAIT FIFTEEN MINUTES FOR AN EDITOR THAT IS NOT THERE.
    #
    # This used to go straight into wait_for_bridge(timeout=900). Nothing here launches an editor, so
    # when a report arrived with none running the replay sat for a QUARTER OF AN HOUR emitting
    # "[waiting Ns - the bridge is not usable yet: ...]" onto the watcher's console. Andre killed the
    # watcher over exactly that noise on 27 August, and the report pipeline has been down since.
    #
    # An editor that is ALREADY COMING UP is worth waiting for - the port binds before it can answer,
    # and a cold start is genuinely slow. An editor that is not running at all is not going to start
    # by itself, and waiting is just a long way to print the same answer. The two are distinguishable
    # by asking once.
    # MEASURED, not assumed: with mifaudit pointed at a dead port (47999) and a queue entry present
    # so this gets past the 'nothing to replay' exit above, this branch returns in 1.7s with code 3.
    # The old path returned in 900s having printed [waiting Ns...] the entire time. Exercised that
    # way deliberately - it tests the branch without closing the editor that happened to be running.
    ok, why = M.require_sdk_bridge()
    if not ok and not M.bridge_pid():
        print("no editor is running, and nothing here starts one - so there is nothing to replay "
              "against.")
        print("  reason: %s" % why)
        print("  The report stays QUEUED. Re-run this with an editor open, or let the watcher pick "
              "it up next time.")
        return 3          # distinct from 1: nothing was WRONG, there was just nowhere to replay
    if not M.wait_for_bridge(timeout=900):
        print("the bridge never came up - nothing was replayed")
        return 1

    results = []
    for entry in queue:
        num, ep = entry.get("number"), entry.get("endpoint")
        print("")
        print("=== #%s  %s ===" % (num, ep))
        if entry.get("shapeOnly"):
            print("  shape-only: %d path(s) were rewritten to scratch, so this tests the SHAPE of the"
                  % len(entry.get("rewrites") or []))
            print("  bug, not the reporter's specific asset. A bug that only happens on one asset will")
            print("  NOT reproduce here and needs the reporter.")

        started = time.time()
        try:
            resp = M.call(ep, entry.get("payload") or {}, timeout=CALL_TIMEOUT)
            err = None
        except Exception as exc:
            resp, err = None, str(exc)
        elapsed = round(time.time() - started, 2)

        alive = M.bridge_responsive()
        rec = {
            "number": num,
            "endpoint": ep,
            "shapeOnly": entry.get("shapeOnly"),
            "elapsedSeconds": elapsed,
            "bridgeSurvived": alive,
            "response": resp,
            "transportError": err,
            # Carried through so whoever reads this file has the claim next to the observation and does
            # not have to join two files by hand.
            "reported": entry.get("reported"),
        }
        results.append(rec)

        if err:
            print("  transport error after %ss: %s" % (elapsed, err[:200]))
        else:
            print("  ok=%s in %ss" % (resp.get("ok"), elapsed))
            if resp.get("error"):
                print("  error: %s" % str(resp.get("error"))[:220])
        print("  bridge alive afterwards: %s" % alive)

        if not alive:
            # PM-011: a modal stops the ticker and the editor still LOOKS alive. Carrying on would bury
            # the report that caused it under a pile of timeouts.
            print("")
            print("  THE BRIDGE STOPPED ANSWERING. Stopping here rather than replaying the rest.")
            print("  This report is the strongest kind of finding: an endpoint that takes the bridge")
            print("  down. It is recorded and the remaining reports are left queued.")
            break

    with open(RESULT_FILE, "w", encoding="utf-8", newline="\r\n") as f:
        json.dump({"results": results}, f, indent=2)
    print("")
    print("replayed %d of %d; wrote %s" % (len(results), len(queue), RESULT_FILE))
    print("")
    print("NOTE: whether any of these REPRODUCE the reported bug is not decided here - that needs the")
    print("reporter's prose read against the response, which is a judgement, not a parse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
