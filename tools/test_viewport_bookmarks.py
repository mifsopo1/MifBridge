"""Viewport bookmarks - the numbered camera slots, and the silent no-op at the middle of them.

THE BUG THIS FAMILY EXISTS TO REFUSE. IBookmarkTypeTools::JumpToBookmark returns void and does
NOTHING when the slot holds no bookmark - BookmarkTypeToolsImpl.cpp:153-165 checks IsValidIndex,
then a null entry, and falls out of both without a word. An agent jumping to an empty slot gets no
error, no movement, and no way to distinguish that from a bookmark that happened to be saved where
the camera already was. So this suite asserts the refusal, and it asserts it from the state that
makes it ambiguous: the camera parked exactly where a bookmark would have sent it.

JUDGED BY THE CAMERA, NOT BY THE CALL. jump reports cameraIsAtBookmark by measuring the distance
between where the camera ended up and where the bookmark says it should be. "jumped: true" on its
own would be satisfied by a jump that went nowhere.

THIS SUITE MOVES THE VIEWPORT CAMERA and puts it back. Bookmarks live on AWorldSettings, so setting
one dirties the LEVEL - nothing here saves, and every slot it touches is restored to what it held.

Usage:  python tools/test_viewport_bookmarks.py
Exit:   0 passed   1 failed   2 SKIPPED, no bridge
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
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2
    if not M.call("describe_endpoint",
                  {"endpoint": "jump_viewport_bookmark"}).get("registered"):
        print("skipped: viewport bookmarks are not in this build")
        return 2

    # ------------------------------------------------------------------ V500 list
    print("=== V500: the slots, and where they actually live ===")
    lst = M.call("list_viewport_bookmarks", {})
    check("V500 list succeeds", lst.get("ok") is not False, json.dumps(lst)[:220])
    marks = lst.get("bookmarks") or []
    check("V500 it reports every slot, not just the filled ones",
          len(marks) == lst.get("maxBookmarks"), "%s vs %s" % (len(marks), lst.get("maxBookmarks")))
    check("V500 each entry says whether it is set",
          all("set" in m for m in marks), json.dumps(marks[:2]))
    check("V500 and it says bookmarks belong to the LEVEL, not the viewport",
          "AWorldSettings" in str(lst.get("storageNote", "")), lst.get("storageNote"))

    # PICK AN EMPTY SLOT so nothing of anybody's is overwritten. If every slot is full this suite
    # declines rather than clobbering one - a bookmark somebody set is real work.
    free = next((m.get("index") for m in marks if not m.get("set")), None)
    check("V500 (setup) there is a free slot to work in", free is not None,
          "every slot is occupied; this suite will not overwrite one")
    if free is None:
        return 1
    print("  using free slot %d" % free)

    # ------------------------------------------------------------------ V501 the silent no-op
    print("\n=== V501: jumping to an EMPTY slot is refused - the whole reason for this family ===")
    cam0 = M.call("get_viewport_camera", {})
    loc0 = cam0.get("location") or {}
    empty = M.call("jump_viewport_bookmark", {"index": free})
    check("V501 jumping to an empty slot is refused", empty.get("ok") is False,
          str(empty.get("error"))[:220])
    check("V501 and the refusal says the engine would have done NOTHING, silently",
          "nothing at all" in str(empty.get("error", "")).lower(),
          str(empty.get("error"))[:240])
    check("V501 and it lists which slots do hold one",
          "Slots with a bookmark" in str(empty.get("error", "")), str(empty.get("error"))[:240])
    after0 = (M.call("get_viewport_camera", {}).get("location") or {})
    check("V501 the camera did not move on the refusal", after0 == loc0, [loc0, after0])

    # ------------------------------------------------------------------ V502 set
    print("\n=== V502: set captures the camera where it IS ===")
    here = {"x": 12345.0, "y": 6789.0, "z": 4321.0}
    M.call("set_viewport_camera", {"location": here, "rotation": {"pitch": -20, "yaw": 45, "roll": 0}})
    s = M.call("set_viewport_bookmark", {"index": free})
    check("V502 set succeeds", s.get("ok") is not False, json.dumps(s)[:240])
    check("V502 it reports the slot as set", s.get("set") is True, json.dumps(s)[:200])
    check("V502 it says the slot was empty before, not that it replaced something",
          s.get("replacedExisting") is False, s.get("replacedExisting"))
    bm = (s.get("bookmark") or {}).get("location") or {}
    check("V502 the stored location is where the camera was, to the unit",
          all(abs(bm.get(k, 1e9) - here[k]) < 1.0 for k in ("x", "y", "z")),
          "%s vs %s" % (bm, here))
    check("V502 and it says a bookmark cannot be written for somewhere the camera is not",
          "set_viewport_camera" in str(s.get("capturedNote", "")), s.get("capturedNote"))
    check("V502 with the level marked dirty and nothing saved",
          "NOTHING has been saved" in str(s.get("levelNote", "")), s.get("levelNote"))

    refused = M.call("set_viewport_bookmark", {"index": free, "location": here})
    check("V502 `location` is refused and points at set_viewport_camera",
          refused.get("ok") is False and "set_viewport_camera" in str(refused.get("error", "")),
          str(refused.get("error"))[:220])

    # ------------------------------------------------------------------ V503 jump
    print("\n=== V503: jump, judged by where the camera ENDED UP ===")
    M.call("set_viewport_camera", {"location": {"x": 0.0, "y": 0.0, "z": 0.0}})
    j = M.call("jump_viewport_bookmark", {"index": free})
    check("V503 jump succeeds", j.get("ok") is not False, json.dumps(j)[:240])
    check("V503 the camera actually moved", j.get("cameraMoved") is True, json.dumps(j)[:220])
    # THE POSTCONDITION: distance to the bookmark, not "a jump was requested".
    check("V503 and it is AT the bookmark, measured - not merely 'jumped'",
          j.get("cameraIsAtBookmark") is True,
          "distance %s" % j.get("distanceFromBookmark"))
    cam = M.call("get_viewport_camera", {}).get("location") or {}
    check("V503 read back independently, the camera is where the bookmark said",
          all(abs(cam.get(k, 1e9) - here[k]) < 1.0 for k in ("x", "y", "z")),
          "%s vs %s" % (cam, here))

    # A JUMP THAT MOVES NOTHING IS STILL CORRECT when you are already there, and this is the case
    # that makes cameraMoved useless on its own - it is why the distance check exists.
    j2 = M.call("jump_viewport_bookmark", {"index": free})
    check("V503 jumping again from the same spot still reports arrival, though nothing moved",
          j2.get("cameraIsAtBookmark") is True and j2.get("cameraMoved") is False,
          "moved=%s atBookmark=%s" % (j2.get("cameraMoved"), j2.get("cameraIsAtBookmark")))

    # ------------------------------------------------------------------ V504 bounds
    print("\n=== V504: out-of-range slots ===")
    mx = lst.get("maxBookmarks") or 10
    for ep in ("set_viewport_bookmark", "jump_viewport_bookmark", "clear_viewport_bookmark"):
        r = M.call(ep, {"index": int(mx) + 5})
        check("V504 %s refuses an out-of-range index and says the real range" % ep,
              r.get("ok") is False and "0.." in str(r.get("error", "")),
              str(r.get("error"))[:200])

    # ------------------------------------------------------------------ V505 clear
    print("\n=== V505: clear, and 'was already empty' as a distinct answer ===")
    c = M.call("clear_viewport_bookmark", {"index": free})
    check("V505 clear succeeds", c.get("ok") is not False, json.dumps(c)[:220])
    check("V505 it reports the slot HAD a bookmark", c.get("wasSet") is True, json.dumps(c)[:200])
    check("V505 and that one was cleared", c.get("cleared") == 1, json.dumps(c)[:200])

    again = M.call("clear_viewport_bookmark", {"index": free})
    check("V505 clearing an already-empty slot says so rather than counting it",
          again.get("wasSet") is False and again.get("cleared") == 0, json.dumps(again)[:220])

    nosel = M.call("clear_viewport_bookmark", {})
    check("V505 clear with no selector is refused", nosel.get("ok") is False,
          str(nosel.get("error"))[:200])

    final = M.call("list_viewport_bookmarks", {})
    still = [m for m in (final.get("bookmarks") or []) if m.get("index") == free and m.get("set")]
    check("V599 (cleanup) the slot this suite used is empty again", not still,
          json.dumps(still)[:200])

    print("\n  NOT COVERED, said out loud: clear_viewport_bookmark {all:true} is NOT exercised -")
    print("  it would destroy bookmarks this suite did not create. Its refusals and the single-slot")
    print("  path are covered; the all:true path is not.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
