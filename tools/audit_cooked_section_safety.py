"""Which cooked sections can be WRAPPED without breaking the suite, and which carry setup.

BUILT BECAUSE I BROKE ONE. test_virtual_bone_authoring's T3300 is banner-titled "cooked skeletons
are refused, and named" and looks exactly like the four sections that wrapped cleanly. It also
duplicates a skeleton into scratch and picks B1/B2/B3, which every later section uses - so wrapping
it made T3301 die on UnboundLocalError. py_compile was happy; only running it found out.

That question is mechanical, so it should not be answered by eye:

  a section is SAFE TO WRAP if no name it ASSIGNS is READ after it ends.

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
        out.append((tag, i + 1, j, leaked))
    return out


print("%-38s %-8s %-7s %s" % ("suite", "section", "lines", "verdict"))
safe = carry = 0
for path in sorted(glob.glob(os.path.join(TOOLS, "test_*.py"))):
    rows = analyse(path)
    if not rows:
        continue
    for tag, a, b, leaked in rows:
        name = os.path.basename(path)
        if "COOKED = M.project_is_cooked()" in io.open(path, encoding="utf-8",
                                                       errors="replace").read():
            verdict = "already guarded"
        elif leaked:
            verdict = "CARRIES SETUP - leaks %s" % ", ".join(leaked[:4])
            carry += 1
        else:
            verdict = "safe to wrap"
            safe += 1
        print("%-38s %-8s %-7s %s" % (name, tag, "%d-%d" % (a, b), verdict))
print("")
print("safe to wrap: %d    carries setup (needs per-assertion guards): %d" % (safe, carry))
