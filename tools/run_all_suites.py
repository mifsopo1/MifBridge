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


KNOWN_FLAGS = {"--once", "--with-pie", "--anyway", "--offline"}


def running_shipping_builds():
    """UE game builds running right now, by process name. Empty list if it cannot tell.

    A packaged UE game is always `<Project>-Win64-Shipping.exe` (or -Test/-Development), whatever
    the project, so this is a general check and not a DDS2 one.

    WHY A SWEEP CARES. "The editor is closed" does NOT mean "the machine is free". On 2026-09-03 the
    editor was closed because the developer had gone to PLAY the game - the Steam build was running
    the whole time - and an unattended sweep would have taken an editor plus 350 suite runs of CPU
    out from under them. A shipping build running is the strongest available signal that a person is
    sitting at this machine right now.
    """
    if os.name != "nt":
        return []          # detection is Windows-only; everywhere else this is a no-op, not a pass
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.split("\n"):
        name = line.split('","')[0].lstrip('"') if '","' in line else ""
        if re.search(r"-Win64-(Shipping|Test|Development)\.exe$", name, re.I):
            found.append(name)
    return sorted(set(found))

USAGE = """usage: run_all_suites.py [--once] [--with-pie] [--anyway] [name-substring ...]

  --once      one pass instead of two. TWO is the default because the second pass is what
              catches state surviving between runs - a suite that adopts a fixture another
              suite created passes alone and fails on pass 2, which is the point.
  --with-pie  include the PIE suites. They need an ATTENDED run: starting PIE stops the bridge
              answering while the editor stays alive, which hangs an unattended sweep.

  --offline   run ONLY the suites that need no editor, no Blender and no bridge. Eight of them,
              190 assertions, a few seconds. Launches nothing - which is the point, since the
              mode exists for when somebody else is using the machine.

  --anyway    sweep even though a packaged UE game build is running. That build is the strongest
              signal somebody is sitting at this machine, and a sweep costs them half an hour of
              CPU, so it is refused by default.

  Bare words are suite-name substrings. One that matches NOTHING is an error, not an empty
  pass - see the comment at the filter below for the half hour that cost."""


def main():
    # AN UNKNOWN FLAG IS AN ERROR, NOT A NO-OP. This is the same lesson as the filter comment
    # further down, one level up and unlearned: every argument starting with `--` used to be
    # silently discarded, so `--help` did not print help - it ran the FULL two-pass sweep, launched
    # an editor, and held the machine for half an hour. A typo like `--onlyy test_x` dropped the
    # flag and then filtered on nothing.
    #
    # Checked FIRST, before the suite discovery below, so asking for usage costs nothing.
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    if "--help" in flags or "-h" in flags:
        print(USAGE)
        return 0
    unknown = [f for f in flags if f not in KNOWN_FLAGS]
    if unknown:
        print("unknown flag(s): %s" % ", ".join(unknown))
        print("known flags: %s" % ", ".join(sorted(KNOWN_FLAGS)))
        print("REFUSING TO RUN - a full sweep takes the editor for a long time, and running one "
              "because a flag was misspelled is not a reasonable default.")
        print("")
        print(USAGE)
        return 2

    # SOMEBODY MAY BE PLAYING ON THIS MACHINE. Checked before the lock and before any editor is
    # launched, because the cost of being wrong is half an hour of stutter in somebody's game.
    # --offline IS EXEMPT, and the refusal below is why the exemption is needed rather than a
    # convenience. This guard exists because a sweep costs "an editor plus ~350 suite runs of CPU
    # for half an hour", which is a real theft from somebody playing. An offline run launches
    # NOTHING and finishes in about two seconds. On 2026-09-03 the developer started the game, and
    # the mode built for exactly that situation - somebody else is using the machine - was refused
    # by a guard protecting against a cost it does not have.
    playing = running_shipping_builds()
    if playing and "--offline" in flags:
        print("a packaged UE build is running (%s) - proceeding anyway because --offline launches"
              % ", ".join(playing))
        print("  no editor and takes seconds. The refusal below is about a half-hour sweep.")
        print("")
        playing = []
    if playing and "--anyway" not in flags:
        print("REFUSING TO SWEEP - a packaged UE build is running: %s" % ", ".join(playing))
        print("  A sweep takes an editor plus ~350 suite runs of CPU for half an hour or more, and a")
        print("  running game build is the strongest signal that somebody is at this machine now.")
        print("  'The editor is closed' does not mean the machine is free - on 2026-09-03 it was")
        print("  closed because the developer had gone to play the game.")
        print("  Wait, or pass --anyway if you know the machine is yours.")
        return 3

    here = os.path.dirname(__file__) or "."
    suites = sorted(os.path.basename(p) for p in glob.glob(os.path.join(here, "test_*.py")))

    # --offline: THE SUITES THAT NEED NO BACKEND AT ALL, which is what you have when somebody else
    # is using the editor. Measured 2026-09-03 by running every suite with the bridge and Blender
    # both down and reading the exit code - 190 assertions across these eight.
    #
    # CURATED, AND THAT IS NOT LAZINESS. The PIE list above is derived because "does it call
    # start_pie" is a question source can answer. "Does it need a backend" is not: a grep for
    # M.call/raw_post/_blender MISSES five suites that roll their own urllib POST against
    # 127.0.0.1:8791, and widening it to match urllib or a port number then EXCLUDES
    # test_blender_headless_guard, which mentions ports while starting its own fake servers and
    # passes with nothing running. Wrong in both directions, which is why the classifier here is an
    # exit code and the list is written down.
    #
    # EXHAUSTIVE WHEN MEASURED, a floor thereafter. All 179 suites were run on 2026-09-03 with the
    # bridge and Blender both down: exactly these 8 passed, 20 more detected the absence and exited
    # 2 SKIPPED without verifying anything, and the remaining 151 hung on a connection that was
    # never going to answer. So this is not a sample somebody stopped adding to - it was the whole
    # set at that moment.
    #
    # It is still a floor going forward: a new backend-free suite will not appear here on its own.
    # What it cannot do is go quietly wrong - run it and a member that starts needing a backend
    # hangs or fails in front of you, which is the point of running them with nothing listening.
    # Re-measure the same way rather than reasoning about imports; see the note above on why a grep
    # gets this wrong in both directions.
    OFFLINE_SUITES = (
        "test_blender_headless_guard.py",   # starts its own fake servers
        "test_find_tools.py",
        "test_fuzz_detector.py",
        "test_mcp_post_errors.py",
        "test_payload_contract.py",         # stubs both transports
        "test_release_gates.py",
        "test_report_intake.py",
        "test_scratch_discrimination.py",
    )
    if "--offline" in flags:
        missing = [s for s in OFFLINE_SUITES if s not in suites]
        if missing:
            print("OFFLINE LIST NAMES A SUITE THAT NO LONGER EXISTS: %s" % ", ".join(missing))
            return 2
        suites = [s for s in suites if s in OFFLINE_SUITES]
        print("OFFLINE MODE - %d suite(s) that need no editor, no Blender and no bridge." % len(suites))
        print("  A floor rather than a full list: these are the ones measured to pass with the")
        print("  backends down. Everything else is unverified by this run.")
        print("")

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
    offline = "--offline" in flags
    for name, which in ordered:
        # OFFLINE MODE MUST NOT LAUNCH AN EDITOR, and this line is why the mode needed a guard
        # rather than just a suite filter. The loop below waits for the bridge and STARTS an editor
        # when none answers - so an --offline run would have launched one on a machine whose whole
        # premise is that somebody else is using it, which is the exact opposite of what the flag is
        # for. Caught before it shipped by reading the loop rather than by trusting the filter.
        if not offline and not M.wait_for_bridge(timeout=600):
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
        # BOTH ORDERINGS. 177 suites end with "PASS 29   FAIL 0" and one - the headless guard -
        # writes "29 PASS  0 FAIL". startswith("PASS ") missed it, so its record was stored with an
        # EMPTY summary, and audit_suite_reach then read empty as the number zero and reported
        # "PASSED while running 0% of itself" against a suite that runs 29 assertions and passes
        # every one. A formatting difference became a five-alarm coverage finding two tools away.
        # WIDENED 2026-09-03 to match the pattern audit_suite_reach reads records back with. This
        # said `startswith("PASS ")` and `^\d+ PASS\s+\d+ FAIL` - one literal space after PASS in
        # the first form and after the count in the second - while the consumer accepts \s+ at every
        # gap. Producer and consumer disagreeing about whitespace is what caused the empty record
        # described above; leaving them disagreeing in a NARROWER direction on the writing side just
        # moves where the next one appears. A summary that cannot be stored cannot be re-read
        # however tolerant the reader becomes.
        line = next((l for l in out.splitlines()
                     if re.match(r"^PASS\s+\d+\s+FAIL\s+\d+", l)
                     or re.match(r"^\d+\s+PASS\s+\d+\s+FAIL", l)), "")
        # BUSY IS NOT DEAD. A timeout here means the editor is alive and its game thread is
        # occupied - the bridge runs every endpoint on that thread - so relaunching starts a SECOND
        # editor beside a working one and both race for the port. That hung a 288-run sweep.
        # THE SECOND LAUNCH PATH, and the one that made --offline start an editor anyway. The first
        # attempt at this flag guarded only the pre-suite launch above; here the runner asks whether
        # the editor survived the suite, and with nothing listening the answer is "dead", so it
        # helpfully started one. An offline run has no editor BY DESIGN, so "dead" is the expected
        # state rather than a crash to recover from.
        #
        # Caught by running the flag and then looking at the machine, not by reading the diff - the
        # log said nothing, every suite passed, and an editor was sitting there afterwards. Guarding
        # one of two paths is the same half-a-pair mistake this repo recorded three times today.
        state = "dead" if offline else M.bridge_liveness()
        alive = state == "alive"
        if offline:
            alive = None          # not applicable: nothing was supposed to be alive
        elif state == "busy":
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
    # `is False`, NOT falsy. An offline run records editorSurvived as None - not applicable, there
    # was no editor - and `not None` is True, so the first version of this counted all eight as
    # having TAKEN THE EDITOR DOWN. A summary line asserting eight crashes that did not happen is
    # worse than no summary, and it is the same shape as a detail that states an interpretation
    # instead of the observation.
    died = [r for r in results if r["editorSurvived"] is False]
    print("")
    print("=" * 72)
    print("%d run(s) across %d suites, %d failed, %d skipped, %d took the editor down%s"
          % (len(results), len(suites), len(bad), len(skipped), len(died),
             " (no editor was involved)" if "--offline" in flags else ""))
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
