"""Sixth batch from coverage_gaps.py's sweep. Two MetaHuman endpoints, landscape sculpt/paint on a
disposable SCRATCH landscape (never the real DDS2 terrain), a console command, and four blueprint/
widget utilities that turned out to have zero committed coverage despite being flagged "landed" in
project memory - the live testing done when they were originally built was ad-hoc and never became a
permanent regression suite.

T920/T921: create_metahuman_character / spawn_metahuman_actor - DDS2's real 5.3.2 has no MetaHuman
Character plugin at all (UE 5.6+ only), so MIF_WITH_METAHUMAN is 0 and both endpoints take their
#if !MIF_WITH_METAHUMAN refusal branch. This directly closes a loose end project memory had flagged:
that refusal branch had never been directly re-verified with a real Build.bat run on 5.3 specifically.

T922/T923: sculpt_landscape / paint_landscape - driven against a LANDSCAPE THIS TEST CREATES far from
any real content, never the real DDS2 terrain (visible, real, and not something to paint on even
though nothing here is ever saved). sculpt_landscape gets full success coverage. paint_landscape gets
an honest, informative REFUSAL: the only layer info asset handy on this project (the engine's own
LandscapeLayerInfoObject) turns out to be the special __LANDSCAPE_VISIBILITY__ hole layer, not a normal
paintable one, and the endpoint correctly refuses painting a layer the landscape's MATERIAL has not
declared - "assign layers on the landscape material first". Building a real paintable-layer landscape
material is out of scope for a coverage batch; the refusal is real, useful coverage on its own.

T924: run_console_captured - a display-toggle command called twice (on, then off) so it reverts itself,
same discipline as T917's set_cast_purity toggle-and-back in the previous batch.

T925: reparent_blueprint - real coverage, previously landed but never given a committed test.

T926: preview_widget - real coverage against a real WidgetBlueprint (the project has no scratch one at
hand, but this endpoint only READS the class and renders it offscreen - nothing about the source asset
changes). Verifies the PNG it claims to write really exists on disk, the same discipline as T900's
backup_blueprint and T913's trace files in the previous batch.

T927: retarget_variable_node - self:true (repoint a variable node at its own declaring class - a
legitimate no-op-shaped success path that still exercises the whole call, not a fabricated scenario).

T928: recipe_override_and_call_parent - same shape as add_override_event with callParent forced. A
fresh Actor blueprint's EventGraph is NOT empty by default - it already carries BeginPlay,
ActorBeginOverlap and Tick event nodes (confirmed live after the first attempt refused with "event
'ReceiveBeginPlay' is already present in the graph") - so this targets ReceiveDestroyed instead, which
is not pre-placed.

T929: remove_widget_binding - turned out NOT to be confirm-gated at all, unlike its "remove_*" naming
neighbours (remove_function, remove_variable, remove_component). Confirmed live: a plain call removes
it directly, and scratch_confirm.confirm_call on it is actively REJECTED ("unrecognised parameter
'confirm'") because confirm is not even in its accepted params. A genuinely different endpoint from
the confirm-gated remove_* family it sits next to in coverage_gaps.py's grouping.
"""
import json
import os
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T920/T921 MetaHuman refusal on 5.3
    print("\n=== T920/T921: MetaHuman endpoints correctly refuse on DDS2's real 5.3.2 (no MIF_WITH_METAHUMAN) ===")
    mh1 = M.call("create_metahuman_character", {"path": "/Game/_MifReads6/MHC_%d" % st})
    check("T920 create_metahuman_character refuses", mh1.get("ok") is False, json.dumps(mh1)[:200])
    check("T920 and names the real reason (UE 5.6+ only, absent from this fork)",
          "5.6" in (mh1.get("error") or "") and "absent" in (mh1.get("error") or ""), mh1.get("error"))
    mh2 = M.call("spawn_metahuman_actor", {"path": "/Game/_MifReads6/MHC_%d" % st})
    check("T921 spawn_metahuman_actor refuses", mh2.get("ok") is False, json.dumps(mh2)[:200])
    check("T921 and names the real reason too", "5.6" in (mh2.get("error") or ""), mh2.get("error"))

    # ------------------------------------------------------------------ T922/T923 landscape sculpt/paint
    print("\n=== T922/T923: sculpt_landscape / paint_landscape on a SCRATCH landscape, never the real terrain ===")
    lx, ly = 700000 + st, 700000 + st
    land = M.call("create_landscape", {
        "location": {"x": lx, "y": ly, "z": 500000}, "componentsX": 2, "componentsY": 2,
        "quadsPerSection": 63, "sectionsPerComponent": 1, "label": "MifReads6Land_%d" % st})
    land_path = land.get("actorPath")
    check("T922-923 (setup) a disposable scratch landscape is created far from real content",
          land.get("ok") is True and bool(land_path), json.dumps(land)[:200])

    if land_path:
        sc = M.call("sculpt_landscape", {"landscape": land_path, "center": {"x": lx, "y": ly},
                                         "radius": 2000, "mode": "raise", "amount": 200})
        check("T922 sculpt_landscape (raise) succeeds", sc.get("ok") is True, json.dumps(sc)[:200])
        check("T922 and really touched vertices", (sc.get("verticesTouched") or 0) > 0, sc.get("verticesTouched"))

        sm = M.call("sculpt_landscape", {"landscape": land_path, "center": {"x": lx, "y": ly},
                                         "radius": 2000, "mode": "smooth"})
        check("T922 sculpt_landscape (smooth) also succeeds", sm.get("ok") is True, json.dumps(sm)[:200])

        # This landscape declares no real paintable layer (the only LandscapeLayerInfoObject handy on
        # this project is the engine's own visibility/hole layer, not a normal one, and painting needs
        # a layer the landscape's MATERIAL has actually declared) - a real, informative refusal, not a
        # placeholder for later.
        pt = M.call("paint_landscape", {"landscape": land_path,
                                        "layerInfo": "/Engine/EditorLandscapeResources/DataLayer",
                                        "center": {"x": lx, "y": ly}, "radius": 2000, "weight": 1.0})
        check("T923 paint_landscape refuses a layer the material never declared", pt.get("ok") is False,
              json.dumps(pt)[:200])
        check("T923 and explains why, not a generic failure",
              "not one of this landscape's layers" in (pt.get("error") or ""), pt.get("error"))


    # CLEANUP. create_landscape spawns into the EDITOR world, so this is NOT torn down when PIE
    # stops - it persists and is carried into every later PIE session. See
    # mifaudit.cleanup_level_actor for the T1606 breakage an uncleaned one already caused.
    if land_path:
        _c = M.cleanup_level_actor(land_path, "scratch landscape")
        check("T922-923 (cleanup) the scratch landscape is removed, not left in the level",
              _c.get("ok") is True, _c.get("error"))
    # ------------------------------------------------------------------ T924 run_console_captured
    print("\n=== T924: run_console_captured - a display toggle, called twice so it reverts itself ===")
    c1 = M.call("run_console_captured", {"command": "stat unit"})
    check("T924 succeeds", c1.get("ok") is True, json.dumps(c1)[:200])
    c2 = M.call("run_console_captured", {"command": "stat unit"})
    check("T924 toggling back off also succeeds", c2.get("ok") is True, json.dumps(c2)[:200])

    # ------------------------------------------------------------------ T925 reparent_blueprint
    print("\n=== T925: reparent_blueprint ===")
    bpath = "/Game/_MifReads6/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    check("T925 (setup) a scratch Actor blueprint is created", bool(bid), bid)
    if bid:
        rp = M.call("reparent_blueprint", {"blueprintId": bid, "newParentClass": "Pawn"})
        check("T925 succeeds", rp.get("ok") is True, json.dumps(rp)[:200])
        info = M.call("open_blueprint", {"blueprintId": bid})
        check("T925 the blueprint really has the new parent class afterward",
              "Pawn" in (info.get("parentClass") or ""), info.get("parentClass"))

    # ------------------------------------------------------------------ T926 preview_widget
    print("\n=== T926: preview_widget - a real WidgetBlueprint, isolated offscreen render ===")
    widgets = M.call("find_assets", {"class": "WidgetBlueprint", "limit": 1}).get("assets") or []
    if widgets:
        wname = widgets[0].get("name")
        wc = widgets[0].get("path").split(".")[0] + "." + wname + "_C"
        pv = M.call("preview_widget", {"widgetClass": wc, "width": 256, "height": 256})
        check("T926 succeeds", pv.get("ok") is True, json.dumps(pv)[:200])
        pv_path = pv.get("path")
        check("T926 a file path is reported", bool(pv_path), json.dumps(pv)[:200])
        if pv_path:
            check("T926 the PNG really exists on disk", os.path.isfile(pv_path), pv_path)
    else:
        print("  SKIP  preview_widget - no WidgetBlueprint asset found on this project")

    # ------------------------------------------------------------------ T927 retarget_variable_node
    print("\n=== T927: retarget_variable_node - self:true, a legitimate no-op-shaped success ===")
    if bid:
        graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
        graph = next((g.get("graphId") for g in graphs if "EventGraph" in (g.get("name") or "")), None)
        M.call("add_variable", {"blueprintId": bid, "name": "Reads6Amt", "type": "float"})
        gv = M.call("add_variable_get", {"graphId": graph, "var": "Reads6Amt", "x": 900, "y": 1800})
        var_guid = gv.get("nodeGuid") or (gv.get("node") or {}).get("guid")
        check("T927 (setup) a variable get node exists", bool(var_guid), json.dumps(gv)[:200])
        if var_guid:
            rt = M.call("retarget_variable_node", {"graphId": graph, "nodeGuid": var_guid, "self": True})
            check("T927 succeeds", rt.get("ok") is True, json.dumps(rt)[:200])
            check("T927 and reports the variable it touched", rt.get("variable") == "Reads6Amt",
                  rt.get("variable"))

    # ------------------------------------------------------------------ T928 recipe_override_and_call_parent
    print("\n=== T928: recipe_override_and_call_parent - ReceiveDestroyed, not pre-placed like BeginPlay/Tick ===")
    if bid:
        # A fresh Actor blueprint's EventGraph already carries BeginPlay/ActorBeginOverlap/Tick nodes by
        # default (confirmed live: overriding any of those refuses "already present in the graph") - so
        # this targets ReceiveDestroyed, which is not pre-placed.
        ov = M.call("recipe_override_and_call_parent", {"blueprintId": bid, "event": "ReceiveDestroyed"})
        guid = ov.get("nodeGuid") or (ov.get("node") or {}).get("guid")
        check("T928 succeeds", ov.get("ok") is True, json.dumps(ov)[:200])
        check("T928 a node guid is reported", bool(guid), json.dumps(ov)[:200])
        check("T928 a parent-call node was wired too (callParent forced on)",
              bool(ov.get("parentNodeGuid")), json.dumps(ov)[:200])
        dup = M.call("recipe_override_and_call_parent", {"blueprintId": bid, "event": "ReceiveDestroyed"})
        check("T928 overriding the same event twice is refused",
              dup.get("ok") is False and "already present" in (dup.get("error") or ""), dup.get("error"))

        c = M.call("compile", {"blueprintId": bid})
        check("T928 the blueprint still compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
              "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T929 remove_widget_binding
    print("\n=== T929: remove_widget_binding - NOT confirm-gated, unlike its remove_* naming neighbours ===")
    wpath = "/Game/_MifReads6/WBP_%d" % st
    wmade = M.call("create_blueprint", {"path": wpath, "blueprintType": "WidgetBlueprint"})
    wbid = wmade.get("blueprintId")
    check("T929 (setup) a scratch WidgetBlueprint is created", wmade.get("ok") is True and bool(wbid),
          json.dumps(wmade)[:200])
    if wbid:
        M.call("add_tree_widget", {"blueprintId": wbid, "widgetClass": "TextBlock", "name": "Reads6Text",
                                   "parentName": "CanvasPanel_0"})
        ab = M.call("add_widget_binding", {"blueprintId": wbid, "widgetName": "Reads6Text",
                                           "propertyName": "Text", "functionName": "GetReads6Text"})
        check("T929 (setup) a binding exists to remove", ab.get("ok") is True and ab.get("bindingCount") == 1,
              json.dumps(ab)[:200])
        rb = M.call("remove_widget_binding", {"blueprintId": wbid, "widgetName": "Reads6Text",
                                              "propertyName": "Text"})
        check("T929 the plain call (no confirm) succeeds directly", rb.get("ok") is True, json.dumps(rb)[:200])
        check("T929 and the binding count is really back to zero", rb.get("bindingCount") == 0,
              json.dumps(rb)[:200])
        SC.confirm_call("delete_asset", {"path": wpath})

    if bid:
        SC.confirm_call("delete_asset", {"path": bpath})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
