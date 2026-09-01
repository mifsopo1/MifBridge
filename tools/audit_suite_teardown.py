"""Suites that switch session-global state ON and can leave without switching it OFF.

WHY THIS EXISTS. test_pie_family.py started PIE and stopped it with a bare stop_pie at the bottom of
main() - no try, no finally - and four lines after the start there was a `return 3`. Any exception,
any missing key, any early return between the two left the editor IN A PLAY SESSION: for whatever
ran next, and for the person sitting in front of it.

The requirement was not missing. The spec entry that reopened that family says, in as many words,
"starts PIE inside a try and stops it in a `finally`, and asserts pie_status is back to
state==stopped". The suite was written without it and nothing in this repo compared the two. A SPEC
LINE IS NOT A CONTROL - this file is the control.

WHAT MAKES THIS DIFFERENT FROM THE OTHER SUITE DETECTORS, none of which would have caught it:
  audit_suite_reach     asks how much of a suite RUNS. PIE's ran fine.
  audit_vacuous_checks  asks whether an assertion can fail. These could.
  audit_suite_payloads  asks whether a call is refused for a bad parameter. It was not.
All three ask about the suite's own verdict. This one asks what the suite leaves behind when it is
wrong, which no verdict reports.

THE RULE, and it is deliberately blunt: an acquire must sit lexically inside a `try` whose `finally`
performs the matching release. Not "there is a release somewhere later" - that is exactly what PIE
had. Anything between the two can raise, so the only placement that survives an exception is a
finally. Nesting counts: a finally on an enclosing try is fine.

SCOPED TO PAIRS WHERE LEAVING IT ON IS HARMFUL, listed below with the harm. Endpoints that mutate an
asset are not in scope - that is audit_suite_payloads' and the scratch guard's territory - because
the blast radius here is specifically the SHARED EDITOR SESSION every later suite and the human both
read through.

Exit codes: 0 clean, 1 findings, 2 nothing to check.
"""
import ast
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# acquire -> (release, what leaving it on costs)
PAIRS = {
    "start_pie": ("stop_pie",
                  "the editor is left in a PLAY SESSION - every later read sees the PIE world "
                  "instead of the editor world, and the person at the keyboard has lost their editor"),
    "ui_scenario_start": ("ui_scenario_stop",
                          "a UI scenario stays active, holding the target actor and the input "
                          "capture it set up, and the next scenario_start collides with it"),
    "pie_load_level_instance": ("pie_unload_level_instance",
                                "a streamed level instance stays resident in the PIE world, so "
                                "actor counts and list_sublevels stay wrong for everything after"),
}

# An acquire whose resource lives INSIDE another acquire's resource, and cannot outlive it, is
# already released by that stronger release. Listing this rather than dropping the pair keeps the
# detector honest in both directions: the pair still matters on its own (unloading an instance
# mid-session is a real thing to get wrong) and it is simply not a LEAK when the containing
# session is torn down in a finally.
#
# VERIFIED IN THE SOURCE RATHER THAN ASSUMED, because "it probably dies with the world" is exactly
# the reasoning that produces a detector nobody trusts. pie_load_level_instance creates its level
# through ULevelStreamingDynamic::LoadLevelInstance into a world resolved from EWorldType::PIE, and
# the world's own StreamingLevels array holds the only reference; MifBridgeStreaming.cpp's comment
# on it says "RF_Transient inside a PIE world that will be torn down", which is also why that
# endpoint takes no undo transaction. stop_pie is GEditor->RequestEndPlayMap(), which destroys that
# world context. Neither handler keeps any bridge-side bookkeeping that could go stale.
#
# THE CONTRARY CASE IS THE ONE WORTH REMEMBERING. ui_scenario is ALSO PIE-scoped and is NOT
# subsumed, because its state lives in a file-static GScenario on the bridge side rather than in
# the world - so the world dying leaves the bridge still believing a scenario is running. Being
# scoped to PIE is not the test; where the STATE lives is.
SUBSUMED_BY = {
    "pie_load_level_instance": "start_pie",
}


def calls_in(node):
    """Every (endpoint_name, ast_node) reachable under `node`, by first string argument.

    Reads the FIRST POSITIONAL ARGUMENT of any call, which is how every transport in this repo
    names its endpoint - M.call("start_pie", ...), M.raw_post("start_pie", {}), call("start_pie").
    Matching on the argument rather than on the function name is what lets one rule cover all
    three without listing them, and without breaking when a fourth is added.
    """
    out = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or not sub.args:
            continue
        first = sub.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append((first.value, sub))
    return out


def protected_by(tree, acquire_node, release_name):
    """True when acquire_node sits inside a try whose FINALLY releases.

    Walks every Try in the file and asks whether this acquire is inside its body while the release
    is inside its finalbody. Enclosing tries count, which is why this checks all of them rather
    than only the nearest.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        in_body = any(acquire_node is c for stmt in node.body
                      for _, c in calls_in(stmt))
        if not in_body:
            continue
        for stmt in node.finalbody:
            if any(name == release_name for name, _ in calls_in(stmt)):
                return True
    return False


def main():
    suites = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if not suites:
        print("no suites found")
        return 2

    findings, checked, subsumed, unparsed = [], 0, 0, []
    for path in suites:
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError as exc:
            # NOT A SKIP. A suite that does not parse cannot be checked, and this used to print a
            # SKIP line and carry on to exit 0 - so a corpus where every file was broken read
            # exactly like a corpus where every file was clean.
            #
            # Found by its own plant, which is the good way to find it: the first plant written for
            # this detector removed a `finally:` and left a `try:` with no handler at all. That is a
            # SyntaxError, so this branch swallowed the whole file, no acquires were seen, and the
            # harness reported the detector ASLEEP for a defect it had been prevented from reading.
            unparsed.append((os.path.basename(path), str(exc)))
            continue
        for name, node in calls_in(tree):
            if name not in PAIRS:
                continue
            release, harm = PAIRS[name]
            checked += 1
            if protected_by(tree, node, release):
                continue
            # Not released directly - but a containing session released in a finally covers it.
            stronger = SUBSUMED_BY.get(name)
            if stronger and protected_by(tree, node, PAIRS[stronger][0]):
                subsumed += 1
                continue
            findings.append((os.path.basename(path), node.lineno, name, release, harm))

    if unparsed:
        print("")
        print("DID NOT PARSE - these were NOT checked, so this run says nothing about them:")
        for name, exc in unparsed:
            print("  %s: %s" % (name, exc))
        print("")
    print("suites read              : %d" % len(suites))
    print("acquire calls found      : %d" % checked)
    print("released by a containing session: %d" % subsumed)
    print("not released in a finally: %d" % len(findings))

    # A DETECTOR THAT CHECKED NOTHING REPORTS THAT, rather than printing a clean bill. A corpus with
    # no acquires at all and a corpus where every acquire is protected look identical from the
    # verdict line, and only one of them is evidence.
    if not checked:
        print("")
        print("NOTHING TO CHECK - no suite calls any of: %s." % ", ".join(sorted(PAIRS)))
        print("That is not a pass. This detector has not looked at anything.")
        return 2

    if findings:
        print("")
        for fname, line, acq, rel, harm in findings:
            print("  %s:%d" % (fname, line))
            print("      %s is not released by %s in a finally." % (acq, rel))
            print("      If anything between them raises or returns early, %s" % harm)
        print("")
        print("Wrap from the acquire to the end of the section in try/finally, and put the release")
        print("in the finally. Then READ THE STATE BACK: the release returning ok proves the request")
        print("was accepted, not that the state actually changed.")
    else:
        print("")
        print("Every acquire is released in a finally.")
    # Unparseable counts as a failure: "could not look" is not "nothing to find".
    return 1 if (findings or unparsed) else 0


if __name__ == "__main__":
    sys.exit(main())
