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

    # CHECKED BEFORE _ensure_world, which is not a reader: on a scene with no world it CREATES one
    # and turns use_nodes on. So set_world({hdri, color}) used to build a World datablock, enable
    # its node tree, and then refuse with "NOTHING was changed" - leaving a world in a file that had
    # none. The conflict needs nothing but params, so there was never a reason for it to be below.
    hdri = take(params, "hdri", "hdriPath", default=None, kind=str)
    col = params.get("color", params.get("backgroundColor"))
    if hdri and col is not None:
        raise MifOpError("pass an hdri OR a colour, not both - the environment texture replaces "
                         "the flat colour, so one of them would silently do nothing. NOTHING was "
                         "changed.")

    # The file check moves up for the same reason: it needs a path and the filesystem, nothing from
    # the world, and it was refusing "NOTHING was changed" with a freshly built World in the file.
    hdri_path = bpy.path.abspath(str(hdri)) if hdri else None
    if hdri_path is not None and not os.path.isfile(hdri_path):
        raise MifOpError("no such HDRI file: %s. Blender would create a broken image "
                         "datablock and render black rather than failing. NOTHING was changed."
                         % hdri_path)

    # And the colour's SHAPE, for the third time the same argument: it is a check on the argument,
    # it needs no world, and below _ensure_world it was refusing with one already created.
    if col is not None and (not isinstance(col, (list, tuple)) or len(col) < 3):
        raise MifOpError("colour must be [r,g,b], got %r. NOTHING was changed." % (col,))

    world = _ensure_world(take(params, "name", default=None, kind=str))
    bg = _background_node(world)

    before = {
        "color": rnd(list(bg.inputs["Color"].default_value)[:3]),
        "strength": round(float(bg.inputs["Strength"].default_value), 6),
        "environmentTexturePresent": any(n.type == "TEX_ENVIRONMENT"
                                         for n in world.node_tree.nodes),
        "environmentTextureDriving": (
            _trace_to_texture(world.node_tree, bg.inputs["Color"]).name
            if _trace_to_texture(world.node_tree, bg.inputs["Color"]) is not None else None),
    }

    used_hdri = None
    if hdri:
        path = hdri_path
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
            # PRESENT vs DRIVING, and they are not the same question. The flat-colour branch
            # above removes the LINK into Background.Color and leaves the TEX_ENVIRONMENT node
            # in the tree, so the old single `hasEnvironmentTexture` stayed true forever once an
            # HDRI had ever been set - reporting an environment in play when the render used the
            # flat colour. In a node tree the effect lives on the link; see world_info.
            "environmentTexturePresent": any(n.type == "TEX_ENVIRONMENT"
                                             for n in world.node_tree.nodes),
            "environmentTextureDriving": (
                _trace_to_texture(world.node_tree, bg.inputs["Color"]).name
                if _trace_to_texture(world.node_tree, bg.inputs["Color"]) is not None else None),
            "mist": bool(sc.world.mist_settings.use_mist),
        },
        "strengthNote": ("strength multiplies the background's emission. 1.0 with a mid grey is "
                         "roughly an overcast day and will wash out a deliberately dark interior - "
                         "a dim room usually wants 0.02 to 0.1."),
    }


_INFO_KEYS = {"name"}

_MAX_TRACE = 32


def _output_node(world):
    """The OUTPUT_WORLD node, if there is one. Pure - creates nothing."""
    return next((n for n in world.node_tree.nodes if n.type == "OUTPUT_WORLD"), None)


def _surface_source(world):
    """The node actually feeding the world output's Surface socket, or None.

    NOT the same question as "is there a Background node", and the difference is the whole reason
    this exists. _background_node returns the first BACKGROUND node in the tree whether or not it is
    WIRED to anything, so a world whose Background node has been disconnected accepts every write,
    reads every value back correctly, and renders as if none of it happened. That is the shape
    02_GOTCHAS calls right-looking and inert, and in a node tree the effect always lives on the LINK
    rather than on the node.
    """
    out = _output_node(world)
    if out is None:
        return None
    for link in world.node_tree.links:
        if link.to_node is out and link.to_socket.name == "Surface":
            return link.from_node
    return None


def _find_background(world):
    """(node, connected). A BACKGROUND node and whether it actually drives the world output.

    PURE, unlike _background_node, which CREATES a Background node and wires it when the tree has
    none. That is right for a setter and disqualifying for a reader: an info op that silently
    authors two nodes changes the file it was asked to describe, and the answer it then returns is
    about the world it just made rather than the one that was there.
    """
    src = _surface_source(world)
    if src is not None and src.type == "BACKGROUND":
        return src, True
    node = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    return node, False


def _trace_to_texture(tree, socket, seen=None, depth=0):
    """Walk BACKWARDS from an input socket to the TEX_ENVIRONMENT actually driving it, if any.

    A caller can put a Mapping, a Mix or a colour ramp between the environment texture and the
    Background node, so "is the texture linked directly" is the wrong test - it answers no for a
    perfectly normal graph. Following the links answers the question that matters, which is whether
    any environment texture reaches this socket at all.

    WHAT ACTUALLY GUARANTEES TERMINATION IS THE DEPTH LIMIT, not the seen-set, and that was
    established rather than assumed: a plant removing the seen-set on 2026-09-03 changed nothing -
    the suite stayed green because a cycle simply recurses until depth exceeds _MAX_TRACE. The
    seen-set is a WORK SAVER, stopping a diamond-shaped graph from being re-walked once per path.
    Both are kept; the comment used to credit the wrong one.

    A KNOWN AND BOUNDED LIMITATION FROM COMBINING THEM: a node first reached by a long path is
    marked seen and may have been cut off by the depth limit there, so a shorter second path to the
    same node is not re-explored. That can miss a texture more than _MAX_TRACE nodes deep on one
    route and shallow on another. It needs a graph over 32 nodes deep on the first route tried,
    which no world tree this addon builds comes near, and the failure is a null - reported as "no
    environment texture driving this" - rather than a wrong name.

    THE TREE IS PASSED IN rather than reached through socket.id_data. For a node tree embedded in a
    World, what id_data resolves to is exactly the kind of API detail that cannot be checked from
    here - there is no Blender in the offline suite to ask - and guessing it would put an
    unverifiable assumption underneath every answer this module gives. The caller always has the
    tree already.
    """
    if seen is None:
        seen = set()
    if depth > _MAX_TRACE:
        return None
    for link in tree.links:
        if link.to_socket is not socket:
            continue
        node = link.from_node
        if node.name in seen:
            continue
        seen.add(node.name)
        if node.type == "TEX_ENVIRONMENT":
            return node
        for inp in node.inputs:
            got = _trace_to_texture(tree, inp, seen, depth + 1)
            if got is not None:
                return got
    return None


def op_world_info(params):
    """What the world IS - the read half of set_world, which had none, for the op that decides black.

    THE FAMILY WAS WRITE-ONLY. set_world could set a colour, a strength, an HDRI, a rotation and
    mist, and nothing anywhere could read any of it back: scene_info omits the world entirely and
    render_info reports only its NAME. So "what is my world actually set to" was unanswerable
    through the typed path, on the one datablock that decides whether an interior renders black.

    EVERY ANSWER HERE IS TAKEN FROM THE LINK, NOT THE NODE, because a shader tree's effect lives on
    its connections:

      * The Background node is reported with `backgroundConnected`, because a Background node that
        is not wired to the world output accepts every write and contributes nothing.
      * The environment texture is reported as `environmentTextureDriving`, found by walking
        backwards from the Background's Colour socket - so a Mapping or a Mix in between still
        counts, and a texture sitting unlinked in the tree correctly does not. set_world's old
        `hasEnvironmentTexture` asked only whether the NODE existed, which stays true forever after
        an HDRI is replaced by a flat colour: the link is removed and the node is left behind.
      * `useNodes` is reported because a world with use_nodes False IGNORES THE WHOLE TREE and
        renders its flat `world.color` instead. Every node in it reads perfectly and none applies.

    `contributesLight` is the diagnosis rather than the inputs to it, matching render_info.

    params:
      name (str)   a specific world by name. Default the scene's world.
    """
    reject_unknown(params, _INFO_KEYS, "world_info")
    name = take(params, "name", kind=str)
    sc = bpy.context.scene
    if name:
        world = bpy.data.worlds.get(name)
        if world is None:
            known = sorted(w.name for w in bpy.data.worlds)
            raise MifOpError("no world named '%s'. Present: %s."
                             % (name, ", ".join(known) if known else "<none>"))
    else:
        world = sc.world

    if world is None:
        # NOT AN ERROR. "There is no world" is a real and common state, it is the single most
        # common cause of a black interior, and refusing here would make the op unable to report
        # the very thing it is most useful for.
        return {
            "ok": True,
            "world": None,
            "isSceneWorld": False,
            "contributesLight": False,
            "worldsInFile": sorted(w.name for w in bpy.data.worlds),
            "blockers": ["scene '%s' has NO world datablock, so it contributes no ambient light at "
                         "all - an interior renders pure black outside its own fixtures and the "
                         "lights usually get blamed. Create one with set_world." % sc.name],
        }

    use_nodes = bool(world.use_nodes)
    out = {
        "ok": True,
        "world": world.name,
        "isSceneWorld": sc.world is not None and sc.world.name == world.name,
        "useNodes": use_nodes,
        "worldsInFile": sorted(w.name for w in bpy.data.worlds),
        # THE FLAT COLOUR, which is what actually renders when useNodes is False.
        "flatColor": rnd(list(world.color)[:3]) if hasattr(world, "color") else None,
    }

    blockers = []
    bg = None
    if not use_nodes:
        blockers.append("use_nodes is FALSE on this world, so its entire node tree is IGNORED and "
                        "the flat world.color renders instead. Every node in it reads correctly "
                        "and none of it applies.")
    else:
        bg, connected = _find_background(world)
        surface = _surface_source(world)
        out["nodeCount"] = len(world.node_tree.nodes)
        out["surfaceDrivenBy"] = surface.type if surface is not None else None
        out["backgroundConnected"] = bool(connected)
        if bg is None:
            blockers.append("there is no Background node in this world's tree at all - nothing "
                            "drives the world output, so it contributes no light.")
        else:
            colour_socket = bg.inputs["Color"]
            tex = _trace_to_texture(world.node_tree, colour_socket)
            strength = round(float(bg.inputs["Strength"].default_value), 6)
            out["color"] = rnd(list(colour_socket.default_value)[:3])
            out["strength"] = strength
            out["environmentTexturePresent"] = any(n.type == "TEX_ENVIRONMENT"
                                                   for n in world.node_tree.nodes)
            out["environmentTextureDriving"] = tex.name if tex is not None else None
            out["hdri"] = (getattr(tex.image, "filepath", None) if tex is not None
                           and tex.image is not None else None)
            if tex is not None and tex.image is None:
                blockers.append("the environment texture '%s' drives the background but has NO "
                                "image assigned - it contributes black." % tex.name)
            mapping = next((n for n in world.node_tree.nodes if n.type == "MAPPING"), None)
            out["rotation"] = (round(float(mapping.inputs["Rotation"].default_value[2]), 6)
                               if mapping is not None else None)
            if not connected:
                blockers.append("the Background node exists but is NOT connected to the world "
                                "output, so every value on it is inert - it accepts writes and "
                                "changes no light.")
            if strength == 0.0:
                blockers.append("world strength is 0, so the background emits nothing regardless "
                                "of its colour.")
            elif (out["environmentTextureDriving"] is None
                  and all(c <= 0.0 for c in out["color"])):
                blockers.append("the background colour is black and no environment texture drives "
                                "it, so the world contributes no light. strength will not help.")

    mist = getattr(world, "mist_settings", None)
    out["mist"] = {
        "use": bool(mist.use_mist) if mist else None,
        "start": round(float(mist.start), 6) if mist else None,
        "depth": round(float(mist.depth), 6) if mist else None,
    }
    vis = getattr(world, "cycles_visibility", None)
    out["cyclesVisibility"] = ({"diffuse": bool(vis.diffuse), "glossy": bool(vis.glossy)}
                               if vis is not None else None)
    if vis is not None and not vis.diffuse and not vis.glossy:
        blockers.append("cycles_visibility has diffuse and glossy both off (set_world's "
                        "useAsLight:false), so the world is visible in the background but lights "
                        "nothing.")

    out["blockers"] = blockers
    out["contributesLight"] = not blockers
    return out


OPS = {
    "set_world": op_set_world,
    "world_info": op_world_info,
}
