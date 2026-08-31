"""Does every addon op's reject_unknown guard actually RUN?

THE DIFFERENCE BETWEEN THIS AND audit_blender_dead_params.py, which was written the same evening and
must not be confused with it. That one reads the source and asks whether an ACCEPTED key is ever
read. This one drives the live addon and asks whether the guard fires at all. A guard that is
declared and unreachable is worse than no guard: the source shows a reject_unknown call, a reader
believes unknown keys are refused, and the op quietly ignores whatever it was sent.

This repo's own history is the argument. Its first rule is that a checker proves nothing until it has
been run against a known instance, and the detector harness exists because four separate tools were
found to be looking at nothing. The addon's guards had never been asked to demonstrate themselves.

METHOD. Every op the addon exposes is called with ONE key it cannot possibly know - and NOTHING else,
no valid parameters at all. reject_unknown is the first statement in an op body by convention, so a
refusal naming the probe key proves the guard ran before anything happened. An op that instead
complains about a MISSING required parameter has failed this test in a specific and interesting way:
the guard is there, and something runs before it.

WHY MISSING-REQUIRED IS A FAILURE AND NOT A PASS. It means the op validated the caller's REAL
arguments before noticing an argument it does not understand. That ordering is what lets a typo'd
parameter survive a call that otherwise looks correct - the caller fixes the missing-required
complaint, resends, and the typo is still there, now silently ignored.

SAFE BY CONSTRUCTION. Every call sends only the probe key, so an op that refuses correctly does
nothing. run_blender_suites.py gives this suite its own throwaway --background --factory-startup
Blender, so even an op that acts before refusing acts on a scene nobody owns.

Usage:
    python tools/test_blender_reject_unknown.py       # needs a Blender with MifBlender listening

Exit codes:  0 every guard fired   1 a guard did not   2 SKIPPED, no Blender
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blender_audit_common as B

PROBE = "mifProbeZz"
PASS, FAIL = [], []

# Ops this probe must not drive even in a throwaway Blender, with the reason. Empty is the honest
# state today - every op refuses before doing anything, which is what the suite proves - and the
# hook is here so a future exclusion has to carry its justification rather than quietly vanishing.
EXCLUDED = {}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)[:220]))


def addon_ops():
    import parity_check as PC
    problems = []
    ops = PC.load_addon_ops(problems) or {}
    return sorted(ops)


def main():
    if not B.reachable():
        return B.skip_banner("reject_unknown")

    ops = addon_ops()
    if len(ops) < 30:
        print("SELF-CHECK FAILED: parity_check resolved only %d addon ops - not scanning." % len(ops))
        return 2
    print("probing %d addon ops with a single unknown key %r" % (len(ops), PROBE))
    print("")

    accepted, wrong_order = [], []
    for op in ops:
        if op in EXCLUDED:
            continue
        try:
            r = B.call(op, {PROBE: 1}, timeout=30.0)
        except Exception as exc:
            check("%s answered at all" % op, False, "%s: %s" % (type(exc).__name__, exc))
            continue
        err = str(r.get("error") or "")
        if r.get("ok") is not False:
            accepted.append(op)
            check("%s REFUSES an unknown key" % op, False,
                  "ok=%r - the guard did not fire, so this op ignores what it does not understand: %s"
                  % (r.get("ok"), json.dumps(r)[:160]))
            continue
        # A refusal is not enough. It has to be THIS refusal - an op that happens to fail for a
        # missing required parameter would otherwise look identical to one whose guard ran.
        if PROBE.lower() in err.lower():
            check("%s refuses an unknown key BEFORE anything else" % op, True)
        else:
            wrong_order.append((op, err[:120]))
            check("%s refuses, but for the wrong reason" % op, False,
                  "the guard did not name %r, so something ran before it: %s" % (PROBE, err[:160]))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    if accepted:
        print("")
        print("ACCEPTED AN UNKNOWN KEY - these ops ignore what they do not understand:")
        for op in accepted:
            print("    %s" % op)
    if wrong_order:
        print("")
        print("REFUSED FOR THE WRONG REASON - the guard exists and something runs before it:")
        for op, err in wrong_order:
            print("    %-26s %s" % (op, err))
    for f in FAIL:
        if isinstance(f, tuple):
            print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
