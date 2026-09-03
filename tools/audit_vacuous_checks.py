"""Find test assertions that pass no matter what the code does.

WHY THIS EXISTS. Three times in one session an assertion was written, run, reported PASS, and was
proving nothing:

  * a struct-member lookup keyed on `name` when the field is the mangled name_index_guid. It found
    nothing, compared None against "", and passed. The two assertions BESIDE it failed loudly, which
    is the only reason it was noticed.
  * `all("cooked" in b for b in rows)` - which asserted the field was PRESENT, while every cooked
    widget and anim blueprint carried the wrong VALUE. 301 of 1475 rows were mislabelled underneath
    a green check.
  * a crash-journal check that called analyse() and printed the length, when the bug only appeared
    on serialisation.

`all([])` is True in Python. That is the mechanically detectable half of this, and it is what this
tool looks for: an `all(...)` inside a check() whose collection is not asserted non-empty anywhere
nearby. When the call returns nothing at all, the assertion written to inspect the results passes.

HOW NOISY IT IS, stated up front because a tool that cries wolf gets ignored. On first run: 60 raw
`all(...)` checks, 43 with a guard right beside them, 11 candidates after excluding literal tuples
(which can never be empty), and THREE genuinely unguarded. So expect roughly one real finding in
four candidates - read them, do not bulk-fix them.

Several legitimate shapes look identical to the bug and are NOT it:
  * a filtered SUBSET that may legitimately be empty - "every 16-byte parameter reports 4 floats" is
    fine to pass on an asset with no 16-byte parameters
  * a guard that lives AFTER the assertion, or on a different expression (`f.get("count") > 0`)
  * an equality on the same collection above it, which fails when the collection is empty

RULE 2 - PRESENCE STANDING IN FOR VALUE. `all("field" in row for row in rows)` asserts that a key
EXISTS on every row and never what it holds. That is exactly how 301 mislabelled rows passed a green
check: the field was present on all of them and wrong on a fifth of them.

This shape is narrow on purpose. The broad version - any condition that is only a membership test -
matches 202 of 1795 checks here, and nearly all of them are substring assertions on ERROR TEXT
(`"BlockAll" in error`), which ARE value assertions and exactly right. Asking only about presence
across a COLLECTION cuts that to 8, of which 3 were worth strengthening. Narrow beats thorough when
the alternative is two hundred lines nobody reads.

Presence is sometimes genuinely the contract - "the response carries a warning field" is a real thing
to test. Those live in the baseline.

RULE 4 - A CHECKER THAT NEVER RUNS. The three rules above ask whether an assertion proves anything.
The fourth asks it of the tools in this directory. On 2026-08-31 a check was added to
mcp_static_check and wired in AFTER main()'s `if not findings: return 0`, so it executed only on runs
where something else had already failed - which is never, in a healthy repo. It printed OK for hours.
The finder was correct; the wiring was dead code, and both the diff and a direct call to the function
looked fine.

This rule scans tools/*.py for that shape and nothing else: an empty list, appended to by the
analysis, tested for emptiness, returning 0, with more locally-defined analysis after it. The
narrowness is the whole design - see _accumulators_before for the two wider versions that were wrong
6 times out of 6 and then 31 times out of 31. Suites are deliberately not this rule's business; a
suite that skips on an absent fixture is correct, and audit_suite_reach.py already measures the case
where that skip was wrong.

BASELINE. Findings are compared against audit_vacuous_baseline.txt so only NEW ones surface. Accept
the current set with --update-baseline once you have read them.

Usage:
    python tools/audit_vacuous_checks.py                  # report new ones
    python tools/audit_vacuous_checks.py --all            # every candidate, baseline ignored
    python tools/audit_vacuous_checks.py --update-baseline
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "audit_vacuous_baseline.txt")

ALL_ITER = re.compile(r"\ball\s*\(.*?\bfor\s+\w+\s+in\s+(.+?)(?:\)\s*,|\)\s*$|\bif\b)", re.S)
LITERAL_TUPLE = re.compile(r'^\(\s*(?:"[^"]*"|\'[^\']*\')\s*(?:,\s*(?:"[^"]*"|\'[^\']*\')\s*)*,?\s*\)?$')
NAME = re.compile(r'check\(\s*"([^"]*)"')
# Rule 2: presence asserted across a COLLECTION - all("field" in row for row in rows).
PRESENCE = re.compile(r'\ball\s*\(\s*(?:all\s*\(\s*)?"([^"]+)"\s+in\s+\w+', re.S)
# Rule 3: `not <name>` where <name> collected counterexamples out of a FILTERED comprehension.
# Group 1 is the bound name, group 2 the source it iterates - the thing that may be empty.
COMP_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*[\[{]\s*[^\]}]*?\bfor\s+.+?\s+in\s+"
                         r"(.+?)\s+if\b")
NOT_NAME = re.compile(r"\bnot\s+(\w+)\b")
# A source that cannot be empty: a literal, or a module-level CONSTANT.
CONST_SOURCE = re.compile(r"^(?:[\[({\"']|[A-Z_]+$)")


def suites():
    return sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))


def call_span(lines, start, limit=8):
    """The whole check(...) call beginning at `start`, gathered by paren depth.

    STOPS AT THE NEXT check(, which it did not at first. Paren depth alone runs on past the end of a
    one-line call and swallows the following one, so a single injected assertion was reported FOUR
    times - twice for its own line and twice for the line above, once per rule. Four findings for one
    problem is the noise that gets a tool ignored, which is the thing this tool cannot afford.
    """
    depth, j, buf = 0, start, []
    while j < len(lines) and j < start + limit:
        if j > start and re.match(r"\s*check\(", lines[j]):
            break
        buf.append(lines[j])
        depth += lines[j].count("(") - lines[j].count(")")
        if j > start and depth <= 0:
            break
        j += 1
    return " ".join(buf), j


def guarded(lines, start, end, collection):
    """Is the collection asserted non-empty within sight of the assertion?

    Deliberately generous - it looks both BEFORE and AFTER, and accepts a length test, a truthiness
    test, or a count comparison. A guard that lives one line below the assertion still means a human
    thought about the empty case, and flagging it would be the noise this tool cannot afford.
    """
    core = re.split(r"[.\[(]", collection.strip())[0].strip()
    if not core:
        return True
    window = " ".join(lines[max(0, start - 12):min(len(lines), end + 4)])
    pattern = (r"(len\(\s*%s|%s\s*\)?\s*(?:>|==)\s*[1-9]|bool\(\s*%s|count.{0,14}>\s*0|if\s+%s\b)"
               % tuple(re.escape(core) for _ in range(4)))
    return re.search(pattern, window) is not None


def findings():
    out = []
    for fn in suites():
        lines = io.open(os.path.join(HERE, fn), encoding="utf-8", errors="replace").read().split("\n")
        for i, line in enumerate(lines):
            if "check(" not in line:
                continue
            call, end = call_span(lines, i)
            if not re.search(r"\ball\s*\(", call):
                continue
            m = ALL_ITER.search(call)
            if not m:
                continue
            coll = m.group(1).strip().rstrip(")").strip()
            probe = coll if coll.endswith(")") else coll + ")"
            if LITERAL_TUPLE.match(probe):
                continue                       # a literal tuple is never empty
            if guarded(lines, i, end, coll):
                continue
            label = NAME.search(call)
            out.append("%s:%d\t%s" % (fn, i + 1, label.group(1)[:70] if label else coll[:70]))
    return out


def presence_findings():
    """Rule 2: a key asserted PRESENT on every row, and never checked for what it holds.

    Narrow ON PURPOSE. The broad version - any condition that is only a membership test - matches 202
    of 1795 checks in this repo, and nearly all of them are substring assertions on ERROR TEXT
    (`"BlockAll" in error`), which ARE value assertions and exactly right. Asking only about presence
    across a COLLECTION cuts it to 8, of which 3 were worth strengthening.

    Narrow beats thorough when the alternative is two hundred lines nobody reads.
    """
    out = []
    for fn in suites():
        lines = io.open(os.path.join(HERE, fn), encoding="utf-8", errors="replace").read().split("\n")
        for i, line in enumerate(lines):
            if "check(" not in line:
                continue
            call, _end = call_span(lines, i)
            m = PRESENCE.search(call)
            if not m:
                continue
            label = NAME.search(call)
            out.append("%s:%d\tPRESENCE of '%s' - %s"
                       % (fn, i + 1, m.group(1), label.group(1)[:48] if label else ""))
    return out


def counterexample_findings():
    """Rule 3: "no counterexamples" asserted over a collection that may hold nothing.

    `bad = [x for x in rows if wrong(x)]` then `check(..., not bad, ...)` is a good idiom, and eight
    of the nine uses in this repo are right. It fails the same way all([]) does: when `rows` is
    empty there were no counterexamples to find, and the assertion cannot tell that apart from a
    clean pass - which is the distinction it was written to make.

    Same question as Rule 1, so the same guarded() answers it: is the SOURCE asserted non-empty
    within sight? Sources that are literals or module constants are skipped - they cannot be empty.
    """
    out = []
    for fn in suites():
        lines = io.open(os.path.join(HERE, fn), encoding="utf-8", errors="replace").read().split("\n")
        # name -> (line index, source expression), last binding before each use wins
        bound = {}
        for i, ln in enumerate(lines):
            m = COMP_ASSIGN.match(ln)
            if m:
                bound[m.group(1)] = (i, m.group(2).strip())
        for i, line in enumerate(lines):
            if "check(" not in line:
                continue
            call, end = call_span(lines, i)
            for m in NOT_NAME.finditer(call):
                name = m.group(1)
                if name not in bound:
                    continue
                decl, source = bound[name]
                if decl > i:
                    continue                       # bound after the assertion, not this one
                if CONST_SOURCE.match(source):
                    continue                       # a literal or CONSTANT is never empty
                # ASK ABOUT EVERY NAME IN THE SOURCE, not just the leading one. guarded() takes the
                # core by splitting on the first . [ or ( which gives `dict` for dict.fromkeys(made)
                # - so a check guarded with bool(made) read as unguarded. Generous on purpose: the
                # thing being detected is whether a human thought about the empty case.
                if any(guarded(lines, min(i, decl), end, nm)
                       for nm in re.findall(r"[A-Za-z_]\w*", source)):
                    continue
                label = NAME.search(call)
                out.append("%s:%d\tNO-COUNTEREXAMPLE over '%s' - %s"
                           % (fn, i + 1, source[:26],
                              label.group(1)[:44] if label else name))
                break
    return out


def baseline_key(entry):
    """(file, assertion text) - the identity of a finding, WITHOUT the line number.

    A LINE NUMBER IS NOT AN IDENTITY, and treating it as one turns this release gate red for
    edits that changed nothing about the assertions. On 2026-09-03 three comment blocks were added
    ABOVE existing checks in test_list_bones, test_niagara_params and test_niagara_user_params;
    six baselined entries moved by 1 to 15 lines, their text identical one-for-one, and the gate
    went from rc 0 to rc 1 reporting ten "new" assertions that were the same ten as before.

    This is the same mistake audit_suite_reach's own header records about mtimes: a stamp that
    moves for reasons unrelated to content cannot stand in for content.

    WHAT IS GIVEN UP, and it is worth naming rather than discovering later: a genuinely NEW
    assertion whose text matches a baselined one in the same file is now accepted silently. That
    is a real narrowing. It is the smaller loss - a duplicated assertion text in one file is a
    near-miss, while an editor's comment reddening a gate is a certainty, and the second teaches
    people to run --update-baseline without reading, which is the failure this file exists to stop.
    """
    where, _, label = entry.partition("\t")
    return (where.rsplit(":", 1)[0], label)


def load_baseline():
    if not os.path.isfile(BASELINE):
        return set()
    return set(baseline_key(l.rstrip("\n")) for l in io.open(BASELINE, encoding="utf-8")
               if l.strip() and not l.startswith("#"))


# --------------------------------------------------------------------------- rule 4: unreachable
#
# The three rules above ask whether an ASSERTION proves anything. This one asks it of the CHECKER,
# because on 2026-08-31 a check was added to mcp_static_check that printed OK on every run without
# ever having executed:
#
#     if not findings:
#         print("OK  every one can be called - no unbound names")
#         return 0                       # every healthy run leaves here
#
#     lossy = lossy_bool_forwards()      # so this only ran when something ELSE was broken
#
# The finder was correct - called directly it returned the planted row. Reading the diff passed.
# Calling the function passed. Only running the entry point against a planted defect caught it.
#
# WHAT IS AND IS NOT FLAGGED. Two naive versions of this rule were wrong 6/6 and then 31/31 - see
# A `return 0` is only suspicious when the tool has ALREADY DONE its analysis and found nothing, so
# the test has to be an EMPTINESS test (`not x`, `len(x) == 0`) on a name built in main() as a list,
# dict, set or comprehension. That excludes every legitimate early exit in this repo:
#
#   * `if "--update-baseline" in sys.argv:`   - a mode flag, not a result   (3 tools)
#   * `if live is None:`                      - "no bridge, could not check", and `live` comes from
#                                               a function call, not a collection literal
#   * `if not os.path.isfile(RESULTS):`       - no input to measure; not a Name at all
#
# All six were read by hand before this rule was written. "Already fine" was the answer for all of
# them, and the rule is shaped so it stays the answer.

def _empty_test_name(test):
    """`not x` or `len(x) == 0` -> 'x'. Anything else -> None."""
    if (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)):
        return test.operand.id
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        f, c = test.left, test.comparators[0]
        if (isinstance(f, ast.Call) and isinstance(f.func, ast.Name) and f.func.id == "len"
                and f.args and isinstance(f.args[0], ast.Name)
                and isinstance(c, ast.Constant) and c.value == 0):
            return f.args[0].id
    return None


def _accumulators_before(stmts):
    """Names bound to an EMPTY list and later appended to - the shape of a findings accumulator.

    This is the distinction that makes the rule usable, and it took three passes to find. Testing
    only for "assigned a collection" flagged 31 sites, every one of them correct code:

      * `terms = [t.lower() for t in sys.argv[1:] ...]` then `if not terms:` - why_not.py's usage
        banner, printed when the user passed no search terms
      * `anims = [a["path"] for a in M.call("find_assets", ...)]` then `if not anims: return 0` -
        a suite SKIPPING because the project holds no fixture of that type. test_anim_curve even
        records `check("(setup) the project has AnimSequences", len(anims) > 0)` before it skips.

    Both test an INPUT that legitimately comes back empty. The defect tests an OUTPUT: a list that
    started empty and was appended to BY THE ANALYSIS, so "empty" means "found nothing wrong" - and
    anything after that return is a second analysis nobody runs. Comprehension = derived input;
    empty-literal-plus-append = accumulated findings. Structural, so rewording cannot game it.

    The suite half of this question is not ours: audit_suite_reach.py already measures suites that
    run a small fraction of their assertions, which is what a wrongly-taken skip looks like there.
    """
    empty, appended = set(), set()
    for stmt in stmts:
        for n in ast.walk(stmt):
            if (isinstance(n, ast.Assign) and isinstance(n.value, ast.List)
                    and not n.value.elts):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        empty.add(t.id)
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("append", "extend")
                    and isinstance(n.func.value, ast.Name)):
                appended.add(n.func.value.id)
    return empty & appended


def unreachable_findings():
    """Analysis called after main() has already exited 0 on the no-findings path."""
    rows = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".py"):
            continue
        try:
            src = io.open(os.path.join(HERE, fn), encoding="utf-8", errors="replace").read()
            tree = ast.parse(src, filename=fn)
        except (SyntaxError, ValueError):
            continue
        local, main_fn = set(), None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local.add(node.name)
                if node.name == "main":
                    main_fn = node
        if main_fn is None:
            continue
        for i, stmt in enumerate(main_fn.body):
            if not isinstance(stmt, ast.If):
                continue
            name = _empty_test_name(stmt.test)
            if not name or name not in _accumulators_before(main_fn.body[:i]):
                continue
            if not any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
                       and n.value.value == 0 for n in ast.walk(stmt)):
                continue
            for later in main_fn.body[i + 1:]:
                for n in ast.walk(later):
                    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                            and n.func.id in local):
                        rows.append("%s:%d\tRULE 4 %s() is only reached once main() has already "
                                    "returned 0 - it never runs on a clean run"
                                    % (fn, n.lineno, n.func.id))
            break
    return sorted(set(rows))


def main():
    found = (findings() + presence_findings() + counterexample_findings()
             + unreachable_findings())
    if "--update-baseline" in sys.argv:
        body = ["# Accepted vacuous-check candidates. Each was READ and judged acceptable - usually a",
                "# filtered subset that may legitimately be empty. Regenerate with --update-baseline",
                "# only after reading the new entries; the point of the baseline is that a NEW one is",
                "# a thing somebody has to look at.", ""]
        io.open(BASELINE, "wb").write(("\r\n".join(body + sorted(found)) + "\r\n").encode("utf-8"))
        print("baseline updated: %d entry(ies)" % len(found))
        return 0
    show_all = "--all" in sys.argv
    base = set() if show_all else load_baseline()
    new = [f for f in found if baseline_key(f) not in base]
    if not new:
        print("checks OK - %d candidate(s) across all four rules, none new against the baseline"
              % len(found))
        return 0
    print("%d assertion(s) not in the baseline that may prove nothing:" % len(new))
    for f in sorted(new):
        where, label = f.split("\t", 1)
        print("  %-40s %s" % (where, label))
    print("")
    print("RULE 1 - all([]) is True, so the assertion passes when the call returned nothing at all,")
    print("which is usually the failure it was written to catch. Guard the collection first.")
    print("RULE 2 - PRESENCE asserts a key exists and never what it holds. That is how 301 mislabelled")
    print("rows passed a green check. Assert the VALUE, or add a companion check that does.")
    print("RULE 3 - NO-COUNTEREXAMPLE asserts an empty list of offenders. `not []` is also True")
    print("when the source held nothing to examine, which is the case it exists to rule out.")
    print("RULE 4 - a CHECKER placed after main()'s `return 0` runs only when something else")
    print("already failed. The finder can be perfect and never execute; mutation-test the entry")
    print("point, not the function.")
    print("Either is fine to accept with --update-baseline once you have read it and it is right.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
