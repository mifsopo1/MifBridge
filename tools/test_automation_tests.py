"""list_automation_tests - the last endpoint in this repo that no suite named.

WHY IT WAS THE LAST ONE. It was built 2026-08-31 alongside six others and verified once through
verify_pending_fixes, which drove it and moved on. coverage_gaps has listed it as "named nowhere"
ever since - the single genuine entry in that list, the other fourteen being twelve foreign kr_*
endpoints and two the harness denies outright.

READ-ONLY BY CONSTRUCTION, which is why it is safe to run against a session somebody is working in:
the endpoint enumerates the automation registry and refuses `run` by name, saying "this endpoint only
LISTS - it never runs a test". Nothing here creates, modifies or deletes anything.

WHAT IS WORTH ASSERTING, and it is not the count. Three things:

  * the flag DECODING is real. Flags are a bitfield and flagNames comes from the engine's own
    EAutomationTestFlags::GetTestFlagsMap(), so a name list that never varies would mean the decode
    is hardcoded. Two tests with different flags proves it reads the bits.
  * matched and count are DIFFERENT numbers, and the difference is what `truncated` reports. A suite
    that only ever asks for everything cannot tell them apart - so this asks for one row and checks
    that matched still describes the whole match set.
  * the filter narrows and stays honest: every returned row must actually contain the substring.

Usage:
    python tools/test_automation_tests.py

Exit codes:  0 passed   1 failed
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)[:240]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T3200 the shape
    print("=== T3200: the registry is enumerable, and the counts describe it ===")
    r = M.call("list_automation_tests", {"limit": 5}, timeout=120)
    check("T3200 it answers", r.get("ok") is not False, json.dumps(r)[:220])
    rows = r.get("tests") or []
    check("T3200 and returns rows", len(rows) > 0,
          "registered=%r matched=%r" % (r.get("registered"), r.get("matched")))
    check("T3200 count equals the length of tests[] - the two describe one call",
          r.get("count") == len(rows), "count=%r len=%d" % (r.get("count"), len(rows)))
    check("T3200 and registered is the whole registry, never smaller than what matched",
          (r.get("registered") or 0) >= (r.get("matched") or 0),
          "registered=%r matched=%r" % (r.get("registered"), r.get("matched")))
    check("T3200 every row carries the path an agent would act on",
          all(x.get("fullTestPath") for x in rows), json.dumps(rows[:1])[:220])

    # ------------------------------------------------------------------ T3201 truncation
    print("\n=== T3201: matched and count are DIFFERENT numbers, and truncated says which ===")
    # A suite that only ever asks for everything cannot tell these apart. Asking for ONE row is what
    # separates "how many matched" from "how many you were given".
    one = M.call("list_automation_tests", {"limit": 1}, timeout=120)
    check("T3201 a limit of 1 returns exactly one row", len(one.get("tests") or []) == 1,
          json.dumps(one)[:200])
    check("T3201 but matched still describes the WHOLE match set",
          (one.get("matched") or 0) > 1,
          "matched=%r with limit 1 - if this equals 1, matched is counting the page" % one.get("matched"))
    check("T3201 and truncated says so", one.get("truncated") is True, json.dumps(one)[:200])
    # The other direction, so truncated is proven to VARY rather than being always true.
    everything = M.call("list_automation_tests", {"limit": 5000}, timeout=180)
    check("T3201 asking for everything reports truncated:false",
          everything.get("truncated") is False,
          "truncated=%r matched=%r count=%r" % (everything.get("truncated"),
                                                everything.get("matched"),
                                                everything.get("count")))

    # ------------------------------------------------------------------ T3202 offset
    print("\n=== T3202: offset pages through, rather than re-serving the same row ===")
    page2 = M.call("list_automation_tests", {"limit": 1, "offset": 1}, timeout=120)
    p1 = (one.get("tests") or [{}])[0].get("fullTestPath")
    p2 = (page2.get("tests") or [{}])[0].get("fullTestPath")
    check("T3202 offset 1 gives a DIFFERENT test from offset 0", bool(p1) and bool(p2) and p1 != p2,
          "offset0=%r offset1=%r" % (p1, p2))
    check("T3202 and matched is unchanged by paging",
          page2.get("matched") == one.get("matched"),
          "%r vs %r" % (page2.get("matched"), one.get("matched")))

    # ------------------------------------------------------------------ T3203 the flag decode
    print("\n=== T3203: flagNames is DECODED from the bitfield, not a constant ===")
    # The decode reads EAutomationTestFlags::GetTestFlagsMap() - the engine's own table. If every
    # test came back with identical flagNames the decode could be hardcoded and nothing would say so,
    # which is why this looks for VARIATION rather than for presence.
    allrows = everything.get("tests") or []
    named = [x for x in allrows if x.get("flagNames")]
    check("T3203 rows carry decoded flag names", len(named) > 0, json.dumps(allrows[:1])[:220])
    combos = {tuple(sorted(x.get("flagNames") or [])) for x in allrows}
    check("T3203 and different tests decode to DIFFERENT names - so the bits are really read",
          len(combos) > 1, "every test decoded to the same %d combination(s)" % len(combos))
    # And the numeric field has to agree with the names: a row with names must have nonzero flags.
    bad = [x for x in named if not x.get("flags")]
    check("T3203 a row with flag NAMES has a nonzero flags bitfield", not bad,
          json.dumps(bad[:1])[:200])

    # ------------------------------------------------------------------ T3204 the filter
    print("\n=== T3204: the filter narrows, and every row it returns really matches ===")
    sample = (allrows[0].get("fullTestPath") or "")
    token = sample.split(".")[0][:12] if sample else ""
    check("T3204 (setup) a token to filter on", bool(token), sample)
    if token:
        f = M.call("list_automation_tests", {"filter": token, "limit": 5000}, timeout=180)
        check("T3204 the filtered set is no larger than the whole registry",
              (f.get("matched") or 0) <= (everything.get("matched") or 0),
              "filtered=%r all=%r" % (f.get("matched"), everything.get("matched")))
        # THE ASSERTION WITH TEETH. A filter that quietly ignored its argument would return
        # everything and still satisfy a count comparison.
        off = [x.get("fullTestPath") for x in (f.get("tests") or [])
               if token.lower() not in (x.get("fullTestPath") or "").lower()]
        check("T3204 and EVERY row returned actually contains the filter, case-insensitively",
              not off, off[:3])

    # ------------------------------------------------------------------ T3205 the refusals
    print("\n=== T3205: it refuses to run a test, by name ===")
    q = M.call("list_automation_tests", {"run": "SomeTest"}, timeout=60)
    check("T3205 `run` is refused", q.get("ok") is False, json.dumps(q)[:200])
    check("T3205 and says it never runs a test",
          "never runs a test" in (q.get("error") or ""), (q.get("error") or "")[:220])
    q = M.call("list_automation_tests", {"name": "SomeTest"}, timeout=60)
    check("T3205 `name` is refused and pointed at filter",
          q.get("ok") is False and "filter" in (q.get("error") or ""), (q.get("error") or "")[:220])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        if isinstance(f, tuple):
            print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
