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
# Sites the scan MUST find, asserted on every run. The original two (MifBridgeAuthoring labelNote,
# lines 306 and 455) were the bug that motivated this tool and were FIXED on 2026-08-26, so they no
# longer exist to anchor against - the self-check correctly refused a green result until this list was
# updated, which is the whole point of it.
#
# These replacements are genuine in-loop writes that are CORRECT, which makes them stable anchors: they
# are not going to be fixed away. MifBridgeNodes ok is an any-failure accumulator writing false
# repeatedly (idempotent); MifBridgeDataTables row writes once and returns on the next line.
KNOWN = [("MifBridgeNodes.cpp", 2583, "ok"),
         ("MifBridgeDataTables.cpp", 489, "row")]

# (?<![A-Za-z0-9_]) so OpOut->Set / RowOut->Set / SubOut->Set do not match: those are per-item objects
# and writing to them inside a loop is exactly right.
TAB_CH = chr(9)
# a line whose first non-tab content opens a loop
LOOP_RE = re.compile("^" + chr(9) + "*(for|while)" + chr(92) + "s*" + chr(92) + "(")

WRITE = re.compile(r'(?<![A-Za-z0-9_])Out->Set(\w*)Field\s*\(\s*TEXT\("([^"]+)"')


def scan():
    found = []
    for path in sorted(glob.glob(os.path.join(SRC, "*.cpp"))):
        name = os.path.basename(path)
        text = io.open(path, encoding="utf-8", errors="replace").read()
        lines = text.replace(chr(13) + NL, NL).split(NL)
        # Span of every loop BODY in this file, by brace matching. Done once per file rather than
        # per candidate: it is O(lines) and the alternative is re-walking for every write.
        loop_ranges = []
        for li, lc in enumerate(lines):
            if not LOOP_RE.match(lc):
                continue
            depth, k, opened = 0, li, False
            while k < len(lines):
                depth += lines[k].count(chr(123)) - lines[k].count(chr(125))
                if chr(123) in lines[k]:
                    opened = True
                if opened and depth <= 0:
                    break
                k += 1
            if opened:
                loop_ranges.append((li, k))
        for i, ln in enumerate(lines, 1):
            # SKIP COMMENTS. This file's comments quote the code they replaced - the labelNote fix
            # left a line reading `//   Out->SetStringField(TEXT("labelNote"), LabelNote);` directly
            # above its replacement, and the scan reported it as a live site. audit_modals already
            # carries this guard; not carrying it here meant the tool's first real run reported a
            # defect that had just been fixed, in the very comment explaining the fix.
            if ln.lstrip().startswith("//") or ln.lstrip().startswith("*"):
                continue
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
            # INSIDE THE LOOP BODY, decided by BRACE MATCHING rather than a heuristic.
            #
            # Two earlier attempts got this wrong and both are worth remembering. Looking back for any
            # for-statement within 40 lines also flags writes that FOLLOW a loop - that is how the
            # foliage sites and remove_pin's duplicateNote were reported, each executing once after its
            # loop finished. Then comparing INDENTATION failed the other way: a write nested inside an
            # if within a loop meets the if's brace first, which is strictly shallower but is not a
            # for, so every site was rejected and the scan found nothing at all.
            #
            # loop_ranges (computed once per file, above) holds the [open, close] line span of every
            # loop BODY, matched on braces. A write is in a loop exactly when its line falls in one.
            if any(lo < i <= hi for lo, hi in loop_ranges):
                found.append(("%s:%s" % (name, m.group(2)), i))
    return sorted(found)


def self_check(found):
    """Refuse to report a clean scan from a scanner that cannot find what it was built to find."""
    missing = []
    for name, line, field in KNOWN:
        if not any(k == "%s:%s" % (name, field) for k, _ in found):
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
            for k, line in found:
                f.write(k + NL)
        print("baseline updated: %d entries" % len(found))
        return 0

    base = load_baseline()
    new = [(k, l) for k, l in found if k not in base]
    gone = [k for k in base if k not in set(x for x, _ in found)]

    print("loop-write scan: %d site(s) (baseline %d)" % (len(found), len(base)))
    for g in gone:
        print("  FIXED    %s  (drop it from the baseline)" % g)
    if new:
        print("")
        print("NEW per-item writes to a single-valued field - each of these reports only the LAST item:")
        for k, l in new:
            print("  %s   (line %d)" % (k, l))
        print("")
        print("If it is genuinely last-wins, or the loop runs at most once, accept it with")
        print("  python tools/audit_loop_writes.py --update-baseline")
        print("and say why in the commit. Otherwise make the field an array.")
        return 1

    print("OK  no new per-item writes to a single-valued field")
    return 0


if __name__ == "__main__":
    sys.exit(main())
