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

    # ------------------------------------------------------------------ T447 the MakeContainer family
    print("\n=== T447: numInputs really makes that many element pins ===")
    # WHY THIS WAS MISSING, and it is a better reason than an oversight. numInputs is on
    # test_node_spawns' COSMETIC list - the set of parameters T330 supplies a token value for because
    # they only move a node or size a comment. It is not cosmetic. It decides how many element pins
    # the node has, and being on that list is exactly why nothing ever checked it: T330 drives all
    # three of these endpoints from the live registry, passes numInputs, and asserts only that a node
    # came back. A build where numInputs was ignored entirely would pass 109 checks.
    #
    # THE WHOLE FAMILY, not just the one that led here. add_make_set was the endpoint that surfaced
    # this (no suite named it - T330 reaches it only generically), but add_make_array and add_make_map
    # share the UK2Node_MakeContainer base and the same NumInputs-before-AllocateDefaultPins ordering,
    # so a defect in that ordering would hit all three. Testing one and trusting the family is how the
    # cooked-AnimSequence guard ended up needing writing four times.
    #
    # MAP COUNTS PAIRS, NOT PINS - and that asymmetry is the reason the expectation is per endpoint
    # rather than shared. One 'input' on a Make Map is one Key/Value ENTRY, so numInputs 3 gives SIX
    # element pins; the handler's own summary says so ("each entry is one Key + Value pin pair").
    WANT = 3
    for ep, per_input in (("add_make_array", 1), ("add_make_set", 1), ("add_make_map", 2)):
        made = M.call(ep, {"graphId": g, "numInputs": WANT})
        check("T447 %s places a node" % ep, made.get("ok") is not False, json.dumps(made)[:200])
        guid = made.get("nodeGuid") or (made.get("node") or {}).get("nodeGuid")
        if not guid:
            check("T447 %s reported a nodeGuid to read back" % ep, False, json.dumps(made)[:200])
            continue
        # FROM THE GRAPH'S OWN ACCOUNT, like everything else here - not from what the call said.
        node = (M.call("get_node", {"graphId": g, "nodeGuid": guid}).get("node") or {})
        pins = node.get("pins") or []
        # The element pins are the INPUTS. Every one of these nodes also has a single output (Array,
        # Set or Map), and counting that would make the map case look like it had an odd pin.
        ins = [x for x in pins if (x.get("direction") or "") == "input"]
        check("T447 %s with numInputs %d has %d element pin(s), not a default 1"
              % (ep, WANT, WANT * per_input),
              len(ins) == WANT * per_input,
              "%d input pin(s): %s" % (len(ins), [x.get("name") for x in ins]))
        outs = [x for x in pins if (x.get("direction") or "") == "output"]
        check("T447 %s still has exactly one container output" % ep, len(outs) == 1,
              [x.get("name") for x in outs])

    # The clamp, which is the half a caller hits by accident. The handler does
    # FMath::Clamp(numInputs, 1, 64), so 0 is not "no pins" and 999 is not a node with 999 pins -
    # both are silently corrected, and a caller who is not told will read the wrong count back.
    for asked, expect in ((0, 1), (999, 64)):
        made = M.call("add_make_array", {"graphId": g, "numInputs": asked})
        guid = made.get("nodeGuid") or (made.get("node") or {}).get("nodeGuid")
        if not guid:
            check("T447 add_make_array answered for numInputs %d" % asked, False, json.dumps(made)[:200])
            continue
        node = (M.call("get_node", {"graphId": g, "nodeGuid": guid}).get("node") or {})
        ins = [x for x in (node.get("pins") or []) if (x.get("direction") or "") == "input"]
        check("T447 numInputs %d is CLAMPED to %d rather than taken literally" % (asked, expect),
              len(ins) == expect, "%d input pin(s)" % len(ins))

    # ------------------------------------------------------------------ T448 the rest of the list
    print("\n=== T448: the other COSMETIC entries that are not cosmetic ===")
    # T447 came from ONE parameter being misfiled. Reading the whole COSMETIC set afterwards found
    # two more that change a node's PIN TOPOLOGY rather than its appearance, and so were never
    # checked for the same reason:
    #
    #   outputs   add_sequence's then_N EXEC pin count (2-64, default 2). The handler's own alias
    #             note draws the distinction the list missed - "add_make_array/add_make_map use
    #             numInputs; Sequence uses outputs".
    #   pure      add_cast builds a PURE cast, which has no exec pins at all. set_cast_purity's
    #             toggle is already asserted by pin shape (T917 in test_uncovered_reads5), but
    #             building one pure from the start is a different path from switching one over.
    #
    # The genuinely cosmetic entries are left alone deliberately: x, y, width, height, comment and
    # title move or label a node and nothing downstream reads them.
    seq = M.call("add_sequence", {"graphId": g, "outputs": 5})
    sguid = seq.get("nodeGuid") or (seq.get("node") or {}).get("nodeGuid")
    check("T448 add_sequence places a node", bool(sguid), json.dumps(seq)[:200])
    if sguid:
        snode = (M.call("get_node", {"graphId": g, "nodeGuid": sguid}).get("node") or {})
        # then_N ONLY. A Sequence also has an exec INPUT, and counting every exec pin would report
        # six for a five-output node and look like an off-by-one in the handler.
        thens = [x for x in (snode.get("pins") or [])
                 if (x.get("direction") or "") == "output" and (x.get("name") or "").startswith("then")]
        check("T448 outputs 5 gives five then_N exec pins, not the default two",
              len(thens) == 5, "%d: %s" % (len(thens), [x.get("name") for x in thens]))
    # The clamp is 2-64 here, NOT 1-64 - a Sequence with one output is not a sequence, and the
    # difference from numInputs' lower bound is exactly the kind of thing a shared test would miss.
    for asked, expect, edge in ((1, 2, "floor is 2 here, not numInputs' 1"), (999, 64, "ceiling is 64")):
        r = M.call("add_sequence", {"graphId": g, "outputs": asked})
        rg = r.get("nodeGuid") or (r.get("node") or {}).get("nodeGuid")
        if not rg:
            check("T448 add_sequence answered for outputs %d" % asked, False, json.dumps(r)[:200])
            continue
        n = (M.call("get_node", {"graphId": g, "nodeGuid": rg}).get("node") or {})
        thens = [x for x in (n.get("pins") or [])
                 if (x.get("direction") or "") == "output" and (x.get("name") or "").startswith("then")]
        check("T448 outputs %d is clamped to %d - the %s" % (asked, expect, edge),
              len(thens) == expect, "%d then pin(s)" % len(thens))

    # A pure cast has NO exec pins. An impure one has exec in and exec out, so this asserts the
    # DIFFERENCE rather than a count on its own - a build that ignored `pure` would give both nodes
    # the same shape, and only comparing them says so.
    shapes = {}
    for label, payload in (("impure", {}), ("pure", {"pure": True})):
        args = {"graphId": g, "targetClass": "Actor"}
        args.update(payload)
        r = M.call("add_cast", args)
        cg = r.get("nodeGuid") or (r.get("node") or {}).get("nodeGuid")
        check("T448 add_cast places a %s cast" % label, bool(cg), json.dumps(r)[:200])
        if not cg:
            continue
        n = (M.call("get_node", {"graphId": g, "nodeGuid": cg}).get("node") or {})
        shapes[label] = [x for x in (n.get("pins") or [])
                         if (x.get("type") or {}).get("category") == "exec"]
    if "pure" in shapes and "impure" in shapes:
        check("T448 a cast built with pure:true has NO exec pins", not shapes["pure"],
              [x.get("name") for x in shapes["pure"]])
        check("T448 and the default impure one does - so `pure` is read, not ignored",
              len(shapes["impure"]) > 0, [x.get("name") for x in shapes["impure"]])

    # ------------------------------------------------------------------ T449 the orphaned pin
    print("\n=== T449: retyping a wired variable leaves an ORPHAN, and says so ===")
    # THE ENGINE KEEPS IT ON PURPOSE. A pin whose type no longer fits but which is still CONNECTED
    # is retained and flagged bOrphanedPin, so a human can see what broke and rewire it instead of
    # losing the link silently. ReconstructNode cannot remove it and should not.
    #
    # So the endpoint's job is not to produce a clean pin list - it is to TELL YOU what it left.
    # An earlier version of the fix counted ReconstructNode calls and reported them as the outcome,
    # which is how it came to claim the pins matched while the node carried two named A.
    #
    # THE GRAPH IS THE ARBITER. The reported count is compared against get_node rather than trusted,
    # because a number the graph does not back up is the same failure wearing a new field.
    M.call("add_variable", {"blueprintId": bid, "name": "T449A", "type": "int"})
    M.call("add_variable", {"blueprintId": bid, "name": "T449B", "type": "int"})
    og = M.call("add_variable_get", {"graphId": g, "variable": "T449A"})
    os_ = M.call("add_variable_set", {"graphId": g, "variable": "T449B"})
    ogg = og.get("nodeGuid") or (og.get("node") or {}).get("nodeGuid")
    osg = os_.get("nodeGuid") or (os_.get("node") or {}).get("nodeGuid")
    wired = M.call("connect_pins", {"graphId": g, "srcNode": ogg, "srcPin": "T449A",
                                    "dstNode": osg, "dstPin": "T449B"})
    check("T449 (setup) a legal int -> int link", wired.get("ok") is True, json.dumps(wired)[:200])
    rt = M.call("set_variable_type", {"blueprintId": bid, "name": "T449A", "type": "Actor"})
    check("T449 the retype succeeds", rt.get("ok") is True, json.dumps(rt)[:200])
    check("T449 it reports how many nodes it reconstructed", rt.get("nodesReconstructed") == 1,
          "nodesReconstructed=%r" % rt.get("nodesReconstructed"))
    check("T449 and MEASURES the orphans it left rather than claiming a clean result",
          rt.get("orphanedPinsRemaining") == 1 and rt.get("nodesWithOrphanedPin") == 1,
          "orphanedPinsRemaining=%r nodesWithOrphanedPin=%r"
          % (rt.get("orphanedPinsRemaining"), rt.get("nodesWithOrphanedPin")))
    named = [x for x in ((M.call("get_node", {"graphId": g, "nodeGuid": ogg}).get("node") or {})
                         .get("pins") or []) if x.get("name") == "T449A"]
    check("T449 the count AGREES with the graph - two pins of that name, one of them the orphan",
          (rt.get("orphanedPinsRemaining") or 0) == max(0, len(named) - 1) and len(named) == 2,
          "%d pin(s): %s" % (len(named), [((x.get("type") or {}).get("category"),
                                           len(x.get("linkedTo") or [])) for x in named]))
    check("T449 and the note says a clean compile is NOT evidence the retype was safe",
          "NOT evidence" in (rt.get("note") or "") or "compiles clean" in (rt.get("note") or ""),
          (rt.get("note") or "")[:200])
    # THE ORPHAN IS THE ONE HOLDING THE LINK. That is the whole hazard: a caller resolving by name
    # gets whichever comes first and cannot tell the live pin from the dead one.
    linked = [x for x in named if len(x.get("linkedTo") or []) > 0]
    check("T449 the surviving link is on the OLD typed pin, which is what makes this dangerous",
          len(linked) == 1 and (linked[0].get("type") or {}).get("category") == "int",
          [((x.get("type") or {}).get("category"), len(x.get("linkedTo") or [])) for x in named])

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
