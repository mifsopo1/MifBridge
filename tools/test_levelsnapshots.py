"""Level Snapshots: create_level_snapshot, describe_level_snapshot, apply_level_snapshot.

Reopened 2026-08-28 after being wrongly declined earlier the same night, with reasoning ("zero plan or
presence in either project") that is exactly the mistake tools' own autopilot hook
(~/.claude/hooks/autopilot-continue.js) warns against: MifBridge is a GENERAL UE5 tool, and neither
DDS2 nor Curfew needing something yet does not make it worthless to every UE5 user. Capture/restore of
level state is useful to any UE5 developer doing iterative editing, and is exactly the "no rollback
story" gap docs/01_POSTMORTEMS.md keeps returning to - this plugin IS one.

Built and verified the same way GAS/MVVM/MetaHuman were when neither project had real content for them
yet: against a FIXTURE (a scratch actor, spawned and moved by this suite) rather than declined outright.

T1100-T1103: the real round trip, not just ok:true. Spawn a scratch actor at the origin, snapshot the
level, move the actor away, INDEPENDENTLY read back its moved position via list_level_actors (not
trusted from set_actor_transform's own response), apply the snapshot, then INDEPENDENTLY read the
position AGAIN and confirm it is back at the origin. This is the actual value of the feature - a real
rollback, proven by reading state through a completely different endpoint than the one that changed it.

T1104: describe_level_snapshot reads back the exact same summary create_level_snapshot returned,
through a separate LoadObject.

T1105-T1108: refusals checked for the specific reason. Overwrite guard (same fix as
create_procedural_mesh's - a second create at the same path must refuse, not silently replace).
Unknown parameter. A snapshot path that does not exist, for both describe and apply.

T1109: unknown parameter rejected on create_level_snapshot (RejectUnknownParams).

DECLINED for this batch: the map-mismatch refusal (applying a snapshot to a DIFFERENT level than it was
captured in) is verified by READING the handler's comparison logic
(Snapshot->GetMapPath() != CurrentWorldPath in MifBridgeLevelSnapshots.cpp), not reproduced live - doing
so would need load_level, which this project's own standing rule already treats as too state-destroying
to exercise casually (see FEATURE_PARITY_SPEC.md's load_level entry, same reasoning applied here).
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def find_actor_location(label):
    r = M.call("list_level_actors", {})
    for a in (r.get("actors") or []):
        if a.get("label") == label:
            return a.get("location")
    return None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    snapshot_path = "/Game/_MifLevelSnapTest%d/LS_BeforeMove" % st
    actor_label = "MifSnapTestActor%d" % st

    # ------------------------------------------------------------------ setup: a real fixture actor
    print("\n=== T1100 (setup): spawn a scratch actor at the origin ===")
    spawned = M.call("spawn_actor_in_level", {
        "class": "StaticMeshActor", "location": {"x": 0, "y": 0, "z": 0}, "label": actor_label})
    check("T1100 (setup) actor spawned", spawned.get("ok") is True, json.dumps(spawned)[:200])
    if not spawned.get("ok"):
        print("cannot continue without the fixture actor")
        return 3

    # ------------------------------------------------------------------ T1101 create_level_snapshot
    print("\n=== T1101: create_level_snapshot captures the actor's current position ===")
    snap = M.call("create_level_snapshot", {
        "path": snapshot_path, "name": "BeforeMove", "description": "actor at origin"})
    check("T1101 create succeeds", snap.get("ok") is True, json.dumps(snap)[:200])
    check("T1101 numSavedActors is a real positive count", isinstance(snap.get("numSavedActors"), int)
          and snap.get("numSavedActors") > 0, snap.get("numSavedActors"))
    check("T1101 mapPath is reported", bool(snap.get("mapPath")), snap.get("mapPath"))

    # ------------------------------------------------------------------ T1102 move, independently verify
    print("\n=== T1102: move the actor, verify the move through a DIFFERENT read path ===")
    moved = M.call("set_actor_transform", {
        "actorPath": spawned.get("actor", {}).get("actorPath"), "location": {"x": 500, "y": 500, "z": 500}})
    check("T1102 set_actor_transform succeeds", moved.get("ok") is True, json.dumps(moved)[:200])
    loc_after_move = find_actor_location(actor_label)
    check("T1102 the actor really moved, read back via list_level_actors (not trusted from the write)",
          loc_after_move == {"x": 500, "y": 500, "z": 500}, loc_after_move)

    # ------------------------------------------------------------------ T1103 apply_level_snapshot: THE REAL ROLLBACK
    print("\n=== T1103: apply_level_snapshot restores the position - the actual point of this feature ===")
    applied = M.call("apply_level_snapshot", {"path": snapshot_path})
    check("T1103 apply succeeds", applied.get("ok") is True, json.dumps(applied)[:200])
    check("T1103 appliedToWorld is true", applied.get("appliedToWorld") is True, applied)
    loc_after_restore = find_actor_location(actor_label)
    check("T1103 the actor is REALLY back at the origin, independently read back - not just ok:true",
          loc_after_restore == {"x": 0, "y": 0, "z": 0}, loc_after_restore)

    # ------------------------------------------------------------------ T1104 describe_level_snapshot
    print("\n=== T1104: describe_level_snapshot reads back the same summary through a separate LoadObject ===")
    desc = M.call("describe_level_snapshot", {"path": snapshot_path})
    check("T1104 describe succeeds", desc.get("ok") is True, json.dumps(desc)[:200])
    check("T1104 numSavedActors matches the original create response",
          desc.get("numSavedActors") == snap.get("numSavedActors"),
          "create=%s describe=%s" % (snap.get("numSavedActors"), desc.get("numSavedActors")))
    check("T1104 snapshotName round-trips", desc.get("snapshotName") == "BeforeMove", desc.get("snapshotName"))
    check("T1104 description round-trips", desc.get("description") == "actor at origin", desc.get("description"))

    # ------------------------------------------------------------------ T1105-T1109 refusals, exact reason
    print("\n=== T1105-T1109: refusals checked for the specific reason ===")
    dupe = M.call("create_level_snapshot", {"path": snapshot_path})
    check("T1105 a second create at the SAME path is refused, not silently applied", dupe.get("ok") is False, dupe)
    check("T1105 refusal explains the path is already taken", "already taken" in (dupe.get("error") or ""),
          dupe.get("error"))

    missing_desc = M.call("describe_level_snapshot", {"path": snapshot_path + "_DoesNotExist"})
    check("T1106 describe on a missing snapshot is refused", missing_desc.get("ok") is False, missing_desc)

    missing_apply = M.call("apply_level_snapshot", {"path": snapshot_path + "_DoesNotExist"})
    check("T1107 apply on a missing snapshot is refused", missing_apply.get("ok") is False, missing_apply)

    outside_game = M.call("create_level_snapshot", {"path": "/Engine/Transient/LS_Bad"})
    check("T1108 a path outside /Game/ is refused", outside_game.get("ok") is False, outside_game)

    unknown_param = M.call("create_level_snapshot", {"path": snapshot_path + "_Bad", "label": "x"})
    check("T1109 unknown parameter 'label' is rejected", unknown_param.get("ok") is False, unknown_param)
    check("T1109 rejection names the unrecognised key", "label" in (unknown_param.get("error") or ""),
          unknown_param.get("error"))

    # ------------------------------------------------------------------ cleanup
    SC.confirm_call("delete_asset", {"path": snapshot_path})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
