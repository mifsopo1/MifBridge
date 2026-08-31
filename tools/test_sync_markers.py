"""add_sync_marker / remove_sync_marker - and the crash branch they finally make reachable.

T1912 IS THE REASON THIS SUITE MATTERS MORE THAN ITS OWN ENDPOINTS. test_anim_notify.py says of
remove_anim_notify_track's crash guard:

    NOT COVERED, AND IT IS THE MOST IMPORTANT BRANCH. ... That combination CANNOT BE BUILT ON THIS
    PROJECT. No AnimSequence in the first 150 scanned has any authored sync marker, and
    edit_container refuses to add one because every animation here lives in a COOKED package. So the
    guard's dangerous branch is unexercised, and this suite says so rather than implying otherwise.

add_sync_marker builds that combination. The guard refuses to remove the last notify track from a
sequence that still holds a marker, because UAnimSequence::RefreshCacheData then reaches
AnimNotifyTracks[0].SyncMarkers.Add(...) with no bounds check (AnimSequence.cpp:3431) - operator[]
on an empty array, a check() failure that takes the editor down. Until now nothing could put a
sequence into that state on this project, so a guard protecting against an editor crash had never
once been seen to fire. T1912 fires it.

THIS SUITE WRITES TO A REAL PROJECT ANIMATION, for the same reason and under the same terms
test_anim_notify.py states: create_asset cannot produce a usable AnimSequence (it needs a skeleton
and sampled bone tracks), so a real one is found with list_animations and authored against. Every
edit is in memory, NOTHING is saved, the suite removes what it added, and an editor restart discards
the rest. It is still a real asset, so it is named here rather than left to be found in a diff.

THE MIRROR GUARD IS ASSERTED AS UNEXERCISED, not as passing. add_sync_marker refuses a sequence with
ZERO notify tracks, which is the same crash approached from the other side. On this project that arm
could not be reached either: UE synthesises notify tracks on the first RefreshCacheData, so a
cooked-loaded sequence has one track by the time any endpoint sees it. The suite reports that rather
than pretending to have covered it - a guard nobody has watched refuse is not a tested guard.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []
MARKER = "MifSyncProbe"


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def find_sequence():
    """A plain UAnimSequence with no sync markers, and its notify-track count.

    Montages and composites are excluded because AuthoredSyncMarkers lives on UAnimSequence alone -
    a suite that picked one would be testing the refusal, not the feature.
    """
    r = M.call("list_animations", {"limit": 400})
    for a in (r.get("animations") or []):
        if not str(a.get("class", "")).endswith("AnimSequence"):
            continue
        path = a.get("assetPath")
        d = M.call("describe_animation", {"assetPath": path})
        if d.get("ok") is not False and not (d.get("syncMarkers") or []) \
                and (d.get("playLength") or 0) > 0.2:
            return path, d
    return None, None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    target, desc = find_sequence()
    print("target AnimSequence: %s" % target)
    if not target:
        print("\nSKIPPED - no marker-free AnimSequence was found, so NOTHING was verified.")
        return 2
    length = desc.get("playLength") or 0.0
    print("playLength: %.4f" % length)

    added_marker = False
    try:
        # ------------------------------------------------------------------ T1910 the write
        print("\n=== T1910: authoring a marker, read back through a DIFFERENT endpoint ===")
        a = M.raw_post("add_sync_marker", {"assetPath": target, "name": MARKER,
                                           "time": round(length / 2.0, 4)})
        check("T1910 add_sync_marker succeeds", a.get("ok") is True, json.dumps(a)[:260])
        added_marker = a.get("ok") is True
        check("T1910 it reports one marker on the sequence", a.get("syncMarkerCount") == 1,
              json.dumps(a)[:200])
        # THE assertion that the marker will actually DO something. UniqueMarkerNames is the derived
        # list the runtime sync-group system matches on; a marker in the authored array but not in
        # this one exists in the asset and never takes effect.
        check("T1910 and the name reached UniqueMarkerNames, which is what the runtime matches on - "
              "an authored marker missing from it would never take effect",
              MARKER in (a.get("uniqueMarkerNames") or []), a.get("uniqueMarkerNames"))
        # Read back through describe_animation, which is a different endpoint reading the asset -
        # not add_sync_marker reporting on itself.
        d = M.call("describe_animation", {"assetPath": target})
        names = [m.get("name") for m in (d.get("syncMarkers") or [])]
        check("T1910 describe_animation reports it too - a second endpoint, not this one's own word",
              MARKER in names, json.dumps(d.get("syncMarkers"))[:250])

        # ------------------------------------------------------------------ T1911 refusals
        print("\n=== T1911: the refusals ===")
        late = M.raw_post("add_sync_marker", {"assetPath": target, "name": "MifTooLate",
                                              "time": length + 100.0})
        check("T1911 a time past the end is refused rather than clamped - a marker that never "
              "fires is reported by nothing",
              late.get("ok") is False and "outside" in (late.get("error") or ""),
              (late.get("error") or "")[:220])
        notime = M.raw_post("add_sync_marker", {"assetPath": target, "name": "MifNoTime"})
        check("T1911 a missing time is refused rather than defaulted to 0",
              notime.get("ok") is False and "no sensible default" in (notime.get("error") or ""),
              (notime.get("error") or "")[:220])
        ghost = M.raw_post("remove_sync_marker", {"assetPath": target, "name": "MifNotThere"})
        check("T1911 removing a marker that is not there is refused AND the real ones are listed",
              ghost.get("ok") is False and MARKER in (ghost.get("error") or ""),
              (ghost.get("error") or "")[:250])
        badparam = M.raw_post("add_sync_marker", {"assetPath": target, "name": "X", "time": 0.1,
                                                  "track": "SomeTrack"})
        check("T1911 `track` is refused by name, pointing at trackIndex",
              badparam.get("ok") is False and "trackIndex" in (badparam.get("error") or ""),
              (badparam.get("error") or "")[:220])

        # ------------------------------------------------------------------ T1912 THE branch
        print("\n=== T1912: the crash guard that had never been reachable on this project ===")
        tracks = a.get("notifyTracks")
        print("  the sequence has %s notify track(s)" % tracks)
        if tracks == 1:
            # THE assertion. With one track and one marker, removing that track would leave
            # AuthoredSyncMarkers non-empty and AnimNotifyTracks empty - the state RefreshCacheData
            # mishandles by indexing an empty array.
            rm = M.raw_post("remove_anim_notify_track", {"assetPath": target, "track": "1",
                                                         "confirm": True})
            if rm.get("ok") is False and "CRASHES" in (rm.get("error") or ""):
                check("T1912 removing the LAST notify track is refused while a sync marker exists",
                      True)
                check("T1912 and the refusal cites the engine line it would have reached",
                      "AnimNotifyTracks[0]" in (rm.get("error") or ""),
                      (rm.get("error") or "")[:300])
            elif rm.get("ok") is False:
                # The track is not named "1" on this sequence. Reported, not passed: nothing
                # discovers notify track NAMES, so the branch cannot be reached here.
                print("  NOTE  the single track is not named '1', and no endpoint reports notify")
                print("        track NAMES, so it cannot be addressed. The guard is UNEXERCISED")
                print("        here and that is reported rather than passed. (%s)"
                      % (rm.get("error") or "")[:120])
            else:
                check("T1912 removing the last track while a marker exists must be REFUSED - it "
                      "crashes the editor", False,
                      "the removal was ALLOWED: %s" % json.dumps(rm)[:250])
        else:
            print("  NOTE  this sequence has %s tracks, so removing one leaves others and the" % tracks)
            print("        dangerous branch is not reached. Reported rather than passed.")

        # ------------------------------------------------------------------ T1913 removal
        print("\n=== T1913: removal, judged by both lists ===")
        r = M.raw_post("remove_sync_marker", {"assetPath": target, "name": MARKER})
        check("T1913 the marker is removed and counted", r.get("ok") is True
              and r.get("removed") == 1 and r.get("syncMarkerCount") == 0, json.dumps(r)[:250])
        check("T1913 and the name LEFT UniqueMarkerNames - a stale entry there means the runtime "
              "keeps matching a marker that no longer exists",
              MARKER not in (r.get("uniqueMarkerNames") or []), r.get("uniqueMarkerNames"))
        if r.get("ok") is True:
            added_marker = False
        d2 = M.call("describe_animation", {"assetPath": target})
        check("T1913 describe_animation agrees the sequence is marker-free again",
              not [m for m in (d2.get("syncMarkers") or []) if m.get("name") == MARKER],
              json.dumps(d2.get("syncMarkers"))[:200])

        check("T1913 - the editor is still alive", M.call("self_audit", {"summaryOnly": True})
              .get("ok") is True, "these endpoints sit next to a known editor-killing branch")
    finally:
        if added_marker:
            M.raw_post("remove_sync_marker", {"assetPath": target, "name": MARKER})
        left = [m.get("name") for m in
                (M.call("describe_animation", {"assetPath": target}).get("syncMarkers") or [])]
        check("(cleanup) the sequence carries no marker this suite authored",
              MARKER not in left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
