"""Creating geometry, and placing it without baking.

WHAT WAS MISSING. Every mesh in this addon entered through import_mesh. The bridge could decimate,
bevel, unwrap, skirt and export - and could not originate a single vertex. That is the right shape
for a round trip and the wrong one for building an asset IN Blender, which is what Andre asked for
on 2026-08-30.

=============================================================================
EVERY PRIMITIVE TAKES A DIFFERENT SIZING PARAMETER, AND THEY ARE NOT INTERCHANGEABLE
=============================================================================
Read off the real bpy.ops RNA rather than assumed, and it is not uniform:

    cube / plane / grid / monkey        size
    sphere / icosphere / cylinder /
      circle                            radius        (a radius is HALF a size, not a synonym)
    cone                                radius1, radius2, depth   - and NO `radius` at all
    torus                               major_radius, minor_radius - and NO size, NO radius,
                                        and NO enter_editmode either

The first version of this file remapped `size` onto `radius` for everything outside the size group.
That is a factor-of-two error on four kinds, a TypeError on cone, and a TypeError on torus - and it
also means a blanket enter_editmode=False would have broken torus on its own. So the table below
records what each primitive ACTUALLY accepts, size and radius are never silently swapped, and asking
for the wrong one is refused with the reason rather than reinterpreted.

=============================================================================
TWO THINGS THE bpy.ops PRIMITIVE CALLS DO THAT HAVE TO BE HANDLED
=============================================================================

1. THEY NAME THE OBJECT THEMSELVES, AND RENAME ON COLLISION. primitive_cube_add makes "Cube"; the
   second is "Cube.001". Blender never fails and never overwrites - it appends a number. So a caller
   who asked for "Crate" can get "Crate.003", and every op here echoes the name the object ACTUALLY
   has.

2. THEY OPERATE ON CONTEXT - the active collection and the current mode - so they depend on state a
   previous op left behind. Each op takes a selection snapshot and restores it on every path, and
   create_primitive refuses outright unless Blender is in OBJECT mode, because in EDIT mode
   primitive_*_add WELDS its geometry into the mesh being edited instead of creating an object.

WHY transform_object IS HERE AND NOT IN ops_mesh. apply_transform and set_origin both BAKE: they
write the transform into the mesh data and leave the object at identity. That is what an export
pipeline wants and is NOT how you place a second object next to a first.
"""
import bpy

from .ops_common import (MifOpError, get_object, mesh_counts, object_info, reject_unknown,
                         rnd, selection_restore, selection_snapshot, take, take_bool,
                         take_float)

# What each primitive really accepts. `size` is that operator's own size-like kwarg, or None when it
# has none at all. `extras` is every other per-kind kwarg. Verified against bpy.ops RNA.
PRIMITIVES = {
    "cube":      {"fn": "primitive_cube_add",       "size": "size",   "extras": ()},
    "plane":     {"fn": "primitive_plane_add",      "size": "size",   "extras": ()},
    "grid":      {"fn": "primitive_grid_add",       "size": "size",
                  "extras": ("x_subdivisions", "y_subdivisions")},
    "monkey":    {"fn": "primitive_monkey_add",     "size": "size",   "extras": ()},
    "sphere":    {"fn": "primitive_uv_sphere_add",  "size": "radius",
                  "extras": ("segments", "ring_count")},
    "uvsphere":  {"fn": "primitive_uv_sphere_add",  "size": "radius",
                  "extras": ("segments", "ring_count")},
    "icosphere": {"fn": "primitive_ico_sphere_add", "size": "radius",
                  "extras": ("subdivisions",)},
    "cylinder":  {"fn": "primitive_cylinder_add",   "size": "radius",
                  "extras": ("vertices", "depth")},
    "circle":    {"fn": "primitive_circle_add",     "size": "radius",
                  "extras": ("vertices", "fill_type")},
    "cone":      {"fn": "primitive_cone_add",       "size": None,
                  "extras": ("vertices", "depth", "radius1", "radius2")},
    # NO enter_editmode on this operator - passing one is a TypeError, hence the opt-out flag.
    "torus":     {"fn": "primitive_torus_add",      "size": None, "noEnterEditmode": True,
                  "extras": ("major_radius", "minor_radius")},
}

# Accepted request key -> the Blender kwarg it maps to. Only these are ever forwarded.
EXTRA_KEYS = {
    "segments": "segments", "ringCount": "ring_count", "subdivisions": "subdivisions",
    "vertices": "vertices", "depth": "depth", "radius1": "radius1", "radius2": "radius2",
    "xSubdivisions": "x_subdivisions", "ySubdivisions": "y_subdivisions",
    "fillType": "fill_type", "majorRadius": "major_radius", "minorRadius": "minor_radius",
}
INT_KWARGS = ("segments", "ring_count", "subdivisions", "vertices",
              "x_subdivisions", "y_subdivisions")
ALIGN_VALUES = ("WORLD", "VIEW", "CURSOR")
EULER_ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")

# MODULE-LEVEL AND A PLAIN LITERAL on purpose. parity_check.py resolves an accepted-key expression
# statically and REFUSES to guess at one it cannot - a parameter list it cannot read is exactly
# where a docstring and the code drift apart unnoticed.
CREATE_PRIMITIVE_PARAMS = (
    "kind", "type", "name", "size", "radius", "location", "rotation", "align",
    "segments", "ringCount", "subdivisions", "vertices", "depth",
    "radius1", "radius2", "xSubdivisions", "ySubdivisions", "fillType",
    "majorRadius", "minorRadius",
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


def _require_object_mode(op):
    """EDIT mode makes several of these operators destructive rather than additive."""
    mode = getattr(bpy.context, "mode", "OBJECT")
    if mode != "OBJECT":
        raise MifOpError(
            "Blender is in %s mode and %s cannot run there. bpy.ops.mesh.primitive_*_add WELDS its "
            "geometry into the mesh being edited instead of creating an object, which changes that "
            "mesh in a way this endpoint cannot undo. Leave edit mode first. NOTHING was changed."
            % (mode, op))


def op_create_primitive(params):
    """Create a primitive mesh object: cube, sphere, icosphere, cylinder, cone, torus, plane, grid,
    circle or monkey.

    THE FOUNDATIONAL GAP - without this the addon can only edit meshes that came from a file.

    `size` AND `radius` ARE NOT SYNONYMS and are never swapped for one another. A cube takes size; a
    sphere takes radius, which is half of it. Passing the wrong one is refused naming which that kind
    takes, rather than silently reinterpreted - an earlier version remapped one onto the other and
    made four kinds come out at twice the requested size with nothing in the response to say so. A
    cone takes radius1/radius2 and a torus majorRadius/minorRadius; neither takes size or radius.

    REFUSED IN EDIT MODE, because primitive_*_add welds into the edited mesh there - a destructive
    outcome dressed up as a failure.

    enter_editmode IS FORCED OFF. Blender defaults that operator property from a USER PREFERENCE
    (Editing > Objects > New Objects > Enter Edit Mode), so on a machine with it turned on the call
    would leave Blender in edit mode and strand every op after it. Torus is the exception: its
    operator has no such property and passing one raises.
    """
    reject_unknown(params, CREATE_PRIMITIVE_PARAMS, "create_primitive")
    kind = (take(params, "kind", "type", required=True, kind=str) or "").lower()
    if kind not in PRIMITIVES:
        raise MifOpError("unknown primitive kind '%s' - accepted: %s. Refused rather than "
                         "defaulted, because a substituted shape looks like success."
                         % (kind, ", ".join(sorted(PRIMITIVES))))
    spec = PRIMITIVES[kind]

    # EVERYTHING IS VALIDATED BEFORE ANYTHING IS CREATED. A refusal must leave nothing behind, and
    # an earlier version checked the name's type only after bpy.ops had already made the object.
    name = take(params, "name", kind=str)
    align = take(params, "align", kind=str)
    if align is not None:
        align = align.upper()
        if align not in ALIGN_VALUES:
            raise MifOpError("align must be one of %s, got %r. NOTHING was created."
                             % (", ".join(ALIGN_VALUES), align))

    kwargs = {}
    for key, blender_key in EXTRA_KEYS.items():
        if key not in params:
            continue
        if blender_key not in spec["extras"]:
            accepted = sorted(k for k, v in EXTRA_KEYS.items() if v in spec["extras"])
            raise MifOpError(
                "'%s' does not apply to a %s - it accepts: %s. NOTHING was created."
                % (key, kind, ", ".join(accepted) or "(no extra parameters)"))
        val = params[key]
        kwargs[blender_key] = (val if isinstance(val, str)
                               else (int(val) if blender_key in INT_KWARGS else float(val)))

    size = take_float(params, "size", default=None)
    radius = take_float(params, "radius", default=None)
    if size is not None and radius is not None:
        raise MifOpError("pass size OR radius, not both. NOTHING was created.")
    given, given_name = ((size, "size") if size is not None else (radius, "radius"))
    if given is not None:
        if spec["size"] is None:
            accepted = sorted(k for k, v in EXTRA_KEYS.items() if v in spec["extras"])
            raise MifOpError(
                "a %s takes neither size nor radius - it is sized by %s. NOTHING was created."
                % (kind, ", ".join(accepted) or "(nothing)"))
        if spec["size"] != given_name:
            raise MifOpError(
                "a %s is sized by `%s`, not `%s`, and those are DIFFERENT dimensions - a radius is "
                "half of a size, so substituting one would quietly give you an object at the wrong "
                "scale. Pass %s instead. NOTHING was created."
                % (kind, spec["size"], given_name, spec["size"]))
        kwargs[spec["size"]] = given

    kwargs["location"] = _vec3(params, "location", (0.0, 0.0, 0.0))
    kwargs["rotation"] = _vec3(params, "rotation", (0.0, 0.0, 0.0))
    if align is not None:
        kwargs["align"] = align
    if not spec.get("noEnterEditmode"):
        kwargs["enter_editmode"] = False

    _require_object_mode("create_primitive")

    snapshot = selection_snapshot()
    before = set(bpy.data.objects.keys())
    try:
        getattr(bpy.ops.mesh, spec["fn"])(**kwargs)
        made = [n for n in bpy.data.objects.keys() if n not in before]
        if len(made) != 1:
            raise MifOpError("expected exactly one new object, got %d (%s). NOTHING usable was "
                             "produced." % (len(made), ", ".join(made)))
        obj = bpy.data.objects[made[0]]
        if name:
            obj.name = name
            obj.data.name = name
    except TypeError as exc:
        raise MifOpError("Blender refused the %s parameters: %s" % (kind, exc))
    finally:
        # ON EVERY PATH, including the raises above. An earlier version had `finally: pass`, which
        # restored nothing and could leave the selection pointing at a half-made object.
        selection_restore(snapshot)

    # NESTED UNDER "object", matching op_object_info (ops_scene.py:177-180). The same payload flat
    # here and nested there would be two shapes for one thing, and an agent that creates a
    # primitive and then re-reads it would get different keys for identical data.
    out = {"object": object_info(obj), "created": True, "kind": kind}
    # ECHOED FROM THE OBJECT. Blender appends .001 on a collision and never says so. Duplicated at
    # top level because the name is the identity every op reports, not just a geometry fact.
    out["name"] = obj.name
    out["verts"] = out["object"].get("verts")
    if name and obj.name != name:
        out["nameNote"] = ("Blender renamed this to '%s' because '%s' was already taken - it "
                           "appends a number rather than failing or overwriting."
                           % (obj.name, name))
    return out


def op_transform_object(params):
    """Move, rotate or scale an object WITHOUT baking it into the mesh data.

    apply_transform and set_origin both bake; there was no way to simply place something.

    ROTATION GOES TO THE FIELD THE OBJECT ACTUALLY USES. Blender evaluates rotation_euler ONLY when
    rotation_mode is one of the Euler orders; a QUATERNION or AXIS_ANGLE object ignores it entirely.
    An earlier version wrote rotation_euler unconditionally, so on a quaternion object - which is
    what glTF import produces for every node, and what constraint-driven objects tend to be - the
    object did not move AND the response read that same dead field back, reporting the requested
    rotation as fact. A silent no-op that confirms itself is the worst shape available. The euler is
    now converted into whichever representation the object is in.

    `after` IS DERIVED FROM matrix_world, not from the field that was written, so it cannot agree
    with the request by construction.
    """
    reject_unknown(params, ("object", "name", "location", "rotation", "scale", "relative"),
                   "transform_object")
    obj = get_object(take(params, "object", "name", required=True))
    relative = take_bool(params, "relative", default=False)
    if not any(k in params for k in ("location", "rotation", "scale")):
        raise MifOpError("nothing to set - pass location, rotation and/or scale. NOTHING was "
                         "changed.")

    before = {
        "location": rnd(list(obj.matrix_world.to_translation())),
        "rotationEuler": rnd(list(obj.matrix_world.to_euler())),
        "scale": rnd(list(obj.matrix_world.to_scale())),
        "rotationMode": obj.rotation_mode,
    }

    if "location" in params:
        v = _vec3(params, "location", (0, 0, 0))
        obj.location = [a + b for a, b in zip(obj.location, v)] if relative else v
    if "scale" in params:
        v = _vec3(params, "scale", (1, 1, 1))
        obj.scale = [a * b for a, b in zip(obj.scale, v)] if relative else v
    if "rotation" in params:
        import mathutils
        v = _vec3(params, "rotation", (0, 0, 0))
        order = obj.rotation_mode if obj.rotation_mode in EULER_ORDERS else "XYZ"
        wanted = mathutils.Euler(v, order)
        if obj.rotation_mode == "QUATERNION":
            q = wanted.to_quaternion()
            obj.rotation_quaternion = (obj.rotation_quaternion @ q) if relative else q
        elif obj.rotation_mode == "AXIS_ANGLE":
            q = wanted.to_quaternion()
            if relative:
                cur = obj.rotation_axis_angle
                q = mathutils.Quaternion(cur[1:4], cur[0]) @ q
            axis, angle = q.to_axis_angle()
            obj.rotation_axis_angle = (angle, axis[0], axis[1], axis[2])
        else:
            obj.rotation_euler = ([a + b for a, b in zip(obj.rotation_euler, v)]
                                  if relative else v)

    # Blender does not refresh matrix_world until the depsgraph runs, and a caller reading the world
    # transform straight afterwards would get the stale one.
    bpy.context.view_layer.update()

    out = {"object": object_info(obj), "name": obj.name, "before": before, "relative": relative}
    # READ BACK OFF matrix_world, so this reports where the object IS rather than what was asked
    # for. That is the whole difference between the two claims the docstring distinguishes.
    out["after"] = {
        "location": rnd(list(obj.matrix_world.to_translation())),
        "rotationEuler": rnd(list(obj.matrix_world.to_euler())),
        "scale": rnd(list(obj.matrix_world.to_scale())),
        "rotationMode": obj.rotation_mode,
    }
    out["bakedNote"] = ("this changed the OBJECT transform only - the mesh data is untouched. "
                        "apply_transform is what bakes it in, and export writes the object "
                        "transform unless you do.")
    if "rotation" in params and obj.rotation_mode != "XYZ":
        out["rotationModeNote"] = (
            "this object's rotation_mode is %s, so the euler you passed was CONVERTED into that "
            "representation - writing rotation_euler directly would have done nothing at all."
            % obj.rotation_mode)
    return out


def op_join_objects(params):
    """Join several mesh objects into one, keeping every material slot.

    JOINING IS DESTRUCTIVE AND ASYMMETRIC: the sources are DELETED and everything lands in the
    target. The target is explicit here because bpy.ops.object.join() otherwise silently uses
    whatever happened to be active.

    A SOURCE THAT CANNOT BE SELECTED IS REFUSED UP FRONT. join() only consumes objects in
    context.selected_objects, so a hidden one - hide_get(), hide_viewport, or outside the view layer
    - is silently skipped. An earlier version echoed the REQUEST back as `joined` and reported
    success having merged nothing. Each source is now checked for selectability by name first, the
    operator's return value is checked, and `consumed` is measured from which objects actually
    disappeared.

    MATERIAL SLOTS ARE THE THING TO WATCH. Join merges the slot lists and remaps face material_index
    values, so the result's slot ORDER is not either input's order - and slot order is what decides
    which Unreal material lands on which face.
    """
    reject_unknown(params, ("target", "objects", "sources"), "join_objects")
    _require_object_mode("join_objects")
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

    view_layer = bpy.context.view_layer
    unreachable = []
    for obj in [target] + sources:
        if obj.name not in view_layer.objects:
            unreachable.append("%s (not in the active view layer)" % obj.name)
        elif obj.hide_get() or obj.hide_viewport:
            unreachable.append("%s (hidden)" % obj.name)
    if unreachable:
        raise MifOpError(
            "these cannot take part in a join, because bpy.ops.object.join() only consumes objects "
            "it can SELECT and skips the rest silently: %s. Unhide them first. NOTHING was changed."
            % ", ".join(unreachable))

    slots_before = [s.material.name if s.material else None for s in target.material_slots]
    counts_before = mesh_counts(target)

    snapshot = selection_snapshot()
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in sources:
            obj.select_set(True)
        target.select_set(True)
        view_layer.objects.active = target
        result = bpy.ops.object.join()
        if "FINISHED" not in result:
            raise MifOpError("bpy.ops.object.join() returned %s rather than FINISHED - nothing was "
                             "merged. NOTHING usable was produced." % set(result))
    finally:
        selection_restore(snapshot)

    gone = [o for o in names if o not in bpy.data.objects]
    if len(gone) != len(names):
        left = [o for o in names if o in bpy.data.objects]
        raise MifOpError(
            "the join consumed %d of %d source(s) - %s still exist. join() skips what it cannot "
            "select and reports nothing, so this is measured rather than assumed. The target holds "
            "whatever WAS merged; re-read it before continuing."
            % (len(gone), len(names), ", ".join(left)))

    slots_after = [s.material.name if s.material else None for s in target.material_slots]
    return {
        "target": target.name,
        "joined": list(names),
        # MEASURED from which objects actually disappeared, never echoed from the request.
        "consumed": gone,
        "consumedCount": len(gone),
        "vertsBefore": counts_before.get("verts"),
        "verts": mesh_counts(target).get("verts"),
        "slotsBefore": slots_before,
        "slots": slots_after,
        "slotNote": ("join MERGES the material slot lists and remaps every face's slot index, so "
                     "the result's slot ORDER is not either input's order. Slot order is what "
                     "lines up against Unreal's material array - check it before export."),
    }


def op_separate_mesh(params):
    """Split a mesh into separate objects, by loose parts or by material.

    The counterpart to join. `mode` is "loose" (each disconnected island becomes its own object) or
    "material" (one object per material slot in use).

    THE NEW OBJECTS ARE NAMED BY BLENDER as <source>.001, .002, so the response lists what actually
    appeared rather than predicting names. A separate that produces nothing means the mesh had
    nothing to split on, reported as a measured zero rather than as bare success.
    """
    reject_unknown(params, ("object", "name", "mode"), "separate_mesh")
    _require_object_mode("separate_mesh")
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
    except RuntimeError as exc:
        raise MifOpError("Blender refused the separate: %s" % exc)
    finally:
        # Leaving Blender in EDIT mode would strand every later op, so this runs on every path.
        try:
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:  # noqa: BLE001
            pass
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
        out["note"] = ("nothing was separated - the mesh has only one %s, so there was nothing to "
                       "split on. createdCount:0 is the measured result, not a failure."
                       % ("loose part" if mode == "loose" else "material in use"))
    return out


OPS = {
    "create_primitive": op_create_primitive,
    "transform_object": op_transform_object,
    "join_objects": op_join_objects,
    "separate_mesh": op_separate_mesh,
}
