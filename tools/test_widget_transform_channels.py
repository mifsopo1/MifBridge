"""A UMG widget's render Scale, Angle and Shear - not just its Translation.

WHY THIS EXISTS. Scale is the most common UI animation there is - pop-in, button press, pulse - and
it was the one channel family the bridge could not reach. Translation, Angle, Scale and Shear are
all FMovieSceneFloatChannels on the SAME UMovieScene2DTransformSection
(MovieScene2DTransformSection.h:136-151), bound to the same "RenderTransform" property, so this is
an extension of the existing track rather than a new one.

THE TRAP THAT MAKES T7302 THE IMPORTANT TEST. Because all four families share one section, the
channel name a caller passes is ambiguous: "X" means Translation[0] to one caller and Scale[0] to
another. The old resolver took only the SECTION and the channel string, which was correct while
Translation was the only transform family and becomes a silent wrong-curve write the moment Scale
exists. So the resolver now takes the PROPERTY too, and T7302 proves the two are really distinct
curves in the only way that cannot be faked: it keys Scale.X twice, then keys Translation.X and
asserts that call sees keysBefore == 0. A shared curve would report 2.

THE SECOND TRAP IS THE MASK, and it is worse because it fails silently in the other direction.
UMovieScene2DTransformSection::ImportEntityImpl builds its evaluation entity from
    EnumHasAnyFlags(Channels, ...ScaleX) && Scale[0].HasAnyData()
(MovieScene2DTransformSection.cpp:239-267), so a channel whose TransformMask bit is clear is never
handed to the evaluator at all. Keys written there are stored, read back perfectly, and animate
nothing. The section constructor defaults the mask to AllTransform (:126), so a section this plugin
created is always fine - but one narrowed in the UMG designer is not, and the handler widens the
mask and says so rather than leaving inert keys behind.

WHAT THIS SUITE NEEDED FIRST. A cooked project has no UWidgetBlueprint at all - only
UWidgetBlueprintGeneratedClass - so every widget endpoint refuses every shipped asset and none of
this is testable against one. It runs on a SCRATCH Widget Blueprint instead. Getting one exposed a
missing guard, recorded in T7300: create_blueprint{parentClass:"UserWidget"} without
blueprintType=WidgetBlueprint answered ok:true and produced a plain UBlueprint with no WidgetTree,
which every widget endpoint then refused. The neighbouring UAnimInstance guard exists for exactly
that near-miss and simply had no widget counterpart.
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
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    st = int(time.time()) % 100000
    bp = None
    try:
        # -------------------------------------------------- T7300 the near-miss guard
        print("=== T7300: a UserWidget parent without blueprintType is refused, not half-made ===")
        bad = M.raw_post("create_blueprint", {"path": "/Game/_MifWid/Bad%d" % st,
                                              "parentClass": "UserWidget"})
        check("T7300 it is refused rather than answering ok with an unusable asset",
              bad.get("ok") is False, json.dumps(bad)[:220])
        # The refusal has to name the fix, or the caller just tries the same thing again.
        check("T7300 and the refusal names blueprintType=WidgetBlueprint",
              "blueprintType=WidgetBlueprint" in (bad.get("error") or ""),
              (bad.get("error") or "")[:250])
        check("T7300 and says WHY - no WidgetTree - rather than only what to type",
              "WidgetTree" in (bad.get("error") or ""), (bad.get("error") or "")[:250])

        # -------------------------------------------------- setup
        made = M.raw_post("create_blueprint", {"path": "/Game/_MifWid/WBP_%d" % st,
                                               "blueprintType": "WidgetBlueprint"})
        bp = made.get("blueprintId")
        check("(setup) a real Widget Blueprint is created", bool(bp), json.dumps(made)[:220])
        if not bp:
            return 1
        tree = M.raw_post("list_tree_widgets", {"blueprintId": bp})
        check("(setup) and it has a root panel, so it is a usable widget tree",
              tree.get("ok") is True and tree.get("count", 0) >= 1, json.dumps(tree)[:200])
        w = M.raw_post("add_tree_widget", {"blueprintId": bp, "widgetClass": "Button",
                                           "parentName": "CanvasPanel_0", "name": "Btn"})
        check("(setup) a child widget is added - the root cannot be bound headless",
              w.get("ok") is True, json.dumps(w)[:200])
        an = M.raw_post("add_widget_animation", {"blueprintId": bp, "name": "Pop"})
        check("(setup) an animation is added", an.get("ok") is True, json.dumps(an)[:200])

        # -------------------------------------------------- T7301 the new families exist
        print("\n=== T7301: Scale, Angle and Shear are authorable ===")
        first = M.raw_post("add_widget_animation_track",
                           {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                            "property": "RenderTransform.Scale"})
        check("T7301 a Scale track can be added", first.get("ok") is True,
              json.dumps(first)[:250])
        check("T7301 it is a 2D transform track, the same class translation uses",
              first.get("trackClass") == "MovieScene2DTransformTrack", first.get("trackClass"))
        check("T7301 and it really created one on a fresh widget",
              first.get("createdTrack") is True, json.dumps(first)[:220])

        # THE SHARED-TRACK FACT, made explicit. A caller asking for a second family sees
        # createdTrack:false and would otherwise conclude nothing happened.
        second = M.raw_post("add_widget_animation_track",
                            {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                             "property": "RenderTransform.Angle"})
        check("T7301 asking for Angle afterwards succeeds", second.get("ok") is True,
              json.dumps(second)[:220])
        check("T7301 but creates NO second track - the four families share one",
              second.get("createdTrack") is False, json.dumps(second)[:220])
        check("T7301 and says so, so createdTrack:false does not read as a failure",
              "UMovieScene2DTransformTrack" in (second.get("trackNote") or "")
              and "no new track was needed" in (second.get("trackNote") or ""),
              (second.get("trackNote") or "")[:250])

        # -------------------------------------------------- T7302 THE assertion
        print("\n=== T7302: Scale.X and Translation.X are different curves ===")
        sc = M.raw_post("set_widget_animation_keys",
                        {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                         "property": "RenderTransform.Scale", "channel": "X",
                         "keys": [{"time": 0, "value": 1.0}, {"time": 0.5, "value": 1.4}]})
        check("T7302 two keys are written to Scale.X",
              sc.get("ok") is True and sc.get("keysAfter") == 2, json.dumps(sc)[:250])

        tr = M.raw_post("set_widget_animation_keys",
                        {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                         "property": "RenderTransform.Translation", "channel": "X",
                         "keys": [{"time": 0, "value": 7.0}]})
        # THIS IS THE ONE. If the resolver still keyed off the section alone, the Scale write above
        # would have landed on Translation[0] and this call would report keysBefore == 2. Nothing
        # else in the response distinguishes the two curves.
        check("T7302 keying Translation.X afterwards sees an EMPTY curve, which is the proof "
              "the Scale keys did not land on it",
              tr.get("ok") is True and tr.get("keysBefore") == 0,
              "keysBefore=%s (2 would mean Scale wrote to Translation[0])" % tr.get("keysBefore"))
        check("T7302 and Translation.X now holds its own single key",
              tr.get("keysAfter") == 1, json.dumps(tr)[:220])

        # Angle is a single curve and must not demand an axis, matching every other
        # single-channel property here rather than inventing a third convention.
        ang = M.raw_post("set_widget_animation_keys",
                         {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                          "property": "RenderTransform.Angle",
                          "keys": [{"time": 0, "value": 0.0}, {"time": 1.0, "value": 90.0}]})
        check("T7302 Angle takes keys with no channel named - it is one curve, not two",
              ang.get("ok") is True and ang.get("keysAfter") == 2, json.dumps(ang)[:250])
        check("T7302 and Angle is its own curve too, untouched by the writes above",
              ang.get("keysBefore") == 0, "keysBefore=%s" % ang.get("keysBefore"))

        shear = M.raw_post("set_widget_animation_keys",
                           {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                            "property": "RenderTransform.Shear", "channel": "Y",
                            "keys": [{"time": 0, "value": 0.2}]})
        check("T7302 Shear.Y is reachable and separate as well",
              shear.get("ok") is True and shear.get("keysBefore") == 0
              and shear.get("keysAfter") == 1, json.dumps(shear)[:250])

        # -------------------------------------------------- T7303 typos still refuse
        print("\n=== T7303: a channel that does not belong to the property is refused ===")
        bogus = M.raw_post("set_widget_animation_keys",
                           {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                            "property": "RenderTransform.Scale", "channel": "R",
                            "keys": [{"time": 0, "value": 1.0}]})
        # A wrong channel must not fall through to some default curve - that is the same
        # wrong-curve write in a different disguise.
        check("T7303 'R' is not a Scale channel and is refused",
              bogus.get("ok") is False, json.dumps(bogus)[:220])
        check("T7303 and the refusal lists what the property's channels actually are",
              "X,Y" in (bogus.get("error") or ""), (bogus.get("error") or "")[:220])
        # Angle has one curve, so an axis is meaningless on it.
        axis = M.raw_post("set_widget_animation_keys",
                          {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                           "property": "RenderTransform.Angle", "channel": "X",
                           "keys": [{"time": 0, "value": 1.0}]})
        check("T7303 an axis on the single-curve Angle is refused rather than guessed",
              axis.get("ok") is False, json.dumps(axis)[:220])

        # -------------------------------------------------- T7304 the shared-track removal guard
        print("\n=== T7304: removing one family must not destroy the other three ===")
        # At this point Scale.X, Translation.X, Angle and Shear.Y all hold keys on the SAME section.
        rem = M.raw_post("remove_widget_animation_track",
                         {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                          "property": "RenderTransform.Scale"})
        check("T7304 removing the Scale track is REFUSED while other families hold keys",
              rem.get("ok") is False, json.dumps(rem)[:250])
        # A refusal that does not say what would be lost is not actionable - the keys cannot be
        # recovered and no read endpoint would show what went missing.
        check("T7304 and it says how many keys would have been destroyed",
              (rem.get("wouldDestroyKeys") or 0) > 0, json.dumps(rem)[:250])
        fams = rem.get("wouldDestroyFamilies") or []
        check("T7304 and names the families, not just a count",
              any("Translation" in f for f in fams) and any("Angle" in f for f in fams),
              json.dumps(fams)[:200])
        check("T7304 the Scale keys are still there afterwards - NOTHING was removed",
              M.raw_post("set_widget_animation_keys",
                         {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                          "property": "RenderTransform.Scale", "channel": "X",
                          "replace": False, "keys": []}).get("keysBefore") == 2,
              "the refusal should have left the track and its keys untouched")

        # THE OTHER DIRECTION. A guard that always refuses is as useless as one that never does, so
        # clear the other families and confirm removal then works.
        for prop, chan in (("RenderTransform.Translation", "X"), ("RenderTransform.Angle", ""),
                           ("RenderTransform.Shear", "Y")):
            M.raw_post("set_widget_animation_keys",
                       {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                        "property": prop, "channel": chan, "replace": True, "keys": []})
        ok = M.raw_post("remove_widget_animation_track",
                        {"blueprintId": bp, "animationName": "Pop", "widgetName": "Btn",
                         "property": "RenderTransform.Scale"})
        check("T7304 once the other families are empty, the track CAN be removed",
              ok.get("ok") is True, json.dumps(ok)[:250])

        print("\n  NOT EXERCISED: the mask-widening path. A section this plugin creates gets the")
        print("  engine's default AllTransform mask (MovieScene2DTransformSection.cpp:126), so no")
        print("  bit is ever clear here. It matters for a section narrowed in the UMG designer,")
        print("  where a masked-off channel stores keys and animates nothing.")

        check("T7303 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
              "binding the root widget headless is a CastChecked on a null preview UUserWidget")
    finally:
        if bp:
            SC.confirm_call("delete_asset", {"path": bp})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
