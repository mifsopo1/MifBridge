"""Lights and cameras: the two highest-value things this addon could not do without run_python.

WHY THESE TWO FIRST. A survey on 2026-09-01 found EIGHT capability families with no typed op at all
- lights, cameras, keyframes, particles, physics, rendering, world, and authoring geometry-node
trees. They are not worth the same. A scene with no light renders black and a scene with no camera
cannot be rendered at all, so these two gate everything else; particles do not gate anything.

WHY A TYPED OP RATHER THAN "just use run_python". run_python reaches all of this today, and it is
the wrong answer for three specific reasons rather than on principle:

  * IT IS AN ARBITRARY-CODE-EXECUTION SWITCH. The addon preference is literally labelled that, and
    a user who turns it off loses every light and camera call with it. A typed op keeps working.
  * IT IS ABSENT WHEN THE ADDON IS IMPORTED RATHER THAN INSTALLED. _prefs() returns None off
    sys.path, so run_python refuses - which is why run_blender_suites.py carries explicit code to
    force the hatch open before its suites run.
  * IT REPORTS NOTHING. reject_unknown catches a misspelled key at the door; exec fails somewhere
    obscure at runtime. And a typed op can read its postcondition back, which is the discipline the
    rest of this bridge is built on.

WHAT IS DELIBERATELY NOT HERE. No keyframing, no render trigger, no world/HDRI. Those are separate
entries and each is its own engine surface; doing them badly alongside these two would be worse
than leaving them recorded.
"""
import math
import os

import bpy
import mathutils

from .ops_common import (finite_floats, check_axis_dict, MifOpError, camera_readback, get_object, light_readback, object_info,
                         reject_unknown,
                         refuse_unsupported_shadow, rnd, shadow_attr,
                         selection_restore, selection_snapshot, take, take_bool, take_float,
                         take_int)

# Blender's own enum, read off the RNA rather than remembered - the same discipline ops_create uses
# for primitive sizing kwargs, and for the same reason: a hardcoded list goes stale silently.
_LIGHT_KEYS = {
    "name", "type", "lightType", "kind", "location", "rotation",
    "energy", "power", "color", "radius", "size", "sizeY", "shape",
    "spotAngle", "spotBlend", "angle", "shadow", "diffuseFactor", "specularFactor",
}

_CAMERA_KEYS = {
    "name", "location", "rotation", "lens", "focalLength", "sensorWidth",
    "clipStart", "clipEnd", "type", "orthoScale", "dofDistance", "fStop",
    "lookAt", "makeActive", "shiftX", "shiftY",
    # BOTH WERE READ AND NOT WRITEABLE. object_info reports dofFocusObject and angle; nothing could
    # set either, so a camera could be DESCRIBED in more detail than it could be built.
    "focusObject", "fieldOfView",
}


def _vec3(params, key, default, verb="created"):
    """Parse a vector, or refuse. CALL THIS BEFORE ANYTHING EXISTS.

    IT REFUSES, so where it is called decides whether the refusal is true. Until 2026-09-04 all four
    ops here called it AFTER bpy.data.*.new and objects.link, so a malformed `location` left a light
    or a camera sitting in the caller's scene and then said "NOTHING was created". The two setters
    were worse: they called it below a comment reading "COMMIT. Nothing below can refuse", one line
    after the type had been applied, so set_light({type:"SPOT", location:"bad"}) retyped the light
    and then denied doing anything.

    That sentence is what every refusal in MifBridge is held to and callers are told to trust, so a
    false one is worse than an ordinary bug. Every call site now parses up front, which is the shape
    op_set_viewport_view adopted for exactly this reason.

    `verb` exists because "NOTHING was created" is the wrong noun for a setter - it is true and
    irrelevant, and it hides that something WAS changed.
    """
    v = params.get(key)
    if v is None:
        return tuple(default)
    if isinstance(v, dict):
        # AT LEAST ONE OF x/y/z, AND NOTHING ELSE. A dict was read with .get(..., default) for
        # each axis, so {"mif":"typo"} returned the DEFAULT vector and the call reported
        # success - a misspelled key silently placed the object at the origin, or left it where
        # it was, and every field in the response agreed. Partial dicts stay legal ({"z": 2} is
        # a useful thing to write); a dict that names none of them is a typo, not a request.
        check_axis_dict(v, key, ("x", "y", "z"))
        return tuple(finite_floats([v.get("x", default[0]), v.get("y", default[1]),
                                    v.get("z", default[2])], key))
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(finite_floats(v, key))
    raise MifOpError("'%s' must be {x,y,z} or a 3-list, got %r. NOTHING was %s."
                     % (key, v, verb))


def _valid_light_types():
    return {i.identifier for i in bpy.types.Light.bl_rna.properties["type"].enum_items}


# THE PER-TYPE KEY MAP, SHARED. create_light validated these against the type being created and
# set_light has to validate them against the type the light will BE after the call - the same rule
# asked twice, so it lives once. A second copy is how allowEditConst got past one guard and not the
# other in this repo on 2026-09-03.
_MISPLACED_LIGHT_KEYS = (
    (("spotAngle", "spotBlend"), ("SPOT",), "spotAngle/spotBlend"),
    (("size", "sizeY", "shape"), ("AREA",), "size/sizeY/shape"),
    (("angle",), ("SUN",), "angle"),
    (("radius",), ("POINT", "SPOT"), "radius (the soft-shadow size)"),
)


def _refuse_misplaced_light_keys(params, kind, verb):
    """Raise if a per-type key was given for a light that is not that type.

    Called BEFORE anything is created or written, in both create_light and set_light, because a
    typo must not leave a stray object or a half-applied change. `verb` completes the sentence so
    the message says NOTHING was created or NOTHING was changed as appropriate - the two are
    different claims and this codebase holds both to being literally true.
    """
    for keys, wants, label in _MISPLACED_LIGHT_KEYS:
        present = [k for k in keys if k in params]
        if present and kind not in wants:
            raise MifOpError("%s only applies to a %s light and this one is %s (%s given). "
                             "NOTHING was %s."
                             % (label, " or ".join(wants), kind, ", ".join(present), verb))


def _look_at_euler(frm, to):
    """Euler that points a Blender camera's -Z at `to`, with +Y up.

    A camera looks down its LOCAL -Z, not +X and not +Z, which is the single thing that makes
    hand-written camera aiming wrong on the first try. Derived here rather than left to the caller.
    """
    dx, dy, dz = (to[0] - frm[0]), (to[1] - frm[1]), (to[2] - frm[2])
    horiz = math.hypot(dx, dy)
    if horiz == 0.0 and dz == 0.0:
        raise MifOpError("lookAt is the same point as the camera location, so there is no "
                         "direction to face. NOTHING was created.")
    # DERIVED, not guessed, because the first version was off by pi and aimed the camera 166
    # degrees away from its target while returning a perfectly plausible euler. For XYZ order the
    # world matrix is Rz(rz) . Rx(rx), so:
    #     forward = Rz(rz) . Rx(rx) . (0,0,-1) = (-sin rz . sin rx,  cos rz . sin rx,  -cos rx)
    # Matching that against d/|d| gives rx = atan2(horiz, -dz) - which was right - and
    #     -sin rz = dx/horiz,  cos rz = dy/horiz   ->   rz = atan2(-dx, dy)
    # atan2(dy, dx) + pi/2 is NOT that; it is that plus pi, which points exactly backwards.
    return (math.atan2(horiz, -dz), 0.0, math.atan2(-dx, dy))


def op_create_light(params):
    """Create a light. Returns what the light ACTUALLY is, not what was asked for.

    params:
      name (str)             requested name; Blender appends .001 on collision and the response
                             reports the name it really got
      type (str)             POINT | SUN | SPOT | AREA - validated against this Blender's own enum
      location / rotation    {x,y,z}
      energy (float)         watts for POINT/SPOT/AREA, irradiance for SUN
      color [r,g,b]
      radius (float)         soft-shadow size for POINT/SPOT
      size / sizeY / shape   AREA only
      spotAngle / spotBlend  SPOT only, spotAngle in RADIANS
      angle (float)          SUN only, angular diameter in radians
    """
    reject_unknown(params, _LIGHT_KEYS, "create_light")
    kind = str(take(params, "type", "lightType", "kind", default="POINT", kind=str)).upper()
    valid = _valid_light_types()
    if kind not in valid:
        raise MifOpError("unknown light type '%s' for this Blender. Valid: %s. NOTHING was created."
                         % (kind, ", ".join(sorted(valid))))

    # TYPE-SPECIFIC KEYS ARE CHECKED BEFORE THE LIGHT EXISTS. The first version created it and then
    # refused, which is honest - the error said "The light WAS created" - and still leaves a stray
    # object in the scene for a caller who did nothing but make a typo. test_blender_anim's cleanup
    # check found it by refusing to ignore an A_Bad nobody meant to keep. Everywhere else in this
    # addon a refusal means NOTHING was created, and this now matches.
    # Shared with set_light, which asks the same question about the type the light will BE.
    _refuse_misplaced_light_keys(params, kind, "created")
    refuse_unsupported_shadow(params, "created")

    # PARSED BEFORE ANYTHING EXISTS. These used to be read after the light was created AND linked,
    # so a malformed vector left a light in the scene and said "NOTHING was created".
    want_loc = _vec3(params, "location", (0.0, 0.0, 0.0))
    want_rot = _vec3(params, "rotation", (0.0, 0.0, 0.0))

    snap = selection_snapshot()
    try:
        wanted = str(take(params, "name", default="Light", kind=str))
        data = bpy.data.lights.new(name=wanted,
                                   type=kind)
        obj = bpy.data.objects.new(data.name, data)
        bpy.context.scene.collection.objects.link(obj)

        obj.location = want_loc
        obj.rotation_euler = want_rot

        energy = take_float(params, "energy", "power", default=None)
        if energy is not None:
            data.energy = energy
        col = params.get("color")
        if col is not None:
            if not isinstance(col, (list, tuple)) or len(col) < 3:
                raise MifOpError("'color' must be [r,g,b] in 0..1, got %r." % (col,))
            data.color = tuple(float(c) for c in col[:3])

        # PER-TYPE PROPERTIES ARE REFUSED ON THE WRONG TYPE rather than silently dropped. A caller
        # who sets spotAngle on a POINT light has a bug, and being told is the whole point of a
        # guarded op - Blender itself would just not have the attribute.
        def _only(prop_names, want, label):
            # The mismatch was already refused above, before anything was created; this only asks
            # whether there is work to do.
            return [p for p in prop_names if p in params]

        if _only(("spotAngle", "spotBlend"), "SPOT", "spotAngle/spotBlend"):
            sa = take_float(params, "spotAngle", default=None)
            if sa is not None:
                data.spot_size = sa
            sb = take_float(params, "spotBlend", default=None)
            if sb is not None:
                data.spot_blend = sb
        if _only(("size", "sizeY", "shape"), "AREA", "size/sizeY/shape"):
            sz = take_float(params, "size", default=None)
            if sz is not None:
                data.size = sz
            szy = take_float(params, "sizeY", default=None)
            if szy is not None:
                data.size_y = szy
            shape = take(params, "shape", default=None, kind=str)
            if shape:
                data.shape = str(shape).upper()
        if _only(("angle",), "SUN", "angle"):
            ang = take_float(params, "angle", default=None)
            if ang is not None:
                data.angle = ang
        if "radius" in params:
            data.shadow_soft_size = take_float(params, "radius", default=0.1)

        if "shadow" in params:
            # Refused up front on a Blender that has none, so this always lands.
            setattr(data, shadow_attr(data), take_bool(params, "shadow", default=True))
        df = take_float(params, "diffuseFactor", default=None)
        if df is not None:
            data.diffuse_factor = df
        sf = take_float(params, "specularFactor", default=None)
        if sf is not None:
            data.specular_factor = sf

        bpy.context.view_layer.update()
        # READ BACK off the datablock, so this reports what the light IS.
        out = {
            "name": obj.name,
            "dataName": data.name,
            "type": data.type,
            "location": rnd(list(obj.matrix_world.to_translation())),
            "rotationEuler": rnd(list(obj.matrix_world.to_euler())),
            "energy": round(float(data.energy), 6),
            "color": rnd(list(data.color)),
        }
        if data.type in ("POINT", "SPOT"):
            out["shadowSoftSize"] = round(float(data.shadow_soft_size), 6)
        if data.type == "SPOT":
            out["spotSize"] = round(float(data.spot_size), 6)
            out["spotBlend"] = round(float(data.spot_blend), 6)
        if data.type == "AREA":
            out["size"] = round(float(data.size), 6)
            out["shape"] = data.shape
        if data.type == "SUN":
            out["angle"] = round(float(data.angle), 6)
        # A NOTE IS NOT A FIELD. This said the name "may differ from what was asked for" and
        # left the caller nothing to test - so a retry after a timeout got Light.001 with prose
        # about it. requestedName and nameWasSuffixed are the answerable form.
        out["requestedName"] = wanted
        out["nameWasSuffixed"] = obj.name != wanted
        out["nameNote"] = ("Blender renames on collision rather than failing, so `name` is what the "
                           "object ACTUALLY got - check nameWasSuffixed rather than assuming.")
        return out
    finally:
        selection_restore(snap)


_SET_LIGHT_KEYS = set(_LIGHT_KEYS) | {"object", "light"}


def op_set_light(params):
    """Change a light that already exists, and report what it IS afterwards.

    WHY THIS EXISTS. Until 2026-09-03 a light could be created and never touched again: there was
    no way to change its energy, colour, cone, size or shadow, and no way to READ any of it back -
    object_info returns early for a light and reports only the transform. So the addon could build
    a lighting rig and then not adjust it, which is most of what lighting work actually is. The
    only route was run_python, i.e. the arbitrary-code switch a user may well have turned off.

    params:
      object / light / name (str)  which light. Required.
      type (str)                   RETYPE the light - POINT | SUN | SPOT | AREA. Per-type keys in
                                   the same call are validated against the NEW type.
      energy / power (float)       watts for POINT/SPOT/AREA, irradiance for SUN
      color [r,g,b]
      radius (float)               soft-shadow size, POINT/SPOT only
      size / sizeY / shape         AREA only
      spotAngle / spotBlend        SPOT only, radians
      angle (float)                SUN only, angular diameter in radians
      shadow (bool)
      diffuseFactor / specularFactor (float)
      location / rotation {x,y,z}  moves the light OBJECT; rotation is radians

    Every refusal fires before any write. A caller who names one bad key gets a light in exactly
    the state it was in before the call.
    """
    reject_unknown(params, _SET_LIGHT_KEYS, "set_light")
    want = take(params, "object", "light", "name", default=None, kind=str)
    if not want:
        raise MifOpError("'object' is required - the name of the light to change "
                         "(list them with list_lights). NOTHING was changed.")
    obj = bpy.data.objects.get(want)
    if obj is None:
        known = [o.name for o in bpy.data.objects if o.type == "LIGHT"][:25]
        raise MifOpError("no object named '%s'. Lights present: %s. NOTHING was changed."
                         % (want, ", ".join(known) if known else "<none>"))
    if obj.type != "LIGHT":
        raise MifOpError("'%s' is a %s, not a LIGHT. NOTHING was changed." % (want, obj.type))
    data = obj.data

    # THE TYPE THE LIGHT WILL BE, which is what the per-type keys must be judged against - not the
    # type it is now. Retyping to SPOT and setting spotAngle in one call is legitimate and has to
    # work; setting spotAngle while retyping to POINT is a caller bug and has to be refused.
    new_type = take(params, "type", "lightType", "kind", default=None, kind=str)
    if new_type is not None:
        new_type = str(new_type).upper()
        valid = _valid_light_types()
        if new_type not in valid:
            raise MifOpError("unknown light type '%s' for this Blender. Valid: %s. "
                             "NOTHING was changed." % (new_type, ", ".join(sorted(valid))))
    effective = new_type or data.type
    _refuse_misplaced_light_keys(params, effective, "changed")
    refuse_unsupported_shadow(params, "changed")

    col = params.get("color")
    if col is not None and (not isinstance(col, (list, tuple)) or len(col) < 3):
        raise MifOpError("'color' must be [r,g,b] in 0..1, got %r. NOTHING was changed." % (col,))

    before = light_readback(obj, data)

    # PARSED ABOVE THE COMMIT, because _vec3 CAN refuse and this comment used to be false: a
    # malformed location was read one line after data.type had been applied, so the op retyped the
    # light and then answered "NOTHING was created".
    set_loc = _vec3(params, "location", tuple(obj.location), "changed") \
        if "location" in params else None
    set_rot = _vec3(params, "rotation", tuple(obj.rotation_euler), "changed") \
        if "rotation" in params else None

    # COMMIT. Nothing below can refuse.
    if new_type is not None:
        data.type = new_type
    if set_loc is not None:
        obj.location = set_loc
    if set_rot is not None:
        obj.rotation_euler = set_rot
    energy = take_float(params, "energy", "power", default=None)
    if energy is not None:
        data.energy = energy
    if col is not None:
        data.color = tuple(float(c) for c in col[:3])
    sa = take_float(params, "spotAngle", default=None)
    if sa is not None:
        data.spot_size = sa
    sb = take_float(params, "spotBlend", default=None)
    if sb is not None:
        data.spot_blend = sb
    sz = take_float(params, "size", default=None)
    if sz is not None:
        data.size = sz
    szy = take_float(params, "sizeY", default=None)
    if szy is not None:
        data.size_y = szy
    shape = take(params, "shape", default=None, kind=str)
    if shape:
        data.shape = str(shape).upper()
    ang = take_float(params, "angle", default=None)
    if ang is not None:
        data.angle = ang
    if "radius" in params:
        data.shadow_soft_size = take_float(params, "radius", default=0.1)
    if "shadow" in params:
        setattr(data, shadow_attr(data), take_bool(params, "shadow", default=True))
    df = take_float(params, "diffuseFactor", default=None)
    if df is not None:
        data.diffuse_factor = df
    sf = take_float(params, "specularFactor", default=None)
    if sf is not None:
        data.specular_factor = sf

    bpy.context.view_layer.update()
    after = light_readback(obj, data)
    # WHAT ACTUALLY MOVED, not what was asked for. A caller who sets energy to the value it already
    # had should be able to see that nothing changed, and a retype that silently drops a per-type
    # property - Blender does discard spot_size when you leave SPOT - shows up here rather than
    # being discovered later in a render.
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "light": obj.name,
        "before": before,
        "after": after,
        "changedFields": changed,
        "changedAnything": bool(changed),
    }


def op_list_lights(params):
    """Every light in the file, with its full state. The read path that did not exist.

    params:
      nameContains (str)  optional substring filter, case-insensitive
      type (str)          optional POINT | SUN | SPOT | AREA filter

    Reports lights in bpy.data.objects, so a light datablock with no object is not listed - it
    cannot illuminate anything until something links it, and clear_scene purges those.
    """
    reject_unknown(params, {"nameContains", "type"}, "list_lights")
    sub = take(params, "nameContains", default=None, kind=str)
    want = take(params, "type", default=None, kind=str)
    if want:
        want = str(want).upper()
        valid = _valid_light_types()
        if want not in valid:
            raise MifOpError("unknown light type '%s'. Valid: %s."
                             % (want, ", ".join(sorted(valid))))
    rows = []
    for obj in bpy.data.objects:
        if obj.type != "LIGHT":
            continue
        if sub and sub.lower() not in obj.name.lower():
            continue
        if want and obj.data.type != want:
            continue
        row = light_readback(obj, obj.data)
        # VISIBILITY IS PART OF "is this lighting anything". A light hidden in the render still
        # reads as a perfectly configured light in every other field, which is exactly the sort of
        # thing somebody debugging a black render needs told.
        row["hideViewport"] = bool(obj.hide_viewport)
        row["hideRender"] = bool(obj.hide_render)
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    return {
        "count": len(rows),
        "lights": rows,
        "sceneHasAnyLight": any(not r["hideRender"] for r in rows),
    }


def _camera_focus_and_fov_plan(params, camera_type, verb):
    """Validate fieldOfView and focusObject BEFORE anything is created or written.

    SPLIT OUT OF THE WRITER ON 2026-09-04, because the writer was called below the commit in both
    ops and every one of its four refusals said "NOTHING was created"/"NOTHING was changed" after the
    camera existed and ten properties had been set. create_camera({lens: 50, fieldOfView: 90}) built
    the camera, linked it, wrote location, rotation, type, lens, sensor, clipping and shift, and then
    answered "NOTHING was created" - with the camera sitting in the file. set_camera was worse: the
    line above its call reads "# COMMIT. Nothing below can refuse."

    Both ops had their _vec3 parses hoisted above the commit that same morning for exactly this
    reason. This helper was ADDED after that, below the commit, and reintroduced the shape the hoist
    had just removed - which is the argument for auditing the property rather than remembering it.
    Found by tools/audit_mutate_then_deny.py; see its docstring.

    camera_type is passed in rather than read off the datablock so this can run before one exists:
    create_camera knows it as `effective_type`, set_camera as the requested type or the current one.

    SPLIT RATHER THAN REORDERED, the same choice as ops_create's _place_values. Moving the call up
    would work today and be undone by the next person who needs `data`; a function that cannot write
    cannot be called too early.
    """
    fov = take_float(params, "fieldOfView", default=None)
    if fov is not None:
        if take_float(params, "lens", "focalLength", default=None) is not None:
            raise MifOpError(
                "pass lens/focalLength OR fieldOfView, not both - they are the SAME property in "
                "two units, and setting angle to 90 degrees moves lens from 50mm to 18mm. "
                "NOTHING was %s." % verb)
        if camera_type != "PERSP":
            raise MifOpError("fieldOfView applies to a PERSP camera and this one is %s - an "
                             "orthographic camera has no field of view. NOTHING was %s."
                             % (camera_type, verb))
        if not (0.0 < fov < 180.0):
            raise MifOpError("fieldOfView is in DEGREES and must be between 0 and 180, got %g. "
                             "NOTHING was %s." % (fov, verb))

    focus_name = take(params, "focusObject", default=None, kind=str)
    target = None
    if focus_name is not None:
        target = bpy.data.objects.get(str(focus_name))
        if target is None:
            have = sorted(o.name for o in bpy.data.objects)[:25]
            raise MifOpError("no object named '%s' to focus on. This scene has: %s. NOTHING was %s."
                             % (focus_name, ", ".join(have) if have else "(none)", verb))
    return fov, target


def _write_camera_focus_and_fov(data, planned):
    """focusObject and fieldOfView - both REPORTED by object_info and, until now, writable by nothing.

    THE TWO ASYMMETRIES THIS CLOSES. ops_common reports dofFocusObject and the camera's angle; the
    write path had focus_distance and lens and neither of the other two. So a camera could be
    described in more detail than it could be built.

    A FOCUS OBJECT OVERRIDES THE FOCUS DISTANCE, silently. Blender uses the object's distance and
    ignores focus_distance entirely once focus_object is set, and BOTH fields keep reading back
    exactly as written - so a caller who sets a distance and an object gets one of them, with
    nothing to say which. That is reported rather than refused, because setting both is a reasonable
    way to express "focus here for now, track this later".

    ANGLE AND LENS ARE ONE PROPERTY IN TWO UNITS. Setting angle to 90 degrees moves lens from 50mm
    to 18mm - measured on all four builds - so passing both is contradictory and is refused rather
    than resolved by declaration order. That refusal, and every other one this pair makes, lives in
    _camera_focus_and_fov_plan above. NOTHING HERE CAN REFUSE, which is the point of the split.
    """
    fov, target = planned
    if fov is not None:
        data.angle = math.radians(fov)

    focus_set = False
    if target is not None:
        data.dof.focus_object = target
        # A FOCUS OBJECT WITHOUT use_dof IS STORED AND IGNORED, the same shape as a cutoff distance
        # with its toggle off - the field reads back perfectly and the render does not change.
        data.dof.use_dof = True
        focus_set = True
    return fov, focus_set


def op_create_camera(params):
    """Create a camera, optionally aimed at a point.

    params:
      name (str)
      location / rotation    {x,y,z}; rotation is RADIANS, like every other op here
      lookAt {x,y,z}         aim at a point INSTEAD of giving rotation - refused together with
                             rotation, because two answers to the same question is a caller bug
      lens / focalLength     mm, PERSP only
      sensorWidth (float)    mm
      type                   PERSP | ORTHO | PANO
      orthoScale (float)     ORTHO only
      clipStart / clipEnd
      fStop / dofDistance    enables depth of field when either is given
      shiftX / shiftY
      makeActive (bool)      also set scene.camera, default True
    """
    reject_unknown(params, _CAMERA_KEYS, "create_camera")
    if "lookAt" in params and "rotation" in params:
        raise MifOpError("pass lookAt OR rotation, not both - they are two answers to the same "
                         "question and there is no sensible way to combine them. NOTHING was "
                         "created.")

    # VALIDATED BEFORE THE CAMERA EXISTS, the same way create_light was fixed. Three refusals used
    # to fire after bpy.data.cameras.new() and the scene link. They were honest - each said "The
    # camera WAS created" - and still left a stray camera in somebody's scene for nothing but a
    # typo, which is what create_light's own comment objects to. Everywhere else in this addon a
    # refusal means nothing was created, and this now matches.
    #
    # The effective type has to be decided up front for the same reason: lens is refused on a
    # non-PERSP camera and orthoScale on a non-ORTHO one, and both questions are about the type the
    # camera is being CREATED as, which is knowable from params without creating anything.
    ctype = take(params, "type", default=None, kind=str)
    if ctype:
        valid = {i.identifier for i in bpy.types.Camera.bl_rna.properties["type"].enum_items}
        ctype = str(ctype).upper()
        if ctype not in valid:
            raise MifOpError("unknown camera type '%s'. Valid: %s. NOTHING was created."
                             % (ctype, ", ".join(sorted(valid))))
    effective_type = ctype or "PERSP"      # Blender's own default for a new camera
    if take_float(params, "lens", "focalLength", default=None) is not None \
            and effective_type != "PERSP":
        raise MifOpError("lens/focalLength applies to a PERSP camera and this one is %s - use "
                         "orthoScale for ORTHO. NOTHING was created." % effective_type)
    if take_float(params, "orthoScale", default=None) is not None and effective_type != "ORTHO":
        raise MifOpError("orthoScale applies to an ORTHO camera and this one is %s. "
                         "NOTHING was created." % effective_type)

    # PARSED BEFORE ANYTHING EXISTS, same reason as create_light above.
    loc = _vec3(params, "location", (0.0, 0.0, 0.0))
    want_rot = (_look_at_euler(loc, _vec3(params, "lookAt", (0.0, 0.0, 0.0)))
                if "lookAt" in params else _vec3(params, "rotation", (0.0, 0.0, 0.0)))
    cam_plan = _camera_focus_and_fov_plan(params, effective_type, "created")

    snap = selection_snapshot()
    try:
        wanted = str(take(params, "name", default="Camera", kind=str))
        data = bpy.data.cameras.new(name=wanted)
        obj = bpy.data.objects.new(data.name, data)
        bpy.context.scene.collection.objects.link(obj)

        obj.location = loc
        obj.rotation_euler = want_rot

        # Type and the two type-gated properties were validated above, before anything existed.
        if ctype:
            data.type = ctype

        lens = take_float(params, "lens", "focalLength", default=None)
        if lens is not None:
            data.lens = lens
        sw = take_float(params, "sensorWidth", default=None)
        if sw is not None:
            data.sensor_width = sw
        os_ = take_float(params, "orthoScale", default=None)
        if os_ is not None:
            data.ortho_scale = os_
        cs = take_float(params, "clipStart", default=None)
        if cs is not None:
            data.clip_start = cs
        ce = take_float(params, "clipEnd", default=None)
        if ce is not None:
            data.clip_end = ce
        sx = take_float(params, "shiftX", default=None)
        if sx is not None:
            data.shift_x = sx
        sy = take_float(params, "shiftY", default=None)
        if sy is not None:
            data.shift_y = sy

        # DOF is only switched on when something was actually asked for, so a plain camera is not
        # silently given a defocus the caller never requested.
        fstop = take_float(params, "fStop", default=None)
        dofd = take_float(params, "dofDistance", default=None)
        if fstop is not None or dofd is not None:
            data.dof.use_dof = True
            if fstop is not None:
                data.dof.aperture_fstop = fstop
            if dofd is not None:
                data.dof.focus_distance = dofd
        _fov, _focus_set = _write_camera_focus_and_fov(data, cam_plan)

        make_active = take_bool(params, "makeActive", default=True)
        was = bpy.context.scene.camera.name if bpy.context.scene.camera else None
        if make_active:
            bpy.context.scene.camera = obj

        bpy.context.view_layer.update()
        out = {
            "name": obj.name,
            # Blender renames on collision rather than failing, so a caller retrying a
            # timed-out create gets "Camera.001" while believing it holds "Camera". Same field
            # pair as _created's, and for the same reason.
            "requestedName": wanted,
            "nameWasSuffixed": obj.name != wanted,
            "dataName": data.name,
            "type": data.type,
            "location": rnd(list(obj.matrix_world.to_translation())),
            "rotationEuler": rnd(list(obj.matrix_world.to_euler())),
            "lens": round(float(data.lens), 6),
            "sensorWidth": round(float(data.sensor_width), 6),
            "clipStart": round(float(data.clip_start), 6),
            "clipEnd": round(float(data.clip_end), 6),
            "dofEnabled": bool(data.dof.use_dof),
            "dofFocusObject": (data.dof.focus_object.name
                               if getattr(data.dof, "focus_object", None) else None),
            # THE OVERRIDE, SAID OUT LOUD. Blender uses the focus OBJECT's distance and ignores
            # focus_distance entirely once one is set, and both fields keep reading back exactly as
            # written - so without this a caller who set both has no way to know which one won.
            "focusDistanceIgnored": bool(getattr(data.dof, "focus_object", None)),
            "fieldOfViewDeg": round(math.degrees(data.angle), 4) if data.type == "PERSP" else None,
            "isSceneCamera": bpy.context.scene.camera is obj,
            "previousSceneCamera": was,
        }
        if data.dof.use_dof:
            out["fStop"] = round(float(data.dof.aperture_fstop), 6)
            out["focusDistance"] = round(float(data.dof.focus_distance), 6)
        if data.type == "ORTHO":
            out["orthoScale"] = round(float(data.ortho_scale), 6)
        if "lookAt" in params:
            out["aimNote"] = ("rotation was DERIVED from lookAt - a Blender camera faces its local "
                              "-Z, which is the thing hand-written aiming gets wrong first.")
        return out
    finally:
        selection_restore(snap)


_SET_CAMERA_KEYS = set(_CAMERA_KEYS) | {"object", "camera"}


def op_set_camera(params):
    """Change a camera that already exists, including which one the scene renders through.

    Same absence set_light closed for lights: a camera could be created and never adjusted, and
    the scene camera could only be chosen at BIRTH via makeActive - so switching between two
    existing cameras, which is ordinary shot work, was impossible without run_python.

    params:
      object / camera / name (str)  which camera. Required.
      type                          PERSP | ORTHO | PANO
      lens / focalLength (float)    mm, PERSP only
      sensorWidth / sensorHeight    mm
      sensorFit                     AUTO | HORIZONTAL | VERTICAL - without which sensorWidth is a
                                    half-answer and no real lens can be matched
      orthoScale                    ORTHO only
      clipStart / clipEnd
      shiftX / shiftY
      fStop / dofDistance           enables depth of field when either is given
      lookAt {x,y,z}                aim at a point; refused together with rotation
      location / rotation {x,y,z}   rotation is RADIANS
      makeActive (bool)             make this the scene camera

    Every refusal fires before any write.
    """
    reject_unknown(params, _SET_CAMERA_KEYS | {"sensorHeight", "sensorFit"}, "set_camera")
    want = take(params, "object", "camera", "name", default=None, kind=str)
    if not want:
        raise MifOpError("'object' is required - the name of the camera to change (list them with "
                         "list_cameras). NOTHING was changed.")
    obj = bpy.data.objects.get(want)
    if obj is None:
        known = [o.name for o in bpy.data.objects if o.type == "CAMERA"][:25]
        raise MifOpError("no object named '%s'. Cameras present: %s. NOTHING was changed."
                         % (want, ", ".join(known) if known else "<none>"))
    if obj.type != "CAMERA":
        raise MifOpError("'%s' is a %s, not a CAMERA. NOTHING was changed." % (want, obj.type))
    cam = obj.data

    if "lookAt" in params and "rotation" in params:
        raise MifOpError("pass lookAt OR rotation, not both - they are two answers to the same "
                         "question. NOTHING was changed.")

    # THE TYPE THE CAMERA WILL BE, judged before anything is written - the same rule set_light
    # follows, because lens is PERSP-only and orthoScale is ORTHO-only and both questions are about
    # the type AFTER this call.
    ctype = take(params, "type", default=None, kind=str)
    if ctype:
        valid = {i.identifier for i in bpy.types.Camera.bl_rna.properties["type"].enum_items}
        ctype = str(ctype).upper()
        if ctype not in valid:
            raise MifOpError("unknown camera type '%s'. Valid: %s. NOTHING was changed."
                             % (ctype, ", ".join(sorted(valid))))
    effective = ctype or cam.type
    if take_float(params, "lens", "focalLength", default=None) is not None and effective != "PERSP":
        raise MifOpError("lens/focalLength applies to a PERSP camera and this one is %s - use "
                         "orthoScale for ORTHO. NOTHING was changed." % effective)
    if take_float(params, "orthoScale", default=None) is not None and effective != "ORTHO":
        raise MifOpError("orthoScale applies to an ORTHO camera and this one is %s. "
                         "NOTHING was changed." % effective)
    fit = take(params, "sensorFit", default=None, kind=str)
    if fit:
        fit = str(fit).upper()
        fits = {i.identifier for i in bpy.types.Camera.bl_rna.properties["sensor_fit"].enum_items}
        if fit not in fits:
            raise MifOpError("unknown sensorFit '%s'. Valid: %s. NOTHING was changed."
                             % (fit, ", ".join(sorted(fits))))

    before = camera_readback(obj, cam)

    # PARSED ABOVE THE COMMIT, same as set_light. lookAt is resolved against the location this
    # call will END with, not the one the object had, so the two orders cannot disagree.
    set_loc = _vec3(params, "location", tuple(obj.location), "changed") \
        if "location" in params else None
    final_loc = set_loc if set_loc is not None else tuple(obj.location)
    set_rot = None
    if "lookAt" in params:
        set_rot = _look_at_euler(final_loc, _vec3(params, "lookAt", (0.0, 0.0, 0.0), "changed"))
    elif "rotation" in params:
        set_rot = _vec3(params, "rotation", tuple(obj.rotation_euler), "changed")

    # Validated here, written after the commit. The line below used to be false: the focus/fov
    # writer sat under it and could refuse with "NOTHING was changed" after eleven writes.
    cam_plan = _camera_focus_and_fov_plan(params, ctype or cam.type, "changed")

    # COMMIT. Nothing below can refuse.
    if ctype:
        cam.type = ctype
    if set_loc is not None:
        obj.location = set_loc
    if set_rot is not None:
        obj.rotation_euler = set_rot
    for key, attr in (("lens", "lens"), ("focalLength", "lens"),
                      ("sensorWidth", "sensor_width"), ("sensorHeight", "sensor_height"),
                      ("orthoScale", "ortho_scale"), ("clipStart", "clip_start"),
                      ("clipEnd", "clip_end"), ("shiftX", "shift_x"), ("shiftY", "shift_y")):
        v = take_float(params, key, default=None)
        if v is not None and hasattr(cam, attr):
            setattr(cam, attr, v)
    if fit:
        cam.sensor_fit = fit
    fstop = take_float(params, "fStop", default=None)
    dofd = take_float(params, "dofDistance", default=None)
    if (fstop is not None or dofd is not None) and getattr(cam, "dof", None) is not None:
        cam.dof.use_dof = True
        if fstop is not None:
            cam.dof.aperture_fstop = fstop
        if dofd is not None:
            cam.dof.focus_distance = dofd
    # THE SAME WRITER create_camera USES. Two ops asking the same question needs one answer;
    # a second copy is how allowEditConst got past one guard and not the other in this very
    # file, which its own comment above _LIGHT_TYPE_KEYS records.
    _write_camera_focus_and_fov(cam, cam_plan)
    if take_bool(params, "makeActive", default=False):
        bpy.context.scene.camera = obj

    bpy.context.view_layer.update()
    after = camera_readback(obj, cam)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "camera": obj.name,
        "before": before,
        "after": after,
        "changedFields": changed,
        "changedAnything": bool(changed),
    }


def op_list_cameras(params):
    """Every camera in the file, and which one the scene actually renders through.

    sceneCamera was unobtainable anywhere in this addon: scene_info omits it,
    set_render_settings reports a bare boolean, and render_still names it only by blocking for a
    whole render. It is the first thing anybody needs to know.

    params:
      nameContains (str)  optional substring filter, case-insensitive
    """
    reject_unknown(params, ("nameContains",), "list_cameras")
    sub = take(params, "nameContains", default=None, kind=str)
    rows = []
    for obj in bpy.data.objects:
        if obj.type != "CAMERA" or obj.data is None:
            continue
        if sub and sub.lower() not in obj.name.lower():
            continue
        row = camera_readback(obj, obj.data)
        row["name"] = obj.name
        row["location"] = rnd(list(obj.matrix_world.to_translation()))
        row["rotationEuler"] = rnd(list(obj.matrix_world.to_euler()))
        row["hideViewport"] = bool(obj.hide_viewport)
        row["hideRender"] = bool(obj.hide_render)
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    sc = bpy.context.scene
    return {
        "count": len(rows),
        "cameras": rows,
        "sceneCamera": sc.camera.name if sc.camera else None,
        "hasSceneCamera": sc.camera is not None,
    }


def op_aim_object(params):
    """Point any object at a point or another object, and MEASURE that it now points there.

    Nothing could aim anything after creation. create_camera takes lookAt at birth and that was
    the only caller of the aiming maths; a light could not be aimed at all, which makes a spot
    light almost unusable through this bridge.

    params:
      object (str, required)   what to aim
      target (str)             an object to aim AT - its world-space origin
      lookAt {x,y,z}           a point to aim at. Exactly one of target/lookAt.

    Blender aims down LOCAL -Z for lights and cameras alike, which is the convention _look_at_euler
    derives - and got wrong by exactly pi in its first version, aiming 166 degrees off while
    returning a plausible euler. So the postcondition is the ANGLE between the object's world -Z
    and the direction to the target, not the euler that was just written. Reading back what you
    wrote is a proxy that cannot fail.
    """
    reject_unknown(params, ("object", "name", "target", "lookAt"), "aim_object")
    want = take(params, "object", "name", default=None, kind=str)
    if not want:
        raise MifOpError("'object' is required - what to aim. NOTHING was changed.")
    obj = bpy.data.objects.get(want)
    if obj is None:
        raise MifOpError("no object named '%s'. NOTHING was changed." % want)

    target_name = take(params, "target", default=None, kind=str)
    has_point = "lookAt" in params
    if bool(target_name) == bool(has_point):
        raise MifOpError("pass exactly one of target (an object) or lookAt (a point) - %s. "
                         "NOTHING was changed."
                         % ("both were given" if target_name else "neither was given"))
    if target_name:
        tgt = bpy.data.objects.get(target_name)
        if tgt is None:
            raise MifOpError("no target object named '%s'. NOTHING was changed." % target_name)
        if tgt is obj:
            raise MifOpError("'%s' cannot be aimed at itself. NOTHING was changed." % want)
        point = tuple(tgt.matrix_world.to_translation())
    else:
        point = _vec3(params, "lookAt", (0.0, 0.0, 0.0))

    frm = tuple(obj.matrix_world.to_translation())
    obj.rotation_euler = _look_at_euler(frm, point)
    bpy.context.view_layer.update()

    # THE MEASUREMENT. World -Z after the write, against the direction to the target.
    fwd = (obj.matrix_world.to_quaternion() @ mathutils.Vector((0.0, 0.0, -1.0))).normalized()
    to = mathutils.Vector((point[0] - frm[0], point[1] - frm[1], point[2] - frm[2]))
    if to.length == 0.0:
        raise MifOpError("the target is at the object's own origin, so there is no direction to "
                         "face. The rotation may already have been written - re-read it.")
    err = fwd.angle(to.normalized())
    if err > 1e-3:
        raise MifOpError("aimed '%s' but its -Z is %.4f rad (%.2f deg) off the target. The "
                         "rotation WAS written; do not trust it." % (want, err, math.degrees(err)))
    return {
        "object": obj.name,
        "target": target_name,
        "aimedAt": rnd(list(point)),
        "rotationEuler": rnd(list(obj.rotation_euler)),
        "aimErrorRadians": round(float(err), 9),
        "aimErrorDegrees": round(math.degrees(err), 7),
        "measuredNote": ("The error is the angle between the object's world-space local -Z and the "
                         "direction to the target, measured after the write - not the euler that "
                         "was written, which cannot disagree with itself."),
    }


# PANORAMA SETTINGS MOVED between versions: they were on camera.cycles in 3.x and are on the
# camera data itself in 4.x+. Both are tried, newest first, the same way the light shadow flag and
# the object ray-visibility flags are handled - a hardcoded single location is a silent no-op on
# half the Blenders this addon supports.
_PANO_FIELDS = (
    ("panoramaType", "panorama_type"),
    ("fisheyeFov", "fisheye_fov"),
    ("fisheyeLens", "fisheye_lens"),
    ("latitudeMin", "latitude_min"),
    ("latitudeMax", "latitude_max"),
    ("longitudeMin", "longitude_min"),
    ("longitudeMax", "longitude_max"),
)


# A LITERAL, for the same reason _VISIBILITY_KEYS is one in ops_scene: parity_check resolves
# accepted-key sets statically and is fail-closed, refusing a set comprehension rather than
# skipping a check it cannot read. The duplication of _PANO_FIELDS' keys is therefore forced, so it
# is GUARDED at import - a key present in _PANO_FIELDS and missing here would be refused at the
# door while every other part of the op supports it.
_PANO_KEYS = {
    "object", "camera", "name",
    "panoramaType", "fisheyeFov", "fisheyeLens",
    "latitudeMin", "latitudeMax", "longitudeMin", "longitudeMax",
}

_pano_missing = {k for k, _a in _PANO_FIELDS} - _PANO_KEYS
if _pano_missing:
    raise RuntimeError(
        "MifBlender ops_lightcam: _PANO_FIELDS names %s but _PANO_KEYS does not, so those keys "
        "would be refused by reject_unknown while the rest of the op supports them. Add them to "
        "the literal - it is duplicated on purpose so parity_check can read it."
        % ", ".join(sorted(_pano_missing)))


def _pano_holder(cam):
    """Where this Blender keeps the panorama settings, or None if it keeps them nowhere."""
    if hasattr(cam, "panorama_type"):
        return cam
    cyc = getattr(cam, "cycles", None)
    if cyc is not None and hasattr(cyc, "panorama_type"):
        return cyc
    return None


def op_set_camera_panorama(params):
    """Configure a panoramic camera - the settings create_camera could accept a type for and
    never reach.

    A DECLARED-AND-UNREACHABLE, closed. create_camera validates PANO against Blender's own enum and
    accepts it, and then nothing in this addon could set a single panorama property - so a PANO
    camera could be created and was unusable, which is worse than not offering the type at all.

    params:
      object (str, required)          must already be a PANO camera
      panoramaType (str)              EQUIRECTANGULAR | FISHEYE_EQUIDISTANT | FISHEYE_EQUISOLID |
                                      MIRRORBALL | ... validated against this Blender's own enum
      fisheyeFov / fisheyeLens (float)
      latitudeMin / latitudeMax (float)     radians, EQUIRECTANGULAR framing
      longitudeMin / longitudeMax (float)

    Panoramic rendering is a CYCLES feature. On EEVEE the settings are stored and ignored, which is
    reported rather than left to be discovered in a render that comes back rectilinear.
    """
    reject_unknown(params, _PANO_KEYS, "set_camera_panorama")
    want = take(params, "object", "camera", "name", default=None, kind=str)
    if not want:
        raise MifOpError("'object' is required - which camera. NOTHING was changed.")
    obj = bpy.data.objects.get(want)
    if obj is None:
        raise MifOpError("no object named '%s'. NOTHING was changed." % want)
    if obj.type != "CAMERA":
        raise MifOpError("'%s' is a %s, not a CAMERA. NOTHING was changed." % (want, obj.type))
    cam = obj.data
    if cam.type != "PANO":
        raise MifOpError("'%s' is a %s camera, not PANO - these settings would be stored and never "
                         "used. Set its type to PANO first with set_camera. NOTHING was changed."
                         % (want, cam.type))

    holder = _pano_holder(cam)
    if holder is None:
        raise MifOpError("this Blender exposes no panorama settings on a camera (tried the camera "
                         "data and camera.cycles), so every key here would be silently ignored. "
                         "NOTHING was changed.")

    ptype = take(params, "panoramaType", default=None, kind=str)
    if ptype:
        ptype = str(ptype).upper()
        try:
            valid = {i.identifier for i in
                     holder.bl_rna.properties["panorama_type"].enum_items}
        except (KeyError, AttributeError):
            valid = set()
        if valid and ptype not in valid:
            raise MifOpError("unknown panoramaType '%s'. Valid: %s. NOTHING was changed."
                             % (ptype, ", ".join(sorted(valid))))

    # Every requested field is resolved BEFORE any is written, so a build missing one refuses the
    # whole call rather than applying half of them.
    plan = []
    for key, attr in _PANO_FIELDS:
        if key not in params:
            continue
        if not hasattr(holder, attr):
            raise MifOpError("this Blender has no '%s' on its panorama settings, so '%s' would be "
                             "silently ignored. NOTHING was changed." % (attr, key))
        value = ptype if key == "panoramaType" else take_float(params, key, default=None)
        plan.append((attr, value))
    if not plan:
        raise MifOpError("nothing to set - pass at least one of %s. NOTHING was changed."
                         % ", ".join(k for k, _a in _PANO_FIELDS))

    for attr, value in plan:
        setattr(holder, attr, value)

    out = {"camera": obj.name, "type": cam.type,
           "storedOn": "camera" if holder is cam else "camera.cycles"}
    for key, attr in _PANO_FIELDS:
        if hasattr(holder, attr):
            v = getattr(holder, attr)
            out[key] = v if isinstance(v, str) else round(float(v), 6)
    # PANORAMIC RENDERING IS A CYCLES FEATURE. On any other engine these are stored and ignored,
    # and a caller finds out when the render comes back rectilinear. Said here instead.
    engine = bpy.context.scene.render.engine
    out["renderEngine"] = engine
    out["engineHonoursPanorama"] = "CYCLES" in engine
    return out


def op_set_light_ies(params):
    """Give a light a real-world IES profile, or clear it.

    An IES file is a MEASURED photometric distribution from a fixture manufacturer - the shape of
    the light a real luminaire throws. It is how archviz and product lighting stop looking like
    computer graphics, and no amount of energy/radius/spot-angle fiddling substitutes for one.

    A LIGHT'S DISTRIBUTION IS NOT A PROPERTY, it is a NODE TREE, which is why this needs an op of
    its own rather than a key on set_light. Blender does it with an IES Texture node feeding the
    strength of an Emission shader - three nodes and two links that nothing here could author,
    because the addon had never touched a light's node tree at all.

    params:
      object (str, required)
      filepath (str)     an .ies file on disk. Loaded as EXTERNAL, so the .blend references it.
      text (str)         the IES data inline, as an INTERNAL text datablock instead
      strength (float)   the emission strength the profile scales
      clear (bool)       remove the tree and return the light to its plain properties

    filepath and text are refused together - two answers to the same question - and a filepath that
    does not exist is refused BEFORE the tree is built, because a half-built node tree on a light
    that then renders black is worse than no change.
    """
    reject_unknown(params, {"object", "light", "name", "filepath", "text", "strength", "clear"},
                   "set_light_ies")
    want = take(params, "object", "light", "name", default=None, kind=str)
    if not want:
        raise MifOpError("'object' is required - which light. NOTHING was changed.")
    obj = bpy.data.objects.get(want)
    if obj is None:
        raise MifOpError("no object named '%s'. NOTHING was changed." % want)
    if obj.type != "LIGHT":
        raise MifOpError("'%s' is a %s, not a LIGHT. NOTHING was changed." % (want, obj.type))
    data = obj.data

    if take_bool(params, "clear", default=False):
        had = bool(getattr(data, "use_nodes", False))
        data.use_nodes = False
        return {"light": obj.name, "cleared": True, "hadNodeTree": had,
                "useNodes": bool(getattr(data, "use_nodes", False))}

    path = take(params, "filepath", default=None, kind=str)
    text = take(params, "text", default=None, kind=str)
    if path and text:
        raise MifOpError("pass filepath OR text, not both - they are two answers to the same "
                         "question. NOTHING was changed.")
    if not path and not text:
        raise MifOpError("pass filepath (an .ies file) or text (the IES data inline), or "
                         "clear:true. NOTHING was changed.")
    if path:
        path = os.path.abspath(os.path.expanduser(str(path)))
        if not os.path.isfile(path):
            raise MifOpError("no IES file at '%s'. Refused before building the node tree, because "
                             "a half-built tree on a light that then renders black is worse than "
                             "no change. NOTHING was changed." % path)

    strength = take_float(params, "strength", default=None)

    # THE TREE. Blender's light nodes are Emission -> Light Output, and an IES Texture drives the
    # emission STRENGTH rather than its colour.
    nodes_before = data.use_nodes
    data.use_nodes = True
    nt = data.node_tree
    if nt is None:
        # PUT BACK before refusing. Turning use_nodes on is itself a change to the light - it swaps
        # what the renderer reads - so leaving it on while saying "NOTHING was changed" is false in
        # exactly the way this whole sentence is meant to rule out.
        data.use_nodes = nodes_before
        raise MifOpError("this Blender gave the light no node tree even with use_nodes set, so "
                         "there is nowhere to put an IES profile. NOTHING was changed.")

    out_node = next((n for n in nt.nodes if n.type == "OUTPUT_LIGHT"), None)
    emit = next((n for n in nt.nodes if n.type == "EMISSION"), None)
    if emit is None:
        emit = nt.nodes.new("ShaderNodeEmission")
        emit.location = (-200, 0)
    if out_node is None:
        out_node = nt.nodes.new("ShaderNodeOutputLight")
        out_node.location = (0, 0)
    if not any(l.to_node is out_node and l.from_node is emit for l in nt.links):
        nt.links.new(emit.outputs[0], out_node.inputs[0])

    ies = next((n for n in nt.nodes if n.type == "TEX_IES"), None)
    if ies is None:
        try:
            ies = nt.nodes.new("ShaderNodeTexIES")
        except RuntimeError as exc:
            raise MifOpError("this Blender has no IES texture node (%s), so a photometric profile "
                             "cannot be applied. NOTHING usable was built." % exc)
        ies.location = (-450, 0)

    if path:
        ies.mode = "EXTERNAL"
        ies.filepath = path
    else:
        ies.mode = "INTERNAL"
        blk = bpy.data.texts.new("%s_IES" % obj.name)
        blk.from_string(str(text))
        ies.ies = blk
    if strength is not None:
        ies.inputs["Strength"].default_value = strength

    linked = any(l.from_node is ies and l.to_node is emit for l in nt.links)
    if not linked:
        nt.links.new(ies.outputs[0], emit.inputs["Strength"])
        linked = any(l.from_node is ies and l.to_node is emit for l in nt.links)

    return {
        "light": obj.name,
        "useNodes": bool(data.use_nodes),
        "iesNode": ies.name,
        "mode": ies.mode,
        "filepath": getattr(ies, "filepath", "") or None,
        "internalText": ies.ies.name if getattr(ies, "ies", None) else None,
        "strength": round(float(ies.inputs["Strength"].default_value), 6),
        # THE POSTCONDITION IS THE LINK. An IES node sitting unconnected in the tree changes
        # nothing at all and looks entirely correct in the node list - the same right-looking,
        # inert shape as an invalid constraint or a dead driver.
        "linkedToEmission": linked,
        "nodeCount": len(nt.nodes),
    }


def op_set_light_linking(params):
    """Control WHICH objects a light affects - light linking and shadow blocking.

    "This key light hits the product and not the backdrop" is a routine request in product and
    archviz work and is impossible to fake: moving the light changes the look, flagging it off with
    geometry changes the reflections, and turning it down changes everything. Light linking is the
    only correct answer and nothing here could reach it.

    params:
      object (str, required)          the LIGHT
      receiverCollection (str)        only objects in this collection are lit by it. Created if it
                                      does not exist.
      blockerCollection (str)         only objects in this collection cast its shadows
      clearReceivers / clearBlockers (bool)

    BLENDER 4.2+. Older builds have no light_linking at all, and this refuses by name rather than
    accepting the keys and doing nothing - which is what a hasattr-skip would have done, and is the
    mistake create_light's `shadow` key made until it was fixed today.
    """
    reject_unknown(params, {"object", "light", "name", "receiverCollection", "blockerCollection",
                            "clearReceivers", "clearBlockers"}, "set_light_linking")
    want = take(params, "object", "light", "name", default=None, kind=str)
    if not want:
        raise MifOpError("'object' is required - which light. NOTHING was changed.")
    obj = bpy.data.objects.get(want)
    if obj is None:
        raise MifOpError("no object named '%s'. NOTHING was changed." % want)
    if obj.type != "LIGHT":
        raise MifOpError("'%s' is a %s, not a LIGHT. NOTHING was changed." % (want, obj.type))

    linking = getattr(obj, "light_linking", None)
    if linking is None:
        raise MifOpError("this Blender has no light_linking on an object - it arrived in 4.2, and "
                         "this build is %s. Every key here would be silently ignored, so the call "
                         "is refused instead. NOTHING was changed."
                         % ".".join(str(v) for v in bpy.app.version))

    recv = take(params, "receiverCollection", default=None, kind=str)
    block = take(params, "blockerCollection", default=None, kind=str)
    clear_r = take_bool(params, "clearReceivers", default=False)
    clear_b = take_bool(params, "clearBlockers", default=False)
    if recv and clear_r:
        raise MifOpError("pass receiverCollection OR clearReceivers, not both. NOTHING was "
                         "changed.")
    if block and clear_b:
        raise MifOpError("pass blockerCollection OR clearBlockers, not both. NOTHING was changed.")
    if not any((recv, block, clear_r, clear_b)):
        raise MifOpError("nothing to do - pass receiverCollection, blockerCollection, "
                         "clearReceivers or clearBlockers. NOTHING was changed.")

    def _collection(name):
        c = bpy.data.collections.get(str(name))
        if c is None:
            c = bpy.data.collections.new(str(name))
        return c

    if clear_r:
        linking.receiver_collection = None
    elif recv:
        linking.receiver_collection = _collection(recv)
    if clear_b:
        linking.blocker_collection = None
    elif block:
        linking.blocker_collection = _collection(block)

    rc = getattr(linking, "receiver_collection", None)
    bc = getattr(linking, "blocker_collection", None)
    return {
        "light": obj.name,
        "receiverCollection": rc.name if rc else None,
        "receiverCount": len(rc.objects) if rc else 0,
        "blockerCollection": bc.name if bc else None,
        "blockerCount": len(bc.objects) if bc else 0,
        # AN EMPTY RECEIVER COLLECTION LIGHTS NOTHING AT ALL. That is a legitimate state to be
        # mid-setup in and a catastrophic one to render from, and it looks identical to a correct
        # link from every other field - so it is named rather than left to be discovered.
        "litsNothing": bool(rc) and len(rc.objects) == 0,
        "blenderVersion": ".".join(str(v) for v in bpy.app.version),
    }


_SHADOW_KEYS = {
    "object", "light", "name", "enabled", "softSize", "bufferClipStart", "color",
    "filterRadius", "jitter", "jitterOverblur", "maxResolution",
    "contactShadow", "contactDistance", "contactBias", "contactThickness",
    "cyclesCastShadow", "cyclesMaxBounces", "cyclesMIS", "isPortal", "isCausticsLight",
}

# param -> (where it lives, the real property name, which builds have it)
#
# EVERY ROW WAS READ OFF bl_rna ON ALL FOUR INSTALLS, not from release notes, and the drift here is
# the reason this op exists at all rather than a set_light parameter:
#
#   light.cycles.cast_shadow          3.6 ONLY - removed at 4.2
#   contact shadows (4 properties)    3.6 and 4.2 - EEVEE Next dropped them at 4.4
#   shadow_buffer_samples / _size     3.6 only
#   shadow_buffer_bias, shadow_color  3.6 and 4.2
#   jitter / filter radius / max res  4.2 and later - they did not exist on 3.6
#
# So a caller asking for a contact shadow on 4.4 is asking for something that build cannot do, and
# the house rule is that this is REFUSED with what happened to it rather than accepted and dropped.
_SHADOW_MAP = {
    "enabled":          ("light", "use_shadow", "every build"),
    "softSize":         ("light", "shadow_soft_size", "every build"),
    "bufferClipStart":  ("light", "shadow_buffer_clip_start", "every build"),
    "color":            ("light", "shadow_color", "3.6 and 4.2 only"),
    "filterRadius":     ("light", "shadow_filter_radius", "4.2 and later"),
    "jitter":           ("light", "use_shadow_jitter", "4.2 and later"),
    "jitterOverblur":   ("light", "shadow_jitter_overblur", "4.2 and later"),
    "maxResolution":    ("light", "shadow_maximum_resolution", "4.2 and later"),
    "contactShadow":    ("light", "use_contact_shadow", "3.6 and 4.2 only - EEVEE Next dropped it"),
    "contactDistance":  ("light", "contact_shadow_distance", "3.6 and 4.2 only"),
    "contactBias":      ("light", "contact_shadow_bias", "3.6 and 4.2 only"),
    "contactThickness": ("light", "contact_shadow_thickness", "3.6 and 4.2 only"),
    "cyclesCastShadow": ("cycles", "cast_shadow", "3.6 ONLY - removed at 4.2"),
    "cyclesMaxBounces": ("cycles", "max_bounces", "every build"),
    "cyclesMIS":        ("cycles", "use_multiple_importance_sampling", "every build"),
    "isPortal":         ("cycles", "is_portal", "every build"),
    "isCausticsLight":  ("cycles", "is_caustics_light", "every build"),
}

_BOOL_SHADOW = {"enabled", "jitter", "contactShadow", "cyclesCastShadow", "cyclesMIS",
                "isPortal", "isCausticsLight"}
_INT_SHADOW = {"maxResolution", "cyclesMaxBounces"}


def _shadow_holder(data, where):
    return data if where == "light" else getattr(data, "cycles", None)


def op_set_light_shadow(params):
    """The engine-specific shadow settings, refusing what this build cannot do.

    THE GENERAL HALF IS set_object_visibility - hide_render, per-ray visibility, holdout and shadow
    catcher live on the OBJECT and apply to every type. This is the half that lives on the LIGHT and
    differs by build and by engine, which is exactly why it was left until it could be measured
    rather than remembered.

    WHAT MOVES, read off bl_rna on 3.6.23, 4.2.17, 4.4.0 and 5.0.1:

      light.cycles.cast_shadow        3.6 ONLY. Removed at 4.2 - a Cycles shadow toggle that simply
                                      stopped existing.
      contact shadows, 4 properties   3.6 and 4.2. EEVEE Next dropped them at 4.4, so a caller
                                      asking for one on 4.4 is asking for a feature the renderer no
                                      longer has.
      shadow_buffer_samples / _size   3.6 only.
      shadow_color, shadow_buffer_bias 3.6 and 4.2.
      jitter, filter radius, max res  4.2 and later. They did not exist on 3.6.

    ANYTHING THIS BUILD LACKS IS REFUSED, NAMING WHICH BUILDS HAVE IT. Accepting a key and writing
    it only `if hasattr` is the shape refuse_unsupported_shadow was written to stop: the caller asks
    for shadows off, gets shadows on, and is told nothing.

    THE ENGINE IS REPORTED because half of these do nothing under the other renderer - contact
    shadows are EEVEE's, cycles.* are Cycles'. They are still WRITTEN when present, since a scene
    is often set up under one engine and rendered with the other, but the response says which of
    them the active engine will actually read.

    params:
      object / light / name (str)   which light. Required.
      enabled (bool)                use_shadow - the master toggle
      softSize (float)              shadow_soft_size, the radius that decides how soft the edge is
      bufferClipStart (float)
      color [r,g,b]                 shadow_color - 3.6 and 4.2 only
      filterRadius (float) / jitter (bool) / jitterOverblur (float) / maxResolution (int)
                                    4.2 and later
      contactShadow (bool) / contactDistance / contactBias / contactThickness (float)
                                    3.6 and 4.2 only
      cyclesCastShadow (bool)       3.6 only
      cyclesMaxBounces (int) / cyclesMIS (bool) / isPortal (bool) / isCausticsLight (bool)
    """
    reject_unknown(params, _SHADOW_KEYS, "set_light_shadow")
    want = take(params, "object", "light", "name", required=True, kind=str)
    obj = get_object(want)
    if obj.type != "LIGHT":
        raise MifOpError("'%s' is a %s, not a LIGHT. NOTHING was changed." % (obj.name, obj.type))
    data = obj.data

    asked = [k for k in _SHADOW_MAP if params.get(k) is not None]
    if not asked:
        raise MifOpError("nothing to do - pass at least one of %s. NOTHING was changed."
                         % ", ".join(sorted(_SHADOW_MAP)))

    # SHAPE BEFORE AVAILABILITY. A malformed colour is wrong on every Blender, so reporting it
    # should not depend on interrogating this one's bl_rna - the same ordering rule ray_cast,
    # face_info and create_collision_hull each needed, and the sixth time an offline check has
    # found it by trying the guard.
    raw_colour = params.get("color")
    if raw_colour is not None and (not isinstance(raw_colour, (list, tuple))
                                   or len(raw_colour) < 3):
        raise MifOpError("'color' must be [r,g,b], got %r. NOTHING was changed." % (raw_colour,))

    # EVERY KEY CHECKED AGAINST THIS BUILD BEFORE ANY OF THEM IS WRITTEN, so a request that is
    # half-supported does not leave half of it applied.
    missing = []
    for key in asked:
        where, attr, availability = _SHADOW_MAP[key]
        holder = _shadow_holder(data, where)
        if holder is None or attr not in holder.bl_rna.properties:
            missing.append((key, attr, availability))
    if missing:
        raise MifOpError(
            "this Blender (%s) has no %s. Available on %s. Accepting the key and writing it only "
            "where it exists is how a caller asks for shadows off, gets shadows on, and is told "
            "nothing - so it is refused. NOTHING was changed."
            % (bpy.app.version_string,
               "; ".join("%s (light.%s%s)" % (k, "cycles." if _SHADOW_MAP[k][0] == "cycles" else "",
                                              a) for k, a, _ in missing),
               "; ".join(v for _, _, v in missing)))

    applied = {}
    for key in asked:
        where, attr, _availability = _SHADOW_MAP[key]
        holder = _shadow_holder(data, where)
        raw = params[key]
        if key in _BOOL_SHADOW:
            value = take_bool(params, key, default=True)
        elif key in _INT_SHADOW:
            value = take_int(params, key)
        elif key == "color":
            value = [float(c) for c in raw[:3]]
        else:
            value = take_float(params, key)
        setattr(holder, attr, value)
        applied[key] = value

    # READ BACK FROM THE DATABLOCK, per key. Several of these are clamped - a negative softSize or
    # an out-of-range resolution is silently corrected rather than refused - so echoing the request
    # would report a value the light does not have.
    after, wrong = {}, {}
    for key in asked:
        where, attr, _ = _SHADOW_MAP[key]
        stored = getattr(_shadow_holder(data, where), attr)
        stored = list(stored)[:3] if hasattr(stored, "__len__") else stored
        after[key] = rnd(stored) if isinstance(stored, list) else (
            round(float(stored), 6) if isinstance(stored, float) else stored)
        want_v = applied[key]
        if isinstance(want_v, bool) and bool(stored) != want_v:
            wrong[key] = (want_v, bool(stored))
        elif isinstance(want_v, (int, float)) and not isinstance(want_v, bool) \
                and abs(float(stored) - float(want_v)) > 1e-4:
            wrong[key] = (want_v, stored)

    engine = bpy.context.scene.render.engine
    is_cycles = "CYCLES" in engine
    inert = [k for k in asked
             if (_SHADOW_MAP[k][0] == "cycles" and not is_cycles)
             or (k.startswith("contact") and is_cycles)]
    return {
        "ok": True,
        "light": obj.name,
        "engine": engine,
        "applied": after,
        # CLAMPED, NOT FAILED. Reported rather than raised: Blender correcting an out-of-range value
        # is legitimate, and a caller comparing what they sent against what stuck needs to see it.
        "clamped": {k: {"requested": v[0], "stored": v[1]} for k, v in wrong.items()} or None,
        # WRITTEN BUT NOT READ BY THIS ENGINE. Still written, because a scene is often set up under
        # one renderer and rendered with another - but saying so is the difference between a
        # setting that will take effect and one that is sitting there.
        "inertUnderThisEngine": inert or None,
        "note": ("%s written and this scene renders with %s, which does not read them. They are "
                 "stored and will apply if the engine changes."
                 % (", ".join(inert), engine)) if inert else None,
        "blenderVersion": bpy.app.version_string,
    }


_INFLUENCE_KEYS = {
    "object", "light", "name", "cutoffDistance", "useCustomDistance", "volumeFactor",
    "transmissionFactor", "spread", "softFalloff", "square", "showCone",
    "cascadeCount", "cascadeMaxDistance", "cascadeExponent", "cascadeFade",
}

# param -> (real property, which TYPES have it, which BUILDS have it)
#
# TWO AXES, AND THE MESSAGE HAS TO SAY WHICH ONE FAILED. set_light_shadow's map carries build
# availability only, so a property missing because of the light TYPE would be reported as "this
# Blender has no X" - true in the letter and wrong in the part that matters, because the caller
# then goes looking for the wrong fix. Every row below was read off bl_rna on 3.6.23, 4.2.17, 4.4.0
# and 5.0.1, for all four light types.
#
#   transmission_factor   4.2 and later on EVERY type - absent on 3.6
#   use_soft_falloff      4.2 and later, and only POINT and SPOT
#   spread                AREA only, every build
#   use_square/show_cone  SPOT only, every build
#   shadow_cascade_*      SUN only, every build
#   the rest              every type, every build
_INFLUENCE_MAP = {
    "cutoffDistance":     ("cutoff_distance", None, "every build"),
    "useCustomDistance":  ("use_custom_distance", None, "every build"),
    "volumeFactor":       ("volume_factor", None, "every build"),
    "transmissionFactor": ("transmission_factor", None, "4.2 and later"),
    "spread":             ("spread", ("AREA",), "every build"),
    "softFalloff":        ("use_soft_falloff", ("POINT", "SPOT"), "4.2 and later"),
    "square":             ("use_square", ("SPOT",), "every build"),
    "showCone":           ("show_cone", ("SPOT",), "every build"),
    "cascadeCount":       ("shadow_cascade_count", ("SUN",), "every build"),
    "cascadeMaxDistance": ("shadow_cascade_max_distance", ("SUN",), "every build"),
    "cascadeExponent":    ("shadow_cascade_exponent", ("SUN",), "every build"),
    "cascadeFade":        ("shadow_cascade_fade", ("SUN",), "every build"),
}
_INFLUENCE_BOOL = {"useCustomDistance", "softFalloff", "square", "showCone"}
_INFLUENCE_INT = {"cascadeCount"}


def op_set_light_influence(params):
    """How far a light reaches and what it reaches INTO - the half set_light never covered.

    THE HOLE THIS CLOSES. set_light writes energy, colour, cone and size; set_light_shadow writes
    the shadow settings. Nothing wrote a light's INFLUENCE RADIUS, its volumetric contribution, an
    area light's spread, or a sun's shadow cascades - and those are most of what separates a lighting
    rig that renders well from one that merely exists. cutoff_distance in particular is the
    performance knob: it is the distance past which a light is not evaluated at all, the direct
    equivalent of an attenuation radius in a game engine, and there was no way to set it.

    A CUTOFF DOES NOTHING UNTIL use_custom_distance IS ON. Blender stores cutoff_distance whether or
    not the toggle is set, so writing it alone changes a number and not the render - and every field
    reads back exactly as asked. Passing cutoffDistance turns the toggle on unless useCustomDistance
    says otherwise, and the response says which happened.

    TWO AXES OF AVAILABILITY, and the refusal says which one failed. A property can be missing
    because this BUILD does not have it (transmission_factor is 4.2 and later) or because this light
    TYPE does not have it (spread is AREA only, cascades are SUN only). set_light_shadow's map
    carries build availability alone, so a type mismatch there reads as "this Blender has no X" -
    true in the letter and wrong in the part that decides what the caller does next.

    params:
      object / light / name (str)   which light. Required.
      cutoffDistance (float)        the distance past which the light is not evaluated
      useCustomDistance (bool)      whether the cutoff applies at all
      volumeFactor (float)          contribution to volumetrics
      transmissionFactor (float)    contribution through transmissive surfaces - 4.2 and later
      spread (float)                AREA only - the angle the light emits over
      softFalloff (bool)            POINT and SPOT, 4.2 and later
      square (bool) / showCone (bool)   SPOT only
      cascadeCount (int) / cascadeMaxDistance (float) / cascadeExponent (float) /
      cascadeFade (float)           SUN only - the cascaded shadow map settings
    """
    reject_unknown(params, _INFLUENCE_KEYS, "set_light_influence")
    want = take(params, "object", "light", "name", required=True, kind=str)
    obj = get_object(want)
    if obj.type != "LIGHT":
        raise MifOpError("'%s' is a %s, not a LIGHT. NOTHING was changed." % (obj.name, obj.type))
    data = obj.data
    kind = str(data.type)

    asked = [k for k in _INFLUENCE_MAP if params.get(k) is not None]
    if not asked:
        raise MifOpError("nothing to do - pass at least one of %s. NOTHING was changed."
                         % ", ".join(sorted(_INFLUENCE_MAP)))

    # EVERY KEY CHECKED BEFORE ANY IS WRITTEN, so a half-supported request does not leave half of
    # itself applied - and the two reasons are reported separately.
    wrong_type, wrong_build = [], []
    for key in asked:
        attr, types, availability = _INFLUENCE_MAP[key]
        if types is not None and kind not in types:
            wrong_type.append((key, attr, types))
        elif attr not in data.bl_rna.properties:
            wrong_build.append((key, attr, availability))
    if wrong_type:
        raise MifOpError(
            "'%s' is of type %s, and %s. That is the light TYPE, not this Blender - retyping the "
            "light with set_light would make it available. NOTHING was changed."
            % (obj.name, kind,
               "; ".join("%s (light.%s) exists only on %s" % (k, a, "/".join(ts))
                         for k, a, ts in wrong_type)))
    if wrong_build:
        raise MifOpError(
            "this Blender (%s) has no %s. Available on %s. Accepting the key and writing it only "
            "where it exists is how a caller asks for something, does not get it, and is told "
            "nothing - so it is refused. NOTHING was changed."
            % (bpy.app.version_string,
               "; ".join("%s (light.%s)" % (k, a) for k, a, _ in wrong_build),
               "; ".join(v for _, _, v in wrong_build)))

    applied = {}
    for key in asked:
        attr = _INFLUENCE_MAP[key][0]
        if key in _INFLUENCE_BOOL:
            value = take_bool(params, key, default=True)
        elif key in _INFLUENCE_INT:
            value = take_int(params, key)
        else:
            value = take_float(params, key)
        setattr(data, attr, value)
        applied[key] = value

    # THE TOGGLE THAT MAKES THE DISTANCE MEAN ANYTHING. Blender stores cutoff_distance whether or
    # not use_custom_distance is on, so a caller who sets only the distance gets a stored number and
    # an unchanged render, with every field reading back exactly as requested.
    auto_enabled = False
    if "cutoffDistance" in applied and "useCustomDistance" not in applied \
            and not data.use_custom_distance:
        data.use_custom_distance = True
        auto_enabled = True

    # READ BACK PER KEY. Several of these clamp - a negative spread, a cascade count outside its
    # range - and echoing the request would report a value the light does not have.
    after, clamped = {}, {}
    for key in asked:
        stored = getattr(data, _INFLUENCE_MAP[key][0])
        after[key] = round(float(stored), 6) if isinstance(stored, float) else stored
        wanted = applied[key]
        if isinstance(wanted, (int, float)) and not isinstance(wanted, bool) \
                and abs(float(stored) - float(wanted)) > 1e-4:
            clamped[key] = {"requested": wanted, "stored": after[key]}

    engine = bpy.context.scene.render.engine
    return {
        "ok": True,
        "light": obj.name,
        "lightType": kind,
        "engine": engine,
        "applied": after,
        "clamped": clamped or None,
        "useCustomDistance": bool(data.use_custom_distance),
        # SAID OUT LOUD, because turning a toggle on that the caller did not mention is a real
        # change to how the light renders and should not be discovered later.
        "cutoffWasEnabledAutomatically": auto_enabled,
        "note": ("use_custom_distance was OFF, so the cutoff distance would have been stored and "
                 "ignored - it has been turned ON. Pass useCustomDistance:false to store the "
                 "distance without applying it.") if auto_enabled else None,
        "blenderVersion": bpy.app.version_string,
    }

OPS = {
    "set_light_linking": op_set_light_linking,
    "set_light_shadow": op_set_light_shadow,
    "set_light_influence": op_set_light_influence,
    "set_light_ies": op_set_light_ies,
    "set_camera_panorama": op_set_camera_panorama,
    "create_light": op_create_light,
    "set_light": op_set_light,
    "list_lights": op_list_lights,
    "create_camera": op_create_camera,
    "set_camera": op_set_camera,
    "list_cameras": op_list_cameras,
    "aim_object": op_aim_object,
}
