"""MifBlender ops: introspection, scene housekeeping, and the run_python hatch."""

from __future__ import annotations

import io
import math
import os
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr

import bpy

from .ops_common import (
    MifOpError, jsonable, object_info, mesh_counts, take, take_bool, take_float,
    reject_unknown, get_object, UU_PER_BU,
)

ADDON_VERSION = (0, 1, 0)


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------

def op_ping(params):
    reject_unknown(params, {"echo"}, "ping")
    from . import server as _server  # local import: avoids an import cycle

    prefs = _prefs()
    out = {
        "pong": True,
        "addon": "MifBlender",
        "addonVersion": list(ADDON_VERSION),
        "protocolVersion": _server.PROTOCOL_VERSION,
        "blenderVersion": list(bpy.app.version),
        "blenderVersionString": bpy.app.version_string,
        "background": bpy.app.background,
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "blendFile": bpy.data.filepath or None,
        "sceneName": bpy.context.scene.name if bpy.context.scene else None,
        "objectCount": len(bpy.data.objects),
        "unitsPerBlenderUnit": UU_PER_BU,
        "runPythonAllowed": bool(getattr(prefs, "allow_run_python", False)),
        "ops": _server.op_names(),
    }
    echo = params.get("echo")
    if echo is not None:
        out["echo"] = jsonable(echo)
    return out


# ---------------------------------------------------------------------------
# list_objects
# ---------------------------------------------------------------------------

def op_list_objects(params):
    reject_unknown(params, {"type", "objectType", "pattern", "detail"}, "list_objects")
    want_type = take(params, "type", "objectType")
    pattern = take(params, "pattern")
    detail = take_bool(params, "detail", default=False)

    if want_type is not None:
        want_type = str(want_type).upper()

    rows = []
    for obj in bpy.data.objects:
        if want_type and obj.type != want_type:
            continue
        if pattern and str(pattern).lower() not in obj.name.lower():
            continue
        if detail:
            rows.append(object_info(obj))
            continue
        row = {"name": obj.name, "type": obj.type,
               "inViewLayer": obj.name in bpy.context.view_layer.objects}
        if obj.type == "MESH":
            row["dimensionsBU"] = [round(float(v), 6) for v in obj.dimensions]
            row.update(mesh_counts(obj))
            row["materialSlots"] = [(s.material.name if s.material else None)
                                    for s in obj.material_slots]
        rows.append(row)

    return {"count": len(rows), "objects": rows,
            "filteredBy": {"type": want_type, "pattern": pattern}}


# ---------------------------------------------------------------------------
# scene_info
# ---------------------------------------------------------------------------

def op_scene_info(params):
    """What is actually in this Blender right now, and is it safe to export from.

    Read-only. Beyond the obvious census it reports scene.unit_settings, because
    scale_length is a silent multiplier on every FBX this pipeline writes.

      MEASURED on Blender 4.4.0 headless, factory startup, with the addon's own
      FBX_EXPORT_ARGS: the same 10 BU cube exported at scale_length 1.0 reimports
      at 10.0 BU, and exported at scale_length 0.01 reimports at 0.1 BU (object
      scale 0.01). UnitScaleFactor stays 1.0 in the file BOTH times, so the
      header does not give the mismatch away -- only the magnitudes do.

    A round trip run in a scene with a non-default scale_length therefore comes
    back 100x wrong while every ok:true in the chain stays true, which is why it
    is a warning here rather than a footnote in a README.
    """
    reject_unknown(params, {"detail"}, "scene_info")
    detail = take_bool(params, "detail", default=False)

    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    units = scene.unit_settings if scene else None
    active = view_layer.objects.active if view_layer else None

    by_type = {}
    for obj in bpy.data.objects:
        by_type[obj.type] = by_type.get(obj.type, 0) + 1

    if detail:
        objects = [object_info(o) for o in bpy.data.objects]
    else:
        objects = [{"name": o.name, "type": o.type,
                    "inViewLayer": bool(view_layer) and o.name in view_layer.objects}
                   for o in bpy.data.objects]

    warnings = []
    scale_length = float(units.scale_length) if units else 1.0
    if units is not None and abs(scale_length - 1.0) > 1e-9:
        warnings.append(
            "scene.unit_settings.scale_length is %g, not 1.0. MEASURED on 4.4.0: this "
            "scales every FBX this addon writes by that factor while UnitScaleFactor in "
            "the file stays 1.0 -- a 1000 uu tile would land in Unreal at %g uu and "
            "nothing in the export would report an error. Set it back to 1.0 before "
            "exporting, or expect the round trip's fidelity gate to abort."
            % (scale_length, 1000.0 * scale_length))
    if units is not None and units.system not in ("METRIC", "NONE"):
        warnings.append(
            "scene.unit_settings.system is %r. The centimetre reasoning this pipeline "
            "rests on (1 BU = %g uu) was verified under METRIC only -- an IMPERIAL scene "
            "is UNVERIFIED here." % (units.system, UU_PER_BU))

    return {
        "sceneName": scene.name if scene else None,
        "blendFile": bpy.data.filepath or None,
        "background": bpy.app.background,
        "objectCount": len(bpy.data.objects),
        "objectsByType": by_type,
        "objects": objects,
        "meshCount": by_type.get("MESH", 0),
        "activeObject": active.name if active else None,
        "selectedObjects": [o.name for o in bpy.context.selected_objects],
        "viewLayerObjectCount": len(view_layer.objects) if view_layer else 0,
        "collections": [c.name for c in bpy.data.collections],
        "unitSettings": {
            "system": units.system if units else None,
            "systemRotation": units.system_rotation if units else None,
            "scaleLength": scale_length,
            "lengthUnit": units.length_unit if units else None,
        },
        "unrealUnitsPerBlenderUnit": UU_PER_BU,
        "frameCurrent": scene.frame_current if scene else None,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# object_info
# ---------------------------------------------------------------------------

def op_object_info(params):
    reject_unknown(params, {"object", "name"}, "object_info")
    name = take(params, "object", "name", required=True, kind=str)
    return {"object": object_info(get_object(name))}


# ---------------------------------------------------------------------------
# clear_scene / delete_object
# ---------------------------------------------------------------------------

def _delete(objs, purge):
    names = [o.name for o in objs]
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)
    purged = 0
    if purge:
        # orphan meshes/materials left behind by the delete
        # LIGHTS, CAMERAS AND NODE GROUPS ADDED 2026-09-01, and their absence was not theoretical.
        # This list predates the ops that create them, so clearing a scene left orphaned light and
        # camera DATA behind holding their names - and the next create_light called "Fluoro_01" got
        # "Fluoro_01.001", because bpy.data.objects.new() takes the datablock's name and the
        # datablock could not have the old one. A later keyframe call then failed with "no object
        # named 'Fluoro_01'" on a scene that had just been cleared and rebuilt. The response had
        # reported the real name all along; nothing was reading it.
        for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images,
                     bpy.data.armatures, bpy.data.lights, bpy.data.cameras,
                     bpy.data.node_groups, bpy.data.particles, bpy.data.actions):
            for datablock in list(coll):
                if datablock.users == 0:
                    coll.remove(datablock)
                    purged += 1
    return names, purged


def op_clear_scene(params):
    reject_unknown(params, {"type", "objectType", "purgeOrphans", "purge"}, "clear_scene")
    want_type = take(params, "type", "objectType")
    purge = take_bool(params, "purgeOrphans", "purge", default=True)
    if want_type is not None:
        want_type = str(want_type).upper()

    targets = [o for o in bpy.data.objects if not want_type or o.type == want_type]
    removed, purged = _delete(targets, purge)
    return {"removed": removed, "removedCount": len(removed),
            "orphansPurged": purged, "remaining": len(bpy.data.objects)}


def op_delete_object(params):
    reject_unknown(params, {"object", "name", "objects", "purgeOrphans", "purge"},
                   "delete_object")
    purge = take_bool(params, "purgeOrphans", "purge", default=False)
    names = take(params, "objects")
    if names is None:
        names = [take(params, "object", "name", required=True, kind=str)]
    if not isinstance(names, list):
        raise MifOpError("'objects' must be a list of object names")

    targets = [get_object(n) for n in names]
    removed, purged = _delete(targets, purge)
    return {"removed": removed, "removedCount": len(removed), "orphansPurged": purged}


# ---------------------------------------------------------------------------
# run_python  -- the escape hatch
# ---------------------------------------------------------------------------

RUN_PYTHON_HELP = (
    "run_python is disabled. Enable it in Edit > Preferences > Add-ons > MifBlender "
    "> 'Allow run_python'. It executes arbitrary code inside Blender with your user's "
    "privileges -- only enable it on a machine you control."
)


def op_run_python(params):
    """Execute arbitrary Python on the main thread.

    Everything MifBlender does not have a first-class op for goes through here.
    It runs on the main thread like every other op, so bpy is safe to touch.

    Contract: whatever the code assigns to a module-level name `result` comes
    back in the response, coerced to JSON-safe values. stdout and stderr are
    captured. An exception is returned as ok:false with the traceback -- it does
    NOT kill the connection.
    """
    reject_unknown(params, {"code", "script", "file", "filepath", "returnLocals"},
                   "run_python")
    prefs = _prefs()
    if not getattr(prefs, "allow_run_python", False):
        raise MifOpError(RUN_PYTHON_HELP)

    code = take(params, "code", "script")
    path = take(params, "file", "filepath")
    if code is None and path is None:
        raise MifOpError("run_python needs 'code' (a string) or 'file' (a path to a .py)")
    if code is not None and path is not None:
        raise MifOpError("pass 'code' or 'file', not both")
    if path is not None:
        if not os.path.isfile(path):
            raise MifOpError("no such file: %s" % path)
        with open(path, "r", encoding="utf-8") as handle:
            code = handle.read()
    if not isinstance(code, str):
        raise MifOpError("'code' must be a string")

    namespace = {
        "__name__": "mifblender_run_python",
        "bpy": bpy,
        "math": math,
        "result": None,
    }
    try:
        import bmesh  # noqa: WPS433 - convenience for callers
        namespace["bmesh"] = bmesh
    except Exception:
        pass
    try:
        import mathutils  # noqa: WPS433
        namespace["mathutils"] = mathutils
    except Exception:
        pass

    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            exec(compile(code, "<mifblender:run_python>", "exec"), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "traceback": traceback.format_exc(),
            "stdout": out_buf.getvalue(),
            "stderr": err_buf.getvalue(),
        }

    response = {
        "stdout": out_buf.getvalue(),
        "stderr": err_buf.getvalue(),
        "result": jsonable(namespace.get("result")),
    }
    if take_bool(params, "returnLocals", default=False):
        response["names"] = sorted(
            k for k in namespace
            if not k.startswith("__") and k not in ("bpy", "bmesh", "mathutils", "math"))
    return response


# RAY VISIBILITY MOVED OFF THE CYCLES ADDON onto the object itself in 3.0, so both spellings are
# tried, newest first - the same discipline the light shadow flag needed. A hardcoded single name
# is a silent no-op on half the Blenders this addon supports.
_VIS_FLAGS = (
    ("hideViewport", ("hide_viewport",), None),
    ("hideRender", ("hide_render",), None),
    ("visibleCamera", ("visible_camera",), "camera"),
    ("visibleDiffuse", ("visible_diffuse",), "diffuse"),
    ("visibleGlossy", ("visible_glossy",), "glossy"),
    ("visibleTransmission", ("visible_transmission",), "transmission"),
    ("visibleVolumeScatter", ("visible_volume_scatter",), "scatter"),
    ("visibleShadow", ("visible_shadow",), "shadow"),
    ("holdout", ("is_holdout",), "is_holdout"),
    ("indirectOnly", ("is_shadow_catcher",), "is_shadow_catcher"),
)


# A LITERAL, because parity_check resolves accepted-key sets statically and is fail-closed - it
# reads a set literal and refuses both a function-local build and a set comprehension rather than
# SKIP a check it cannot read. That is the right behaviour and it costs this duplication of the
# names in _VIS_FLAGS.
#
# So the duplication is GUARDED rather than trusted. The check below runs at import: it can only
# fail if somebody edits one list and not the other, which is exactly the moment you want to be
# stopped - and a key present in _VIS_FLAGS but missing here would be silently REFUSED at the door
# while looking supported everywhere else, which is the worst of both.
_VISIBILITY_KEYS = {
    "object", "name",
    "hideViewport", "hideRender",
    "visibleCamera", "visibleDiffuse", "visibleGlossy", "visibleTransmission",
    "visibleVolumeScatter", "visibleShadow",
    "holdout", "indirectOnly",
}

_missing = {k for k, _a, _c in _VIS_FLAGS} - _VISIBILITY_KEYS
if _missing:
    raise RuntimeError(
        "MifBlender ops_scene: _VIS_FLAGS names %s but _VISIBILITY_KEYS does not, so those keys "
        "would be refused by reject_unknown while every other part of the op supports them. Add "
        "them to the literal - it is duplicated on purpose so parity_check can read it."
        % ", ".join(sorted(_missing)))


def _vis_target(obj, attrs, cycles_attr):
    """(holder, attribute) for a visibility flag on this Blender, or None if it has none."""
    for a in attrs:
        if hasattr(obj, a):
            return obj, a
    cyc = getattr(obj, "cycles", None)
    if cycles_attr and cyc is not None and hasattr(cyc, cycles_attr):
        return cyc, cycles_attr
    return None


def _visibility_readback(obj):
    """Every visibility flag this Blender exposes for the object, by our key names."""
    out = {}
    for key, attrs, cyc in _VIS_FLAGS:
        tgt = _vis_target(obj, attrs, cyc)
        if tgt is not None:
            out[key] = bool(getattr(tgt[0], tgt[1]))
    return out


def op_set_object_visibility(params):
    """Control what an object is visible TO - the viewport, the render, and each ray type.

    WHY THIS IS THE MOST-WANTED TOGGLE. Stopping a softbox appearing as a white rectangle in every
    reflection is visibleGlossy=false, and it is the single most common adjustment in product and
    archviz lighting. Nothing in this addon could reach it. The general form also answers the other
    frequent question - "why is my object missing from the render" - because hide_render and the
    per-ray flags are exactly where that answer lives.

    Deliberately for EVERY object type rather than lights alone: ray visibility is an OBJECT
    property, and a mesh acting as a reflector or a holdout needs it as much as a lamp does.

    params:
      object (str, required)
      hideViewport / hideRender (bool)
      visibleCamera / visibleDiffuse / visibleGlossy / visibleTransmission /
      visibleVolumeScatter / visibleShadow (bool)
      holdout / indirectOnly (bool)

    A flag this Blender does not expose is REFUSED by name rather than silently ignored - the
    mistake create_light's `shadow` made until it was fixed the same day.
    """
    reject_unknown(params, _VISIBILITY_KEYS, "set_object_visibility")
    want = take(params, "object", "name", default=None, kind=str)
    if not want:
        raise MifOpError("'object' is required. NOTHING was changed.")
    obj = bpy.data.objects.get(want)
    if obj is None:
        raise MifOpError("no object named '%s'. NOTHING was changed." % want)

    # RESOLVE EVERY REQUESTED FLAG BEFORE WRITING ANY, so a flag this build lacks refuses the whole
    # call rather than leaving half of them applied.
    plan = []
    for key, attrs, cyc in _VIS_FLAGS:
        if key not in params:
            continue
        tgt = _vis_target(obj, attrs, cyc)
        if tgt is None:
            raise MifOpError("this Blender exposes no '%s' on an object (tried %s%s), so it would "
                             "have been silently ignored. NOTHING was changed."
                             % (key, ", ".join(attrs),
                                " and cycles.%s" % cyc if cyc else ""))
        plan.append((key, tgt, take_bool(params, key, default=True)))
    if not plan:
        raise MifOpError("no visibility flag was given. Pass at least one of %s. NOTHING was "
                         "changed." % ", ".join(sorted(k for k, _a, _c in _VIS_FLAGS)))

    before = _visibility_readback(obj)
    for _key, (holder, attr), value in plan:
        setattr(holder, attr, value)
    after = _visibility_readback(obj)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "object": obj.name,
        "before": before,
        "after": after,
        "changedFields": changed,
        "changedAnything": bool(changed),
        # THE QUESTION PEOPLE ACTUALLY ASK. Two flags decide whether this object appears at all,
        # and they are easy to set and then forget about.
        "appearsInRender": not after.get("hideRender", False)
                           and after.get("visibleCamera", True),
    }


_CUSTOM_SKIP = {"_RNA_UI", "cycles", "cycles_visibility"}


def _custom_holder(obj, bone_name, verb):
    """Object or pose bone. Custom properties live on both and mean the same thing on each."""
    if not bone_name:
        return obj, "object"
    if obj.type != "ARMATURE" or obj.pose is None:
        raise MifOpError("'bone' was given but '%s' is not a posed ARMATURE. NOTHING was %s."
                         % (obj.name, verb))
    pb = obj.pose.bones.get(str(bone_name))
    if pb is None:
        raise MifOpError("no bone named '%s' on '%s'. NOTHING was %s."
                         % (bone_name, obj.name, verb))
    return pb, "bone '%s'" % pb.name


def _custom_rows(holder):
    rows = []
    for key in holder.keys():
        if key in _CUSTOM_SKIP:
            continue
        try:
            raw = holder[key]
        except (KeyError, TypeError):
            continue
        try:
            value = list(raw) if hasattr(raw, "__len__") and not isinstance(raw, str) else raw
        except TypeError:
            value = str(raw)
        row = {"key": key, "value": value, "type": type(raw).__name__}
        # UI metadata is a separate store from the value, and a rig slider is useless without it.
        try:
            ui = holder.id_properties_ui(key).as_dict()
            row["min"] = ui.get("min")
            row["max"] = ui.get("max")
            row["description"] = ui.get("description") or None
        except (AttributeError, TypeError, KeyError):
            row["min"] = row["max"] = row["description"] = None
        rows.append(row)
    return sorted(rows, key=lambda r: r["key"])


def op_list_custom_properties(params):
    """Custom properties on an object or pose bone, with their UI range.

    params:
      object (str, required)
      bone (str)   read the pose bone's instead

    Custom properties are how a rig exposes controls, and glTF writes them into the engine as
    `extras` - so they are metadata that travels, not just annotations. The UI min/max is a
    separate store from the value and is reported alongside, because a slider without a range is
    not a control.
    """
    reject_unknown(params, {"object", "name", "bone"}, "list_custom_properties")
    obj = get_object(take(params, "object", "name", required=True))
    holder, label = _custom_holder(obj, take(params, "bone", default=None, kind=str), "read")
    rows = _custom_rows(holder)
    return {
        "object": obj.name,
        "owner": label,
        "count": len(rows),
        "properties": rows,
        # Named rather than silently filtered: cycles settings live in the same namespace and are
        # not user metadata, so a count that included them would be wrong in a confusing way.
        "skippedInternalKeys": sorted(k for k in holder.keys() if k in _CUSTOM_SKIP),
    }


def op_set_custom_property(params):
    """Set a custom property, and its UI range, on an object or pose bone.

    params:
      object (str, required)
      key (str, required)
      value                    number, bool, string or list
      bone (str)
      min / max (float)        UI range - a rig slider without one is not a control
      description (str)
      delete (bool)            remove the property instead

    THE TYPE IS REPORTED BACK because Blender coerces silently: an int written where a float is
    expected stays an int, and a driver or an exporter reading it later gets a different type than
    the caller thinks they stored.
    """
    reject_unknown(params, {"object", "name", "key", "value", "bone", "min", "max",
                            "description", "delete"}, "set_custom_property")
    obj = get_object(take(params, "object", "name", required=True))
    holder, label = _custom_holder(obj, take(params, "bone", default=None, kind=str), "changed")
    key = take(params, "key", default=None, kind=str)
    if not key:
        raise MifOpError("'key' is required. NOTHING was changed.")
    key = str(key)
    if key in _CUSTOM_SKIP:
        raise MifOpError("'%s' is an internal key, not user metadata - writing it would collide "
                         "with Blender's own storage. NOTHING was changed." % key)

    if take_bool(params, "delete", default=False):
        if key not in holder.keys():
            raise MifOpError("no custom property '%s' on %s to delete. NOTHING was changed."
                             % (key, label))
        del holder[key]
        if key in holder.keys():
            raise MifOpError("deleted '%s' but it is still present. Do not trust this state." % key)
        return {"object": obj.name, "owner": label, "key": key, "deleted": True,
                "properties": _custom_rows(holder)}

    if "value" not in params:
        raise MifOpError("'value' is required unless delete:true. NOTHING was changed.")
    value = params.get("value")
    if isinstance(value, (list, tuple)):
        value = list(value)
    holder[key] = value

    lo = take_float(params, "min", default=None)
    hi = take_float(params, "max", default=None)
    desc = take(params, "description", default=None, kind=str)
    if lo is not None and hi is not None and lo > hi:
        raise MifOpError("min %g is above max %g. The value WAS written; the range was not."
                         % (lo, hi))
    ui_set = False
    if lo is not None or hi is not None or desc:
        try:
            ui = holder.id_properties_ui(key)
            kwargs = {}
            if lo is not None:
                kwargs["min"] = lo
            if hi is not None:
                kwargs["max"] = hi
            if desc:
                kwargs["description"] = str(desc)
            ui.update(**kwargs)
            ui_set = True
        except (AttributeError, TypeError, KeyError) as exc:
            raise MifOpError("the value was written but this Blender would not take the UI range "
                             "for '%s' (%s). The property EXISTS and has no slider bounds."
                             % (key, exc))

    rows = [r for r in _custom_rows(holder) if r["key"] == key]
    return {
        "object": obj.name,
        "owner": label,
        "key": key,
        "property": rows[0] if rows else None,
        "uiRangeSet": ui_set,
        # BLENDER COERCES SILENTLY. An int written where a float was meant stays an int, and an
        # exporter or a driver reading it later gets a type the caller did not think they stored.
        "storedType": type(holder[key]).__name__,
        "requestedType": type(value).__name__,
        "typeChanged": type(holder[key]).__name__ != type(value).__name__,
    }


OPS = {
    "list_custom_properties": op_list_custom_properties,
    "set_custom_property": op_set_custom_property,
    "ping": op_ping,
    "scene_info": op_scene_info,
    "list_objects": op_list_objects,
    "object_info": op_object_info,
    "clear_scene": op_clear_scene,
    "delete_object": op_delete_object,
    "run_python": op_run_python,
    "set_object_visibility": op_set_object_visibility,
}
