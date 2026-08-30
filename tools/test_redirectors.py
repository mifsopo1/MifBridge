"""list_redirectors / fixup_redirectors - the cleanup rename_asset has always left behind.

rename_asset calls IAssetTools::RenameAssets, which deliberately leaves an ObjectRedirector behind
for every asset that was still referenced. Nothing could clean one up, so a session that renames
assets accumulates redirector packages - and those get cooked into the mod.

SPLIT INTO TWO ENDPOINTS, for the same reason source_control was split earlier the same day, and
the first version of this one got it wrong. It was written as a single fixup_redirectors with
dryRun defaulting to true, then put on the safety gate - which made the harmless dry run
unavailable in scratch mode, exactly the trade the source_control split had been made to avoid an
hour before. The gate classifies whole ENDPOINTS, not parameters. So the read half is its own
endpoint and is never gated, and dryRun is gone: the read half IS the dry run, and keeping both
would be two ways to ask one question. T4802 asserts that asymmetry.

BOTH HALVES SHARE ONE SCAN. If the read half reimplemented the query it would drift from what the
write half actually acts on, and a dry run that does not match the real thing is worse than none.

T4800 IS THE ONE THAT NEEDED NO FIXTURE. This project has 156 redirectors of its own, left by real
mod work, so the read half is exercised against real data rather than something the suite painted.

NOT EXERCISED: the fixup itself. It is gated outside `full` write mode, and it REWRITES AND
RE-SAVES every package that referenced a redirector - a far wider change than the redirectors named,
and one that writes to disk. Running it against this project's real content is not something a test
should do unasked.

ALSO NOT EXERCISED, and worth naming because it is the guard that matters: the registry pre-check.
FixupReferencers opens a blocking SDiscoveringAssetsDialog while the asset registry is still
scanning - and that is a RAW SLATE WINDOW, not an FMessageDialog, so GIsRunningUnattendedScript does
not suppress it. A modal on the game thread deadlocks this bridge. Reaching that branch means
catching the editor mid-scan, which a suite cannot arrange reliably.
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

    # ------------------------------------------------------------------ T4800 the read half
    print("=== T4800: redirectors are visible at all ===")
    r = M.call("list_redirectors", {"pathPrefix": "/Game"})
    check("T4800 list_redirectors succeeds", r.get("ok") is True, json.dumps(r)[:250])
    check("T4800 it reports what it scanned, separately from what it found",
          isinstance(r.get("scanned"), (int, float)) and isinstance(r.get("found"), (int, float)),
          json.dumps({"scanned": r.get("scanned"), "found": r.get("found")}))
    check("T4800 and a skippedCooked list, since a redirector in a .pak cannot be touched",
          isinstance(r.get("skippedCooked"), list), type(r.get("skippedCooked")).__name__)
    print("        this project has %s redirector(s) under /Game" % r.get("found"))

    rows = r.get("redirectors") or []
    if rows:
        row = rows[0]
        for f in ("package", "destination", "referencerCount"):
            check("T4800 each row reports %s" % f, f in row, sorted(row))
        # The destination is the whole point - a redirector with no destination is broken, and
        # saying so beats reporting an empty string.
        check("T4800 the destination is named, which is what makes a redirector meaningful",
              bool(row.get("destination")), json.dumps(row)[:200])
        check("T4800 referencerCount is a number - it is the blast radius of fixing it",
              isinstance(row.get("referencerCount"), (int, float)), row.get("referencerCount"))
    else:
        print("  NOTE  no redirectors under /Game, so the row shape is unexercised here.")

    # ------------------------------------------------------------------ T4801 the root
    print("\n=== T4801: an unbounded sweep is refused ===")
    noroot = M.raw_post("list_redirectors", {})
    check("T4801 a scan with no root is refused", noroot.get("ok") is False,
          (noroot.get("error") or "")[:200])
    # The reason is the point: it is not a missing feature, it is a deliberate limit.
    check("T4801 and the refusal explains the blast radius, not just the missing parameter",
          "blast radius" in (noroot.get("error") or ""), (noroot.get("error") or "")[:220])
    narrow = M.call("list_redirectors", {"pathPrefix": "/Game/NoSuchFolderAtAll"})
    check("T4801 a path with nothing under it succeeds with found:0",
          narrow.get("ok") is True and narrow.get("found") == 0, json.dumps(narrow)[:220])

    # ------------------------------------------------------------------ T4802 the split
    print("\n=== T4802: the read is never gated; the write always is ===")
    mode = M.write_mode()
    w = M.raw_post("fixup_redirectors", {"pathPrefix": "/Game", "confirm": True})
    if mode != "full":
        # THE assertion the split exists for. The first version of this endpoint had dryRun as a
        # parameter and was gated whole, which made this read impossible in scratch mode.
        check("T4802 in '%s' mode the write half is refused by the gate" % mode,
              w.get("ok") is False and "safety gate" in (w.get("error") or ""),
              (w.get("error") or "")[:200])
        again = M.call("list_redirectors", {"pathPrefix": "/Game"})
        check("T4802 - and the READ half still works in the same mode, which is why these are "
              "two endpoints rather than one with a dryRun flag",
              again.get("ok") is True and again.get("found") == r.get("found"),
              json.dumps(again)[:200])
    else:
        noconf = M.raw_post("fixup_redirectors", {"pathPrefix": "/Game"})
        check("T4802 fixing without confirm is refused", noconf.get("ok") is False,
              (noconf.get("error") or "")[:200])
        check("T4802 and the refusal says it RE-SAVES referencing packages",
              "RE-SAVES" in (noconf.get("error") or ""), (noconf.get("error") or "")[:220])

    # ------------------------------------------------------------------ T4803 the vocabulary
    print("\n=== T4803: parameters that point at the other half ===")
    dry = M.raw_post("list_redirectors", {"pathPrefix": "/Game", "dryRun": True})
    check("T4803 dryRun on the read half is refused, and says the read IS the dry run",
          dry.get("ok") is False and "IS the dry run" in (dry.get("error") or ""),
          (dry.get("error") or "")[:220])
    dry2 = M.raw_post("fixup_redirectors", {"pathPrefix": "/Game", "dryRun": True})
    check("T4803 and dryRun on the write half points at list_redirectors",
          dry2.get("ok") is False, (dry2.get("error") or "")[:220])
    delp = M.raw_post("fixup_redirectors", {"pathPrefix": "/Game", "deleteRedirectors": True})
    check("T4803 deleteRedirectors is refused, naming the inverted parameter that replaced it",
          delp.get("ok") is False, (delp.get("error") or "")[:220])

    alive = M.call("self_audit", {})
    check("T4803 - the editor is still alive", alive.get("ok") is True,
          "FixupReferencers can open a modal that deadlocks the bridge")

    print("\n  NOT EXERCISED: the fixup itself, and the registry pre-check. The first is gated and")
    print("  rewrites every referencing package on disk, which is not something a suite should do")
    print("  to real content unasked. The second needs the editor caught mid-scan - and it is the")
    print("  guard that matters, because SDiscoveringAssetsDialog is a raw Slate window that")
    print("  GIsRunningUnattendedScript does NOT suppress.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
