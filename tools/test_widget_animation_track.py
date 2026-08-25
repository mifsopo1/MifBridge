"""UMG WidgetAnimation authoring, part 2: bind a widget, add a track, key it.

The test case IS the report. From the cooked vanilla DropUnpackageWidget, the ArrowLoop animation is
1.5s at display rate 20 with tick resolution 60000/1, animating Image_Arrow's
RenderTransform.Translation Y through five Cubic/Auto keys:

    0.00s -> 0     0.50s -> 15     0.95s -> 3     1.15s -> 0     1.50s -> 0

T87 checks those land on ticks 0 / 30000 / 57000 / 69000 / 90000. 0.95s -> 57000 is the one that
matters: seconds leaking through would give 0.95, display frames would give 19, and either would still
report success. That is the whole reason this suite pins numbers instead of just asserting ok:true.

T88 is the batch guarantee. A key list is validated in full before the channel is touched, so a bad
key at index 3 cannot leave three keys written and a curve half-authored.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []

# The reported source animation, verbatim.
ARROWLOOP = [(0.00, 0), (0.50, 15), (0.95, 3), (1.15, 0), (1.50, 0)]
EXPECTED_TICKS = [0, 30000, 57000, 69000, 90000]


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    stamp = int(time.time() % 100000)
    path = "/Game/_MifAnim/WBP_Track_%d" % stamp
    bid = M.call("create_blueprint", {"path": path, "parentClass": "UserWidget",
                                      "blueprintType": "WidgetBlueprint"}).get("blueprintId")
    if not bid:
        print("setup failed")
        return 3
    M.call("add_tree_widget", {"blueprintId": bid, "widgetClass": "Image", "name": "Image_Arrow"})
    M.call("add_widget_animation", {"blueprintId": bid, "name": "ArrowLoop",
                                    "endTime": 1.5, "displayRate": 20})
    print("widget blueprint:", bid)

    A = {"blueprintId": bid, "animationName": "ArrowLoop", "widgetName": "Image_Arrow"}

    # ------------------------------------------------------------------ T85 track
    print("\n=== T85: binding the widget and adding the transform track ===")
    t = M.call("add_widget_animation_track", dict(A))
    print("  ", json.dumps({k: v for k, v in t.items() if k != "animation"})[:260])
    check("T85 created", t.get("ok") is True, json.dumps(t)[:220])
    check("T85 it made a binding", t.get("createdBinding") is True, t.get("createdBinding"))
    check("T85 it made a track", t.get("createdTrack") is True, t.get("createdTrack"))
    anim = t.get("animation") or {}
    check("T85 the animation now has a possessable and a track",
          anim.get("possessableCount") == 1 and anim.get("trackCount") >= 1,
          "possessables=%s tracks=%s" % (anim.get("possessableCount"), anim.get("trackCount")))
    binds = anim.get("bindings") or []
    check("T85 the binding names the widget (not just a guid in the MovieScene)",
          len(binds) == 1 and binds[0].get("widgetName") == "Image_Arrow",
          json.dumps(binds)[:200])

    # ------------------------------------------------------------------ T86 idempotent
    print("\n=== T86: calling it again binds and creates nothing new ===")
    t2 = M.call("add_widget_animation_track", dict(A))
    check("T86 second call succeeds", t2.get("ok") is True, json.dumps(t2)[:200])
    check("T86 without a second binding", t2.get("createdBinding") is False, t2.get("createdBinding"))
    check("T86 without a second track", t2.get("createdTrack") is False, t2.get("createdTrack"))
    check("T86 and the same binding guid", t2.get("bindingGuid") == t.get("bindingGuid"),
          "%s vs %s" % (t2.get("bindingGuid"), t.get("bindingGuid")))

    # ------------------------------------------------------------------ T87 the reported keys
    print("\n=== T87 [the report]: the five ArrowLoop keys, in tick space ===")
    payload = dict(A)
    payload["channel"] = "Y"
    payload["keys"] = [{"time": tm, "value": v, "interp": "cubic"} for tm, v in ARROWLOOP]
    k = M.call("set_widget_animation_keys", payload)
    got = k.get("keys") or []
    print("  ticks:", [x.get("timeTick") for x in got])
    check("T87 five keys written", k.get("ok") is True and k.get("keysAfter") == 5,
          json.dumps(k)[:220])
    check("T87 every key on the right TICK (0.95s -> 57000, not 0.95 and not 19)",
          [x.get("timeTick") for x in got] == EXPECTED_TICKS,
          "%s vs expected %s" % ([x.get("timeTick") for x in got], EXPECTED_TICKS))
    check("T87 values preserved",
          [x.get("value") for x in got] == [v for _, v in ARROWLOOP],
          str([x.get("value") for x in got]))
    check("T87 seconds round-trip back",
          [round(x.get("time"), 4) for x in got] == [tm for tm, _ in ARROWLOOP],
          str([x.get("time") for x in got]))

    # ------------------------------------------------------------------ T88 batch preflight
    print("\n=== T88 [batch]: one bad key rejects the WHOLE list, leaving the curve alone ===")
    bad = dict(A)
    bad["channel"] = "Y"
    bad["keys"] = [{"time": 0.0, "value": 1}, {"time": 0.2, "value": 2},
                   {"time": 0.4, "value": 3}, {"time": 0.6, "interp": "nope"}]
    r = M.call("set_widget_animation_keys", bad)
    check("T88 refused", r.get("ok") is False, json.dumps(r)[:220])
    check("T88 and says nothing was changed", "NOTHING was changed" in (r.get("error") or ""),
          (r.get("error") or "")[:180])
    after = M.call("set_widget_animation_keys", dict(A, channel="Y", keys=[], replace=False))
    check("T88 the five original keys survive untouched", after.get("keysAfter") == 5,
          "keysAfter=%s - a partial write would show 3 or 4" % after.get("keysAfter"))

    # ------------------------------------------------------------------ T89 refusals
    print("\n=== T89: the refusals ===")
    r = M.call("add_widget_animation_track", dict(A, property="Opacity"))
    check("T89 unsupported property refused rather than ignored", r.get("ok") is False,
          json.dumps(r)[:200])
    check("T89 and it names what IS supported", "RenderTransform.Translation" in (r.get("error") or ""),
          (r.get("error") or "")[:160])
    r = M.call("set_widget_animation_keys", dict(A, widgetName="NoSuchWidget", channel="Y", keys=[]))
    check("T89 keying an unbound widget refused", r.get("ok") is False, json.dumps(r)[:200])
    r = M.call("set_widget_animation_keys", dict(A, channel="Z", keys=[]))
    check("T89 a bad channel refused", r.get("ok") is False, json.dumps(r)[:200])

    # ------------------------------------------------------------------ T90 compiles
    print("\n=== T90: the widget still compiles with a keyed animation ===")
    c = M.call("compile", {"blueprintId": bid})
    check("T90 compiles clean", c.get("ok") is True and c.get("numErrors", 1) == 0,
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
