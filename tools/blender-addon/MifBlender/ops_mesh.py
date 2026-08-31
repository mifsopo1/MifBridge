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
from mathutils import Vector

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
    # add_leaf_bones DELIBERATELY DISAGREES WITH BLENDER, whose default is True. A leaf bone is a
    # synthetic tip Blender appends to every chain end so the FBX records bone LENGTH; Unreal has no
    # concept of it and imports each one as a real extra bone named <parent>_end. On a hand that is
    # five junk bones, and they then appear in every retarget chain and every anim asset built from
    # that skeleton. Off unless the caller asks.
    "add_leaf_bones": False,
    "use_armature_deform_only": False,
    "primary_bone_axis": "Y",
    "secondary_bone_axis": "X",
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

# glTF ADDED 2026-08-31, and the reason it is safe is the reason OBJ still is not.
#
# The old refusal said "FBX is the only format whose axis and unit round-trip is verified". That was
# a claim about EVIDENCE, so it was answered with evidence rather than argued with: a 1 x 2 x 3 box -
# asymmetric on every axis, so any swap shows as a permutation - exported to GLB and imported back on
# Blender 5.0.1 came back 1 x 2 x 3 exactly, name intact.
#
# It round-trips because the glTF SPEC fixes the convention: +Y up, right-handed, metres, and
# Blender's importer applies that conversion itself. There is nothing to guess. FBX by contrast
# carries its axis and unit metadata IN THE FILE, which is why FBX_IMPORT_ARGS passes no axis
# arguments and lets the importer read them. OBJ remains refused because it fixes nothing and
# declares nothing: UE's exporter swaps Y/Z, de-indexes, and writes no normals, and the file cannot
# tell you it did.
#
# WHAT GLTF DOES CHANGE, and callers are told rather than left to discover it: it has no concept of
# a shared vertex with split normals, so vertices are de-indexed per corner. The 8-vertex cube in
# that probe came back with 24. Nothing is lost - the shape, dimensions and normals are identical -
# but a caller comparing vertexCount across a round trip will see it, and a surprise like that reads
# as corruption.
_SUPPORTED = (".fbx", ".gltf", ".glb")
_GLTF = (".gltf", ".glb")


def _check_format(path, verb):
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SUPPORTED:
        raise MifOpError(
            "%s: supported formats are FBX and glTF/GLB (got '%s'). Those two round-trip "
            "axis and unit verifiably - glTF because its spec FIXES the convention (+Y up, "
            "metres) and FBX because it carries its own metadata. OBJ in particular does "
            "neither, which is why it stays refused: UE's OBJ exporter swaps Y/Z, de-indexes "
            "to 3 verts per triangle and writes no normals, and the file cannot tell you so. "
            "Use run_python if you need another format and accept that the orientation is on "
            "you." % (verb, ext or "<no extension>"))
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

    ext = os.path.splitext(path)[1].lower()
    is_gltf = ext in _GLTF

    # useCustomNormals is an FBX-IMPORTER ARGUMENT and glTF's importer has no equivalent - normals
    # come from the file or are generated by the spec's rules. Refused rather than ignored: silently
    # accepting a parameter that cannot do anything is the class this bridge refuses on principle,
    # and a caller who set it would otherwise believe it took effect.
    if is_gltf and "useCustomNormals" in params:
        raise MifOpError(
            "useCustomNormals is an FBX importer option and has no glTF equivalent - glTF "
            "normals come from the file, or are generated per the spec when absent. Drop the "
            "parameter for a %s import rather than assuming it did nothing." % ext)

    # The import operators return {'FINISHED'}, never the objects they made, so
    # capture by set difference. (bpy.ops.import_scene.fbx has no other handle, and
    # import_scene.gltf has none either.)
    before = set(bpy.data.objects)
    if is_gltf:
        # NO axis or scale arguments, deliberately, and for a different reason than FBX's: the glTF
        # spec fixes +Y up / metres and Blender's importer converts from it. Passing a conversion
        # here would apply it twice.
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        args = dict(FBX_IMPORT_ARGS)
        if "useCustomNormals" in params:
            args["use_custom_normals"] = take_bool(params, "useCustomNormals", default=True)
        bpy.ops.import_scene.fbx(filepath=path, **args)
    bpy.context.view_layer.update()
    created = [o for o in bpy.data.objects if o not in before]

    if not created:
        raise MifOpError(
            "import produced NO objects from %s (%d bytes). The file parsed but held "
            "nothing importable -- check it is a mesh %s and not, say, an animation-only "
            "or camera-only export." % (path, size, "glTF" if is_gltf else "FBX"))

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
    if is_gltf:
        # Not a defect and not optional to mention. glTF has no shared-vertex-with-split-normals
        # concept, so a cube's 8 vertices come back as 24 - measured, not assumed. Shape, dimensions
        # and normals are identical; a caller comparing vertexCount across a round trip is not
        # looking at corruption.
        warnings.append(
            "glTF de-indexes vertices per corner, so vertexCount is higher than the source "
            "mesh had (a cube's 8 becomes 24). Geometry, dimensions and normals are unchanged - "
            "compare dimensions rather than vertex counts across a glTF round trip.")
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
    # SKELETAL EXPORT, added 2026-08-30. See op_export_mesh's own note for why the defaults are
    # what they are - two of them deliberately disagree with Blender's.
    "addLeafBones": ("add_leaf_bones", bool),
    "armatureDeformOnly": ("use_armature_deform_only", bool),
    "primaryBoneAxis": ("primary_bone_axis", str),
    "secondaryBoneAxis": ("secondary_bone_axis", str),
    "bakeAnim": ("bake_anim", bool),
}


def op_export_mesh(params):
    reject_unknown(params, set(_EXPORT_OVERRIDES) | {
        "object", "name", "objects", "file", "filepath", "path",
        "overwrite", "replaceExisting", "objectTypes"}, "export_mesh")

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

    # ------------------------- SKELETAL EXPORT -------------------------
    # WHAT WAS WRONG BEFORE THIS, and it was worse than "the skeleton is missing". object_types was
    # pinned to {"MESH"}, and io_scene_fbx only backs up and restores the REST POSE when ARMATURE is
    # in that set (export_fbx_bin.py). With it absent, an Armature modifier is evaluated like any
    # other modifier, so the mesh was written DEFORMED INTO WHATEVER POSE THE RIG HAPPENED TO BE IN.
    # A character posed mid-animation exported as a static mesh frozen in that pose, with no
    # skeleton, and the file was perfectly valid - nothing failed, nothing warned. The Unreal side
    # needs no work at all: UFbxFactory auto-detects skeletal versus static from the file.
    types = take(params, "objectTypes")
    if types is not None:
        if not isinstance(types, (list, tuple)) or not types:
            raise MifOpError("objectTypes must be a non-empty array, e.g. [\"MESH\", \"ARMATURE\"]. "
                             "NOTHING was written.")
        valid = {"EMPTY", "CAMERA", "LIGHT", "ARMATURE", "MESH", "OTHER"}
        types = [str(t).upper() for t in types]
        bad = [t for t in types if t not in valid]
        if bad:
            raise MifOpError("unknown objectTypes %s. Accepted: %s. NOTHING was written."
                             % (", ".join("'%s'" % b for b in bad), ", ".join(sorted(valid))))
        if "MESH" not in types:
            raise MifOpError("objectTypes must include MESH - this is export_mesh, and a file with "
                             "no mesh in it is not something any caller here wants. NOTHING was "
                             "written.")
        args["object_types"] = set(types)

    # THE ARMATURE MUST BE IN THE SELECTION, not merely allowed by object_types. The exporter
    # gathers its context objects from the selection when use_selection is on, so naming ARMATURE in
    # object_types without selecting the armature produces exactly the same silently-posed static
    # mesh as before - the filter would have let it through and nothing offered it. Found by reading
    # export_fbx_bin.py rather than by exporting and wondering.
    export_set = list(targets)
    armatures = []
    unexported_deformers = []
    for obj in targets:
        for mod in getattr(obj, "modifiers", []):
            if mod.type == "ARMATURE" and getattr(mod, "object", None) is not None:
                arm = mod.object
                if "ARMATURE" in args["object_types"]:
                    if arm not in export_set:
                        export_set.append(arm)
                    if arm.name not in armatures:
                        armatures.append(arm.name)
                elif arm.name not in unexported_deformers:
                    unexported_deformers.append(arm.name)

    snapshot = selection_snapshot()
    try:
        select_only(export_set)
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

    bones_written = 0
    for name in armatures:
        arm = bpy.data.objects.get(name)
        if arm is not None and getattr(arm, "data", None) is not None:
            bones_written += len(arm.data.bones)

    out = {
        "file": path,
        "fileExists": True,
        "fileSizeBytes": size,
        "exportedCount": len(targets),
        "exported": [object_info(o) for o in targets],
        "objectTypesWritten": sorted(args["object_types"]),
        "armaturesExported": armatures,
        "bonesWritten": bones_written,
        "leafBonesAdded": bool(args.get("add_leaf_bones")),
        "isSkeletal": bool(armatures),
        "axis": {"up": "Z", "front": "-Y", "handedness": "right", "unit": "cm",
                 "unrealUnitsPerBlenderUnit": UU_PER_BU},
        "fbxArgs": {k: (sorted(v) if isinstance(v, set) else v)
                    for k, v in args.items()},
    }

    # THE WARNING THAT MATTERS. Silent pose-baking is what this whole block exists to stop, so a
    # mesh whose deforming armature was left out of the file is named, not left for the caller to
    # discover on import.
    if unexported_deformers:
        out["deformerNotExportedWarning"] = (
            "%s deform%s a mesh in this export and %s NOT written to the file, because objectTypes "
            "is %s. The exporter only preserves the REST POSE when ARMATURE is included, so the "
            "mesh has been written deformed into its CURRENT POSE, as a static mesh. If that was "
            "not intended, re-export with objectTypes:[\"MESH\",\"ARMATURE\"]."
            % (", ".join("'%s'" % a for a in unexported_deformers),
               "s" if len(unexported_deformers) == 1 else "",
               "was" if len(unexported_deformers) == 1 else "were",
               sorted(args["object_types"])))
    if armatures and args.get("add_leaf_bones"):
        out["leafBoneWarning"] = (
            "addLeafBones is on. Blender appends a synthetic tip bone to every chain end so the FBX "
            "can record bone length; Unreal has no concept of one and imports each as a REAL extra "
            "bone named <parent>_end, which then appears in every retarget chain and anim asset "
            "built on this skeleton. It is off by default here for that reason.")
    return out


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

        # affect='EDGES' is a SILENT NO-OP on a pure boundary edge (exactly one linked face) -
        # VERIFIED empirically 2026-08-27/28 on both a minimal test plane and this addon's own
        # barrel fixture: bmesh.ops.bevel(geom=boundary_edges, affect='EDGES', ...) leaves
        # vert/face counts bit-for-bit unchanged regardless of offset or segment count, while
        # ok:true and every reported "before" field were still correct - a confidently-wrong
        # answer for exactly the tool's own headline use case (_MIF_DEFAULT_EDGE_SELECTOR's
        # comment: "the two long edges of a road/sidewalk tile", which boundaryOnly selects as
        # PURE boundary edges by construction). affect='VERTICES' with the same edges' vertices
        # as geom genuinely bevels them - also verified empirically, same fixture.
        #
        # affect='VERTICES' is NOT a universal replacement, though: on already-working interior
        # edges it produces MORE geometry than affect='EDGES' for the identical selection (measured:
        # +368 verts vs +141 for the same 21-edge sharp-angle selection on the barrel) - a vertex
        # bevel treats every touched vertex as its own corner rather than following the edge loop's
        # dihedral, which is a real behaviour change, not just a bug fix, for the case that already
        # worked. So this is NOT swapped in wholesale - only routed to boundary edges, which is the
        # ONLY case that was actually broken.
        #
        # boundaryOnly and the angle selector (min/maxAngleDeg) are each PURE by construction -
        # boundaryOnly keeps only len(link_faces)==1, and calc_face_angle(None) returns None (and
        # is skipped) for exactly those same edges - so in normal use this partition is never mixed.
        # A mixed selection is only reachable via axis+side, edgeIndices or allEdges, and mixing the
        # two Blender bevel algorithms in one call has no single correct answer, so it is refused
        # rather than guessed at.
        boundary_edges = [e for e in edges if len(e.link_faces) == 1]
        other_edges = [e for e in edges if len(e.link_faces) != 1]
        if boundary_edges and other_edges:
            raise MifOpError(
                "the selection mixes %d boundary edge(s) (one linked face) with %d non-boundary "
                "edge(s) - these need different Blender bevel algorithms (affect='VERTICES' vs "
                "'EDGES') and there is no single correct way to bevel both in one call. Make two "
                "calls: one with a selector that matches only boundary edges (boundaryOnly:true), "
                "one that matches only the rest (minAngleDeg/maxAngleDeg, or edgeIndices filtered "
                "by hand)." % (len(boundary_edges), len(other_edges)))
        if boundary_edges:
            bevel_affect = "VERTICES"
            bevel_geom = list({v for e in boundary_edges for v in e.verts})
        else:
            bevel_affect = "EDGES"
            bevel_geom = edges

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
            geom=bevel_geom,
            offset=offset,
            offset_type=str(take(params, "offsetType", default="OFFSET")).upper(),
            segments=segments,
            profile=take_float(params, "profile", default=0.5),
            # affect is 'EDGES' for a normal (non-boundary) selection and 'VERTICES' for a pure
            # boundary one - see the note above where bevel_affect/bevel_geom are computed; a
            # boundary edge has only one linked face, and affect='EDGES' is a silent no-op on
            # those regardless of offset or segments. material=0 would drag every new face into
            # the first material slot instead of inheriting, which -1 avoids either way.
            affect=bevel_affect,
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

        # REFUSE RATHER THAN PRETEND, same discipline as decimate_mesh: a non-empty selection
        # that added no geometry at all is not success, whatever bmesh.ops.bevel itself returned.
        # This is a safety net behind the boundary/interior split above, not a replacement for it -
        # it exists for whatever combination of offset/segments/topology neither experiment covered.
        if len(bm.verts) == verts0 and len(bm.faces) == faces0:
            raise MifOpError(
                "bevel selected %d edge(s) on '%s' but added no geometry at all - vert and face "
                "counts are unchanged. The mesh was NOT modified. This can happen when "
                "clamp_overlap reduces every selected edge's effective offset to zero against "
                "tightly packed neighbouring geometry; try a smaller offset, or check the "
                "selection with select_edges first." % (len(edges), obj.name))

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


_UV_KEYS = {
    "object", "name",
    "method", "angleLimitDeg", "islandMargin", "uvLayer", "replace",
    "correctAspect", "dryRun",
    # seams -> unwrap -> pack -> transform, in that order and no other. Packing before unwrapping
    # packs the old layout; seams marked afterwards do nothing until the next unwrap.
    "markSeams", "clearSeams", "uvPack", "packMargin", "uvTransform",
}


def op_uv_unwrap(params):
    """Generate a UV layer. The addon has REPORTED uvLayers in three places from the
    beginning -- object_info, gen_status, and a quality warning that says in as many
    words "no UVs - texturing and lightmaps will both fail until it is unwrapped" --
    and had nothing that could unwrap. Detects the problem, cannot act on it: the same
    inverse gap set_material_slots and create_water_zone were built to close.

    THREE METHODS, and they are for different jobs:

      SMART      (default) bpy.ops.uv.smart_project. Cuts its own seams by angle. The
                 one to use on a prop that has none, which is most imported geometry.
      LIGHTMAP   bpy.ops.uv.lightmap_pack. Non-overlapping islands in 0-1, which is
                 what Unreal wants from a LIGHTMAP channel specifically. Usually
                 belongs on a SECOND layer - see uvLayer below.
      ANGLE      bpy.ops.uv.unwrap, angle-based. Respects SEAMS you have already
                 marked; with no seams on the mesh it flattens the whole thing as one
                 island and the result is unusable. Warned about rather than refused,
                 because a mesh WITH seams is exactly when you want this.

    THESE OPERATORS NEED EDIT MODE, and that is worth recording because the sibling
    case goes the other way: bpy.ops.mesh.bevel cannot run under `blender -b` at all -
    it needs a real VIEW_3D area - which is why bevel_edges goes through bmesh.ops
    instead. The UV operators only need mode_set plus a selection, both of which work
    headless. VERIFIED on 4.4.0 and 5.0.1 before this was written, not assumed from the
    bevel case.

    uvLayer NAMES THE TARGET. Unreal reads lightmaps from a second UV channel, so
    generating one usually means adding a layer rather than overwriting the first.
    Passing a name that already exists is refused unless replace:true, because
    silently overwriting an artist's UVs is not a thing to do by default.

    The response reports uvLayersBefore and uvLayersAfter by NAME, so "did it land on
    the channel I meant" is answerable without a second call.
    """
    reject_unknown(params, _UV_KEYS, "uv_unwrap")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)

    method = (take(params, "method") or "SMART").upper()
    if method not in ("SMART", "LIGHTMAP", "ANGLE"):
        raise MifOpError(
            "unknown method %r. Use SMART (cuts its own seams by angle - the one for a prop "
            "with none), LIGHTMAP (non-overlapping islands in 0-1, what Unreal wants from a "
            "lightmap channel), or ANGLE (respects seams you have already marked)." % method)

    angle_deg = take_float(params, "angleLimitDeg")
    margin = take_float(params, "islandMargin")
    layer_name = take(params, "uvLayer")
    replace = take_bool(params, "replace", default=False)
    correct_aspect = take_bool(params, "correctAspect", default=True)
    dry_run = take_bool(params, "dryRun", default=False)

    if angle_deg is not None and method != "SMART":
        raise MifOpError("'angleLimitDeg' applies to SMART only; %s does not take one." % method)
    if angle_deg is None:
        angle_deg = 66.0
    if not (0.0 < angle_deg < 90.0):
        raise MifOpError("'angleLimitDeg' must be between 0 and 90; got %r. 66 is Blender's own "
                         "default and a sane starting point." % angle_deg)
    if margin is None:
        margin = 0.02
    if not (0.0 <= margin < 1.0):
        raise MifOpError("'islandMargin' must be in [0, 1); got %r" % margin)

    mesh = obj.data
    before = [uv.name for uv in mesh.uv_layers]

    if layer_name and layer_name in before and not replace:
        raise MifOpError(
            "'%s' already has a UV layer named %r. Pass replace:true to overwrite it, or a "
            "different uvLayer name to add a channel beside it. Overwriting somebody's UVs is "
            "not something to do by default. Existing layers: %s"
            % (obj.name, layer_name, before))
    if len(before) >= 8 and (not layer_name or layer_name not in before):
        raise MifOpError(
            "'%s' already has %d UV layers and Blender's limit is 8, so there is no room for "
            "another. Pass uvLayer with one of the existing names plus replace:true. Layers: %s"
            % (obj.name, len(before), before))

    # ---- SEAMS FIRST, and before the ANGLE check below, which is the whole point ----------------
    # That check refuses an ANGLE unwrap on a mesh with no seams, so the endpoint has always offered
    # a method its callers could not use: nothing in this addon could set edge.use_seam. Marking
    # here, in the same call, is what makes ANGLE reachable.
    #
    # The angle criterion is edge.calc_face_angle() - the DIHEDRAL angle between the two faces an
    # edge joins - which is exactly right for seams and is why _select_edges is reused here even
    # though the same grammar was refuted for FACE selection, where a dihedral angle means nothing.
    seams = None
    mark = take(params, "markSeams", default=None)
    clear = take_bool(params, "clearSeams", default=False)
    if mark is not None or clear:
        import bmesh as _bmesh
        bm = _bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.edges.ensure_lookup_table()
            cleared = 0
            if clear:
                for e in bm.edges:
                    if e.seam:
                        e.seam = False
                        cleared += 1
            marked = 0
            criteria = None
            if mark is not None:
                spec = mark if isinstance(mark, dict) else {}
                if mark is True:
                    # Bare true is ambiguous - "every edge" and "the sharp ones" are different
                    # meshes, and guessing would produce one of them silently.
                    raise MifOpError(
                        "markSeams:true is ambiguous - say which edges. Pass an object using the "
                        "same selectors bevel_edges takes: {minAngleDeg: 40} for everything "
                        "sharper than 40 degrees, {boundaryOnly: true}, {edgeIndices: [...]}, or "
                        "{allEdges: true}. NOTHING was changed.")
                edges, criteria = _select_edges(bm, obj, spec, "uv_unwrap.markSeams")
                for e in edges:
                    if not e.seam:
                        e.seam = True
                        marked += 1
                if not edges:
                    raise MifOpError(
                        "markSeams matched NO edges on '%s', so it would mark nothing and the "
                        "unwrap below would behave as though seams had never been asked for. "
                        "Criteria: %s. NOTHING was changed." % (obj.name, criteria))
            bm.to_mesh(mesh)
            mesh.update()
        finally:
            bm.free()
        # Read back off the MESH, not the bmesh that wrote it.
        seams = {
            "marked": marked,
            "cleared": cleared,
            "seamEdgesNow": sum(1 for e in mesh.edges if e.use_seam),
            "criteria": criteria,
        }

    warnings = []
    if method == "ANGLE" and not any(e.use_seam for e in mesh.edges):
        warnings.append(
            "ANGLE unwrap on '%s', which has NO seams marked. Without seams the whole mesh "
            "flattens as a single island and the result is unusable for texturing. Mark seams "
            "first, or use SMART, which cuts its own." % obj.name)
    if not mesh.polygons:
        raise MifOpError("'%s' has no faces, so there is nothing to unwrap." % obj.name)

    if dry_run:
        return {
            "dryRun": True,
            "object": obj.name,
            "method": method,
            "uvLayersBefore": before,
            "targetLayer": layer_name,
            "angleLimitDeg": angle_deg,
            "islandMargin": margin,
            "warnings": warnings,
            "note": "nothing was modified. Drop dryRun to apply.",
        }

    # The layer has to exist BEFORE the unwrap, and be the ACTIVE one, or the operator writes
    # into whichever channel happened to be active - which is how a lightmap lands on top of
    # the base UVs and nobody notices until the bake.
    created = None
    if layer_name:
        if layer_name in before:
            target = mesh.uv_layers[layer_name]
        else:
            target = mesh.uv_layers.new(name=layer_name)
            created = target.name
    elif not before:
        target = mesh.uv_layers.new(name="UVMap")
        created = target.name
    else:
        target = mesh.uv_layers.active or mesh.uv_layers[0]
    mesh.uv_layers.active = target
    active_name = target.name

    prev_active = bpy.context.view_layer.objects.active
    prev_mode = obj.mode
    try:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        if method == "SMART":
            bpy.ops.uv.smart_project(angle_limit=math.radians(angle_deg),
                                     island_margin=margin,
                                     correct_aspect=correct_aspect)
        elif method == "LIGHTMAP":
            bpy.ops.uv.lightmap_pack(PREF_MARGIN_DIV=max(0.001, margin * 100.0))
        else:
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=margin,
                              correct_aspect=correct_aspect)

        # PACK AFTER THE UNWRAP, never before - packing first packs the OLD layout, which looks
        # like it worked and is simply the previous islands rearranged.
        if take_bool(params, "uvPack", default=False):
            pack_margin = take_float(params, "packMargin")
            bpy.ops.uv.select_all(action="SELECT")
            bpy.ops.uv.pack_islands(margin=margin if pack_margin is None else pack_margin)
    except Exception as exc:
        # A layer created for an unwrap that then threw is debris that would change the next
        # export without appearing in any response.
        if created and created in [uv.name for uv in mesh.uv_layers]:
            mesh.uv_layers.remove(mesh.uv_layers[created])
        raise MifOpError("uv_unwrap failed on '%s' (%s): %s: %s"
                         % (obj.name, method, type(exc).__name__, exc))
    finally:
        try:
            if obj.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            if prev_mode and prev_mode != "OBJECT":
                pass    # deliberately left in OBJECT: headless has no mode to return to
            bpy.context.view_layer.objects.active = prev_active
        except Exception:
            pass

    # ---- TRANSFORM LAST, on the layer that was just written ------------------------------------
    # Plain RNA on the UV loops rather than an operator: no mode change, no selection, and the
    # postcondition is the bounds read back off the layer afterwards.
    transform = take(params, "uvTransform", default=None)
    uv_transform = None
    if transform is not None:
        if not isinstance(transform, dict):
            raise MifOpError("uvTransform must be an object like {scale: [1,1], offset: [0,0]}. "
                             "The unwrap above already ran.")
        unknown = set(transform) - {"scale", "offset"}
        if unknown:
            raise MifOpError("uvTransform does not take %s - it takes scale [u,v] and offset "
                             "[u,v]. Rotation is not offered because rotating a packed layout "
                             "moves islands out of 0-1 with nothing to put them back."
                             % ", ".join(sorted(unknown)))

        def _pair(key, default):
            v = transform.get(key, default)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return (float(v), float(v))
            if not isinstance(v, (list, tuple)) or len(v) != 2 \
                    or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in v):
                raise MifOpError("uvTransform.%s must be a number or [u,v]; got %r" % (key, v))
            return (float(v[0]), float(v[1]))

        sx, sy = _pair("scale", 1.0)
        ox, oy = _pair("offset", 0.0)
        layer = mesh.uv_layers.get(active_name) or mesh.uv_layers.active
        if layer is None:
            raise MifOpError("no UV layer to transform after the unwrap, which should be "
                             "impossible - reported rather than crashing on None.")
        n = len(layer.data)
        flat = [0.0] * (n * 2)
        layer.data.foreach_get("uv", flat)
        before_u = flat[0::2]
        before_v = flat[1::2]
        for i in range(n):
            flat[i * 2] = flat[i * 2] * sx + ox
            flat[i * 2 + 1] = flat[i * 2 + 1] * sy + oy
        layer.data.foreach_set("uv", flat)
        mesh.update()

        check = [0.0] * (n * 2)
        layer.data.foreach_get("uv", check)
        us, vs = check[0::2], check[1::2]
        # Read back off the layer - foreach_set reports nothing, and a transform that did not take
        # would leave a layout that looks unwrapped and is in the wrong place.
        uv_transform = {
            "scale": [sx, sy],
            "offset": [ox, oy],
            "loops": n,
            "boundsBefore": {"min": [min(before_u), min(before_v)],
                             "max": [max(before_u), max(before_v)]} if n else None,
            "boundsAfter": {"min": [min(us), min(vs)], "max": [max(us), max(vs)]} if n else None,
        }
        if n and (min(us) < -1e-6 or min(vs) < -1e-6 or max(us) > 1.0 + 1e-6
                  or max(vs) > 1.0 + 1e-6):
            # Said, not refused: leaving 0-1 is legal and sometimes deliberate (tiling), but it is
            # NOT what Unreal wants from a lightmap channel and nothing else would mention it.
            warnings.append(
                "after uvTransform the layout extends outside 0-1 (u %.4f..%.4f, v %.4f..%.4f). "
                "That is legal and fine for a tiling texture, but a lightmap channel must stay "
                "inside 0-1 or Unreal will pack it wrong."
                % (min(us), max(us), min(vs), max(vs)))

    after = [uv.name for uv in mesh.uv_layers]
    return {
        "object": obj.name,
        "method": method,
        "uvLayersBefore": before,
        "uvLayersAfter": after,
        "activeLayer": active_name,
        "createdLayer": created,
        "angleLimitDeg": angle_deg if method == "SMART" else None,
        "islandMargin": margin,
        "faces": len(mesh.polygons),
        "seams": seams,
        "packed": bool(take_bool(params, "uvPack", default=False)),
        "uvTransform": uv_transform,
        "warnings": warnings,
        "note": ("the unwrap wrote into %r. Unreal reads lightmaps from a SECOND UV channel, so "
                 "pass uvLayer to put a LIGHTMAP pass somewhere other than the base UVs."
                 % active_name),
    }


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
    # THE NAME AS A PYTHON STRING, taken now. modifier_apply frees the modifier, and the except
    # below covers that call - so reading mod.name in the handler, or handing `mod` to remove(),
    # would be a read of released RNA memory. boolean_op did exactly that and raised
    # UnicodeDecodeError from inside a plain `.name` on Blender 5.0.1 while passing on 3.6/4.2/4.4.
    mod_name = str(mod.name)
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
            bpy.ops.object.modifier_apply(modifier=mod_name)
        finally:
            bpy.context.view_layer.objects.active = prev
    except Exception as exc:
        # Leave nothing behind on the failure path. A stranded modifier would change the next
        # export without appearing in any response -- an invisible edit is the worst kind.
        stale = obj.modifiers.get(mod_name)
        if stale is not None:
            obj.modifiers.remove(stale)
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


def op_apply_transform(params):
    """Bake an object's loc/rot/scale into its MESH DATA, restoring the identity transform.

    THE ONE THAT MAKES THE FIDELITY GATE SATISFIABLE. object_info reports
    isIdentityTransform, and mif_mesh_roundtrip asserts it before and after every
    edit, because a non-identity object transform means the pivot moved -- an FBX
    written from an object scaled 0.01 at the OBJECT level imports into Unreal at a
    size nobody asked for, and the mesh's own vertices still disagree with what the
    viewport showed. Until now the addon could DETECT that state and had no way out
    of it: nothing could apply a transform. Detecting a problem you cannot fix is
    the same half-a-subsystem shape uv_unwrap closed for UVs.

    WHICH CHANNELS, and why they are separate. `location`, `rotation` and `scale`
    default to all three, but they are independent on purpose: baking rotation into
    a mesh destined for a rig is usually wrong (the armature expects the object
    rotation), while baking SCALE almost always right, because non-uniform object
    scale is what silently breaks normals on import.

    NEGATIVE SCALE IS REPORTED, NOT SILENTLY FIXED. A mirrored object carries a
    negative scale component, and applying it inverts the winding order, so the mesh
    renders inside-out. Blender does not warn. This does, and recalculates the
    normals when `fixNormals` is left on -- which is the correct repair, and it is
    named in the response rather than done invisibly.

    MULTI-USER MESH DATA IS REFUSED. Applying a transform to a mesh shared by two
    objects would move BOTH, one of them silently. Blender's own operator raises;
    this refuses first with the sharing count in the message.
    """
    reject_unknown(params, ("object", "name", "location", "rotation", "scale", "fixNormals"),
                   "apply_transform")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)

    do_loc = take_bool(params, "location", default=True)
    do_rot = take_bool(params, "rotation", default=True)
    do_scale = take_bool(params, "scale", default=True)
    if not (do_loc or do_rot or do_scale):
        raise MifOpError(
            "apply_transform was asked to apply nothing - location, rotation and scale are all "
            "false. Leave them unset to apply all three, or set at least one. NOTHING was changed.")

    if obj.data.users > 1:
        raise MifOpError(
            "'%s' shares its mesh data with %d other object(s), and applying a transform would "
            "move every one of them - one of which you did not ask about. Make the data "
            "single-user in Blender first. NOTHING was changed." % (obj.name, obj.data.users - 1))

    before = object_info(obj)
    had_negative_scale = any(v < 0.0 for v in obj.scale)

    snap = selection_snapshot()
    try:
        select_only([obj])
        bpy.ops.object.transform_apply(location=do_loc, rotation=do_rot, scale=do_scale)
    finally:
        selection_restore(snap)

    fixed_normals = False
    if had_negative_scale and do_scale and take_bool(params, "fixNormals", default=True):
        me = obj.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(me)
        bm.free()
        me.update()
        fixed_normals = True

    after = object_info(obj)
    out = {
        "object": obj.name,
        "applied": {"location": do_loc, "rotation": do_rot, "scale": do_scale},
        "before": before,
        "after": after,
        "isIdentityTransform": after["isIdentityTransform"],
        "hadNegativeScale": had_negative_scale,
        "normalsRecalculated": fixed_normals,
    }
    if had_negative_scale:
        out["negativeScaleNote"] = (
            "this object had a NEGATIVE scale component - it was mirrored. Applying that inverts "
            "the winding order, so the mesh would render inside-out in Unreal. Face normals were "
            "recalculated to repair it." if fixed_normals else
            "this object had a NEGATIVE scale component - it was mirrored. Applying that inverts "
            "the winding order, so the mesh will render inside-out in Unreal. fixNormals was off, "
            "so nothing was corrected - call clean_mesh{recalcNormals:true} if that was not "
            "intended.")
    if not after["isIdentityTransform"]:
        out["note"] = (
            "the transform is still not identity, because only some channels were applied. "
            "isIdentityTransform is what mif_mesh_roundtrip gates on, so apply all three before "
            "exporting for Unreal.")
    return out


def op_set_origin(params):
    """Move an object's ORIGIN without moving its geometry in the world.

    The pivot is what Unreal rotates and places the mesh around, and Blender puts
    it wherever the object happened to be created. A prop whose origin sits in the
    middle of its bounding box cannot be placed on a floor by setting Z; a door
    whose origin is not on its hinge edge cannot be rotated open. Neither is fixable
    on the Unreal side -- the origin is baked into the FBX -- so it has to be right
    before export, and nothing here could set it.

    MODES:
      geometry (default) the median point of the mesh, Blender's ORIGIN_GEOMETRY.
      bounds             the centre of the bounding box. Different from geometry on
                         any mesh with uneven vertex density, which is most of them.
      bottom             the bounding-box centre in X/Y, its MINIMUM in Z. The one a
                         placeable prop almost always wants, because it puts the
                         pivot on the floor.
      cursor             the 3D cursor's current position.
      world              the world origin, (0,0,0).
      point              an explicit `location` in Blender units.

    THE GEOMETRY DOES NOT MOVE. That is the whole point, and it is asserted: the
    world-space bounds are measured before and after and reported together, so a
    caller can see that only the pivot moved.
    """
    reject_unknown(params, ("object", "name", "mode", "location"), "set_origin")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    mode = (take(params, "mode", default="geometry") or "geometry").lower()

    valid = ("geometry", "bounds", "bottom", "cursor", "world", "point")
    if mode not in valid:
        raise MifOpError("unknown mode '%s' for set_origin. Accepted: %s. NOTHING was changed."
                         % (mode, ", ".join(valid)))

    def world_bounds():
        pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        return (
            [min(p[i] for p in pts) for i in range(3)],
            [max(p[i] for p in pts) for i in range(3)],
        )

    wmin_before, wmax_before = world_bounds()
    before_origin = list(obj.matrix_world.translation)

    snap = selection_snapshot()
    saved_cursor = list(bpy.context.scene.cursor.location)
    try:
        select_only([obj])
        if mode == "geometry":
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")
        elif mode == "bounds":
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
        else:
            if mode == "bottom":
                target = Vector(((wmin_before[0] + wmax_before[0]) * 0.5,
                                 (wmin_before[1] + wmax_before[1]) * 0.5,
                                 wmin_before[2]))
            elif mode == "world":
                target = Vector((0.0, 0.0, 0.0))
            elif mode == "point":
                loc = take(params, "location", required=True)
                if not isinstance(loc, (list, tuple)) or len(loc) != 3:
                    raise MifOpError(
                        "mode 'point' needs location as [x, y, z] in Blender units. "
                        "NOTHING was changed.")
                target = Vector([float(v) for v in loc])
            else:  # cursor
                target = Vector(saved_cursor)
            bpy.context.scene.cursor.location = target
            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    finally:
        bpy.context.scene.cursor.location = saved_cursor
        selection_restore(snap)

    wmin_after, wmax_after = world_bounds()
    moved = max(abs(wmin_after[i] - wmin_before[i]) for i in range(3))
    moved = max(moved, max(abs(wmax_after[i] - wmax_before[i]) for i in range(3)))

    out = {
        "object": obj.name,
        "mode": mode,
        "originBeforeBU": rnd(before_origin),
        "originAfterBU": rnd(list(obj.matrix_world.translation)),
        "worldBoundsMinBU": rnd(wmin_after),
        "worldBoundsMaxBU": rnd(wmax_after),
        "geometryMovedBU": round(moved, 6),
        "geometryStayedPut": moved < 1e-4,
    }
    if not out["geometryStayedPut"]:
        out["note"] = (
            "the world-space bounds moved by %.6f BU. Setting an origin should move the PIVOT and "
            "leave the geometry where it is, so this is reported rather than assumed harmless."
            % moved)
    return out


def op_clean_mesh(params):
    """The cleanup pass an imported or edited mesh needs before it goes back to Unreal.

    FIVE INDEPENDENT STEPS, each off or on by name, run in the only order that is
    correct: merge first (so loose/degenerate detection sees the merged topology),
    then delete loose, then dissolve degenerates, then triangulate, then recalc
    normals last (because every earlier step can change what a face's normal should
    be).

      mergeDistance   weld vertices closer than this. The single most useful one on
                      an imported mesh: duplicate verts along a seam are invisible
                      in the viewport and split the mesh's smoothing in Unreal.
      removeLoose     delete verts and edges belonging to no face. They export, they
                      cost nothing visible, and they make Unreal's bounds wrong.
      dissolveDegenerate  collapse zero-area faces and zero-length edges.
      triangulate     Unreal triangulates on import anyway; doing it here means the
                      triangulation you SEE is the one you ship, rather than one the
                      importer picked.
      recalcNormals   make normals consistently outward.

    IT REPORTS WHAT EACH STEP DID, not that it ran. Counts before and after per
    step, so "cleaned" is a number rather than a claim -- a mesh that was already
    clean returns zeroes and says so, instead of a cheerful ok that reads as work
    performed.

    CUSTOM SPLIT NORMALS. recalcNormals discards them, so it is refused with a named
    reason when the mesh has them and `force` is not set, rather than quietly
    throwing away data a rigger put there on purpose.
    """
    reject_unknown(params, ("object", "name", "mergeDistance", "removeLoose",
                            "dissolveDegenerate", "triangulate", "recalcNormals", "force"),
                   "clean_mesh")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)

    merge_distance = take_float(params, "mergeDistance", default=0.0)
    do_loose = take_bool(params, "removeLoose", default=False)
    do_degenerate = take_bool(params, "dissolveDegenerate", default=False)
    do_tri = take_bool(params, "triangulate", default=False)
    do_normals = take_bool(params, "recalcNormals", default=False)

    if not (merge_distance > 0.0 or do_loose or do_degenerate or do_tri or do_normals):
        raise MifOpError(
            "clean_mesh was asked to do nothing - every step is off. Set at least one of "
            "mergeDistance, removeLoose, dissolveDegenerate, triangulate, recalcNormals. "
            "NOTHING was changed.")

    me = obj.data
    has_custom_normals = bool(getattr(me, "has_custom_normals", False))
    if do_normals and has_custom_normals and not take_bool(params, "force", default=False):
        raise MifOpError(
            "'%s' has CUSTOM SPLIT NORMALS, and recalcNormals would discard them - they are "
            "usually authored deliberately (hard-surface shading, foliage cards). Pass force:true "
            "to recalculate anyway, or leave recalcNormals off. NOTHING was changed." % obj.name)

    before = mesh_counts(obj)
    steps = {}

    bm = bmesh.new()
    bm.from_mesh(me)

    if merge_distance > 0.0:
        v0 = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=merge_distance)
        steps["merged"] = {"vertsRemoved": v0 - len(bm.verts), "distance": merge_distance}

    if do_loose:
        loose_v = [v for v in bm.verts if not v.link_faces]
        loose_e = [e for e in bm.edges if not e.link_faces]
        n_e = len(loose_e)
        if loose_e:
            bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
        loose_v = [v for v in bm.verts if not v.link_faces]
        n_v = len(loose_v)
        if loose_v:
            bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
        steps["removedLoose"] = {"verts": n_v, "edges": n_e}

    if do_degenerate:
        f0, e0 = len(bm.faces), len(bm.edges)
        bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
        steps["dissolvedDegenerate"] = {"facesRemoved": f0 - len(bm.faces),
                                        "edgesRemoved": e0 - len(bm.edges)}

    if do_tri:
        n_before = len([f for f in bm.faces if len(f.verts) > 3])
        if n_before:
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
        steps["triangulated"] = {"nonTriFacesConverted": n_before}

    if do_normals:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        steps["recalcNormals"] = {"faces": len(bm.faces),
                                  "discardedCustomSplitNormals": has_custom_normals}

    bm.to_mesh(me)
    bm.free()
    me.update()

    after = mesh_counts(obj)
    removed_total = (before["verts"] - after["verts"]) + (before["faces"] - after["faces"])
    out = {
        "object": obj.name,
        "before": before,
        "after": after,
        "steps": steps,
        "changedAnything": before != after,
    }
    if not out["changedAnything"]:
        out["note"] = (
            "every requested step ran and the mesh was already clean by those measures - the "
            "counts are identical. Said in words rather than returned as an ok that reads as work "
            "performed.")
    else:
        out["netElementsRemoved"] = removed_total
    return out


def op_uv_info(params):
    """Read a mesh's UVs per layer - the verification half of the unwrap this addon can already do.

    uv_unwrap can create a layer; object_info reports only that layers EXIST. So
    "did the unwrap actually produce something Unreal can bake a lightmap into"
    had no answer short of opening Blender and looking. This answers it.

    WHAT UNREAL ACTUALLY REQUIRES, which is why these particular numbers:
      inside 0-1     a lightmap UV must lie within the unit square. Unreal's
                     lightmass packs charts into it; anything outside is clamped
                     and bakes as garbage. facesOutside01 is therefore the first
                     thing to check on a lightmap channel and means nothing on a
                     texture channel, where tiling outside 0-1 is the point.
      non-overlapping  two islands sharing UV space share lightmap texels, so one
                     surface's light bleeds onto another. Overlap is legal and
                     normal on a texture channel and fatal on a lightmap one.
      the channel INDEX is what Unreal's Lightmap Coordinate Index points at, and
                     it is positional - the order layers appear here is the order
                     FBX writes them.

    So `lightmapReady` is reported PER LAYER with a named reason, and it is a
    judgement about that layer used AS a lightmap - a texture layer failing it is
    not a defect and the reason says which test it failed.

    OVERLAP IS MEASURED IN BMESH, NOT VIA bpy.ops.uv.select_overlap, which needs a
    UV editor area and cannot run under `blender -b` at all - the same constraint
    that pushed bevel_edges onto bmesh.ops. Islands are found by walking shared UV
    coordinates, then tested pairwise by AABB. AABB overlap is a CONSERVATIVE test:
    it reports every real overlap plus some pairs whose bounding boxes touch while
    the islands themselves do not. That is stated in the response rather than
    presented as exact, because a false "your lightmap is broken" that sends someone
    re-unwrapping a fine mesh is worse than a number with a stated bound.

    A PURE READ. Nothing here writes, so it cannot damage a mesh it is diagnosing.
    """
    reject_unknown(params, ("object", "name", "layer", "maxReportedIslands"), "uv_info")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    want_layer = take(params, "layer", default=None)
    max_islands = take_int(params, "maxReportedIslands", default=64)

    me = obj.data
    if not me.uv_layers:
        return {
            "object": obj.name,
            "layerCount": 0,
            "layers": [],
            "note": ("this mesh has NO UV layers at all. Texturing and lightmap baking both fail "
                     "until it is unwrapped - call uv_unwrap."),
        }

    names = [l.name for l in me.uv_layers]
    if want_layer is not None and want_layer not in names:
        raise MifOpError("no UV layer named '%s' on '%s'. It has %d: %s."
                         % (want_layer, obj.name, len(names), ", ".join(names)))

    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()

    layers_out = []
    for index, name in enumerate(names):
        if want_layer is not None and name != want_layer:
            continue
        uv_layer = bm.loops.layers.uv.get(name)
        if uv_layer is None:
            continue

        umin = [float("inf"), float("inf")]
        umax = [float("-inf"), float("-inf")]
        outside = 0
        zero_area = 0
        densities = []

        # Islands by shared UV coordinate, walked over face adjacency. Quantised to 1e-5 so two
        # loops that are the same corner but differ in the last float bit are one point.
        face_island = {}
        islands = []
        for face in bm.faces:
            if face.index in face_island:
                continue
            stack = [face]
            island = []
            face_island[face.index] = len(islands)
            while stack:
                f = stack.pop()
                island.append(f)
                keys = {(round(l[uv_layer].uv.x, 5), round(l[uv_layer].uv.y, 5)) for l in f.loops}
                for edge in f.edges:
                    for nf in edge.link_faces:
                        if nf.index in face_island:
                            continue
                        nkeys = {(round(l[uv_layer].uv.x, 5), round(l[uv_layer].uv.y, 5))
                                 for l in nf.loops}
                        if keys & nkeys:
                            face_island[nf.index] = len(islands)
                            stack.append(nf)
            islands.append(island)

        island_boxes = []
        for island in islands:
            imin = [float("inf"), float("inf")]
            imax = [float("-inf"), float("-inf")]
            for f in island:
                for l in f.loops:
                    uv = l[uv_layer].uv
                    for a, v in enumerate((uv.x, uv.y)):
                        imin[a] = min(imin[a], v)
                        imax[a] = max(imax[a], v)
                        umin[a] = min(umin[a], v)
                        umax[a] = max(umax[a], v)
            island_boxes.append((imin, imax))

        for face in bm.faces:
            uvs = [l[uv_layer].uv for l in face.loops]
            if any(uv.x < -1e-6 or uv.x > 1.0 + 1e-6 or uv.y < -1e-6 or uv.y > 1.0 + 1e-6
                   for uv in uvs):
                outside += 1
            # UV-space area by the shoelace formula, against 3D area, for texel density.
            a2 = 0.0
            for i in range(len(uvs)):
                x1, y1 = uvs[i].x, uvs[i].y
                x2, y2 = uvs[(i + 1) % len(uvs)].x, uvs[(i + 1) % len(uvs)].y
                a2 += x1 * y2 - x2 * y1
            uv_area = abs(a2) * 0.5
            if uv_area <= 1e-12:
                zero_area += 1
                continue
            face_area = face.calc_area()
            if face_area > 1e-12:
                densities.append((uv_area / face_area) ** 0.5)

        overlaps = 0
        for i in range(len(island_boxes)):
            amin, amax = island_boxes[i]
            for j in range(i + 1, len(island_boxes)):
                bmin, bmax = island_boxes[j]
                if (amin[0] < bmax[0] and bmin[0] < amax[0]
                        and amin[1] < bmax[1] and bmin[1] < amax[1]):
                    overlaps += 1

        densities.sort()
        face_total = len(bm.faces)
        row = {
            "name": name,
            "index": index,
            "islandCount": len(islands),
            "boundsMin": rnd(umin) if face_total else None,
            "boundsMax": rnd(umax) if face_total else None,
            "facesTotal": face_total,
            "facesOutside01": outside,
            "zeroAreaFaces": zero_area,
            "overlappingIslandPairs": overlaps,
            "texelDensityMin": round(densities[0], 6) if densities else None,
            "texelDensityMax": round(densities[-1], 6) if densities else None,
            "texelDensityMedian": round(densities[len(densities) // 2], 6) if densities else None,
        }
        if len(islands) > max_islands:
            row["islandsTruncatedNote"] = (
                "%d islands, more than maxReportedIslands (%d). The COUNTS above are exact - only "
                "per-island detail would have been truncated, and none is reported at this size."
                % (len(islands), max_islands))

        reasons = []
        if outside:
            reasons.append("%d of %d faces lie outside 0-1" % (outside, face_total))
        if overlaps:
            reasons.append("%d island pair(s) overlap by bounding box" % overlaps)
        if zero_area:
            reasons.append("%d face(s) have zero UV area" % zero_area)
        row["lightmapReady"] = not reasons
        if reasons:
            row["lightmapReadyReason"] = (
                "not usable as a LIGHTMAP channel: " + "; ".join(reasons) +
                ". None of this is a defect on a TEXTURE channel - tiling outside 0-1 and "
                "overlapping islands are both normal and often deliberate there.")
        if overlaps:
            row["overlapNote"] = (
                "measured by island BOUNDING BOX, which is conservative: every real overlap is "
                "counted, plus pairs whose boxes touch while the islands do not. Exact per-triangle "
                "overlap needs a UV editor area and cannot run headless.")
        layers_out.append(row)

    bm.free()
    return {
        "object": obj.name,
        "layerCount": len(names),
        "activeLayer": me.uv_layers.active.name if me.uv_layers.active else None,
        "layers": layers_out,
        "indexNote": ("index is positional and is what FBX writes and what Unreal's Lightmap "
                      "Coordinate Index points at."),
    }


OPS = {
    "import_mesh": op_import_mesh,
    "decimate_mesh": op_decimate_mesh,
    "uv_unwrap": op_uv_unwrap,
    "export_mesh": op_export_mesh,
    "select_edges": op_select_edges,
    "bevel_edges": op_bevel_edges,
    "extrude_skirt": op_extrude_skirt,
    "set_material_slots": op_set_material_slots,
    "apply_transform": op_apply_transform,
    "set_origin": op_set_origin,
    "clean_mesh": op_clean_mesh,
    "uv_info": op_uv_info,
}
