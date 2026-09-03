"""Catch MCP wrappers that cannot run at all, without needing an editor.

WHY THIS EXISTS
---------------
`move_tree_widget` shipped in a state where EVERY call raised NameError:

    def move_tree_widget(blueprint_id, widget_name, parent_name=None, as_root=False, index=None):
        return _post("move_tree_widget", ..., replaceRoot=replace_root)
                                                          ^^^^^^^^^^^^ never a parameter

It was reported from outside, by a user, as GitHub issue #1. Nothing in this repo would have found
it, and that is the gap worth closing rather than the one line.

The existing checks all pass a wrapper like this:

  * parity_check   - the name exists in all three registries. It does.
  * param_reach    - the endpoint's parameters are reachable from the tool. They are; the tool
                     even names replaceRoot, which is precisely why it looked correct.
  * the test suites - they drive ENDPOINTS over HTTP, not the Python wrappers, so a broken wrapper
                     is invisible to every one of them.

Nothing asked the one question a reader would ask first: *can this function be called?*

WHAT IT CHECKS
--------------
Every name a function reads that is not bound anywhere it could be bound - parameters, locals,
enclosing scopes, module globals, builtins. That is the exact shape of the bug and it needs no
editor, no bridge, and no network.

WHAT IT DOES NOT CHECK
----------------------
That the wrapper sends the RIGHT things. `param_reach.py` covers reachability and the suites cover
behaviour. This answers only "does it run", which turned out to be a question nobody was asking.

SCOPING IS THE WHOLE DIFFICULTY, and a naive version is useless. The first pass at this reported 35
findings of which 34 were false: module-level constants, `except ... as exc` bindings, comprehension
targets, and - the one that matters most here - parameters of NESTED functions, which a flat
ast.walk() attributes to the outer function. A checker that cries wolf 34 times out of 35 gets
ignored, so the scope handling below is deliberate rather than incidental.
"""
import argparse
import ast
import builtins
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp-server", "server.py")
BUILTINS = set(dir(builtins))


def _target_names(node):
    """Every name bound by an assignment target, including tuple and starred forms."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
    return out


def _bound_in_scope(fn):
    """Names bound anywhere in THIS function's own scope.

    Deliberately does NOT descend into nested function or class bodies - their bindings belong to
    their own scope, and treating them as this one's is how a checker starts lying in both
    directions at once."""
    bound = set()
    a = fn.args
    for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
        bound.add(arg.arg)
    if a.vararg:
        bound.add(a.vararg.arg)
    if a.kwarg:
        bound.add(a.kwarg.arg)

    def walk(node, top=False):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # The nested thing's NAME is bound here; its innards are not.
                bound.add(child.name)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    bound.update(_target_names(t))
            elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                bound.update(_target_names(child.target))
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                bound.update(_target_names(child.target))
            elif isinstance(child, ast.ExceptHandler):
                if child.name:
                    bound.add(child.name)          # `except X as exc` - bound, and scoped to the handler
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for al in child.names:
                    bound.add((al.asname or al.name).split(".")[0])
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    if item.optional_vars is not None:
                        bound.update(_target_names(item.optional_vars))
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                bound.update(child.names)
            elif isinstance(child, ast.NamedExpr):
                bound.update(_target_names(child.target))
            walk(child)

    walk(fn, top=True)
    return bound


def _comprehension_targets(fn):
    """Comprehension variables. Their own scope in py3, but reading them inside the comprehension is
    legal, and the flat read-scan below cannot tell where it is - so collect and allow them."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in n.generators:
                out.update(_target_names(gen.target))
        elif isinstance(n, ast.Lambda):
            a = n.args
            for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                out.add(arg.arg)
            if a.vararg:
                out.add(a.vararg.arg)
            if a.kwarg:
                out.add(a.kwarg.arg)
    return out


def _reads(fn):
    """(name, lineno) for every Load of a bare name in this function's own body, nested scopes
    excluded - they are checked in their own right."""
    out = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                out.append((child.id, child.lineno))
            walk(child)

    walk(fn)
    return out


def collect(tree):
    """Every function in the file, paired with the scopes enclosing it."""
    found = []

    def descend(node, enclosing):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                own = _bound_in_scope(child) | _comprehension_targets(child)
                found.append((child, enclosing | own))
                descend(child, enclosing | own)
            elif isinstance(child, ast.ClassDef):
                descend(child, enclosing)
            else:
                descend(child, enclosing)

    module_names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            module_names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                module_names.add((al.asname or al.name).split(".")[0])
    descend(tree, module_names | BUILTINS)
    return found


def is_mcp_tool(fn):
    for d in fn.decorator_list:
        src = ast.dump(d)
        if "mcp" in src and "tool" in src:
            return True
    return False


# --------------------------------------------------------------------------- lossy bool forwarding
#
# `someFlag=some_flag or None` turns an explicit False into ABSENT, because _post and _blender drop
# only None. That is harmless exactly while the endpoint's own default is also false - and there are
# 30 of these, every one currently safe by that coincidence. The day an endpoint default becomes
# true, a caller who passed False gets true behaviour and a success response, which is the silent
# class this repo calls its most damaging.
#
# The safe shape, from override_inherited_component, needs BOTH halves:
#     if (JHasAny(In, { TEXT("confirm") }) && !JBool(In, TEXT("confirm"), true))
# the JHasAny makes the `true` unreachable unless the key is PRESENT, AND its wrapper forwards
# `confirm=confirm` rather than `confirm or None` so the False survives the trip. Either half alone
# is not enough, which is why this check reads both files.

PRIV = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")

LOSSY = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([a-z_][a-z0-9_]*)\s+or\s+None")


def _endpoint_bool_defaults():
    """key -> set of defaults seen in JBool*/JHasAny-guarded reads across the C++."""
    out = {}
    guarded = set()
    try:
        names = sorted(os.listdir(PRIV))
    except OSError:
        return out, guarded
    for fn in names:
        if not fn.endswith(".cpp"):
            continue
        src = io.open(os.path.join(PRIV, fn), encoding="utf-8", errors="replace").read()
        for m in re.finditer(
                r'JBool\w*\s*\((?:[^;]{0,300}?)TEXT\(\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\)'
                r'(?:[^;]{0,200}?),\s*(true|false)\s*\)', src, re.S):
            out.setdefault(m.group(1), set()).add(m.group(2))
        for m in re.finditer(r'JHasAny\s*\([^;]{0,200}?TEXT\(\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\)',
                             src, re.S):
            guarded.add(m.group(1))
    return out, guarded


def lossy_bool_forwards():
    """(wrapper, key, param) where an explicit False is dropped AND the endpoint defaults true."""
    server = io.open(SERVER, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    defaults, guarded = _endpoint_bool_defaults()
    fn = re.compile(r"@mcp\.tool\(\)\s*\n(?:\s*#[^\n]*\n|\s*\n)*(?:async\s+)?def\s+"
                    r"([a-z0-9_]+)\s*\(([^)]*)\)", re.S)
    rows = []
    for m in fn.finditer(server):
        name, sig = m.group(1), m.group(2)
        bools = set(re.findall(r"([a-z_][a-z0-9_]*)\s*:\s*bool", sig))
        if not bools:
            continue
        nxt = server.find("@mcp.tool()", m.end())
        body = server[m.end(): nxt if nxt > 0 else len(server)]
        for key, param in LOSSY.findall(body):
            if param not in bools:
                continue
            d = defaults.get(key, set())
            if "true" in d and key not in guarded:
                rows.append((name, key, param))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=SERVER)
    ap.add_argument("--all", action="store_true",
                    help="report every function, not only @mcp.tool ones")
    a = ap.parse_args()

    src = io.open(a.file, encoding="utf-8").read()
    tree = ast.parse(src, filename=a.file)

    findings = []
    checked = 0
    for fn, visible in collect(tree):
        if not a.all and not is_mcp_tool(fn):
            continue
        checked += 1
        seen = set()
        for name, line in _reads(fn):
            if name in visible or name in seen:
                continue
            seen.add(name)
            findings.append((fn.name, name, line))

    label = "function" if a.all else "@mcp.tool wrapper"
    print("checked %d %s(s) in %s" % (checked, label, os.path.basename(a.file)))
    # This has to run BEFORE the no-unbound-names early return, not after it. Placed after, it was
    # unreachable on every clean run - which is every run - so the checker printed OK while never
    # having looked. Found by planting `deep=hide_knots or None` into list_nodes and watching it
    # still say OK; the finder function was right the whole time, the wiring was dead code.
    # A TOOL DEFINED AFTER THE __main__ GUARD IS NEVER REGISTERED, and nothing else would say so.
    # main() ends in mcp.run(), which BLOCKS serving, so when server.py runs as a script - which is
    # the only way it runs - execution never reaches a decorator below the guard. Found 2026-09-03:
    # mif_layout_graph, mif_create_curve and mif_help sat after it, so 535 of 538 tools registered
    # and those three were invisible to every MCP client. mif_help is the documented way to read
    # tool prose and is backed by all 406 tool_help.json entries, none of which were reachable.
    #
    # Checked here rather than in a suite because it is a property of the FILE, not of a running
    # server: importing the module registers everything, so the bug is invisible to any test that
    # imports it. That is exactly why it survived - the module looks correct from inside Python.
    unreachable = []
    try:
        tree = ast.parse(io.open(a.file, encoding="utf-8", errors="replace").read())
        guards = [n.lineno for n in tree.body
                  if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)]
        if guards:
            first = min(guards)
            for n in tree.body:
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if n.lineno <= first:
                    continue
                for d in n.decorator_list:
                    f = d.func if isinstance(d, ast.Call) else d
                    if getattr(f, "attr", None) == "tool":
                        unreachable.append((n.name, n.lineno))
    except (OSError, SyntaxError) as exc:               # noqa: BLE001
        print("could not parse %s for the __main__ ordering check: %s" % (a.file, exc))
        return 2
    if unreachable:
        print("")
        print("NEVER REGISTERED - %d @mcp.tool definition(s) sit BELOW `if __name__ == \"__main__\"`:"
              % len(unreachable))
        for name, ln in unreachable:
            print("  %s (line %d)" % (name, ln))
        print("  main() ends in mcp.run(), which blocks, so these decorators never execute when the")
        print("  server runs as a script. Move the __main__ block to the END of the file.")

    lossy = lossy_bool_forwards()
    if lossy:
        print("")
        print("LOSSY BOOL FORWARD - an explicit False is DROPPED and the endpoint defaults TRUE:")
        for fnname, key, param in lossy:
            print("  %s() sends %s=%s or None; the endpoint reads %s with a true default and does "
                  "not guard it with JHasAny, so False silently becomes true."
                  % (fnname, key, param, key))
        print("  Forward it directly - `key=param` - or guard the C++ read with JHasAny.")

    if not findings:
        if not lossy and not unreachable:
            print("OK  every one can be called - no unbound names")
        return 1 if (lossy or unreachable) else 0

    print("")
    print("UNBOUND NAMES - these raise NameError on EVERY call:")
    for fnname, name, line in findings:
        print("  %s:%d  %s() reads '%s', which is never bound" % (
            os.path.basename(a.file), line, fnname, name))
    print("")
    print("This is the move_tree_widget shape: the wrapper names a parameter its signature does not")
    print("declare. parity_check and param_reach both pass it, and the suites never call the wrapper.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
