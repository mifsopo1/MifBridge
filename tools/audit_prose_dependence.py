"""Does any tool's ANSWER depend on the text of a C++ COMMENT?

WHY THIS EXISTS. On 2026-08-31 five source-scanning tools were found reading prose as evidence, all
from one root cause: a grep for a symbol finds the places that USE it and the places that DISCUSS it,
and a well-commented repo has more of the second. The tools therefore got WORSE in proportion to how
carefully each fix explained itself - every postmortem-quality comment above a repaired call site is
a fresh false positive.

  param_reach          read a handler's list of REFUSED spellings as its accepted keys, because
                       "RejectUnknownParams" appeared in a comment saying not to add one.
  audit_postconditions centred a +/-6000 character window on a COMMENT 25 lines above the function it
                       wanted, and saw 56 of 88 read-only endpoints.
  audit_postconditions reported set_pin_default - the founding defect in its own docstring, listed
                       there as FIXED - because the comment written by the fix names the API.
  audit_loop_writes    counted search-and-return loops it had been taught to ignore.
  parity_check         marked a plugin guard "used" because files EXPLAINED its absence. The clearest
                       case is self-refuting: MifBridgeMetasound.cpp says "parity_check still reports
                       that dependency as idle, correctly" - and that sentence is what stopped it.

Reading each tool to check for the pattern is what produced the wrong answer twice: string literals
that looked like search terms turned out to be os.path.join components. So this does not read the
tools at all.

HOW IT WORKS. Run the tool. Run it again with every C++ comment blanked underneath it - same byte
offsets, string literals untouched, so line numbers and quoted text are unaffected. Diff what it
printed. A tool whose output changes is using comment text as evidence.

Comments only, never strings: several of these tools legitimately read string literals (advice text,
LOCTEXT, refusal messages) and blanking those would prove nothing.

THE INTERCEPT COUNT IS PART OF THE RESULT. The first version of this reported all seven tools clean
while intercepting ZERO reads in five of them - the paths they build contain "tools/../Source", which
did not match a resolved prefix, so nothing was ever substituted and "identical" meant "the
experiment did not run". A tool that reads no C++ is reported as such rather than as passing.
"""
import builtins
import difflib
import io
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PRIV = os.path.realpath(os.path.join(ROOT, "Source", "MifBridge", "Private")).lower()

# Tools that scan the C++ sources. Not every tool in the directory - only ones that read Private/.
CANDIDATES = [
    "audit_advice_gaps", "audit_blocking", "audit_dead_params", "audit_family_asymmetry",
    "audit_loop_writes", "audit_message_endpoints", "audit_modals", "audit_mode_params",
    "audit_postconditions", "audit_promise_flags", "audit_suite_payloads", "harvest_param_table",
    "mcp_sends_unknown", "mcp_static_check", "param_reach", "parity_check", "why_not",
]

# WHAT IS DELIBERATELY NOT DRIVEN, and why - because "not in the list" was how audit_blocking went a
# day unexamined, and an unexplained omission is indistinguishable from an oversight.
#
# Every one of these reads Source/MifBridge/Private and is still excluded:
NOT_DRIVEN = {
    "audit_detectors_fire": "it PLANTS defects into Private/ and restores them. Running it "
                            "in-process, inside a harness that swaps open() for a scrubbing "
                            "reader, would have it plant into a scrubbed view and restore "
                            "something else. Nothing here may drive it.",
    "make_release": "a release gate that runs the other tools; driving it from here would nest "
                    "this harness inside itself",
    "audit_describe_drift": "reads the LIVE editor, not the source, for half its answer - a "
                            "scrubbed source view changes one side of a comparison and the "
                            "difference would mean nothing",
    "coverage_gaps": "same - it asks the running registry what exists",
    "spec_check": "reads the SPEC as its corpus; Private/ only to resolve names",
}

# STRING LITERALS ARE THE OTHER HALF, and this harness could not see them until 2026-08-31.
#
# blank_comments says so in its own docstring: "string literals untouched". So a tool matching an API
# name inside a TEXT("...") was invisible here - and audit_blocking was exactly that, sitting at
# exit 1 for a day on MifBridgeDescribe.cpp:297, a generated notes entry reading "FPhysicsAssetUtils
# ::CreateFromSkeletalMesh puts up an FScopedSlowTask MakeDialog". Prose saying a blocker is NOT
# used, counted as a blocker. audit_blocking was also not in CANDIDATES at all, so neither half of
# the miss had a chance.
#
# Three passes now: raw, comments blanked, comments AND strings blanked. A tool whose answer changes
# between the second and third is STRING-dependent, which is a different claim from comment-
# dependent and needs its own list of deliberate readers - several tools here read strings as their
# entire job.
EXPECTED_STRINGS = {
    "audit_message_endpoints":
        "its whole subject is the TEXT(...) a caller reads - blanking strings removes the corpus",
    "audit_advice_gaps":
        "collects imperative advice out of Fail()/warning STRINGS; that is the input, not noise",
    "harvest_param_table":
        "harvests the accepted-key TEXT(\"...\") literals themselves",
    "audit_dead_params":
        "reads the accepted-key literals to know what an endpoint accepts",
    "param_reach":
        "same - the accepted keys are string literals",
    "parity_check":
        "reads MIF_BIND(name) and _post(\"name\") literals",
    "mcp_sends_unknown":
        "compares literal key names on both sides",
    "audit_modals":
        "its FOUNDATIONS quote engine LINES as strings and check they still say it",
    "audit_loop_writes":
        "keys on Out->SetXField(TEXT(\"name\")) - the field name is a string literal",
    "audit_postconditions":
        "matches SILENT_APIS names, some of which appear in TEXT() as well as in code",
    "audit_promise_flags":
        "uses a COMMENTS-ONLY scrubber on purpose - TEXT(\"confirm\") in the accepted-key list IS "
        "the evidence that an endpoint promised the flag, so blanking strings removes the subject",
    "audit_suite_payloads":
        "compares suite call literals against the accepted-key literals in RejectUnknownParams; "
        "both sides are strings",
    "mcp_static_check":
        "reads JBool/JBoolAny defaults and the TEXT(\"key\") they are keyed on",
    "audit_family_asymmetry":
        "groups MIF_BIND(name) literals and matches them against SetXField(TEXT(\"field\")) names",
    "audit_mode_params":
        "reads the accepted-key literals to know which parameters a mode ignores",
    "why_not":
        "its entire subject is recorded decisions - the refusal reasons in TEXT() and the design "
        "notes in comments. Blanking either removes what it exists to search.",
}

# Some of these tools WRITE when run bare. Running one in-process is not a read-only act, and this
# harness found that out the hard way: harvest_param_table regenerates a table compiled into the DLL,
# and the first run of this file rewrote MifBridgeDescribe.cpp underneath it. The regeneration
# happened to be correct - the table was stale, because a source edit earlier the same night had
# shifted 21 lines - but that is luck, not safety. Give every tool the argv that makes it read-only,
# and verify afterwards that it was (see the digest guard in run_tool).
ARGS = {
    "harvest_param_table": ["--check"],
}

# DELIBERATE prose readers. Each must say what it reads and why, because the alternative to this
# list is silently accepting every future accident.
EXPECTED = {
    "why_not":
        "reads comments AS ITS SUBJECT and says so in its own docstring: it answers 'was this "
        "absence deliberate' from the refusal reasons in TEXT() and the design notes in comment "
        "blocks. Blanking comments takes its corpus from 178 notes to 0, which is the tool "
        "working. It is the one tool here whose ANSWER SHOULD change - anything it reports is "
        "prose by definition, and a reader is told that at the top of every run.",
    "harvest_param_table":
        "finds the region it owns by MARKER COMMENTS - '// >>> MIF_HARVEST_BEGIN' and its END - so "
        "blanking comments makes the markers vanish and it reports them missing. That is the one "
        "place in this repo where comment text is legitimately structural: the markers exist to be "
        "found, and a generator that located its output any other way would be worse.",
    "audit_postconditions":
        "asks two questions of two texts, on purpose. What a handler DOES is read from scrubbed "
        "code; what it CLAIMS is read from prose, because half of VERIFY_MARKERS are comment idioms "
        "(READ BACK, 'rather than assume') added to stop re-reporting handlers that genuinely "
        "verify. The cost is accepted and written down at the call site: a handler could claim in a "
        "comment to verify and not do it. Mutation is the claim worth doubting, because a false "
        "mutation flag sends a reader to a handler with nothing wrong with it.",
    "audit_mode_params":
        "same trade as audit_postconditions, and made deliberately on 2026-09-03. Its READING LIST "
        "is computed from scrubbed code; its SUPPRESSIONS are a comment idiom, '// MODE-PARAMS-OK: "
        "<reason>', so blanking comments brings nine handlers back. The alternative was a smarter "
        "scan and it was tried first: 'the parameter literal appears somewhere other than the "
        "accept-list and the accessors' cleared six handlers and three of them for reasons that had "
        "nothing to do with refusals - a range-for alias read, a ReadVectorField helper, and an "
        "Out->SetStringField writing the RESPONSE. Right answer, wrong reason. THE COST, stated "
        "rather than discovered: a handler can carry the marker and refuse nothing, and this file "
        "cannot tell. It is bounded by the reason being mandatory and the suppressed count being "
        "printed on every run, including when it is zero.",
}


from harvest_param_table import blank_comments_and_strings   # the one shared scrubber


def blank_comments(text):
    """Comment CONTENT -> spaces. Byte offsets and newlines preserved; string literals untouched."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            q = c
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            if text[i + 1] == "/":
                j = text.find("\n", i)
                j = n if j < 0 else j
            else:
                j = text.find("*/", i + 2)
                j = n if j < 0 else j + 2
            for k in range(i, j):
                if text[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


_real_open, _real_io_open = builtins.open, io.open
_state = {"reads": 0, "scrub": False}


def _source_digest():
    """path -> (size, mtime) for every C++ file, so a tool that writes one cannot do it quietly."""
    out = {}
    for base, _dirs, files in os.walk(os.path.join(ROOT, "Source")):
        for fn in files:
            if fn.endswith((".cpp", ".h", ".cs")):
                full = os.path.join(base, fn)
                try:
                    st = os.stat(full)
                    out[full] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
    return out


def _patch(fn):
    def opener(path, *a, **kw):
        try:
            p = os.path.realpath(str(path)).lower()
        except Exception:
            return fn(path, *a, **kw)
        mode = a[0] if a else kw.get("mode", "r")
        if "b" not in mode and p.startswith(PRIV) and p.endswith((".cpp", ".h")):
            _state["reads"] += 1
            if _state["scrub"]:
                text = _real_open(path, encoding="utf-8", errors="replace").read()
                # "comments" blanks comment CONTENT only; "strings" blanks comments AND string
                # literals, so a difference between the two passes isolates string dependence.
                fn_scrub = (blank_comments_and_strings if _state["scrub"] == "strings"
                            else blank_comments)
                return io.StringIO(fn_scrub(text))
        return fn(path, *a, **kw)
    return opener


def run_tool(tool, scrub):
    """(stdout+stderr, number of C++ files read). Runs in-process; cwd is the plugin root."""
    _state["reads"], _state["scrub"] = 0, scrub
    before = _source_digest()
    buf = io.StringIO()
    old = (sys.stdout, sys.stderr, sys.argv, os.getcwd())
    sys.stdout = sys.stderr = buf
    sys.argv = [tool + ".py"] + ARGS.get(tool, [])
    os.chdir(ROOT)
    builtins.open, io.open = _patch(_real_open), _patch(_real_io_open)
    try:
        runpy.run_path(os.path.join(HERE, tool + ".py"), run_name="__main__")
    except SystemExit:
        pass
    except Exception:
        buf.write("\nEXCEPTION\n" + traceback.format_exc())
    finally:
        builtins.open, io.open = _real_open, _real_io_open
        sys.stdout, sys.stderr, sys.argv = old[0], old[1], old[2]
        os.chdir(old[3])
    # Testing this guard is not as simple as removing a tool's read-only argv and watching it fire:
    # harvest_param_table skips the write entirely when the rendered table already matches the file,
    # so a bare run over a FRESH table is genuinely a no-op and nothing should fire. The detector was
    # verified separately by moving one source file's mtime by a second and confirming it was named
    # (then restoring the timestamp exactly). Size and mtime, not content hashing, because a write of
    # identical bytes still means the tool holds a file handle open for writing on the tree it is
    # supposed to be measuring.
    changed = sorted(k for k, v in _source_digest().items() if before.get(k) != v)
    if changed:
        # Loud and immediate. A diagnostic that edits the thing it is measuring is worse than no
        # diagnostic, and the damage is silent unless something looks for it.
        raise SystemExit("audit_prose_dependence: %s MODIFIED source files and must not have: %s\n"
                         "Give it a read-only argv in ARGS, or drop it from CANDIDATES."
                         % (tool, ", ".join(changed)))
    return buf.getvalue(), _state["reads"]


def main():
    print("Does a tool's answer change when C++ comments - or string literals - are blanked?")
    print("=" * 78)
    unexpected, skipped = [], []
    for tool in CANDIDATES:
        if not os.path.isfile(os.path.join(HERE, tool + ".py")):
            continue
        plain, nreads = run_tool(tool, None)
        if nreads == 0:
            skipped.append(tool)
            print("  %-24s reads no C++ directly - not tested" % tool)
            continue
        scrubbed, _ = run_tool(tool, "comments")
        stringless, _ = run_tool(tool, "strings")

        # THE STRING PASS FIRST, because it is the one that was missing and the one that caught a
        # real defect. Reported separately from comment dependence: they are different claims.
        if scrubbed != stringless and tool not in EXPECTED_STRINGS:
            sdiff = [l for l in difflib.unified_diff(scrubbed.splitlines(),
                                                     stringless.splitlines(), lineterm="", n=0)
                     if l[:1] in "+-" and l[:3] not in ("+++", "---")]
            unexpected.append((tool + " (strings)", sdiff))
            print("  STRINGS %-24s %3d files, ANSWER CHANGES when string literals are blanked "
                  "(%d lines)" % (tool, nreads, len(sdiff)))
            for l in sdiff[:6]:
                print("            %s" % l[:110])

        if plain == scrubbed:
            print("  ok      %-24s %3d files, answer unchanged by comments" % (tool, nreads))
            continue
        diff = [l for l in difflib.unified_diff(plain.splitlines(), scrubbed.splitlines(),
                                                lineterm="", n=0)
                if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        if tool in EXPECTED:
            print("  by design %-22s %3d files, answer changes (%d lines)"
                  % (tool, nreads, len(diff)))
            print("            %s" % EXPECTED[tool])
            continue
        unexpected.append((tool, diff))
        print("  PROSE   %-24s %3d files, ANSWER CHANGES (%d lines)" % (tool, nreads, len(diff)))
        for l in diff[:6]:
            print("            %s" % l[:132])

    print("=" * 78)
    if skipped:
        print("  not tested (no direct C++ reads): %s" % ", ".join(skipped))
    if not unexpected:
        print("OK  no tool reads comment text as evidence except where it says it does")
        return 0
    print("%d tool(s) read PROSE as evidence." % len(unexpected))
    print("Match against scrubbed source - harvest_param_table.blank_comments_and_strings is the")
    print("one scrubber - or add the tool to EXPECTED with what it reads and why. Do not add it to")
    print("EXPECTED to make this quiet: every entry there is a claim someone will rely on.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
