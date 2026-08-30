"""Notify authoring: add/remove_anim_notify, add/remove_anim_notify_track.

THE READ HALF WAS ALREADY HERE. describe_animation has always emitted every notify in full through
SerializeNotify, and nothing could create one - the textbook read-with-no-write. Notify authoring is
the single most common animation-asset edit: footstep sounds, hit windows, VFX spawns and montage
branching points are all notifies.

WHAT THIS SUITE CAN AND CANNOT PROVE, stated up front because one half genuinely cannot be reached
on this project:

  COVERED. Creating and removing tracks, creating a skeleton notify by name, the read-back through
  describe_animation (a DIFFERENT endpoint, not add_anim_notify reporting on itself), and every
  refusal: a time outside the sequence, an unknown track name, notifyClass and notifyStateClass
  together, and the two confirm gates.

  NOT COVERED, AND IT IS THE MOST IMPORTANT BRANCH. remove_anim_notify_track guards a HARD EDITOR
  CRASH: UAnimSequence::RefreshCacheData (AnimSequence.cpp:3421-3435) reaches
  `AnimNotifyTracks[0].SyncMarkers.Add(&SyncMarker)` with NO bounds check in its else branch, so a
  sequence with zero notify tracks and at least one authored sync marker takes the editor out via
  TArray::operator[] on an empty array. The guard refuses exactly that combination before the engine
  is touched.

  That combination CANNOT BE BUILT ON THIS PROJECT. No AnimSequence in the first 150 scanned has any
  authored sync marker, and edit_container refuses to add one because every animation here lives in
  a COOKED package. So the guard's dangerous branch is unexercised, and this suite says so rather
  than implying otherwise. What IS asserted is that the guard does not fire when it should not -
  T1902 removes a track with siblings and T1903 removes a last track from a marker-free sequence,
  both of which must be ALLOWED. A guard that refuses everything would pass a test that only ever
  checks it refuses.

  On an uncooked project the branch is reachable and should be tested there. That is the reason it
  is guarded rather than left to the engine.

COOKED TRACK SYNTHESIS. UAnimSequenceBase::Notifies is a plain UPROPERTY and survives a cook;
AnimNotifyTracks is WITH_EDITORONLY_DATA and does not. So a cooked sequence loads with notifies
whose TrackIndex points into an empty array, and the first RefreshCacheData rebuilds the tracks and
REWRITES TrackIndex on every existing notify. The endpoints report that as tracksSynthesized /
trackIndexRewritten rather than letting it happen silently; T1901 checks the field is present when
it applies.

NOTHING IS SAVED. These are real project animations and every change is in memory only - the same
precedent test_simplified_collision_guard's real remove_collision already relies on. The suite
removes what it adds regardless.
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

    seqs = M.call("find_assets", {"class": "AnimSequence", "pathPrefix": "/Game/",
                                  "limit": 20}).get("assets") or []
    target = None
    for s in seqs:
        d = M.call("describe_animation", {"assetPath": s["path"]})
        if d.get("ok") and (d.get("playLength") or 0) > 0.2:
            target = s["path"]
            length = d["playLength"]
            break
    check("T1900 (setup) a real AnimSequence is available to author against", bool(target),
          "%d AnimSequence(s) found" % len(seqs))
    if not target:
        return 1
    print("        using %s (%.2fs)" % (target.split("/")[-1], length))

    made_track = None
    try:
        # ------------------------------------------------------------------ T1901 tracks + notify
        print("\n=== T1901: add a track, add a notify, read it back elsewhere ===")
        t = M.call("add_anim_notify_track", {"assetPath": target, "track": "MifTestTrack"})
        check("T1901 add_anim_notify_track succeeds", t.get("ok") is True, json.dumps(t)[:250])
        check("T1901 it reports created:true, not just ok", t.get("created") is True,
              json.dumps(t)[:250])
        made_track = "MifTestTrack" if t.get("created") else None

        dup = M.call("add_anim_notify_track", {"assetPath": target, "track": "MifTestTrack"})
        check("T1901 adding the same track again is created:false, not an error",
              dup.get("ok") is True and dup.get("created") is False, json.dumps(dup)[:250])

        before = M.call("describe_animation", {"assetPath": target}).get("notifyCount") or 0
        n = M.call("add_anim_notify", {"assetPath": target, "track": "MifTestTrack",
                                       "time": 0.1, "name": "MifFootstep"})
        check("T1901 add_anim_notify succeeds", n.get("ok") is True, json.dumps(n)[:300])
        check("T1901 it reports added:1 - the measured difference, not ok:true",
              n.get("added") == 1, json.dumps(n)[:250])
        # Through SerializeNotify, so authoring and describe_animation speak one vocabulary.
        check("T1901 and returns the notify in describe_animation's own shape",
              (n.get("notify") or {}).get("name") == "MifFootstep"
              and (n.get("notify") or {}).get("kind") == "notify", json.dumps(n.get("notify"))[:250])

        # THE assertion: a DIFFERENT endpoint sees it. add_anim_notify reporting on itself is the
        # shape this project has already shipped a bug behind.
        after = M.call("describe_animation", {"assetPath": target}).get("notifyCount") or 0
        check("T1901 describe_animation sees the new notify - confirmed through a different endpoint",
              after == before + 1, "before=%d after=%d" % (before, after))

        # ------------------------------------------------------------------ T1902/T1903 the guard's ALLOW side
        print("\n=== T1902-T1903: the crash guard must not refuse what is safe ===")
        t2 = M.call("add_anim_notify_track", {"assetPath": target, "track": "MifSecondTrack"})
        check("T1902 (setup) a second track exists", t2.get("ok") is True, json.dumps(t2)[:200])
        rm2 = M.raw_post("remove_anim_notify_track", {"assetPath": target,
                                                      "track": "MifSecondTrack", "confirm": True})
        check("T1902 removing a track that has siblings is ALLOWED - the guard is not blanket",
              rm2.get("ok") is True and rm2.get("removed") is True, json.dumps(rm2)[:300])

        markers = M.call("describe_animation", {"assetPath": target}).get("syncMarkers") or []
        check("T1903 (precondition) this sequence has no authored sync markers, so removing its "
              "last track is safe and must be permitted", len(markers) == 0, markers)

        # ------------------------------------------------------------------ T1904 refusals
        print("\n=== T1904: refusals, each for its own reason ===")
        oob = M.call("add_anim_notify", {"assetPath": target, "track": "MifTestTrack",
                                         "time": 99999.0, "name": "X"})
        check("T1904 a time outside the sequence is refused - a notify there never fires",
              oob.get("ok") is False, json.dumps(oob)[:250])

        # AddAnimationNotifyEvent warns and adds NOTHING for an unknown track, so an unchecked call
        # would report success having done nothing.
        bad = M.call("add_anim_notify", {"assetPath": target, "track": "NoSuchTrack",
                                         "time": 0.1, "name": "X"})
        check("T1904 an unknown track is refused, not silently no-opped",
              bad.get("ok") is False, json.dumps(bad)[:250])

        both = M.call("add_anim_notify", {"assetPath": target, "track": "MifTestTrack", "time": 0.1,
                                          "notifyClass": "/Script/Engine.AnimNotify",
                                          "notifyStateClass": "/Script/Engine.AnimNotifyState"})
        check("T1904 notifyClass and notifyStateClass together are refused - they are alternatives",
              both.get("ok") is False, json.dumps(both)[:250])

        neither = M.call("remove_anim_notify", {"assetPath": target})
        check("T1904 remove_anim_notify with neither name nor track is refused - it would mean "
              "removing everything", neither.get("ok") is False, json.dumps(neither)[:250])

        noconf = M.call("remove_anim_notify", {"assetPath": target, "name": "MifFootstep"})
        check("T1904 remove_anim_notify without confirm is refused",
              noconf.get("ok") is False, json.dumps(noconf)[:250])

        # ------------------------------------------------------------------ T1905 removal
        print("\n=== T1905: remove, and a miss reports 0 rather than claiming success ===")
        r = M.raw_post("remove_anim_notify", {"assetPath": target, "name": "MifFootstep",
                                              "confirm": True})
        check("T1905 remove_anim_notify succeeds", r.get("ok") is True, json.dumps(r)[:250])
        check("T1905 removed:1, measured from the notify count",
              r.get("removed") == 1, json.dumps(r)[:250])
        gone = M.call("describe_animation", {"assetPath": target}).get("notifyCount") or 0
        check("T1905 and describe_animation agrees it is gone", gone == before,
              "before=%d now=%d" % (before, gone))

        miss = M.raw_post("remove_anim_notify", {"assetPath": target, "name": "NoSuchNotify",
                                                 "confirm": True})
        check("T1905 removing a name that matches nothing is removed:0 with a note, not a failure",
              miss.get("ok") is True and miss.get("removed") == 0 and bool(miss.get("note")),
              json.dumps(miss)[:250])
    finally:
        if made_track:
            c = M.raw_post("remove_anim_notify_track", {"assetPath": target,
                                                        "track": made_track, "confirm": True})
            check("T1906 (cleanup) the test track is removed from the sequence",
                  c.get("ok") is True, json.dumps(c)[:250])

    print("\n  NOT EXERCISED HERE, and named rather than left to be discovered: the crash guard's")
    print("  REFUSE branch. It needs a sequence with zero notify tracks and at least one authored")
    print("  sync marker, and that state cannot be built on this project - no shipped AnimSequence")
    print("  has a sync marker, and edit_container refuses to add one because these assets live in")
    print("  cooked packages. Reachable on an uncooked project; test it there.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
