"""Does UNDO actually put back what an endpoint changed?

Nothing tested this, and it is the thing a person leans on hardest. A modder drives a few endpoints,
does not like the result, and presses Ctrl+Z. If the editor's undo does not restore what the bridge
changed, the damage is silent and permanent, and it is discovered much later against an asset nobody
remembers editing.

There is already a reason to doubt it. PM-007: FTransaction::Cancel discards the undo ENTRY without
applying anything, so atomicity cannot be assumed from the transaction system just because a
transaction was opened. That is a statement about rollback-on-failure, but it comes from the same
place - what the transaction system does versus what it looks like it does - and the success path had
never been checked at all.

HOW EACH CASE WORKS, and why the middle step matters most:

    fingerprint  ->  call the endpoint  ->  fingerprint  ->  undo  ->  fingerprint

  1. the AFTER fingerprint must DIFFER from BEFORE, or the case proves nothing. A test that mutates
     nothing and then confirms nothing changed passes against every possible bug. That exact failure
     already happened once here - test_array_wildcard_durability read the wrong key, saw an empty
     dict, found no wildcards and asserted happily over all of it - so every case below refuses to
     report a result unless it first proves it changed something.
  2. the UNDONE fingerprint must equal BEFORE. Not "roughly": the same serialized state.

A failure here is not necessarily a bug in the endpoint - some engine operations genuinely are not
transacted - but an endpoint that mutates a Blueprint and cannot be undone needs to SAY so, and none
of them currently do.

SAFETY. Everything happens on a scratch Blueprint under /Game/_MifUndo, nothing is saved, and undo is
bounded to one step per case so a failure cannot walk backwards through unrelated history.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []
RESULTS = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def fingerprint(bid, graph):
    """A deterministic picture of the parts of a Blueprint these endpoints touch.

    Sorted and serialized so two fingerprints compare exactly. Node GUIDs are deliberately INCLUDED:
    an undo that leaves a node behind under a new guid has not undone anything.
    """
    def norm(x):
        return json.dumps(x, sort_keys=True, default=str)

    vs = M.call("list_variables", {"blueprintId": bid}).get("variables") or []
    cs = M.call("list_components", {"blueprintId": bid}).get("components") or []
    gs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    ns = M.call("list_nodes", {"graphId": graph}).get("nodes") or [] if graph else []

    # PINS TOO - connections and pin defaults. Without these the fingerprint cannot see a wiring
    # change at all, and the first run of T372 proved it: connect_pins and a four-operation
    # apply_graph_patch both came back "changed nothing observable". They had changed plenty; this
    # function was not looking. The non-vacuity guard caught it rather than handing out a free pass,
    # which is the whole reason that guard is there.
    pinstate = []
    for n in ns:
        guid = n.get("guid") or n.get("nodeGuid")
        if not guid:
            continue
        gn = M.call("get_node", {"graphId": graph, "nodeGuid": guid})
        nd = gn.get("node") or gn
        for pin in (nd.get("pins") or []):
            pinstate.append(norm({"node": guid, "pin": pin.get("name"),
                                  "default": pin.get("default"),
                                  "linkedTo": sorted(norm(x) for x in (pin.get("linkedTo") or []))}))
    ifs = M.call("list_interfaces", {"blueprintId": bid}).get("interfaces") or []
    return norm({
        "vars": sorted(norm(v) for v in vs),
        "comps": sorted(norm(c) for c in cs),
        "graphs": sorted((g.get("name") or "") for g in gs),
        "nodes": sorted(norm({k: n.get(k) for k in ("title", "guid", "nodeClass")}) for n in ns),
        "ifaces": sorted(norm(i) for i in ifs),
        "pins": sorted(pinstate),
    })


def one_case(label, bid, graph, mutate):
    """fingerprint -> mutate -> fingerprint -> undo -> fingerprint. Returns a result dict."""
    before = fingerprint(bid, graph)
    r = mutate()
    if not isinstance(r, dict) or r.get("ok") is not True:
        RESULTS.append((label, "setup-failed", json.dumps(r)[:110] if isinstance(r, dict) else str(r)))
        check("%s the mutation itself succeeded" % label, False,
              json.dumps(r)[:180] if isinstance(r, dict) else str(r))
        return None
    after = fingerprint(bid, graph)

    # THE NON-VACUITY GUARD. Without this the undo assertion below is free.
    if after == before:
        RESULTS.append((label, "no-op", "the endpoint reported ok but changed nothing observable"))
        check("%s actually changed something (else the undo check proves nothing)" % label, False,
              "the endpoint returned ok:true and the fingerprint is identical - either it did nothing, "
              "or it changed something this fingerprint does not look at")
        return None
    check("%s changed something, so the undo check is meaningful" % label, True)

    u = M.call("undo_transactions", {"count": 1})
    undone = fingerprint(bid, graph)
    restored = (undone == before)
    RESULTS.append((label, "restored" if restored else "NOT RESTORED",
                    "" if restored else "undo reported %s" % json.dumps(u)[:90]))
    check("%s undo puts the blueprint back exactly" % label, restored,
          "state after undo differs from before the call; undo_transactions said %s"
          % json.dumps(u)[:150])
    return restored


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    bp = "/Game/_MifUndo/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bp, "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned no blueprintId")
    if not bid:
        return 1
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    g = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    check("its event graph resolves", bool(g), str([x.get("name") for x in graphs]))

    # Undo has to be reachable at all before any of the cases below mean anything.
    lt = M.call("list_transactions", {})
    check("the transaction buffer is readable", lt.get("ok") is True, json.dumps(lt)[:170])

    # add_interface needs a REAL interface asset - there is no class literally called
    # "BlueprintInterface", which is how the first run of this suite failed. Built here rather than
    # borrowed so this suite does not depend on another one's leftovers.
    ipath = "/Game/_MifUndo/BPI_%d" % st
    iid = M.call("create_blueprint", {"path": ipath, "parentClass": "Interface",
                                      "blueprintType": "Interface"}).get("blueprintId")
    if iid:
        M.call("create_function", {"blueprintId": iid, "name": "GetUndoProbe",
                                   "outputs": [{"name": "Value", "type": "float"}]})
        M.call("compile", {"blueprintId": iid})
    iclass = "%s.BPI_%d_C" % (ipath, st)
    check("an interface asset exists for the add_interface case", bool(iid), "create_blueprint failed")

    print("")
    print("=== T370: the everyday Blueprint edits ===")
    cases = [
        ("T370 add_variable",
         lambda: M.call("add_variable", {"blueprintId": bid, "name": "U_%d" % st, "type": "float"})),
        ("T370 add_component",
         lambda: M.call("add_component", {"blueprintId": bid, "componentClass": "SceneComponent",
                                          "name": "UndoComp_%d" % st})),
        ("T370 add_branch",
         lambda: M.call("add_branch", {"graphId": g, "x": 1200, "y": 1200})),
        ("T370 add_comment",
         lambda: M.call("add_comment", {"graphId": g, "x": 1400, "y": 1400,
                                        "text": "undo probe %d" % st})),
        ("T370 add_interface",
         lambda: M.call("add_interface", {"blueprintId": bid, "interface": iclass})),
    ]
    for label, fn in cases:
        one_case(label, bid, g, fn)

    print("")
    print("=== T371: edits that change a variable rather than adding one ===")
    M.call("add_variable", {"blueprintId": bid, "name": "Flags_%d" % st, "type": "int"})
    one_case("T371 set_variable_flags", bid, g,
             lambda: M.call("set_variable_flags", {"blueprintId": bid, "name": "Flags_%d" % st,
                                                   "instanceEditable": True, "category": "Undo"}))
    one_case("T371 set_variable_type", bid, g,
             lambda: M.call("set_variable_type", {"blueprintId": bid, "name": "Flags_%d" % st,
                                                  "type": "float"}))

    print("")
    print("=== T372: multi-edit operations, where undo is most likely to half-revert ===")
    # apply_graph_patch performs MANY dependent edits in one call. The question this asks is whether
    # ONE undo takes the whole patch back, or only its last operation - a patch that half-reverts is
    # worse than one that does not revert at all, because the graph is then in a state the caller
    # never asked for and did not see.
    nodes = []
    for i in range(3):
        r = M.call("add_function_call", {"graphId": g, "function": "PrintString",
                                         "class": "KismetSystemLibrary",
                                         "x": 400 + 300 * i, "y": 600})
        if r.get("ok"):
            nodes.append(r.get("nodeGuid"))
    check("T372 three nodes exist to patch", len(nodes) == 3, "got %d" % len(nodes))

    if len(nodes) == 3:
        one_case("T372 connect_pins", bid, g,
                 lambda: M.call("connect_pins", {"graphId": g, "srcNode": nodes[0], "srcPin": "then",
                                                 "dstNode": nodes[1], "dstPin": "execute"}))
        one_case("T372 apply_graph_patch (4 ops)", bid, g,
                 lambda: M.call("apply_graph_patch", {"graphId": g, "operations": [
                     {"op": "connect_pins", "srcNode": nodes[0], "srcPin": "then",
                      "dstNode": nodes[1], "dstPin": "execute"},
                     {"op": "connect_pins", "srcNode": nodes[1], "srcPin": "then",
                      "dstNode": nodes[2], "dstPin": "execute"},
                     {"op": "set_pin_default", "node": nodes[1], "pin": "InString", "value": "A"},
                     {"op": "set_pin_default", "node": nodes[2], "pin": "InString", "value": "B"},
                 ]}))

    print("")
    print("=" * 72)
    print("PER-ENDPOINT SUMMARY")
    for label, verdict, note in RESULTS:
        print("  %-34s %-14s %s" % (label.replace("T370 ", "").replace("T371 ", ""), verdict, note[:70]))
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
