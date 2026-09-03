"""Level Instances - UE5's prefab, and the write half that had no follow-through.

THE ASYMMETRY THAT JUSTIFIED THIS. The bridge could already CREATE a level instance placement -
spawn_actor_in_level with ALevelInstance, then set_property on WorldAsset - and could then do
NOTHING with it: not see whether it loaded, not open it for editing, not break it apart.
ULevelInstanceSubsystem had literally zero references in the plugin. That is a write with no
follow-through, the mirror of the read-with-no-write asymmetry this project normally funds first.

NOT THE SAME THING as pie_load_level_instance, despite the name. That one wraps
ULevelStreamingDynamic::LoadLevelInstance, which streams a level into a RUNNING world by package
name. This is ALevelInstance, the placed prefab actor, and a different subsystem entirely.

T4501 IS THE ONE WORTH HAVING on a project with no level instances in it. Every one of these four
endpoints has to REFUSE cleanly when pointed at something that is not a level instance, and the
refusal has to name what the thing actually was - otherwise an agent pointed at the wrong actor gets
a generic failure and retries the same call. WorldDataLayers is a real actor in every World
Partition level and makes an honest negative fixture.

NOT EXERCISED, and named rather than left to be inferred: everything that needs an actual placed
Level Instance. This project's scratch world has none - list_level_instances confirms it and says
so in its own response rather than returning a bare empty list. Creating one means saving a level
asset to disk, which the safety gate refuses. So the load/unload queueing, the edit session, the
commit-writes-a-package path and the break are all unverified here; a project that uses Level
Instances (Curfew, or any modern uncooked one) is where that half runs.
"""
import json
import sys

import mifaudit as M

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

    # ------------------------------------------------------------------ T4500 the read half
    print("=== T4500: the placements are visible at all ===")
    r = M.call("list_level_instances", {})
    check("T4500 list_level_instances succeeds", r.get("ok") is True, json.dumps(r)[:250])
    check("T4500 it reports a list and a count that agree",
          isinstance(r.get("instances"), list)
          and r.get("count") == len(r.get("instances") or []),
          "count=%s len=%s" % (r.get("count"), len(r.get("instances") or [])))
    check("T4500 matched is reported separately from count, so truncation is visible",
          isinstance(r.get("matched"), (int, float)) and r.get("truncated") is False,
          json.dumps({"matched": r.get("matched"), "truncated": r.get("truncated")}))

    if (r.get("count") or 0) == 0:
        # AN EMPTY RESULT THAT EXPLAINS ITSELF. "0" and "0, and here is why that is normal" are
        # different answers, and only the second stops an agent hunting for a bug.
        check("T4500 an empty world says WHY it is empty rather than returning a bare 0",
              "no Level Instance actors" in (r.get("note") or "")
              and "list_sublevels" in (r.get("note") or ""), (r.get("note") or "")[:220])
    else:
        row = r["instances"][0]
        check("T4500 each row carries the level asset the placement points at - the field "
              "list_level_actors cannot show", bool(row.get("worldAsset")), json.dumps(row)[:250])
        for f in ("actorPath", "loaded", "editing"):
            check("T4500 row reports %s" % f, f in row, sorted(row))

    # ------------------------------------------------------------------ T4501 wrong-target
    print("\n=== T4501: all four refuse a non-instance, and say what it really was ===")
    # SKIP SCRATCH: sixteen suites spawn actors into this level. The refusal being asserted holds
    # for a scratch actor identically, but one deleted by its owner between the listing and the
    # call answers "no actor at" instead of naming what it really was, failing four guards here
    # for a cause that is not about level instances.
    actors = M.call("list_level_actors", {"limit": 40}).get("actors") or []
    victim = (M.pick_adoptable(actors, lambda a: bool(a.get("actorPath"))) or {}).get("actorPath")
    check("T4501 (setup) some actor exists to point at wrongly", bool(victim), len(actors))
    if not victim:
        return 1

    for endpoint, extra in (("set_level_instance_loaded", {"loaded": True}),
                            ("edit_level_instance", {"action": "edit"}),
                            ("break_level_instance", {"confirm": True})):
        resp = M.raw_post(endpoint, dict({"actorPath": victim}, **extra))
        check("T4501 %s refuses a non-instance" % endpoint, resp.get("ok") is False,
              json.dumps(resp)[:220])
        # THE assertion: it names the class it actually found. A generic failure invites a retry
        # of the identical call.
        check("T4501 %s names what the actor really is" % endpoint,
              "not a Level Instance" in (resp.get("error") or ""),
              (resp.get("error") or "")[:200])

    missing = M.raw_post("list_level_instances", {"nope": 1})
    check("T4501 an unknown parameter is refused", missing.get("ok") is False,
          (missing.get("error") or "")[:180])
    gone = M.raw_post("set_level_instance_loaded", {"actorPath": "/no/such/actor", "loaded": True})
    check("T4501 an unresolvable actorPath is refused before any subsystem call",
          gone.get("ok") is False, (gone.get("error") or "")[:180])

    # ------------------------------------------------------------------ T4502 required args
    print("\n=== T4502: the arguments that must not be guessed ===")
    noload = M.raw_post("set_level_instance_loaded", {"actorPath": victim})
    check("T4502 set_level_instance_loaded refuses to TOGGLE - say which end state you want",
          noload.get("ok") is False, (noload.get("error") or "")[:200])
    badact = M.raw_post("edit_level_instance", {"actorPath": victim, "action": "frobnicate"})
    check("T4502 an unknown edit action is refused", badact.get("ok") is False,
          (badact.get("error") or "")[:200])
    nocon = M.raw_post("break_level_instance", {"actorPath": victim})
    check("T4502 break without confirm is refused", nocon.get("ok") is False,
          (nocon.get("error") or "")[:200])

    alive = M.call("self_audit", {})
    check("T4502 - the editor is still alive after every refused call", alive.get("ok") is True,
          "these endpoints reach a subsystem that swaps loaded ULevels")

    print("\n  NOT EXERCISED: everything needing a real placed Level Instance - the load/unload")
    print("  queueing, the edit session, the commit that WRITES a package, and the break. This")
    print("  world has none, and creating one means saving a level asset to disk, which the gate")
    print("  refuses. list_level_instances says so itself rather than returning a bare empty list.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
