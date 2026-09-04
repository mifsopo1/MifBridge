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

from .ops_common import (MifOpError, get_object, reject_unknown, rnd, select_only,
                         selection_restore, selection_snapshot, take, take_bool, take_float,
                         take_int)

_KEY_KEYS = {
    "object", "name", "frame", "location", "rotation", "scale",
    "dataPath", "path", "value", "index", "target", "interpolation",
}
_RANGE_KEYS = {"start", "end", "fps", "current", "frameStart", "frameEnd",
               "fpsBase", "frameStep", "previewStart", "previewEnd", "usePreviewRange"}
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
            # THE SAME COUNT THE dataPath BRANCH KEEPS. It was fixed there and not here,
            # which left the branch this op's docstring LEADS with - location, rotation,
            # scale, the ordinary way anybody keys anything - reporting no per-call
            # evidence at all. keyframesTotal below cannot stand in for it: it sums every
            # fcurve on the object and its data, so any PRIOR key keeps it non-zero and it
            # cannot fail. A read/write asymmetry inside one function.
            keyed_here = _apply_interpolation(obj, dp, frame, interp)
            written.append({"target": "object", "dataPath": dp,
                            "keysAtThisFrame": keyed_here})
    else:
        owner, why = _resolve_target(obj, path, take(params, "target", default=None, kind=str))
        if "value" not in params:
            raise MifOpError("dataPath needs a `value` - keyframe_insert stores the property's "
                             "CURRENT value, so there is nothing to record without one. NOTHING "
                             "was keyed.")
        value = params.get("value")
        index = take_int(params, "index", default=None)  # take_int, NOT int(): a bad value must REFUSE, not raise ValueError out of the op.
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
    # FPS_BASE IS THE OTHER HALF OF THE FRAME RATE and nothing here could set it. Blender stores
    # 29.97 as fps 30 with fps_base 1.001, and 23.976 as 24 with 1.001 - so every broadcast rate is
    # unreachable through `fps` alone, and a caller asking for 30 on an NTSC scene silently got
    # 29.97 with no way to tell or to change it.
    fps_base = take_float(params, "fpsBase", default=None)
    if fps_base is not None:
        if fps_base <= 0:
            raise MifOpError("fpsBase must be positive, got %g." % fps_base)
        sc.render.fps_base = fps_base
    step = take_float(params, "frameStep", default=None)
    if step is not None:
        if int(step) < 1:
            raise MifOpError("frameStep must be at least 1, got %g." % step)
        sc.frame_step = int(step)

    # PREVIEW RANGE, which overrides the scene range for playback and rendering when it is on. A
    # scene with one enabled renders the preview range and NOT frame_start..frame_end, which is a
    # standing trap for anyone reading only the scene range and wondering why the output is short.
    pv_start = take_float(params, "previewStart", default=None)
    pv_end = take_float(params, "previewEnd", default=None)
    if pv_start is not None or pv_end is not None:
        ws = int(pv_start) if pv_start is not None else sc.frame_preview_start
        we = int(pv_end) if pv_end is not None else sc.frame_preview_end
        if we < ws:
            raise MifOpError("previewEnd (%d) is before previewStart (%d). NOTHING was changed to "
                             "the preview range." % (we, ws))
        sc.frame_preview_start, sc.frame_preview_end = ws, we
        sc.use_preview_range = True
    if "usePreviewRange" in params:
        sc.use_preview_range = take_bool(params, "usePreviewRange", default=True)

    cur = take_float(params, "current", default=None)
    if cur is not None:
        sc.frame_set(int(cur))

    # THE TRUE RATE IS fps / fps_base, and durationSeconds divided by fps alone until 2026-09-03 -
    # so every NTSC rate was reported 0.1% short. Small, and exactly the kind of small that makes a
    # 60-minute programme land a frame and a half out against audio.
    base = float(sc.render.fps_base) or 1.0
    effective_fps = float(sc.render.fps) / base
    span = (sc.frame_end - sc.frame_start + 1)
    return {"before": before,
            "after": {"start": sc.frame_start, "end": sc.frame_end,
                      "fps": sc.render.fps, "current": sc.frame_current},
            "fpsBase": round(base, 6),
            "effectiveFps": round(effective_fps, 6),
            "frameStep": sc.frame_step,
            "usePreviewRange": bool(sc.use_preview_range),
            "previewStart": sc.frame_preview_start,
            "previewEnd": sc.frame_preview_end,
            "durationSeconds": round(span / effective_fps, 6),
            # NAMED, because a preview range silently replaces the scene range at render time and
            # a caller reading only start/end would be describing a different clip than the one
            # Blender will produce.
            "rendersFrames": ([sc.frame_preview_start, sc.frame_preview_end]
                              if sc.use_preview_range else [sc.frame_start, sc.frame_end])}


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
            # SOLO SILENCES EVERY OTHER TRACK while each of them still reports mute:false.
            # Measured on all four builds: two tracks moving a cube to -5 and +5 evaluate to
            # -5 with both live and +5 with the first soloed, and the second track says
            # nothing about it. Reporting mute alone made this op actively misleading -
            # every track unmuted, one playing, no field that could explain it.
            "isSolo": bool(tr.is_solo),
            "lock": bool(tr.lock),
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
    frame = take_float(params, "frame", default=None)  # take_float, NOT float(): same contract - see the note on index above.
    index = take_int(params, "index", default=None)  # take_int, NOT int(): a bad value must REFUSE, not raise ValueError out of the op.

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


def _curves_for(obj, path, index):
    """Fcurves on the object or its data matching a data path, and optionally one array index."""
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


def _enum_ids(rna_type, prop):
    """Blender's own enum for a property, read off the RNA rather than remembered."""
    try:
        return {i.identifier for i in rna_type.bl_rna.properties[prop].enum_items}
    except (KeyError, AttributeError):
        return set()


def op_edit_fcurve(params):
    """Change how a curve moves BETWEEN its keys - interpolation, easing, handles, extrapolation.

    set_keyframe can set an interpolation at INSERT time and reaches three of Blender's thirteen.
    Nothing could change one afterwards, and nothing could touch easing at all - which is most of
    the craft in motion graphics, where the difference between BACK/ease-out and LINEAR is the
    entire look. Retiming or re-feeling an existing animation meant deleting and re-keying it.

    params:
      object (str, required)
      dataPath (alias path, required)
      index (int)              one array element; omitted means every curve on the path
      frame (int)              one keyframe; omitted means every key on the curve
      interpolation (str)      CONSTANT | LINEAR | BEZIER | SINE | QUAD | CUBIC | QUART | QUINT |
                               EXPO | CIRC | BACK | BOUNCE | ELASTIC - validated against this
                               Blender's own enum
      easing (str)             AUTO | EASE_IN | EASE_OUT | EASE_IN_OUT - the DIRECTION an easing
                               interpolation runs, and meaningless without one of the above
      handleType (str)         FREE | ALIGNED | VECTOR | AUTO | AUTO_CLAMPED, both handles
      extrapolation (str)      CONSTANT | LINEAR, on the curve rather than its keys

    Every value is validated before anything is written.
    """
    reject_unknown(params, {"object", "name", "dataPath", "path", "index", "frame",
                            "interpolation", "easing", "handleType", "extrapolation"},
                   "edit_fcurve")
    obj = get_object(take(params, "object", "name", required=True))
    path = take(params, "dataPath", "path", default=None, kind=str)
    if not path:
        raise MifOpError("'dataPath' is required. NOTHING was changed.")
    index = take_int(params, "index", default=None)  # take_int, NOT int(): a bad value must REFUSE, not raise ValueError out of the op.
    frame = take_float(params, "frame", default=None)  # take_float, NOT float(): same contract - see the note on index above.

    wants = {}
    for key, prop, rna in (("interpolation", "interpolation", bpy.types.Keyframe),
                           ("easing", "easing", bpy.types.Keyframe),
                           ("handleType", "handle_left_type", bpy.types.Keyframe),
                           ("extrapolation", "extrapolation", bpy.types.FCurve)):
        raw = take(params, key, default=None, kind=str)
        if raw is None:
            continue
        val = str(raw).upper()
        valid = _enum_ids(rna, prop)
        if valid and val not in valid:
            raise MifOpError("unknown %s '%s'. Valid: %s. NOTHING was changed."
                             % (key, val, ", ".join(sorted(valid))))
        wants[key] = val
    if not wants:
        raise MifOpError("nothing to change - pass at least one of interpolation, easing, "
                         "handleType or extrapolation. NOTHING was changed.")

    curves = _curves_for(obj, path, index)
    if not curves:
        raise MifOpError("no fcurve on '%s' for dataPath '%s'%s - list them with list_keyframes. "
                         "NOTHING was changed."
                         % (obj.name, path, "" if index is None else " at index %s" % index))

    before = sorted({kp.interpolation for fc in curves for kp in fc.keyframe_points})
    touched = 0
    for fc in curves:
        if "extrapolation" in wants:
            fc.extrapolation = wants["extrapolation"]
        for kp in fc.keyframe_points:
            if frame is not None and abs(kp.co[0] - float(frame)) > 1e-6:
                continue
            if "interpolation" in wants:
                kp.interpolation = wants["interpolation"]
            if "easing" in wants:
                kp.easing = wants["easing"]
            if "handleType" in wants:
                kp.handle_left_type = wants["handleType"]
                kp.handle_right_type = wants["handleType"]
            touched += 1
        fc.update()

    if touched == 0 and frame is not None:
        raise MifOpError("'%s' has no keyframe at frame %s, so nothing was edited. NOTHING was "
                         "changed." % (path, frame))
    after = sorted({kp.interpolation for fc in _curves_for(obj, path, index)
                    for kp in fc.keyframe_points})
    return {
        "object": obj.name,
        "dataPath": path,
        "curves": len(curves),
        "keyframesTouched": touched,
        "applied": wants,
        "interpolationBefore": before,
        "interpolationAfter": after,
        "extrapolation": [fc.extrapolation for fc in curves],
    }


def op_add_fcurve_modifier(params):
    """Put a modifier on a curve - most usefully CYCLES, which is how an animation LOOPS.

    There was no way to loop anything. Every turntable, idle, cycling fan and blinking light had
    to be keyed out to its full length by hand, and a two-key rotation could not be made to repeat
    at all. A CYCLES modifier is the one-call answer and Blender has had it forever.

    params:
      object (str, required)
      dataPath (alias path, required)
      index (int)          one array element; omitted means every curve on the path
      type (str)           CYCLES | NOISE | GENERATOR | LIMITS | STEPPED | ENVELOPE | FNGENERATOR
                           - validated against this Blender's own enum. Default CYCLES.
      modeBefore/modeAfter (str)  CYCLES only: NONE | REPEAT | REPEAT_OFFSET | MIRROR
      strength (float)     NOISE only
      scale (float)        NOISE only

    A curve with fewer than two keyframes is REFUSED for CYCLES: there is no cycle to repeat, and
    Blender adds the modifier anyway and does nothing with it, which looks like success.
    """
    reject_unknown(params, {"object", "name", "dataPath", "path", "index", "type",
                            "modeBefore", "modeAfter", "strength", "scale"},
                   "add_fcurve_modifier")
    obj = get_object(take(params, "object", "name", required=True))
    path = take(params, "dataPath", "path", default=None, kind=str)
    if not path:
        raise MifOpError("'dataPath' is required. NOTHING was added.")
    index = take_int(params, "index", default=None)  # take_int, NOT int(): a bad value must REFUSE, not raise ValueError out of the op.

    kind = str(take(params, "type", default="CYCLES", kind=str)).upper()
    valid = _enum_ids(bpy.types.FModifier, "type")
    if valid and kind not in valid:
        raise MifOpError("unknown fcurve modifier type '%s'. Valid: %s. NOTHING was added."
                         % (kind, ", ".join(sorted(valid))))

    modes = _enum_ids(bpy.types.FModifierCycles, "mode_before")
    for key in ("modeBefore", "modeAfter"):
        raw = take(params, key, default=None, kind=str)
        if raw is None:
            continue
        if kind != "CYCLES":
            raise MifOpError("%s applies to a CYCLES modifier and this one is %s. NOTHING was "
                             "added." % (key, kind))
        if modes and str(raw).upper() not in modes:
            raise MifOpError("unknown %s '%s'. Valid: %s. NOTHING was added."
                             % (key, raw, ", ".join(sorted(modes))))

    curves = _curves_for(obj, path, index)
    if not curves:
        raise MifOpError("no fcurve on '%s' for dataPath '%s' - key it first with set_keyframe. "
                         "NOTHING was added." % (obj.name, path))
    if kind == "CYCLES":
        thin = [fc for fc in curves if len(fc.keyframe_points) < 2]
        if thin:
            raise MifOpError(
                "a CYCLES modifier needs at least TWO keyframes to have a cycle to repeat, and %d "
                "of the matching curve(s) have fewer. Blender would add the modifier and do "
                "nothing with it, which looks like success. NOTHING was added." % len(thin))

    before_counts = [len(fc.modifiers) for fc in curves]
    added = []
    for fc in curves:
        mod = fc.modifiers.new(type=kind)
        if kind == "CYCLES":
            mb = take(params, "modeBefore", default=None, kind=str)
            ma = take(params, "modeAfter", default=None, kind=str)
            if mb:
                mod.mode_before = str(mb).upper()
            if ma:
                mod.mode_after = str(ma).upper()
        if kind == "NOISE":
            st = take_float(params, "strength", default=None)
            sc_ = take_float(params, "scale", default=None)
            if st is not None:
                mod.strength = st
            if sc_ is not None:
                mod.scale = sc_
        added.append({"index": fc.array_index,
                      "type": mod.type,
                      "modeBefore": getattr(mod, "mode_before", None),
                      "modeAfter": getattr(mod, "mode_after", None)})
        fc.update()

    after_counts = [len(fc.modifiers) for fc in _curves_for(obj, path, index)]
    return {
        "object": obj.name,
        "dataPath": path,
        "type": kind,
        "curves": len(curves),
        "modifiersAdded": added,
        "modifierCountBefore": before_counts,
        "modifierCountAfter": after_counts,
        # COUNTED OFF THE CURVES. fcurve.modifiers.new returns an object whether or not it stuck,
        # and one modifier per curve is the postcondition - not "the call returned something".
        "countsAgree": all(a == b + 1 for a, b in zip(after_counts, before_counts)),
        "loopNote": ("A CYCLES modifier repeats the keyed range outside it. Sample with "
                     "evaluate_at_frame beyond the last key to prove the loop is live - the "
                     "modifier existing is not the same as it having an effect."),
    }


def _action_curves(act):
    """Fcurves belonging to an ACTION, on any supported Blender.

    _fcurves() above takes a HOLDER and goes through its animation_data, which is the right shape
    for "what is animating this object" and the wrong one for "what is in this action" - an action
    with no user has no holder to reach it through. Same 5.0 slotted-layout problem, one level up:
    Action.fcurves is gone in 5.0 and the curves live under layers/strips/channelbags.
    """
    if hasattr(act, "fcurves"):
        return list(act.fcurves)
    out = []
    for layer in getattr(act, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for cb in getattr(strip, "channelbags", ()):
                out.extend(getattr(cb, "fcurves", ()))
    return out


def _action_row(act):
    curves = _action_curves(act)
    try:
        frame_range = [round(float(v), 4) for v in act.frame_range]
    except (AttributeError, TypeError):
        frame_range = None
    return {
        "name": act.name,
        "users": int(act.users),
        "useFakeUser": bool(act.use_fake_user),
        "curveCount": len(curves),
        "keyframeTotal": sum(len(fc.keyframe_points) for fc in curves),
        "frameRange": frame_range,
        # THE FIELD THAT MATTERS. An action with no users and no fake user is DELETED the next time
        # the file is saved - silently, and by the save succeeding. That is the same purge
        # save_file reports as purgedOrphans, seen from the other side.
        "survivesSave": bool(act.users) or bool(act.use_fake_user),
    }


def op_list_actions(params):
    """Every action in the file, who uses it, and whether it will survive a save.

    params:
      nameContains (str)  optional substring filter, case-insensitive

    An action is also the CLIP NAME glTF and FBX write into an engine, so "whatever Blender
    auto-named it" becomes a name somebody has to live with downstream.
    """
    reject_unknown(params, ("nameContains",), "list_actions")
    sub = take(params, "nameContains", default=None, kind=str)
    rows = []
    for act in bpy.data.actions:
        if sub and sub.lower() not in act.name.lower():
            continue
        rows.append(_action_row(act))
    rows.sort(key=lambda r: r["name"])

    # WHO IS USING WHAT, built by walking objects rather than asked of the action - an action knows
    # its user COUNT and not their names, and "which object is this clip on" is the question.
    by_object = {}
    for obj in bpy.data.objects:
        for label, holder in (("object", obj), ("data", obj.data)):
            ad = getattr(holder, "animation_data", None) if holder is not None else None
            if ad is not None and ad.action is not None:
                by_object.setdefault(ad.action.name, []).append("%s (%s)" % (obj.name, label))
    for row in rows:
        row["usedBy"] = sorted(by_object.get(row["name"], []))

    doomed = [r["name"] for r in rows if not r["survivesSave"]]
    return {
        "count": len(rows),
        "actions": rows,
        "willBeDeletedOnSave": doomed,
        "willBeDeletedOnSaveCount": len(doomed),
    }


def op_create_action(params):
    """Create a named action, optionally assigning it. Naming is the point.

    An object gets whatever Blender auto-named its action - "Action.003" - and that string is what
    glTF and FBX write into the engine as the clip name. Nothing here could set it, so every clip
    exported through this bridge arrived downstream named after nothing.

    params:
      name (str, required)
      object (str)        assign it to this object as well
      fakeUser (bool)     default TRUE. An action with no users and no fake user is deleted on the
                          next save, and a freshly created unassigned one has no users by
                          definition - so the safe default is the one that does not lose work.
    """
    reject_unknown(params, {"name", "object", "fakeUser"}, "create_action")
    name = take(params, "name", default=None, kind=str)
    if not name:
        raise MifOpError("'name' is required - actions are the clip name an engine sees, so an "
                         "auto-generated one is the thing this op exists to avoid. NOTHING was "
                         "created.")
    obj_name = take(params, "object", default=None, kind=str)
    obj = None
    if obj_name:
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            raise MifOpError("no object named '%s' to assign to. NOTHING was created." % obj_name)

    act = bpy.data.actions.new(name=str(name))
    # DEFAULTS TRUE on purpose: a new unassigned action has zero users, so the default that loses
    # the work is the one that does nothing.
    act.use_fake_user = take_bool(params, "fakeUser", default=True)

    assigned = False
    if obj is not None:
        if obj.animation_data is None:
            obj.animation_data_create()
        obj.animation_data.action = act
        assigned = obj.animation_data.action is act

    return {
        "action": act.name,
        "requestedName": str(name),
        # Blender uniquifies silently, and a caller who then looks up the name they asked for finds
        # a DIFFERENT action - or none.
        "nameWasTaken": act.name != str(name),
        "assignedTo": obj.name if assigned else None,
        "assigned": assigned,
        "info": _action_row(act),
    }


def op_assign_action(params):
    """Put an existing action on an object, or clear it. The switch nothing could make.

    An object held one action forever: set_keyframe creates one on first use and nothing could
    swap it, so a second clip on the same rig was impossible.

    params:
      object (str, required)
      action (str)       the action to assign. Omit with clear:true to unlink.
      clear (bool)       unlink the current action instead of assigning one.

    CLEARING IS THE DANGEROUS ONE and it says so: an unlinked action with no fake user drops to
    zero users and is deleted on the next save. The response reports whether the action that was
    unlinked will survive.
    """
    reject_unknown(params, {"object", "name", "action", "clear"}, "assign_action")
    obj = get_object(take(params, "object", "name", required=True))
    clear = take_bool(params, "clear", default=False)
    want = take(params, "action", default=None, kind=str)
    if clear and want:
        raise MifOpError("pass an action to assign OR clear:true, not both. NOTHING was changed.")
    if not clear and not want:
        raise MifOpError("'action' is required unless clear:true. NOTHING was changed.")

    if obj.animation_data is None:
        if clear:
            raise MifOpError("'%s' has no animation data, so there is no action to clear. NOTHING "
                             "was changed." % obj.name)
        obj.animation_data_create()

    previous = obj.animation_data.action
    prev_row = _action_row(previous) if previous is not None else None

    if clear:
        obj.animation_data.action = None
        after = obj.animation_data.action
        if after is not None:
            raise MifOpError("asked to clear the action on '%s' but it still holds '%s'. Do not "
                             "trust this state." % (obj.name, after.name))
        # RE-READ the previous action AFTER unlinking: its user count has changed and that is the
        # number that decides whether it survives the next save.
        return {
            "object": obj.name,
            "cleared": True,
            "previousAction": prev_row["name"] if prev_row else None,
            "previousActionNow": _action_row(previous) if previous is not None else None,
            "previousSurvivesSave": (bool(previous.users) or bool(previous.use_fake_user))
                                    if previous is not None else None,
        }

    act = bpy.data.actions.get(str(want))
    if act is None:
        known = [a.name for a in bpy.data.actions][:20]
        raise MifOpError("no action named '%s'. Present: %s. NOTHING was changed."
                         % (want, ", ".join(known) if known else "<none>"))
    obj.animation_data.action = act
    if obj.animation_data.action is not act:
        raise MifOpError("assigned '%s' to '%s' but it did not take. Do not trust this state."
                         % (want, obj.name))
    return {
        "object": obj.name,
        "action": act.name,
        "previousAction": prev_row["name"] if prev_row else None,
        "previousSurvivesSave": (bool(previous.users) or bool(previous.use_fake_user))
                                if previous is not None else None,
        "info": _action_row(act),
    }


def _sample_world(obj, frames):
    """Evaluated world matrices at frames, as (loc, quat) pairs. The measurement bake_to_keyframes
    is judged by - see its docstring for why a key COUNT is not one."""
    sc = bpy.context.scene
    out = []
    for f in frames:
        sc.frame_set(int(f))
        dg = bpy.context.evaluated_depsgraph_get()
        mw = obj.evaluated_get(dg).matrix_world
        out.append((mw.to_translation().copy(), mw.to_quaternion().copy()))
    return out


def op_bake_to_keyframes(params):
    """Bake evaluated motion into real keyframes, and PROVE the motion survived.

    WHY THIS MATTERS MORE THAN IT SOUNDS. bake_physics bakes POINT CACHES, and no exporter reads
    them - not FBX, not glTF, not Alembic through this addon. So a rigid-body simulation authored
    through this bridge could be rendered here and handed to NOTHING. Constraints and drivers have
    the same problem one step removed: they evaluate correctly in Blender and export as a static
    object, because an exporter writes keyframes and a constraint is not one.

    params:
      object (str, required)
      frameStart / frameEnd (int)   defaults to the scene range
      step (int)                    default 1
      visualKeying (bool)           default TRUE. Off, the bake records the object's OWN transform
                                    and throws away everything a constraint or simulation was
                                    contributing - which is the normal way this goes wrong.
      clearConstraints (bool)       default False. Removes the constraints after baking; without
                                    this they keep evaluating ON TOP of the new keys.
      clearParents (bool)           default False.
      removeRigidBody (bool)        default False. A baked object whose rigid body is still active
                                    is still driven by the sim, and the keys are ignored.

    THE POSTCONDITION IS THE MOTION, NOT THE KEY COUNT. Producing the right NUMBER of keyframes
    while losing the motion is the normal failure when visual keying is off, and a key count cannot
    see it. So the evaluated world matrix is sampled across the range BEFORE the bake, again after,
    and the maximum position and rotation error between them is reported. A bake that kept the keys
    and lost the movement shows up as a large error rather than as a success.
    """
    reject_unknown(params, {"object", "name", "frameStart", "frameEnd", "step", "visualKeying",
                            "clearConstraints", "clearParents", "removeRigidBody"},
                   "bake_to_keyframes")
    obj = get_object(take(params, "object", "name", required=True))
    sc = bpy.context.scene
    f0 = int(take_float(params, "frameStart", default=sc.frame_start))
    f1 = int(take_float(params, "frameEnd", default=sc.frame_end))
    if f1 < f0:
        raise MifOpError("frameEnd (%d) is before frameStart (%d). NOTHING was baked." % (f1, f0))
    step = int(take_float(params, "step", default=1))
    if step < 1:
        raise MifOpError("step must be at least 1, got %d. NOTHING was baked." % step)
    visual = take_bool(params, "visualKeying", default=True)

    # THE PROBE FRAMES, spread across the range rather than taken from one end - a bake that loses
    # the motion often still matches at the first frame, where the object has not moved yet.
    span = f1 - f0
    probes = sorted({f0 + int(round(span * t / 6.0)) for t in range(7)}) if span else [f0]
    started_on = sc.frame_current
    try:
        before = _sample_world(obj, probes)
    finally:
        sc.frame_set(started_on)

    had_constraints = len(obj.constraints)
    had_rigid = getattr(obj, "rigid_body", None) is not None

    snap = selection_snapshot()
    try:
        # A LIST, not a bare object. select_only iterates its argument, so passing the
        # object itself raises "TypeError: 'Object' object is not iterable" - and this was
        # the ONLY one of nine call sites getting it wrong, so bake_to_keyframes had NEVER
        # WORKED on any Blender since it was written. Found 2026-09-03 by
        # blender_version_matrix, and only after a payload fix let the op past its own
        # guards - it had been refusing for a bad argument and never reaching this line.
        select_only([obj])
        try:
            bpy.ops.nla.bake(frame_start=f0, frame_end=f1, step=step,
                             only_selected=True, visual_keying=visual,
                             clear_constraints=take_bool(params, "clearConstraints",
                                                         default=False),
                             clear_parents=take_bool(params, "clearParents", default=False),
                             bake_types={"OBJECT"})
        except RuntimeError as exc:
            raise MifOpError("Blender's bake refused: %s. NOTHING was baked." % exc)
    finally:
        selection_restore(snap)

    if take_bool(params, "removeRigidBody", default=False) and had_rigid:
        # Unlinked from the rigid body world's collection rather than through
        # bpy.ops.rigidbody.object_remove, whose context override differs between versions; the
        # data path is stable and does the same thing.
        try:
            sc.rigidbody_world.collection.objects.unlink(obj)
        except (AttributeError, RuntimeError, ReferenceError, KeyError):
            pass

    curves = _fcurves(obj)
    keys = sum(len(fc.keyframe_points) for fc in curves)
    if keys == 0:
        raise MifOpError("the bake produced NO keyframes on '%s'. Nothing was captured - check the "
                         "frame range and that the object actually moves." % obj.name)

    try:
        after = _sample_world(obj, probes)
    finally:
        sc.frame_set(started_on)

    max_pos = 0.0
    max_rot = 0.0
    for (lb, qb), (la, qa) in zip(before, after):
        max_pos = max(max_pos, (la - lb).length)
        try:
            max_rot = max(max_rot, qb.rotation_difference(qa).angle)
        except (AttributeError, ValueError):
            pass

    return {
        "object": obj.name,
        "frameStart": f0,
        "frameEnd": f1,
        "step": step,
        "visualKeying": visual,
        "curveCount": len(curves),
        "keyframeTotal": keys,
        "probeFrames": probes,
        # THE MEASUREMENT. Small means the baked keys reproduce what the scene was doing; large
        # means the keys exist and the motion is gone, which is what visual_keying=False does and
        # what a key count cannot see.
        "maxPositionError": round(float(max_pos), 6),
        "maxRotationErrorRadians": round(float(max_rot), 6),
        "motionPreserved": max_pos < 1e-3 and max_rot < 1e-3,
        "hadConstraints": had_constraints,
        "hadRigidBody": had_rigid,
        "exportNote": ("This is why the op exists: bake_physics writes POINT CACHES and no "
                       "exporter reads them, and a constraint or driver exports as a static "
                       "object because an exporter writes keyframes and a constraint is not one. "
                       "These are real keyframes and will travel."),
        "stillDrivenNote": ("A source left in place keeps evaluating ON TOP of the new keys - "
                            "constraints unless clearConstraints, and an active rigid body unless "
                            "removeRigidBody. hadConstraints/hadRigidBody say what was there."),
    }


def _marker_row(m):
    return {
        "name": m.name,
        "frame": int(m.frame),
        # THE BOUND CAMERA is the whole reason markers matter beyond being labels: binding one
        # makes the scene CUT to that camera at that frame, which is how a multi-camera edit is
        # actually done in Blender and is invisible from anywhere else in this addon.
        "camera": m.camera.name if getattr(m, "camera", None) else None,
    }


def op_list_markers(params):
    """Every timeline marker, and which camera each one cuts to.

    No parameters. A marker with a bound camera makes the scene switch to it at that frame, so a
    render can use several cameras without anything else in the file recording that - list_cameras
    reports which camera is active NOW, and this reports which one each part of the timeline uses.
    """
    reject_unknown(params, (), "list_markers")
    sc = bpy.context.scene
    rows = sorted((_marker_row(m) for m in sc.timeline_markers), key=lambda r: r["frame"])
    bound = [r for r in rows if r["camera"]]
    return {
        "count": len(rows),
        "markers": rows,
        "boundCameraCount": len(bound),
        # A scene with ANY bound marker cuts between cameras, and scene.camera is then only the
        # camera for frames before the first binding. Worth saying outright.
        "sceneCutsBetweenCameras": len(bound) > 1,
        "sceneCamera": sc.camera.name if sc.camera else None,
    }


def op_set_marker(params):
    """Create, move, rename, camera-bind or delete a timeline marker.

    params:
      name (str, required)   the marker to act on, matched by name
      frame (int)            create it here, or MOVE it here if it already exists
      camera (str)           bind a camera - the scene cuts to it at this marker's frame
      unbindCamera (bool)    clear the binding
      rename (str)           new name
      delete (bool)          remove the marker

    Markers are matched BY NAME, and Blender allows duplicates - two markers can share one. The
    response reports how many matched, because acting on the first of several silently is how a
    caller ends up moving the wrong one.
    """
    reject_unknown(params, {"name", "frame", "camera", "unbindCamera", "rename", "delete"},
                   "set_marker")
    sc = bpy.context.scene
    name = take(params, "name", default=None, kind=str)
    if not name:
        raise MifOpError("'name' is required - which marker. NOTHING was changed.")
    matches = [m for m in sc.timeline_markers if m.name == str(name)]

    if take_bool(params, "delete", default=False):
        if not matches:
            raise MifOpError("no marker named '%s' to delete. NOTHING was changed." % name)
        for m in list(matches):
            sc.timeline_markers.remove(m)
        remaining = [m for m in sc.timeline_markers if m.name == str(name)]
        if remaining:
            raise MifOpError("removed %d marker(s) named '%s' but %d remain. Do not trust this "
                             "state." % (len(matches), name, len(remaining)))
        return {"name": str(name), "deleted": len(matches),
                "markers": len(sc.timeline_markers)}

    frame = take_float(params, "frame", default=None)
    cam_name = take(params, "camera", default=None, kind=str)
    cam = None
    if cam_name:
        cam = bpy.data.objects.get(str(cam_name))
        if cam is None:
            raise MifOpError("no object named '%s' to bind. NOTHING was changed." % cam_name)
        if cam.type != "CAMERA":
            raise MifOpError("'%s' is a %s, not a CAMERA - only a camera can be bound to a "
                             "marker. NOTHING was changed." % (cam_name, cam.type))
    if cam_name and take_bool(params, "unbindCamera", default=False):
        raise MifOpError("pass a camera to bind OR unbindCamera, not both. NOTHING was changed.")

    created = False
    if not matches:
        if frame is None:
            raise MifOpError("no marker named '%s' exists, so 'frame' is required to create one. "
                             "NOTHING was changed." % name)
        m = sc.timeline_markers.new(str(name), frame=int(frame))
        created = True
        matches = [m]
    else:
        if frame is not None:
            for m in matches:
                m.frame = int(frame)

    for m in matches:
        if cam is not None:
            m.camera = cam
        elif take_bool(params, "unbindCamera", default=False):
            m.camera = None
        new_name = take(params, "rename", default=None, kind=str)
        if new_name:
            m.name = str(new_name)

    final_name = matches[0].name
    rows = [_marker_row(m) for m in sc.timeline_markers if m.name == final_name]
    return {
        "name": final_name,
        "created": created,
        # NAMED because Blender permits duplicates and this op acts on ALL of them: a caller who
        # thinks they moved one marker and moved three should be told, not left to discover it.
        "matched": len(matches),
        "markers": rows,
        "totalMarkers": len(sc.timeline_markers),
    }


def _driver_target(obj, path, index):
    """The existing driver fcurve for a path, or None."""
    ad = getattr(obj, "animation_data", None)
    if ad is None:
        return None
    for dr in (getattr(ad, "drivers", None) or []):
        if dr.data_path == path and (index is None or dr.array_index == int(index)):
            return dr
    return None


def op_add_driver(params):
    """Wire a property to an expression, and PROVE the driver actually evaluates.

    DRIVERS ARE THE ONE ANIMATION FEATURE THAT FAILS COMPLETELY SILENTLY. A driver with a broken
    expression, or a variable pointing at an object that no longer exists, stays in place and
    evaluates to ZERO. Nothing errors. Nothing warns. The property simply sits at 0 while every
    field a caller can read - the expression, the variable, the data path - looks perfectly
    correct. Blender shows it purple-then-red in the UI and reports it nowhere else.

    params:
      object (str, required)
      dataPath (alias path, required)   the property to drive
      index (int)                       array element; omitted drives element 0 for a vector
      expression (str)                  default "var" - the driver's SCRIPTED expression
      variables (list)                  [{name, object, dataPath}] - each becomes a driver
                                        variable reading a property off another object

    THE POSTCONDITION IS EVALUATION, not existence. is_valid is checked, and the driven property
    is read back through the depsgraph, because a driver that exists and evaluates to nothing is
    the normal failure and looks identical to a working one from the data.
    """
    reject_unknown(params, {"object", "name", "dataPath", "path", "index", "expression",
                            "variables"}, "add_driver")
    obj = get_object(take(params, "object", "name", required=True))
    path = take(params, "dataPath", "path", default=None, kind=str)
    if not path:
        raise MifOpError("'dataPath' is required - the property to drive. NOTHING was added.")
    index = take_int(params, "index", default=None)  # take_int, NOT int(): a bad value must REFUSE, not raise ValueError out of the op.

    # THE PROPERTY MUST EXIST BEFORE IT CAN BE DRIVEN. Blender will happily create a driver on a
    # path that resolves to nothing and leave it permanently invalid, which is the silent failure
    # this op is arranged to prevent rather than to produce.
    try:
        obj.path_resolve(path)
    except (ValueError, AttributeError, TypeError) as exc:
        raise MifOpError("'%s' does not resolve on '%s' (%s), so a driver on it would be created "
                         "invalid and evaluate to zero forever. NOTHING was added."
                         % (path, obj.name, exc))

    if _driver_target(obj, path, index) is not None:
        raise MifOpError("'%s' already has a driver on '%s'%s. Remove it first - Blender allows a "
                         "second and the result is not what anybody means. NOTHING was added."
                         % (obj.name, path, "" if index is None else "[%s]" % index))

    variables = params.get("variables") or []
    if not isinstance(variables, (list, tuple)):
        raise MifOpError("'variables' must be a list of {name, object, dataPath}. NOTHING was "
                         "added.")
    resolved = []
    for i, v in enumerate(variables):
        if not isinstance(v, dict):
            raise MifOpError("variables[%d] must be an object, got %r. NOTHING was added."
                             % (i, v))
        vname = str(v.get("name") or "var")
        vobj_name = v.get("object")
        vobj = bpy.data.objects.get(str(vobj_name)) if vobj_name else None
        if vobj_name and vobj is None:
            raise MifOpError("variables[%d] targets no object named '%s'. A driver variable "
                             "pointing at nothing evaluates to zero SILENTLY, so this is refused "
                             "rather than created. NOTHING was added." % (i, vobj_name))
        resolved.append((vname, vobj, str(v.get("dataPath") or "location")))

    before = None
    try:
        before = obj.path_resolve(path)
        before = list(before) if hasattr(before, "__len__") else float(before)
    except (TypeError, ValueError):
        before = None

    # POSITIONAL, NOT KEYWORDS. driver_add() takes NO keyword arguments - not on 5.0, and not on
    # 3.6, 4.2 or 4.4 either. This op called it as driver_add(data_path=..., index=...) and had
    # therefore NEVER WORKED ON ANY BLENDER, on any build this addon supports.
    #
    # It shipped and stayed green because the TypeError was caught right here and re-raised as a
    # MifOpError reading "Blender refused to add a driver" - so an API break that had never once
    # succeeded wore the clothes of a legitimate refusal, and every check that asks only "did it
    # refuse politely" agreed with it.
    #
    # Found 2026-09-03 by blender_version_matrix, and only because the reach report made it
    # conspicuous that this op never reached its bpy calls on any version. Verified against all four
    # installs: keywords raise TypeError everywhere, positional returns an FCurve everywhere.
    try:
        fc = obj.driver_add(path) if index is None else obj.driver_add(path, int(index))
    except (RuntimeError, TypeError) as exc:
        raise MifOpError("Blender refused to add a driver on '%s': %s. NOTHING was added."
                         % (path, exc))
    # driver_add returns a LIST for a vector property when no index was given.
    if isinstance(fc, list):
        fc = fc[0]

    drv = fc.driver
    drv.type = "SCRIPTED"
    for vname, vobj, vpath in resolved:
        var = drv.variables.new()
        var.name = vname
        var.type = "SINGLE_PROP"
        if vobj is not None:
            var.targets[0].id = vobj
            var.targets[0].data_path = vpath
    drv.expression = str(take(params, "expression", default="var", kind=str))

    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    try:
        after = ev.path_resolve(path)
        after = list(after) if hasattr(after, "__len__") else float(after)
    except (ValueError, AttributeError, TypeError):
        after = None

    valid = bool(getattr(drv, "is_valid", True))
    return {
        "object": obj.name,
        "dataPath": path,
        "index": index,
        "expression": drv.expression,
        "variables": [{"name": v.name,
                       "target": (v.targets[0].id.name if v.targets and v.targets[0].id
                                  else None),
                       "targetPath": (v.targets[0].data_path if v.targets else None)}
                      for v in drv.variables],
        # THE MEASUREMENT. is_valid is Blender's own verdict, and the evaluated value is the proof.
        # A driver that exists and evaluates to nothing is the NORMAL failure and is identical to a
        # working one from every other field.
        "isValid": valid,
        "valueBefore": before,
        "valueAfterEvaluated": after,
        # NO STATIC WARNING FIELD. The silent-failure explanation belongs in the tool help, where
        # it is read BEFORE the call - a constant string in the response is something no suite can
        # check and no caller can act on differently, which is what
        # audit_blender_consequence_fields objects to and what open_file's discardedNote was
        # removed for earlier today. isValid and evaluates are the measurements; they carry it.
        "evaluates": valid and after is not None,
    }


def op_remove_driver(params):
    """Remove a driver, and report what the property fell back to.

    params:
      object (str, required)
      dataPath (alias path, required)
      index (int)

    A property with its driver removed returns to whatever it was last set to, which is not
    necessarily what it was showing while driven - so the value is read back rather than assumed.
    """
    reject_unknown(params, {"object", "name", "dataPath", "path", "index"}, "remove_driver")
    obj = get_object(take(params, "object", "name", required=True))
    path = take(params, "dataPath", "path", default=None, kind=str)
    if not path:
        raise MifOpError("'dataPath' is required. NOTHING was removed.")
    index = take_int(params, "index", default=None)  # take_int, NOT int(): a bad value must REFUSE, not raise ValueError out of the op.
    if _driver_target(obj, path, index) is None:
        raise MifOpError("no driver on '%s' for '%s'%s - list them with list_animation_data. "
                         "NOTHING was removed."
                         % (obj.name, path, "" if index is None else "[%s]" % index))

    # POSITIONAL for the same reason as driver_add above - driver_remove() takes no keyword
    # arguments either, so the removal half had never worked on any build either.
    try:
        if index is None:
            obj.driver_remove(path)
        else:
            obj.driver_remove(path, int(index))
    except (RuntimeError, TypeError) as exc:
        raise MifOpError("Blender refused to remove the driver on '%s': %s." % (path, exc))

    still = _driver_target(obj, path, index)
    if still is not None:
        raise MifOpError("asked to remove the driver on '%s' but one is still there. Do not trust "
                         "this state." % path)
    bpy.context.view_layer.update()
    try:
        now = obj.path_resolve(path)
        now = list(now) if hasattr(now, "__len__") else float(now)
    except (ValueError, AttributeError, TypeError):
        now = None
    return {
        "object": obj.name,
        "dataPath": path,
        "index": index,
        "removed": True,
        "valueNow": now,
        "fallbackNote": ("The property has returned to its own stored value, which is not "
                         "necessarily what it was showing while driven."),
    }


def op_add_nla_strip(params):
    """Put an action on an NLA track as a strip - how several clips live on one object.

    An object holds ONE active action at a time. The NLA is how a walk, an idle and a wave coexist
    on the same rig and how they blend, and it is what glTF reads to export multiple clips. None of
    it was reachable.

    params:
      object (str, required)
      action (str, required)    the action to place
      track (str)               track name; a new track is created if it does not exist
      start (int)               first frame, default the action's own start
      name (str)                strip name, default the action's name
      blendType (str)           REPLACE | ADD | SUBTRACT | MULTIPLY
      influence (float)         0..1
      pushDownActive (bool)     default FALSE. See below - this is the trap.

    THE ACTIVE ACTION SHADOWS THE NLA. Blender evaluates animation_data.action ON TOP of the whole
    NLA stack, so an object with an active action set plays THAT and the strips appear to do
    nothing. This op detects it and says so rather than leaving a stack that looks correct and is
    inert; pushDownActive moves the active action onto its own track first, which is what the UI's
    Push Down button does.
    """
    reject_unknown(params, {"object", "name", "action", "track", "start", "stripName",
                            "blendType", "influence", "pushDownActive"}, "add_nla_strip")
    obj = get_object(take(params, "object", "name", required=True))
    act_name = take(params, "action", default=None, kind=str)
    if not act_name:
        raise MifOpError("'action' is required - which action to place. NOTHING was added.")
    act = bpy.data.actions.get(str(act_name))
    if act is None:
        known = [a.name for a in bpy.data.actions][:20]
        raise MifOpError("no action named '%s'. Present: %s. NOTHING was added."
                         % (act_name, ", ".join(known) if known else "<none>"))

    blend = take(params, "blendType", default=None, kind=str)
    if blend:
        blend = str(blend).upper()
        valid = _enum_ids(bpy.types.NlaStrip, "blend_type")
        if valid and blend not in valid:
            raise MifOpError("unknown blendType '%s'. Valid: %s. NOTHING was added."
                             % (blend, ", ".join(sorted(valid))))
    influence = take_float(params, "influence", default=None)
    if influence is not None and not (0.0 <= influence <= 1.0):
        raise MifOpError("influence must be between 0 and 1, got %g. NOTHING was added."
                         % influence)

    if obj.animation_data is None:
        obj.animation_data_create()
    ad = obj.animation_data

    pushed = None
    if ad.action is not None and take_bool(params, "pushDownActive", default=False):
        pushed = ad.action.name
        tr = ad.nla_tracks.new()
        tr.name = "%s_pushed" % pushed
        try:
            tr.strips.new(pushed, int(ad.action.frame_range[0]), ad.action)
        except RuntimeError as exc:
            raise MifOpError("could not push the active action '%s' down onto a track: %s. "
                             "NOTHING was added." % (pushed, exc))
        ad.action = None

    track_name = take(params, "track", default=None, kind=str)
    track = None
    if track_name:
        track = ad.nla_tracks.get(str(track_name))
    if track is None:
        track = ad.nla_tracks.new()
        if track_name:
            track.name = str(track_name)

    start = take_float(params, "start", default=None)
    start = int(start) if start is not None else int(act.frame_range[0])
    strip_name = take(params, "stripName", default=None, kind=str) or act.name
    try:
        strip = track.strips.new(str(strip_name), start, act)
    except RuntimeError as exc:
        raise MifOpError("Blender refused to place '%s' at frame %d on track '%s': %s. A strip "
                         "cannot overlap another on the same track. NOTHING was added."
                         % (act.name, start, track.name, exc))
    if blend:
        strip.blend_type = blend
    if influence is not None:
        strip.influence = influence
        strip.use_animated_influence = False

    # THE TRAP, REPORTED. animation_data.action is evaluated ON TOP of the entire NLA stack, so a
    # remaining active action makes every strip below it inert while the stack reads as correct.
    shadowed = ad.action is not None
    return {
        "object": obj.name,
        "track": track.name,
        "strip": {
            "name": strip.name,
            "action": strip.action.name if strip.action else None,
            "frameStart": round(float(strip.frame_start), 4),
            "frameEnd": round(float(strip.frame_end), 4),
            "blendType": strip.blend_type,
            "influence": round(float(strip.influence), 6),
        },
        "trackCount": len(ad.nla_tracks),
        "stripCount": sum(len(t.strips) for t in ad.nla_tracks),
        "pushedDownActive": pushed,
        "activeActionShadowsNla": shadowed,
        # No static explanation field. activeActionShadowsNla is the measurement and carries the
        # answer; the reason it matters belongs in the tool help, read BEFORE the call. Two such
        # notes were removed elsewhere today for the same reason - a constant string is something
        # no suite can check and no caller can act on differently.
        "activeAction": ad.action.name if ad.action else None,
    }


def op_move_keyframes(params):
    """Retime keyframes - shift them, or scale the timing about a pivot.

    Retiming is a core animation operation and nothing here could do it. delete_keyframe and
    set_keyframe together can only rebuild an animation from scratch, which loses every handle,
    interpolation and easing on the way - so "make this 20% slower" or "push everything after
    frame 50 back ten frames" meant re-authoring it.

    params:
      object (str, required)
      dataPath (alias path)     limit to one channel; omitted moves EVERY curve on the object
      index (int)               limit to one array element
      offset (float)            frames to shift by. Negative moves earlier.
      scale (float)             multiply the timing about `pivot`. 2.0 is half speed.
      pivot (float)             the frame that stays put under scale, default the first key
      frameStart / frameEnd     only touch keys inside this range

    offset and scale are refused TOGETHER: applying both leaves the order ambiguous, and a caller
    who wants each can ask twice and see each result.

    THE HANDLES MOVE WITH THE KEYS. A bezier handle is stored in absolute frame coordinates, so
    moving only co leaves the handles behind and silently reshapes every curve it touches.
    """
    reject_unknown(params, {"object", "name", "dataPath", "path", "index", "offset", "scale",
                            "pivot", "frameStart", "frameEnd"}, "move_keyframes")
    obj = get_object(take(params, "object", "name", required=True))
    path = take(params, "dataPath", "path", default=None, kind=str)
    index = take_int(params, "index", default=None)  # take_int, NOT int(): a bad value must REFUSE, not raise ValueError out of the op.

    offset = take_float(params, "offset", default=None)
    scale = take_float(params, "scale", default=None)
    if offset is not None and scale is not None:
        raise MifOpError("pass offset OR scale, not both - applying both leaves the order "
                         "ambiguous. Call twice if you want each. NOTHING was moved.")
    if offset is None and scale is None:
        raise MifOpError("pass offset (frames to shift) or scale (a timing multiplier). NOTHING "
                         "was moved.")
    if scale is not None and scale <= 0:
        raise MifOpError("scale must be positive, got %g - a zero or negative multiplier collapses "
                         "or reverses the timing and is never what is meant. NOTHING was moved."
                         % scale)

    lo = take_float(params, "frameStart", default=None)
    hi = take_float(params, "frameEnd", default=None)
    if lo is not None and hi is not None and hi < lo:
        raise MifOpError("frameEnd (%g) is before frameStart (%g). NOTHING was moved." % (hi, lo))

    curves = _curves_for(obj, path, index) if path else [
        fc for holder in (obj, obj.data if obj.data is not None else None) if holder is not None
        for fc in _fcurves(holder)
        if index is None or fc.array_index == int(index)]
    if not curves:
        raise MifOpError("no fcurve on '%s'%s - nothing to retime. List them with list_keyframes. "
                         "NOTHING was moved."
                         % (obj.name, " for dataPath '%s'" % path if path else ""))

    selected = []
    for fc in curves:
        for kp in fc.keyframe_points:
            f = kp.co[0]
            if lo is not None and f < lo:
                continue
            if hi is not None and f > hi:
                continue
            selected.append((fc, kp))
    if not selected:
        raise MifOpError("no keyframe on '%s' falls inside %s. NOTHING was moved."
                         % (obj.name,
                            "frames %s..%s" % (lo if lo is not None else "-inf",
                                               hi if hi is not None else "+inf")))

    pivot = take_float(params, "pivot", default=None)
    if pivot is None:
        pivot = min(kp.co[0] for _fc, kp in selected)

    before = sorted({round(float(kp.co[0]), 4) for _fc, kp in selected})

    def new_frame(f):
        return f + offset if offset is not None else pivot + (f - pivot) * scale

    # MOVED IN THE DIRECTION OF TRAVEL. Blender keeps keyframe_points sorted by frame, and shifting
    # a key past its neighbour while iterating forwards makes the walk skip or revisit keys. Moving
    # later-first when going forwards, and earlier-first when going backwards, means a key never
    # crosses one that has not moved yet.
    forwards = (offset is not None and offset > 0) or (scale is not None and scale > 1.0)
    ordered = sorted(selected, key=lambda pair: pair[1].co[0], reverse=forwards)
    for fc, kp in ordered:
        f0 = kp.co[0]
        f1 = new_frame(f0)
        delta = f1 - f0
        kp.co[0] = f1
        # HANDLES ARE ABSOLUTE FRAME COORDINATES. Moving only co leaves them behind and reshapes
        # the curve silently - the interpolation still reads BEZIER and the motion is different.
        try:
            kp.handle_left[0] += delta
            kp.handle_right[0] += delta
        except (AttributeError, TypeError):
            pass
    for fc in curves:
        fc.update()

    after = sorted({round(float(kp.co[0]), 4) for _fc, kp in selected})
    return {
        "object": obj.name,
        "dataPath": path,
        "curves": len(curves),
        "keyframesMoved": len(selected),
        "offset": offset,
        "scale": scale,
        "pivot": round(float(pivot), 4),
        "framesBefore": before[:40],
        "framesAfter": after[:40],
        # MEASURED. A retime that produced the same frame list did nothing, whatever the parameters
        # said - which is what an offset of 0 or a scale of 1 looks like, and what a range that
        # matched nothing would look like if it were not refused above.
        "framesChanged": before != after,
        "handlesMoved": True,
    }



_SETACTION_KEYS = {"action", "name", "fakeUser", "rename"}


def op_set_action(params):
    """Protect an action from being destroyed by the next save, or rename it.

    THE SHARPEST ASYMMETRY IN THIS FILE. _action_row computes survivesSave and its own comment says
    what it means: "An action with no users and no fake user is DELETED the next time the file is
    saved - silently, and by the save succeeding." list_actions reports it for every action, and
    assign_action reports previousSurvivesSave for the action it just displaced - which is precisely
    the moment one drops to zero users.

    So the addon told you your work was about to be destroyed, in two places, and there was nothing
    you could do about it: use_fake_user was writable only by create_action, at birth. An action
    imported with a mesh, or displaced by assign_action, could not be protected at all.

    A FAKE USER IS THE WHOLE MECHANISM. Blender purges datablocks with no users on save; a fake user
    is a deliberate reference that says "keep this even though nothing points at it". It is the same
    purge save_file reports as purgedOrphans, seen from the side that can prevent it.

    params:
      action / name (str)   which action. Required.
      fakeUser (bool)       keep it through a save even with no users
      rename (str)          a new name. Blender suffixes a clash rather than refusing, so the name
                            you get is reported and may not be the one you asked for.
    """
    reject_unknown(params, _SETACTION_KEYS, "set_action")
    want = take(params, "action", "name", required=True, kind=str)
    act = bpy.data.actions.get(str(want))
    if act is None:
        have = sorted(a.name for a in bpy.data.actions)[:25]
        raise MifOpError("no action named '%s'. This file has: %s. NOTHING was changed."
                         % (want, ", ".join(have) if have else "(none)"))

    if params.get("fakeUser") is None and params.get("rename") is None:
        raise MifOpError("nothing to do - pass fakeUser, rename, or both. NOTHING was changed.")

    before = _action_row(act)
    if params.get("fakeUser") is not None:
        act.use_fake_user = take_bool(params, "fakeUser", default=True)

    renamed_to = None
    new_name = take(params, "rename", default=None, kind=str)
    if new_name is not None:
        asked = str(new_name)
        if len(asked) > 63:
            raise MifOpError("the name is %d characters and Blender truncates at 63, so the action "
                             "you get would not be the one you named. The fake-user change, if any, "
                             "stands." % len(asked))
        act.name = asked
        renamed_to = act.name

    after = _action_row(act)
    return {
        "ok": True,
        "action": act.name,
        "before": before,
        "after": after,
        "renamedTo": renamed_to,
        # BLENDER SUFFIXES A CLASHING NAME rather than refusing, so anything looking this up by the
        # name it asked for would find the wrong action - or nothing.
        "nameWasSuffixed": bool(renamed_to and renamed_to != str(new_name)),
        "changedFields": sorted(k for k in set(before) | set(after)
                                if before.get(k) != after.get(k)),
        "note": ("this action has no users and no fake user, so the next save DELETES it - silently, "
                 "and by the save succeeding. Pass fakeUser:true to keep it."
                 if not after["survivesSave"] else
                 ("it had no users and would have been purged by the next save; the fake user now "
                  "keeps it." if not before["survivesSave"] else None)),
    }


_NLATRACK_KEYS = {"object", "name", "track", "mute", "solo", "lock", "rename"}


def op_set_nla_track(params):
    """Mute, solo, lock or rename an NLA track - none of which was possible.

    list_keyframes has always reported a track's mute and nothing could write it, so a track could
    be built and never silenced. That is the small half.

    THE LARGE HALF IS SOLO, AND THE READ SIDE WAS MISLEADING ABOUT IT. Setting is_solo on one track
    silences every OTHER track's contribution while each of them still reports mute:False and
    is_solo:False. Measured on 3.6.23, 4.2.17, 4.4.0 and 5.0.1: two tracks moving a cube to z=-5 and
    z=+5, evaluated at frame 10, give -5 with both live and +5 with the first soloed - the second
    track's contribution is gone and nothing about the second track says so.

    So a caller reading every track, seeing every one unmuted, and asking why only one plays had no
    field that could answer. list_keyframes now reports isSolo per track and names the soloing track
    when one exists.

    SOLO IS EXCLUSIVE, and Blender enforces that itself: setting is_solo on a second track clears it
    on the first. The response reports which track ended up soloed rather than assuming it is this
    one.

    params:
      object / name (str)   required
      track (str)           which NLA track. Required.
      mute (bool)           silence this track
      solo (bool)           silence every OTHER track
      lock (bool)           protect it from editing
      rename (str)          a new name
    """
    reject_unknown(params, _NLATRACK_KEYS, "set_nla_track")
    obj = get_object(take(params, "object", "name", required=True, kind=str))
    ad = getattr(obj, "animation_data", None)
    tracks = list(getattr(ad, "nla_tracks", None) or []) if ad is not None else []
    if not tracks:
        raise MifOpError("'%s' has no NLA tracks. add_nla_strip makes one - it takes a `track` name "
                         "and creates it if absent. NOTHING was changed." % obj.name)

    want = take(params, "track", required=True, kind=str)
    track = None
    for tr in tracks:
        if tr.name == str(want):
            track = tr
            break
    if track is None:
        raise MifOpError("'%s' has no NLA track named '%s'. It has: %s. NOTHING was changed."
                         % (obj.name, want, ", ".join(tr.name for tr in tracks)))

    asked = [k for k in ("mute", "solo", "lock", "rename") if params.get(k) is not None]
    if not asked:
        raise MifOpError("nothing to do - pass mute, solo, lock or rename. NOTHING was changed.")

    before = {"mute": bool(track.mute), "isSolo": bool(track.is_solo), "lock": bool(track.lock),
              "name": track.name}
    if params.get("mute") is not None:
        track.mute = take_bool(params, "mute", default=True)
    if params.get("lock") is not None:
        track.lock = take_bool(params, "lock", default=True)
    if params.get("solo") is not None:
        track.is_solo = take_bool(params, "solo", default=True)
    renamed_to = None
    new_name = take(params, "rename", default=None, kind=str)
    if new_name is not None:
        track.name = str(new_name)
        renamed_to = track.name

    # READ BACK ACROSS EVERY TRACK, not just this one. Solo is exclusive and Blender clears it on
    # the others itself, so "which track is soloed" is a property of the stack rather than of the
    # track that was written.
    soloed = [tr.name for tr in tracks if tr.is_solo]
    after = {"mute": bool(track.mute), "isSolo": bool(track.is_solo), "lock": bool(track.lock),
             "name": track.name}
    silenced = [tr.name for tr in tracks
                if not tr.is_solo and not tr.mute and soloed and tr.name not in soloed]
    return {
        "ok": True,
        "object": obj.name,
        "track": track.name,
        "before": before,
        "after": after,
        "renamedTo": renamed_to,
        "changedFields": sorted(k for k in before if before[k] != after[k]),
        "soloedTrack": soloed[0] if soloed else None,
        # THE FIELD THE READ SIDE COULD NOT ANSWER. These tracks are NOT muted and contribute
        # NOTHING, because another track is soloed - and every one of them reports mute:False.
        "silencedBySolo": silenced or None,
        "trackCount": len(tracks),
        "note": ("'%s' is soloed, so %s contribute NOTHING while still reporting mute:false. That "
                 "is what solo does and it is invisible on the tracks it silences."
                 % (soloed[0], ", ".join(silenced))) if silenced else
                ("this track is muted - its strips still exist and do nothing."
                 if after["mute"] else None),
    }

OPS = {
    "move_keyframes": op_move_keyframes,
    "add_nla_strip": op_add_nla_strip,
    "set_nla_track": op_set_nla_track,
    "add_driver": op_add_driver,
    "remove_driver": op_remove_driver,
    "list_markers": op_list_markers,
    "set_marker": op_set_marker,
    "bake_to_keyframes": op_bake_to_keyframes,
    "set_keyframe": op_set_keyframe,
    "set_frame_range": op_set_frame_range,
    "list_keyframes": op_list_keyframes,
    "list_animation_data": op_list_animation_data,
    "delete_keyframe": op_delete_keyframe,
    "evaluate_at_frame": op_evaluate_at_frame,
    "edit_fcurve": op_edit_fcurve,
    "add_fcurve_modifier": op_add_fcurve_modifier,
    "list_actions": op_list_actions,
    "create_action": op_create_action,
    "set_action": op_set_action,
    "assign_action": op_assign_action,
}
