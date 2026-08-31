"""Verify the fixes that are built and committed but not yet observable. Run AFTER the editor reloads.

WHY THIS EXISTS. Three C++ fixes landed on 2026-08-31 that are compile-verified and nothing more,
because the running editor loads a DLL older than they are. Each is marked `- [ ]` in the spec rather
than `- [x]`, on that file's own rule - "'- [x]' only when BUILT, TESTED and COMMITTED". This turns
"go and check three things by hand" into one command.

It is NOT named test_*.py on purpose: `run_all_suites` picks those up, and a suite that must fail
until someone rebuilds is a red result nobody can act on. This one SKIPS with exit 2 when the loaded
build predates the fixes, which is the honest answer to "did it work?" before the code is loaded.

WHAT IT CANNOT CHECK, stated rather than skipped past. layersCreated needs an `add` that reaches the
implicit-creation path, and that needs a classic non-partitioned level current - the SDK editor's
open world is World Partitioned, where AActor::SupportsLayers is false for every actor and every add
is refused first. See the spec item; it is not reachable from here even with a fresh DLL.
"""
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mifaudit as M
import scratch_confirm as SC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "\n        " + str(detail)[:300]))


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
    if current is None:
        print("SKIPPED - could not establish whether the loaded build contains the fixes.")
        return 2
    if not current:
        print("")
        print("SKIPPED - the running editor loads a DLL OLDER than the fixes. Nothing is wrong;")
        print("they simply are not in the process yet. Rebuild, then re-run this:")
        print("    live_coding_compile {confirm:true}   - patches the running editor. Its own")
        print("        refusal warns that a bad patch can destabilise the process holding your")
        print("        unsaved work, so this is a decision for a person, not for a sweep.")
        print("    or close the editor and build normally.")
        print("Exit code 2 means SKIPPED, distinct from 0 (passed) and 1 (failed) on purpose.")
        return 2

    # ---------------------------------------------------------------- 1. the refusal messages
    print("\n=== V1: a missing parameter is named, not reported as a failed lookup ===")
    for ep, want in (("describe_behavior_tree", "path"), ("list_blackboard_keys", "path")):
        r = M.raw_post(ep, {})
        err = (r.get("error") or "")
        check("V1 %s with no arguments is refused" % ep, r.get("ok") is False, json.dumps(r)[:200])
        check("V1 %s names the missing parameter rather than 'not found: '" % ep,
              want in err.lower() and "required" in err.lower(), err[:220])
        check("V1 %s does not report a lookup of the empty string" % ep,
              not err.rstrip().endswith(":"), err[:220])

    # ---------------------------------------------------------------- 2. create after delete
    print("\n=== V2: create_asset after delete_asset at the same path (docs/06 issue 28) ===")
    path = "/Game/_MifScratch/MifVerify%d" % (int(time.time()) % 100000)
    made = M.call("create_asset", {"path": path, "class": "/Script/EnhancedInput.InputAction"})
    check("V2 (setup) the scratch asset is created", made.get("ok") is True, json.dumps(made)[:220])
    if made.get("ok") is True:
        obj = path + "." + path.rsplit("/", 1)[1]
        # TOUCH IT FIRST, and this is the whole difference between a real check and a vacuous one.
        # The first draft created and deleted within milliseconds and then PASSED against a build
        # that certainly lacks the fix - because nothing had referenced the object, so it was
        # collected and there was no corpse to collide with. The original reproduction had read and
        # written the asset before deleting it. A check that passes when the precondition never
        # occurred proves nothing at all.
        M.call("get_property", {"objectPath": obj, "propertyPath": "ValueType"})
        gone = SC.confirm_call("delete_asset", {"path": path, "confirm": True})
        check("V2 (setup) and deleted", gone.get("ok") is True, json.dumps(gone)[:220])

        # Is there actually a corpse? If the object is fully gone, this build cannot exhibit the
        # defect and the re-create says nothing either way. SKIP rather than claim a pass.
        resident = M.call("get_property", {"objectPath": obj, "propertyPath": "ValueType"})
        still_there = resident.get("ok") is True
        print("        (deleted object still resident: %s)" % still_there)
        again = M.call("create_asset", {"path": path, "class": "/Script/EnhancedInput.InputAction"})
        if still_there:
            check("V2 a deleted-but-RESIDENT object no longer blocks re-creation - the dead end "
                  "is gone", again.get("ok") is True,
                  "still refused: %s" % (again.get("error") or "")[:240])
        else:
            print("  SKIP  V2 the object was collected before the re-create, so this run cannot")
            print("        exercise the defect. Not a pass. Re-run, or reproduce by hand the way")
            print("        docs/06 issue 28 records it.")
        if again.get("ok") is True:
            SC.confirm_call("delete_asset", {"path": path, "confirm": True})

    print("")
    print("NOT COVERED HERE: layersCreated. Reaching modify_actor_layers' implicit-creation path")
    print("needs a classic non-partitioned level current, and the open world is World Partitioned -")
    print("every add is refused before any layer work. See the spec item.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
