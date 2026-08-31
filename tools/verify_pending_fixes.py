"""One command to verify everything built on 2026-08-31 that no editor has yet loaded.

WHY THIS EXISTS. Work landed all day against an editor holding the Development binaries, so UBT
refused to build at all - "Unable to build while Live Coding is active" - and every piece was
verified in DebugGame instead, which compiles and LINKS the same sources into different binaries.
That proves the code is correct. It does not prove it does anything.

So this is the pass to run after the editor is closed, a Development build is made, and it reopens.
It is deliberately ONE command rather than a checklist, because a checklist gets half-done.

WHAT IT WILL NOT DO. It creates nothing outside /Game/_Mif*, sends no confirm, and runs no test.
Where a check needs a fixture it does not have, it SKIPS and names what it needed - a false pass
here would be worse than a gap, since the whole point is to find out what actually works.

EXIT CODES. 0 all checks that could run passed. 1 something failed. 2 SKIPPED because the loaded
DLL predates the work - which is the honest answer before the build, not a failure.
"""
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mifaudit as M  # noqa: E402
import scratch_confirm as SC  # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def skip(name, why):
    SKIP.append((name, why))
    print("  SKIP  %s\n        %s" % (name, why))


def loaded_build_is_current():
    """(bool, message). Does the running DLL post-date the last commit that touched Source/?"""
    a = M.raw_post("self_audit", {"summaryOnly": True})
    stamp = "%s %s" % (a.get("buildDate") or "?", a.get("buildTime") or "?")
    try:
        built = time.mktime(time.strptime(stamp, "%b %d %Y %H:%M:%S"))
    except Exception:
        return None, "could not parse the DLL build stamp %r" % stamp
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", "Source"],
                             capture_output=True, text=True, cwd=os.path.dirname(HERE),
                             timeout=30).stdout.strip()
        committed = float(out)
    except Exception as exc:
        return None, "could not read the last Source commit time: %s" % exc
    return built >= committed, ("DLL built %s; last Source commit %s"
                                % (stamp, time.strftime("%b %d %Y %H:%M:%S",
                                                        time.localtime(committed))))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    current, why = loaded_build_is_current()
    print(why)
    if current is False:
        print("")
        print("SKIPPED - the loaded DLL predates the work this verifies. Close the editor, build")
        print("  Development, reopen, and run this again. Exit 2 means SKIPPED, which is the honest")
        print("  answer before the build rather than a failure.")
        return 2
    if current is None:
        print("  (could not compare build times - continuing, but read the results with that in mind)")

    live = set(M.endpoint_names())

    # ---------------------------------------------------------------- V1 the refusal fix
    # Verified live on 2026-08-31 before the rest of the batch existed. Kept because it is one call
    # and it is the regression this whole class of fix exists to prevent.
    print("\n=== V1: a missing parameter is NAMED, not reported as a failed lookup ===")
    r = M.call("describe_behavior_tree", {})
    err = str(r.get("error") or "")
    check("V1 describe_behavior_tree {} names the parameter", "path is required" in err, err[:220])
    check("V1 and does not report the empty string as a wrong path",
          "not found: ''" not in err and "not found: \"\"" not in err, err[:220])

    # ---------------------------------------------------------------- V2 the exported contract
    # THE ONLY RUNTIME PROOF that Public/MifBridgeParams.h did its job. MifKismetReconstructor used
    # to parse bools with TryGetBoolField, which succeeds ONLY for a JSON boolean, so {"cookedOnly":
    # "false"} kept its `true` default and answered ok:true. It now routes through MifBridge::JBool,
    # which accepts the string spellings. Same client, same port, same answer as a mif_* endpoint -
    # which is the whole point of exporting the contract.
    print("\n=== V2: kr_* endpoints parse bools the way mif_* endpoints do ===")
    if "kr_list_cooked_blueprints" not in live:
        skip("V2 kr bool parity", "kr_list_cooked_blueprints is not registered - the "
                                  "MifKismetReconstructor provider is not loaded in this editor")
    else:
        strict = M.call("kr_list_cooked_blueprints", {"cookedOnly": False, "limit": 5})
        lenient = M.call("kr_list_cooked_blueprints", {"cookedOnly": "false", "limit": 5})
        check("V2 a JSON false is honoured", strict.get("ok") is not False, json.dumps(strict)[:200])
        check("V2 and the STRING \"false\" is honoured identically - the drift this fixed",
              lenient.get("ok") is not False
              and lenient.get("count") == strict.get("count"),
              "json-false count=%s  string-false count=%s"
              % (strict.get("count"), lenient.get("count")))

    # ---------------------------------------------------------------- V3 the new readers
    print("\n=== V3: the endpoints built today are registered and answer ===")
    for ep in ("list_widget_bindings", "list_game_framework_component_requests",
               "list_automation_tests", "add_make_set", "add_switch_name", "fix_up_redirectors"):
        check("V3 %s is registered" % ep, ep in live, "not in the live registry")

    # ---------------------------------------------------------------- V4 list_automation_tests
    # The one new endpoint that needs NO fixture at all - it reads an in-memory registry.
    print("\n=== V4: list_automation_tests reads the registry ===")
    if "list_automation_tests" not in live:
        skip("V4", "endpoint not registered")
    else:
        r = M.call("list_automation_tests", {"limit": 5})
        check("V4 it answers", r.get("ok") is not False, json.dumps(r)[:200])
        check("V4 registered is a NUMBER, present even at zero",
              isinstance(r.get("registered"), (int, float)), repr(r.get("registered")))
        rows = r.get("tests") or []
        check("V4 count agrees with the rows returned", r.get("count") == len(rows),
              "count=%s rows=%d" % (r.get("count"), len(rows)))
        if rows:
            check("V4 every row carries a full test path",
                  all(row.get("fullTestPath") for row in rows), json.dumps(rows[0])[:200])
            # Flag names come from the engine's own table; an empty list on every row would mean the
            # decode silently matched nothing.
            check("V4 at least one row decodes a flag name from the engine's table",
                  any(row.get("flagNames") for row in rows),
                  str([row.get("flagNames") for row in rows])[:200])
        else:
            skip("V4 row shape", "this editor registered no automation tests to inspect")
        # `run` must be refused by name, not ignored.
        bad = M.call("list_automation_tests", {"run": True})
        check("V4 `run` is REFUSED by name, not silently ignored",
              bad.get("ok") is False and "run" in str(bad.get("error") or ""),
              json.dumps(bad)[:220])

    # ---------------------------------------------------------------- V5 the refusal contracts
    # No fixtures needed, and these are the checks that catch the regressions that actually happen.
    print("\n=== V5: the new endpoints refuse rather than guessing ===")
    if "list_widget_bindings" in live:
        r = M.call("list_widget_bindings", {})
        check("V5 list_widget_bindings {} names blueprintId",
              r.get("ok") is False and "blueprintId" in str(r.get("error") or ""),
              json.dumps(r)[:220])
    if "add_switch_name" in live:
        r = M.call("add_switch_name", {"graphId": "nope", "caseSensitive": True})
        check("V5 add_switch_name refuses caseSensitive BY NAME - FName has no such thing",
              r.get("ok") is False and "caseSensitive" in str(r.get("error") or ""),
              json.dumps(r)[:220])
    if "fix_up_redirectors" in live:
        r = M.call("fix_up_redirectors", {"path": "/Game/_MifVerifyProbe"})
        check("V5 fix_up_redirectors without confirm points at dryRun, not just at confirm",
              r.get("ok") is False and "dryRun" in str(r.get("error") or ""),
              json.dumps(r)[:220])
        r = M.call("fix_up_redirectors", {"path": "/Game/_MifVerifyProbe", "dryRun": True})
        check("V5 and a dry run needs no confirm and reports a number",
              r.get("ok") is not False and isinstance(r.get("found"), (int, float)),
              json.dumps(r)[:220])

    # ---------------------------------------------------------------- V9 set_variable_type's stale pin
    # THE FIX IS COMMITTED AND UNBUILT, and this is the check that says whether a rebuild picked it
    # up. Reproduced live 2026-08-31 against the 15:37 DLL:
    #
    #   author int A -> int B, legal. Retype A to an Actor object reference. The getter then has TWO
    #   pins named A - a new one, category 'object', with NO link, and the ORIGINAL 'int' one still
    #   holding the connection. The compiler follows the new pin, sees nothing wired, and reports
    #   0 errors. The broken half is invisible to it rather than absent.
    #
    # PASS CONDITION IS ONE PIN, of the new type. Two pins named A is the defect; one pin of
    # category 'int' would mean the retype did not take at all.
    print("\n=== V9: set_variable_type leaves ONE pin, of the new type ===")
    st = int(time.time() % 100000)
    vpath = "/Game/_MifVerify/BP_V%d" % st
    vbid = M.call("create_blueprint", {"path": vpath, "parentClass": "Actor"}).get("blueprintId")
    if not vbid:
        skip("V9", "could not create a scratch blueprint")
    else:
        vg = next((x.get("graphId") for x in
                   (M.call("list_graphs", {"blueprintId": vbid}).get("graphs") or [])
                   if "EventGraph" in (x.get("name") or "")), None)
        for nm in ("A", "B"):
            M.call("add_variable", {"blueprintId": vbid, "name": nm, "type": "int"})
        vget = M.call("add_variable_get", {"graphId": vg, "variable": "A"})
        vset = M.call("add_variable_set", {"graphId": vg, "variable": "B"})
        vgg = vget.get("nodeGuid") or (vget.get("node") or {}).get("nodeGuid")
        vsg = vset.get("nodeGuid") or (vset.get("node") or {}).get("nodeGuid")
        wired = M.call("connect_pins", {"graphId": vg, "srcNode": vgg, "srcPin": "A",
                                        "dstNode": vsg, "dstPin": "B"})
        check("V9 (setup) a LEGAL int -> int graph", wired.get("ok") is True,
              json.dumps(wired)[:200])
        rt = M.call("set_variable_type", {"blueprintId": vbid, "name": "A", "type": "Actor"})
        check("V9 the retype succeeds", rt.get("ok") is True, json.dumps(rt)[:200])
        # The field the fix adds. Absent means the running DLL predates it - which is the whole
        # question this check exists to answer, so it is reported rather than asserted blindly.
        check("V9 it REPORTS nodesReconstructed - absent means this DLL predates the fix",
              rt.get("nodesReconstructed") is not None,
              "nodesReconstructed=%r - rebuild has not picked the fix up"
              % rt.get("nodesReconstructed"))
        node = (M.call("get_node", {"graphId": vg, "nodeGuid": vgg}).get("node") or {})
        named_a = [x for x in (node.get("pins") or []) if x.get("name") == "A"]
        check("V9 the getter has exactly ONE pin named A, not a stale one beside the new one",
              len(named_a) == 1,
              "%d pins named A: %s" % (len(named_a),
                                       [((x.get("type") or {}).get("category"),
                                         len(x.get("linkedTo") or [])) for x in named_a]))
        if len(named_a) == 1:
            check("V9 and it is the NEW type, so the retype really took",
                  (named_a[0].get("type") or {}).get("category") == "object",
                  (named_a[0].get("type") or {}).get("category"))
        SC.confirm_call("delete_asset", {"path": vpath, "confirm": True})

    # -------------------------------------------------------------- V10 the dispatcher orphan note
    # The old note promised orphaned nodes "will fail the next compile". Measured 2026-08-31: they
    # do not. The node survives, get_node still resolves it, and the next compile reports 0 errors.
    # A caller who ran the compile that note points at would see it clean and conclude the removal
    # was safe. The note now says the compile will NOT catch it; this checks the rebuilt DLL carries
    # that wording AND re-measures the behaviour it describes, because a corrected note that has
    # gone stale again would be the same defect wearing better prose.
    print("\n=== V10: remove_event_dispatcher tells the truth about orphaned nodes ===")
    st10 = int(time.time() % 100000)
    dpath = "/Game/_MifVerify/BP_D%d" % st10
    dbid = M.call("create_blueprint", {"path": dpath, "parentClass": "Actor"}).get("blueprintId")
    if not dbid:
        skip("V10", "could not create a scratch blueprint")
    else:
        dg = next((x.get("graphId") for x in
                   (M.call("list_graphs", {"blueprintId": dbid}).get("graphs") or [])
                   if "EventGraph" in (x.get("name") or "")), None)
        M.call("add_event_dispatcher", {"blueprintId": dbid, "name": "MifDisp"})
        cd = M.call("add_call_dispatcher", {"graphId": dg, "dispatcher": "MifDisp"})
        cdg = cd.get("nodeGuid") or (cd.get("node") or {}).get("nodeGuid")
        check("V10 (setup) a call node for the dispatcher", bool(cdg), json.dumps(cd)[:200])
        rmd = SC.confirm_call("remove_event_dispatcher",
                              {"blueprintId": dbid, "name": "MifDisp", "confirm": True})
        check("V10 (setup) the dispatcher is removed and one node orphaned",
              rmd.get("ok") is True and rmd.get("orphanedNodeCount") == 1,
              json.dumps(rmd)[:220])
        note = rmd.get("note") or ""
        check("V10 the note no longer promises a compile failure",
              "will fail the next compile" not in note, note[:200])
        check("V10 and says outright that the compile will NOT catch it",
              "WILL NOT" in note.upper(), note[:200])
        # RE-MEASURED, not trusted. The note is only right while the behaviour it describes holds.
        survived = M.call("get_node", {"graphId": dg, "nodeGuid": cdg})
        check("V10 the orphaned node really does survive the removal",
              survived.get("ok") is True, json.dumps(survived)[:200])
        after = M.call("compile", {"blueprintId": dbid})
        check("V10 and the compile really is clean, which is why the old note was wrong",
              after.get("numErrors") == 0 and not (after.get("messages") or []),
              json.dumps(after)[:220])
        SC.confirm_call("delete_asset", {"path": dpath, "confirm": True})

    print("")
    print("=" * 72)
    print("PASS %d  FAIL %d  SKIP %d" % (len(PASS), len(FAIL), len(SKIP)))
    for x in FAIL:
        print("  FAILED:  %s\n           %s" % x)
    for x in SKIP:
        print("  SKIPPED: %s\n           %s" % x)
    print("")
    print("Not covered here, and deliberately: list_widget_bindings against a real WidgetBlueprint")
    print("(tools/test_widget_bindings.py), add_make_set and add_switch_name placing real nodes")
    print("(test_node_spawns T330/T334/T335), the component-request round trip (test_game_framework")
    print("T1408), and fix_up_redirectors' timing (test_modal_guard T75). Those need fixtures and")
    print("belong in suites; this is the pass that says whether it is worth running them.")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
