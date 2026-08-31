"""CHECK: how much of each suite actually RUNS?

A suite that reports PASS is not a suite that tested what it contains. test_safety_gate ran 5 of its
38 assertions on this machine for months - everything below its fail-safe bail-out is skipped
whenever the write gate is off, which is the mode this editor runs in, so the export-path contract it
was written to protect had no coverage at all where it runs. Nothing said so. "PASS 5 FAIL 0" reads
like success.

The measurement is crude and was worth doing anyway: count `check(` calls in the source, compare
against PASS+FAIL from the last recorded run, and look at the ratio. It found two real things the
same afternoon:

  test_blender_rig    PASS 12 FAIL 4 with no Blender running. A private bare-connect reachable()
                      answered True against a UE editor squatting MifBlender's port, so the suite
                      ran its whole body against the wrong protocol. FOUR FALSE FAILURES.
  test_niagara_params 0 of 34, returning 3 - a setup ERROR - when the honest answer was SKIPPED.

WHAT THE RATIO DOES NOT MEAN, and why this prints a reading list rather than failing. A low ratio is
usually CORRECT: a suite that skips because its fixture is genuinely absent is behaving well, and
loops make the count exceed the source. What is worth a human's eye is the combination of a low
ratio with an exit code claiming something else - a rc=0 that ran a tenth of itself, or a rc=1 whose
failures might be an environment problem rather than a defect.

STALE RECORDS ARE CALLED OUT, because they cost me two false leads out of five. suite_results.json
is written by run_all_suites and can predate the source it describes; a record older than the file
it is about says nothing about the code that is there now.

KNOWN LIMITATION, AND IT FAILS IN THE DANGEROUS DIRECTION. Staleness is judged on the results FILE's
mtime, and an mtime is not a content age. Copy a backup over suite_results.json and every record in
it is suddenly "current" while describing runs from hours earlier - observed doing exactly that on
2026-08-31, after which this tool stopped marking a record it had correctly marked a moment before.

The real fix is for run_all_suites to stamp each record with the time that suite RAN, so staleness
can be judged per record against the source it describes instead of per file. Until then: a row is
only as trustworthy as your memory of when the sweep last ran, and if that is in doubt, re-run it.
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "suite_results.json")

# Exit codes this repo uses, so the report can say whether a low ratio AGREES with the verdict.
RC_MEANING = {0: "passed", 1: "FAILED", 2: "skipped", 3: "setup error"}


def check_count(src):
    """`check(` calls in the source - what the suite could assert if every branch ran."""
    return len(re.findall(r"(?<![A-Za-z_.])check\s*\(", src))


def newest_input_mtime(path, src):
    """The suite's own mtime, or a LOCAL module it imports if that is newer.

    A record is stale when the behaviour it describes has changed, and that is not only the suite's
    own file. Three Blender suites were reported FAILED-having-run-0% from records that predated a
    fix in blender_audit_common, which they import - their own sources were untouched, so comparing
    against those alone called the records current and the report was confidently wrong about all
    three.
    """
    newest = os.path.getmtime(path)
    for mod in set(re.findall(r"^\s*(?:import|from)\s+([a-z_][a-z0-9_]*)", src, re.M)):
        local = os.path.join(HERE, mod + ".py")
        if os.path.isfile(local):
            newest = max(newest, os.path.getmtime(local))
    return newest


def main():
    if not os.path.isfile(RESULTS):
        print("no %s - run tools/run_all_suites.py first; nothing to measure." % RESULTS)
        return 0
    try:
        records = json.load(io.open(RESULTS, encoding="utf-8"))
    except Exception as exc:
        print("could not read %s: %s" % (RESULTS, exc))
        return 0

    results_mtime = os.path.getmtime(RESULTS)
    latest = {}
    for rec in records:                      # later entries win: the most recent run of each suite
        if isinstance(rec, dict) and rec.get("suite"):
            latest[rec["suite"]] = rec

    rows, stale = [], []
    for name, rec in sorted(latest.items()):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        src = io.open(path, encoding="utf-8", errors="replace").read()
        defined = check_count(src)
        if defined < 8:                      # too small for the ratio to mean anything
            continue
        m = re.search(r"PASS (\d+)\s+FAIL (\d+)", rec.get("summary") or "")
        ran = (int(m.group(1)) + int(m.group(2))) if m else 0
        rc = rec.get("rc")
        # MARKED, NOT DROPPED. Excluding stale rows outright left 11 of 151 measurable, because
        # nearly every suite imports mifaudit and one edit to it invalidates the lot. A number the
        # reader is told to distrust is worth more than no number - the point is the reading list.
        is_stale = newest_input_mtime(path, src) > results_mtime
        if is_stale:
            stale.append(name)
        rows.append((ran / float(defined), ran, defined, name, rc, is_stale))

    rows.sort()
    print("measured %d suite(s) with 8+ assertions; %d record(s) predate their source or a local"
          % (len(rows), len(stale)))
    print("module they import, and are MARKED rather than hidden")

    # The combination worth reading: a low ratio beside a verdict that implies more was covered.
    suspicious = [r for r in rows if r[0] < 0.5]
    if suspicious:
        print()
        print("RAN FAR LESS THAN THEY CONTAIN. A READING LIST, not a defect list - a suite that")
        print("skips because its fixture is genuinely absent is behaving correctly. What deserves")
        print("a look is a low ratio beside an exit code claiming something else:")
        print()
        print("  %-34s %5s %8s  %-12s %s" % ("suite", "ran", "defined", "verdict", "note"))
        for frac, ran, defined, name, rc, is_stale in suspicious:
            if is_stale:
                note = "<- RECORD IS STALE, re-run before believing this"
            elif rc == 0:
                note = "<- PASSED while running %.0f%% of itself" % (frac * 100)
            elif rc in (1, 3):
                note = "<- %s having run %.0f%% - environment, or a real defect?" % (
                    RC_MEANING.get(rc, rc), frac * 100)
            else:
                note = ""
            print("  %-34s %5d %8d  %-12s %s"
                  % (name, ran, defined, RC_MEANING.get(rc, "rc=%s" % rc), note))

    if stale:
        print()
        print("%d of %d records predate their source or a local module they import. Re-run"
              % (len(stale), len(rows)))
        print("tools/run_all_suites.py to refresh - one edit to mifaudit stales nearly all of them,")
        print("which is why they are marked rather than dropped.")

    if not suspicious and not stale:
        print()
        print("every measured suite ran at least half of what it defines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
