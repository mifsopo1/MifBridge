"""read_engine_log: tail this editor process's own Output Log (Saved/Logs/<Project>.log).

Built 2026-08-29, closing a real, concrete gap found the previous night during the PIE-family sweep:
diagnosing why move_actor_to's target pawn never moved required triangulating the cause from
list_pie_actors and engine source, because there was no way to just read the actual
FMessageLog("PIE") warning UAIBlueprintHelperLibrary::SimpleMoveToLocation calls directly - it would
have named the real cause outright. This endpoint exists so that investigation is one call next time.

Different from the existing read_modloader_log (MifBridgePipeline.cpp, same file): that one tails an
EXTERNAL log (UE4SS.log, a packaged-game runtime file that usually does not even exist in this SDK
editor). This one always tails THIS PROCESS'S OWN live Output Log - guaranteed to exist and be growing
the whole time the editor is up, since it is the same file every UE_LOG call in the whole engine and
project writes to, including FMessageLog entries (they mirror to the regular log by default).

A REAL BUG CAUGHT BY THE COMPILER ON THE FIRST BUILD ATTEMPT, both engines: the path was built as
`FPaths::ProjectLogDir() / (FApp::GetProjectName() + TEXT(".log"))` - FApp::GetProjectName() returns a
raw `const TCHAR*`, not an FString, so `+ TEXT(".log")` was literal POINTER ARITHMETIC between two
pointers, not string concatenation (MSVC C2110, "cannot add two pointers"). Fixed by wrapping it in
FString() first. Both engines "completed" with exit code 0 on the failing attempt and both actually
printed `Result: Failed (OtherCompilationError)` - the same lying-exit-code trap this whole project's
buildcheck.py exists to catch, caught again live rather than assumed fixed.

T1700: the log is found and has real content - this should never be "not found" the way
read_modloader_log legitimately can be, since it is reading the very process answering the call.

T1701: filtering for this exact editor session's own real startup line ("MifBridge listening on") finds
it even though the log has grown well past a 200-line tail since then - proving the filter is applied
to the WHOLE file before the tail cut, not after (the same read_modloader_log pattern this file reuses
deliberately, verified rather than assumed carried over correctly).

T1702: the lines parameter genuinely clamps the response size - asking for 3 returns at most 3, not the
whole matched set.

T1703: path is NOT an accepted parameter here (unlike read_modloader_log, which reads an arbitrary
file) - refused by name, with the refusal explaining WHY (there is only one such log for a running
process) rather than a generic "unrecognised parameter".

T1704: the path the endpoint reports really is a real file on disk, independently confirmed via a
plain filesystem check rather than trusted from found:true alone.
"""
import json
import os
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

    print("\n=== T1700: read_engine_log finds this process's own live Output Log ===")
    basic = M.call("read_engine_log", {"lines": 50})
    check("T1700 succeeds", basic.get("ok") is True, json.dumps(basic)[:200])
    check("T1700 found:true - this should never be 'not found', it is reading THIS process's own log",
          basic.get("found") is True, basic)
    check("T1700 reports a real, non-trivial matched count", basic.get("matched", 0) > 0, basic.get("matched"))
    check("T1700 reports real line content, not an empty array",
          len(basic.get("lines", [])) > 0, basic.get("lines"))

    print("\n=== T1701: filtering finds this session's own real startup line, even scrolled past the tail ===")
    startup = M.call("read_engine_log", {"lines": 10, "filter": "MifBridge listening on"})
    check("T1701 succeeds", startup.get("ok") is True, json.dumps(startup)[:200])
    check("T1701 the filter genuinely matched at least one real line - not silently zero",
          startup.get("matched", 0) >= 1, startup)
    check("T1701 the returned line really contains the filter text (not a coincidental match)",
          any("MifBridge listening on" in ln for ln in startup.get("lines", [])), startup.get("lines"))

    print("\n=== T1702: the lines parameter genuinely clamps the response size ===")
    small = M.call("read_engine_log", {"lines": 3})
    check("T1702 succeeds", small.get("ok") is True, json.dumps(small)[:200])
    check("T1702 returned at most 3 lines even though matched is almost certainly larger",
          len(small.get("lines", [])) <= 3, small.get("lines"))
    check("T1702 matched still reports the REAL total, not clamped to 3",
          small.get("matched", 0) > 3, small.get("matched"))

    print("\n=== T1703: path is refused by name, not silently ignored ===")
    bad_path = M.call("read_engine_log", {"path": "C:/Windows/win.ini"})
    check("T1703 passing 'path' is refused", bad_path.get("ok") is False, bad_path)
    check("T1703 the refusal explains WHY (this always reads the current process's own log)",
          "own" in (bad_path.get("error") or "").lower(), bad_path.get("error"))

    print("\n=== T1704: the reported path is a real file on disk, independently confirmed ===")
    real_path = basic.get("path", "")
    check("T1704 a path was reported at all", bool(real_path), basic)
    if real_path:
        check("T1704 the file genuinely exists on disk - not just found:true from the endpoint",
              os.path.isfile(real_path), real_path)
        check("T1704 the file has real, non-trivial size", os.path.getsize(real_path) > 1000, real_path)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
