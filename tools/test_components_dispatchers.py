"""Component transforms and event dispatchers - the last two families named in no suite.

Both came back clean, like interfaces and unlike enums. That is recorded rather than passed over:
four families were hunted tonight off tools/coverage_gaps.py, two were clean and two were not, and
knowing which is which is worth as much as the fixes when someone decides where to spend an evening.

T321 is the assertion worth keeping. set_component_transform reports locationApplied /
rotationApplied / scaleApplied SEPARATELY, and those are honest - sending only a location leaves
rotationApplied false rather than claiming a rotation was written. The test asserts both directions,
because a per-field flag that is always true would be indistinguishable from no flag at all, and the
value is then read back through a DIFFERENT endpoint (get_property on the component template) rather
than from the response that claimed to have written it.

Three of these endpoints are confirm-gated - remove_component, rename_event_dispatcher - and the audit
harness strips confirm, so only their refusals are reachable. Stated rather than hidden, as in the
other suites.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def component(bid, name):
    for c in (M.call("list_components", {"blueprintId": bid}).get("components") or []):
        if c.get("name") == name:
            return c
    return {}


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)
    bpath = "/Game/_MifComp/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    if not bid:
        return 3
    M.call("add_component", {"blueprintId": bid, "componentClass": "StaticMeshComponent", "name": "Body"})
    M.call("add_component", {"blueprintId": bid, "componentClass": "SceneComponent", "name": "Pivot"})
    M.call("compile", {"blueprintId": bid})

    # ------------------------------------------------------------------ T320 the components exist
    print("\n=== T320: components ===")
    check("T320 both components are listed",
          bool(component(bid, "Body")) and bool(component(bid, "Pivot")),
          str([c.get("name") for c in (M.call("list_components", {"blueprintId": bid}).get("components") or [])]))
    tp = component(bid, "Body").get("templatePath")
    # The template path is the handle everything else needs; a listing without it sends the caller hunting.
    check("T320 and the listing hands back a template path", bool(tp), tp)

    # ------------------------------------------------------------------ T321 per-field honesty
    print("\n=== T321 [the point]: the applied flags are per-field and honest ===")
    r = M.call("set_component_transform", {"blueprintId": bid, "name": "Body",
                                           "location": {"x": 10, "y": 20, "z": 30},
                                           "scale": {"x": 2, "y": 2, "z": 2}})
    check("T321 the transform is applied", r.get("ok") is True, json.dumps(r)[:200])
    check("T321 location reports applied", r.get("locationApplied") is True, r.get("locationApplied"))
    check("T321 scale reports applied", r.get("scaleApplied") is True, r.get("scaleApplied"))
    # BOTH directions. A flag that is always true carries no information.
    check("T321 and rotation reports NOT applied, because none was sent",
          r.get("rotationApplied") is False,
          "rotationApplied=%s - a per-field flag that is always true is the same as no flag"
          % r.get("rotationApplied"))

    # Read back through get_property on the template, not from the response that claimed to write it.
    if tp:
        loc = M.call("get_property", {"objectPath": tp, "property": "RelativeLocation"})
        scl = M.call("get_property", {"objectPath": tp, "property": "RelativeScale3D"})
        check("T321 the location really landed on the template",
              "X=10.000000" in str(loc.get("value")) and "Z=30.000000" in str(loc.get("value")),
              str(loc.get("value"))[:120])
        check("T321 and so did the scale",
              "X=2.000000" in str(scl.get("value")), str(scl.get("value"))[:120])
        rot = M.call("get_property", {"objectPath": tp, "property": "RelativeRotation"})
        check("T321 while the rotation was left alone",
              "P=0.000000" in str(rot.get("value")) or "0.000000" in str(rot.get("value")),
              str(rot.get("value"))[:120])

    print("\n=== T321b: transform guards ===")
    for label, payload, expect in (
        ("nothing to set", {"blueprintId": bid, "name": "Body"}, "at least one"),
        ("unknown component", {"blueprintId": bid, "name": "NoSuch_zz",
                               "location": {"x": 1, "y": 1, "z": 1}}, "not found"),
        ("malformed vector", {"blueprintId": bid, "name": "Body",
                              "location": {"x": 1, "y": "oops", "z": 1}}, "Nothing was changed"),
    ):
        q = M.call("set_component_transform", payload)
        check("T321b %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T321b %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])
    # The refusals must not have moved anything.
    if tp:
        loc = M.call("get_property", {"objectPath": tp, "property": "RelativeLocation"})
        check("T321b the transform survived every refusal",
              "X=10.000000" in str(loc.get("value")), str(loc.get("value"))[:120])

    print("\n=== T322: removal is confirm-gated ===")
    rm = M.call("remove_component", {"blueprintId": bid, "name": "Pivot"})
    check("T322 remove_component refuses without confirm", rm.get("ok") is False, json.dumps(rm)[:150])
    check("T322 and says confirm is what is missing", "confirm" in (rm.get("error") or ""),
          (rm.get("error") or "")[:150])
    check("T322 the component survives a refused removal", bool(component(bid, "Pivot")),
          "the component disappeared on a refused call")

    # ------------------------------------------------------------------ T323 dispatchers
    print("\n=== T323: event dispatchers ===")
    d = M.call("add_event_dispatcher", {"blueprintId": bid, "name": "OnPriceChanged"})
    check("T323 a dispatcher is added", d.get("ok") is True, json.dumps(d)[:180])
    check("T323 and it compiles clean as part of the add",
          (d.get("compile") or {}).get("numErrors") == 0, json.dumps(d.get("compile"))[:150])
    l = M.call("list_dispatchers", {"blueprintId": bid})
    # Verified through the listing rather than from add's own answer.
    check("T323 the listing sees it",
          any(x.get("name") == "OnPriceChanged" for x in (l.get("dispatchers") or [])),
          json.dumps(l)[:200])

    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    g = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    c = M.call("add_call_dispatcher", {"graphId": g, "dispatcher": "OnPriceChanged", "x": 0, "y": 0})
    check("T323 a call node can be placed for it", c.get("ok") is True, json.dumps(c)[:180])
    check("T323 and it is a CallDelegate node",
          (c.get("node") or {}).get("class") == "K2Node_CallDelegate",
          json.dumps(c.get("node"))[:170])
    comp = M.call("compile", {"blueprintId": bid})
    check("T323 the blueprint still compiles with the call node",
          comp.get("ok") is True and comp.get("numErrors", 1) == 0,
          "errors=%s %s" % (comp.get("numErrors"), json.dumps(comp.get("messages", []))[:150]))

    q = M.call("add_call_dispatcher", {"graphId": g, "dispatcher": "NoSuchDispatcher_zz",
                                       "x": 0, "y": 0})
    check("T323 an unknown dispatcher is refused", q.get("ok") is False, json.dumps(q)[:160])

    print("\n=== T324: renaming a dispatcher is confirm-gated ===")
    rn = M.call("rename_event_dispatcher", {"blueprintId": bid, "oldName": "OnPriceChanged",
                                            "newName": "OnCostChanged"})
    check("T324 rename refuses without confirm", rn.get("ok") is False, json.dumps(rn)[:150])
    check("T324 and says confirm is what is missing", "confirm" in (rn.get("error") or ""),
          (rn.get("error") or "")[:150])
    still = M.call("list_dispatchers", {"blueprintId": bid})
    check("T324 the dispatcher keeps its name after a refused rename",
          any(x.get("name") == "OnPriceChanged" for x in (still.get("dispatchers") or [])),
          json.dumps(still)[:180])

    # ------------------------------------------------------------------ T325 the success paths
    print("")
    print("=== T325: renaming and REMOVING a dispatcher, through the scratch-only confirm path ===")
    # These were the documented coverage gap in this file: both need confirm=true, which mifaudit
    # strips. scratch_confirm sends it only when every path in the payload is under /Game/_Mif, which
    # this blueprint is - so the gap is closable now rather than permanent.
    rn = SC.confirm_call("rename_event_dispatcher", {"blueprintId": bid, "oldName": "OnPriceChanged",
                                                     "newName": "OnCostChanged"})
    check("T325 the rename succeeds with confirm", rn.get("ok") is True, json.dumps(rn)[:180])
    after = [x.get("name") for x in (M.call("list_dispatchers", {"blueprintId": bid}).get("dispatchers") or [])]
    check("T325 and the dispatcher carries the new name", "OnCostChanged" in after, str(after))
    check("T325 and not the old one", "OnPriceChanged" not in after, str(after))

    # remove_event_dispatcher - the endpoint that did not exist until the add_*/remove_* families were
    # compared. Everything else in the family had a remover; this one had add, rename and list only.
    no = M.call("remove_event_dispatcher", {"blueprintId": bid, "name": "OnCostChanged"})
    check("T325 removal refuses without confirm", no.get("ok") is False, json.dumps(no)[:150])
    check("T325 and says confirm is what is missing", "confirm" in (no.get("error") or ""),
          (no.get("error") or "")[:150])
    bad = SC.confirm_call("remove_event_dispatcher", {"blueprintId": bid, "name": "NoSuch_zz"})
    check("T325 an unknown dispatcher is refused", bad.get("ok") is False, json.dumps(bad)[:170])

    rm = SC.confirm_call("remove_event_dispatcher", {"blueprintId": bid, "name": "OnCostChanged"})
    check("T325 the removal succeeds", rm.get("ok") is True, json.dumps(rm)[:200])
    # BOTH HALVES. A dispatcher is a signature graph plus a backing delegate variable, and leaving
    # either behind is worse than leaving both: the survivor still resolves by name, so the blueprint
    # looks like it has a dispatcher that no longer works.
    gone_d = [x.get("name") for x in (M.call("list_dispatchers", {"blueprintId": bid}).get("dispatchers") or [])]
    gone_v = [x.get("name") for x in (M.call("list_variables", {"blueprintId": bid}).get("variables") or [])]
    check("T325 the dispatcher is gone", "OnCostChanged" not in gone_d, str(gone_d))
    check("T325 and so is its backing delegate variable", "OnCostChanged" not in gone_v, str(gone_v))
    # THE RESPONSE'S OWN CLAIM ABOUT THE SAME TWO FACTS, asserted here for the first time. The
    # handler checks both halves and refuses the whole call if either survives, so these flags are
    # observations rather than echoes - which is exactly why they are worth pinning against the
    # independent reads above. A response claiming both halves removed while list_variables still
    # shows the delegate would be the failure this endpoint was written to make impossible.
    check("T325 and the response claims the signature graph was removed",
          rm.get("removedSignatureGraph") is True, json.dumps(rm)[:220])
    check("T325 and claims the delegate variable was removed",
          rm.get("removedDelegateVariable") is True, json.dumps(rm)[:220])
    check("T325 and neither claim disagrees with what list_* reports",
          (rm.get("removedSignatureGraph") is True) == ("OnCostChanged" not in gone_d)
          and (rm.get("removedDelegateVariable") is True) == ("OnCostChanged" not in gone_v),
          "flags=(%r, %r) actual=(graph gone %r, var gone %r)"
          % (rm.get("removedSignatureGraph"), rm.get("removedDelegateVariable"),
             "OnCostChanged" not in gone_d, "OnCostChanged" not in gone_v))
    # T323 left a call node behind, so the count has to be real rather than always zero.
    check("T325 and the orphaned call node is reported", (rm.get("orphanedNodeCount") or 0) >= 1,
          "orphanedNodeCount=%s - a caller who is not told goes looking for the compile error blind"
          % rm.get("orphanedNodeCount"))

    SC.confirm_call("delete_asset", {"path": bpath})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("The confirm-gated SUCCESS paths are exercised in T325 via tools/scratch_confirm.py, which")
    print("sends confirm only for a payload whose every path is under /Game/_Mif. The note that used")
    print("to stand here called that gap permanent; it was only permanent until something safe existed.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
