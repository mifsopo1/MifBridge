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

The fix is a per-record `ranAt` stamp so staleness is judged per RECORD against the source it
describes. This side is done: `ranAt` is used when a record carries it. Records written before it
existed fall back to the file mtime, and the report says how many rows rested on that weaker basis
rather than quietly mixing the two. Once run_all_suites stamps it, the fallback stops being reached.
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

    # PER-RECORD TIME WHEN THE RUNNER PROVIDES IT, the file's mtime only as a fallback. An mtime is
    # not a content age: copying a backup over suite_results.json moves it without changing a single
    # record, and this then calls hours-old results current - wrong in the direction that gets
    # believed. run_all_suites stamps `ranAt` (epoch seconds); records written before that existed
    # fall back to the file, and the report says which basis it used.
    results_mtime = os.path.getmtime(RESULTS)
    latest = {}
    for rec in records:                      # later entries win: the most recent run of each suite
        if isinstance(rec, dict) and rec.get("suite"):
            latest[rec["suite"]] = rec

    rows, stale, used_file_mtime, unparsed = [], [], set(), []
    for name, rec in sorted(latest.items()):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        src = io.open(path, encoding="utf-8", errors="replace").read()
        defined = check_count(src)
        if defined < 8:                      # too small for the ratio to mean anything
            continue
        # A SUMMARY THIS CANNOT PARSE IS NOT A SUITE THAT RAN NOTHING.
        #
        # `ran = ... if m else 0` turned every parse failure into the number zero, and zero is the
        # input to the scariest line this tool prints: "PASSED while running 0% of itself". On
        # 2026-09-03 that fired against test_blender_headless_guard, which actually runs 29
        # assertions and passes all of them.
        #
        # THE ROOT CAUSE IS ONE LAYER EARLIER and is fixed there too: run_all_suites picked the
        # summary with startswith("PASS "), and this suite ends "29 PASS  0 FAIL" while the other
        # 177 write "PASS 29   FAIL 0". So the RUNNER stored an empty summary, and the line below
        # turned empty into zero. A formatting difference became a five-alarm coverage finding two
        # tools away from where it started.
        #
        # The regex here accepts both orderings as well, so a record written by an older runner
        # still reads correctly. The `if m else 0`
        # is the more important half though: it made a MEASUREMENT FAILURE indistinguishable from a
        # measurement of nothing, in the direction that manufactures a five-alarm finding out of a
        # tool that is working. Unparseable records are listed separately now and excluded from the
        # ratio entirely, because a ratio computed from a number nobody could read is not a ratio.
        # \s+ ON EVERY GAP, not just the ones that happened to vary. The first version wrote
        # `PASS (\d+)` with a literal single space while using \s+ everywhere else, so a summary
        # with two spaces after PASS - or a tab - fell into the unparsed bucket and out of the
        # ratio, taking the "ran 0% of itself" alarm with it. All 187 suites in the tree write one
        # space there today (185 as "PASS %d   FAIL %d", one with two spaces before FAIL, one as
        # "%d PASS  %d FAIL"), so this is brittleness rather than a live defect - which is exactly
        # when it is cheap to fix. Verified the new pattern parses everything the old one did plus
        # the two shapes it dropped, and that the ratio is unchanged on the current tree.
        m = re.search(r"PASS\s+(\d+)\s+FAIL\s+(\d+)|(\d+)\s+PASS\s+(\d+)\s+FAIL",
                      rec.get("summary") or "")
        if not m:
            unparsed.append((name, (rec.get("summary") or "").strip()[:60]))
            continue
        got = [g for g in m.groups() if g is not None]
        ran = int(got[0]) + int(got[1])
        rc = rec.get("rc")
        # MARKED, NOT DROPPED. Excluding stale rows outright left 11 of 151 measurable, because
        # nearly every suite imports mifaudit and one edit to it invalidates the lot. A number the
        # reader is told to distrust is worth more than no number - the point is the reading list.
        ran_at = rec.get("ranAt")
        if not isinstance(ran_at, (int, float)):
            ran_at = results_mtime
            used_file_mtime.add(name)
        is_stale = newest_input_mtime(path, src) > ran_at
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

    if unparsed:
        print()
        print("SUMMARY NOT PARSEABLE - measured as NOTHING, not as zero. These are excluded from")
        print("the ratio above rather than counted as suites that ran no assertions:")
        for name, summary in unparsed:
            print("  %-34s %s" % (name, summary or "(no summary recorded)"))

    if stale:
        print()
        print("%d of %d records predate their source or a local module they import. Re-run"
              % (len(stale), len(rows)))
        if used_file_mtime:
            print("(%d of them judged on the results FILE's mtime because the record carries no"
                  % len(used_file_mtime))
            print("ranAt - an mtime is not a content age, so those are the least trustworthy rows.)")
        print("tools/run_all_suites.py to refresh - one edit to mifaudit stales nearly all of them,")
        print("which is why they are marked rather than dropped.")

    if not suspicious and not stale:
        print()
        print("every measured suite ran at least half of what it defines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
