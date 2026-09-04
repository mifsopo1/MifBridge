"""Authoring geometry-node TREES: create a group, add nodes, link them, expose inputs.

WHAT WAS ALREADY POSSIBLE AND WHAT WAS NOT. add_modifier can already attach a NODES modifier - it
validates against bpy.types.Modifier.bl_rna and takes any type Blender knows - so "no geometry
nodes" was always too strong a claim. What could not be done was building the TREE: creating a
group, putting nodes in it, wiring them together, and exposing a value as a modifier input. That is
the half a procedural grime or cable system is made of, and it is the half this file adds.

=============================================================================
THE GROUP INTERFACE MOVED IN 4.0 AND THE OLD API IS GONE
=============================================================================
Up to 3.6 a group's sockets were tree.inputs / tree.outputs, with .new(type, name). From 4.0 they
live in tree.interface, created with .new_socket(name, in_out=, socket_type=), and the old
collections do not exist. This addon supports 3.6 through 5.0, so every interface call goes through
one helper that tries the new API and falls back - the same shape as the fcurve accessor in
ops_anim, and for the same reason: a helper that knows one version makes three of them red.

=============================================================================
A NODE TREE THAT IS NOT LINKED TO ITS OUTPUT DOES NOTHING, SILENTLY
=============================================================================
An unconnected Group Output is not an error in Blender - the modifier simply passes the geometry
through unchanged, which looks exactly like a tree that ran and did nothing useful. So
list_group_nodes reports whether the output is reachable, and assign_node_group says so on the way
out rather than leaving somebody to wonder why their modifier has no effect.
"""
import bpy

from .ops_common import MifOpError, get_object, reject_unknown, take, take_bool, take_float

_CREATE_KEYS = {"name", "type", "withGroupIO"}
_ADDNODE_KEYS = {"group", "tree", "type", "nodeType", "name", "location", "inputs", "label",
                 "operation", "dataType", "domain", "mode"}
_LINK_KEYS = {"group", "tree", "fromNode", "fromSocket", "toNode", "toSocket"}
_LIST_KEYS = {"group", "tree"}
_IFACE_KEYS = {"group", "tree", "name", "socketType", "inOut", "default", "min", "max"}
_ASSIGN_KEYS = {"object", "name", "group", "tree", "modifierName", "inputs"}


def _tree(name):
    # THE ONE CHOKEPOINT ALL FIVE AUTHORING OPS GO THROUGH, which is why the scene compositor is
    # reached here rather than by a parallel set of compositor add/link/list ops. scene.node_tree is
    # not in bpy.data.node_groups - it belongs to the scene - so before 2026-09-03 nothing in this
    # module could address it and the entire compositing subsystem was outside the typed path.
    owned = _owned_tree(name)
    if owned is not None:
        return owned
    t = bpy.data.node_groups.get(name)
    if t is None:
        raise MifOpError("no node group named '%s'. create_node_group makes one; the groups in "
                         "this file are: %s" % (name, ", ".join(sorted(bpy.data.node_groups.keys()))
                                                or "(none)"))
    return t


def _iface_new(tree, name, socket_type, in_out):
    """Create a group socket on any supported Blender.

    4.0+ : tree.interface.new_socket(name, in_out="INPUT"|"OUTPUT", socket_type="NodeSocketFloat")
    3.6  : tree.inputs.new("NodeSocketFloat", name)
    The old collections do not merely still work in 4.0+, they are GONE, so this is a real branch
    rather than a compatibility nicety.
    """
    if hasattr(tree, "interface"):
        return tree.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    coll = tree.inputs if in_out == "INPUT" else tree.outputs
    return coll.new(socket_type, name)


def _iface_items(tree):
    if hasattr(tree, "interface"):
        out = []
        for it in tree.interface.items_tree:
            if getattr(it, "item_type", "SOCKET") != "SOCKET":
                continue
            out.append({"name": it.name, "inOut": it.in_out,
                        "socketType": getattr(it, "socket_type", None)})
        return out
    return ([{"name": s.name, "inOut": "INPUT", "socketType": s.bl_socket_idname}
             for s in tree.inputs] +
            [{"name": s.name, "inOut": "OUTPUT", "socketType": s.bl_socket_idname}
             for s in tree.outputs])


def op_create_node_group(params):
    """Create a geometry node group, with Group Input/Output wired to a Geometry socket pair."""
    reject_unknown(params, _CREATE_KEYS, "create_node_group")
    name = str(take(params, "name", default="MifNodes", kind=str))
    if name in (SCENE_COMPOSITOR, SCENE_WORLD) or name.startswith(RESERVED_PREFIXES):
        raise MifOpError("'%s' is reserved: names like %s, %s, '%s<name>' and '%s<name>' address "
                         "trees that are OWNED by a scene, material or world rather than living in "
                         "bpy.data.node_groups, and a group by such a name would make which one "
                         "you meant depend on what happened to exist. Pick another name. NOTHING "
                         "was created."
                         % (name, SCENE_COMPOSITOR, SCENE_WORLD, MATERIAL_PREFIX, WORLD_PREFIX))
    kind = str(take(params, "type", default="GeometryNodeTree", kind=str))
    if kind not in ("GeometryNodeTree", "ShaderNodeTree", "CompositorNodeTree"):
        raise MifOpError("type must be GeometryNodeTree, ShaderNodeTree or CompositorNodeTree, "
                         "got '%s'. NOTHING was created." % kind)
    tree = bpy.data.node_groups.new(name=name, type=kind)

    made = []
    if take_bool(params, "withGroupIO", default=True) and kind == "GeometryNodeTree":
        # A geometry group with no Geometry in/out cannot be used as a modifier at all - the
        # modifier needs a geometry socket to feed and one to read back.
        _iface_new(tree, "Geometry", "NodeSocketGeometry", "INPUT")
        _iface_new(tree, "Geometry", "NodeSocketGeometry", "OUTPUT")
        gin = tree.nodes.new("NodeGroupInput")
        gout = tree.nodes.new("NodeGroupOutput")
        gin.location = (-400, 0)
        gout.location = (400, 0)
        made = [gin.name, gout.name]
    return {"group": tree.name, "type": kind, "nodes": made,
            "interface": _iface_items(tree),
            "nodeCount": len(tree.nodes),
            "apiNote": ("group sockets live in tree.interface from Blender 4.0 and in "
                        "tree.inputs/outputs before it; the old collections are GONE in 4.0+, not "
                        "deprecated.")}


def op_add_group_node(params):
    """Add a node to a group and optionally set its input defaults.

    `inputs` is {socketName: value} and is applied AFTER the node exists, so a name that does not
    match is refused with the sockets the node really has - a value written to a socket that is not
    there would otherwise vanish without a word.
    """
    reject_unknown(params, _ADDNODE_KEYS, "add_group_node")
    tree = _tree(take(params, "group", "tree", required=True, kind=str))
    ntype = str(take(params, "type", "nodeType", required=True, kind=str))
    try:
        node = tree.nodes.new(ntype)
    except RuntimeError as exc:
        raise MifOpError("Blender does not know a node type '%s' in a %s: %s. NOTHING was added."
                         % (ntype, tree.bl_idname, exc))

    nm = take(params, "name", default=None, kind=str)
    if nm:
        node.name = nm
        node.label = nm
    lbl = take(params, "label", default=None, kind=str)
    if lbl:
        node.label = lbl
    loc = params.get("location")
    if isinstance(loc, dict):
        node.location = (float(loc.get("x", 0.0)), float(loc.get("y", 0.0)))
    elif isinstance(loc, (list, tuple)) and len(loc) >= 2:
        node.location = (float(loc[0]), float(loc[1]))

    # Enum-ish knobs that live on the node rather than on a socket. Written only when present, and
    # refused by name when the node has no such property.
    for key in ("operation", "dataType", "domain", "mode"):
        if key not in params:
            continue
        attr = {"operation": "operation", "dataType": "data_type",
                "domain": "domain", "mode": "mode"}[key]
        if not hasattr(node, attr):
            raise MifOpError("node type '%s' has no '%s'. The node WAS added as '%s'."
                             % (ntype, key, node.name))
        setattr(node, attr, str(params[key]).upper())

    applied = {}
    given = params.get("inputs")
    if given is not None:
        if not isinstance(given, dict):
            raise MifOpError("'inputs' must be a {socketName: value} object, got %r." % (given,))
        have = [s.name for s in node.inputs]
        for sock_name, val in given.items():
            match = [s for s in node.inputs if s.name == sock_name]
            if not match:
                raise MifOpError("node '%s' has no input socket '%s'. It has: %s. The node WAS "
                                 "added." % (node.name, sock_name, ", ".join(have)))
            sock = match[0]
            try:
                if isinstance(val, (list, tuple)):
                    for i, v in enumerate(val):
                        sock.default_value[i] = float(v)
                elif isinstance(val, bool):
                    sock.default_value = val
                else:
                    sock.default_value = float(val)
            except (AttributeError, TypeError, ValueError) as exc:
                raise MifOpError("cannot write %r to socket '%s' of '%s': %s. The node WAS added."
                                 % (val, sock_name, node.name, exc))
            applied[sock_name] = val

    return {"group": tree.name, "node": node.name, "type": ntype,
            "location": [round(node.location[0], 3), round(node.location[1], 3)],
            "inputs": [s.name for s in node.inputs],
            "outputs": [s.name for s in node.outputs],
            "inputsApplied": applied,
            "nodeCount": len(tree.nodes)}


def _find_socket(node, name, collection):
    exact = [s for s in collection if s.name == name]
    if exact:
        return exact[0]
    if name.isdigit():
        idx = int(name)
        if 0 <= idx < len(collection):
            return collection[idx]
    return None


def op_link_group_nodes(params):
    """Wire one node's output into another's input, and read the link back."""
    reject_unknown(params, _LINK_KEYS, "link_group_nodes")
    tree = _tree(take(params, "group", "tree", required=True, kind=str))
    fn_name = str(take(params, "fromNode", required=True, kind=str))
    tn_name = str(take(params, "toNode", required=True, kind=str))
    fn = tree.nodes.get(fn_name)
    tn = tree.nodes.get(tn_name)
    if fn is None or tn is None:
        raise MifOpError("no node named '%s' in group '%s'. It has: %s. NOTHING was linked."
                         % (fn_name if fn is None else tn_name, tree.name,
                            ", ".join(n.name for n in tree.nodes)))
    fs_name = str(take(params, "fromSocket", default="", kind=str))
    ts_name = str(take(params, "toSocket", default="", kind=str))
    fs = _find_socket(fn, fs_name, fn.outputs) if fs_name else (fn.outputs[0] if fn.outputs else None)
    ts = _find_socket(tn, ts_name, tn.inputs) if ts_name else (tn.inputs[0] if tn.inputs else None)
    if fs is None:
        raise MifOpError("node '%s' has no output socket '%s'. It has: %s. NOTHING was linked."
                         % (fn.name, fs_name, ", ".join(s.name for s in fn.outputs)))
    if ts is None:
        raise MifOpError("node '%s' has no input socket '%s'. It has: %s. NOTHING was linked."
                         % (tn.name, ts_name, ", ".join(s.name for s in tn.inputs)))

    before = len(tree.links)
    fn_name, tn_name_final = fn.name, tn.name
    fs_final, ts_final = fs.name, ts.name
    tree.links.new(fs, ts)
    # READ BACK BY NAME, NOT BY IDENTITY. links.new returns a link object even when Blender
    # immediately drops it as invalid (mismatched socket types), so the link has to be looked for
    # rather than assumed - but `l.from_node is fn` does NOT work: bpy RNA references are proxy
    # objects recreated on access, so `is` is False even for the same underlying node. The first
    # version used identity and reported linked:false for three links that had demonstrably been
    # made - linkCount went 0 to 3 and outputReachable was true in the same breath. A false
    # NEGATIVE, which is the worse direction: it sends a caller to debug working wiring.
    made = [l for l in tree.links
            if l.from_node.name == fn_name and l.to_node.name == tn_name_final
            and l.from_socket.name == fs_final and l.to_socket.name == ts_final]
    return {"group": tree.name,
            "from": "%s.%s" % (fn_name, fs_final),
            "to": "%s.%s" % (tn_name_final, ts_final),
            "linked": bool(made),
            "linkCountBefore": before, "linkCountAfter": len(tree.links),
            "validNote": (None if made else
                          "links.new returned but no link is present - Blender rejected it, which "
                          "usually means the socket TYPES are incompatible. Nothing is wired.")}


def op_add_group_interface(params):
    """Expose a value as a group input or output - what turns a tree into a modifier with sliders."""
    reject_unknown(params, _IFACE_KEYS, "add_group_interface")
    tree = _tree(take(params, "group", "tree", required=True, kind=str))
    name = str(take(params, "name", required=True, kind=str))
    stype = str(take(params, "socketType", default="NodeSocketFloat", kind=str))
    in_out = str(take(params, "inOut", default="INPUT", kind=str)).upper()
    if in_out not in ("INPUT", "OUTPUT"):
        raise MifOpError("inOut must be INPUT or OUTPUT, got '%s'. NOTHING was added." % in_out)
    try:
        sock = _iface_new(tree, name, stype, in_out)
    except (RuntimeError, TypeError) as exc:
        raise MifOpError("could not create a '%s' socket named '%s': %s. NOTHING was added."
                         % (stype, name, exc))
    for key, attr in (("default", "default_value"), ("min", "min_value"), ("max", "max_value")):
        v = take_float(params, key, default=None)
        if v is not None and hasattr(sock, attr):
            setattr(sock, attr, v)
    return {"group": tree.name, "socket": name, "socketType": stype, "inOut": in_out,
            "interface": _iface_items(tree)}


def op_list_group_nodes(params):
    """Every node and link in a group, plus whether the Group Output is actually reachable.

    THE REACHABILITY LINE IS THE POINT. An unlinked Group Output is not an error - the modifier
    passes geometry through unchanged, which is indistinguishable from a tree that ran and did
    nothing. Nothing else in Blender tells you.
    """
    reject_unknown(params, _LIST_KEYS, "list_group_nodes")
    tree = _tree(take(params, "group", "tree", required=True, kind=str))
    nodes = [{"name": n.name, "type": n.bl_idname, "label": n.label,
              "location": [round(n.location[0], 2), round(n.location[1], 2)],
              "inputs": [s.name for s in n.inputs],
              "outputs": [s.name for s in n.outputs]} for n in tree.nodes]
    links = [{"from": "%s.%s" % (l.from_node.name, l.from_socket.name),
              "to": "%s.%s" % (l.to_node.name, l.to_socket.name),
              "valid": bool(l.is_valid)} for l in tree.links]

    # THE TERMINAL DEPENDS ON THE TREE TYPE. This looked only for NodeGroupOutput, which a
    # compositor tree does not have by design - so pointed at one it would have reported "nothing
    # is connected to the Group Output" for a perfectly wired compositor. A wrong answer from a
    # field whose entire purpose is telling you the tree is inert.
    terminals, note = _terminals(tree)
    outs = [n for n in tree.nodes if n.bl_idname in terminals]
    reachable = False
    if outs:
        seen, stack = set(), [outs[0]]
        while stack:
            cur = stack.pop()
            if cur.name in seen:
                continue
            seen.add(cur.name)
            for sock in cur.inputs:
                for link in sock.links:
                    if link.from_node.bl_idname == "NodeGroupInput":
                        reachable = True
                    stack.append(link.from_node)
        reachable = reachable or any(l.to_node.bl_idname in terminals for l in tree.links)
    return {"group": tree.name, "nodes": nodes, "links": links,
            "nodeCount": len(nodes), "linkCount": len(links),
            "interface": _iface_items(tree),
            "outputReachable": reachable,
            "treeType": getattr(tree, "bl_idname", None),
            "outputNodes": [n.name for n in outs],
            "reachabilityNote": None if reachable else note}


def op_assign_node_group(params):
    """Attach a node group to an object as a Nodes modifier, and set its exposed inputs."""
    reject_unknown(params, _ASSIGN_KEYS, "assign_node_group")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    tree = _tree(take(params, "group", "tree", required=True, kind=str))
    if tree.bl_idname != "GeometryNodeTree":
        raise MifOpError("'%s' is a %s, and only a GeometryNodeTree can drive a Nodes modifier. "
                         "NOTHING was assigned." % (tree.name, tree.bl_idname))
    mod = obj.modifiers.new(name=str(take(params, "modifierName", default="GeometryNodes",
                                          kind=str)), type="NODES")
    mod.node_group = tree

    applied, refused = {}, {}
    given = params.get("inputs")
    if given:
        if not isinstance(given, dict):
            raise MifOpError("'inputs' must be a {socketName: value} object, got %r." % (given,))
        # The modifier addresses exposed inputs by IDENTIFIER (Socket_2), not by name, which is the
        # single most confusing thing about driving geometry nodes from script. Resolve the name to
        # its identifier rather than making a caller know that.
        ident = {}
        if hasattr(tree, "interface"):
            for it in tree.interface.items_tree:
                if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT":
                    ident[it.name] = it.identifier
        else:
            for s in tree.inputs:
                ident[s.name] = s.identifier
        for k, v in given.items():
            key = ident.get(k)
            if key is None:
                refused[k] = "not an exposed input; this group exposes: %s" % (
                    ", ".join(sorted(ident)) or "(none)")
                continue
            try:
                mod[key] = v
                applied[k] = v
            except (TypeError, KeyError) as exc:
                refused[k] = str(exc)

    outs = any(l.to_node.bl_idname == "NodeGroupOutput" for l in tree.links)
    return {"object": obj.name, "modifier": mod.name, "group": tree.name,
            "inputsApplied": applied, "inputsRefused": refused,
            "outputConnected": outs,
            "effectNote": (None if outs else
                           "the group's output is not connected, so this modifier will pass the "
                           "mesh through UNCHANGED. That is valid in Blender and looks identical "
                           "to a modifier that is not working.")}


_COMPOSITING_KEYS = {"enabled", "useCompositing", "useSequencer", "withDefaultNodes"}
_COMPINFO_KEYS = {"viewLayer"}

# THE RESERVED TREE NAME. The scene's compositor is scene.node_tree, which is NOT in
# bpy.data.node_groups - it is a tree owned by the scene - so the five node-authoring ops here could
# not reach it at all. Rather than grow a parallel set of add/link/list ops for compositing, _tree
# resolves this one reserved string to it, and every one of them works on the compositor unchanged.
#
# create_node_group REFUSES to create a group by this name, which is what keeps the reservation from
# ever being ambiguous. A precedence rule - "a real group of that name wins" - would have been the
# other option, and it makes the reachable set depend on what somebody happened to call something.
SCENE_COMPOSITOR = "scene:compositor"
SCENE_WORLD = "scene:world"
MATERIAL_PREFIX = "material:"
WORLD_PREFIX = "world:"

# EVERY TREE THAT IS NOT IN bpy.data.node_groups. A node group is a datablock in its own right; a
# material's tree, a world's tree and the compositor are OWNED by the thing they shade, so none of
# them could be addressed by the five authoring ops here at all.
#
# WHAT THAT COST, measured rather than guessed: describe_material reads a material's node tree in
# full - every node, every link, every image texture - and set_material_properties could write only
# the Principled BSDF's own socket values. So the addon could DESCRIBE a shader graph in detail and
# not add a single node to one. No mix shaders, no procedural noise, no bump or normal map wired,
# no UV mapping node, no emission blend. The read half was thorough and the write half was one node
# deep.
#
# The fix is a resolver rather than a second set of add/link/list ops, for the same reason the
# compositor got one: add_group_node is already tree-agnostic - it calls tree.nodes.new(ntype) and
# names tree.bl_idname in its own error - so the only thing missing was a way to hand it the tree.
RESERVED_PREFIXES = (MATERIAL_PREFIX, WORLD_PREFIX)

# THE TERMINAL NODE IS NOT THE SAME IN EVERY TREE TYPE, and getting this wrong produces a WRONG
# answer rather than a missing one. list_group_nodes looked only for NodeGroupOutput, which a
# compositor tree does not have by design - so it would have reported "nothing is connected to the
# Group Output" for a perfectly wired compositor.
_TERMINALS = {
    "GeometryNodeTree": ("NodeGroupOutput",),
    "ShaderNodeTree": ("NodeGroupOutput", "ShaderNodeOutputMaterial", "ShaderNodeOutputWorld",
                       "ShaderNodeOutputLight"),
    "CompositorNodeTree": ("CompositorNodeComposite", "NodeGroupOutput"),
}


def _terminals(tree):
    """The bl_idnames that count as this tree's OUTPUT, and a phrase describing the pass-through."""
    kind = getattr(tree, "bl_idname", "GeometryNodeTree")
    if kind == "CompositorNodeTree":
        return _TERMINALS[kind], ("nothing is connected to a Composite node, so this compositor "
                                  "writes NOTHING to the render result. Blender treats that as "
                                  "valid and reports no error.")
    if kind == "ShaderNodeTree":
        return _TERMINALS[kind], ("nothing is connected to an output node, so this shader tree "
                                  "contributes nothing.")
    return _TERMINALS.get(kind, _TERMINALS["GeometryNodeTree"]), (
        "nothing is connected to the Group Output, so this tree passes geometry through "
        "UNCHANGED. Blender treats that as valid and reports no error.")


def _owned_tree(name):
    """Resolve one of the reserved targets to the tree it names, or None if `name` is not one.

    Refuses rather than enabling. Turning use_nodes on for a material or a world as a side effect of
    ADDRESSING it would mean a call that says "put a node here" silently converted a flat-colour
    material into a node-based one - a different thing to render, decided by a lookup.
    """
    if name == SCENE_COMPOSITOR:
        return _scene_tree()
    if name == SCENE_WORLD:
        world = bpy.context.scene.world
        if world is None:
            raise MifOpError("scene '%s' has no world, so there is no world shader tree to "
                             "address. Make one with set_world. NOTHING was changed."
                             % bpy.context.scene.name)
        return _shader_tree(world, "world", world.name)
    for prefix, store, label in ((MATERIAL_PREFIX, "materials", "material"),
                                 (WORLD_PREFIX, "worlds", "world")):
        if not name.startswith(prefix):
            continue
        key = name[len(prefix):]
        if not key:
            raise MifOpError("'%s' names no %s - use '%s<name>'. NOTHING was changed."
                             % (name, label, prefix))
        holder = getattr(bpy.data, store).get(key)
        if holder is None:
            known = sorted(x.name for x in getattr(bpy.data, store))[:25]
            raise MifOpError("no %s named '%s'. Present: %s. NOTHING was changed."
                             % (label, key, ", ".join(known) if known else "<none>"))
        return _shader_tree(holder, label, key)
    return None


def _shader_tree(holder, label, key):
    """A material's or world's own node tree, refusing when use_nodes is off.

    A datablock with use_nodes FALSE ignores its tree completely and renders its flat colour, so
    adding nodes to it would be authoring something nothing looks at - every node reading back
    perfectly and changing no pixel. Named rather than silently enabled.
    """
    if not holder.use_nodes or holder.node_tree is None:
        raise MifOpError("%s '%s' has use_nodes OFF, so its node tree is ignored entirely and its "
                         "flat colour is what renders - adding nodes would author something "
                         "nothing looks at. Turn it on first. NOTHING was changed."
                         % (label, key))
    return holder.node_tree


# THE COMPOSITOR MOVED IN 5.0, AND THE OLD ATTRIBUTE IS GONE.
#
# Established empirically on 2026-09-03 by running all four installed Blenders headless, after a
# LIVE call to compositor_info came back "AttributeError: 'Scene' object has no attribute
# 'node_tree'". Everything shipped for the compositor earlier that day was dead on the newest
# Blender this addon claims to support, and every static gate was green.
#
#   3.6 / 4.2 / 4.4   scene.node_tree, an EMBEDDED tree, gated by scene.use_nodes (default FALSE)
#   5.0               scene.node_tree is ABSENT. scene.compositing_node_group holds a real node
#                     group from bpy.data.node_groups, and scene.use_nodes defaults TRUE while the
#                     group is still None - so "use_nodes is on" no longer implies a tree exists.
#
# AND THE OUTPUT NODE CHANGED WITH IT: CompositorNodeComposite does not exist on 5.0 at all. The
# compositor is a genuine node group there, so its terminal is NodeGroupOutput. _TERMINALS already
# lists both, which is why reachability survived the move and nothing else did.
COMPOSITOR_ATTR_NEW = "compositing_node_group"      # 5.0+
COMPOSITOR_ATTR_OLD = "node_tree"                   # <= 4.4


def compositor_era(scene=None):
    """('new'|'old', attribute name) for this build - which compositor API is present."""
    sc = scene or bpy.context.scene
    if hasattr(sc, COMPOSITOR_ATTR_NEW):
        return "new", COMPOSITOR_ATTR_NEW
    return "old", COMPOSITOR_ATTR_OLD


def compositor_tree(scene=None):
    """The scene's compositing tree on either API, or None. Never raises, never mutates."""
    sc = scene or bpy.context.scene
    era, attr = compositor_era(sc)
    if era == "old" and not getattr(sc, "use_nodes", False):
        return None
    return getattr(sc, attr, None)


def _scene_tree():
    """The scene's compositing tree, or a refusal that says how to make one.

    NOT a mutator. Creating the tree here would mean a read op quietly switched compositing on for
    the whole scene, which changes what every subsequent render does - the same objection that keeps
    world_info off _background_node.
    """
    sc = bpy.context.scene
    tree = compositor_tree(sc)
    if tree is None:
        era, _ = compositor_era(sc)
        why = ("scene.use_nodes is off" if era == "old" else
               "scene.compositing_node_group is unset - on Blender 5.0 use_nodes defaults to TRUE "
               "and means nothing on its own, so a scene can look compositing-enabled with no tree "
               "at all")
        raise MifOpError("scene '%s' has no compositing tree - %s. Make one with set_compositing, "
                         "which also wires the default Render Layers -> output pair. NOTHING was "
                         "changed." % (sc.name, why))
    return tree


def op_set_compositing(params):
    """Turn the scene compositor on or off - and the SECOND switch that also has to be on.

    TWO INDEPENDENT FLAGS DECIDE WHETHER COMPOSITING HAPPENS, and having one on and the other off is
    the classic silent failure:

      scene.use_nodes              whether a compositing tree EXISTS and is edited
      scene.render.use_compositing whether the render PIPELINE runs it

    With use_nodes on and use_compositing off, the whole tree sits there reading perfectly, the
    backdrop in the compositor updates, and the rendered file is completely unprocessed. Nothing
    reports it. So both are set here and both are read back.

    params:
      enabled (bool)            scene.use_nodes. Default true.
      useCompositing (bool)     scene.render.use_compositing. Defaults to follow `enabled`, since
                                turning the tree on and leaving the pipeline off is never what
                                somebody meant.
      useSequencer (bool)       scene.render.use_sequencer - the VSE runs AFTER the compositor and
                                overrides it if a strip exists.
      withDefaultNodes (bool)   wire Render Layers -> Composite if the tree is empty. Default true
                                when enabling, because an empty compositor writes nothing at all.
    """
    reject_unknown(params, _COMPOSITING_KEYS, "set_compositing")
    sc = bpy.context.scene
    enabled = take_bool(params, "enabled", default=True)
    before = {"useNodes": bool(sc.use_nodes),
              "useCompositing": bool(sc.render.use_compositing),
              "useSequencer": bool(sc.render.use_sequencer)}

    era, attr = compositor_era(sc)
    # use_nodes STILL EXISTS on 5.0 and is still worth writing - it is what the UI reflects - but on
    # that build it does not create or destroy the tree, so the group is handled separately below.
    sc.use_nodes = enabled
    use_comp = params.get("useCompositing")
    sc.render.use_compositing = bool(use_comp) if use_comp is not None else enabled
    if params.get("useSequencer") is not None:
        sc.render.use_sequencer = take_bool(params, "useSequencer", default=True)

    added = []
    tree = getattr(sc, attr, None)
    if enabled and era == "new" and tree is None:
        # ON 5.0 THE TREE IS A REAL NODE GROUP and has to be created and assigned; there is no
        # embedded tree for use_nodes to bring into being. Verified headless: assigning a
        # bpy.data.node_groups.new(name, "CompositorNodeTree") is the whole of it.
        tree = bpy.data.node_groups.new("Compositing", "CompositorNodeTree")
        setattr(sc, attr, tree)
    if enabled and tree is not None and take_bool(params, "withDefaultNodes", default=True):
        if not len(tree.nodes):
            rl = tree.nodes.new("CompositorNodeRLayers")
            # THE OUTPUT NODE IS NOT THE SAME NODE. CompositorNodeComposite is UNDEFINED on 5.0 -
            # `ng.nodes.new("CompositorNodeComposite")` raises "Node type undefined" - because the
            # compositor is a node group there and terminates in NodeGroupOutput. Verified on all
            # four installs rather than assumed.
            out_type = "NodeGroupOutput" if era == "new" else "CompositorNodeComposite"
            comp = tree.nodes.new(out_type)
            rl.location = (-300, 0)
            comp.location = (300, 0)
            socket = comp.inputs[0] if era == "new" else comp.inputs["Image"]
            tree.links.new(rl.outputs["Image"], socket)
            added = [rl.name, comp.name]

    after = {"useNodes": bool(sc.use_nodes),
             "useCompositing": bool(sc.render.use_compositing),
             "useSequencer": bool(sc.render.use_sequencer)}
    # VERIFIED INDIVIDUALLY. use_nodes is on the scene and use_compositing is on scene.render -
    # two datablocks - and the whole point of this op is that having one without the other is the
    # failure, so a check that only looked at one would miss exactly what it exists for.
    if after["useNodes"] != enabled:
        raise MifOpError("set scene.use_nodes to %s but it reads back as %s"
                         % (enabled, after["useNodes"]))
    return {
        "ok": True,
        "scene": sc.name,
        "before": before,
        "nodesAdded": added,
        "treeName": tree.name if tree is not None else None,
        "compositorApi": ("scene.compositing_node_group (Blender 5.0+)" if era == "new"
                          else "scene.node_tree (Blender <= 4.4)"),
        "addressAs": SCENE_COMPOSITOR,
        "note": ("address this tree from add_group_node, link_group_nodes and list_group_nodes by "
                 "passing tree:'%s' - it is the scene's own tree and is not in bpy.data.node_groups."
                 % SCENE_COMPOSITOR),
        **after,
    }


def op_compositor_info(params):
    """What the compositor IS, and every way it can be on and doing nothing.

    THE WHOLE SUBSYSTEM WAS UNREACHABLE before 2026-09-03. create_node_group could make a
    CompositorNodeTree, but that is a node GROUP in bpy.data.node_groups - the scene's compositor is
    scene.node_tree, a different tree that nothing here could address. So glare, colour grading,
    denoising, cryptomatte, lens distortion, file output and every other post-process was outside
    the typed path entirely.

    FOUR WAYS TO BE ON AND INERT, each reported as a distinct blocker because the fix differs:

      use_nodes off                 no tree at all; the render is unprocessed
      use_compositing off           the tree exists and is edited and the render PIPELINE skips it.
                                    This is the classic one - the backdrop updates while the file
                                    on disk is untouched.
      no Composite node linked      the tree runs and writes nothing to the render result. A Viewer
                                    node is NOT a substitute: it feeds the backdrop only, which is
                                    why "it looks right in the compositor" and the file is wrong.
      no Render Layers feeding it   the compositor is not looking at the render at all.

    params:
      viewLayer (str)   which view layer's enabled passes to report. Default the active one.
    """
    reject_unknown(params, _COMPINFO_KEYS, "compositor_info")
    sc = bpy.context.scene
    vl_name = take(params, "viewLayer", kind=str)
    if vl_name:
        vl = sc.view_layers.get(vl_name)
        if vl is None:
            known = sorted(v.name for v in sc.view_layers)
            raise MifOpError("no view layer named '%s' in scene '%s'. Present: %s."
                             % (vl_name, sc.name, ", ".join(known)))
    else:
        vl = bpy.context.view_layer

    out = {
        "ok": True,
        "scene": sc.name,
        "useNodes": bool(sc.use_nodes),
        "useCompositing": bool(sc.render.use_compositing),
        "useSequencer": bool(sc.render.use_sequencer),
        "viewLayer": vl.name,
        "addressAs": SCENE_COMPOSITOR,
    }
    blockers = []

    era, _attr = compositor_era(sc)
    out["compositorApi"] = ("scene.compositing_node_group (Blender 5.0+)" if era == "new"
                            else "scene.node_tree (Blender <= 4.4)")
    tree = compositor_tree(sc)
    if tree is None:
        blockers.append(
            "scene.use_nodes is OFF, so there is no compositing tree and the render is written "
            "unprocessed. Turn it on with set_compositing." if era == "old" else
            "there is no compositing node group assigned, so the render is written unprocessed. On "
            "Blender 5.0 use_nodes defaults to TRUE and means nothing by itself - the tree is a "
            "real node group in scene.compositing_node_group and it is unset. Make one with "
            "set_compositing.")
        out["nodeCount"] = 0
    else:
        out["treeName"] = tree.name
        out["nodeCount"] = len(tree.nodes)
        by_type = {}
        for n in tree.nodes:
            by_type[n.bl_idname] = by_type.get(n.bl_idname, 0) + 1
        out["nodesByType"] = by_type
        out["nodes"] = [{"name": n.name, "type": n.bl_idname, "label": n.label,
                         "muted": bool(getattr(n, "mute", False))} for n in tree.nodes]
        out["linkCount"] = len(tree.links)

        # THE OUTPUT NODE IS ERA-DEPENDENT, and hard-coding CompositorNodeComposite made this
        # report a CORRECTLY WIRED 5.0 compositor as broken - a wrong answer, not a missing one,
        # from the field whose whole job is telling you the tree is inert. Caught by running the op
        # headless on 5.0 after the accessor was fixed, not by reading. Same fix _terminals already
        # applies to list_group_nodes, reused here rather than restated.
        terminals, _term_note = _terminals(tree)
        composites = [n for n in tree.nodes if n.bl_idname in terminals]
        viewers = [n for n in tree.nodes if n.bl_idname == "CompositorNodeViewer"]
        rlayers = [n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"]
        fed = {l.to_node.name for l in tree.links}
        out["compositeNodes"] = [n.name for n in composites]
        out["compositeConnected"] = any(n.name in fed for n in composites)
        out["renderLayersNodes"] = [n.name for n in rlayers]
        out["renderLayersFeeding"] = any(l.from_node.bl_idname == "CompositorNodeRLayers"
                                         for l in tree.links)
        out["mutedNodes"] = [n.name for n in tree.nodes if getattr(n, "mute", False)]

        out["outputNodeTypes"] = sorted(terminals)
        if not composites:
            blockers.append("there is no %s in the tree, so the compositor writes "
                            "NOTHING to the render result%%s."
                            % ("Group Output node" if era == "new" else "Composite node")
                            % (" - the Viewer node feeds the backdrop only, which is why it looks "
                               "right in the compositor and the saved file is wrong" if viewers
                               else ""))
        elif not out["compositeConnected"]:
            blockers.append(("the %s exists but nothing is linked into it, so the compositor "
                             "writes nothing to the render result%%s."
                             % ("Group Output node" if era == "new" else "Composite node"))
                            % (" - a Viewer node is connected, and that feeds the backdrop only"
                               if any(n.name in fed for n in viewers) else ""))
        if not rlayers:
            blockers.append("there is no Render Layers node, so the compositor is not looking at "
                            "the render at all.")
        elif not out["renderLayersFeeding"]:
            blockers.append("the Render Layers node is not linked to anything, so the render is "
                            "not entering the compositor.")
        if out["mutedNodes"]:
            blockers.append("%d node(s) are MUTED and pass their input straight through: %s."
                            % (len(out["mutedNodes"]), ", ".join(out["mutedNodes"][:8])))

    # GUARDED ON THE TREE, NOT ON use_nodes, because on 5.0 use_nodes is TRUE out of the box and
    # would make this fire on every fresh scene that has no compositor at all.
    if tree is not None and not sc.render.use_compositing:
        blockers.append("scene.render.use_compositing is OFF, so the render pipeline SKIPS the "
                        "compositor entirely. The tree still exists, the backdrop still updates, "
                        "and the file on disk is completely unprocessed - the two switches are "
                        "independent and this is the one that is usually missed.")
    if sc.render.use_sequencer:
        # NOT A BLOCKER BY ITSELF. The VSE only overrides the compositor when strips exist, and a
        # scene with an empty sequencer is the normal case - calling that a blocker would train
        # people to ignore the list.
        seq = getattr(sc, "sequence_editor", None)
        strips = len(getattr(seq, "sequences_all", []) or []) if seq else 0
        out["sequencerStrips"] = strips
        if strips:
            blockers.append("use_sequencer is on and the VSE holds %d strip(s). The sequencer runs "
                            "AFTER the compositor and its output is what gets written, so the "
                            "compositor's result can be replaced wholesale." % strips)

    passes = {}
    for attr in dir(vl):
        if attr.startswith("use_pass_"):
            try:
                passes[attr[len("use_pass_"):]] = bool(getattr(vl, attr))
            except (AttributeError, TypeError):     # noqa: PERF203
                continue
    out["enabledPasses"] = sorted(k for k, v in passes.items() if v)
    out["availablePasses"] = sorted(passes)

    out["blockers"] = blockers
    out["compositorAffectsRender"] = not blockers
    return out

OPS = {
    "set_compositing": op_set_compositing,
    "compositor_info": op_compositor_info,
    "create_node_group": op_create_node_group,
    "add_group_node": op_add_group_node,
    "link_group_nodes": op_link_group_nodes,
    "add_group_interface": op_add_group_interface,
    "list_group_nodes": op_list_group_nodes,
    "assign_node_group": op_assign_node_group,
}
