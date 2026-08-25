"""UMG WidgetAnimation authoring, part 1: create and read back.

The reported gap (QOLCrafting_P / WBP_QOL_DropZone) was that MifBridge could not author a UMG
animation at all. The reference the report extracted from the cooked vanilla widget is used here as
the test case, because it pins the exact invariant that fails silently:

    ArrowLoop, 1.5 seconds, display rate 20 fps, tick resolution 60000/1

T80 checks the TIME conversion against those numbers. A MovieScene stores times as FFrameNumber in
TICK space, so 1.5s at 60000/1 is tick 90000, and the playback range end is exclusive so it stores
90001 - the same +1 the editor's own Add Animation does. Passing seconds straight through, or passing
display frames (30), would land somewhere else entirely and still report success. That is the
add_timeline failure mode, which is why this suite exists before the track/key endpoints rather than
after them.

T82 is the other silent one: an animation that is not in WidgetBlueprint->Animations exists, compiles,
and is simply not in the widget.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    stamp = int(time.time() % 100000)
    path = "/Game/_MifAnim/WBP_Anim_%d" % stamp
    r = M.call("create_blueprint", {"path": path, "parentClass": "UserWidget",
                                    "blueprintType": "WidgetBlueprint"})
    bid = r.get("blueprintId")
    if not bid:
        print("setup failed:", json.dumps(r)[:300])
        return 3
    print("widget blueprint:", bid)

    # ------------------------------------------------------------------ T80 the reported case
    print("\n=== T80: the reported ArrowLoop parameters, and the tick conversion ===")
    a = M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop",
                                        "startTime": 0.0, "endTime": 1.5, "displayRate": 20})
    anim = a.get("animation") or {}
    print("  ", json.dumps(anim)[:300])
    check("T80 created", a.get("ok") is True, json.dumps(a)[:250])
    check("T80 it has a MovieScene", anim.get("hasMovieScene") is True, json.dumps(anim)[:200])
    check("T80 display rate is 20 fps as asked", anim.get("displayRate") == "20/1", anim.get("displayRate"))
    check("T80 tick resolution matches the cooked reference (60000/1)",
          anim.get("tickResolution") == "60000/1", anim.get("tickResolution"))
    # THE conversion check. 1.5s * 60000 = 90000, +1 because the range end is exclusive.
    check("T80 1.5s stored as tick 90001, not as 1.5 and not as frame 30",
          anim.get("endTick") == 90001,
          "endTick=%r (1.5 would mean seconds leaked through; 31 would mean display frames did)"
          % (anim.get("endTick"),))
    check("T80 start is tick 0", anim.get("startTick") == 0, anim.get("startTick"))

    # ------------------------------------------------------------------ T81 read-back
    print("\n=== T81: it reads back through the blueprint, not just through the response ===")
    l = M.call("list_widget_animations", {"blueprintId": bid})
    names = [x.get("name") for x in (l.get("animations") or [])]
    check("T81 listed", l.get("ok") is True and l.get("count") == 1, json.dumps(l)[:220])
    check("T81 by name", names == ["ArrowLoop"], str(names))
    listed = (l.get("animations") or [{}])[0]
    check("T81 the listing agrees with the create response",
          listed.get("endTick") == anim.get("endTick")
          and listed.get("tickResolution") == anim.get("tickResolution"),
          "%s vs %s" % (json.dumps(listed)[:120], json.dumps(anim)[:120]))

    # ------------------------------------------------------------------ T82 membership
    print("\n=== T82 [the silent one]: a second animation, and both are really attached ===")
    b = M.call("add_widget_animation", {"blueprintId": bid, "name": "FadeIn", "endTime": 0.25})
    check("T82 second created", b.get("ok") is True, json.dumps(b)[:220])
    l2 = M.call("list_widget_animations", {"blueprintId": bid})
    names2 = sorted(x.get("name") for x in (l2.get("animations") or []))
    check("T82 both are in WidgetBlueprint->Animations", names2 == ["ArrowLoop", "FadeIn"], str(names2))
    # 0.25s at 60000/1 = 15000, +1
    fade = [x for x in (l2.get("animations") or []) if x.get("name") == "FadeIn"]
    check("T82 the short one converted independently (0.25s -> 15001)",
          bool(fade) and fade[0].get("endTick") == 15001,
          fade[0].get("endTick") if fade else "missing")

    # ------------------------------------------------------------------ T83 refusals
    print("\n=== T83: it refuses rather than making a mess ===")
    dup = M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop"})
    check("T83 duplicate name refused", dup.get("ok") is False, json.dumps(dup)[:200])
    check("T83 and says nothing was created", "NOTHING was created" in (dup.get("error") or ""),
          (dup.get("error") or "")[:160])
    bad = M.call("add_widget_animation", {"blueprintId": bid, "name": "Backwards",
                                          "startTime": 2.0, "endTime": 1.0})
    check("T83 end before start refused", bad.get("ok") is False, json.dumps(bad)[:200])
    l3 = M.call("list_widget_animations", {"blueprintId": bid})
    check("T83 the refusals left nothing behind", l3.get("count") == 2, l3.get("count"))

    # ------------------------------------------------------------------ T84 compiles
    print("\n=== T84: the widget still compiles ===")
    c = M.call("compile", {"blueprintId": bid})
    check("T84 compiles clean", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:250]))

    M.call("delete_asset", {"path": path})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
