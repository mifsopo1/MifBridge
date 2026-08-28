"""add_sequence_possessable / add_sequence_track - real success, on a scratch LevelSequence this
suite creates and initialises properly.

A REAL BUG FOUND AND FIXED to make this suite possible at all: create_asset{class:"LevelSequence"}
produced a MALFORMED asset - add_sequence_possessable refused it live with "has no MovieScene. The
asset exists but is malformed." create_asset's generic path is a bare NewObject<UObject>, and
ULevelSequence needs one more call after that (Initialize(), which creates and assigns its internal
UMovieScene sub-object) - the exact same extra step the engine's own stock "Add Level Sequence"
content-browser action takes (ULevelSequenceFactoryNew::FactoryCreateNew, LevelSequenceFactoryNew.cpp).
Fixed in MifBridgeUserTypes.cpp's H_create_asset: after NewObject succeeds, if the result is a
ULevelSequence, call Initialize() on it before registering it - checked by exact type, not by class
NAME the way the cooked-asset crash guards elsewhere in this plugin are, because this is a
construction step to RUN, not a class to refuse.

Verified with a real Build.bat on both engines this plugin targets before this suite was written
(DDS2's actual 5.3.2 and the 5.7 probe), not inferred from the source.

Both add_sequence_possessable and add_sequence_track are confirm-gated but addressed by an ACTOR
INSTANCE path for the possessable step - the SAME /Temp/-actor-path situation
set_niagara_component_parameter hit in an earlier batch this session. Same resolution, same
discipline: M.raw_post directly, justified narrowly because the actor this test binds was proven safe
by construction one line earlier in this exact run, not as a reusable shortcut - see
scratch_confirm.py's own module docstring for why a blanket /Temp/ trust rule was deliberately never
added.

T970: create_asset for a LevelSequence really is well-formed now (has a real MovieScene, not just
      ok:true).
T971: add_sequence_possessable - a real actor bound for real, with a real guid back.
T972: add_sequence_track - a real track added against that real guid, empty (no sections) but real.
T973: list_sequence_bindings reflects both, read back independently of the write calls' own claims.
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


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T970 create_asset really initialises it
    print("\n=== T970: create_asset for a LevelSequence is really well-formed now ===")
    lspath = "/Game/_MifReads9/LS_%d" % st
    ls = M.call("create_asset", {"path": lspath, "class": "LevelSequence"})
    check("T970 create_asset succeeds", ls.get("ok") is True, json.dumps(ls)[:200])
    desc = M.call("describe_level_sequence", {"path": lspath})
    check("T970 describe_level_sequence reads it back cleanly (a real MovieScene exists)",
          desc.get("ok") is True, json.dumps(desc)[:250])

    # ------------------------------------------------------------------ T971 add_sequence_possessable
    print("\n=== T971: add_sequence_possessable - a real actor bound for real ===")
    spawn = M.call("spawn_actor_in_level", {
        "actorClass": "StaticMeshActor",
        "location": {"x": 1900000 + st, "y": 1900000 + st, "z": 500000},
        "label": "MifReads9SeqActor_%d" % st})
    actor_path = (spawn.get("actor") or {}).get("actorPath")
    check("T971 (setup) a scratch actor spawns", spawn.get("ok") is True and bool(actor_path),
          json.dumps(spawn)[:200])
    guid = None
    if actor_path:
        # Deliberate M.raw_post, not scratch_confirm - see module docstring. actor_path is proven safe
        # by construction one call above (this test spawned it, this exact run).
        r = M.raw_post("add_sequence_possessable", {"path": lspath, "actorPath": actor_path, "confirm": True})
        check("T971 the real bind succeeds", r.get("ok") is True, json.dumps(r)[:300])
        guid = r.get("guid")
        check("T971 a real binding guid is reported", bool(guid), json.dumps(r)[:250])

        dup = M.raw_post("add_sequence_possessable", {"path": lspath, "actorPath": actor_path, "confirm": True})
        check("T971 binding the SAME actor a second time does not silently duplicate",
              dup.get("ok") is False or dup.get("guid") == guid, json.dumps(dup)[:250])

    # ------------------------------------------------------------------ T972 add_sequence_track
    print("\n=== T972: add_sequence_track - a real track against the real guid ===")
    if guid:
        r = M.raw_post("add_sequence_track", {
            "path": lspath, "guid": guid,
            "trackClass": "/Script/MovieSceneTracks.MovieScene3DTransformTrack", "confirm": True})
        check("T972 succeeds", r.get("ok") is True, json.dumps(r)[:300])
        check("T972 reports a real trackCount", (r.get("trackCount") or 0) > 0, r.get("trackCount"))

        bad = M.raw_post("add_sequence_track", {
            "path": lspath, "guid": "DEADBEEF00004444DEADBEEF00004444",
            "trackClass": "/Script/MovieSceneTracks.MovieScene3DTransformTrack", "confirm": True})
        check("T972 an unknown guid is refused", bad.get("ok") is False, json.dumps(bad)[:200])

    # ------------------------------------------------------------------ T973 list_sequence_bindings
    print("\n=== T973: list_sequence_bindings reflects both writes, read back independently ===")
    bindings = M.call("list_sequence_bindings", {"path": lspath})
    check("T973 succeeds", bindings.get("ok") is True, json.dumps(bindings)[:200])
    found = next((b for b in (bindings.get("bindings") or []) if b.get("guid") == guid), None)
    check("T973 the real binding is really there", bool(found), json.dumps(bindings.get("bindings"))[:250])
    if found:
        check("T973 and really carries the track this suite added",
              found.get("trackCount", 0) > 0, found.get("trackCount"))

    SC.confirm_call("delete_asset", {"path": lspath})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
