"""Which cooked sections CAN be wrapped without breaking the suite. Not which SHOULD be.

READ THIS BEFORE ACTING ON THE OUTPUT. This tool answers a mechanical question about scope and
control flow, and it was mistaken for an answer to a different one - "does this section need a
cooked-project guard". It cannot answer that, and 9 of the 17 guards placed as if it could were
measured to skip 48 working checks while preventing nothing. audit_cooked_guard_value answers that
one, by removing each guard in turn against a live editor and reporting whether a real failure
appears without it. Use this column as a list of candidates and that tool as the decision.

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

CONSERVATIVE, BUT NOT BLINDLY. A name assigned in the section and merely REASSIGNED afterwards -
the throwaway `r` that half these suites reuse for every response - used to count as leaked, which
sent 17 sections to the slow path that a wrap would not have broken. reassigned_first now clears
those, under three conditions that all have to hold; anything it cannot prove keeps the old verdict.
The asymmetry is deliberate: a false positive costs a careful edit, a false negative costs a suite
that dies at runtime.

The "already guarded" verdict is PER SECTION, read off the else-branch spans of the actual guards in
the tree. It used to be per file, which made one guarded section report seven of its neighbours as
guarded when they were not - and the applier skips what this calls guarded, so those were
unreachable by the fast path for no reason.
"""
import ast
import glob
import io
import os
import re

TOOLS = r"D:\DDS2SDK\Game\Plugins\MifBridge\tools"
SECTION = re.compile(r'print\(\s*["\'](?:\\n)?=== (T\w+)')


def guarded_spans(tree):
    """Line spans of the else-branches of every `if COOKED is False:` in the tree.

    PER SECTION, not per file, and the difference was hiding work. The verdict used to be "does
    this FILE contain the guard marker anywhere", so one guarded section made every other section
    in the same suite report "already guarded" - test_cooked_class_trap showed eight of them when
    exactly one was true. The applier skips guarded files, so those seven were unreachable by the
    fast path and were being counted towards the hand-editing backlog for no reason.
    """
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        t = node.test
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "COOKED"
                and t.comparators and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value is False):
            lo = min(b.lineno for b in node.orelse)
            hi = max(getattr(n, "lineno", 0) for b in node.orelse for n in ast.walk(b))
            spans.append((lo, hi))
    return spans


def reassigned_first(tree, name, after_line, ind):
    """Is `name` OVERWRITTEN before it is next read, below line `after_line`?

    Half these suites reuse one throwaway - `r`, `b`, `q` - for every response, so almost every
    section "leaks" a name that the next section immediately assigns again. Counting those as
    leaks sent 17 sections to hand-editing that a wrap would not have broken. That was the right
    default while nothing checked it; it is not the right answer.

    CONSERVATIVE ON PURPOSE, in three ways, because the cost of being wrong here is a suite that
    dies at runtime:

      - the overwrite must be a plain assignment whose target IS the bare name (not a subscript,
        not an attribute, not an augmented assign, which reads before it writes),
      - it must sit at the SECTION'S OWN indent, so a store inside an `if:` or a `for:` that may
        never execute does not count,
      - and the name must not be read anywhere in that same statement, so `r = f(r)` is a read.

    Anything else keeps the old verdict and goes to the slow path.
    """
    first_use = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and node.lineno > after_line:
            if first_use is None or node.lineno < first_use:
                first_use = node.lineno
    if first_use is None:
        return True                      # never mentioned again: not leaked at all
    for st in ast.walk(tree):
        if not isinstance(st, ast.Assign) or getattr(st, "col_offset", -1) != ind:
            continue
        if not (st.lineno <= first_use <= st.end_lineno):
            continue
        targets = [t.id for t in st.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        reads = [n for n in ast.walk(st.value)
                 if isinstance(n, ast.Name) and n.id == name]
        if not reads:
            return True
    return False


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
        # DOES THIS SECTION MENTION COOKED CONTENT? Note what this is NOT: evidence that the
        # section DEPENDS on a cooked project. The regex matches check() labels, and an AST
        # version that ignored labels did worse - it fired on the guard's own COOKED variable
        # and on every `"cooked" in (r.get("error") or "")` substring assertion, 22 candidates
        # against a measured ground truth of 5. Neither answers the real question. Candidates
        # only; audit_cooked_guard_value decides, by running the thing.
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
        leaked = sorted(n for n in (assigned & used_after) if not reassigned_first(tree, n, j, ind))
        # Belt and braces on the trim above: if a `return` still falls inside the block, wrapping
        # it hides an exit path, so the section is not wrappable whatever the leak column says.
        holds_return = any(isinstance(n, ast.Return) and i < n.lineno <= j for n in ast.walk(tree))
        guarded = any(lo <= i + 1 <= hi for lo, hi in guarded_spans(tree))
        out.append((tag, i + 1, j, leaked, holds_return, guarded))
    return out


print("%-38s %-8s %-7s %s" % ("suite", "section", "lines", "verdict"))
safe = carry = 0
for path in sorted(glob.glob(os.path.join(TOOLS, "test_*.py"))):
    rows = analyse(path)
    if not rows:
        continue
    for tag, a, b, leaked, holds_return, guarded in rows:
        name = os.path.basename(path)
        if guarded:
            verdict = "already guarded"
        elif holds_return:
            verdict = "HOLDS A RETURN - wrapping would hide an exit path"
            carry += 1
        elif leaked:
            verdict = "CARRIES SETUP - leaks %s" % ", ".join(leaked[:4])
            carry += 1
        else:
            verdict = "wrappable - NOT a recommendation, measure it"
            safe += 1
        print("%-38s %-8s %-7s %s" % (name, tag, "%d-%d" % (a, b), verdict))
print("")
print("wrappable: %d    carries setup (would need per-assertion guards): %d" % (safe, carry))
print("")
print("WRAPPABLE IS NOT A TO-DO LIST. It says a wrap would not break the suite, not that the")
print("section needs one. 9 of 17 guards placed on that reading were measured to skip 48 working")
print("checks and prevent nothing. Run audit_cooked_guard_value against a live editor before")
print("adding any guard from this column - it removes each one in turn and reports whether a real")
print("failure appears without it.")
