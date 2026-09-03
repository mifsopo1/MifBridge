"""blueprint_breakpoint - set, clear and list Blueprint breakpoints without editing the asset.

WHAT IT REPLACES. To see one value today an agent must splice a print node into the graph, compile,
run PIE, read the log, then unpick the edit - four mutations of somebody's blueprint to answer a
read-only question, any of which can be left behind. A breakpoint changes editor-only state and
nothing on disk.

EVERY ENGINE CALL BEHIND THIS RETURNS void. CreateBreakpoint, RemoveBreakpointFromNode,
SetBreakpointEnabled and ClearBreakpoints all report nothing at all, so nothing here may be judged
by "the call returned". Every op is checked with FindBreakpointForNode afterwards - T8400 and T8401
assert the STATE, never the call.

T8402 IS THE ONE THAT KEEPS THE VERBS HONEST. enable and disable refuse when there is no breakpoint
rather than quietly creating one: a boolean that also means "create it" turns a typo'd node guid
into a new breakpoint somewhere the caller never looked. Likewise `add` on a node that already has
one reports created:false instead of failing, because "already set" and "just set" are different
answers and both are fine.

AND THE STATE IS NOT SAVED, which every response says. Breakpoints live on the loaded UBlueprint and
vanish with the editor session, so "it worked and was gone tomorrow" is the design rather than a bug
to chase. A suite that did not assert that would let the note rot.
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

    st = int(time.time()) % 100000
    bp = None
    try:
        made = M.raw_post("create_blueprint", {"path": "/Game/_MifBP/BP_BK%d" % st,
                                               "parentClass": "Actor"})
        bp = made.get("blueprintId")
        graph = made.get("eventGraphId")
        check("(setup) a scratch blueprint with an event graph", bool(bp and graph),
              json.dumps(made)[:220])
        if not (bp and graph):
            return 1
        n1 = M.raw_post("add_k2_node", {"graphId": graph, "class": "K2Node_Select",
                                        "x": 100, "y": 100})
        n2 = M.raw_post("add_k2_node", {"graphId": graph, "class": "K2Node_Select",
                                        "x": 400, "y": 100})
        a, b = n1.get("nodeGuid"), n2.get("nodeGuid")
        check("(setup) two nodes to hang breakpoints on", bool(a and b),
              "%s / %s" % (a, b))
        if not (a and b):
            return 1

        # ------------------------------------------------------------------ T8400 the lifecycle
        print("\n=== T8400: add, list, disable, remove - each judged by the STATE ===")
        add = M.raw_post("blueprint_breakpoint", {"op": "add", "graphId": graph, "nodeGuid": a})
        check("T8400 adding a breakpoint succeeds", add.get("ok") is True, json.dumps(add)[:220])
        check("T8400 and reports it was newly created", add.get("created") is True,
              json.dumps(add)[:200])
        check("T8400 enabled by default", add.get("enabled") is True, json.dumps(add)[:200])
        # THE assertion: CreateBreakpoint returns void, so the list is the only evidence.
        lst = M.raw_post("blueprint_breakpoint", {"op": "list", "graphId": graph})
        check("T8400 the list shows exactly one, on the node that was named",
              lst.get("count") == 1
              and (lst.get("breakpoints") or [{}])[0].get("nodeGuid") == a,
              json.dumps(lst)[:260])
        check("T8400 and carries the node's title and graph, so the list is readable without a "
              "second lookup",
              bool((lst.get("breakpoints") or [{}])[0].get("nodeTitle"))
              and bool((lst.get("breakpoints") or [{}])[0].get("graph")),
              json.dumps(lst)[:260])

        dis = M.raw_post("blueprint_breakpoint", {"op": "disable", "graphId": graph, "nodeGuid": a})
        check("T8400 disabling succeeds and reports the new state",
              dis.get("ok") is True and dis.get("enabled") is False, json.dumps(dis)[:220])
        lst2 = M.raw_post("blueprint_breakpoint", {"op": "list", "graphId": graph})
        check("T8400 and the LIST agrees it is disabled - the state, not the call's word",
              (lst2.get("breakpoints") or [{}])[0].get("enabled") is False,
              json.dumps(lst2)[:220])

        en = M.raw_post("blueprint_breakpoint", {"op": "enable", "graphId": graph, "nodeGuid": a})
        check("T8400 enabling puts it back", en.get("ok") is True and en.get("enabled") is True,
              json.dumps(en)[:200])

        rem = M.raw_post("blueprint_breakpoint", {"op": "remove", "graphId": graph, "nodeGuid": a})
        check("T8400 removing succeeds", rem.get("ok") is True, json.dumps(rem)[:200])
        check("T8400 and the list is empty afterwards",
              M.raw_post("blueprint_breakpoint", {"op": "list", "graphId": graph}).get("count") == 0,
              "still listed after removal")

        # ------------------------------------------------------------------ T8401 clear
        print("\n=== T8401: clear removes all of them and says how many ===")
        M.raw_post("blueprint_breakpoint", {"op": "add", "graphId": graph, "nodeGuid": a})
        M.raw_post("blueprint_breakpoint", {"op": "add", "graphId": graph, "nodeGuid": b})
        check("T8401 two breakpoints are set",
              M.raw_post("blueprint_breakpoint", {"op": "list", "graphId": graph}).get("count") == 2,
              "expected two")
        # T8401b, BEFORE the real clear and with TWO breakpoints standing, so a wrong answer is
        # visibly destructive rather than a no-op. `clear` is blueprint-wide: {"op":"clear",
        # "nodeGuid":X} reads as "clear the breakpoint on this node", the node argument was dropped
        # in silence, and the caller lost every other breakpoint they had set. Same guard and same
        # harm as blueprint_watch T8502b.
        scoped = M.raw_post("blueprint_breakpoint", {"op": "clear", "graphId": graph,
                                                     "nodeGuid": a})
        check("T8401b clear with a nodeGuid is REFUSED rather than clearing everything",
              scoped.get("ok") is False, json.dumps(scoped)[:220])
        check("T8401b and it names op:remove as the verb that was meant",
              "EVERY breakpoint" in (scoped.get("error") or "")
              and "op:remove" in (scoped.get("error") or ""),
              (scoped.get("error") or "")[:240])
        check("T8401b and BOTH breakpoints survived - judged by what is still there, not by the "
              "error string, which a handler that refused and cleared anyway would also produce",
              M.raw_post("blueprint_breakpoint", {"op": "list", "graphId": graph}).get("count") == 2,
              "the refusal came back but the breakpoints were cleared anyway")

        clr = M.raw_post("blueprint_breakpoint", {"op": "clear", "graphId": graph})
        check("T8401 clear succeeds and reports how many it removed",
              clr.get("ok") is True and clr.get("removed") == 2, json.dumps(clr)[:220])
        # ClearBreakpoints returns void too - the handler recounts, and so does this.
        check("T8401 and none remain",
              M.raw_post("blueprint_breakpoint", {"op": "list", "graphId": graph}).get("count") == 0,
              "breakpoints survived clear")

        # ------------------------------------------------------------------ T8402 the verbs
        print("\n=== T8402: verbs that refuse rather than doing something adjacent ===")
        # A boolean that also means "create it" turns a typo'd guid into a new breakpoint.
        for verb in ("enable", "disable"):
            r = M.raw_post("blueprint_breakpoint", {"op": verb, "graphId": graph, "nodeGuid": a})
            check("T8402 '%s' on a node with no breakpoint is REFUSED, not silently creating one"
                  % verb,
                  r.get("ok") is False and "add one first" in (r.get("error") or ""),
                  (r.get("error") or "")[:220])
        rr = M.raw_post("blueprint_breakpoint", {"op": "remove", "graphId": graph, "nodeGuid": a})
        check("T8402 removing one that is not there is refused",
              rr.get("ok") is False and "no breakpoint to remove" in (rr.get("error") or ""),
              (rr.get("error") or "")[:200])

        M.raw_post("blueprint_breakpoint", {"op": "add", "graphId": graph, "nodeGuid": a})
        twice = M.raw_post("blueprint_breakpoint", {"op": "add", "graphId": graph, "nodeGuid": a})
        # "already set" and "just set" are different answers, and both are fine - so this succeeds
        # while saying which happened.
        check("T8402 adding twice succeeds but reports created:false, so 'already set' and 'just "
              "set' are distinguishable",
              twice.get("ok") is True and twice.get("created") is False, json.dumps(twice)[:220])

        bad = M.raw_post("blueprint_breakpoint", {"op": "toggle", "graphId": graph, "nodeGuid": a})
        check("T8402 an unknown op is refused and lists the real ones",
              bad.get("ok") is False and "add, remove, enable, disable, list, clear"
              in (bad.get("error") or ""), (bad.get("error") or "")[:220])
        line = M.raw_post("blueprint_breakpoint", {"op": "add", "graphId": graph, "nodeGuid": a,
                                                   "line": 12})
        check("T8402 a 'line' parameter is refused by name - breakpoints sit on a NODE",
              line.get("ok") is False and "NODE" in (line.get("error") or ""),
              (line.get("error") or "")[:220])

        # ------------------------------------------------------------------ T8403 not saved
        print("\n=== T8403: the state is editor-only, and every response says so ===")
        st_r = M.raw_post("blueprint_breakpoint", {"op": "list", "graphId": graph})
        check("T8403 the response states breakpoints are not saved with the asset",
              "not saved" in (st_r.get("note") or ""), (st_r.get("note") or "")[:200])
        check("T8403 and that losing them on restart is expected rather than a defect",
              "expected" in (st_r.get("note") or ""), (st_r.get("note") or "")[:220])

        check("T8403 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
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
