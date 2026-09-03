"""add_anim_state - and the endpoint that was deliberately NOT built beside it.

ONE MISSING CONSTRUCTOR CALL WAS BLOCKING ALL OF IT. list_graphs and list_nodes already read state
machines, states and transition rule graphs; add_anim_node could already place the
UAnimGraphNode_StateMachine container. What could not be done was put a single STATE inside it - and
with no state there is nothing for a transition to join, so no locomotion Anim Blueprint could be
authored end to end. Anim Blueprints are a top-tier asset type and an agent hits this immediately.

WHY THERE IS NO add_anim_transition, and this suite PROVES it rather than asserting it. The survey
proposed two endpoints. The vetting pass said one, on the grounds that the state machine schema's
own connection response is a MAKE_WITH_CONVERSION_NODE that spawns the transition itself. T2004
tests exactly that: connect_pins from one state's Out pin to another's In pin, and then checks that
an AnimStateTransitionNode AND its rule graph now exist. If that ever stops being true, this test
fails and the missing endpoint becomes real work again - which is the only honest way to record a
scoped-out capability.

THE GUARD IS ON THE GRAPH CLASS, NOT THE SCHEMA, and T2003 is the one assertion that matters most
here. FAnimStateNodeNameValidator does CastChecked<UAnimationStateMachineGraph>(GetOuter())
(AnimStateNodeBase.cpp:27), and a failed CastChecked TERMINATES the editor rather than returning an
error. A schema-only test would let a fatal case through, because a graph can carry the
state-machine schema without being that class. T2003 aims a state at the plain AnimGraph and then
asks self_audit whether the editor is still answering - the same discipline
test_simplified_collision_guard uses, because a failed guard here is a dead process, not a bad
response.

NAMING IS NOT COSMETIC. UAnimStateNode::GetStateName() returns BoundGraph->GetName()
(AnimStateNode.cpp:68) - a state's name IS its bound graph's name, there is no separate field, and
nothing in this bridge can rename a graph afterwards. So the name has to be right at creation, and
the response reports the name that ACTUALLY landed beside the one requested, because RenameGraph
sanitises and de-duplicates.

CLEANS UP: the fixture Anim Blueprint is deleted at the end through scratch_confirm.
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


def graphs(bp):
    return M.call("list_graphs", {"blueprintId": bp}).get("graphs") or []


def graph_id(bp, name_contains):
    for g in graphs(bp):
        if name_contains in (g.get("name") or ""):
            return g.get("graphId")
    return None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # SKIP SCRATCH - same reason as test_anim_nodes, which does the same thing three lines in.
    # A limit-3 window over all of /Game/ can be filled entirely by the scratch Skeletons
    # test_virtual_bone_authoring and test_blend_profiles duplicate, and an Animation Blueprint
    # parented to one loses its skeleton when that suite cleans up.
    sk = [a for a in (M.call("find_assets", {"class": "Skeleton", "pathPrefix": "/Game/",
                                             "limit": 20}).get("assets") or [])
          if not M.is_scratch_fixture(a)]
    check("T2000 (setup) a Skeleton exists to parent an Anim Blueprint to", bool(sk), len(sk))
    if not sk:
        return 1

    st = int(time.time() % 100000)
    bp = "/Game/_MifAnimState/ABP_%d" % st
    made = M.call("create_blueprint", {"path": bp, "parentClass": "AnimInstance",
                                       "blueprintType": "AnimBlueprint",
                                       "skeleton": sk[0]["path"]})
    check("T2000 (setup) a scratch Anim Blueprint is created", made.get("ok") is True,
          json.dumps(made)[:250])
    if not made.get("ok"):
        return 1

    try:
        anim_graph = graph_id(bp, "AnimGraph")
        sm_node = M.call("add_anim_node", {"graphId": anim_graph,
                                           "nodeClass": "AnimGraphNode_StateMachine",
                                           "x": 0, "y": 0})
        check("T2000 (setup) a state machine is placed in the AnimGraph",
              sm_node.get("ok") is True, json.dumps(sm_node)[:250])
        sm = graph_id(bp, "State Machine")
        check("T2000 (setup) the state machine has an inner graph to add states to", bool(sm), sm)
        if not sm:
            return 1

        # ------------------------------------------------------------------ T2001
        print("\n=== T2001: add_anim_state ===")
        s1 = M.call("add_anim_state", {"blueprintId": bp, "graphId": sm, "name": "Idle",
                                       "x": 0, "y": 0})
        check("T2001 add_anim_state succeeds", s1.get("ok") is True, json.dumps(s1)[:300])
        check("T2001 the state really is called what was asked for",
              s1.get("stateName") == "Idle", json.dumps(s1)[:250])
        # boundGraphId is the point of the endpoint - a state you cannot fill is useless.
        check("T2001 it returns boundGraphId - the state's OWN animation graph",
              bool(s1.get("boundGraphId")), json.dumps(s1)[:250])
        check("T2001 and reports the state count in the machine, measured",
              s1.get("statesInMachine") == 1, json.dumps(s1)[:250])

        # THE assertion that boundGraphId is real and not just a string: put an anim node in it.
        filled = M.call("add_anim_node", {"graphId": s1.get("boundGraphId"),
                                          "nodeClass": "AnimGraphNode_SequencePlayer",
                                          "x": 0, "y": 0})
        check("T2001 boundGraphId is a usable graph - a SequencePlayer goes straight into it",
              filled.get("ok") is True, json.dumps(filled)[:250])

        s2 = M.call("add_anim_state", {"blueprintId": bp, "graphId": sm, "name": "Run",
                                       "x": 400, "y": 0})
        check("T2001 a second state is added and the count follows",
              s2.get("ok") is True and s2.get("statesInMachine") == 2, json.dumps(s2)[:250])

        # ------------------------------------------------------------------ T2002 refusals
        print("\n=== T2002: refusals ===")
        dup = M.call("add_anim_state", {"blueprintId": bp, "graphId": sm, "name": "Idle"})
        check("T2002 a duplicate state name is refused - state names are graph names and must be "
              "unique", dup.get("ok") is False, json.dumps(dup)[:250])
        noname = M.call("add_anim_state", {"blueprintId": bp, "graphId": sm})
        check("T2002 a missing name is refused, because nothing can rename it afterwards",
              noname.get("ok") is False, json.dumps(noname)[:250])

        # ------------------------------------------------------------------ T2003 THE FATAL-CAST GUARD
        print("\n=== T2003: the fatal-cast guard - a failed CastChecked kills the editor ===")
        wrong = M.call("add_anim_state", {"blueprintId": bp, "graphId": anim_graph, "name": "Nope"})
        check("T2003 aiming a state at the plain AnimGraph is REFUSED",
              wrong.get("ok") is False, json.dumps(wrong)[:300])
        check("T2003 and the refusal names the CastChecked, not a generic type error",
              "CastChecked" in (wrong.get("error") or ""), wrong.get("error"))
        # THE assertion. A failed guard here is a dead process, not a bad response, so the editor
        # answering at all afterwards is the real proof it held.
        alive = M.call("self_audit", {})
        check("T2003 - the editor is still alive afterwards",
              alive.get("ok") is True,
              "a failed guard here terminates the process rather than returning an error")

        # ------------------------------------------------------------------ T2004 the scoped-out endpoint
        print("\n=== T2004: why there is no add_anim_transition ===")
        nodes = M.call("list_nodes", {"graphId": sm}).get("nodes") or []
        idle = next((n for n in nodes if n.get("title") == "Idle"), None)
        run = next((n for n in nodes if n.get("title") == "Run"), None)
        check("T2004 (setup) both states are visible in the state machine graph",
              bool(idle) and bool(run), [n.get("title") for n in nodes])
        if idle and run:
            before_t = len([n for n in nodes if "Transition" in (n.get("class") or "")])
            c = M.call("connect_pins", {"graphId": sm, "srcNode": idle["guid"], "srcPin": "Out",
                                        "dstNode": run["guid"], "dstPin": "In"})
            check("T2004 connect_pins joins two states", c.get("ok") is True, json.dumps(c)[:250])

            after = M.call("list_nodes", {"graphId": sm}).get("nodes") or []
            trans = [n for n in after if "Transition" in (n.get("class") or "")]
            # THIS is the assertion that justifies not building add_anim_transition. If the schema
            # ever stops creating the node, this fails and the endpoint becomes real work again.
            check("T2004 and the SCHEMA created the transition node itself - which is why "
                  "add_anim_transition was scoped out rather than built",
                  len(trans) > before_t, [n.get("class") for n in after])
            names = [g.get("name") for g in graphs(bp)]
            check("T2004 and the transition's rule graph exists too, ready for the K2 endpoints",
                  any("Transition" in (n or "") for n in names), names)
    finally:
        d = SC.confirm_call("delete_asset", {"path": bp})
        check("T2005 (cleanup) the scratch Anim Blueprint is deleted",
              d.get("ok") is True or d.get("deleted") is True, json.dumps(d)[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
