"""Shared helpers for MifBlender ops.

Everything in here runs on the MAIN thread (ops are only ever called from
server._execute, which is only ever called from the drain timer or inline in
background mode). bpy calls are therefore legal below this line.
"""

from __future__ import annotations

import math

import bpy

# 1 Unreal unit == 1 cm, and Blender's FBX pipeline maps 1 Blender unit to 1 m
# in a cm-declared file. So a 1000 uu road is 10.0 Blender units.
# VERIFIED empirically on Blender 4.4.0: exporting with apply_unit_scale=True /
# apply_scale_options='FBX_SCALE_NONE' writes UnitScaleFactor=1.0 with the
# geometry pre-multiplied by 100, i.e. centimetre magnitudes in a centimetre
# file. See ops_mesh.FBX_EXPORT_ARGS.
UU_PER_BU = 100.0


class MifOpError(Exception):
    """A deliberate, actionable refusal. The message must name the fix.

    Raise this (not a bare Exception) whenever the caller can do something about
    it. server._execute turns it into {"ok": false, "error": ...} WITHOUT a
    traceback, because the message is the whole diagnosis.
    """


# ---------------------------------------------------------------------------
# JSON safety
# ---------------------------------------------------------------------------

def jsonable(value, _depth=0):
    """Coerce bpy/mathutils values into something json.dumps will accept.

    A response that cannot be serialised is a silent hang from the caller's
    point of view, so this is defensive on purpose.
    """
    if _depth > 8:
        return repr(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v, _depth + 1) for v in value]
    # mathutils Vector / Euler / Matrix / Color are all iterable
    try:
        return [jsonable(v, _depth + 1) for v in value]
    except TypeError:
        pass
    for attr in ("name_full", "name"):
        if hasattr(value, attr):
            return getattr(value, attr)
    return repr(value)


def rnd(seq, places=6):
    return [round(float(v), places) for v in seq]


# ---------------------------------------------------------------------------
# Param plumbing
# ---------------------------------------------------------------------------

def take(params, *names, default=None, required=False, kind=None):
    """Read the first present key out of `names`. Aliases are first-class here
    because MifBridge's UE side accepts them too, and a caller should not have
    to remember which side it is talking to."""
    for name in names:
        if name in params and params[name] is not None:
            value = params[name]
            if kind is not None and not isinstance(value, kind):
                want = getattr(kind, "__name__", str(kind))
                raise MifOpError("'%s' must be %s, got %s"
                                 % (name, want, type(value).__name__))
            return value
    if required:
        raise MifOpError("'%s' is required" % names[0]
                         + (" (aliases: %s)" % ", ".join(names[1:]) if len(names) > 1 else ""))
    return default


def take_bool(params, *names, default=False):
    value = take(params, *names, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    raise MifOpError("'%s' must be a boolean" % names[0])


def take_float(params, *names, default=None, required=False):
    value = take(params, *names, default=default, required=required)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise MifOpError("'%s' must be a number, got %r" % (names[0], value))


def take_int(params, *names, default=None, required=False):
    value = take(params, *names, default=default, required=required)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise MifOpError("'%s' must be an integer, got %r" % (names[0], value))


def reject_unknown(params, accepted, endpoint):
    """Fail loudly on a key we do not understand.

    Mirrors the UE side's RejectUnknownParams. Silently ignoring a misspelled
    param is how you get 'the op ran but did nothing' bug reports.
    """
    unknown = [k for k in params if k not in accepted]
    if unknown:
        raise MifOpError(
            "%s: unknown param(s) %s. Accepted: %s"
            % (endpoint, ", ".join(sorted(unknown)), ", ".join(sorted(accepted))))


AXES = {"X": 0, "Y": 1, "Z": 2}


def axis_index(name, param="axis"):
    if not isinstance(name, str) or name.strip().upper() not in AXES:
        raise MifOpError("'%s' must be one of X, Y, Z (got %r)" % (param, name))
    return AXES[name.strip().upper()]


# ---------------------------------------------------------------------------
# Object lookup / info
# ---------------------------------------------------------------------------

def get_object(name, want_mesh=False):
    if not isinstance(name, str) or not name:
        raise MifOpError("'object' is required (an object name; list them with list_objects)")
    obj = bpy.data.objects.get(name)
    if obj is None:
        known = [o.name for o in bpy.data.objects][:25]
        raise MifOpError("no object named '%s'. Present: %s%s"
                         % (name, ", ".join(known) if known else "<scene is empty>",
                            " ..." if len(bpy.data.objects) > 25 else ""))
    if want_mesh and obj.type != "MESH":
        raise MifOpError("object '%s' is a %s, not a MESH" % (name, obj.type))
    if want_mesh and obj.mode != "OBJECT":
        raise MifOpError("object '%s' is in %s mode. MifBlender edits mesh data "
                         "directly via bmesh and needs OBJECT mode -- leave edit mode "
                         "first." % (name, obj.mode))
    return obj


def local_bounds(obj):
    """The LOCAL-space bounding box, read from the VERTEX DATA, not obj.bound_box.

    Local space rather than world: obj.dimensions folds in object scale, and a
    stray non-unit scale would mask geometry drift in a tiling assertion.

    Vertex data rather than obj.bound_box, because bound_box is a CACHE and it is
    stale exactly when this function matters most. MEASURED on Blender 4.4.0: a
    plane extruded via bmesh and written back with bm.to_mesh(mesh) +
    mesh.update() reports verts spanning z -0.15..0.0 while obj.bound_box still
    reports 0.0..0.0 and obj.dimensions still reports z=0. obj.update_tag() does
    NOT refresh it either; only bpy.context.view_layer.update() does.

    That is not a cosmetic difference. The round trip's X-length assert reads
    boundsLocalSizeUU straight out of object_info immediately after an edit, so a
    cached box would have it measuring the PRE-edit mesh and passing a sheared
    tile with a clean bill of health. Reading the verts cannot go stale.

    (The editing ops call view_layer.update() as well, so obj.bound_box and
    obj.dimensions are correct for every OTHER consumer. This function simply
    does not depend on their having done so.)

    VERIFIED identical to a refreshed obj.bound_box to 1e-6 on a 7-segment cone.
    """
    mesh = obj.data if obj.type == "MESH" else None
    count = len(mesh.vertices) if mesh is not None else 0
    if count:
        flat = [0.0] * (count * 3)
        mesh.vertices.foreach_get("co", flat)
        xs, ys, zs = flat[0::3], flat[1::3], flat[2::3]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    # No vertex data to read (a non-mesh object, or a mesh with no verts, whose
    # bound_box is all zeros anyway -- MEASURED).
    corners = [tuple(c) for c in obj.bound_box]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    zs = [c[2] for c in corners]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def mesh_counts(obj):
    mesh = obj.data
    try:
        mesh.calc_loop_triangles()
        tris = len(mesh.loop_triangles)
    except Exception:
        tris = -1
    return {
        "verts": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "tris": tris,
    }


def object_info(obj, with_counts=True):
    """The pre-image/post-image record. Every number a round-trip assertion
    might need, in both Blender units and Unreal units."""
    info = {
        "name": obj.name,
        "type": obj.type,
        "locationBU": rnd(obj.location),
        "rotationEulerRad": rnd(obj.rotation_euler),
        "scale": rnd(obj.scale),
        "isIdentityTransform": (
            all(abs(v) < 1e-6 for v in obj.location)
            and all(abs(v) < 1e-6 for v in obj.rotation_euler)
            and all(abs(v - 1.0) < 1e-6 for v in obj.scale)
        ),
    }
    if obj.type != "MESH":
        return info

    bmin, bmax = local_bounds(obj)
    size = [bmax[i] - bmin[i] for i in range(3)]
    info.update({
        "dimensionsBU": rnd(obj.dimensions),
        "boundsLocalMinBU": rnd(bmin),
        "boundsLocalMaxBU": rnd(bmax),
        "boundsLocalSizeBU": rnd(size),
        "boundsLocalSizeUU": rnd([v * UU_PER_BU for v in size], 4),
        "materialSlots": [(s.material.name if s.material else None)
                          for s in obj.material_slots],
        "uvLayers": [uv.name for uv in obj.data.uv_layers],
        "hasCustomSplitNormals": bool(obj.data.has_custom_normals),
    })
    if with_counts:
        info.update(mesh_counts(obj))
    return info


def selection_snapshot():
    view_layer = bpy.context.view_layer
    return ([o.name for o in bpy.context.selected_objects],
            view_layer.objects.active.name if view_layer.objects.active else None)


def selection_restore(snapshot):
    names, active = snapshot
    try:
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj.name in names)
        bpy.context.view_layer.objects.active = bpy.data.objects.get(active) if active else None
    except Exception:
        pass


def select_only(objs):
    """Deselect everything, select `objs`, make the first one active.

    bpy.ops.object.select_all needs a context; in background mode with no
    window it can fail, so do it by hand over the view layer instead.
    """
    view_layer = bpy.context.view_layer
    for obj in view_layer.objects:
        obj.select_set(False)
    first = None
    for obj in objs:
        if obj.name not in view_layer.objects:
            raise MifOpError("object '%s' is not in the active view layer, so it cannot "
                             "be selected for export (is it in an excluded collection?)"
                             % obj.name)
        obj.select_set(True)
        first = first or obj
    view_layer.objects.active = first
