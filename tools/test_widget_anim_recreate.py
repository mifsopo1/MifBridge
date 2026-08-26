"""Remove then recreate a WidgetAnimation with the same name - the crash reported 2026-08-25.

REPORTED against QOLCrafting_P / WBP_QOL_DropZone: remove_widget_animation("ArrowLoop") followed by
add_widget_animation("ArrowLoop") in the same session killed the editor outright with
"Renaming an object ... on top of an existing object ... is not allowed" (Obj.cpp:265).

The cause was one missing line. remove_widget_animation did only WBP->Animations.Remove(Anim), which
detaches the animation from the array and leaves the UObject ALIVE under the widget blueprint, still
holding its object name. add_widget_animation then renamed its new animation onto that name.
FindAnimation searches only the Animations array, so nothing in the bridge could see the debris.

The engine's own delete path has the missing line, with the reason in a comment
(AnimationTabSummoner.cpp:823-829): Rename(NULL, GetTransientPackage()) before removing from the
array. add_widget_animation was written by mirroring the CREATE path in that same file; the DELETE
path was not mirrored, which is how the gap got in.

T250 is the reported reproduction, verbatim, and its real assertion is the last one: the editor is
still answering afterwards. Everything else in this file is downstream of that.

T252 covers the defence in depth. Fixing remove does not make the add-side guard redundant: an
animation hand-deleted in the UMG designer, or removed by an older build of this endpoint, leaves the
same debris, and a structured refusal beats a dead editor.

What T252 can and cannot reach is worth being precise about. Two guards sit in front of the rename, in
this order: the pre-existing check against WBP->Animations, which fires for a LIVE animation and gives
the friendlier "you already have one of these" answer, and behind it the object-collision check that
names the crash it is preventing. Only the first is exercised here, because the second now has no
reachable trigger THROUGH THIS BRIDGE - remove frees the name, and nothing else creates a direct child
of the widget blueprint under a caller-chosen name. That unreachability is the fix working rather than
a hole in the test, but it does mean the second guard is unproven against a real hand-deleted asset.

T253 covers set_widget_animation_range, which exists so the destructive sequence is not needed at all.
The reporter only wanted a 0.5s animation to become 1.5s; remove-and-recreate was the obvious path
only because nothing could edit a range in place.

HONEST LIMITATION. The report asks for save/reload persistence in the regression. This suite does NOT
save - the audit rules it runs under keep every write to scratch assets and save nothing - so it
verifies through compile and read-back instead. Persistence across a reload is therefore not covered
here and is worth checking by hand once.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def anim(bid, name="ArrowLoop"):
    for a in (M.call("list_widget_animations", {"blueprintId": bid}).get("animations") or []):
        if a.get("name") == name:
            return a
    return {}


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)
    p = "/Game/_MifWA/WBP_%d" % st
    bid = M.call("create_blueprint", {"path": p, "parentClass": "UserWidget",
                                      "blueprintType": "WidgetBlueprint"}).get("blueprintId")
    if not bid:
        print("setup failed")
        return 3
    M.call("add_tree_widget", {"blueprintId": bid, "widgetClass": "Image", "name": "Image_Arrow"})

    # ------------------------------------------------------------------ T250 the reported crash
    print("\n=== T250 [the report]: remove then recreate the same name ===")
    a = M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop",
                                        "endTime": 0.5, "displayRate": 20})
    check("T250 the animation is created", a.get("ok") is True, json.dumps(a)[:160])
    M.call("add_widget_animation_track", {"blueprintId": bid, "animationName": "ArrowLoop",
                                          "widgetName": "Image_Arrow"})

    r = M.call("remove_widget_animation", {"blueprintId": bid, "animationName": "ArrowLoop"})
    check("T250 it is removed", r.get("ok") is True, json.dumps(r)[:160])
    check("T250 removal says it left the Animations array",
          r.get("removedFromAnimationsArray") is True, r.get("removedFromAnimationsArray"))
    # The postcondition the report asked for, and the one that actually predicts a crash: detaching
    # from the array and freeing the NAME are different things.
    check("T250 and that the NAME is reusable", r.get("objectNameReusable") is True,
          "%s - if false, recreating would refuse or crash" % r.get("objectNameReusable"))

    b = M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop",
                                        "endTime": 1.5, "displayRate": 20})
    check("T250 recreating with the SAME name succeeds", b.get("ok") is True,
          (b.get("error") or json.dumps(b))[:200])
    # THE assertion. This used to be a dead editor and a ConnectionResetError.
    check("T250 and the editor is still alive afterwards", M.bridge_responsive() is True,
          "the bridge stopped answering - the editor died, which is the reported bug")

    v = anim(bid)
    check("T250 the recreated animation reads back", v.get("name") == "ArrowLoop", json.dumps(v)[:160])
    check("T250 with the NEW range, not the old one",
          abs((v.get("endTime") or 0) - 1.5) < 0.01,
          "endTime=%s (the removed one was 0.5)" % v.get("endTime"))
    c = M.call("compile", {"blueprintId": bid})
    check("T250 and the widget compiles clean",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:150]))

    # Twice more, because a leak of this kind usually survives one round.
    ok = True
    for i in range(2):
        M.call("remove_widget_animation", {"blueprintId": bid, "animationName": "ArrowLoop"})
        q = M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop", "endTime": 1.0})
        ok = ok and q.get("ok") is True
    check("T250 the cycle survives repetition", ok and M.bridge_responsive() is True,
          "a name-holding leak would show on the second or third round")

    # ------------------------------------------------------------------ T251 removal is real
    print("\n=== T251: removal actually removes ===")
    n_before = len(M.call("list_widget_animations", {"blueprintId": bid}).get("animations") or [])
    M.call("add_widget_animation", {"blueprintId": bid, "name": "Second", "endTime": 1.0})
    check("T251 a second animation can coexist",
          len(M.call("list_widget_animations", {"blueprintId": bid}).get("animations") or []) == n_before + 1,
          "count did not grow")
    d = M.call("remove_widget_animation", {"blueprintId": bid, "animationName": "Second"})
    check("T251 removing it reports the remaining count",
          d.get("remaining") == n_before, "%s vs %s" % (d.get("remaining"), n_before))
    check("T251 and it is gone from the listing", anim(bid, "Second") == {}, "still listed")

    # ------------------------------------------------------------------ T252 the add-side guard
    print("\n=== T252: creating over a LIVE name refuses instead of crashing ===")
    dup = M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop", "endTime": 1.0})
    check("T252 a name already in use is refused", dup.get("ok") is False, json.dumps(dup)[:160])
    # The friendlier pre-existing array check fires FIRST and should: for a live animation, "you
    # already have one of these" is the useful answer. The crash-warning guard sits behind it and
    # covers only DEBRIS - an object holding the name that is NOT in the Animations array.
    check("T252 and it names the animation that is in the way",
          "already has an animation named" in (dup.get("error") or ""), (dup.get("error") or "")[:200])
    check("T252 and points at how to see it",
          "list_widget_animations" in (dup.get("error") or ""), (dup.get("error") or "")[:200])
    check("T252 and that nothing was created",
          "NOTHING was created" in (dup.get("error") or ""), (dup.get("error") or "")[:160])
    # The refusal must not have damaged the animation it refused to overwrite.
    v = anim(bid)
    check("T252 the existing animation is untouched", v.get("name") == "ArrowLoop", json.dumps(v)[:150])
    check("T252 and the editor is still alive", M.bridge_responsive() is True, "editor died on a refusal")

    # ------------------------------------------------------------------ T253 the range endpoint
    print("\n=== T253: changing a range in place, which is what was actually wanted ===")
    M.call("remove_widget_animation", {"blueprintId": bid, "animationName": "ArrowLoop"})
    M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop",
                                    "endTime": 0.5, "displayRate": 20})
    M.call("add_widget_animation_track", {"blueprintId": bid, "animationName": "ArrowLoop",
                                          "widgetName": "Image_Arrow"})
    M.call("set_widget_animation_keys", {"blueprintId": bid, "animationName": "ArrowLoop",
                                         "widgetName": "Image_Arrow", "channel": "Y",
                                         "keys": [{"time": 0, "value": 0}, {"time": 0.5, "value": 15}]})
    before = anim(bid)
    n_keys_before = (before.get("bindings") or [{}])[0].get("trackCount")

    s = M.call("set_widget_animation_range", {"blueprintId": bid, "animationName": "ArrowLoop",
                                              "endTime": 1.5})
    check("T253 the range is changed in place", s.get("ok") is True, json.dumps(s)[:180])
    check("T253 to the requested length", abs((s.get("endTime") or 0) - 1.5) < 0.01, s.get("endTime"))
    # Reporting what it WAS is what lets a caller confirm the right animation was changed.
    check("T253 and it reports the previous range",
          abs((s.get("previousEndTime") or 0) - 0.5) < 0.01, s.get("previousEndTime"))
    v = anim(bid)
    check("T253 the animation itself agrees", abs((v.get("endTime") or 0) - 1.5) < 0.01, v.get("endTime"))
    # The obvious wrong assumption: a longer range does NOT stretch the motion.
    check("T253 no key was moved", s.get("keysUnchanged") is True, s.get("keysUnchanged"))
    check("T253 and the note says so plainly",
          "no key moved" in (s.get("note") or ""), (s.get("note") or "")[:170])
    check("T253 the track survived the range change",
          (v.get("bindings") or [{}])[0].get("trackCount") == n_keys_before,
          "%s vs %s" % ((v.get("bindings") or [{}])[0].get("trackCount"), n_keys_before))
    c = M.call("compile", {"blueprintId": bid})
    check("T253 and it still compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s" % c.get("numErrors"))

    rate = M.call("set_widget_animation_range", {"blueprintId": bid, "animationName": "ArrowLoop",
                                                 "displayRate": 60})
    check("T253 displayRate alone can be changed",
          rate.get("ok") is True and abs((rate.get("displayRate") or 0) - 60) < 0.01,
          json.dumps(rate)[:160])
    check("T253 and changing it does not move the range",
          abs((rate.get("endTime") or 0) - 1.5) < 0.01, rate.get("endTime"))

    # ------------------------------------------------------------------ T254 guards
    print("\n=== T254: guards ===")
    for label, payload, expect in (
        ("nothing to change", {"blueprintId": bid, "animationName": "ArrowLoop"}, "nothing to change"),
        ("end before start", {"blueprintId": bid, "animationName": "ArrowLoop",
                              "startTime": 2.0, "endTime": 1.0}, "must be greater than"),
        ("zero rate", {"blueprintId": bid, "animationName": "ArrowLoop", "displayRate": 0},
         "positive number of frames"),
        ("unknown animation", {"blueprintId": bid, "animationName": "NoSuch_zz", "endTime": 1.0},
         "no animation named"),
    ):
        q = M.call("set_widget_animation_range", payload)
        check("T254 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T254 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])
        check("T254 %s changes nothing" % label, "NOTHING was changed" in (q.get("error") or ""),
              (q.get("error") or "")[:150])
    # Every refusal above must have left the range where it was.
    v = anim(bid)
    check("T254 the range survived every refusal", abs((v.get("endTime") or 0) - 1.5) < 0.01,
          v.get("endTime"))
    q = M.call("set_widget_animation_range", {"blueprintId": bid, "animationName": "ArrowLoop",
                                              "length": 2.0})
    check("T254 a 'length' parameter is pointed at endTime",
          q.get("ok") is False and "endTime" in (q.get("error") or ""), (q.get("error") or "")[:160])

    M.call("delete_asset", {"path": p})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
