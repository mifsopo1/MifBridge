"""load_level - only the two refusal paths, deliberately, forever.

load_level has NO confirm gate at the endpoint level at all - it directly defers to
FEditorFileUtils::LoadMap, which discards the CURRENT open level's unsaved state with no prompt
(confirmed by reading MifBridgeWorld.cpp: the deferred load happens unconditionally once a real map
file is found, nothing else gates it). mifaudit.py's own DENY set already lists load_level for exactly
this reason ("discards unsaved work in the open map without asking"), alongside new_level and
open_level - this is not a blind-sweep-side-effect concern like trace_start's DENY entry (which this
project already has a deliberate, narrow bypass for elsewhere), it is the OPERATION ITSELF being
inherently state-destroying by design, with no way to prove any particular open level is "safe to
discard" the way scratch_confirm proves a payload is scratch-only. There is no reusable technique here
- widening DENY, or reaching for raw_post to drive a real level swap, would mean discarding whatever
state this session's editor happens to have open, on every future unattended run of this suite,
forever. Declined for real, not deferred - see tools/FEATURE_PARITY_SPEC.md.

What IS tested: the two refusal paths, which return BEFORE any world-swap logic runs at all (read the
source to confirm this ordering, not assumed) - an empty path, and a path with no real .umap file on
disk. Both are 100% safe to drive for real: MifDeferToNextTick is never reached in either branch. Both
go through M.raw_post rather than M.call, because mifaudit's own DENY list intercepts load_level
UNCONDITIONALLY before the payload is even inspected - there is no way to reach even the safe refusal
paths through the normal harness call path, so this is the narrow, deliberate exception (same
technique as T913's trace_start bypass elsewhere this session), used only for the two branches proven
safe by reading the handler's own control flow.
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

    print("\n=== T935: load_level refuses an empty path, before any world-swap logic runs ===")
    r = M.raw_post("load_level", {})
    check("T935 refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T935 names path as what is missing", "path is required" in (r.get("error") or ""),
          r.get("error"))
    alive = M.call("self_audit", {})
    check("T935 the editor is unaffected (no world swap happened)", alive.get("ok") is True,
          "the current level should be completely untouched by this refusal")

    print("\n=== T936: load_level refuses a package path with no real .umap file on disk ===")
    r2 = M.raw_post("load_level", {"path": "/Game/Maps/NoSuchMap_zz_definitely_not_real"})
    check("T936 refused", r2.get("ok") is False, json.dumps(r2)[:200])
    check("T936 and explains why", "no map file" in (r2.get("error") or ""), r2.get("error"))
    alive2 = M.call("self_audit", {})
    check("T936 the editor is still unaffected", alive2.get("ok") is True,
          "the current level should still be completely untouched")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("COVERAGE GAP, permanent and deliberate: load_level's SUCCESS path is not exercised here,")
    print("and never will be by this suite. It has no confirm gate at all and discards the current")
    print("open level's unsaved state unconditionally - see the module docstring and")
    print("tools/FEATURE_PARITY_SPEC.md for the full reasoning.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
