"""Compare a project's vendored MifBridge against this tree IN BOTH DIRECTIONS, before overwriting it.

WHY THIS EXISTS, and it is not hypothetical. On 2026-09-05, preparing a 5.7 sweep against Curfew,
`git status` in D:/RoguelikeDealerGame showed 431 UNCOMMITTED lines in its vendored copy: 427 in
MifBridgeLandscape.cpp plus the declare-and-bind pair for import_landscape_heightmap and
export_landscape_heightmap. Two whole endpoints that exist nowhere in this tree.

docs/14's documented sync is "extract the release zip over the project's Plugins/ folder". Run that
day, it would have destroyed all 431 lines, and because they were uncommitted git could not have
recovered one of them. The only thing that prevented it was looking first, by hand, because the
vendored copy happened to be stale in a way worth checking.

docs/14 ALREADY DESCRIBES THIS EXACT LOSS HAPPENING BEFORE: "Curfew was 62 endpoints behind, missing
11 whole source files... Work was being lost in both directions until the field reports were merged
back by hand." The procedure it then documents is safe only in the direction it assumes, and nothing
checked the other one. This is that check.

=============================================================================
WHAT IT COMPARES, AND WHY IT IS ENDPOINTS RATHER THAN LINES
=============================================================================
A line-level diff of the two trees reported 1700 lines "only in Curfew" and was useless: almost all
of them were the OLD version of a line since edited here, which is drift in the safe direction
wearing the same shape as drift in the dangerous one. A diff that cannot tell those apart is a diff
nobody will read twice.

Endpoint NAMES do tell them apart. MIF_DECL and MIF_BIND names are declared once, are stable across
edits, and a name present in the copy and absent here is unique work by definition - there is no
version of "this endpoint was edited" that produces one. So the blocking question is asked of names,
and the line counts are reported underneath as context rather than as a verdict.

FILES ONLY IN THE COPY are the other blocking case, and the cheaper one to reason about: docs/14
records eleven of them going missing once already.
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DECL = re.compile(r"\bMIF_(?:DECL|BIND)\s*\(\s*(\w+)\s*\)")
CODE = (".cpp", ".h", ".cs", ".py")


def endpoints(root):
    """Every MIF_DECL/MIF_BIND name under a tree. The macro DEFINITION is excluded.

    `#define MIF_DECL(Name) ...` matches this regex exactly as a real declaration does, and counting
    it is the off-by-one that made every release badge one too high from 0.3.0 to 2026-09-02. It is
    excluded by name rather than by line, because the definition can move.
    """
    found = set()
    for base, _, names in os.walk(root):
        for n in names:
            if not n.endswith((".cpp", ".h")):
                continue
            try:
                src = io.open(os.path.join(base, n), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in DECL.finditer(src):
                if m.group(1) != "Name":
                    found.add(m.group(1))
    return found


def code_files(root):
    out = {}
    for base, _, names in os.walk(root):
        if any(p in base for p in ("Binaries", "Intermediate", "__pycache__", ".git")):
            continue
        for n in names:
            if n.endswith(CODE):
                out[os.path.relpath(os.path.join(base, n), root).replace("\\", "/")] = \
                    os.path.join(base, n)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", help="the vendored copy, e.g. D:/RoguelikeDealerGame/Plugins/MifBridge")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the copy holds anything this tree does not")
    args = ap.parse_args()

    other = os.path.abspath(args.path)
    if not os.path.isdir(other):
        print("no such directory: %s" % other)
        return 2

    mine, theirs = endpoints(ROOT), endpoints(other)
    fm, ft = code_files(ROOT), code_files(other)

    only_there = sorted(theirs - mine)
    only_here = sorted(mine - theirs)
    files_only_there = sorted(set(ft) - set(fm))

    print("comparing %s" % other)
    print("  this tree : %d endpoint(s), %d code file(s)" % (len(mine), len(fm)))
    print("  the copy  : %d endpoint(s), %d code file(s)" % (len(theirs), len(ft)))
    print("")

    if only_there:
        print("THE COPY HAS %d ENDPOINT(S) THIS TREE DOES NOT. Overwriting it would destroy them,"
              % len(only_there))
        print("and if they are uncommitted there - check `git status` in that project - git cannot")
        print("bring them back. Merge them here FIRST:")
        for e in only_there:
            print("    %s" % e)
        print("")
    if files_only_there:
        print("AND %d FILE(S) EXIST ONLY IN THE COPY:" % len(files_only_there))
        for f in files_only_there[:20]:
            print("    %s" % f)
        print("")
    if not only_there and not files_only_there:
        print("nothing in the copy is missing from this tree - a sync would lose no work.")
        print("")

    if only_here:
        print("This tree is ahead by %d endpoint(s), which is the SAFE direction and what a sync"
              % len(only_here))
        print("is for. Listed for completeness, not as a problem:")
        print("    %s" % ", ".join(only_here[:14]))
        if len(only_here) > 14:
            print("    ... and %d more" % (len(only_here) - 14))
        print("")

    print("REACH - what this does NOT tell you:")
    print("  It compares endpoint NAMES and file presence. A line-level diff was tried and is")
    print("  useless here: it reported 1700 lines 'only in the copy' and nearly all of them were")
    print("  the OLD version of a line since edited in this tree - drift in the safe direction")
    print("  wearing the shape of the dangerous one. Names cannot do that, which is why they are")
    print("  what the verdict rests on.")
    print("  It also cannot see edits to an endpoint that exists in both. Read the diff for those.")

    if args.check and (only_there or files_only_there):
        print("")
        print("BLOCKING: the copy holds %d endpoint(s) and %d file(s) this tree does not."
              % (len(only_there), len(files_only_there)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
