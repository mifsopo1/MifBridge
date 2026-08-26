"""Run every tools/test_*.py against the live editor and summarise.

Sequential on purpose: they all drive the same editor, and two suites creating scratch assets at once
would interleave in ways that make a failure impossible to attribute.

Relaunches the editor if a suite kills it, so one crash does not abort the whole run - and RECORDS
that it had to, because a suite that takes the editor down is the most important thing in the report.
"""
import glob
import json
import os
import subprocess
import sys
import time

import mifaudit as M

TIMEOUT = 900


def main():
    suites = sorted(os.path.basename(p) for p in glob.glob(os.path.join(os.path.dirname(__file__) or ".", "test_*.py")))
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    if only:
        suites = [s for s in suites if any(o in s for o in only)]
    results = []
    print("running %d suites\n" % len(suites))
    for name in suites:
        if not M.wait_for_bridge(timeout=600):
            M.launch_editor()
            M.wait_for_bridge(timeout=900)
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, name], capture_output=True, text=True,
                               timeout=TIMEOUT, cwd=os.path.dirname(__file__) or ".")
            out = (r.stdout or "") + (r.stderr or "")
            rc = r.returncode
        except subprocess.TimeoutExpired:
            out, rc = "TIMEOUT after %ds" % TIMEOUT, -99
        dt = time.time() - t0
        line = next((l for l in out.splitlines() if l.startswith("PASS ")), "")
        alive = M.bridge_responsive()
        if not alive:
            # A suite that takes the editor down is the headline of the report.
            M.launch_editor()
            M.wait_for_bridge(timeout=900)
        results.append({"suite": name, "rc": rc, "summary": line.strip(),
                        "seconds": round(dt, 1), "editorSurvived": alive,
                        "tail": "\n".join(out.splitlines()[-25:]) if rc != 0 else ""})
        print("  %-34s rc=%-4s %-22s %5.1fs%s" % (name, rc, line.strip(), dt,
                                                  "" if alive else "   EDITOR DIED"))
    with open("suite_results.json", "w") as f:
        json.dump(results, f, indent=1)
    bad = [r for r in results if r["rc"] != 0]
    died = [r for r in results if not r["editorSurvived"]]
    print("\n" + "=" * 72)
    print("%d suites, %d failed, %d took the editor down" % (len(results), len(bad), len(died)))
    for r in bad:
        print("\n--- %s (rc=%s) ---" % (r["suite"], r["rc"]))
        print(r["tail"][-1200:])
    print("=" * 72)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
