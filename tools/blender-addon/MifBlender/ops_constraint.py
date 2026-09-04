"""Object and bone constraints - the procedural half of posing, and the one that fails silently.

WHY A SEPARATE MODULE. Constraints apply to OBJECTS and to POSE BONES with the same API and the
same failure modes, so splitting them between ops_scene and ops_rig would put one rule in two
places. This addon has spent a day finding out what that costs.

WHY THEY MATTER MORE THAN aim_object. aim_object points something at a target ONCE, and the moment
either end moves the aim is stale. A Track To constraint stays correct as the target moves, which
is why it - not a computed euler - is how camera and light rigs are actually built. Nothing here
could create one.

=============================================================================
THE MEASUREMENT PROBLEM, WHICH IS THE WHOLE DESIGN OF THIS FILE
=============================================================================
A CONSTRAINT DOES NOT TOUCH obj.matrix_world. It is applied by the depsgraph when the scene
evaluates, and the object's own transform is left exactly as it was. So:

  * reading obj.matrix_world after adding a constraint reports NO CHANGE, every time, for every
    constraint that works perfectly;
  * reading back the influence value you just wrote is a proxy that cannot fail;
  * and a constraint pointing at a deleted target, or one whose influence is zero, looks
    identical to a working one from anywhere except the evaluated depsgraph.

So every op here measures through evaluated_get(depsgraph). add_constraint samples the evaluated
world matrix before and after and reports how far the object actually MOVED - which is the only
evidence that the constraint does anything at all.
"""
import bpy

from .ops_common import (MifOpError, get_object, reject_unknown, rnd, take, take_bool, take_float)


def _holder(obj, bone_name, verb):
    """The object or pose bone that owns the constraint stack, with a message that names which."""
    if not bone_name:
        return obj, "object"
    if obj.type != "ARMATURE":
        raise MifOpError("'bone' was given but '%s' is a %s, not an ARMATURE. NOTHING was %s."
                         % (obj.name, obj.type, verb))
    if obj.pose is None:
        raise MifOpError("'%s' has no pose data. NOTHING was %s." % (obj.name, verb))
    pb = obj.pose.bones.get(str(bone_name))
    if pb is None:
        known = [b.name for b in obj.pose.bones][:25]
        raise MifOpError("no bone named '%s' on '%s'. Present: %s. NOTHING was %s."
                         % (bone_name, obj.name, ", ".join(known) if known else "<none>", verb))
    return pb, "bone '%s'" % pb.name


def _valid_types():
    return {i.identifier for i in bpy.types.Constraint.bl_rna.properties["type"].enum_items}


def _constraint_row(c):
    tgt = getattr(c, "target", None)
    return {
        "name": c.name,
        "type": c.type,
        "influence": round(float(getattr(c, "influence", 1.0)), 6),
        "mute": bool(getattr(c, "mute", False)),
        "target": tgt.name if tgt is not None else None,
        "subtarget": getattr(c, "subtarget", None) or None,
        # A CONSTRAINT WITH A MISSING TARGET IS THE SILENT FAILURE. Blender leaves it in place,
        # contributes nothing, and shows a red field in the UI that no API caller can see.
        #
        # is_valid ALONE IS NOT ENOUGH, measured on 5.0.1 and it used to be all this read. Created
        # with no target, is_valid is correctly False for 19 of the 20 target-taking types. Have
        # the target DELETED afterwards and it stays TRUE - through view_layer.update(),
        # update_tag(), a depsgraph update, and on the evaluated copy. It is simply never
        # recomputed. That is the exact case this docstring describes and the exact case the flag
        # could not report, so a deleted target read as a healthy constraint.
        "isValid": bool(getattr(c, "is_valid", True)) and not _target_missing(c),
        "needsTarget": hasattr(c, "target"),
        # WHICH OF THE TWO IT WAS. isValid false because Blender says so, and isValid false because
        # the target is gone, want different fixes - re-point it, or look at why it was deleted -
        # and collapsing them into one boolean loses that.
        "targetMissing": _target_missing(c),
    }


# PIVOT is the ONE target-taking type whose is_valid is TRUE with no target, and it is right: a
# Pivot constraint with no target pivots around the object's own point. Measured across all 29
# types on 5.0.1 - 20 have a .target, 19 of those report is_valid False when created without one.
# Treating PIVOT like the other nineteen would report a correctly configured constraint as broken,
# and a false failure is worse than a false pass because it teaches the reader to ignore the field.
_TARGET_OPTIONAL = ("PIVOT",)


def _target_missing(c):
    """True when this constraint NEEDS a target and has none."""
    if not hasattr(c, "target") or c.type in _TARGET_OPTIONAL:
        return False
    return getattr(c, "target", None) is None


def _evaluated_matrix(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(dg).matrix_world.copy()


def op_list_constraints(params):
    """Every constraint on an object or one of its pose bones, and whether each one is VALID.

    params:
      object (str, required)
      bone (str)    read the bone's stack instead of the object's

    invalidCount is the field to read. A constraint whose target has been deleted stays in the
    stack, contributes nothing, and is indistinguishable from a working one everywhere except
    is_valid - Blender shows it red in the UI and reports it nowhere an API caller can reach.
    """
    reject_unknown(params, {"object", "name", "bone"}, "list_constraints")
    obj = get_object(take(params, "object", "name", required=True))
    holder, label = _holder(obj, take(params, "bone", default=None, kind=str), "read")
    rows = [_constraint_row(c) for c in holder.constraints]
    invalid = [r["name"] for r in rows if not r["isValid"]]
    return {
        "object": obj.name,
        "owner": label,
        "count": len(rows),
        "constraints": rows,
        "invalidCount": len(invalid),
        "invalid": invalid,
        "mutedCount": sum(1 for r in rows if r["mute"]),
    }


def op_add_constraint(params):
    """Add a constraint, and MEASURE that it actually moves the thing.

    params:
      object (str, required)
      type (str, required)     TRACK_TO | COPY_LOCATION | COPY_ROTATION | COPY_TRANSFORMS |
                               CHILD_OF | LIMIT_LOCATION | DAMPED_TRACK | IK | ... validated
                               against this Blender's own enum
      bone (str)               put it on this pose bone instead of the object
      target (str)             the object it follows
      subtarget (str)          a bone on the target, for an armature target
      influence (float)        0..1, default 1
      name (str)               constraint name

    THE POSTCONDITION IS MOVEMENT, MEASURED THROUGH THE DEPSGRAPH. A constraint does not touch
    obj.matrix_world, so reading the object's own transform reports no change for every constraint
    that works. This samples the EVALUATED world matrix before and after and reports the distance
    and angle the object actually moved.

    A zero delta is NOT reported as failure - a Copy Location onto something already in the right
    place legitimately moves nothing - but it IS reported, alongside the constraint's validity, so
    the difference between "nothing to do" and "pointing at a deleted object" is visible.
    """
    reject_unknown(params, {"object", "name", "type", "bone", "target", "subtarget",
                            "influence", "constraintName"}, "add_constraint")
    obj = get_object(take(params, "object", "name", required=True))
    holder, label = _holder(obj, take(params, "bone", default=None, kind=str), "added")

    kind = take(params, "type", default=None, kind=str)
    if not kind:
        raise MifOpError("'type' is required. NOTHING was added.")
    kind = str(kind).upper()
    valid = _valid_types()
    if valid and kind not in valid:
        raise MifOpError("unknown constraint type '%s'. Valid: %s. NOTHING was added."
                         % (kind, ", ".join(sorted(valid))))

    target_name = take(params, "target", default=None, kind=str)
    target = None
    if target_name:
        target = bpy.data.objects.get(str(target_name))
        if target is None:
            raise MifOpError("no target object named '%s'. NOTHING was added." % target_name)
        if target is obj and not take(params, "bone", default=None, kind=str):
            raise MifOpError("'%s' cannot be constrained to itself. NOTHING was added." % obj.name)

    influence = take_float(params, "influence", default=None)
    if influence is not None and not (0.0 <= influence <= 1.0):
        raise MifOpError("influence must be between 0 and 1, got %g. NOTHING was added."
                         % influence)

    before = _evaluated_matrix(obj)

    con = holder.constraints.new(type=kind)
    named = take(params, "constraintName", default=None, kind=str)
    if named:
        con.name = str(named)
    if target is not None:
        if not hasattr(con, "target"):
            holder.constraints.remove(con)
            raise MifOpError("a %s constraint takes no target, but one was given. NOTHING was "
                             "added." % kind)
        con.target = target
        sub = take(params, "subtarget", default=None, kind=str)
        if sub:
            con.subtarget = str(sub)
    if influence is not None:
        con.influence = influence

    bpy.context.view_layer.update()
    after = _evaluated_matrix(obj)
    moved = (after.to_translation() - before.to_translation()).length
    try:
        turned = before.to_quaternion().rotation_difference(after.to_quaternion()).angle
    except (AttributeError, ValueError):
        turned = 0.0

    row = _constraint_row(con)
    return {
        "object": obj.name,
        "owner": label,
        "constraint": row,
        # THE EVIDENCE. Measured through the depsgraph, because obj.matrix_world does not move when
        # a constraint is added and reading it would report every constraint as inert.
        "movedDistance": round(float(moved), 6),
        "turnedRadians": round(float(turned), 6),
        "hadEffect": moved > 1e-6 or turned > 1e-6,
        "effectNote": ("hadEffect false is not necessarily wrong - a Copy Location onto something "
                       "already in place moves nothing. Read it beside isValid: valid and inert is "
                       "'nothing to do', INVALID and inert is a target that does not resolve."),
        "constraintCount": len(holder.constraints),
    }


def op_remove_constraint(params):
    """Remove a constraint by name, and report where the thing went when it was taken off.

    params:
      object (str, required)
      constraintName (str, required)
      bone (str)

    Removing a constraint MOVES the object back to its own transform, and that movement is the
    proof the constraint was doing something. It is measured for the same reason adding one is.
    """
    reject_unknown(params, {"object", "name", "constraintName", "bone"}, "remove_constraint")
    obj = get_object(take(params, "object", "name", required=True))
    holder, label = _holder(obj, take(params, "bone", default=None, kind=str), "removed")
    want = take(params, "constraintName", default=None, kind=str)
    if not want:
        raise MifOpError("'constraintName' is required - list them with list_constraints. "
                         "NOTHING was removed.")
    con = holder.constraints.get(str(want))
    if con is None:
        known = [c.name for c in holder.constraints]
        raise MifOpError("no constraint named '%s' on %s. Present: %s. NOTHING was removed."
                         % (want, label, ", ".join(known) if known else "<none>"))

    row = _constraint_row(con)
    before = _evaluated_matrix(obj)
    count_before = len(holder.constraints)
    holder.constraints.remove(con)
    bpy.context.view_layer.update()
    after = _evaluated_matrix(obj)
    moved = (after.to_translation() - before.to_translation()).length

    return {
        "object": obj.name,
        "owner": label,
        "removed": row,
        "constraintCountBefore": count_before,
        "constraintCountAfter": len(holder.constraints),
        # COUNTED, not assumed. constraints.remove() returns None either way.
        "countsAgree": len(holder.constraints) == count_before - 1,
        "movedDistance": round(float(moved), 6),
        "wasDoingSomething": moved > 1e-6,
    }


OPS = {
    "list_constraints": op_list_constraints,
    "add_constraint": op_add_constraint,
    "remove_constraint": op_remove_constraint,
}
