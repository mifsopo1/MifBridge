"""add_k2_node - the generic adder, built instead of the add_async_action that was asked for.

WHY THIS SHAPE. The backlog item was add_async_action. The vetting pointed at
docs/06_CAPABILITY_ROADMAP.md:92, which frames that as one symptom of "no generic add-node-by-class"
alongside UK2Node_Select and GenericCreateObject - so a narrow add_async_action would have left its
siblings out for the same day of work. This plugin has forty-odd class-specific add_* endpoints,
each existing because its node needs real class-specific configuration. What was missing is the case
where a node needs only construction plus a couple of reflective writes: a long tail nobody will
ever build one endpoint at a time.

T5701 IS THE PROOF THE DESIGN PAID OFF: the same endpoint places an async node AND a
UK2Node_Select, which was the sibling gap the narrow item would have left open.

T5700 IS THE ASYNC CONFIGURATION, and `title` is the honest read on whether it took. An async node
whose factory did not resolve does NOT crash - the engine is null-tolerant - it titles itself
"Async Task: Missing Function" and carries almost no pins. So the suite asserts the real title and
a pin count high enough to include the proxy's delegate outputs, which is the structure that cannot
be synthesised from ordinary call nodes.

TWO CORRECTIONS THAT SHAPED THE GUARDS:
  - The crash justification in the original item was FALSE. 5.7 uses ensure(), not check(), and 5.3
    is null-tolerant throughout. The factory function is still validated, because refusing beats
    emitting a dead node - but as a quality guard, not a crash guard, and the code says so rather
    than inheriting a scary story that is not true.
  - UK2Node_BaseAsyncTask::IsCompatibleWithGraph allows GT_Ubergraph and GT_Macro only, so a
    function graph is refused BEFORE the node is made. Otherwise it lands and the compiler rejects
    it later, far from the call that caused it.

ProxyActivateFunctionName is deliberately NOT written: the constructor already set it, and a
subclass that overrides its activate function would be silently broken by writing it here. The
suite checks only the three the engine's own spawner sets appear in `configured`.

CLEANS UP: the scratch Blueprint is deleted at the end.
"""
import json
import sys
import time

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
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    path = "/Game/_MifK2/BP_K2Test%d" % (int(time.time()) % 100000)
    made = None
    try:
        c = M.raw_post("create_blueprint", {"path": path,
                                            "parentClass": "/Script/Engine.Actor"})
        check("(setup) a scratch Blueprint", c.get("ok") is True, json.dumps(c)[:200])
        if not c.get("ok"):
            return 1
        found = [a["path"] for a in
                 (M.call("find_assets", {"pathPrefix": "/Game/_MifK2",
                                         "limit": 10}).get("assets") or [])
                 if path.rsplit("/", 1)[-1] in a["path"]]
        made = found[0] if found else None
        check("(setup) and it is findable", bool(made), found)
        if not made:
            return 1

        graphs = M.call("list_graphs", {"blueprintId": made}).get("graphs") or []
        event = next((g.get("graphId") for g in graphs if g.get("name") == "EventGraph"), None)
        func = next((g.get("graphId") for g in graphs
                     if g.get("name") == "UserConstructionScript"), None)
        check("(setup) an event graph and a function graph to aim at", bool(event) and bool(func),
              [g.get("name") for g in graphs])

        # ------------------------------------------------------------------ T5700 async
        print("\n=== T5700: an async node, configured the way the engine's own spawner does ===")
        r = M.raw_post("add_k2_node", {
            "graphId": event, "nodeClass": "K2Node_AsyncAction",
            "proxyFactoryFunction": "AsyncLoadPrimaryAsset",
            "proxyFactoryClass": "AsyncActionLoadPrimaryAsset"})
        check("T5700 the node is placed", r.get("ok") is True, json.dumps(r)[:250])
        # THE assertion. A node whose factory did not resolve does not fail - it titles itself
        # "Async Task: Missing Function". The real title is the only honest proof.
        check("T5700 and it resolved - the title is the real one, not 'Missing Function'",
              r.get("title") == "Async Load Primary Asset", r.get("title"))
        check("T5700 it carries the proxy's delegate output pins, which is the structure that "
              "cannot be synthesised from ordinary call nodes",
              (r.get("pinCount") or 0) >= 5, r.get("pinCount"))
        cfg = r.get("configured") or []
        check("T5700 exactly the three properties the engine's spawner sets were written",
              sorted(cfg) == ["ProxyClass", "ProxyFactoryClass", "ProxyFactoryFunctionName"],
              json.dumps(cfg))
        # ProxyActivateFunctionName is set by the constructor; writing it would break a subclass
        # that overrides its activate function.
        check("T5700 and ProxyActivateFunctionName was NOT touched",
              "ProxyActivateFunctionName" not in cfg, json.dumps(cfg))
        check("T5700 the response explains how to tell a dead node from a live one",
              "Missing Function" in (r.get("asyncNote") or ""), (r.get("asyncNote") or "")[:200])

        # ------------------------------------------------------------------ T5701 the siblings
        print("\n=== T5701: the same endpoint closes the gaps a narrow one would have left ===")
        sel = M.raw_post("add_k2_node", {"graphId": event, "nodeClass": "K2Node_Select"})
        check("T5701 UK2Node_Select places through the same endpoint",
              sel.get("ok") is True and sel.get("title") == "Select", json.dumps(sel)[:220])
        check("T5701 and allocated its own pins", (sel.get("pinCount") or 0) > 0,
              sel.get("pinCount"))
        # This is the whole argument for the generic shape over add_async_action.
        check("T5701 - two unrelated node classes, one endpoint, which is why this was built "
              "generic rather than as add_async_action",
              r.get("ok") is True and sel.get("ok") is True,
              "async=%s select=%s" % (r.get("ok"), sel.get("ok")))

        # ------------------------------------------------------------------ T5705 legacy input
        print("\n=== T5705: the legacy input nodes, which needed no endpoint of their own ===")
        # add_input_event was a separate backlog item (K2Node_InputKey / InputAction /
        # InputAxisEvent / InputTouch). All four are configured ENTIRELY through UPROPERTYs, which
        # is exactly what this endpoint's `properties` map applies before pin allocation - so the
        # item was DECLINED and these checks are the evidence, kept here so nobody proposes it
        # again without first seeing it already works.
        inputs = [
            ("K2Node_InputKey", {"InputKey": "SpaceBar"}, "Space Bar"),
            ("K2Node_InputAction", {"InputActionName": "Jump"}, "InputAction Jump"),
            ("K2Node_InputAxisEvent", {"InputAxisName": "MoveForward"}, "InputAxis MoveForward"),
            ("K2Node_InputTouch", None, "InputTouch"),
        ]
        for cls, props, expect_title in inputs:
            payload = {"graphId": event, "nodeClass": cls}
            if props:
                payload["properties"] = props
            n = M.raw_post("add_k2_node", payload)
            check("T5705 %s places" % cls, n.get("ok") is True, json.dumps(n)[:200])
            # The TITLE is the proof the property actually configured the node - an InputKey with
            # no key set titles itself differently from one bound to Space Bar.
            check("T5705 and titles itself '%s', so the property took" % expect_title,
                  n.get("title") == expect_title, n.get("title"))
            check("T5705 with real pins", (n.get("pinCount") or 0) >= 3, n.get("pinCount"))

        # ------------------------------------------------------------------ T5702 the guards
        print("\n=== T5702: refusing before the node exists ===")
        wrong = M.raw_post("add_k2_node", {
            "graphId": func, "nodeClass": "K2Node_AsyncAction",
            "proxyFactoryFunction": "AsyncLoadPrimaryAsset",
            "proxyFactoryClass": "AsyncActionLoadPrimaryAsset"})
        check("T5702 an async node in a FUNCTION graph is refused before it is made",
              wrong.get("ok") is False and "event graph or a macro" in (wrong.get("error") or ""),
              (wrong.get("error") or "")[:220])
        nofac = M.raw_post("add_k2_node", {"graphId": event, "nodeClass": "K2Node_AsyncAction"})
        check("T5702 an async node with no factory is refused rather than placed dead",
              nofac.get("ok") is False and "Missing Function" in (nofac.get("error") or ""),
              (nofac.get("error") or "")[:220])
        badfn = M.raw_post("add_k2_node", {
            "graphId": event, "nodeClass": "K2Node_AsyncAction",
            "proxyFactoryFunction": "NoSuchFactoryFunction",
            "proxyFactoryClass": "AsyncActionLoadPrimaryAsset"})
        check("T5702 an unknown factory function is refused and real statics are listed",
              badfn.get("ok") is False and "static functions include" in (badfn.get("error") or ""),
              (badfn.get("error") or "")[:220])

        notk2 = M.raw_post("add_k2_node", {"graphId": event, "nodeClass": "Actor"})
        check("T5702 a class that is not a UK2Node is refused",
              notk2.get("ok") is False and "not a UK2Node" in (notk2.get("error") or ""),
              (notk2.get("error") or "")[:200])
        dedicated = M.raw_post("add_k2_node", {"graphId": event,
                                               "nodeClass": "K2Node_CallFunction"})
        check("T5702 a class with a purpose-built endpoint is refused, naming it",
              dedicated.get("ok") is False
              and "add_function_call" in (dedicated.get("error") or ""),
              (dedicated.get("error") or "")[:220])
        # That refusal is the anti-parallel-system guard: the generic adder must not become a
        # worse way to do something a specific endpoint already does properly.
        check("T5702 and says WHY the specific one is better rather than just refusing",
              "resolving overloads" in (dedicated.get("error") or ""),
              (dedicated.get("error") or "")[:220])

        badprop = M.raw_post("add_k2_node", {"graphId": event, "nodeClass": "K2Node_Select",
                                             "properties": {"NoSuchProperty": 1}})
        check("T5702 an unknown property discards the node rather than half-configuring it",
              badprop.get("ok") is False and "NOTHING was added" in (badprop.get("error") or ""),
              (badprop.get("error") or "")[:220])

        # ------------------------------------------------------------------ T5703 compile
        print("\n=== T5703: the graph still compiles with these nodes in it ===")
        comp = M.raw_post("compile", {"blueprintId": made})
        check("T5703 the Blueprint compiles", comp.get("ok") is True, json.dumps(comp)[:220])
        alive = M.call("self_audit", {})
        check("T5703 - the editor is still alive", alive.get("ok") is True,
              "node construction runs engine code with reflective writes before pin allocation")
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": made})
        left = [a["path"] for a in
                (M.call("find_assets", {"pathPrefix": "/Game/_MifK2"}).get("assets") or [])
                if made and made in a["path"]]
        check("T5704 (cleanup) the scratch Blueprint is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
