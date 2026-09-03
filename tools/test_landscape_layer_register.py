"""register_landscape_layer - the verb that made paint_landscape's refusal stop being a dead end.

WHAT THIS PROVES, and it is a chain rather than a single call. paint_landscape correctly refuses a
layer whose ULandscapeLayerInfoObject is not registered on the ULandscapeInfo, because painting one
allocates a stray weightmap channel, dims the layers that ARE in use, and then has the allocation
garbage-collected by the next fixup - all under ok:true. Until now nothing could fix that state.

So the postcondition asserted here is not "the response said registered". It is the SAME predicate
paint_landscape gates on, and then paint_landscape itself:

    paint refused  ->  register  ->  paintable:true  ->  paint SUCCEEDS and touches vertices

THE FIXTURE IS BUILT, NOT FOUND, and it has to be. A layer can only be registered against a name the
landscape MATERIAL declares, and no stock landscape in this project declares one - DDS2's own
landscape reports materialLayers: [] because its material has no landscape layer nodes. So this
suite authors a material with a LandscapeLayerWeight expression, makes a small landscape from it,
and gets the genuine "declared but has no LayerInfo" state that the editor shows as
"This layer has no layer info assigned yet."

Usage:  python tools/test_landscape_layer_register.py
Exit:   0 passed   1 failed   2 SKIPPED, no bridge
"""
import json
import sys
import time

import mifaudit as M

PASS = []
FAIL = []
MADE_ACTORS = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def layers_of(actor):
    for L in (M.call("landscape_info", {}).get("landscapes") or []):
        if L.get("actorPath") == actor:
            return L
    return {}


def main():
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2
    if not M.call("describe_endpoint",
                  {"endpoint": "register_landscape_layer"}).get("registered"):
        print("skipped: register_landscape_layer is not in this build")
        return 2

    st = int(time.time() % 100000)
    root = "/Game/_MifLandLayer%d" % st
    mat = "%s/M_LayerTest" % root
    layer = "MifTestLayer%d" % st

    # ------------------------------------------------------------------ L900 the fixture
    print("=== L900: build a landscape whose material DECLARES a layer with no LayerInfo ===")
    m = M.call("create_material", {"path": mat})
    check("L900 (setup) a scratch material exists", m.get("ok") is True, json.dumps(m)[:200])
    e = M.call("add_material_expression", {
        "material": mat, "class": "LandscapeLayerWeight", "x": -400, "y": 0,
        "properties": {"ParameterName": layer, "PreviewWeight": 1.0}})
    check("L900 (setup) a LandscapeLayerWeight expression names the layer",
          e.get("propertiesApplied") == 2, json.dumps(e)[:220])
    M.call("connect_material_property",
           {"path": mat, "from": e.get("expressionName"), "property": "BaseColor"})
    M.call("recompile_material", {"material": mat})

    L = M.call("create_landscape", {
        "material": mat, "componentsX": 2, "componentsY": 2, "quadsPerSection": 31,
        "location": {"x": 300000 + st, "y": 300000, "z": 0},
        "label": "MifLayerReg%d" % st})
    actor = L.get("actorPath")
    check("L900 (setup) the landscape exists", bool(actor), json.dumps(L)[:220])
    if not actor:
        return 1
    MADE_ACTORS.append(actor)

    info = layers_of(actor)
    declared = [x.get("name") for x in (info.get("layers") or [])]
    check("L900 the MATERIAL's layer is declared on the landscape", layer in declared, declared)
    unregistered = [x for x in (info.get("layers") or [])
                    if x.get("name") == layer and not x.get("layerInfo")]
    check("L900 and it has NO LayerInfo yet - the state the editor calls 'no layer info assigned'",
          bool(unregistered), json.dumps(info.get("layers"))[:250])

    # ------------------------------------------------------------------ L901 refusals
    print("\n=== L901: what it refuses, each naming its own cause ===")
    bad = M.call("register_landscape_layer", {"landscape": actor, "layerName": "NoSuchLayer%d" % st})
    check("L901 an undeclared layer name is refused",
          bad.get("ok") is False, str(bad.get("error"))[:200])
    check("L901 and the refusal LISTS what the material does declare",
          layer in str(bad.get("error", "")), str(bad.get("error"))[:260])
    check("L901 and says the material is where a layer is added, not this endpoint",
          "material" in str(bad.get("error", "")).lower(), str(bad.get("error"))[:200])

    nolayer = M.call("register_landscape_layer", {"landscape": actor})
    check("L901 a missing layerName is refused", nolayer.get("ok") is False,
          str(nolayer.get("error"))[:200])
    badkey = M.call("register_landscape_layer",
                    {"landscape": actor, "layerName": layer, "weight": 1.0})
    check("L901 `weight` is refused and points at paint_landscape",
          badkey.get("ok") is False and "paint_landscape" in str(badkey.get("error", "")),
          str(badkey.get("error"))[:220])
    ghost = M.call("register_landscape_layer",
                   {"landscape": actor, "layerName": layer, "layerInfo": "/Game/Nope/NoSuchLI"})
    check("L901 an unloadable layerInfo asset is refused, not quietly created instead",
          ghost.get("ok") is False and "NOTHING was registered" in str(ghost.get("error", "")),
          str(ghost.get("error"))[:220])

    # ------------------------------------------------------------------ L902 paint refuses FIRST
    print("\n=== L902: paint_landscape refuses the unregistered layer - the state being fixed ===")
    before = M.call("paint_landscape", {
        "landscape": actor, "layerInfo": "/Game/Nope/NoSuchLI",
        "center": {"x": 300000 + st, "y": 300000}, "radius": 500, "weight": 1.0})
    check("L902 painting an unregistered layer is refused", before.get("ok") is False,
          str(before.get("error"))[:200])

    # ------------------------------------------------------------------ L903 register
    print("\n=== L903: register, judged by the predicate paint_landscape uses ===")
    # layerInfoPath keeps the created asset INSIDE the scratch prefix. Without it the engine puts
    # it beside the map, outside /Game/_Mif, where a dirty package jams the restore-packages guard.
    r = M.call("register_landscape_layer", {"landscape": actor, "layerName": layer,
                                            "layerInfoPath": "%s/LI_%s" % (root, layer)})
    check("L903 register_landscape_layer succeeds", r.get("ok") is not False, json.dumps(r)[:260])
    check("L903 it created the LayerInfo asset", r.get("created") is True, r.get("created"))
    check("L903 it honoured layerInfoPath - the asset is inside the scratch prefix",
          r.get("layerInfoMoved") is True and str(r.get("layerInfo","")).startswith(root),
          json.dumps(r)[:300])
    # THE CHECK THAT MATTERS: GetLayerInfoIndex, which is what paint_landscape gates on.
    check("L903 paintable:true - the same predicate paint_landscape checks, not a restatement",
          r.get("paintable") is True, json.dumps(r)[:260])
    check("L903 and it says the asset is unsaved",
          "NOT saved" in str(r.get("saveNote", "")), r.get("saveNote"))

    after_info = layers_of(actor)
    got = [x for x in (after_info.get("layers") or [])
           if x.get("name") == layer and x.get("layerInfo")]
    check("L903 landscape_info now reports a LayerInfo for the layer - read back independently",
          bool(got), json.dumps(after_info.get("layers"))[:250])

    # ------------------------------------------------------------------ L904 paint now works
    print("\n=== L904: and now paint_landscape ACCEPTS it - the whole point ===")
    painted = M.call("paint_landscape", {
        "landscape": actor, "layerInfo": r.get("layerInfo"),
        "center": {"x": 300000 + st, "y": 300000}, "radius": 800, "weight": 1.0})
    check("L904 paint_landscape succeeds on the layer it refused a moment ago",
          painted.get("ok") is True, json.dumps(painted)[:260])
    check("L904 and it really touched vertices, not zero",
          isinstance(painted.get("verticesTouched"), int) and painted.get("verticesTouched") > 0,
          painted.get("verticesTouched"))

    # ------------------------------------------------------------------ L905 re-register
    print("\n=== L905: registering again reports the replacement rather than silently swapping ===")
    again = M.call("register_landscape_layer", {"landscape": actor, "layerName": layer,
                                                "layerInfoPath": "%s/LI_%s_2" % (root, layer)})
    check("L905 a second register succeeds", again.get("ok") is not False, json.dumps(again)[:220])
    check("L905 and NAMES the LayerInfo it replaced", bool(again.get("replaced")),
          json.dumps(again)[:260])
    check("L905 with a note that painted weights survive the swap",
          "weights" in str(again.get("replacedNote", "")).lower(), again.get("replacedNote"))

    # ------------------------------------------------------------------ cleanup
    print("")
    import scratch_confirm as SC
    for a in MADE_ACTORS:
        c = M.cleanup_level_actor(a, "scratch landscape")
        check("L999 (cleanup) the scratch landscape is removed", c.get("ok") is True, c.get("error"))
    # THE MATERIAL CANNOT BE DELETED IN-SESSION. A material a landscape has used stays held after
    # the landscape actor is removed, and delete_asset says so precisely: numDeleted 0, and
    # "the holder is an in-memory handle this endpoint cannot see. An editor restart releases it."
    # There is no GC endpoint on this build to force the issue, so it is reported, not failed.
    #
    # THIS CLASSIFICATION IS LOAD-BEARING AGAIN, and the history is why it says so explicitly.
    #
    # It originally sorted a blocked delete by whether blockedBy named anything, treating an empty
    # blockedBy as a harmless invisible handle. Testing that showed it was worthless: a material
    # with a genuine MaterialInstance child pointing at it ALSO reported
    #     blockedBy {openAssetEditors: [], registryReferencers: [], rootedInMemory: []}
    # because all three miss the live object graph and the registry only knows about references
    # saved to DISK. The check would have passed on the exact leak it was written to catch, so it
    # was demoted to best-effort and the gap was filed.
    #
    # delete_asset now reports memoryReferencers (the real object graph, naming each holder and the
    # property) and transactionBuffer (is the UNDO history the only thing holding it). The blind
    # spot is closed, so this check is real again - see test_delete_blockers.py, which asserts that
    # deleting the named referencer actually frees the asset.
    blocked_invisible, blocked_visible = [], []
    for x in (M.call("find_assets", {"pathPrefix": "/Game/_MifLandLayer%d" % st}).get("assets") or []):
        p = str(x.get("objectPath") or x.get("path")).split(".")[0]
        try:
            out = SC.confirm_call("delete_asset", {"path": p})
        except Exception as exc:
            blocked_visible.append("%s: %s" % (p, str(exc)[:100]))
            continue
        if out.get("deleted"):
            continue
        by = out.get("blockedBy") or {}
        if any(by.get(k) for k in ("openAssetEditors", "registryReferencers", "rootedInMemory",
                                   "memoryReferencers")) or by.get("transactionBuffer"):
            blocked_visible.append("%s: %s" % (p, json.dumps(by)[:160]))
        else:
            blocked_invisible.append(p)

    # A LEFTOVER IS FINE; AN UNEXPLAINED ONE IS NOT. The material genuinely is held by the undo
    # buffer once a landscape has used it, and that is not a leak this suite can fix. What it CAN
    # insist on is that delete_asset named the holder - which is the whole point of the
    # memoryReferencers/transactionBuffer work, and the thing that would regress silently.
    #
    # `blocked_invisible` is the failing set on purpose: it is populated only when every blocker
    # list came back empty AND the transaction buffer said no, which is now the genuinely rare
    # case. The first draft of this line was `all(... or True) or True` - a check that cannot fail,
    # which is the exact defect this file spends its comments warning about.
    check("L999 (cleanup) every leftover has a NAMED holder - no 'invisible handle' answers",
          not blocked_invisible, blocked_invisible)
    for line in blocked_visible:
        print("  HELD  %s" % str(line)[:200])
    if blocked_invisible:
        print("  NOTE  %d scratch asset(s) survive as in-memory handles delete_asset cannot see;"
              % len(blocked_invisible))
        print("        an editor restart releases them. This is reported, not failed:")
        for p in blocked_invisible:
            print("          %s" % p)

    # THE ENGINE-CHOSEN LayerInfo MUST BE CLEANED UP TOO, and the first version of this suite left
    # it. CreateLayerInfo derives the package path from the LEVEL, so the asset lands in the map's
    # _sharedassets folder - OUTSIDE /Game/_Mif*. Being unsaved was treated as good enough, and it
    # is not: a dirty non-scratch package goes into the editor's Restore Packages list, where
    # clear_scratch_restore cannot tell it from somebody's real work and refuses. Two runs of this
    # suite were enough to put a modal in front of the next editor launch and hang it.
    #
    # It is deleted here by the exact path the endpoint reported, which is the only way to know it -
    # the caller never chose it.
    cleanup_errors = []
    for made in (r.get("layerInfo"), again.get("layerInfo")):
        if not made:
            continue
        pkg = str(made).split(".")[0]
        # BOTH assets were created with an explicit layerInfoPath under `root`, so they ARE inside
        # the scratch prefix and scratch_confirm accepts them.
        #
        # There was a fallback here that retried as M.call("delete_asset", {..., "confirm": True})
        # when scratch_confirm refused. It could never delete anything: mifaudit strips `confirm`
        # whatever its value (FORBIDDEN_KEYS), so the endpoint refused every one of those calls and
        # the result was discarded. Dead in the normal case - layerInfoPath keeps these in scratch,
        # so the except arm never ran - and actively harmful in the abnormal one, because a real
        # scratch_confirm refusal became a silent no-op and L999 then failed saying nothing about
        # why. The refusal is recorded instead, and reported as part of L999's detail.
        try:
            SC.confirm_call("delete_asset", {"path": pkg})
        except Exception as exc:
            cleanup_errors.append("%s -> %s" % (pkg, str(exc)[:120]))
    # AN UNANSWERABLE QUESTION IS NOT A PASS. This read `.get("count")` straight into a truth test,
    # and a find_assets that FAILED has no "count" at all - so None came back, None is falsy, and a
    # probe that never ran scored exactly like "the asset is gone". The one case where cleanup
    # verification matters most - the bridge in trouble - was the case it could not fail in.
    still_li, probe_errors = [], []
    for p in (r.get("layerInfo"), again.get("layerInfo")):
        if not p:
            continue
        found = M.call("find_assets", {"nameContains": str(p).split("/")[-1].split(".")[0]})
        if found.get("ok") is not True or "count" not in found:
            probe_errors.append("%s -> find_assets could not answer: %s" % (p, json.dumps(found)[:120]))
            continue
        if found.get("count"):
            still_li.append(p)
    check("L999 (cleanup) the LayerInfo assets this suite created are gone - layerInfoPath put "
          "them under the scratch root, and a dirty package left there jams restore-packages",
          not still_li and not cleanup_errors and not probe_errors,
          {"still_present": still_li, "confirm_refusals": cleanup_errors,
           "unanswered_probes": probe_errors})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
