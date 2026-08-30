"""Creating geometry, and placing it without baking.

WHAT WAS MISSING. Every mesh in this addon entered through import_mesh. The bridge could
decimate, bevel, unwrap, skirt and export - and could not originate a single vertex. That
made it an EDITOR of assets authored elsewhere, which is the right shape for a round trip
and the wrong one for building an asset in Blender, which is what Andre asked for on
2026-08-30.

=============================================================================
TWO THINGS THE bpy.ops PRIMITIVE CALLS DO THAT HAVE TO BE HANDLED
=============================================================================

1. THEY NAME THE OBJECT THEMSELVES, AND RENAME ON COLLISION. bpy.ops.mesh.primitive_cube_add
   makes an object called "Cube"; the second one is "Cube.001". Blender never fails and never
   overwrites - it appends a number. So a caller who asked for "Crate" can get "Crate.003",
   and every op here echoes the name the object ACTUALLY has. Reporting the requested name
   would be wrong exactly as often as a scene has a name clash, which is often.

2. THEY OPERATE ON THE ACTIVE OBJECT AND THE ACTIVE COLLECTION, which means they depend on
   selection state a previous op left behind. Each op here takes a selection snapshot and
   restores it, the same discipline the mesh ops already use, so calling one does not quietly
   change what the next one acts on.

WHY transform_object IS HERE AND NOT IN ops_mesh. apply_transform and set_origin both BAKE:
they write the transform into the mesh data and leave the object at identity. That is what an
export pipeline wants and it is NOT how you place a second object next to a first. The round
trip papers over the gap by asserting isIdentityTransform stays true; as soon as a scene holds
more than one object that assertion is no longer the goal.
"""
import bpy

from .ops_common import (MifOpError, get_object, mesh_counts, object_info, reject_unknown,
                         rnd, selection_restore, selection_snapshot, take, take_bool,
                         take_float, take_int)

# kind -> (bpy.ops function name, the extra kwargs it accepts beyond size/location)
PRIMITIVES = {
    "cube":     ("primitive_cube_add", ()),
    "sphere":   ("primitive_uv_sphere_add", ("segments", "ring_count")),
    "uvsphere": ("primitive_uv_sphere_add", ("segments", "ring_count")),
    "icosphere": ("primitive_ico_sphere_add", ("subdivisions",)),
    "cylinder": ("primitive_cylinder_add", ("vertices", "depth")),
    "cone":     ("primitive_cone_add", ("vertices", "depth", "radius1", "radius2")),
    "torus":    ("primitive_torus_add", ()),
    "plane":    ("primitive_plane_add", ()),
    "grid":     ("primitive_grid_add", ("x_subdivisions", "y_subdivisions")),
    "circle":   ("primitive_circle_add", ("vertices", "fill_type")),
    "monkey":   ("primitive_monkey_add", ()),
}


# MODULE-LEVEL AND A PLAIN LITERAL on purpose. parity_check.py resolves an accepted-key
# expression statically and REFUSES to guess at one it cannot - a parameter list it cannot read is
# exactly where a docstring and the code drift apart unnoticed.
CREATE_PRIMITIVE_PARAMS = (
    "kind", "type", "name", "size", "radius", "location", "rotation", "align",
    "segments", "ringCount", "subdivisions", "vertices", "depth",
    "radius1", "radius2", "xSubdivisions", "ySubdivisions", "fillType",
)


def _vec3(params, key, default):
    val = params.get(key)
    if val is None:
        return list(default)
    if isinstance(val, dict):
        return [float(val.get("x", 0.0)), float(val.get("y", 0.0)), float(val.get("z", 0.0))]
    if hasattr(val, "__len__") and not isinstance(val, str):
        if len(val) != 3:
            raise MifOpError("'%s' takes [x,y,z] or {x,y,z}, got %d component(s)" % (key, len(val)))
        return [float(x) for x in val]
    raise MifOpError("'%s' takes [x,y,z] or {x,y,z}, got %r" % (key, val))


def op_create_primitive(params):
    """Create a primitive mesh object: cube, sphere, cylinder, cone, torus, plane, grid, circle.

    THE FOUNDATIONAL GAP. Without this the addon can only edit meshes that came from a file.

    `kind` is required and an unknown one is refused with the full list rather than defaulting
    to a cube - a silently substituted shape is the kind of wrong-but-plausible result that
    survives all the way to an import.

    Per-kind parameters (segments, vertices, subdivisions, depth, radius1/radius2,
    x_subdivisions/y_subdivisions) are accepted only for the kinds that HAVE them, and passing
    one to a kind that does not is refused - bpy.ops would raise TypeError deep inside Blender
    otherwise, which surfaces as an opaque traceback rather than a usable message.
    """
    reject_unknown(params, CREATE_PRIMITIVE_PARAMS, "create_primitive")
    kind = (take(params, "kind", "type", required=True, kind=str) or "").lower()
    if kind not in PRIMITIVES:
        raise MifOpError("unknown primitive kind '%s' - accepted: %s. Refused rather than "
                         "defaulted, because a substituted shape looks like success."
                         % (kind, ", ".join(sorted(PRIMITIVES))))
    fn_name, extras = PRIMITIVES[kind]

    # Refuse a parameter this kind cannot use, rather than letting bpy.ops raise TypeError.
    camel = {"ringCount": "ring_count", "xSubdivisions": "x_subdivisions",
             "ySubdivisions": "y_subdivisions", "fillType": "fill_type"}
    kwargs = {}
    for key in ("segments", "ringCount", "subdivisions", "vertices", "depth",
                "radius1", "radius2", "xSubdivisions", "ySubdivisions", "fillType"):
        if key not in params:
            continue
        blender_key = camel.get(key, key)
        if blender_key not in extras:
            raise MifOpError(
                "'%s' does not apply to a %s - it accepts: %s. NOTHING was created."
                % (key, kind, ", ".join(extras) or "(no extra parameters)"))
        val = params[key]
        kwargs[blender_key] = val if isinstance(val, str) else (
            int(val) if blender_key in ("segments", "ring_count", "subdivisions", "vertices",
                                        "x_subdivisions", "y_subdivisions") else float(val))

    size = take_float(params, "size", default=None)
    radius = take_float(params, "radius", default=None)
    if size is not None and radius is not None:
        raise MifOpError("pass size OR radius, not both - they set the same dimension and which "
                         "one Blender uses depends on the primitive. NOTHING was created.")
    if size is not None:
        # Blender's own split: some primitives take `size`, others `radius`.
        kwargs["size" if kind in ("cube", "plane", "grid", "monkey") else "radius"] = size
    if radius is not None:
        if kind in ("cube", "plane", "grid", "monkey"):
            raise MifOpError("a %s takes `size`, not `radius`. NOTHING was created." % kind)
        kwargs["radius"] = radius

    kwargs["location"] = _vec3(params, "location", (0.0, 0.0, 0.0))
    kwargs["rotation"] = _vec3(params, "rotation", (0.0, 0.0, 0.0))

    snapshot = selection_snapshot()
    before = set(bpy.data.objects.keys())
    try:
        getattr(bpy.ops.mesh, fn_name)(**kwargs)
    except TypeError as exc:
        raise MifOpError("Blender refused the %s parameters: %s" % (kind, exc))
    finally:
        pass

    made = [n for n in bpy.data.objects.keys() if n not in before]
    if len(made) != 1:
        selection_restore(snapshot)
        raise MifOpError("expected exactly one new object, got %d (%s). NOTHING usable was "
                         "produced." % (len(made), ", ".join(made)))
    obj = bpy.data.objects[made[0]]

    requested = take(params, "name", kind=str)
    if requested:
        obj.name = requested
        obj.data.name = requested
    selection_restore(snapshot)

    # NESTED UNDER "object", matching op_object_info (ops_scene.py:177-180). Returning the same
    # payload FLAT here and nested there would be two shapes for one thing, and an agent that
    # creates a primitive and then re-reads it would get different keys for identical data.
    out = {"object": object_info(obj)}
    out["created"] = True
    out["kind"] = kind
    # ECHOED FROM THE OBJECT. Blender appends .001 on a collision and never says so. Duplicated at
    # top level because the name is the identity every op reports, not just a geometry fact.
    out["name"] = obj.name
    out["verts"] = out["object"].get("verts")
    if requested and obj.name != requested:
        out["nameNote"] = ("Blender renamed this to '%s' because '%s' was already taken - it "
                           "appends a number rather than failing or overwriting."
                           % (obj.name, requested))
    return out


def op_transform_object(params):
    """Move, rotate or scale an object WITHOUT baking it into the mesh data.

    apply_transform and set_origin both bake; there was no way to simply place something.
    That is fine for a one-object round trip - which asserts isIdentityTransform stays true -
    and impossible to live with as soon as a scene holds two objects.

    `relative` adds to the current transform instead of replacing it. The response reports the
    transform before and after, because "it moved" is not the same claim as "it is where I
    asked", and only the second one is worth making.
    """
    reject_unknown(params, ("object", "name", "location", "rotation", "scale", "relative"),
                   "transform_object")
    obj = get_object(take(params, "object", "name", required=True))
    relative = take_bool(params, "relative", default=False)

    before = {
        "location": rnd(list(obj.location)),
        "rotation": rnd(list(obj.rotation_euler)),
        "scale": rnd(list(obj.scale)),
    }
    if not any(k in params for k in ("location", "rotation", "scale")):
        raise MifOpError("nothing to set - pass location, rotation and/or scale. NOTHING was "
                         "changed.")

    if "location" in params:
        v = _vec3(params, "location", (0, 0, 0))
        obj.location = [a + b for a, b in zip(obj.location, v)] if relative else v
    if "rotation" in params:
        v = _vec3(params, "rotation", (0, 0, 0))
        obj.rotation_euler = ([a + b for a, b in zip(obj.rotation_euler, v)]
                              if relative else v)
    if "scale" in params:
        v = _vec3(params, "scale", (1, 1, 1))
        obj.scale = [a * b for a, b in zip(obj.scale, v)] if relative else v

    # Blender does not update matrix_world until the depsgraph runs, and a caller reading the
    # world position straight afterwards would get the stale one.
    bpy.context.view_layer.update()

    out = {"object": object_info(obj), "name": obj.name}
    out["before"] = before
    out["after"] = {
        "location": rnd(list(obj.location)),
        "rotation": rnd(list(obj.rotation_euler)),
        "scale": rnd(list(obj.scale)),
    }
    out["relative"] = relative
    out["bakedNote"] = ("this changed the OBJECT transform only - the mesh data is untouched "
                        "and isIdentityTransform is now false. apply_transform is what bakes "
                        "it in, and export writes the object transform unless you do.")
    return out


def op_join_objects(params):
    """Join several mesh objects into one, keeping every material slot.

    JOINING IS DESTRUCTIVE AND ASYMMETRIC: the sources are DELETED and everything lands in the
    target, which is Blender's active object. That is stated and the target is explicit here,
    because bpy.ops.object.join() silently uses whatever happened to be active.

    MATERIAL SLOTS ARE THE THING TO WATCH. Join merges the slot lists, so face material_index
    values are remapped - the result's slot ORDER is not either input's order. The response
    reports the slot list before and after for exactly that reason: slot order is what decides
    which Unreal material lands on which face.
    """
    reject_unknown(params, ("target", "objects", "sources"), "join_objects")
    target = get_object(take(params, "target", required=True), want_mesh=True)
    names = take(params, "objects", "sources", required=True)
    if not hasattr(names, "__len__") or isinstance(names, str):
        raise MifOpError("objects must be a list of object names to join INTO target")
    sources = [get_object(n, want_mesh=True) for n in names]
    if target in sources:
        raise MifOpError("'%s' is both the target and a source - a join cannot consume its own "
                         "target. NOTHING was changed." % target.name)
    if not sources:
        raise MifOpError("objects[] is empty - nothing to join. NOTHING was changed.")

    slots_before = [s.material.name if s.material else None for s in target.material_slots]
    counts_before = mesh_counts(target)

    snapshot = selection_snapshot()
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in sources:
            obj.select_set(True)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.join()
    finally:
        selection_restore(snapshot)

    gone = [o for o in names if o not in bpy.data.objects]
    slots_after = [s.material.name if s.material else None for s in target.material_slots]
    return {
        "target": target.name,
        "joined": list(names),
        # MEASURED: join silently ignores an object it cannot merge, so which ones actually
        # disappeared is the only honest report of what happened.
        "consumed": gone,
        "consumedCount": len(gone),
        "vertsBefore": counts_before.get("verts"),
        "verts": mesh_counts(target).get("verts"),
        "slotsBefore": slots_before,
        "slots": slots_after,
        "slotNote": ("join MERGES the material slot lists and remaps every face's slot index, "
                     "so the result's slot ORDER is not either input's order. Slot order is "
                     "what lines up against Unreal's material array - check it before export."),
    }


def op_separate_mesh(params):
    """Split a mesh into separate objects, by loose parts or by material.

    The counterpart to join. `mode` is "loose" (every disconnected island becomes its own
    object) or "material" (one object per material slot in use).

    THE NEW OBJECTS ARE NAMED BY BLENDER, as <source>.001, .002 - so the response lists what
    actually appeared rather than predicting names. A separate that produces ONE object means
    the mesh had nothing to split on, which is reported as a note rather than as success with
    an empty list.
    """
    reject_unknown(params, ("object", "name", "mode"), "separate_mesh")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    mode = (take(params, "mode", default="loose", kind=str) or "loose").lower()
    if mode not in ("loose", "material"):
        raise MifOpError("mode must be \"loose\" or \"material\", got '%s'" % mode)

    before = set(bpy.data.objects.keys())
    snapshot = selection_snapshot()
    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.separate(type="LOOSE" if mode == "loose" else "MATERIAL")
        bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError as exc:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:  # noqa: BLE001
            pass
        raise MifOpError("Blender refused the separate: %s" % exc)
    finally:
        selection_restore(snapshot)

    made = sorted(n for n in bpy.data.objects.keys() if n not in before)
    out = {
        "object": obj.name,
        "mode": mode,
        "created": made,
        "createdCount": len(made),
        "verts": mesh_counts(obj).get("verts"),
    }
    if not made:
        out["note"] = ("nothing was separated - the mesh has only one %s, so there was nothing "
                       "to split on. createdCount:0 is the measured result, not a failure."
                       % ("loose part" if mode == "loose" else "material in use"))
    return out


OPS = {
    "create_primitive": op_create_primitive,
    "transform_object": op_transform_object,
    "join_objects": op_join_objects,
    "separate_mesh": op_separate_mesh,
}
