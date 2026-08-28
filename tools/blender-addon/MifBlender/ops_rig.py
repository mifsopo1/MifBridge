"""MifBlender ops: armatures, shape keys, vertex groups - the character-rigging data the
addon had ZERO ops for before this file.

WHY THIS EXISTS. object_info() (ops_common.py) reports transform, bounds, materials and UVs for
a MESH, and NOTHING for an ARMATURE object beyond its bare transform - `if obj.type != "MESH":
return info` skips straight past it. Shape keys and vertex groups are absent even for a mesh.
That is a real gap for a game-asset pipeline: DDS2/Curfew are character-driven, the UE side of
this bridge can already read a skeleton's bones, virtual bones and morph targets
(MifBridgeSkeleton.cpp, list_bones/list_virtual_bones/list_morph_targets), and until now nothing
on the BLENDER side - where a rigger actually AUTHORS that data before export - could read any
of it back.

NAMED TO MATCH THE UE SIDE ON PURPOSE. list_bones here reads an Armature's REST pose the same
way UE's list_bones reads a Skeleton's ReferenceSkeleton; list_shape_keys is Blender's name for
what UE calls morph targets, cross-referenced in both docstrings so a caller working across the
pipeline recognises the pairing instead of having to know that "shape key" and "morph target"
are the same thing under two names.

READ-ONLY, ALL THREE. Nothing here creates a bone, key or group, or changes scene data -
matching this addon's existing op_object_info / list_objects, and the read-before-you-can-fix-it
shape UE's own list_bones/list_morph_targets already established.

VERIFIED against a live Blender 4.4 instance before being trusted, not assumed from API docs -
see the commit this file was added in for the exact live-call transcript.
"""

from __future__ import annotations

import bpy

from .ops_common import (
    MifOpError,
    get_object,
    reject_unknown,
    rnd,
    take,
)


def _bone_dict(bone):
    return {
        "name": bone.name,
        "parent": bone.parent.name if bone.parent else None,
        # ARMATURE-SPACE, not parent-relative - head_local/tail_local, not head/tail, which are in
        # the bone's OWN local space and would need every ancestor's transform composed to mean
        # anything on their own. Matches UE list_bones reporting refPose as parent-relative but
        # DOCUMENTING it as such; here the armature-space form is the one riggers actually compare
        # against a reference skeleton, so it is the default rather than an opt-in.
        "headArmatureSpaceBU": rnd(bone.head_local),
        "tailArmatureSpaceBU": rnd(bone.tail_local),
        "length": round(float(bone.length), 6),
        "useDeform": bool(bone.use_deform),
        "isRoot": bone.parent is None,
        "childCount": len(bone.children),
    }


def op_list_bones(params):
    """The rest-pose bone hierarchy of an Armature object - Blender's name for what UE calls a
    Skeleton's ReferenceSkeleton."""
    reject_unknown(params, {"object", "name", "nameContains"}, "list_bones")
    obj = get_object(take(params, "object", "name", required=True, kind=str))
    if obj.type != "ARMATURE":
        raise MifOpError("object '%s' is a %s, not an ARMATURE. list_bones reads bone rest pose; "
                         "list_objects with type:'ARMATURE' finds one if you are not sure of the "
                         "name." % (obj.name, obj.type))

    filt = take(params, "nameContains", default=None)
    bones = obj.data.bones
    rows = []
    for bone in bones:
        if filt and str(filt) not in bone.name:
            continue
        rows.append(_bone_dict(bone))

    return {
        "armature": obj.name,
        "boneCount": len(bones),
        "count": len(rows),
        "bones": rows,
    }


def op_list_shape_keys(params):
    """Shape keys on a mesh object - Blender's name for what UE calls morph targets. Names,
    value, slider range, mute state and which key each is relative to."""
    reject_unknown(params, {"object", "name"}, "list_shape_keys")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    keys = obj.data.shape_keys
    if keys is None:
        # A real, common state - most game meshes have none - not a failure. Matches UE
        # list_morph_targets' own "this mesh has no morph targets" note rather than an error.
        return {"object": obj.name, "hasShapeKeys": False, "count": 0, "shapeKeys": [],
               "note": "this mesh has no shape keys. That is normal for most meshes - only ones "
                       "authored for facial or blend-shape animation need them."}

    blocks = keys.key_blocks
    rows = []
    for kb in blocks:
        rows.append({
            "name": kb.name,
            "value": round(float(kb.value), 6),
            "sliderMin": round(float(kb.slider_min), 6),
            "sliderMax": round(float(kb.slider_max), 6),
            "mute": bool(kb.mute),
            # The BASIS key (index 0) is relative to itself and drives nothing - reported so a
            # caller can tell "the neutral pose" from "an actual pose target" without guessing
            # from index 0 by convention.
            "isBasis": kb.name == blocks[0].name,
            "relativeTo": kb.relative_key.name if kb.relative_key else None,
        })

    return {
        "object": obj.name,
        "hasShapeKeys": True,
        "count": len(rows),
        "shapeKeys": rows,
    }


def op_list_vertex_groups(params):
    """Vertex groups on a mesh object - the bone-weight assignment groups a skinned mesh needs
    one per deforming bone. Reports which groups actually have any vertex assigned, since a
    group with ZERO weighted vertices is a rig that will not deform on that bone at all - the
    same class of bug UE's analyze_skeletal_split flags with `influencesGeometry:false`."""
    reject_unknown(params, {"object", "name"}, "list_vertex_groups")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    groups = obj.vertex_groups
    if not groups:
        return {"object": obj.name, "count": 0, "vertexGroups": [],
               "note": "this mesh has no vertex groups. If it is meant to be skinned to an "
                       "armature, this mesh cannot deform on any bone yet."}

    # ONE PASS over the mesh rather than one pass PER GROUP - counting per-group by iterating all
    # vertices once and bucketing is O(verts) instead of O(verts * groups), which matters once a
    # character mesh with 30+ groups gets into the tens of thousands of vertices.
    counts = [0] * len(groups)
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > 0.0 and 0 <= g.group < len(counts):
                counts[g.group] += 1

    rows = [{"name": g.name, "index": g.index, "weightedVertexCount": counts[g.index],
            "influencesGeometry": counts[g.index] > 0}
           for g in groups]

    return {
        "object": obj.name,
        "count": len(rows),
        "vertexGroups": rows,
    }


# A handful of settings PER TYPE that actually decide what export produces, not every property
# Blender exposes for that type - e.g. Mirror's use_axis/merge_threshold change the resulting
# geometry, its vertex_group/bisect settings are refinements on top. Curated rather than
# exhaustive on purpose: Blender ships 100+ modifier types, and hand-describing all of them for
# one addon nobody has asked to author modifiers through (only READ what is already there) would
# be effort spent on the wrong problem. A type not listed here still reports name/type/visibility
# - never silently dropped, just without a decoded settings dict.
_MODIFIER_FIELDS = {
    "ARMATURE": (("object", lambda m: m.object.name if m.object else None),),
    "MIRROR": (("axisX", lambda m: bool(m.use_axis[0])), ("axisY", lambda m: bool(m.use_axis[1])),
              ("axisZ", lambda m: bool(m.use_axis[2])),
              ("mergeThreshold", lambda m: round(float(m.merge_threshold), 6))),
    "SOLIDIFY": (("thickness", lambda m: round(float(m.thickness), 6)),),
    "BEVEL": (("width", lambda m: round(float(m.width), 6)), ("segments", lambda m: int(m.segments))),
    "SUBSURF": (("levels", lambda m: int(m.levels)),
               ("renderLevels", lambda m: int(m.render_levels))),
    "DECIMATE": (("decimateType", lambda m: str(m.decimate_type)),
                ("ratio", lambda m: round(float(m.ratio), 6))),
    "TRIANGULATE": (("quadMethod", lambda m: str(m.quad_method)),),
}


def _modifier_dict(mod):
    row = {
        "name": mod.name,
        "type": mod.type,
        "showViewport": bool(mod.show_viewport),
        "showRender": bool(mod.show_render),
    }
    fields = _MODIFIER_FIELDS.get(mod.type)
    if fields:
        settings = {}
        for key, getter in fields:
            try:
                settings[key] = getter(mod)
            except Exception:  # noqa: BLE001 - a field this addon's own curation got wrong for
                                # some modifier sub-state must not take the whole row down with it
                settings[key] = None
        row["settings"] = settings
    return row


def op_list_modifiers(params):
    """The modifier stack on a mesh object, in EVALUATION ORDER (top to bottom in the Modifier
    Properties panel, which is also the order they apply in). Answers "what will export_mesh
    actually produce" before spending an export on finding out - a Mirror or Subsurf still in the
    stack changes the exported geometry; a disabled one (showViewport/showRender both false)
    does not, and both states are reported rather than only "is it there"."""
    reject_unknown(params, {"object", "name"}, "list_modifiers")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    rows = [_modifier_dict(m) for m in obj.modifiers]
    return {
        "object": obj.name,
        "count": len(rows),
        "modifiers": rows,
    }


OPS = {
    "list_bones": op_list_bones,
    "list_shape_keys": op_list_shape_keys,
    "list_vertex_groups": op_list_vertex_groups,
    "list_modifiers": op_list_modifiers,
}
