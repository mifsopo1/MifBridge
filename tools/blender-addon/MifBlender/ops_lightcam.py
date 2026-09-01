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

from .ops_common import (MifOpError, object_info, reject_unknown, rnd,
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
            present = [p for p in prop_names if p in params]
            if present and kind != want:
                raise MifOpError("%s only applies to a %s light and this one is %s (%s given). "
                                 "The light WAS created; fix the parameters and set them, or "
                                 "delete it." % (label, want, kind, ", ".join(present)))
            return present

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
        if "radius" in params and kind in ("POINT", "SPOT"):
            data.shadow_soft_size = take_float(params, "radius", default=0.1)
        elif "radius" in params:
            raise MifOpError("'radius' is the soft-shadow size and applies to POINT and SPOT "
                             "lights; this one is %s. The light WAS created." % kind)

        if "shadow" in params:
            val = take_bool(params, "shadow", default=True)
            for attr in ("use_shadow",):
                if hasattr(data, attr):
                    setattr(data, attr, val)
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

        ctype = take(params, "type", default=None, kind=str)
        if ctype:
            valid = {i.identifier for i in bpy.types.Camera.bl_rna.properties["type"].enum_items}
            ctype = str(ctype).upper()
            if ctype not in valid:
                raise MifOpError("unknown camera type '%s'. Valid: %s. The camera WAS created."
                                 % (ctype, ", ".join(sorted(valid))))
            data.type = ctype

        lens = take_float(params, "lens", "focalLength", default=None)
        if lens is not None:
            if data.type != "PERSP":
                raise MifOpError("lens/focalLength applies to a PERSP camera and this one is %s - "
                                 "use orthoScale for ORTHO. The camera WAS created." % data.type)
            data.lens = lens
        sw = take_float(params, "sensorWidth", default=None)
        if sw is not None:
            data.sensor_width = sw
        os_ = take_float(params, "orthoScale", default=None)
        if os_ is not None:
            if data.type != "ORTHO":
                raise MifOpError("orthoScale applies to an ORTHO camera and this one is %s. "
                                 "The camera WAS created." % data.type)
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


OPS = {
    "create_light": op_create_light,
    "create_camera": op_create_camera,
}
