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

    outs = [n for n in tree.nodes if n.bl_idname == "NodeGroupOutput"]
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
        reachable = reachable or any(l.to_node.bl_idname == "NodeGroupOutput" for l in tree.links)
    return {"group": tree.name, "nodes": nodes, "links": links,
            "nodeCount": len(nodes), "linkCount": len(links),
            "interface": _iface_items(tree),
            "outputReachable": reachable,
            "reachabilityNote": (None if reachable else
                                 "nothing is connected to the Group Output, so this tree passes "
                                 "geometry through UNCHANGED. Blender treats that as valid and "
                                 "reports no error.")}


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


OPS = {
    "create_node_group": op_create_node_group,
    "add_group_node": op_add_group_node,
    "link_group_nodes": op_link_group_nodes,
    "add_group_interface": op_add_group_interface,
    "list_group_nodes": op_list_group_nodes,
    "assign_node_group": op_assign_node_group,
}
