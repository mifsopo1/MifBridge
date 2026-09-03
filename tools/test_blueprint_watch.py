"""blueprint_watch - read a pin's value without mutating the asset, and say why when you cannot.

The other half of replacing splice-a-print-node. A breakpoint stops execution; a watch reads a
value. Neither writes anything to disk.

T8501 IS THE ONE THAT JUSTIFIES THE ENDPOINT. GetWatchText returns an ENUM, not a string:

    EWTR_Valid          the value
    EWTR_NoDebugObject  nothing is being debugged - no PIE session, or no instance selected
    EWTR_NotInScope     running, but execution is not where this pin holds anything
    EWTR_NoProperty     the pin has no backing property, so NO session will ever produce a value

Three of the four are "no value, and here is exactly why". Collapsing them into an empty string
would make "you are not running PIE" and "this pin can never have a value" the same answer, and the
difference is the whole reason to ask. The suite asserts the RESULT CODE, never merely that a value
came back - a read that returns nothing is still a correct read when it says which nothing.

T8500 IS THE GUARD THAT WOULD OTHERWISE FAIL SILENTLY, and it fired for real on the first live run.
AddPinWatch accepts any pin and simply produces nothing for one that cannot be watched - so without
CanWatchPin first, watching an exec or wildcard pin reports success and leaves no watch. On a Select
node, ReturnValue can be watched and Index cannot; the endpoint refuses the second with the reason
instead of pretending.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []
RESULTS = ("valid", "noDebugObject", "notInScope", "noProperty")


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

    st = int(time.time()) % 100000
    bp = None
    try:
        made = M.raw_post("create_blueprint", {"path": "/Game/_MifBP/BP_W%d" % st,
                                               "parentClass": "Actor"})
        bp, graph = made.get("blueprintId"), made.get("eventGraphId")
        check("(setup) a scratch blueprint with an event graph", bool(bp and graph),
              json.dumps(made)[:220])
        if not (bp and graph):
            return 1
        n = M.raw_post("add_k2_node", {"graphId": graph, "class": "K2Node_Select",
                                       "x": 100, "y": 100})
        nid = n.get("nodeGuid")
        pins = [p.get("name") for p in ((n.get("node") or {}).get("pins") or [])]
        check("(setup) a node with pins to watch", bool(nid) and len(pins) >= 2, json.dumps(pins))
        if not nid:
            return 1

        # ------------------------------------------------------------------ T8500 CanWatchPin
        print("\n=== T8500: a pin that cannot be watched is REFUSED, not silently ignored ===")
        ok_pin, bad_pin = None, None
        for pin in pins:
            r = M.raw_post("blueprint_watch", {"op": "add", "graphId": graph, "nodeGuid": nid,
                                               "pin": pin})
            if r.get("ok") and ok_pin is None:
                ok_pin = pin
            elif r.get("ok") is False and "cannot be watched" in (r.get("error") or "") \
                    and bad_pin is None:
                bad_pin = (pin, r)
        check("T8500 at least one pin on this node CAN be watched", bool(ok_pin), pins)
        if bad_pin:
            pin, r = bad_pin
            # THE assertion. Without CanWatchPin, AddPinWatch takes the pin, does nothing useful,
            # and the call reports success - a watch that was never created.
            check("T8500 '%s' cannot be watched and is refused rather than accepted-and-ignored"
                  % pin, r.get("ok") is False, json.dumps(r)[:200])
            check("T8500 and the refusal explains that AddPinWatch would have reported success "
                  "while leaving no watch",
                  "report success" in (r.get("error") or ""), (r.get("error") or "")[:250])
        else:
            print("  NOTE  every pin on this node can be watched, so the refusal arm is")
            print("        unexercised here. Reported rather than passed silently.")

        # ------------------------------------------------------------------ T8501 the read
        print("\n=== T8501: a read that has no value says WHICH nothing ===")
        rd = M.raw_post("blueprint_watch", {"op": "read", "graphId": graph, "nodeGuid": nid,
                                            "pin": ok_pin})
        check("T8501 the read answers", rd.get("ok") is True, json.dumps(rd)[:220])
        # A read with no PIE session is a CORRECT read - it must say so with a code, not "".
        check("T8501 and returns a named result code rather than an empty value",
              rd.get("result") in RESULTS, json.dumps(rd)[:220])
        check("T8501 with an explanation attached, so 'not running' and 'never will' are "
              "distinguishable",
              bool(rd.get("note")) or rd.get("result") == "valid",
              json.dumps(rd)[:250])
        if rd.get("result") == "noDebugObject":
            check("T8501 the no-session note says to start PIE and pick an instance",
                  "PIE" in (rd.get("note") or ""), (rd.get("note") or "")[:200])
        if rd.get("result") == "noProperty":
            # This one is permanent, and saying so saves someone starting PIE to find out.
            # Matched on "ever produce", which is the wording, rather than on "never" - the first
            # version asserted a synonym of the note instead of the note.
            check("T8501 the no-property note says NO session will ever produce a value, which "
                  "saves starting one to find out",
                  "ever produce" in (rd.get("note") or ""), (rd.get("note") or "")[:220])
        check("T8501 and it reports the pin is watched, separately from whether it has a value",
              rd.get("watched") is True, json.dumps(rd)[:200])

        # ------------------------------------------------------------------ T8502 lifecycle
        print("\n=== T8502: list, remove and clear, judged by the list ===")
        lst = M.raw_post("blueprint_watch", {"op": "list", "graphId": graph})
        check("T8502 the watch is listed with its pin and owning node",
              lst.get("count") == 1
              and (lst.get("watches") or [{}])[0].get("pin") == ok_pin
              and bool((lst.get("watches") or [{}])[0].get("nodeTitle")),
              json.dumps(lst)[:250])
        again = M.raw_post("blueprint_watch", {"op": "add", "graphId": graph, "nodeGuid": nid,
                                               "pin": ok_pin})
        check("T8502 watching the same pin twice succeeds with created:false",
              again.get("ok") is True and again.get("created") is False, json.dumps(again)[:220])
        rem = M.raw_post("blueprint_watch", {"op": "remove", "graphId": graph, "nodeGuid": nid,
                                             "pin": ok_pin})
        # AddPinWatch/RemovePinWatch do not report whether the list changed - so ask it.
        check("T8502 removing it succeeds and the list is empty afterwards",
              rem.get("ok") is True
              and M.raw_post("blueprint_watch", {"op": "list", "graphId": graph}).get("count") == 0,
              json.dumps(rem)[:220])
        M.raw_post("blueprint_watch", {"op": "add", "graphId": graph, "nodeGuid": nid,
                                       "pin": ok_pin})
        clr = M.raw_post("blueprint_watch", {"op": "clear", "graphId": graph})
        check("T8502 clear reports how many it removed and leaves none",
              clr.get("ok") is True and clr.get("removed") == 1 and clr.get("count") == 0,
              json.dumps(clr)[:220])

        # ---------------------------------------------------------- T8502b clear is not remove
        # THE DESTRUCTIVE ONE. `clear` calls ClearPinWatches(BP) and removes EVERY watch on the
        # blueprint. A caller who writes {"op":"clear","nodeGuid":...,"pin":...} means "clear this
        # one" - the verb for that is `remove` - and used to get every watch deleted with the pin
        # arguments silently dropped and `removed: N` coming back looking like confirmation.
        # Watches are editor-only state that is not saved with the asset, so nothing undoes it and
        # nothing notices.
        #
        # The precondition matters: TWO watches, so a wrong answer is visibly destructive rather
        # than a no-op. Asserted AFTER the refusal, because the point is that nothing was removed.
        M.raw_post("blueprint_watch", {"op": "add", "graphId": graph, "nodeGuid": nid,
                                       "pin": ok_pin})
        before = M.raw_post("blueprint_watch", {"op": "list", "graphId": graph}).get("count")
        check("T8502b (setup) a watch exists to be destroyed by a wrong clear", before == 1, before)
        scoped = M.raw_post("blueprint_watch", {"op": "clear", "graphId": graph,
                                                "nodeGuid": nid, "pin": ok_pin})
        check("T8502b clear with a pin-scoped argument is REFUSED rather than clearing everything",
              scoped.get("ok") is False, json.dumps(scoped)[:220])
        check("T8502b and it says clear is blueprint-wide and names op:remove as the one meant",
              "EVERY watch" in (scoped.get("error") or "")
              and "op:remove" in (scoped.get("error") or ""),
              (scoped.get("error") or "")[:240])
        check("T8502b and the watch is STILL THERE - the refusal is judged by what survived, not "
              "by the error string",
              M.raw_post("blueprint_watch", {"op": "list", "graphId": graph}).get("count") == 1,
              "the refusal came back but the watch was cleared anyway")
        M.raw_post("blueprint_watch", {"op": "clear", "graphId": graph})

        # ------------------------------------------------------------------ T8503 refusals
        print("\n=== T8503: the guards ===")
        nowatch = M.raw_post("blueprint_watch", {"op": "read", "graphId": graph, "nodeGuid": nid,
                                                 "pin": ok_pin})
        check("T8503 reading a pin that is not watched is refused, telling you to add it",
              nowatch.get("ok") is False and "add it first" in (nowatch.get("error") or ""),
              (nowatch.get("error") or "")[:200])
        norem = M.raw_post("blueprint_watch", {"op": "remove", "graphId": graph, "nodeGuid": nid,
                                               "pin": ok_pin})
        check("T8503 removing a watch that is not there is refused",
              norem.get("ok") is False, (norem.get("error") or "")[:200])
        badpin = M.raw_post("blueprint_watch", {"op": "add", "graphId": graph, "nodeGuid": nid,
                                                "pin": "NoSuchPin"})
        check("T8503 an unknown pin name is refused AND the real pin names are listed",
              badpin.get("ok") is False and "It has:" in (badpin.get("error") or ""),
              (badpin.get("error") or "")[:220])
        badop = M.raw_post("blueprint_watch", {"op": "toggle", "graphId": graph})
        check("T8503 an unknown op is refused and names the real ones",
              badop.get("ok") is False
              and "add, remove, list, clear, read" in (badop.get("error") or ""),
              (badop.get("error") or "")[:200])
        val = M.raw_post("blueprint_watch", {"op": "add", "graphId": graph, "nodeGuid": nid,
                                             "pin": ok_pin, "value": 1})
        check("T8503 a 'value' parameter is refused by name - a watch READS",
              val.get("ok") is False and "never sets" in (val.get("error") or ""),
              (val.get("error") or "")[:220])

        check("T8503 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
              "the debugger touches editor-only blueprint state")
    finally:
        if bp:
            SC.confirm_call("delete_asset", {"path": bp})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
