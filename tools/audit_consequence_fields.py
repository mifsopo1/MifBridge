"""Which response fields report a CONSEQUENCE the caller did not ask for, and does any suite read one?

WHY THESE FIELDS AND NOT ALL 2000. Most response fields answer the question that was asked -
`count`, `guid`, `path`. A smaller set exists to report something the caller did NOT ask about and
cannot otherwise see: a property that did not take, an actor that could not be placed, a rollback
that could not put everything back. That is the read-back surface an agent depends on when something
goes PARTIALLY wrong, which is the case where a wrong answer costs most.

The concrete lesson is move_tree_widget. It answers a root swap with displacedRoot,
displacedSubtreeSize and a warning that the old subtree "will not render" - a displaced root does not
vanish from the asset, it stops being MOUNTED - and nothing in its ok:true would tell you. Those
three were asserted by nothing until T435.

WHY THIS IS A TOOL AND NOT A LIST. The 48 in FEATURE_PARITY_SPEC.md were counted by hand once. A
number nothing recomputes is a number that will be wrong again next month - harvest_param_table.py
says exactly that about the table it regenerates, having watched it go stale twice. It was already
drifting when this was written: the spec names propertiesFailed as asserted by nothing, and
test_inherited_components T295 asserts it.

WHAT COUNTS AS READ, and this is the whole point of the tool. A suite must contain a string whose
WHOLE VALUE is the field name. modalHazard was in the hand-counted list even though a test had
"asserted" it an hour earlier, because that test checked whether the string "modal" appeared
ANYWHERE in the response, which a note mentioning modals satisfies just as well - so a name inside a
check LABEL like "T295 propertiesFailed names the one that was bad" is not a read, and an exact-value
rule drops every label of that shape.

The first version of that rule demanded a SUBSCRIPT and was wrong within the hour: test_rollback_real
drives `resp[k] for k in ("rollbackUnresolvedPins", "rollbackLostLinks")`, so both fields read as
unasserted while a suite was asserting them. A scanner that understands one spelling of a read
manufactures the backlog it was written to measure.

NOT ALL OF THEM ARE REACHABLE, and that is an answer rather than an excuse. discardedUnsaved on
remove_sublevel needs discardUnsaved, which mifaudit's FORBIDDEN_KEYS strips from every payload on
purpose, and reaching remove_sublevel at all needs a sublevel, which needs a SAVED .umap. Anything
gated behind saving or discarding unsaved work is out of scope for an unattended suite by the
standing rules. Those live in UNREACHABLE below, with the reason, rather than sitting in the backlog
looking undone.

PARSING. Comments are blanked with harvest_param_table's shared scrubber before anything is located,
because a comment discussing a field would otherwise read as an emission - the same trap that let a
comment about MIF_WITH_METASOUND mark that dependency used in parity_check. The field NAME is then
read from the ORIGINAL text at the same offset, because the scrubber blanks string bodies too and
the name is a string. Locate on the scrubbed copy, extract from the raw one.

Usage:
    python tools/audit_consequence_fields.py            # the backlog, exit 0
    python tools/audit_consequence_fields.py --check    # exit 1 if the backlog GREW past the baseline
    python tools/audit_consequence_fields.py --baseline # re-record the count after closing some
    python tools/audit_consequence_fields.py --all      # every consequence field, covered or not

Talks to nothing. Source and suites, both static.
"""
import argparse
import ast
import glob
import io
import os
import re
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "Source", "MifBridge", "Private")

sys.path.insert(0, HERE)
import harvest_param_table as H            # the one scrubber and the one function-body walk

# --------------------------------------------------------------------------- what counts

# A field is a CONSEQUENCE field if its name says something did not happen, happened to something
# else, or happened and was undone. Curated rather than inferred: an inferred rule over 2090 field
# names would need prose to justify each call, and prose is what this repo keeps catching itself
# trusting. Every entry here is a claim that can be checked by reading the emitter.
CONSEQUENCE = re.compile(
    r"(?:^|[a-z])("
    r"[Ff]ailed|[Ff]ailure|[Dd]ropped|[Ss]kipped|[Rr]everted|[Dd]iscarded|[Dd]isplaced|"
    r"[Oo]rphaned|[Ll]eftBehind|[Ll]ost|[Uu]nresolved|[Uu]nchanged|[Rr]ejected|[Rr]emoved|"
    r"[Tt]runcated|[Cc]lamped|[Ss]ilentl|[Pp]artial|[Ss]tale|[Bb]roken|[Mm]issing|[Ii]nvalid|"
    r"[Ii]ncomplete"
    r")")

# WHAT THIS PATTERN CANNOT SEE, said out loud rather than left as a clean-looking number. It matches
# names that say something went WRONG. It does NOT match names that say something merely MOVED -
# `axisChanged` on set_blendspace_samples is as much an unasked-for consequence as anything in the
# list above, and no name-based rule can separate it from the dozens of `changed` fields that are the
# honest ANSWER to "did this change anything". Adding [Cc]hanged would drown the real findings in
# them. So a side effect named for the thing it moved rather than for the failure it represents is
# invisible here, and has to be found by reading the handler - which is how axisChanged was found.

# Emitted by an endpoint the standing rules put out of reach of an unattended suite. Listed with the
# reason, because "no suite asserts it" and "no suite CAN assert it" are different findings and only
# one of them is work.
UNREACHABLE = {
    "discardedUnsaved": "remove_sublevel needs discardUnsaved, which mifaudit's FORBIDDEN_KEYS "
                        "strips on purpose, and needs a sublevel, which needs a SAVED .umap",
    "discardedNote": "emitted beside discardedUnsaved, same gate",
}

FIELD_CALL = re.compile(r"Out->Set(?:String|Number|Bool|Array|Object|StringArray)Field\s*\(")
TEXT_LIT = re.compile(r'TEXT\("([A-Za-z_][A-Za-z0-9_]*)"\)')
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A FILE OF NEEDLES IS NOT A FILE OF READS. audit_detectors_fire.py plants a defect for every
# detector, so its source contains, by construction, a literal of whatever each plant targets - here
# `.get("propertiesFailed")`. Scanning it made this tool report that field as read BY THE HARNESS,
# which meant the plant could never make it unread and the harness called this tool asleep. That is
# the third time in this repo a probe has declared itself: a MIF_BIND probe, a blocking probe whose
# marker was the word its own declaration contained, and now this. If a scanner reads the corpus,
# the corpus includes the thing doing the planting.
NOT_A_READER = {"audit_detectors_fire.py"}

# The backlog as measured on 2026-08-31, so this can be ratcheted rather than left failing forever -
# the same shape param_reach.py runs on, and for the same reason: a check that fails on the whole
# existing backlog gets switched off instead of fixed.
BASELINE_FILE = os.path.join(HERE, "consequence_fields_baseline.txt")


def emitted_fields():
    """[(field, endpoint_or_helper, file, line)] for every Out->Set*Field in a function body."""
    rows = []
    for path in sorted(glob.glob(os.path.join(SRC, "*.cpp"))):
        raw = io.open(path, encoding="utf-8", errors="replace").read()
        scrubbed = H.blank_comments_and_strings(raw)
        base = os.path.basename(path)
        for name, start, end in H.function_spans(raw, scrubbed):
            for m in FIELD_CALL.finditer(scrubbed, start, end):
                # Locate on the scrubbed copy, EXTRACT FROM THE RAW ONE - the scrubber blanks
                # string bodies, and the field name is a string.
                lit = TEXT_LIT.match(raw, m.end())
                if not lit:
                    continue
                line = raw.count("\n", 0, m.start()) + 1
                who = name[2:] if name.startswith("H_") else name
                rows.append((lit.group(1), who, base, line, name.startswith("H_")))
    return rows


def suite_reads():
    """Every field name a suite NAMES as a field, mapped to the suites that do it.

    A STRING WHOSE WHOLE VALUE IS THE FIELD NAME, which is narrower and wider than it first looks,
    and both on purpose.

    Narrower than "the name appears in the file". modalHazard was in the hand-counted backlog even
    though a test had "asserted" it an hour before, because that test looked for the substring
    "modal" anywhere in the response - which a note mentioning modals satisfies. A check LABEL like
    "T295 propertiesFailed names the one that was bad" CONTAINS the field and is not a read of it.
    Requiring the literal to equal the name exactly drops every label.

    Wider than "the name is subscripted", which was this function's first rule and was wrong within
    an hour of being written. test_rollback_real does:

        residue = {k: resp[k] for k in ("rollbackUnresolvedPins", "rollbackLostLinks") ...}

    The subscript is resp[k] - a VARIABLE - and the names live in a tuple, so a rule that demanded
    the literal inside the brackets reported both fields as read by nothing while a suite
    asserted them. Driving names from a
    tuple is ordinary Python, and a scanner that only understands one spelling of a read manufactures
    exactly the false backlog it was written to measure.

    Comments are stripped with tokenize rather than a regex, because a regex for a Python comment
    finds one inside a string literal - and these suites are full of JSON in strings. Strings are
    KEPT, unlike the C++ side: here the field name IS the string being looked for.
    """
    reads = {}
    for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))
                       + glob.glob(os.path.join(HERE, "audit_*.py"))
                       + glob.glob(os.path.join(HERE, "fuzz_*.py"))):
        if os.path.basename(path) in NOT_A_READER:
            continue
        try:
            with io.open(path, "rb") as fh:
                toks = list(tokenize.tokenize(fh.readline))
        except Exception:                      # a file this tool cannot tokenize is not a verdict
            continue
        base = os.path.basename(path)
        for t in toks:
            if t.type != tokenize.STRING:
                continue
            try:
                val = ast.literal_eval(t.string)
            except Exception:
                continue                        # f-strings and the like: not a plain field name
            if isinstance(val, str) and IDENT.match(val):
                reads.setdefault(val, set()).add(base)
    return reads


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="list every consequence field, not just gaps")
    ap.add_argument("--check", action="store_true", help="exit 1 if the backlog grew past baseline")
    ap.add_argument("--baseline", action="store_true",
                    help="write the current count as the baseline (never done by --check)")
    args = ap.parse_args()

    rows = emitted_fields()
    if len(rows) < 500:
        print("SELF-CHECK FAILED: only %d response fields found across %s."
              % (len(rows), os.path.basename(SRC)))
        print("The extraction has drifted - do not trust a clean result until this is resolved.")
        return 2

    reads = suite_reads()
    if "guid" not in reads:
        print("SELF-CHECK FAILED: no suite appears to read 'guid', which is not credible.")
        print("The suite scan is broken, so every field below would read as UNCOVERED.")
        return 2

    consequence = {}
    for field, who, base, line, is_handler in rows:
        if not CONSEQUENCE.search(field):
            continue
        consequence.setdefault(field, []).append((who, base, line, is_handler))

    covered, gaps, unreachable = [], [], []
    for field in sorted(consequence):
        if field in UNREACHABLE:
            unreachable.append(field)
        elif field in reads:
            covered.append(field)
        else:
            gaps.append(field)

    total_fields = len({r[0] for r in rows})
    print("response fields emitted        : %d distinct, %d call sites" % (total_fields, len(rows)))
    print("of those, CONSEQUENCE fields   : %d" % len(consequence))
    print("  read by a suite              : %d" % len(covered))
    print("  out of reach by the rules    : %d" % len(unreachable))
    print("  NO suite reads them          : %d" % len(gaps))
    print("")

    if args.all and covered:
        print("READ BY A SUITE:")
        for f in covered:
            print("  %-32s %s" % (f, ", ".join(sorted(reads[f])[:3])))
        print("")
    if unreachable:
        print("OUT OF REACH - not work, and not a gap:")
        for f in unreachable:
            print("  %-32s %s" % (f, UNREACHABLE[f]))
        print("")
    if gaps:
        print("NO SUITE READS THESE:")
        for f in gaps:
            sites = consequence[f]
            where = sites[0]
            extra = " (+%d more)" % (len(sites) - 1) if len(sites) > 1 else ""
            print("  %-32s %s  %s:%d%s"
                  % (f, "endpoint " + where[0] if where[3] else "helper " + where[0],
                     where[1], where[2], extra))
        print("")

    if args.baseline:
        io.open(BASELINE_FILE, "w", encoding="utf-8", newline="").write("%d\r\n" % len(gaps))
        print("baseline written: %d unread consequence field(s). Commit it." % len(gaps))
        return 0

    if args.check:
        # --check NEVER WRITES. Ratcheting automatically on a shrink sounds tidy and means the
        # threshold can move without anybody reviewing the move - including a shrink caused by a
        # suite being deleted rather than a field being covered. The number is committed, so a
        # change to it shows up in a diff like any other claim.
        prev = None
        if os.path.isfile(BASELINE_FILE):
            try:
                prev = int(io.open(BASELINE_FILE, encoding="utf-8").read().split()[0])
            except Exception:
                prev = None
        if prev is None:
            print("no baseline at %s - run with --baseline once and commit it."
                  % os.path.basename(BASELINE_FILE))
            return 2
        if len(gaps) > prev:
            print("FAIL: %d unread consequence fields, baseline %d. A field reports something the "
                  "caller did not ask for and cannot see, and nothing reads it." % (len(gaps), prev))
            for f in gaps:
                print("  unread: %s" % f)
            return 1
        if len(gaps) < prev:
            print("OK  backlog shrank %d -> %d. Re-baseline with --baseline and commit."
                  % (prev, len(gaps)))
            return 0
        print("OK  %d unread, unchanged from baseline." % len(gaps))
        return 0

    print("A mention is not a read, and a read is not an assertion - this tool only proves the")
    print("field is INDEXED somewhere. Read the check before believing it tests anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
