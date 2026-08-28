"""The pin family - connect, disconnect, reconnect, retype, add. Core graph editing, no suite.

Every graph edit ends in a pin operation, and a pin is where "reported success, did something else"
is hardest to see: a link that exists on one side only, a retype the node quietly overrode, a
disconnect that cleared the pin you named and left its partner pointing at nothing. None of that
shows in an ok:true, and all of it compiles until something reads it.

So every assertion here is made from get_node's `linkedTo` and `type` - the graph's own account -
rather than from what the mutating call said about itself.

The two that carry history:

  set_pin_type had a silent revert. Nodes that derive their pin types from their CONNECTIONS
  (K2Node_MakeArray and friends) ignore a directly written type and put the wildcard straight back,
  and the endpoint used to report success anyway. It now reads the pin back and FAILS with the reason,
  which is the behaviour T443 protects - a fix that turns a silent wrong answer into a loud right one
  is exactly the kind that gets undone by a later refactor.

  disconnect_pin has to clear BOTH ends. Clearing only the named side leaves the partner linked to a
  pin that no longer links back, which is a graph that looks fine in the editor and misbehaves on
  compile.
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

    bid = M.call("create_blueprint", {"path": "/Game/_MifPin/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    g = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    check("its event graph resolves", bool(g), str([x.get("name") for x in graphs]))
    if not g:
        return 1

    def pin_of(guid, pin):
        d = M.call("get_node", {"graphId": g, "nodeGuid": guid})
        node = d.get("node") or d
        for p in (node.get("pins") or []):
            if p.get("name") == pin:
                return p
        return None

    def links(guid, pin):
        p = pin_of(guid, pin)
        return None if p is None else (p.get("linkedTo") or [])

    n = []
    for i in range(3):
        r = M.call("add_function_call", {"graphId": g, "function": "PrintString",
                                         "class": "KismetSystemLibrary", "x": 300 * i, "y": 500})
        if r.get("ok"):
            n.append(r.get("nodeGuid"))
    check("three nodes exist to wire", len(n) == 3, "got %d" % len(n))
    if len(n) != 3:
        return 1

    # ------------------------------------------------------------------ T440 connect
    print("")
    print("=== T440: a connection exists on BOTH ends or it is not a connection ===")
    c = M.call("connect_pins", {"graphId": g, "srcNode": n[0], "srcPin": "then",
                                "dstNode": n[1], "dstPin": "execute"})
    check("T440 the connect succeeds", c.get("ok") is True, json.dumps(c)[:200])
    src = links(n[0], "then")
    dst = links(n[1], "execute")
    check("T440 the source pin records it", any(l.get("node") == n[1] for l in (src or [])),
          json.dumps(src)[:180])
    # The half a caller never checks.
    check("T440 and so does the destination", any(l.get("node") == n[0] for l in (dst or [])),
          json.dumps(dst)[:180])

    # ------------------------------------------------------------------ T441 disconnect
    print("")
    print("=== T441: disconnecting must clear both ends too ===")
    d = M.call("disconnect_pin", {"graphId": g, "node": n[0], "pin": "then"})
    check("T441 the disconnect succeeds", d.get("ok") is True, json.dumps(d)[:200])
    check("T441 the named pin is clear", links(n[0], "then") == [], json.dumps(links(n[0], "then")))
    # THE assertion. A one-sided disconnect leaves the partner pointing at a pin that no longer
    # points back - the graph looks right and misbehaves on compile.
    check("T441 and its former partner is clear as well", links(n[1], "execute") == [],
          "n1.execute still links to %s" % json.dumps(links(n[1], "execute")))

    # Disconnecting something already disconnected must not claim to have done work.
    again = M.call("disconnect_pin", {"graphId": g, "node": n[0], "pin": "then"})
    check("T441 disconnecting an already-clear pin answers rather than erroring oddly",
          isinstance(again.get("ok"), bool), json.dumps(again)[:170])

    # ------------------------------------------------------------------ T442 reconnect
    print("")
    print("=== T442: reconnect moves a link, and the old end lets go ===")
    M.call("connect_pins", {"graphId": g, "srcNode": n[0], "srcPin": "then",
                            "dstNode": n[1], "dstPin": "execute"})
    r = M.call("reconnect_pin", {"graphId": g, "srcNode": n[0], "srcPin": "then",
                                 "dstNode": n[2], "dstPin": "execute"})
    check("T442 the reconnect succeeds", r.get("ok") is True, json.dumps(r)[:220])
    if r.get("ok"):
        check("T442 the link now points at the new node",
              any(l.get("node") == n[2] for l in (links(n[0], "then") or [])),
              json.dumps(links(n[0], "then"))[:180])
        # If the old destination keeps its link, the graph now has a connection nobody asked for.
        check("T442 and the OLD destination let go",
              not any(l.get("node") == n[0] for l in (links(n[1], "execute") or [])),
              "n1.execute still links back to n0: %s" % json.dumps(links(n[1], "execute")))

    # ------------------------------------------------------------------ T443 the silent revert
    print("")
    print("=== T443 [the history]: a retype the node overrides must FAIL, not succeed ===")
    arr = M.call("add_make_array", {"graphId": g, "x": 0, "y": 900})
    aguid = arr.get("nodeGuid")
    check("T443 a MakeArray node exists", bool(aguid), json.dumps(arr)[:170])
    if aguid:
        before = (pin_of(aguid, "[0]") or {}).get("type")
        q = M.call("set_pin_type", {"graphId": g, "node": aguid, "pin": "[0]", "type": "int"})
        after = (pin_of(aguid, "[0]") or {}).get("type")
        # MakeArray derives its element type from what is WIRED to it, so a written type is put back.
        # The endpoint must notice and say so - reporting ok:true here is the original defect.
        if (after or {}).get("category") == "wildcard":
            check("T443 the endpoint reports the revert instead of claiming success",
                  q.get("ok") is False, json.dumps(q)[:220])
            check("T443 and says the node overrode it",
                  "did not stick" in (q.get("error") or "") or "override" in (q.get("error") or "").lower(),
                  (q.get("error") or "")[:200])
            check("T443 and names both what was asked and what the pin is now",
                  "int" in (q.get("error") or "") and "wildcard" in (q.get("error") or ""),
                  (q.get("error") or "")[:200])
        else:
            # If the engine ever lets it stick, that is fine - but then it must have STUCK.
            check("T443 a retype that reports success really took",
                  q.get("ok") is True and (after or {}).get("category") == "int",
                  "before=%s after=%s said=%s" % (json.dumps(before), json.dumps(after),
                                                  json.dumps(q)[:120]))

    # ------------------------------------------------------------------ T444 add_node_pin
    print("")
    print("=== T444: adding a pin to a variadic node ===")
    if aguid:
        before_pins = [p.get("name") for p in
                       ((M.call("get_node", {"graphId": g, "nodeGuid": aguid}).get("node") or {}).get("pins") or [])]
        a = M.call("add_node_pin", {"graphId": g, "node": aguid})
        check("T444 the pin is added", a.get("ok") is True, json.dumps(a)[:200])
        check("T444 and it names the pins it added", bool(a.get("addedPins")), json.dumps(a)[:200])
        after_pins = [p.get("name") for p in
                      ((M.call("get_node", {"graphId": g, "nodeGuid": aguid}).get("node") or {}).get("pins") or [])]
        check("T444 and the node really has one more pin", len(after_pins) == len(before_pins) + 1,
              "%s -> %s" % (before_pins, after_pins))
        # added vs requested is only worth reporting if they can differ; assert they agree here.
        check("T444 added matches requested", a.get("added") == a.get("requested"),
              "added=%s requested=%s" % (a.get("added"), a.get("requested")))

    # ------------------------------------------------------------------ T445 guards
    print("")
    print("=== T445: bad pin references are refused ===")
    q = M.call("connect_pins", {"graphId": g, "srcNode": n[0], "srcPin": "NoSuchPin_zz",
                                "dstNode": n[1], "dstPin": "execute"})
    check("T445 connecting a pin that does not exist is refused", q.get("ok") is False,
          json.dumps(q)[:180])
    q = M.call("disconnect_pin", {"graphId": g, "node": n[0], "pin": "NoSuchPin_zz"})
    check("T445 disconnecting a pin that does not exist is refused", q.get("ok") is False,
          json.dumps(q)[:180])
    q = M.call("connect_pins", {"graphId": g, "srcNode": n[0], "srcPin": "then",
                                "dstNode": n[1], "dstPin": "then"})
    # Two outputs cannot be wired together; accepting it would produce a graph the schema rejects.
    check("T445 wiring two outputs together is refused", q.get("ok") is False, json.dumps(q)[:180])

    c = M.call("compile", {"blueprintId": bid})
    check("T445 the blueprint still compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s" % c.get("numErrors"))

    SC.confirm_call("delete_asset", {"path": "/Game/_MifPin/BP_%d" % st})
    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
