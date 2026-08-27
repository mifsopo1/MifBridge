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
    if not findings:
        print("OK  every one can be called - no unbound names")
        return 0

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
