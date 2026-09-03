"""add_anim_node - the guard that checked the blueprint while its comment promised the graph.
Also covers connect_pins hardcoding the K2 schema on AnimGraph pins (T553-T554).

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

T553-T554, ADDED 2026-08-29: connect_pins hardcoded UEdGraphSchema_K2 instead of resolving the pin's
OWN graph's schema (docs/06_CAPABILITY_ROADMAP.md flagged it, checked against the 5.3 engine source
rather than taken on the roadmap's word). UAnimationGraphSchema overrides
DetermineConnectionResponseOfCompatibleTypedPins to enforce a pose TREE: a pose pin, unlike an ordinary
K2 data pin, may have only ONE link even on its OUTPUT side - connecting the same source pose to a
second target must BREAK the first connection, not fan it out. K2's schema has no such rule and would
have silently allowed the fan-out. T553 proves it live: SequencePlayer.Pose -> Root.Result, then the
SAME Pose output -> a Slot node's Source pin, and asserts Root.Result comes back UNLINKED afterwards
(verified this exact behaviour manually against the running editor before writing the assertion -
the response literally says "Replace existing connections", the same string the engine's own override
returns). T554 is the regression control: an ordinary K2 EventGraph connection - the overwhelming
majority of connect_pins' real-world use - must still behave exactly as before, since 10 other suites
already exercise it and this fix only changes WHICH schema gets picked, not how any of them behave.

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

    # SKIP SCRATCH. limit 1 over all of /Game/ takes whatever the registry lists first, and
    # test_virtual_bone_authoring and test_blend_profiles each duplicate a real Skeleton into their
    # own scratch tree. Parenting an Animation Blueprint to one of those means the ABP's skeleton is
    # deleted out from under it when that suite cleans up, and the failure lands here.
    skels = [a for a in (M.call("find_assets", {"class": "Skeleton", "pathPrefix": "/Game/",
                                                "limit": 20}, timeout=90).get("assets") or [])
             if not M.is_scratch_fixture(a)]
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

    # ------------------------------------------------------------------ T553 the schema fix
    print("")
    print("=== T553 [connect_pins schema]: a pose OUTPUT can feed only ONE target ===")
    root = next((x for x in (M.call("find_nodes", {"graphId": ag}, timeout=60).get("nodes") or [])
                 if x.get("class") == "AnimGraphNode_Root"), None)
    check("T553 the default Output Pose root node exists", bool(root), "no AnimGraphNode_Root found")
    if root:
        root_guid = root.get("guid")
        seq = M.call("add_anim_node", {"graphId": ag, "nodeClass": "AnimGraphNode_SequencePlayer",
                                       "x": -300, "y": 300}, timeout=90)
        slot = M.call("add_anim_node", {"graphId": ag, "nodeClass": "AnimGraphNode_Slot",
                                        "x": -300, "y": 500}, timeout=90)
        check("T553 SequencePlayer placed", seq.get("ok") is True, json.dumps(seq)[:200])
        check("T553 Slot placed", slot.get("ok") is True, json.dumps(slot)[:200])
        seq_guid, slot_guid = seq.get("nodeGuid"), slot.get("nodeGuid")
        if seq_guid and slot_guid:
            c1 = M.call("connect_pins", {"srcNode": seq_guid, "srcPin": "Pose",
                                         "dstNode": root_guid, "dstPin": "Result", "graphId": ag}, timeout=60)
            check("T553 first connect (SequencePlayer.Pose -> Root.Result) succeeds",
                  c1.get("ok") is True and c1.get("connected") is True, json.dumps(c1)[:250])

            # THE assertion. K2's schema allows a data output to fan out to many inputs - AnimGraph's
            # own override does not: a pose output may have only one link, so connecting the SAME
            # source to a DIFFERENT target must break the first link rather than add a second one.
            c2 = M.call("connect_pins", {"srcNode": seq_guid, "srcPin": "Pose",
                                         "dstNode": slot_guid, "dstPin": "Source", "graphId": ag}, timeout=60)
            check("T553 second connect (SAME Pose -> Slot.Source) succeeds",
                  c2.get("ok") is True and c2.get("connected") is True, json.dumps(c2)[:250])
            check("T553 and the engine reports it as a replacement, not an addition",
                  "Replace" in (c2.get("response") or ""), json.dumps(c2)[:250])

            root_after = M.call("get_node", {"nodeGuid": root_guid, "graphId": ag}, timeout=60)
            root_result_pin = next((p for p in (root_after.get("node", {}).get("pins") or [])
                                    if p.get("name") == "Result"), {})
            check("T553 Root.Result is now UNLINKED - the first connection was genuinely broken",
                  root_result_pin.get("linkedTo") == [], json.dumps(root_result_pin)[:200])

            slot_after = M.call("get_node", {"nodeGuid": slot_guid, "graphId": ag}, timeout=60)
            slot_source_pin = next((p for p in (slot_after.get("node", {}).get("pins") or [])
                                    if p.get("name") == "Source"), {})
            check("T553 Slot.Source carries the new link",
                  any(l.get("node") == seq_guid for l in (slot_source_pin.get("linkedTo") or [])),
                  json.dumps(slot_source_pin)[:200])

            seq_after = M.call("get_node", {"nodeGuid": seq_guid, "graphId": ag}, timeout=60)
            seq_pose_pin = next((p for p in (seq_after.get("node", {}).get("pins") or [])
                                 if p.get("name") == "Pose"), {})
            check("T553 SequencePlayer.Pose has exactly ONE link, not two (no fan-out survived)",
                  len(seq_pose_pin.get("linkedTo") or []) == 1, json.dumps(seq_pose_pin)[:200])
    check("T553 the editor is still alive", M.bridge_responsive() is True, "bridge died")

    # ------------------------------------------------------------------ T554 the regression control
    print("")
    print("=== T554 [regression control]: ordinary K2 EventGraph connects are unaffected ===")
    # Reuses obid/oeg from T552 - the ordinary Blueprint's EventGraph. connect_pins is exercised
    # heavily elsewhere (10 suites) for K2 semantics; this is a cheap, direct smoke test that
    # resolving the schema from the pin's own graph did not change K2's own behaviour at all.
    br1 = M.call("add_branch", {"graphId": oeg, "x": 0, "y": 0}, timeout=60)
    br2 = M.call("add_branch", {"graphId": oeg, "x": 300, "y": 0}, timeout=60)
    check("T554 two ordinary K2 nodes placed",
          br1.get("ok") is True and br2.get("ok") is True, "%s / %s" % (br1.get("ok"), br2.get("ok")))
    if br1.get("ok") and br2.get("ok"):
        b1_guid, b2_guid = br1.get("nodeGuid"), br2.get("nodeGuid")
        cx = M.call("connect_pins", {"srcNode": b1_guid, "srcPin": "then",
                                     "dstNode": b2_guid, "dstPin": "execute", "graphId": oeg}, timeout=60)
        check("T554 an ordinary exec connection still succeeds",
              cx.get("ok") is True and cx.get("connected") is True, json.dumps(cx)[:250])
    check("T554 the editor is still alive", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
