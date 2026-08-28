"""Do array-library wildcard pins stay typed across a node reconstruct?

TWO DOCS DISAGREE, which is why this exists.

  docs/02_GOTCHAS.md §4c says array calls are first-class now: "The old 'Array_Find won't stay typed,
  use a ForEachLoop macro' rule no longer applies: the cause was the spawned node class, and it is
  fixed."

  docs/06_OPEN_ISSUES_FROM_USE.md §5 says the opposite and calls it the most severe item in the file:
  the pin stays wildcard, the node is reconstructed on save/cook, the containing function is STUBBED
  during the cook, "so the editor says fine and the shipped game silently does nothing". Its triage
  note asks for a reproduction before any fix is attempted.

Both cannot be true. This suite is that reproduction, and its job is to settle which doc is wrong -
either outcome is a result worth having, and leaving two docs contradicting each other on the most
severe known defect is worse than either answer.

WHAT IT ACTUALLY TESTS. A cook cannot be run from here, so the proxy is refresh_node, which
02_GOTCHAS §4c itself nominates: "refresh_node still reproduces a reload reconstruct, so it remains
the way to prove durability before you cook." If a pin type survives a reconstruct it will survive the
reload; if it does not, that is the reported bug reproduced.

HONEST LIMIT: refresh_node is a proxy for the cook, not the cook. A pin that survives here could still
be lost by the cooker for some other reason, and this suite cannot see that. What it CAN do is prove
the reconstruct half, which is the half both docs are arguing about.
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


def pin_types(graph, node):
    """name -> category for every pin on a node, read back through the bridge.

    get_node nests the pins under "node", and each pin nests its type under "type" as
    {category, container, subObject}. The first version of this read r["pins"] and p["category"],
    which are both absent - so it saw NO pins, found NO wildcards, and every assertion below passed
    while measuring an empty dict. A green suite that observed nothing is worse than no suite, and it
    is exactly the failure this file was written to catch in someone else.
    """
    r = M.call("get_node", {"graphId": graph, "nodeGuid": node})
    node_obj = r.get("node") or {}
    pins = node_obj.get("pins") or []
    out = {}
    for p in pins:
        t = p.get("type") or {}
        out[p.get("name")] = "%s%s" % (t.get("category") or "?",
                                       "[" + t["container"] + "]" if t.get("container") else "")
    return out, node_obj


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    bp = "/Game/_MifArr/BP_%d" % st
    made = M.call("create_blueprint", {"path": bp, "parentClass": "Actor"})
    bid = made.get("blueprintId")
    if not bid:
        print("setup failed: %s" % json.dumps(made)[:200])
        return 3
    print("blueprint: %s" % bid)

    # An array variable gives the wildcard something concrete to take its type FROM.
    av = M.call("add_variable", {"blueprintId": bid, "name": "Numbers",
                                 "type": "int", "container": "Array"})
    check("setup: an int array variable exists", av.get("ok") is True, json.dumps(av)[:170])

    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    graph = next((g.get("graphId") for g in graphs
                  if "EventGraph" in (g.get("name") or "")), None)
    if not graph:
        graph = (graphs[0].get("graphId") if graphs else None)
    check("setup: a graph to work in", bool(graph), json.dumps(graphs)[:170])
    if not graph:
        return 3

    # ------------------------------------------------------------------ T280 the wildcard resolves
    print("\n=== T280: connecting an array to a library call resolves its wildcard ===")
    getter = M.call("add_variable_get", {"graphId": graph, "var": "Numbers", "x": 0, "y": 0})
    # The class key is required: Array_* live on UKismetArrayLibrary, not on the blueprint, and the
    # endpoint refuses rather than guessing - which is the right call and worth the extra argument.
    call = M.call("add_function_call", {"graphId": graph, "function": "Array_Length",
                                        "class": "KismetArrayLibrary", "x": 400, "y": 0})
    check("T280 a variable getter is created", getter.get("ok") is True, json.dumps(getter)[:150])
    check("T280 an Array_Length call is created", call.get("ok") is True, json.dumps(call)[:150])
    if not (getter.get("ok") and call.get("ok")):
        print("cannot continue without both nodes")
        return 1
    gnode = getter.get("nodeGuid") or getter.get("guid")
    cnode = call.get("nodeGuid") or call.get("guid")

    before_types, before_raw = pin_types(graph, cnode)
    wildcard_pins = [k for k, v in before_types.items() if "wildcard" in v.lower()]
    print("   pins before connecting: %s" % json.dumps(before_types))
    # NON-VACUITY GUARD. Everything below asks whether a wildcard stayed resolved; if none was ever
    # observed, those questions have no subject and passing them proves nothing.
    check("T280 a wildcard pin was actually observed", bool(wildcard_pins),
          "no wildcard on an unconnected Array_Length - either the reader is wrong again or this "
          "engine spawns it pre-typed, and either way the rest of this suite is vacuous")

    conn = M.call("connect_pins", {"graphId": graph,
                                   "srcNode": gnode, "srcPin": "Numbers",
                                   "dstNode": cnode, "dstPin": "TargetArray"})
    check("T280 the array connects to the library call", conn.get("ok") is True,
          json.dumps(conn)[:200])

    after_types, _ = pin_types(graph, cnode)
    still_wild = [k for k, v in after_types.items() if "wildcard" in v.lower()]
    check("T280 the wildcard RESOLVED on connection", not still_wild,
          "still wildcard after connecting: %s" % still_wild)
    resolved = {k: v for k, v in after_types.items() if k in wildcard_pins}
    print("   resolved to: %s" % json.dumps(resolved)[:150])

    # ------------------------------------------------------------------ T281 the actual question
    print("\n=== T281 [the disagreement]: does the type SURVIVE a reconstruct? ===")
    # 02_GOTCHAS §4c nominates refresh_node as the way to prove durability before a cook.
    ref = M.call("refresh_node", {"graphId": graph, "nodeGuid": cnode})
    check("T281 the node can be refreshed", ref.get("ok") is True, json.dumps(ref)[:180])

    post_types, post_raw = pin_types(graph, cnode)
    post_wild = [k for k, v in post_types.items() if "wildcard" in v.lower()]
    # THE assertion the whole file exists for.
    check("T281 the pin is STILL typed after the reconstruct", not post_wild,
          "reverted to wildcard: %s -- this REPRODUCES 06_OPEN_ISSUES §5" % post_wild)
    linked = [p.get("name") for p in (post_raw.get("pins") or []) if p.get("linkedTo")]
    check("T281 and the connection survived the reconstruct", "TargetArray" in linked,
          "pins still linked after refresh: %s" % linked)

    # ------------------------------------------------------------------ T282 does it compile
    print("\n=== T282: and the blueprint compiles ===")
    c = M.call("compile", {"blueprintId": bid})
    check("T282 compiles with no errors", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))
    # A compile that passes while the pin is wildcard is exactly the reported symptom: green in the
    # editor, stubbed in the cook. So the two facts are reported together rather than separately.
    if post_wild and c.get("numErrors", 1) == 0:
        check("T282 GREEN-IN-EDITOR-DEAD-IN-BUILD signature", False,
              "the pin is wildcard AND the blueprint compiles clean - this is precisely the "
              "condition 06_OPEN_ISSUES §5 describes as stubbed during cook")

    SC.confirm_call("delete_asset", {"path": bp})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("VERDICT: if T281 passed, 02_GOTCHAS §4c is right and 06_OPEN_ISSUES §5 is STALE.")
    print("If T281 failed, §5 is real and this is the reproduction its triage note asked for.")
    print("Either way one of the two documents must be corrected - they cannot both stand.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
