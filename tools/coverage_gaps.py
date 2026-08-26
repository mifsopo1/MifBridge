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
"""
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    snapshot = os.path.join(HERE, "endpoints_current.json")
    names = json.load(open(snapshot, encoding="utf-8"))

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
