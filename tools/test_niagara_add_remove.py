"""add_niagara_emitter / remove_niagara_emitter, which were BUILT and never verified.

WHY THIS SUITE EXISTS AT ALL. parity_check found both endpoints had a MIF_BIND and no MCP wrapper -
HTTP-reachable and MCP-invisible - which meant they had been built at some point without being
exposed, tested, or ticked. The backlog had been listing built work as open. The wrappers went in
immediately; ticking the entry waited for this, because a box ticked on the strength of a handler
existing is the claim the built-tested-committed rule exists to stop.

THE SUCCESS PATH NEEDS A SCRATCH SYSTEM, and that is why this could not have been written earlier.
Every NiagaraSystem shipped in this project is cooked, and the cooked guard - correctly checked
first - answers every call made against one. create_asset makes a usable scratch system
(it calls InitializeSystem; confirmed 2026-08-30), and this project has four NiagaraEmitter source
assets to add from, including engine /Niagara/VectorFields/ ones.

T8100 IS THE PAIR, and the postcondition is the emitter list rather than either call's return.
AddEmitterHandle returns a handle by value and RemoveEmitterHandle returns void, so neither says
anything trustworthy about what the system now contains.

T8102 GUARDS THE ASYMMETRY THAT MADE remove ITS OWN ITEM. RemoveEmitterHandle calls
RemoveSystemParametersForEmitter and RemoveEmitterHandlesById does not, while only the latter calls
InitEmitterCompiledData - they differ in BOTH directions, so picking one silently leaves either
orphaned system parameters or stale compiled data. The response says which was used, and this
asserts that it still does.
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


def names(system):
    r = M.raw_post("list_niagara_emitters", {"path": system})
    return [e.get("name") for e in (r.get("emitters") or []) if isinstance(e, dict)]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    sources = [a["path"] for a in
               (M.call("find_assets", {"class": "NiagaraEmitter", "limit": 6}).get("assets") or [])]
    if len(sources) < 2:
        print("SKIPPED - this project has fewer than two NiagaraEmitter assets to add from, so the")
        print("  success path cannot be exercised. Nothing was verified.")
        return 2

    st = int(time.time()) % 100000
    system = None
    try:
        made = M.raw_post("create_asset", {"path": "/Game/_MifNiag/NS_T%d" % st,
                                           "class": "NiagaraSystem"})
        system = made.get("assetPath")
        check("(setup) a scratch NiagaraSystem is created - every shipped one is cooked and would "
              "be refused", bool(system), json.dumps(made)[:220])
        if not system:
            return 1
        check("(setup) and it starts with no emitters", names(system) == [],
              json.dumps(names(system)))

        # ------------------------------------------------------------------ T8100 the pair
        print("\n=== T8100: add then remove, judged by what the system CONTAINS ===")
        a = M.raw_post("add_niagara_emitter", {"path": system, "emitter": sources[0],
                                               "name": "MifA"})
        check("T8100 adding an emitter succeeds", a.get("ok") is True, json.dumps(a)[:250])
        # THE assertion. AddEmitterHandle returns a handle by value, which says nothing about
        # whether the system kept it - so the emitter list is the only real evidence.
        check("T8100 and the system now lists it by the name that was asked for",
              names(system) == ["MifA"], json.dumps(names(system)))
        check("T8100 the response reports the source it copied, not just success",
              a.get("source") == sources[0], a.get("source"))
        check("T8100 and says the compile results were invalidated rather than silently rebuilt",
              "invalidated" in (a.get("compileNote") or ""), (a.get("compileNote") or "")[:200])

        b = M.raw_post("add_niagara_emitter", {"path": system, "emitter": sources[1],
                                               "name": "MifB"})
        check("T8100 a second emitter is APPENDED, not replacing the first",
              b.get("ok") is True and names(system) == ["MifA", "MifB"],
              json.dumps(names(system)))

        r = M.raw_post("remove_niagara_emitter", {"path": system, "emitter": "MifA"})
        check("T8100 removing by name succeeds", r.get("ok") is True, json.dumps(r)[:250])
        # RemoveEmitterHandle returns void. The list is the postcondition.
        check("T8100 and exactly the named one is gone - the other survives",
              names(system) == ["MifB"], json.dumps(names(system)))
        check("T8100 the counts before and after are both reported, so a no-op is visible",
              r.get("emitterCountBefore") == 2 and r.get("emitterCount") == 1,
              json.dumps(r)[:220])

        # ------------------------------------------------------------------ T8101 refusals
        print("\n=== T8101: what it refuses, and whether the reason is actionable ===")
        miss = M.raw_post("remove_niagara_emitter", {"path": system, "emitter": "NoSuchEmitter"})
        check("T8101 removing an emitter that is not there is refused",
              miss.get("ok") is False, json.dumps(miss)[:200])
        # A refusal that lists what IS there turns a typo into a one-step fix.
        check("T8101 and the refusal names what the system actually has",
              "MifB" in (miss.get("error") or ""), (miss.get("error") or "")[:200])
        check("T8101 and says nothing was changed",
              "NOTHING was changed" in (miss.get("error") or ""), (miss.get("error") or "")[:200])
        check("T8101 the survivor is still there after the refused removal",
              names(system) == ["MifB"], json.dumps(names(system)))

        wrong = M.raw_post("add_niagara_emitter", {"path": system, "emitter": system})
        check("T8101 a NiagaraSystem passed where an EMITTER is wanted is refused by CLASS",
              wrong.get("ok") is False and "not a NiagaraEmitter" in (wrong.get("error") or ""),
              (wrong.get("error") or "")[:200])
        nopath = M.raw_post("add_niagara_emitter", {"emitter": sources[0]})
        check("T8101 a missing system path is refused", nopath.get("ok") is False,
              (nopath.get("error") or "")[:180])
        idx = M.raw_post("remove_niagara_emitter", {"path": system, "emitter": "MifB", "index": 0})
        check("T8101 an index is refused by NAME - it shifts when anything is added or removed",
              idx.get("ok") is False and "index" in (idx.get("error") or "").lower(),
              (idx.get("error") or "")[:200])

        # ------------------------------------------------------------------ T8102 the asymmetry
        print("\n=== T8102: the removal path is the one that cleans up ===")
        r2 = M.raw_post("remove_niagara_emitter", {"path": system, "emitter": "MifB"})
        check("T8102 the last emitter can be removed", r2.get("ok") is True, json.dumps(r2)[:220])
        check("T8102 leaving an empty system rather than a broken one", names(system) == [],
              json.dumps(names(system)))
        # RemoveEmitterHandle vs RemoveEmitterHandlesById differ in BOTH directions - only the
        # former clears system parameters, only the latter rebuilds compiled data. Saying which was
        # used is the difference between a caller knowing what was cleaned and guessing.
        check("T8102 and the response says WHICH removal path ran, because the two engine calls "
              "clean up different things",
              "RemoveEmitterHandle" in (r2.get("cleanupNote") or ""),
              (r2.get("cleanupNote") or "")[:220])

        check("T8102 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
              "AddEmitterHandle reaches unguarded dereferences of editor-only fields on a cooked "
              "source emitter")
    finally:
        if system:
            SC.confirm_call("delete_asset", {"path": system})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
