"""PCG graph authoring: add/remove_pcg_node, connect/disconnect_pcg_nodes, and edges on the read half.

THE TRAP THAT SHAPES ALL OF THIS: UPCGGraph::AddEdge CANNOT REPORT FAILURE. It calls AddLabeledEdge,
THROWS THE RESULT AWAY, and returns `To` unconditionally (PCGGraph.cpp:473-477). A wrong pin label
therefore returns a perfectly good node pointer, logs an error to LogPCG that no HTTP caller will ever
see, and wires nothing. An endpoint built on AddEdge would report success for a graph it had not
connected.

AND AddLabeledEdge'S OWN BOOL IS AMBIGUOUS, which is the part that is easy to get wrong twice. It
returns false for "invalid node", false for "no such from-pin", false for "no such to-pin" - and then
on the SUCCESS path returns bToPinBrokeOtherEdges (PCGGraph.cpp:521). So false means EITHER "nothing
happened" OR "it worked cleanly", which are opposites. Reading the bool cannot tell you which.

The endpoint resolves this by making the ambiguity impossible rather than interpreting it: every
failure case AddLabeledEdge checks is checked FIRST, so its false can only mean "added without
displacing". The edge is then verified by reading the graph back, and displacement is reported as a
MEASURED count. T2802 and T2804 are the tests for those two halves.

T2804 IS THE ONE THAT PROTECTS WORK. A single-capacity input pin SILENTLY BREAKS whatever was already
attached when a second edge arrives - that is the engine's behaviour, not the endpoint's choice, and
a caller who is not told has lost a connection with no error to notice. PCGCopyPointsSettings.Source
is such a pin, which is what this test uses; PCGStaticMeshSpawnerSettings.In is NOT, and the test
asserts the difference so that "replacedEdges is always 0" can never pass by accident.

THE READ HALF WAS HALF-BLIND. describe_pcg_graph reported nodes and pin COUNTS and no edges at all,
so it could say what was IN a graph and nothing about what it DOES - two graphs with identical node
lists and no shared wiring compute completely different things. It also reported how many pins a node
had without their LABELS, while connect_pcg_nodes addresses pins BY label, so the read half could not
tell you the one string the write half required.

CLEANS UP: the scratch graph is deleted at the end. Nothing is saved.
"""
import json
import sys

import mifaudit as M
import scratch_confirm as SC

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

    probe = M.call("list_pcg_graphs", {})
    if not probe.get("ok") and "not available" in (probe.get("error") or "").lower():
        print("SKIPPED - the PCG plugin is not available on this engine build.")
        return 0

    import time
    st = int(time.time() % 100000)
    G = "/Game/_MifPCG/PCG_MifTest%d" % st
    made = False

    try:
        c = M.raw_post("create_asset", {"path": G, "class": "PCGGraph"})
        check("T2800 (setup) a scratch PCGGraph can be created", c.get("ok") is True,
              json.dumps(c)[:250])
        if not c.get("ok"):
            return 1
        made = True

        # ------------------------------------------------------------------ T2800 the read fix
        print("\n=== T2800: the read half can describe what a graph DOES, not just what is in it ===")
        d = M.call("describe_pcg_graph", {"path": G})
        check("T2800 describe_pcg_graph reports an edges array at all",
              isinstance(d.get("edges"), list) and isinstance(d.get("edgeCount"), (int, float)),
              json.dumps(d)[:250])
        check("T2800 a fresh graph has no edges", d.get("edgeCount") == 0, d.get("edgeCount"))

        # ------------------------------------------------------------------ T2801 nodes
        print("\n=== T2801: adding nodes, and refusing a class that is not one ===")
        bad = M.raw_post("add_pcg_node", {"graph": G, "settingsClass": "NotARealSettingsClass"})
        check("T2801 an unknown settings class is refused", bad.get("ok") is False,
              json.dumps(bad)[:250])
        check("T2801 and the refusal says how many real ones exist rather than just 'no'",
              "registered" in (bad.get("error") or ""), (bad.get("error") or "")[:180])

        a = M.raw_post("add_pcg_node", {"graph": G, "settingsClass": "PCGSurfaceSamplerSettings",
                                        "x": 100, "y": 50})
        check("T2801 a node can be added", a.get("ok") is True, json.dumps(a)[:250])
        sampler = a.get("node")
        check("T2801 it returns a stable node name", bool(sampler), sampler)
        # settingsPath is the field that makes the next call possible without guesswork.
        check("T2801 and settingsPath, so set_property can configure it immediately",
              bool(a.get("settingsPath")) and G in (a.get("settingsPath") or ""),
              a.get("settingsPath"))
        got = M.call("get_property", {"objectPath": a.get("settingsPath"),
                                      "propertyPath": "PointsPerSquaredMeter"})
        check("T2801 settingsPath really resolves through get_property - the round trip works",
              got.get("ok") is True, json.dumps(got)[:220])
        check("T2801 pin LABELS come back, not just counts - connect addresses pins by label",
              isinstance(a.get("outputPins"), list) and "Out" in (a.get("outputPins") or []),
              json.dumps(a.get("outputPins"))[:150])

        b = M.raw_post("add_pcg_node", {"graph": G,
                                        "settingsClass": "PCGStaticMeshSpawnerSettings",
                                        "x": 400, "y": 50})
        spawner = b.get("node")
        check("T2801 a second node is distinct from the first", bool(spawner) and spawner != sampler,
              "%s vs %s" % (sampler, spawner))
        check("T2801 the node count moved", b.get("nodeCount") == 2, b.get("nodeCount"))

        # ------------------------------------------------------------------ T2802 the AddEdge trap
        print("\n=== T2802: a bad pin label must be REFUSED - AddEdge would report success ===")
        badpin = M.raw_post("connect_pcg_nodes", {"graph": G, "fromNode": sampler,
                                                  "fromPin": "NoSuchPin", "toNode": spawner,
                                                  "toPin": "In"})
        check("T2802 an unknown output pin is refused", badpin.get("ok") is False,
              json.dumps(badpin)[:250])
        check("T2802 and the refusal lists the pins that DO exist",
              "Out" in (badpin.get("error") or ""), (badpin.get("error") or "")[:200])
        # The reason this check exists, stated in the response itself.
        check("T2802 and it names the trap - AddEdge would have looked like success",
              "wired nothing" in (badpin.get("error") or ""), (badpin.get("error") or "")[:220])
        badin = M.raw_post("connect_pcg_nodes", {"graph": G, "fromNode": sampler, "fromPin": "Out",
                                                 "toNode": spawner, "toPin": "NoSuchInput"})
        check("T2802 an unknown INPUT pin is refused too", badin.get("ok") is False,
              (badin.get("error") or "")[:180])
        check("T2802 neither refused call wired anything",
              M.call("describe_pcg_graph", {"path": G}).get("edgeCount") == 0,
              M.call("describe_pcg_graph", {"path": G}).get("edgeCount"))

        # ------------------------------------------------------------------ T2803 connect
        print("\n=== T2803: connecting, verified by reading the graph back ===")
        conn = M.raw_post("connect_pcg_nodes", {"graph": G, "fromNode": sampler, "fromPin": "Out",
                                                "toNode": spawner, "toPin": "In"})
        check("T2803 a valid connection succeeds", conn.get("ok") is True, json.dumps(conn)[:250])
        check("T2803 connected:true and it reports both ends", conn.get("connected") is True
              and conn.get("from") and conn.get("to"), json.dumps(conn)[:250])
        # THE postcondition - through the READ endpoint, not from connect's own response.
        d = M.call("describe_pcg_graph", {"path": G})
        edges = d.get("edges") or []
        check("T2803 describe_pcg_graph reports the edge - the two halves agree",
              any(e.get("fromNode") == sampler and e.get("toNode") == spawner for e in edges),
              json.dumps(edges)[:250])
        # GUARDED. all() over an empty list is vacuously true, so without the len() this would
        # pass hardest exactly when the edge list came back empty - the failure it exists to catch.
        check("T2803 and each edge names both pins, not just the nodes",
              len(edges) > 0 and all(e.get("fromPin") and e.get("toPin") for e in edges),
              json.dumps(edges)[:250])
        check("T2803 an edge is reported ONCE, not once per end",
              d.get("edgeCount") == 1, d.get("edgeCount"))

        dup = M.raw_post("connect_pcg_nodes", {"graph": G, "fromNode": sampler, "fromPin": "Out",
                                               "toNode": spawner, "toPin": "In"})
        check("T2803 reconnecting the same pins reports connected:false rather than duplicating",
              dup.get("ok") is True and dup.get("connected") is False
              and M.call("describe_pcg_graph", {"path": G}).get("edgeCount") == 1,
              json.dumps(dup)[:220])

        # ------------------------------------------------------------------ T2804 displacement
        print("\n=== T2804: a single-capacity pin silently breaks what was there - say so ===")
        # PCGStaticMeshSpawnerSettings.In DOES accept multiple connections. Asserting that first is
        # what stops "replacedEdges is always 0" from passing by accident.
        second = M.raw_post("add_pcg_node", {"graph": G,
                                             "settingsClass": "PCGSurfaceSamplerSettings"})
        s2 = second.get("node")
        multi = M.raw_post("connect_pcg_nodes", {"graph": G, "fromNode": s2, "fromPin": "Out",
                                                 "toNode": spawner, "toPin": "In"})
        check("T2804 a MULTI-connection pin accepts a second edge and displaces nothing",
              multi.get("ok") is True and multi.get("replacedEdges") == 0
              and M.call("describe_pcg_graph", {"path": G}).get("edgeCount") == 2,
              json.dumps(multi)[:250])

        copy = M.raw_post("add_pcg_node", {"graph": G, "settingsClass": "PCGCopyPointsSettings"})
        cp = copy.get("node")
        check("T2804 (setup) a node with a single-capacity input pin exists", bool(cp),
              json.dumps(copy)[:200])
        if cp:
            M.raw_post("connect_pcg_nodes", {"graph": G, "fromNode": sampler, "fromPin": "Out",
                                             "toNode": cp, "toPin": "Source"})
            disp = M.raw_post("connect_pcg_nodes", {"graph": G, "fromNode": spawner,
                                                    "fromPin": "Out", "toNode": cp,
                                                    "toPin": "Source"})
            check("T2804 the second edge onto a single-capacity pin succeeds",
                  disp.get("ok") is True, json.dumps(disp)[:250])
            # THE assertion. Without this the caller loses a connection with no error at all.
            check("T2804 and REPORTS that it broke the existing one",
                  disp.get("replacedEdges") == 1, json.dumps(disp)[:250])
            check("T2804 with a note saying it is the engine's behaviour, not a choice",
                  "does not accept multiple connections" in (disp.get("note") or ""),
                  (disp.get("note") or "")[:200])

        # ------------------------------------------------------------------ T2805 removal
        print("\n=== T2805: disconnect and remove, both measured ===")
        dis = M.raw_post("disconnect_pcg_nodes", {"graph": G, "fromNode": s2, "fromPin": "Out",
                                                  "toNode": spawner, "toPin": "In"})
        check("T2805 an edge can be removed by naming it", dis.get("ok") is True
              and dis.get("removed") == 1, json.dumps(dis)[:250])
        miss = M.raw_post("disconnect_pcg_nodes", {"graph": G, "fromNode": s2, "fromPin": "Out",
                                                   "toNode": spawner, "toPin": "In"})
        check("T2805 removing it again is not an error and reports removed:0",
              miss.get("ok") is True and miss.get("removed") == 0, json.dumps(miss)[:250])
        check("T2805 and cross-checks the count against what RemoveEdge claimed",
              "RemoveEdge reported" in (miss.get("note") or ""), (miss.get("note") or "")[:200])

        nc = M.raw_post("remove_pcg_node", {"graph": G, "node": s2})
        check("T2805 removing a node without confirm is refused", nc.get("ok") is False,
              json.dumps(nc)[:250])
        check("T2805 and the refusal states how many edges it would destroy - a real number",
              "edge(s) attached" in (nc.get("error") or ""), (nc.get("error") or "")[:200])

        before = M.call("describe_pcg_graph", {"path": G}).get("nodeCount")
        rm = M.raw_post("remove_pcg_node", {"graph": G, "node": s2, "confirm": True})
        check("T2805 with confirm it is removed", rm.get("ok") is True and rm.get("removed") is True,
              json.dumps(rm)[:250])
        check("T2805 and the graph really holds one fewer node",
              M.call("describe_pcg_graph", {"path": G}).get("nodeCount") == before - 1,
              "%s -> %s" % (before, M.call("describe_pcg_graph", {"path": G}).get("nodeCount")))

        gone = M.raw_post("remove_pcg_node", {"graph": G, "node": "NoSuchNodeAtAll",
                                              "confirm": True})
        check("T2805 an unknown node name is refused and the real ones are listed",
              gone.get("ok") is False and sampler in (gone.get("error") or ""),
              (gone.get("error") or "")[:200])
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": G})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
