"""A wrong endpoint name used to return an empty 404. Now it teaches.

WHERE THIS CAME FROM. A UE 5.7 session building a city in Curfew burned three round trips guessing
`delete_actor`, `destroy_actor` and `remove_actor` before finding `delete_level_actor`, and separately
guessed `list_endpoints` without ever learning that `self_audit` already enumerates everything. Filed as
issue 2 in that project's own copy of the issues doc, which this repo had never seen until the merge on
2026-08-26.

WHY IT WAS EMPTY. The routes are bound one per endpoint name, so an unknown path never reaches MifBridge
at all - UE's own router answers it:

    {"errorCode": "errors.com.epicgames.httpserver.route_handler_not_found", "errorMessage": ""}

An empty message. The handler could not say anything because the handler never ran. The fix is a request
PREPROCESSOR, which runs before routing and can answer an unknown /api/ path itself.

WHAT THE SUITE ACTUALLY GUARDS. Three things, and the second is the one that took two attempts:

  T560  an unknown name gets a real error that names self_audit - so the next step is obvious.
  T561  the suggestions CONTAIN THE RIGHT ANSWER and rank it near the top. The first implementation
        returned the first eight names sharing any word, which put delete_datatable_rows and
        add_spawn_actor ahead of delete_level_actor - the very answer being looked for. A did-you-mean
        that omits the right answer is barely better than none, so this asserts rank, not presence.
  T562  REAL endpoints still route normally. A preprocessor sits in front of every request in the
        process; if it ever returned true for a valid path it would take the whole bridge down with it.
        That check matters more than the feature.
"""
import json
import sys
import urllib.request

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def raw_post(name):
    """POST straight at a path, bypassing mifaudit - the point is what an UNROUTED path answers."""
    req = urllib.request.Request(
        "http://127.0.0.1:8791/api/" + name, data=b"{}",
        headers={"X-Mif-Token": "dev", "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {}


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T560 it says something
    print("=== T560: an unknown endpoint answers with a real error, not an empty 404 ===")
    r = raw_post("delete_actor")
    check("T560 the body is not empty", bool(r), "an unrouted path returned nothing at all")
    check("T560 ok is false", r.get("ok") is False, json.dumps(r)[:200])
    err = r.get("error") or ""
    check("T560 the error names the endpoint that was asked for", "delete_actor" in err, err[:170])
    # The whole point: the caller must learn where to look next without another guess.
    check("T560 and points at self_audit", "self_audit" in err, err[:170])
    check("T560 and says how many endpoints exist", any(c.isdigit() for c in err), err[:170])

    # ------------------------------------------------------------------ T561 the suggestion is USEFUL
    print("")
    print("=== T561 [the part that took two attempts]: the right answer, ranked ===")
    for guess, want in (("delete_actor", "delete_level_actor"),
                        ("destroy_actor", "delete_level_actor"),
                        ("get_spline", "get_spline_points")):
        sug = raw_post(guess).get("didYouMean") or []
        check("T561 %s suggests %s" % (guess, want), want in sug, json.dumps(sug)[:180])
        if want in sug:
            # Ranked, not merely present. The first implementation had delete_level_actor buried
            # behind delete_datatable_rows and add_spawn_actor, which is a list nobody reads to the end.
            check("T561 and ranks it in the top 3 (was buried at first)", sug.index(want) < 3,
                  "position %d in %s" % (sug.index(want) + 1, json.dumps(sug)[:140]))

    # A guess with nothing in common should not invent nonsense.
    sug = raw_post("zzzz_not_a_thing_at_all").get("didYouMean")
    check("T561 a guess sharing nothing suggests nothing", not sug, json.dumps(sug)[:150])

    # ------------------------------------------------------------------ T562 routing is intact
    print("")
    print("=== T562 [the one that matters most]: real endpoints still route ===")
    # The preprocessor runs in front of EVERY request. If it ever swallowed a valid path, the bridge
    # would be dead for that endpoint - so this is the assertion protecting the whole feature.
    a = M.call("self_audit", {}, timeout=90)
    check("T562 self_audit still answers", a.get("ok") is True, json.dumps(a)[:160])
    check("T562 and still enumerates every endpoint", len(a.get("endpoints") or []) > 200,
          "got %d" % len(a.get("endpoints") or []))
    d = M.call("describe_endpoint", {"name": "self_audit"}, timeout=30)
    check("T562 describe_endpoint still answers", d.get("ok") is True, json.dumps(d)[:160])
    # A REAL endpoint refusing bad input must still produce its own refusal, not the 404 text.
    q = M.call("list_variables", {"blueprintId": "/Game/DoesNotExist_zz.DoesNotExist_zz"}, timeout=30)
    check("T562 a real endpoint's own refusal is unchanged",
          q.get("ok") is False and "not an endpoint on this build" not in (q.get("error") or ""),
          (q.get("error") or "")[:170])
    check("T562 the bridge is alive", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
