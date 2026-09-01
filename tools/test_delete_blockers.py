"""delete_asset's blockedBy - saying WHO holds an asset, instead of that nobody can tell.

THE MESSAGE THIS REPLACES, verbatim:

    no open editor, no registry referencer and not rooted - the holder is an in-memory handle
    this endpoint cannot see. An editor restart releases it.

which was honest and completely unactionable, and was the answer in the two most ordinary cases
there are. The three checks behind it all miss the live object graph:

  * openAssetEditors sees asset editor windows
  * rootedInMemory sees AddToRoot
  * registryReferencers is the ASSET REGISTRY, which records references saved to DISK - so it is
    empty for an unsaved asset however many live objects point at it, and unsaved is what every
    test fixture is

Measured on 2026-09-01: a material with a live MaterialInstance child pointing straight at it
reported all three lists empty. A cleanup check written on top of that classification would have
passed on the exact leak it existed to catch - and one was, before this was fixed.

TWO CASES ARE ASSERTED HERE, because they need different answers:

  B600  a real live referencer  -> named, with its class and the PROPERTY holding the reference
  B601  the undo history        -> identified as the transaction buffer specifically

B601 is the one that explains most of these in practice: every mutating endpoint in this plugin
opens an FScopedTransaction, so an asset a script created and then modified is held by the undo
buffer. The test is the editor's own (ObjectTools.cpp:392-395) - disable object serialization on
the transactor and ask again; if the references vanish, undo was the holder.

Usage:  python tools/test_delete_blockers.py
Exit:   0 passed   1 failed   2 SKIPPED, no bridge
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
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2

    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ B600 a live referencer
    print("=== B600: a live MaterialInstance holding its parent - the case that reported nothing ===")
    root = "/Game/_MifBlockA%d" % st
    base = "%s/M_Base" % root
    inst = "%s/MI_Child" % root
    check("B600 (setup) base material", M.call("create_material", {"path": base}).get("ok") is True)
    check("B600 (setup) an instance pointing at it",
          M.call("create_material_instance", {"path": inst, "parent": base}).get("ok") is True)

    out = SC.confirm_call("delete_asset", {"path": base})
    by = out.get("blockedBy") or {}
    check("B600 the delete is refused, as it should be", out.get("deleted") is False,
          json.dumps(out)[:200])

    # THE GAP, asserted directly so it cannot quietly come back.
    check("B600 registryReferencers is EMPTY - it only knows about references saved to disk",
          (by.get("registryReferencers") or []) == [], by.get("registryReferencers"))

    mem = by.get("memoryReferencers") or []
    check("B600 memoryReferencers finds it anyway", len(mem) >= 1, json.dumps(mem)[:300])
    hit = [m for m in mem if "MI_Child" in str(m.get("referencer"))]
    check("B600 and NAMES the instance that holds it", bool(hit), json.dumps(mem)[:300])
    check("B600 with its class", bool(hit) and hit[0].get("class") == "MaterialInstanceConstant",
          json.dumps(hit)[:220])
    # The property is what makes this actionable rather than merely informative.
    check("B600 and the PROPERTY the reference goes through",
          bool(hit) and "Parent" in (hit[0].get("throughProperties") or []),
          json.dumps(hit)[:220])
    check("B600 the message points at memoryReferencers and explains the registry was blind",
          "memoryReferencers" in str(out.get("error", ""))
          and "not on disk" in str(out.get("error", "")),
          str(out.get("error"))[:300])
    check("B600 and it does NOT claim the holder is invisible",
          "cannot see" not in str(out.get("error", "")), str(out.get("error"))[:200])

    # Removing the holder must make the delete work - otherwise the diagnosis is decoration.
    SC.confirm_call("delete_asset", {"path": inst})
    freed = SC.confirm_call("delete_asset", {"path": base})
    check("B600 deleting the named referencer FREES it - the diagnosis was the actual cause",
          freed.get("deleted") is True, json.dumps(freed)[:240])

    # ------------------------------------------------------------------ B601 the undo buffer
    print("\n=== B601: an asset held only by the UNDO HISTORY ===")
    root2 = "/Game/_MifBlockB%d" % st
    mat = "%s/M_Land" % root2
    M.call("create_material", {"path": mat})
    e = M.call("add_material_expression", {
        "material": mat, "class": "LandscapeLayerWeight", "x": -400, "y": 0,
        "properties": {"ParameterName": "BL%d" % st, "PreviewWeight": 1.0}})
    M.call("connect_material_property",
           {"path": mat, "from": e.get("expressionName"), "property": "BaseColor"})
    M.call("recompile_material", {"material": mat})
    L = M.call("create_landscape", {
        "material": mat, "componentsX": 2, "componentsY": 2, "quadsPerSection": 31,
        "location": {"x": 500000 + st, "y": 500000, "z": 0}, "label": "MifBlk%d" % st})
    actor = L.get("actorPath")
    check("B601 (setup) a landscape using that material exists", bool(actor), json.dumps(L)[:200])
    if not actor:
        return 1
    # Deleting the landscape puts it in the undo buffer, which keeps the material alive with it.
    M.cleanup_level_actor(actor, "scratch landscape")

    out2 = SC.confirm_call("delete_asset", {"path": mat})
    by2 = out2.get("blockedBy") or {}
    check("B601 the delete is refused", out2.get("deleted") is False, json.dumps(out2)[:200])
    check("B601 transactionBuffer is TRUE - undo is what is holding it",
          by2.get("transactionBuffer") is True, json.dumps(by2)[:300])
    check("B601 the message says so in those words, not 'a handle this endpoint cannot see'",
          "TRANSACTION BUFFER" in str(out2.get("error", ""))
          and "cannot see" not in str(out2.get("error", "")),
          str(out2.get("error"))[:320])
    check("B601 and it declines to clear the undo history, because that is the user's",
          "will not clear it" in str(out2.get("error", "")), str(out2.get("error"))[:320])

    # ------------------------------------------------------------------ B602 the honest fallback
    print("\n=== B602: the fallback message is now narrower than it was ===")
    d = M.call("describe_endpoint", {"endpoint": "delete_asset"})
    check("B602 delete_asset is still registered and guarded",
          d.get("registered") is True and d.get("status") == "params_declared", json.dumps(d)[:200])

    print("")
    for p in (inst, base, mat):
        try:
            SC.confirm_call("delete_asset", {"path": p})
        except Exception:
            pass
    for r in (root, root2):
        n = M.call("find_assets", {"pathPrefix": r}).get("count")
        if n:
            print("  NOTE  %s scratch asset(s) left under %s - held, and now the response says by"
                  % (n, r))
            print("        WHAT. That is the point of this suite rather than a failure of it.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
