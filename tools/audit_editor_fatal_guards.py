"""Where does this plugin know an operation would KILL the editor, and does every door check?

WHY THIS EXISTS, and it is one evening's mistake generalised. On 2026-08-31 create_asset was taught
to refuse a UAnimSequence, because creating one leaves the sequencer data model without its MovieScene
and the next toucher asserts. Ninety minutes later duplicate_asset took the same editor down on the
same asset class, through a different engine path (EXCEPTION_ACCESS_VIOLATION reading 0x28 inside
AssetTools' DuplicateAsset). Two doors into one fragile class; one of them was locked.

The knowledge existed in the codebase and did not travel. create_asset's refusal and
duplicate_asset's cooked-asset guards live in different files and neither mentions the other, so
nothing - no tool, no comment, no test - would have said "this class is already known to kill the
editor somewhere else".

WHAT IT DOES. Collects every refusal string in the module that says an operation would crash,
terminate or assert the editor, and reports them grouped BY CLASS: which classes are known fatal,
and which handlers guard them. A class guarded at one door and not another is then visible in the
listing rather than discoverable by crashing.

WHAT IT DOES NOT DO, said plainly so the output is not over-read. It cannot know whether an unguarded
endpoint would ACTUALLY crash on that class - only that one part of this codebase believes the class
is fragile and another part handles it without saying so. That is a reading list, not a defect list.
Some pairings are fine: reading an asset is safe in every case guarded here, and it is specifically
creation, duplication or rebuild that dies.

NOT A GATE. Exits 0 always, the same rule audit_advice_gaps.py runs on - a tool that failed the build
over prose would be satisfied by rewording the prose, which makes the source worse.

Usage:
    python tools/audit_editor_fatal_guards.py            # classes and the handlers that guard them
    python tools/audit_editor_fatal_guards.py --sites    # every site, with its file and line

Talks to nothing. Source only.
"""
import argparse
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "Source", "MifBridge", "Private")

sys.path.insert(0, HERE)
import harvest_param_table as H          # the one function-body walk

# The vocabulary this codebase actually uses when it means "this would take the process down". Drawn
# from the sites that exist rather than invented: CRASHES/TERMINATE in refusal text, plus the engine
# failure shapes those refusals cite.
FATAL = re.compile(
    r"(CRASHES the editor|crashes the editor|TERMINATE THE EDITOR|terminates? the editor|"
    r"takes? the editor down|took the editor down|kills the editor|process gone|"
    r"EXCEPTION_ACCESS_VIOLATION|Assertion failed)")

# UE class names cited inside those strings. Deliberately narrow - a capitalised identifier that
# looks like a UObject class - because the point is to group by CLASS, and a looser rule turns the
# grouping into noise.
CLASSISH = re.compile(r"\b(?:U|A|F)?([A-Z][A-Za-z0-9]*(?:Asset|Sequence|Mesh|System|Emitter|Enum|"
                      r"Struct|Texture|Material|Blueprint|Component|Actor|Node|Graph|Skeleton))\b")

# ADJACENT LITERALS ARE THE HOUSE STYLE HERE, and the first version of this regex assumed one
# fragment:
#
#     TEXT("'%s' is a COOKED %s, and duplicating one CRASHES the editor %s. Cook strips "
#          "the editor-only data the post-duplicate rebuild step then dereferences. ")
#
# Requiring a closing paren immediately after the body matched none of the multi-line refusals -
# which is every refusal in this module worth reading - so the scan found 5 sites and missed the
# two that motivated it. Capture the whole RUN of fragments instead. This is the fourth
# regex-too-simple bug of the day; C++ string concatenation across lines is not the exception in
# this codebase, it is the norm, and a scanner that does not expect it reads a fraction of the
# source and reports a clean number.
TEXT_LIT = re.compile(r'TEXT\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\)')


def sites():
    """[(handler, class_or_None, file, line, quote)] for every fatal-sounding refusal string."""
    found = []
    for path in sorted(glob.glob(os.path.join(SRC, "*.cpp"))):
        raw = io.open(path, encoding="utf-8", errors="replace").read()
        scrubbed = H.blank_comments_and_strings(raw)
        base = os.path.basename(path)
        for fn, start, end in H.function_spans(raw, scrubbed):
            who = fn[2:] if fn.startswith("H_") else "helper " + fn
            for m in TEXT_LIT.finditer(raw, start, end):
                body = m.group(1)
                if not FATAL.search(body):
                    continue
                line = raw.count("\n", 0, m.start()) + 1
                classes = sorted(set(CLASSISH.findall(body)))
                found.append((who, classes, base, line, " ".join(body.split())[:150]))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sites", action="store_true", help="list every site rather than the grouping")
    args = ap.parse_args()

    rows = sites()
    if len(rows) < 5:
        print("SELF-CHECK FAILED: only %d fatal-guard strings found. This module is known to carry "
              "more than that, so the scan has drifted." % len(rows))
        return 2

    handlers = sorted({r[0] for r in rows})
    print("refusal strings naming an editor-fatal outcome: %d, across %d handler(s)"
          % (len(rows), len(handlers)))
    print("")

    if args.sites:
        for who, classes, base, line, quote in rows:
            print("  %-32s %s:%d" % (who, base, line))
            print("      %s" % quote)
            if classes:
                print("      classes: %s" % ", ".join(classes))
        return 0

    by_class = {}
    unclassed = []
    for who, classes, base, line, quote in rows:
        if not classes:
            unclassed.append((who, base, line, quote))
        for c in classes:
            by_class.setdefault(c, set()).add(who)

    print("KNOWN-FATAL CLASSES, and the doors that check them:")
    for c in sorted(by_class):
        guards = sorted(by_class[c])
        flag = "  <-- ONE DOOR ONLY" if len(guards) == 1 else ""
        print("  %-22s %s%s" % (c, ", ".join(guards), flag))
    print("")
    if unclassed:
        print("FATAL GUARDS NAMING NO CLASS - a condition rather than a type:")
        for who, base, line, quote in unclassed:
            print("  %-32s %s:%d" % (who, base, line))
    print("")
    print("NOT EVERY NAME ABOVE IS A CLASS. The grouping reads capitalised identifiers out of the")
    print("refusal text, and those texts quote assert messages and callstacks - so DuplicateAsset is")
    print("an AssetTools function and OwningNode is the variable an engine assert names, not types.")
    print("Tightening the pattern to exclude them would also drop real classes named in the same")
    print("sentences, and this is a reading list: a reader who sees a function name loses ten")
    print("seconds, where a reader who never sees a real class loses an editor.")
    print("")
    print("A class listed against ONE handler is not automatically a gap - reading an asset is safe")
    print("in every case guarded here, and it is creation, duplication or rebuild that dies. But it")
    print("IS the shape that cost two editors on 2026-08-31: AnimSequence was guarded in create_asset")
    print("and not in duplicate_asset, and nothing connected the two.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
