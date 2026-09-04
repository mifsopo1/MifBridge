"""View layers and render passes - what the compositor is given to work with.

WHY THIS FOLLOWS THE COMPOSITOR. compositor_info reports which passes are enabled and nothing could
turn one on, which is the same read/write asymmetry that turned up world and physics - just facing
the other way. It matters more here than a missing setter usually does, because a Render Layers node
only offers sockets for passes the view layer ACTUALLY OUTPUTS: ask for a Z-depth composite with the
Z pass off and there is no socket to connect, or in some versions a socket that outputs black. The
compositing ops shipped without the one thing that decides what they can see.

=============================================================================
A VIEW LAYER WITH use OFF IS THE INERT SHAPE AGAIN
=============================================================================
`view_layer.use` decides whether the layer is rendered at all. With it off, every pass, every
collection assignment, every override on that layer reads back perfectly and no pixel of it is ever
produced. Nothing warns. So it is reported as `renders` and called out as a blocker rather than
being left as one boolean among many.

=============================================================================
THE PASS SET IS NOT FIXED, SO IT IS NOT HARD-CODED
=============================================================================
Which use_pass_* properties exist depends on the Blender version and the render engine - Cycles
offers passes EEVEE does not, cryptomatte moved, and new ones arrive. So the accepted names are
enumerated from the view layer in front of us, the same reasoning as set_color_management reading
its enums from the instance rather than from bpy.types. A remembered list goes wrong quietly, by
refusing something that works.
"""
import bpy

from .ops_common import MifOpError, reject_unknown, take, take_bool, take_int

# NO "scene" KEY. It was declared here reflexively and never read - every op in this module works
# on bpy.context.scene, and an accepted parameter that is silently ignored is worse than an absent
# one. Second time this session: render_animation carried the same dead key and param_reach caught
# both. If cross-scene addressing is wanted it is a real piece of work, not a key.
_LIST_KEYS = {"withPasses"}
_SET_KEYS = {"name", "viewLayer", "use", "enablePasses", "disablePasses", "passes", "samples"}
_CREATE_KEYS = {"name", "copyFrom", "use"}
_DELETE_KEYS = {"name", "viewLayer"}

_PREFIX = "use_pass_"


def _pass_names(vl):
    """Every pass this view layer actually offers, without the use_pass_ prefix.

    ENUMERATED, NOT LISTED. The set differs by Blender version and by render engine, so a constant
    in this file would refuse passes that exist and accept ones that do not - and the failure would
    be a refusal of something legal, which reads like a bug in Blender rather than in here.
    """
    out = {}
    for attr in dir(vl):
        if not attr.startswith(_PREFIX):
            continue
        try:
            out[attr[len(_PREFIX):]] = bool(getattr(vl, attr))
        except (AttributeError, TypeError):
            continue
    return out


def _find_layer(scene, name):
    vl = scene.view_layers.get(name)
    if vl is None:
        known = sorted(v.name for v in scene.view_layers)
        raise MifOpError("no view layer named '%s' in scene '%s'. Present: %s. NOTHING was changed."
                         % (name, scene.name, ", ".join(known)))
    return vl


def _layer_row(vl, scene, with_passes):
    passes = _pass_names(vl)
    row = {
        "name": vl.name,
        # THE ONE THAT DECIDES WHETHER ANY OF THE REST HAPPENS.
        "renders": bool(getattr(vl, "use", True)),
        "isActive": bpy.context.view_layer.name == vl.name,
        "enabledPasses": sorted(k for k, v in passes.items() if v),
        "passCount": len(passes),
        "samples": getattr(vl, "samples", None),
    }
    if with_passes:
        row["passes"] = passes
    return row


def op_list_view_layers(params):
    """Every view layer, what it outputs, and whether it renders at all.

    params:
      withPasses (bool)   include the full on/off map per layer, not just the enabled names.
    """
    reject_unknown(params, _LIST_KEYS, "list_view_layers")
    sc = bpy.context.scene
    with_passes = take_bool(params, "withPasses", default=False)
    rows = [_layer_row(vl, sc, with_passes) for vl in sc.view_layers]
    dark = [r["name"] for r in rows if not r["renders"]]
    return {
        "ok": True,
        "scene": sc.name,
        "activeViewLayer": bpy.context.view_layer.name,
        "count": len(rows),
        "viewLayers": rows,
        "notRendering": dark,
        "note": ("%d view layer(s) have use OFF: every pass and collection assignment on them reads "
                 "back correctly and no pixel of them is ever produced." % len(dark)) if dark else None,
    }


def op_set_view_layer(params):
    """Turn render passes on or off, and decide whether the layer renders at all.

    A Render Layers node only offers sockets for passes the layer OUTPUTS, so this is what decides
    what the compositor can see. Asking for a Z-depth composite with the Z pass off leaves nothing
    to connect.

    PASS NAMES ARE VALIDATED AGAINST THIS LAYER, not a list in this file - the set depends on the
    Blender version and the render engine, and a hard-coded one would refuse passes that exist.
    Names are given without the use_pass_ prefix: "z", "normal", "mist", "cryptomatte_object".

    params:
      name / viewLayer (str)      which layer. Default the active one.
      use (bool)                  whether this layer renders AT ALL.
      enablePasses (list[str])    passes to turn on
      disablePasses (list[str])   passes to turn off
      passes (dict)               {"z": true, "mist": false} - an alternative to the two lists
      samples (int)               per-layer sample override, 0 to follow the scene
    """
    reject_unknown(params, _SET_KEYS, "set_view_layer")
    sc = bpy.context.scene
    name = take(params, "name", "viewLayer", kind=str)
    vl = _find_layer(sc, name) if name else bpy.context.view_layer

    available = _pass_names(vl)
    wanted = {}
    for key, value in (("enablePasses", True), ("disablePasses", False)):
        raw = params.get(key)
        if raw is None:
            continue
        if not isinstance(raw, (list, tuple)):
            raise MifOpError("'%s' must be a list of pass names, got %s. NOTHING was changed."
                             % (key, type(raw).__name__))
        for p in raw:
            wanted[str(p)] = value
    table = params.get("passes")
    if table is not None:
        if not isinstance(table, dict):
            raise MifOpError("'passes' must be a {name: bool} map, got %s. NOTHING was changed."
                             % type(table).__name__)
        for p, v in table.items():
            wanted[str(p)] = bool(v)

    # VALIDATED BEFORE ANYTHING IS WRITTEN, all of them, so a typo in the fourth name does not leave
    # three passes changed and the call reported as a failure.
    unknown = [p for p in wanted if p not in available]
    if unknown:
        raise MifOpError("this view layer has no pass named %s. Available on this Blender and "
                         "engine: %s. Names are given without the use_pass_ prefix. NOTHING was "
                         "changed." % (", ".join(sorted(unknown)[:6]), ", ".join(sorted(available))))

    use = params.get("use")
    samples = take_int(params, "samples", default=None)
    if not wanted and use is None and samples is None:
        raise MifOpError("nothing to do - pass enablePasses, disablePasses, passes, use or "
                         "samples. NOTHING was changed.")

    before = {"renders": bool(getattr(vl, "use", True)),
              "enabledPasses": sorted(k for k, v in available.items() if v)}

    for p, v in wanted.items():
        setattr(vl, _PREFIX + p, v)
    if use is not None:
        vl.use = take_bool(params, "use", default=True)
    if samples is not None:
        if not hasattr(vl, "samples"):
            raise MifOpError("this view layer has no per-layer sample override on this Blender. "
                             "Everything else requested was applied; samples was not.")
        vl.samples = samples

    after = _pass_names(vl)
    # READ BACK EVERY REQUESTED PASS INDIVIDUALLY. Several use_pass_* properties are read-only under
    # a given engine - the property exists, the write is accepted, and the value does not move - so
    # a response built from what was asked for would report a pass that is still off.
    wrong = {p: (v, after.get(p)) for p, v in wanted.items() if after.get(p) != v}
    if wrong:
        raise MifOpError("wrote %s but the layer reads back %s afterwards - some passes are "
                         "read-only under the current render engine (%s), and the write is "
                         "accepted without taking effect."
                         % ({k: v[0] for k, v in wrong.items()},
                            {k: v[1] for k, v in wrong.items()}, sc.render.engine))

    row = _layer_row(vl, sc, False)
    blockers = []
    if not row["renders"]:
        blockers.append("view layer '%s' has use OFF, so it is not rendered at all - every pass "
                        "and collection assignment on it reads back correctly and no pixel of it "
                        "is ever produced." % vl.name)
    return {
        "ok": True,
        "scene": sc.name,
        "before": before,
        "passesChanged": sorted(wanted),
        "blockers": blockers,
        **row,
    }


def op_create_view_layer(params):
    """Add a view layer - a second pass over the same scene with its own collections and outputs.

    params:
      name (str)        required
      copyFrom (str)    copy the enabled passes from this layer. Blender's new layers start from
                        defaults, which is rarely what somebody splitting a shot wants.
      use (bool)        whether the new layer renders. Default true.
    """
    reject_unknown(params, _CREATE_KEYS, "create_view_layer")
    sc = bpy.context.scene
    name = take(params, "name", required=True, kind=str)
    if sc.view_layers.get(name) is not None:
        raise MifOpError("scene '%s' already has a view layer named '%s'. NOTHING was created."
                         % (sc.name, name))
    src = _find_layer(sc, take(params, "copyFrom", kind=str)) if params.get("copyFrom") else None
    src_passes = _pass_names(src) if src is not None else None

    vl = sc.view_layers.new(name)
    if src_passes is not None:
        for p, v in src_passes.items():
            try:
                setattr(vl, _PREFIX + p, v)
            except (AttributeError, TypeError):     # noqa: PERF203
                continue                            # read-only under this engine; not fatal
    if params.get("use") is not None:
        vl.use = take_bool(params, "use", default=True)

    made = sc.view_layers.get(name)
    if made is None:
        raise MifOpError("created view layer '%s' and it is not in scene.view_layers afterwards."
                         % name)
    return {"ok": True, "scene": sc.name, "copiedFrom": src.name if src else None,
            **_layer_row(made, sc, False)}


def op_delete_view_layer(params):
    """Remove a view layer, refusing to remove the last one.

    A scene with no view layer cannot render at all, and Blender does not stop you from getting
    there through the API.

    params:
      name / viewLayer (str)   required
    """
    reject_unknown(params, _DELETE_KEYS, "delete_view_layer")
    sc = bpy.context.scene
    name = take(params, "name", "viewLayer", required=True, kind=str)
    vl = _find_layer(sc, name)
    if len(sc.view_layers) <= 1:
        raise MifOpError("'%s' is the only view layer in scene '%s', and a scene with none cannot "
                         "be rendered at all. NOTHING was changed." % (name, sc.name))
    sc.view_layers.remove(vl)
    if sc.view_layers.get(name) is not None:
        raise MifOpError("removed view layer '%s' and it is still in scene.view_layers." % name)
    return {"ok": True, "scene": sc.name, "deleted": name,
            "remaining": sorted(v.name for v in sc.view_layers),
            "activeViewLayer": bpy.context.view_layer.name}


OPS = {
    "list_view_layers": op_list_view_layers,
    "set_view_layer": op_set_view_layer,
    "create_view_layer": op_create_view_layer,
    "delete_view_layer": op_delete_view_layer,
}
