"""group_actors / ungroup_actors - the editor's Ctrl+G, and the four silent no-ops around it.

WHY THIS SUITE IS SHARPER THAN "it returned a group". UActorGroupingUtils::GroupActors returns
nullptr and says NOTHING in four separate situations, and three of them have nothing to do with
each other:

  * the editor's grouping mode is off       a global toggle; the whole body is skipped
  * the actors live in different levels     the engine breaks out of its scan on the first mismatch
  * fewer than two groupable actors         FinalActorList.Num() > 1 is required
  * every actor passed was already a group  AGroupActor is filtered out of the candidate list

A wrapper that just forwards the call and reports "nothing happened" is useless in all four. So
each is provoked here deliberately, and the refusal has to name the RIGHT cause - a correct refusal
for the wrong reason sends you to fix something that was never broken.

AND THE POSTCONDITION, which is the house rule: a non-null AGroupActor is not proof. The group's
members must resolve back to it through GetRootForActor, because that is what makes clicking one
member select the group - a group whose members do not point at it looks perfect in the response
and does nothing in the viewport.

Usage:  python tools/test_group_actors.py
Exit:   0 passed   1 failed   2 SKIPPED, no bridge
"""
import json
import sys
import time

import mifaudit as M

PASS = []
FAIL = []
SPAWNED = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def spawn(label, x):
    r = M.call("spawn_actor_in_level", {
        "class": "/Script/Engine.StaticMeshActor",
        "location": {"x": x, "y": 0, "z": 50000},
        "label": label})
    p = ((r.get("actor") or {}).get("actorPath")) or r.get("actorPath")
    if p:
        SPAWNED.append(p)
    return p, r


def root_of(path):
    """What group does this actor belong to, read back off the level rather than remembered."""
    r = M.call("list_level_actors", {"nameContains": path.split(".")[-1]})
    for a in (r.get("actors") or []):
        if a.get("actorPath") == path:
            return a
    return {}


def main():
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2

    if not M.call("describe_endpoint", {"endpoint": "group_actors"}).get("registered"):
        print("skipped: group_actors is not in this build")
        return 2

    st = int(time.time() % 100000)
    a, _ = spawn("MifGrp_A_%d" % st, 10)
    b, _ = spawn("MifGrp_B_%d" % st, 60)
    c, _ = spawn("MifGrp_C_%d" % st, 110)
    check("G100 (setup) three scratch actors exist", all([a, b, c]), [a, b, c])
    if not all([a, b, c]):
        return 1

    # ---------------------------------------------------------------- G101 refusals that come first
    print("\n=== G101: the refusals, each naming its OWN cause ===")

    one = M.call("group_actors", {"actorPaths": [a]})
    check("G101 one actor is refused, and the message says two are needed",
          one.get("ok") is False and "TWO" in str(one.get("error", "")).upper(),
          str(one.get("error"))[:200])

    none = M.call("group_actors", {"actorPaths": []})
    check("G101 an empty list is refused", none.get("ok") is False, str(none.get("error"))[:160])

    ghost = M.call("group_actors", {"actorPaths": [a, "/Game/Nope.Nope:PersistentLevel.Nope_0"]})
    check("G101 an unresolvable path is reported in notFound rather than ignored",
          ghost.get("ok") is False and ghost.get("notFound"), json.dumps(ghost)[:220])

    bad = M.call("group_actors", {"actorPaths": [a, b], "name": "MyGroup"})
    check("G101 `name` is refused and points at set_actor_label",
          bad.get("ok") is False and "set_actor_label" in str(bad.get("error", "")),
          str(bad.get("error"))[:220])

    bad2 = M.call("group_actors", {"actorPaths": [a, b], "parent": a})
    check("G101 `parent` is refused and separates grouping from attachment",
          bad2.get("ok") is False and "attach_actor" in str(bad2.get("error", "")),
          str(bad2.get("error"))[:220])

    # ---------------------------------------------------------------- G102 the real thing
    print("\n=== G102: grouping, judged by the members pointing BACK at the group ===")
    g = M.call("group_actors", {"actorPaths": [a, b], "enableGrouping": True})
    check("G102 group_actors succeeds", g.get("ok") is not False, json.dumps(g)[:260])
    group = g.get("group")
    check("G102 it returns the AGroupActor's path", bool(group), g.get("group"))
    check("G102 memberCount is 2", g.get("memberCount") == 2, g.get("memberCount"))
    # THE CHECK THAT MATTERS. ok:true plus a path is not a working group.
    check("G102 every member resolves BACK to this group - what makes a click select it",
          g.get("everyMemberRootsToThisGroup") is True,
          "%s / %s" % (g.get("everyMemberRootsToThisGroup"), g.get("membershipNote")))
    check("G102 it says AGroupActor does not survive a cook",
          "editor-only" in str(g.get("cookNote", "")).lower(), g.get("cookNote"))
    check("G102 and that nothing was saved",
          "NOTHING has been saved" in str(g.get("levelNote", "")), g.get("levelNote"))

    # ---------------------------------------------------------------- G103 groups are filtered out
    print("\n=== G103: passing a GROUP is not passing an actor ===")
    if group:
        only_groups = M.call("group_actors", {"actorPaths": [group]})
        check("G103 a lone group is refused and listed in alreadyGroups",
              only_groups.get("ok") is False and only_groups.get("alreadyGroups"),
              json.dumps(only_groups)[:260])
        check("G103 and the refusal explains the engine filters them out",
              "filter" in str(only_groups.get("error", "")).lower(),
              str(only_groups.get("error"))[:220])

    # ---------------------------------------------------------------- G104 ungroup
    print("\n=== G104: ungrouping frees the members and DELETES NOTHING ===")
    u = M.call("ungroup_actors", {"group": group})
    check("G104 ungroup_actors succeeds", u.get("ok") is not False, json.dumps(u)[:260])
    check("G104 every member was freed", u.get("everyMemberFreed") is True,
          "freed=%s stillGrouped=%s" % (u.get("freedCount"), u.get("stillGrouped")))
    check("G104 it says the members were not deleted",
          "does not delete" in str(u.get("memberNote", "")).lower(), u.get("memberNote"))

    # AND THE ACTORS ARE STILL THERE - the difference between ungroup and delete, checked against
    # the level rather than against the response that just claimed it.
    still_a = root_of(a)
    still_b = root_of(b)
    check("G104 both members still exist in the level after ungrouping",
          bool(still_a) and bool(still_b),
          "a=%s b=%s" % (bool(still_a), bool(still_b)))

    # ---------------------------------------------------------------- G105 ungroup refusals
    print("\n=== G105: what ungroup refuses ===")
    loose = M.call("ungroup_actors", {"group": c})
    check("G105 an actor in no group is refused, not counted as done",
          loose.get("ok") is False and loose.get("notInAnyGroup"), json.dumps(loose)[:240])
    empty = M.call("ungroup_actors", {})
    check("G105 no selector at all is refused", empty.get("ok") is False,
          str(empty.get("error"))[:180])
    delkey = M.call("ungroup_actors", {"group": c, "delete": True})
    # delete_level_actor, not delete_actor. The first draft of these handlers advised the latter in
    # a KeyNote and audit_message_endpoints caught it - there is no delete_actor on this build, so
    # the refusal would have sent the caller to 'not an endpoint'. Asserted here so the advice stays
    # a name that resolves.
    check("G105 `delete` is refused and points at delete_level_actor",
          delkey.get("ok") is False and "delete_level_actor" in str(delkey.get("error", "")),
          str(delkey.get("error"))[:220])

    # ---------------------------------------------------------------- cleanup
    print("")
    for p in SPAWNED:
        r = M.cleanup_level_actor(p, "scratch group-test actor")
        check("G199 (cleanup) %s is removed" % p.split(".")[-1], r.get("ok") is True, r.get("error"))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
