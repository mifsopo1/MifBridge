"""list_sockets, describe_behavior_tree, list_blackboard_keys - read endpoints for real DDS2 content.

These are read-first on purpose: 188 SkeletalMeshes, 17 BehaviorTrees and 4 BlackboardData assets in
the game, and no way to inspect any of them. A modder attaching a prop to a character, or changing NPC
behaviour, needs to SEE what is there before touching it.

T110 is the one worth reading. The first version of list_sockets returned only the MESH's sockets and
carried a polite note saying the skeleton has its own list. Pointed at real content, all 12 sampled
DDS2 skeletal meshes returned ZERO - the game keeps its sockets on one shared DDS2_CharacterSkeleton,
which is the normal pattern for a common rig. So the endpoint was honest, correct, and useless for
every character in the game. Explaining an empty answer is not the same as giving the right one.
The test asserts real sockets come back, not merely that the call succeeded.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def first_asset(cls):
    a = (M.call("find_assets", {"class": cls, "limit": 1}).get("assets") or [{}])[0]
    return a.get("path")


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T110 sockets
    print("\n=== T110 [the one that mattered]: sockets resolve on a REAL character ===")
    mesh = "/DDS2Casino/Characters/Alisha/Alisha.Alisha"
    r = M.call("list_sockets", {"path": mesh})
    print("  total=%s mesh=%s skeleton=%s" % (r.get("count"), r.get("meshSocketCount"),
                                              r.get("skeletonSocketCount")))
    names = [s.get("name") for s in (r.get("sockets") or [])]
    check("T110 read", r.get("ok") is True and r.get("assetKind") == "SkeletalMesh", json.dumps(r)[:200])
    # The whole point: a DDS2 character has zero MESH sockets, so anything that only reads those
    # returns an empty list for every character in the game.
    check("T110 it found real sockets, not an empty list", (r.get("count") or 0) > 0,
          "count=%s - reading only the mesh's own list gives 0 here" % r.get("count"))
    check("T110 they came from the SKELETON", (r.get("skeletonSocketCount") or 0) > 0,
          "skeletonSocketCount=%s" % r.get("skeletonSocketCount"))
    check("T110 the shared skeleton is named", "CharacterSkeleton" in (r.get("skeleton") or ""),
          r.get("skeleton"))
    check("T110 the sockets are the ones a mod would attach to",
          any("Hand" in (n or "") for n in names), str(names[:8]))
    check("T110 each socket says which list it came from",
          all(s.get("source") in ("mesh", "skeleton") for s in (r.get("sockets") or [])),
          json.dumps((r.get("sockets") or [{}])[0])[:160])
    check("T110 and each names its bone",
          all(s.get("bone") for s in (r.get("sockets") or [])),
          json.dumps((r.get("sockets") or [{}])[0])[:160])

    print("\n=== T111: list_sockets refuses a non-mesh rather than returning empty ===")
    bt_path = first_asset("BehaviorTree")
    q = M.call("list_sockets", {"path": bt_path})
    check("T111 refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T111 and it says what the asset actually is", "BehaviorTree" in (q.get("error") or ""),
          (q.get("error") or "")[:160])

    # ------------------------------------------------------------------ T112 behavior tree
    print("\n=== T112: a real DDS2 behavior tree walks ===")
    r = M.call("describe_behavior_tree", {"path": bt_path})
    nodes = r.get("nodes") or []
    print("  %s -> %s nodes" % ((bt_path or "")[-40:], r.get("nodeCount")))
    check("T112 read", r.get("ok") is True, json.dumps(r)[:200])
    check("T112 it has a root", r.get("hasRoot") is True, json.dumps(r)[:180])
    check("T112 and more than just the root", len(nodes) > 1, "nodeCount=%s" % r.get("nodeCount"))
    check("T112 nodes carry depth, name, class and kind",
          all(all(k in n for k in ("depth", "name", "class", "kind")) for n in nodes),
          json.dumps(nodes[:1])[:200])
    check("T112 the tree nests (some node is deeper than the root)",
          any((n.get("depth") or 0) > 0 for n in nodes), str([n.get("depth") for n in nodes[:8]]))
    check("T112 it resolves which blackboard the tree uses", bool(r.get("blackboard")),
          r.get("blackboard"))
    check("T112 it did not silently truncate", r.get("truncated") is None, r.get("truncatedNote"))

    # ------------------------------------------------------------------ T113 blackboard
    print("\n=== T113: blackboard keys, with the inherited flag ===")
    bb = r.get("blackboard") or first_asset("BlackboardData")
    k = M.call("list_blackboard_keys", {"path": bb})
    keys = k.get("keys") or []
    print("  %s -> %s keys" % ((bb or "")[-38:], k.get("count")))
    check("T113 read", k.get("ok") is True and (k.get("count") or 0) > 0, json.dumps(k)[:200])
    check("T113 keys carry name and type",
          all(x.get("name") and x.get("type") for x in keys), json.dumps(keys[:1])[:200])
    # The inherited flag decides whether a key can be EDITED on this asset. A caller who cannot tell
    # will try to change an inherited key and wonder why nothing happened.
    check("T113 every key says whether it is inherited",
          all(isinstance(x.get("inherited"), bool) for x in keys), json.dumps(keys[:1])[:200])
    check("T113 the types are real blackboard key types",
          any("BlackboardKeyType" in (x.get("type") or "") for x in keys),
          str([x.get("type") for x in keys[:4]]))

    print("\n=== T114: the remaining guards ===")
    for ep, payload, expect in (
        ("describe_behavior_tree", {"path": "/Game/NoSuchTree_zz"}, "not found"),
        ("list_blackboard_keys", {"path": "/Game/NoSuchBB_zz"}, "not found"),
        ("list_sockets", {"path": ""}, "required"),
        ("describe_behavior_tree", {"path": bb}, "not a BehaviorTree"),
    ):
        q = M.call(ep, payload)
        check("T114 %s refuses %s" % (ep, expect), q.get("ok") is False, json.dumps(q)[:160])
        check("T114 %s explains" % ep, expect in (q.get("error") or ""), (q.get("error") or "")[:150])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
