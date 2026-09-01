"""Keyframes, the frame range, and reading animation back.

WHY THIS IS THE THIRD GAP CLOSED, after lights and cameras. Of the eight families with no typed op,
this is the one that gates a whole CLASS of request rather than a property: without it nothing
moves, so a fan cannot spin, a light cannot flicker, a camera cannot travel and an object cannot
fall. Every one of those was in the benchmark Andre was handed.

=============================================================================
THE THING THAT MAKES KEYFRAMING BY HAND WRONG: WHAT DO YOU KEY, THE OBJECT OR THE DATA?
=============================================================================
An object's transform lives on the OBJECT (obj.location). A light's brightness lives on its DATA
(obj.data.energy), and so does a camera's focal length. keyframe_insert() has to be called on the
datablock that OWNS the property, and calling it on the wrong one raises a bare RuntimeError naming
a data path rather than the mistake.

So `target` is explicit here - "object" or "data" - and defaults to the right one for the channel
being written rather than making every caller know. A caller keying `energy` gets the light data
without asking; a caller keying `location` gets the object.

=============================================================================
INSERTING A KEYFRAME MEANS WRITING THE VALUE FIRST
=============================================================================
keyframe_insert reads the CURRENT value of the property and stores it at the given frame. It does
not take a value. So every op here sets the property, then keys it - and a caller who expects to
pass a value and have the object left alone afterwards is wrong about what a keyframe is: the
object really is left holding the last value written. That is stated rather than hidden, because
"my object moved" is otherwise a confusing side effect.
"""
import bpy

from .ops_common import (MifOpError, get_object, reject_unknown, rnd, take, take_bool, take_float)

_KEY_KEYS = {
    "object", "name", "frame", "location", "rotation", "scale",
    "dataPath", "path", "value", "index", "target", "interpolation",
}
_RANGE_KEYS = {"start", "end", "fps", "current", "frameStart", "frameEnd"}
_LIST_KEYS = {"object", "name", "target"}

# Channels this op knows where to put without being told. Anything else needs an explicit target.
_OBJECT_CHANNELS = {"location", "rotation_euler", "scale", "rotation_quaternion",
                    "hide_viewport", "hide_render"}
_DATA_HINTS = {"energy", "color", "spot_size", "spot_blend", "lens", "focus_distance",
               "shadow_soft_size", "size", "angle", "ortho_scale", "default_value"}



def _fcurves(holder):
    """Every fcurve on `holder`, on ANY Blender this addon supports.

    BLENDER 5.0 MOVED THEM AND THE OLD PATH IS GONE, not deprecated. Up to 4.4 an action owned its
    curves directly as action.fcurves. In 5.0's slotted actions they live at
        action.layers[i].strips[j].channelbag(slot).fcurves
    and `Action` has no `fcurves` attribute at all, so the old access raises AttributeError rather
    than returning empty - which is how this was found: the first run of set_keyframe died with
    "'Action' object has no attribute 'fcurves'" on 5.0.1.

    Both paths are tried, newest first, because the suites run 3.6, 4.2, 4.4 and 5.0 and a helper
    that only knows one of them makes three of those versions red.
    """
    ad = getattr(holder, "animation_data", None)
    if not ad or not ad.action:
        return []
    act = ad.action
    if hasattr(act, "fcurves"):
        return list(act.fcurves)
    out = []
    slot = getattr(ad, "action_slot", None)
    for layer in getattr(act, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            cb = None
            if hasattr(strip, "channelbag") and slot is not None:
                try:
                    cb = strip.channelbag(slot)
                except (RuntimeError, TypeError):
                    cb = None
            if cb is None:
                for maybe in getattr(strip, "channelbags", ()):
                    out.extend(getattr(maybe, "fcurves", ()))
                continue
            out.extend(getattr(cb, "fcurves", ()))
    return out


def _vec3(params, key):
    v = params.get(key)
    if isinstance(v, dict):
        return (float(v.get("x", 0.0)), float(v.get("y", 0.0)), float(v.get("z", 0.0)))
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(float(x) for x in v)
    raise MifOpError("'%s' must be {x,y,z} or a 3-list, got %r. NOTHING was keyed." % (key, v))


def _resolve_target(obj, data_path, explicit):
    """The datablock that OWNS data_path, and why - so a refusal can say which one it tried."""
    if explicit:
        want = str(explicit).lower()
        if want in ("object", "obj"):
            return obj, "object (explicit)"
        if want in ("data", "datablock"):
            if obj.data is None:
                raise MifOpError("target 'data' was asked for but '%s' has no data datablock. "
                                 "NOTHING was keyed." % obj.name)
            return obj.data, "data (explicit)"
        raise MifOpError("unknown target '%s' - use 'object' or 'data'. NOTHING was keyed."
                         % explicit)
    root = data_path.split(".")[0].split("[")[0]
    if root in _OBJECT_CHANNELS:
        return obj, "object (a transform channel lives on the object)"
    if root in _DATA_HINTS and obj.data is not None:
        return obj.data, "data (%s lives on the %s datablock)" % (root, type(obj.data).__name__)
    # Fall back to whichever one actually HAS the attribute - resolved, not guessed.
    if hasattr(obj, root):
        return obj, "object (resolved by attribute)"
    if obj.data is not None and hasattr(obj.data, root):
        return obj.data, "data (resolved by attribute)"
    raise MifOpError(
        "neither '%s' nor its data has '%s', so there is nothing to key. Pass target explicitly if "
        "this is a nested path. NOTHING was keyed." % (obj.name, root))


def _apply_interpolation(owner, data_path, frame, mode):
    """Set interpolation on the points just written. LINEAR and CONSTANT are the two that matter:
    a flickering light on BEZIER eases between states and stops reading as a flicker at all."""
    n = 0
    for fc in _fcurves(owner):
        if fc.data_path != data_path:
            continue
        for kp in fc.keyframe_points:
            if abs(kp.co[0] - frame) < 1e-6:
                kp.interpolation = mode
                n += 1
    return n


def op_set_keyframe(params):
    """Write a value and key it at a frame. Returns the keyframe count read back off the fcurves.

    params:
      object (str, required)
      frame (int, required)
      location / rotation / scale   {x,y,z} - keyed on the OBJECT
      dataPath (alias path) + value - any other property; `value` may be a number, bool or list
      index (int)                   which array element to key, default all
      target                        object | data - only needed for a path this cannot place
      interpolation                 CONSTANT | LINEAR | BEZIER (default BEZIER, Blender's own)
    """
    reject_unknown(params, _KEY_KEYS, "set_keyframe")
    obj = get_object(take(params, "object", "name", required=True))
    frame = take_float(params, "frame", required=True)
    interp = str(take(params, "interpolation", default="BEZIER", kind=str)).upper()
    if interp not in ("CONSTANT", "LINEAR", "BEZIER"):
        raise MifOpError("interpolation must be CONSTANT, LINEAR or BEZIER, got '%s'. NOTHING was "
                         "keyed." % interp)

    transforms = {"location": "location", "rotation": "rotation_euler", "scale": "scale"}
    asked = [k for k in transforms if k in params]
    path = take(params, "dataPath", "path", default=None, kind=str)
    if not asked and not path:
        raise MifOpError("nothing to key - pass location, rotation, scale, or dataPath+value. "
                         "NOTHING was keyed.")
    if asked and path:
        raise MifOpError("pass transform channels OR dataPath, not both - they key different "
                         "things and combining them hides which one failed. NOTHING was keyed.")

    written = []
    if asked:
        for key in asked:
            dp = transforms[key]
            setattr(obj, dp, _vec3(params, key))
            obj.keyframe_insert(data_path=dp, frame=frame)
            _apply_interpolation(obj, dp, frame, interp)
            written.append({"target": "object", "dataPath": dp})
    else:
        owner, why = _resolve_target(obj, path, take(params, "target", default=None, kind=str))
        if "value" not in params:
            raise MifOpError("dataPath needs a `value` - keyframe_insert stores the property's "
                             "CURRENT value, so there is nothing to record without one. NOTHING "
                             "was keyed.")
        value = params.get("value")
        index = params.get("index")
        try:
            if index is not None:
                cur = getattr(owner, path.split(".")[0])
                cur[int(index)] = float(value)
            elif isinstance(value, (list, tuple)):
                setattr(owner, path, tuple(float(v) for v in value))
            elif isinstance(value, bool):
                setattr(owner, path, value)
            else:
                setattr(owner, path, float(value))
        except (AttributeError, TypeError, ValueError) as exc:
            raise MifOpError("could not write %r to '%s' on the %s datablock: %s. NOTHING was "
                             "keyed." % (value, path, why, exc))
        kwargs = {"data_path": path, "frame": frame}
        if index is not None:
            kwargs["index"] = int(index)
        owner.keyframe_insert(**kwargs)
        _apply_interpolation(owner, path, frame, interp)
        written.append({"target": why, "dataPath": path})

    # READ BACK off the fcurves. keyframe_insert returns a bool, and a True that produced no curve
    # is exactly the silent success this bridge exists to refuse.
    total = 0
    curves = []
    for holder in (obj, obj.data if obj.data is not None else None):
        if holder is None:
            continue
        for fc in _fcurves(holder):
            total += len(fc.keyframe_points)
            curves.append({"dataPath": fc.data_path, "index": fc.array_index,
                           "keyframes": len(fc.keyframe_points)})
    return {
        "object": obj.name,
        "frame": frame,
        "written": written,
        "interpolation": interp,
        "fcurves": curves,
        "keyframesTotal": total,
        "valueNote": ("keyframe_insert stores the property's CURRENT value, so the value passed was "
                      "WRITTEN to the object before being keyed - the object is left holding it."),
    }


def op_set_frame_range(params):
    """Scene frame range, fps and current frame. Reports what the scene ACTUALLY holds."""
    reject_unknown(params, _RANGE_KEYS, "set_frame_range")
    sc = bpy.context.scene
    before = {"start": sc.frame_start, "end": sc.frame_end, "fps": sc.render.fps,
              "current": sc.frame_current}
    start = take_float(params, "start", "frameStart", default=None)
    end = take_float(params, "end", "frameEnd", default=None)

    # CHECKED BEFORE ANYTHING IS WRITTEN, and the first version checked afterwards and did not
    # work. Blender CLAMPS these against each other as they are assigned: setting frame_end to 10
    # while frame_start is 90 drags frame_start down to 10 as a side effect, so by the time the
    # resulting state was inspected it read 10..10 - consistent, accepted, and renders a single
    # frame. The bad input had already been swallowed by the clamp.
    #
    # So the REQUESTED range is validated against itself, falling back to the current value for
    # whichever end was not supplied. Found by test_blender_anim A102 on its first run.
    want_start = int(start) if start is not None else before["start"]
    want_end = int(end) if end is not None else before["end"]
    if want_end < want_start:
        raise MifOpError("end (%d) is before start (%d), which Blender does not reject - it CLAMPS "
                         "one to the other as you assign them, leaving a range that renders a "
                         "single frame or nothing. NOTHING was changed; the range is still %d..%d."
                         % (want_end, want_start, before["start"], before["end"]))
    # Widen first, then narrow, so the clamp never fights a legitimate move: assigning end before
    # start when both are increasing would otherwise drag start along with it.
    if want_end >= before["end"]:
        sc.frame_end = want_end
        sc.frame_start = want_start
    else:
        sc.frame_start = want_start
        sc.frame_end = want_end
    fps = take_float(params, "fps", default=None)
    if fps is not None:
        sc.render.fps = int(fps)
    cur = take_float(params, "current", default=None)
    if cur is not None:
        sc.frame_set(int(cur))
    return {"before": before,
            "after": {"start": sc.frame_start, "end": sc.frame_end,
                      "fps": sc.render.fps, "current": sc.frame_current},
            "durationSeconds": round((sc.frame_end - sc.frame_start + 1) / float(sc.render.fps), 4)}


def op_list_keyframes(params):
    """Every fcurve on an object and/or its data, with the frames actually stored.

    The read half. Without it `set_keyframe` can only be trusted on its own word, and this repo's
    standing rule is that a write is not verified by the writer.
    """
    reject_unknown(params, _LIST_KEYS, "list_keyframes")
    obj = get_object(take(params, "object", "name", required=True))
    want = str(take(params, "target", default="both", kind=str)).lower()
    out = []
    holders = []
    if want in ("both", "object", "obj"):
        holders.append(("object", obj))
    if want in ("both", "data", "datablock") and obj.data is not None:
        holders.append(("data", obj.data))
    for label, holder in holders:
        for fc in _fcurves(holder):
            out.append({
                "target": label,
                "dataPath": fc.data_path,
                "index": fc.array_index,
                "keyframes": [rnd([kp.co[0], kp.co[1]]) for kp in fc.keyframe_points],
                "interpolation": sorted({kp.interpolation for kp in fc.keyframe_points}),
            })
    return {"object": obj.name, "curves": out, "curveCount": len(out),
            "keyframeTotal": sum(len(c["keyframes"]) for c in out)}


OPS = {
    "set_keyframe": op_set_keyframe,
    "set_frame_range": op_set_frame_range,
    "list_keyframes": op_list_keyframes,
}
