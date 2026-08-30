"""set_niagara_emitter - only the ENABLE direction was actually broken.

SCOPE, NARROWED AFTER CHECKING. set_property{propertyPath:"EmitterHandles[N].bIsEnabled"} already
reaches this flag, and DISABLE genuinely works through it - InitEmitters builds an instance per
handle unconditionally and Init sets ExecutionState=Disabled from IsAllowedToExecute.

ENABLE is the half that fails, and it fails SILENTLY. FNiagaraEmitterHandle::SetIsEnabled does two
things a property write skips (NiagaraEmitterHandle.cpp:110-124): RefreshFromExternalChanges on the
system spawn script's source, and InvalidateCompileResults. UNiagaraSystem::PostEditChangeProperty
does not compensate. So set_property flips the bool, the system keeps stale compile results, and the
emitter stays dark with a flag that reads as enabled - a wrong answer rather than an error.

ADD AND REMOVE ARE DELIBERATELY ABSENT and are refused by name. Raw AddEmitterHandle contains an
UNGUARDED null dereference at NiagaraSystem.cpp:2309 - GetLatestEmitterData()->RemoveParent() on a
Template or Behavior emitter - and RemoveEmitterHandle vs RemoveEmitterHandlesById differ in whether
system parameters are cleaned up. Those need their own guards, not a ride alongside a boolean.

COOKED IS REFUSED FOR THE RIGHT REASON, which matters because a wrong reason invites someone to
"fix" it. SetIsEnabled's side-effect block self-skips on cooked content (GetLatestSource is null
there), so nothing crashes. It is refused because the change cannot be persisted and the system
cannot be recompiled - the emitter would come back on restart with the flag saying otherwise.

WHAT THIS SUITE CANNOT DO, AND WHY THAT REASON CHANGED. Every NiagaraSystem shipped in this project
is cooked, so the cooked guard - checked before anything else, correctly - answers every call made
against a real one. The SUCCESS path is unexercised here and this suite says so rather than
implying coverage.

The original reason for not using a scratch system NO LONGER HOLDS, and leaving it written down
would be the stale-rationale trap this repo keeps finding elsewhere. It said a scratch system was
rejected because create_asset's bare NewObject had just been found to leave a UserDefinedEnum in a
state that TERMINATES the editor, and whether UNiagaraSystem needed comparable factory
initialisation was unknown. That audit has since been done - tools/audit_factory_init.py, both
scans - and UNiagaraSystem IS handled: create_asset calls InitializeSystem. A scratch system was
created, read and deleted cleanly on 2026-08-30 to confirm that rather than trust it.

What still blocks the toggle test is narrower than it was: a freshly created system has ZERO
emitters, so there is nothing to toggle until one can be added. That is add_niagara_emitter's job.
When it lands, this suite should gain a scratch-system arm that adds an emitter and then exercises
the enable/disable path for real.
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

    systems = [a["path"] for a in
               (M.call("find_assets", {"class": "NiagaraSystem", "limit": 6}).get("assets") or [])]
    check("(setup) the project has NiagaraSystems", len(systems) > 0, len(systems))
    if not systems:
        print("SKIPPED - no NiagaraSystem in this project.")
        return 0
    target = systems[0]

    # ------------------------------------------------------------------ T6100 the read half
    print("=== T6100: the emitters are readable, which is how you name one ===")
    d = M.call("list_niagara_emitters", {"path": target})
    check("T6100 list_niagara_emitters succeeds", d.get("ok") is True, json.dumps(d)[:220])
    emitters = [e.get("name") for e in (d.get("emitters") or []) if isinstance(e, dict)]
    # ASSERT THE VALUE, NOT THE KEY. `"enabled" in e` only says the field is present, which is
    # how a row full of nulls passes a green check - and counting rather than all() stops it
    # passing vacuously on an empty list.
    rows = [e for e in (d.get("emitters") or []) if isinstance(e, dict)]
    typed = sum(1 for e in rows if isinstance(e.get("enabled"), bool) and e.get("name"))
    check("T6100 it returned emitter rows at all", len(rows) > 0, len(rows))
    check("T6100 and every row carries a name and a real boolean enabled state",
          len(rows) > 0 and typed == len(rows),
          "%d of %d rows fully typed: %s" % (typed, len(rows), json.dumps(rows)[:180]))
    first = emitters[0] if emitters else "x"

    # ------------------------------------------------------------------ T6101 the cooked reason
    print("\n=== T6101: cooked is refused, and for the right reason ===")
    r = M.raw_post("set_niagara_emitter", {"path": target, "emitter": first, "enabled": False})
    if r.get("ok") is False and "COOKED" in (r.get("error") or ""):
        check("T6101 a cooked system is refused", True, "")
        # THE assertion. A refusal that blamed a crash would invite someone to "fix" it by adding
        # a guard the engine already has - the real reason is that the change cannot persist.
        check("T6101 and the reason is persistence, NOT a crash",
              "cannot be saved" in (r.get("error") or "")
              and "cannot be recompiled" in (r.get("error") or ""),
              (r.get("error") or "")[:250])
        check("T6101 it says explicitly that the engine's own block self-skips there, so nobody "
              "adds a redundant safety guard later",
              "self-skips" in (r.get("error") or ""), (r.get("error") or "")[:280])
        print("\n  NOT EXERCISED: the toggle itself. Every NiagaraSystem in this project is cooked,")
        print("  so the cooked guard - checked first, correctly - answers every call. An uncooked")
        print("  project is where the enable/disable path runs.")
    else:
        check("T6101 an uncooked system toggles", r.get("ok") is True, json.dumps(r)[:250])
        check("T6101 and reports the state it read back, not the one requested",
              r.get("enabled") is False and r.get("wasEnabled") is True, json.dumps(r)[:220])
        back = M.raw_post("set_niagara_emitter", {"path": target, "emitter": first,
                                                  "enabled": True})
        check("T6101 (restore) it can be turned back on", back.get("ok") is True,
              json.dumps(back)[:200])
        again = M.raw_post("set_niagara_emitter", {"path": target, "emitter": first,
                                                   "enabled": True})
        check("T6101 setting the state it already has succeeds with changed:false",
              again.get("ok") is True and again.get("changed") is False, json.dumps(again)[:200])

    # ------------------------------------------------------------------ T6102 the refusals
    print("\n=== T6102: what is deliberately not offered ===")
    for param, why in (("add", "AddEmitterHandle"), ("remove", "RemoveEmitterHandle"),
                       ("index", "shifts when anything is added")):
        bad = M.raw_post("set_niagara_emitter", {"path": target, "emitter": first,
                                                 "enabled": True, param: True})
        check("T6102 '%s' is refused by name" % param, bad.get("ok") is False,
              (bad.get("error") or "")[:180])
    addr = M.raw_post("set_niagara_emitter", {"path": target, "emitter": first,
                                              "enabled": True, "add": True})
    # The refusal has to say WHY, or the next person just builds it unguarded.
    check("T6102 and the add refusal names the unguarded null dereference",
          "null dereference" in (addr.get("error") or ""), (addr.get("error") or "")[:220])

    notsys = M.raw_post("set_niagara_emitter", {
        "path": "/Engine/EditorResources/S_Actor.S_Actor", "emitter": "x", "enabled": True})
    check("T6102 a non-NiagaraSystem is refused by class",
          notsys.get("ok") is False and "not a NiagaraSystem" in (notsys.get("error") or ""),
          (notsys.get("error") or "")[:200])
    nopath = M.raw_post("set_niagara_emitter", {"emitter": "x", "enabled": True})
    check("T6102 a missing path is refused", nopath.get("ok") is False,
          (nopath.get("error") or "")[:180])

    check("T6102 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
          "this project's own gotchas record a cooked NiagaraSystem killing the editor in PostLoad")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
