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

import bpy
import mathutils

from .ops_common import (MifOpError, camera_readback, light_readback, object_info,
                         reject_unknown,
                         refuse_unsupported_shadow, rnd, shadow_attr,
                         selection_restore, selection_snapshot, take, take_bool, take_float)

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
}


def _vec3(params, key, default):
    v = params.get(key)
    if v is None:
        return tuple(default)
    if isinstance(v, dict):
        return (float(v.get("x", default[0])), float(v.get("y", default[1])),
                float(v.get("z", default[2])))
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(float(x) for x in v)
    raise MifOpError("'%s' must be {x,y,z} or a 3-list, got %r. NOTHING was created." % (key, v))


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

    snap = selection_snapshot()
    try:
        data = bpy.data.lights.new(name=str(take(params, "name", default="Light", kind=str)),
                                   type=kind)
        obj = bpy.data.objects.new(data.name, data)
        bpy.context.scene.collection.objects.link(obj)

        obj.location = _vec3(params, "location", (0.0, 0.0, 0.0))
        obj.rotation_euler = _vec3(params, "rotation", (0.0, 0.0, 0.0))

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
        out["nameNote"] = ("Blender renames on collision rather than failing, so `name` is what the "
                           "object ACTUALLY got - it may differ from what was asked for.")
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

    # COMMIT. Nothing below can refuse.
    if new_type is not None:
        data.type = new_type
    if "location" in params:
        obj.location = _vec3(params, "location", tuple(obj.location))
    if "rotation" in params:
        obj.rotation_euler = _vec3(params, "rotation", tuple(obj.rotation_euler))
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

    snap = selection_snapshot()
    try:
        data = bpy.data.cameras.new(name=str(take(params, "name", default="Camera", kind=str)))
        obj = bpy.data.objects.new(data.name, data)
        bpy.context.scene.collection.objects.link(obj)

        loc = _vec3(params, "location", (0.0, 0.0, 0.0))
        obj.location = loc
        if "lookAt" in params:
            obj.rotation_euler = _look_at_euler(loc, _vec3(params, "lookAt", (0.0, 0.0, 0.0)))
        else:
            obj.rotation_euler = _vec3(params, "rotation", (0.0, 0.0, 0.0))

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

        make_active = take_bool(params, "makeActive", default=True)
        was = bpy.context.scene.camera.name if bpy.context.scene.camera else None
        if make_active:
            bpy.context.scene.camera = obj

        bpy.context.view_layer.update()
        out = {
            "name": obj.name,
            "dataName": data.name,
            "type": data.type,
            "location": rnd(list(obj.matrix_world.to_translation())),
            "rotationEuler": rnd(list(obj.matrix_world.to_euler())),
            "lens": round(float(data.lens), 6),
            "sensorWidth": round(float(data.sensor_width), 6),
            "clipStart": round(float(data.clip_start), 6),
            "clipEnd": round(float(data.clip_end), 6),
            "dofEnabled": bool(data.dof.use_dof),
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

    # COMMIT. Nothing below can refuse.
    if ctype:
        cam.type = ctype
    if "location" in params:
        obj.location = _vec3(params, "location", tuple(obj.location))
    if "lookAt" in params:
        obj.rotation_euler = _look_at_euler(tuple(obj.location),
                                            _vec3(params, "lookAt", (0.0, 0.0, 0.0)))
    elif "rotation" in params:
        obj.rotation_euler = _vec3(params, "rotation", tuple(obj.rotation_euler))
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


OPS = {
    "create_light": op_create_light,
    "set_light": op_set_light,
    "list_lights": op_list_lights,
    "create_camera": op_create_camera,
    "set_camera": op_set_camera,
    "list_cameras": op_list_cameras,
    "aim_object": op_aim_object,
}
