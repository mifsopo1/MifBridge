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

READ-ONLY WHEN THIS FILE WAS WRITTEN, AND NO LONGER - normalize_weights and
transfer_weights were added 2026-08-30 as its write half, for the reason this paragraph
itself makes: the reads could report a problem nobody could act on. The four list_* ops
below are still read-only. The original note, kept because the reasoning behind the reads
still stands: nothing here creates a bone, key or group, or changes scene data -
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
    select_only,
    selection_restore,
    selection_snapshot,
    take,
    take_bool,
    take_int,
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


def op_normalize_weights(params):
    """Make every vertex's bone weights sum to 1, and cap how many bones influence one vertex.

    THE WRITE HALF this file did not have. list_vertex_groups could report that a
    mesh has 40 groups and that some vertex is influenced by 11 of them, and nothing
    could act on it - the same detect-but-cannot-fix shape uv_unwrap closed for UVs
    and apply_transform closed for pivots.

    WHY THE LIMIT MATTERS, and it is not a Blender concern at all. Unreal's GPU skin
    cache supports a bounded number of influences per vertex - 4 by default, 8 or 12
    with the project setting raised - and the FBX importer DROPS the smallest weights
    past that limit and renormalises silently. So a mesh that deforms correctly in
    Blender can deform differently in Unreal, and nothing in either tool says why.
    Limiting here, deliberately, means the weights you tested are the weights that
    ship.

    ORDER IS LOAD-BEARING: limit first, THEN normalise. Limiting drops the smallest
    influences, which leaves the remaining weights summing to less than 1; normalise
    afterwards restores the sum. Doing it the other way round renormalises and then
    throws part of the result away, so the sum ends up wrong - which is exactly the
    bug the importer produces and this op exists to pre-empt.

    UNWEIGHTED VERTICES ARE REPORTED, NEVER INVENTED. A vertex in no group at all
    cannot be normalised - there is nothing to scale - and guessing a bone for it
    would be a silent wrong answer of the worst kind, because it deforms plausibly.
    They are counted and named in the response instead.
    """
    reject_unknown(params, ("object", "name", "maxInfluences", "normalize", "groups"),
                   "normalize_weights")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)

    max_inf = take_int(params, "maxInfluences", default=0)
    do_norm = take_bool(params, "normalize", default=True)
    only = take(params, "groups", default=None)

    if not obj.vertex_groups:
        raise MifOpError(
            "'%s' has no vertex groups, so there are no weights to normalise. NOTHING was changed."
            % obj.name)
    if max_inf and max_inf < 1:
        raise MifOpError("maxInfluences must be at least 1 - a vertex with zero influences is not "
                         "skinned at all. NOTHING was changed.")
    if not do_norm and not max_inf:
        raise MifOpError(
            "normalize_weights was asked to do nothing - normalize is false and maxInfluences is "
            "unset. NOTHING was changed.")

    if only is not None:
        if not isinstance(only, (list, tuple)):
            raise MifOpError("groups must be an array of vertex group names. NOTHING was changed.")
        missing = [g for g in only if g not in obj.vertex_groups]
        if missing:
            raise MifOpError(
                "no vertex group named %s on '%s' - %d group(s) exist. NOTHING was changed."
                % (", ".join("'%s'" % m for m in missing), obj.name, len(obj.vertex_groups)))
    allowed = None if only is None else {obj.vertex_groups[g].index for g in only}

    me = obj.data
    unweighted = 0
    limited = 0
    normalised = 0
    dropped_total = 0
    max_seen_before = 0

    for v in me.vertices:
        elems = [g for g in v.groups if allowed is None or g.group in allowed]
        if not elems:
            unweighted += 1
            continue
        max_seen_before = max(max_seen_before, len(elems))

        if max_inf and len(elems) > max_inf:
            elems.sort(key=lambda g: g.weight, reverse=True)
            for g in elems[max_inf:]:
                # Zeroing rather than removing: removing while iterating a vertex's
                # own group list is what corrupts the mesh, and a zero weight is
                # equivalent to absent everywhere Unreal reads it.
                dropped_total += 1
                g.weight = 0.0
            elems = elems[:max_inf]
            limited += 1

        if do_norm:
            total = sum(g.weight for g in elems)
            if total > 0.0 and abs(total - 1.0) > 1e-6:
                for g in elems:
                    g.weight = g.weight / total
                normalised += 1

    me.update()

    out = {
        "object": obj.name,
        "vertices": len(me.vertices),
        "groupsConsidered": len(obj.vertex_groups) if allowed is None else len(allowed),
        "maxInfluencesRequested": max_inf or None,
        "maxInfluencesSeenBefore": max_seen_before,
        "verticesLimited": limited,
        "influencesDropped": dropped_total,
        "verticesNormalized": normalised,
        "unweightedVertices": unweighted,
        "changedAnything": bool(limited or normalised),
    }
    if unweighted:
        out["unweightedNote"] = (
            "%d vertex/vertices belong to NO vertex group and were left alone. They cannot be "
            "normalised - there is nothing to scale - and assigning them a bone would be a guess "
            "that deforms plausibly and wrongly. In Unreal these bind to the root bone." % unweighted)
    if max_inf and max_seen_before <= max_inf:
        out["limitNote"] = (
            "no vertex exceeded %d influences (the most any had was %d), so the limit changed "
            "nothing. Reported rather than returned as work performed." % (max_inf, max_seen_before))
    if not out["changedAnything"]:
        out["note"] = ("the weights were already normalised and within the influence limit - "
                       "counts are unchanged.")
    return out


def op_transfer_weights(params):
    """Copy vertex weights from one mesh onto another by proximity.

    The op a retopology or LOD pass needs and this addon had no answer for: a
    decimated or rebuilt mesh comes out of clean_mesh/decimate_mesh with its
    topology changed and its skinning destroyed, and re-rigging by hand is the
    expensive part. Blender's data transfer maps weights from the original by
    nearest-surface, which is exactly the operation, and nothing exposed it.

    IT REFUSES RATHER THAN GUESSES in the two cases where a plausible-looking
    result would be wrong:
      - the source has no vertex groups: there is nothing to transfer, and
        producing an unskinned mesh while reporting success is the silent-success
        shape this project keeps finding.
      - source and destination are the same object: the operator would happily run
        and achieve nothing.

    WHAT IT DOES NOT DO: it does not normalise. Transferred weights routinely do not
    sum to 1, because a nearest-surface mapping interpolates between source vertices
    with different totals. Call normalize_weights afterwards - separately and
    visibly, rather than folded in here, so the caller knows it happened. The
    response says so when the result needs it.
    """
    reject_unknown(params, ("source", "from", "destination", "to", "object", "mapping"),
                   "transfer_weights")
    src = get_object(take(params, "source", "from", required=True), want_mesh=True)
    dst = get_object(take(params, "destination", "to", "object", required=True), want_mesh=True)

    if src.name == dst.name:
        raise MifOpError(
            "source and destination are the same object ('%s') - there is nothing to transfer. "
            "NOTHING was changed." % src.name)
    if not src.vertex_groups:
        raise MifOpError(
            "source '%s' has no vertex groups, so there are no weights to transfer. Producing an "
            "unskinned result and reporting success would be worse than refusing. NOTHING was "
            "changed." % src.name)

    mapping = (take(params, "mapping", default="POLYINTERP_NEAREST") or "POLYINTERP_NEAREST").upper()
    valid = ("NEAREST", "EDGE_NEAREST", "EDGEINTERP_NEAREST", "POLY_NEAREST",
             "POLYINTERP_NEAREST", "POLYINTERP_VNORPROJ")
    if mapping not in valid:
        raise MifOpError("unknown mapping '%s' for transfer_weights. Accepted: %s. NOTHING was "
                         "changed." % (mapping, ", ".join(valid)))

    groups_before = len(dst.vertex_groups)
    snap = selection_snapshot()
    try:
        select_only([src, dst])
        bpy.context.view_layer.objects.active = dst
        bpy.ops.object.data_transfer(
            use_reverse_transfer=True,
            data_type="VGROUP_WEIGHTS",
            vert_mapping=mapping,
            layers_select_src="ALL",
            layers_select_dst="NAME",
            mix_mode="REPLACE",
        )
    finally:
        selection_restore(snap)

    # READ BACK. data_transfer reports nothing useful, so the postcondition is
    # measured: how many groups the destination has now, and how many of its
    # vertices actually carry a weight.
    weighted = sum(1 for v in dst.data.vertices if v.groups)
    out = {
        "source": src.name,
        "destination": dst.name,
        "mapping": mapping,
        "sourceGroups": len(src.vertex_groups),
        "destinationGroupsBefore": groups_before,
        "destinationGroupsAfter": len(dst.vertex_groups),
        "destinationVertices": len(dst.data.vertices),
        "destinationVerticesWeighted": weighted,
        "transferred": len(dst.vertex_groups) > groups_before or weighted > 0,
    }
    if not out["transferred"]:
        raise MifOpError(
            "the transfer ran and '%s' still has no weighted vertices (%d groups before, %d after). "
            "The usual cause is the two meshes being far apart in world space - a nearest-surface "
            "mapping needs them roughly coincident. NOTHING usable was produced."
            % (dst.name, groups_before, len(dst.vertex_groups)))
    if weighted < len(dst.data.vertices):
        out["coverageNote"] = (
            "%d of %d destination vertices carry no weight - the mapping found nothing near them. "
            "In Unreal those bind to the root bone."
            % (len(dst.data.vertices) - weighted, len(dst.data.vertices)))
    out["normalizeNote"] = (
        "transferred weights are NOT normalised - a nearest-surface mapping interpolates between "
        "source vertices whose totals differ, so sums drift from 1. Call normalize_weights on "
        "'%s' before exporting." % dst.name)
    return out


OPS = {
    "list_bones": op_list_bones,
    "list_shape_keys": op_list_shape_keys,
    "list_vertex_groups": op_list_vertex_groups,
    "list_modifiers": op_list_modifiers,
    "normalize_weights": op_normalize_weights,
    "transfer_weights": op_transfer_weights,
}
