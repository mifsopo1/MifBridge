"""describe_niagara_system and list_niagara_emitters - the first structural Niagara coverage.

Before these, MifBridge had exactly ONE Niagara endpoint: list_niagara_user_parameters, which answers
about a system's exposed parameters and nothing about what the system IS. There was no way to ask how
many emitters an effect has or whether they are enabled.

THREE THINGS THIS SUITE GUARDS.

The first is THE GUARD ITSELF. Niagara is a PLUGIN, so unlike LevelSequence it can be absent, and these
endpoints are compiled behind MIF_WITH_NIAGARA. The whole point of the IK Rig pattern is that the
endpoint stays REGISTERED either way and answers with a named refusal instead of vanishing - so T590
asserts registration UNCONDITIONALLY and then branches on whether the plugin actually answered. A suite
that skipped itself when the plugin was missing would let the guard rot silently, which is the one
failure the pattern exists to prevent.

The second is that ENABLED IS NOT THE SAME AS PRESENT. A disabled emitter is invisible at runtime and
perfectly visible in the editor, which is a recurring source of "the effect does nothing". T591 asserts
enabled + disabled == emitterCount, so the two counts can never drift apart or double-count.

The third is the FILTER HONESTY RULE this project keeps re-learning: `count` is what survived the filter
and `totalEmitters` is the truth. T592 filters to nothing and asserts totalEmitters still reports the
real number, because a filtered list that looks like completeness is how a caller concludes an emitter
was deleted.

SAFETY: read-only throughout. Nothing is created, duplicated, reinitialised or compiled. That last point
is not incidental - docs/02_GOTCHAS.md section 6c records duplicate_asset on a COOKED UNiagaraSystem
crashing the editor in FVersionedNiagaraEmitterData::PostLoad, and this suite runs against exactly that
cooked content. It reads handles off loaded systems and nothing else.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T590 the guard
    print("=== T590 [the guard]: registered whether or not the plugin is here ===")
    eps = M.endpoint_names()
    for e in ("describe_niagara_system", "list_niagara_emitters"):
        # UNCONDITIONAL. If MIF_WITH_NIAGARA is 0 these must still exist and refuse by name - an endpoint
        # that disappears with its plugin tells a caller nothing about why.
        check("T590 %s is registered" % e, e in eps,
              "%d endpoints and this one is absent - the MIF_WITH_NIAGARA guard is supposed to keep it "
              "registered and compile a refusal, not drop it" % len(eps))

    systems = M.call("find_assets", {"class": "NiagaraSystem", "pathPrefix": "/Game/", "limit": 8},
                     timeout=180).get("assets") or []
    probe = M.call("describe_niagara_system",
                   {"path": (systems[0].get("path") if systems else "/Game/zz.zz")}, timeout=120)
    plugin_absent = "no Niagara plugin" in (probe.get("error") or "")
    if plugin_absent:
        # The refusal path. Assert it says WHY rather than just failing, then stop - there is no Niagara
        # here to read and pretending otherwise would be a vacuous pass.
        print("  (this engine has no Niagara plugin - asserting the refusal is well-formed)")
        check("T590 the refusal explains the plugin is what is missing",
              "plugin" in (probe.get("error") or ""), (probe.get("error") or "")[:200])
        check("T590 list_niagara_emitters refuses the same way",
              "no Niagara plugin" in (M.call("list_niagara_emitters", {"path": "/Game/zz.zz"},
                                             timeout=60).get("error") or ""), "")
        print("")
        print("=" * 72)
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1 if FAIL else 0

    # ------------------------------------------------------------------ T591 the counts
    print("")
    print("=== T591 [enabled is not present]: the three counts must reconcile ===")
    if not systems:
        check("T591 (not exercised: this project ships no NiagaraSystem)", True)
    else:
        for s in systems[:5]:
            path = s.get("path")
            d = M.call("describe_niagara_system", {"path": path}, timeout=180)
            label = str(d.get("name") or s.get("name") or "?")
            check("T591 %s describes" % label, d.get("ok") is True, json.dumps(d)[:200])
            n, en, dis = d.get("emitterCount"), d.get("enabledEmitterCount"), d.get("disabledEmitterCount")
            check("T591 %s reports all three counts" % label,
                  all(isinstance(x, (int, float)) for x in (n, en, dis)), json.dumps(d)[:200])
            if all(isinstance(x, (int, float)) for x in (n, en, dis)):
                # THE assertion. If these ever disagree, one of the two states is being double-counted
                # or dropped, and every conclusion drawn from the pair is unsound.
                check("T591 %s: enabled + disabled == emitterCount" % label, en + dis == n,
                      "%s + %s != %s" % (en, dis, n))
                check("T591 %s: neither count is negative" % label, en >= 0 and dis >= 0,
                      json.dumps(d)[:180])
            check("T591 %s echoes an object path" % label,
                  str(d.get("system", "")).startswith("/"), json.dumps(d)[:180])

            # ---- compile state, added 2026-08-31 ------------------------------------------
            # set_niagara_emitter warns that the wrong kind of edit leaves "a stale compile result
            # and an emitter that stays dark with a flag saying otherwise", and until this endpoint
            # reported compile state there was NO WAY to check that - the only thing on offer was
            # the flag the note says is lying. Measured then: set_property flips bIsEnabled both
            # ways and list_niagara_emitters reports it happily either way.
            check("T591 %s reports whether its compiled data is current" % label,
                  isinstance(d.get("compiledDataCurrent"), bool),
                  "compiledDataCurrent=%r" % d.get("compiledDataCurrent"))
            check("T591 %s reports compilePending and readyToRun as real bools" % label,
                  isinstance(d.get("compilePending"), bool)
                  and isinstance(d.get("readyToRun"), bool),
                  "compilePending=%r readyToRun=%r"
                  % (d.get("compilePending"), d.get("readyToRun")))
            # THE AGREEMENT CHECK, which is what makes this more than a presence test: the note is
            # emitted on exactly the condition the two bools describe, so the three are one fact
            # told twice and must not disagree.
            if isinstance(d.get("compiledDataCurrent"), bool)                     and isinstance(d.get("compilePending"), bool):
                stale = (not d.get("compiledDataCurrent")) or d.get("compilePending")
                check("T591 %s: compileNote is present exactly when it is stale or compiling"
                      % label,
                      bool(d.get("compileNote")) is stale,
                      "current=%r pending=%r note=%r"
                      % (d.get("compiledDataCurrent"), d.get("compilePending"),
                         (d.get("compileNote") or "")[:70]))

    # ------------------------------------------------------------------ T592 filter honesty
    print("")
    print("=== T592 [filter honesty]: a filtered list must never read as completeness ===")
    if not systems:
        check("T592 (not exercised: no NiagaraSystem)", True)
    else:
        path = systems[0].get("path")
        full = M.call("list_niagara_emitters", {"path": path}, timeout=180)
        check("T592 the listing succeeds", full.get("ok") is True, json.dumps(full)[:200])
        rows = full.get("emitters") or []
        check("T592 count agrees with the array it returned", full.get("count") == len(rows),
              "count=%s but %d rows" % (full.get("count"), len(rows)))
        check("T592 the unfiltered count equals totalEmitters",
              full.get("count") == full.get("totalEmitters"),
              json.dumps({k: full.get(k) for k in ("count", "totalEmitters")}))
        # It must also agree with what describe said - two endpoints reading the same handles that
        # disagree is worse than either being wrong alone.
        d = M.call("describe_niagara_system", {"path": path}, timeout=180)
        check("T592 and it agrees with describe_niagara_system",
              full.get("totalEmitters") == d.get("emitterCount"),
              "list says %s, describe says %s" % (full.get("totalEmitters"), d.get("emitterCount")))

        for e in rows[:4]:
            check("T592 emitter %s has an index" % (e.get("name") or "?"),
                  isinstance(e.get("index"), (int, float)), json.dumps(e)[:160])
            check("T592 emitter %s has a name" % (e.get("name") or "?"), bool(e.get("name")),
                  json.dumps(e)[:160])
            check("T592 emitter %s has a GUID" % (e.get("name") or "?"), bool(e.get("id")),
                  json.dumps(e)[:160])
            check("T592 emitter %s has a bool enabled" % (e.get("name") or "?"),
                  isinstance(e.get("enabled"), bool), json.dumps(e)[:160])
        if rows:
            # Indices must be the real positions, contiguous from 0 on an unfiltered list. They are the
            # documented way to address an emitter, so a wrong one is worse than none.
            check("T592 indices are contiguous from 0 on an unfiltered list",
                  [e.get("index") for e in rows] == list(range(len(rows))),
                  str([e.get("index") for e in rows])[:120])

        # THE assertion this test is named for.
        none = M.call("list_niagara_emitters",
                      {"path": path, "nameContains": "zzNoSuchEmitter_zz"}, timeout=120)
        check("T592 a filter matching nothing still succeeds", none.get("ok") is True,
              json.dumps(none)[:180])
        check("T592 and returns zero rows", none.get("count") == 0, json.dumps(none)[:180])
        check("T592 but totalEmitters STILL reports the real count",
              none.get("totalEmitters") == full.get("totalEmitters"),
              "filtered-to-nothing reported totalEmitters=%s, real=%s - a caller would conclude the "
              "emitters were deleted" % (none.get("totalEmitters"), full.get("totalEmitters")))
        if full.get("totalEmitters"):
            check("T592 and says so in a note", bool(none.get("note")), json.dumps(none)[:200])

        if rows:
            one = M.call("list_niagara_emitters",
                         {"path": path, "nameContains": rows[0].get("name")}, timeout=120)
            check("T592 filtering by a real name matches at least that one",
                  (one.get("count") or 0) >= 1, json.dumps(one)[:180])

    # ------------------------------------------------------------------ T593 contracts
    print("")
    print("=== T593: bad references and unknown keys are refused with a pointer ===")
    q = M.call("describe_niagara_system", {"path": "/Game/NoSuchSystem_zz.NoSuchSystem_zz"}, timeout=90)
    check("T593 a system that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T593 and the refusal shows how to find one",
          "find_assets" in (q.get("error") or ""), (q.get("error") or "")[:190])
    q = M.call("describe_niagara_system", {}, timeout=60)
    check("T593 a missing path is refused", q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("describe_niagara_system", {"emitter": "x"}, timeout=60)
    check("T593 'emitter' is refused on describe", q.get("ok") is False, json.dumps(q)[:180])
    check("T593 and points at list_niagara_emitters",
          "list_niagara_emitters" in (q.get("error") or ""), (q.get("error") or "")[:190])
    q = M.call("list_niagara_emitters", {"index": 0}, timeout=60)
    check("T593 'index' is refused on the listing", q.get("ok") is False, json.dumps(q)[:180])
    check("T593 and points at nameContains",
          "nameContains" in (q.get("error") or ""), (q.get("error") or "")[:190])
    check("T593 the bridge is still answering", M.bridge_responsive() is True,
          "bridge died - see gotchas 6c, cooked Niagara has killed this editor before")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
