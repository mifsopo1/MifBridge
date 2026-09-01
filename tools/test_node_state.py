"""set_node_state - disabling a node, and the three things about it that are easy to get wrong.

WHY THIS SUITE IS SHARPER THAN "it returned ok". Three separate ways this endpoint can appear to
work and not:

  * SetEnabledState(bUserAction=false) writes the state and the editor REVERTS it on the next
    compile, so the call succeeds, a read straight afterwards agrees, and the graph is unchanged by
    the time anyone looks. So this suite compiles the blueprint and re-reads.
  * A disabled node must keep its PINS AND LINKS. That is the entire difference from deleting it,
    and a "disable" that quietly severed connections would pass any check that only asked about the
    enabled flag.
  * developmentOnly is a THIRD state. A bool-shaped implementation collapses it to enabled, which
    is invisible until something ships with a debug print in it.

Usage:  python tools/test_node_state.py
Exit:   0 passed   1 failed   2 SKIPPED, no bridge
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))


def bail(why):
    """Exit a setup failure LOUDLY.

    The first run of this suite exited 1 having printed NOTHING AT ALL, which reads exactly like a
    crash and sent me looking at the editor. It was not a crash: check() only accumulates, so the
    three `return 1` setup guards below discarded every failure they had just recorded.

    A suite that dies mute is worse than one that dies red - the failure it was holding said
    `add_call_function is not an endpoint on this build (453 are registered)`, with a didYouMean
    naming add_function_call. The bridge had diagnosed the bug perfectly and the harness threw the
    message away.
    """
    print("SETUP FAILED: %s" % why)
    for f in FAIL:
        if isinstance(f, tuple):
            print("  FAILED: %s\n          %s" % f)
    print("%d setup check(s) passed before this." % len(PASS))
    return 1


def main():
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2

    import time
    st = int(time.time() % 100000)
    bpath = "/Game/_MifNodeState/BP_NS_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    if not bid:
        return bail("create_blueprint returned no blueprintId")
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    graph = next((g.get("graphId") for g in graphs if "EventGraph" in (g.get("name") or "")), None)
    check("N600 (setup) the fixture has an event graph", bool(graph), graphs)
    if not graph:
        return bail("no EventGraph in list_graphs")

    # A print node wired to BeginPlay - the exact thing somebody disables while debugging.
    # add_function_call, NOT add_call_function - the first draft of this suite had the words the
    # other way round and the bridge said so, with a didYouMean. PrintString lives on
    # KismetSystemLibrary and the class has to be named or the lookup has nowhere to start.
    pr = M.call("add_function_call", {"graphId": graph, "class": "KismetSystemLibrary",
                                      "function": "PrintString", "x": 400, "y": 0})
    guid = pr.get("nodeGuid") or (pr.get("node") or {}).get("guid")
    check("N600 (setup) a PrintString node exists", bool(guid), json.dumps(pr)[:200])
    if not guid:
        return bail("add_function_call produced no node")

    nodes = M.call("list_nodes", {"graphId": graph}).get("nodes") or []
    begin = next((n.get("guid") for n in nodes
                  if "BeginPlay" in str(n.get("title") or "")), None)
    linked = False
    if begin:
        # srcNode/dstNode, NOT fromNode/toNode. connect_pins takes fromPin and toPin as aliases
        # but has no from/to alias for the NODE, so the obvious symmetric spelling is refused -
        # which is how this suite's setup failed the first time it ran for real.
        c = M.call("connect_pins", {"graphId": graph, "srcNode": begin, "srcPin": "then",
                                    "dstNode": guid, "dstPin": "execute"})
        linked = c.get("ok") is not False
        if not linked:
            print("  connect_pins refused: %s" % str(c.get("error"))[:200])
    check("N600 (setup) it is wired to BeginPlay", linked, "needed for the link-survival check")

    def links_on(g):
        for n in (M.call("list_nodes", {"graphId": graph}).get("nodes") or []):
            if n.get("guid") != g:
                continue
            return sum(len(p.get("linkedTo") or []) for p in (n.get("pins") or []))
        return -1

    links_before = links_on(guid)
    check("N600 (setup) the node has at least one link to lose", links_before > 0, links_before)

    # ---------------------------------------------------------------- N601 disable
    print("=== N601: disabling reports the change, and is read back off the node ===")

    # A PrintString NODE IS BORN developmentOnly, and this suite asserts it rather than working
    # around it. KismetSystemLibrary::PrintString is declared
    #     UFUNCTION(BlueprintCallable, meta=(... DevelopmentOnly), Category="Development")
    # so K2Node_CallFunction creates the node already in ENodeEnabledState::DevelopmentOnly. The
    # first draft of this suite assumed "enabled" and read back "developmentOnly", which looked
    # like the endpoint inventing a state and was in fact the endpoint reporting the truth.
    #
    # Worth an assertion because it is the one state a bool-shaped implementation cannot express,
    # and here the engine hands it to us for free on the most common debug node there is.
    zero = M.call("set_node_state", {"node": guid, "comment": "before anything"})
    check("N601 a PrintString node starts developmentOnly - the engine flags the UFUNCTION so",
          zero.get("enabledBefore") == "developmentOnly", zero.get("enabledBefore"))

    on = M.call("set_node_state", {"node": guid, "enabled": "enabled"})
    check("N601 (setup) it can be turned fully on first", on.get("enabledAfter") == "enabled",
          on.get("enabledAfter"))

    r = M.call("set_node_state", {"node": guid, "enabled": "disabled"})
    check("N601 set_node_state succeeds", r.get("ok") is not False, json.dumps(r)[:220])
    check("N601 enabledBefore was enabled", r.get("enabledBefore") == "enabled",
          r.get("enabledBefore"))
    check("N601 enabledAfter is disabled", r.get("enabledAfter") == "disabled",
          r.get("enabledAfter"))
    check("N601 and it says the state actually moved", r.get("enabledChanged") is True, r)
    check("N601 the response explains disabled is not deleted",
          "not deleted" in str(r.get("disabledNote", "")).lower(), r.get("disabledNote"))

    # THE CHECK THAT SEPARATES DISABLE FROM DELETE.
    check("N601 the node KEPT every link - that is the whole difference from deleting it",
          links_on(guid) == links_before,
          "before %r after %r" % (links_before, links_on(guid)))

    # ---------------------------------------------------------------- N602 survives a compile
    print("")
    print("=== N602: it survives a compile, which bUserAction=false would not ===")
    comp = M.call("compile", {"blueprintId": bid})
    check("N602 the blueprint still compiles with a disabled node",
          comp.get("ok") is not False, json.dumps(comp)[:200])
    again = M.call("set_node_state", {"node": guid, "comment": "still here"})
    check("N602 and the node is STILL disabled after compiling",
          again.get("enabledBefore") == "disabled", again.get("enabledBefore"))

    # ---------------------------------------------------------------- N603 the third state
    print("")
    print("=== N603: developmentOnly is a third state, not a synonym for enabled ===")
    d = M.call("set_node_state", {"node": guid, "enabled": "developmentOnly"})
    check("N603 developmentOnly is accepted", d.get("ok") is not False, json.dumps(d)[:200])
    check("N603 and reads back as developmentOnly, NOT enabled",
          d.get("enabledAfter") == "developmentOnly", d.get("enabledAfter"))
    check("N603 the response says it is stripped from a shipping cook",
          "strip" in str(d.get("developmentNote", "")).lower(), d.get("developmentNote"))

    e = M.call("set_node_state", {"node": guid, "enabled": "enabled"})
    check("N603 and it can be turned back on", e.get("enabledAfter") == "enabled",
          e.get("enabledAfter"))
    check("N603 with the links still intact after the whole round trip",
          links_on(guid) == links_before,
          "before %r after %r" % (links_before, links_on(guid)))

    # ---------------------------------------------------------------- N604 comment
    print("")
    print("=== N604: the comment ON a node, which is not a comment BOX ===")
    c = M.call("set_node_state", {"node": guid, "comment": "checked by N604",
                                  "commentBubble": True})
    check("N604 the comment is set", c.get("comment") == "checked by N604", c.get("comment"))
    check("N604 and the bubble is pinned", c.get("commentBubblePinned") is True,
          c.get("commentBubblePinned"))

    # ---------------------------------------------------------------- N605 refusals
    print("")
    print("=== N605: what it refuses ===")
    n1 = M.call("set_node_state", {"node": guid})
    check("N605 a call that changes nothing is refused", n1.get("ok") is False,
          str(n1.get("error"))[:150])
    n2 = M.call("set_node_state", {"node": guid, "enabled": "sometimes"})
    check("N605 an unknown state is refused with the valid list",
          n2.get("ok") is False and "developmentOnly" in str(n2.get("error", "")),
          str(n2.get("error"))[:170])
    still = M.call("set_node_state", {"node": guid, "comment": "checked by N604"})
    check("N605 and the refusal changed nothing - still enabled",
          still.get("enabledBefore") == "enabled", still.get("enabledBefore"))
    n3 = M.call("set_node_state", {"node": guid, "text": "wrong key"})
    check("N605 `text` is refused and points at add_comment",
          n3.get("ok") is False and "add_comment" in str(n3.get("error", "")),
          str(n3.get("error"))[:170])

    # ---------------------------------------------------------------- cleanup
    print("")
    import scratch_confirm as SC
    try:
        SC.confirm_call("delete_asset", {"path": bpath})
    except Exception as exc:
        print("  cleanup: %s" % str(exc)[:140])
    left = M.call("find_assets", {"pathPrefix": "/Game/_MifNodeState"}).get("count")
    check("N699 (cleanup) the fixture blueprint is gone", left == 0, left)

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        if isinstance(f, tuple):
            print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
