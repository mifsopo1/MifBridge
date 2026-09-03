"""Seventh batch from coverage_gaps.py's sweep: sublevels, landscape RVT binding, a water body spline,
a spawn-actor blueprint node, a nav volume, and GAS's add_gameplay_effect_modifier.

Landscape/water work reuses the same discipline as the previous batch's sculpt_landscape: a disposable
scratch landscape/water body this test creates itself, never anything real.

T950-T954: the sublevel family, against /Game/Maps/MifWeaponTest - one of the very few LOOSE (uncooked)
.umap files left in this whole project (confirmed live: every real DDS2 gameplay map, e.g.
testing_iga, is COOKED .pak content with no loose file on disk, so add_sublevel correctly refuses
them - "no loose map file... or it is cooked .pak content"). MifWeaponTest's own name suggests it was
already a MifBridge-created scratch/test map from an earlier session, which is exactly the kind of
asset this kind of test should reach for. remove_sublevel's success path is NOT reached here: merely
ADDING a sublevel (before ANY other change) is enough to dirty the persistent level's streaming setup,
so removal always needs discardUnsaved - which has NO scratch_confirm exemption, ever (see
scratch_confirm.py's NEVER tuple; save/force/overwrite/discardUnsaved/replaceExisting are never about
one provably-scratch asset, so a path check says nothing useful about them). This is filed as a real,
correctly-understood permanent gap, tested at the refusal, not routed around.

T955: bind_landscape_rvt - a scratch landscape, one of this project's real RuntimeVirtualTexture
assets (VT_Height_Example, Brushify content - reading and binding a texture asset does not mutate it).

T956: set_water_body_spline - a scratch water body (create_water_body, already used safely elsewhere
this session), REPLACING its default spline with a real 4-point loop.

T957: add_spawn_actor - a blueprint graph node (SpawnActorFromClass), same family as T336's
add_parent_call - needs nothing but a real actor class name.

T958: add_nav_volume - just a location and a size in world units, placed far from real content.

T959: add_gameplay_effect_modifier - VALIDATION coverage only, not a real success path. DDS2 itself
has no real custom AttributeSet class with declared attributes to point this at (GAS was built for a
DIFFERENT, related project per this project's own memory - "MifBridge is a GENERAL UE5 tool, not a
DDS2-only one" - and confirmed live here: find_assets finds no AttributeSet content on this project at
all). This is the same shape as PCG's already-documented structural wall: real, not a placeholder for
later. Tests both refusals the handler's own guard chain actually reaches: a Blueprint CDO path that
does not resolve to a UGameplayEffect, and an attribute name the base AttributeSet class does not
declare.
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


def main():
    """Restore the editor-wide state in a FINALLY, whatever happens to the body.

    T952 changes the CURRENT LEVEL and changes it back, and that restore works - verified. But it
    was a plain statement in the middle of the run, so a timeout or an exception before it left a
    streaming sublevel current for every suite afterwards in the same editor. That is not a
    tidiness problem: with a classic sublevel current inside a partitioned world,
    AActor::SupportsLayers flips, and test_layers spent three assertions failing against an
    endpoint that was right. It happened exactly once, to a run that timed out while the editor was
    compositing landscape edit layers.

    The ADDED sublevel is deliberately not restored - remove_sublevel needs discardUnsaved, which
    has no scratch_confirm exemption and should not get one. Leaving a sublevel added is harmless;
    leaving it CURRENT is not.
    """
    try:
        return _run()
    finally:
        try:
            M.call("set_current_sublevel", {"path": "persistent"})
        except Exception as exc:                       # the editor may be gone or wedged
            print("  NOTE  could not restore the current level (%s). The next suite in this editor"
                  % type(exc).__name__)
            print("        may be placing actors into a sublevel - restart it if results look odd.")


def _run():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T950-T954 sublevel family
    print("\n=== T950: add_sublevel refuses a cooked/nonexistent map, no loose .umap on disk ===")
    # THE TWO MAPS THIS FAMILY NEEDS, CLASSIFIED BY THE ENDPOINT ITSELF rather than named.
    #
    # A cooked map to refuse and a loose one to accept. add_sublevel refuses a cooked map BEFORE
    # changing anything, so walking candidates is free until one is accepted - and the accepted one
    # is the fixture the suite was about to add anyway. Its own refusal is the classifier, which
    # beats a filesystem probe: that would need the project's content directory and nothing in the
    # bridge reports it (filed separately).
    cooked_map = loose_map = None
    open_level = (M.call("list_sublevels", {}) or {}).get("persistentLevel")
    for row in (M.call("find_assets", {"class": "World", "pathPrefix": "/Game/",
                                       "limit": 60}).get("assets") or []):
        pkg = (row.get("path") or row.get("objectPath") or "").rsplit(".", 1)[0]
        if not pkg or pkg == open_level:
            continue
        probe = M.call("add_sublevel", {"path": pkg, "streamingClass": "alwaysloaded"})
        if probe.get("ok") is False:
            if cooked_map is None and "loose map file" in (probe.get("error") or ""):
                cooked_map, bad = pkg, probe
        elif loose_map is None:
            loose_map, r = pkg, probe
        elif pkg != loose_map:
            # A SECOND map that would also be added. Stop: refusals cost nothing, but every
            # success MUTATES the persistent level's streaming setup, and one fixture is enough.
            # (The first version broke on the first success, so on a project whose loose map sorts
            # first it never probed a cooked one and reported "no COOKED map in this project" for
            # a project that is almost entirely cooked.)
            break
        if cooked_map and loose_map:
            break

    if cooked_map:
        check("T950 a cooked map is refused - cooked .pak content has no loose file to stream",
              bad.get("ok") is False, json.dumps(bad)[:200])
        check("T950 and explains why", "loose map file" in (bad.get("error") or ""), bad.get("error"))
    else:
        print("  NOTE  no COOKED map in this project, so T950's refusal is UNEXERCISED. On an")
        print("        uncooked project every map is loose and there is nothing to refuse.")

    if not loose_map:
        print("  NOTE  no LOOSE map in this project, so T951-T954 are UNEXERCISED. add_sublevel")
        print("        needs a .umap on disk, and a cooked project may legitimately have none.")
        return 0
    print("\n=== T951: add_sublevel succeeds against a loose map (%s) ===" % loose_map)
    # Idempotent: if an earlier live probe this same session already added it (it did, while working
    # out this batch's shape), the response is alreadyPresent:true/changed:false rather than a fresh
    # deferred op - both are a legitimate "the sublevel is present" outcome, confirmed live rather than
    # assumed to always be the fresh-add shape.
    check("T951 is accepted, fresh or already-present",
          r.get("ok") is True and (r.get("deferred") is True or r.get("alreadyPresent") is True),
          json.dumps(r)[:200])
    time.sleep(0.5)
    listed = M.call("list_sublevels", {})
    check("T951 and really appears in list_sublevels afterward",
          any(s.get("packageName") == loose_map for s in (listed.get("sublevels") or [])),
          json.dumps(listed.get("sublevels"))[:250])
    check("T951 the currently open level really is World Partition (confirms T914's flipped finding)",
          listed.get("isPartitioned") is True, listed.get("isPartitioned"))

    # T951b: netMode is only read when world is "pie" - it reaches the handler through
    # ResolvePIEWorld, which the editor branch never calls. {"netMode":"client"} with no world at
    # all defaulted to EDITOR, dropped the netMode, and answered with the editor's sublevels under
    # ok:true, so a caller asking what the CLIENT had streamed in got a different world's answer.
    #
    # Tested HERE rather than in the PIE suite on purpose: the refusal needs no play session, and a
    # guard about "you did not ask for pie" should be proved without one.
    print("\n=== T951b: netMode is refused when the world is not pie ===")
    wrong = M.call("list_sublevels", {"netMode": "client"})
    check("T951b netMode without world:pie is refused rather than answering about the editor",
          wrong.get("ok") is False, json.dumps(wrong)[:220])
    check("T951b and the refusal says which world it would have listed, and what to pass instead",
          "EDITOR" in (wrong.get("error") or "") and "world:\"pie\"" in (wrong.get("error") or ""),
          (wrong.get("error") or "")[:240])
    check("T951b and it did NOT answer anyway - no sublevels came back with the refusal",
          wrong.get("sublevels") is None and wrong.get("worldName") is None,
          json.dumps({k: wrong.get(k) for k in ("sublevels", "worldName")})[:200])
    # The ordinary call still works - a guard that refuses everything would pass the three above.
    still = M.call("list_sublevels", {"world": "editor"})
    check("T951b and an explicit world:editor with no netMode still answers",
          still.get("ok") is True and still.get("world") == "editor", json.dumps(still)[:200])

    print("\n=== T952: set_current_sublevel ===")
    to_sub = M.call("set_current_sublevel", {"path": loose_map})
    # changed:True is not the only honest success - a full-suite regression sweep found this suite's
    # OWN earlier calls (or a prior pass of this same suite, since run_all_suites runs everything
    # twice) can already have left the discovered loose map as the current level by then, and
    # set_current_sublevel correctly answers ok:true, changed:false, "already the current level -
    # nothing was changed" rather than pretending to switch. What actually matters is currentLevel
    # ending up right, not whether a switch was NEEDED to get there.
    check("T952 switching to the sublevel succeeds", to_sub.get("ok") is True, json.dumps(to_sub)[:200])
    check("T952 and reports the right currentLevel", to_sub.get("currentLevel") == loose_map,
          to_sub.get("currentLevel"))
    back = M.call("set_current_sublevel", {"path": "persistent"})
    check("T952 switching back to persistent succeeds", back.get("ok") is True, json.dumps(back)[:200])

    print("\n=== T953: set_sublevel_streaming ===")
    ss = M.call("set_sublevel_streaming", {"path": loose_map, "streamingClass": "dynamic"})
    check("T953 succeeds (deferred to next tick)", ss.get("ok") is True, json.dumps(ss)[:200])
    time.sleep(0.5)

    print("\n=== T954: set_sublevel_visibility, and remove_sublevel's real, permanent limitation ===")
    vis_off = M.call("set_sublevel_visibility", {"path": loose_map, "visible": False})
    check("T954 hiding succeeds", vis_off.get("ok") is True and vis_off.get("changed", {}).get("visible") is False,
          json.dumps(vis_off)[:200])
    vis_on = M.call("set_sublevel_visibility", {"path": loose_map, "visible": True})
    check("T954 showing it again succeeds", vis_on.get("ok") is True and vis_on.get("changed", {}).get("visible") is True,
          json.dumps(vis_on)[:200])

    # remove_sublevel: even the bare act of adding it above already dirtied the persistent level's
    # streaming setup, so this refusal is not a contrived edge case - it is the ONLY path reachable
    # here, and discardUnsaved can never be scratch-verified (see module docstring).
    rm = M.call("remove_sublevel", {"path": loose_map})
    check("T954 remove_sublevel refuses - the level has real unsaved changes", rm.get("ok") is False,
          json.dumps(rm)[:200])
    check("T954 and explains why, naming discardUnsaved", "UNSAVED" in (rm.get("error") or ""),
          rm.get("error"))

    # ------------------------------------------------------------------ T955 bind_landscape_rvt
    print("\n=== T955: bind_landscape_rvt - a scratch landscape, a real RVT asset ===")
    lx, ly = 1000000 + st, 1000000 + st
    land = M.call("create_landscape", {"location": {"x": lx, "y": ly, "z": 500000}, "componentsX": 2,
                                       "componentsY": 2, "quadsPerSection": 63, "sectionsPerComponent": 1,
                                       "label": "MifReads7RvtLand_%d" % st})
    land_path = land.get("actorPath")
    check("T955 (setup) a scratch landscape is created", land.get("ok") is True and bool(land_path),
          json.dumps(land)[:200])
    # ANY RuntimeVirtualTexture will do - T955 asserts the BINDING, and binding is scene-wide and
    # does not mutate the texture asset. This named one Brushify asset, which only DDS2 has.
    rvt_rows = M.call("find_assets", {"class": "RuntimeVirtualTexture", "limit": 10}).get("assets") or []
    rvt_asset = (rvt_rows[0].get("path") or rvt_rows[0].get("objectPath")) if rvt_rows else None
    if land_path and not rvt_asset:
        print("  NOTE  no RuntimeVirtualTexture asset in this project, so T955 is UNEXERCISED.")
    if land_path and rvt_asset:
        rvt = M.call("bind_landscape_rvt", {
            "landscape": land_path,
            "runtimeVirtualTextures": [rvt_asset]})
        check("T955 succeeds", rvt.get("ok") is True, json.dumps(rvt)[:300])
        check("T955 and really bound the RVT", bool(rvt.get("bound")), json.dumps(rvt.get("bound")))
        # A RuntimeVirtualTextureVolume is a SCENE-WIDE contract for one RVT asset, not per-landscape -
        # confirmed live: binding the SAME RVT to a second scratch landscape reused the volume an
        # earlier bind already created (alreadyPresent), rather than creating a second one. Either
        # shape proves the RVT ends up covered by a volume, which is the actual thing worth checking.
        check("T955 and the RVT is covered by a volume - fresh or already-present",
              bool(rvt.get("volumesCreated")) or bool(rvt.get("alreadyPresent")),
              json.dumps({"volumesCreated": rvt.get("volumesCreated"), "alreadyPresent": rvt.get("alreadyPresent")}))

    # CLEANUP. create_landscape spawns into the EDITOR world, so this is NOT torn down when PIE
    # stops - it persists and is carried into every later PIE session. See
    # mifaudit.cleanup_level_actor for the T1606 breakage an uncleaned one already caused.
    if land_path:
        _c = M.cleanup_level_actor(land_path, "scratch landscape")
        check("T955 (cleanup) the scratch landscape is removed, not left in the level",
              _c.get("ok") is True, _c.get("error"))

    # ------------------------------------------------------------------ T956 set_water_body_spline
    print("\n=== T956: set_water_body_spline - a scratch water body, a real 4-point loop ===")
    wx, wy = 1100000 + st, 1100000 + st
    # LABELLED so mifaudit.is_scratch_fixture can see it. The cleanup below is the normal path, but
    # anything raising between here and there leaves a water body in the editor world, and an
    # unlabelled one is indistinguishable from project content to every suite hunting for something
    # to adopt.
    wb = M.call("create_water_body", {"type": "Lake", "x": wx, "y": wy, "z": 500000,
                                      "label": "MifLakeT956_%d" % st})
    wb_path = wb.get("actorPath")
    check("T956 (setup) a scratch Lake water body is created", wb.get("ok") is True and bool(wb_path),
          json.dumps(wb)[:200])
    if wb_path:
        pts = [{"x": wx, "y": wy, "z": 500000}, {"x": wx + 1000, "y": wy, "z": 500000},
               {"x": wx + 1000, "y": wy + 1000, "z": 500000}, {"x": wx, "y": wy + 1000, "z": 500000}]
        sp = M.call("set_water_body_spline", {"path": wb_path, "points": pts})
        check("T956 succeeds", sp.get("ok") is True, json.dumps(sp)[:200])
        check("T956 the spline really has the 4 points now", sp.get("splinePoints") == 4, sp.get("splinePoints"))

    # CLEANUP. create_water_body spawns into the EDITOR world, so this is NOT torn down when PIE
    # stops - it persists and is carried into every later PIE session. See
    # mifaudit.cleanup_level_actor for the T1606 breakage an uncleaned one already caused.
    if wb_path:
        _c = M.cleanup_level_actor(wb_path, "scratch water body")
        check("T956 (cleanup) the scratch water body is removed, not left in the level",
              _c.get("ok") is True, _c.get("error"))

    # ------------------------------------------------------------------ T957 add_spawn_actor
    print("\n=== T957: add_spawn_actor - a SpawnActorFromClass node ===")
    bpath = "/Game/_MifReads7/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    check("T957 (setup) a scratch blueprint is created", bool(bid), bid)
    if bid:
        graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
        graph = next((g.get("graphId") for g in graphs if "EventGraph" in (g.get("name") or "")), None)
        r = M.call("add_spawn_actor", {"graphId": graph, "actorClass": "StaticMeshActor", "x": 500, "y": 500})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T957 succeeds", r.get("ok") is True, json.dumps(r)[:200])
        if r.get("ok") and guid:
            resolved = M.call("get_node", {"graphId": graph, "nodeGuid": guid})
            check("T957 the node is really in the graph", bool((resolved.get("node") or {}).get("guid")),
                  json.dumps(resolved)[:200])

    # ------------------------------------------------------------------ T958 add_nav_volume
    print("\n=== T958: add_nav_volume ===")
    nv = M.call("add_nav_volume", {"location": {"x": 1200000 + st, "y": 1200000 + st, "z": 500000},
                                   "size": {"x": 2000, "y": 2000, "z": 1000}, "label": "MifReads7NavVol_%d" % st})
    check("T958 succeeds", nv.get("ok") is True, json.dumps(nv)[:200])
    check("T958 reports the coverage size back", nv.get("coverage", {}).get("x") == 2000, nv.get("coverage"))
    # CLEANUP - added 2026-08-29, found live by a full run_all_suites.py double-pass sweep. This
    # spawns straight into the EDITOR world (World->SpawnActor in H_add_nav_volume, ActiveWorld() not
    # a PIE-scoped one), so an uncleaned volume here is NOT torn down when PIE stops - it persists in
    # the persistent level and gets carried into every LATER PIE session too, one more accumulating
    # with every run of this suite. That silently broke tools/test_pie_family.py's own T1606 check
    # (its own "0 NavMeshBoundsVolume actors -> no navigation coverage" precondition is no longer true
    # once one of these exists ANYWHERE in the level, even parked a million units away and providing
    # no real coverage where a pawn actually is) - a real "state surviving between runs" bug, not a
    # test_pie_family.py bug. delete_level_actor addresses a live actor path, not a /Game/... asset,
    # so scratch_confirm.confirm_call's path-prefix check does not apply here (and would wrongly
    # refuse it) - M.raw_post is the same narrow, deliberate bypass used elsewhere in this project for
    # exactly this shape of call.
    if nv.get("actorPath"):
        cleanup = M.raw_post("delete_level_actor", {"actorPath": nv["actorPath"], "confirm": True})
        check("T958 (cleanup) the scratch NavMeshBoundsVolume is removed, not left in the level",
              cleanup.get("ok") is True, cleanup.get("error"))

    # ------------------------------------------------------------------ T959 add_gameplay_effect_modifier
    print("\n=== T959: add_gameplay_effect_modifier - validation only, no real AttributeSet on this project ===")
    gepath = "/Game/_MifReads7/GE_%d" % st
    ge = M.call("create_blueprint", {"path": gepath, "parentClass": "GameplayEffect"})
    check("T959 (setup) a scratch GameplayEffect blueprint is created", ge.get("ok") is True, json.dumps(ge)[:200])
    if ge.get("ok"):
        # the class path, not the CDO - a real, distinct guard from the attribute one below.
        wrong_kind = M.call("add_gameplay_effect_modifier", {
            "objectPath": ge.get("class"), "attributeSetClass": "AttributeSet", "attributeName": "X",
            "operation": "Add", "magnitude": 1.0})
        check("T959 a non-CDO Blueprint class path is refused", wrong_kind.get("ok") is False,
              json.dumps(wrong_kind)[:200])

        cdo = gepath + ".Default__GE_%d_C" % st
        bad_attr = M.call("add_gameplay_effect_modifier", {
            "objectPath": cdo, "attributeSetClass": "AttributeSet", "attributeName": "NoSuchAttr_zz",
            "operation": "Add", "magnitude": 1.0})
        check("T959 an unknown attribute on the CDO is refused", bad_attr.get("ok") is False,
              json.dumps(bad_attr)[:200])
        check("T959 and names the real reason - the base AttributeSet declares no such property",
              "no property named" in (bad_attr.get("error") or ""), bad_attr.get("error"))
        SC.confirm_call("delete_asset", {"path": gepath})

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
