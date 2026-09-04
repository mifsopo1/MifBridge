"""Viewport shading and framing - what makes the work VISIBLE while it is happening.

WHY THIS IS A REAL GAP AND NOT A CONVENIENCE. Andre was recording a build and reported "it was all
grey and I couldn't see any of that happening in real time" - while three fluorescent fixtures, a
window shaft and two practicals were being created correctly. Nothing was wrong with the lighting.
The viewport was in SOLID shading, which ignores materials and lights by design, so the entire
lighting stage was invisible to the only person watching.

A bridge that can light a scene and cannot show it has not finished the job. Every other op here
changes what is IN the file; this one changes what the human can see of it, and for a tool driven
by an agent on somebody else's screen that is not a lesser thing.

MATERIAL vs RENDERED, since picking the wrong one is the usual disappointment: MATERIAL preview
uses a built-in studio light and shows materials but NOT your scene's lamps. Only RENDERED runs the
actual render engine, so it is the only mode in which a flickering light flickers.
"""
import bpy

from .ops_common import MifOpError, get_object, reject_unknown, take, take_bool, take_float

_SHADE_KEYS = {"shading", "mode", "studioLight", "useSceneLights", "useSceneWorld",
               "showOverlays", "showGizmos", "colorType"}
# NO "all". Framing everything is what this op does when no object is named - view3d.view_all
# is the else branch - so "all" named the DEFAULT and was read nowhere. Worse in combination:
# object plus all:true is a contradiction the caller can express, and the object silently
# won. Omitting object is the way to say it, and a caller who sends all now gets a refusal
# instead of agreement.
_FRAME_KEYS = {"object", "name", "camera"}
# No "target" alias for focus. It was accepted and no tool sent it, which param_reach
# correctly called unreachable - and a second name for the same thing is not worth a
# baseline entry. One name, one meaning.
_VIEW_KEYS = {"focus", "distance", "azimuth", "elevation", "lookFrom",
              "perspective", "lens"}


def _view3d_spaces():
    """Every 3D viewport in the current screen. There may be none in a headless run."""
    out = []
    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return out
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for sp in area.spaces:
            if sp.type == "VIEW_3D":
                out.append((area, sp))
    return out


def op_set_viewport_shading(params):
    """Set the 3D viewport's shading mode, and report how many viewports actually changed.

    params:
      shading (alias mode)  WIREFRAME | SOLID | MATERIAL | RENDERED
      useSceneLights        RENDERED/MATERIAL: use the scene's own lamps rather than a studio light
      useSceneWorld         RENDERED/MATERIAL: use the scene's world rather than a studio HDRI
      studioLight           name of the studio light, when not using scene lights
      showOverlays          grids, outlines, relationship lines
      showGizmos
      colorType             MATERIAL | OBJECT | RANDOM | SINGLE | TEXTURE - SOLID mode only
    """
    reject_unknown(params, _SHADE_KEYS, "set_viewport_shading")
    spaces = _view3d_spaces()
    if not spaces:
        raise MifOpError("there is no 3D viewport in this Blender - a background/headless session "
                         "has no screen at all, so there is nothing to shade. NOTHING was changed.")

    want = take(params, "shading", "mode", default=None, kind=str)
    if want:
        want = str(want).upper()
        valid = {"WIREFRAME", "SOLID", "MATERIAL", "RENDERED"}
        if want not in valid:
            raise MifOpError("shading must be one of %s, got '%s'. NOTHING was changed."
                             % (", ".join(sorted(valid)), want))

    changed, before = 0, None
    for _area, sp in spaces:
        sh = sp.shading
        if before is None:
            before = sh.type
        if want:
            sh.type = want
        if "useSceneLights" in params:
            v = take_bool(params, "useSceneLights", default=True)
            for attr in ("use_scene_lights", "use_scene_lights_render"):
                if hasattr(sh, attr):
                    setattr(sh, attr, v)
        if "useSceneWorld" in params:
            v = take_bool(params, "useSceneWorld", default=True)
            for attr in ("use_scene_world", "use_scene_world_render"):
                if hasattr(sh, attr):
                    setattr(sh, attr, v)
        sl = take(params, "studioLight", default=None, kind=str)
        if sl:
            try:
                sh.studio_light = str(sl)
            except (TypeError, ValueError) as exc:
                raise MifOpError("no studio light called '%s' in this Blender: %s" % (sl, exc))
        ct = take(params, "colorType", default=None, kind=str)
        if ct:
            sh.color_type = str(ct).upper()
        if "showOverlays" in params:
            sp.overlay.show_overlays = take_bool(params, "showOverlays", default=True)
        if "showGizmos" in params:
            sp.show_gizmo = take_bool(params, "showGizmos", default=True)
        changed += 1

    now = spaces[0][1].shading
    return {
        "viewportsChanged": changed,
        "before": before,
        "shading": now.type,
        "useSceneLights": bool(getattr(now, "use_scene_lights", False)),
        "useSceneWorld": bool(getattr(now, "use_scene_world", False)),
        "modeNote": ("MATERIAL preview uses a studio light and does NOT show your scene's lamps; "
                     "only RENDERED runs the render engine, so it is the only mode in which a "
                     "flickering light actually flickers."),
    }


def op_frame_viewport(params):
    """Point the viewport at something - the whole scene, one object, or through the camera.

    Framing is not cosmetic when somebody is watching: a build that happens off-screen is a build
    nobody can see happen.
    """
    reject_unknown(params, _FRAME_KEYS, "frame_viewport")
    spaces = _view3d_spaces()
    if not spaces:
        raise MifOpError("there is no 3D viewport in this Blender. NOTHING was changed.")
    area, sp = spaces[0]

    if take_bool(params, "camera", default=False):
        if bpy.context.scene.camera is None:
            raise MifOpError("there is no scene camera to look through. NOTHING was changed.")
        sp.region_3d.view_perspective = "CAMERA"
        return {"view": "CAMERA", "camera": bpy.context.scene.camera.name}

    name = take(params, "object", "name", default=None, kind=str)
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None:
        raise MifOpError("the viewport has no WINDOW region to frame into. NOTHING was changed.")

    try:
        with bpy.context.temp_override(area=area, region=region, space_data=sp,
                                       screen=bpy.context.screen, scene=bpy.context.scene,
                                       view_layer=bpy.context.view_layer):
            if name:
                obj = get_object(name)
                for o in bpy.context.view_layer.objects:
                    o.select_set(False)
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.view3d.view_selected()
            else:
                bpy.ops.view3d.view_all()
            sp.region_3d.view_perspective = "PERSP"
    except RuntimeError as exc:
        raise MifOpError("could not frame the viewport: %s" % exc)
    return {"view": "PERSP", "framed": name or "all objects",
            "objectCount": len(bpy.context.view_layer.objects)}


def op_set_viewport_view(params):
    """Place the viewport's own camera - the orbit pivot, the distance and the angle.

    WHY THIS EXISTS SEPARATELY FROM frame_viewport. Framing answers "show me this object"; this
    answers "stand HERE and look THERE", which is what a walkthrough needs. Andre asked for the
    view to move itself while a scene is being built so he does not have to drive it, and framing
    alone cannot do a slow push down a room.

    THE VIEWPORT IS AN ORBIT, NOT A CAMERA. region_3d has view_location (the pivot), view_distance
    (how far back) and view_rotation (a quaternion). There is no "position" to set directly - the
    eye is derived from those three, so a caller who thinks in eye-positions gets lookFrom, which
    is converted here rather than left as an exercise.

    params:
      focus {x,y,z}            the point being orbited - what you are looking AT
      distance (float)         metres back from the focus
      azimuth (float)          RADIANS around Z. 0 looks along +Y, increasing turns anticlockwise
      elevation (float)        RADIANS above the horizon; positive looks DOWN at the focus
      lookFrom {x,y,z}         eye position INSTEAD of azimuth/elevation/distance - all three are
                               derived from it, and passing both is refused
      perspective              PERSP | ORTHO | CAMERA
    """
    import math
    import mathutils

    reject_unknown(params, _VIEW_KEYS, "set_viewport_view")
    spaces = _view3d_spaces()
    if not spaces:
        raise MifOpError("there is no 3D viewport in this Blender. NOTHING was changed.")
    _area, sp = spaces[0]
    r3d = sp.region_3d

    have_polar = any(k in params for k in ("azimuth", "elevation", "distance"))
    if "lookFrom" in params and have_polar:
        raise MifOpError("pass lookFrom OR azimuth/elevation/distance, not both - lookFrom already "
                         "determines all three. NOTHING was changed.")

    # EVERYTHING IS VALIDATED BEFORE ANY OF IT IS APPLIED. This block used to write
    # r3d.view_location as soon as `focus` was parsed and then run FOUR more refusals below it,
    # three of them ending "NOTHING was changed" - so {"focus":[1,2,3],"lookFrom":[1,2,3]} moved
    # the viewport pivot and then told the caller nothing had happened. Same for a negative
    # distance, and for perspective:"CAMERA" with no scene camera, which could fire after the
    # pivot, the distance AND the rotation had all been set.
    #
    # That sentence is what every refusal in MifBridge is held to, so a false one is worse than an
    # ordinary bug. The house rule is that a refusal fires before a mutation; this now obeys it by
    # computing the whole target state first and committing it only once nothing can still refuse.
    focus = params.get("focus")
    f = _vec3_of(focus, "focus") if focus is not None else tuple(r3d.view_location)

    new_distance = None
    new_rotation = None
    if "lookFrom" in params:
        eye = _vec3_of(params["lookFrom"], "lookFrom")
        d = mathutils.Vector((f[0] - eye[0], f[1] - eye[1], f[2] - eye[2]))
        if d.length == 0.0:
            raise MifOpError("lookFrom is the focus point, so there is no direction to look. "
                             "NOTHING was changed.")
        new_distance = d.length
        # The view looks down its own -Z, same convention as a camera object.
        new_rotation = d.to_track_quat("-Z", "Y")
    else:
        dist = take_float(params, "distance", default=None)
        if dist is not None:
            if dist <= 0:
                raise MifOpError("distance must be positive, got %r. NOTHING was changed." % dist)
            new_distance = dist
        az = take_float(params, "azimuth", default=None)
        el = take_float(params, "elevation", default=None)
        if az is not None or el is not None:
            az = 0.0 if az is None else az
            el = 0.35 if el is None else el
            # Direction from eye toward focus, from polar angles: azimuth 0 looks along +Y.
            d = mathutils.Vector((math.sin(az) * math.cos(el),
                                  math.cos(az) * math.cos(el),
                                  -math.sin(el)))
            new_rotation = d.to_track_quat("-Z", "Y")

    persp = take(params, "perspective", default=None, kind=str)
    want = None
    if persp:
        want = str(persp).upper()
        if want not in ("PERSP", "ORTHO", "CAMERA"):
            raise MifOpError("perspective must be PERSP, ORTHO or CAMERA, got '%s'. "
                             "NOTHING was changed." % persp)
        if want == "CAMERA" and bpy.context.scene.camera is None:
            raise MifOpError("there is no scene camera to look through. NOTHING was changed.")
    lens = take_float(params, "lens", default=None)

    # COMMIT. Nothing below here can refuse.
    if focus is not None:
        r3d.view_location = f
    if new_distance is not None:
        r3d.view_distance = new_distance
    if new_rotation is not None:
        r3d.view_rotation = new_rotation
    if want:
        r3d.view_perspective = want
    if lens is not None:
        sp.lens = lens

    eye = r3d.view_matrix.inverted().translation
    return {
        "focus": [round(v, 4) for v in r3d.view_location],
        "distance": round(float(r3d.view_distance), 4),
        "eye": [round(v, 4) for v in eye],
        "perspective": r3d.view_perspective,
        "lens": round(float(sp.lens), 3),
    }


def _vec3_of(v, key):
    if isinstance(v, dict):
        return (float(v.get("x", 0.0)), float(v.get("y", 0.0)), float(v.get("z", 0.0)))
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(float(x) for x in v)
    raise MifOpError("'%s' must be {x,y,z} or a 3-list, got %r." % (key, v))


OPS = {
    "set_viewport_shading": op_set_viewport_shading,
    "set_viewport_view": op_set_viewport_view,
    "frame_viewport": op_frame_viewport,
}
