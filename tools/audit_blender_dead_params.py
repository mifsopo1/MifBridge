"""Find Blender op parameters the addon ACCEPTS and nothing ever reads.

THE BLENDER ARM OF audit_dead_params.py, same question and same reasoning, different corpus. Read
that file's header for the argument; this one only records what differs.

WHY IT IS WORTH HAVING TWICE. reject_unknown is the addon's answer to the silent-parameter-ignore
class - send a name the op does not know and it refuses, loudly. The blind spot is the worse half: a
name ON the accepted list passes by definition, and if nothing then reads it the call succeeds,
reports ok, and does nothing with the thing the caller asked for. That is the same silent wrong
result the guard exists to end, arriving through the door the guard holds open.

Until 2026-08-31 the Blender half had two audit tools to the UE half's twenty, and neither asked this
question. It went unasked long enough that the answer was worth finding out.

SCOPE IS MODULE-WIDE AND DELIBERATELY PERMISSIVE, exactly as on the UE side. A key counts as read if
its literal appears anywhere in the op's own module outside the reject_unknown call - in a take()
call, in a shared resolver, in a mapping table. create_primitive is why: it accepts "fillType" and
reads it through a module-level dict, `EXTRA_KEYS = {"fillType": "fill_type", ...}`, so a scan
confined to the function body would call a working parameter dead. False positives are what kill an
audit tool's credibility, and the regression actually being guarded against - a name added to an
accept list and never wired up - appears nowhere else at all.

CASE MATTERS AND COST A WRONG ANSWER ALREADY. param_reach lowercases its keys, and a first pass at
this question compared those against the addon's camelCase source. decimate_mesh.targetTriangles and
create_primitive.fillType both read as dead and both are read perfectly well - the classifier was
matching "targettriangles" against a file that says "targetTriangles". Comparison here is
case-insensitive on both sides.

Usage:
    python tools/audit_blender_dead_params.py

Talks to nothing. Addon source only.
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

sys.path.insert(0, HERE)

OP_DEF = re.compile(r"^def op_(\w+)\s*\(", re.M)
# The reject_unknown call itself, so its own literals can be excluded from "appears elsewhere".
REJECT = re.compile(r"reject_unknown\s*\(", re.S)
LITERAL = re.compile(r"""["']([A-Za-z_][A-Za-z0-9_]*)["']""")

# Accepted and ignored ON PURPOSE, with the reason. The UE tool's header makes the same point: a
# deliberately ignored parameter is fine and the next reader has no way to tell it from an oversight
# unless it is written down.
INTENTIONAL = {}


def match_paren(text, open_idx):
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1




def call_graph(tree):
    """name -> set of names it calls. Plain `f()` and `mod.f()` both count, by the trailing name."""
    graph = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        called = set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
        graph[node.name] = called
    return graph


def reachable_from(graph, start):
    """Transitive closure, so a helper that calls a helper still counts."""
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        for nxt in graph.get(cur, ()):
            if nxt in graph and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def out_of_reach_spans(text, keep_op):
    """Char ranges of every module function keep_op cannot reach - including other ops.

    THE LAST SOFTNESS THIS FILE ADMITTED TO, now closed. Scoping to "the module minus every OTHER
    op's body" already stopped one op vouching for another, but a shared HELPER still vouched for
    every op that calls it - and for every op that does NOT. That is exactly the shape that bit:
    _check_format in ops_mesh.py is called by import_mesh and export_mesh, recited the IMPORT
    format list to both, and told an export caller glTF was supported right up to refusing them
    for asking.

    So the scope is now: module-level code, plus this op's body, plus the bodies of the functions it
    can actually REACH. Module-level tables stay in scope - create_primitive still reads fillType
    through EXTRA_KEYS - and an unreachable helper no longer counts.

    CONSERVATIVE WHERE IT CANNOT SEE. The call graph matches on plain and attribute call names, so
    dispatch through a dict or getattr is invisible to it. A function that cannot be parsed, or an
    op that is not in the graph at all, leaves EVERYTHING in scope rather than guessing - because
    under-narrowing costs a missed finding and over-narrowing costs a false one.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    graph = call_graph(tree)
    if keep_op not in graph:
        return []
    keep = reachable_from(graph, keep_op) | {keep_op}
    lines = text.split("\n")
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln) + 1)
    spans = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name in keep:
            continue
        end = getattr(node, "end_lineno", None)
        if not end:
            continue
        spans.append((starts[node.lineno - 1], min(starts[end], len(text))))
    return spans


def other_op_bodies(text, keep_op):
    """Character ranges of every `op_*` function in `text` EXCEPT keep_op's.

    THE MIDDLE TIER BETWEEN THE TWO OBVIOUS SCOPES, and both obvious ones are wrong here. Confining
    the search to the op's own function body calls create_primitive.fillType dead, because it is
    read through a module-level EXTRA_KEYS dict - the docstring above records that as the reason
    this tool went module-wide. But module-wide is what makes the all-clear WEAK by its own
    admission: a key that appears ONLY inside a different op's body counts as read, and that is
    exactly the wrong-op's-helper hole.

    So the scope is: the whole module, minus every OTHER op's function body. Module-level tables,
    shared resolvers and helper functions all stay in scope - fillType still resolves - while
    another op's private body no longer vouches for this one.

    THE COST OF GETTING THIS WRONG IS ALREADY ON RECORD, one file over. _check_format in
    ops_mesh.py is shared by import_mesh and export_mesh and recited the IMPORT format list to
    BOTH, so an export caller was told glTF was supported and then refused when they believed it.
    One helper, two callers, and the answer was right for only one of them. That is this hole with
    a different shape.

    Falls back to the whole module if the file will not parse - a syntax error is the addon's
    problem to report, not a reason for this tool to invent a narrower answer.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.split("\n")
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln) + 1)
    spans = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("op_"):
            continue
        if node.name == "op_%s" % keep_op:
            continue
        end = getattr(node, "end_lineno", None)
        if not end:
            continue
        spans.append((starts[node.lineno - 1], min(starts[end], len(text))))
    return spans


def scan():
    """[(op, key, module)] for every accepted key whose literal appears only in the accept list."""
    try:
        import parity_check as PC
    except Exception as exc:
        print("could not import parity_check (%s) - it owns the accept-list parser." % exc)
        return None
    problems = []
    ops = PC.load_addon_ops(problems) or {}
    if len(ops) < 30:
        print("SELF-CHECK FAILED: parity_check resolved only %d addon ops." % len(ops))
        return None

    dead = []
    for op, entry in sorted(ops.items()):
        accepts = entry.get("accepts")
        if not accepts:
            continue                       # unresolved accept list is parity_check's problem to say
        source = entry.get("source") or ""
        module = source.split(":")[0] if source else ""
        path = os.path.join(ADDON, module)
        if not module or not os.path.isfile(path):
            continue
        text = io.open(path, encoding="utf-8", errors="replace").read()

        # Blank every reject_unknown call so its own literals do not count as a read.
        blanked = text
        for m in REJECT.finditer(text):
            close = match_paren(text, text.index("(", m.start()))
            if close > 0:
                blanked = blanked[:m.start()] + " " * (close - m.start() + 1) + blanked[close + 1:]

        # Blank every OTHER op's body too - see other_op_bodies for why this is the right scope
        # and why neither obvious alternative is.
        for lo, hi in out_of_reach_spans(text, "op_%s" % op):
            blanked = blanked[:lo] + " " * (hi - lo) + blanked[hi:]

        present = {n.lower() for n in LITERAL.findall(blanked)}
        for key in sorted(accepts):
            if key.lower() in present:
                continue
            if INTENTIONAL.get("%s.%s" % (op, key)):
                continue
            dead.append((op, key, module))
    return dead


def main():
    dead = scan()
    if dead is None:
        return 2
    print("addon ops with a resolved accept list: scanned")
    print("parameters accepted and never read elsewhere in their module: %d" % len(dead))
    print("")
    if not dead:
        print("OK  every accepted key appears somewhere other than the accept list.")
        print("")
        print("Scope is now per-op REACHABILITY: module-level code, plus this op's body, plus the")
        print("helpers it can actually reach through the call graph. A shared helper vouches only")
        print("for the ops that call it - which is what _check_format in ops_mesh.py needed, being")
        print("shared by import_mesh and export_mesh and correct for only one of them.")
        print("")
        print("Blind to dispatch it cannot see statically - a dict of handlers, getattr - and in")
        print("that case it leaves EVERYTHING in scope rather than guessing.")
        return 0
    for op, key, module in dead:
        print("  %-28s %-24s %s" % (op, key, module))
    print("")
    print("Each of these is a name a caller can send, that passes the guard, and that nothing")
    print("reads. Wire it, remove it from the accept list, or add it to INTENTIONAL with the")
    print("reason - all three are fine and silence is not.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
