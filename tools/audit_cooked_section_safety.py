"""Which cooked sections can be WRAPPED without breaking the suite, and which carry setup.

BUILT BECAUSE I BROKE ONE. test_virtual_bone_authoring's T3300 is banner-titled "cooked skeletons
are refused, and named" and looks exactly like the four sections that wrapped cleanly. It also
duplicates a skeleton into scratch and picks B1/B2/B3, which every later section uses - so wrapping
it made T3301 die on UnboundLocalError. py_compile was happy; only running it found out.

That question is mechanical, so it should not be answered by eye. It took three tries to state, and
each miss is a hazard the previous rule was blind to:

  a section is SAFE TO WRAP if no name it ASSIGNS is READ after it ends,
  it does not span a DEDENT,
  and it ends at its LAST ASSERTION rather than at the end of the enclosing function.

The first clause is about setup leaking forward. The second is about re-indenting code out of its
own scope. The third is about the epilogue: for the LAST section in main(), everything up to the
dedent includes the PASS/FAIL summary and `return 1 if FAIL else 0`, and wrapping those in an else:
makes the uncooked run fall off the end of main() - sys.exit(None) reports SUCCESS having printed
no summary and DISCARDED every failure. See PM-015. A false pass is worse than a crash, and rc=0
is exactly what hid it.

Anything else carries setup, and its cooked ASSERTIONS have to be guarded individually instead.
This reports the split and names the leaked variables, so the next pass starts from a list rather
than from a suite that dies at runtime.

DELIBERATELY CONSERVATIVE, and the direction matters. A name that is assigned in the section and
merely REASSIGNED later - the throwaway `r` that half these suites reuse for every response - counts
as leaked here even though wrapping would not break it. So "carries setup" is a superset: it will
send a few safe sections to the slower per-assertion treatment, and it will not send an unsafe one
to the fast path. A false positive costs a careful edit; a false negative costs a suite that dies at
runtime, which is what this exists to prevent.

The "already guarded" verdict is per FILE, not per section - a suite with one guarded section shows
it against all of them. Informational only; the wrapping is decided by the leak column.
"""
import ast
import glob
import io
import os
import re

TOOLS = r"D:\DDS2SDK\Game\Plugins\MifBridge\tools"
SECTION = re.compile(r'print\(\s*["\'](?:\\n)?=== (T\w+)')


def analyse(path):
    src = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    lines = src.split("\n")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    # section start lines, in order
    starts = [(i, SECTION.search(l).group(1)) for i, l in enumerate(lines) if SECTION.search(l)]
    if not starts:
        return None
    out = []
    for n, (i, tag) in enumerate(starts):
        j = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        # THE SECTION ENDS AT THE FIRST SHALLOWER LINE, not necessarily at the next banner. A
        # banner can sit inside a nested block while the section that opened it closes earlier, and
        # taking banner-to-banner then spans a dedent - so a wrap would re-indent code out of its
        # own scope. The applier refused three sections this audit had called safe for exactly that
        # reason, which means "safe to wrap" was answering only half the question: self-contained,
        # yes, but not actually wrappable. Both have to hold.
        ind = len(lines[i]) - len(lines[i].lstrip())
        for k in range(i + 1, j):
            l = lines[k]
            if l.strip() and (len(l) - len(l.lstrip())) < ind:
                j = k
                break
        # AND IT ENDS AT ITS LAST ASSERTION, not at the last line before that dedent. The rule
        # above alone is wrong for the LAST section in a function: the first shallower line is
        # `if __name__`, so the section swallows main()'s epilogue - the PASS/FAIL summary and the
        # `return 1 if FAIL else 0`. Wrapped in an else:, the uncooked run then falls off the end
        # of main() and sys.exit(None) reports SUCCESS having printed no summary and DISCARDED
        # every failure. I shipped that into three suites and rc=0 is what hid it; a false pass is
        # the one outcome this repo treats as worse than a crash.
        #
        # Whole statements at the section's own indent, so a trailing `else: print(NOTE)` on the
        # last check is kept rather than orphaned. Only assertions need the guard; a summary, a
        # teardown or a return after them never did.
        last = None
        for st in ast.walk(tree):
            if not isinstance(st, ast.stmt) or getattr(st, "col_offset", -1) != ind:
                continue
            if not (i < st.lineno <= j):
                continue
            if any(isinstance(c, ast.Name) and c.id == "check" for c in ast.walk(st)):
                last = max(last or 0, st.end_lineno)
        if last is not None:
            j = min(j, last)
        # does this section contain a cooked ASSERTION?
        body = "\n".join(lines[i:j])
        if not re.search(r"check\([^)]*cooked", body, re.I | re.S):
            continue
        assigned, used_after = set(), set()
        for node in ast.walk(tree):
            ln = getattr(node, "lineno", None)
            if ln is None:
                continue
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store) and i < ln <= j:
                    assigned.add(node.id)
                elif isinstance(node.ctx, ast.Load) and ln > j:
                    used_after.add(node.id)
        leaked = sorted(assigned & used_after)
        # Belt and braces on the trim above: if a `return` still falls inside the block, wrapping
        # it hides an exit path, so the section is not wrappable whatever the leak column says.
        holds_return = any(isinstance(n, ast.Return) and i < n.lineno <= j for n in ast.walk(tree))
        out.append((tag, i + 1, j, leaked, holds_return))
    return out


print("%-38s %-8s %-7s %s" % ("suite", "section", "lines", "verdict"))
safe = carry = 0
for path in sorted(glob.glob(os.path.join(TOOLS, "test_*.py"))):
    rows = analyse(path)
    if not rows:
        continue
    for tag, a, b, leaked, holds_return in rows:
        name = os.path.basename(path)
        if "COOKED = M.project_is_cooked()" in io.open(path, encoding="utf-8",
                                                       errors="replace").read():
            verdict = "already guarded"
        elif holds_return:
            verdict = "HOLDS A RETURN - wrapping would hide an exit path"
            carry += 1
        elif leaked:
            verdict = "CARRIES SETUP - leaks %s" % ", ".join(leaked[:4])
            carry += 1
        else:
            verdict = "safe to wrap"
            safe += 1
        print("%-38s %-8s %-7s %s" % (name, tag, "%d-%d" % (a, b), verdict))
print("")
print("safe to wrap: %d    carries setup (needs per-assertion guards): %d" % (safe, carry))
