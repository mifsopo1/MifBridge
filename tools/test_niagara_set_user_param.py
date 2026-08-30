"""set_niagara_user_parameter - the guards, and an honest account of what cannot be tested here.

WHAT THIS SUITE CANNOT DO, said first because it is the most important thing about it. The SUCCESS
path is not exercised in this project and cannot be, which is a property of the project rather than
of the endpoint:

  - every NiagaraSystem shipped here is COOKED, and the write refuses cooked content
  - a scratch system from create_asset has ZERO user parameters (verified: count 0), so there is
    nothing to set on one
  - duplicating a cooked system would give both, and duplicate_asset correctly REFUSES that -
    duplicating a cooked Niagara asset crashes the editor inside Niagara's own code
    (MifBridgeAssetOps.cpp:430)

So this suite verifies the refusals, the type dispatch, and the postcondition contract. An uncooked
project with a real system - Curfew - is where the write itself gets exercised, and saying so beats
a green run that implies coverage it does not have.

THE TWO CRASH TRAPS ARE WHY THE TYPE DISPATCH MATTERS MORE THAN USUAL. Both are check(), which
terminates the process rather than returning an error:

    SetParameterValue<T>   check(Param.GetSizeInBytes() == sizeof(T))   ParameterStore.h:527
    Position parameters    check(HasPositionData(Param.GetName()))      ParameterStore.h:531

So an unhandled type must be REFUSED, never attempted with a plausible-looking T. T8302 asserts the
refusal says that in as many words, because the next person to add a type needs to know that the
default case is not "wrong value" but "editor gone".

T8301 IS THE COOKED REASON, and it is deliberately not a safety claim. The store is runtime data and
the write would succeed; it is refused because the change cannot be saved or recompiled, so the old
value returns on restart while the response claims the new one. A refusal that blamed a crash would
invite someone to "fix" it by removing a guard the engine does not need.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

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
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    # A cooked system that actually HAS user parameters - the refusal is only meaningful against one.
    cooked = None
    for a in (M.call("find_assets", {"class": "NiagaraSystem", "limit": 12}).get("assets") or []):
        r = M.raw_post("list_niagara_user_parameters", {"path": a["path"]})
        if r.get("ok") and (r.get("count") or 0) > 0:
            cooked = (a["path"], r)
            break

    st = int(time.time()) % 100000
    scratch = None
    try:
        # ------------------------------------------------------------------ T8300 unknown name
        print("=== T8300: an unknown parameter is refused, and adding one is not offered ===")
        made = M.raw_post("create_asset", {"path": "/Game/_MifNiag/NS_S%d" % st,
                                           "class": "NiagaraSystem"})
        scratch = made.get("assetPath")
        check("(setup) a scratch NiagaraSystem exists", bool(scratch), json.dumps(made)[:200])
        if not scratch:
            return 1
        empty = M.raw_post("list_niagara_user_parameters", {"path": scratch})
        check("(setup) and it has no user parameters, which is why the success path cannot run here",
              empty.get("count") == 0, json.dumps(empty)[:200])

        u = M.raw_post("set_niagara_user_parameter", {"path": scratch, "name": "NoSuchParam",
                                                      "value": 1})
        check("T8300 an unknown parameter name is refused", u.get("ok") is False,
              json.dumps(u)[:220])
        check("T8300 and the refusal lists what the system actually has, so a typo is one step from "
              "fixed",
              "It has:" in (u.get("error") or ""), (u.get("error") or "")[:200])
        # Adding by typo would leave an invisible parameter that does nothing - worse than a refusal.
        check("T8300 and says adding one is deliberately not offered, with the reason",
              "no emitter reads" in (u.get("error") or ""), (u.get("error") or "")[:250])
        add = M.raw_post("set_niagara_user_parameter", {"path": scratch, "name": "X", "value": 1,
                                                        "add": True})
        check("T8300 an 'add' parameter is refused BY NAME rather than silently ignored",
              add.get("ok") is False and "add" in (add.get("error") or "").lower(),
              (add.get("error") or "")[:220])

        # ------------------------------------------------------------------ T8301 cooked
        print("\n=== T8301: cooked is refused for persistence, NOT for safety ===")
        if cooked:
            path, listing = cooked
            pname = (listing.get("parameters") or [{}])[0].get("name") or "User.Unknown"
            w = M.raw_post("set_niagara_user_parameter", {"path": path, "name": pname,
                                                          "value": 0.5})
            check("T8301 writing a cooked system is refused", w.get("ok") is False,
                  json.dumps(w)[:200])
            # THE assertion. A refusal blaming a crash would invite someone to remove a guard the
            # engine does not need; the true reason is that the change cannot outlive the session.
            check("T8301 and the reason is that it cannot be SAVED or recompiled, not that it would "
                  "crash",
                  "cannot be SAVED" in (w.get("error") or "")
                  and "recompiled" in (w.get("error") or ""), (w.get("error") or "")[:260])
            check("T8301 it says explicitly that the write itself would succeed, so nobody 'fixes' "
                  "this by adding a safety guard",
                  "would succeed" in (w.get("error") or ""), (w.get("error") or "")[:260])
            # And the read must still work on the same asset - the refusal is about writing only.
            still = M.raw_post("list_niagara_user_parameters", {"path": path})
            check("T8301 the cooked system is still READABLE - only the write is refused",
                  still.get("ok") is True and (still.get("count") or 0) > 0,
                  json.dumps(still)[:200])
        else:
            print("  NOTE  no cooked NiagaraSystem here exposes a user parameter, so T8301 is")
            print("        unexercised. Reported rather than passed silently.")

        # ------------------------------------------------------------------ T8302 ordinary guards
        print("\n=== T8302: the guards around the type dispatch ===")
        notsys = M.raw_post("set_niagara_user_parameter", {
            "path": "/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial",
            "name": "X", "value": 1})
        check("T8302 a non-NiagaraSystem is refused by class",
              notsys.get("ok") is False and "not a NiagaraSystem" in (notsys.get("error") or ""),
              (notsys.get("error") or "")[:200])
        noval = M.raw_post("set_niagara_user_parameter", {"path": scratch, "name": "X"})
        check("T8302 a missing value is refused", noval.get("ok") is False,
              (noval.get("error") or "")[:180])
        noname = M.raw_post("set_niagara_user_parameter", {"path": scratch, "value": 1})
        check("T8302 a missing name is refused", noname.get("ok") is False,
              (noname.get("error") or "")[:180])
        nopath = M.raw_post("set_niagara_user_parameter", {"name": "X", "value": 1})
        check("T8302 a missing path is refused", nopath.get("ok") is False,
              (nopath.get("error") or "")[:180])
        typ = M.raw_post("set_niagara_user_parameter", {"path": scratch, "name": "X", "value": 1,
                                                        "type": "float"})
        # The type is the system's own record. Letting a caller assert one is how a mismatched T
        # reaches a check() and ends the process.
        check("T8302 a caller-supplied 'type' is refused - the type is the system's, and a mismatch "
              "would terminate the editor",
              typ.get("ok") is False and "terminate" in (typ.get("error") or "").lower(),
              (typ.get("error") or "")[:250])

        print("\n  NOT EXERCISED: the write itself. Every NiagaraSystem here is cooked, a scratch")
        print("  one has no user parameters, and duplicating a cooked Niagara asset is correctly")
        print("  refused because it crashes the editor. An uncooked project is where this runs.")

        check("T8302 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
              "SetParameterValue check()s the size against the type - a mismatch is a process kill")
    finally:
        if scratch:
            SC.confirm_call("delete_asset", {"path": scratch})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
