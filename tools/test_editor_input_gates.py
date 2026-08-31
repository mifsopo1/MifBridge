"""send_editor_key / invoke_editor_command - the gates, and nothing past them.

WHY THIS EXISTS. Both endpoints are confirm-gated, and both are named in exactly one suite -
test_safety_gate.py - which SKIPS in this write mode, so 8 of its 38 assertions run and these gates
are effectively untested. Found by asking which of the 57 confirm-gated endpoints have a suite that
mentions confirm anywhere near them: 54 do, and these two do not. (The third, move_tree_widget, reads
confirm as an alias for replaceRoot and needs a widget fixture; it is filed rather than covered here.)

These two are worth the care more than most. invoke_editor_command's own context list includes
NewLevel, OpenLevel, Save and SaveAllLevels, and send_editor_key delivers a synthetic keystroke to
whatever currently has focus - "a synthetic key runs whatever is bound to it", in its own words. The
gate is the only thing between a mistyped payload and one of those.

THIS SUITE NEVER SENDS confirm:true. Not once. Every assertion is about the refusal or about dryRun,
which both endpoints offer precisely so a caller can check without firing. That is not a limitation
of the suite - for these two, the gate IS the behaviour worth testing, and the success path is a
thing to do deliberately with a person watching.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "\n        " + str(detail)[:300]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    registry = set(M.raw_post("self_audit", {}).get("endpoints") or [])
    for ep in ("send_editor_key", "invoke_editor_command", "list_editor_commands"):
        if ep not in registry:
            print("SKIPPED - %s is not registered on this build." % ep)
            return 2

    # ------------------------------------------------------------------ E1 the key gate
    print("\n=== E1: send_editor_key refuses without confirm, and says what to pass ===")
    r = M.raw_post("send_editor_key", {"key": "A"})
    check("E1 a valid key with no confirm is REFUSED - a synthetic keystroke runs whatever is bound "
          "to it, which the request never names",
          r.get("ok") is False, json.dumps(r)[:220])
    err = (r.get("error") or "")
    check("E1 the refusal names confirm, so the caller knows what is missing",
          "confirm" in err.lower(), err[:240])
    check("E1 and offers dryRun as the way to check WITHOUT sending - a gate that only says no "
          "teaches people to pass confirm reflexively",
          "dryrun" in err.lower(), err[:240])

    # ------------------------------------------------------------------ E2 dryRun really is dry
    print("\n=== E2: dryRun validates and does NOT send ===")
    d = M.raw_post("send_editor_key", {"key": "A", "dryRun": True})
    check("E2 dryRun answers ok rather than refusing", d.get("ok") is True, json.dumps(d)[:220])
    check("E2 and reports sent:false - the field is the claim, not the absence of an error",
          d.get("sent") is False, json.dumps(d)[:220])
    check("E2 it still validated the key", d.get("keyValid") is True, json.dumps(d)[:220])
    check("E2 and reports what HAS focus, which is the thing a real send would go to",
          isinstance(d.get("focusedWidget"), dict), json.dumps(d.get("focusedWidget"))[:200])

    # ------------------------------------------------------------------ E3 key validation
    print("\n=== E3: an unknown key is refused BY NAME, before any gate argument matters ===")
    b = M.raw_post("send_editor_key", {"key": "NotAKey_zz"})
    berr = (b.get("error") or "")
    check("E3 a bogus key is refused", b.get("ok") is False, json.dumps(b)[:220])
    check("E3 the refusal quotes the key it did not recognise", "NotAKey_zz" in berr, berr[:240])
    check("E3 and states that nothing was sent - so a caller knows the failure was total",
          "nothing was sent" in berr.lower(), berr[:240])
    bd = M.raw_post("send_editor_key", {"key": "NotAKey_zz", "dryRun": True})
    check("E3 dryRun does not skip validation - a dry run that accepts a key the real call would "
          "reject is a dry run that proves nothing",
          bd.get("ok") is False or bd.get("keyValid") is False, json.dumps(bd)[:240])

    # ------------------------------------------------------------------ E4 the command gate
    print("\n=== E4: invoke_editor_command validates before it fires, and dryRun never fires ===")
    n = M.raw_post("invoke_editor_command", {"context": "LevelEditor", "command": "NoSuchCmd_zz"})
    nerr = (n.get("error") or "")
    check("E4 an unknown command is refused", n.get("ok") is False, json.dumps(n)[:220])
    check("E4 and the refusal points at list_editor_commands rather than leaving the caller guessing",
          "list_editor_commands" in nerr, nerr[:240])

    dr = M.raw_post("invoke_editor_command",
                    {"context": "LevelEditor", "command": "BrowseLevel", "dryRun": True})
    check("E4 dryRun reports invoked:false", dr.get("invoked") is False, json.dumps(dr)[:240])
    check("E4 and names the MODAL hazard - an invoked action is third-party code that may open a "
          "dialog, and a modal on the game thread takes this bridge down with it",
          "modal" in json.dumps(dr).lower(), json.dumps(dr)[:260])

    # ------------------------------------------------------------------ E5 parameter guards
    print("\n=== E5: unknown parameters are refused, not ignored ===")
    for ep, payload in (("send_editor_key", {"key": "A", "zzz": 1}),
                        ("invoke_editor_command", {"context": "LevelEditor", "zzz": 1})):
        u = M.raw_post(ep, payload)
        check("E5 %s refuses an unrecognised parameter" % ep,
              u.get("ok") is False and "zzz" in (u.get("error") or ""), (u.get("error") or "")[:220])

    print("")
    print("NOT PROVEN BY THIS SUITE, deliberately: that confirm:true actually delivers the key or")
    print("fires the command. Reaching that means firing arbitrary bound code - the LevelEditor")
    print("context alone offers NewLevel, OpenLevel, Save and SaveAllLevels - and it is a thing to")
    print("do with a person watching, not in an unattended sweep.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
