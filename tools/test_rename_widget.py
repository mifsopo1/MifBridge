"""rename_tree_widget - and the five places a widget's name is also recorded.

Renaming the widget is one line. This suite is about the rest, because every one of them fails
SILENTLY:

  * property bindings store the widget name as a STRING
  * each animation's FWidgetAnimationBinding stores it as an FName
  * the MovieScene POSSESSABLE behind that binding stores it separately - and this is the sharp one.
    Rename the binding and not the possessable and the animation still compiles, still plays, and
    animates nothing. That is the identical two-halves split add_widget_animation_track had to handle.
  * navigation bindings
  * every graph node that gets or sets the widget as a variable

T181 is therefore the real test: it does not ask "did the name change", it asks whether the ANIMATION
still points at the widget afterwards - same GUID, track intact, and the binding naming the new name.
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


def anim_binding(bid):
    a = (M.call("list_widget_animations", {"blueprintId": bid}).get("animations") or [{}])[0]
    return (a.get("bindings") or [{}])[0]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    stamp = int(time.time() % 100000)
    p = "/Game/_MifRename/WBP_%d" % stamp
    bid = M.call("create_blueprint", {"path": p, "parentClass": "UserWidget",
                                      "blueprintType": "WidgetBlueprint"}).get("blueprintId")
    if not bid:
        print("setup failed")
        return 3
    M.call("add_tree_widget", {"blueprintId": bid, "widgetClass": "Image", "name": "Image_Arrow"})
    M.call("add_widget_animation", {"blueprintId": bid, "name": "Anim", "endTime": 1.0})
    M.call("add_widget_animation_track", {"blueprintId": bid, "animationName": "Anim",
                                          "widgetName": "Image_Arrow"})
    M.call("set_widget_animation_keys", {"blueprintId": bid, "animationName": "Anim",
                                         "widgetName": "Image_Arrow", "channel": "Y",
                                         "keys": [{"time": 0, "value": 0}, {"time": 1, "value": 50}]})
    before = anim_binding(bid)
    print("set up: widget Image_Arrow, animated, binding guid %s" % (before.get("animationGuid") or "")[:12])

    # ------------------------------------------------------------------ T180 rename
    print("\n=== T180: the rename itself ===")
    r = M.call("rename_tree_widget", {"blueprintId": bid, "widgetName": "Image_Arrow",
                                      "newName": "Image_Renamed"})
    print("  ", json.dumps({k: v for k, v in r.items() if k != "note"})[:250])
    check("T180 renamed", r.get("ok") is True and r.get("renamed") is True, json.dumps(r)[:200])
    names = [w.get("name") for w in (M.call("list_tree_widgets", {"blueprintId": bid}).get("widgets") or [])]
    check("T180 the tree shows the new name", "Image_Renamed" in names, str(names))
    check("T180 and not the old one", "Image_Arrow" not in names, str(names))

    # ------------------------------------------------------------------ T181 the actual point
    print("\n=== T181 [the point]: the ANIMATION still points at the widget ===")
    after = anim_binding(bid)
    check("T181 the animation binding names the new widget",
          after.get("widgetName") == "Image_Renamed", json.dumps(after)[:180])
    # Same GUID means the binding was UPDATED, not recreated - a recreated one would lose the track.
    check("T181 it is the SAME binding, not a new one",
          after.get("animationGuid") == before.get("animationGuid"),
          "%s vs %s" % (after.get("animationGuid"), before.get("animationGuid")))
    check("T181 and its track survived",
          (after.get("trackCount") or 0) == (before.get("trackCount") or 0) and (after.get("trackCount") or 0) > 0,
          "before=%s after=%s" % (before.get("trackCount"), after.get("trackCount")))
    # The counts are how a caller SEES it carried through rather than assuming.
    check("T181 the response reports the animation binding it updated",
          r.get("animationBindingsUpdated") == 1, r.get("animationBindingsUpdated"))
    # THE sharp one. Rename the binding and not the possessable and the animation animates nothing.
    check("T181 and the MovieScene possessable was renamed too",
          r.get("possessablesRenamed") == 1,
          "possessablesRenamed=%s - without this the animation compiles, plays and moves nothing"
          % r.get("possessablesRenamed"))

    # ------------------------------------------------------------------ T182 still valid
    print("\n=== T182: the widget blueprint still compiles ===")
    c = M.call("compile", {"blueprintId": bid})
    check("T182 compiles clean", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))

    # ------------------------------------------------------------------ T183 guards
    print("\n=== T183: guards ===")
    for name, payload, expect in (
        ("unknown widget", {"blueprintId": bid, "widgetName": "NoSuch", "newName": "X"},
         "no widget named"),
        ("name collision", {"blueprintId": bid, "widgetName": "Image_Renamed",
                            "newName": "CanvasPanel_0"}, "already has a widget"),
        ("same name", {"blueprintId": bid, "widgetName": "Image_Renamed",
                       "newName": "Image_Renamed"}, "are the same"),
        ("invalid identifier", {"blueprintId": bid, "widgetName": "Image_Renamed",
                                "newName": "not a name!"}, "valid identifier"),
        ("missing newName", {"blueprintId": bid, "widgetName": "Image_Renamed"}, "both required"),
    ):
        q = M.call("rename_tree_widget", payload)
        check("T183 %s refused" % name, q.get("ok") is False, json.dumps(q)[:150])
        check("T183 %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:140])
    # Nothing may have half-applied across all those refusals.
    names2 = [w.get("name") for w in (M.call("list_tree_widgets", {"blueprintId": bid}).get("widgets") or [])]
    check("T183 the tree is unchanged after every refusal",
          sorted(names2) == sorted(names), "%s vs %s" % (sorted(names2), sorted(names)))

    SC.confirm_call("delete_asset", {"path": p})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
