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

import array
import hashlib
import math
import os

import bmesh
import bpy
from mathutils import Vector

from .ops_common import (MifOpError, UU_PER_BU, axis_index, check_output_path,
                         edit_mode_stale, finite_float, finite_floats,
                         get_object, mesh_counts, object_info, reject_unknown, require_editable,
                         rnd, select_only, selection_restore, selection_snapshot,
                         shared_data_note, take, take_bool, take_float, take_int)

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
# PER-VERB, and that split is not cosmetic - it is a bug this file already had for two commits.
#
# _check_format is called by BOTH import_mesh and export_mesh. Widening one shared _SUPPORTED tuple
# to add glTF IMPORT silently widened export too, and export_mesh does not dispatch on extension -
# it always calls export_scene.fbx. So `export_mesh {file: "x.glb"}` answered ok:true and wrote a
# file beginning "Kaydara FBX Binary". Silent, plausible, and exactly the class this bridge exists
# to refuse: a caller gets a .glb that no glTF loader will open and nothing said a word.
#
# Caught by asking what else touches _check_format, not by a tool. Two verbs sharing a capability
# list is fine only while their capabilities are the same, and the moment they diverge the shared
# list is a liability rather than a convenience.
_IMPORT_FORMATS = (".fbx", ".gltf", ".glb")
_EXPORT_FORMATS = (".fbx",)
_GLTF = (".gltf", ".glb")


def _check_format(path, verb, allowed=None):
    ext = os.path.splitext(path)[1].lower()
    allowed = allowed or _IMPORT_FORMATS
    if ext in _GLTF and allowed is _EXPORT_FORMATS:
        # audit-ok: export_mesh, import_mesh - this branch is gated on `allowed is _EXPORT_FORMATS`,
        # so export_mesh is the ONLY caller that can reach it and naming it is correct. import_mesh
        # is named on purpose too: the useful half of the sentence is which verb DOES take glTF.
        raise MifOpError(
            "export_mesh writes FBX only (got '%s'). import_mesh DOES take glTF and GLB, so the "
            "asymmetry is deliberate rather than an oversight: the FBX export path carries the "
            "armature and object_types handling that keeps a rigged mesh from being written frozen "
            "in whatever pose it happened to be in, and none of that transfers to the glTF "
            "exporter unexamined. Export .fbx, or use run_python and own the result." % ext)
    if ext not in allowed:
        # THE LIST MUST BE THIS CALLER'S LIST. This branch used to recite "FBX and glTF/GLB"
        # whatever it was passed, which is the import set - so `export_mesh {file:"x.obj"}` was
        # told glTF and GLB were available, and the caller who believed it got the branch ABOVE
        # saying "export_mesh writes FBX only". One helper, two refusals, contradicting each other,
        # and the verb name on both was right. That is what made it survive: a wrong refusal gets
        # reported, a right refusal followed by a wrong sentence gets acted on.
        #
        # Derived from `allowed` rather than restated, so widening either tuple cannot desynchronise
        # the message again - which is the same failure the comment at the top of this block already
        # records once, when a shared _SUPPORTED let export_mesh write FBX bytes into a .glb.
        takes_gltf = any(g in allowed for g in _GLTF)
        # audit-ok: import_mesh - the verb this refusal is FOR arrives as `verb` and is formatted
        # in, never hard-coded. import_mesh appears only in the not-takes_gltf arm, which is
        # reachable only by export_mesh, and it is there to say where glTF support does live.
        raise MifOpError(
            "%s: supported formats are %s (got '%s'). %s OBJ in particular does not, which is why "
            "it stays refused: UE's OBJ exporter swaps Y/Z, de-indexes to 3 verts per triangle and "
            "writes no normals, and the file cannot tell you so. Use run_python if you need "
            "another format and accept that the orientation is on you."
            % (verb,
               "FBX and glTF/GLB" if takes_gltf else "FBX",
               ext or "<no extension>",
               "Those two round-trip axis and unit verifiably - glTF because its spec FIXES the "
               "convention (+Y up, metres) and FBX because it carries its own metadata."
               if takes_gltf else
               "FBX round-trips axis and unit verifiably because it carries its own metadata. "
               "import_mesh additionally takes glTF and GLB; this verb does not, and the reason "
               "is on the glTF refusal above."))
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
                            "useCustomNormals", "importAnimation", "rename"}, "import_mesh")
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

    # THE SAME VERB BEHAVED OPPOSITELY BY EXTENSION, and said nothing either way. FBX_IMPORT_ARGS
    # pinned use_anim False with no parameter reaching it, so every FBX animation was dropped; the
    # glTF importer has NO animation option at all - measured on 3.6.23, 4.2.17, 4.4.0 and 5.0.1,
    # where the only animation properties on the operators are fbx's use_anim and anim_offset and
    # gltf has none - so it always imports them. An animated cube written to both and imported back
    # on 4.4 came in with no action from FBX and with one from glTF.
    #
    # importAnimation reaches use_anim on FBX. On glTF it can only be refused when false, for
    # useCustomNormals' reason one paragraph up: accepting a parameter that cannot do anything is
    # the class this bridge refuses on principle.
    want_anim = params.get("importAnimation")
    if is_gltf and want_anim is not None and not take_bool(params, "importAnimation", default=True):
        raise MifOpError(
            "importAnimation:false cannot be honoured for %s - Blender's glTF importer has no "
            "animation option on any supported build (3.6, 4.2, 4.4, 5.0) and always imports "
            "them. Import, then delete the action if it is unwanted. Refused rather than "
            "accepted and ignored." % ext)

    # The import operators return {'FINISHED'}, never the objects they made, so
    # capture by set difference. (bpy.ops.import_scene.fbx has no other handle, and
    # import_scene.gltf has none either.)
    before = set(bpy.data.objects)
    # ACTIONS TOO, because whether an animation arrived is the thing that was going unreported and
    # it cannot be read off the request - use_anim True on a file with no animation creates nothing.
    actions_before = set(bpy.data.actions)
    if is_gltf:
        # NO axis or scale arguments, deliberately, and for a different reason than FBX's: the glTF
        # spec fixes +Y up / metres and Blender's importer converts from it. Passing a conversion
        # here would apply it twice.
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        args = dict(FBX_IMPORT_ARGS)
        if "useCustomNormals" in params:
            args["use_custom_normals"] = take_bool(params, "useCustomNormals", default=True)
        # DEFAULT STAYS FALSE so nothing silently changes for callers who have been importing static
        # meshes; what changed is that the response now SAYS an animation was left behind.
        args["use_anim"] = take_bool(params, "importAnimation", default=False)
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

    new_actions = [a.name for a in bpy.data.actions if a not in actions_before]
    animated = [o.name for o in created
                if getattr(o, "animation_data", None) and o.animation_data.action]

    warnings = []
    if not is_gltf and not args.get("use_anim") and not new_actions:
        warnings.append(
            "animation was NOT imported - import_mesh passes use_anim:false to the FBX importer "
            "by default, so any animation in this file was dropped. Pass importAnimation:true to "
            "keep it. glTF imports animation unconditionally, so the same call behaves differently "
            "by extension; that is now said out loud rather than left to be discovered.")
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
        # MEASURED, NOT REQUESTED. use_anim:true on a file with no animation creates
        # nothing, so asking what was requested answers a different question.
        "animationImported": bool(new_actions),
        "actionsCreated": new_actions,
        "objectsWithAction": animated,
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
    # BEFORE THE EXPORT RUNS. A control character in the path survives every guard here and then
    # collapses inside the exporter, which comes back as a bare RuntimeError carrying a Python
    # traceback - measured on 5.0.1 with an FBX export. Every other refusal in this addon is a
    # sentence.
    check_output_path(raw, path, "exported")
    _check_format(path, "export_mesh", _EXPORT_FORMATS)

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
        # WHAT THE EXPORTER ACCEPTS IS NOT WHAT THIS OP CAN REACH. object_types is a FILTER applied
        # to the selection, and the selection this op builds is meshes (want_mesh=True, or every
        # MESH in the view layer) plus the armatures their modifiers point at - nothing else is ever
        # selected. So EMPTY, CAMERA, LIGHT and OTHER were accepted, forwarded, and could not change
        # the output by a single byte. That is a key the op declares and never really reads, which
        # this addon refuses rather than tolerates.
        #
        # The comment a few lines down already makes this exact point for ARMATURE - "must be in the
        # SELECTION, not merely allowed by object_types" - and the other four were never revisited
        # in that light.
        reachable = {"MESH", "ARMATURE"}
        valid = {"EMPTY", "CAMERA", "LIGHT", "ARMATURE", "MESH", "OTHER"}
        types = [str(t).upper() for t in types]
        bad = [t for t in types if t not in valid]
        if bad:
            raise MifOpError("unknown objectTypes %s. Accepted: %s. NOTHING was written."
                             % (", ".join("'%s'" % b for b in bad), ", ".join(sorted(valid))))
        unreachable = [t for t in types if t not in reachable]
        if unreachable:
            raise MifOpError(
                "objectTypes %s cannot affect this export. object_types filters the SELECTION, and "
                "export_mesh selects meshes and the armatures their modifiers point at - never a "
                "camera, light or empty - so naming them would have changed nothing while looking "
                "like it did. Use %s. Exporting a whole scene including lights and cameras is a "
                "different job than export_mesh. NOTHING was written."
                % (", ".join("'%s'" % u for u in unreachable), " or ".join(sorted(reachable))))
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

    # THE OTHER DIRECTION OF A CHECK THIS FILE ALREADY MAKES. import_mesh REFUSES a non-identity
    # transform on the way in; export was silent on the way out, which is the costly half. A
    # non-identity object transform means the pivot has moved and the loc/rot/scale is NOT
    # baked into the mesh data, so the receiving engine applies it again on top - a mesh that
    # is double-scaled or offset from its own origin, discovered in Unreal rather than here.
    # isIdentityTransform was ALREADY in the response, per object, inside `exported`; nothing
    # raised it to where somebody reads it. A note rather than a refusal, because exporting a
    # transformed object is legitimate when you mean it - apply_transform is the fix when you
    # do not.
    moved = [o["name"] for o in out["exported"] if not o.get("isIdentityTransform", True)]
    if moved:
        out["nonIdentityTransformWarning"] = (
            "%s do NOT have an identity transform, so their loc/rot/scale is not baked into "
            "the mesh data and the importing engine will apply it AGAIN on top of what is in "
            "the file. Call apply_transform first if the mesh should arrive where it looks "
            "like it is. The file was written either way."
            % ", ".join("'%s'" % n for n in moved))

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
    require_editable(obj, "reslot")
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


# Blender's hard maximum UV layers per mesh. MEASURED on 3.6.23, 4.2.17, 4.4.0 and 5.0.1 -
# all four accept 8 and return None from new() on the ninth, silently.
_UV_LAYER_CAP = 8


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
    # THE CAP WAS ALREADY GUARDED HERE and this check is unchanged in substance - only the literal
    # 8 has moved into _UV_LAYER_CAP, which is measured rather than remembered (3.6.23, 4.2.17,
    # 4.4.0 and 5.0.1 all accept 8). Two spellings of the same number in one file is how one of
    # them goes stale.
    if len(before) >= _UV_LAYER_CAP and (not layer_name or layer_name not in before):
        raise MifOpError(
            "'%s' already has %d UV layers and Blender's limit is %d, so there is no room for "
            "another. Pass uvLayer with one of the existing names plus replace:true. Layers: %s"
            % (obj.name, len(before), _UV_LAYER_CAP, before))

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

    # Defaulted here because it is only set on the LIGHTMAP branch, and a response field that
    # exists on some paths and raises NameError on others is its own bug.
    lightmap_div = None

    # The layer has to exist BEFORE the unwrap, and be the ACTIVE one, or the operator writes
    # into whichever channel happened to be active - which is how a lightmap lands on top of
    # the base UVs and nobody notices until the bake.
    # A BACKSTOP, NOT THE CAP CHECK - that one is above and pre-dates this, and it is what actually
    # fires on a full mesh. This guards the DIFFERENT failure: uv_layers.new() signals "no room" by
    # RETURNING None rather than raising, measured on all four builds, so any path that reaches a
    # new() the count check did not cover would take .name off None and produce an AttributeError
    # from the middle of an op that has already entered edit mode.
    #
    # Said plainly because it would be easy to write this up as a fix for something that was
    # already handled: the cap was covered, the None return was not.
    def _new_layer(name):
        if len(mesh.uv_layers) >= _UV_LAYER_CAP:
            raise MifOpError(
                "'%s' already has %d UV layers, which is Blender's maximum - uv_layers.new() "
                "returns None past it rather than failing, so there is no room for '%s'. Remove "
                "one, or pass uvLayer with an existing name plus replace:true. Layers: %s. NOTHING "
                "was changed." % (obj.name, len(mesh.uv_layers), name, ", ".join(before)))
        made = mesh.uv_layers.new(name=name)
        if made is None:
            raise MifOpError("uv_layers.new() returned None for '%s' on '%s' with %d layer(s) "
                             "present, which is how Blender reports 'no room' - it does not raise. "
                             "NOTHING was changed." % (name, obj.name, len(mesh.uv_layers)))
        return made

    created = None
    if layer_name:
        if layer_name in before:
            target = mesh.uv_layers[layer_name]
        else:
            target = _new_layer(layer_name)
            created = target.name
    elif not before:
        target = _new_layer("UVMap")
        created = target.name
    else:
        target = mesh.uv_layers.active or mesh.uv_layers[0]
    mesh.uv_layers.active = target
    active_name = target.name

    # A FINGERPRINT OF EVERY OTHER LAYER, TAKEN BEFORE THE UNWRAP.
    #
    # THE DEFAULT FAILURE OF A SECOND UV CHANNEL IS WRITING INTO THE FIRST ONE. uv_layers.new()
    # does NOT make the new layer active - measured on 3.6.23, 4.2.17, 4.4.0 and 5.0.1, where
    # active_index stayed 0 after creating a layer - and every UV operator writes to the ACTIVE
    # layer. Miss the `uv_layers.active = target` line above and lightmap_pack silently repacks the
    # base colour UVs while the layer you asked for stays empty. Nothing raises, the response looks
    # correct, and it is found at bake time.
    #
    # This op sets active correctly. What it could not do until now is PROVE it: activeLayer and
    # createdLayer both report what was INTENDED, and neither can disagree with itself. So the
    # other layers are fingerprinted before and after, and the answer is a measurement.
    #
    # foreach_get, not a Python loop - it is a C-level bulk copy, so this costs almost nothing on a
    # mesh where a per-loop comprehension would be the expensive part of the whole op.
    def _uv_fingerprint(layer):
        n = len(layer.data)
        if not n:
            return (0, b"")
        buf = array.array("f", [0.0]) * (2 * n)
        layer.data.foreach_get("uv", buf)
        return (n, hashlib.sha256(buf.tobytes()).digest())

    others_before = {uv.name: _uv_fingerprint(uv) for uv in mesh.uv_layers
                     if uv.name != active_name}

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
            # PREF_MARGIN_DIV IS CAPPED AT 1.0, read off its own rna_type on 3.6.23, 4.2.17, 4.4.0
            # and 5.0.1 - hard_min 0.001, hard_max 1.0 on every one of them.
            #
            # This used to pass `margin * 100.0`. islandMargin is validated to [0, 1), so anything
            # from 0.01 upward multiplied past the cap and was clamped by RNA to exactly 1.0 -
            # INCLUDING this op's own default of 0.02. The entire declared range above 0.01 was one
            # value, and the response echoed the requested margin back as though it had applied.
            # A parameter that reports itself applied and does nothing is worse than one that is
            # missing, because nobody goes looking.
            #
            # Passed through directly now, floored at the operator's own minimum. What was actually
            # handed over is reported, so a caller can see it rather than infer it.
            lightmap_div = max(0.001, min(1.0, margin))
            bpy.ops.uv.lightmap_pack(PREF_MARGIN_DIV=lightmap_div)
        else:
            # fill_holes IS PINNED, and that is not tidiness. Its DEFAULT FLIPPED at 4.4:
            # True on 3.6.23 and 4.2.17, False on 4.4.0 and 5.0.1 - read off the operator's own
            # rna_type on each build rather than from release notes. Left unpassed, the identical
            # call fills holes on the LTS builds and does not on the newer ones, so the same op on
            # the same mesh produces two different layouts depending on which Blender is running,
            # with nothing anywhere saying so.
            #
            # `method` was already pinned to ANGLE_BASED, which is why THAT flip - ANGLE_BASED to
            # CONFORMAL, same version - never bit. This is the same lesson, one argument over.
            #
            # Pinned to True, the behaviour this op had on the builds it was written and tested
            # against. Making it a parameter is filed rather than done, so the change here is a
            # version-independence fix and not a new feature.
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=margin, fill_holes=True,
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
            # THE isinstance CHECK DOES NOT EXCLUDE NaN, because a NaN IS a float. A NaN scale or
            # offset lands on the UV layer and every coordinate it touches becomes nan - and the
            # unwrap that follows reports success over a layer that is now unusable.
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return tuple(finite_floats([v, v], "uvTransform.%s" % key))
            if not isinstance(v, (list, tuple)) or len(v) != 2 \
                    or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in v):
                raise MifOpError("uvTransform.%s must be a number or [u,v]; got %r" % (key, v))
            return tuple(finite_floats(v, "uvTransform.%s" % key))

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
    _clobbered = sorted(n for n, fp in others_before.items()
                        if n in mesh.uv_layers and _uv_fingerprint(mesh.uv_layers[n]) != fp)
    _active_render = next((uv.name for uv in mesh.uv_layers if uv.active_render), None)
    return {
        # SHARED MESH DATA - see ops_common.shared_data_note. This edit lands on every
        # object sharing the datablock, and nothing here said so.
        **shared_data_note(obj),
        "object": obj.name,
        "method": method,
        "uvLayersBefore": before,
        "uvLayersAfter": after,
        "activeLayer": active_name,
        "createdLayer": created,
        # MEASURED, NOT ASSERTED. See the fingerprint above: this is the difference between
        # "the op meant to write to the layer you named" and "no other layer moved".
        "otherLayersUnchanged": not _clobbered,
        "layersClobbered": _clobbered,
        # WHICH LAYER THE RENDERER READS FOR TEXTURES, which is not the same as the active one and
        # is what a caller adding a lightmap channel needs to see stayed put. Unreal reads the base
        # colour from active_render and the lightmap from a second channel; if a lightmap pass has
        # moved active_render, the material is now sampling the lightmap layout.
        "activeRenderLayer": _active_render,
        "angleLimitDeg": angle_deg if method == "SMART" else None,
        "islandMargin": margin,
        # WHAT THE OPERATOR ACTUALLY GOT, on the one path where it is not the margin
        # itself. None on every other method. Reported because this value was silently
        # pinned at its 1.0 cap for every islandMargin from 0.01 up, the default among
        # them, while this response echoed the request back.
        "lightmapMarginDiv": lightmap_div,
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
    # kind=str, because .lower() on whatever arrived raised AttributeError out of the op for
    # a dict - take() with kind refuses instead, which is the handler's contract.
    mode = (take(params, "mode", default="geometry", kind=str) or "geometry").lower()

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
    # A LINKED MESH CANNOT BE CLEANED. Measured on 5.0.1: this ran, changed nothing, and answered
    # ok with a full before/after - the quiet half of the failure the transform had loudly.
    require_editable(obj, "clean")

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
    # SHARED MESH DATA. See ops_common.shared_data_note - this edit lands on every object
    # that shares the datablock, which is what a linked duplicate is for and which nothing
    # in this response said.
    out.update(shared_data_note(obj))
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


_UCX_KEYS = {"object", "name", "index", "worldSpace", "maxVertices", "collection", "prefix"}

# Unreal's collision-mesh naming. UCX_<RenderMeshName>_## alongside the render mesh in one FBX, and
# the importer attaches it as a convex collision primitive.
#
# STATED AS THE CONVENTION, NOT AS SOMETHING THIS ADDON VERIFIED. Everything else in this op was
# measured on four real Blenders; whether a given Unreal build parses a given name is a UE-side
# behaviour that cannot be checked from here, and this repo's rule is to say so rather than to imply
# a test that never ran.
_UCX_PREFIX = "UCX_"

# UE's convex collision limit. Above this the importer rejects or simplifies the hull depending on
# version, so it is reported rather than silently exceeded.
_UE_HULL_VERT_LIMIT = 255


def _hull_audit(mesh):
    """The seven measurements that separate a real hull from one that merely looks like one.

    A BROKEN HULL HAS THE RIGHT VERTEX COUNT. Measured on Suzanne, 4.4.0 and 5.0.1: the recipe every
    tutorial gives - bm.from_mesh(source), convex_hull, delete geom_interior - returns 66 vertices,
    and so does the correct one. Identical count, and the broken one carries 51 NON-MANIFOLD edges,
    4 boundary edges, Euler 16 instead of 2, 22 convexity violations and a volume inflated from
    3.5321 to 3.8298. Nothing raises. Anything that checks "did I get a hull with a sensible number
    of verts" passes on the wreck.

    So the postcondition is these seven, re-read from the finished MESH DATABLOCK rather than from
    the bmesh that built it - a bmesh reports what was constructed, the datablock reports what
    survived being written.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
        nonmanifold = sum(1 for e in bm.edges if len(e.link_faces) > 2)
        loose = sum(1 for v in bm.verts if not v.link_edges)
        euler = len(bm.verts) - len(bm.edges) + len(bm.faces)
        volume = bm.calc_volume(signed=True)
        # CONVEXITY, MEASURED RATHER THAN ASSUMED: every vertex must lie behind every face plane.
        # O(V*F), which is 8448 dot products for a 66-vertex hull - cheap enough to run every time,
        # and the only check that catches a hull that is closed and manifold and still concave.
        span = 1.0
        if bm.verts:
            span = max((max(v.co[i] for v in bm.verts) - min(v.co[i] for v in bm.verts))
                       for i in range(3)) or 1.0
        eps = max(1e-5 * span, 1e-7)
        violations = 0
        for face in bm.faces:
            normal, point = face.normal, face.verts[0].co
            for vert in bm.verts:
                if normal.dot(vert.co - point) > eps:
                    violations += 1
        return {
            "vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
            "boundaryEdges": boundary, "nonManifoldEdges": nonmanifold, "looseVertices": loose,
            "eulerCharacteristic": euler, "volume": round(float(volume), 6),
            "convexityViolations": violations,
        }
    finally:
        bm.free()


def _build_hull(points, merge_dist):
    """A bmesh holding the convex hull of a POINT SET. Caller frees it.

    POINTS ONLY - never bm.from_mesh(source). That is the whole finding and it is not a nicety:
    feeding convex_hull a bmesh that already carries the source's edges and faces produces a hull
    with the source's topology tangled through it, and the result is broken on ANY concave mesh
    while looking entirely plausible. See _hull_audit.

    THE THREE RESULT KEYS OVERLAP AND NONE IS RELIABLY POPULATED. geom_interior and geom_unused
    were the same four vertices on a convex test case and 441-and-empty on Suzanne, so all three are
    collected and DEDUPED BY id() - deleting the same vertex twice is a crash, and trusting one key
    alone leaves interior geometry behind.

    context='VERTS' is the only delete context that removes anything: measured, EDGES, FACES,
    EDGES_FACES, FACES_ONLY and FACES_KEEP_BOUNDARY all leave the mesh untouched with no exception
    and no warning. Five of six spellings silently do nothing.
    """
    bm = bmesh.new()
    for co in points:
        bm.verts.new(co)
    bm.verts.ensure_lookup_table()
    if merge_dist > 0.0:
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=merge_dist)
        bm.verts.ensure_lookup_table()
    result = bmesh.ops.convex_hull(bm, input=bm.verts)
    seen, doomed = set(), []
    for key in ("geom_interior", "geom_unused", "geom_holes"):
        for element in result.get(key, []):
            if id(element) not in seen:
                seen.add(id(element))
                doomed.append(element)
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def op_create_collision_hull(params):
    """A convex hull collision mesh, named for Unreal's UCX_ convention.

    WHY THIS IS NOT add_collision. That op makes an object a RIGID BODY COLLIDER - a physics
    setting, evaluated by Blender's own simulation. This builds a separate MESH that travels in the
    FBX beside the render mesh and becomes collision in the engine. Same word, unrelated jobs, and
    the addon had the physics one and not this one.

    THE TUTORIAL RECIPE IS WRONG AND LOOKS RIGHT. bm.from_mesh(source) then convex_hull then delete
    geom_interior gives, on Suzanne, a mesh with the SAME 66 vertices as the correct hull and also
    51 non-manifold edges, 4 boundary edges, Euler 16 instead of 2, 22 convexity violations and a
    volume inflated by 8%. Nothing raises. Measured on 4.4.0 and 5.0.1. This builds from POINTS ONLY
    and then AUDITS the result, refusing rather than returning a wreck.

    DEGENERATE INPUT PRODUCES SILENT GARBAGE, never an exception - collinear points give an empty
    mesh, coplanar points give a zero-volume sheet - which is why the audit is a refusal and not a
    warning.

    NAME COLLISIONS ARE A UCX KILLER. bpy.data.objects.new on a taken name returns 'UCX_Rock_00.001'
    and that dot-suffix survives FBX export, so the engine parses the base name as 'Rock_00.001' and
    attaches the hull to nothing. Refused rather than uniquified.

    params:
      object / name (str)      the render mesh to hull. Required.
      index (int)              the ## suffix. Default 0, giving UCX_<object>_00.
      name (str)               override the whole object name, bypassing the convention
      prefix (str)             default 'UCX_'
      worldSpace (bool)        hull the world-space points and leave the object at the origin,
                               instead of hulling local points and copying the source transform
      maxVertices (int)        simplify to at most this many by merging and RE-HULLING, which stays
                               convex by construction. Decimating a hull does not.
      collection (str)         where to link it. Default the source's own collection.
    """
    reject_unknown(params, _UCX_KEYS, "create_collision_hull")
    # EVERY CHEAP ARGUMENT CHECKED BEFORE THE MESH IS TOUCHED. Reading the source's geometry to
    # report a negative index is work done on the way to refusing, and it also makes the guards
    # untestable without a real mesh - which is how three of these went unexercised until an offline
    # check tried them. Same ordering rule as ray_cast and face_info.
    src_name = take(params, "object", required=True, kind=str)
    world = take_bool(params, "worldSpace", default=False)
    index = take_int(params, "index", default=0)
    if index < 0:
        raise MifOpError("index cannot be negative, got %d. NOTHING was created." % index)
    prefix = take(params, "prefix", default=_UCX_PREFIX, kind=str)
    override = take(params, "name", kind=str)
    max_verts_early = take_int(params, "maxVertices", default=None)
    if max_verts_early is not None and max_verts_early < 4:
        raise MifOpError("maxVertices must be at least 4 to enclose a volume, got %d. NOTHING was "
                         "created." % max_verts_early)
    coll_early = take(params, "collection", kind=str)
    if coll_early and bpy.data.collections.get(coll_early) is None:
        known = sorted(c.name for c in bpy.data.collections)[:25]
        raise MifOpError("no collection named '%s'. Present: %s. NOTHING was created."
                         % (coll_early, ", ".join(known) if known else "<none>"))

    src = get_object(src_name, want_mesh=True)
    mesh = src.data
    if len(mesh.vertices) < 4:
        raise MifOpError("'%s' has %d vertice(s). A convex hull needs at least 4 non-coplanar "
                         "points to enclose any volume at all - fewer produces an empty or flat "
                         "result with no error. NOTHING was created."
                         % (src.name, len(mesh.vertices)))
    hull_name = override or ("%s%s_%02d" % (prefix, src.name, index))

    # REFUSED, NOT UNIQUIFIED. See the docstring: Blender's .001 suffix silently breaks the naming
    # the whole convention rests on, and it survives the export.
    if hull_name in bpy.data.objects:
        raise MifOpError("an object named '%s' already exists. Blender would silently rename this "
                         "to '%s.001', and that suffix survives FBX export - the engine then parses "
                         "the base name wrongly and attaches the hull to nothing. Pass a different "
                         "index, or delete the existing one. NOTHING was created."
                         % (hull_name, hull_name))

    max_verts = max_verts_early
    coll = bpy.data.collections.get(coll_early) if coll_early else None

    matrix = src.matrix_world
    points = [(matrix @ v.co) if world else v.co.copy() for v in mesh.vertices]
    span = max((max(p[i] for p in points) - min(p[i] for p in points)) for i in range(3)) or 1.0

    bm = _build_hull(points, merge_dist=span * 1e-6)
    simplified = None
    try:
        # SIMPLIFY BY MERGING AND RE-HULLING, not by decimating. A decimated hull is no longer
        # convex - measured: ratio 0.1 gave 24 convexity violations at maxdist 0.017 - whereas the
        # hull of a reduced POINT SET is convex by construction. Doubling the merge distance is a
        # crude search and a cheap one, and it terminates because each pass strictly reduces or the
        # loop gives up.
        if max_verts is not None and len(bm.verts) > max_verts:
            dist = span * 1e-4
            for _ in range(24):
                keep = [v.co.copy() for v in bm.verts]
                bm.free()
                bm = _build_hull(keep, merge_dist=dist)
                if len(bm.verts) <= max_verts:
                    break
                dist *= 2.0
            simplified = len(bm.verts)
        hull_mesh = bpy.data.meshes.new(hull_name)
        bm.to_mesh(hull_mesh)
    finally:
        bm.free()

    hull_obj = bpy.data.objects.new(hull_name, hull_mesh)
    (coll or (src.users_collection[0] if src.users_collection
              else bpy.context.scene.collection)).objects.link(hull_obj)
    if not world:
        hull_obj.matrix_world = src.matrix_world.copy()

    audit = _hull_audit(hull_mesh)
    # THE REFUSAL IS THE POINT. A hull that fails any of these is worse than no hull: it imports,
    # it looks like collision, and things fall through it.
    failures = []
    if audit["boundaryEdges"]:
        failures.append("%d boundary edge(s) - the hull is not closed" % audit["boundaryEdges"])
    if audit["nonManifoldEdges"]:
        failures.append("%d non-manifold edge(s)" % audit["nonManifoldEdges"])
    if audit["looseVertices"]:
        failures.append("%d loose vertice(s)" % audit["looseVertices"])
    if audit["eulerCharacteristic"] != 2:
        failures.append("Euler characteristic %d, not 2 - leftover interior geometry"
                        % audit["eulerCharacteristic"])
    if audit["volume"] <= 1e-9:
        failures.append("volume %g - the points are collinear or coplanar, so this encloses nothing"
                        % audit["volume"])
    if audit["convexityViolations"]:
        failures.append("%d convexity violation(s) - vertices lie OUTSIDE the face planes"
                        % audit["convexityViolations"])
    if failures:
        bpy.data.objects.remove(hull_obj, do_unlink=True)
        bpy.data.meshes.remove(hull_mesh)
        raise MifOpError("the hull built from '%s' failed its audit and was REMOVED rather than "
                         "returned: %s. A broken hull imports, looks like collision, and things "
                         "fall through it. Measurements: %s"
                         % (src.name, "; ".join(failures), audit))

    over = audit["vertices"] > _UE_HULL_VERT_LIMIT
    return {
        "ok": True,
        "object": hull_obj.name,
        "source": src.name,
        "space": "WORLD" if world else "LOCAL (source transform copied)",
        "collection": next((c.name for c in bpy.data.collections if hull_obj.name in c.objects),
                           bpy.context.scene.collection.name),
        "simplifiedTo": simplified,
        "audit": audit,
        "sourceVertices": len(mesh.vertices),
        "withinEngineLimit": not over,
        "note": ("%d vertices is above the %d a convex collision hull is usually limited to - pass "
                 "maxVertices to reduce it." % (audit["vertices"], _UE_HULL_VERT_LIMIT))
        if over else None,
        "namingNote": ("UCX_<RenderMesh>_## is the engine-side convention this name follows. That "
                       "half is NOT verified here - everything else in this response was measured "
                       "on this Blender, but whether a given engine build parses a given name "
                       "cannot be checked from inside Blender."),
    }

_RENAME_KEYS = {"object", "name", "to", "newName", "renameData", "allowCollision"}
_VCOL_KEYS = {"object", "name", "color", "domain", "dataType", "faces", "makeActive",
              "makeRender"}
_STATS_KEYS = {"object", "name", "evaluated"}

# Blender's ID name limit on 3.6 through 4.4. It rose at 5.0 - a 200-character name truncates to 63
# on the older three and is stored whole on 5.0.1 - which means two names differing only past
# character 63 collide on one build and stay distinct on another. Measured, not remembered.
_ID_NAME_LIMIT = 63


def op_rename_object(params):
    """Rename an object, refusing a collision instead of letting Blender resolve it.

    THE COLLISION RESOLVES IN OPPOSITE DIRECTIONS ACROSS THE 3.6 / 4.x LINE, and that is not a
    curiosity - it is silent corruption of an object the caller never mentioned.

    Measured on all four builds. With an existing object called 'Alpha', setting another object's
    name to 'Alpha' gives:

        3.6.23              the RENAMER takes 'Alpha' and the INCUMBENT becomes 'Alpha.001'
        4.2 / 4.4 / 5.0     the incumbent keeps 'Alpha' and the RENAMER becomes 'Alpha.001'

    So the same script renames a different object depending on the Blender running it, and on 3.6 it
    quietly renames something nobody asked it to touch - which then breaks every STRING reference to
    that object elsewhere in the file. Refusing makes the behaviour identical everywhere.

    THE DATA NAME DOES NOT FOLLOW. After obj.name = 'Renamed', obj.data.name is still whatever it
    was - objects and meshes live in separate namespaces. A mesh still named after the old object
    survives into the FBX and confuses anybody reading it later, so renameData defaults ON.

    POINTERS SURVIVE A RENAME AND STRINGS DO NOT. Modifier .object, constraint .target and driver
    variable targets are pointers and are unaffected - verified live. What breaks is anything
    holding the NAME: a vertex group named after a bone, a shape key driver expression, an exporter
    convention like UCX_<name>_00. Those are reported so the caller can see what may need following.

    params:
      object (str)                which object. Required.
      to / newName / name (str)   the new name. Required.
      renameData (bool)           rename the object's DATA to match. Default true.
      allowCollision (bool)       let Blender resolve a clash its own way. Default false, and
                                  turning it on means accepting a different outcome per version.
    """
    reject_unknown(params, _RENAME_KEYS, "rename_object")
    src = take(params, "object", required=True, kind=str)
    wanted = take(params, "to", "newName", "name", required=True, kind=str)
    wanted = str(wanted)
    if not wanted.strip():
        raise MifOpError("the new name is empty. NOTHING was changed.")
    allow = take_bool(params, "allowCollision", default=False)
    rename_data = take_bool(params, "renameData", default=True)

    obj = get_object(src)
    require_editable(obj, "rename")
    if obj.name == wanted:
        return {"ok": True, "object": obj.name, "renamedFrom": obj.name, "changed": False,
                "dataName": getattr(obj.data, "name", None),
                "note": "the object is already called that - nothing to do."}

    clash = bpy.data.objects.get(wanted)
    if clash is not None and clash is not obj and not allow:
        raise MifOpError(
            "an object named '%s' already exists, and letting Blender resolve that does DIFFERENT "
            "things on different builds: on 3.6 the RENAMED object takes the name and the existing "
            "'%s' is silently renamed to '%s.001' - corrupting an object you did not ask to touch - "
            "while on 4.2 and later this object would become '%s.001' instead. Pick another name, "
            "rename the existing one first, or pass allowCollision:true to accept a per-version "
            "outcome. NOTHING was changed." % (wanted, wanted, wanted, wanted))

    if len(wanted) > _ID_NAME_LIMIT and bpy.app.version < (5, 0, 0):
        raise MifOpError(
            "'%s' is %d characters and this Blender (%s) truncates object names at %d, so the name "
            "you get back would not be the one you asked for - and two names differing only past "
            "that point would collide here and not on 5.0. Shorten it. NOTHING was changed."
            % (wanted, len(wanted), bpy.app.version_string, _ID_NAME_LIMIT))

    was = obj.name
    other_before = clash.name if clash is not None else None
    obj.name = wanted
    # THE POSTCONDITION, and it is the whole point on a name assignment: Blender resolves silently,
    # so the only way to know what happened is to read it back.
    if obj.name != wanted:
        raise MifOpError("asked for '%s' and Blender stored '%s' - the name was resolved rather "
                         "than refused." % (wanted, obj.name))
    data_name = None
    if rename_data and getattr(obj, "data", None) is not None:
        try:
            obj.data.name = wanted
            data_name = obj.data.name
        except (AttributeError, TypeError):
            data_name = getattr(obj.data, "name", None)

    stolen = (clash is not None and clash.name != other_before)
    return {
        "ok": True,
        "object": obj.name,
        "renamedFrom": was,
        "changed": True,
        "dataName": data_name if rename_data else getattr(obj.data, "name", None),
        "dataRenamed": bool(rename_data and data_name == wanted),
        # ONLY REACHABLE WITH allowCollision. Reported because on 3.6 this is how an untouched
        # object ends up renamed, and a caller who opted in should still be told it happened.
        "otherObjectRenamed": ({"was": other_before, "now": clash.name} if stolen else None),
        "note": ("pointers survive a rename - modifier targets, constraint targets and driver "
                 "variables all still point at this object. What breaks is anything holding the "
                 "old NAME as a string, such as an exporter convention like UCX_%s_00." % was),
    }


def op_set_vertex_color(params):
    """Write a colour attribute - what games use for masks, wear and blend weights.

    mesh.vertex_colors is the legacy path; color_attributes is the one that exists on every build
    this addon supports, so there is no branch here. BYTE_COLOR on the CORNER domain is the default
    because it is what Blender's own legacy call produced and what survives an FBX round trip.

    THE NAME CAN SILENTLY FAIL. color_attributes.new() with an over-long name TRUNCATES to 63
    characters on 3.6 and RETURNS None on 4.2, 4.4 and 5.0 - measured, with no exception on any of
    them. The next line taking .name off None is an AttributeError from the middle of an op, so the
    length is checked first and the None is guarded anyway.

    params:
      object (str)          required, must be a MESH
      name (str)            attribute name. Default 'Col'.
      color [r,g,b] or [r,g,b,a]   linear float. Required unless the attribute already exists.
      domain (str)          CORNER (per face-corner, the default) or POINT (per vertex)
      dataType (str)        BYTE_COLOR (default) or FLOAT_COLOR
      faces (list[int])     colour only these faces. CORNER domain only.
      makeActive (bool)     make it the active colour attribute. Default true.
      makeRender (bool)     make it the one the renderer uses. Default true.
    """
    reject_unknown(params, _VCOL_KEYS, "set_vertex_color")
    src = take(params, "object", required=True, kind=str)
    name = str(take(params, "name", default="Col", kind=str))
    if len(name) > _ID_NAME_LIMIT:
        raise MifOpError("the attribute name is %d characters and Blender's limit is %d. Past it, "
                         "3.6 silently TRUNCATES and 4.2+ silently returns None - neither raises, "
                         "so a longer name fails differently on different builds. NOTHING was "
                         "created." % (len(name), _ID_NAME_LIMIT))
    domain = str(take(params, "domain", default="CORNER", kind=str)).upper()
    if domain not in ("CORNER", "POINT"):
        raise MifOpError("domain must be CORNER (per face-corner) or POINT (per vertex), got '%s'. "
                         "NOTHING was created." % domain)
    dtype = str(take(params, "dataType", default="BYTE_COLOR", kind=str)).upper()
    if dtype not in ("BYTE_COLOR", "FLOAT_COLOR"):
        raise MifOpError("dataType must be BYTE_COLOR or FLOAT_COLOR, got '%s'. NOTHING was "
                         "created." % dtype)
    faces = params.get("faces")
    if faces is not None:
        if domain != "CORNER":
            raise MifOpError("'faces' selects face corners, so it needs domain CORNER - got %s. "
                             "NOTHING was created." % domain)
        if not isinstance(faces, (list, tuple)):
            raise MifOpError("'faces' must be a list of face indices, got %s. NOTHING was created."
                             % type(faces).__name__)
    raw = params.get("color")
    if raw is not None:
        if not isinstance(raw, (list, tuple)) or len(raw) not in (3, 4):
            raise MifOpError("'color' must be [r,g,b] or [r,g,b,a], got %r. NOTHING was created."
                             % (raw,))
        colour = tuple(float(c) for c in raw) + ((1.0,) if len(raw) == 3 else ())
    else:
        colour = None

    obj = get_object(src, want_mesh=True)
    mesh = obj.data
    if not mesh.polygons:
        raise MifOpError("'%s' has no faces, so there are no corners to colour." % obj.name)

    existed = name in mesh.color_attributes
    if not existed:
        if colour is None:
            raise MifOpError("'%s' has no colour attribute named '%s' and no 'color' was given, so "
                             "there is nothing to create it with. NOTHING was created."
                             % (obj.name, name))
        attr = mesh.color_attributes.new(name=name, type=dtype, domain=domain)
        if attr is None:
            raise MifOpError("color_attributes.new() returned None for '%s' - Blender signals "
                             "failure that way rather than raising. NOTHING was created." % name)
    else:
        attr = mesh.color_attributes[name]
        if attr.domain != domain or attr.data_type != dtype:
            raise MifOpError("'%s' already exists as %s/%s and was asked for as %s/%s. Changing "
                             "either would rewrite every value; delete it first if that is what "
                             "you want. NOTHING was changed."
                             % (name, attr.domain, attr.data_type, domain, dtype))

    written = 0
    if colour is not None:
        if faces is not None:
            highest = len(mesh.polygons) - 1
            bad = sorted(i for i in faces if not isinstance(i, int) or i < 0 or i > highest)
            if bad:
                raise MifOpError("face index %s is out of range - '%s' has %d face(s). The "
                                 "attribute WAS created." % (bad[0], obj.name, len(mesh.polygons)))
            for fi in faces:
                for li in mesh.polygons[fi].loop_indices:
                    attr.data[li].color = colour
                    written += 1
        else:
            for entry in attr.data:
                entry.color = colour
            written = len(attr.data)

    if take_bool(params, "makeActive", default=True):
        mesh.color_attributes.active_color = attr
    if take_bool(params, "makeRender", default=True):
        try:
            mesh.color_attributes.render_color_index = mesh.color_attributes.find(name)
        except (AttributeError, TypeError):
            pass
    mesh.update()

    # READ BACK FROM THE MESH, not from the value that was written. A colour written to a
    # BYTE_COLOR attribute is quantised to 8 bits per channel, so what comes back is not what went
    # in, and reporting the request would hide that entirely.
    sample = list(attr.data[0].color) if len(attr.data) else None
    return {
        "ok": True,
        "object": obj.name,
        "attribute": attr.name,
        "created": not existed,
        "domain": attr.domain,
        "dataType": attr.data_type,
        "elements": len(attr.data),
        "elementsWritten": written,
        "requestedColor": rnd(list(colour)) if colour else None,
        "storedColor": rnd(sample) if sample else None,
        "quantised": bool(colour and sample and
                          any(abs(a - b) > 1e-4 for a, b in zip(colour, sample))),
        "activeColor": getattr(getattr(mesh.color_attributes, "active_color", None), "name", None),
        "note": ("BYTE_COLOR stores 8 bits per channel, so storedColor differs from what was asked "
                 "for. That is the format doing its job, not a failure - use FLOAT_COLOR if the "
                 "exact value matters more than the file size.")
        if (colour and sample and any(abs(a - b) > 1e-4 for a, b in zip(colour, sample))) else None,
    }


def op_mesh_stats(params):
    """Counts and a world bounding box - computed from vertices, never from bound_box.

    obj.bound_box IS CACHED AND STALE. Measured on all four builds: move a vertex to z=50 and
    bound_box[6] still reads the old extent. mesh.update() does NOT refresh it - only
    bpy.context.view_layer.update() does. So a box read straight off bound_box after any edit is
    quietly wrong, which is the worst kind of wrong for a number people position things against.

    This computes the box from the vertices directly, so there is nothing to be stale.

    THE EVALUATED COUNTS ARE THE ONES THAT MATTER FOR EXPORT. A mesh with a Subsurf modifier has a
    base count and a rendered count that differ by an order of magnitude, and both are reported
    rather than one being chosen for the caller.

    params:
      object (str)      required, must be a MESH
      evaluated (bool)  include the modifier-result counts and box. Default true.
    """
    reject_unknown(params, _STATS_KEYS, "mesh_stats")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    want_eval = take_bool(params, "evaluated", default=True)
    mesh = obj.data

    def _box(points):
        if not points:
            return None
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        return {"min": rnd(lo), "max": rnd(hi),
                "size": rnd([hi[i] - lo[i] for i in range(3)]),
                "center": rnd([(hi[i] + lo[i]) / 2.0 for i in range(3)])}

    mw = obj.matrix_world
    local_pts = [v.co for v in mesh.vertices]
    world_pts = [mw @ v.co for v in mesh.vertices]
    mesh.calc_loop_triangles()
    out = {
        "ok": True,
        "object": obj.name,
        "base": {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "triangles": len(mesh.loop_triangles),
            "loops": len(mesh.loops),
        },
        "bboxLocal": _box(local_pts),
        "bboxWorld": _box(world_pts),
        "uvLayers": [uv.name for uv in mesh.uv_layers],
        "colorAttributes": [a.name for a in mesh.color_attributes],
        "materialSlots": [s.material.name if s.material else None for s in obj.material_slots],
        "modifiers": [m.name for m in obj.modifiers],
        "bboxNote": ("computed from vertices, NOT from obj.bound_box - that is cached and does not "
                     "refresh after a vertex edit until view_layer.update() runs, so it reports the "
                     "previous extent with nothing to say so."),
    }
    if want_eval and obj.modifiers:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        source = obj.evaluated_get(depsgraph)
        # evaluated_get SILENTLY RETURNS THE UNEVALUATED OBJECT when the object is not in the
        # active view layer's depsgraph, so the identity is checked rather than assumed.
        if source.data is mesh:
            out["evaluated"] = None
            out["evaluatedNote"] = ("evaluated_get returned the BASE mesh - the object is not in "
                                    "the active view layer's depsgraph, so no modifier result "
                                    "exists to measure. This is silent in Blender.")
        else:
            emesh = source.to_mesh()
            try:
                emesh.calc_loop_triangles()
                epts = [source.matrix_world @ v.co for v in emesh.vertices]
                out["evaluated"] = {
                    "vertices": len(emesh.vertices), "edges": len(emesh.edges),
                    "faces": len(emesh.polygons), "triangles": len(emesh.loop_triangles),
                    "bboxWorld": _box(epts),
                }
            finally:
                source.to_mesh_clear()
    elif want_eval:
        out["evaluated"] = None
        out["evaluatedNote"] = "no modifiers, so the evaluated mesh is the base mesh."
    return out


_SETUV_KEYS = {"object", "name", "layer", "active", "rename", "remove"}


def _uv_layer_map(mesh):
    """Layer name -> index. The INDEX is what an engine reads, so it is the thing worth watching."""
    return dict((layer.name, i) for i, layer in enumerate(mesh.uv_layers))


def op_set_uv_layer(params):
    """Choose the ACTIVE UV layer, rename one, or remove one - none of which was possible.

    THE ACTIVE LAYER IS THE ONE EVERYTHING WRITES TO. uv_info reports activeLayer and the only way
    to change it was uv_unwrap, which sets it as a side effect of unwrapping - so selecting a layer
    meant re-unwrapping it and destroying the UVs it held. That is the file's own trap seen from the
    other side: uv_unwrap carries a comment about active_index staying 0 after creating a layer,
    because every UV operator and every bake writes to the ACTIVE layer, and missing that line makes
    a lightmap pass silently repack the base colour UVs.

    REMOVING A LAYER SHIFTS EVERY LATER LAYER'S INDEX. Measured on 3.6.23 and 5.0.1: with UVMap, A,
    B, C, removing A moves B from 2 to 1 and C from 3 to 2. Unreal's lightmap coordinate index is an
    INDEX, not a name, so removing an earlier layer silently repoints what the engine reads at a
    mesh it has already imported. The layers that moved are named in the response.

    A MESH IS LIMITED TO 8 UV LAYERS, which is why removing junk ones matters at all.

    params:
      object / name (str)   required, must be a MESH
      layer (str)           which layer. Required.
      active (bool)         make it the active layer
      rename (str)          a new name; Blender suffixes a clash rather than refusing
      remove (bool)         delete it - excludes active and rename
    """
    reject_unknown(params, _SETUV_KEYS, "set_uv_layer")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    mesh = obj.data
    want = take(params, "layer", required=True, kind=str)

    if not mesh.uv_layers:
        raise MifOpError("'%s' has NO UV layers at all, so there is none to change - uv_unwrap "
                         "makes one. NOTHING was changed." % obj.name)
    layer = mesh.uv_layers.get(str(want))
    if layer is None:
        raise MifOpError("'%s' has no UV layer named '%s'. It has: %s. NOTHING was changed."
                         % (obj.name, want, ", ".join(l.name for l in mesh.uv_layers)))

    remove = take_bool(params, "remove", default=False)
    rename = take(params, "rename", default=None, kind=str)
    make_active = params.get("active")
    if remove and (rename is not None or make_active is not None):
        raise MifOpError("remove deletes the layer, so making it active or renaming it in the same "
                         "call is contradictory. NOTHING was changed.")
    if not remove and rename is None and make_active is None:
        raise MifOpError("nothing to do - pass active, rename or remove. NOTHING was changed.")

    before_map = _uv_layer_map(mesh)
    before_active = mesh.uv_layers.active.name if mesh.uv_layers.active else None

    removed, moved, renamed_to = None, {}, None
    # TRACKED AS AN EVENT, not inferred by comparing NAMES before and after. Renaming the
    # active layer changes that name, so a name comparison called it a switch and the note
    # told a caller the active layer had moved when only its label had.
    switched = False
    if remove:
        if len(mesh.uv_layers) == 1:
            raise MifOpError(
                "'%s' is the only UV layer on '%s'. Removing it leaves the mesh unwrappable - "
                "texturing and lightmap baking both fail until it is unwrapped again. Refused "
                "rather than done quietly. NOTHING was changed." % (want, obj.name))
        removed = layer.name
        mesh.uv_layers.remove(layer)
        after_map = _uv_layer_map(mesh)
        # WHICH LAYERS MOVED, by name, because an engine reads the INDEX.
        moved = dict((n, {"was": before_map[n], "now": after_map[n]})
                     for n in after_map if before_map.get(n) != after_map[n])
    else:
        if rename is not None:
            asked = str(rename)
            if len(asked) > _ID_NAME_LIMIT:
                raise MifOpError("the name is %d characters and Blender truncates at %d, so the "
                                 "layer you get would not be the one you named. NOTHING was "
                                 "changed." % (len(asked), _ID_NAME_LIMIT))
            layer.name = asked
            renamed_to = layer.name
        if make_active is not None and take_bool(params, "active", default=True):
            mesh.uv_layers.active = layer
            switched = True

    after_map = _uv_layer_map(mesh)
    active_now = mesh.uv_layers.active.name if mesh.uv_layers.active else None
    return {
        "ok": True,
        "object": obj.name,
        "layers": [{"name": n, "index": i} for n, i in sorted(after_map.items(),
                                                              key=lambda kv: kv[1])],
        "layerCount": len(after_map),
        "activeLayer": active_now,
        "activeLayerIndex": after_map.get(active_now) if active_now else None,
        "activeChanged": active_now != before_active,
        "removed": removed,
        "renamedTo": renamed_to,
        "nameWasSuffixed": bool(renamed_to and rename is not None and renamed_to != str(rename)),
        # THE FIELD THAT MATTERS ON A REMOVE. Named layers whose INDEX moved, because an engine
        # importing this mesh reads a lightmap coordinate INDEX and will now read a different layer.
        "indicesShifted": moved or None,
        "note": (("removing '%s' moved %s. Unreal's lightmap coordinate index is an INDEX, not a "
                  "name, so anything already importing this mesh now reads a different layer."
                  % (removed, ", ".join("%s %d->%d" % (n, v["was"], v["now"])
                                        for n, v in sorted(moved.items()))))
                 if moved else
                 ("every UV operator and every bake writes to the ACTIVE layer, which is now '%s'."
                  % active_now if switched else None)),
    }

# ---------------------------------------------------------------------------
# mesh_quality
# ---------------------------------------------------------------------------

# Faces smaller than this in square metres are treated as degenerate. Not zero: a float-exact zero
# almost never occurs, and a face of 1e-12 m2 is a defect that exports and renders as nothing.
_DEGENERATE_FACE_AREA = 1e-9
# UV coordinates are compared against 0-1 with a tolerance, because an unwrap that lands a vertex at
# 1.0000001 is not a defect and reporting it as one is how a check gets ignored.
_UV_BOUND_EPS = 1e-4


def _uv_area(face, uv_layer):
    """Signed UV area of one face, by the shoelace formula over its loops."""
    total = 0.0
    loops = face.loops
    n = len(loops)
    for i in range(n):
        x1, y1 = loops[i][uv_layer].uv
        x2, y2 = loops[(i + 1) % n][uv_layer].uv
        total += (x1 * y2) - (x2 * y1)
    return abs(total) * 0.5


def op_mesh_quality(params):
    """Measure the objectively checkable things that get an asset rejected.

    NOT A VERDICT ON THE ART. Everything here is a number a machine can defend: topology, UV bounds,
    texel density spread, applied transforms, loose and degenerate geometry. Whether the thing is
    beautiful is not in scope and is not claimed.

    params:
      object (str, required)   the mesh to measure
      uvLayer (str)            which UV layer to judge; default is the active one
      texelDensityFor (float)  texture size in pixels used for the density figure, default 1024
    """
    reject_unknown(params, {"object", "name", "uvLayer", "texelDensityFor"}, "mesh_quality")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)

    # A read off mesh.polygons is STALE in edit mode - the live data is in a BMesh nobody has
    # written back. Reporting numbers from it would describe the mesh as it was before the user
    # started editing, confidently.
    stale = edit_mode_stale(obj)

    tex_px = take_float(params, "texelDensityFor", default=1024.0)
    tex_px = finite_float(tex_px, "texelDensityFor")
    if tex_px <= 0:
        raise MifOpError("texelDensityFor is a texture size in PIXELS and must be positive - got "
                         "%r. NOTHING was measured." % tex_px)

    mesh = obj.data
    skipped = {}
    checks = {}
    concerns = []

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        # ---- topology -------------------------------------------------------------------
        tris = quads = ngons = 0
        degenerate = []
        world = obj.matrix_world
        world_area = 0.0
        for f in bm.faces:
            n = len(f.verts)
            if n == 3:
                tris += 1
            elif n == 4:
                quads += 1
            else:
                ngons += 1
            # Area in WORLD space, because a metre is what texel density is per - an object scaled
            # 100x has the same local area and a hundredth of the density.
            a = f.calc_area() * (world.to_scale().x * world.to_scale().y)
            world_area += a
            if a < _DEGENERATE_FACE_AREA:
                degenerate.append(f.index)

        checks["faces"] = {"total": len(bm.faces), "tris": tris, "quads": quads, "ngons": ngons}
        checks["degenerateFaces"] = {"count": len(degenerate), "indices": degenerate[:50]}

        # An edge on 0 faces is loose; on 1 it is a boundary (legitimate on an open mesh); on 3+ it
        # is non-manifold and will confuse every baker and engine downstream. Counted separately
        # because only the last is unambiguously wrong.
        loose_edges = [e.index for e in bm.edges if len(e.link_faces) == 0]
        boundary = [e.index for e in bm.edges if len(e.link_faces) == 1]
        nonmanifold = [e.index for e in bm.edges if len(e.link_faces) > 2]
        loose_verts = [v.index for v in bm.verts if len(v.link_edges) == 0]
        checks["looseVerts"] = {"count": len(loose_verts), "indices": loose_verts[:50]}
        checks["looseEdges"] = {"count": len(loose_edges), "indices": loose_edges[:50]}
        checks["boundaryEdges"] = {"count": len(boundary),
                                   "note": "legitimate on an open mesh; a closed one should have 0"}
        checks["nonManifoldEdges"] = {"count": len(nonmanifold), "indices": nonmanifold[:50]}

        # ---- UVs ------------------------------------------------------------------------
        want_layer = take(params, "uvLayer", default=None, kind=str)
        uv_layer = None
        if not bm.loops.layers.uv:
            skipped["uv"] = ("this mesh has NO UV layer, so UV bounds and texel density were not "
                             "measured - that is an absence of data, not a pass")
        else:
            if want_layer:
                uv_layer = bm.loops.layers.uv.get(want_layer)
                if uv_layer is None:
                    raise MifOpError(
                        "no UV layer named '%s' on '%s'. Layers: %s. NOTHING was measured."
                        % (want_layer, obj.name, ", ".join(bm.loops.layers.uv.keys()) or "(none)"))
            else:
                uv_layer = bm.loops.layers.uv.verify()

        if uv_layer is not None:
            outside = 0
            uv_total = 0.0
            densities = []
            for f in bm.faces:
                ua = _uv_area(f, uv_layer)
                uv_total += ua
                for loop in f.loops:
                    u, v = loop[uv_layer].uv
                    if (u < -_UV_BOUND_EPS or u > 1.0 + _UV_BOUND_EPS
                            or v < -_UV_BOUND_EPS or v > 1.0 + _UV_BOUND_EPS):
                        outside += 1
                wa = f.calc_area() * (world.to_scale().x * world.to_scale().y)
                if wa > _DEGENERATE_FACE_AREA and ua > 0.0:
                    # px per metre: the texture edge in pixels times the UV edge length, over the
                    # world edge length. Using areas, that is sqrt(uv_area)/sqrt(world_area)*px.
                    densities.append((ua ** 0.5) / (wa ** 0.5) * tex_px)

            checks["uv"] = {
                "layer": uv_layer.name if hasattr(uv_layer, "name") else str(want_layer or "active"),
                "loopsOutside01": outside,
                "uvAreaTotal": round(uv_total, 6),
                "note": ("uvAreaTotal well above 1.0 means islands overlap or spill outside the "
                         "tile; it does NOT prove overlap on its own, and this op does not do "
                         "island intersection."),
            }
            if densities:
                densities.sort()
                lo, hi = densities[0], densities[-1]
                mid = densities[len(densities) // 2]
                checks["texelDensity"] = {
                    "pixelsPerMetreMin": round(lo, 2),
                    "pixelsPerMetreMedian": round(mid, 2),
                    "pixelsPerMetreMax": round(hi, 2),
                    "spreadRatio": round(hi / lo, 2) if lo > 0 else None,
                    "forTextureSize": tex_px,
                    "note": ("spreadRatio is max/min across faces. A uniform unwrap sits near 1; a "
                             "large number means some faces get far more texture than others, "
                             "which is what makes one part of a model look blurry beside another."),
                }
            else:
                skipped["texelDensity"] = ("no face had both a non-zero world area and a non-zero "
                                           "UV area, so density is undefined rather than 0")
    finally:
        bm.free()

    # ---- transforms -----------------------------------------------------------------------
    sc = tuple(round(v, 6) for v in obj.scale)
    rot = tuple(round(v, 6) for v in obj.rotation_euler)
    checks["transform"] = {
        "scale": list(sc),
        "scaleApplied": sc == (1.0, 1.0, 1.0),
        "rotationEuler": list(rot),
        "rotationApplied": rot == (0.0, 0.0, 0.0),
        "note": ("unapplied scale is the most common export surprise: the mesh looks right in "
                 "Blender and arrives in the engine at a different size or with broken normals."),
    }

    slots = [s.material.name if s.material else None for s in obj.material_slots]
    checks["materials"] = {"slots": len(slots), "empty": sum(1 for s in slots if s is None),
                           "names": [s for s in slots if s]}

    # ---- the only judgements made, and each one is defensible -------------------------------
    if ngons:
        concerns.append("%d ngon(s) - many stores and engines require tris or quads only" % ngons)
    if nonmanifold:
        concerns.append("%d non-manifold edge(s) - these break bakes, booleans and collision"
                        % len(nonmanifold))
    if loose_verts or loose_edges:
        concerns.append("%d loose vert(s) and %d loose edge(s) - geometry that renders as nothing "
                        "and still costs file size" % (len(loose_verts), len(loose_edges)))
    if degenerate:
        concerns.append("%d zero-area face(s) - they export, shade unpredictably and show as "
                        "artefacts" % len(degenerate))
    if not checks["transform"]["scaleApplied"]:
        concerns.append("scale is not applied (%s) - apply it before export" % (list(sc),))
    if checks["materials"]["empty"]:
        concerns.append("%d empty material slot(s)" % checks["materials"]["empty"])
    if "uv" in checks and checks["uv"]["loopsOutside01"]:
        concerns.append("%d UV loop(s) outside the 0-1 tile" % checks["uv"]["loopsOutside01"])

    out = {
        "object": obj.name,
        "checks": checks,
        "concerns": concerns,
        "concernCount": len(concerns),
        # REACH, NOT GREEN. An empty concerns list next to a skipped check is not a clean bill of
        # health, and saying so here is what stops it being read as one.
        "notMeasured": skipped,
        "reachNote": ("concerns lists only defects with an objective threshold. It says NOTHING "
                      "about whether the asset looks good, and anything in notMeasured was not "
                      "judged at all - an absence of data rather than a pass."),
    }
    out.update(stale)
    out.update(shared_data_note(obj))
    return out

# ---------------------------------------------------------------------------
# recipe_game_ready - the boring pipeline, banked
# ---------------------------------------------------------------------------

def op_recipe_game_ready(params):
    """Apply transforms, ensure UVs, and MEASURE the result. The first Blender-side recipe.

    Composes ops that already exist rather than reimplementing them, so every guard they carry -
    shared mesh data, linked libraries, edit mode - still applies. It does nothing a caller could not
    do by hand; it does it in the right order and then checks.

    params:
      object (str, required)
      applyTransform (bool)  bake loc/rot/scale into the mesh data. Default true.
      unwrap (bool)          unwrap when there is no UV layer. Default true.
      forceUnwrap (bool)     unwrap even if a layer already exists. Default FALSE - re-unwrapping a
                             mesh somebody already laid out by hand is destructive and silent.
      uvMethod (str)         SMART or ANGLE, default SMART.
    """
    reject_unknown(params, {"object", "name", "applyTransform", "unwrap", "forceUnwrap",
                            "uvMethod"}, "recipe_game_ready")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)

    do_apply = take_bool(params, "applyTransform", default=True)
    do_unwrap = take_bool(params, "unwrap", default=True)
    force_unwrap = take_bool(params, "forceUnwrap", default=False)
    uv_method = str(take(params, "uvMethod", default="SMART", kind=str)).upper()

    steps = []
    left_behind = []

    def _record(name, changed, detail=""):
        steps.append({"step": name, "changed": bool(changed), "detail": detail})

    # ---- 1. transforms ------------------------------------------------------------------
    # WHY FIRST. Unwrapping before the scale is baked lays UVs out against the unscaled mesh, so a
    # non-uniformly scaled object gets a texel density that is wrong the moment the scale is applied.
    # Order is the entire value of a recipe; doing the same three ops in the wrong one is why this
    # keeps being got wrong by hand.
    if do_apply:
        before_scale = tuple(round(v, 6) for v in obj.scale)
        before_rot = tuple(round(v, 6) for v in obj.rotation_euler)
        if before_scale == (1.0, 1.0, 1.0) and before_rot == (0.0, 0.0, 0.0):
            _record("applyTransform", False, "already identity - nothing to bake")
        else:
            try:
                op_apply_transform({"object": obj.name, "location": False,
                                    "rotation": True, "scale": True})
                _record("applyTransform", True,
                        "baked scale %s and rotation %s into the mesh data"
                        % (list(before_scale), list(before_rot)))
            except MifOpError as exc:
                # NOTHING HAS BEEN CHANGED YET at this point, so the recipe can stop cleanly and
                # say so. That is worth stating rather than leaving the caller to infer it.
                raise MifOpError(
                    "recipe stopped at applyTransform and NOTHING was changed: %s" % exc)
    else:
        _record("applyTransform", False, "skipped - applyTransform was false")

    # ---- 2. UVs -------------------------------------------------------------------------
    had_uvs = len(obj.data.uv_layers) > 0
    if do_unwrap and (force_unwrap or not had_uvs):
        try:
            op_uv_unwrap({"object": obj.name, "method": uv_method})
            _record("uvUnwrap", True,
                    "unwrapped with %s (%s)" % (uv_method,
                                                "replaced an existing layout" if had_uvs
                                                else "there was no UV layer"))
        except MifOpError as exc:
            # HALF-APPLIED, AND IT SAYS SO. The transform above is already baked into the mesh data
            # and no exception undoes it - FTransaction-style rollback does not exist here. The UE
            # recipes' contract is not "leaves nothing" but "TELLS YOU what it left", and this is
            # that contract on the Blender side.
            if steps and steps[0].get("changed"):
                left_behind.append(
                    "applyTransform ALREADY RAN and its result is baked into the mesh data - the "
                    "object's scale and rotation are now identity and that is NOT undone by this "
                    "failure. The mesh is in a valid state, just further along than you asked for.")
            raise MifOpError(
                "recipe stopped at uvUnwrap: %s%s"
                % (exc, (" WHAT IS LEFT BEHIND: " + " ".join(left_behind)) if left_behind else ""))
    elif had_uvs and not force_unwrap:
        _record("uvUnwrap", False,
                "skipped - a UV layer already exists and forceUnwrap is false. Re-unwrapping a "
                "layout somebody made by hand is destructive and silent, so it is opt-in.")
    else:
        _record("uvUnwrap", False, "skipped - unwrap was false")

    # ---- 3. measure ---------------------------------------------------------------------
    # THE POINT OF THE RECIPE. Reporting the steps it took would be exactly the "ok:true is not
    # proof" failure in a new hat: the steps ran, and whether the result is shippable is a different
    # question that only a measurement answers.
    quality = op_mesh_quality({"object": obj.name})

    return {
        "object": obj.name,
        "steps": steps,
        # NO leftBehind FIELD HERE, and its absence is deliberate. A failure RAISES, so this dict is
        # only ever built on the success path where nothing was left half-applied - the field could
        # never be anything but empty, and a field that is always empty is decoration. What was left
        # behind is named in the ERROR TEXT instead, which is where the UE recipes put it too and
        # where a caller who hit the failure will actually read it. audit_blender_consequence_fields
        # caught this by asking who reads the field; the answer was nobody, correctly.
        "quality": quality,
        "concernCount": quality.get("concernCount"),
        "recipeNote": ("the steps above say what was DONE; quality says whether the result is "
                       "shippable, and they are different questions. Read quality.notMeasured "
                       "before treating concernCount:0 as a clean bill of health."),
    }

OPS = {
    "import_mesh": op_import_mesh,
    "decimate_mesh": op_decimate_mesh,
    "uv_unwrap": op_uv_unwrap,
    "create_collision_hull": op_create_collision_hull,
    "rename_object": op_rename_object,
    "set_vertex_color": op_set_vertex_color,
    "mesh_stats": op_mesh_stats,
    "mesh_quality": op_mesh_quality,
    "recipe_game_ready": op_recipe_game_ready,
    "export_mesh": op_export_mesh,
    "select_edges": op_select_edges,
    "bevel_edges": op_bevel_edges,
    "extrude_skirt": op_extrude_skirt,
    "set_material_slots": op_set_material_slots,
    "apply_transform": op_apply_transform,
    "set_origin": op_set_origin,
    "clean_mesh": op_clean_mesh,
    "uv_info": op_uv_info,
    "set_uv_layer": op_set_uv_layer,
}
