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
_FRAME_KEYS = {"object", "name", "all", "camera"}


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


OPS = {
    "set_viewport_shading": op_set_viewport_shading,
    "frame_viewport": op_frame_viewport,
}
