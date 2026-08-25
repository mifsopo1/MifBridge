"""The modal-dialog fixes: duplicate_asset, rename_asset, delete_asset.

The sweep recorded duplicate_asset as a critical crasher. It was not a crash - it was a modal dialog.
Handlers run synchronously inline on the game thread, which is the same thread the HTTP server answers
on, so a modal stops the bridge answering anything at all and is indistinguishable from a crash from
outside.

Both duplicate_asset and rename_asset carried the comment "headless - no dialog". Choosing
IAssetTools::DuplicateAsset over DuplicateAssetWithDialog does pass bWithDialog=false, but that flag
only reaches the OVERWRITE prompt at the end. PerformDuplicateAsset calls CanCreateAsset first
(AssetTools.cpp:4287), which calls FMessageDialog::Open unconditionally. delete_asset had the same
shape: bShowConfirmation:false does not gate the dialog at ObjectTools.cpp:2833.

THE ASSERTION THAT MATTERS IS THAT THE CALL COMES BACK. A wrong answer is a bug; no answer is the
whole bridge down. Every check here is wall-clock bounded for that reason - if the guard regresses,
these time out rather than passing slowly.

The old dialog was also destructive: "If you click 'Yes', the existing object will be deleted." So
T72 proves the original asset SURVIVES a refused duplicate, not merely that the call returned.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []
BUDGET_S = 20.0     # generous for a busy editor, far under any human-answered modal


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def timed(endpoint, payload):
    """Call and return (elapsed, response). A modal would blow the timeout instead of returning."""
    t = time.time()
    try:
        r = M.call(endpoint, payload, timeout=int(BUDGET_S))
    except Exception as e:
        return time.time() - t, {"__transport_error": repr(e)}
    return time.time() - t, r


def exists(path):
    r = M.call("describe_package", {"path": path})
    return bool(r.get("ok"))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    stamp = int(time.time() % 100000)
    root = "/Game/_MifModal_%d" % stamp
    src = "%s/BP_Src" % root
    taken = "%s/BP_Taken" % root

    r = M.call("create_blueprint", {"path": src, "parentClass": "Actor"})
    r2 = M.call("create_blueprint", {"path": taken, "parentClass": "Actor"})
    if not (r.get("ok") and r2.get("ok")):
        print("setup failed:", json.dumps(r)[:200], json.dumps(r2)[:200])
        return 3
    print("scene: %s and %s" % (src, taken))

    # ------------------------------------------------------------------ T70 the reported freeze
    print("\n=== T70: duplicating ONTO an existing asset answers instead of opening a dialog ===")
    dt, resp = timed("duplicate_asset", {"path": src, "newPath": taken})
    print("   %.2fs  %s" % (dt, json.dumps(resp)[:240]))
    check("T70 it came back at all", "__transport_error" not in resp,
          "no answer within %.0fs - the modal guard is not working: %s" % (BUDGET_S, resp))
    check("T70 it came back promptly", dt < BUDGET_S, "%.1fs" % dt)
    check("T70 it refused rather than overwriting", resp.get("ok") is False, json.dumps(resp)[:220])
    check("T70 the refusal names the real reason",
          "already taken" in (resp.get("error") or "").lower(),
          (resp.get("error") or "")[:200])

    # ------------------------------------------------------------------ T71 nothing was destroyed
    print("\n=== T71 [the destructive part]: the existing asset SURVIVES the refusal ===")
    # The dialog this replaced said "If you click 'Yes', the existing object will be deleted".
    check("T71 the destination still exists", exists(taken), taken)
    check("T71 the source still exists", exists(src), src)

    # ------------------------------------------------------------------ T72 the happy path still works
    print("\n=== T72: an ordinary duplicate still succeeds ===")
    fresh = "%s/BP_Copy" % root
    dt, resp = timed("duplicate_asset", {"path": src, "newPath": fresh})
    print("   %.2fs  %s" % (dt, json.dumps(resp)[:200]))
    check("T72 duplicated", resp.get("ok") is True, json.dumps(resp)[:220])
    check("T72 the copy is really there", exists(fresh), fresh)

    # ------------------------------------------------------------------ T73 rename
    # HONEST LIMITATION: rename_asset and delete_asset both require confirm=true, and these audits
    # never send it. T73/T74 therefore stop at the confirm gate and DO NOT reach
    # AssetTools.RenameAssets or ObjectTools::DeleteAssets - they do not exercise the modal guard at
    # all. What they prove is that the refusal path answers promptly, which is worth having but is
    # not the guard. The guard on those two is covered statically by tools/audit_modals.py, which
    # checks both that each call site sits inside the TGuardValue scope and that the engine lines
    # cited as proof those APIs can prompt still say what they are quoted as saying.
    print("\n=== T73: rename_asset answers promptly (confirm gate - does NOT reach the guard) ===")
    dt, resp = timed("rename_asset", {"path": fresh, "newPath": taken})
    print("   %.2fs  %s" % (dt, json.dumps(resp)[:240]))
    check("T73 the confirm refusal came back at all", "__transport_error" not in resp,
          "no answer within %.0fs: %s" % (BUDGET_S, resp))
    check("T73 the confirm refusal came back promptly", dt < BUDGET_S, "%.1fs" % dt)
    check("T73 it did not silently clobber the target", exists(taken), taken)

    # ------------------------------------------------------------------ T74 delete refusal path
    print("\n=== T74: delete_asset answers promptly (confirm gate - does NOT reach the guard) ===")
    # Not a real delete: confirm:true is never sent by these audits. This exercises the refusal path,
    # which is the one that reaches ObjectTools and therefore the ungated dialog.
    dt, resp = timed("delete_asset", {"path": "%s/NoSuchAsset" % root})
    print("   %.2fs  %s" % (dt, json.dumps(resp)[:200]))
    check("T74 the confirm refusal came back at all", "__transport_error" not in resp,
          "no answer within %.0fs: %s" % (BUDGET_S, resp))
    check("T74 the confirm refusal came back promptly", dt < BUDGET_S, "%.1fs" % dt)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("NOTE: T70-T72 exercise the real guard (duplicate_asset needs no confirm). T73/T74 stop at")
    print("the confirm gate - audit_modals.py is what covers those two.")
    print("scratch left under %s - nothing here is saved, and removing it would mean" % root)
    print("sending confirm:true, which these audits do not do.")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
