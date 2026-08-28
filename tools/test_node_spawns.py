"""Do the node-creation endpoints actually put a usable node in the graph?

`add_*` is the largest block that no suite names - 33 endpoints when this was written, and node
creation is what this bridge is mostly FOR. The failure worth hunting is not a crash: it is an endpoint
that answers ok:true with a node guid while the graph gains nothing usable, which is invisible until a
compile much later blames something else.

DRIVEN FROM THE LIVE REGISTRY, not a hand-written list. It asks describe_endpoint for each add_*
endpoint's acceptedParams and drives every one that needs nothing beyond a graph and coordinates. So a
node endpoint added next month is covered the day it lands, without anyone remembering to add it here -
which is the specific way this file would otherwise go stale, since the 33 uncovered ones got that way
by being added one at a time.

Every node is checked three ways, because ok:true is the thing under suspicion:
  * the response carries a node guid,
  * get_node can resolve that guid in the graph afterwards - a guid that resolves to nothing is the
    exact silent failure being hunted,
  * and the blueprint still compiles with every node present.

The endpoints needing real arguments (a struct, a class, an enum) are driven explicitly further down,
because a generated argument would test the guess rather than the endpoint.

T334/T335 (added 2026-08-28, from a coverage_gaps.py sweep) are more of the same "needs a real
argument" family - a class cast, a format string, switch/enum/subsystem/literal/InputAction nodes -
plus add_blackboard_key on its own scratch BlackboardData asset. That last one hit the SAME wall
remove_node already had documented: mifaudit.py's FORBIDDEN_KEYS strips confirm from every call this
harness makes, so a confirm-gated endpoint's success path (and, for add_blackboard_key specifically,
every check the handler makes AFTER its confirm gate too) is structurally unreachable here. An earlier
draft asserted "duplicate name refused" and "bad type refused" as if they proved those specific
checks; both were actually re-triggering the confirm refusal for an unrelated reason, caught only by
reading the literal error text under a passing check with a more specific name than what it proved.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []

# Anything in here is either cosmetic or has a usable default, so an endpoint accepting only these can
# be driven with nothing but a graph.
COSMETIC = {"graphId", "x", "y", "width", "height", "text", "outputs", "numInputs",
            "comment", "title", "purity", "pure"}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def node_exists(graph, guid):
    """True when the graph can still resolve this guid - the assertion ok:true cannot make."""
    r = M.call("get_node", {"graphId": graph, "nodeGuid": guid})
    return bool(r.get("ok")) and bool((r.get("node") or {}).get("guid"))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)
    bpath = "/Game/_MifNodes/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    if not bid:
        print("setup failed")
        return 3
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    graph = next((g.get("graphId") for g in graphs if "EventGraph" in (g.get("name") or "")), None)
    if not graph:
        print("setup failed: no event graph")
        return 3

    # ------------------------------------------------------------------ T330 registry-driven
    print("\n=== T330: every node endpoint that needs only a graph ===")
    simple = []
    for ep in sorted(n for n in M.endpoint_names() if n.startswith("add_")):
        acc = set(M.call("describe_endpoint", {"name": ep}).get("acceptedParams") or [])
        if acc and "graphId" in acc and acc <= COSMETIC:
            simple.append(ep)
    # If this ever finds nothing, the suite is vacuous and should say so rather than pass.
    check("T330 the registry yielded endpoints to drive", len(simple) >= 5,
          "only %d found - describe_endpoint may have changed shape, and this suite is then vacuous"
          % len(simple))
    print("   driving: %s" % ", ".join(simple))

    placed, y = [], 0
    for ep in simple:
        y += 150
        r = M.call(ep, {"graphId": graph, "x": 0, "y": y})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T330 %s reports success" % ep, r.get("ok") is True, json.dumps(r)[:150])
        check("T330 %s returns a node guid" % ep, bool(guid), json.dumps(r)[:150])
        if guid:
            # THE assertion. ok:true plus a guid that resolves to nothing is the failure being hunted.
            check("T330 %s's node is really in the graph" % ep, node_exists(graph, guid),
                  "guid %s does not resolve - the call said it created a node and the graph has none"
                  % guid)
            placed.append((ep, guid))

    # ------------------------------------------------------------------ T331 endpoints with arguments
    print("\n=== T331: node endpoints that need a real argument ===")
    M.call("add_variable", {"blueprintId": bid, "name": "Amount", "type": "float"})
    # A USER-DEFINED struct for the make/break pair. FVector breaks fine and cannot be MADE - the
    # engine refuses with "no BP-visible members", because breaking needs only read access while
    # making needs every member writable from Blueprint. Using it for both would have tested that
    # asymmetry rather than the endpoint, and the refusal is correct behaviour worth not mistaking
    # for a bug.
    spath = "/Game/_MifNodes/S_%d" % st
    sres = M.call("create_struct", {"path": spath})
    struct_name = sres.get("name") or ("S_%d" % st)
    M.call("add_struct_member", {"struct": sres.get("structPath") or spath,
                                 "name": "Price", "type": "float"})
    specific = [
        ("add_variable_get", {"graphId": graph, "var": "Amount", "x": 400, "y": 0}),
        ("add_variable_set", {"graphId": graph, "var": "Amount", "x": 400, "y": 150}),
        ("add_custom_event", {"graphId": graph, "name": "MifTestEvent_%d" % st, "x": 400, "y": 300}),
        ("add_cast", {"graphId": graph, "castTo": "Pawn", "x": 400, "y": 450}),
        ("add_make_struct", {"graphId": graph, "structName": struct_name, "x": 400, "y": 600}),
        ("add_break_struct", {"graphId": graph, "structName": "Vector", "x": 400, "y": 750}),
    ]
    for ep, payload in specific:
        r = M.call(ep, payload)
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        ok = r.get("ok") is True
        check("T331 %s succeeds" % ep, ok, (r.get("error") or json.dumps(r))[:170])
        if ok and guid:
            check("T331 %s's node is really in the graph" % ep, node_exists(graph, guid),
                  "guid %s does not resolve" % guid)
            placed.append((ep, guid))

    # ------------------------------------------------------------------ T332 they survive together
    print("\n=== T332: the graph holds them all and still compiles ===")
    listed = M.call("list_nodes", {"graphId": graph}).get("nodes") or []
    guids = {n.get("guid") for n in listed}
    missing = [ep for ep, g in placed if g not in guids]
    # A node can resolve individually and still be absent from the listing - two different reads, and
    # disagreement between them is worth catching.
    check("T332 every placed node appears in list_nodes", not missing,
          "created but not listed: %s" % missing)
    check("T332 the listing is not suspiciously short", len(listed) >= len(placed),
          "%d listed vs %d placed" % (len(listed), len(placed)))
    c = M.call("compile", {"blueprintId": bid})
    # Unconnected nodes are legal, so a clean compile is the right expectation here.
    check("T332 the blueprint compiles with all of them",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))

    # ------------------------------------------------------------------ T333 removal
    print("\n=== T333: a placed node can be removed again ===")
    if placed:
        ep, guid = placed[0]
        # CONFIRM-GATED, so only the refusal is reachable: the audit harness strips `confirm`.
        rm = M.call("remove_node", {"graphId": graph, "nodeGuid": guid})
        check("T333 remove_node refuses without confirm", rm.get("ok") is False, json.dumps(rm)[:170])
        check("T333 and says confirm is what is missing", "confirm" in (rm.get("error") or ""),
              (rm.get("error") or "")[:150])
        # The refusal must leave the node it declined to remove.
        check("T333 the node survives a refused removal", node_exists(graph, guid),
              "the node vanished on a refused call")
        ghost = M.call("remove_node", {"graphId": graph,
                                       "nodeGuid": "DEADBEEF00004444DEADBEEF00004444"})
        check("T333 removing a node that does not exist is refused",
              ghost.get("ok") is False, json.dumps(ghost)[:150])
        c = M.call("compile", {"blueprintId": bid})
        check("T333 and the blueprint still compiles",
              c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T334 more node endpoints
    # needing a real argument the registry sweep and T331 do not already cover - found by comparing
    # coverage_gaps.py's list against what T330's own "driving:" line actually swept: three names on
    # that list (add_get_array_item, add_make_map, add_self) turned out to be dynamically covered by
    # T330 already - coverage_gaps.py cannot see a name that is never typed as a literal string, only
    # produced by iterating describe_endpoint's live registry. These are the ones genuinely missing.
    print("\n=== T334: more node endpoints needing a real argument ===")
    y2 = 900
    node_specific = [
        ("add_class_cast", {"graphId": graph, "targetClass": "Pawn"}),
        ("add_format_text", {"graphId": graph, "format": "Hello {Name}, you have {Count}"}),
        ("add_switch_int", {"graphId": graph, "cases": 3}),
        ("add_switch_string", {"graphId": graph, "cases": ["Open", "Closed", "Locked"]}),
        # A stock engine enum, not a project one, so this passes on any project this bridge runs
        # against - the same reasoning list_enum_values' own tests use a stock enum for.
        ("add_switch_enum", {"graphId": graph, "enumName": "ECollisionChannel"}),
        ("add_get_subsystem", {"graphId": graph, "subsystemClass": "EditorActorSubsystem"}),
        # An object-reference literal needs a real asset path; the scratch blueprint itself is one.
        ("add_literal", {"graphId": graph, "object": bpath}),
    ]
    for ep, payload in node_specific:
        payload = dict(payload)
        y2 += 150
        payload["x"], payload["y"] = 700, y2
        r = M.call(ep, payload)
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        ok = r.get("ok") is True
        check("T334 %s succeeds" % ep, ok, (r.get("error") or json.dumps(r))[:200])
        if ok and guid:
            check("T334 %s's node is really in the graph" % ep, node_exists(graph, guid),
                  "guid %s does not resolve" % guid)
            placed.append((ep, guid))

    # add_enhanced_input_action needs a REAL InputAction asset - DDS2 is a real shipped game and has
    # real Enhanced Input content (confirmed earlier this session, test_uncovered_reads.py T824), so
    # this is exercised against real content rather than a fabricated one.
    actions = M.call("find_assets", {"class": "InputAction", "limit": 1}).get("assets") or []
    if actions:
        y2 += 150
        r = M.call("add_enhanced_input_action",
                   {"graphId": graph, "inputAction": actions[0].get("path"), "x": 700, "y": y2})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T334 add_enhanced_input_action succeeds on a real InputAction",
              r.get("ok") is True, (r.get("error") or json.dumps(r))[:200])
        if r.get("ok") and guid:
            check("T334 add_enhanced_input_action's node is really in the graph",
                  node_exists(graph, guid), "guid %s does not resolve" % guid)
            placed.append(("add_enhanced_input_action", guid))
    else:
        print("  SKIP  add_enhanced_input_action - no InputAction asset found on this project")

    c = M.call("compile", {"blueprintId": bid})
    check("T334 the blueprint still compiles with all of T334's nodes too",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))

    q = M.call("add_class_cast", {"graphId": graph})
    check("T334 add_class_cast with no target class refuses", q.get("ok") is False, q.get("error"))
    q = M.call("add_get_subsystem", {"graphId": graph, "subsystemClass": "Actor"})
    check("T334 add_get_subsystem refuses a non-Subsystem class",
          q.get("ok") is False and "not a Subsystem" in (q.get("error") or ""), q.get("error"))
    q = M.call("add_switch_enum", {"graphId": graph, "enumName": "NoSuchEnum_zz_definitely_not_real"})
    check("T334 add_switch_enum refuses an unknown enum name", q.get("ok") is False, q.get("error"))

    # ------------------------------------------------------------------ T335 add_blackboard_key
    # SAME DELIBERATE GAP AS T333's remove_node, and worth stating with the same honesty rather than
    # routing around it: mifaudit.py's FORBIDDEN_KEYS strips `confirm` from every call this harness
    # ever makes (its own module docstring: "confirm:true is NEVER sent"), so add_blackboard_key's
    # confirm gate is the ONLY thing reachable here - not just the success path, but the duplicate-
    # name and bad-type checks too, since the handler tests confirm BEFORE either of those and every
    # call this harness sends is missing it. An earlier draft of this test passed "duplicate name"
    # and "bad type" checks that were actually re-triggering the SAME confirm refusal for an unrelated
    # reason - true assertions proving the wrong thing, caught only by noticing the real
    # "needs confirm:true" error text under a check with a different name. Fixed to test only what
    # this harness can actually reach, named accurately.
    print("\n=== T335: add_blackboard_key - confirm-gate only, same limitation as remove_node ===")
    bbpath = "/Game/_MifNodes/BB_%d" % st
    made = M.call("create_asset", {"path": bbpath, "class": "BlackboardData"})
    check("T335 (setup) a scratch BlackboardData is created", made.get("ok") is True, json.dumps(made)[:200])
    if made.get("ok"):
        for label, payload in (
            ("a well-formed add", {"path": bbpath, "name": "TestFlag", "type": "Bool"}),
            ("even a request that would ALSO fail its own validation (bad type)",
             {"path": bbpath, "name": "TestFlag", "type": "NoSuchType_zz"}),
        ):
            q = M.call("add_blackboard_key", payload)
            check("T335 %s refuses on confirm alone (this harness never sends it)" % label,
                  q.get("ok") is False and "confirm" in (q.get("error") or "").lower(), q.get("error"))

        keys = M.call("list_blackboard_keys", {"path": bbpath}).get("keys") or []
        check("T335 and nothing was actually added - the blackboard is unchanged",
              not any(k.get("name") == "TestFlag" for k in keys), keys)

        M.call("delete_asset", {"path": bbpath})

    M.call("delete_asset", {"path": bpath})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("COVERAGE GAP, deliberate: remove_node's and add_blackboard_key's SUCCESS paths are not")
    print("exercised, because both require confirm=true and the audit harness (mifaudit.py's own")
    print("FORBIDDEN_KEYS) never sends it. Both were verified live outside this harness when built -")
    print("see tools/FEATURE_PARITY_SPEC.md - this suite proves the refusal, which is the part safe")
    print("to run unattended forever.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
