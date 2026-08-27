"""Find spec items that are open but already done, and items written down twice.

WHY THIS EXISTS. Four times on 2026-08-27 an item was built, ticked, and kept surfacing as "next" -
PCG twice, Sequencer twice, Niagara once, and the write half of each. The cause is always the same
shape:

    a decline written in two places gets reopened in two places and then ticked in ONE

The copy nobody edited stays `- [ ]` forever and keeps being offered as the next thing to do. Each
time I closed it by hand, noticed the pattern, and closed the next one by hand as well.

So this checks it instead. Two rules, and both are advisory - it never edits the spec, because "this
looks done" is a judgement and a tool that ticks items on a name match would quietly hide real work.

  1. AN OPEN ITEM WHOSE ENDPOINTS EXIST. Matches an open `- [ ]` line against MIF_DECL names in the
     header. Deliberately conservative: it needs a full endpoint name to appear in the item text, so
     a vague item is not flagged on a coincidence.
  2. TWO ITEMS WITH THE SAME BOLD TITLE. `- [ ] **PCG**` twice is the exact failure above. Reported
     whatever their checkbox state, since a `- [x]` and a `- [ ]` for one title is precisely the mess.

Usage:
    python tools/spec_check.py            # report
    python tools/spec_check.py --quiet    # exit code only: 0 clean, 1 something to look at
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "FEATURE_PARITY_SPEC.md")
HEADER = os.path.join(HERE, "..", "Source", "MifBridge", "Private", "MifBridgeHandlers.h")


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


def built_endpoints():
    try:
        return set(re.findall(r"MIF_DECL\(([a-z_0-9]+)\)", read(HEADER)))
    except Exception:
        return set()


def items(text):
    """(line number, checkbox, title, full text) for every spec item."""
    out = []
    for i, line in enumerate(text.split("\n")):
        m = re.match(r"^\s*-\s*\[([ x~])\]\s*(.*)$", line)
        if not m:
            continue
        body = m.group(2)
        title = re.match(r"\*\*(.+?)\*\*", body)
        out.append((i + 1, m.group(1), title.group(1) if title else body[:60], body))
    return out


def main():
    quiet = "--quiet" in sys.argv
    try:
        text = read(SPEC)
    except Exception as exc:
        print("could not read the spec: %s" % exc)
        return 2

    built = built_endpoints()
    all_items = items(text)
    problems = []

    # 1. open, but the endpoints it names already exist
    for lineno, box, title, body in all_items:
        if box != " ":
            continue
        named = [e for e in built if e in body and len(e) > 8]
        if named:
            problems.append(
                "L%-5d OPEN but built: %s\n           names %s, which exist in MifBridgeHandlers.h"
                % (lineno, title[:58], ", ".join(sorted(named)[:3])))

    # 2. the same title written twice
    seen = {}
    for lineno, box, title, _ in all_items:
        key = title.strip().lower()
        seen.setdefault(key, []).append((lineno, box))
    for key, where in sorted(seen.items()):
        if len(where) < 2:
            continue
        boxes = "".join(b for _, b in where)
        # Two [~] declines of one thing is untidy but harmless. A MIX is the failure that keeps
        # resurfacing: one copy ticked, another still open.
        if len(set(boxes)) > 1:
            problems.append(
                "       DUPLICATE with mixed state: '%s' at %s\n"
                "           one copy was ticked and another was not - the untouched one keeps "
                "surfacing as 'next'" % (key[:52], ", ".join("L%d[%s]" % w for w in where)))

    if not problems:
        if not quiet:
            print("spec OK - %d items, no open-but-built, no mixed duplicates" % len(all_items))
        return 0
    if not quiet:
        print("spec: %d thing(s) to look at" % len(problems))
        for p in problems:
            print("  " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
