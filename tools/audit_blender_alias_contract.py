"""A parameter the docstring gives its own meaning, that take() reads as an alias of another one.

WHAT THIS CATCHES, and it caught two on the day it was written. In this addon a call like

    obj = get_object(take(params, "object", "name", required=True))

declares that `name` is a SPELLING OF `object` - the same parameter, two names. If the op's docstring
also lists `name` with a meaning of its own:

    name (str)   constraint name

then the contract and the reader disagree, and the caller loses every way round it:

  * sending both, as the docstring implies, is refused - "'object' and 'name' are two names for the
    same parameter and were given different values"
  * sending `name` alone is read as the OBJECT, so it fails with "no object named 'MifConA'"
  * the thing the docstring promised - naming the constraint - is unreachable, and the parameter
    that does it has a different name entirely (constraintName)

MEASURED, NOT INFERRED. Both findings were confirmed against a live headless Blender 5.0 before
either was believed: add_constraint's `name` fails in all three forms while `constraintName` works,
and add_nla_strip's `name` fails while `stripName` works.

WHY audit_blender_dead_params DOES NOT CATCH IT. That tool asks which accepted parameters nothing
READS. `name` is read - as the object - so it passes there. The defect is not an unread parameter,
it is a parameter read as something other than what the contract says it is. Different question,
which is why it is a different file.

THE RULE IS DELIBERATELY NARROW. Only an alias declared in a take() call, only when the docstring
gives the alias its own params-block line, and only when that line's description does not itself say
it is the other parameter. An op is free to document `name (str) an ALIAS FOR object` and this stays
quiet - which is exactly how the two known cases were fixed, since `name` meaning `object` is the
convention everywhere else in the addon and changing it in two ops would make those the odd ones.
"""
import argparse
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

# take(params, "primary", "alias1", "alias2", ...) - positional names after params are one
# parameter under several spellings. Keyword arguments end the list.
TAKE = re.compile(r'\btake(?:_bool|_float|_int)?\(\s*params\s*,\s*((?:"[^"]+"\s*,\s*)*"[^"]+")')

# A params-block line: two or more spaces, the name, a parenthesised type, then a description.
DOC_LINE = re.compile(r'^\s{2,}(\w+)\s*\(([^)]*)\)\s{2,}(\S.*)$', re.M)

# A description that already says "I am really the other one" is the FIX, not the defect.
SAYS_ALIAS = re.compile(r'\balias\b|\bsame as\b|\bspelling of\b|\bnot for\b', re.I)


def ops_in(path):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("op_"):
            yield node.name, (ast.get_docstring(node) or ""), (ast.get_source_segment(src, node) or "")


def aliases(body):
    """{alias: primary} for every multi-name take() in this op."""
    out = {}
    for m in TAKE.finditer(body):
        names = re.findall(r'"([^"]+)"', m.group(1))
        if len(names) < 2:
            continue
        primary = names[0]
        for a in names[1:]:
            out.setdefault(a, primary)
    return out


def documented(doc):
    """{param: description} from the docstring's params block."""
    return {m.group(1): m.group(3).strip() for m in DOC_LINE.finditer(doc)}


def scan():
    findings = []
    for fn in sorted(os.listdir(ADDON)):
        if not (fn.startswith("ops_") and fn.endswith(".py")):
            continue
        for op, doc, body in ops_in(os.path.join(ADDON, fn)):
            al, docs = aliases(body), documented(doc)
            for alias, primary in sorted(al.items()):
                desc = docs.get(alias)
                if desc is None:
                    continue                      # not documented separately - nothing to disagree
                if SAYS_ALIAS.search(desc):
                    continue                      # says it is the other one; that is the fix
                if primary.lower() in desc.lower():
                    continue                      # describes itself in terms of the primary
                findings.append((fn, op, alias, primary, desc[:64], docs.get(primary, "")[:40]))
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on any finding")
    args = ap.parse_args()

    findings = scan()
    ops = sum(1 for fn in sorted(os.listdir(ADDON))
              if fn.startswith("ops_") and fn.endswith(".py")
              for _ in ops_in(os.path.join(ADDON, fn)))
    print("audit_blender_alias_contract: %d op(s) read, %d finding(s)" % (ops, len(findings)))

    if not findings:
        print("")
        print("no op documents an alias as if it were a parameter of its own.")
    for fn, op, alias, primary, desc, pdesc in findings:
        print("")
        print("  %s :: %s" % (fn, op))
        print("    take() reads %-14s as a spelling of %r" % ("'%s'" % alias, primary))
        print("    the docstring says   %s (...)  %s" % (alias, desc))
        print("    so a caller sending both is REFUSED as an alias conflict, and sending %r alone"
              % alias)
        print("    sets %r instead. Whatever the docstring promised is unreachable." % primary)

    print("")
    print("REACH - what this audit can and cannot judge:")
    print("  covered      alias groups declared in a take() call, against the op's own params block")
    print("  NOT covered  aliases resolved anywhere else - a dict, a shared resolver, getattr - and")
    print("               any disagreement between a docstring and an implementation that is not")
    print("               spelled as an alias. This is one narrow shape, not contract checking.")
    print("  It says nothing about the C++ side, which has its own contract check in")
    print("  harvest_param_table's CONTRACT DRIFT.")

    if args.check and findings:
        print("")
        print("BLOCKING: %d documented parameter(s) are read as a spelling of something else."
              % len(findings))
        print("Either document the alias as an alias, or give the promised parameter its own name.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
