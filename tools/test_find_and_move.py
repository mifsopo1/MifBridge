"""find_nodes and move_node - graph search and layout, neither in a suite.

find_nodes is a DISCOVERY endpoint, which makes a wrong answer from it especially expensive: an agent
that searches and finds nothing concludes the node is not there and goes and builds a second one. A
search that silently under-reports does not look like a failure, it looks like an empty graph.

THE TRAP THIS SUITE EXISTS TO DOCUMENT. `byClass` matches the node's C++ CLASS, `byTitle` matches what
you SEE. Those differ for the most common node in Blueprints: a Branch node's title is "Branch" and its
class is `K2Node_IfThenElse`. So byClass:"Branch" correctly returns nothing, and a caller who read the
graph and typed what they saw gets zero results for a node sitting in front of them. The endpoint's
own parameter documentation says which is which; this pins it down so the behaviour cannot drift into
matching titles by accident, and so the distinction is written somewhere a person will look.

find_nodes searches ONE graph, deliberately - it takes graphId, not blueprintId. That is also asserted,
because "searches everything" is the natural assumption and it is wrong.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    bid = M.call("create_blueprint", {"path": "/Game/_MifFind/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    eg = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    check("its event graph resolves", bool(eg), str([x.get("name") for x in graphs]))
    if not eg:
        return 1

    for i in range(2):
        M.call("add_function_call", {"graphId": eg, "function": "PrintString",
                                     "class": "KismetSystemLibrary", "x": 300 * i, "y": 300})
    br = M.call("add_branch", {"graphId": eg, "x": 100, "y": 200})
    bguid = br.get("nodeGuid")
    check("the search subjects exist", bool(bguid), json.dumps(br)[:170])
    M.call("compile", {"blueprintId": bid})

    def node_of(guid):
        return M.call("get_node", {"graphId": eg, "nodeGuid": guid}).get("node") or {}

    # ------------------------------------------------------------------ T460 the three search modes
    print("")
    print("=== T460: each search mode finds what it says it finds ===")
    r = M.call("find_nodes", {"graphId": eg, "byFunction": "PrintString"})
    check("T460 byFunction finds both calls", r.get("count") == 2, json.dumps(r)[:200])
    check("T460 and the count matches the array it returned",
          r.get("count") == len(r.get("nodes") or []),
          "count=%s but returned %d nodes - a count that disagrees with its own payload is worse "
          "than no count" % (r.get("count"), len(r.get("nodes") or [])))

    r = M.call("find_nodes", {"graphId": eg, "byTitle": "Print"})
    check("T460 byTitle finds them too", (r.get("count") or 0) >= 2, json.dumps(r)[:200])

    r = M.call("find_nodes", {"graphId": eg, "byFunction": "NoSuchFunction_zz"})
    check("T460 a search with no matches succeeds and returns zero", r.get("ok") is True
          and r.get("count") == 0, json.dumps(r)[:200])
    check("T460 rather than failing", r.get("ok") is True,
          "an empty result is an answer, not an error")

    # ------------------------------------------------------------------ T461 class vs title
    print("")
    print("=== T461 [the trap]: byClass is the C++ class, byTitle is what you see ===")
    n = node_of(bguid)
    check("T461 the Branch node's title really is 'Branch'", n.get("title") == "Branch",
          json.dumps(n)[:170])
    check("T461 while its class is K2Node_IfThenElse", "IfThenElse" in (n.get("class") or ""),
          json.dumps(n)[:170])
    hit = M.call("find_nodes", {"graphId": eg, "byClass": "IfThenElse"})
    check("T461 byClass finds it by CLASS", hit.get("count") == 1, json.dumps(hit)[:190])
    miss = M.call("find_nodes", {"graphId": eg, "byClass": "Branch"})
    # This is the assertion that documents the trap. It is asserted as CORRECT behaviour: byClass is
    # documented as the class name, and quietly matching titles too would make the two modes
    # indistinguishable.
    check("T461 and byClass does NOT match the title", miss.get("count") == 0,
          "byClass:'Branch' returned %s - byClass and byTitle are meant to be different questions"
          % miss.get("count"))
    bytitle = M.call("find_nodes", {"graphId": eg, "byTitle": "Branch"})
    check("T461 while byTitle does find it", (bytitle.get("count") or 0) >= 1, json.dumps(bytitle)[:190])

    # ------------------------------------------------------------------ T462 one graph only
    print("")
    print("=== T462: it searches ONE graph, and says so if you assume otherwise ===")
    M.call("create_function", {"blueprintId": bid, "name": "Helper_%d" % st})
    fg = next((x.get("graphId") for x in (M.call("list_graphs", {"blueprintId": bid}).get("graphs") or [])
               if (x.get("name") or "") == "Helper_%d" % st), None)
    if fg:
        M.call("add_function_call", {"graphId": fg, "function": "PrintString",
                                     "class": "KismetSystemLibrary", "x": 0, "y": 0})
        M.call("compile", {"blueprintId": bid})
        ev = M.call("find_nodes", {"graphId": eg, "byFunction": "PrintString"}).get("count")
        fn = M.call("find_nodes", {"graphId": fg, "byFunction": "PrintString"}).get("count")
        check("T462 the event graph search sees only its own nodes", ev == 2, "count=%s" % ev)
        check("T462 and the function graph search sees only its own", fn == 1, "count=%s" % fn)
    q = M.call("find_nodes", {"blueprintId": bid, "byFunction": "PrintString"})
    check("T462 passing blueprintId is refused with a pointer to graphId",
          q.get("ok") is False and "graphId" in (q.get("error") or ""), (q.get("error") or "")[:190])

    # ------------------------------------------------------------------ T463 move_node
    print("")
    print("=== T463: moving a node actually moves it ===")
    before = (node_of(bguid).get("x"), node_of(bguid).get("y"))
    m = M.call("move_node", {"graphId": eg, "node": bguid, "x": 999, "y": 777})
    check("T463 the move succeeds", m.get("ok") is True, json.dumps(m)[:200])
    after = (node_of(bguid).get("x"), node_of(bguid).get("y"))
    # Read from the GRAPH, not from the move's own echo of the coordinates it was handed.
    check("T463 and the graph agrees the node is there now", after == (999, 777),
          "%s -> %s" % (before, after))
    check("T463 the position really changed", before != after, "%s -> %s" % (before, after))

    q = M.call("move_node", {"graphId": eg, "node": "00000000-0000-0000-0000-000000000000",
                             "x": 0, "y": 0})
    check("T463 moving a node that does not exist is refused", q.get("ok") is False,
          json.dumps(q)[:190])

    c = M.call("compile", {"blueprintId": bid})
    check("T463 the blueprint still compiles",
          c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    M.call("delete_asset", {"path": "/Game/_MifFind/BP_%d" % st})
    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
