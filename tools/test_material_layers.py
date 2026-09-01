"""Material layer stacks - reading one, writing one, and the invariant that makes it easy to corrupt.

THE INVARIANT. Layers[0] is the BASE and takes no blend; every layer above it needs one, so Blends
holds exactly Layers.Num() - 1 entries. Four editor-only arrays run parallel to Layers as well
(LayerNames, LayerStates, LayerGuids, LayerLinkStates), and SetMaterialLayers ACCEPTS a stack whose
parallel arrays disagree in length - it misbehaves later, in the material editor, rather than at
the point of the mistake. So the writer never assembles the arrays itself: it builds through
AddDefaultBackgroundLayer() and AppendBlendedLayer(), and this suite asserts the result is
well-formed rather than trusting that it is.

A DIFFERENT AXIS FROM PARAMETERS, which is why the read is opt-in. Parameters are scalars and
textures; the stack is which UMaterialFunctions composite and in what order. A material instance
can override the whole stack and nothing in the parameter table hints that it has.

Fixtures use the engine's own ML_ExampleMaterialLayer and MatLayerBlend_Standard, so no layer
functions have to be authored here.

Usage:  python tools/test_material_layers.py
Exit:   0 passed   1 failed   2 SKIPPED, no bridge
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []

LAYER = "/Engine/Functions/MaterialLayerFunctions/ML_ExampleMaterialLayer"
BLEND = "/Engine/Functions/MaterialLayerFunctions/MatLayerBlend_Standard"


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("skipped: %s" % why)
        return 2
    if not M.call("describe_endpoint", {"endpoint": "set_material_layers"}).get("registered"):
        print("skipped: set_material_layers is not in this build")
        return 2

    st = int(time.time() % 100000)
    root = "/Game/_MifMatLayers%d" % st
    mat = "%s/M_Base" % root
    mi = "%s/MI_Test" % root

    print("=== L400: a material and an instance of it ===")
    check("L400 (setup) base material", M.call("create_material", {"path": mat}).get("ok") is True)
    check("L400 (setup) material instance",
          M.call("create_material_instance", {"path": mi, "parent": mat}).get("ok") is True)

    # ------------------------------------------------------------------ L401 read with no stack
    print("\n=== L401: a material with NO stack says so, and is not an error ===")
    none = M.call("list_material_parameters", {"path": mi, "layers": True})
    check("L401 the read succeeds", none.get("ok") is not False, json.dumps(none)[:200])
    check("L401 hasLayers is false", none.get("hasLayers") is False, none.get("hasLayers"))
    check("L401 and it explains that most materials have no stack and that is not a failure",
          "not an error" in str(none.get("layersNote", "")), none.get("layersNote"))
    check("L401 layers[] is absent rather than an empty array pretending to be a stack",
          "layers" not in none, sorted(none.keys())[:12])

    # ------------------------------------------------------------------ L402 refusals
    print("\n=== L402: what the writer refuses ===")
    base_mat = M.call("set_material_layers", {"path": mat, "layers": [{"function": LAYER}]})
    check("L402 a base UMaterial is refused, saying the stack comes from its graph",
          base_mat.get("ok") is False and "expression graph" in str(base_mat.get("error", "")),
          str(base_mat.get("error"))[:240])

    noarr = M.call("set_material_layers", {"path": mi})
    check("L402 a missing layers[] is refused and points at the reader",
          noarr.get("ok") is False and "list_material_parameters" in str(noarr.get("error", "")),
          str(noarr.get("error"))[:220])

    empty = M.call("set_material_layers", {"path": mi, "layers": []})
    check("L402 an empty layers[] is refused - a stack needs a base",
          empty.get("ok") is False and "base" in str(empty.get("error", "")).lower(),
          str(empty.get("error"))[:220])

    # THE INVARIANT, refused in both directions.
    base_blend = M.call("set_material_layers", {"path": mi, "layers": [
        {"function": LAYER, "blend": BLEND}]})
    check("L402 a blend on the BASE layer is refused - nothing is beneath it to composite over",
          base_blend.get("ok") is False and "nothing is beneath" in str(base_blend.get("error", "")),
          str(base_blend.get("error"))[:240])

    no_blend = M.call("set_material_layers", {"path": mi, "layers": [
        {"function": LAYER}, {"function": LAYER}]})
    check("L402 a layer ABOVE the base with no blend is refused, naming the invariant",
          no_blend.get("ok") is False and "one entry fewer" in str(no_blend.get("error", "")),
          str(no_blend.get("error"))[:240])

    ghost = M.call("set_material_layers", {"path": mi, "layers": [
        {"function": "/Game/Nope/NoSuchFunction"}]})
    check("L402 an unloadable function is refused before anything is touched",
          ghost.get("ok") is False and "NOTHING was changed" in str(ghost.get("error", "")),
          str(ghost.get("error"))[:220])

    badkey = M.call("set_material_layers", {"path": mi, "blends": [BLEND],
                                            "layers": [{"function": LAYER}]})
    check("L402 a separate `blends` array is refused - each layer carries its own",
          badkey.get("ok") is False and "in step" in str(badkey.get("error", "")),
          str(badkey.get("error"))[:240])

    # ------------------------------------------------------------------ L403 write
    print("\n=== L403: write a two-layer stack, judged by what the instance reports back ===")
    w = M.call("set_material_layers", {"path": mi, "layers": [
        {"function": LAYER, "name": "Base"},
        {"function": LAYER, "blend": BLEND, "name": "Second", "enabled": True}]})
    check("L403 the write succeeds", w.get("ok") is not False, json.dumps(w)[:260])
    check("L403 the instance now HAS a stack", w.get("hasLayers") is True, w.get("hasLayers"))
    check("L403 two layers, one blend - the invariant holds",
          w.get("layerCount") == 2 and w.get("blendCount") == 1, json.dumps(w)[:220])
    check("L403 stackWellFormed, computed from the arrays rather than asserted",
          w.get("stackWellFormed") is True, json.dumps(w)[:200])
    check("L403 it got the number of layers it was asked for",
          w.get("layerCountMatches") is True, json.dumps(w)[:200])
    lay = w.get("layers") or []
    check("L403 layer 0 is flagged as the base and carries NO blend",
          len(lay) == 2 and lay[0].get("isBase") is True and "blend" not in lay[0],
          json.dumps(lay[:1]))
    check("L403 layer 1 carries the blend that composites it",
          len(lay) == 2 and lay[1].get("isBase") is False and BLEND in str(lay[1].get("blend")),
          json.dumps(lay[1:]) if len(lay) > 1 else lay)
    check("L403 the names round-tripped",
          [x.get("name") for x in lay] == ["Base", "Second"], [x.get("name") for x in lay])

    # ------------------------------------------------------------------ L404 read it back
    print("\n=== L404: and the READER sees the same stack, through a different call ===")
    r = M.call("list_material_parameters", {"path": mi, "layers": True})
    check("L404 hasLayers is now true", r.get("hasLayers") is True, r.get("hasLayers"))
    check("L404 the reader reports the same two layers and one blend",
          r.get("layerCount") == 2 and r.get("blendCount") == 1, json.dumps(r)[:220])
    rl = r.get("layers") or []
    check("L404 with the same names, in the same order",
          [x.get("name") for x in rl] == ["Base", "Second"], [x.get("name") for x in rl])
    check("L404 and it says which entry is the base and how blends pair up",
          "one fewer blend than layers" in str(r.get("layersNote", "")), r.get("layersNote"))

    # ------------------------------------------------------------------ L405 disabled state
    print("\n=== L405: a disabled layer stays in the stack and reports disabled ===")
    d = M.call("set_material_layers", {"path": mi, "layers": [
        {"function": LAYER, "name": "Base"},
        {"function": LAYER, "blend": BLEND, "name": "Off", "enabled": False}]})
    dl = d.get("layers") or []
    check("L405 the disabled layer is still present", len(dl) == 2, json.dumps(d)[:200])
    check("L405 and reports enabled:false rather than vanishing",
          len(dl) == 2 and dl[1].get("enabled") is False,
          json.dumps(dl[1:]) if len(dl) > 1 else dl)

    # ------------------------------------------------------------------ cleanup
    print("")
    for p in (mi, mat):
        try:
            SC.confirm_call("delete_asset", {"path": p})
        except Exception as exc:
            print("  cleanup: %s" % str(exc)[:120])
    left = M.call("find_assets", {"pathPrefix": root}).get("count")
    if left:
        print("  NOTE  %s scratch asset(s) still held by in-memory handles; an editor restart"
              % left)
        print("        releases them. See the delete_asset blockedBy item in the spec.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
