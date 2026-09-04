"""make_release's refusal gates - do they actually refuse?

WHY THIS EXISTS. make_release.py will not package when the README badge is stale, and (since
2026-09-02) will not package when CHANGELOG.md's top row disagrees with the tree. Both are gates
whose entire value is going red at the right moment, and neither had ever been proven to do so. The
changelog gate exists because the badge gate's absence had a twin - the badge was checked and the
changelog was not, so the changelog's UE column sat one too high for six releases while the correct
number lived two files away. A gate nobody tests is that same gap one level up.

IT NEVER TOUCHES A TRACKED FILE, and the first version of this suite did.

That version planted into the real README.md and CHANGELOG.md and restored them in a finally. It
worked, and it was still wrong: this file lives in tools/test_*.py, so run_all_suites picks it up,
and a sweep killed mid-plant would leave one of those two files corrupted in the working tree. Two
sweeps were killed during the session that wrote it. A finally does not survive the process being
terminated.

It now copies both files into a temp directory and points make_release's ROOT at the copy for the
duration. The counters are unaffected - BIND_FILE and the addon path are module-level constants
resolved at import - so the gates read REAL numbers against PLANTED documents, which is exactly the
comparison under test.

THESE ARE RELEASE-TIME GATES, NOT ALWAYS-TRUE INVARIANTS. The badge is regenerated when a release is
packaged, so between releases it legitimately lags the tree. This asserts the gates ANSWER, and that
they go red on a planted disagreement - never that today's tree happens to be green.

Usage:  python tools/test_release_gates.py
Exit:   0 passed   1 failed
"""
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import make_release as R

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:300]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:300]))


def plant(sandbox, fname, old, new):
    """Rewrite a COPY inside the sandbox. The tracked file is never opened for writing."""
    p = os.path.join(sandbox, fname)
    text = io.open(p, encoding="utf-8").read()
    assert old in text, "plant anchor %r not found in the %s copy" % (old, fname)
    io.open(p, "w", encoding="utf-8").write(text.replace(old, new, 1))


def restore(sandbox, fname):
    shutil.copy2(os.path.join(REPO, fname), os.path.join(sandbox, fname))


def main():
    before = {f: io.open(os.path.join(REPO, f), "rb").read()
              for f in ("README.md", "CHANGELOG.md")}

    sandbox = tempfile.mkdtemp(prefix="mif_release_gate_")
    for f in ("README.md", "CHANGELOG.md"):
        shutil.copy2(os.path.join(REPO, f), os.path.join(sandbox, f))
    real_root = R.ROOT
    R.ROOT = sandbox
    try:
        # -------------------------------------------------------------- R101
        print("=== R101: both gates return a verdict on the tree as it stands ===")
        okc, msgc = R.check_changelog()
        check("R101 the changelog gate returns a verdict with a reason",
              isinstance(okc, bool) and bool(msgc), (okc, msgc))
        okb, msgb = R.check_badge()
        check("R101 the badge gate returns a verdict with a reason",
              isinstance(okb, bool) and bool(msgb), (okb, msgb))
        print("       changelog: %s" % ("current" if okc else "stale (expected between releases)"))
        print("       badge:     %s" % ("current" if okb else "stale (expected between releases)"))

        # -------------------------------------------------------------- R102
        print("\n=== R102: a wrong number in the changelog's top row is REFUSED ===")
        top = R.endpoint_count()
        plant(sandbox, "CHANGELOG.md", "| %d | 68 |" % top, "| %d | 68 |" % (top + 7))
        ok, msg = R.check_changelog()
        check("R102 a top row that disagrees with the tree is refused", ok is False, msg)
        check("R102 and the message gives both numbers, not just 'stale'",
              ("%d" % (top + 7)) in msg and ("%d" % top) in msg, msg)
        # THE WARNING THAT MATTERS: the obvious wrong fix is to 'correct' every historical row.
        check("R102 and it says historical rows must NOT be edited",
              "must NOT be edited" in msg, msg)
        restore(sandbox, "CHANGELOG.md")
        check("R102 and it passes again once the copy is restored",
              R.check_changelog()[0] == okc, "verdict should match the pre-plant one")

        # -------------------------------------------------------------- R103
        print("\n=== R103: a stale badge is REFUSED ===")
        # Planted against what the badge ACTUALLY says, not the generated count - between releases
        # those differ, and comparing them was a bug in this suite's first version.
        text = io.open(os.path.join(sandbox, "README.md"), encoding="utf-8").read()
        m = re.search(r"\*\*(\d+) UE endpoints\*\*", text)
        check("R103 (setup) the badge carries a UE endpoint figure", bool(m), "no match in README")
        if m:
            plant(sandbox, "README.md", m.group(0), "**1 UE endpoints**")
            ok, msg = R.check_badge()
            check("R103 a stale badge is refused", ok is False, msg)
            check("R103 and it names --update-badge as the fix", "--update-badge" in msg, msg)
            restore(sandbox, "README.md")
    finally:
        R.ROOT = real_root
        shutil.rmtree(sandbox, ignore_errors=True)

    # ------------------------------------------------------------------ R200
    print("")
    print("=== R200 run_all_suites.merge_suite_records - a partial sweep must not erase ===")
    # WHY HERE. This is sweep infrastructure whose failure mode is destroying the evidence a release
    # is judged on, which is the same family as the gates above: tooling that must behave at the
    # moment nobody is watching. It has no suite of its own, and a third file to remember is worse
    # than one block in the file that already runs offline and is already gated.
    #
    # WHAT IT GUARDS. Until 2026-09-03 the runner wrote its results over "w", so any run that
    # narrowed the suite list DELETED the record of every suite it skipped - and one of the three
    # narrowing paths is the DEFAULT, since PIE suites are excluded unless --with-pie. An --offline
    # run took the file from 346 records to 9. The predicate is injected so this needs no disk.
    import run_all_suites as RS
    always = lambda name: True

    old = [{"suite": "a.py", "pass": 1}, {"suite": "a.py", "pass": 2}, {"suite": "b.py", "pass": 1}]
    kept, dropped = RS.merge_suite_records(old, [{"suite": "c.py", "pass": 1}], always)
    check("R200 a narrowed sweep CARRIES FORWARD the suites it never ran - the whole bug",
          [(r["suite"], r["pass"]) for r in kept]
          == [("a.py", 1), ("a.py", 2), ("b.py", 1), ("c.py", 1)], "got %s" % kept)

    # THE KEY IS (suite, pass). Keying on the name alone silently halves the file, which is what the
    # first version of this fix actually did - 168 carried where 337 were expected.
    kept, _ = RS.merge_suite_records(old, [{"suite": "a.py", "pass": 2, "rc": 9}], always)
    check("R200 both passes of a suite survive - keying on the name alone halves the file",
          len(kept) == 3 and kept[1]["rc"] == 9,
          "got %s" % [(r["suite"], r["pass"], r.get("rc")) for r in kept])

    kept, dropped = RS.merge_suite_records(
        old, [], lambda name: name != "b.py")
    check("R200 a record for a suite file that no longer exists is DROPPED",
          dropped == ["b.py"] and all(r["suite"] != "b.py" for r in kept),
          "dropped=%s kept=%s" % (dropped, [r["suite"] for r in kept]))

    check("R200 an empty run is not an erasure",
          len(RS.merge_suite_records(old, [], always)[0]) == 3)

    # A MIXED-TYPE SORT IS A CRASH, not a wrong order, and a crash loses the WHOLE file because the
    # write never happens. Same hazard as T606 in test_report_intake.
    kept, _ = RS.merge_suite_records([{"suite": "z.py"}, {"pass": 1}], [{"suite": "a.py", "pass": 1}],
                                     always)
    check("R200 records missing suite or pass sort instead of crashing the sort",
          len(kept) == 3, "got %s" % kept)

    # ------------------------------------------------------------------ R100
    print("")
    # LAST, and it is about the whole run: this suite must be incapable of changing the repository.
    after = {f: io.open(os.path.join(REPO, f), "rb").read()
             for f in ("README.md", "CHANGELOG.md")}
    check("R100 README.md is byte-identical - the tracked file was never written",
          after["README.md"] == before["README.md"], "the sandbox leaked")
    check("R100 CHANGELOG.md is byte-identical - the tracked file was never written",
          after["CHANGELOG.md"] == before["CHANGELOG.md"], "the sandbox leaked")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
