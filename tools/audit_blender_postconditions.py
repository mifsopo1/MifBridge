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

# THIS CHECKOUT'S PROJECT, AND THAT IS CORRECT HERE - unlike mifwatch and test_crash_journal, which
# had the same shape and were wrong. Both of those describe a LIVE editor, so computing the path
# from this file's location made them read another project's data; they now ask the running process
# (mifaudit.live_saved_dir). This audit is deliberately OFFLINE - it needs only Blender, not the UE
# bridge - so there is no process to ask, and a previously exported fixture in this tree is exactly
# what it wants. Pass --fbx to point it elsewhere.
DEFAULT_EXPORT_GLOB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "..", "Saved", "MifBridge", "Export", "*.fbx")


def _find_fbx():
    candidates = sorted(glob.glob(DEFAULT_EXPORT_GLOB), key=os.path.getmtime, reverse=True)
    return candidates[0] if candidates else None


VERIFIED = []


def _ok(step, detail=""):
    VERIFIED.append(step)
    print("  OK    %-16s %s" % (step, detail))


# Names shaped like reads. Used ONLY to size the write surface for the reach line - the audit does
# not decide what to check from this.
_READ_PREFIXES = ("list_", "describe_", "get_")
_READ_SUFFIXES = ("_info",)


def _write_surface():
    """(verified, roughly-how-many-write-ops, whether the count is trustworthy).

    A HEURISTIC, AND IT SAYS SO WHEN IT PRINTS. The addon has no authoritative list of write ops,
    so this counts the ops whose names are not read-shaped and not the handful that neither read
    nor write the scene. Publishing a heuristic without labelling it would trade one confident
    wrong number for another, which is the whole failure this line exists to end.
    """
    try:
        ops = _call("ping", {}, timeout=5.0).get("ops") or []
    except Exception:                                               # noqa: BLE001
        return len(VERIFIED), None, False
    if not ops:
        return len(VERIFIED), None, False
    writes = [o for o in ops
              if not o.startswith(_READ_PREFIXES) and not o.endswith(_READ_SUFFIXES)
              and o not in ("ping",)]
    return len(VERIFIED), len(writes), True


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

    # Everything below this line starts by emptying the scene. Ask whose scene it is first.
    import blender_audit_common as _BC
    stop = _BC.require_headless("audit_blender_postconditions", _call)
    if stop is not None:
        return stop

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

    # ---- A REFUSAL IS NOT A BROKEN POSTCONDITION -------------------------------------------------
    #
    # Both mesh ops below were reported for months as "the op's own ok:true was not backed by
    # reality" when what actually happened is that they returned ok:FALSE and refused. The op never
    # claimed anything; the fixture did not suit the selector. That is a FALSE FAILURE, and this
    # directory's own rule - written into test_blender_rig - is that a false failure is worse than a
    # false pass, because it teaches the reader to ignore the tool.
    #
    # So refusals are now separated from postcondition breaks and the op's own error is quoted. The
    # two need different fixes: a refusal means look at the payload, a break means look at the op.
    def _refused(findings_, name, r_, note=""):
        _fail(findings_, name, "REFUSED (not a postcondition break): %s%s"
              % (str(r_.get("error"))[:180], note))

    # ---- extrude_skirt --------------------------------------------------------------------------
    # direction defaults to "down" (extrude_skirt's own default), which extends Z-MIN downward, not
    # Z-max - checking Z-size growth instead of either bound specifically is direction-agnostic and
    # matches what the op's own response calls sizeDeltaUU.
    # ON ITS OWN FIXTURE, not the Sphere. extrude_skirt extrudes BOUNDARY edge loops - the fix for a
    # flat-edged tile that hovers where terrain falls away - and the imported Sphere is a CLOSED mesh
    # with no boundary at all. boundaryOnly matched 0 edges there and the op refused, correctly, with
    # a message naming the bounds and the selection breakdown. Testing an op against input it does
    # not apply to measures nothing; a plane has exactly the four boundary edges this op is for.
    skirt = "MifAuditSkirt"
    _call("create_primitive", {"kind": "plane", "name": skirt, "size": 2.0})
    soi = _call("object_info", {"object": skirt}).get("object") or {}
    z_size_before = (soi.get("boundsLocalSizeBU") or [0, 0, 0])[2]
    depth = 10.0
    r = _call("extrude_skirt", {"object": skirt, "boundaryOnly": True, "depthUU": depth})
    soi = _call("object_info", {"object": skirt}).get("object") or {}
    z_size_after = (soi.get("boundsLocalSizeBU") or [0, 0, 0])[2] if r.get("ok") else None
    # depthUU is in UNREAL units; boundsLocalSizeBU is in BLENDER units - the addon's own
    # unrealUnitsPerBlenderUnit (100.0, from ping/scene_info) is the conversion, same constant
    # server.py's fidelity gate now folds object scale through rather than assuming.
    expected_growth_bu = depth / 100.0
    if r.get("ok") and z_size_after is not None and \
            abs((z_size_after - z_size_before) - expected_growth_bu) < 1e-3:
        _ok("extrude_skirt", "object_info independently confirms Z size grew by %.4f BU (depthUU %.1f / 100)"
            % (z_size_after - z_size_before, depth))
    elif not r.get("ok"):
        _refused(findings, "extrude_skirt", r, " (fixture: a %s plane)" % skirt)
    else:
        _fail(findings, "extrude_skirt", "op:%r Z size before/after %r/%r (want +%.4f BU)"
              % (r.get("ok"), z_size_before, z_size_after, expected_growth_bu))
    _call("delete_object", {"object": skirt})
    oi = _call("object_info", {"object": obj}).get("object") or {}

    # ---- bevel_edges ------------------------------------------------------------------------
    edges_before = oi.get("edges")
    # allEdges, not boundaryOnly: the Sphere is closed, so boundaryOnly selects nothing and the op
    # refuses. allEdges selects all 792 and is what "bevel this whole mesh" means.
    r = _call("bevel_edges", {"object": obj, "allEdges": True, "offsetUU": 2.0, "segments": 2})
    oi = _call("object_info", {"object": obj}).get("object") or {}
    edges_after = oi.get("edges") if r.get("ok") else None
    # A bevel with segments>1 always ADDS edges - it is topologically incapable of a no-op on a
    # real selection. Equal or fewer edges after means it did not actually run against this mesh.
    if r.get("ok") and edges_after is not None and edges_after > edges_before:
        _ok("bevel_edges", "object_info independently confirms edge count grew %d -> %d"
            % (edges_before, edges_after))
    elif not r.get("ok"):
        _refused(findings, "bevel_edges", r)
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
    # ---- transform_object ----------------------------------------------------------------------
    # locationBU is the object's own transform read back off the datablock, not an echo of what was
    # sent - object_info cannot know what the caller asked for.
    want_loc = [1.0, 2.0, 3.0]
    r = _call("transform_object", {"object": obj, "location": want_loc})
    oi = _call("object_info", {"object": obj}).get("object") or {}
    got = oi.get("locationBU")
    if r.get("ok") and got and all(abs(a - b) < 1e-6 for a, b in zip(got, want_loc)):
        _ok("transform_object", "object_info independently confirms locationBU == %r" % want_loc)
    elif not r.get("ok"):
        _refused(findings, "transform_object", r)
    else:
        _fail(findings, "transform_object", "object_info.locationBU:%r (want %r)"
              % (got, want_loc))

    # ---- add_modifier --------------------------------------------------------------------------
    # THE MODIFIER'S NAME IS `modifier`, NOT `name` - `name` aliases the OBJECT here. Asking with
    # the wrong one is what surfaced take()'s silent alias drop on 2026-09-04; getting it right is
    # what makes this a postcondition rather than a second copy of that bug.
    mod_name = "MifAuditSub"
    r = _call("add_modifier", {"object": obj, "type": "SUBSURF", "modifier": mod_name})
    lm = _call("list_modifiers", {"object": obj})
    stack = [m.get("name") for m in (lm.get("modifiers") or [])]
    if r.get("ok") and mod_name in stack:
        _ok("add_modifier", "list_modifiers independently confirms '%s' is on the stack %r"
            % (mod_name, stack))
    elif not r.get("ok"):
        _refused(findings, "add_modifier", r)
    else:
        _fail(findings, "add_modifier", "list_modifiers stack:%r (want %r present)"
              % (stack, mod_name))

    # ---- add_shape_key -------------------------------------------------------------------------
    key_name = "MifAuditKey"
    r = _call("add_shape_key", {"object": obj, "name": key_name})
    sk = _call("list_shape_keys", {"object": obj})
    keys = [k.get("name") for k in (sk.get("shapeKeys") or [])]
    if r.get("ok") and key_name in keys:
        _ok("add_shape_key", "list_shape_keys independently confirms '%s' among %r"
            % (key_name, keys))
    elif not r.get("ok"):
        _refused(findings, "add_shape_key", r)
    else:
        _fail(findings, "add_shape_key", "list_shape_keys:%r (want %r present)"
              % (keys, key_name))

    # ---- rename_object -------------------------------------------------------------------------
    # PROVED BY THE NEXT OP, not only by a read. delete_object below is given the NEW name, so a
    # rename that did not take fails there too - a read answering once is a weaker statement than
    # the rest of the pipeline being able to find the object again.
    new_name = "MifAuditRenamed"
    r = _call("rename_object", {"object": obj, "newName": new_name})
    si = _call("scene_info", {})
    names = [o["name"] for o in (si.get("objects") or [])]
    if r.get("ok") and new_name in names and obj not in names:
        _ok("rename_object", "scene_info independently confirms '%s' -> '%s' and the old name is "
                             "gone" % (obj, new_name))
        obj = new_name
    elif not r.get("ok"):
        _refused(findings, "rename_object", r)
    else:
        _fail(findings, "rename_object", "scene_info.objects:%r (want %r present, %r absent)"
              % (names, new_name, obj))

    r = _call("delete_object", {"object": obj})
    si = _call("scene_info", {})
    names = [o["name"] for o in (si.get("objects") or [])]
    if r.get("ok") and obj not in names:
        _ok("delete_object", "scene_info independently confirms '%s' is gone" % obj)
    else:
        _fail(findings, "delete_object", "op:%r '%s' still in scene_info.objects:%r"
              % (r.get("ok"), obj, names))

    print("")
    checked, surface, sized = _write_surface()
    if findings:
        # DERIVED. Both this line and the pass line below carried a literal 7, so an eighth check
        # would have left both of them lying - the same stale denominator read_purity's
        # "exercised: %d/5" carried until this morning.
        print("%d of %d postconditions FAILED - the op's own ok:true was not backed by reality:"
              % (len(findings), checked + len(findings)))
        for step, detail in findings:
            print("  %-16s %s" % (step, detail))
        return 1
    print("OK  %d write op(s) independently re-verified via a separate call: %s"
          % (checked, ", ".join(VERIFIED)))
    if sized:
        # REACH, NOT GREEN. This line is the point of the change: "all 7" read as the whole write
        # surface and is a fourteenth of it. Every op not named above is UNJUDGED here, which is a
        # different thing from clean.
        print("REACH - roughly %d op(s) in this addon write something; %d are checked here (%d%%)."
              % (surface, checked, round(100.0 * checked / surface) if surface else 0))
        print("        The %d is a name-shaped estimate, not an authoritative list - the addon has"
              % surface)
        print("        no such list. Everything not named above is UNJUDGED, not clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
