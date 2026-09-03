"""Does any OPEN issue heading in docs/06 contradict its own body or the file's status table?

WHY THIS EXISTS. On 2026-09-03 twelve entries in docs/06_OPEN_ISSUES_FROM_USE.md - seventeen separate
defects - were found fixed while still reading as OPEN in the section index. One of them was an
EDITOR-FATAL crash with a crash GUID, fixed for over a week, sitting at the top of what a reader
triaging for danger picks up first.

The shape was consistent. Every layer that records a fix gets updated except the one people read:

    3 entries  the entry's own BODY says "Fixed and verified"        heading did not
    4 entries  the file's own STATUS TABLE says "FIXED + verified"   heading did not
    5 entries  only the source said so                               nothing in the file did

This catches the first two. It cannot catch the third, and a companion attempt at that was written
the same day and DELETED - see docs/02, "You cannot detect a stale ABSENCE claim by name-matching":
an entry describing something that is not there has no identifier to match on, so that detector
flagged two of three cases for the wrong reason and missed the cleanest one. This one is different in
kind, and the difference is the whole reason it is worth having: it compares two statements that BOTH
EXIST in the same file and disagree. That is a fact, not a heuristic.

IT REPORTS, IT DOES NOT DECIDE. A heading that lags its body is not automatically stale - a body can
say "fixed" about one half of a two-part entry, which is why issue 1 was QUALIFIED rather than marked
FIXED after reading it. Read the entry. This only says which ones are worth re-reading, which is the
question that went unasked for a month.

  python tools/audit_issue_headings.py            the reading list
  python tools/audit_issue_headings.py --plant    prove it sees a known contradiction
  python tools/audit_issue_headings.py --file X   run against another copy (used to validate against
                                                  git history, where the answer is already known)
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT = os.path.join(ROOT, "docs", "06_OPEN_ISSUES_FROM_USE.md")

# A heading carrying any of these already announces its own state and is not re-read.
# EVERY MARKER THIS FILE ACTUALLY USES. "resolved" belongs here as well as in BODY_FIXED: the
# detector flagged the very entry it had just found, because the heading was marked RESOLVED and only
# the FIXED spellings were recognised as closed. A detector that cannot read its own fix re-reports it
# forever.
CLOSED = ("fixed", "resolved", "not a defect", "hazard record", "not a mifbridge",
          "capability is built", "all seven", "was built", "still open", "also correct",
          "note on", "downgraded")

# The body claiming its own defect is closed.
# BOLD IS LOAD-BEARING ON THE RESOLVED ARM, and the narrowness is measured rather than guessed. This
# file marks a verdict in bold; the unbolded word means something else entirely. Entry 21 contains
# "a relative path is resolved against the bridge's own export root" inside a CODE COMMENT, and entry
# 3 says "not a defect so much as a sharp edge" while remaining a live entry. Accepting bare
# `resolved` or bare `not a defect` gave one true positive and two false ones; the bolded verdict form
# alone gives one true positive and none.
BODY_FIXED = re.compile(
    r"(\*\*Fixed(?: and verified)?\*\*|\bboth are fixed\b|\bis fixed\b|\bare fixed\b|"
    r"\bfixed and verified\b|\bnow fixed\b|\bhas been fixed\b|\*\*RESOLVED\b[^*]*\*\*|"
    r"\bpositive example\b|\bNOT a MifBridge defect\b)", re.I)
# `positive example` is a slightly different claim - not "this defect is closed" but "this was never
# a defect, it is here as an example of good behaviour" - and it has the same consequence for a
# reader: the heading asserts a problem the body denies. The phrase is exact for a reason. "Not a
# bug" hits two entries, and in entry 21 it settles one of three sub-questions rather than the entry,
# so it would report a live issue as answered.

# A status-table row: | ... | <subject> | **FIXED ...** |  - the section it refers to is named as
# a paragraph reference, and that is what ties a row to a heading.
SECTION_REF = re.compile(r"§\s*(\d+)")
ROW_FIXED = re.compile(r"\bFIXED\b|\bVERIFIED\b")
HEAD_NUM = re.compile(r"^##\s*(\d+)\.")


STOP = set("the a an and or of to in on is are was were it its this that with for from by not "
           "no any every all does do did cannot can could would should still open fixed verified "
           "issue when even given returns return reports report".split())


def _words(s):
    return {w for w in re.findall(r"[a-z_][a-z0-9_]{2,}", s.lower()) if w not in STOP}


def _shares_subject(heading, row, need=2):
    """Does the status row talk about the same thing as the heading?

    Guards duplicate section numbers - see the call site. `need` is 2 because one shared word is
    routinely a coincidence ("package", "asset") while two rarely are.
    """
    return len(_words(heading) & _words(row)) >= need



def sections(text):
    parts = re.split(r"^(## .*)$", text, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def table_says_fixed(text):
    """{section number: row text} for status rows that claim FIXED and name a section."""
    out = {}
    for line in text.split("\n"):
        if not line.startswith("|") or not ROW_FIXED.search(line):
            continue
        # THE FIRST REFERENCE IDENTIFIES THE ROW; LATER ONES ARE CROSS-REFERENCES. Mapping a row to
        # every section it mentions attributed the CallArrayFunction row - subject "(§5)" but citing
        # §4 further along - to issue 4 as well, which is wrong-row attribution: the same defect that
        # got the companion absence-detector deleted the same day. One row, one subject.
        refs = SECTION_REF.findall(line)
        if refs:
            out.setdefault(int(refs[0]), " ".join(line.split())[:110])
    return out


def scan(text):
    rows = table_says_fixed(text)
    hits = []
    for heading, body in sections(text):
        low = heading.lower()
        if any(c in low for c in CLOSED):
            continue
        # NUMBERED ISSUE SECTIONS ONLY. "## Status" is a meta-section ABOUT the entries and quotes
        # their state ("issue 14 is fixed"), so a body scan flags it every run for saying exactly what
        # it is there to say.
        m = HEAD_NUM.match(heading)
        if not m:
            continue
        num = int(m.group(1))
        why = []
        bm = BODY_FIXED.search(body)
        if bm:
            ctx = " ".join(body[max(0, bm.start() - 60):bm.start() + 80].split())
            why.append("BODY says %s  ... %s" % (repr(bm.group(0)), ctx[:110]))
        # THE NUMBER ALONE IS NOT AN IDENTITY. This file has TWO numbering sequences - there is a
        # "## 5." and a "## 8." in each - so a row citing §5 matches two different headings, and
        # matching on the number alone attributed the DataTable row (§8) to
        # "save_dirty_packages cannot commit a DELETED package" (the other §8). Both false positives
        # looked like new findings until they were read.
        #
        # So the row must also SHARE CONTENT with the heading. Two distinctive words is enough to
        # separate "No way to CREATE a DataTable asset" from "save_dirty_packages cannot commit a
        # DELETED package", and no weaker rule survives duplicate numbering.
        if num in rows and _shares_subject(heading, rows[num]):
            why.append("STATUS TABLE: %s" % rows[num])
        if why:
            hits.append((heading, why))
    return hits


def main():
    path = DEFAULT
    if "--file" in sys.argv:
        path = sys.argv[sys.argv.index("--file") + 1]
    if not os.path.isfile(path):
        print("no %s - nothing to check." % path)
        return 0
    text = io.open(path, encoding="utf-8", errors="replace").read()

    if "--plant" in sys.argv:
        # PLANTED IN MEMORY, NEVER ON DISK. This reads a documentation file a person edits by hand;
        # a killed run must not be able to leave a fabricated issue in it.
        planted = text + ("\n## 998. A planted entry whose body contradicts it\n\n"
                          "Reported and then **Fixed and verified** 2026-01-01. Left open on purpose\n"
                          "so the detector has something it must see.\n")
        hits = [h for h in scan(planted) if h[0].startswith("## 998.")]
        seen = bool(hits) and any("BODY says" in w for w in (hits[0][1] if hits else []))
        print("PLANT  entry seen=%s  for the BODY contradiction=%s" % (bool(hits), seen))
        print("\n%s" % ("PLANT SEEN FOR THE RIGHT REASON - a clean run is worth something" if seen
                        else "PLANT NOT SEEN AS MINE - a clean run would mean NOTHING"))
        return 0 if seen else 1

    hits = scan(text)
    print("%s\n%d open section(s) contradicted by their own body or the status table\n"
          % (os.path.basename(path), len(hits)))
    if not hits:
        print("OK  every open heading agrees with the body under it and with the status table.")
    else:
        print("WORTH RE-READING - the heading says open, something else in this same file says")
        print("otherwise. Read the entry before marking it: a body can be fixed about one half.\n")
        for heading, why in hits:
            print("  %s" % heading[:94])
            for w in why:
                print("      %s" % w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
