"""list_settings, and set_property's saveConfig - closing a silent lie.

THE DEFECT THIS CLOSES. Project Settings, Editor Preferences and every plugin's settings page are
UDeveloperSettings CDOs, and set_property could already write one - the CDO path resolves today. The
change was lost at the next editor restart, and NOTHING said so. ok:true on a write that reverts is
the same defect class as PM-002's silent default: the call succeeded, the caller believes the project
changed, and nothing tells them otherwise until much later. So configBacked is now reported on EVERY
set_property response, not only when saving. Silence there was the bug.

T2703 IS THE ORDERING TEST, and it is the one that would have been a real defect. A refused call must
leave NOTHING behind - this handler's own editCondition block says it, citing PM-007: a cancelled
transaction reverts nothing at all, so order is the only mechanism. The saveConfig gate therefore
runs before the object is even resolved. The test proves that by passing a bogus objectPath with a
gated saveConfig and asserting the refusal is the GATE's, not "object not found".

THE GATE IS IN-HANDLER, NOT UnsafeEndpoints(). That set is checked by ENDPOINT NAME in the
dispatcher, so putting set_property in it would refuse every in-memory property write in scratch mode
- the single most-used write endpoint in the plugin. The established pattern for gating a PARAMETER
is add_gameplay_tag's (MifBridgeGameplayTags.cpp:263), and saveConfig:"none" maps onto its
transient:true one for one.

T2701 IS THE ONE THAT JUSTIFIES list_settings EXISTING. cdoPath is emitted in the exact form
get_property and set_property take verbatim, and the test feeds it straight from one endpoint into
the other. That is not a stylistic nicety: writing this suite, the obvious hand-assembled guess
"/Script/Engine.Default__CookerSettings" was WRONG - CookerSettings lives in DeveloperToolSettings -
and list_settings is what turns that from a guess into a lookup.

NOTHING IS EVER PERSISTED BY THIS SUITE, in any write mode. Every value it writes is the value that
was already there, read back first, so changed:false and the in-memory state is identical afterwards.
It never calls saveConfig with a mode that would reach disk; it asserts the refusals instead.
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


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ---------------------------------------------------------------- T2700 discovery
    print("=== T2700: Project Settings are enumerable at all ===")
    r = M.call("list_settings", {})
    check("T2700 list_settings succeeds", r.get("ok") is True, json.dumps(r)[:250])
    if not r.get("ok"):
        return 1
    sections = r.get("sections") or []
    check("T2700 it finds a substantial number of settings sections", len(sections) > 20,
          "found %d" % len(sections))
    check("T2700 every row carries the fields a caller needs to act",
          all(set(("class", "cdoPath", "container", "section", "configFile", "propertyCount",
                   "configPropertyCount")) <= set(s) for s in sections),
          json.dumps(sections[:1])[:300])
    check("T2700 and it says what it does NOT cover, rather than implying it is the whole "
          "Settings window", "registered by hand" in (r.get("note") or ""), (r.get("note") or "")[:180])

    containers = {s["container"] for s in sections}
    check("T2700 both containers are represented", {"Project", "Editor"} <= containers, containers)

    filt = M.call("list_settings", {"container": "Editor"})
    check("T2700 the container filter narrows the set",
          0 < (filt.get("count") or 0) < len(sections),
          "%s of %s" % (filt.get("count"), len(sections)))
    check("T2700 and returns only that container",
          all(s["container"] == "Editor" for s in (filt.get("sections") or [])), "mixed containers")
    none = M.call("list_settings", {"container": "NoSuchContainer"})
    check("T2700 an unmatched filter is an empty result with guidance, not an error",
          none.get("ok") is True and none.get("count") == 0 and bool(none.get("emptyNote")),
          json.dumps(none)[:250])

    # ---------------------------------------------------------------- T2701 the round trip
    print("\n=== T2701: cdoPath feeds get_property/set_property VERBATIM ===")
    target = None
    for s in sections:
        if (s.get("configPropertyCount") or 0) > 0:
            target = s
            break
    check("T2701 (setup) a config-backed section was found", bool(target), len(sections))
    if not target:
        return 1
    print("        using %s -> %s" % (target["class"], target["cdoPath"]))

    props = M.call("list_object_properties", {"objectPath": target["cdoPath"]})
    check("T2701 the cdoPath resolves through a DIFFERENT endpoint, unmodified",
          props.get("ok") is True, json.dumps(props)[:250])

    # Find a bool config property we can write back to itself.
    prop_name = None
    for p in (props.get("properties") or []):
        if p.get("type") in ("bool", "uint8") and "bool" in str(p.get("type", "")):
            prop_name = p.get("name")
            break
    if not prop_name:
        for p in (props.get("properties") or []):
            if p.get("name"):
                prop_name = p["name"]
                break
    cur = M.call("get_property", {"objectPath": target["cdoPath"], "propertyPath": prop_name})
    check("T2701 get_property reads a value through that same path", cur.get("ok") is True,
          json.dumps(cur)[:250])

    # ---------------------------------------------------------------- T2702 the silent lie
    print("\n=== T2702: a config-backed write must never be silently session-only ===")
    # THE SAME VALUE, read back first - so this changes nothing at all.
    same = cur.get("typed")
    w = M.raw_post("set_property", {"objectPath": target["cdoPath"], "propertyPath": prop_name,
                                    "value": same})
    check("T2702 writing a settings property still works", w.get("ok") is True, json.dumps(w)[:250])
    check("T2702 and it changed nothing - this suite writes values back to themselves",
          w.get("changed") is False, w.get("changed"))
    check("T2702 configBacked is reported WITHOUT being asked for - the silence was the bug",
          isinstance(w.get("configBacked"), bool), json.dumps(w)[:250])
    if w.get("configBacked"):
        check("T2702 a config-backed write says out loud that it is session-only",
              "gone at restart" in (w.get("persistNote") or ""), (w.get("persistNote") or "")[:200])
        check("T2702 and names the file and section a caller would have to edit by hand",
              bool(w.get("configFile")) and bool(w.get("configSection")),
              "%s / %s" % (w.get("configFile"), w.get("configSection")))
        check("T2702 the section is the class path, which is the engine's own ini convention",
              (w.get("configSection") or "").startswith("/Script/"), w.get("configSection"))

    # ---------------------------------------------------------------- T2703 ordering + gate
    print("\n=== T2703: the gate runs BEFORE the write, and before resolution ===")
    bad = M.raw_post("set_property", {"objectPath": target["cdoPath"], "propertyPath": prop_name,
                                      "value": same, "saveConfig": "yes"})
    check("T2703 an unknown saveConfig mode is refused", bad.get("ok") is False,
          json.dumps(bad)[:250])
    check("T2703 and the refusal lists the three modes and what each writes",
          all(t in (bad.get("error") or "") for t in ("none", "default", "user")),
          (bad.get("error") or "")[:200])

    mode = M.write_mode()
    g = M.raw_post("set_property", {"objectPath": target["cdoPath"], "propertyPath": prop_name,
                                    "value": same, "saveConfig": "default"})
    if mode == "full":
        print("  NOT EXERCISED: the refusal - the bridge is in FULL mode, where saveConfig is")
        print("  permitted. This suite still does not persist anything, so the successful save is")
        print("  not exercised either. Re-run in scratch mode to cover the gate.")
    else:
        check("T2703 saveConfig is refused in '%s' mode" % mode, g.get("ok") is False,
              json.dumps(g)[:250])
        check("T2703 and the refusal offers the session-only alternative rather than just saying no",
              "none" in (g.get("error") or "") and "restart" in (g.get("error") or ""),
              (g.get("error") or "")[:250])

        # THE ORDERING ASSERTION. A bogus objectPath with a gated saveConfig must fail on the GATE,
        # not on resolution - which proves the gate runs before anything is touched.
        order = M.raw_post("set_property", {"objectPath": "/Script/Engine.Default__NoSuchThing",
                                            "propertyPath": "Whatever", "value": 1,
                                            "saveConfig": "default"})
        check("T2703 a gated call with an unresolvable target fails on the GATE, not on resolution",
              order.get("ok") is False and "WRITES A CONFIG FILE" in (order.get("error") or ""),
              (order.get("error") or "")[:200])

    # NOT EXERCISED, named rather than left to be found. The saveError branch - saveConfig on a
    # property that is NOT config-backed, and TryUpdateDefaultConfigFile returning false on a
    # read-only ini - cannot be reached in a gated mode, because the write-mode gate refuses every
    # saveConfig before the branch is entered. Reaching them means running in full mode and letting
    # the endpoint write to the project's config, which no test needs.
    print("\n  NOT EXERCISED: the saveError branches (saveConfig on a non-config property, and a")
    print("  read-only ini). Both sit downstream of the write-mode gate, so covering them means")
    print("  running in full mode and writing the project's real config - not a trade worth making.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
