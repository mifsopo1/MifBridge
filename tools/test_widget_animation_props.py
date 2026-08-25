"""UMG WidgetAnimation part 3: the other property tracks, and removal.

Part 2 hardcoded the 2D transform track. Three of the four properties people actually animate in UMG
share FMovieSceneFloatChannel, so one key path covers them:

    RenderTransform.Translation   X, Y
    RenderOpacity                 single channel
    ColorAndOpacity               R, G, B, A

Visibility is a BOOL channel and is deliberately absent. T93 checks it is refused BY NAME with the
supported list, rather than accepted and silently ignored - an endpoint that looks generic and handles
one case is worse than one that says what it does.

T94 is the one that would otherwise bite quietly: asking for channel "X" on an opacity track, or "R"
on a translation track. Without a per-property channel check those would either write to the wrong
curve or no-op under an ok:true.

T96 covers the split that makes a binding animate nothing: a widget binding is a possessable in the
MovieScene AND an entry in UWidgetAnimation::AnimationBindings, so removal has to drop both.
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
    path = "/Game/_MifAnim/WBP_Props_%d" % stamp
    bid = M.call("create_blueprint", {"path": path, "parentClass": "UserWidget",
                                      "blueprintType": "WidgetBlueprint"}).get("blueprintId")
    if not bid:
        print("setup failed")
        return 3
    M.call("add_tree_widget", {"blueprintId": bid, "widgetClass": "Image", "name": "Image_Arrow"})
    M.call("add_widget_animation", {"blueprintId": bid, "name": "Fade", "endTime": 1.0})
    A = {"blueprintId": bid, "animationName": "Fade", "widgetName": "Image_Arrow"}
    print("widget blueprint:", bid)

    # ------------------------------------------------------------------ T91 opacity
    print("\n=== T91: RenderOpacity — a single-channel float track ===")
    t = M.call("add_widget_animation_track", dict(A, property="RenderOpacity"))
    print("  ", json.dumps({k: v for k, v in t.items() if k != "animation"})[:250])
    check("T91 track created", t.get("ok") is True and t.get("createdTrack") is True, json.dumps(t)[:220])
    check("T91 it is a float track", t.get("trackClass") == "MovieSceneFloatTrack", t.get("trackClass"))
    # No channel passed: a single-channel property should not require one.
    k = M.call("set_widget_animation_keys", dict(A, property="RenderOpacity",
               keys=[{"time": 0.0, "value": 0.0}, {"time": 1.0, "value": 1.0}]))
    print("  ", json.dumps(k)[:260])
    check("T91 keyed without naming a channel", k.get("ok") is True and k.get("keysAfter") == 2,
          json.dumps(k)[:220])
    check("T91 ticks converted (1.0s -> 60000)",
          [x.get("timeTick") for x in (k.get("keys") or [])] == [0, 60000],
          str([x.get("timeTick") for x in (k.get("keys") or [])]))

    # ------------------------------------------------------------------ T92 colour
    print("\n=== T92: ColorAndOpacity — four channels ===")
    t = M.call("add_widget_animation_track", dict(A, property="ColorAndOpacity"))
    check("T92 track created", t.get("ok") is True, json.dumps(t)[:220])
    check("T92 it is a colour track", t.get("trackClass") == "MovieSceneColorTrack", t.get("trackClass"))
    check("T92 it reports its channels", t.get("channels") == "R,G,B,A", t.get("channels"))
    k = M.call("set_widget_animation_keys", dict(A, property="ColorAndOpacity", channel="A",
               keys=[{"time": 0.0, "value": 1.0}, {"time": 0.5, "value": 0.0}]))
    check("T92 keyed the alpha channel", k.get("ok") is True and k.get("keysAfter") == 2,
          json.dumps(k)[:220])
    check("T92 the response names the channel it wrote", k.get("channel") == "A", k.get("channel"))
    # R must be untouched - proof the channel selection is real and not writing to whatever is first.
    r = M.call("set_widget_animation_keys", dict(A, property="ColorAndOpacity", channel="R",
               keys=[], replace=False))
    check("T92 the R channel was NOT written to", r.get("keysAfter") == 0,
          "keysAfter=%s - nonzero means the channel selector is not selecting" % r.get("keysAfter"))

    # ------------------------------------------------------------------ T93 unsupported
    # This check used to name Visibility, and it started failing the moment Visibility was
    # implemented - a test correctly failing because the behaviour improved. Repointed at a property
    # that genuinely has no track mapping rather than deleted, because the REFUSAL is the thing worth
    # pinning: an unknown property must be named and rejected, never accepted and ignored.
    print("\n=== T93: a property with no track mapping is refused by name ===")
    r = M.call("add_widget_animation_track", dict(A, property="ToolTipText"))
    check("T93 refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T93 and it lists what IS supported",
          all(w in (r.get("error") or "")
              for w in ("RenderOpacity", "ColorAndOpacity", "Visibility")),
          (r.get("error") or "")[:240])

    # ------------------------------------------------------------------ T94 wrong channel
    print("\n=== T94: a channel that belongs to a different property is refused ===")
    r = M.call("set_widget_animation_keys", dict(A, property="RenderOpacity", channel="X", keys=[]))
    check("T94 X refused on an opacity track", r.get("ok") is False, json.dumps(r)[:200])
    M.call("add_widget_animation_track", dict(A, property="RenderTransform.Translation"))
    r = M.call("set_widget_animation_keys", dict(A, property="RenderTransform.Translation",
                                                 channel="R", keys=[]))
    check("T94 R refused on a translation track", r.get("ok") is False, json.dumps(r)[:200])

    # ------------------------------------------------------------------ T95/T96 removal
    print("\n=== T95: removing one track leaves the binding and the other tracks ===")
    before = M.call("list_widget_animations", {"blueprintId": bid})
    n_before = ((before.get("animations") or [{}])[0]).get("trackCount")
    rm = M.call("remove_widget_animation_track", dict(A, property="ColorAndOpacity"))
    print("  ", json.dumps({k: v for k, v in rm.items() if k != "animation"})[:220])
    check("T95 removed", rm.get("ok") is True and rm.get("removedTrack") is True, json.dumps(rm)[:220])
    check("T95 the binding is kept by default", rm.get("removedBinding") is False, rm.get("removedBinding"))
    anim = rm.get("animation") or {}
    check("T95 one fewer track", anim.get("trackCount") == (n_before or 0) - 1,
          "%s -> %s" % (n_before, anim.get("trackCount")))
    check("T95 the widget is still bound", len(anim.get("bindings") or []) == 1,
          json.dumps(anim.get("bindings"))[:200])

    print("\n=== T96 [both halves]: removeBinding drops the possessable AND the widget mapping ===")
    rm = M.call("remove_widget_animation_track", dict(A, property="RenderOpacity", removeBinding=True))
    anim = rm.get("animation") or {}
    check("T96 removed with the binding", rm.get("removedBinding") is True, json.dumps(rm)[:200])
    check("T96 the AnimationBindings entry is gone", (anim.get("bindings") or []) == [],
          json.dumps(anim.get("bindings"))[:200])
    check("T96 and the MovieScene possessable is gone too", anim.get("possessableCount") == 0,
          "possessableCount=%s - nonzero means only half the binding was removed"
          % anim.get("possessableCount"))

    # ------------------------------------------------------------------ T97 remove the animation
    print("\n=== T97: removing the animation itself ===")
    M.call("add_widget_animation", {"blueprintId": bid, "name": "Second", "endTime": 0.5})
    rm = M.call("remove_widget_animation", {"blueprintId": bid, "animationName": "Fade"})
    check("T97 removed", rm.get("ok") is True, json.dumps(rm)[:200])
    check("T97 one animation remains", rm.get("remaining") == 1, rm.get("remaining"))
    l = M.call("list_widget_animations", {"blueprintId": bid})
    check("T97 and it is the right one",
          [x.get("name") for x in (l.get("animations") or [])] == ["Second"],
          str([x.get("name") for x in (l.get("animations") or [])]))
    r = M.call("remove_widget_animation", {"blueprintId": bid, "animationName": "Fade"})
    check("T97 removing it twice is refused, not silently ok", r.get("ok") is False, json.dumps(r)[:200])

    # ------------------------------------------------------------------ T99 visibility (bool)
    print("\n=== T99: Visibility — a BOOL channel, so stepped and no interpolation ===")
    M.call("add_widget_animation", {"blueprintId": bid, "name": "Blink", "endTime": 1.0})
    V = {"blueprintId": bid, "animationName": "Blink", "widgetName": "Image_Arrow"}
    t = M.call("add_widget_animation_track", dict(V, property="Visibility"))
    check("T99 track created", t.get("ok") is True, json.dumps(t)[:220])
    check("T99 it is a visibility track", t.get("trackClass") == "MovieSceneVisibilityTrack",
          t.get("trackClass"))
    k = M.call("set_widget_animation_keys", dict(V, property="Visibility",
               keys=[{"time": 0.0, "value": True}, {"time": 0.5, "value": False},
                     {"time": 0.75, "value": 1}]))
    print("  ", json.dumps(k)[:320])
    check("T99 three bool keys written", k.get("ok") is True and k.get("keysAfter") == 3,
          json.dumps(k)[:220])
    check("T99 it says the channel is stepped", k.get("stepped") is True, k.get("stepped"))
    check("T99 ticks converted the same way as the float path",
          [x.get("timeTick") for x in (k.get("keys") or [])] == [0, 30000, 45000],
          str([x.get("timeTick") for x in (k.get("keys") or [])]))
    check("T99 a numeric 1 is stored as true, and reported as true",
          [x.get("value") for x in (k.get("keys") or [])] == [True, False, True],
          str([x.get("value") for x in (k.get("keys") or [])]))
    # The point: a parameter that cannot apply is REFUSED, not silently dropped.
    bad = M.call("set_widget_animation_keys", dict(V, property="Visibility",
                 keys=[{"time": 0.0, "value": True, "interp": "cubic"}]))
    check("T99 interp on a bool channel is refused, not ignored", bad.get("ok") is False,
          json.dumps(bad)[:200])
    check("T99 and it explains why", "BOOL channel" in (bad.get("error") or ""),
          (bad.get("error") or "")[:180])
    still = M.call("set_widget_animation_keys", dict(V, property="Visibility", keys=[], replace=False))
    check("T99 the refusal changed nothing", still.get("keysAfter") == 3, still.get("keysAfter"))

    # ------------------------------------------------------------------ T98 compiles
    print("\n=== T98: still compiles after all that ===")
    c = M.call("compile", {"blueprintId": bid})
    check("T98 compiles clean", c.get("ok") is True and c.get("numErrors", 1) == 0,
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
