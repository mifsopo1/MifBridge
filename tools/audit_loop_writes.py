"""Find per-item facts written into a single-valued response field from inside a loop.

THE DEFECT. `Out` is the one response object. `Out->SetStringField(TEXT("labelNote"), ...)` called from
inside a per-item loop REPLACES the previous value, so N items that each had something to say produce
exactly one message - the last - and the caller has no hint the others happened. They read a single
oddity where there was a pattern.

Found in spawn_many and duplicate_actors (MifBridgeAuthoring.cpp:306 and :455, issue K). What makes
those two worth the tool rather than a one-line fix is where they sit: both write the note produced by
SetActorLabelChecked, a helper that exists ONLY because the engine's SetActorLabel is a void API that
silently declines names it dislikes. So the mechanism built to stop silent label loss was itself losing
label notices silently. That is the same defect class one layer up, and it is the kind of thing that
comes back unless something watches for it.

WHAT THIS IS NOT. Not every hit is a bug, and the tool does not pretend otherwise:
  * a loop that provably runs at most once makes last-wins identical to only-wins;
  * some fields are deliberately last-wins (a running total, a final state);
  * a Set on an object that is itself per-item (OpOut, RowOut) is CORRECT, and is excluded by the
    word-boundary in the pattern - `OpOut->Set` must not match.
So this is ratcheted against a baseline like param_reach: the existing set is recorded, and the tool
fails only when a NEW one appears. Adding to the baseline is a deliberate act with a reason in the
commit.

THE SELF-CHECK MATTERS MORE THAN THE SCAN. This is a heuristic over indentation and nearby loop
keywords, and a heuristic that quietly stops matching is worse than no tool at all - it reports a clean
scan forever. So the two known-positive sites are asserted on every run, and the tool FAILS if it cannot
find them. An earlier version of this scan used brace-depth tracking, reported two candidates, and had
silently missed both known sites; it looked like a clean result. That is exactly the failure this guard
exists to prevent.

Usage:
    python tools/audit_loop_writes.py
    python tools/audit_loop_writes.py --update-baseline   # accept the current set, deliberately
"""
import io
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")
BASELINE = os.path.join(HERE, "audit_loop_writes_baseline.txt")
NL = chr(10)

# The two sites that motivated the tool. If the scan cannot find these, the scan is broken.
KNOWN = [("MifBridgeAuthoring.cpp", 306, "labelNote"),
         ("MifBridgeAuthoring.cpp", 455, "labelNote")]

# (?<![A-Za-z0-9_]) so OpOut->Set / RowOut->Set / SubOut->Set do not match: those are per-item objects
# and writing to them inside a loop is exactly right.
WRITE = re.compile(r'(?<![A-Za-z0-9_])Out->Set(\w*)Field\s*\(\s*TEXT\("([^"]+)"')


def scan():
    found = []
    for path in sorted(glob.glob(os.path.join(SRC, "*.cpp"))):
        name = os.path.basename(path)
        text = io.open(path, encoding="utf-8", errors="replace").read()
        lines = text.replace(chr(13) + NL, NL).split(NL)
        for i, ln in enumerate(lines, 1):
            m = WRITE.search(ln)
            if not m:
                continue
            # An array field accumulates by construction and is never this bug.
            if m.group(1) == "Array":
                continue
            tabs = len(ln) - len(ln.lstrip("\t"))
            # Handler bodies sit at 2 tabs; a loop body is 3+. Require 4 to cut noise from a single
            # `if` nested in a handler, which is not a loop.
            if tabs < 4:
                continue
            ctx = lines[max(0, i - 41):i - 1]
            if any(re.match(r"^\t{2,%d}(for|while)\s*\(" % (tabs - 1), c) for c in ctx):
                found.append("%s:%d:%s" % (name, i, m.group(2)))
    return sorted(found)


def self_check(found):
    """Refuse to report a clean scan from a scanner that cannot find what it was built to find."""
    missing = []
    for name, line, field in KNOWN:
        if not any(e.startswith("%s:%d:%s" % (name, line, field)) for e in found):
            # The line may legitimately have MOVED as the file changed; a field/file match is enough
            # to show the scan still works.
            if not any(e.startswith(name) and e.endswith(":" + field) for e in found):
                missing.append("%s %s (was line %d)" % (name, field, line))
    return missing


def load_baseline():
    try:
        return set(l.strip() for l in io.open(BASELINE, encoding="utf-8")
                   if l.strip() and not l.startswith("#"))
    except Exception:
        return set()


def main():
    found = scan()

    missing = self_check(found)
    if missing:
        print("SELF-CHECK FAILED - the scan can no longer find its own known cases:")
        for m in missing:
            print("  %s" % m)
        print("")
        print("Either those sites were fixed (update KNOWN), or the heuristic has drifted and is now")
        print("reporting a clean scan while matching nothing. Do NOT trust a green result until this")
        print("is resolved.")
        return 2

    if "--update-baseline" in sys.argv:
        with io.open(BASELINE, "w", encoding="utf-8", newline=chr(13) + NL) as f:
            f.write("# Per-item writes to a single-valued response field, accepted deliberately." + NL)
            f.write("# Regenerate with: python tools/audit_loop_writes.py --update-baseline" + NL)
            for e in found:
                f.write(e + NL)
        print("baseline updated: %d entries" % len(found))
        return 0

    base = load_baseline()
    new = [e for e in found if e not in base]
    gone = [e for e in base if e not in found]

    print("loop-write scan: %d site(s) (baseline %d)" % (len(found), len(base)))
    for g in gone:
        print("  FIXED    %s  (drop it from the baseline)" % g)
    if new:
        print("")
        print("NEW per-item writes to a single-valued field - each of these reports only the LAST item:")
        for e in new:
            print("  %s" % e)
        print("")
        print("If it is genuinely last-wins, or the loop runs at most once, accept it with")
        print("  python tools/audit_loop_writes.py --update-baseline")
        print("and say why in the commit. Otherwise make the field an array.")
        return 1

    print("OK  no new per-item writes to a single-valued field")
    return 0


if __name__ == "__main__":
    sys.exit(main())
