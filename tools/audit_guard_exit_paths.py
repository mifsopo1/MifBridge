"""Does a cooked guard swallow the function's exit path? Run with --check to gate it.

THE BUG THIS EXISTS FOR, which I wrote and which rc=0 hid. See PM-015.

The cooked guards wrap a section in `if COOKED is False: <banner> else: <the section>`, so something
has to decide where the section ends. audit_cooked_section_safety asks whether the section leaks a
name forward, which is the right question for setup - and completely blind to the opposite hazard.
A section that is LAST inside main() runs, on a naive boundary, all the way to `if __name__`, so the
wrap also takes main()'s epilogue:

    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL: ...
    return 1 if FAIL else 0

Inside the `else:`, none of that runs on an uncooked project. main() falls off the end, returns None,
and sys.exit(None) exits ZERO - a suite reporting success having printed no summary and discarded
every failure it found. Three suites shipped that way for about ten minutes.

The question is mechanical: inside each `if COOKED is False:` statement, does the else-branch hold a
`return` that no path after the statement can reach? If the function has no return below the whole
if/else, every uncooked run falls off the end.

DELIBERATELY NOT the same check as the section-safety audit. That one decides what may be wrapped
BEFORE the edit; this one inspects what is actually in the tree AFTER it, so it also catches a guard
written by hand, and it keeps working if the applier is ever rewritten. Two questions, two tools.

A false pass is worse than a crash: a crash gets looked at.
"""
import ast
import glob
import io
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))


def guard_nodes(tree):
    """Every `if COOKED is False:` statement that has an else, with its enclosing function."""
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.If) or not node.orelse:
                continue
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "COOKED" and t.comparators
                    and isinstance(t.comparators[0], ast.Constant)
                    and t.comparators[0].value is False):
                out.append((fn, node))
    return out


def analyse(path):
    """-> [(line, n_returns_in_else, n_returns_after)] for every guard in the file."""
    src = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    return analyse_src(src)


def analyse_src(src):
    if "COOKED = M.project_is_cooked()" not in src:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    rows = []
    for fn, node in guard_nodes(tree):
        in_else = [n for b in node.orelse for n in ast.walk(b) if isinstance(n, ast.Return)]
        # The span of the whole if/else, so "after" means genuinely below it rather than in a
        # branch of it. max() over the subtree because an else-branch is not contiguous by lineno
        # in every ast version.
        hi = max(getattr(n, "lineno", 0) for n in ast.walk(node))
        after = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Return) and n.lineno > hi]
        rows.append((node.lineno, len(in_else), len(after)))
    return rows


SWALLOWED = '''
def main():
    check("T1 something", True, "")
    COOKED = M.project_is_cooked()
    if COOKED is False:
        print("skipped")
    else:
        check("T2 cooked thing is refused", True, "")
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1 if FAIL else 0
'''

FINE = '''
def main():
    check("T1 something", True, "")
    COOKED = M.project_is_cooked()
    if COOKED is False:
        print("skipped")
    else:
        check("T2 cooked thing is refused", True, "")
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0
'''

# A return in the else that is NOT the only one - an early-out inside the cooked branch, with the
# real exit still below the if/else. This must NOT be flagged, or the check is unusable.
EARLY_OUT = '''
def main():
    COOKED = M.project_is_cooked()
    if COOKED is False:
        print("skipped")
    else:
        if not M.call("x", {}).get("ok"):
            return 2
        check("T2 cooked thing is refused", True, "")
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0
'''


def selftest():
    """The check must be shown FAILING before it is believed, and shown NOT firing on the good
    shapes - a false failure teaches people to skip the gate, which is worse than no gate."""
    cases = [("a guard that swallows main()'s epilogue", SWALLOWED, True),
             ("the same guard ending at its last assertion", FINE, False),
             ("an early-out inside the cooked branch, real exit below", EARLY_OUT, False)]
    bad = 0
    for label, src, want_flag in cases:
        rows = analyse_src(src)
        got = any(in_else and not after for _, in_else, after in rows)
        mark = "ok  " if got == want_flag else "MISS"
        if got != want_flag:
            bad += 1
        print("  %s  %-52s flagged=%-5s want=%s" % (mark, label, got, want_flag))
    print("")
    print("selftest: %d case(s), %d wrong" % (len(cases), bad))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    check = "--check" in sys.argv
    print("%-42s %-6s %s" % ("suite", "line", "verdict"))
    bad = ok = 0
    for path in sorted(glob.glob(os.path.join(TOOLS, "test_*.py"))):
        name = os.path.basename(path)
        for line, in_else, after in analyse(path):
            if in_else and not after:
                print("%-42s %-6d SWALLOWED THE EXIT PATH - %d return(s) inside the else:, none "
                      "after it. The uncooked run falls off the end and exits 0." % (name, line,
                                                                                     in_else))
                bad += 1
            else:
                print("%-42s %-6d fine (%d return after the guard)" % (name, line, after))
                ok += 1
    print("")
    print("guards inspected: %d    swallowed exit path: %d" % (bad + ok, bad))
    if not bad:
        print("Every cooked guard leaves its function's return reachable on the uncooked branch.")
    if check and bad:
        print("")
        print("FAIL: a guarded suite would exit 0 on an uncooked project without asserting or")
        print("      reporting anything. End the wrapped section at its last assertion - see")
        print("      audit_cooked_section_safety and PM-015.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
