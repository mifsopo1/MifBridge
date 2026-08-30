"""The dispatcher teardown half - unbind and unbindAll.

THE SUBSYSTEM WAS NOT HALF MISSING, which is why this is a parameter rather than a new subsystem.
Declaration (add_event_dispatcher, rename, remove, list_dispatchers), broadcast
(add_call_dispatcher) and bind (add_bind_dispatcher, add_component_bound_event) all shipped. What
was absent is two of the four UK2Node_BaseMCDelegate subclasses, both on the TEARDOWN path - and
there was no workaround at all, because those node classes are the only way to emit those calls.

A PARAMETER, NOT NEW NAMES. All four subclasses take the identical single configuration call,
SetFromProperty, so a new endpoint per node kind would be four spellings of one thing.
add_call_dispatcher keeps its own name because it is already in the MCP tool surface and removing
it would break callers - but it now ANSWERS an op that is not its own rather than silently
broadcasting.

T5801 IS THE ONE WORTH HAVING, and it is about a pin that does not exist. UK2Node_ClearDelegate
creates NO Delegate pin at all (K2Node_MCDelegate.cpp:368-390 gives it a title and a node handler
and nothing else), because clearing removes EVERY binding rather than one named handler. So
op:"unbindAll" comes back with a genuinely different pin set from bind and unbind, and a caller
expecting to wire an event into it would sit there hunting for a pin that was never going to be
there. The suite asserts the difference and that the response says so.

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


def pin_names(resp):
    node = resp.get("node") or {}
    return [p.get("name") for p in (node.get("pins") or [])]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    path = "/Game/_MifDisp/BP_DispTest%d" % (int(time.time()) % 100000)
    made = None
    try:
        c = M.raw_post("create_blueprint", {"path": path,
                                            "parentClass": "/Script/Engine.Actor"})
        check("(setup) a scratch Blueprint", c.get("ok") is True, json.dumps(c)[:200])
        if not c.get("ok"):
            return 1
        made = next((a["path"] for a in
                     (M.call("find_assets", {"pathPrefix": "/Game/_MifDisp",
                                             "limit": 20}).get("assets") or [])
                     if path.rsplit("/", 1)[-1] in a["path"]), None)
        check("(setup) and it is findable", bool(made), made)
        if not made:
            return 1

        d = M.raw_post("add_event_dispatcher", {"blueprintId": made, "name": "MifSignal"})
        check("(setup) a dispatcher to bind to", d.get("ok") is True, json.dumps(d)[:200])
        event = next((g.get("graphId") for g in
                      (M.call("list_graphs", {"blueprintId": made}).get("graphs") or [])
                      if g.get("name") == "EventGraph"), None)
        check("(setup) an event graph", bool(event), event)

        # ------------------------------------------------------------------ T5800 the three ops
        print("\n=== T5800: bind, unbind and unbindAll are three different node classes ===")
        want = {"bind": "K2Node_AddDelegate",
                "unbind": "K2Node_RemoveDelegate",
                "unbindAll": "K2Node_ClearDelegate"}
        got = {}
        for op, cls in want.items():
            r = M.raw_post("add_bind_dispatcher", {"graphId": event, "dispatcher": "MifSignal",
                                                   "op": op})
            got[op] = r
            check("T5800 op=%s places a node" % op, r.get("ok") is True, json.dumps(r)[:200])
            check("T5800 and it is a %s" % cls, (r.get("node") or {}).get("class") == cls,
                  (r.get("node") or {}).get("class"))
            check("T5800 with a title that says what it does",
                  bool((r.get("node") or {}).get("title")), (r.get("node") or {}).get("title"))

        default = M.raw_post("add_bind_dispatcher", {"graphId": event, "dispatcher": "MifSignal"})
        check("T5800 omitting op still means bind, so no existing caller changed behaviour",
              (default.get("node") or {}).get("class") == "K2Node_AddDelegate",
              (default.get("node") or {}).get("class"))

        # ------------------------------------------------------------------ T5801 the missing pin
        print("\n=== T5801: unbindAll has NO Delegate pin, and that is not a bug ===")
        bind_pins = pin_names(got["bind"])
        unbind_pins = pin_names(got["unbind"])
        clear_pins = pin_names(got["unbindAll"])
        print("        bind      %s" % bind_pins)
        print("        unbind    %s" % unbind_pins)
        print("        unbindAll %s" % clear_pins)
        check("T5801 bind has a Delegate pin to wire an event into", "Delegate" in bind_pins,
              bind_pins)
        check("T5801 unbind has one too - it removes ONE named handler", "Delegate" in unbind_pins,
              unbind_pins)
        # THE assertion. Clearing removes every binding, so there is no handler to name - and a
        # caller hunting for a pin that was never going to exist is exactly what this prevents.
        check("T5801 unbindAll has NONE - it removes EVERY binding, so there is nothing to name",
              "Delegate" not in clear_pins, clear_pins)
        check("T5801 and the response says so rather than leaving it to be discovered",
              "NO Delegate pin" in (got["unbindAll"].get("pinNote") or ""),
              (got["unbindAll"].get("pinNote") or "")[:200])
        check("T5801 the note appears ONLY on unbindAll",
              got["bind"].get("pinNote") is None and got["unbind"].get("pinNote") is None,
              "bind=%s unbind=%s" % (got["bind"].get("pinNote"), got["unbind"].get("pinNote")))

        # ------------------------------------------------------------------ T5802 routing
        print("\n=== T5802: each endpoint answers an op that is not its own ===")
        call_here = M.raw_post("add_bind_dispatcher", {"graphId": event,
                                                       "dispatcher": "MifSignal", "op": "call"})
        check("T5802 op=call on the BIND endpoint is routed, not silently bound",
              call_here.get("ok") is False
              and "add_call_dispatcher" in (call_here.get("error") or ""),
              (call_here.get("error") or "")[:220])
        unbind_there = M.raw_post("add_call_dispatcher", {"graphId": event,
                                                          "dispatcher": "MifSignal",
                                                          "op": "unbind"})
        # Without this the CALL endpoint would accept op (the guard whitelists it) and quietly
        # broadcast - the worst outcome, since the caller asked to unbind.
        check("T5802 op=unbind on the CALL endpoint is refused rather than quietly broadcasting",
              unbind_there.get("ok") is False
              and "add_bind_dispatcher" in (unbind_there.get("error") or ""),
              (unbind_there.get("error") or "")[:220])
        bad = M.raw_post("add_bind_dispatcher", {"graphId": event, "dispatcher": "MifSignal",
                                                 "op": "detonate"})
        check("T5802 an unknown op is refused with the accepted list",
              bad.get("ok") is False and "unbindAll" in (bad.get("error") or ""),
              (bad.get("error") or "")[:200])

        # ------------------------------------------------------------------ T5803 compile
        print("\n=== T5803: all four node kinds coexist and compile ===")
        comp = M.raw_post("compile", {"blueprintId": made})
        check("T5803 the Blueprint compiles with bind, unbind and unbindAll nodes in it",
              comp.get("ok") is True, json.dumps(comp)[:220])
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": made})
        left = [a["path"] for a in
                (M.call("find_assets", {"pathPrefix": "/Game/_MifDisp"}).get("assets") or [])
                if made and made in a["path"]]
        check("T5804 (cleanup) the scratch Blueprint is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
