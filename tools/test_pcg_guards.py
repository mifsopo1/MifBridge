"""pcg_generate / pcg_cleanup - the confirm gate, which is all of them that can be tested here.

WHY THIS EXISTS. Both endpoints were declined for coverage with a real reason: "PCG has no
node-authoring endpoints so there is no way to build real graph content to test generation against"
(docs/audit deferred list). That is true of the SUCCESS path and was taken to mean the endpoints
were untestable. They are not - the same over-generalisation test_pie_idle was written to correct,
where "needs PIE for its full function" had been read as "cannot be tested at all".

What IS testable without a PCG graph, a PCG component, or the plugin being enabled:

  - both REFUSE without confirm:true, rather than defaulting it
  - each refusal names ITS OWN consequence, not a shared boilerplate: generate can spawn thousands
    of actors with no single undo, cleanup DESTROYS what a component generated. A caller deciding
    whether to pass confirm needs to know which of those they are about to do.
  - the gate is checked BEFORE the actor is resolved, so an unconfirmed call does no lookup work

THIS SUITE NEVER PASSES confirm:true. Not once, not with a scratch path. pcg_generate's own refusal
says it "can spawn thousands of actors into the OPEN level ... and there is no single undo for it",
which is a straightforward description of why an automated suite must not be the thing that finds
out. test_consolidate was fixed earlier the same night for sending confirm:true before checking the
write mode; this file does not send it at all.

So the success path stays UNCOVERED and this says so, rather than leaving a green result to imply
otherwise.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []

GATED = (
    ("pcg_generate", "spawn", "thousands of actors"),
    ("pcg_cleanup", "destroy", "DESTROYS"),
)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "\n        " + str(detail)[:300]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    registry = set(M.raw_post("self_audit", {}).get("endpoints") or [])
    for ep, _, _ in GATED:
        if ep not in registry:
            print("SKIPPED - %s is not registered on this build." % ep)
            return 2

    # ------------------------------------------------------------------ P400 the gate
    print("\n=== P400: both refuse without confirm, rather than defaulting it ===")
    for ep, verb, phrase in GATED:
        r = M.raw_post(ep, {})
        check("P400 %s refuses with no confirm - a destructive default is how an agent %ss "
              "something it never asked to" % (ep, verb),
              r.get("ok") is False, json.dumps(r)[:220])
        check("P400 %s says confirm:true is what is missing, so the caller can act on it" % ep,
              "confirm" in (r.get("error") or ""), (r.get("error") or "")[:200])

    # ------------------------------------------------------------------ P401 the reasons differ
    print("\n=== P401: each refusal names ITS OWN consequence, not shared boilerplate ===")
    # A caller about to pass confirm:true needs to know WHICH irreversible thing they are choosing.
    # Two endpoints sharing one message would tell them neither.
    gen = (M.raw_post("pcg_generate", {}).get("error") or "")
    cln = (M.raw_post("pcg_cleanup", {}).get("error") or "")
    check("P401 pcg_generate's refusal names what generating COSTS - actors spawned into the open "
          "level, with no single undo",
          "thousands of actors" in gen and "undo" in gen, gen[:250])
    check("P401 pcg_cleanup's refusal names that it DESTROYS, which is the opposite risk",
          "DESTROYS" in cln, cln[:250])
    check("P401 and the two messages are different - one shared boilerplate would leave a caller "
          "unable to tell which irreversible thing they were confirming",
          gen != cln, "both endpoints returned the same refusal text")

    # ------------------------------------------------------------------ P402 gate before lookup
    print("\n=== P402: the gate is checked BEFORE the actor is resolved ===")
    # Deliberate, and worth pinning: an unconfirmed destructive call should not do resolution work,
    # and should not leak whether a path exists. The caller still learns about a bad path - they
    # just learn it after confirming, which is the safe order for this shape of endpoint.
    for ep, _, _ in GATED:
        bad = M.raw_post(ep, {"actorPath": "/Game/NoSuchPcgActor_zz.NoSuchPcgActor_zz"})
        check("P402 %s with a nonexistent actor still reports the CONFIRM requirement, not a "
              "lookup failure - the gate runs first" % ep,
              bad.get("ok") is False and "confirm" in (bad.get("error") or ""),
              (bad.get("error") or "")[:220])

    # ------------------------------------------------------------------ P403 the parameter guard
    print("\n=== P403: unknown parameters are refused, not ignored ===")
    for ep, _, _ in GATED:
        r = M.raw_post(ep, {"zzz": 1})
        check("P403 %s refuses an unrecognised parameter" % ep,
              r.get("ok") is False and "zzz" in (r.get("error") or ""),
              (r.get("error") or "")[:200])

    print("")
    print("NOT PROVEN BY THIS SUITE, and it is the larger half:")
    print("  - the GENERATION path. Reaching it needs confirm:true against a real PCG component,")
    print("    and pcg_generate's own refusal explains why a suite must not be what discovers that:")
    print("    it can spawn thousands of actors into the OPEN level with no single undo.")
    print("  - the CLEANUP path, for the same reason in reverse - it destroys what was generated.")
    print("  - PCG is EnabledByDefault:false and not enabled in this project, so there is no graph")
    print("    to point either endpoint at even if confirming were acceptable.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
