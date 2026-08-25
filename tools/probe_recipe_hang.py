"""Isolate the recipe_reset_and_loop hang against an IDLE editor, and bisect it.

Run 4 recorded HANG on the absurd probe with every parameter set to '\\x00\\x01\\x02'. HANG and not
CRASH, so the bridge was alive on the confirming re-check - it stopped answering that one call.

The reason this needs isolating rather than believing: handlers run synchronously inline on the game
thread, so a "hang" as the fuzzer measures it is a CLIENT-side timeout and can simply mean the editor
was busy behind an earlier expensive call. Run 4 spent most of its wall clock in the kr_* band doing
real blueprint reconstruction, and recipe_reset_and_loop sits after it alphabetically. So the first
question is not "which line blocks" but "does this reproduce at all when nothing else is running".

A static pass already ruled out the obvious candidates:
  * not "treated as empty" - the probe tries "" FIRST and that does not hang, so the control-char
    string gets FURTHER, not less far. Len()==3 passes every IsEmpty() guard while *Str is empty as a
    C string.
  * not a graph-resolution scan - ResolveGraphField sees IsEmpty() false and calls ResolveGraph, which
    Splits on "::" and returns immediately when that fails.
  * not response truncation - FJsonSerializer escapes control characters, so the body carries the six
    characters \\u0000 and never a raw NUL; Content-Length and payload agree.

Each probe is timed and followed by a liveness check, so a hang is distinguished from a slow answer
and from a dead editor.
"""
import json
import sys
import time
import urllib.error
import urllib.request

import mifaudit as M

CTRL = "\x00\x01\x02"
TIMEOUT = 25


def raw(endpoint, payload, timeout=TIMEOUT):
    """Deliberately NOT mifaudit.call - this needs the timing and must not raise past the caller."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/%s" % (M.BRIDGE_PORT, endpoint), data=body,
        headers={"X-Mif-Token": "dev", "Content-Type": "application/json"})
    t = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return time.time() - t, json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return time.time() - t, {"__no_answer": type(e).__name__}


def alive():
    t = time.time()
    dt, r = raw("self_audit", {}, timeout=10)
    return ("__no_answer" not in r), dt


def attempt(label, payload):
    dt, r = raw("recipe_reset_and_loop", payload)
    hung = "__no_answer" in r
    print("  %-42s %6.2fs  %s" % (label, dt, "NO ANSWER" if hung else json.dumps(r)[:110]))
    if hung:
        ok, adt = alive()
        print("       liveness after: %s (%.2fs)" % ("alive - it was that CALL" if ok else "DEAD", adt))
    return hung


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    ok, dt = alive()
    print("editor idle and answering in %.2fs\n" % dt if ok else "editor not answering to begin with\n")
    if not ok:
        return 1

    keys = ["graphId", "arrayVar", "indexVar", "scoreVar", "indexInit", "scoreInit",
            "afterNode", "afterPin", "x", "y"]

    print("=== 1. the exact run-4 payload: every parameter set to the control string ===")
    full = {k: CTRL for k in keys}
    reproduced = attempt("all params = '\\x00\\x01\\x02'", full)

    print("\n=== 2. the control it must be compared against: every parameter empty ===")
    # Run 4 tried "" first and it did NOT hang. If "" hangs here too, the trigger is not the control
    # characters at all and the whole framing was wrong.
    attempt("all params = ''", {k: "" for k in keys})

    if not reproduced:
        print("\nDID NOT REPRODUCE against an idle editor.")
        print("That is a real result, not a failure: it points at the client-side-timeout")
        print("explanation - the editor was busy behind the kr_* band when run 4 probed this.")
        print("Re-check by timing the endpoint immediately after a kr_* call rather than in isolation.")
        return 0

    print("\n=== 3. bisect: one parameter at a time carries the control string ===")
    for k in keys:
        payload = {j: ("" if j != k else CTRL) for j in keys}
        if attempt("only %s = control" % k, payload):
            print("\n  ^ that parameter alone reproduces it.")
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
