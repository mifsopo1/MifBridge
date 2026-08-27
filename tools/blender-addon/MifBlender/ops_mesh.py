"""MifBlender ops: FBX import/export and bmesh edge bevelling.

=============================================================================
FBX AXIS + SCALE -- THE ONE THING THAT MUST NOT BE GUESSED
=============================================================================
Unreal writes, and Unreal reads back, this axis system:

    Up    = Z, sign +1
    Front = -eParityOdd, i.e. axis Y, sign -1
    Coord = X, sign +1   (right-handed)
    System unit = centimetres

  VERIFIED in engine source, D:/UE532:
    Engine/Source/Editor/UnrealEd/Private/Fbx/FbxMainExport.cpp:268-276
        FbxAxisSystem UnrealZUp(eZAxis, -eParityOdd, eRightHanded);
        Scene->GetGlobalSettings().SetAxisSystem(UnrealZUp);
        Scene->GetGlobalSettings().SetSystemUnit(FbxSystemUnit::cm);
    Engine/Source/Editor/UnrealEd/Private/Fbx/FbxMainImport.cpp:1500-1515
        builds the IDENTICAL axis system and only calls ConvertScene()
        `if (SourceSetup != UnrealImportAxis)`. Match it and nothing rotates.
    FbxMainImport.cpp:1540-1542 -- unit conversion only runs if the file's
        system unit is not already cm.

Blender's operator DEFAULTS DO NOT MATCH. Defaults are axis_up='Y',
axis_forward='-Z' (the Maya Y-up convention). Exporting with the defaults writes
UpAxis=1 and UE rotates the mesh on import.

  VERIFIED empirically on Blender 4.4.0 by exporting the same object twice and
  parsing GlobalSettings straight out of the binary FBX:

    axis_up='Z', axis_forward='Y'  ->  UpAxis 2 (+1)  FrontAxis 1 (-1)  CoordAxis 0 (+1)
    operator defaults              ->  UpAxis 1 (+1)  FrontAxis 2 (+1)  CoordAxis 0 (+1)

  The first row is bit-for-bit UE's system. ('Z','Y') is also the row Blender's
  own table marks `# Blender system!`
  (4.4/scripts/addons_core/io_scene_fbx/fbx_utils.py:126), so this is the
  identity mapping in both directions -- not a correction, an absence of one.

SCALE. With apply_unit_scale=True and apply_scale_options='FBX_SCALE_NONE'
(both defaults, both pinned here anyway) Blender bakes x100 into the geometry
and writes UnitScaleFactor=1.0, i.e. centimetre magnitudes in a centimetre
file. VERIFIED by reading UnitScaleFactor=1.0 out of the exported binary while
a 10.0 Blender-unit object measured 1000 in the file. So:

        1 Blender unit == 100 Unreal units.   A 1000 uu road is 10.0 BU.

IMPORT takes NO axis arguments on purpose. use_manual_orientation defaults to
False, which makes the importer read FrontAxis/UpAxis/CoordAxis out of the file
and reverse-map them (io_scene_fbx/import_fbx.py:3136-3145). Passing axis args
would only be able to make it wrong.

  VERIFIED 2026-08-27, three of the four legs, on Blender 4.4.0 AND 5.0.1:

      UE export_asset  ->  PH_HumanGizmoSitLowPoly.fbx, 164,880 bytes
      import_mesh      ->  802 vertices
      extrude_skirt    ->  995 vertices  (boundaryOnly, depth 2.0)
      export_mesh      ->  91,420 bytes on 4.4 / 91,484 on 5.0

  Both engines produce the SAME vertex counts, so the geometry path is not
  version-sensitive between 4.4 and 5.0.

  STILL NOT VERIFIED: the last leg, FBX back INTO Unreal. import_asset persists
  to disk, so the safety gate refuses it in scratch mode - correctly, and it is
  not something to work around. Until someone runs that leg in full mode, the
  axis/scale claims above rest on the byte-level header read plus three of four
  legs, not on a mesh having made the whole loop and come back the right way up.

  NOT VERIFIED: mesh_smooth_type. 'FACE' writes smoothing groups AND normals,
  which is strictly more information than the 'OFF' default -- but which of the
  two Unreal actually consumes depends on the static-mesh import options
  ("Normal Import Method"), and I did not confirm that. If normals come back
  wrong, this is the first knob to turn.
=============================================================================
"""

from __future__ import annotations

import math
import os

import bmesh
import bpy

from .ops_common import (
    MifOpError, axis_index, get_object, mesh_counts, object_info, reject_unknown, rnd,
    select_only, selection_restore, selection_snapshot, take, take_bool,
    take_float, take_int, UU_PER_BU,
)

# Pinned, not defaulted. Anything that changes the geometry, the axes or the
# units of the written file lives in this dict so there is exactly one place to
# audit. axis_up / axis_forward are the two that are NOT operator defaults.
FBX_EXPORT_ARGS = {
    "axis_up": "Z",                          # default 'Y'  -- MUST override
    "axis_forward": "Y",                     # default '-Z' -- MUST override
    "apply_unit_scale": True,
    "apply_scale_options": "FBX_SCALE_NONE",
    "global_scale": 1.0,
    "use_space_transform": True,
    "bake_space_transform": False,           # flagged experimental upstream; leave OFF
    "object_types": {"MESH"},                # default drags in EMPTY/CAMERA/LIGHT/ARMATURE
    "use_mesh_modifiers": True,
    "mesh_smooth_type": "FACE",              # see the NOT VERIFIED note above
    "use_tspace": False,
    "use_triangles": False,
    "colors_type": "SRGB",
    "use_custom_props": False,
    "bake_anim": False,                      # a static mesh has no animation to write
    "path_mode": "AUTO",
}

FBX_IMPORT_ARGS = {
    "use_custom_normals": True,
    "use_anim": False,
    "use_image_search": False,               # do not crawl the filesystem for textures
    # NO axis args, NO global_scale. The importer reads them from the file.
}

_SUPPORTED = (".fbx",)


def _check_format(path, verb):
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SUPPORTED:
        raise MifOpError(
            "%s: only FBX is supported (got '%s'). FBX is the only format whose axis "
            "and unit round-trip with Unreal is verified -- OBJ in particular is not "
            "(UE's OBJ exporter swaps Y/Z, de-indexes to 3 verts per triangle and "
            "writes no normals). Use run_python if you need another format and accept "
            "that the orientation is on you." % (verb, ext or "<no extension>"))
    return ext


def _resolve_out_path(path):
    path = bpy.path.abspath(str(path))
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise MifOpError("cannot create output directory %s: %s" % (parent, exc))
    return path


# ---------------------------------------------------------------------------
# import_mesh
# ---------------------------------------------------------------------------

def op_import_mesh(params):
    reject_unknown(params, {"file", "filepath", "path", "clearScene", "clear_scene",
                            "useCustomNormals", "rename"}, "import_mesh")
    raw = take(params, "file", "filepath", "path", required=True, kind=str)
    path = os.path.abspath(bpy.path.abspath(raw))
    _check_format(path, "import_mesh")
    if not os.path.isfile(path):
        raise MifOpError("no such file: %s" % path)
    size = os.path.getsize(path)
    if size <= 0:
        raise MifOpError("file %s is empty (0 bytes) -- nothing to import" % path)

    if take_bool(params, "clearScene", "clear_scene", default=False):
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    args = dict(FBX_IMPORT_ARGS)
    if "useCustomNormals" in params:
        args["use_custom_normals"] = take_bool(params, "useCustomNormals", default=True)

    # The import operators return {'FINISHED'}, never the objects they made, so
    # capture by set difference. (bpy.ops.import_scene.fbx has no other handle.)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path, **args)
    bpy.context.view_layer.update()
    created = [o for o in bpy.data.objects if o not in before]

    if not created:
        raise MifOpError(
            "import produced NO objects from %s (%d bytes). The file parsed but held "
            "nothing importable -- check it is a mesh FBX and not, say, an animation-only "
            "or camera-only export." % (path, size))

    rename = take(params, "rename")
    if rename:
        if len(created) != 1:
            raise MifOpError("'rename' needs exactly one imported object, got %d: %s"
                             % (len(created), ", ".join(o.name for o in created)))
        created[0].name = str(rename)

    warnings = []
    non_identity = [o.name for o in created if not object_info(o)["isIdentityTransform"]]
    if non_identity:
        warnings.append(
            "imported object(s) %s do NOT have an identity transform. Do not call "
            "transform_apply to 'fix' it -- that bakes the round trip into the mesh and "
            "shears anything that tiles. Leave the transform alone."
            % ", ".join(non_identity))
    if len(created) > 1:
        warnings.append(
            "%d objects were imported. If you expected one mesh, the source export "
            "probably included LODs or collision -- re-export with levelOfDetail:false "
            "and collision:false." % len(created))

    return {
        "file": path,
        "fileSizeBytes": size,
        "importedCount": len(created),
        "imported": [object_info(o) for o in created],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# export_mesh
# ---------------------------------------------------------------------------

_EXPORT_OVERRIDES = {
    "meshSmoothType": ("mesh_smooth_type", str),
    "useTriangles": ("use_triangles", bool),
    "useMeshModifiers": ("use_mesh_modifiers", bool),
    "useTspace": ("use_tspace", bool),
}


def op_export_mesh(params):
    reject_unknown(params, set(_EXPORT_OVERRIDES) | {
        "object", "name", "objects", "file", "filepath", "path",
        "overwrite", "replaceExisting"}, "export_mesh")

    raw = take(params, "file", "filepath", "path", required=True, kind=str)
    path = _resolve_out_path(raw)
    _check_format(path, "export_mesh")

    if os.path.exists(path) and not take_bool(params, "overwrite", "replaceExisting",
                                              default=True):
        raise MifOpError("%s already exists and overwrite:false. Pass overwrite:true or "
                         "a different file." % path)

    names = take(params, "objects")
    if names is None:
        single = take(params, "object", "name")
        names = [single] if single else None
    if names is not None and not isinstance(names, list):
        raise MifOpError("'objects' must be a list of object names")

    if names:
        targets = [get_object(n, want_mesh=True) for n in names]
    else:
        targets = [o for o in bpy.context.view_layer.objects if o.type == "MESH"]
        if not targets:
            raise MifOpError("no mesh objects in the view layer to export. Pass 'object' "
                             "explicitly, or import something first.")

    args = dict(FBX_EXPORT_ARGS)
    for key, (arg, kind) in _EXPORT_OVERRIDES.items():
        if key in params:
            value = params[key]
            if kind is bool:
                value = take_bool(params, key)
            args[arg] = value

    snapshot = selection_snapshot()
    try:
        select_only(targets)
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True, **args)
    finally:
        selection_restore(snapshot)

    # ------------------------- VERIFY AFTER WRITE -------------------------
    # The operator reports {'FINISHED'} on paths that produce nothing useful, so
    # re-stat the file rather than trusting the return. An op that answered
    # ok:true over a missing or 0-byte FBX is the exact bug this guards.
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else -1
    if not exists or size <= 0:
        raise MifOpError(
            "export wrote no usable file: %s (exists=%s, bytes=%d). Check Blender's "
            "console for the exporter's own error, and that the path is writable."
            % (path, exists, size))

    return {
        "file": path,
        "fileExists": True,
        "fileSizeBytes": size,
        "exportedCount": len(targets),
        "exported": [object_info(o) for o in targets],
        "axis": {"up": "Z", "front": "-Y", "handedness": "right", "unit": "cm",
                 "unrealUnitsPerBlenderUnit": UU_PER_BU},
        "fbxArgs": {k: (sorted(v) if isinstance(v, set) else v)
                    for k, v in args.items()},
    }


# ---------------------------------------------------------------------------
# bevel_edges
# ---------------------------------------------------------------------------

_BEVEL_KEYS = {
    "object", "name",
    # selection
    "minAngleDeg", "maxAngleDeg", "axis", "side", "tolerance",
    "boundaryOnly", "edgeIndices", "allEdges",
    # geometry
    "offset", "offsetUU", "offsetType", "segments", "profile", "clampOverlap",
    "loopSlide", "hardenNormals", "miterOuter", "miterInner", "spread",
    # safety
    "preserveAxes", "preserveTolerance", "assertAxes", "assertTolerance",
    "seamTolerance", "seamBand", "dryRun",
}


def _select_edges(bm, obj, params, op_name="this op"):
    """Build the edge set. Every supplied criterion is ANDed.

    Two selectors matter for real work:
      * by angle   -- minAngleDeg/maxAngleDeg, the angle between the two faces
                      an edge joins (0 = coplanar, 90 = a box corner). This is
                      the "chamfer everything sharper than X" selector.
      * by extreme -- axis + side, i.e. edges lying in the object's min or max
                      plane along an axis. This is the "min/max Z" selector.
    """
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    explicit = params.get("edgeIndices")
    if explicit is not None:
        if not isinstance(explicit, list):
            raise MifOpError("'edgeIndices' must be a list of integers")
        count = len(bm.edges)
        bad = [i for i in explicit if not isinstance(i, int) or i < 0 or i >= count]
        if bad:
            raise MifOpError("edgeIndices out of range for '%s' (%d edges): %s"
                             % (obj.name, count, bad[:10]))
        return [bm.edges[i] for i in explicit], {"edgeIndices": len(explicit)}

    min_angle = take_float(params, "minAngleDeg")
    max_angle = take_float(params, "maxAngleDeg")
    axis_name = take(params, "axis")
    boundary_only = take_bool(params, "boundaryOnly", default=False)
    all_edges = take_bool(params, "allEdges", default=False)

    if not any((min_angle is not None, max_angle is not None, axis_name,
                boundary_only, all_edges)):
        # NAMES THE OP THE CALLER ACTUALLY INVOKED. This helper is shared by THREE ops -
        # select_edges, bevel_edges and extrude_skirt - and the message hardcoded "bevel_edges"
        # for all of them. Calling extrude_skirt without a selector sent you to investigate an
        # op you had never called, which is a wrong answer wearing the clothes of a helpful one.
        # Found by driving a real UE-exported FBX through the round trip on 4.4 and 5.0.
        raise MifOpError(
            "%s needs a selector, and refuses to guess. Use minAngleDeg / "
            "maxAngleDeg (angle between the two faces an edge joins), or axis + side "
            "(e.g. axis:'Z', side:'max' for the top edges), or boundaryOnly:true, or "
            "edgeIndices:[...], or allEdges:true to really mean every edge."
            % op_name)

    criteria = {}
    candidates = list(bm.edges)

    if boundary_only:
        candidates = [e for e in candidates if len(e.link_faces) == 1]
        criteria["boundaryOnly"] = len(candidates)

    if min_angle is not None or max_angle is not None:
        lo = math.radians(min_angle) if min_angle is not None else -1.0
        hi = math.radians(max_angle) if max_angle is not None else math.pi + 1.0
        kept = []
        for edge in candidates:
            angle = edge.calc_face_angle(None)  # None for wire/boundary/non-manifold
            if angle is None:
                continue
            if lo <= angle <= hi:
                kept.append(edge)
        candidates = kept
        criteria["angle"] = {"minDeg": min_angle, "maxDeg": max_angle,
                             "matched": len(candidates)}

    if axis_name:
        idx = axis_index(axis_name)
        side = str(take(params, "side", default="both")).lower()
        if side not in ("min", "max", "both"):
            raise MifOpError("'side' must be 'min', 'max' or 'both' (got %r)" % side)
        tol = take_float(params, "tolerance", default=1e-4)
        coords = [v.co[idx] for v in bm.verts]
        lo_val, hi_val = min(coords), max(coords)

        def at_extreme(edge):
            a, b = edge.verts[0].co[idx], edge.verts[1].co[idx]
            if side in ("min", "both") and abs(a - lo_val) <= tol and abs(b - lo_val) <= tol:
                return True
            if side in ("max", "both") and abs(a - hi_val) <= tol and abs(b - hi_val) <= tol:
                return True
            return False

        candidates = [e for e in candidates if at_extreme(e)]
        criteria["extreme"] = {"axis": axis_name.upper(), "side": side,
                               "toleranceBU": tol,
                               "minBU": round(lo_val, 6), "maxBU": round(hi_val, 6),
                               "matched": len(candidates)}

    if all_edges and not criteria:
        criteria["allEdges"] = len(candidates)

    return candidates, criteria


def _axis_list(params, key):
    """Accept ["X","Z"] or the forgiving "X" / "xz" spellings."""
    raw = take(params, key, default=[]) or []
    if isinstance(raw, str):
        raw = list(raw) if len(raw) > 1 else [raw]
    if not isinstance(raw, list):
        raise MifOpError("'%s' must be a list of axis names, e.g. [\"X\"]" % key)
    return [axis_index(a, key) for a in raw]


def _axis_sizes(bm):
    if not bm.verts:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    lo = [min(v.co[i] for v in bm.verts) for i in range(3)]
    hi = [max(v.co[i] for v in bm.verts) for i in range(3)]
    return tuple(lo), tuple(hi), tuple(hi[i] - lo[i] for i in range(3))


# ---------------------------------------------------------------------------
# SEAM PLANARITY -- the measurement a bounding-box extent cannot make
# ---------------------------------------------------------------------------
#
# WHY THE EXTENT CHECK IS NOT ENOUGH. sizeBeforeBU/sizeAfterBU and assertAxes are
# built on _axis_sizes, which is a bounding box. A bounding box is pinned by its
# EXTREMES, so as long as ONE vertex survives on each seam plane the reported
# size is bit-identical no matter what the rest of the end cap did.
#
#   MEASURED on Blender 4.4.0 headless. A 1000 x 300 x 50 uu tile (10 x 3 x 0.5
#   BU), bevel of the Y-extreme edge loops, offset 15 uu, guards OFF:
#
#     verts 8 -> 32     sizeDeltaUU [0.0, 0.0, 0.0]      <- extent says CLEAN
#     verts inside the near-but-off band on X:  0 -> 24   <- 24 verts 15 uu in
#
#   assertAxes:["X"] passes that. The tile ships, and every instance along the
#   spline shears at the join. With preserveAxes:["X"] the same run snaps 24
#   verts back and the band count returns to 0 -- so the guard WORKS, it was
#   just never measuring the thing that fails.
#
# WHAT IS MEASURED, per axis, and reported unconditionally:
#
#   bandVerts        verts lying strictly INSIDE (lo, lo+band) or (hi-band, hi):
#                    off the seam plane, but close enough that the preserve snap
#                    would have caught them. An increase IS the shear. This is
#                    the primary detector.
#   movedOffSeam     vertices that were ON the plane before and, having SURVIVED
#                    the op, are now further than `band` from it. Identity, by
#                    BMVert reference. Catches a survivor that drifted clean past
#                    the band, which the band cannot see.
#   seamVertsRemoved tracked seam verts the op destroyed (BMVert.is_valid False).
#                    Reported, never failed on: MEASURED, the bevel above removes
#                    all 8 originals and rebuilds 8 in the same places, which is
#                    correct behaviour. It is here so `movedOffSeam: 0` is never
#                    read as "all the originals are fine" when they are gone.
#   onSeamBefore/After  population of the plane, for reading the other three.
#
# BAND WIDTH. The band must be as wide as the lateral distance the op can drag a
# seam vertex, which is the same number preserveTolerance defaults to: a bevel
# pulls the seam in by its offset, so the band is the offset; a skirt moves
# nothing in X or Y at all, so it is an epsilon. Both are overridable with
# `seamBand`. A band NARROWER than the snap tolerance is the blind spot again --
# it would pass verts the snap itself considers on-seam.


def _seam_snapshot(bm, lo, hi, axes, on_tol, band):
    """Record, BY IDENTITY, which verts sit on each seam plane, plus the band count.

    Called before the edit. `axes` is a list of axis indices; pass all three so
    the report exists whether or not the axis is guarded.
    """
    watch = {}
    for idx in axes:
        rows = []
        for vert in bm.verts:
            coord = vert.co[idx]
            if abs(coord - lo[idx]) <= on_tol:
                rows.append((vert, lo[idx]))
            elif abs(coord - hi[idx]) <= on_tol:
                rows.append((vert, hi[idx]))
        watch[idx] = {"rows": rows, "band": _band_count(bm, lo, hi, idx, band)}
    return watch


def _band_count(bm, lo, hi, idx, band):
    """Verts strictly inside the near-but-off band on either seam plane."""
    return sum(1 for vert in bm.verts
               if lo[idx] < vert.co[idx] < lo[idx] + band
               or hi[idx] - band < vert.co[idx] < hi[idx])


def _seam_verdict(watch, bm, lo, hi, on_tol, band):
    """Re-measure after the edit. Returns {axisLetter: {...}} -- pure measurement,
    no policy: which axes are allowed to fail is _seam_violations' business."""
    report = {}
    for idx, before in watch.items():
        moved = removed = 0
        worst = 0.0
        for vert, plane in before["rows"]:
            if not vert.is_valid:
                removed += 1
                continue
            drift = abs(vert.co[idx] - plane)
            if drift > band:
                moved += 1
                worst = max(worst, drift)
        on_after = sum(1 for vert in bm.verts
                       if abs(vert.co[idx] - lo[idx]) <= on_tol
                       or abs(vert.co[idx] - hi[idx]) <= on_tol)
        report["XYZ"[idx]] = {
            "onSeamBefore": len(before["rows"]),
            "onSeamAfter": on_after,
            "bandVertsBefore": before["band"],
            "bandVertsAfter": _band_count(bm, lo, hi, idx, band),
            "movedOffSeam": moved,
            "seamVertsRemoved": removed,
            "maxSeamDriftBU": round(worst, 8),
            "maxSeamDriftUU": round(worst * UU_PER_BU, 6),
        }
    return report


def _seam_violations(report, guarded_axes):
    """The subset of `report` that is a hard failure on a GUARDED axis.

    Two triggers, both measured rather than inferred:
      bandVertsAfter > bandVertsBefore -- verts now sit off the plane but inside
        the snap band. This is the 24-of-32 case above.
      movedOffSeam > 0 -- a vert that was on the plane survived and left it by
        more than the band, which is too far for the band to see.
    """
    bad = []
    for letter in guarded_axes:
        row = report.get(letter)
        if row is None:
            continue
        if row["bandVertsAfter"] > row["bandVertsBefore"]:
            bad.append("%s: %d vert(s) now sit off the seam plane but inside the snap "
                       "band (was %d)" % (letter, row["bandVertsAfter"],
                                          row["bandVertsBefore"]))
        if row["movedOffSeam"]:
            bad.append("%s: %d vert(s) that were ON the seam plane moved off it (max "
                       "%.4f uu)" % (letter, row["movedOffSeam"], row["maxSeamDriftUU"]))
    return bad


def _off_seam_verts(report):
    """The single flat number per axis, reported the way sizeDeltaBU is."""
    return {letter: row["bandVertsAfter"] for letter, row in report.items()}


def _seam_warnings(report, guarded, criteria, band):
    """Planarity warnings for axes that are NOT guarded. Deliberately quiet.

    Two suppressions, because a warning nobody reads is worse than no warning:

      * the SELECTOR's own axis. `axis:'Y'` means "bevel the Y extremes", so
        taking verts off the Y seam plane is the request, not a surprise.
      * every unguarded axis, once ANY axis is guarded. Passing assertAxes:['X']
        is the caller stating which axis tiles; warning about Y and Z after that
        is guessing on their behalf. MEASURED: without this, the ordinary
        preserve_x bevel of a closed tile emits two warnings that are true,
        irrelevant and identical every single time.

    Nothing is hidden by either: seamPlanarity and offSeamVerts carry the raw
    per-axis numbers on every response, guarded or not.
    """
    if guarded:
        return []
    sel_axis = (criteria.get("extreme") or {}).get("axis")
    out = []
    for letter in ("X", "Y", "Z"):
        row = report.get(letter)
        if row is None or letter == sel_axis:
            continue
        if row["bandVertsAfter"] > row["bandVertsBefore"]:
            out.append(
                "seam planarity on %s degraded: %d -> %d vert(s) now sit off the %s "
                "min/max plane but within %.4f uu of it, while the SIZE along %s is "
                "UNCHANGED. No extent check can ever see this, and a tile like it shears "
                "at every join. No axis was guarded on this call -- add %s to assertAxes "
                "(preserveAxes follows it automatically and snaps such verts back)."
                % (letter, row["bandVertsBefore"], row["bandVertsAfter"], letter,
                   band * UU_PER_BU, letter, letter))
    return out


def _guard_axes(params, default_band):
    """assertAxes / preserveAxes / the seam tolerances, resolved TOGETHER.

    preserveAxes DEFAULTS TO assertAxes when it is not supplied. Asserting an
    axis without preserving it is not a configuration, it is a guaranteed
    failure: the assert measures exactly the drift the preserve exists to
    remove. Passing preserveAxes:[] explicitly still means "none" -- the default
    only applies when the key is absent.
    """
    assert_axes = _axis_list(params, "assertAxes")
    if "preserveAxes" in params and params["preserveAxes"] is not None:
        preserve = _axis_list(params, "preserveAxes")
    else:
        preserve = list(assert_axes)
    snap_tol = take_float(params, "preserveTolerance", default=default_band)
    on_tol = take_float(params, "seamTolerance", default=1e-4)
    band = take_float(params, "seamBand", default=max(snap_tol, on_tol))
    return assert_axes, preserve, snap_tol, on_tol, band


# ---------------------------------------------------------------------------
# select_edges  -- the selector, resolved and REPORTED, nothing written
# ---------------------------------------------------------------------------

_SELECT_KEYS = {
    "object", "name",
    # selection -- byte-identical grammar to bevel_edges / extrude_skirt
    "minAngleDeg", "maxAngleDeg", "axis", "side", "tolerance",
    "boundaryOnly", "edgeIndices", "allEdges",
    "maxReported",
}


def op_select_edges(params):
    """Resolve an edge selector against a mesh and report what it matches.

    READ-ONLY: the bmesh is never written back. This runs the SAME _select_edges
    the two editing ops run -- not a reimplementation -- so what it reports is
    exactly what bevel_edges / extrude_skirt would act on. Run it first: a
    selector that matches nothing is the commonest reason an edit "did nothing".
    """
    reject_unknown(params, _SELECT_KEYS, "select_edges")
    obj = get_object(take(params, "object", "name", required=True, kind=str),
                     want_mesh=True)
    limit = take_int(params, "maxReported", default=512)
    if limit < 0:
        raise MifOpError("'maxReported' must be >= 0 (it caps how many edge indices "
                         "come back; the count is always exact)")

    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        edges, criteria = _select_edges(bm, obj, params, "select_edges")
        lo0, hi0, size0 = _axis_sizes(bm)

        boundary = sum(1 for e in edges if len(e.link_faces) == 1)
        wire = sum(1 for e in edges if len(e.link_faces) == 0)
        interior = len(edges) - boundary - wire
        indices = [e.index for e in edges]
        total = len(bm.edges)

        warnings = []
        if not edges:
            warnings.append(
                "the selector matched 0 of %d edges on '%s'. bevel_edges and "
                "extrude_skirt both REFUSE on an empty selection rather than "
                "no-op, so fix the selector here before calling either."
                % (total, obj.name))
        if interior or wire:
            warnings.append(
                "%d interior and %d wire edge(s) are in this selection. "
                "extrude_skirt refuses those (extruding an interior edge splits "
                "the mesh instead of adding a skirt) -- add boundaryOnly:true."
                % (interior, wire))

        return {
            "object": obj.name,
            "count": len(edges),
            "totalEdges": total,
            "boundaryEdges": boundary,
            "interiorEdges": interior,
            "wireEdges": wire,
            "edgeIndices": indices[:limit],
            "edgeIndicesTruncated": len(indices) > limit,
            "criteria": criteria,
            "boundsLocalMinBU": rnd(lo0),
            "boundsLocalMaxBU": rnd(hi0),
            "boundsLocalSizeBU": rnd(size0),
            "boundsLocalSizeUU": rnd([v * UU_PER_BU for v in size0], 4),
            "warnings": warnings,
        }
    finally:
        bm.free()


def op_bevel_edges(params):
    """Chamfer or round a selected set of edges, with a tiling guard.

    segments=1 gives a flat chamfer; segments>1 gives a rounded fillet.

    THE TILING GUARD. A bevel moves the endpoints of every edge it touches, so
    bevelling the long side edges of a tiling mesh also pulls its end-cap verts
    inward and the tile no longer butts up against the next one. Three knobs:

      preserveAxes: ["X"]  -- after the bevel, snap any vert that ended up near
                              the ORIGINAL min/max along X back onto it.
                              DEFAULTS TO assertAxes when it is not supplied.
      assertAxes:   ["X"]  -- after that, fail and throw the whole edit away if
                              either (a) the SIZE along X moved by more than
                              assertTolerance, or (b) the seam PLANARITY on X
                              broke. Nothing is written.
      seamBand             -- how wide the near-but-off band is. Defaults to the
                              preserve snap tolerance, which for a bevel is the
                              offset: that is exactly how far a bevel can drag a
                              seam vertex.

    (b) is not a refinement of (a), it is the check that (a) cannot make. The
    size along X is a bounding-box EXTENT and one surviving corner vertex pins
    it -- MEASURED, a guards-off bevel on a 1000 uu tile put 24 verts 15 uu
    inside the X seam and still reported sizeDeltaUU [0,0,0]. See the SEAM
    PLANARITY block above for the numbers.

    assertAxes defaults to empty, i.e. off. The response ALWAYS carries
    sizeBeforeBU / sizeAfterBU / sizeDeltaBU AND offSeamVerts / seamPlanarity for
    all three axes, so neither kind of drift can pass unnoticed even with the
    guards off -- off means "do not fail", never "do not look".
    """
    reject_unknown(params, _BEVEL_KEYS, "bevel_edges")
    obj = get_object(take(params, "object", "name", required=True, kind=str),
                     want_mesh=True)

    offset = take_float(params, "offset")
    offset_uu = take_float(params, "offsetUU")
    if (offset is None) == (offset_uu is None):
        raise MifOpError("pass exactly one of 'offset' (Blender units) or 'offsetUU' "
                         "(Unreal units; 1 BU = %g uu)" % UU_PER_BU)
    if offset is None:
        offset = offset_uu / UU_PER_BU
    if offset <= 0.0:
        raise MifOpError("offset must be > 0")

    segments = take_int(params, "segments", default=1)
    if segments < 1:
        raise MifOpError("'segments' must be >= 1 (1 = flat chamfer, >1 = rounded)")

    dry_run = take_bool(params, "dryRun", default=False)

    mesh = obj.data
    warnings = []
    if mesh.has_custom_normals:
        warnings.append(
            "'%s' carries custom split normals. New bevel geometry has no custom normal "
            "of its own, so shading across the new faces may look wrong until normals "
            "are re-authored." % obj.name)

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        edges, criteria = _select_edges(bm, obj, params, "bevel_edges")
        lo0, hi0, size0 = _axis_sizes(bm)
        verts0, faces0 = len(bm.verts), len(bm.faces)

        if not edges:
            raise MifOpError(
                "the selector matched 0 edges on '%s'. Mesh local bounds are min %s "
                "max %s (Blender units); selection breakdown: %s"
                % (obj.name, rnd(lo0), rnd(hi0), criteria))

        # Resolved BEFORE the edit: preserveAxes defaults to assertAxes, and a
        # bad axis name should refuse without having touched the mesh.
        assert_axes, preserve, snap_tol, seam_on_tol, seam_band = _guard_axes(
            params, offset * 1.01 + 1e-4)
        guarded = ["XYZ"[i] for i in assert_axes]
        # All three axes are WATCHED whatever is guarded -- guarding decides what
        # fails, never what is looked at.
        watch = _seam_snapshot(bm, lo0, hi0, (0, 1, 2), seam_on_tol, seam_band)

        if dry_run:
            return {
                "dryRun": True,
                "object": obj.name,
                "selectedEdges": len(edges),
                "edgeIndices": [e.index for e in edges][:512],
                "edgeIndicesTruncated": len(edges) > 512,
                "criteria": criteria,
                "sizeBeforeBU": rnd(size0),
                "boundsLocalMinBU": rnd(lo0),
                "boundsLocalMaxBU": rnd(hi0),
                "preserveAxes": ["XYZ"[i] for i in preserve],
                "assertAxes": guarded,
                "seamToleranceBU": seam_on_tol,
                "seamBandBU": seam_band,
                "onSeamVertsBefore": {"XYZ"[i]: len(watch[i]["rows"]) for i in watch},
                "warnings": warnings,
            }

        bmesh.ops.bevel(
            bm,
            geom=edges,
            offset=offset,
            offset_type=str(take(params, "offsetType", default="OFFSET")).upper(),
            segments=segments,
            profile=take_float(params, "profile", default=0.5),
            # affect defaults to 'VERTICES' and material defaults to 0. Both are
            # wrong here: we bevel EDGES, and material=0 would drag every new
            # face into the first material slot instead of inheriting.
            affect="EDGES",
            material=-1,
            clamp_overlap=take_bool(params, "clampOverlap", default=True),
            loop_slide=take_bool(params, "loopSlide", default=True),
            harden_normals=take_bool(params, "hardenNormals", default=False),
            miter_outer=str(take(params, "miterOuter", default="SHARP")).upper(),
            miter_inner=str(take(params, "miterInner", default="SHARP")).upper(),
            spread=take_float(params, "spread", default=0.0),
        )

        # ---- preserve: snap drifted extremes back onto the original planes ----
        # The snap tolerance must exceed the offset, or the very verts the bevel
        # pulled inward are the ones it fails to catch. seam_band is the same
        # number by default, so "in the band" == "the snap should have got it".
        snapped = 0
        for idx in preserve:
            for vert in bm.verts:
                if abs(vert.co[idx] - lo0[idx]) <= snap_tol:
                    if vert.co[idx] != lo0[idx]:
                        vert.co[idx] = lo0[idx]
                        snapped += 1
                elif abs(vert.co[idx] - hi0[idx]) <= snap_tol:
                    if vert.co[idx] != hi0[idx]:
                        vert.co[idx] = hi0[idx]
                        snapped += 1

        lo1, hi1, size1 = _axis_sizes(bm)
        delta = [size1[i] - size0[i] for i in range(3)]
        seam = _seam_verdict(watch, bm, lo0, hi0, seam_on_tol, seam_band)

        # ---- assert 1 of 2: EXTENT. Discard rather than write a sheared tile ----
        assert_tol = take_float(params, "assertTolerance", default=1e-5)
        violated = [("XYZ"[i], size0[i], size1[i]) for i in assert_axes
                    if abs(delta[i]) > assert_tol]
        if violated:
            raise MifOpError(
                "bevel changed a size that was asserted constant: %s (tolerance %g BU). "
                "The mesh was NOT modified. Anything tiling along that axis would shear. "
                "Either add the axis to preserveAxes, reduce the offset, or pick a "
                "selector that does not touch the seam."
                % ("; ".join("%s %.6f -> %.6f" % v for v in violated), assert_tol))

        # ---- assert 2 of 2: PLANARITY. The one the extent cannot see ----
        seam_bad = _seam_violations(seam, guarded)
        if seam_bad:
            raise MifOpError(
                "the bevel broke seam PLANARITY on an asserted axis: %s (band %g BU / "
                "%.4f uu). The mesh was NOT modified. The bounding box is UNCHANGED and "
                "would have passed the size assert -- one surviving corner vertex pins "
                "the extent while the rest of the end cap slides inward, which is exactly "
                "the tile that shears at every spline join. Full measurement: %s. Fix it "
                "by adding the axis to preserveAxes (it snaps such verts back), reducing "
                "the offset, or picking a selector that does not reach the seam."
                % ("; ".join(seam_bad), seam_band, seam_band * UU_PER_BU, seam))

        for i in range(3):
            if abs(delta[i]) > 1e-5 and i not in assert_axes:
                warnings.append(
                    "size along %s changed %.6f -> %.6f BU (%.4f uu). If the mesh tiles "
                    "along %s this will shear the seam -- use preserveAxes/assertAxes."
                    % ("XYZ"[i], size0[i], size1[i], delta[i] * UU_PER_BU, "XYZ"[i]))
        warnings.extend(_seam_warnings(seam, guarded, criteria, seam_band))

        bm.normal_update()
        bm.to_mesh(mesh)
        mesh.update()
        # obj.bound_box and obj.dimensions are CACHES and neither mesh.update()
        # nor obj.update_tag() refreshes them -- MEASURED on 4.4.0, see
        # ops_common.local_bounds. This is the call that does. local_bounds does
        # not depend on it (it reads the verts), but every other consumer of
        # obj.dimensions does, including list_objects and the viewport.
        bpy.context.view_layer.update()
    finally:
        bm.free()

    return {
        "object": obj.name,
        "selectedEdges": len(edges),
        "criteria": criteria,
        "offsetBU": round(offset, 8),
        "offsetUU": round(offset * UU_PER_BU, 6),
        "segments": segments,
        "vertsBefore": verts0,
        "vertsAfter": len(mesh.vertices),
        "facesBefore": faces0,
        "facesAfter": len(mesh.polygons),
        "sizeBeforeBU": rnd(size0),
        "sizeAfterBU": rnd(size1),
        "sizeDeltaBU": rnd(delta, 8),
        "sizeDeltaUU": rnd([d * UU_PER_BU for d in delta], 6),
        "boundsLocalMinBU": rnd(lo1),
        "boundsLocalMaxBU": rnd(hi1),
        "preserveAxes": ["XYZ"[i] for i in preserve],
        "assertAxes": guarded,
        "preserveSnappedVerts": snapped,
        "preserveToleranceBU": snap_tol,
        "seamToleranceBU": seam_on_tol,
        "seamBandBU": seam_band,
        # Reported unconditionally, for all three axes, exactly the way
        # sizeDeltaBU is -- an unguarded axis is still MEASURED.
        "offSeamVerts": _off_seam_verts(seam),
        "seamPlanarity": seam,
        "objectAfter": object_info(obj),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# extrude_skirt
# ---------------------------------------------------------------------------

_SKIRT_KEYS = {
    "object", "name",
    # selection -- byte-identical grammar to bevel_edges / select_edges
    "minAngleDeg", "maxAngleDeg", "axis", "side", "tolerance",
    "boundaryOnly", "edgeIndices", "allEdges",
    # geometry
    "depth", "depthUU", "direction", "flipNormals",
    # safety
    "preserveAxes", "preserveTolerance", "assertAxes", "assertTolerance",
    "seamTolerance", "seamBand", "allowNonBoundary", "dryRun",
}

# Sign applied to the extrude vector's Z. X and Y are ZERO in both cases and
# there is deliberately no knob to make them anything else -- see the docstring.
_SKIRT_DIRECTIONS = {"down": -1.0, "up": 1.0}


def op_extrude_skirt(params):
    """Extrude a boundary edge loop straight along Z, forming a skirt.

    WHY THIS EXISTS. A road/sidewalk tile with a flat cut edge hovers wherever
    the terrain falls away under it. A skirt hides that. A bevel would too, but
    a bevel moves the endpoints of every edge it touches -- including the verts
    on the end-cap seam -- and a tile whose seam has moved shears when it is
    instanced along a spline. This op moves NOTHING in X or Y:

        new geometry = a duplicate of the selected boundary loop,
                       translated by (0, 0, +/-depth).

    That is by construction, not by assertion. The extrude duplicates each
    selected edge's verts in place (bmesh.ops.extrude_edge_only) and the only
    subsequent edit is bmesh.ops.translate with a vec whose X and Y are literal
    zeros. VERIFIED on Blender 4.4.0 headless against a 10 x 3 BU grid tile:
    8 boundary edges extruded and translated -0.15 in Z gave dX 0.0, dY 0.0,
    dZ 0.15, minX/maxX unmoved, and zero verts near-but-off the X seam planes.

    NORMALS. use_normal_flip defaults False, and on that same test the new side
    faces on the +Y boundary came back with normal (0, +1, 0) -- outward, which
    is what a skirt wants when the tile's top faces +Z. Pass flipNormals:true if
    your source winding is the other way round.

    UVs. The new faces get whatever bmesh copies from the source loop, which is
    not a meaningful skirt unwrap. Expect stretched texturing until UVs are
    authored -- nothing here does that for you, and it is not a bug to report.

    SAFETY. Same guard vocabulary as bevel_edges (preserveAxes / assertAxes /
    preserveTolerance / assertTolerance / seamTolerance / seamBand) and the same
    two-part assert: EXTENT plus seam PLANARITY, because an extent check alone
    cannot see a seam that has partially slid (one surviving corner vert pins the
    bounding box -- MEASURED, see the SEAM PLANARITY block above). preserveAxes
    defaults to assertAxes. assertAxes must NOT include Z: the whole point is
    that Z grows by up to `depth`.
    """
    reject_unknown(params, _SKIRT_KEYS, "extrude_skirt")
    obj = get_object(take(params, "object", "name", required=True, kind=str),
                     want_mesh=True)

    depth = take_float(params, "depth")
    depth_uu = take_float(params, "depthUU")
    if (depth is None) == (depth_uu is None):
        raise MifOpError("pass exactly one of 'depth' (Blender units) or 'depthUU' "
                         "(Unreal units; 1 BU = %g uu)" % UU_PER_BU)
    if depth is None:
        depth = depth_uu / UU_PER_BU
    if depth <= 0.0:
        raise MifOpError("depth must be > 0. Use direction:'up' rather than a negative "
                         "depth, so the response reports what actually happened.")

    direction = str(take(params, "direction", default="down")).lower()
    if direction not in _SKIRT_DIRECTIONS:
        raise MifOpError("'direction' must be 'down' or 'up' (got %r). There is no "
                         "sideways option: moving the loop in X or Y is exactly the "
                         "seam-shearing edit this op exists to avoid." % direction)
    flip_normals = take_bool(params, "flipNormals", default=False)
    allow_non_boundary = take_bool(params, "allowNonBoundary", default=False)
    dry_run = take_bool(params, "dryRun", default=False)

    mesh = obj.data
    warnings = []
    if mesh.has_custom_normals:
        warnings.append(
            "'%s' carries custom split normals. The new skirt faces have no custom "
            "normal of their own, so shading down the skirt may look wrong until "
            "normals are re-authored." % obj.name)

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        edges, criteria = _select_edges(bm, obj, params, "extrude_skirt")
        lo0, hi0, size0 = _axis_sizes(bm)
        verts0, faces0 = len(bm.verts), len(bm.faces)

        if not edges:
            raise MifOpError(
                "the selector matched 0 edges on '%s'. Mesh local bounds are min %s "
                "max %s (Blender units); selection breakdown: %s. Run select_edges to "
                "iterate on the selector without touching the mesh."
                % (obj.name, rnd(lo0), rnd(hi0), criteria))

        non_boundary = [e for e in edges if len(e.link_faces) != 1]
        if non_boundary and not allow_non_boundary:
            raise MifOpError(
                "%d of %d selected edges on '%s' are not boundary edges (they have 0 or "
                "2+ linked faces). Extruding an interior edge does not add a skirt -- it "
                "duplicates the loop and SPLITS the mesh along it, and the seam is then "
                "invisible from outside. Add boundaryOnly:true to the selector, or pass "
                "allowNonBoundary:true if you genuinely mean to split."
                % (len(non_boundary), len(edges), obj.name))

        # Nothing here moves laterally at all, so the default snap band is an
        # epsilon rather than bevel's offset-wide one: a wide band would drag
        # genuine interior verts onto the seam and call that a fix.
        assert_axes, preserve, snap_tol, seam_on_tol, seam_band = _guard_axes(
            params, 1e-6)
        if 2 in assert_axes:
            raise MifOpError(
                "assertAxes includes Z, which this op is guaranteed to violate: a skirt "
                "extends the mesh along Z by up to depth (%.6f BU). Assert X and/or Y."
                % depth)
        guarded = ["XYZ"[i] for i in assert_axes]
        watch = _seam_snapshot(bm, lo0, hi0, (0, 1, 2), seam_on_tol, seam_band)

        if dry_run:
            return {
                "dryRun": True,
                "object": obj.name,
                "selectedEdges": len(edges),
                "boundaryEdges": len(edges) - len(non_boundary),
                "nonBoundaryEdges": len(non_boundary),
                "edgeIndices": [e.index for e in edges][:512],
                "edgeIndicesTruncated": len(edges) > 512,
                "criteria": criteria,
                "depthBU": round(depth, 8),
                "depthUU": round(depth * UU_PER_BU, 6),
                "direction": direction,
                "sizeBeforeBU": rnd(size0),
                "boundsLocalMinBU": rnd(lo0),
                "boundsLocalMaxBU": rnd(hi0),
                "preserveAxes": ["XYZ"[i] for i in preserve],
                "assertAxes": guarded,
                "seamToleranceBU": seam_on_tol,
                "seamBandBU": seam_band,
                "onSeamVertsBefore": {"XYZ"[i]: len(watch[i]["rows"]) for i in watch},
                "warnings": warnings,
            }

        # ---- the extrude: duplicate the loop IN PLACE, then move it in Z only ----
        # bmesh.ops.extrude_edge_only(bmesh, edges=[], use_normal_flip=False,
        #   use_select_history=False) -> dict(geom=[])   [signature read off
        #   bmesh.ops.extrude_edge_only.__doc__ on Blender 4.4.0]
        result = bmesh.ops.extrude_edge_only(bm, edges=edges,
                                             use_normal_flip=flip_normals)
        geom = result.get("geom") or []
        new_verts = [g for g in geom if isinstance(g, bmesh.types.BMVert)]
        new_faces = [g for g in geom if isinstance(g, bmesh.types.BMFace)]
        if not new_verts:
            raise MifOpError(
                "extrude_edge_only returned no new vertices for %d selected edges on "
                "'%s'. The mesh was NOT modified. This is not a selector problem -- the "
                "extrude itself produced nothing, which usually means the edges were "
                "already consumed by a previous op on the same bmesh."
                % (len(edges), obj.name))

        # X and Y are literal zeros. This is the whole safety argument of the op.
        bmesh.ops.translate(bm, verts=new_verts,
                            vec=(0.0, 0.0, _SKIRT_DIRECTIONS[direction] * depth))

        # ---- preserve: a belt, and it should never have anything to do ----
        snapped = 0
        for idx in preserve:
            for vert in bm.verts:
                if abs(vert.co[idx] - lo0[idx]) <= snap_tol:
                    if vert.co[idx] != lo0[idx]:
                        vert.co[idx] = lo0[idx]
                        snapped += 1
                elif abs(vert.co[idx] - hi0[idx]) <= snap_tol:
                    if vert.co[idx] != hi0[idx]:
                        vert.co[idx] = hi0[idx]
                        snapped += 1

        lo1, hi1, size1 = _axis_sizes(bm)
        delta = [size1[i] - size0[i] for i in range(3)]
        seam = _seam_verdict(watch, bm, lo0, hi0, seam_on_tol, seam_band)

        # ---- assert 1 of 2: EXTENT ----
        assert_tol = take_float(params, "assertTolerance", default=1e-5)
        violated = [("XYZ"[i], size0[i], size1[i]) for i in assert_axes
                    if abs(delta[i]) > assert_tol]
        if violated:
            raise MifOpError(
                "extrude_skirt changed a size that was asserted constant: %s (tolerance "
                "%g BU). The mesh was NOT modified. A skirt cannot do this by itself, so "
                "the selector picked up edges that are not on the boundary loop you "
                "meant -- run select_edges and check boundaryEdges/interiorEdges."
                % ("; ".join("%s %.6f -> %.6f" % v for v in violated), assert_tol))

        # ---- assert 2 of 2: PLANARITY, the failure an extent cannot see ----
        seam_bad = _seam_violations(seam, guarded)
        if seam_bad:
            raise MifOpError(
                "seam PLANARITY broke on an asserted axis: %s (band %g BU / %.4f uu). The "
                "mesh was NOT modified. The bounding box is UNCHANGED, so the size assert "
                "passed -- one corner vert left on the plane keeps the reported extent "
                "identical while the rest of the seam has slid inward. A skirt moves "
                "nothing in X or Y, so this means the selection was not the boundary loop "
                "you meant: run select_edges and check boundaryEdges/interiorEdges. Full "
                "measurement: %s"
                % ("; ".join(seam_bad), seam_band, seam_band * UU_PER_BU, seam))

        # Z grows by up to `depth` and that is the point; anything outside
        # [0, depth] is not a skirt and gets said out loud.
        if delta[2] < -1e-5 or delta[2] > depth + 1e-5:
            warnings.append(
                "Z size changed %.6f -> %.6f BU (delta %.6f), which is outside the "
                "[0, depth=%.6f] a skirt can produce. Inspect before exporting."
                % (size0[2], size1[2], delta[2], depth))
        for i in (0, 1):
            if abs(delta[i]) > 1e-5 and i not in assert_axes:
                warnings.append(
                    "size along %s changed %.6f -> %.6f BU (%.4f uu). A skirt moves "
                    "nothing in X or Y, so this means the selection was not a clean "
                    "boundary loop. If the mesh tiles along %s this will shear the seam."
                    % ("XYZ"[i], size0[i], size1[i], delta[i] * UU_PER_BU, "XYZ"[i]))
        warnings.extend(_seam_warnings(seam, guarded, criteria, seam_band))

        new_face_count = len(new_faces)
        bm.normal_update()
        bm.to_mesh(mesh)
        mesh.update()
        # See the identical call in op_bevel_edges: obj.bound_box / obj.dimensions
        # are stale until a view-layer update, MEASURED on 4.4.0.
        bpy.context.view_layer.update()
    finally:
        bm.free()

    return {
        "object": obj.name,
        "selectedEdges": len(edges),
        "criteria": criteria,
        "depthBU": round(depth, 8),
        "depthUU": round(depth * UU_PER_BU, 6),
        "direction": direction,
        "flipNormals": flip_normals,
        "skirtVertsAdded": len(new_verts),
        "skirtFacesAdded": new_face_count,
        "vertsBefore": verts0,
        "vertsAfter": len(mesh.vertices),
        "facesBefore": faces0,
        "facesAfter": len(mesh.polygons),
        "sizeBeforeBU": rnd(size0),
        "sizeAfterBU": rnd(size1),
        "sizeDeltaBU": rnd(delta, 8),
        "sizeDeltaUU": rnd([d * UU_PER_BU for d in delta], 6),
        "boundsLocalMinBU": rnd(lo1),
        "boundsLocalMaxBU": rnd(hi1),
        "preserveAxes": ["XYZ"[i] for i in preserve],
        "assertAxes": guarded,
        "preserveSnappedVerts": snapped,
        "preserveToleranceBU": snap_tol,
        "seamToleranceBU": seam_on_tol,
        "seamBandBU": seam_band,
        # Reported unconditionally, for all three axes, the way sizeDeltaBU is.
        "offSeamVerts": _off_seam_verts(seam),
        "seamPlanarity": seam,
        "objectAfter": object_info(obj),
        "warnings": warnings,
    }



_MATERIAL_SLOT_KEYS = {"object", "slots", "allowResize"}


def op_set_material_slots(params):
    """Set an object's material slot names, in ORDER.

    THE GAP THIS CLOSES WAS ALREADY DETECTED AND UNFIXABLE. mif_mesh_roundtrip
    compares the material-slot SEQUENCE before and after a Blender edit and warns
    when it differs, with the note "the mesh is valid, the assignment may not be -
    a human decides". It warns because there was nothing it could call. Slot
    ORDER is what decides which Unreal material lands on which face, so a
    reordered slot list renders the wrong material on a mesh that is otherwise
    perfect - and the round trip could see that and not act on it.

    WHY NAMES AND NOT MATERIALS. A slot holds a bpy.types.Material. The round trip
    cares about the SEQUENCE OF NAMES, because that is what lines up against
    Unreal's FStaticMaterial array on reimport. So this takes names, reuses an
    existing material of that name if one is in the file, and creates an empty one
    only when it must - the material's CONTENT is irrelevant here and inventing a
    shader would be inventing data.

    THE COUNT IS NOT CHANGED BY DEFAULT. Adding or removing a slot silently
    re-indexes every polygon's material_index and can leave faces pointing past the
    end of the list. So a list whose length differs from the current slot count is
    REFUSED unless allowResize is passed, and the refusal says what the counts are.

    params:
      object       (str, required)  the mesh object
      slots        (list[str|null], required)  the slot names, in order
      allowResize  (bool, default False)  permit changing the slot COUNT
    """
    reject_unknown(params, _MATERIAL_SLOT_KEYS, "set_material_slots")
    obj = get_object(take(params, "object"), want_mesh=True)
    slots = params.get("slots")
    if not isinstance(slots, list) or not slots:
        raise MifOpError("'slots' is required: a list of material names in ORDER, "
                         "e.g. [\"M_Road\", \"M_Kerb\"]. null means an empty slot. "
                         "Read the current order from object_info.materialSlots.")
    for s in slots:
        if s is not None and not isinstance(s, str):
            raise MifOpError("every entry in 'slots' must be a string or null; got %r" % (s,))

    before = [(s.material.name if s.material else None) for s in obj.material_slots]
    allow_resize = take_bool(params, "allowResize", False)

    if len(slots) != len(before) and not allow_resize:
        # REFUSED rather than resized. Changing the count re-indexes polygons, and a
        # face whose material_index now points past the end renders as the LAST slot
        # with no error anywhere - exactly the silent-wrong-result this project keeps
        # finding. Pass allowResize when that is genuinely what you want.
        raise MifOpError(
            "'%s' has %d material slot(s) and %d were given. Changing the COUNT "
            "re-indexes every polygon's material_index, and a face left pointing past "
            "the end renders as the last slot with no error. Pass allowResize:true if "
            "that is intended. Current order: %s. NOTHING was changed."
            % (obj.name, len(before), len(slots), before))

    # Resize first, so the assignment loop below always has a slot to write into.
    if allow_resize:
        while len(obj.material_slots) < len(slots):
            obj.data.materials.append(None)
        while len(obj.material_slots) > len(slots):
            obj.data.materials.pop()

    created = []
    for i, name in enumerate(slots):
        if name is None:
            obj.material_slots[i].material = None
            continue
        mat = bpy.data.materials.get(name)
        if mat is None:
            # Created EMPTY and reported. The name is what the round trip lines up
            # against Unreal's slot array; the shader is Unreal's business, and
            # inventing one here would be inventing data nobody asked for.
            mat = bpy.data.materials.new(name=name)
            created.append(mat.name)
            if mat.name != name:
                # Blender uniquifies a clashing name silently (M_Road -> M_Road.001).
                # A slot named M_Road.001 will not line up with Unreal's M_Road, so
                # this is reported rather than left to be discovered at reimport.
                raise MifOpError(
                    "asked for material '%s' but Blender created '%s' - a name clash "
                    "was silently uniquified, and that name will NOT line up against "
                    "Unreal's slot array on reimport. NOTHING further was changed."
                    % (name, mat.name))
        obj.material_slots[i].material = mat

    after = [(s.material.name if s.material else None) for s in obj.material_slots]

    # Every polygon's material_index must still be in range. Checked rather than
    # assumed, because an out-of-range index is exactly the failure allowResize
    # invites and it is invisible until something renders.
    out_of_range = 0
    if obj.type == "MESH" and len(after) > 0:
        for poly in obj.data.polygons:
            if poly.material_index >= len(after):
                out_of_range += 1
    elif len(after) == 0:
        out_of_range = len(obj.data.polygons)

    result = {
        "object": obj.name,
        "before": before,
        "materialSlots": after,
        "slotCount": len(after),
        "createdMaterials": created,
        "polygonsOutOfRange": out_of_range,
    }
    if out_of_range:
        result["warning"] = (
            "%d polygon(s) have a material_index past the end of the slot list. They "
            "will render as the last slot. This is a consequence of resizing; fix it "
            "by restoring the slot count or reassigning those faces."
            % out_of_range)
    return result


_DECIMATE_KEYS = {
    "object", "name",
    "ratio", "targetTris", "targetTriangles",
    "mode", "angleLimit", "iterations",
    "dryRun",
}


def op_decimate_mesh(params):
    """Reduce triangle count. The edit a game pipeline wants most, and the one
    analyze_skeletal_split's triangle counts had nowhere to send their answer.

    THREE MODES, because Blender's decimate modifier is three different algorithms
    wearing one name, and they fail in different ways:

      COLLAPSE   (default) ratio-driven edge collapse. The general-purpose one.
                 Give it `ratio` (0-1) or `targetTris` and it solves for the ratio.
      UNSUBDIV   reverses subdivision. Only sensible on quad grids that WERE
                 subdivided; on arbitrary geometry it mangles.
      DISSOLVE   planar merge by `angleLimit`. Removes only geometry that was flat
                 anyway, so it is the lossless-looking one -- and it may remove
                 NOTHING on an already-tight mesh, which is not a failure.

    WHAT IT REPORTS IS WHAT HAPPENED, NOT WHAT WAS ASKED. A collapse decimate
    almost never lands exactly on the requested ratio -- it solves for a face
    budget and cannot split a triangle to hit a target -- so the response carries
    trisBefore, trisAfter and the ratioAchieved that actually resulted, beside the
    ratioRequested. Echoing the request back would be the silent-wrong-number this
    project keeps finding.

    IT REFUSES TO PRETEND. If the modifier removed nothing at all, that is said in
    words rather than returned as a cheerful ok with two identical counts.

    UVs AND CUSTOM NORMALS. Decimation rewrites topology, so a UV layer is
    stretched across the survivors and custom split normals do not survive a
    collapse. Both are WARNED about when present rather than silently damaged --
    the caller may not know the mesh had them.
    """
    reject_unknown(params, _DECIMATE_KEYS, "decimate_mesh")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)

    mode = (take(params, "mode") or "COLLAPSE").upper()
    if mode not in ("COLLAPSE", "UNSUBDIV", "DISSOLVE"):
        raise MifOpError(
            "unknown mode %r. Use COLLAPSE (ratio-driven, the general one), UNSUBDIV "
            "(reverses subdivision; only sensible on quad grids that WERE subdivided), "
            "or DISSOLVE (planar merge by angleLimit, removes only what was already "
            "flat)." % mode)

    ratio = take_float(params, "ratio")
    target = take_int(params, "targetTris", "targetTriangles")
    angle_limit = take_float(params, "angleLimit")
    iterations = take_int(params, "iterations")
    dry_run = take_bool(params, "dryRun", default=False)

    before = mesh_counts(obj)
    tris0 = before.get("tris", -1)

    if mode == "COLLAPSE":
        if (ratio is None) == (target is None):
            raise MifOpError(
                "COLLAPSE needs exactly one of 'ratio' (0-1, the fraction of faces to KEEP) "
                "or 'targetTris' (an absolute triangle count to aim for). Passing both is "
                "ambiguous and passing neither has no meaning. This mesh has %d triangles "
                "now." % tris0)
        if target is not None:
            if target < 1:
                raise MifOpError("'targetTris' must be >= 1; got %d" % target)
            if tris0 <= 0:
                raise MifOpError(
                    "cannot solve a ratio for targetTris: this mesh reports %d triangles, so "
                    "there is nothing to divide by. Pass 'ratio' directly." % tris0)
            if target >= tris0:
                raise MifOpError(
                    "targetTris %d is not BELOW the current %d, so there is nothing to "
                    "decimate. Decimation only removes; it cannot add detail."
                    % (target, tris0))
            ratio = float(target) / float(tris0)
        if not (0.0 < ratio <= 1.0):
            raise MifOpError(
                "'ratio' must be in (0, 1]; got %r. It is the fraction of faces to KEEP, so "
                "0.25 means a quarter of them." % ratio)
    else:
        if ratio is not None or target is not None:
            raise MifOpError(
                "'ratio' and 'targetTris' apply to COLLAPSE only. %s is driven by %s."
                % (mode, "angleLimit" if mode == "DISSOLVE" else "iterations"))
        if mode == "DISSOLVE" and angle_limit is None:
            angle_limit = 5.0
        if mode == "UNSUBDIV" and iterations is None:
            iterations = 1

    warnings = []
    mesh = obj.data
    if mode == "COLLAPSE" and mesh.has_custom_normals:
        warnings.append(
            "'%s' carries custom split normals. A collapse decimate rewrites topology and they "
            "will NOT survive it -- re-author normals afterwards, or use DISSOLVE, which only "
            "removes geometry that was already planar." % obj.name)
    if getattr(mesh, "uv_layers", None) and len(mesh.uv_layers) > 0:
        warnings.append(
            "'%s' has %d UV layer(s). Decimation stretches UVs across the surviving vertices; it "
            "does not re-unwrap. Check the result before baking anything against it."
            % (obj.name, len(mesh.uv_layers)))

    if dry_run:
        return {
            "dryRun": True,
            "object": obj.name,
            "mode": mode,
            "trisBefore": tris0,
            "countsBefore": before,
            "ratioRequested": round(ratio, 6) if ratio is not None else None,
            "targetTris": target,
            "angleLimit": angle_limit,
            "iterations": iterations,
            "warnings": warnings,
            "note": "nothing was modified. Drop dryRun to apply.",
        }

    # THROUGH THE MODIFIER, not a hand-rolled bmesh collapse. Blender's decimate is a
    # quadric-error solver and anything written here would be a worse one wearing the same name.
    mod = obj.modifiers.new(name="MifDecimate", type="DECIMATE")
    try:
        mod.decimate_type = mode
        if mode == "COLLAPSE":
            mod.ratio = ratio
        elif mode == "UNSUBDIV":
            mod.iterations = iterations
        else:
            mod.angle_limit = math.radians(angle_limit)

        prev = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        finally:
            bpy.context.view_layer.objects.active = prev
    except Exception as exc:
        # Leave nothing behind on the failure path. A stranded modifier would change the next
        # export without appearing in any response -- an invisible edit is the worst kind.
        if mod.name in [m.name for m in obj.modifiers]:
            obj.modifiers.remove(mod)
        raise MifOpError("decimate failed on '%s': %s: %s" % (obj.name, type(exc).__name__, exc))

    after = mesh_counts(obj)
    tris1 = after.get("tris", -1)
    achieved = (float(tris1) / float(tris0)) if tris0 > 0 and tris1 >= 0 else None

    result = {
        "object": obj.name,
        "mode": mode,
        "trisBefore": tris0,
        "trisAfter": tris1,
        "trisRemoved": (tris0 - tris1) if (tris0 >= 0 and tris1 >= 0) else None,
        # round(), not rnd(). rnd maps over a SEQUENCE - it exists for vectors - and passing it a
        # scalar raised "float object is not iterable" on every successful path while every
        # REFUSAL path returned cleanly, so the guards all looked right and the op never ran.
        "ratioRequested": round(ratio, 6) if ratio is not None else None,
        "ratioAchieved": round(achieved, 6) if achieved is not None else None,
        "targetTris": target,
        "angleLimit": angle_limit,
        "iterations": iterations,
        "countsBefore": before,
        "countsAfter": after,
        "warnings": warnings,
    }

    # SAID IN WORDS, not left to be spotted in two identical numbers. An op reporting ok while
    # changing nothing is the exact failure shape this codebase keeps finding.
    if tris0 >= 0 and tris1 == tris0:
        result["nothingRemoved"] = True
        result["note"] = (
            "the mesh has the SAME %d triangles it started with -- nothing was removed. For "
            "DISSOLVE that usually means no faces were coplanar within angleLimit=%s; for "
            "COLLAPSE it can mean the ratio was too close to 1 to drop a single face."
            % (tris0, angle_limit))
    elif ratio is not None and achieved is not None and abs(achieved - ratio) > 0.05:
        result["note"] = (
            "landed at ratio %.3f against the %.3f requested. A collapse decimate solves for a "
            "face budget and cannot split a triangle to hit a target exactly; this is the real "
            "figure, not a rounding of the request." % (achieved, ratio))
    return result


OPS = {
    "import_mesh": op_import_mesh,
    "decimate_mesh": op_decimate_mesh,
    "export_mesh": op_export_mesh,
    "select_edges": op_select_edges,
    "bevel_edges": op_bevel_edges,
    "extrude_skirt": op_extrude_skirt,
    "set_material_slots": op_set_material_slots,
}
