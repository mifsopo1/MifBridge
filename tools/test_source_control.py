"""source_control / source_control_checkout - and why they are two endpoints, not one.

THE SPLIT IS THE DESIGN DECISION WORTH RECORDING. The survey proposed a single
source_control{path, action} endpoint. The safety gate classifies whole ENDPOINTS, not actions, so
one endpoint would have to be either entirely safe - letting `revert` discard a day's local changes
in read mode - or entirely gated, which would make a harmless status query unavailable in scratch
mode. Split, the read is always allowed and the write half sits on the gate with every other
persist-to-disk verb. T4702 asserts exactly that asymmetry.

NOT BEING UNDER REVISION CONTROL IS A NORMAL ANSWER. That is this project's situation and the
common solo-developer case, so enabled:false comes back with ok:true and a plain statement that no
checkout is needed. An endpoint that failed here would be wrong about the most common configuration
there is.

WHAT IS NOT EXERCISED, and it is most of the interesting behaviour: every provider path. No
revision control is configured on this project, so QueryFileState is never reached, no state block
is ever built, and checkout/add/revert are never attempted. Those need a Perforce-, SVN- or
Plastic-backed project. What IS verified is the shape of the no-provider answer, the gate split,
and the argument validation - which is what a solo project can honestly check.

THE BLOCKING-CALL GUARD is likewise unexercised and worth naming. QueryFileState is not a local
read: SourceControlHelpers.cpp:1513-1515 builds an FUpdateStatus with SetUpdateModifiedState(true)
and runs Provider->Execute SYNCHRONOUSLY - the engine's own comment says Perforce "requires this
since can be a more expensive test". MifBridge dispatches on the game thread, so querying a
configured-but-unreachable server would freeze the editor for the full timeout. The read half
therefore checks IsAvailable() before querying and says so in its response rather than trying.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T4700 the read half
    print("=== T4700: reporting revision control, including when there is none ===")
    r = M.call("source_control", {})
    check("T4700 source_control succeeds with no arguments", r.get("ok") is True,
          json.dumps(r)[:250])
    for f in ("enabled", "available", "provider"):
        check("T4700 it reports %s" % f, f in r, sorted(r))
    check("T4700 available is never true while enabled is false",
          not (r.get("available") and not r.get("enabled")),
          json.dumps({"enabled": r.get("enabled"), "available": r.get("available")}))

    if not r.get("enabled"):
        # THE COMMON CASE, and it must not be an error. A solo project has no provider, and an
        # endpoint that failed here would be wrong about the most common configuration there is.
        check("T4700 no provider is reported as ok:true, not as a failure",
              r.get("ok") is True and r.get("provider") == "None", json.dumps(r)[:200])
        check("T4700 and the note says plainly that no checkout is needed",
              "nothing needs checking out" in (r.get("note") or ""), (r.get("note") or "")[:200])
    else:
        print("        a provider IS configured (%s) - the provider paths below are live"
              % r.get("provider"))

    # ------------------------------------------------------------------ T4701 with a path
    print("\n=== T4701: a path, and the one useful fact left when there is no provider ===")
    some = (M.call("find_assets", {"pathPrefix": "/Game", "limit": 1}).get("assets") or [{}])
    target = some[0].get("packageName") or some[0].get("path")
    check("T4701 (setup) an asset path to ask about", bool(target), json.dumps(some)[:200])
    if target:
        p = M.call("source_control", {"path": target})
        check("T4701 a path is accepted", p.get("ok") is True, json.dumps(p)[:250])
        check("T4701 and echoed back", p.get("path") == target, p.get("path"))
        if not p.get("enabled"):
            check("T4701 with no provider it reports no state block - there is nothing to report",
                  p.get("state") is None, json.dumps(p)[:200])
            check("T4701 and says readOnlyOnDisk is the only thing that would still stop a save",
                  "readOnlyOnDisk" in (p.get("note") or ""), (p.get("note") or "")[:200])
        else:
            st = p.get("state") or {}
            check("T4701 a state block is returned", isinstance(st, dict) and bool(st),
                  json.dumps(st)[:250])
            # The survey had this field name wrong - it is CheckedOutOther, not checkedOutBy.
            check("T4701 and uses the engine's real field name for the other user",
                  "checkedOutOther" in st, sorted(st))

    # ------------------------------------------------------------------ T4702 the split
    print("\n=== T4702: the read is always allowed; the write sits on the gate ===")
    w = M.raw_post("source_control_checkout", {"path": target or "/Game/X", "action": "checkout"})
    mode = M.write_mode()
    if mode != "full":
        # THE assertion the whole two-endpoint design exists for: in a non-full mode the WRITE half
        # is refused by the gate while the READ half above answered perfectly well. One endpoint
        # with an `action` parameter could not have both.
        check("T4702 in '%s' mode the write half is refused by the safety gate" % mode,
              w.get("ok") is False and "safety gate" in (w.get("error") or ""),
              (w.get("error") or "")[:200])
        again = M.call("source_control", {})
        check("T4702 - and the READ half still works in the same mode, which is the whole "
              "reason these are two endpoints and not one",
              again.get("ok") is True, json.dumps(again)[:200])
        print("  NOT EXERCISED: every argument-validation path in the write half - the gate")
        print("  refuses before the handler is entered, which is the gate working correctly.")
        print("  Relaunch with MIF_BRIDGE_WRITE_MODE=full to reach them.")
    else:
        bad = M.raw_post("source_control_checkout", {"path": "/Game/X", "action": "submit"})
        # WHICH REFUSAL FIRED MATTERS. source_control_checkout refuses on the most fundamental
        # failure first, and on a project with NO revision control provider that is the provider
        # check - execution never reaches the per-action validation whose wording is asserted
        # below. Asserting it anyway made this fail on every run here, in both sweep passes.
        #
        # The same mistake as test_consolidate's T5102 and the struct suites' cooked-asset
        # selection, all found the same day: naming a late gate's wording without establishing
        # that anything gets that far.
        no_provider = "no revision control provider" in (bad.get("error") or "")
        rev = M.raw_post("source_control_checkout", {"path": "/Game/X", "action": "revert"})
        if no_provider:
            print("  NOTE  this project has no revision control provider, so the endpoint refuses")
            print("        at the provider check and the per-action refusals below are")
            print("        unreachable. Asserting the provider refusal instead - the per-action")
            print("        wording is NOT verified here.")
            check("T4702 checkin is refused, naming the missing provider rather than something "
                  "vaguer",
                  bad.get("ok") is False and bad.get("available") is False
                  and bad.get("provider") == "None",
                  json.dumps(bad)[:220])
            check("T4702 revert is refused the same way, and reports the same provider state",
                  rev.get("ok") is False and rev.get("available") is False,
                  json.dumps(rev)[:220])
        else:
            check("T4702 checking IN is not offered, and the refusal says so",
                  bad.get("ok") is False and "not offered" in (bad.get("error") or ""),
                  (bad.get("error") or "")[:200])
            check("T4702 revert without confirm is refused - it discards local changes",
                  rev.get("ok") is False and "DISCARDS" in (rev.get("error") or ""),
                  (rev.get("error") or "")[:200])
        nopath = M.raw_post("source_control_checkout", {"action": "checkout"})
        check("T4702 a missing path is refused", nopath.get("ok") is False,
              (nopath.get("error") or "")[:180])

    # ------------------------------------------------------------------ T4703 parameters
    print("\n=== T4703: parameters that would have been a loop of blocking calls ===")
    plural = M.raw_post("source_control", {"paths": ["/Game/A", "/Game/B"]})
    check("T4703 a plural `paths` is refused", plural.get("ok") is False,
          (plural.get("error") or "")[:220])
    # The reason matters: it is not a missing feature, it is a deliberate one.
    check("T4703 and the refusal explains it would block the game thread per file",
          "blocks the game thread" in (plural.get("error") or "")
          or "blocking" in (plural.get("error") or ""), (plural.get("error") or "")[:250])
    ckparam = M.raw_post("source_control", {"path": "/Game/X", "checkout": True})
    check("T4703 asking the READ half to check out points at the write half",
          ckparam.get("ok") is False and "source_control_checkout" in (ckparam.get("error") or ""),
          (ckparam.get("error") or "")[:200])

    print("\n  NOT EXERCISED: every provider path - QueryFileState, checkout, add and revert.")
    print("  No revision control is configured here, which is exactly the case the endpoints")
    print("  answer with enabled:false. A Perforce-, SVN- or Plastic-backed project is where the")
    print("  rest runs, including the IsAvailable() guard that keeps a synchronous server")
    print("  round-trip off the game thread.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
