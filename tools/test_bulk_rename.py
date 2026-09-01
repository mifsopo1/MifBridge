"""rename_asset renames[] - one IAssetTools pass, and why that is not just a loop.

WHAT A BATCH BUYS, and what this suite actually checks. IAssetTools::RenameAssets fixes up
REFERENCES to everything it is handed, as one operation. Renaming A and then B in two calls means a
reference from B to A is redirected while B is still moving; handing both to one call lets
AssetTools resolve the whole graph at once.

So the interesting assertion is not "two assets moved". It is that a material instance still points
at its parent material AFTER both were renamed in the same pass - which is the thing a loop of
single renames leaves a redirector trail behind for.

TWO TRAPS THIS ENDPOINT REFUSES RATHER THAN REPORTS:

  * two entries aiming at ONE destination. AssetTools answers that by UNIQUIFYING - you asked for
    NewName twice and get NewName and NewName1 - which returns true and is not what was asked.
  * a bad entry anywhere in the array. RenameAssets takes the whole array and returns ONE bool, so
    there is no per-entry failure to report and no partial rollback; the batch is refused whole,
    before the engine is touched.

AND THE POSTCONDITION IS PER ASSET. The single bool cannot say that every entry landed where it was
asked to, so each object is read back for where it ACTUALLY is and compared to what was requested.

Usage:  python tools/test_bulk_rename.py
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


def exists(path):
    r = M.call("find_assets", {"pathPrefix": path.rsplit("/", 1)[0]})
    for a in (r.get("assets") or []):
        p = str(a.get("objectPath") or a.get("path")).split(".")[0]
        if p == path:
            return True
    return False


def main():
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2
    accepted = M.call("describe_endpoint", {"endpoint": "rename_asset"}).get("acceptedParams") or []
    if "renames" not in accepted:
        print("skipped: rename_asset has no renames[] on this build")
        return 2

    st = int(time.time() % 100000)
    root = "/Game/_MifBulkRename%d" % st
    base = "%s/M_Base" % root
    inst = "%s/MI_Child" % root
    lone = "%s/M_Lone" % root

    # ------------------------------------------------------------------ R700 fixture
    print("=== R700: a material, an instance that REFERENCES it, and a spare ===")
    check("R700 (setup) base material", M.call("create_material", {"path": base}).get("ok") is True)
    mi = M.call("create_material_instance", {"path": inst, "parent": base})
    check("R700 (setup) a material instance pointing at it", mi.get("ok") is True,
          json.dumps(mi)[:200])
    check("R700 (setup) a third, unrelated asset",
          M.call("create_material", {"path": lone}).get("ok") is True)

    # ------------------------------------------------------------------ R701 refusals
    print("\n=== R701: refused whole, before the engine is touched ===")
    both = SC.confirm_call("rename_asset", {"path": lone, "newPath": lone + "X",
                                            "renames": [{"path": lone, "newPath": lone + "Y"}]})
    check("R701 renames[] together with path/newPath is refused",
          both.get("ok") is False and "not both" in str(both.get("error", "")),
          str(both.get("error"))[:200])
    check("R701 and the refused call renamed nothing", exists(lone), lone)

    # NO confirm and NO path. An empty batch is refused for being empty BEFORE the confirm gate,
    # which is what makes this reachable at all - scratch_confirm will not send confirm for a
    # payload with no scratch path in it, and there is no path to put in an empty array. The first
    # version of this check added `path` to get past that and was answered by the not-both refusal
    # instead, which is a pass for a cause it never reached.
    empty = M.call("rename_asset", {"renames": []})
    check("R701 an empty renames[] is refused for BEING empty, before confirm is even considered",
          empty.get("ok") is False and "empty" in str(empty.get("error", "")).lower(),
          str(empty.get("error"))[:180])

    noconfirm = M.call("rename_asset", {"renames": [{"path": lone, "newPath": lone + "Z"}]})
    check("R701 a batch without confirm is refused, saying so",
          noconfirm.get("ok") is False and "confirm" in str(noconfirm.get("error", "")),
          str(noconfirm.get("error"))[:180])
    check("R701 and that refusal renamed nothing either", exists(lone), lone)

    # EVERY CONFIRMED CALL GOES THROUGH scratch_confirm, and the first draft of this suite did not.
    # It passed {"confirm": True} to M.call, which STRIPS it - so three refusal checks below were
    # satisfied by "rename_asset requires confirm=true" and recorded as passes for causes they never
    # reached. Asserting only `ok is False` is what let that through, so each now asserts the words
    # of its OWN cause as well.
    ghost = SC.confirm_call("rename_asset", {"renames": [
        {"path": base, "newPath": "%s/M_BaseR" % root},
        {"path": "%s/NoSuchAsset" % root, "newPath": "%s/Whatever" % root}]})
    check("R701 ONE bad entry refuses the WHOLE batch, naming which entry",
          ghost.get("ok") is False and "renames[1]" in str(ghost.get("error", "")),
          str(ghost.get("error"))[:220])
    # THE CHECK THAT MATTERS FOR ALL-OR-NOTHING: the good entry beside it must not have moved.
    check("R701 and the GOOD entry beside it was not renamed - nothing was touched",
          exists(base) and not exists("%s/M_BaseR" % root), base)

    clash = SC.confirm_call("rename_asset", {"renames": [
        {"path": base, "newPath": "%s/M_Same" % root},
        {"path": lone, "newPath": "%s/M_Same" % root}]})
    check("R701 two entries aiming at ONE destination are refused, not uniquified",
          clash.get("ok") is False and "uniquif" in str(clash.get("error", "")).lower(),
          str(clash.get("error"))[:240])
    check("R701 and neither of those moved", exists(base) and exists(lone), [base, lone])

    badkey = M.call("rename_asset", {"assets": [base]})
    check("R701 `assets` is refused and names renames[] with its entry shape",
          badkey.get("ok") is False and "renames[]" in str(badkey.get("error", "")),
          str(badkey.get("error"))[:220])

    # ------------------------------------------------------------------ R702 the real batch
    print("\n=== R702: rename the material AND its instance in one pass ===")
    new_base = "%s/M_Renamed" % root
    new_inst = "%s/MI_Renamed" % root
    r = SC.confirm_call("rename_asset", {"renames": [
        {"path": base, "newPath": new_base},
        {"path": inst, "newPath": new_inst}]})
    check("R702 the batch succeeds", r.get("ok") is not False, json.dumps(r)[:260])
    check("R702 it reports both entries", len(r.get("results") or []) == 2,
          json.dumps(r.get("results"))[:250])
    check("R702 renamedExactly is 2 - read back per asset, not taken from the single bool",
          r.get("renamedExactly") == 2, json.dumps(r)[:260])
    check("R702 renamed is true only because every entry landed exactly",
          r.get("renamed") is True, json.dumps(r)[:200])
    check("R702 each result compares expectedPath against where it ACTUALLY is",
          all(x.get("exact") is True and x.get("expectedPath") == x.get("newPackageName")
              for x in (r.get("results") or [])),
          json.dumps(r.get("results"))[:300])
    check("R702 both assets are at their new paths", exists(new_base) and exists(new_inst),
          [exists(new_base), exists(new_inst)])
    check("R702 and neither is at its old path", not exists(base) and not exists(inst),
          [exists(base), exists(inst)])

    # THE POINT OF THE BATCH: the reference survived, pointing at the RENAMED parent.
    print("\n=== R703: the instance still points at its parent, by the NEW name ===")
    params = M.call("list_material_parameters", {"path": new_inst})
    parent = str(params.get("parent") or "")
    check("R703 the material instance resolves to the renamed parent, not a stale path",
          "M_Renamed" in parent and "M_Base" not in parent, parent[:300])

    # ------------------------------------------------------------------ cleanup
    print("")
    for p in (new_base, new_inst, lone):
        try:
            SC.confirm_call("delete_asset", {"path": p})
        except Exception as exc:
            print("  cleanup: %s" % str(exc)[:120])
    left = M.call("find_assets", {"pathPrefix": root}).get("count")
    if left:
        print("  NOTE  %s scratch asset(s) still held by in-memory handles; an editor restart"
              % left)
        print("        releases them. See the delete_asset blockedBy item in the spec.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
