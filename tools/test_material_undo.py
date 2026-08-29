"""set_material_parameter's undo correctness - locks in a fix that shipped with no test.

Found 2026-08-29 while sweeping the source for TODO markers (a different search method from
coverage_gaps.py or param_reach.py - reading developer notes left in the code directly). A comment
right above H_set_material_parameter (MifBridgeAuthoring.cpp) still read "TODO(audit D.1): this handler
never calls MIC->Modify(), so its writes are invisible to the blanket transaction and Ctrl-Z does not
restore the previous parameter values" - a real, documented undo-correctness bug.

Reading further into the SAME function found the fix already shipped: MIC->Modify() is called right
before the first write, with its own comment explaining exactly why ("Modify() BEFORE the first write.
Without it this handler recorded NOTHING into RunEndpoint's blanket transaction..."). The TODO at the
top of the function was simply never removed once the fix landed further down - a stale comment
describing a bug that no longer existed, the actual defect by the time this was checked.

Verified live before touching anything, not assumed from reading code alone: created a scratch
MaterialInstanceConstant, set a real scalar parameter, confirmed list_transactions shows a genuine new
entry (not popped as a no-op transient), called undo_transactions, and confirmed the parameter value
actually reverted. This test formalizes that exact sequence so the fix stays proven rather than resting
on a comment nobody re-verifies.

T1730: the write actually changes the value and registers a real transaction (queueLength/currentIndex
genuinely advance, not just ok:true).
T1731: undo_transactions genuinely reverts the parameter value - the actual undo-correctness property
the original TODO said was broken.
T1732: redo brings the change back, proving the transaction round-trips both directions, not just undo.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def scalar_value(material, param_name):
    r = M.call("list_material_parameters", {"material": material})
    for p in r.get("parameters", []):
        if p.get("name") == param_name:
            return p.get("value"), p.get("overriddenOnThisInstance")
    return None, None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # A real DDS2 master material with a known scalar parameter (Wind_Intensity, default 1) -
    # confirmed live via list_material_parameters before writing this test, not assumed.
    parent = "/Game/Blueprints/Enviro/PoleCableMat"
    mi_path = "/Game/_MifMaterialUndo/MI_UndoTest_%d" % st

    created = M.call("create_material_instance", {"parent": parent, "path": mi_path})
    check("(setup) a scratch material instance was created", created.get("ok") is True,
          json.dumps(created)[:200])
    if not created.get("ok"):
        return 3

    before_val, before_overridden = scalar_value(mi_path, "Wind_Intensity")
    check("(setup) Wind_Intensity starts at the parent's default (1), not overridden",
          before_val == 1 and before_overridden is False, (before_val, before_overridden))

    # ------------------------------------------------------------------ T1730 the write itself
    print("\n=== T1730: set_material_parameter genuinely registers a transaction ===")
    before_tx = M.call("list_transactions", {"limit": 1})
    before_index = before_tx.get("currentIndex")

    written = M.call("set_material_parameter", {"material": mi_path, "scalars": {"Wind_Intensity": 42}})
    check("T1730 the write succeeds", written.get("ok") is True, json.dumps(written)[:200])
    check("T1730 it reports one scalar applied", written.get("scalarsApplied") == 1, written)

    after_val, after_overridden = scalar_value(mi_path, "Wind_Intensity")
    check("T1730 the value genuinely changed on read-back", after_val == 42 and after_overridden is True,
          (after_val, after_overridden))

    after_tx = M.call("list_transactions", {"limit": 3})
    check("T1730 a real NEW transaction was recorded - currentIndex genuinely advanced, not a no-op "
          "the transaction system silently discarded",
          after_tx.get("currentIndex") == (before_index or 0) + 1,
          "before=%s after=%s" % (before_index, after_tx.get("currentIndex")))
    top_title = (after_tx.get("transactions") or [{}])[0].get("title", "")
    check("T1730 and it is genuinely titled for this call, not a generic/unrelated entry",
          "set_material_parameter" in top_title, top_title)

    # ------------------------------------------------------------------ T1731 the actual undo-correctness fix
    print("\n=== T1731: undo_transactions genuinely reverts the parameter - the property the old TODO said was broken ===")
    undone = M.call("undo_transactions", {"count": 1})
    check("T1731 undo succeeds", undone.get("ok") is True, json.dumps(undone)[:200])
    check("T1731 it reports undoing THIS call's transaction by title",
          "set_material_parameter" in json.dumps(undone.get("titlesUndone") or []),
          undone.get("titlesUndone"))

    reverted_val, reverted_overridden = scalar_value(mi_path, "Wind_Intensity")
    check("T1731 the parameter value genuinely reverted to the parent default - this is the whole "
          "point: the stale TODO claimed this never happens",
          reverted_val == 1 and reverted_overridden is False, (reverted_val, reverted_overridden))

    # ------------------------------------------------------------------ T1732 redo, both directions
    print("\n=== T1732: redo brings the change back - a full round trip, not just one direction ===")
    redone = M.call("redo_transactions", {"count": 1})
    check("T1732 redo succeeds", redone.get("ok") is True, json.dumps(redone)[:200])
    redone_val, redone_overridden = scalar_value(mi_path, "Wind_Intensity")
    check("T1732 the value is back to 42 after redo", redone_val == 42 and redone_overridden is True,
          (redone_val, redone_overridden))

    SC.confirm_call("delete_asset", {"path": mi_path})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
