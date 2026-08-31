"""Run every tools/test_*.py against the live editor, TWICE, and summarise.

Sequential on purpose: they all drive the same editor, and two suites creating scratch assets at once
would interleave in ways that make a failure impossible to attribute.

TWICE BY DEFAULT, and that is the point of this runner rather than a nicety. Five suites were broken
in one night by the same underlying thing: state surviving between runs inside one editor session.
Unsaved scratch assets live until the process ends, so a suite that hardcodes a scratch path creates
it on run one and dies in setup on run two, and one that pages its own results falls off the end once
enough have piled up. Every one of them was green on a single pass and had been for weeks - the set had
simply never been run twice without a restart in between, which is exactly what an unattended overnight
run does.

The two passes INTERLEAVE - every suite once, then every suite again - rather than running each suite
twice back to back. That matters: it is often another suite's leftovers that break a suite, not only
its own, and back-to-back runs would miss that.

--once skips the second pass for a quick check. A suite that has only ever been run with --once is not
known to work.

The editor is relaunched if a suite kills it, and that is RECORDED, because a suite that takes the
editor down is the headline of the report rather than a footnote.
"""
import glob
import io
import json
import os
import re
import subprocess
import sys
import time

import mifaudit as M

TIMEOUT = 900


def main():
    here = os.path.dirname(__file__) or "."
    suites = sorted(os.path.basename(p) for p in glob.glob(os.path.join(here, "test_*.py")))

    # PIE SUITES ARE SKIPPED UNLESS ASKED FOR. Starting PIE makes the bridge stop answering while
    # the editor stays alive (seen twice on 2026-08-30, with LogPlayLevel in the editor log and
    # connection refused on 8791 immediately after), so an unattended sweep never gets past one.
    # That is why 42 of 144 suites had never been in a full sweep: the sweep did not finish.
    #
    # DERIVED, NOT HARDCODED. A suite that mentions start_pie starts PIE. A hand-kept list is one
    # forgotten entry away from hanging the sweep again - the same drift that left five finished
    # items sitting in the backlog and a factory table 44% incomplete on the same day.
    # An INVOCATION, not a mention. A plain "start_pie" substring matched five suites when only
    # three start PIE: test_safety_gate CALLS it deliberately but its own harness blocks the call -
    # that is what the suite is for - and test_uncovered_reads5 only names it in comments. Skipping
    # those two traded a hang for a silent coverage hole, which is no better.
    pie_call = re.compile(r"(?:raw_post|confirm_call|\bcall)\s*\(\s*[\"']start_pie[\"']")
    pie_suites = []
    for name in suites:
        try:
            with io.open(os.path.join(here, name), "r", encoding="utf-8", errors="ignore") as fh:
                body = fh.read()
        except OSError:
            continue
        if not pie_call.search(body):
            continue
        # A suite whose harness blocks the call never actually enters PIE.
        if "HARNESS_BLOCKED" in body and "start_pie" in body.split("HARNESS_BLOCKED")[1][:200]:
            continue
        pie_suites.append(name)
    if pie_suites and "--with-pie" not in sys.argv:
        suites = [n for n in suites if n not in pie_suites]
        print("SKIPPING %d PIE suite(s): %s" % (len(pie_suites), ", ".join(pie_suites)))
        print("  Starting PIE stops the bridge answering while the editor stays alive, which hangs")
        print("  an unattended sweep. Run them attended with --with-pie. NOT verified by this run.")
        print("")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    passes = 1 if "--once" in sys.argv else 2
    if args:
        # A FILTER THAT MATCHES NOTHING IS AN ERROR, NOT AN EMPTY PASS. Every argument that does not
        # start with -- is a suite-name substring, so a mistyped flag VALUE lands here as a filter:
        # `--passes 2` filters on "2", matches no suite, and the runner then prints
        #   0 run(s) across 0 suites, 0 failed, 0 took the editor down
        # which reads exactly like a clean regression. That happened on 2026-08-26 and a batch of six
        # fixes was momentarily believed to be verified when nothing had run at all. Same shape as the
        # Build.bat postmortem: a success report from a step that never executed.
        wanted = [s for s in suites if any(a in s for a in args)]
        if not wanted:
            print("no suite matches %s" % (args,))
            print("(if you meant a flag, note only --once and --help are flags; everything else is"
                  " treated as a suite-name filter)")
            return 2
        suites = wanted

    # Claim the editor for the duration. See mifaudit.warn_if_sweep_running for why: a second process
    # driving the same editor corrupts THIS run's results, not just its own, because the undo buffer
    # is one stack for the whole editor.
    try:
        with open(M.SWEEP_LOCK, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    os.environ["MIF_SWEEP"] = str(os.getpid())   # inherited by every suite launched below, so they
                                                 # are exempt from their own interlock by construction

    results = []
    note = " - the second pass is what catches state surviving between runs" if passes > 1 else ""
    print("running %d suites, %d pass(es) each%s" % (len(suites), passes, note))
    print("")

    ordered = [(name, p) for p in range(1, passes + 1) for name in suites]
    for name, which in ordered:
        if not M.wait_for_bridge(timeout=600):
            M.launch_editor()
            M.wait_for_bridge(timeout=900)
        # NAMED BEFORE IT RUNS, not after. The result line is printed when a suite FINISHES, so a
        # suite that hangs produces no line at all and the log simply stops - which is how a
        # 568-second stall in test_transactions looked like nothing happening, and cost a round of
        # process-hunting to attribute. flush because a stalled run is exactly when the buffer will
        # not be flushed for you.
        print("  [%d] %-32s running..." % (which, name), flush=True)
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, name], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=TIMEOUT, cwd=here)
            out = (r.stdout or "") + (r.stderr or "")
            rc = r.returncode
        except subprocess.TimeoutExpired:
            out, rc = "TIMEOUT after %ds" % TIMEOUT, -99
            print("  [%d] %-32s TIMED OUT after %ds - killed. The suite hung; the editor may be "
                  "fine. Run it standalone to see whether it needs a full run's accumulated state."
                  % (which, name, TIMEOUT), flush=True)
        dt = time.time() - t0
        line = next((l for l in out.splitlines() if l.startswith("PASS ")), "")
        # BUSY IS NOT DEAD. A timeout here means the editor is alive and its game thread is
        # occupied - the bridge runs every endpoint on that thread - so relaunching starts a SECOND
        # editor beside a working one and both race for the port. That hung a 288-run sweep.
        state = M.bridge_liveness()
        alive = state == "alive"
        if state == "busy":
            print("  ... bridge busy (listening, not answering). The editor's game thread is "
                  "occupied; waiting rather than relaunching.", flush=True)
            alive = M.wait_for_bridge(timeout=900)
        elif state == "dead":
            M.launch_editor()
            alive = M.wait_for_bridge(timeout=900)
        results.append({"suite": name, "pass": which, "rc": rc, "summary": line.strip(),
                        "seconds": round(dt, 1), "editorSurvived": alive,
                        # WHEN THIS SUITE RAN. audit_suite_reach decides whether a record still
                        # describes the code by comparing it against the suite's source, and without
                        # this it can only compare against suite_results.json's MTIME - which is not
                        # a content age. Copying a backup over that file moves the mtime without
                        # changing a single record, and hours-old results then read as current.
                        # Wrong in the direction that gets believed rather than re-checked.
                        "ranAt": time.time(),
                        "tail": "\n".join(out.splitlines()[-25:]) if rc != 0 else ""})
        print("  [%d] %-32s rc=%-4s %-22s %5.1fs%s"
              % (which, name, rc, line.strip(), dt, "" if alive else "   EDITOR DIED"))

    try:
        os.remove(M.SWEEP_LOCK)
    except Exception:
        pass

    with open(os.path.join(here, "suite_results.json"), "w") as f:
        json.dump(results, f, indent=1)

    # EXIT CODE 2 MEANS SKIPPED, NOT FAILED, and the distinction is the whole reason a suite bothers to
    # return it. test_blender_ops cannot run without Blender listening, which it usually is not. A
    # suite that quietly returns 0 when it verified nothing is the worst option - it manufactures
    # confidence - and one that returns 1 is noise that trains everyone to ignore a red line.
    #
    # So skipped is counted and REPORTED separately. "62 passed, 1 skipped" is an honest sweep;
    # "63 passed" and "1 failed" are both lies in different directions.
    skipped = [r for r in results if r["rc"] == 2]
    bad = [r for r in results if r["rc"] not in (0, 2)]
    died = [r for r in results if not r["editorSurvived"]]
    print("")
    print("=" * 72)
    print("%d run(s) across %d suites, %d failed, %d skipped, %d took the editor down"
          % (len(results), len(suites), len(bad), len(skipped), len(died)))
    for r in skipped:
        # Named, not just counted. A skip nobody reads is indistinguishable from a pass.
        print("  SKIPPED (verified nothing): %s" % r["suite"])

    # A suite that passes once and fails the second time is the specific failure this runner exists to
    # catch, so it is named rather than left to be spotted in the list above.
    if passes > 1:
        first = dict((r["suite"], r["rc"]) for r in results if r["pass"] == 1)
        flaky = [r["suite"] for r in results
                 if r["pass"] == 2 and r["rc"] != 0 and first.get(r["suite"]) == 0]
        if flaky:
            print("NOT REPEAT-SAFE - passed on the first run and failed on the second: %s"
                  % ", ".join(flaky))
            print("  That is state surviving between runs in one editor session: a hardcoded scratch")
            print("  path, or an assertion that pages past its own data once enough has piled up.")

    for r in bad:
        print("")
        print("--- %s (pass %d, rc=%s) ---" % (r["suite"], r["pass"], r["rc"]))
        print(r["tail"][-1200:])
    print("=" * 72)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
