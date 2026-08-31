"""Fifth batch from coverage_gaps.py's sweep - level-actor ops, blueprint editing utilities, and two
confirm-gated removals now that scratch_confirm.py's real reach is understood (see test_node_spawns.py's
T333/T333b for the correction: remove_* endpoints ARE scratch-verifiable through an optional graphId or
path, not a permanent gap - the same reasoning applies here to remove_function/remove_variable, which
are addressed by blueprintId directly).

T900-T908: level-actor and editor-utility ops (backup_blueprint, list_object_properties, list_sublevels,
duplicate_actors, reset_property_to_default, select_level_actors, close_asset_editors,
open_asset_editor, open_blueprint) - all driven against a SCRATCH spawned actor and a scratch blueprint,
never a real DDS2 level actor, even though nothing here is ever saved. move_actor_to turned out to
belong with the PIE-dependent family below, not this list - see T903's note.

T909-T912: blueprint/widget editing (set_variable_default, set_widget_is_variable,
create_material_function, read_modloader_log).

T913: trace_start/trace_stop - UE Insights profiling, NOT PIE-related despite living next to the PIE
family in coverage_gaps.py's uncovered list. Writes a real trace file under Saved/MifBridge/Traces,
verified to actually exist on disk, not just trusted from the response.

T914: create_data_layer - DDS2's currently open level is not World Partition, so this is a genuine,
correctly-named REFUSAL test (same shape as the MetaHuman-on-5.3 pattern elsewhere in this project),
not a placeholder for later.

T915-T916: remove_function, remove_variable - confirm-gated, real success path via scratch_confirm.

T917: set_cast_purity - toggles an existing cast node's purity, verified by pin shape (impure casts
carry exec pins, pure casts do not).

DEFERRED, not in this batch: retarget_variable_node, recipe_override_and_call_parent,
set_niagara_component_parameter, remove_widget_binding, remove_collision, remove_sublevel,
bind_landscape_rvt, reimport_asset, set_asset_thumbnail, load_level - each needs either a real
inherited-variable/parent-event/Niagara-component scenario or carries enough state risk (load_level
switches the editor's open level; remove_sublevel's discardUnsaved has NO scratch_confirm exemption,
ever) to deserve its own careful batch rather than being rushed into this one.

DECLINED, not pursued at all: move_actor_to (confirmed live: "needs a running PIE session - AI
controllers only exist at runtime").

THE PIE DECLINE ABOVE WAS TOO BROAD, and is superseded - see tools/test_pie_idle.py (2026-08-31).
It read "list_pie_actors, pie_status, pie_load_level_instance, pie_unload_level_instance,
spawn_actor_in_pie, describe_live_widget, list_live_widgets, ui_scenario_* - all forbidden by the
standing rule against starting PIE". Two things were wrong with that. The rule was lifted on
2026-08-28. And it generalised from "needs PIE for its full function" to "cannot be tested at all",
which is a different claim: every one of those has specified behaviour with NO PIE world, and
pie_status is a pure read that never needed a session. The idle contract - refusals that name the
remedy, an idempotent ui_scenario_stop, list_live_widgets falling back to the editor world - is now
covered, 26 assertions, without starting anything. The RUNNING paths remain open and are filed. save_dirty_packages, save_level_as - forbidden by the standing rule against
saving. pcg_generate/pcg_cleanup - already-documented structural wall, PCG has no node-authoring
endpoints so there is no way to build real graph content to test generation against.
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

    # PIE needs full write mode - the gate refuses start_pie in scratch/read, in the dispatcher,
    # before the handler runs. SKIP (exit 2) rather than report failures the gate caused correctly.
    if M.needs_full_write_mode("test_uncovered_reads5.py"):
        return 2
    st = int(time.time() % 100000)
    bpath = "/Game/_MifReads5/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    if not bid:
        print("setup failed: scratch blueprint")
        return 3
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    graph = next((g.get("graphId") for g in graphs if "EventGraph" in (g.get("name") or "")), None)

    # ------------------------------------------------------------------ T900 backup_blueprint
    # backup_blueprint COPIES the package file on disk - it does not touch the original asset at all,
    # so this is safe against a REAL, already-saved DDS2 blueprint. It has to be a real one: a scratch
    # blueprint from create_blueprint is never saved (this whole project's standing invariant), and
    # backup_blueprint correctly refuses anything with no .uasset on disk yet - confirmed live, not
    # assumed, and worth keeping as the honest reason a scratch target would have been the wrong call.
    print("\n=== T900: backup_blueprint - a real file copy on disk, not just a claimed one ===")
    # A SAVED blueprint, FOUND rather than named. The trait that matters is "has a .uasset on
    # disk", which is why a scratch one cannot stand in - and it is also why this does not need a
    # PARTICULAR blueprint, only a pre-existing one. This used to name a DDS2 mod asset.
    real_bp = None
    for row in (M.call("find_assets", {"class": "Blueprint", "pathPrefix": "/Game/",
                                       "limit": 60}).get("assets") or []):
        cand = row.get("path") or row.get("objectPath")
        if cand and "/_Mif" not in cand:          # scratch assets are never saved, by standing rule
            real_bp = cand.rsplit(".", 1)[0]
            break
    if not real_bp:
        print("  NOTE  no pre-existing saved blueprint in this project, so T900 is UNEXERCISED.")
        print("        backup_blueprint copies a package FILE, so a scratch target cannot stand in.")
    bk = M.call("backup_blueprint", {"blueprintId": real_bp}) if real_bp else {}
    check("T900 backup_blueprint succeeds against a real, saved blueprint", bk.get("ok") is True,
          json.dumps(bk)[:200])
    backup_path = bk.get("backup")
    check("T900 a backup path is reported", bool(backup_path), json.dumps(bk)[:200])
    backup_local = None
    if backup_path:
        # The project root from the editor itself, not a literal. backup_blueprint returns a
        # PROJECT-RELATIVE path and this used to join it against "D:/DDS2SDK/Game" - a
        # hardcoded machine path inside a tool meant to run on any project. project_paths
        # exists because of this line.
        proj_dir = M.call("project_paths", {}).get("projectDir") or ""
        backup_local = os.path.normpath(os.path.join(proj_dir, backup_path.lstrip("/\\")))
        check("T900 the backup file really exists on disk", os.path.isfile(backup_local), backup_local)

    # ------------------------------------------------------------------ T901 list_object_properties
    # Needs objectPath (a placed actor's path IS one) OR (blueprintId + widgetName) - a blueprintId
    # alone is not enough, confirmed live. Run against the scratch actor spawned for T903-906 below.

    # ------------------------------------------------------------------ T902 list_sublevels
    print("\n=== T902: list_sublevels - read-only, whatever the current level actually has ===")
    subs = M.call("list_sublevels", {})
    check("T902 succeeds", subs.get("ok") is True, json.dumps(subs)[:200])
    check("T902 the response shape is a list (empty is a valid, honest answer)",
          isinstance(subs.get("sublevels"), list), json.dumps(subs)[:200])

    # ------------------------------------------------------------------ setup: a scratch actor, never
    # a real DDS2 level actor, for T903-T906
    print("\n=== T903-T906 setup: spawn a scratch actor, not a real level actor ===")
    spawn = M.call("spawn_actor_in_level",
                   {"actorClass": "StaticMeshActor", "location": {"x": 500000, "y": 500000, "z": 500000},
                    "label": "MifReads5Probe_%d" % st, "folder": "_MifReads5"})
    # actorPath is nested under "actor", not top-level - confirmed live after the first draft's wrong
    # extraction (spawn.get("actor") or {}).get("path") reported an empty path despite ok:true.
    actor_path = spawn.get("actorPath") or (spawn.get("actor") or {}).get("actorPath")
    check("T903-906 (setup) the scratch actor spawns far off the real level content",
          spawn.get("ok") is True and bool(actor_path), json.dumps(spawn)[:200])

    if actor_path:
        # ------------------------------------------------------------------ T901 list_object_properties
        print("\n=== T901: list_object_properties - on the scratch actor's own objectPath ===")
        props = M.call("list_object_properties", {"objectPath": actor_path, "limit": 20})
        check("T901 succeeds", props.get("ok") is True, json.dumps(props)[:200])
        check("T901 reports at least one property", len(props.get("properties") or []) > 0,
              json.dumps(props)[:200])

        # ------------------------------------------------------------------ T903 duplicate_actors
        print("\n=== T903: duplicate_actors ===")
        dup = M.call("duplicate_actors", {"actorPaths": [actor_path], "offset": {"x": 100, "y": 0, "z": 0},
                                          "labelSuffix": "_Dup"})
        check("T903 succeeds", dup.get("ok") is True, json.dumps(dup)[:200])
        # Same nested shape as spawn_actor_in_level's response - actorPath lives under each entry of
        # "actors", not a top-level list of strings. Confirmed live after the first draft's wrong
        # extraction reported nothing despite a real duplicate having been made (duplicated:1).
        new_paths = [a.get("actorPath") for a in (dup.get("actors") or []) if a.get("actorPath")]
        check("T903 a new actor path is reported", bool(new_paths), json.dumps(dup)[:250])

        # T904 move_actor_to is deliberately NOT tested here: confirmed live it needs a running PIE
        # session ("AI controllers only exist at runtime. start_pie first.") - it moves an actor via
        # its AI Controller, not a general transform setter despite the name, so it belongs with the
        # PIE-dependent family this batch declines, not the safe editor-world ops.

        # ---------------------------------------------------------------- T905b the C-array branch
        print("\n=== T905b: resetting one element of a fixed-size C-array ===")
        # reset_property_to_default carries a whole branch for fixed-size C-arrays - a bWholeCArray
        # flag, an arrayDim report, and a comment block explaining why a TEXT compare cannot carry
        # the invariant there ("ExportText_Direct emits ONE element ... so for a C-array UPROPERTY it
        # puts element 0 against element 0 and returns equal no matter what elements 1..n hold").
        # None of it had a test, and the question that produced this one was whether the branch is
        # reachable through the API at all.
        #
        # IT IS, and finding the fixture took reading the ENGINE rather than sampling the project:
        # grepping for an editable fixed-size C-array UPROPERTY gives exactly one on this surface,
        # FPostProcessSettings::LensFlareTints, FLinearColor[8] (Engine/Scene.h:1898). A scratch
        # Actor blueprint has 63 properties and its CDO 112, and NONE of them is a C-array.
        #
        # NO LEVEL INVOLVED. The component TEMPLATE is addressable as
        # '<BP>.<Name>_C:<Comp>_GEN_VARIABLE', so this needs no spawned actor - unlike T905 above,
        # which drives whatever level happens to be open.
        cam = M.call("add_component", {"blueprintId": bpath, "componentClass": "CameraComponent",
                                       "name": "MifCam"})
        check("T905b (setup) a CameraComponent, for its PostProcessSettings", cam.get("ok") is True,
              json.dumps(cam)[:200])
        M.call("compile", {"blueprintId": bpath})
        tmpl = "%s.%s_C:MifCam_GEN_VARIABLE" % (bpath, bpath.rsplit("/", 1)[-1])
        ARR = "PostProcessSettings.LensFlareTints"
        setc = M.call("set_property", {"objectPath": tmpl, "propertyPath": ARR + "[2]",
                                       "value": "(R=1,G=0,B=0,A=1)"})
        check("T905b (setup) element [2] is set away from its default", setc.get("ok") is True,
              json.dumps(setc)[:220])
        if setc.get("ok") is True:
            r = M.call("reset_property_to_default", {"objectPath": tmpl, "propertyPath": ARR + "[2]"})
            check("T905b the element resets", r.get("ok") is True and r.get("changed") is True,
                  json.dumps(r)[:240])
            # THE FIELD THAT PROVES WHICH BRANCH RAN. arrayDim is emitted only when Leaf->ArrayDim
            # is greater than 1, so its presence is the C-array path saying so.
            check("T905b and reports arrayDim 8 - proof the C-array branch is the one that ran",
                  r.get("arrayDim") == 8, "arrayDim=%r" % r.get("arrayDim"))
            check("T905b and the reset VERIFIES, which for a C-array is checked per element rather "
                  "than by a text compare of element 0",
                  r.get("verified") is True,
                  "verified=%r verifyFailure=%r" % (r.get("verified"), r.get("verifyFailure")))
        # And the out-of-range refusal, which names the real size rather than failing vaguely.
        oob = M.call("reset_property_to_default", {"objectPath": tmpl, "propertyPath": ARR + "[99]"})
        check("T905b an out-of-range index is refused and NAMES the array's real size",
              oob.get("ok") is False and "8 elements" in (oob.get("error") or ""),
              (oob.get("error") or "")[:220])

        # ------------------------------------------------------------------ T905 reset_property_to_default
        print("\n=== T905: reset_property_to_default ===")
        set_r = M.call("set_property", {"objectPath": actor_path, "propertyPath": "bHidden", "value": True})
        check("T905 (setup) bHidden set to a non-default value", set_r.get("ok") is True, json.dumps(set_r)[:200])
        rst = M.call("reset_property_to_default", {"objectPath": actor_path, "propertyPath": "bHidden"})
        check("T905 reset succeeds", rst.get("ok") is True, json.dumps(rst)[:200])
        # get_property reports value as a STRING export-text ("False"/"True"), not a Python bool -
        # confirmed live after the first draft's `in (False, "false", None)` check missed the real,
        # correct capitalised string.
        back = M.call("get_property", {"objectPath": actor_path, "propertyPath": "bHidden"})
        check("T905 the property is really back at its default (false)",
              (back.get("value") or "").strip().lower() == "false", json.dumps(back)[:150])

        # ------------------------------------------------------------------ T906 select_level_actors
        print("\n=== T906: select_level_actors ===")
        sel = M.call("select_level_actors", {"actorPaths": [actor_path]})
        check("T906 selecting succeeds", sel.get("ok") is True, json.dumps(sel)[:200])
        clr = M.call("select_level_actors", {"actorPaths": [], "clear": True})
        check("T906 clearing succeeds", clr.get("ok") is True, json.dumps(clr)[:200])

    # ------------------------------------------------------------------ T907 close_asset_editors
    # Confirm-gated - "an open editor may hold UNSAVED work and closing it discards that work without
    # a prompt" - confirmed live, not assumed from the name. It DOES accept an optional path/assetPath
    # to scope the close to one asset, so scratch_confirm can prove this call is scratch-only.
    print("\n=== T907: close_asset_editors ===")
    cf = M.call("close_asset_editors", {"path": bpath})
    check("T907 refuses without confirm", cf.get("ok") is False, json.dumps(cf)[:200])
    ce = SC.confirm_call("close_asset_editors", {"path": bpath})
    check("T907 the real close (scoped to the scratch blueprint) succeeds", ce.get("ok") is True,
          json.dumps(ce)[:200])

    # ------------------------------------------------------------------ T908 open_asset_editor / open_blueprint
    print("\n=== T908: open_asset_editor and open_blueprint ===")
    oa = M.call("open_asset_editor", {"path": bpath})
    check("T908 open_asset_editor succeeds", oa.get("ok") is True, json.dumps(oa)[:200])
    ob = M.call("open_blueprint", {"blueprintId": bid})
    check("T908 open_blueprint succeeds", ob.get("ok") is True, json.dumps(ob)[:200])
    check("T908 open_blueprint reports the graphs", len(ob.get("graphs") or []) > 0, json.dumps(ob)[:200])
    # parentClass is reported as the full native path ("/Script/Engine.Actor"), not the short name -
    # confirmed live after the first draft's exact-match assertion failed against a true value.
    check("T908 open_blueprint reports the right parent class", "Actor" in (ob.get("parentClass") or ""),
          ob.get("parentClass"))
    SC.confirm_call("close_asset_editors", {"path": bpath})

    # ------------------------------------------------------------------ T909 set_variable_default
    print("\n=== T909: set_variable_default ===")
    M.call("add_variable", {"blueprintId": bid, "name": "Reads5Amount", "type": "float"})
    sv = M.call("set_variable_default", {"blueprintId": bid, "name": "Reads5Amount", "value": "42.5"})
    check("T909 succeeds", sv.get("ok") is True, json.dumps(sv)[:200])
    check("T909 the response reflects the new default",
          "42.5" in str(sv.get("value") or sv.get("default") or ""), json.dumps(sv)[:200])

    # ------------------------------------------------------------------ T910 set_widget_is_variable
    print("\n=== T910: set_widget_is_variable - own scratch WidgetBlueprint ===")
    wpath = "/Game/_MifReads5/WBP_%d" % st
    wmade = M.call("create_blueprint", {"path": wpath, "blueprintType": "WidgetBlueprint"})
    wbid = wmade.get("blueprintId")
    check("T910 (setup) scratch WidgetBlueprint created", wmade.get("ok") is True and bool(wbid),
          json.dumps(wmade)[:200])
    if wbid:
        tw = M.call("add_tree_widget", {"blueprintId": wbid, "widgetClass": "TextBlock",
                                        "name": "Reads5Text", "parentName": "CanvasPanel_0"})
        check("T910 (setup) a TextBlock is added under the auto-created root", tw.get("ok") is True,
              json.dumps(tw)[:200])
        sw = M.call("set_widget_is_variable", {"blueprintId": wbid, "widgetName": "Reads5Text", "isVariable": True})
        check("T910 succeeds", sw.get("ok") is True, json.dumps(sw)[:200])
        tree = M.call("list_tree_widgets", {"blueprintId": wbid}).get("widgets") or []
        node = next((w for w in tree if w.get("name") == "Reads5Text"), None)
        check("T910 the widget really is a variable afterward",
              bool(node) and node.get("isVariable") is True, json.dumps(node)[:150])
        SC.confirm_call("delete_asset", {"path": wpath})

    # ------------------------------------------------------------------ T911 create_material_function
    print("\n=== T911: create_material_function ===")
    mfpath = "/Game/_MifReads5/MF_%d" % st
    mf = M.call("create_material_function", {"path": mfpath, "description": "MifBridge coverage probe"})
    check("T911 succeeds", mf.get("ok") is True, json.dumps(mf)[:200])
    # pathPrefix wants a FOLDER, not the exact asset path - confirmed live after the first draft passed
    # the asset's own full path as "prefix" and got zero results even though the asset was real; the
    # folder-only form finds it fine.
    exists = M.call("find_assets", {"pathPrefix": "/Game/_MifReads5", "class": "MaterialFunction",
                                     "limit": 5}).get("assets") or []
    check("T911 the asset really exists afterward",
          any(a.get("path", "").startswith(mfpath) for a in exists), json.dumps(exists)[:200])
    SC.confirm_call("delete_asset", {"path": mfpath})

    # ------------------------------------------------------------------ T912 read_modloader_log
    print("\n=== T912: read_modloader_log ===")
    rl = M.call("read_modloader_log", {"lines": 20})
    # A missing UE4SS.log (this is the SDK editor, not a shipped game with the mod loader running) is
    # a legitimate, honest answer here, not a crash - either shape is acceptable, a silent exception is
    # not.
    check("T912 answers cleanly either way (found or honestly not found)",
          "ok" in rl, json.dumps(rl)[:200])

    # TWO DIFFERENT TRUNCATIONS, and conflating them is the easy mistake. `truncatedRead` means the
    # LOG FILE exceeded 64 MB and only its tail was read, so the oldest entries are missing and line
    # numbers do not match the file's own. It has nothing to do with the `lines` cap, which limits
    # how much of the MATCHED set comes back. A caller who reads truncatedRead:false as "I got
    # everything" is wrong whenever lines < matched.
    #
    # truncatedRead was among 48 consequence-reporting response fields no suite named (2026-08-31).
    if rl.get("ok") is True and rl.get("found") is True:
        one = M.call("read_modloader_log", {"lines": 1})
        allp = M.call("read_modloader_log", {"lines": 1000000})
        check("T912 truncatedRead is ALWAYS present, so its absence never has to be interpreted",
              isinstance(one.get("truncatedRead"), bool), json.dumps(one)[:200])
        check("T912 a line cap of 1 returns exactly 1 line", len(one.get("lines") or []) == 1,
              "returned %d" % len(one.get("lines") or []))
        check("T912 and `returned` agrees with the array it describes",
              one.get("returned") == len(one.get("lines") or []),
              "returned=%s len=%d" % (one.get("returned"), len(one.get("lines") or [])))
        check("T912 `matched` still reports the WHOLE population, so a capped caller learns how "
              "much they did not get",
              (one.get("matched") or 0) >= (one.get("returned") or 0), json.dumps(one)[:220])
        check("T912 a LINE CAP does not set truncatedRead - that field is about the 64 MB file "
              "tail-read, and a caller reading it as 'I got everything' would be wrong whenever "
              "lines < matched",
              one.get("truncatedRead") is False, json.dumps(one)[:220])
        check("T912 uncapped, `returned` rises to `matched` - which is what proves the cap was the "
              "thing limiting it",
              allp.get("returned") == allp.get("matched"),
              "returned=%s matched=%s" % (allp.get("returned"), allp.get("matched")))
    else:
        print("  SKIP - no log file on this machine, so the truncation reporting is unreachable.")

    # ------------------------------------------------------------------ T913 trace_start / trace_stop
    # mifaudit.py DENIES trace_start for every OTHER caller in this codebase, deliberately: a blind
    # sweep that enumerates endpoint_names() would call it with no matching stop and leave a profiler
    # running for the rest of the process. That comment names the exact exception this test is:
    # "tracing is a deliberate act with a matching stop" - which is exactly what this is, immediately
    # paired. M.raw_post bypasses the DENY list the same way scratch_confirm bypasses FORBIDDEN_KEYS -
    # narrowly, for a provably safe case, not by weakening the guard for anyone else.
    print("\n=== T913: trace_start / trace_stop - UE Insights profiling, not PIE-related ===")
    ts = M.raw_post("trace_start", {"channels": "cpu,frame"})
    check("T913 trace_start succeeds", ts.get("ok") is True, json.dumps(ts)[:200])
    time.sleep(1.5)
    tp = M.call("trace_stop", {})
    check("T913 trace_stop succeeds", tp.get("ok") is True, json.dumps(tp)[:200])
    trace_path = tp.get("path") or ts.get("path")
    check("T913 a trace path is reported", bool(trace_path), json.dumps(tp)[:200])
    if trace_path:
        local = trace_path
        if local.startswith("/") or ":" not in local[:3]:
            local = os.path.join(M.call("project_paths", {}).get("projectDir") or "",
                                 local.lstrip("/\\"))
        check("T913 the trace file really exists on disk", os.path.isfile(local), local)

    # ------------------------------------------------------------------ T914 create_data_layer
    # First drafted as an expected REFUSAL (DDS2's landscape-based map was assumed non-World-Partition
    # from earlier session context), but the editor's CURRENTLY open level answered ok:true - confirmed
    # live rather than trusted from old notes, so this is real success-path coverage instead. Its own
    # response says the layer is in-memory only ("nothing was saved... An editor restart loses both"),
    # matching this whole project's invariant, so no cleanup call is needed or even possible - a
    # delete_asset attempt on it fails the same documented in-memory-handle way structs do.
    print("\n=== T914: create_data_layer ===")
    dl = M.call("create_data_layer", {"name": "MifReads5ProbeLayer_%d" % st})
    check("T914 succeeds", dl.get("ok") is True, json.dumps(dl)[:200])
    check("T914 reports a real dataLayerAsset path", bool(dl.get("dataLayerAsset")), json.dumps(dl)[:200])
    check("T914 and confirms nothing was saved", "nothing was saved" in (dl.get("note") or ""),
          dl.get("note"))

    # ------------------------------------------------------------------ T915 remove_function
    print("\n=== T915: remove_function - refusal, then the real removal via scratch_confirm ===")
    cf = M.call("create_function", {"blueprintId": bid, "name": "Reads5Func_%d" % st})
    check("T915 (setup) a scratch function is created", cf.get("ok") is True, json.dumps(cf)[:200])
    if cf.get("ok"):
        rf = M.call("remove_function", {"blueprintId": bid, "name": "Reads5Func_%d" % st})
        check("T915 refuses without confirm", rf.get("ok") is False, json.dumps(rf)[:170])
        before = [f.get("name") for f in (M.call("list_functions", {"blueprintId": bid}).get("functions") or [])]
        check("T915 the refusal left the function in place", "Reads5Func_%d" % st in before, before)

        real = SC.confirm_call("remove_function", {"blueprintId": bid, "name": "Reads5Func_%d" % st})
        check("T915 the real removal succeeds", real.get("ok") is True, json.dumps(real)[:170])
        after = [f.get("name") for f in (M.call("list_functions", {"blueprintId": bid}).get("functions") or [])]
        check("T915 the function is really gone", "Reads5Func_%d" % st not in after, after)

        # ---------------------------------------------------------- T915b functionGraphsRemaining
        # remove_function reports how many function graphs SURVIVE the removal - a consequence the
        # caller did not ask about and cannot see from ok:true. Nothing asserted it until 2026-08-31,
        # because the consequence classifier could not see a field named for what REMAINS.
        #
        # CHECKED AGAINST ITSELF, NOT AGAINST list_functions. The tempting cross-check is
        # "functionGraphsRemaining == len(list_functions)", and it is not safe: the field is
        # Blueprint->FunctionGraphs.Num() while list_functions applies its own filter, so the two
        # may legitimately disagree and a mismatch would prove nothing about either. Removing a
        # SECOND function and requiring the count to fall by exactly one is arithmetic the endpoint
        # cannot satisfy by accident, and it holds whatever the baseline number happens to be.
        first_remaining = real.get("functionGraphsRemaining")
        check("T915b it reports functionGraphsRemaining as a real number",
              isinstance(first_remaining, (int, float)), "got %r" % first_remaining)
        cf2 = M.call("create_function", {"blueprintId": bid, "name": "Reads5Func2_%d" % st})
        check("T915b (setup) a second scratch function", cf2.get("ok") is True, json.dumps(cf2)[:170])
        if cf2.get("ok") and isinstance(first_remaining, (int, float)):
            mid = SC.confirm_call("remove_function",
                                  {"blueprintId": bid, "name": "Reads5Func2_%d" % st})
            check("T915b the second removal succeeds", mid.get("ok") is True, json.dumps(mid)[:170])
            second_remaining = mid.get("functionGraphsRemaining")
            # The second function was CREATED after the first count was taken, so the graph list went
            # up by one and back down by one: the count after removing it must equal the first count.
            check("T915b and the count tracks the graphs rather than being a constant",
                  second_remaining == first_remaining,
                  "after removing one: %r; after adding and removing another: %r"
                  % (first_remaining, second_remaining))

    # ------------------------------------------------------------------ T916 remove_variable
    print("\n=== T916: remove_variable - refusal, then the real removal via scratch_confirm ===")
    rv = M.call("remove_variable", {"blueprintId": bid, "name": "Reads5Amount"})
    check("T916 refuses without confirm", rv.get("ok") is False, json.dumps(rv)[:170])
    real2 = SC.confirm_call("remove_variable", {"blueprintId": bid, "name": "Reads5Amount"})
    check("T916 the real removal succeeds", real2.get("ok") is True, json.dumps(real2)[:170])
    vars_after = M.call("list_variables", {"blueprintId": bid}).get("variables") or []
    check("T916 the variable is really gone",
          not any(v.get("name") == "Reads5Amount" for v in vars_after), vars_after)
    # removedVerified is not decoration and not a synonym for ok. RemoveMemberVariable returns
    # nothing, so the handler re-runs FindNewVariableIndex and FAILS the call if the variable is
    # still there - this flag is that observation, and it is the only thing separating "the engine
    # was asked" from "the variable is gone". Asserted here for the first time; the two facts must
    # agree, because a true flag over a surviving variable would be the worst of both.
    check("T916 and removedVerified reports the READ-BACK, not the request",
          real2.get("removedVerified") is True,
          "removedVerified=%r - the handler proves removal by re-reading, so this must be True on "
          "any successful call" % real2.get("removedVerified"))
    check("T916 and the flag agrees with list_variables",
          (real2.get("removedVerified") is True)
          == (not any(v.get("name") == "Reads5Amount" for v in vars_after)),
          "flag=%r list_variables still has it=%r"
          % (real2.get("removedVerified"),
             any(v.get("name") == "Reads5Amount" for v in vars_after)))

    # ------------------------------------------------------------------ T917 set_cast_purity
    print("\n=== T917: set_cast_purity - toggles an existing cast between pure and impure ===")
    if graph:
        cn = M.call("add_cast", {"graphId": graph, "castTo": "Pawn", "x": 1200, "y": 0})
        cast_guid = cn.get("nodeGuid") or (cn.get("node") or {}).get("guid")
        check("T917 (setup) a cast node exists", bool(cast_guid), json.dumps(cn)[:200])
        if cast_guid:
            before = M.call("get_node", {"graphId": graph, "nodeGuid": cast_guid}).get("node") or {}
            before_pins = {p.get("name") for p in (before.get("pins") or [])}
            check("T917 (setup) a fresh cast is impure - it carries exec pins",
                  "execute" in before_pins or "then" in before_pins, before_pins)

            top = M.call("set_cast_purity", {"graphId": graph, "nodeGuid": cast_guid, "pure": True})
            check("T917 switching to pure succeeds", top.get("ok") is True, json.dumps(top)[:200])
            after = M.call("get_node", {"graphId": graph, "nodeGuid": cast_guid}).get("node") or {}
            after_pins = {p.get("name") for p in (after.get("pins") or [])}
            check("T917 a pure cast really has no exec pins",
                  "execute" not in after_pins and "then" not in after_pins, after_pins)

            back = M.call("set_cast_purity", {"graphId": graph, "nodeGuid": cast_guid, "pure": False})
            check("T917 switching back to impure succeeds", back.get("ok") is True, json.dumps(back)[:200])
            final = M.call("get_node", {"graphId": graph, "nodeGuid": cast_guid}).get("node") or {}
            final_pins = {p.get("name") for p in (final.get("pins") or [])}
            check("T917 exec pins really came back",
                  "execute" in final_pins or "then" in final_pins, final_pins)

    c = M.call("compile", {"blueprintId": bid})
    check("T917 the blueprint still compiles after all the graph edits",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))

    SC.confirm_call("delete_asset", {"path": bpath})
    # T900's backup left a real .bak file next to a REAL DDS2 asset on disk (not a UE asset, so
    # delete_asset does not apply) - clean it up directly so this suite leaves no debris in Andre's
    # actual MODS folder.
    if backup_local and os.path.isfile(backup_local):
        os.remove(backup_local)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
