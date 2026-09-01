"""The world: background colour, strength, and an HDRI - the difference between lit and black.

WHY IT IS A GAP WORTH ITS OWN OP. A scene with no world contributes no ambient light at all, so an
interior lit only by its own fixtures renders as pure black outside their falloff, and the usual
first reaction is to blame the lights. Setting it is three nodes of shader graph, which is exactly
the sort of thing nobody should be writing through an exec hatch.

WHAT "STRENGTH" ACTUALLY DOES, since it is the most misread control here: it multiplies the
background's emission. A strength of 1.0 with a mid-grey colour is roughly an overcast day and will
wash out a deliberately dark interior; the abandoned-lab look wants something nearer 0.02-0.1.
Stated because the number that looks reasonable is usually two orders of magnitude too high.
"""
import os

import bpy

from .ops_common import MifOpError, reject_unknown, rnd, take, take_bool, take_float

_WORLD_KEYS = {
    "name", "color", "backgroundColor", "strength", "hdri", "hdriPath",
    "rotation", "mistUse", "mistStart", "mistDepth", "useAsLight",
}


def _ensure_world(name):
    sc = bpy.context.scene
    if sc.world is None:
        sc.world = bpy.data.worlds.new(name or "World")
    if not sc.world.use_nodes:
        sc.world.use_nodes = True
    return sc.world


def _background_node(world):
    for n in world.node_tree.nodes:
        if n.type == "BACKGROUND":
            return n
    # A world whose tree was rebuilt by hand may have no Background node; make one and wire it
    # rather than refusing, since there is exactly one sensible answer.
    bg = world.node_tree.nodes.new("ShaderNodeBackground")
    out = next((n for n in world.node_tree.nodes if n.type == "OUTPUT_WORLD"), None)
    if out is None:
        out = world.node_tree.nodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return bg


def op_set_world(params):
    """Set the world background, and report what it holds afterwards.

    params:
      color / backgroundColor [r,g,b]   flat background colour
      strength (float)                  emission multiplier - see the header, 1.0 is bright
      hdri / hdriPath (str)             an image file to use as the environment instead
      rotation (float)                  radians, Z rotation of the HDRI
      mistUse / mistStart / mistDepth   the scene mist pass
      useAsLight (bool)                 whether the world contributes to lighting at all
    """
    reject_unknown(params, _WORLD_KEYS, "set_world")
    world = _ensure_world(take(params, "name", default=None, kind=str))
    bg = _background_node(world)

    before = {
        "color": rnd(list(bg.inputs["Color"].default_value)[:3]),
        "strength": round(float(bg.inputs["Strength"].default_value), 6),
        "hasEnvironmentTexture": any(n.type == "TEX_ENVIRONMENT"
                                     for n in world.node_tree.nodes),
    }

    hdri = take(params, "hdri", "hdriPath", default=None, kind=str)
    col = params.get("color", params.get("backgroundColor"))
    if hdri and col is not None:
        raise MifOpError("pass an hdri OR a colour, not both - the environment texture replaces "
                         "the flat colour, so one of them would silently do nothing. NOTHING was "
                         "changed.")

    used_hdri = None
    if hdri:
        path = bpy.path.abspath(str(hdri))
        if not os.path.isfile(path):
            raise MifOpError("no such HDRI file: %s. Blender would create a broken image "
                             "datablock and render black rather than failing. NOTHING was changed."
                             % path)
        tex = next((n for n in world.node_tree.nodes if n.type == "TEX_ENVIRONMENT"), None)
        if tex is None:
            tex = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
            tex.location = (bg.location[0] - 320, bg.location[1])
        tex.image = bpy.data.images.load(path, check_existing=True)
        world.node_tree.links.new(tex.outputs["Color"], bg.inputs["Color"])
        used_hdri = path
        rot = take_float(params, "rotation", default=None)
        if rot is not None:
            mapping = next((n for n in world.node_tree.nodes if n.type == "MAPPING"), None)
            if mapping is None:
                mapping = world.node_tree.nodes.new("ShaderNodeMapping")
                coord = world.node_tree.nodes.new("ShaderNodeTexCoord")
                mapping.location = (tex.location[0] - 220, tex.location[1])
                coord.location = (mapping.location[0] - 220, mapping.location[1])
                world.node_tree.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
                world.node_tree.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
            mapping.inputs["Rotation"].default_value[2] = rot
    elif col is not None:
        if not isinstance(col, (list, tuple)) or len(col) < 3:
            raise MifOpError("colour must be [r,g,b], got %r. NOTHING was changed." % (col,))
        # Unlink any environment texture first, or the flat colour is written and then overridden
        # by the texture that is still plugged in - a change that reports success and does nothing.
        for link in list(world.node_tree.links):
            if link.to_node is bg and link.to_socket.name == "Color":
                world.node_tree.links.remove(link)
        vals = [float(c) for c in col[:3]] + [1.0]
        bg.inputs["Color"].default_value = vals

    strength = take_float(params, "strength", default=None)
    if strength is not None:
        bg.inputs["Strength"].default_value = strength

    sc = bpy.context.scene
    if "useAsLight" in params:
        want = take_bool(params, "useAsLight", default=True)
        if hasattr(sc.world, "cycles_visibility"):
            sc.world.cycles_visibility.diffuse = want
            sc.world.cycles_visibility.glossy = want
    if "mistUse" in params:
        sc.world.mist_settings.use_mist = take_bool(params, "mistUse", default=False)
    ms = take_float(params, "mistStart", default=None)
    if ms is not None:
        sc.world.mist_settings.start = ms
    md = take_float(params, "mistDepth", default=None)
    if md is not None:
        sc.world.mist_settings.depth = md

    return {
        "world": world.name,
        "before": before,
        "after": {
            "color": rnd(list(bg.inputs["Color"].default_value)[:3]),
            "strength": round(float(bg.inputs["Strength"].default_value), 6),
            "hdri": used_hdri,
            "hasEnvironmentTexture": any(n.type == "TEX_ENVIRONMENT"
                                         for n in world.node_tree.nodes),
            "mist": bool(sc.world.mist_settings.use_mist),
        },
        "strengthNote": ("strength multiplies the background's emission. 1.0 with a mid grey is "
                         "roughly an overcast day and will wash out a deliberately dark interior - "
                         "a dim room usually wants 0.02 to 0.1."),
    }


OPS = {"set_world": op_set_world}
