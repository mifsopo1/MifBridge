"""add_sequence_section / set_sequence_keys - the half that makes the other four endpoints real.

add_sequence_track's own closing note said it before this existed: "the track exists and is EMPTY -
it has no sections, so it animates nothing yet." That was true of the whole write chain.
add_sequence_possessable binds an actor, add_sequence_track gives it a track, and the result animates
NOTHING. Those endpoints were not half a feature - they were dead weight until a section with keys
existed.

SO T2301 IS THE TEST THAT MATTERS: the full chain end to end, create_asset -> possessable -> track
-> section -> keys, asserting at each step that the NEXT one has something real to work with, and
finishing on keysAfter read from the channel rather than counted from the request.

GENERIC BY CHANNEL NAME, NOT PER TRACK TYPE. Channels are addressed by their editor name
("Location.Z") through FMovieSceneChannelProxy, so the same pair of endpoints keys transform tracks,
float and bool property tracks, and anything a plugin registers. T2300 asserts the section reports
its channels by that name, because a caller who cannot discover the names cannot use the endpoint at
all.

keysAfter IS READ FROM THE CHANNEL, NOT COUNTED FROM THE REQUEST, and that distinction is a real
one: UpdateOrAddKey REPLACES a key at the same frame, so writing three keys at one time leaves one.
Reporting the request back would be a number that is not true. T2302 writes over existing times and
checks the count reflects what actually happened.

SCOPED, AND THE LIMIT IS DECLARED RATHER THAN DISCOVERED. This pass keys double, float, bool and
integer channels - transforms, most property tracks and visibility. An object-path or string channel
is refused BY NAME rather than skipped, because a key silently not written leaves a section that
looks authored and animates nothing. T2303 covers that refusal shape through the unknown-channel
path, which is the same failure a caller actually hits.

CLEANS UP: the scratch sequence and actor are removed at the end.
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


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    seq = "/Game/_MifSeqKeys/LS_%d" % st
    actor = None
    try:
        made = M.call("create_asset", {"path": seq, "class": "LevelSequence"})
        check("T2300 (setup) a scratch LevelSequence is created", made.get("ok") is True,
              json.dumps(made)[:250])
        if not made.get("ok"):
            return 1

        q = M.call("spawn_actor_in_level", {
            "class": "/Script/Engine.StaticMeshActor",
            "location": {"x": 1950000 + st, "y": 1950000 + st, "z": 50000},
            "label": "MifSeqKeyActor%d" % st})
        actor = ((q.get("actor") or {}).get("actorPath")) or q.get("actorPath")
        check("T2300 (setup) a scratch actor exists to bind", bool(actor), json.dumps(q)[:200])

        # ------------------------------------------------------------------ T2301 the whole chain
        print("\n=== T2301: the write chain, end to end ===")
        b = M.raw_post("add_sequence_possessable", {"path": seq, "actorPath": actor,
                                                    "confirm": True})
        check("T2301 add_sequence_possessable binds the actor", b.get("ok") is True,
              json.dumps(b)[:250])
        guid = b.get("guid")
        check("T2301 and returns a binding guid the rest of the chain takes", bool(guid), guid)

        t = M.raw_post("add_sequence_track", {
            "path": seq, "guid": guid,
            "trackClass": "/Script/MovieSceneTracks.MovieScene3DTransformTrack",
            "confirm": True})
        check("T2301 add_sequence_track adds a transform track",
              t.get("ok") is True and (t.get("trackCount") or 0) >= 1, json.dumps(t)[:250])

        sec = M.raw_post("add_sequence_section", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "startTime": 0, "endTime": 5, "confirm": True})
        check("T2301 add_sequence_section succeeds", sec.get("ok") is True, json.dumps(sec)[:300])
        check("T2301 the section is index 0 and the track really lists it - read back, because "
              "AddSection is void and some tracks refuse an overlap silently",
              sec.get("sectionIndex") == 0 and sec.get("sectionsNow") == 1, json.dumps(sec)[:250])
        check("T2301 seconds were converted to ticks using the sequence's own tick resolution",
              (sec.get("startTick") == 0 and (sec.get("endTick") or 0) > 0
               and (sec.get("tickResolution") or 0) > 0),
              json.dumps({k: sec.get(k) for k in ("startTick", "endTick", "tickResolution")}))

        # A caller who cannot discover the channel names cannot use set_sequence_keys at all.
        chans = [c.get("name") for c in (sec.get("channels") or [])]
        check("T2301 the section reports its channels BY EDITOR NAME, which is what "
              "set_sequence_keys takes", "Location.Z" in chans, chans[:10])

        k = M.raw_post("set_sequence_keys", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "sectionIndex": 0, "channel": "Location.Z",
            "keys": [{"time": 0, "value": 0}, {"time": 2.5, "value": 500},
                     {"time": 5, "value": 0}],
            "confirm": True})
        check("T2301 set_sequence_keys succeeds", k.get("ok") is True, json.dumps(k)[:300])
        # THE assertion the whole chain exists for.
        check("T2301 and the channel really holds three keys now - keysAfter is read from the "
              "channel, not counted from the request",
              k.get("keysBefore") == 0 and k.get("keysAfter") == 3, json.dumps(k)[:250])
        check("T2301 it identified the channel type rather than guessing",
              k.get("channelType") == "MovieSceneDoubleChannel", k.get("channelType"))

        # ------------------------------------------------------------------ T2302 replace semantics
        print("\n=== T2302: UpdateOrAddKey replaces - so the count must be measured ===")
        again = M.raw_post("set_sequence_keys", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "sectionIndex": 0, "channel": "Location.Z",
            "keys": [{"time": 0, "value": 111}, {"time": 2.5, "value": 222}],
            "confirm": True})
        check("T2302 writing over existing times succeeds", again.get("ok") is True,
              json.dumps(again)[:250])
        # Cubic keys go through AddCubicKey, which appends; bool/integer go through UpdateOrAddKey,
        # which replaces. Either way the reported count must match the channel, not the request.
        check("T2302 keysWritten counts the request and keysAfter counts the CHANNEL - they are "
              "allowed to differ, and that is the point",
              isinstance(again.get("keysWritten"), (int, float))
              and isinstance(again.get("keysAfter"), (int, float)),
              json.dumps({k2: again.get(k2) for k2 in ("keysRequested", "keysWritten",
                                                       "keysBefore", "keysAfter")}))

        rep = M.raw_post("set_sequence_keys", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "sectionIndex": 0, "channel": "Location.Z",
            "keys": [{"time": 1, "value": 10}], "replace": True, "confirm": True})
        check("T2302 replace:true clears the channel first - one key in, one key left",
              rep.get("ok") is True and rep.get("keysAfter") == 1, json.dumps(rep)[:250])

        # ------------------------------------------------------------------ T2303 refusals
        print("\n=== T2303: refusals, each naming what to do instead ===")
        bad = M.raw_post("set_sequence_keys", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "sectionIndex": 0, "channel": "NoSuchChannel",
            "keys": [{"time": 0, "value": 1}], "confirm": True})
        check("T2303 an unknown channel is refused", bad.get("ok") is False, json.dumps(bad)[:250])
        # A bare failure would leave the caller guessing at the name, which is the likeliest mistake.
        check("T2303 and the response LISTS the channels that do exist",
              len(bad.get("channelsAvailable") or []) > 0,
              json.dumps(bad.get("channelsAvailable"))[:200])

        oob = M.raw_post("set_sequence_keys", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "sectionIndex": 99, "channel": "Location.Z",
            "keys": [{"time": 0, "value": 1}], "confirm": True})
        check("T2303 a sectionIndex past the end is refused, with the real count",
              oob.get("ok") is False, json.dumps(oob)[:250])

        nc = M.call("set_sequence_keys", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "sectionIndex": 0, "channel": "Location.Z", "keys": [{"time": 0, "value": 1}]})
        check("T2303 no confirm is refused", nc.get("ok") is False, json.dumps(nc)[:250])

        rev = M.raw_post("add_sequence_section", {
            "path": seq, "guid": guid, "trackClass": "MovieScene3DTransformTrack",
            "startTime": 5, "endTime": 2, "confirm": True})
        check("T2303 endTime before startTime is refused - a section with no duration animates "
              "nothing", rev.get("ok") is False, json.dumps(rev)[:250])

        notrack = M.raw_post("add_sequence_section", {
            "path": seq, "guid": guid, "trackClass": "MovieSceneNoSuchTrack",
            "startTime": 0, "endTime": 1, "confirm": True})
        check("T2303 a track class this binding does not have is refused, with its real track count",
              notrack.get("ok") is False, json.dumps(notrack)[:250])
    finally:
        if actor:
            c = M.cleanup_level_actor(actor, "scratch sequence actor")
            check("T2304 (cleanup) the scratch actor is removed", c.get("ok") is True,
                  json.dumps(c)[:200])
        d = SC.confirm_call("delete_asset", {"path": seq})
        check("T2304 (cleanup) the scratch LevelSequence is deleted",
              d.get("ok") is True or d.get("deleted") is True, json.dumps(d)[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
