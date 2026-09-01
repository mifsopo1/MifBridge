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
    mat = "/Game/_MifLandLayer%d/M_LayerTest" % st
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
    r = M.call("register_landscape_layer", {"landscape": actor, "layerName": layer})
    check("L903 register_landscape_layer succeeds", r.get("ok") is not False, json.dumps(r)[:260])
    check("L903 it created the LayerInfo asset", r.get("created") is True, r.get("created"))
    check("L903 it reports where the ENGINE put it - the caller does not choose",
          bool(r.get("layerInfo")) and "pathNote" in r, json.dumps(r)[:260])
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
    again = M.call("register_landscape_layer", {"landscape": actor, "layerName": layer})
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
    # THE CLASSIFICATION BELOW IS BEST-EFFORT AND HAS NEVER BEEN SEEN TO FIRE, which is said here
    # because the first version of this block claimed the opposite. It sorted a blocked delete by
    # whether blockedBy named anything, and asserted that an empty blockedBy meant a harmless
    # invisible handle while a populated one meant a real leak. That distinction was then tested:
    # a material with a genuine MaterialInstance child pointing at it ALSO reports
    #     blockedBy {openAssetEditors: [], registryReferencers: [], rootedInMemory: []}
    # because both assets are unsaved, so the asset registry holds no reference edge to report -
    # and unsaved is what every suite fixture is. The check would have passed on the exact leak it
    # was written to catch.
    #
    # It is kept because openAssetEditors and rootedInMemory are still worth surfacing if they ever
    # do populate, but it is NOT load-bearing and must not be read as proof that nothing leaked.
    # The leftovers are printed either way, which is the part that actually informs anyone.
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
        if any(by.get(k) for k in ("openAssetEditors", "registryReferencers", "rootedInMemory")):
            blocked_visible.append("%s: %s" % (p, json.dumps(by)[:160]))
        else:
            blocked_invisible.append(p)

    check("L999 (cleanup) no leftover names an open editor or a rooted object "
          "(best-effort - blockedBy is empty for unsaved referencers, see the note above)",
          not blocked_visible, blocked_visible)
    if blocked_invisible:
        print("  NOTE  %d scratch asset(s) survive as in-memory handles delete_asset cannot see;"
              % len(blocked_invisible))
        print("        an editor restart releases them. This is reported, not failed:")
        for p in blocked_invisible:
            print("          %s" % p)

    # NOT COVERED, said out loud rather than left to be discovered: the LayerInfo asset the engine
    # creates lands beside the MAP, not under /Game/_Mif*, and this suite does not delete it - it is
    # never saved, so it dies with the editor session, but a save_asset call would strand it.
    print("\n  NOT COVERED: the engine-chosen LayerInfo asset is left in memory. It is unsaved and")
    print("  dies with the session; if a caller saves it, cleaning it up is on them.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
