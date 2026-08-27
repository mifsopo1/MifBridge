"""Measure per-call bridge latency, and say whether the editor had focus - because that dominates it.

WHY THIS EXISTS. A change landed and the whole suite set got roughly three times slower. The obvious
reading was "the change did it", and that reading would have been wrong.

Handlers run inline in the editor's ticker, so per-call latency is essentially ONE TICK PERIOD. And
UE throttles Slate's tick rate when the editor is not the foreground window
(EditorPerformanceSettings::bThrottleCPUWhenNotForeground, on by default). A freshly launched editor
takes focus and is fast; the same editor twenty minutes later, behind a terminal, is several times
slower - with no code change involved at all.

So any before/after timing comparison across separate editor sessions is measuring focus as much as
it is measuring the code. This reports both numbers together, so the comparison is at least an honest
one.

Autosave is the other periodic distortion: once a session has accumulated dirty scratch packages, the
editor writes them out on a timer, and whatever call is in flight wears the stall. That shows up as
occasional huge outliers rather than a shifted middle, which is why the MEDIAN is the headline number
here and the max is reported beside it rather than folded into a mean.
"""
import sys
import time

import mifaudit as M

try:
    import subprocess
    FOREGROUND_PS = (
        "Add-Type 'using System;using System.Runtime.InteropServices;"
        "public class F{[DllImport(\"user32.dll\")]public static extern IntPtr GetForegroundWindow();"
        "[DllImport(\"user32.dll\")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);"
        "public static uint Pid(){uint p;GetWindowThreadProcessId(GetForegroundWindow(),out p);return p;}}';"
        "[F]::Pid()")
except Exception:
    FOREGROUND_PS = None


def foreground_pid():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", FOREGROUND_PS],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


def measure(endpoint="list_endpoints", payload=None, n=60):
    """Median/min/max seconds for n calls. Read-only endpoint, nothing is mutated."""
    samples = []
    for _ in range(n):
        t0 = time.time()
        try:
            M.call(endpoint, payload or {}, timeout=60)
        except Exception:
            continue
        samples.append(time.time() - t0)
    samples.sort()
    if not samples:
        return None
    mid = samples[len(samples) // 2]
    return {"n": len(samples), "median": mid, "min": samples[0], "max": samples[-1]}


def main():
    if not M.wait_for_bridge(timeout=600):
        print("bridge is not up")
        return 1
    ok, why = M.require_sdk_bridge(force=True)
    print("target: %s" % why)
    if not ok:
        return 2

    editor_pid = M.bridge_pid()
    fg = foreground_pid()
    focused = (fg is not None and editor_pid is not None and fg == editor_pid)
    print("editor pid %s, foreground pid %s -> the editor %s the foreground window"
          % (editor_pid, fg, "IS" if focused else "is NOT"))
    if not focused:
        print("  Expect several times the latency of a focused editor. UE throttles Slate's tick rate")
        print("  when the editor is in the background, and one tick is what a call waits for.")

    n = 60
    for arg in sys.argv[1:]:
        if arg.isdigit():
            n = int(arg)

    r = measure(n=n)
    if not r:
        print("no samples - every call failed")
        return 1
    print("")
    print("%d calls of list_endpoints" % r["n"])
    print("  median %6.1f ms   <- the headline; autosave outliers do not move it" % (r["median"] * 1000))
    print("  min    %6.1f ms" % (r["min"] * 1000))
    print("  max    %6.1f ms   <- an autosave or GC landing on one call" % (r["max"] * 1000))
    print("")
    print("Comparing two builds? Both editors must be in the SAME focus state, or you are measuring")
    print("the throttle. That is the mistake this file exists to stop repeating.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
