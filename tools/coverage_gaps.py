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
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HANDLERS_H = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private", "MifBridgeHandlers.h")

# Endpoints registered by SIBLING plugins via RegisterExternalEndpoint (MifBridgeEndpointRegistry.h's
# provider pattern) never appear as MIF_DECL in MifBridgeHandlers.h - that is the whole point of the
# provider split. Without this, every one of them is permanent, unfixable "stale noise": the 12 kr_*
# endpoints MifKismetReconstructor registers were flagged STALE on every single run (found 2026-08-29),
# which trains a reader to skip the warning - exactly the "crying wolf" failure this file's own
# docstring exists to prevent. So a provider's own Reg(TEXT("name"), ...) call sites count as live too.
EXTERNAL_PROVIDERS = [
    os.path.join(os.path.dirname(HERE), "..", "MifKismetReconstructor",
                 "Source", "MifKismetReconstructor", "Private", "MifKrBridgeEndpoints.cpp"),
]


def _live_decl_names():
    """MIF_DECL(...) names straight from source - no editor needed, same extraction parity_check.py
    documents as equivalent to the live registry. Used ONLY to detect staleness, never as the list
    itself: self_audit is still the documented authority (see module docstring)."""
    try:
        text = open(HANDLERS_H, encoding="utf-8").read()
    except OSError:
        return None
    names = set(re.findall(r"^\s*MIF_DECL\(([A-Za-z0-9_]+)\)", text, re.MULTILINE))
    for provider_path in EXTERNAL_PROVIDERS:
        try:
            provider_text = open(provider_path, encoding="utf-8").read()
        except OSError:
            continue  # sibling plugin not present in this checkout - nothing to add, nothing to break
        names |= set(re.findall(r'\bReg\(\s*TEXT\("([A-Za-z0-9_]+)"\)', provider_text))
    return names



def dynamic_coverage():
    """(endpoint -> suite, newest timestamp) for endpoints driven from the live registry.

    EVIDENCE, NOT A DECLARATION. A suite that iterates endpoint_names() records what it actually
    drove; this subtracts that from the "named nowhere" list. The record's AGE is reported rather
    than trusted - a run that predates an endpoint being added proves nothing about it, which is the
    same failure endpoints_current.json shouts about.
    """
    import json
    path = os.path.join(HERE, "dynamic_coverage.json")
    if not os.path.isfile(path):
        return {}, 0
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}, 0
    owner, newest = {}, 0
    for suite, rec in (data or {}).items():
        newest = max(newest, int(rec.get("recordedAt") or 0))
        for ep in rec.get("endpoints") or []:
            owner.setdefault(ep, suite)
    return owner, newest

def main():
    snapshot = os.path.join(HERE, "endpoints_current.json")
    names = json.load(open(snapshot, encoding="utf-8"))

    live = _live_decl_names()
    if live is not None:
        stale_added = []
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
            print("!! Whether those are COVERED is answered below, after the suite scan.")
            print("!" * 72)
            print("")
            stale_added = added

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

    # ANSWER THE STALENESS WARNING INSTEAD OF ONLY RAISING IT.
    #
    # "every result below is blind to these" is true and unhelpful alone: it tells a reader the
    # coverage number is incomplete without saying whether the gap MATTERS, and refreshing needs a
    # LIVE EDITOR - which is exactly what somebody reading this at 3am does not have. The suites
    # are already scanned by this point, so the unseen endpoints can be checked against them
    # directly and the stale snapshot stops blocking the question.
    #
    # Measured 2026-09-03: all 13 endpoints the snapshot had never seen WERE named in suites, so
    # the coverage figure was conservative rather than wrong. Knowing that without an editor is
    # the entire point - the warning had been shouting for a fortnight with no way to resolve it.
    if stale_added:
        unseen = {e: [s for s, b in text.items()
                      if ('"%s"' % e) in b or ("'%s'" % e) in b] for e in stale_added}
        blind = sorted(e for e, s in unseen.items() if not s)
        print("THE %d ENDPOINTS THE SNAPSHOT HAS NOT SEEN, checked against the suites anyway:"
              % len(stale_added))
        print("  %d named in a suite, %d named nowhere%s"
              % (len(stale_added) - len(blind), len(blind),
                 (": " + ", ".join(blind[:8])) if blind else "."))
        print("  (a name match is still not coverage - but a stale snapshot no longer hides it)")
        print("")
    # SUBTRACT WHAT A SUITE ACTUALLY DROVE FROM THE LIVE REGISTRY. This scanner reads suite
    # SOURCE for literal endpoint names, so an endpoint reached by iterating endpoint_names() reads
    # as untested - four names were wrong on this list for that reason. The subtraction is shown
    # rather than applied quietly, because a claim nobody can see is a claim nobody can check.
    dyn, dyn_at = dynamic_coverage()
    uncovered = [e for e in names if e not in covered and e not in dyn]
    dyn_used = sorted(e for e in names if e not in covered and e in dyn)

    # COMPUTED BEFORE THE HEADLINE, because the headline is the number a reader acts on. Leaving
    # deliberately-denied endpoints inside "named nowhere" made that number overstate the gap by two
    # and it could never be driven to zero - the same shape as a docs heading that reads OPEN while
    # the body says fixed, one file along.
    denied = set()
    try:
        sys.path.insert(0, HERE)
        import mifaudit as _M
        denied = {e for e in uncovered if e in getattr(_M, "DENY", ())}
    except Exception as exc:                       # noqa: BLE001
        print("(could not read mifaudit.DENY, so denied endpoints count as uncovered: %s)" % exc)

    print("endpoints: %d   named in a suite: %d   named nowhere: %d  (%d of those are DENIED to "
          "suites by design)" % (len(names), len(covered), len(uncovered), len(denied)))
    print("suites: %d\n" % len(suites))

    # Grouped by prefix, because a whole missing family is a different problem from a stray endpoint.
    fam = collections.defaultdict(list)
    for e in uncovered:
        fam[e.split("_")[0]].append(e)
    if dyn_used:
        import time as _time
        age = ""
        if dyn_at:
            hours = (int(_time.time()) - dyn_at) / 3600.0
            age = " recorded %.1fh ago" % hours
            if hours > 72:
                age += " - STALE, and a record older than the endpoint proves nothing about it"
        print("")
        print("driven dynamically by a suite, so NOT counted as uncovered%s:" % age)
        for e in dyn_used:
            print("  %-34s by %s" % (e, dyn.get(e)))
        print("")

    # DENIED IS NOT UNCOVERED, and conflating them leaves a permanently unclosable row.
    #
    # mifaudit.DENY blocks an endpoint from every suite payload on purpose - save_dirty_packages and
    # save_level_as WRITE TO DISK, and the standing rule for this project is that audits save
    # nothing. They will therefore never be "named in a suite" in the ordinary sense, so listing
    # them beside genuinely forgotten endpoints trains a reader to skip the list. That is the same
    # crying-wolf failure this file's own kr_* handling was written to avoid, one category along.
    #
    # They are not UNTESTABLE, only undrivable through M.call: a suite can still raw_post one to
    # prove its refusal path, the way test_pie_family does with start_pie. So they are reported as
    # their own line rather than dropped.
    if denied:
        print("")
        print("DENIED BY mifaudit, so no suite can call them - not a coverage gap:")
        for e in sorted(denied):
            print("  %-34s writes to disk; a suite may still raw_post it to prove a refusal" % e)
        fam = {k: [e for e in v if e not in denied] for k, v in fam.items()}
        fam = {k: v for k, v in fam.items() if v}
        print("")

    print("never named in any suite, grouped by verb:")
    for k in sorted(fam, key=lambda x: -len(fam[x])):
        print("  %-14s %2d  %s" % (k, len(fam[k]), ", ".join(sorted(fam[k]))[:96]))

    with open(os.path.join(HERE, "coverage_gaps.json"), "w", encoding="utf-8") as f:
        json.dump({"uncovered": uncovered,
                   "dynamicallyCovered": dyn_used,
                   "covered": {k: v for k, v in sorted(covered.items())}}, f, indent=1)
    print("\nwritten to tools/coverage_gaps.json")
    print("A NAME MATCH IS NOT COVERAGE. Read the suite before believing an endpoint is tested,")
    print("and remember fuzz_endpoints.py sweeps everything without being a suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
