"""Does a Blender WRITE op's claimed effect actually independently verify?

UE's audit_postconditions.py asks whether a write endpoint's own ok:true response is backed by a
SEPARATE read-back, rather than trusted at face value - "ok:true is not proof" is the bridge manual's
own first rule. mif_mesh_roundtrip's fidelity gate (fixed 2026-08-27/28, see FEATURE_PARITY_SPEC.md)
is the concrete lesson this exists to generalise from: it compiled clean, ran clean end to end by its
OWN accounting, and was completely broken - because nothing had ever independently re-measured what it
claimed to have verified. This is that discipline applied to the addon's write surface, which had no
postcondition check of any kind before this.

Andre, 2026-08-27/28: "make sure our blender porting and endpoints are as indepth testing wise as our
UE side" - this is the second piece of that (see audit_blender_read_purity.py for the first).

METHOD. A linear pipeline against ONE real, non-trivial mesh (whatever export_asset/import_mesh already
produced on disk from earlier session testing, reused here rather than re-exporting from UE - this
script only needs Blender, not the UE half of the bridge, to run): clear_scene, import_mesh, then each
write op in turn, each followed by an INDEPENDENT object_info/scene_info call (never the op's own
response body) that checks the SPECIFIC, concrete thing that op claims to do. A postcondition that only
re-checks what the op already said would not be independent - each check below asks a question the op's
own response cannot beg.

Usage:
    python tools/audit_blender_postconditions.py --fbx <path to a real, non-trivial mesh FBX>
Needs a live Blender with the MifBlender addon listening. Reuses an FBX already on disk (e.g. one
export_asset already wrote to Saved/MifBridge/Export/) rather than needing the UE bridge to be up too -
pass --fbx explicitly, or it looks for the most recently modified .fbx under the default export dir.
"""
import argparse
import glob
import os
import sys

from blender_audit_common import call as _call

DEFAULT_EXPORT_GLOB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "..", "Saved", "MifBridge", "Export", "*.fbx")


def _find_fbx():
    candidates = sorted(glob.glob(DEFAULT_EXPORT_GLOB), key=os.path.getmtime, reverse=True)
    return candidates[0] if candidates else None


def _ok(step, detail=""):
    print("  OK    %-16s %s" % (step, detail))


def _fail(findings, step, detail):
    print("  FAIL  %-16s %s" % (step, detail))
    findings.append((step, detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fbx", default=None, help="path to an FBX to import; defaults to the most "
                    "recently modified file under Saved/MifBridge/Export/")
    args = ap.parse_args()

    fbx = args.fbx or _find_fbx()
    if not fbx or not os.path.isfile(fbx):
        print("no FBX to work with - pass --fbx <path>, or export one first (export_asset on the UE "
              "side, or reuse one already under Saved/MifBridge/Export/).")
        return 2

    try:
        p = _call("ping", {}, timeout=5.0)
    except OSError as exc:
        # A SQUATTER IS NOT AN ABSENCE. blender_audit_common owns the distinction so the two audits
        # and three suites give the same answer - see docs/06 issue 15 for the UE editor that has
        # held MifBlender's port on this machine.
        try:
            # No HOST/PORT globals in this file - blender_audit_common owns the address as well as
            # the diagnosis, which is the point of sharing it.
            import blender_audit_common as _B
            print("Blender backend unreachable (%s)." % exc)
            return _B.skip_banner("postconditions")
        except ImportError:
            print("Blender backend unreachable (%s). Start it first." % exc)
            return 2
    if not p.get("ok"):
        print("ping failed: %s" % p.get("error"))
        return 2

    findings = []
    print("using %s" % fbx)

    # ---- clear_scene -------------------------------------------------------------------------
    r = _call("clear_scene", {})
    si = _call("scene_info", {})
    if r.get("ok") and si.get("ok") and si.get("objectCount") == 0:
        _ok("clear_scene", "scene_info independently confirms objectCount:0")
    else:
        _fail(findings, "clear_scene", "op:%r scene_info.objectCount:%r (want 0)"
              % (r.get("ok"), si.get("objectCount")))

    # ---- import_mesh (setup, not itself a candidate - but its postcondition is free to check) ----
    r = _call("import_mesh", {"file": fbx, "clearScene": False})
    if not r.get("ok") or not r.get("imported"):
        print("import_mesh failed, cannot continue: %s" % r.get("error"))
        return 2
    obj = r["imported"][0]["name"]
    si = _call("scene_info", {})
    names = [o["name"] for o in (si.get("objects") or [])]
    if obj in names:
        _ok("import_mesh", "scene_info independently confirms '%s' is present" % obj)
    else:
        _fail(findings, "import_mesh", "'%s' claimed imported but absent from scene_info" % obj)

    oi0 = _call("object_info", {"object": obj}).get("object") or {}
    tris0 = oi0.get("tris")
    print("  baseline: %s verts / %s tris" % (oi0.get("verts"), tris0))

    # ---- uv_unwrap ----------------------------------------------------------------------------
    layer_name = "MifAuditUV"
    r = _call("uv_unwrap", {"object": obj, "method": "SMART", "uvLayer": layer_name})
    oi = _call("object_info", {"object": obj}).get("object") or {}
    if r.get("ok") and layer_name in (oi.get("uvLayers") or []):
        _ok("uv_unwrap", "object_info independently confirms '%s' is now in uvLayers %r"
            % (layer_name, oi.get("uvLayers")))
    else:
        _fail(findings, "uv_unwrap", "op:%r object_info.uvLayers:%r (want %r present)"
              % (r.get("ok"), oi.get("uvLayers"), layer_name))

    # ---- set_material_slots --------------------------------------------------------------------
    want_slots = ["MifAudit_A", "MifAudit_B"]
    cur_count = len(oi.get("materialSlots") or [])
    r = _call("set_material_slots", {"object": obj, "slots": want_slots,
                                      "allowResize": len(want_slots) != cur_count})
    oi = _call("object_info", {"object": obj}).get("object") or {}
    if r.get("ok") and oi.get("materialSlots") == want_slots:
        _ok("set_material_slots", "object_info independently confirms materialSlots == %r" % want_slots)
    else:
        _fail(findings, "set_material_slots", "op:%r object_info.materialSlots:%r (want %r)"
              % (r.get("ok"), oi.get("materialSlots"), want_slots))

    # ---- extrude_skirt --------------------------------------------------------------------------
    # direction defaults to "down" (extrude_skirt's own default), which extends Z-MIN downward, not
    # Z-max - checking Z-size growth instead of either bound specifically is direction-agnostic and
    # matches what the op's own response calls sizeDeltaUU.
    z_size_before = (oi.get("boundsLocalSizeBU") or [0, 0, 0])[2]
    depth = 10.0
    r = _call("extrude_skirt", {"object": obj, "boundaryOnly": True, "depthUU": depth})
    oi = _call("object_info", {"object": obj}).get("object") or {}
    z_size_after = (oi.get("boundsLocalSizeBU") or [0, 0, 0])[2] if r.get("ok") else None
    # depthUU is in UNREAL units; boundsLocalSizeBU is in BLENDER units - the addon's own
    # unrealUnitsPerBlenderUnit (100.0, from ping/scene_info) is the conversion, same constant
    # server.py's fidelity gate now folds object scale through rather than assuming.
    expected_growth_bu = depth / 100.0
    if r.get("ok") and z_size_after is not None and \
            abs((z_size_after - z_size_before) - expected_growth_bu) < 1e-3:
        _ok("extrude_skirt", "object_info independently confirms Z size grew by %.4f BU (depthUU %.1f / 100)"
            % (z_size_after - z_size_before, depth))
    else:
        _fail(findings, "extrude_skirt", "op:%r Z size before/after %r/%r (want +%.4f BU)"
              % (r.get("ok"), z_size_before, z_size_after, expected_growth_bu))

    # ---- bevel_edges ------------------------------------------------------------------------
    edges_before = oi.get("edges")
    r = _call("bevel_edges", {"object": obj, "boundaryOnly": True, "offsetUU": 2.0, "segments": 2})
    oi = _call("object_info", {"object": obj}).get("object") or {}
    edges_after = oi.get("edges") if r.get("ok") else None
    # A bevel with segments>1 always ADDS edges - it is topologically incapable of a no-op on a
    # real selection. Equal or fewer edges after means it did not actually run against this mesh.
    if r.get("ok") and edges_after is not None and edges_after > edges_before:
        _ok("bevel_edges", "object_info independently confirms edge count grew %d -> %d"
            % (edges_before, edges_after))
    else:
        _fail(findings, "bevel_edges", "op:%r edges before/after %r/%r (want strictly more)"
              % (r.get("ok"), edges_before, edges_after))

    # ---- decimate_mesh ------------------------------------------------------------------------
    tris_before = oi.get("tris")
    r = _call("decimate_mesh", {"object": obj, "mode": "COLLAPSE", "ratio": 0.5})
    oi = _call("object_info", {"object": obj}).get("object") or {}
    tris_after = oi.get("tris") if r.get("ok") else None
    if r.get("ok") and tris_after is not None and tris_after < tris_before:
        _ok("decimate_mesh", "object_info independently confirms tri count dropped %d -> %d"
            % (tris_before, tris_after))
    else:
        _fail(findings, "decimate_mesh", "op:%r tris before/after %r/%r (want strictly fewer)"
              % (r.get("ok"), tris_before, tris_after))

    # ---- delete_object ------------------------------------------------------------------------
    r = _call("delete_object", {"object": obj})
    si = _call("scene_info", {})
    names = [o["name"] for o in (si.get("objects") or [])]
    if r.get("ok") and obj not in names:
        _ok("delete_object", "scene_info independently confirms '%s' is gone" % obj)
    else:
        _fail(findings, "delete_object", "op:%r '%s' still in scene_info.objects:%r"
              % (r.get("ok"), obj, names))

    print("")
    if findings:
        print("%d of 7 postconditions FAILED - the op's own ok:true was not backed by reality:" %
              len(findings))
        for step, detail in findings:
            print("  %-16s %s" % (step, detail))
        return 1
    print("OK  all 7 write ops' claimed effects independently re-verified via a separate call")
    return 0


if __name__ == "__main__":
    sys.exit(main())
