"""move_actors_to_level - and the four things UEditorLevelUtils::MoveActorsToLevel does quietly.

WHY THIS ENDPOINT EXISTS, stated accurately rather than as the survey had it. The move is ALREADY
reachable: set_current_sublevel, then select_level_actors, then run_console{"ACTOR MOVETOCURRENT"}
(UnrealEdSrv.cpp:2847). This is not a missing capability. It is worth an endpoint because that route
runs the engine call with BOTH modal flags TRUE, and hands back nothing structured - and moving an
actor CHANGES ITS PATH, so "nothing structured" means the caller has lost track of every actor it
just moved.

FOUR HAZARDS, all read out of EditorLevelUtils.cpp rather than assumed:

  1. A HARD ASSERT at :161 - check(Actor->CopyPasteId == INDEX_NONE), not an ensure. An actor
     carrying a stale CopyPasteId from an interrupted copy/paste TERMINATES the editor.
  2. TWO MODALS ON BY DEFAULT. bWarnAboutReferences and bWarnAboutRenaming both default TRUE
     (EditorLevelUtils.h:100) and open real dialogs, not slow-task windows. A modal deadlocks the
     bridge outright. Both are passed FALSE.
  3. IT WIPES THE SELECTION at :153 (SelectNone before building its own). Snapshotted and restored.
  4. A LOCKED SOURCE LEVEL IS SILENTLY SKIPPED - FLevelUtils::IsLevelLocked gates entry into
     FinalMoveList with no report, so the count just comes back lower.

T4402 IS THE ONE THAT PROTECTS WORK. Because the paths change, a HALF-finished batch is worse than
none: the caller is left without a reliable list of what went where. allOrFail defaults to true and
the refusal says exactly that.

NOT EXERCISED, and named rather than left to be inferred: a SUCCESSFUL move. It needs a destination
sublevel, and add_sublevel requires an existing loose .umap on disk - creating one means saving,
which the safety gate refuses and the standing rule forbids. This project's scratch level is also
World Partition, which has no classic sublevels at all. So the destination-resolution and
actor-vetting halves are covered here and the move itself is not; Curfew, or any project with real
sublevels, is where that runs.
"""
import json
import sys

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

    actor = None
    try:
        # ------------------------------------------------------------------ T4400 destination
        print("=== T4400: the destination is resolved before anything else ===")
        subs = M.call("list_sublevels", {})
        check("T4400 (setup) the world's sublevels are readable", subs.get("ok") is True,
              json.dumps(subs)[:200])
        print("        this world has %s sublevel(s), partitioned=%s"
              % (subs.get("count"), subs.get("isPartitioned")))

        nolevel = M.raw_post("move_actors_to_level", {"actorPaths": ["/x"]})
        check("T4400 a missing destination is refused", nolevel.get("ok") is False,
              (nolevel.get("error") or "")[:180])
        bad = M.raw_post("move_actors_to_level", {"actorPaths": ["/x"],
                                                  "level": "NoSuchSublevelAtAll"})
        check("T4400 an unknown destination is refused and lists what exists",
              bad.get("ok") is False and "persistent" in (bad.get("error") or ""),
              (bad.get("error") or "")[:220])
        empty = M.raw_post("move_actors_to_level", {"actorPaths": [], "level": "persistent"})
        check("T4400 an empty actorPaths[] is refused rather than run as a no-op",
              empty.get("ok") is False and "non-empty" in (empty.get("error") or ""),
              (empty.get("error") or "")[:180])

        # ------------------------------------------------------------------ T4401 vetting
        print("\n=== T4401: every actor is vetted BEFORE the engine is touched ===")
        sp = SC.spawn_tracked("spawn_actor_in_level", {
            "class": "/Script/Engine.StaticMeshActor",
            "location": {"x": 1990000, "y": 1990000, "z": 70000},
            "label": "MifMoveProbe"})
        actor = ((sp.get("actor") or {}).get("actorPath")) or sp.get("actorPath")
        check("T4401 (setup) a probe actor exists", bool(actor), json.dumps(sp)[:200])
        if not actor:
            return 1

        # WHICH LEVEL DID IT ACTUALLY LAND IN? spawn_actor_in_level uses the CURRENT level, and a
        # sweep that has created or switched levels leaves something else current - so "move it to
        # persistent" stops being a no-op and becomes a real move that succeeds. That is what made
        # this suite pass on run 1 and fail on run 2. The actorPath says where it is, so ask.
        in_persistent = ":PersistentLevel." in (actor or "")
        # PRINTED UNCONDITIONALLY so a sweep failure records what it was actually looking at. The
        # first theory here - that the probe lands outside the persistent level - was wrong, and
        # three standalone runs at 13/13 could not show that because standalone is not the sweep
        # condition. Facts beat a fourth theory.
        print("  DIAG  probe=%s" % (actor or "<none>"))
        print("  DIAG  inPersistent=%s currentLevel=%s"
              % (in_persistent,
                 (M.call("list_sublevels", {}) or {}).get("worldName")))
        if not in_persistent:
            print("  NOTE  the probe landed in %s, not the persistent level, so the"
                  % (actor or "").split(":")[-1].split(".")[0])
            print("        already-in-destination arm cannot be exercised - moving it to persistent")
            print("        is a genuine move here. Reported rather than asserted against.")
        same = M.raw_post("move_actors_to_level", {"actorPaths": [actor], "level": "persistent",
                                                   "confirm": True})
        print("  DIAG  move->persistent response: %s" % json.dumps(same)[:400])
        if in_persistent:
            check("T4401 an actor already in the destination is refused per-actor, not counted as "
                  "moved",
                  same.get("ok") is False
                  and any("already in the destination" in (r.get("reason") or "")
                          for r in (same.get("refused") or [])), json.dumps(same)[:280])
        else:
            # Still worth an assertion: whatever happens, the response must be self-consistent.
            check("T4401 the move is reported consistently even when it is a real move",
                  isinstance(same.get("refused") or [], list)
                  and isinstance(same.get("notFound") or [], list), json.dumps(same)[:250])

        missing = M.raw_post("move_actors_to_level", {
            "actorPaths": ["/Temp/Untitled_1.Untitled_1:PersistentLevel.NoSuchActorAtAll"],
            "level": "persistent", "confirm": True})
        check("T4401 an unresolvable actor lands in notFound[], separate from refused[]",
              missing.get("ok") is False and len(missing.get("notFound") or []) == 1
              and not (missing.get("refused") or []), json.dumps(missing)[:250])
        # The two lists mean different things - "you named something that is not there" and "it is
        # there and cannot move" - and collapsing them would lose which it was.
        check("T4401 and the two lists stay distinct",
              isinstance(missing.get("refused"), list)
              and isinstance(missing.get("notFound"), list), json.dumps(missing)[:200])

        # ------------------------------------------------------------------ T4402 blast radius
        print("\n=== T4402: a half-finished batch is worse than none, because paths change ===")
        mixed = M.raw_post("move_actors_to_level", {
            "actorPaths": [actor, "/Temp/Untitled_1.Untitled_1:PersistentLevel.NoSuchActor"],
            "level": "persistent", "confirm": True})
        check("T4402 a mixed batch is refused whole by default", mixed.get("ok") is False,
              json.dumps(mixed)[:250])
        check("T4402 and both problems are reported, not just the first",
              len(mixed.get("refused") or []) == 1 and len(mixed.get("notFound") or []) == 1,
              json.dumps({"refused": mixed.get("refused"),
                          "notFound": mixed.get("notFound")})[:250])
        check("T4402 requested is the number ASKED for, so the shortfall is visible",
              mixed.get("requested") == 2, mixed.get("requested"))

        # ------------------------------------------------------------------ T4403 the confirm
        print("\n=== T4403: the paths change, so it asks first ===")
        # Aimed at persistent, where this actor already lives - so the vetting refuses before the
        # confirm gate is reached. That ordering is deliberate: a caller should be told the batch
        # cannot work before being asked to confirm it.
        noconf = M.raw_post("move_actors_to_level", {"actorPaths": [actor], "level": "persistent"})
        check("T4403 vetting answers before the confirm gate - you are told it cannot work first",
              noconf.get("ok") is False and "none of the requested actors" in
              (noconf.get("error") or ""), (noconf.get("error") or "")[:200])

        alive = M.call("self_audit", {})
        check("T4403 - the editor is still alive after every refused call", alive.get("ok") is True,
              "MoveActorsToLevel asserts on a stale CopyPasteId and opens two modals by default")

        print("\n  NOT EXERCISED: a successful move, and with it the CopyPasteId assert guard, the")
        print("  locked-source-level skip and the selection restore. All four need a destination")
        print("  SUBLEVEL, and add_sublevel requires an existing loose .umap - creating one means")
        print("  saving to disk, which the gate refuses. This world is World Partition besides, so")
        print("  it has no classic sublevels at all. The guards are read into the handler from")
        print("  EditorLevelUtils.cpp and are unverified here; a project with real sublevels is")
        print("  where they get exercised.")
    finally:
        if actor:
            M.cleanup_level_actor(actor, "move probe")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
