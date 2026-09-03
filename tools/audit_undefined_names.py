"""Names a function loads that are neither local, enclosing, global, nor builtin.

WHY THIS EXISTS. On 2026-08-30 a one-word typo shipped into mifaudit.launch_editor: it printed a
diagnostic using PORT, and the module calls it BRIDGE_PORT. Nothing caught it. py_compile cannot -
a NameError is a runtime failure - and that function only executes when the bridge is ALREADY down,
which is the worst possible moment to discover a typo. It killed a 288-run regression sweep at run
90, and the log ended on

    NameError: name 'PORT' is not defined

with no other context. Recovery paths, error branches and rarely-taken refusals are exactly where
this class of bug hides, because they are the code least exercised by a green test run.

WHAT IT IS NOT. It is not a type checker and not a linter. It answers one question - can this name
possibly resolve? - and it answers it the way Python does at runtime, by scope.

FALSE POSITIVES WERE THE HARD PART, and getting them out matters more than the rule being clever: a
checker that cries wolf is one nobody runs. The first version reported four, and every one was a
scope it did not model:
  - a nested helper's own parameter (`def figures(line)` inside check_badge)
  - a for-loop variable captured by a closure (`for label, exc in ...: def raiser(): raise exc`)
  - two closures over an ENCLOSING function's parameters (`check_once` using `want_state`)
All four are legal Python. The walk below therefore carries a scope STACK rather than checking each
function against module globals alone.

USAGE
    python tools/audit_undefined_names.py                 # every tools/*.py
    python tools/audit_undefined_names.py path/to/file.py

Exit 1 if anything is reported.
"""
import ast
import builtins
import glob
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Module-level dunders Python provides that no assignment creates.
MODULE_DUNDERS = {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__builtins__"}


def _bind_targets(node, into):
    """Add every Name bound by an assignment/for/with/except target."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            into.add(n.id)


def _module_scope(tree):
    names = set(dir(builtins)) | MODULE_DUNDERS
    for node in tree.body:
        _collect_bindings(node, names, top_level=True)
    # Also pick up bindings inside module-level if/try/for blocks, which is where conditional
    # imports and platform branches put things.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _collect_bindings(node, into, top_level=False):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        into.add(node.name)
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            into.add((a.asname or a.name).split(".")[0])
        return
    if isinstance(node, ast.Assign):
        for t in node.targets:
            _bind_targets(t, into)
        return
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        _bind_targets(node.target, into)
        return
    for child in ast.iter_child_nodes(node):
        _collect_bindings(child, into)


def _own_bindings(fn):
    """Everything bound inside fn's OWN scope - args, assignments, imports, comprehension targets,
    nested def/class names - but NOT the interiors of nested functions, which are their own scope."""
    bound = set()
    args = fn.args
    for a in args.args + args.kwonlyargs + args.posonlyargs:
        bound.add(a.arg)
    for extra in (args.vararg, args.kwarg):
        if extra:
            bound.add(extra.arg)

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(child.name)          # the name is bound here; its body is not our scope
                continue
            if isinstance(child, ast.Lambda):
                continue                       # a lambda body is its own scope too
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                bound.add(child.id)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for a in child.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(child, ast.ExceptHandler) and child.name:
                bound.add(child.name)
            elif isinstance(child, ast.comprehension):
                _bind_targets(child.target, bound)
            walk(child)

    walk(fn)
    return bound


def _direct_children(fn):
    """Functions and classes lexically inside fn but not inside a deeper function of fn."""
    out = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                  ast.ClassDef)):
                out.append(child)
                continue          # its interior belongs to it, not to fn
            walk(child)

    walk(fn)
    return out


def _loads(fn):
    """Names LOADED directly in fn's own scope, excluding nested function bodies."""
    out = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                  ast.Lambda)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                out.append((child.id, child.lineno))
            walk(child)

    walk(fn)
    return out


def undefined_in(path):
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    except SyntaxError as exc:
        return [("<parse>", getattr(exc, "lineno", 0), str(exc))]

    problems = []

    def visit(fn, enclosing):
        # A lambda has args and a single expression; treat it like a tiny function.
        bound = _own_bindings(fn)   # handles Lambda too: args plus any comprehension targets
        scope = enclosing | bound
        for name, line in _loads(fn):
            if name not in scope:
                problems.append((getattr(fn, "name", "<lambda>"), line, name))
        # DIRECT children only. ast.walk reaches every descendant, so a doubly-nested function was
        # visited twice - once correctly from its real parent, and once from an outer function whose
        # scope lacks the intermediate parameters. That reported `self` undefined inside a decorator
        # nested in a method, and `resp` undefined inside a lambda nested in a function. The comment
        # below always said "directly inside fn"; the code did not do it.
        for child in _direct_children(fn):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                # Only descend into functions DIRECTLY inside fn; deeper ones are reached
                # through their own parent.
                visit(child, scope)
            elif isinstance(child, ast.ClassDef):
                # A class nested inside a function: its methods are functions in their own right,
                # and their bodies were previously scanned as part of the enclosing function.
                for sub in child.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        visit(sub, scope | {n.name for n in child.body
                                            if isinstance(n, (ast.FunctionDef,
                                                              ast.AsyncFunctionDef))})

    module = _module_scope(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit(node, module)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(sub, module | {n.name for n in node.body
                                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))})
    return problems


def main():
    # THE BLENDER ADDON WAS OUTSIDE THIS SCAN UNTIL 2026-09-03, and it is 9,251 lines of Python -
    # a third of what this tool exists to protect. The default was tools/*.py, which is where the
    # UE-side helpers live, so the entire other backend went unchecked by the one detector written
    # for exactly its failure mode.
    #
    # Found by walking into the bug: ops_rig.py gained three take_float() calls with no import for
    # it. That compiles, passes every other check here, and raises NameError on the first real
    # call - which is the shape of GitHub issue #1, the move_tree_widget wrapper this file's own
    # docstring is written around. Reported from OUTSIDE by a user, because nothing here looked.
    #
    # The MCP server directory is included for the same reason: it is a third tree of Python that
    # nothing else name-checks (mcp_static_check asks a narrower question about wrappers only).
    paths = sys.argv[1:] or sorted(
        glob.glob(os.path.join(HERE, "*.py"))
        + glob.glob(os.path.join(HERE, "blender-addon", "MifBlender", "*.py"))
        + glob.glob(os.path.join(HERE, "mcp-server", "*.py")))
    seen = set()
    bad = 0
    for path in paths:
        for fn, line, name in undefined_in(path):
            key = (path, line, name)
            if key in seen:
                continue
            seen.add(key)
            print("%s:%d  %s() loads undefined name %r" % (path.replace("\\", "/"), line, fn, name))
            bad += 1
    if bad:
        print("\n%d name(s) cannot resolve. A NameError here is a RUNTIME failure, so nothing else "
              "in this repo would catch it - and these live disproportionately in error and "
              "recovery paths, which green test runs never reach." % bad)
        return 1
    print("OK - %d file(s) checked, every loaded name resolves" % len(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
