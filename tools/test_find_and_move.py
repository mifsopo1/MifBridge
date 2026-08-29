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

T464, added 2026-08-29: tools/param_reach.py (which checks whether the MCP tools in server.py can
actually SEND every parameter the C++ endpoints accept - a different question from parity_check.py's
name-level check) flagged move_node.graphid, remove_node.graphid, refresh_node.graphid and
rename_event.graphid as UNREACHABLE - the C++ accepted an optional graphId that scopes a node-guid
lookup to one graph (real, documented reason: a cooked Blueprint's editable child can carry the same
node guids as the original if both are loaded), but no MCP tool ever sent it, so an agent driving
through MCP had no way to disambiguate at all. Fixed by wiring graph_id through all four wrappers. T464
proves the scoping is genuinely ENFORCED, not just accepted-and-ignored: fg (T462's own Helper function
graph) really does not contain bguid, so passing it is refused by name rather than silently falling
through to the global lookup that would have found the node anyway.
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

    # ------------------------------------------------------------------ T464 graphId disambiguation
    # move_node/remove_node/refresh_node/rename_event all accept an OPTIONAL graphId that SCOPES the
    # node-guid lookup to one graph - documented (ResolveNodeField, MifBridgeCommon.cpp) as existing
    # for when the SAME node guid exists in more than one loaded copy of a Blueprint (a cooked original
    # plus an editable child made via create_editable_child is the real, recurring case in this
    # project). param_reach.py had flagged all four as UNREACHABLE - the C++ accepted graphId, but no
    # MCP tool in server.py ever sent it, so an agent using the MCP tools had no way to invoke the
    # disambiguation at all. Fixed 2026-08-29 by wiring graph_id through all four wrappers.
    # This does not reproduce an actual guid collision (that needs two loaded copies of one Blueprint,
    # a heavier setup than this suite's scope) - it proves the SCOPING itself is real and enforced: fg
    # (T462's Helper function graph) genuinely does not contain bguid, so passing it should refuse by
    # name rather than silently falling through to the global lookup that would have found the node
    # anyway.
    print("")
    print("=== T464: graphId genuinely SCOPES the node lookup, not just accepted-and-ignored ===")
    if fg:
        wrong_scope = M.call("move_node", {"graphId": fg, "nodeGuid": bguid, "x": 500, "y": 500})
        check("T464 move_node with a graphId that does not contain the node is refused",
              wrong_scope.get("ok") is False, json.dumps(wrong_scope)[:200])
        check("T464 the refusal names the graph, not a generic 'not found'",
              fg in (wrong_scope.get("error") or ""), wrong_scope.get("error"))
        still_at_old_pos = node_of(bguid)
        check("T464 the wrongly-scoped call genuinely moved nothing",
              (still_at_old_pos.get("x"), still_at_old_pos.get("y")) == (999, 777), still_at_old_pos)

        refresh_wrong = M.call("refresh_node", {"graphId": fg, "nodeGuid": bguid})
        check("T464 refresh_node with the wrong graphId is refused the same way",
              refresh_wrong.get("ok") is False and fg in (refresh_wrong.get("error") or ""),
              refresh_wrong.get("error"))

        ev = M.call("add_custom_event", {"graphId": eg, "name": "MifScopeProbe_%d" % st, "x": 1200, "y": 1200})
        ev_guid = ev.get("nodeGuid") or (ev.get("node") or {}).get("guid")
        check("T464 (setup) a throwaway custom event exists", bool(ev_guid), json.dumps(ev)[:170])
        if ev_guid:
            # NOT plain M.call - confirm is checked BEFORE graph-scoping in H_rename_event (verified
            # live, not assumed: a first attempt via M.call got "requires confirm=true" instead of the
            # graph-scope error, since guarded_payload strips "confirm" from every payload). Use
            # scratch_confirm so the call actually reaches the scoping check this test is about.
            rename_wrong = SC.confirm_call("rename_event", {"graphId": fg, "nodeGuid": ev_guid,
                                                             "newName": "ShouldNotApply"})
            check("T464 rename_event with the wrong graphId is refused",
                  rename_wrong.get("ok") is False and fg in (rename_wrong.get("error") or ""),
                  rename_wrong.get("error"))
            still_named = M.call("get_node", {"graphId": eg, "nodeGuid": ev_guid}).get("node") or {}
            check("T464 the wrongly-scoped rename left the real name in place",
                  still_named.get("title") == "MifScopeProbe_%d" % st, still_named)

            rename_right = SC.confirm_call("rename_event", {
                "graphId": eg, "nodeGuid": ev_guid, "newName": "MifScopeProbeRenamed_%d" % st})
            check("T464 rename_event with the CORRECT graphId succeeds", rename_right.get("ok") is True,
                  json.dumps(rename_right)[:200])

            remove_wrong = SC.confirm_call("remove_node", {"graphId": fg, "nodeGuid": ev_guid})
            check("T464 remove_node with the wrong graphId is refused", remove_wrong.get("ok") is False,
                  json.dumps(remove_wrong)[:200])
            still_there = M.call("get_node", {"graphId": eg, "nodeGuid": ev_guid})
            check("T464 the wrongly-scoped remove left the node in place",
                  still_there.get("ok") is True, still_there)

            remove_right = SC.confirm_call("remove_node", {"graphId": eg, "nodeGuid": ev_guid})
            check("T464 remove_node with the CORRECT graphId succeeds", remove_right.get("ok") is True,
                  json.dumps(remove_right)[:200])
            gone = M.call("get_node", {"graphId": eg, "nodeGuid": ev_guid})
            check("T464 and the node is really gone", gone.get("ok") is False, gone)

    SC.confirm_call("delete_asset", {"path": "/Game/_MifFind/BP_%d" % st})
    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
