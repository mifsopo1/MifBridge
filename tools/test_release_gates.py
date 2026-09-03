"""make_release's refusal gates - do they actually refuse?

WHY THIS EXISTS. make_release.py will not package when the README badge is stale, and (since
2026-09-02) will not package when CHANGELOG.md's top row disagrees with the tree. Both are gates
whose entire value is going red at the right moment, and neither had ever been proven to do so.

The changelog gate exists because the badge gate's absence had a twin: the badge was checked and the
changelog was not, so the changelog's UE column sat one too high for six releases while the correct
number lived two files away. A gate nobody tests is the same shape of gap one level up.

NO BRIDGE AND NO EDITOR. These are pure file checks, so this runs anywhere, which is the point - a
gate that can only be tested during a release is a gate that gets tested during a release.

IT MUTATES TRACKED FILES and puts them back. Every plant is wrapped in try/finally, the originals are
held in memory as bytes, and R100 asserts both files are byte-identical afterwards. If that check
ever fails, `git checkout -- README.md CHANGELOG.md` is the recovery and nothing else was touched.

Usage:  python tools/test_release_gates.py
Exit:   0 passed   1 failed
"""
import importlib
import io
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import make_release as R

PASS, FAIL = [], []

CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
README = os.path.join(ROOT, "README.md")


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:300]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:300]))


def read(path):
    return io.open(path, "rb").read()


def write(path, data):
    io.open(path, "wb").write(data)


def planted(path, old, new):
    """Replace `old` with `new` in a tracked file, returning the original bytes for restoration."""
    orig = read(path)
    text = orig.decode("utf-8")
    assert old in text, "plant anchor not found in %s" % os.path.basename(path)
    write(path, text.replace(old, new, 1).encode("utf-8"))
    return orig


def main():
    before_ch, before_rm = read(CHANGELOG), read(README)

    # ------------------------------------------------------------------ R101 the clean tree
    #
    # THESE ARE RELEASE-TIME GATES, NOT ALWAYS-TRUE INVARIANTS, and the first version of this suite
    # got that wrong. The badge is regenerated when a release is packaged, so between releases it
    # legitimately lags the tree - it read 440/525/168 against a tree at 453/538/178 while this was
    # being written, which is correct behaviour and exactly what the gate is for: it fires at the
    # moment somebody tries to package, not continuously.
    #
    # So this asserts the gate ANSWERS, not that the answer is yes. A gate that cannot go red is the
    # bug; a gate that is red between releases is the design.
    print("=== R101: both gates return a verdict on the tree as it stands ===")
    okc, msgc = R.check_changelog()
    check("R101 the changelog gate returns a verdict with a reason",
          isinstance(okc, bool) and bool(msgc), (okc, msgc))
    okb, msgb = R.check_badge()
    check("R101 the badge gate returns a verdict with a reason",
          isinstance(okb, bool) and bool(msgb), (okb, msgb))
    print("       changelog: %s" % ("current" if okc else "stale (expected between releases)"))
    print("       badge:     %s" % ("current" if okb else "stale (expected between releases)"))

    # ------------------------------------------------------------------ R102 changelog goes red
    print("\n=== R102: a wrong number in the changelog's top row is REFUSED ===")
    top = R.endpoint_count()
    orig = planted(CHANGELOG, "| %d | 68 |" % top, "| %d | 68 |" % (top + 7))
    try:
        importlib.reload(R)
        ok, msg = R.check_changelog()
        check("R102 a top row that disagrees with the tree is refused", ok is False, msg)
        check("R102 and the message gives both numbers, not just 'stale'",
              ("%d" % (top + 7)) in msg and ("%d" % top) in msg, msg)
        # THE WARNING THAT MATTERS: the obvious wrong fix is to 'correct' every historical row.
        check("R102 and it says historical rows must NOT be edited",
              "must NOT be edited" in msg, msg)
    finally:
        write(CHANGELOG, orig)
        importlib.reload(R)

    # ------------------------------------------------------------------ R103 badge goes red
    print("\n=== R103: a stale badge is REFUSED ===")
    # Plant against what the badge ACTUALLY says, not against the generated count - between
    # releases those differ, and asserting they match was the same mistake as R101's.
    text = read(README).decode("utf-8")
    m = re.search(r"\*\*(\d+) UE endpoints\*\*", text)
    marker = "**%s UE endpoints**" % m.group(1) if m else None
    if not marker:
        check("R103 (setup) the badge carries a UE endpoint figure", False, "no match in README")
    else:
        orig = planted(README, marker, "**1 UE endpoints**")
        try:
            importlib.reload(R)
            ok, msg = R.check_badge()
            check("R103 a stale badge is refused", ok is False, msg)
            check("R103 and it names --update-badge as the fix",
                  "--update-badge" in msg, msg)
        finally:
            write(README, orig)
            importlib.reload(R)

    # ------------------------------------------------------------------ R100 the tree is back
    print("")
    # LAST, because it is about everything above. A suite that edits tracked files and does not
    # prove it put them back is worse than one that never edited them.
    check("R100 CHANGELOG.md is byte-identical to before this run",
          read(CHANGELOG) == before_ch, "run: git checkout -- CHANGELOG.md")
    check("R100 README.md is byte-identical to before this run",
          read(README) == before_rm, "run: git checkout -- README.md")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
