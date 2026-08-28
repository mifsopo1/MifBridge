"""Which endpoints does no test suite ever call?

The 31 suites in this folder were each written alongside a feature, so coverage follows the order
things were built rather than the shape of the surface. That leaves holes nobody chose: an endpoint
added in a batch of twelve, where two got suites and ten did not.

This turns "hunt for silent failures" from a guess about which families feel under-tested into a list.
It answers one question only - is this endpoint's name mentioned anywhere in a suite - and that is a
WEAK signal in both directions:

  * A mention is not coverage. `test_foo.py` may name an endpoint once in a setup line and never
    assert anything about it.
  * An absence is not necessarily a gap. Some endpoints are exercised by fuzz_endpoints.py, which
    sweeps everything and is not a suite.

So the output is a shortlist to read, never a verdict - the same rule as capability_gaps.py, and for
the same reason: this project has repeatedly been wrong when it judged coverage by name.

Purely local. Reads the endpoint snapshot and the suites; talks to nothing.

THE SNAPSHOT WENT STALE ONCE ALREADY, SILENTLY, FOR TWO DAYS (found 2026-08-28). endpoints_current.json
is documented (README.md, FEATURE_PARITY_SPEC.md) as "regenerated from the live editor's self_audit",
but nothing regenerates it automatically and nothing here checked whether it still matched reality. It
was last written 2026-08-26 with 286 names; by the time this was noticed the real surface had grown to
334 - 60 endpoints added, 12 renamed or removed - across two days of real feature work, INCLUDING
endpoints this exact file's own coverage claims were being read to judge. Every "uncovered" list this
tool produced in that window was computed over the wrong universe.

THE FIX IS A CHECK, NOT A GUESS. Rather than trust the snapshot or silently regenerate it (which would
hide the same failure mode behind one layer of indirection - a snapshot that auto-refreshes wrong is no
better than one that never refreshes), this now diffs the snapshot against a STATIC extraction of
`MIF_DECL(...)` from MifBridgeHandlers.h - the same source-of-truth extraction parity_check.py already
uses and cross-validates against MIF_BIND, so it needs no editor and stays true to "talks to nothing".
Any disagreement is a loud, unmissable warning, not a silent wrong answer. Regenerate for real with:
`python tools/refresh_endpoints_snapshot.py` (reads a live self_audit, the DOCUMENTED source - MIF_DECL
alone cannot tell a declared-but-unbound handler from a truly live one, which is why the snapshot's
contract names self_audit specifically rather than this file's own static extraction).
"""
import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HANDLERS_H = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private", "MifBridgeHandlers.h")


def _live_decl_names():
    """MIF_DECL(...) names straight from source - no editor needed, same extraction parity_check.py
    documents as equivalent to the live registry. Used ONLY to detect staleness, never as the list
    itself: self_audit is still the documented authority (see module docstring)."""
    try:
        text = open(HANDLERS_H, encoding="utf-8").read()
    except OSError:
        return None
    return set(re.findall(r"^\s*MIF_DECL\(([A-Za-z0-9_]+)\)", text, re.MULTILINE))


def main():
    snapshot = os.path.join(HERE, "endpoints_current.json")
    names = json.load(open(snapshot, encoding="utf-8"))

    live = _live_decl_names()
    if live is not None:
        snap_set = set(names)
        added, removed = sorted(live - snap_set), sorted(snap_set - live)
        if added or removed:
            print("!" * 72)
            print("!! endpoints_current.json is STALE - it disagrees with MifBridgeHandlers.h right now.")
            if added:
                print("!! %d endpoint(s) exist in source but are MISSING from the snapshot (every result"
                      % len(added))
                print("!!  below is blind to these): %s" % ", ".join(added[:12]))
                if len(added) > 12:
                    print("!!  ...and %d more" % (len(added) - 12))
            if removed:
                print("!! %d name(s) in the snapshot no longer exist in source (stale noise below): %s"
                      % (len(removed), ", ".join(removed[:12])))
            print("!! Regenerate with: python tools/refresh_endpoints_snapshot.py")
            print("!" * 72)
            print("")

    suites = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    text = {}
    for p in suites:
        text[os.path.basename(p)] = open(p, encoding="utf-8", errors="replace").read()

    mentions = collections.defaultdict(list)
    for ep in names:
        for suite, body in text.items():
            # Quoted, so add_ik_goal does not count as covering add_ik_goal_something.
            if ('"%s"' % ep) in body or ("'%s'" % ep) in body:
                mentions[ep].append(suite)

    covered = {e: s for e, s in mentions.items() if s}
    uncovered = [e for e in names if e not in covered]

    print("endpoints: %d   named in a suite: %d   named nowhere: %d"
          % (len(names), len(covered), len(uncovered)))
    print("suites: %d\n" % len(suites))

    # Grouped by prefix, because a whole missing family is a different problem from a stray endpoint.
    fam = collections.defaultdict(list)
    for e in uncovered:
        fam[e.split("_")[0]].append(e)
    print("never named in any suite, grouped by verb:")
    for k in sorted(fam, key=lambda x: -len(fam[x])):
        print("  %-14s %2d  %s" % (k, len(fam[k]), ", ".join(sorted(fam[k]))[:96]))

    with open(os.path.join(HERE, "coverage_gaps.json"), "w", encoding="utf-8") as f:
        json.dump({"uncovered": uncovered,
                   "covered": {k: v for k, v in sorted(covered.items())}}, f, indent=1)
    print("\nwritten to tools/coverage_gaps.json")
    print("A NAME MATCH IS NOT COVERAGE. Read the suite before believing an endpoint is tested,")
    print("and remember fuzz_endpoints.py sweeps everything without being a suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
