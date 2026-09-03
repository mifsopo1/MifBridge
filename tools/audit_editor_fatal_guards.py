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
# CASE-INSENSITIVE, AND IT COUNTS THE VERB IN EVERY TENSE THIS CODEBASE ACTUALLY WRITES.
#
# The first version listed exact-case alternatives - "CRASHES the editor|crashes the editor" - and
# on 2026-08-31 audit_detectors_fire caught it ASLEEP: a planted guard saying "would CRASH the
# editor" went unnoticed, because the pattern only knew the -ES form. That looked like a badly worded
# plant until the corpus was counted, and the corpus settled it:
#
#     CRASHES the editor 5   CRASHES THE EDITOR 3   crashes the editor 2   <- matched, 10
#     editor crash 3   EDITOR CRASH 2   would CRASH the editor 2           <- MISSED
#     hard crash 2   crash the editor 1   CRASH the editor 1   editor CRASH 1   would CRASH THE EDITOR 1
#
# It was seeing ten of twenty-three - fewer than half of the real citations, including guards written
# the same day. The plant was not wrong; it imitated the house style, which is exactly what a plant is
# for, and imitating it is what exposed the gap.
#
# Deliberately GENEROUS now. This tool prints a reading list and always exits 0, so a borderline
# extra line costs a reader ten seconds and a missed one costs an editor - the trade its own footer
# already argues for.
FATAL = re.compile(
    r"(crash(?:es|ed|ing)?\s+(?:the\s+)?editor|editor\s+(?:will\s+|would\s+|may\s+)?crash(?:es|ed)?|"
    r"hard\s+crash|terminates?\s+the\s+editor|TERMINATE THE EDITOR|"
    r"takes?\s+the\s+editor\s+down|took\s+the\s+editor\s+down|kills\s+the\s+editor|process gone|"
    r"EXCEPTION_ACCESS_VIOLATION|Assertion failed)", re.IGNORECASE)

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


ONE_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def joined_literal(body):
    """The string the COMPILER builds, not the source text of the literals.

    THIS IS NOT COSMETIC, AND ITS ABSENCE HID REAL GUARDS. TEXT_LIT captures a run of adjacent C++
    string literals with the quotes and the line breaks between them still in it. This file wraps at
    about 100 columns, so a phrase that matters lands across a wrap all the time:

        TEXT("'%s' CANNOT be created ... would TERMINATE THE "
             "EDITOR. A plain NewObject ...")

    Matched against the raw body, `TERMINATE THE EDITOR` is not present - what is present is
    `TERMINATE THE " "EDITOR`. So create_asset's UAnimSequence refusal, which exists precisely
    because a bare NewObject terminated the editor on 2026-08-31, was invisible to an audit whose
    entire job is finding editor-fatal guards. It appeared in NO list: not against the class, not
    even under 'naming no class'.

    That is the dangerous direction of error for this tool. Its own footer warns about the harmless
    one - a reader who sees a function name loses ten seconds - while a guard it cannot see at all
    reads as a guard that is not there.
    """
    return "".join(ONE_LITERAL.findall(body))


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
                # Adjacent literals are joined FIRST - see joined_literal(). Matching the raw source
                # text misses every phrase that straddles a line wrap, and in this file that is most
                # of them.
                body = joined_literal(m.group(1))
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
        # NOT ALL OF THESE ARE CONDITIONS, and calling them that was wrong. A refusal that
        # interpolates the class - Fail(..., FString::Printf(TEXT("'%s' CANNOT be created ...")))
        # - names a TYPE perfectly well; it just names it at runtime, where a static read cannot
        # see it. create_asset's UAnimSequence guard is exactly that, and it is the guard for the
        # type that terminated an editor on 2026-08-31.
        interp = [u for u in unclassed if "%s" in u[3]]
        print("FATAL GUARDS THIS CANNOT GROUP BY CLASS:")
        for who, base, line, quote in unclassed:
            mark = "  <-- names its class at RUNTIME (%s)" if "%s" in quote else ""
            print("  %-32s %s:%d%s" % (who, base, line, mark))
        if interp:
            print("")
            print("  %d of those interpolate the class into the message, so the door list above is a"
                  % len(interp))
            print("  LOWER BOUND: a class can show ONE DOOR ONLY while a second door guards it by")
            print("  name at runtime. Read these before believing any 'ONE DOOR ONLY' line.")
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
