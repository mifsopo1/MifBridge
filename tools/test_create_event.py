"""add_create_event - and an ordering that silently erases what you just set.

WHAT IS ACTUALLY MISSING, narrowed from what the item claimed. "The only way to bind an inherited
event" is false: UK2Node_Event::AllocateDefaultPins creates the PC_Delegate OutputDelegate pin on
EVERY event node (K2Node_Event.cpp:104-106), and add_override_event already spawns UK2Node_Event -
so inherited and override events in the ubergraph are bindable today with add_override_event +
connect_pins. The two cases genuinely uncovered are binding an ordinary existing FUNCTION, and
binding from inside a function or macro graph where no event node can exist.

T5901 IS THE ASSERTION THE WHOLE ENDPOINT SHAPE EXISTS FOR, and it is about something that leaves
no trace when it goes wrong. UK2Node_CreateDelegate::HandleAnyChangeWithoutNotifying ends with:

    if (DelegatePin->LinkedTo.Num() == 0) { SelectedFunctionName = NAME_None; }
    SelectedFunctionGuid.Invalidate();

and reaches that branch whenever IsValid() fails - which on a freshly placed, UNCONNECTED node is
always, because GetDelegateSignature returns nullptr unless the delegate pin is linked. So the
obvious sequence - place, SetFunction, HandleAnyChange - silently ERASES the function it just set
and leaves a node that looks fine. This endpoint therefore takes the destination and makes the
CONNECTION FIRST. The suite asserts the function reads back as the one requested, which is the only
evidence the ordering worked.

IsValid IS NOT CALLABLE FROM A PLUGIN - declared without BLUEPRINTGRAPH_API on a MinimalAPI class
and defined out-of-line, so it will not link. Validation goes through the exported
GetDelegateSignature() plus that read-back.

THERE IS NO scopeClass PARAMETER, and T5902 asserts it is refused rather than accepted. GetScopeClass
derives the scope entirely from what is wired into the Self pin; there is no setter and no UPROPERTY
behind it, so a scopeClass argument would be silently ignored - the exact failure RejectUnknownParams
exists to prevent.

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

    path = "/Game/_MifCE/BP_CETest%d" % (int(time.time()) % 100000)
    made = None
    try:
        c = M.raw_post("create_blueprint", {"path": path,
                                            "parentClass": "/Script/Engine.Actor"})
        check("(setup) a scratch Blueprint", c.get("ok") is True, json.dumps(c)[:200])
        if not c.get("ok"):
            return 1
        made = next((a["path"] for a in
                     (M.call("find_assets", {"pathPrefix": "/Game/_MifCE",
                                             "limit": 20}).get("assets") or [])
                     if path.rsplit("/", 1)[-1] in a["path"]), None)
        check("(setup) and it is findable", bool(made), made)
        if not made:
            return 1

        M.raw_post("add_event_dispatcher", {"blueprintId": made, "name": "MifSig"})
        M.raw_post("create_function", {"blueprintId": made, "name": "MifHandler"})
        # A compile is needed so the function exists on the generated class to be found by name.
        M.raw_post("compile", {"blueprintId": made})
        event = next((g.get("graphId") for g in
                      (M.call("list_graphs", {"blueprintId": made}).get("graphs") or [])
                      if g.get("name") == "EventGraph"), None)
        bind = M.raw_post("add_bind_dispatcher", {"graphId": event, "dispatcher": "MifSig",
                                                  "op": "bind"})
        check("(setup) a bind node to feed", bind.get("ok") is True, json.dumps(bind)[:200])
        guid = bind.get("nodeGuid")

        # ------------------------------------------------------------------ T5900 the node
        print("\n=== T5900: wrapping an ordinary function as an event ===")
        r = M.raw_post("add_create_event", {"graphId": event, "function": "MifHandler",
                                            "bindNode": guid})
        check("T5900 add_create_event succeeds", r.get("ok") is True, json.dumps(r)[:250])
        check("T5900 it connected itself to the bind node", r.get("connected") is True,
              json.dumps(r)[:220])
        check("T5900 and reports which pin it fed", "Delegate" in (r.get("boundTo") or ""),
              r.get("boundTo"))
        check("T5900 the node really is a CreateDelegate",
              (r.get("node") or {}).get("class") == "K2Node_CreateDelegate",
              (r.get("node") or {}).get("class"))

        # ------------------------------------------------------------------ T5901 the erase
        print("\n=== T5901: the function must SURVIVE HandleAnyChange ===")
        # THE assertion. Had this endpoint used the obvious order - place, SetFunction,
        # HandleAnyChange - the function would come back as None, silently, with a node that
        # looks fine.
        check("T5901 the function reads back as the one requested, which is the only proof the "
              "connect-first ordering worked",
              r.get("function") == "MifHandler", r.get("function"))
        check("T5901 and the delegate signature resolved from the connection",
              r.get("signatureResolved") is True, r.get("signatureResolved"))
        check("T5901 the response explains the scope came from the Self pin, not a parameter",
              "Self pin" in (r.get("scopeNote") or ""), (r.get("scopeNote") or "")[:180])

        # ------------------------------------------------------------------ T5902 the refusals
        print("\n=== T5902: parameters and targets that cannot work ===")
        scope = M.raw_post("add_create_event", {"graphId": event, "function": "MifHandler",
                                                "bindNode": guid, "scopeClass": "Actor"})
        check("T5902 scopeClass is REFUSED rather than silently ignored",
              scope.get("ok") is False and "silently ignored" in (scope.get("error") or ""),
              (scope.get("error") or "")[:220])

        nofn = M.raw_post("add_create_event", {"graphId": event, "function": "NoSuchFunction",
                                               "bindNode": guid})
        check("T5902 an unknown function is refused and real ones listed",
              nofn.get("ok") is False and "MifHandler" in (nofn.get("error") or ""),
              (nofn.get("error") or "")[:220])

        # A ClearDelegate node has no Delegate pin at all - which is the thing the dispatcher-op
        # work documented, and this is where a caller would actually trip over it.
        clear = M.raw_post("add_bind_dispatcher", {"graphId": event, "dispatcher": "MifSig",
                                                   "op": "unbindAll"})
        nopin = M.raw_post("add_create_event", {"graphId": event, "function": "MifHandler",
                                                "bindNode": clear.get("nodeGuid")})
        check("T5902 binding into an unbindAll node is refused - it has no Delegate pin",
              nopin.get("ok") is False and "no pin 'Delegate'" in (nopin.get("error") or ""),
              (nopin.get("error") or "")[:220])
        check("T5902 and the refusal explains WHY that node has none",
              "clears every binding" in (nopin.get("error") or ""),
              (nopin.get("error") or "")[:250])

        nonode = M.raw_post("add_create_event", {"graphId": event, "function": "MifHandler"})
        check("T5902 a missing bindNode is refused - the connection is not optional here",
              nonode.get("ok") is False, (nonode.get("error") or "")[:200])

        # ------------------------------------------------------------------ T5903 compile
        print("\n=== T5903: the wiring is real, so it compiles ===")
        comp = M.raw_post("compile", {"blueprintId": made})
        check("T5903 the Blueprint compiles with the bound event in it",
              comp.get("ok") is True, json.dumps(comp)[:220])
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": made})
        left = [a["path"] for a in
                (M.call("find_assets", {"pathPrefix": "/Game/_MifCE"}).get("assets") or [])
                if made and made in a["path"]]
        check("T5904 (cleanup) the scratch Blueprint is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
