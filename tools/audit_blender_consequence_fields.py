"""Which addon response fields report a CONSEQUENCE, and does any suite read one?

THE BLENDER ARM OF audit_consequence_fields.py. Read that file's header for the argument - a field
that reports something the caller did not ask for and cannot otherwise see is the read-back surface
an agent depends on when something goes PARTIALLY wrong, which is when a wrong answer costs most.

WHY IT EXISTS SEPARATELY. The UE tool parses C++ Out->Set*Field calls and carries a UE baseline. The
addon returns plain Python dicts and has its own backlog. Same question, two corpora, two ratchets -
the same split audit_dead_params.py and audit_blender_dead_params.py already use.

WHY IT EXISTS AT ALL. The eleven fields below were counted by hand on 2026-08-31, in a throwaway
script, and five were closed the same evening. A number nothing recomputes is a number that will be
wrong again next month - which is exactly what happened to the UE side's "48", counted once and
already drifting when it was next read. This makes the Blender figure derived.

NESTED FIELDS COUNT, and they are most of the interesting ones. clean_mesh reports vertsRemoved
inside steps.merged, not at the top level, so a scan that only looked at an op's outermost return
dict would miss the whole family. Every "key": literal inside an op body is collected.

WHAT COUNTS AS READ is a string whose WHOLE VALUE is the field name, appearing in a Blender suite or
audit - the same rule the UE arm settled on after getting it wrong twice. A name inside a check LABEL
is not a read of the field; a name driven from a tuple IS. Requiring a subscript would miss the
second, and matching the label would pass on the first.

Usage:
    python tools/audit_blender_consequence_fields.py            # the backlog
    python tools/audit_blender_consequence_fields.py --check    # exit 1 if it GREW past baseline
    python tools/audit_blender_consequence_fields.py --baseline # re-record after closing some

Talks to nothing. Addon source and Blender suites, both static.
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
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")
BASELINE_FILE = os.path.join(HERE, "blender_consequence_baseline.txt")

OP_DEF = re.compile(r"^def op_(\w+)\s*\(", re.M)
# ANY module-level def, not just op_ ones. Without this a helper sitting BETWEEN two ops has its
# fields credited to whichever op happens to precede it in the file - which sent a reader looking for
# seamVertsRemoved in export_mesh when it is reported by bevel_edges and extrude_skirt through
# _seam_verdict. The UE arm already labels these "helper <name>"; this one now does too, because a
# derived list is used as a to-do list and a wrong location wastes the reader rather than the tool.
ANY_DEF = re.compile(r"^def (\w+)\s*\(", re.M)
KEY = re.compile(r'''["']([A-Za-z_][A-Za-z0-9_]*)["']\s*:''')
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Curated, and every entry is a claim that can be checked by reading the emitter. A consequence field
# says something did not happen, happened to something else, or happened and was undone.
CONSEQUENCE = re.compile(
    r"(?:^|[a-z_])("
    r"failed|failure|dropped|skipped|reverted|discarded|displaced|orphaned|lost|unresolved|"
    r"rejected|removed|truncated|clamped|partial|stale|broken|missing|invalid|incomplete|"
    r"leftBehind|left_behind"
    r")", re.I)

# Reachable only through a path an unattended suite must not take, with the reason. Empty today.
UNREACHABLE = {}


def emitted():
    """field -> set(op) for every consequence-shaped key an op body returns, nested ones included."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ADDON, "ops_*.py"))):
        text = io.open(path, encoding="utf-8", errors="replace").read()
        bounds = [(m.group(1), m.start()) for m in ANY_DEF.finditer(text)]
        for i, (name, start) in enumerate(bounds):
            end = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
            where = name[3:] if name.startswith("op_") else "helper " + name
            for m in KEY.finditer(text, start, end):
                key = m.group(1)
                if CONSEQUENCE.search(key):
                    out.setdefault(key, set()).add(where)
    return out


def suite_reads():
    """Every field name a Blender suite or audit NAMES as a field."""
    reads = {}
    for path in sorted(glob.glob(os.path.join(HERE, "test_blender_*.py"))
                       + glob.glob(os.path.join(HERE, "audit_blender_*.py"))):
        base = os.path.basename(path)
        # A file of NEEDLES is not a file of reads - the same exclusion the UE arm needed once the
        # detector harness started containing a literal of everything it plants.
        if base == "audit_detectors_fire.py":
            continue
        try:
            with io.open(path, "rb") as fh:
                toks = list(tokenize.tokenize(fh.readline))
        except Exception:
            continue
        for t in toks:
            if t.type != tokenize.STRING:
                continue
            try:
                val = ast.literal_eval(t.string)
            except Exception:
                continue
            if isinstance(val, str) and IDENT.match(val):
                reads.setdefault(val, set()).add(base)
    return reads


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the backlog grew past baseline")
    ap.add_argument("--baseline", action="store_true", help="write the current count as the baseline")
    args = ap.parse_args()

    fields = emitted()
    if len(fields) < 5:
        print("SELF-CHECK FAILED: only %d consequence fields found across the addon - the scan has "
              "drifted." % len(fields))
        return 2
    reads = suite_reads()
    if "ok" not in reads:
        print("SELF-CHECK FAILED: no Blender suite appears to name 'ok', which is not credible.")
        return 2

    covered, gaps, unreachable = [], [], []
    for f in sorted(fields):
        if f in UNREACHABLE:
            unreachable.append(f)
        elif f in reads:
            covered.append(f)
        else:
            gaps.append(f)

    print("consequence-shaped response fields in the addon: %d" % len(fields))
    print("  read by a Blender suite     : %d" % len(covered))
    print("  out of reach by the rules   : %d" % len(unreachable))
    print("  NO suite reads them         : %d" % len(gaps))
    print("")
    for f in unreachable:
        print("  out of reach  %-26s %s" % (f, UNREACHABLE[f]))
    for f in gaps:
        print("  unread        %-26s %s" % (f, ", ".join(sorted(fields[f]))[:60]))
    print("")

    if args.baseline:
        io.open(BASELINE_FILE, "w", encoding="utf-8", newline="").write("%d\r\n" % len(gaps))
        print("baseline written: %d unread. Commit it." % len(gaps))
        return 0
    if args.check:
        # --check NEVER writes, for the reason the UE arm gives: a threshold that ratchets itself can
        # move without anybody reviewing the move, including a shrink caused by deleting a suite.
        prev = None
        if os.path.isfile(BASELINE_FILE):
            try:
                prev = int(io.open(BASELINE_FILE, encoding="utf-8").read().split()[0])
            except Exception:
                prev = None
        if prev is None:
            print("no baseline - run with --baseline once and commit it.")
            return 2
        if len(gaps) > prev:
            print("FAIL: %d unread, baseline %d. A field reports something the caller cannot see "
                  "and nothing checks it." % (len(gaps), prev))
            return 1
        if len(gaps) < prev:
            print("OK  backlog shrank %d -> %d. Re-baseline with --baseline and commit."
                  % (prev, len(gaps)))
            return 0
        print("OK  %d unread, unchanged from baseline." % len(gaps))
        return 0

    print("A name appearing in a suite proves it is READ, never that it is asserted against")
    print("anything. Read the check before believing the field is tested.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
