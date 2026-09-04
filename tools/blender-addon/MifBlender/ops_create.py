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

from .ops_common import (check_axis_dict, MifOpError, get_object, mesh_counts, object_info, reject_unknown,
                         rnd, selection_restore, selection_snapshot, take, take_bool,
                         take_float, take_int)

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
        # AT LEAST ONE OF x/y/z, AND NOTHING ELSE. A dict was read with .get(..., default) for
        # each axis, so {"mif":"typo"} returned the DEFAULT vector and the call reported
        # success - a misspelled key silently placed the object at the origin, or left it where
        # it was, and every field in the response agreed. Partial dicts stay legal ({"z": 2} is
        # a useful thing to write); a dict that names none of them is a typo, not a request.
        check_axis_dict(val, key, ("x", "y", "z"))
        return [float(val.get("x", 0.0)), float(val.get("y", 0.0)),
                float(val.get("z", 0.0))]
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


def op_boolean_op(params):
    """Cut, merge or intersect one mesh with another, and prove it by the resulting counts.

    THIS IS A MODIFIER, NOT AN OPERATOR, which is what makes it the awkward one of the three
    mesh-combining verbs. Three steps fail independently: add the BOOLEAN modifier naming the
    cutter, APPLY it, and dispose of the cutter. A modifier added and never applied is the worst
    failure available here - the viewport shows the cut, and the exported FBX does not, so the 3D
    view agrees with the request while the deliverable disagrees.

    So success is measured from the mesh afterwards. modifier_add returns a status about the
    OPERATOR, not about the geometry.

    AN UNCHANGED COUNT IS REPORTED, not swallowed. A DIFFERENCE whose cutter misses, or an INTERSECT
    with no overlap, legitimately changes nothing or empties the mesh - and both are almost always a
    modelling mistake the caller wants to hear about at once rather than discover in Unreal.

    The cutter is KEPT unless deleteCutter is asked for: it is usually reusable, and deleting
    someone's object as a side effect is not a default worth having.
    """
    reject_unknown(params, ("target", "cutter", "operation", "op", "deleteCutter", "solver"),
                   "boolean_op")
    _require_object_mode("boolean_op")

    target = get_object(take(params, "target", required=True), want_mesh=True)
    cutter = get_object(take(params, "cutter", required=True), want_mesh=True)
    if target is cutter:
        raise MifOpError("target and cutter are the same object ('%s') - a boolean cannot cut an "
                         "object with itself. NOTHING was changed." % target.name)

    raw = take(params, "operation", "op") or "difference"
    operation = str(raw).strip().upper()
    valid = ("DIFFERENCE", "UNION", "INTERSECT")
    if operation not in valid:
        raise MifOpError("operation '%s' is not one of difference, union, intersect. NOTHING was "
                         "changed." % raw)

    # A hidden object is not a boolean problem the way it is a join problem - the modifier reads the
    # cutter's mesh data directly rather than going through selection - but applying the modifier
    # DOES need the target reachable and active, so that is checked.
    view_layer = bpy.context.view_layer
    if target.name not in view_layer.objects:
        raise MifOpError("'%s' is not in the active view layer, so its modifier cannot be applied. "
                         "NOTHING was changed." % target.name)

    before = mesh_counts(target)
    cutter_counts = mesh_counts(cutter)
    if cutter_counts["faces"] == 0:
        raise MifOpError("the cutter '%s' has no faces, so a boolean against it is undefined - "
                         "DIFFERENCE would remove nothing and INTERSECT would empty '%s'. NOTHING "
                         "was changed." % (cutter.name, target.name))

    modifier = target.modifiers.new(name="MifBoolean", type="BOOLEAN")
    modifier.object = cutter
    modifier.operation = operation
    solver = take(params, "solver")
    if solver:
        # READ OFF THE LIVE ENUM, not a hardcoded pair, and this is the third place in the addon
        # that has had to learn it - _valid_light_types and the render-engine alias came first.
        #
        # WHAT MOVED, measured on 3.6.23, 4.2.17, 4.4.0 and 5.0.1:
        #   3.6 / 4.2 / 4.4   FAST, EXACT
        #   5.0               FLOAT, EXACT, MANIFOLD
        # FAST was RENAMED to FLOAT and MANIFOLD was added. The old pair was stale in both
        # directions: solver:'fast' raised TypeError from RNA on 5.0, and solver:'manifold' was
        # refused on the only build that has it.
        #
        # THE ASSIGNMENT USED TO SIT OUTSIDE THE try BELOW, so that TypeError escaped as a raw
        # exception with the BOOLEAN modifier still on the target - an unapplied boolean shows the
        # cut in the viewport and exports the original, which is the exact failure the apply path
        # further down was written to prevent. It is refused up front now, and the modifier removed.
        valid = [i.identifier
                 for i in modifier.bl_rna.properties["solver"].enum_items]
        wanted = str(solver).strip().upper()
        if wanted not in valid:
            # FAST AND FLOAT ARE THE SAME SOLVER under two names, so a caller written against
            # either spelling keeps working on every build rather than breaking at 5.0.
            alias = {"FAST": "FLOAT", "FLOAT": "FAST"}.get(wanted)
            if alias in valid:
                wanted = alias
            else:
                target.modifiers.remove(modifier)
                raise MifOpError(
                    "solver '%s' does not exist on Blender %s, which has %s. FAST was renamed FLOAT "
                    "at 5.0 and MANIFOLD was added there, so a solver name is not portable across "
                    "builds - fast and float are accepted as each other wherever one of them "
                    "exists. NOTHING was changed."
                    % (solver, bpy.app.version_string, ", ".join(valid)))
        modifier.solver = wanted

    # TAKE WHAT IS NEEDED AS PYTHON VALUES NOW, because modifier_apply FREES the modifier and every
    # attribute read after it is a read of released RNA memory. That is undefined behaviour, and it
    # behaved differently per version rather than failing honestly: 3.6, 4.2 and 4.4 returned the
    # old string, 5.0.1 returned bytes that are not UTF-8 and raised UnicodeDecodeError from inside
    # a plain `.name`. Passing on three versions was luck, not correctness.
    mod_name = str(modifier.name)
    mod_solver = str(modifier.solver) if solver else "default"

    # APPLY IT. Everything above only describes the cut; this is the step that performs it, and
    # leaving it out is the failure this whole function is arranged around.
    view_layer.objects.active = target
    try:
        bpy.ops.object.modifier_apply(modifier=mod_name)
    except Exception as exc:
        # Leave nothing half-done: an unapplied modifier would render as a cut that does not export.
        # Looked up by NAME rather than through the handle - apply may have freed it even on the
        # path that then raised, and removing a freed modifier is its own crash.
        stale = target.modifiers.get(mod_name)
        if stale is not None:
            target.modifiers.remove(stale)
        raise MifOpError("the boolean modifier could not be applied to '%s' (%s). The modifier was "
                         "removed rather than left in place, because an unapplied one shows the cut "
                         "in the viewport and exports the original. NOTHING was changed."
                         % (target.name, exc))

    # THE postcondition, and it must be asked of data that is still alive. `modifier` is freed by
    # now; asking IT whether it still exists is the bug this line used to be.
    leftover = target.modifiers.get(mod_name)
    if leftover is not None:
        target.modifiers.remove(leftover)
        raise MifOpError("the boolean modifier is still on '%s' after apply, so the geometry was "
                         "NOT changed - it has been removed. An unapplied modifier renders as a cut "
                         "and exports as the original." % target.name)

    after = mesh_counts(target)

    deleted = False
    if bool(take(params, "deleteCutter")):
        name = cutter.name
        bpy.data.objects.remove(cutter, do_unlink=True)
        deleted = name not in bpy.data.objects

    result = {
        "target": target.name,
        "operation": operation,
        "solver": mod_solver,
        "before": before,
        "after": after,
        "cutterDeleted": deleted,
    }
    # THE POSTCONDITION, and it is reported rather than asserted, because "no change" is a legal
    # outcome that is nearly always a mistake.
    if after["faces"] == before["faces"] and after["verts"] == before["verts"]:
        result["changed"] = False
        result["note"] = (
            "the mesh is UNCHANGED. The modifier applied cleanly, so this is geometry rather than "
            "failure: for DIFFERENCE it almost always means '%s' and '%s' do not overlap, and for "
            "UNION it means the cutter is entirely inside the target. Check their positions before "
            "exporting this." % (target.name, cutter.name))
    elif after["faces"] == 0:
        result["changed"] = True
        result["note"] = (
            "the mesh is now EMPTY - every face was removed. For INTERSECT that means the two "
            "objects share no volume; for DIFFERENCE it means the cutter completely enclosed the "
            "target. Almost certainly not what was wanted.")
    else:
        result["changed"] = True
    return result


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


_EMPTY_KEYS = {"name", "location", "rotation", "displayType", "displaySize", "collection"}
_CURVE_KEYS = {"name", "location", "rotation", "points", "splineType", "cyclic", "bevelDepth",
               "bevelResolution", "extrude", "resolution", "usePath", "collection", "dimensions"}
_TEXT_KEYS = {"name", "location", "rotation", "body", "size", "extrude", "align", "alignY",
              "collection", "bevelDepth"}
_ARM_KEYS = {"name", "location", "rotation", "bones", "displayType", "showInFront", "collection"}

_EMPTY_TYPES = ("PLAIN_AXES", "ARROWS", "SINGLE_ARROW", "CIRCLE", "CUBE", "SPHERE", "CONE",
                "IMAGE")


def _resolve_collection(params):
    """Where a new object will be linked - RESOLVED BEFORE ANYTHING IS CREATED.

    Split out from the linking on 2026-09-03 because the first version did the lookup after
    bpy.data.objects.new(), so naming a collection that does not exist left the object BEHIND: in
    bpy.data, in no collection, therefore in no scene - invisible, unrendered, absent from the
    outliner, surviving the save with nothing to warn anybody. Precisely the state _link_new's own
    comment describes as the thing to avoid, produced by the refusal meant to prevent it.

    Found by a check asserting the refusal, not by reading. Every op in this module now resolves
    first and creates second, which is the same rule stated everywhere else here: a refusal fires
    BEFORE a mutation.
    """
    name = take(params, "collection", kind=str)
    if not name:
        return bpy.context.scene.collection
    coll = bpy.data.collections.get(name)
    if coll is None:
        known = sorted(c.name for c in bpy.data.collections)[:25]
        raise MifOpError("no collection named '%s'. Present: %s. Make one with create_collection. "
                         "NOTHING was created."
                         % (name, ", ".join(known) if known else "<none>"))
    return coll


def _link_new(obj, coll):
    """Link a freshly made object into an ALREADY-RESOLVED collection.

    LINKED SOMEWHERE ALWAYS. bpy.data.objects.new() creates an object that belongs to NO collection,
    which means it is in no scene: invisible in the viewport, absent from the render, missing from
    the outliner, and it survives the save with nothing to warn anybody. The same inert shape
    ops_collection was written around, and the default outcome of the obvious API call.
    """
    coll.objects.link(obj)
    return coll


def _place(obj, params):
    obj.location = _vec3(params, "location", (0.0, 0.0, 0.0))
    obj.rotation_euler = _vec3(params, "rotation", (0.0, 0.0, 0.0))


def _created(obj, coll, want_type, verb):
    """The shared postcondition: it exists, it is the RIGHT TYPE, and it is in a scene.

    Type is checked because these ops build objects out of datablocks rather than through
    bpy.ops.*_add, so a wrong datablock class produces an object that exists and is not what was
    asked for - and every other field on it would read back fine.
    """
    if obj.name not in bpy.data.objects:
        raise MifOpError("%s reported success and '%s' is not in bpy.data.objects." % (verb, obj.name))
    if obj.type != want_type:
        raise MifOpError("%s made '%s' but its type is %s, not %s." % (verb, obj.name, obj.type,
                                                                       want_type))
    if not any(obj.name in c.objects for c in
               list(bpy.data.collections) + [bpy.context.scene.collection]):
        raise MifOpError("%s made '%s' and it is in NO collection, so it is in no scene - "
                         "invisible, unrendered and absent from the outliner." % (verb, obj.name))
    return {"ok": True, "name": obj.name, "type": obj.type, "collection": coll.name,
            "location": rnd(list(obj.location)), "rotation": rnd(list(obj.rotation_euler))}


def op_create_empty(params):
    """An Empty - the most-used object in Blender that this addon could not make.

    WHY IT WAS THE FIRST GAP WORTH CLOSING in object creation. An Empty is what a Track To or Copy
    Location constraint points AT, what a rig is controlled by, what a camera is aimed at, and what
    a set of objects is parented to for one shared pivot. add_constraint and aim_object both take a
    target and neither could create the object people overwhelmingly use as one, so the typed path
    could set up a constraint only against something that already existed.

    params:
      name (str)             default "Empty"
      location / rotation    3-lists
      displayType (str)      PLAIN_AXES | ARROWS | SINGLE_ARROW | CIRCLE | CUBE | SPHERE | CONE
      displaySize (float)    viewport size only - an Empty has no geometry and renders nothing
      collection (str)       link into this collection instead of the scene root
    """
    reject_unknown(params, _EMPTY_KEYS, "create_empty")
    kind = str(take(params, "displayType", default="PLAIN_AXES", kind=str)).upper()
    if kind not in _EMPTY_TYPES:
        raise MifOpError("displayType '%s' is not one Blender offers. Valid: %s. NOTHING was "
                         "created." % (kind, ", ".join(_EMPTY_TYPES)))
    size = take_float(params, "displaySize", default=None)

    # AN EMPTY IS AN OBJECT WITH NO DATA - objects.new(name, None). There is no "empty datablock",
    # which is why this cannot follow the create_light shape of make-data-then-object.
    coll = _resolve_collection(params)
    obj = bpy.data.objects.new(str(take(params, "name", default="Empty", kind=str)), None)
    _link_new(obj, coll)
    _place(obj, params)
    obj.empty_display_type = kind
    if size is not None:
        obj.empty_display_size = size

    out = _created(obj, coll, "EMPTY", "create_empty")
    out.update({"displayType": obj.empty_display_type,
                "displaySize": round(float(obj.empty_display_size), 6),
                "note": "an Empty has no geometry and renders nothing - displaySize is viewport "
                        "only. It exists to be a target, a parent or a pivot."})
    return out


def op_create_curve(params):
    """A curve - a path to follow, a profile to bevel, or a cable.

    A curve is what a Follow Path constraint needs, what a bevel turns into a pipe or a cable, and
    what text can be laid along. add_constraint accepts FOLLOW_PATH and nothing here could make the
    one object it requires.

    THE SPLINE TYPE DECIDES WHAT THE POINTS MEAN. POLY and NURBS points live in spline.points with
    a 4th weight component; BEZIER points live in spline.bezier_points and have handles instead.
    They are different collections with different lengths, so the type is chosen first and the
    points are added to the right one - mixing them up produces a curve with no points and no error.

    params:
      name (str)
      points (list)          [[x,y,z], ...] - at least 2. Required.
      splineType (str)       POLY | BEZIER | NURBS. Default POLY.
      cyclic (bool)          close the loop
      bevelDepth (float)     round profile radius - this is what makes a cable out of a line
      bevelResolution (int)
      extrude (float)        flat extrusion, an alternative to bevel
      resolution (int)       preview resolution per segment
      usePath (bool)         default true - a Follow Path constraint does NOTHING without it
      location / rotation / collection
    """
    reject_unknown(params, _CURVE_KEYS, "create_curve")
    raw = params.get("points")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise MifOpError("'points' is required and needs at least 2 points, each [x,y,z]. A curve "
                         "with fewer has no length and nothing can follow it. NOTHING was created.")
    pts = []
    for i, p in enumerate(raw):
        if not isinstance(p, (list, tuple)) or len(p) < 3:
            raise MifOpError("points[%d] must be [x,y,z], got %r. NOTHING was created." % (i, p))
        pts.append([float(p[0]), float(p[1]), float(p[2])])

    stype = str(take(params, "splineType", default="POLY", kind=str)).upper()
    if stype not in ("POLY", "BEZIER", "NURBS"):
        raise MifOpError("splineType must be POLY, BEZIER or NURBS, got '%s'. NOTHING was created."
                         % stype)

    coll = _resolve_collection(params)
    data = bpy.data.curves.new(str(take(params, "name", default="Curve", kind=str)), type="CURVE")
    data.dimensions = str(take(params, "dimensions", default="3D", kind=str)).upper()
    spline = data.splines.new(stype)
    if stype == "BEZIER":
        # add() is relative to the ONE point a new spline already has, for both collections - so
        # this adds len-1 and then writes all of them, rather than adding len and leaving a stray.
        spline.bezier_points.add(len(pts) - 1)
        for bp, xyz in zip(spline.bezier_points, pts):
            bp.co = xyz
            bp.handle_left_type = bp.handle_right_type = "AUTO"
    else:
        spline.points.add(len(pts) - 1)
        for sp, xyz in zip(spline.points, pts):
            sp.co = (xyz[0], xyz[1], xyz[2], 1.0)      # POLY/NURBS points are 4-component
    spline.use_cyclic_u = take_bool(params, "cyclic", default=False)

    for key, attr in (("bevelDepth", "bevel_depth"), ("extrude", "extrude")):
        v = take_float(params, key, default=None)
        if v is not None:
            setattr(data, attr, v)
    for key, attr in (("bevelResolution", "bevel_resolution"), ("resolution", "resolution_u")):
        v = take_int(params, key, default=None)
        if v is not None:
            setattr(data, attr, v)
    # DEFAULT TRUE, and not a detail: a Follow Path constraint evaluates to NOTHING when use_path is
    # off, with the constraint and the curve both reading back perfectly.
    data.use_path = take_bool(params, "usePath", default=True)

    obj = bpy.data.objects.new(data.name, data)
    _link_new(obj, coll)
    _place(obj, params)

    # THE POINT COUNT IS THE POSTCONDITION, taken from the collection the type actually uses. A
    # spline built into the wrong collection has zero points and raises nothing.
    made = len(spline.bezier_points) if stype == "BEZIER" else len(spline.points)
    if made != len(pts):
        raise MifOpError("asked for %d points and the %s spline holds %d afterwards."
                         % (len(pts), stype, made))
    out = _created(obj, coll, "CURVE", "create_curve")
    out.update({"splineType": stype, "pointCount": made, "cyclic": bool(spline.use_cyclic_u),
                "bevelDepth": round(float(data.bevel_depth), 6),
                "usePath": bool(data.use_path),
                "note": (None if data.use_path else
                         "usePath is OFF, so a Follow Path constraint against this curve will "
                         "evaluate to nothing while the constraint and the curve both read back "
                         "correctly.")})
    return out


def op_create_text(params):
    """A text object - titles, labels, mograph, and anything with words in the render.

    params:
      name (str)
      body (str)            the text itself. Required - an empty text object renders nothing.
      size (float)          font size
      extrude (float)       depth, which is what turns flat text into 3D
      bevelDepth (float)
      align (str)           horizontal: LEFT | CENTER | RIGHT | JUSTIFY | FLUSH
      alignY (str)          vertical: TOP | TOP_BASELINE | CENTER | BOTTOM | BOTTOM_BASELINE
      location / rotation / collection
    """
    reject_unknown(params, _TEXT_KEYS, "create_text")
    body = take(params, "body", required=True, kind=str)
    if not str(body):
        raise MifOpError("'body' is empty, and a text object with no body renders nothing while "
                         "existing perfectly. NOTHING was created.")

    coll = _resolve_collection(params)
    data = bpy.data.curves.new(str(take(params, "name", default="Text", kind=str)), type="FONT")
    data.body = str(body)
    for key, attr in (("size", "size"), ("extrude", "extrude"), ("bevelDepth", "bevel_depth")):
        v = take_float(params, key, default=None)
        if v is not None:
            setattr(data, attr, v)
    for key, attr in (("align", "align_x"), ("alignY", "align_y")):
        v = take(params, key, kind=str)
        if v is None:
            continue
        valid = {i.identifier for i in data.bl_rna.properties[attr].enum_items}
        if str(v).upper() not in valid:
            raise MifOpError("%s '%s' is not one this Blender offers. Valid: %s. NOTHING was "
                             "created." % (key, v, ", ".join(sorted(valid))))
        setattr(data, attr, str(v).upper())

    obj = bpy.data.objects.new(data.name, data)
    _link_new(obj, coll)
    _place(obj, params)

    out = _created(obj, coll, "FONT", "create_text")
    out.update({"body": data.body, "size": round(float(data.size), 6),
                "extrude": round(float(data.extrude), 6),
                "align": data.align_x, "alignY": data.align_y,
                "note": ("extrude is 0, so this text is a flat plane with no thickness - which is "
                         "correct for a 2D title and wrong for anything meant to catch a light.")
                if not data.extrude else None})
    return out


def op_create_armature(params):
    """An armature, with its bones - without which the whole rigging family could only EDIT.

    ops_rig has twelve ops and not one of them creates an armature: list_bones, rename_bones,
    set_bone_pose, the vertex-group and weight ops all operate on a rig that already exists. So
    nothing could be rigged from scratch through the typed path.

    BONES CAN ONLY BE CREATED IN EDIT MODE, which is the whole difficulty. armature.edit_bones does
    not exist outside it, so this switches mode, builds, and switches back - and RESTORING THE MODE
    IS A POSTCONDITION, not a courtesy: being left in edit mode strands every op that follows,
    which is the same failure create_primitive forces enter_editmode off to avoid.

    params:
      name (str)
      bones (list)          [{"name","head":[x,y,z],"tail":[x,y,z],"parent":"...","connect":bool}]
                            Parents must appear BEFORE their children.
      displayType (str)     OCTAHEDRAL | STICK | BBONE | ENVELOPE | WIRE
      showInFront (bool)    draw the rig over the mesh - default true, because a rig inside a body
                            is invisible and looks like it was never created
      location / rotation / collection
    """
    reject_unknown(params, _ARM_KEYS, "create_armature")
    bones = params.get("bones") or []
    if not isinstance(bones, (list, tuple)):
        raise MifOpError("'bones' must be a list of {name, head, tail}. NOTHING was created.")
    # VALIDATED IN FULL BEFORE ANY MODE CHANGE. A refusal partway through would leave an armature
    # with half its bones AND Blender in edit mode.
    seen = set()
    for i, b in enumerate(bones):
        if not isinstance(b, dict) or not b.get("name"):
            raise MifOpError("bones[%d] needs a 'name'. NOTHING was created." % i)
        for end in ("head", "tail"):
            v = b.get(end)
            if not isinstance(v, (list, tuple)) or len(v) < 3:
                raise MifOpError("bones[%d]['%s'] must be [x,y,z], got %r. NOTHING was created."
                                 % (i, end, v))
        if b["name"] in seen:
            raise MifOpError("bones[%d] repeats the name '%s'. NOTHING was created."
                             % (i, b["name"]))
        parent = b.get("parent")
        if parent and parent not in seen:
            raise MifOpError("bones[%d] ('%s') names parent '%s', which is not defined ABOVE it. "
                             "Parents must come first. NOTHING was created."
                             % (i, b["name"], parent))
        seen.add(b["name"])

    mode_before = bpy.context.object.mode if bpy.context.object else "OBJECT"
    if mode_before != "OBJECT":
        raise MifOpError("Blender is in %s mode. Creating an armature has to switch modes, and "
                         "doing that from anything but OBJECT mode would drop whatever is being "
                         "edited. NOTHING was created." % mode_before)

    coll = _resolve_collection(params)
    data = bpy.data.armatures.new(str(take(params, "name", default="Armature", kind=str)))
    disp = take(params, "displayType", kind=str)
    if disp:
        valid = {i.identifier for i in data.bl_rna.properties["display_type"].enum_items}
        if str(disp).upper() not in valid:
            bpy.data.armatures.remove(data)
            raise MifOpError("displayType '%s' is not one this Blender offers. Valid: %s. NOTHING "
                             "was created." % (disp, ", ".join(sorted(valid))))
        data.display_type = str(disp).upper()
    obj = bpy.data.objects.new(data.name, data)
    _link_new(obj, coll)
    _place(obj, params)
    obj.show_in_front = take_bool(params, "showInFront", default=True)

    made = []
    if bones:
        snap = selection_snapshot()
        try:
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.mode_set(mode="EDIT")
            ebs = data.edit_bones
            for b in bones:
                eb = ebs.new(str(b["name"]))
                eb.head = [float(x) for x in b["head"][:3]]
                eb.tail = [float(x) for x in b["tail"][:3]]
                if b.get("parent"):
                    eb.parent = ebs[str(b["parent"])]
                    eb.use_connect = bool(b.get("connect", False))
                made.append(eb.name)
        finally:
            # ALWAYS, and this is the postcondition rather than the tidy-up. Left in edit mode,
            # every op after this one fails on an editor nobody asked to be in.
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
            selection_restore(snap)

    mode_after = bpy.context.object.mode if bpy.context.object else "OBJECT"
    if mode_after != "OBJECT":
        raise MifOpError("created '%s' but Blender is left in %s mode, which strands every op "
                         "after this one." % (obj.name, mode_after))
    # BONES COUNTED FROM data.bones, NOT from the edit_bones list built above. edit_bones only
    # exist in edit mode; the real bones appear when it is left, and a bone that failed to survive
    # that transition would still be in the list this op made.
    if len(data.bones) != len(bones):
        raise MifOpError("asked for %d bone(s) and the armature holds %d after leaving edit mode."
                         % (len(bones), len(data.bones)))

    out = _created(obj, coll, "ARMATURE", "create_armature")
    out.update({"bones": [b.name for b in data.bones], "boneCount": len(data.bones),
                "displayType": data.display_type, "showInFront": bool(obj.show_in_front),
                "modeAfter": mode_after,
                "note": (None if bones else
                         "created with NO bones - an armature with none deforms nothing and shows "
                         "nothing in the viewport. Add them with the bones parameter.")})
    return out


_LATTICE_KEYS = {"name", "resolution", "pointsU", "pointsV", "pointsW", "interpolation",
                 "useOutside", "location", "rotation", "scale", "collection"}
# "useColorRamp" IS DELIBERATELY ABSENT. It was listed here while the handler ignored it, so
# reject_unknown ACCEPTED it and nothing happened - the same declared-and-ignored mistake as
# set_material_texture's "slot" a few hours earlier, caught by the same audit. A caller
# passing it now gets a refusal naming what this op does take.
_TEXTURE_KEYS = {"name", "type", "image", "intensity", "contrast"}


def op_create_lattice(params):
    """A lattice object - the thing a LATTICE modifier deforms with, which nothing could create.

    FOUND BY THE PRODUCER/CONSUMER QUESTION, the sixth time it has paid: add_modifier learned to
    point a LATTICE modifier at an object on 2026-09-04, and bpy.data.lattices appears NOWHERE else
    in this addon. So the modifier could be aimed and there was nothing to aim it at. Same shape as
    vertex groups, shape keys, armatures, bones and material images before it.

    A DEFAULT LATTICE IS 2x2x2 AND CAN ONLY DEFORM LINEARLY. Eight corner points describe an affine
    transform and nothing else, so a lattice left at the default bends nothing however it is moved -
    it reads back as a perfectly healthy lattice and the mesh stays straight. That is reported
    rather than left to be discovered, and it is why `resolution` is the first parameter.

    THE MESH HAS TO BE INSIDE IT. A lattice only influences geometry within its own volume, so a
    lattice created at the origin at default scale will not touch a mesh standing somewhere else,
    and the modifier will report itself perfectly configured. Position and scale it over the mesh.

    params:
      name (str)
      resolution [u,v,w] (list[int])   or pointsU / pointsV / pointsW individually. Default 2,2,2.
      interpolation (str)              KEY_LINEAR | KEY_CARDINAL | KEY_BSPLINE | KEY_CATMULL_ROM,
                                       validated against this build's own enum. Applied to all
                                       three axes.
      useOutside (bool)                deform only points outside the lattice's own volume
      location / rotation / scale / collection
    """
    reject_unknown(params, _LATTICE_KEYS, "create_lattice")
    res = params.get("resolution")
    if res is not None and (not isinstance(res, (list, tuple)) or len(res) < 3):
        raise MifOpError("'resolution' must be [u, v, w]. NOTHING was created.")
    pts = []
    for i, key in enumerate(("pointsU", "pointsV", "pointsW")):
        raw = take_int(params, key, default=None)
        if raw is None:
            raw = int(res[i]) if res is not None else 2
        if raw < 1 or raw > 64:
            raise MifOpError("%s must be between 1 and 64, got %d - Blender clamps outside that "
                             "range and the lattice you get would not be the one you asked for. "
                             "NOTHING was created." % (key, raw))
        pts.append(int(raw))

    coll = _resolve_collection(params)
    data = bpy.data.lattices.new(str(take(params, "name", default="Lattice", kind=str)))
    # RESOLUTION FIRST, before anything else touches the lattice: changing points_u resets the
    # point positions, so writing it after any edit would silently discard the edit.
    data.points_u, data.points_v, data.points_w = pts

    interp = take(params, "interpolation", default=None, kind=str)
    if interp:
        valid = [i.identifier
                 for i in data.bl_rna.properties["interpolation_type_u"].enum_items]
        want = str(interp).upper()
        match = [v for v in valid if v.upper() == want]
        if not match:
            bpy.data.lattices.remove(data)
            raise MifOpError("interpolation '%s' is not one this Blender offers. Valid: %s. "
                             "NOTHING was created." % (interp, ", ".join(valid)))
        data.interpolation_type_u = match[0]
        data.interpolation_type_v = match[0]
        data.interpolation_type_w = match[0]
    if params.get("useOutside") is not None:
        data.use_outside = take_bool(params, "useOutside", default=False)

    obj = bpy.data.objects.new(data.name, data)
    _link_new(obj, coll)
    _place(obj, params)
    # SCALE IS HANDLED HERE RATHER THAN IN _place, which does location and rotation only.
    # It matters more for a lattice than for anything else this file makes: a lattice only
    # influences geometry inside its own volume, so scaling it to cover the mesh is not a
    # cosmetic choice, it is the difference between deforming something and deforming
    # nothing. audit_blender_dead_params caught it accepted and unread.
    if "scale" in params:
        obj.scale = _vec3(params, "scale", (1.0, 1.0, 1.0))

    # READ BACK OFF THE DATABLOCK. points_u is clamped by Blender rather than refused, so the
    # resolution the lattice HAS is not necessarily the one that was asked for.
    got = [int(data.points_u), int(data.points_v), int(data.points_w)]
    out = _created(obj, coll, "LATTICE", "create_lattice")
    out.update({
        "resolution": got,
        "resolutionRequested": pts,
        "pointCount": got[0] * got[1] * got[2],
        "interpolation": str(data.interpolation_type_u),
        "useOutside": bool(data.use_outside),
        # THE NUMBER THAT DECIDES WHETHER IT CAN BEND ANYTHING. Two points on an axis describe a
        # straight line and nothing else.
        "canDeformNonLinearly": any(p > 2 for p in got),
        "note": ("every axis is at 2 points, so this lattice can only apply an AFFINE deform - "
                 "moving its corners scales, shears or translates and cannot bend. Raise "
                 "resolution on at least one axis for anything else. It reads back as a perfectly "
                 "healthy lattice either way.") if not any(p > 2 for p in got) else
                ("a lattice only influences geometry INSIDE its own volume. Position and scale it "
                 "over the mesh before adding the LATTICE modifier, or the modifier will report "
                 "itself correctly configured and deform nothing."),
    })
    return out


def op_create_texture(params):
    """A legacy texture datablock - what a DISPLACE modifier reads, and nothing could create one.

    THE SAME PRODUCER/CONSUMER GAP as create_lattice above, found in the same pass. add_modifier can
    point DISPLACE at a texture and bpy.data.textures appears NOWHERE else in this addon.

    THESE ARE NOT SHADER NODES. bpy.data.textures is Blender's older texture system, and it is what
    the modifier stack reads - a DISPLACE modifier cannot take a ShaderNodeTexNoise. Both exist and
    they are not interchangeable, which is the confusion this op is most likely to be met with.

    params:
      name (str)
      type (str)         validated against this build's own enum. BLEND, CLOUDS, DISTORTED_NOISE,
                         IMAGE, MAGIC, MARBLE, MUSGRAVE, NOISE, STUCCI, VORONOI, WOOD - identical
                         on 3.6.23, 4.2.17, 4.4.0 and 5.0.1, read off the enum rather than assumed.
                         Default CLOUDS.
      image (str)        an image datablock, for type IMAGE. Refused for any other type.
      intensity / contrast (float)
    """
    reject_unknown(params, _TEXTURE_KEYS, "create_texture")
    name = str(take(params, "name", default="Texture", kind=str))
    kind = str(take(params, "type", default="CLOUDS", kind=str)).upper()
    valid = [i.identifier for i in bpy.types.Texture.bl_rna.properties["type"].enum_items]
    match = [v for v in valid if v.upper() == kind]
    if not match:
        raise MifOpError("texture type '%s' is not one this Blender offers. Valid: %s. NOTHING was "
                         "created." % (kind, ", ".join(valid)))
    kind = match[0]

    image_name = take(params, "image", default=None, kind=str)
    if image_name is not None and kind != "IMAGE":
        raise MifOpError("'image' only applies to an IMAGE texture and this one is %s. Accepting it "
                         "here and writing nothing is how a caller believes they attached an image "
                         "they did not. NOTHING was created." % kind)
    img = None
    if image_name is not None:
        img = bpy.data.images.get(str(image_name))
        if img is None:
            have = sorted(i.name for i in bpy.data.images)[:25]
            raise MifOpError("no image datablock named '%s'. This file has: %s. Load one with "
                             "set_material_texture, or import a file that carries it. NOTHING was "
                             "created." % (image_name, ", ".join(have) if have else "(none)"))

    tex = bpy.data.textures.new(name, type=kind)
    if img is not None:
        tex.image = img
    intensity = take_float(params, "intensity", default=None)
    if intensity is not None:
        tex.intensity = intensity
    contrast = take_float(params, "contrast", default=None)
    if contrast is not None:
        tex.contrast = contrast

    return {
        "ok": True,
        "texture": tex.name,
        "requestedName": name,
        "nameWasSuffixed": tex.name != name,
        "type": str(tex.type),
        "image": tex.image.name if getattr(tex, "image", None) else None,
        "intensity": round(float(tex.intensity), 6),
        "contrast": round(float(tex.contrast), 6),
        "users": tex.users,
        "note": ("this is a bpy.data.textures datablock, which is the OLD texture system and the "
                 "one the modifier stack reads - a DISPLACE modifier cannot take a shader node. "
                 "It is not the same thing as a ShaderNodeTexNoise and the two are not "
                 "interchangeable."),
    }

OPS = {
    "create_primitive": op_create_primitive,
    "create_lattice": op_create_lattice,
    "create_texture": op_create_texture,
    "transform_object": op_transform_object,
    "boolean_op": op_boolean_op,
    "join_objects": op_join_objects,
    "separate_mesh": op_separate_mesh,
    "create_empty": op_create_empty,
    "create_curve": op_create_curve,
    "create_text": op_create_text,
    "create_armature": op_create_armature,
}
