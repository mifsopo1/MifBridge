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
    # rigid_body, collision, cloth and friends hang off the OBJECT, and a dotted path rooted at one
    # of them is an object path however deep it goes.
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
        # A DOTTED PATH NEEDS WALKING. keyframe_insert takes "rigid_body.kinematic" happily, but
        # setattr does not - it would look for an attribute literally named that and fail with
        # "'Object' object has no attribute 'kinematic'", which names the leaf and hides that the
        # problem was the walk. Found keying a rigid body's kinematic flag, which is the ordinary
        # way to hold an object still and then hand it to the simulation.
        holder, leaf = owner, path
        if "." in path:
            head, leaf = path.rsplit(".", 1)
            # RESOLVED BY BLENDER, NOT BY A HAND-ROLLED WALK. The previous version did
            # `getattr(holder, part.split("[")[0])`, which STRIPS a subscript and never puts it
            # back: for pose.bones["hand"].location it walked to the bones COLLECTION and then
            # tried to set `location` on it. Every bracketed path was therefore unwritable, which
            # is most of the interesting ones - bones, modifiers by name, shape keys, node inputs,
            # constraints, particle settings. keyframe_insert below never had the problem because
            # it takes the FULL path and Blender resolves it, so the op could key a path it could
            # not write, and the caller got a keyframe holding the OLD value.
            #
            # path_resolve does the same resolution Blender does everywhere else, subscripts
            # included. It raises ValueError for a path that does not exist, which is a better
            # error than AttributeError naming only the leaf.
            try:
                holder = owner.path_resolve(head)
            except (ValueError, AttributeError, TypeError) as exc:
                raise MifOpError("could not resolve '%s' on '%s' (%s). Check the path against "
                                 "Blender's own - right-click a field and Copy Full Data Path, "
                                 "then drop the object prefix. NOTHING was keyed."
                                 % (head, obj.name, exc))
            if holder is None:
                raise MifOpError("'%s' is None on '%s', so '%s' cannot be written. A rigid "
                                 "body path needs add_rigid_body to have run first. NOTHING "
                                 "was keyed." % (head, obj.name, path))
        try:
            if index is not None:
                cur = getattr(holder, leaf)
                cur[int(index)] = float(value)
            elif isinstance(value, (list, tuple)):
                setattr(holder, leaf, tuple(float(v) for v in value))
            elif isinstance(value, bool):
                setattr(holder, leaf, value)
            else:
                setattr(holder, leaf, float(value))
        except (AttributeError, TypeError, ValueError) as exc:
            raise MifOpError("could not write %r to '%s' on the %s datablock: %s. NOTHING was "
                             "keyed." % (value, path, why, exc))
        kwargs = {"data_path": path, "frame": frame}
        if index is not None:
            kwargs["index"] = int(index)
        owner.keyframe_insert(**kwargs)
        # THE COUNT IS KEPT. _apply_interpolation returns how many keys it actually found AT THIS
        # FRAME on this path, and it was being discarded - so the only per-call measurement that
        # this keyframe exists went in the bin, leaving keyframesTotal below as the only evidence.
        # That total sums every fcurve on the object and its data, so any PRIOR key keeps it
        # non-zero and it cannot fail. Same shape as op_transfer_weights counting group membership.
        keyed_here = _apply_interpolation(owner, path, frame, interp)
        written.append({"target": why, "dataPath": path, "keysAtThisFrame": keyed_here})

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
        # WHAT THIS CALL DID, as opposed to what the object now holds. keyframesTotal is a scene
        # measurement and cannot fall to zero once anything has ever been keyed, so on its own it
        # can never report a failure. This is the per-call number, summed from the paths written
        # above, and it IS zero when a call keyed nothing.
        "keysWrittenAtThisFrame": sum(w.get("keysAtThisFrame") or 0 for w in written),
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
            "keyframeTotal": sum(len(c["keyframes"]) for c in out),
            # SAYS WHAT IT DID NOT LOOK AT. This walks animation_data.action only, so an object
            # animated entirely by drivers or by NLA strips reports curveCount 0 here and is not
            # un-animated. Anyone reading a zero needs to know which question was asked.
            "scopeNote": ("Action fcurves only. Drivers and NLA strips are separate collections "
                          "and are NOT counted here - use list_animation_data for all three.")}


def _anim_summary(label, holder):
    """Every route by which `holder` can be animated, not just the one list_keyframes reads.

    THE REASON THIS EXISTS. list_keyframes walks animation_data.action, which is one of THREE
    places animation lives. Drivers are on animation_data.drivers and NLA strips on
    animation_data.nla_tracks, both separate collections. So an object driven entirely by drivers -
    a rig control, a procedural offset, anything wired rather than keyed - came back with
    curveCount 0 from an op whose entire purpose is verification. That is not a missing answer, it
    is a WRONG one, and a caller checking whether their driver landed was told it had not.
    """
    ad = getattr(holder, "animation_data", None)
    if ad is None:
        return {"target": label, "hasAnimationData": False, "animatedBy": []}

    action = getattr(ad, "action", None)
    action_curves = len(_fcurves(holder)) if action is not None else 0

    drivers = []
    for dr in (getattr(ad, "drivers", None) or []):
        try:
            var_targets = []
            for var in (dr.driver.variables or []):
                for tgt in (var.targets or []):
                    if tgt.id is not None:
                        var_targets.append(tgt.id.name)
            drivers.append({
                "dataPath": dr.data_path,
                "index": dr.array_index,
                "expression": getattr(dr.driver, "expression", ""),
                "type": getattr(dr.driver, "type", None),
                # A driver whose variable points at a deleted object is the silent failure mode -
                # it evaluates to zero and reports nothing.
                "isValid": bool(getattr(dr.driver, "is_valid", True)),
                "variableTargets": sorted(set(var_targets)),
            })
        except (AttributeError, TypeError):
            continue

    tracks = []
    for tr in (getattr(ad, "nla_tracks", None) or []):
        tracks.append({
            "name": tr.name,
            "mute": bool(tr.mute),
            "strips": [{"name": s.name,
                        "action": s.action.name if s.action else None,
                        "frameStart": round(float(s.frame_start), 4),
                        "frameEnd": round(float(s.frame_end), 4)} for s in (tr.strips or [])],
        })

    by = []
    if action_curves:
        by.append("action")
    if drivers:
        by.append("drivers")
    if tracks:
        by.append("nla")
    return {
        "target": label,
        "hasAnimationData": True,
        "actionName": action.name if action is not None else None,
        # An action with a fake user survives a save; one without is DELETED on save, which is
        # data loss nobody is warned about anywhere else.
        "actionHasFakeUser": bool(getattr(action, "use_fake_user", False)) if action else False,
        "actionCurveCount": action_curves,
        "driverCount": len(drivers),
        "drivers": drivers,
        "nlaTrackCount": len(tracks),
        "nlaTracks": tracks,
        "animatedBy": by,
    }


def op_list_animation_data(params):
    """Every route by which an object is animated - action, drivers AND NLA.

    list_keyframes answers "which keyframes are on the action", which is a narrower question than
    it looks, and returns curveCount 0 for an object animated entirely by drivers. This answers
    "is this animated at all, and by what".

    params:
      object (str, required)
      target (str)   object | data | both (default)
    """
    reject_unknown(params, _LIST_KEYS, "list_animation_data")
    obj = get_object(take(params, "object", "name", required=True))
    want = str(take(params, "target", default="both", kind=str)).lower()
    rows = []
    if want in ("both", "object", "obj"):
        rows.append(_anim_summary("object", obj))
    if want in ("both", "data", "datablock") and obj.data is not None:
        rows.append(_anim_summary("data", obj.data))
    routes = sorted({r for row in rows for r in row.get("animatedBy", [])})
    return {
        "object": obj.name,
        "sources": rows,
        "animatedBy": routes,
        "isAnimated": bool(routes),
        "invalidDrivers": sum(1 for row in rows for d in row.get("drivers", [])
                              if not d.get("isValid", True)),
    }


def op_delete_keyframe(params):
    """Remove a keyframe, and prove it went. The correction path set_keyframe never had.

    params:
      object (str, required)
      dataPath (alias path, required)  e.g. "location", "hide_render", 'pose.bones["x"].location'
      frame (int)                      the frame to clear. Omitted removes EVERY key on the path.
      index (int)                      which array element; omitted means all of them

    Counted before and after on the matching curves, because keyframe_delete returns a bool that is
    False both for "there was nothing there" and for "it refused", and those are different answers.
    """
    reject_unknown(params, {"object", "name", "dataPath", "path", "frame", "index"},
                   "delete_keyframe")
    obj = get_object(take(params, "object", "name", required=True))
    path = take(params, "dataPath", "path", default=None, kind=str)
    if not path:
        raise MifOpError("'dataPath' is required - which channel to clear. NOTHING was deleted.")
    frame = params.get("frame")
    index = params.get("index")

    def matching():
        found = []
        for holder in (obj, obj.data if obj.data is not None else None):
            if holder is None:
                continue
            for fc in _fcurves(holder):
                if fc.data_path != path:
                    continue
                if index is not None and fc.array_index != int(index):
                    continue
                found.append(fc)
        return found

    curves = matching()
    if not curves:
        raise MifOpError("no fcurve on '%s' for dataPath '%s'%s. Nothing to delete - list them "
                         "with list_keyframes. NOTHING was deleted."
                         % (obj.name, path,
                            "" if index is None else " at index %s" % index))
    before = sum(len(fc.keyframe_points) for fc in curves)

    removed = 0
    for fc in curves:
        # Reverse order: removing shifts the collection under an ascending walk, which silently
        # skips every second key.
        for kp in reversed(list(fc.keyframe_points)):
            if frame is None or abs(kp.co[0] - float(frame)) < 1e-6:
                fc.keyframe_points.remove(kp)
                removed += 1
        fc.update()

    after = sum(len(fc.keyframe_points) for fc in matching())
    if removed == 0:
        raise MifOpError("'%s' on '%s' has %d keyframe(s) but none at frame %s. NOTHING was "
                         "deleted." % (path, obj.name, before, frame))
    return {
        "object": obj.name,
        "dataPath": path,
        "frame": frame,
        "index": index,
        "keyframesBefore": before,
        "keyframesAfter": after,
        "removed": removed,
        # MEASURED, not the operator's word. before-after is the count that actually left the
        # curve; `removed` is what this op thinks it did, and they must agree.
        "countsAgree": (before - after) == removed,
    }


def op_evaluate_at_frame(params):
    """What an object ACTUALLY is at given frames, through the depsgraph.

    THE VERIFICATION SUBSTRATE, and the reason it comes before the rest of the animation work.
    Every read in this addon reads the RAW property off the datablock, and that is not the value
    the scene evaluates to whenever a constraint, driver, NLA stack, parent or simulation cache is
    involved. A constraint does not touch obj.matrix_world at all: reading the base object reports
    every constraint as having done nothing, and reading back the value you just wrote is a proxy
    that cannot fail. Anything built on top of drivers or constraints cannot be proven without
    this.

    params:
      object (str, required)
      frames (list[int], required)  the frames to sample. A single int is accepted.
      dataPaths (list[str])         extra properties to read at each frame, evaluated. The world
                                    matrix is always reported.

    THE SCENE FRAME IS RESTORED and the restoration is ASSERTED, not assumed - this op moves
    frame_current to sample, and leaving somebody's scene on frame 47 because a read had a side
    effect is exactly the kind of quiet damage this codebase refuses.
    """
    reject_unknown(params, {"object", "name", "frames", "frame", "dataPaths", "paths"},
                   "evaluate_at_frame")
    obj = get_object(take(params, "object", "name", required=True))

    raw_frames = params.get("frames", params.get("frame"))
    if raw_frames is None:
        raise MifOpError("'frames' is required - which frames to sample. NOTHING was read.")
    if isinstance(raw_frames, (int, float)):
        raw_frames = [raw_frames]
    if not isinstance(raw_frames, (list, tuple)) or not raw_frames:
        raise MifOpError("'frames' must be a non-empty list of frame numbers, got %r. NOTHING was "
                         "read." % (raw_frames,))
    try:
        frames = [int(f) for f in raw_frames]
    except (TypeError, ValueError) as exc:
        raise MifOpError("every entry in 'frames' must be a number: %s. NOTHING was read." % exc)

    paths = take(params, "dataPaths", "paths", default=None)
    if paths is not None and not isinstance(paths, (list, tuple)):
        raise MifOpError("'dataPaths' must be a list of property paths. NOTHING was read.")
    paths = [str(p) for p in (paths or [])]

    sc = bpy.context.scene
    started_on = sc.frame_current
    samples = []
    try:
        for f in frames:
            sc.frame_set(f)
            # RE-FETCHED EVERY FRAME. The evaluated object is a temporary owned by the depsgraph
            # and is invalidated by the frame change; holding one across frames reads stale data
            # that looks perfectly plausible.
            dg = bpy.context.evaluated_depsgraph_get()
            ev = obj.evaluated_get(dg)
            mw = ev.matrix_world
            row = {
                "frame": f,
                "location": rnd(list(mw.to_translation())),
                "rotationEuler": rnd(list(mw.to_euler())),
                "scale": rnd(list(mw.to_scale())),
            }
            if paths:
                values = {}
                for p in paths:
                    try:
                        v = ev.path_resolve(p)
                    except (ValueError, AttributeError, TypeError) as exc:
                        values[p] = {"error": str(exc)[:120]}
                        continue
                    try:
                        values[p] = rnd(list(v))
                    except TypeError:
                        values[p] = round(float(v), 6) if isinstance(v, (int, float)) else str(v)
                row["values"] = values
            samples.append(row)
    finally:
        sc.frame_set(started_on)

    restored = sc.frame_current
    if restored != started_on:
        raise MifOpError("sampled the frames but the scene was left on frame %d instead of %d. Do "
                         "not trust the scene state - set the frame yourself."
                         % (restored, started_on))

    # DID ANYTHING ACTUALLY MOVE. A caller verifying a constraint, a driver or a bake needs to know
    # the samples DIFFER, and a list of identical matrices is the normal failure - it is what a
    # dead driver, a muted NLA track or a bake that lost its motion all look like.
    locs = [tuple(s["location"]) for s in samples]
    rots = [tuple(s["rotationEuler"]) for s in samples]
    return {
        "object": obj.name,
        "frames": frames,
        "samples": samples,
        "frameRestored": restored == started_on,
        "startedOnFrame": started_on,
        "movedAcrossFrames": len(set(locs)) > 1 or len(set(rots)) > 1,
        "evaluatedNote": ("Read through evaluated_get(depsgraph), so constraints, drivers, NLA, "
                          "parents and simulation caches are all applied. obj.matrix_world on the "
                          "base object shows none of them."),
    }


OPS = {
    "set_keyframe": op_set_keyframe,
    "set_frame_range": op_set_frame_range,
    "list_keyframes": op_list_keyframes,
    "list_animation_data": op_list_animation_data,
    "delete_keyframe": op_delete_keyframe,
    "evaluate_at_frame": op_evaluate_at_frame,
}
