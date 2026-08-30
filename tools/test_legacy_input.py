"""Legacy (pre-Enhanced) input: list/map/unmap_legacy_input, and the gated save.

THE ONE INPUT SYSTEM WITH NO COVERAGE AT ALL, read or write. Enhanced Input got a read half with
list_input_mappings and a write half with map_input_key; legacy UInputSettings input had neither,
and it is still what a large amount of existing UE content and every UE4-era tutorial uses. A project
mid-migration has both systems live at once and an agent could previously see only one of them.

THESE ARE SEPARATE ENDPOINTS, not the settings:true branch on map_input_key that the spec proposed.
The two systems only look alike from a distance: legacy has no context, its `name` is a bare FName
rather than an InputAction asset, and it adds bShift/bCtrl/bAlt/bCmd for actions and `scale` for
axes. A settings:true flag would make `context` meaningless, change what `action` even is, and switch
four more parameters on - half a signature going dead depending on a boolean is exactly the shape
audit_mode_params.py exists to find, and building one deliberately to save an endpoint name would be
the wrong trade.

T2603 IS THE ONE THAT MATTERS MOST, and it asserts a REFUSAL rather than a success.
UInputSettings::SaveKeyMappings writes Config/DefaultInput.ini - a real file in the user's project,
not an in-memory edit that reverts on restart. That is why it is its own endpoint, save_input_settings,
and why it is on the safety gate's unsafe list: RefuseIfGated classifies per ENDPOINT NAME, so the
same write hidden behind a save:true parameter on map_legacy_input could not have been gated at all.
This suite never persists anything; it proves the gate stops it.

THIS SUITE WRITES PROJECT-WIDE SETTINGS, said plainly because it is not scratch. UInputSettings is a
CDO, not an asset, so there is no /Game/_Mif equivalent and scratch_confirm can say nothing about it.
The safety argument is the one test_anim_notify and test_simplified_collision_guard already make:
every edit is in memory, nothing is persisted (T2603 proves it cannot be), the suite removes exactly
what it added, and an editor restart discards the rest. It also verifies the project had NO legacy
mappings before it started and has none after - if that precondition ever stops holding, the suite
says so instead of quietly editing someone's real bindings.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def state(name=None):
    return M.call("list_legacy_input_mappings", {"name": name} if name else {})


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ---------------------------------------------------------------- T2600 the read half
    print("=== T2600: legacy input is readable at all ===")
    start = state()
    check("T2600 list_legacy_input_mappings succeeds", start.get("ok") is True,
          json.dumps(start)[:250])
    if not start.get("ok"):
        return 1
    check("T2600 it reports both families separately - they are different structs",
          isinstance(start.get("actionMappings"), list)
          and isinstance(start.get("axisMappings"), list), json.dumps(start)[:200])

    # THE PRECONDITION. This suite edits project-wide settings, so it only proceeds if there is
    # nothing of anyone else's to disturb.
    baseline_a = start.get("actionCount")
    baseline_x = start.get("axisCount")
    if baseline_a or baseline_x:
        print("\n  SKIPPED - this project HAS legacy input mappings (%s action, %s axis)."
              % (baseline_a, baseline_x))
        print("  This suite edits project-wide UInputSettings, not a scratch asset, so it refuses")
        print("  to run where it could disturb real bindings. That is the precondition failing")
        print("  safely, not a defect.")
        return 0
    check("T2600 and says so honestly when a project defines none",
          "no legacy input mappings" in (start.get("note") or ""), start.get("note"))

    try:
        # ------------------------------------------------------------ T2601 action mappings
        print("\n=== T2601: action mappings, and modifiers as part of the identity ===")
        r = M.raw_post("map_legacy_input", {"name": "MifTestJump", "key": "SpaceBar"})
        check("T2601 an action mapping can be created", r.get("ok") is True, json.dumps(r)[:250])
        check("T2601 and the settings list it on read-back", state().get("actionCount") == 1,
              state().get("actionCount"))

        # Ctrl+SpaceBar is a DIFFERENT binding from SpaceBar. If modifiers were not part of the
        # identity this would collapse into the first one.
        r2 = M.raw_post("map_legacy_input", {"name": "MifTestJump", "key": "SpaceBar", "ctrl": True})
        check("T2601 Ctrl+Key is a SEPARATE binding, not a duplicate", r2.get("ok") is True
              and state().get("actionCount") == 2, state().get("actionCount"))
        rows = state("MifTestJump").get("actionMappings") or []
        check("T2601 and the modifiers come back on every row, set or not",
              len(rows) == 2 and all(set(("shift", "ctrl", "alt", "cmd")) <= set(m) for m in rows),
              json.dumps(rows)[:250])

        dup = M.raw_post("map_legacy_input", {"name": "MifTestJump", "key": "SpaceBar"})
        check("T2601 an exact duplicate reports mapped:false rather than erroring or doubling",
              dup.get("ok") is True and dup.get("mapped") is False
              and state().get("actionCount") == 2, json.dumps(dup)[:200])

        # ------------------------------------------------------------ T2602 axis mappings
        print("\n=== T2602: axis mappings, and the parameters that do not cross over ===")
        a = M.raw_post("map_legacy_input", {"name": "MifTestFwd", "key": "W", "axis": True,
                                            "scale": 1.0})
        check("T2602 an axis mapping can be created", a.get("ok") is True, json.dumps(a)[:250])
        check("T2602 and lands in axisMappings, not actionMappings",
              state().get("axisCount") == 1 and state().get("actionCount") == 2,
              "action=%s axis=%s" % (state().get("actionCount"), state().get("axisCount")))

        # REFUSED, not ignored. FInputAxisKeyMapping has no modifier fields, so accepting shift:true
        # would drop it silently and report success - a binding that is not what was asked for.
        bad = M.raw_post("map_legacy_input", {"name": "MifTestFwd", "key": "S", "axis": True,
                                              "shift": True})
        check("T2602 a modifier on an AXIS mapping is refused, not silently dropped",
              bad.get("ok") is False, json.dumps(bad)[:250])
        check("T2602 and the refusal says why - the struct has no such field",
              "no such field" in (bad.get("error") or ""), (bad.get("error") or "")[:200])
        bad2 = M.raw_post("map_legacy_input", {"name": "MifTestX", "key": "E", "scale": 2.0})
        check("T2602 and scale on an ACTION mapping is refused the same way",
              bad2.get("ok") is False, json.dumps(bad2)[:200])
        check("T2602 neither refused call changed anything",
              state().get("actionCount") == 2 and state().get("axisCount") == 1,
              "action=%s axis=%s" % (state().get("actionCount"), state().get("axisCount")))

        badkey = M.raw_post("map_legacy_input", {"name": "MifTestJump", "key": "Space"})
        check("T2602 an unknown key is refused here too, with near matches",
              badkey.get("ok") is False and "SpaceBar" in (badkey.get("error") or ""),
              (badkey.get("error") or "")[:180])

        # ------------------------------------------------------------ T2603 the disk write
        print("\n=== T2603: the ONLY endpoint here that reaches disk must be gated ===")
        gated = M.gated_in_this_mode("save_input_settings",
                                     "writing Config/DefaultInput.ini")
        if gated:
            refused = M.raw_post("save_input_settings", {"confirm": True})
            check("T2603 save_input_settings is refused by the safety gate", refused.get("ok") is False,
                  json.dumps(refused)[:250])
            check("T2603 and the refusal is the GATE's, naming the mode - not a parameter check",
                  "safety gate" in (refused.get("error") or ""), (refused.get("error") or "")[:220])
            check("T2603 nothing was written and the mappings are still only in memory",
                  state().get("actionCount") == 2, state().get("actionCount"))
        else:
            # In full mode the gate permits it, so the confirm guard is what stands between a
            # caller and a real file. Assert THAT, and still never write.
            noconf = M.raw_post("save_input_settings", {})
            check("T2603 (full mode) save_input_settings without confirm is refused",
                  noconf.get("ok") is False, json.dumps(noconf)[:250])
            check("T2603 (full mode) and the refusal names the file it would write",
                  "DefaultInput.ini" in (noconf.get("error") or ""), (noconf.get("error") or "")[:200])
            print("  NOT EXERCISED: the successful save. This suite never writes "
                  "Config/DefaultInput.ini")
            print("  in any mode - it is a real file in the user's project, and no test needs it "
                  "written.")

        # ------------------------------------------------------------ T2604 removal
        print("\n=== T2604: removal matches on modifiers too, and the count is measured ===")
        u = M.raw_post("unmap_legacy_input", {"name": "MifTestJump", "key": "SpaceBar"})
        check("T2604 removing the unmodified binding takes exactly one",
              u.get("ok") is True and u.get("removed") == 1, json.dumps(u)[:200])
        # THE trap. Ctrl+SpaceBar is still there; asking again without ctrl:true matches nothing,
        # and the endpoint must say so rather than implying it removed something.
        again = M.raw_post("unmap_legacy_input", {"name": "MifTestJump", "key": "SpaceBar"})
        check("T2604 asking again matches nothing - the Ctrl binding is a different mapping",
              again.get("ok") is True and again.get("removed") == 0, json.dumps(again)[:200])
        check("T2604 and the note explains that modifiers are part of the match",
              "modifier" in (again.get("note") or ""), (again.get("note") or "")[:200])
        c = M.raw_post("unmap_legacy_input", {"name": "MifTestJump", "key": "SpaceBar", "ctrl": True})
        check("T2604 with ctrl:true it matches and removes", c.get("removed") == 1,
              json.dumps(c)[:200])
        x = M.raw_post("unmap_legacy_input", {"name": "MifTestFwd", "key": "W", "axis": True})
        check("T2604 axis removal works and reports its own count", x.get("removed") == 1,
              json.dumps(x)[:200])
    finally:
        # RESTORE. These are project-wide settings, so leaving anything behind would be a change
        # nobody asked for that outlives the suite.
        for payload in ({"name": "MifTestJump", "key": "SpaceBar"},
                        {"name": "MifTestJump", "key": "SpaceBar", "ctrl": True},
                        {"name": "MifTestFwd", "key": "W", "axis": True},
                        {"name": "MifTestX", "key": "E"}):
            M.raw_post("unmap_legacy_input", payload)
        end = state()
        check("T2605 (cleanup) the project has no legacy mappings again, as it started",
              end.get("actionCount") == 0 and end.get("axisCount") == 0,
              "action=%s axis=%s" % (end.get("actionCount"), end.get("axisCount")))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
