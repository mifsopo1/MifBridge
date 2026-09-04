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

import array
import hashlib

import bpy

from .ops_common import (MifOpError, check_axis_dict, finite_floats, get_object, mesh_counts,
                         reject_unknown, rnd, select_only, selection_restore, selection_snapshot,
                         take, take_bool, take_float, take_int)


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


# Blender truncates ID and group names here on 3.6 through 4.4. Measured on all four builds:
# a 100-character vertex group name comes back at 63 characters on every one of them.
_ID_NAME_LIMIT = 63


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


def _read_modifier_attr(mod, attr):
    """One modifier property as something JSON can carry, whatever kind it is."""
    value = getattr(mod, attr)
    if value is None:
        return None
    if hasattr(value, "name") and not isinstance(value, str):
        return value.name                       # a datablock pointer - report what it points AT
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(float(value), 6)
    if isinstance(value, int):
        return int(value)
    if hasattr(value, "__len__") and not isinstance(value, str):
        return [round(float(v), 6) for v in value]
    return str(value)


def _generated_fields(mod_type):
    """The read side, DERIVED from the write table rather than typed out again.

    WHY THIS IS GENERATED. _MODIFIER_FIELDS was a second hand-written table listing the same seven
    types _MODIFIER_WRITES did, and the moment fourteen types were added to the write side the two
    disagreed - every new setting was writable and unreadable, which is precisely the read/write
    asymmetry this repo treats as a defect class. Two tables that must agree will not, so there is
    one table and the other is computed from it.

    Only entries whose target is a plain RNA name are generated; the handful with callable setters
    (MIRROR's use_axis indices) keep their hand-written getters in _MODIFIER_FIELDS, which wins.
    """
    out = []
    for key, (target, _coerce) in sorted(_MODIFIER_WRITES.get(mod_type, {}).items()):
        if isinstance(target, str):
            out.append((key, lambda m, a=target: _read_modifier_attr(m, a)))
    return tuple(out)


def _modifier_dict(mod):
    row = {
        "name": mod.name,
        "type": mod.type,
        "showViewport": bool(mod.show_viewport),
        "showRender": bool(mod.show_render),
    }
    fields = _MODIFIER_FIELDS.get(mod.type) or _generated_fields(mod.type)
    if fields:
        settings = {}
        for key, getter in fields:
            try:
                settings[key] = getter(mod)
            except Exception:  # noqa: BLE001 - a field this addon's own curation got wrong for
                                # some modifier sub-state must not take the whole row down with it
                settings[key] = None
        row["settings"] = settings

    # NODES IS NOT IN EITHER TABLE AND CANNOT BE, because its settings are not RNA properties -
    # they are per-group inputs addressed by IDENTIFIER (Socket_2) inside the modifier itself.
    #
    # Until this was added, list_modifiers reported a geometry-nodes modifier as a bare name and
    # type: which GROUP it held was invisible, so two assign_node_group calls stacking two
    # modifiers were indistinguishable from one, and there was no way to see what any of their
    # inputs had been set to. assign_node_group could write them and nothing could read them back.
    if mod.type == "NODES":
        tree = getattr(mod, "node_group", None)
        row["group"] = tree.name if tree is not None else None
        inputs = {}
        if tree is not None:
            if hasattr(tree, "interface"):
                items = [it for it in tree.interface.items_tree
                         if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT"]
            else:
                items = list(tree.inputs)
            for it in items:
                try:
                    held = mod[it.identifier]
                except (KeyError, TypeError):
                    inputs[it.name] = None
                    continue
                inputs[it.name] = (held.name if hasattr(held, "name") and not isinstance(held, str)
                                   else (round(float(held), 6) if isinstance(held, float)
                                         else (list(held) if hasattr(held, "__len__")
                                               and not isinstance(held, str) else held)))
        row["inputs"] = inputs
        row["outputConnected"] = (
            any(l.to_node.bl_idname == "NodeGroupOutput" for l in tree.links)
            if tree is not None else None)
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
        # A ZERO WEIGHT IS NOT AN INFLUENCE, and counting one is what made this op report a full
        # round of work on a mesh it had already capped. v.groups is MEMBERSHIP: the trim below
        # zeroes a weight rather than removing the group - deliberately, because "removing while
        # iterating a vertex's own group list is what corrupts the mesh" - so after one run every
        # vertex is still IN all its original groups at weight 0.
        #
        # Measured 2026-08-31 on a cube with 8 groups at 0.125 each, maxInfluences 4:
        #
        #     before        64 influences (list_vertex_groups)
        #     run 1         influencesDropped 32, verticesLimited 8, maxSeenBefore 8   -> 32 left
        #     run 2         influencesDropped 32, verticesLimited 8, maxSeenBefore 8   -> 32 left
        #
        # The second call changed NOTHING and reported the same work as the first. A caller who
        # normalises twice is told twice that weights were thrown away, and there is no other field
        # that would tell them otherwise.
        #
        # Filtering here also settles a disagreement between two ops about what an "influence" is:
        # list_vertex_groups' weightedVertexCount already counts only NONZERO weights, so the two
        # were describing the same mesh with different numbers. The op's own comment on the trim -
        # "a zero weight is equivalent to absent everywhere Unreal reads it" - is the argument for
        # this being the right side of that disagreement.
        elems = [g for g in v.groups
                 if (allowed is None or g.group in allowed) and g.weight > 0.0]
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
            # SWAPPED, BECAUSE use_reverse_transfer SWAPS WHICH ENUM VALIDATES WHICH ARGUMENT.
            # layers_select_src normally takes ACTIVE|ALL|BONE_SELECT|BONE_DEFORM and
            # layers_select_dst takes ACTIVE|NAME|INDEX - but with reverse on, "ALL" passed to
            # _src is checked against the DST enum and raises
            #   TypeError: enum "ALL" not found in ('ACTIVE', 'NAME', 'INDEX')
            # so this op had NEVER WORKED on any Blender. Measured on 3.6, 4.2, 4.4 and 5.0: as
            # shipped it raises on all four; swapped it returns FINISHED and the destination gains
            # the group on all four.
            #
            # Found by blender_version_matrix once set_vertex_weights existed to give the source a
            # group - before that this op refused for "no vertex groups" and never reached the call.
            layers_select_src="NAME",
            layers_select_dst="ALL",
            mix_mode="REPLACE",
        )
    finally:
        selection_restore(snap)

    # READ BACK. data_transfer reports nothing useful, so the postcondition is
    # measured: how many groups the destination has now, and how many of its
    # vertices actually carry a weight.
    #
    # NONZERO WEIGHT, NOT MEMBERSHIP - the same distinction op_normalize_weights documents at length
    # 120 lines up, and the 2026-08-31 fix there corrected only that site. `v.groups` is MEMBERSHIP:
    # a vertex trimmed to weight 0 is still IN the group, and layers_select_dst="NAME" reuses groups
    # that already exist rather than adding any. So on a SECOND transfer into the same destination,
    # `len(dst.vertex_groups) > groups_before` is False by construction and `weighted > 0` was
    # guaranteed True by the memberships the first run left behind - which made the refusal below
    # unreachable in exactly the iterate-the-rig case it exists for.
    #
    # It also made two numbers wrong rather than merely weak: destinationVerticesWeighted reported
    # the vertex COUNT, and coverageNote tested the same measure, so a destination whose every
    # weight is 0.0 was reported as fully covered with no note. This file's own argument for the
    # filter - "a zero weight is equivalent to absent everywhere Unreal reads it" - applies here
    # unchanged, and list_vertex_groups' weightedVertexCount has always counted it this way.
    weighted = sum(1 for v in dst.data.vertices if any(g.weight > 0.0 for g in v.groups))
    out = {
        "source": src.name,
        "destination": dst.name,
        "mapping": mapping,
        "sourceGroups": len(src.vertex_groups),
        "destinationGroupsBefore": groups_before,
        "destinationGroupsAfter": len(dst.vertex_groups),
        "destinationVertices": len(dst.data.vertices),
        "destinationVerticesWeighted": weighted,
        # A NON-ZERO WEIGHT, AND NOTHING ELSE. This was
        #     len(dst.vertex_groups) > groups_before or weighted > 0
        # and the OR made the group COUNT sufficient - so a first transfer that created the group
        # and wrote every weight as 0.0 reported transferred:True with weighted 0 of 8. Measured on
        # 4.4.0 and 5.0.1 with a half-weighted source: exactly the unskinned result the docstring
        # above says would be worse than refusing, reported as a success.
        #
        # The comment above added `weighted > 0` to rescue the SECOND-transfer case, where the group
        # already exists so the count cannot move. That was right; keeping the count as an
        # alternative was not. A group is not evidence - a group whose every weight is zero exists,
        # reads back perfectly in list_vertex_groups, and deforms nothing.
        "transferred": weighted > 0,
        "destinationGroupCountMoved": len(dst.vertex_groups) > groups_before,
    }
    if not out["transferred"]:
        raise MifOpError(
            "the transfer ran and '%s' still has NO WEIGHTED VERTICES (%d groups before, %d "
            "after%s). Two usual causes: the meshes are far apart in world space, since a "
            "nearest-surface mapping needs them roughly coincident; or the source vertices nearest "
            "the destination are themselves unweighted, which a partially weighted source does "
            "quietly. Either way the group may now EXIST on '%s' and deform nothing, which is why "
            "this refuses instead of reporting the group it made. NOTHING usable was produced."
            % (dst.name, groups_before, len(dst.vertex_groups),
               " - a group WAS created" if len(dst.vertex_groups) > groups_before else "",
               dst.name))
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


# The WRITE side of _MODIFIER_FIELDS. Two tables, not one, and that is deliberate rather than
# duplication left unresolved: the read table is getter lambdas, and a write table needs setters,
# type coercion and a per-version presence check, so "share one description" is not achievable
# without making both halves worse. What IS enforced is that the two describe the same TYPES -
# test_blender_rig asserts the key sets match, so a type added to one and forgotten in the other
# is a failing test rather than a silent asymmetry.
#
# Each entry: param name -> (attribute, coercion). Coercion is applied before assignment so a JSON
# number arriving as a float for an int property is a clean error rather than a Blender exception
# surfacing as a stack trace.
def _idx_setter(i):
    def setter(mod, value):
        axes = list(mod.use_axis)
        axes[i] = bool(value)
        mod.use_axis = axes
    return setter


_MODIFIER_WRITES = {
    "ARMATURE": {
        "object": ("object", "object"),
    },
    "MIRROR": {
        "axisX": (_idx_setter(0), bool),
        "axisY": (_idx_setter(1), bool),
        "axisZ": (_idx_setter(2), bool),
        "mergeThreshold": ("merge_threshold", float),
    },
    "SOLIDIFY": {"thickness": ("thickness", float)},
    "BEVEL": {"width": ("width", float), "segments": ("segments", int)},
    "SUBSURF": {"levels": ("levels", int), "renderLevels": ("render_levels", int)},
    "DECIMATE": {"decimateType": ("decimate_type", str), "ratio": ("ratio", float)},
    "TRIANGULATE": {"quadMethod": ("quad_method", str)},

    # EVERY IDENTIFIER BELOW WAS READ OFF bl_rna ON 3.6.23, 4.2.17, 4.4.0 AND 5.0.1 and is present
    # on all four - not taken from documentation, and not guessed from the UI labels, which differ.
    #
    # WHY THESE FOURTEEN. add_modifier could point exactly ONE modifier at an object (ARMATURE), and
    # a modifier that cannot be pointed at anything sits in the stack doing nothing while reading
    # back as a perfectly healthy modifier. That closed retopo (shrinkwrap), lattice/hook/mesh-deform
    # rigging, arrays along a curve, mirror-across-object, boolean operands and displacement - and
    # every one of them looked like a separate missing feature when the blocker was a single missing
    # idea: a NAME becoming a POINTER.
    #
    # vertex_group is a STRING on the modifier and is validated against the OBJECT's groups anyway,
    # because Blender accepts any string and a name that matches nothing is not an error. On a MASK
    # that is the difference between masking by a group and masking EVERYTHING.
    "SHRINKWRAP": {
        "target": ("target", "object"), "offset": ("offset", float),
        "vertexGroup": ("vertex_group", "vgroup"),
        "wrapMethod": ("wrap_method", "enum"), "wrapMode": ("wrap_mode", "enum"),
    },
    "LATTICE": {
        "object": ("object", "object"), "strength": ("strength", float),
        "vertexGroup": ("vertex_group", "vgroup"),
    },
    "HOOK": {
        "object": ("object", "object"), "strength": ("strength", float),
        "falloffRadius": ("falloff_radius", float),
        "vertexGroup": ("vertex_group", "vgroup"),
    },
    "MASK": {
        "vertexGroup": ("vertex_group", "vgroup"),
        "invert": ("invert_vertex_group", bool), "threshold": ("threshold", float),
        "armature": ("armature", "object"),
    },
    "CAST": {
        "object": ("object", "object"), "factor": ("factor", float),
        "radius": ("radius", float), "size": ("size", float),
        "castType": ("cast_type", "enum"),
    },
    "ARRAY": {
        "count": ("count", int), "curve": ("curve", "object"),
        "offsetObject": ("offset_object", "object"),
        "startCap": ("start_cap", "object"), "endCap": ("end_cap", "object"),
        "fitType": ("fit_type", "enum"), "mergeThreshold": ("merge_threshold", float),
        "mergeVertices": ("use_merge_vertices", bool),
    },
    "CURVE": {
        "object": ("object", "object"), "deformAxis": ("deform_axis", "enum"),
        "vertexGroup": ("vertex_group", "vgroup"),
    },
    "BOOLEAN": {
        "object": ("object", "object"), "collection": ("collection", "collection"),
        "operation": ("operation", "enum"), "solver": ("solver", "enum"),
        "doubleThreshold": ("double_threshold", float),
    },
    "DISPLACE": {
        "strength": ("strength", float), "midLevel": ("mid_level", float),
        "direction": ("direction", "enum"), "texture": ("texture", "texture"),
        "textureCoordsObject": ("texture_coords_object", "object"),
        "vertexGroup": ("vertex_group", "vgroup"),
    },
    "SIMPLE_DEFORM": {
        "deformMethod": ("deform_method", "enum"), "angle": ("angle", float),
        "factor": ("factor", float), "deformAxis": ("deform_axis", "enum"),
        "origin": ("origin", "object"), "vertexGroup": ("vertex_group", "vgroup"),
    },
    "MESH_DEFORM": {
        "object": ("object", "object"), "precision": ("precision", int),
        "vertexGroup": ("vertex_group", "vgroup"),
    },
    "SMOOTH": {
        "factor": ("factor", float), "iterations": ("iterations", int),
        "vertexGroup": ("vertex_group", "vgroup"),
    },
    "WELD": {
        "mergeThreshold": ("merge_threshold", float), "mode": ("mode", "enum"),
        "vertexGroup": ("vertex_group", "vgroup"),
    },
    "WIREFRAME": {
        "thickness": ("thickness", float), "offset": ("offset", float),
        "boundary": ("use_boundary", bool), "evenOffset": ("use_even_offset", bool),
        "creaseWeight": ("crease_weight", float),
    },
}


# WHICH bpy.data COLLECTION EACH POINTER KIND DRAWS FROM. Same idea as ops_nodes' _POINTER_SOCKETS,
# kept separate because a modifier field is addressed by RNA name rather than by socket type.
_DATABLOCK_COERCE = {
    "object": ("objects", "object"),
    "collection": ("collections", "collection"),
    "texture": ("textures", "texture"),
}


def _resolve_datablock(kind, value, key):
    """A NAME becoming a POINTER, refusing with what is actually in the file."""
    attr, noun = _DATABLOCK_COERCE[kind]
    if value is None:
        return None
    coll = getattr(bpy.data, attr)
    found = coll.get(str(value))
    if found is None:
        have = sorted(d.name for d in coll)[:25]
        raise MifOpError("no %s named '%s' for setting '%s'. This file has: %s. NOTHING was "
                         "changed." % (noun, value, key, ", ".join(have) if have else "(none)"))
    return found


def _apply_modifier_settings(mod, settings, mod_type):
    """Assign a settings dict onto a modifier. Returns the names actually applied."""
    table = _MODIFIER_WRITES.get(mod_type, {})
    if not isinstance(settings, dict):
        raise MifOpError("settings must be an object of {name: value}. NOTHING was changed.")
    unknown = [k for k in settings if k not in table]
    if unknown:
        raise MifOpError(
            "%s has no writable setting named %s here. This addon curates a handful of settings per "
            "type - the ones that decide what an export produces - rather than exposing every "
            "property Blender has. Accepted for %s: %s. NOTHING was changed."
            % (mod_type, ", ".join("'%s'" % u for u in unknown), mod_type,
               ", ".join(sorted(table)) or "(none yet)"))

    applied = []
    for key, value in settings.items():
        target, coerce = table[key]
        try:
            if coerce in _DATABLOCK_COERCE:
                setattr(mod, target, _resolve_datablock(coerce, value, key))
            elif coerce == "vgroup":
                # A STRING PROPERTY THAT MUST NAME A REAL GROUP. Blender takes any string here and
                # a name matching nothing is not an error - it just selects no vertices. On a MASK
                # that is the difference between masking by a group and masking the whole object.
                owner = mod.id_data
                if value is not None and str(value) not in owner.vertex_groups:
                    have = [g.name for g in owner.vertex_groups][:25]
                    raise MifOpError(
                        "'%s' has no vertex group named '%s'. Blender accepts any string here and "
                        "a name that matches nothing is NOT an error - it simply selects no "
                        "vertices, which on a MASK empties the object. Groups: %s. NOTHING was "
                        "changed." % (owner.name, value, ", ".join(have) if have else "(none)"))
                setattr(mod, target, str(value))
            elif coerce == "enum":
                # VALIDATED AGAINST THIS BUILD'S OWN ENUM, so the refusal names what is available
                # rather than letting RNA raise a TypeError that names the type and not the fix.
                valid = [i.identifier for i in mod.bl_rna.properties[target].enum_items]
                want = str(value).upper()
                match = [v for v in valid if v.upper() == want]
                if not match:
                    raise MifOpError(
                        "'%s' is not a valid %s for a %s modifier on Blender %s. Available: %s. "
                        "NOTHING was changed."
                        % (value, key, mod_type, bpy.app.version_string, ", ".join(valid)))
                setattr(mod, target, match[0])
            else:
                cast = coerce(value)
                if callable(target):
                    target(mod, cast)
                else:
                    setattr(mod, target, cast)
        except MifOpError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MifOpError("could not set '%s' on the %s modifier: %s. NOTHING was changed."
                             % (key, mod_type, exc))
        applied.append(key)
    return sorted(applied)


def _evaluated_counts(obj):
    """What the modifier stack currently PRODUCES, not what the mesh data holds.

    THE TWO ARE DIFFERENT AND THAT IS THE WHOLE POINT. obj.data is the mesh before any modifier;
    the depsgraph result is what renders and what exports. A modifier that empties the object
    changes the second and not the first.
    """
    try:
        deps = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(deps)
        mesh = ev.to_mesh()
        n = len(mesh.vertices)
        # A COORDINATE FINGERPRINT, NOT JUST COUNTS, and this was got wrong here FIRST.
        # The version of this helper written an hour before apply_modifier was fixed for
        # exactly this compared counts alone, so a SHRINKWRAP pointed at a real target
        # reported evaluatedUnchanged:true - a deforming modifier moves vertices and
        # changes no count. Knowing about the trap did not stop me walking into it in the
        # next function, which is the argument for the fingerprint being in the helper
        # rather than remembered at each call site.
        buf = array.array("f", [0.0]) * (3 * n)
        mesh.vertices.foreach_get("co", buf)
        out = {"verts": n, "edges": len(mesh.edges), "faces": len(mesh.polygons),
               "shape": hashlib.sha256(buf.tobytes()).hexdigest()[:16]}
        ev.to_mesh_clear()
        return out
    except Exception:  # noqa: BLE001
        # A REPORTING FIELD MUST NOT BE ABLE TO BREAK THE OP. If the depsgraph cannot be read here,
        # the answer is "unknown" rather than a raised exception from a diagnostic.
        return {}


def op_add_modifier(params):
    """Add a modifier to a mesh object's stack - the write half of list_modifiers.

    THE GENERAL FORM of the edits this addon otherwise hardcodes one at a time.
    decimate_mesh already builds, configures and applies a DECIMATE modifier
    internally; this exposes the same mechanism for the other types, so mirror,
    solidify, subsurf, weighted-normal and the rest stop each needing their own op.

    IT DOES NOT APPLY. Adding and applying are separate on purpose: a modifier left
    in the stack still changes what export_mesh writes (use_mesh_modifiers is on),
    so the caller may well want it live and unapplied. apply_modifier is the
    separate, destructive step, and list_modifiers is how you check what is stacked
    before spending an export finding out.

    SETTINGS ARE CURATED, NOT EXHAUSTIVE, and the refusal says so with the list for
    that type. Blender ships 100+ modifier types and describing every property of
    each would be effort spent on the wrong problem; what is here is the handful
    that decide what an export produces, mirroring _MODIFIER_FIELDS on the read side.
    """
    reject_unknown(params, ("object", "name", "type", "modifier", "settings", "index"),
                   "add_modifier")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    mod_type = str(take(params, "type", required=True, kind=str)).upper()

    valid = {t.identifier for t in
             bpy.types.Modifier.bl_rna.properties["type"].enum_items}
    if mod_type not in valid:
        raise MifOpError(
            "unknown modifier type '%s' for this Blender. NOTHING was changed. Types this addon can "
            "also configure: %s - any other type can be added but will carry Blender's defaults."
            % (mod_type, ", ".join(sorted(_MODIFIER_WRITES))))

    mod_name = take(params, "modifier", default=None) or mod_type.title()
    if mod_name in obj.modifiers:
        raise MifOpError("'%s' already has a modifier named '%s'. Names must be unique on an "
                         "object. NOTHING was changed." % (obj.name, mod_name))

    before = [m.name for m in obj.modifiers]
    # WHAT THE STACK PRODUCES NOW, so what it produces after is comparable. See the postcondition
    # at the end of this function for why a modifier's own fields cannot answer this.
    evaluated_before = _evaluated_counts(obj)
    mod = obj.modifiers.new(name=mod_name, type=mod_type)
    if mod is None:
        raise MifOpError("Blender refused to create a %s modifier on '%s'. NOTHING was changed."
                         % (mod_type, obj.name))

    applied = []
    try:
        settings = take(params, "settings", default=None)
        if settings:
            applied = _apply_modifier_settings(mod, settings, mod_type)

        index = take_int(params, "index", default=None)
        if index is not None:
            if index < 0 or index >= len(obj.modifiers):
                raise MifOpError("index %d is outside the stack (0-%d). NOTHING was changed."
                                 % (index, len(obj.modifiers) - 1))
            snap = selection_snapshot()
            try:
                select_only([obj])
                bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=index)
            finally:
                selection_restore(snap)
    except Exception:
        # CLEAN UP ON FAILURE, the same discipline decimate_mesh uses: a half-configured modifier
        # left in the stack silently changes every later export, and the caller was told NOTHING
        # was changed.
        try:
            obj.modifiers.remove(mod)
        except Exception:  # noqa: BLE001
            pass
        raise

    # READ BACK through the same describer list_modifiers uses, so add and read speak one
    # vocabulary and a settings value that did not take is visible rather than assumed.
    row = _modifier_dict(obj.modifiers[mod.name])

    # WHAT THE MODIFIER ACTUALLY DOES TO THE OBJECT, evaluated rather than inferred from its fields.
    #
    # A MASK modifier added with no vertex group masks EVERYTHING: the object evaluates to zero
    # vertices and disappears from the viewport and from every export, while this op returned a
    # clean success. Measured on 4.4 - a default cube, 8 vertices, evaluates to 0 the moment a bare
    # MASK is added. Nothing in the modifier's own properties says so, which is why reading them
    # back through _modifier_dict was not enough.
    #
    # The same measurement answers the quieter half. Most modifier types need to be POINTED at
    # something - a shrinkwrap target, a lattice, a hook, a curve - and until they are they sit in
    # the stack doing nothing. An inert modifier and a working one are identical in every field.
    evaluated_after = _evaluated_counts(obj)
    became_empty = (evaluated_before.get("verts", 0) > 0
                    and evaluated_after.get("verts", 1) == 0)
    inert = evaluated_before == evaluated_after

    return {
        "object": obj.name,
        "modifier": mod.name,
        "type": mod_type,
        "settingsApplied": applied,
        "stackBefore": before,
        "stackAfter": [m.name for m in obj.modifiers],
        "evaluatedBefore": evaluated_before,
        "evaluatedAfter": evaluated_after,
        # THE FIELD THAT MATTERS. True means the object is GONE from every render and every export
        # while still sitting in the outliner looking normal.
        "evaluatesToEmpty": became_empty,
        "evaluatedUnchanged": inert,
        "effectWarning": (
            "'%s' evaluates to ZERO vertices with this modifier on it - the object will not render "
            "and will export as an empty mesh, while still looking present in the outliner. A MASK "
            "with no vertex group does exactly this. Configure it or remove it."
            % obj.name) if became_empty else (
            "this modifier changed nothing about the evaluated mesh. Most types have to be POINTED "
            "at something - a target, a lattice, a curve, a vertex group - before they do anything, "
            "and an unconfigured one is indistinguishable from a working one in every field here."
            if inert else None),
        "index": list(obj.modifiers).index(obj.modifiers[mod.name]),
        "readBack": row,
        "note": ("added to the stack, NOT applied - it will still affect what export_mesh writes "
                 "unless useMeshModifiers is off. Call apply_modifier to bake it into the mesh."),
    }


def op_remove_modifier(params):
    """Remove a modifier from the stack WITHOUT applying it - the geometry is untouched."""
    reject_unknown(params, ("object", "name", "modifier"), "remove_modifier")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    mod_name = take(params, "modifier", required=True, kind=str)

    mod = obj.modifiers.get(mod_name)
    if mod is None:
        raise MifOpError(
            "'%s' has no modifier named '%s'. It has %d: %s. NOTHING was changed."
            % (obj.name, mod_name, len(obj.modifiers),
               ", ".join(m.name for m in obj.modifiers) or "(none)"))

    before = [m.name for m in obj.modifiers]
    counts_before = mesh_counts(obj)
    # WHAT THE STACK PRODUCED, which is the half that can actually change here.
    evaluated_before = _evaluated_counts(obj)
    obj.modifiers.remove(mod)
    after = [m.name for m in obj.modifiers]

    # POSTCONDITION, read back rather than assumed - modifiers.remove returns nothing.
    if mod_name in after:
        raise MifOpError("Blender reported no error but '%s' is still on the stack." % mod_name)

    evaluated_after = _evaluated_counts(obj)

    return {
        "object": obj.name,
        "modifier": mod_name,
        "removed": True,
        "stackBefore": before,
        "stackAfter": after,
        # TRUE BY CONSTRUCTION, and kept only because it is documented. modifiers.remove
        # never touches obj.data, so this compares the original mesh against itself and
        # cannot report anything else - it is a restatement of the note below, not
        # evidence. The field that can actually be wrong is the evaluated one.
        "meshUnchanged": mesh_counts(obj) == counts_before,
        "evaluatedBefore": evaluated_before,
        "evaluatedAfter": evaluated_after,
        # WHAT THE REMOVAL ACTUALLY DID. The mesh data is untouched either way; what moved
        # is what RENDERS and what export_mesh writes. False here means the modifier was
        # contributing nothing - it was inert, or already disabled - which is worth knowing
        # before concluding that removing it fixed something.
        "evaluatedChanged": bool(evaluated_before) and evaluated_before != evaluated_after,
        "note": ("removed WITHOUT applying - the mesh data is untouched, and whatever this modifier "
                 "was contributing to the viewport and to export_mesh is gone."),
    }


def _vertex_coords(mesh):
    """Every vertex position as a flat array, for comparing a mesh against itself.

    foreach_get RATHER THAN A PYTHON LOOP, the same technique _uv_fingerprint uses: a hundred
    thousand vertices through attribute access is seconds, and this runs on the main thread while
    Blender is blocked.
    """
    n = len(mesh.vertices)
    buf = array.array("f", [0.0]) * (3 * n)
    mesh.vertices.foreach_get("co", buf)
    return buf


def op_apply_modifier(params):
    """Bake a modifier into the mesh data. Destructive, and it says what it cost.

    THE COUNTS ARE THE POINT. modifier_apply reports {'FINISHED'} whether it changed
    the mesh or not, so this reads vertex and face counts before and after and
    reports both. A Mirror that doubled the mesh and a disabled modifier that did
    nothing are indistinguishable from the operator's return value alone.

    IT REFUSES ON MULTI-USER DATA, for the same reason apply_transform does:
    applying would rewrite a mesh other objects share, and Blender's own operator
    fails there anyway - refusing first with the sharing count is a better error
    than the operator's.

    dryRun reports what is on the stack and what applying would touch, and changes
    nothing.
    """
    reject_unknown(params, ("object", "name", "modifier", "dryRun"), "apply_modifier")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)
    mod_name = take(params, "modifier", required=True, kind=str)

    mod = obj.modifiers.get(mod_name)
    if mod is None:
        raise MifOpError(
            "'%s' has no modifier named '%s'. It has %d: %s. NOTHING was changed."
            % (obj.name, mod_name, len(obj.modifiers),
               ", ".join(m.name for m in obj.modifiers) or "(none)"))

    counts_before = mesh_counts(obj)
    # POSITIONS TOO. See the postcondition below for why counts were not enough.
    coords_before = _vertex_coords(obj.data)
    row = _modifier_dict(mod)

    if take_bool(params, "dryRun", default=False):
        return {
            "object": obj.name,
            "modifier": mod_name,
            "dryRun": True,
            "applied": False,
            "wouldApply": row,
            "counts": counts_before,
            "meshDataUsers": obj.data.users,
        }

    if obj.data.users > 1:
        raise MifOpError(
            "'%s' shares its mesh data with %d other object(s), and applying a modifier rewrites "
            "that data for every one of them. Make it single-user in Blender first. NOTHING was "
            "changed." % (obj.name, obj.data.users - 1))

    snap = selection_snapshot()
    try:
        select_only([obj])
        bpy.ops.object.modifier_apply(modifier=mod_name)
    finally:
        selection_restore(snap)

    still_there = mod_name in obj.modifiers
    counts_after = mesh_counts(obj)
    if still_there:
        raise MifOpError(
            "modifier_apply reported no error but '%s' is still on '%s'. The mesh may or may not "
            "have been changed - counts before %s, after %s." % (mod_name, obj.name,
                                                                 counts_before, counts_after))

    # TOPOLOGY AND MOVEMENT ARE DIFFERENT QUESTIONS, and this used to ask only the first.
    #
    # changedGeometry was `counts_before != counts_after`, and mesh_counts is vertex/edge/face
    # COUNTS. Every DEFORMING modifier - SHRINKWRAP, LATTICE, CAST, DISPLACE, SIMPLE_DEFORM, WAVE,
    # WARP, HOOK, SMOOTH, ARMATURE - moves vertices and changes no count, so all of them read as
    # unchanged. The note then stated a conclusion: "it was either disabled, or its settings
    # amounted to a no-op on this mesh". A shrinkwrap that had just conformed an entire mesh to
    # terrain was reported as having done nothing, in the confident register the rest of this addon
    # earns - which is worse than saying nothing, because somebody acts on it.
    changed_topology = counts_before != counts_after
    coords_after = _vertex_coords(obj.data)
    moved, max_delta = 0, 0.0
    if not changed_topology and len(coords_before) == len(coords_after):
        for i in range(0, len(coords_before), 3):
            dx = abs(coords_after[i] - coords_before[i])
            dy = abs(coords_after[i + 1] - coords_before[i + 1])
            dz = abs(coords_after[i + 2] - coords_before[i + 2])
            worst = max(dx, dy, dz)
            if worst > 1e-6:
                moved += 1
                if worst > max_delta:
                    max_delta = worst

    out = {
        "object": obj.name,
        "modifier": mod_name,
        "applied": True,
        "wasApplied": row,
        "countsBefore": counts_before,
        "countsAfter": counts_after,
        "changedTopology": changed_topology,
        # HOW MANY VERTICES ACTUALLY MOVED. None when the topology changed, because the two meshes
        # have no vertex-to-vertex correspondence to compare and a number there would be invented.
        "movedVertices": None if changed_topology else moved,
        "maxVertexDelta": None if changed_topology else round(max_delta, 6),
        # KEPT, and now true. It was False for every deforming modifier ever applied through here.
        "changedGeometry": changed_topology or moved > 0,
        "stackAfter": [m.name for m in obj.modifiers],
    }
    if not out["changedGeometry"]:
        out["note"] = (
            "the modifier applied cleanly and NOTHING changed - same vertex/face counts and not one "
            "vertex moved by more than 1e-6. It was either disabled, or its settings amounted to a "
            "no-op on this mesh. Said in words rather than returned as a bare ok, because the "
            "operator reports FINISHED either way.")
    elif not changed_topology:
        out["note"] = (
            "the counts are identical and %d vertex/vertices MOVED, by up to %.6f - a deforming "
            "modifier does exactly this, and judging by counts alone would have called it a no-op."
            % (moved, max_delta))
    return out


def op_rename_bones(params):
    """Rename armature bones through a map, refusing the collision that silently breaks skinning.

    BLENDER ALREADY SYNCS THE EASY PART, verified against 4.4 rather than assumed: setting
    bone.name renames the matching vertex group on every skinned mesh, and updates constraint
    subtargets and driver bone targets. This op does not re-implement any of that.

    WHAT IT EXISTS FOR is the case where that sync silently fails. Renaming a bone to a name another
    bone already holds gives you 'Hips.001' - a name nobody asked for - and leaves the vertex group
    under its OLD name, matching no bone. That part of the mesh then stops deforming, and nothing
    says so. Measured:

        bones ['Hips','Spine'] vgroups ['Hips','Spine']  ->  bones['Spine'].name = 'Hips'
        bones ['Hips','Hips.001'] vgroups ['Hips','Spine']

    So collisions are refused before anything is written, and every rename is READ BACK - a bone
    whose name is not what was asked for means Blender suffixed it, which is the failure.

    SWAPS ARE SUPPORTED because a retarget map is full of them (L/R mix-ups especially). A->B and
    B->A cannot be done in either order directly - whichever runs first collides - so the batch goes
    through unique temporary names first. That is invisible in the result and stated here because a
    reader will otherwise wonder why the two-pass exists.
    """
    reject_unknown(params, ("object", "name", "renames", "map", "dryRun"), "rename_bones")
    obj = get_object(take(params, "object", "name", required=True, kind=str))
    if obj.type != "ARMATURE":
        raise MifOpError("object '%s' is a %s, not an ARMATURE. list_objects with type:'ARMATURE' "
                         "finds one. NOTHING was changed." % (obj.name, obj.type))

    renames = take(params, "renames", "map", required=True)
    if not isinstance(renames, dict) or not renames:
        raise MifOpError("renames must be a non-empty object of {oldName: newName}. NOTHING was "
                         "changed.")

    bones = obj.data.bones
    existing = [b.name for b in bones]
    dry_run = bool(take(params, "dryRun"))

    # ---- every check BEFORE any write, so a bad batch changes nothing at all ----------------
    missing = [old for old in renames if old not in existing]
    if missing:
        raise MifOpError("no bone named %s on '%s'. It has %d: %s. NOTHING was changed."
                         % (", ".join("'%s'" % m for m in sorted(missing)), obj.name,
                            len(existing), ", ".join(existing[:24])))

    bad = [(o, n) for o, n in renames.items() if not isinstance(n, str) or not n.strip()]
    if bad:
        raise MifOpError("every new name must be a non-empty string; %s is not. NOTHING was changed."
                         % ", ".join("'%s' -> %r" % (o, n) for o, n in bad))

    # A target is only a collision if the name is held by a bone that is NOT being renamed away in
    # this same batch - otherwise a swap or a chain would be refused for no reason.
    freed = set(renames.keys())
    taken = {b for b in existing if b not in freed}
    collisions = {o: n for o, n in renames.items() if n in taken}
    if collisions:
        raise MifOpError(
            "these targets are already used by a bone this batch does NOT rename away: %s. Blender "
            "would not fail - it would silently suffix the bone ('Hips' becomes 'Hips.001') AND "
            "leave the vertex group under its old name, matching no bone, so that part of the mesh "
            "stops deforming with nothing to say so. Refused before anything was written. NOTHING "
            "was changed."
            % ", ".join("'%s' -> '%s'" % (o, n) for o, n in sorted(collisions.items())))

    dupes = [n for n in set(renames.values()) if list(renames.values()).count(n) > 1]
    if dupes:
        raise MifOpError("two bones are being renamed to the same name (%s), which collides with "
                         "itself whichever order it runs in. NOTHING was changed."
                         % ", ".join("'%s'" % d for d in sorted(dupes)))

    meshes = [o for o in bpy.data.objects
              if o.type == "MESH" and any(m.type == "ARMATURE" and m.object is obj
                                          for m in o.modifiers)]
    groups_before = {o.name: [g.name for g in o.vertex_groups] for o in meshes}

    if dry_run:
        return {
            "armature": obj.name,
            "dryRun": True,
            "changed": False,
            "wouldRename": dict(sorted(renames.items())),
            "skinnedMeshes": [o.name for o in meshes],
            "note": "DRY RUN - nothing was written. Every name checked: all sources exist, no "
                    "target collides with a bone kept by this batch, and no two bones want the "
                    "same name.",
        }

    # ---- TWO PASSES, so a swap is expressible ------------------------------------------------
    # A->B with B->A collides whichever runs first. Parking every source on a name nothing else can
    # hold makes the order irrelevant.
    temp = {}
    for i, old in enumerate(renames):
        tmp = "__mif_rn_%d__" % i
        bones[old].name = tmp
        temp[tmp] = renames[old]
    for tmp, new in temp.items():
        bones[tmp].name = new

    # ---- POSTCONDITION: read every name back -------------------------------------------------
    now = [b.name for b in bones]
    not_applied = {o: n for o, n in renames.items() if n not in now}
    if not_applied:
        raise MifOpError(
            "renamed, but %s are not present afterwards - Blender suffixed at least one name, which "
            "is the collision this op checks for and means the check missed a case. The armature is "
            "now: %s"
            % (", ".join("'%s'" % n for n in sorted(not_applied.values())), ", ".join(now[:24])))

    # Vertex groups matching NO bone. Reported whether or not this call caused them: an orphan is
    # the thing that makes a mesh stop deforming, and a caller renaming bones is the one person
    # certain to care.
    bone_names = set(now)
    orphans = {}
    for o in meshes:
        stray = [g.name for g in o.vertex_groups if g.name not in bone_names]
        if stray:
            orphans[o.name] = stray

    result = {
        "armature": obj.name,
        "renamed": dict(sorted(renames.items())),
        "renamedCount": len(renames),
        "changed": True,
        "boneNames": now,
        "skinnedMeshes": [o.name for o in meshes],
        "vertexGroupsBefore": groups_before,
        "vertexGroupsAfter": {o.name: [g.name for g in o.vertex_groups] for o in meshes},
    }
    if orphans:
        result["orphanedVertexGroups"] = orphans
        result["note"] = (
            "these vertex groups match NO bone on '%s' and so deform nothing: %s. Blender renames a "
            "vertex group along with its bone, so an orphan here was already orphaned before this "
            "call - reported because a caller renaming bones is the one person certain to care."
            % (obj.name, "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in sorted(orphans.items()))))
    else:
        result["orphanedVertexGroups"] = {}
        result["note"] = ("every vertex group on every skinned mesh still matches a bone. Blender "
                          "renames groups along with their bones; this confirms it happened rather "
                          "than assuming it.")
    return result


def op_set_bone_pose(params):
    """Pose a bone, and read the result back through the DEPSGRAPH.

    Character animation had zero coverage here: bones could be listed and renamed and nothing
    else. This was also unreachable through set_keyframe until the same day, because its dotted-
    path walk stripped subscripts and pose.bones["x"].location resolved to the bone COLLECTION.

    params:
      object (str, required)   the ARMATURE object
      bone (str, required)     pose bone name
      location [x,y,z]         in the bone's own space, as Blender stores it
      rotation [x,y,z]         euler radians; applied in the bone's current rotation mode
      quaternion [w,x,y,z]     for a bone in QUATERNION mode - refused together with rotation
      scale [x,y,z]

    THE READ-BACK IS EVALUATED. A pose bone with a constraint on it - an IK chain, a copy
    rotation, a limit - does not end up where you put it, and pose_bone.matrix is the raw value
    rather than the result. Reporting what you wrote back at you would be a proxy that cannot fail,
    so this reports both and says which is which.
    """
    reject_unknown(params, {"object", "name", "bone", "location", "rotation", "quaternion",
                            "scale"}, "set_bone_pose")
    obj = get_object(take(params, "object", "name", required=True))
    if obj.type != "ARMATURE":
        raise MifOpError("'%s' is a %s, not an ARMATURE. NOTHING was changed."
                         % (obj.name, obj.type))
    bone_name = take(params, "bone", default=None, kind=str)
    if not bone_name:
        raise MifOpError("'bone' is required - which pose bone to move. NOTHING was changed.")
    if obj.pose is None:
        raise MifOpError("'%s' has no pose data. NOTHING was changed." % obj.name)
    pb = obj.pose.bones.get(str(bone_name))
    if pb is None:
        known = [b.name for b in obj.pose.bones][:25]
        raise MifOpError("no bone named '%s' on '%s'. Present: %s. NOTHING was changed."
                         % (bone_name, obj.name, ", ".join(known) if known else "<none>"))

    if "rotation" in params and "quaternion" in params:
        raise MifOpError("pass rotation OR quaternion, not both - they are two answers to the same "
                         "question. NOTHING was changed.")

    def _vec(key, n):
        v = params.get(key)
        if v is None:
            return None
        if isinstance(v, dict):
            order = ("w", "x", "y", "z") if n == 4 else ("x", "y", "z")
            # SIXTH COPY OF THIS PARSER IN THE ADDON, and the defect was in all six: a dict read
            # with .get(axis, 0.0) turns {"mif":"typo"} into a zero vector - or a zero QUATERNION,
            # which is not even a rotation - and reports success.
            check_axis_dict(v, key, order)
            # FINITE TOO. A NaN quaternion is not a rotation any more than a zero one is, and it
            # reads back as nan while every field in the response agrees the pose was set.
            return finite_floats([v.get(k, 0.0) for k in order], key)
        if isinstance(v, (list, tuple)) and len(v) == n:
            return finite_floats(v, key)
        raise MifOpError("'%s' must be a %d-list%s, got %r. NOTHING was changed."
                         % (key, n, " or {x,y,z}" if n == 3 else " or {w,x,y,z}", v))

    loc = _vec("location", 3)
    rot = _vec("rotation", 3)
    quat = _vec("quaternion", 4)
    scl = _vec("scale", 3)
    if quat is not None and pb.rotation_mode != "QUATERNION":
        raise MifOpError("'%s' is in %s rotation mode, so a quaternion would be ignored - set "
                         "rotation instead, or change the bone's mode first. NOTHING was changed."
                         % (bone_name, pb.rotation_mode))
    if rot is not None and pb.rotation_mode == "QUATERNION":
        raise MifOpError("'%s' is in QUATERNION rotation mode, so a euler would be ignored - pass "
                         "quaternion instead. NOTHING was changed." % bone_name)
    if loc is None and rot is None and quat is None and scl is None:
        raise MifOpError("nothing to set - pass location, rotation, quaternion or scale. NOTHING "
                         "was changed.")

    if loc is not None:
        pb.location = loc
    if rot is not None:
        pb.rotation_euler = rot
    if quat is not None:
        pb.rotation_quaternion = quat
    if scl is not None:
        pb.scale = scl

    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    epb = ev.pose.bones.get(str(bone_name)) if ev.pose is not None else None
    return {
        "object": obj.name,
        "bone": pb.name,
        "rotationMode": pb.rotation_mode,
        # WHAT WAS WRITTEN, off the raw pose bone.
        "written": {
            "location": rnd(list(pb.location)),
            "rotationEuler": rnd(list(pb.rotation_euler)),
            "rotationQuaternion": rnd(list(pb.rotation_quaternion)),
            "scale": rnd(list(pb.scale)),
        },
        # WHERE IT ACTUALLY ENDED UP, after constraints. An IK chain, a Copy Rotation or a Limit
        # will move it somewhere else entirely, and pose_bone.matrix is the raw value.
        "evaluatedHeadWorld": rnd(list((ev.matrix_world @ epb.head))) if epb else None,
        "evaluatedTailWorld": rnd(list((ev.matrix_world @ epb.tail))) if epb else None,
        "constraints": [c.type for c in pb.constraints],
        "constrainedNote": ("evaluated* is read through the depsgraph and is where the bone really "
                            "is. With a constraint on the bone it will NOT match `written`, and "
                            "that difference is the constraint working rather than a fault."),
    }


def op_set_shape_key(params):
    """Set a shape key's value, and optionally its range. The other half of list_shape_keys.

    params:
      object (str, required)
      key (str, required)      the shape key name
      value (float)            the influence, normally 0..1
      sliderMin / sliderMax    the allowed range
      mute (bool)

    A value outside the slider range is CLAMPED BY BLENDER SILENTLY, so this reports what the key
    actually holds afterwards rather than what was asked for, and says when they differ.
    """
    reject_unknown(params, {"object", "name", "key", "shapeKey", "value", "sliderMin",
                            "sliderMax", "mute"}, "set_shape_key")
    obj = get_object(take(params, "object", "name", required=True))
    if obj.data is None or getattr(obj.data, "shape_keys", None) is None:
        raise MifOpError("'%s' has no shape keys. NOTHING was changed." % obj.name)
    key_name = take(params, "key", "shapeKey", default=None, kind=str)
    if not key_name:
        raise MifOpError("'key' is required - which shape key. NOTHING was changed.")
    blocks = obj.data.shape_keys.key_blocks
    kb = blocks.get(str(key_name))
    if kb is None:
        raise MifOpError("no shape key named '%s' on '%s'. Present: %s. NOTHING was changed."
                         % (key_name, obj.name, ", ".join(b.name for b in blocks) or "<none>"))

    smin = take_float(params, "sliderMin", default=None)
    smax = take_float(params, "sliderMax", default=None)
    if smin is not None and smax is not None and smin > smax:
        raise MifOpError("sliderMin %g is above sliderMax %g. NOTHING was changed." % (smin, smax))

    requested = take_float(params, "value", default=None)
    # RANGE FIRST, then the value: setting a value outside the OLD range would be clamped to it
    # and then look wrong even though the new range would have allowed it.
    if smin is not None:
        kb.slider_min = smin
    if smax is not None:
        kb.slider_max = smax
    if requested is not None:
        kb.value = requested
    if "mute" in params:
        kb.mute = take_bool(params, "mute", default=False)

    actual = round(float(kb.value), 6)
    return {
        "object": obj.name,
        "key": kb.name,
        "requestedValue": requested,
        "value": actual,
        # BLENDER CLAMPS SILENTLY to the slider range. Asking for 2.0 on a 0..1 key leaves 1.0 and
        # reports nothing, so the difference is named rather than left to be discovered in a render.
        "clamped": requested is not None and abs(actual - requested) > 1e-6,
        "sliderMin": round(float(kb.slider_min), 6),
        "sliderMax": round(float(kb.slider_max), 6),
        "mute": bool(kb.mute),
    }


_VGROUP_KEYS = {"object", "name", "group", "vertices", "weight", "weights", "mode", "create",
                "remove"}
_SHAPEKEY_KEYS = {"object", "name", "key", "fromMix", "value", "sliderMin", "sliderMax"}

_VGROUP_MODES = ("REPLACE", "ADD", "SUBTRACT")


def op_set_vertex_weights(params):
    """Create a vertex group and put weights in it - which nothing in this addon could do.

    THE HOLE THIS CLOSES, found by asking what the addon consumes and cannot produce - the fourth
    time that question has paid, after collections, empties and armatures. list_vertex_groups,
    normalize_weights and transfer_weights all operate on vertex groups, and grepping the whole
    addon for vertex_groups.new returned NOTHING. So a rig imported with weights could be
    normalised and transferred, and a rig built here could not be weighted at all. transfer_weights
    refuses rather than producing an unskinned result, which is that op being honest about a hole it
    could not fill.

    THE POSTCONDITION IS THE WEIGHTS, NOT THE GROUP. A vertex group that exists with every weight at
    zero deforms nothing and reads back perfectly - it has a name, an index, and it appears in
    list_vertex_groups exactly like a working one. So this reads the stored weight back for every
    vertex it touched and reports the range, and reports how many vertices ended up with a NON-ZERO
    weight, which is the number that decides whether anything will actually move.

    vg.weight(i) RAISES RuntimeError for a vertex that is not in the group rather than returning 0,
    on every build, so the read-back catches per vertex rather than assuming membership.

    params:
      object (str)              required, must be a MESH
      group / name (str)        the vertex group. Created if absent unless create:false.
      vertices (list[int])      which vertices. Omit to create an empty group.
      weight (float)            one weight for all of them. Default 1.0.
      weights (list[float])     a weight per vertex, parallel to `vertices`. Excludes `weight`.
      mode (str)                REPLACE (default) | ADD | SUBTRACT
      create (bool)             create the group if it does not exist. Default true.
      remove (bool)             REMOVE the listed vertices from the group instead of weighting them
    """
    reject_unknown(params, _VGROUP_KEYS, "set_vertex_weights")
    src = take(params, "object", required=True, kind=str)
    group_name = take(params, "group", "name", required=True, kind=str)
    if len(str(group_name)) > _ID_NAME_LIMIT:
        raise MifOpError("the group name is %d characters and Blender truncates at %d on every "
                         "build, so the group you get would not be the one you named. NOTHING was "
                         "changed." % (len(str(group_name)), _ID_NAME_LIMIT))
    mode = str(take(params, "mode", default="REPLACE", kind=str)).upper()
    if mode not in _VGROUP_MODES:
        raise MifOpError("mode must be one of %s, got '%s'. NOTHING was changed."
                         % (", ".join(_VGROUP_MODES), mode))
    create = take_bool(params, "create", default=True)
    remove = take_bool(params, "remove", default=False)

    raw_verts = params.get("vertices")
    if raw_verts is not None and not isinstance(raw_verts, (list, tuple)):
        raise MifOpError("'vertices' must be a list of vertex indices, got %s. NOTHING was changed."
                         % type(raw_verts).__name__)
    raw_weights = params.get("weights")
    single = take_float(params, "weight", default=None)
    if raw_weights is not None and single is not None:
        raise MifOpError("pass weight OR weights, not both - one is a value for every vertex and "
                         "the other is a value per vertex, and they cannot both win. NOTHING was "
                         "changed.")
    if raw_weights is not None:
        if not isinstance(raw_weights, (list, tuple)):
            raise MifOpError("'weights' must be a list of numbers, got %s. NOTHING was changed."
                             % type(raw_weights).__name__)
        if raw_verts is None or len(raw_weights) != len(raw_verts):
            raise MifOpError("'weights' has %d entries and 'vertices' has %s - they are parallel "
                             "lists and must match. NOTHING was changed."
                             % (len(raw_weights), len(raw_verts) if raw_verts is not None else 0))
    if remove and (raw_weights is not None or single is not None):
        raise MifOpError("remove:true takes vertices OUT of the group, so a weight has nothing to "
                         "apply to. NOTHING was changed.")

    obj = get_object(src, want_mesh=True)
    mesh = obj.data
    verts = []
    if raw_verts is not None:
        try:
            verts = [int(i) for i in raw_verts]
        except (TypeError, ValueError):
            raise MifOpError("'vertices' must be whole numbers. NOTHING was changed.")
        highest = len(mesh.vertices) - 1
        bad = sorted(i for i in verts if i < 0 or i > highest)
        if bad:
            raise MifOpError("vertex index %d is out of range - '%s' has %d vertice(s), so the "
                             "highest index is %d. NOTHING was changed."
                             % (bad[0], obj.name, len(mesh.vertices), highest))

    group = obj.vertex_groups.get(group_name)
    created = False
    if group is None:
        if not create:
            known = [g.name for g in obj.vertex_groups][:25]
            raise MifOpError("'%s' has no vertex group named '%s' and create:false was given. "
                             "Groups: %s. NOTHING was changed."
                             % (obj.name, group_name, ", ".join(known) if known else "<none>"))
        if remove:
            raise MifOpError("'%s' has no vertex group named '%s', so there is nothing to remove "
                             "vertices from. NOTHING was changed." % (obj.name, group_name))
        group = obj.vertex_groups.new(name=str(group_name))
        created = True
        # A DUPLICATE NAME GIVES THE NEW GROUP A .001 SUFFIX and leaves the incumbent alone -
        # uniform on 3.6, 4.4 and 5.0, unlike OBJECT renaming which reverses across the 3.6/4.x
        # line. It is reported rather than refused because a second group is a legitimate thing to
        # want, but a caller who then looks for their name by string needs to know.
        if group.name != str(group_name):
            created = True

    weights_before = {}
    for i in verts:
        try:
            weights_before[i] = round(group.weight(i), 6)
        except RuntimeError:
            weights_before[i] = None      # not in the group at all - a distinct state from 0.0

    if verts:
        if remove:
            group.remove(verts)
        elif raw_weights is not None:
            for index, w in zip(verts, raw_weights):
                group.add([index], float(w), mode)
        else:
            group.add(verts, 1.0 if single is None else single, mode)

    # READ BACK PER VERTEX. vg.weight() raises for a vertex outside the group rather than returning
    # zero, so membership and a zero weight are distinguishable - and they are different states: one
    # deforms nothing, the other is not in the group to be normalised or transferred at all.
    after, nonzero, missing = {}, 0, []
    for i in verts:
        try:
            value = round(group.weight(i), 6)
            after[i] = value
            if value > 0.0:
                nonzero += 1
        except RuntimeError:
            after[i] = None
            missing.append(i)
    if verts and not remove and missing:
        raise MifOpError("%d of %d vertices are NOT in group '%s' after the write: %s. The group "
                         "exists and those vertices are outside it, which deforms nothing."
                         % (len(missing), len(verts), group.name, missing[:10]))

    total_in_group = 0
    for v in mesh.vertices:
        try:
            group.weight(v.index)
            total_in_group += 1
        except RuntimeError:
            pass
    values = [w for w in after.values() if w is not None]
    return {
        "ok": True,
        "object": obj.name,
        "group": group.name,
        "groupIndex": group.index,
        "created": created,
        "requestedName": str(group_name),
        "nameWasSuffixed": group.name != str(group_name),
        "mode": mode if not remove else "REMOVE",
        "verticesTouched": len(verts),
        # THE NUMBER THAT DECIDES WHETHER ANYTHING MOVES. A group full of zero weights is a group
        # that exists and deforms nothing, and it reads back exactly like a working one.
        "verticesWithNonZeroWeight": nonzero,
        "verticesInGroup": total_in_group,
        "weightRange": [min(values), max(values)] if values else None,
        "weightsBefore": weights_before if len(weights_before) <= 32 else None,
        "note": ("every weight written is 0, so this group exists and deforms NOTHING - which reads "
                 "back identically to a working one in list_vertex_groups.")
        if (values and not nonzero and not remove) else
        (("the group name was taken, so Blender created '%s' instead of '%s' - the existing group "
          "was left alone. Anything looking this up by name needs the new one."
          % (group.name, group_name)) if group.name != str(group_name) else None),
    }


def op_add_shape_key(params):
    """Add a shape key - the other thing five ops could consume and nothing could produce.

    set_shape_key drives a key's value and list_shape_keys reports them, and nothing anywhere could
    CREATE one, so a mesh without shape keys could never be given any.

    THE FIRST KEY IS THE BASIS AND IT IS NOT A SHAPE. Blender names the first key added on a mesh
    'Basis' and every later key is stored RELATIVE to it - verified on 3.6, 4.4 and 5.0, where the
    second key came back with relative_key 'Basis'. A caller adding one key to a bare mesh gets the
    rest position and nothing that can move, which is why this reports basisCreated separately
    rather than counting it as the key that was asked for.

    fromMix TAKES THE CURRENT EVALUATED MIX rather than the rest shape, which is how a corrective
    key is made. Off by default: a key silently capturing whatever the sliders happened to be set
    to is a surprise, not a convenience.

    params:
      object (str)         required, must be a MESH
      name / key (str)     the key's name. Default 'Key'.
      fromMix (bool)       capture the current mix instead of the rest shape. Default false.
      value (float)        the key's influence, 0-1
      sliderMin / sliderMax (float)
    """
    reject_unknown(params, _SHAPEKEY_KEYS, "add_shape_key")
    src = take(params, "object", required=True, kind=str)
    name = str(take(params, "name", "key", default="Key", kind=str))
    if len(name) > _ID_NAME_LIMIT:
        raise MifOpError("the key name is %d characters and Blender truncates at %d. NOTHING was "
                         "created." % (len(name), _ID_NAME_LIMIT))
    from_mix = take_bool(params, "fromMix", default=False)
    value = take_float(params, "value", default=None)
    smin = take_float(params, "sliderMin", default=None)
    smax = take_float(params, "sliderMax", default=None)
    if smin is not None and smax is not None and smax < smin:
        raise MifOpError("sliderMax %g is below sliderMin %g. NOTHING was created." % (smax, smin))

    obj = get_object(src, want_mesh=True)
    existing = obj.data.shape_keys
    had = list(existing.key_blocks.keys()) if existing else []
    if name in had:
        raise MifOpError("'%s' already has a shape key named '%s'. Blender would create '%s.001' "
                         "beside it, which is rarely what somebody adding a named key wants - pick "
                         "another name or drive the existing one with set_shape_key. NOTHING was "
                         "created." % (obj.name, name, name))

    basis_created = not had
    key = obj.shape_key_add(name=name, from_mix=from_mix)
    if key is None:
        raise MifOpError("shape_key_add returned None for '%s' - Blender reports failure that way "
                         "rather than raising. NOTHING was created." % name)
    if value is not None:
        key.value = value
    if smin is not None:
        key.slider_min = smin
    if smax is not None:
        key.slider_max = smax

    blocks = obj.data.shape_keys.key_blocks
    return {
        "ok": True,
        "object": obj.name,
        "key": key.name,
        "keyCount": len(blocks),
        "keys": list(blocks.keys()),
        # THE FIRST KEY ON A BARE MESH IS THE REST POSITION. Reported so a caller who asked for one
        # key and got 'Basis' can see why nothing moves.
        "basisCreated": basis_created,
        "relativeTo": key.relative_key.name if getattr(key, "relative_key", None) else None,
        "value": round(float(key.value), 6),
        "sliderRange": [round(float(key.slider_min), 6), round(float(key.slider_max), 6)],
        "fromMix": from_mix,
        "note": ("this was the FIRST key on the mesh, so Blender made it the BASIS - the rest "
                 "position. It holds the shape the mesh already has and moves nothing. Add a "
                 "second key and edit its data to get something that deforms.")
        if basis_created else None,
    }


_ADDBONES_KEYS = {"object", "name", "armature", "bones", "replaceExisting"}


def op_add_bones(params):
    """Add bones to an armature that already exists - which nothing could do.

    THE HOLE THIS CLOSES. create_armature builds bones and every other rigging op edits what is
    already there: list_bones, rename_bones, set_bone_pose, the weight ops. edit_bones appears in
    exactly ONE place in the whole addon, inside create_armature, so a skeleton that arrived through
    import_mesh could be renamed, posed and weighted and never given another bone. Adding a socket
    bone, or an ik_hand_gun beside the hand, is ordinary skeletal work for a game engine and was
    impossible through the typed path.

    BONES ONLY EXIST IN EDIT MODE, which is the whole difficulty, and RESTORING THE MODE IS A
    POSTCONDITION rather than a courtesy - being left in edit mode strands every op that follows.
    Same discipline as create_armature, and the mode is asserted afterwards rather than assumed.

    A PARENT MAY BE A BONE THAT WAS ALREADY THERE or one created in this same call, which is the
    difference from create_armature: it resolves against the live edit_bones list, so an imported
    skeleton's existing bone is a legal parent.

    A ZERO-LENGTH BONE IS REFUSED. Blender DELETES a bone whose head equals its tail when edit mode
    is left - silently, with no error - so the count check at the end would catch it and the caller
    would get a confusing failure instead of the real reason.

    params:
      object / name / armature (str)  the armature object. Required.
      bones (list)   [{"name","head":[x,y,z],"tail":[x,y,z],"parent":"...","connect":bool,
                       "roll":float}]   Parents must exist already or appear earlier in the list.
      replaceExisting (bool)   default false. Adding a bone whose name is taken gives it a .001
                               suffix, and anything looking it up by name afterwards finds the wrong
                               one - so it is refused unless this says otherwise.
    """
    reject_unknown(params, _ADDBONES_KEYS, "add_bones")
    obj = get_object(take(params, "object", "name", "armature", required=True, kind=str))
    if obj.type != "ARMATURE":
        raise MifOpError("'%s' is a %s, not an ARMATURE. NOTHING was changed."
                         % (obj.name, obj.type))
    data = obj.data
    bones = params.get("bones")
    if not isinstance(bones, (list, tuple)) or not bones:
        raise MifOpError("'bones' must be a non-empty list of {name, head, tail}. NOTHING was "
                         "changed.")

    # VALIDATED IN FULL BEFORE ANY MODE CHANGE, for create_armature's reason: a refusal partway
    # through would leave the armature half-built AND Blender sitting in edit mode.
    replace = take_bool(params, "replaceExisting", default=False)
    existing = {b.name for b in data.bones}
    planned = set()
    for i, b in enumerate(bones):
        # A STRING, not merely truthy - and str() would have hidden it. A dict name passed
        # this guard and str({...}) turned it into a bone literally called
        # "{'mif': 'not-a-value'}", ACCEPTED and reported as created. My own code from the
        # same day, found by the matrix pass once it learned to corrupt list-of-dict entries.
        if not isinstance(b, dict) or not isinstance(b.get("name"), str) or not b["name"]:
            raise MifOpError("bones[%d] needs a 'name' as text, got %r. NOTHING was changed."
                             % (i, b.get("name") if isinstance(b, dict) else b))
        nm = b["name"]
        if len(nm) > _ID_NAME_LIMIT:
            raise MifOpError("bones[%d] name is %d characters and Blender truncates at %d, so the "
                             "bone you get would not be the one you named. NOTHING was changed."
                             % (i, len(nm), _ID_NAME_LIMIT))
        if nm in existing and not replace:
            raise MifOpError(
                "'%s' already has a bone named '%s'. Blender would add '%s.001' beside it and "
                "anything looking the name up afterwards would find the wrong bone - including the "
                "vertex groups that skin it. Pass replaceExisting:true to add it anyway, or pick "
                "another name. NOTHING was changed." % (obj.name, nm, nm))
        if nm in planned:
            raise MifOpError("bones[%d] repeats the name '%s' inside this call. NOTHING was "
                             "changed." % (i, nm))
        planned.add(nm)
        for end in ("head", "tail"):
            v = b.get(end)
            if not isinstance(v, (list, tuple)) or len(v) < 3:
                raise MifOpError("bones[%d] needs '%s' as [x,y,z]. NOTHING was changed." % (i, end))
        head = [float(x) for x in b["head"][:3]]
        tail = [float(x) for x in b["tail"][:3]]
        if head == tail:
            raise MifOpError(
                "bones[%d] ('%s') has head == tail, so it has ZERO length. Blender DELETES a "
                "zero-length bone when edit mode is left, silently and with no error, so this would "
                "report a confusing count mismatch instead of the real reason. NOTHING was changed."
                % (i, nm))
        parent = b.get("parent")
        if parent is not None and str(parent) not in existing and str(parent) not in planned:
            known = sorted(existing)[:25]
            raise MifOpError(
                "bones[%d] ('%s') names parent '%s', which is neither on '%s' already nor earlier "
                "in this list. Existing bones: %s. NOTHING was changed."
                % (i, nm, parent, obj.name, ", ".join(known) if known else "(none)"))

    count_before = len(data.bones)
    snap = selection_snapshot()
    made = []
    try:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        ebs = data.edit_bones
        for b in bones:
            eb = ebs.new(str(b["name"]))
            eb.head = [float(x) for x in b["head"][:3]]
            eb.tail = [float(x) for x in b["tail"][:3]]
            if b.get("roll") is not None:
                eb.roll = float(b["roll"])
            if b.get("parent"):
                eb.parent = ebs[str(b["parent"])]
                # use_connect MOVES THE CHILD'S HEAD onto the parent's tail. Off by default, because
                # a socket bone placed at a deliberate offset would silently jump.
                eb.use_connect = bool(b.get("connect", False))
            made.append(eb.name)
    finally:
        # ALWAYS. Left in edit mode, every op after this one fails on an editor nobody asked for.
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
        selection_restore(snap)

    mode_after = bpy.context.object.mode if bpy.context.object else "OBJECT"
    if mode_after != "OBJECT":
        raise MifOpError("added bones to '%s' but Blender is left in %s mode, which strands every "
                         "op after this one." % (obj.name, mode_after))

    # COUNTED FROM data.bones, NOT from the edit_bones list above. edit_bones exist only in edit
    # mode; the real bones appear when it is left, and one that did not survive the transition
    # would still be in the list this op built.
    now = {b.name for b in data.bones}
    landed = [n for n in made if n in now]
    if len(landed) != len(bones):
        lost = [n for n in made if n not in now]
        raise MifOpError("asked for %d bone(s); %d survived leaving edit mode. Missing: %s."
                         % (len(bones), len(landed), ", ".join(lost) or "(renamed)"))

    return {
        "ok": True,
        "object": obj.name,
        "added": landed,
        "boneCountBefore": count_before,
        "boneCountAfter": len(data.bones),
        "bones": sorted(now),
        # THE MODE IS REPORTED because it is the postcondition that matters to the NEXT call, not
        # to this one.
        "modeAfter": mode_after,
        "parentedTo": {str(b["name"]): str(b["parent"]) for b in bones if b.get("parent")} or None,
        "note": ("these bones deform NOTHING until a mesh is weighted to them - add_bones only "
                 "builds the skeleton. set_vertex_weights puts weights on the groups that match "
                 "them, and a vertex group whose name matches no bone is skinned to nothing."),
    }

OPS = {
    "set_bone_pose": op_set_bone_pose,
    "set_shape_key": op_set_shape_key,
    "list_bones": op_list_bones,
    "list_shape_keys": op_list_shape_keys,
    "list_vertex_groups": op_list_vertex_groups,
    "list_modifiers": op_list_modifiers,
    "normalize_weights": op_normalize_weights,
    "set_vertex_weights": op_set_vertex_weights,
    "add_shape_key": op_add_shape_key,
    "add_bones": op_add_bones,
    "transfer_weights": op_transfer_weights,
    "add_modifier": op_add_modifier,
    "remove_modifier": op_remove_modifier,
    "apply_modifier": op_apply_modifier,
    "rename_bones": op_rename_bones,
}
