"""PER GUARD, not per suite: does THIS guard prevent a false failure, or skip working checks?

The suite-level pass showed 9 of 17 suites losing coverage, but its verdict order hid the mixed
case: test_anim_curve prevents 9 failures AND skips 5 passing checks, and reported only the first.
A suite can hold two guards where one earns its place and the other is a hole, so the unit of
judgement has to be the guard.

METHOD. Take the file as it is, remove exactly ONE guard - restoring that section to running
unconditionally - run it against the disposable probe, and compare with the fully guarded baseline:

  new FAILURES appear  -> the guard prevents them. Keep it.
  only new PASSES      -> it was skipping checks that worked. Remove it.
  nothing changes      -> inert. It costs a round trip and some noise; say so.

No git archaeology, so it also judges guards added by hand, and it compares like with like: same
file, same day, one construct removed.
"""
import ast
import io
import os
import re
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
MARK = "COOKED = M.project_is_cooked()"
SUMMARY = re.compile(r"^PASS (\d+)   FAIL (\d+)", re.M)
# The AMBIENT environment, not a hardcoded port. This was written against a disposable probe on
# 8801 and hardcoding that would have made it silently measure the wrong editor - or nothing - for
# anyone else. Set MIF_BRIDGE_PORT / MIF_PROJECT_MARKER the way every other tool here expects.
ENV = dict(os.environ)


def guards(tree):
    """Every `if COOKED is False:` If-node that has an else, in source order."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        t = node.test
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "COOKED"
                and t.comparators and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value is False):
            out.append(node)
    out.sort(key=lambda n: n.lineno)
    return out


def unwrap(src, k):
    """Remove the k-th guard, de-indenting its else-body back to the guard's own level."""
    lines = src.split("\n")
    node = guards(ast.parse(src))[k]
    if_line = node.lineno - 1                                  # 0-based
    body_start = min(b.lineno for b in node.orelse) - 1
    # ast.walk yields expression CONTEXTS (Load/Store) too, and those carry no lineno at all - the
    # first version of this called n.lineno on them and every single unwrap died. getattr with a
    # floor of 0, then max, so a context contributes nothing instead of raising.
    body_end = max(getattr(n, "end_lineno", None) or getattr(n, "lineno", 0)
                   for b in node.orelse for n in ast.walk(b))  # 1-based, inclusive
    ind = len(lines[if_line]) - len(lines[if_line].lstrip())
    body = [(l[4:] if l.startswith(" " * (ind + 4)) else l) for l in lines[body_start:body_end]]
    # Everything above the `if`: the COOKED assignment and the comment block explaining it.
    top = if_line
    while top > 0 and (lines[top - 1].strip().startswith("#") or MARK in lines[top - 1]):
        top -= 1
    return "\n".join(lines[:top] + body + lines[body_end:])


def run(path):
    p = subprocess.run([sys.executable, "-u", path], cwd=REPO, env=ENV,
                       capture_output=True, text=True, timeout=900)
    m = SUMMARY.search(p.stdout)
    return (p.returncode, int(m.group(1)), int(m.group(2))) if m else (p.returncode, None, None)


targets = [f for f in sorted(os.listdir(TOOLS))
           if f.startswith("test_") and f.endswith(".py")
           and MARK in io.open(os.path.join(TOOLS, f), encoding="utf-8", errors="replace").read()]

# IT MUST BE TOLD WHICH EDITOR. This runs suites that perform writes - that is the hazard it
# measures - so it will not fall back to the default port and find out afterwards whose project it
# just wrote into. The first version hardcoded a probe port; the second silently inherited the
# default, which is worse.
if not os.environ.get("MIF_PROJECT_MARKER"):
    print("REFUSING to run: set MIF_PROJECT_MARKER (and MIF_BRIDGE_PORT if not the default) to name")
    print("the editor this may write into. It runs suites with their cooked guards REMOVED, which")
    print("is precisely the state where an 'assert this is refused' becomes 'perform this'. Point")
    print("it at a disposable project.")
    raise SystemExit(2)

# THIS TOOL EDITS SUITES ON DISK and puts them back in a finally. A finally does not survive a
# Ctrl-C at the wrong moment or a machine going down, so refuse to start unless the suites are
# clean in git - then `git checkout -- tools/` always recovers. Learned the mild way: an earlier
# version's restore raised TypeError inside its own finally and left a suite unwrapped.
#
# EVERY test_ FILE, not just the guarded ones. Checking only `targets` has a hole with teeth: a
# suite left unwrapped by a previous crash no longer contains the marker, so it drops out of
# targets and its dirty state goes unreported by the very check meant to catch it.
allsuites = ["tools/" + f for f in sorted(os.listdir(TOOLS))
             if f.startswith("test_") and f.endswith(".py")]
dirty = subprocess.run(["git", "status", "--porcelain", "--"] + allsuites,
                       cwd=REPO, capture_output=True, text=True).stdout.strip()
if dirty:
    print("REFUSING to run: these suites have uncommitted changes, and this tool rewrites them")
    print("in place. Commit them first, so an interrupted run is recoverable with git.")
    print(dirty)
    raise SystemExit(2)

print("%-38s %-4s %-13s %-13s %s" % ("suite", "#", "guarded", "one removed", "verdict"))
keep = drop = inert = odd = 0
for f in targets:
    path = os.path.join(TOOLS, f)
    # RAW BYTES for the restore, text for the analysis. The first version restored the decoded
    # string and TypeError'd inside the finally, which left test_anim_curve unwrapped on disk -
    # exactly the state the finally exists to prevent.
    raw = io.open(path, "rb").read()
    orig = raw.decode("utf-8")
    n = len(guards(ast.parse(orig.replace("\r\n", "\n"))))
    base = run(path)
    for k in range(n):
        try:
            new = unwrap(orig.replace("\r\n", "\n"), k)
            ast.parse(new)
        except Exception as exc:                               # noqa: BLE001
            print("%-38s %-4d unwrap failed: %s" % (f, k, str(exc)[:60]))
            odd += 1
            continue
        io.open(path, "wb").write(new.replace("\n", "\r\n").encode("utf-8"))
        try:
            got = run(path)
        finally:
            io.open(path, "wb").write(raw)                     # always put the file back
        b = "rc=%s %s/%s" % base
        g = "rc=%s %s/%s" % got
        if base[1] is None or got[1] is None:
            verdict = "UNDECIDED - no summary line from one of the runs"
            odd += 1
        elif got[2] > base[2]:
            verdict = "KEEP - %d failure(s) appear without it" % (got[2] - base[2])
            keep += 1
        elif got[1] > base[1]:
            verdict = "REMOVE - skips %d working check(s), prevents nothing" % (got[1] - base[1])
            drop += 1
        else:
            verdict = "inert - changes nothing either way"
            inert += 1
        print("%-38s %-4d %-13s %-13s %s" % (f, k, b, g, verdict))
print("")
print("keep: %d    remove: %d    inert: %d    undecided: %d" % (keep, drop, inert, odd))
