"""add_anim_node - the guard that checked the blueprint while its comment promised the graph.

WHY THIS SUITE EXISTS, and why its first assertion matters more than the rest. add_anim_node had a
guard reading `if (!Blueprint->IsA<UAnimBlueprint>())`, above a comment that said:

    An anim node in a non-anim GRAPH compiles to nothing and is a confusing thing to debug, so refuse
    it here rather than let it sit in an EventGraph looking placed.

The comment describes a graph-level check. The code performed a blueprint-level one. An Animation
Blueprint has BOTH an AnimGraph and an EventGraph, so aiming add_anim_node at the EventGraph of a
perfectly valid Animation Blueprint sailed past the guard.

It did not "sit in an EventGraph looking placed". It KILLED THE EDITOR:

    Fatal error: [Casts.cpp:10] Cast of EdGraph .../ABP:EventGraph to AnimationGraph failed
      FAnimStateMachineNodeNameValidator::FAnimStateMachineNodeNameValidator()
      UAnimGraphNode_StateMachineBase::MakeNameValidator()
      UAnimGraphNode_StateMachineBase::PostPlacedNewNode()
      MifBridge::H_add_anim_node()

PostPlacedNewNode builds a name validator that CastChecks its graph to UAnimationGraph, and a failed
CastChecked terminates the process rather than returning null. So the distance between what the comment
claimed and what the code did was the distance between an error message and a dead editor mid-request.
See PM-013.

T550 is that exact call. It asserts the refusal AND that the bridge is still answering afterwards,
because a suite that only checked ok:false would pass just as happily against an editor that had died
one call later.

T551 is the control that stops the fix being over-tightened into uselessness: the AnimGraph itself must
still accept the node. A guard that refuses everything is not a fix.

SAFETY: a scratch Animation Blueprint under /Game/_MifAnim, built on whatever Skeleton the project
already ships. Nothing is saved.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    skels = M.call("find_assets", {"class": "Skeleton", "pathPrefix": "/Game/", "limit": 1},
                   timeout=90).get("assets") or []
    if not skels:
        print("no Skeleton in /Game/ - an Animation Blueprint cannot be created, so this suite cannot run")
        return 0
    skel = skels[0].get("path")

    r = M.call("create_blueprint", {"path": "/Game/_MifAnim/ABP_%d" % st,
                                    "blueprintType": "AnimBlueprint", "skeleton": skel}, timeout=120)
    check("a scratch Animation Blueprint exists", r.get("ok") is True, json.dumps(r)[:200])
    bid = r.get("blueprintId")
    if not bid:
        return 1

    graphs = M.call("list_graphs", {"blueprintId": bid}, timeout=60).get("graphs") or []
    names = [g.get("name") for g in graphs]
    eg = next((g.get("graphId") for g in graphs if "EventGraph" in (g.get("name") or "")), None)
    ag = next((g.get("graphId") for g in graphs if "AnimGraph" in (g.get("name") or "")), None)
    # The premise of the whole bug: an ABP really does carry both.
    check("it has BOTH an AnimGraph and an EventGraph", bool(eg) and bool(ag), str(names))
    if not eg or not ag:
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1

    # ------------------------------------------------------------------ T550 the killer call
    print("")
    print("=== T550 [the crash]: an anim node aimed at the ABP's EVENT graph ===")
    a = M.call("add_anim_node", {"graphId": eg, "nodeClass": "AnimGraphNode_StateMachine",
                                 "x": 100, "y": 100}, timeout=90)
    check("T550 it is refused", a.get("ok") is False, json.dumps(a)[:240])
    err = (a.get("error") or "")
    check("T550 and the refusal names the graph, not the blueprint",
          "animation graph" in err.lower(), err[:200])
    check("T550 and points at the AnimGraph", "AnimGraph" in err, err[:200])
    # THE assertion. Before the fix this call did not return a refusal - the process ended.
    check("T550 THE EDITOR IS STILL ALIVE", M.bridge_responsive() is True,
          "the bridge stopped answering - add_anim_node terminated the editor again (PM-013)")
    # Nothing may be left behind by a refusal.
    # NOT count == 0. A fresh Animation Blueprint ships its EventGraph with default K2Node_Event
    # nodes already in it (BlueprintUpdateAnimation and friends), so an empty-graph assertion fails
    # against correct behaviour. The question is only whether an ANIM node was left behind.
    n = M.call("find_nodes", {"graphId": eg}, timeout=60)
    placed = [x for x in (n.get("nodes") or []) if "AnimGraphNode" in str(x.get("class"))]
    check("T550 and no ANIM node was left in the event graph", not placed,
          "found %s" % json.dumps(placed)[:200])

    # ------------------------------------------------------------------ T551 not over-tightened
    print("")
    print("=== T551 [the control]: the AnimGraph must still accept anim nodes ===")
    b = M.call("add_anim_node", {"graphId": ag, "nodeClass": "AnimGraphNode_StateMachine",
                                 "x": 100, "y": 100}, timeout=90)
    check("T551 the AnimGraph accepts it", b.get("ok") is True, json.dumps(b)[:240])
    check("T551 and reports the node", bool(b.get("nodeGuid") or b.get("node")), json.dumps(b)[:200])
    check("T551 the editor is still alive", M.bridge_responsive() is True, "bridge died")

    # ------------------------------------------------------------------ T552 the ordinary mistake
    print("")
    print("=== T552: an ordinary Blueprint still gets the clearer blueprint-level message ===")
    obid = M.call("create_blueprint", {"path": "/Game/_MifAnim/BP_%d" % st,
                                       "parentClass": "Actor"}, timeout=90).get("blueprintId")
    oeg = next((g.get("graphId") for g in (M.call("list_graphs", {"blueprintId": obid}, timeout=60).get("graphs") or [])
                if "EventGraph" in (g.get("name") or "")), None)
    q = M.call("add_anim_node", {"graphId": oeg, "nodeClass": "AnimGraphNode_StateMachine"}, timeout=90)
    check("T552 it is refused", q.get("ok") is False, json.dumps(q)[:200])
    # The blueprint arm is kept first precisely because this message is more useful for this mistake.
    check("T552 and the message is about the BLUEPRINT, which is the useful advice here",
          "Animation Blueprint" in (q.get("error") or ""), (q.get("error") or "")[:190])
    check("T552 the editor is still alive", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
