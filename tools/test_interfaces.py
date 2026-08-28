"""Blueprint interfaces - add, list, implement, remove.

Named in no suite until now (tools/coverage_gaps.py). Hunted the same way as the enum family, and
unlike that one it came back clean - which is worth recording as a result rather than quietly not
mentioning. Two of the families hunted tonight had real bugs; this one does not, and the suite exists
so that stays true.

T312 is the part worth having. `add_interface` does more than record a name: UE's ImplementNewInterface
CONFORMS the blueprint, which creates a function graph for every non-event function the interface
declares. So the meaningful question is not "did the interface get added" but "can the blueprint
actually answer the interface's functions afterwards" - and that is what the test asks, by finding the
graph and compiling.

It also pins down a behaviour that reads like a bug and is not: `implement_interface_function` on a
freshly added interface answers "function graph already exists", because adding the interface created
it. Refusing there is correct; the alternative is a duplicate graph.
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


def graph_names(bid):
    return [g.get("name") for g in (M.call("list_graphs", {"blueprintId": bid}).get("graphs") or [])]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ---- fixtures: an interface with a real (non-event) function ------------------------------
    ipath = "/Game/_MifIface/BPI_%d" % st
    iface = M.call("create_blueprint", {"path": ipath, "parentClass": "Interface",
                                        "blueprintType": "Interface"})
    check("setup: an interface blueprint is created", iface.get("ok") is True, json.dumps(iface)[:180])
    iid = iface.get("blueprintId")
    if not iid:
        return 3
    # An OUTPUT makes it a function rather than an event, which is the branch
    # implement_interface_function handles; an event-style one is routed to add_override_event.
    fn = M.call("create_function", {"blueprintId": iid, "name": "GetItemPrice",
                                    "outputs": [{"name": "Price", "type": "float"}]})
    check("setup: the interface declares a function", fn.get("ok") is True, json.dumps(fn)[:170])
    M.call("compile", {"blueprintId": iid})

    bpath = "/Game/_MifIface/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    if not bid:
        return 3
    iclass = "%s.BPI_%d_C" % (ipath, st)

    # ------------------------------------------------------------------ T310 add and list
    print("\n=== T310: adding an interface ===")
    before = M.call("list_interfaces", {"blueprintId": bid})
    check("T310 a fresh blueprint implements nothing", before.get("count") == 0, json.dumps(before)[:150])
    a = M.call("add_interface", {"blueprintId": bid, "interface": iclass})
    check("T310 the interface is added", a.get("ok") is True, json.dumps(a)[:180])
    l = M.call("list_interfaces", {"blueprintId": bid})
    # Verified through the LISTING rather than from add's own answer.
    check("T310 and it shows up in the listing", l.get("count") == 1, json.dumps(l)[:200])
    check("T310 with its class path", any(iclass in (x.get("path") or "")
                                          for x in (l.get("interfaces") or [])),
          json.dumps(l.get("interfaces"))[:200])

    dup = M.call("add_interface", {"blueprintId": bid, "interface": iclass})
    check("T310 adding it twice is refused", dup.get("ok") is False, json.dumps(dup)[:170])
    check("T310 and does not duplicate the entry",
          M.call("list_interfaces", {"blueprintId": bid}).get("count") == 1,
          "the count changed on a refused add")

    # ------------------------------------------------------------------ T312 the real question
    print("\n=== T312 [the point]: can the blueprint actually ANSWER the interface? ===")
    # add_interface conforms the blueprint, which creates a graph per non-event function. Asking only
    # "was it added" would miss a conform that silently did nothing.
    names = graph_names(bid)
    check("T312 adding the interface created its function graph", "GetItemPrice" in names, str(names))
    c = M.call("compile", {"blueprintId": bid})
    check("T312 and the blueprint compiles with it",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:170]))
    # Reads like a bug, is not: the graph exists because adding the interface made it, and creating a
    # second one would be the actual defect.
    impl = M.call("implement_interface_function", {"blueprintId": bid, "function": "GetItemPrice"})
    check("T312 implement_interface_function refuses a graph that already exists",
          impl.get("ok") is False and "already exists" in (impl.get("error") or ""),
          (impl.get("error") or "")[:170])
    check("T312 and did not create a duplicate graph",
          graph_names(bid).count("GetItemPrice") == 1, str(graph_names(bid)))

    # ------------------------------------------------------------------ T313 guards
    print("\n=== T313: guards ===")
    notiface = M.call("add_interface", {"blueprintId": bid, "interface": "/Script/Engine.Actor"})
    check("T313 a non-interface class is refused", notiface.get("ok") is False, json.dumps(notiface)[:150])
    check("T313 and says so by name",
          "not an interface class" in (notiface.get("error") or ""), (notiface.get("error") or "")[:170])
    missing = M.call("add_interface", {"blueprintId": bid, "interface": "/Game/NoSuchIface_zz"})
    check("T313 an unknown class is refused", missing.get("ok") is False, json.dumps(missing)[:150])
    # The error should show the shape it wants, since the _C suffix is the usual mistake.
    check("T313 and shows the expected path shape",
          "_C" in (missing.get("error") or ""), (missing.get("error") or "")[:170])
    q = M.call("implement_interface_function", {"blueprintId": bid, "function": "NoSuchFn_zz"})
    check("T313 implementing an unknown function is refused",
          q.get("ok") is False and "no overridable function" in (q.get("error") or ""),
          (q.get("error") or "")[:170])

    # ------------------------------------------------------------------ T314 removal is gated
    print("\n=== T314: removal is confirm-gated ===")
    r = M.call("remove_interface", {"blueprintId": bid, "interface": iclass})
    check("T314 remove refuses without confirm", r.get("ok") is False, json.dumps(r)[:150])
    check("T314 and says confirm is what is missing",
          "confirm" in (r.get("error") or ""), (r.get("error") or "")[:150])
    check("T314 the interface survives a refused removal",
          M.call("list_interfaces", {"blueprintId": bid}).get("count") == 1,
          "the interface disappeared on a refused call")
    notimpl = M.call("remove_interface", {"blueprintId": bid, "interface": "/Script/Engine.Actor"})
    check("T314 removing something that is not an interface is refused",
          notimpl.get("ok") is False, (notimpl.get("error") or "")[:150])

    SC.confirm_call("delete_asset", {"path": bpath})
    SC.confirm_call("delete_asset", {"path": ipath})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("COVERAGE GAP, deliberate: remove_interface's SUCCESS path is not exercised, because it")
    print("requires confirm=true and the audit harness strips confirm.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
