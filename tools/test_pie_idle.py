"""The PIE family's IDLE contract - what these 14 endpoints do when nothing is playing.

WHY THIS EXISTS. test_uncovered_reads5 declined the whole family with "all forbidden by the standing
rule against starting PIE". Two things changed. The rule was lifted on 2026-08-28. And more
importantly the decline over-generalised: it went from "needs PIE for its full function" to "cannot
be tested at all", which is not the same claim. Every one of these has real, specified behaviour
with no PIE world, and that behaviour is worth more than it looks:

  - pie_status is a pure READ that needs no session ever. It was simply uncovered.
  - the six that require a PIE world REFUSE, and each refusal names the remedy - "start_pie, then
    poll pie_status until state=='running'". That is the difference between an agent that recovers
    and one that guesses.
  - ui_scenario_stop is IDEMPOTENT when idle (wasActive:false, "nothing to stop") rather than an
    error, which is the behaviour a cleanup path in someone else's suite depends on.

THIS SUITE NEVER STARTS PIE. Not because it may not - it may - but because the idle contract is
exactly what is untested, and a suite that starts PIE to test the not-playing state would be testing
the wrong thing. The RUNNING paths are filed separately.

THE REFUSALS ARE CHECKED FOR NAMING SOMETHING REAL, not merely for failing. A refusal saying "call
start_pie" is only useful if start_pie exists, and this project has already shipped advice naming an
endpoint that did not (list_endpoints, fixed 2026-08-31). So the remedy named is verified against
the live registry rather than trusted.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []

# Everything that genuinely needs a running PIE world. Each must REFUSE when idle.
NEEDS_PIE = [
    ("list_pie_actors", {}),
    ("pie_load_level_instance", {}),
    ("pie_unload_level_instance", {}),
    ("spawn_actor_in_pie", {}),
]


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "\n        " + str(detail)[:300]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    registry = set(M.raw_post("self_audit", {}).get("endpoints") or [])

    # ------------------------------------------------------------------ P200 the read
    print("\n=== P200: pie_status answers with no session, and its flags agree ===")
    st = M.call("pie_status", {})
    check("P200 pie_status answers without a PIE session - it is a READ, and needed no session to "
          "be testable at any point", st.get("ok") is True, json.dumps(st)[:250])

    # A status endpoint whose booleans can disagree with its own summary is worse than none.
    idle_flags = ("running", "sessionActive", "simulating", "startPending", "stopPending",
                  "worldHasBegunPlay")
    disagree = [k for k in idle_flags if st.get(k) is not False]
    check("P200 every liveness flag is false while stopped - a status whose booleans disagree with "
          "its own summary is worse than no status",
          not disagree, "not false: %s" % {k: st.get(k) for k in disagree})
    check("P200 and `state` agrees with them", st.get("state") == "stopped", st.get("state"))
    check("P200 it names the editor world, so a caller can tell WHICH editor answered",
          bool(st.get("editorWorld")), json.dumps(st)[:200])

    # ------------------------------------------------------------------ P201 the refusals
    print("\n=== P201: what needs a PIE world refuses, and says how to get one ===")
    for ep, payload in NEEDS_PIE:
        r = M.raw_post(ep, payload)
        err = r.get("error") or ""
        check("P201 %s refuses when nothing is playing rather than returning an empty result - an "
              "empty list would read as 'there are none'" % ep,
              r.get("ok") is False, json.dumps(r)[:200])
        check("P201 %s names the remedy, so the caller can recover instead of guessing" % ep,
              "start_pie" in err and "pie_status" in err, err[:220])
        # The advice must name something that EXISTS. This project shipped a refusal pointing at
        # `list_endpoints`, which does not exist, so the remedy is checked against the registry.
        named = [w for w in ("start_pie", "pie_status") if w in err]
        check("P201 %s - and every endpoint its advice names is really registered" % ep,
              all(n in registry for n in named), "named %s; missing %s"
              % (named, [n for n in named if n not in registry]))

    # ------------------------------------------------------------------ P202 idle is not an error
    print("\n=== P202: the idle-safe ones answer rather than failing ===")
    sc = M.call("ui_scenario_status", {})
    check("P202 ui_scenario_status answers when idle", sc.get("ok") is True, json.dumps(sc)[:200])
    check("P202 and reports IDLE with no scenario, not an empty success",
          sc.get("active") is False and sc.get("state") == "IDLE", json.dumps(sc)[:200])

    # Idempotent stop. A cleanup path that cannot safely run twice is a trap for every `finally`.
    stop1 = M.call("ui_scenario_stop", {})
    stop2 = M.call("ui_scenario_stop", {})
    check("P202 ui_scenario_stop succeeds with nothing active - a cleanup that errors when there is "
          "nothing to clean up is a trap for every finally block that calls it",
          stop1.get("ok") is True and stop1.get("wasActive") is False, json.dumps(stop1)[:200])
    check("P202 and it is IDEMPOTENT - calling it twice is still success, not a second error",
          stop2.get("ok") is True and stop2.get("wasActive") is False, json.dumps(stop2)[:200])

    lw = M.call("list_live_widgets", {})
    check("P202 list_live_widgets answers from the EDITOR world when no PIE world exists, and says "
          "which world it read", lw.get("ok") is True and lw.get("worldSource") == "editor",
          json.dumps(lw)[:220])
    check("P202 and reports a count rather than omitting the field when it is zero",
          isinstance(lw.get("count"), (int, float)), json.dumps(lw)[:200])

    # ------------------------------------------------------------------ P203 the guards
    print("\n=== P203: the parameter refusals ===")
    dlw = M.raw_post("describe_live_widget", {})
    check("P203 describe_live_widget with no path is refused and points at the endpoint that "
          "produces one, rather than just naming the missing key",
          dlw.get("ok") is False and "list_live_widgets" in (dlw.get("error") or ""),
          (dlw.get("error") or "")[:220])
    cap = M.raw_post("ui_scenario_capture", {})
    check("P203 ui_scenario_capture refuses with no scenario READY and says what to poll",
          cap.get("ok") is False and "ui_scenario_status" in (cap.get("error") or ""),
          (cap.get("error") or "")[:220])
    bad = M.raw_post("pie_status", {"zzz": 1})
    check("P203 pie_status takes no parameters and says so rather than ignoring one",
          bad.get("ok") is False, json.dumps(bad)[:200])

    # ------------------------------------------------------------------ P204 still idle
    print("\n=== P204: nothing here started PIE ===")
    # THE POINT OF THE WHOLE SUITE. If any call above had started a session, every assertion in it
    # would have been testing a different state than the one it claims to test.
    after = M.call("pie_status", {})
    check("P204 PIE is still stopped after every call above - this suite asserts the IDLE contract, "
          "so a session started along the way would invalidate all of it",
          after.get("state") == "stopped" and after.get("running") is False,
          json.dumps(after)[:220])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
