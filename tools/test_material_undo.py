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

    # A MATERIAL WITH A SCALAR PARAMETER, DISCOVERED. This used to hardcode a DDS2 master material
    # and assert Wind_Intensity started at exactly 1, so on any other project the setup failed and
    # main() returned 3 - reported as an ERROR rather than as "nothing here to test". Nothing this
    # suite asserts needs a particular material; it needs A material with A scalar parameter.
    #
    # /Engine/ content is preferred because it ships with every UE install, so this runs on a blank
    # project. Project content is the fallback rather than the assumption.
    parent, mparams = M.discover_material(require="scalar")
    param = default_val = None
    if parent:
        scalars = M.params_of_kind(mparams, "scalar")
        if scalars:
            param, default_val = scalars[0].get("name"), scalars[0].get("value")

    if not parent or param is None:
        print("SKIPPED - no material with a scalar parameter in this project, so there is nothing")
        print("  to drive set_material_parameter with. Nothing was verified.")
        return 0
    print("using %s, scalar parameter %r (default %r)" % (parent, param, default_val))

    mi_path = "/Game/_MifMaterialUndo/MI_UndoTest_%d" % st

    created = M.call("create_material_instance", {"parent": parent, "path": mi_path})
    check("(setup) a scratch material instance was created", created.get("ok") is True,
          json.dumps(created)[:200])
    if not created.get("ok"):
        return 3

    # A value that is DIFFERENT from the default, whatever the default turned out to be - writing
    # a parameter's existing value would make the read-backs below pass without the write working.
    test_val = 42.0 if default_val != 42.0 else 7.0

    before_val, before_overridden = scalar_value(mi_path, param)
    check("(setup) %s starts at the parent's default (%r), not overridden" % (param, default_val),
          before_val == default_val and before_overridden is False,
          (before_val, default_val, before_overridden))

    # ------------------------------------------------------------------ T1730 the write itself
    print("\n=== T1730: set_material_parameter genuinely registers a transaction ===")
    before_tx = M.call("list_transactions", {"limit": 1})
    before_index = before_tx.get("currentIndex")

    written = M.call("set_material_parameter", {"material": mi_path, "scalars": {param: test_val}})
    check("T1730 the write succeeds", written.get("ok") is True, json.dumps(written)[:200])
    check("T1730 it reports one scalar applied", written.get("scalarsApplied") == 1, written)

    after_val, after_overridden = scalar_value(mi_path, param)
    check("T1730 the value genuinely changed on read-back", after_val == test_val and after_overridden is True,
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

    reverted_val, reverted_overridden = scalar_value(mi_path, param)
    check("T1731 the parameter value genuinely reverted to the parent default - this is the whole "
          "point: the stale TODO claimed this never happens",
          reverted_val == default_val and reverted_overridden is False,
          (reverted_val, default_val, reverted_overridden))

    # ------------------------------------------------------------------ T1732 redo, both directions
    print("\n=== T1732: redo brings the change back - a full round trip, not just one direction ===")
    redone = M.call("redo_transactions", {"count": 1})
    check("T1732 redo succeeds", redone.get("ok") is True, json.dumps(redone)[:200])
    redone_val, redone_overridden = scalar_value(mi_path, param)
    check("T1732 the value is back to %r after redo" % test_val,
          redone_val == test_val and redone_overridden is True,
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
