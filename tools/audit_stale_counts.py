"""Numbers written in the front-door docs, checked against the tools that compute them.

WHY THIS EXISTS. On 2026-09-03 and 04 this failed four separate times in one working day:

  * docs/00_ARCHITECTURE.md said "68 ops across 14 ops_* modules" against a real 140 across 20 -
    and that line ALREADY carried a comment explaining it had previously read "12 ops" for long
    enough to be misleading. Knowing about the trap did not prevent it.
  * README.md's Blender cell said "68 ops across 14 modules", with a sentence claiming the figure
    was "counted at packaging time, not typed here" while being typed there.
  * The README badge was 72 Blender ops, 13 endpoints, 85 MCP tools and 12 suites out of date.
  * Three numbers written that same day - a gate count, a timing, and a paragraph ABOUT stale
    numbers - went stale within hours of being written.

check_badge already covers the badge LINE, and covers it well. It is not in --gates, and it reads
one line of one file. Everything else was unguarded.

WHAT THIS CHECKS AND WHAT IT DELIBERATELY DOES NOT

Only four counts, and only where a tool already computes them: UE endpoints, Blender ops, MCP tools
and test suites. Anything else - timings, percentages, "roughly N" - is out of scope, because a
number nothing can recompute cannot be checked and guessing would make this cry wolf.

SCOPED TO THE DOCS THAT CLAIM WHAT IS TRUE NOW. README.md and the architecture, start-here, design
and dashboard docs describe the current state, so a wrong number there misleads. The postmortems,
the gotchas and the parity spec are LOGS: they are full of dated numbers that were correct when
written and are supposed to stay as they are. Auditing a log for staleness would be asking history
to keep changing, and the noise would teach everyone to ignore the check.

RATCHETED, like its siblings. Existing mentions go into the baseline once, deliberately, and only a
NEW disagreement fails. A check that goes red on a line somebody has already read and accepted is a
tax rather than a guard.

Usage:
    python tools/audit_stale_counts.py            # report
    python tools/audit_stale_counts.py --check    # non-zero on anything NEW (this is the gate form)
    python tools/audit_stale_counts.py --update-baseline
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE = os.path.join(HERE, "audit_stale_counts_baseline.json")

# THE DOCS THAT ASSERT THE PRESENT. See the header for why the logs are excluded rather than
# forgotten - a dated line in a postmortem is correct history, and demanding it change is wrong.
CURRENT_DOCS = (
    "README.md",
    os.path.join("docs", "00_ARCHITECTURE.md"),
    os.path.join("docs", "05_DESIGN_SPEC.md"),
    os.path.join("docs", "16_DASHBOARD.md"),
    os.path.join("docs", "18_START_HERE.md"),
)

# (label, regex, which counter). The qualifier word is REQUIRED in every pattern - a bare "140" or
# even "140 ops" appears in prose about all sorts of things, and a pattern loose enough to catch
# every phrasing is loose enough to be wrong constantly.
PATTERNS = (
    ("UE endpoints", re.compile(r"\b(\d{2,4})\s+(?:UE\s+)?endpoints\b"), "endpoints"),
    ("Blender ops", re.compile(r"\b(\d{2,4})\s+(?:Blender\s+|addon\s+)?ops\b"), "ops"),
    ("MCP tools", re.compile(r"\b(\d{2,4})\s+MCP\s+tools\b"), "tools"),
    ("test suites", re.compile(r"\b(\d{2,4})\s+test\s+suites\b"), "suites"),
)


def live_counts():
    """The four numbers, from the same functions make_release's badge uses.

    ONE SOURCE. Reimplementing any of these here would be the exact duplication this file exists to
    complain about, one level up.
    """
    sys.path.insert(0, HERE)
    import make_release as M
    return {
        "endpoints": M.endpoint_count(),
        "ops": M.blender_op_count(),
        "tools": M.mcp_tool_count(),
        "suites": M.suite_count(),
    }


def looks_historical(line):
    """Is this number being QUOTED as history rather than asserted as current?

    The architecture doc and the README both now carry sentences like `this line read "12 ops"` -
    which are correct, deliberate, and must not be flagged. Two signals, both conservative:

      * the number sits inside quotes - somebody is citing a previous version of the text
      * the sentence is in the past tense about the text itself - read / said / was / used to

    A heuristic, and it can be wrong in the safe direction: a MISSED historical line lands in the
    baseline on its first run and is accepted once by a person, which is the same outcome as
    getting it right. Being wrong the other way - suppressing a real staleness - needs the number
    to also be in quotes, which an asserted count is not.
    """
    if re.search(r'["“‘’”]\s*\d{2,4}\s+\w+', line):
        return True
    return bool(re.search(r"\b(?:read|said|was|were|used to (?:say|read)|previously|"
                          r"until|had been|stale)\b", line, re.I))


def scan():
    counts = live_counts()
    found = []
    for rel in CURRENT_DOCS:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        text = io.open(path, encoding="utf-8", errors="replace").read()
        for lineno, line in enumerate(text.splitlines(), 1):
            if looks_historical(line):
                continue
            for label, pattern, key in PATTERNS:
                for m in pattern.finditer(line):
                    written = int(m.group(1))
                    if written != counts[key]:
                        found.append({
                            "file": rel.replace("\\", "/"), "line": lineno, "what": label,
                            "written": written, "actual": counts[key],
                            # ASCII-FOLDED FOR PRINTING. These docs carry en-dashes and curly
                            # quotes, and Windows consoles are cp1252 - printing the line verbatim
                            # crashed this audit on its first run with a UnicodeEncodeError, which
                            # is a check that cannot report its own finding.
                            "text": (line.strip()[:110]
                                     .encode("ascii", "replace").decode("ascii")),
                        })
    return found, counts


def load_baseline():
    try:
        with io.open(BASELINE, encoding="utf-8") as fh:
            return {tuple(r) for r in json.load(fh)}
    except Exception:
        return set()


def key_of(row):
    # DELIBERATELY NOT the number itself. Baselining "this file:line said 68" would go red again the
    # moment the real count moved, which is every time somebody adds an op - the opposite of a
    # ratchet. The accepted thing is the LOCATION, and a person accepted that it may drift.
    return (row["file"], row["what"], row["text"])


def main():
    check = "--check" in sys.argv
    update = "--update-baseline" in sys.argv
    found, counts = scan()
    base = load_baseline()

    print("live counts: %d UE endpoints, %d Blender ops, %d MCP tools, %d test suites"
          % (counts["endpoints"], counts["ops"], counts["tools"], counts["suites"]))
    print("scanned %d doc(s) that assert the PRESENT; logs are excluded on purpose - see the header."
          % len(CURRENT_DOCS))

    if update:
        with io.open(BASELINE, "w", encoding="utf-8", newline="\r\n") as fh:
            json.dump(sorted(list(key_of(r)) for r in found), fh, indent=1)
        print("baseline updated: %d accepted mention(s)" % len(found))
        return 0

    new = [r for r in found if key_of(r) not in base]
    if found:
        print("")
        print("%d number(s) disagree with the tools that compute them (%d NEW):"
              % (len(found), len(new)))
        for r in found:
            print("  %-6s %s:%d  %s says %d, actually %d"
                  % ("NEW" if key_of(r) not in base else "known",
                     r["file"], r["line"], r["what"], r["written"], r["actual"]))
            print("         %s" % r["text"])
    else:
        print("")
        print("OK  every count written in a current-state doc matches the tool that computes it.")

    if new and check:
        print("")
        print("A number in prose beside a tool that prints the same number is a second source of")
        print("truth, and it is always the prose that rots. Point at the command instead, or accept")
        print("it with --update-baseline once you have read it.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
