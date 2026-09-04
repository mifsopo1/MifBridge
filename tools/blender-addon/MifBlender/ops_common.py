"""Shared helpers for MifBlender ops.

Everything in here runs on the MAIN thread (ops are only ever called from
server._execute, which is only ever called from the drain timer or inline in
background mode). bpy calls are therefore legal below this line.
"""

from __future__ import annotations

import math
import os

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

    APPLIED TO EVERY RESPONSE from 2026-09-04, not just to the `result` key. server._execute used to
    call this only on the rare non-dict path, so an op returning a dict - nearly all of them - went
    to json.dumps untouched. A NaN anywhere in one then reached the wire as bare `NaN`, which is not
    valid JSON: Python's json.loads accepts it and a strict parser rejects the entire frame.

    THE DEPTH CAP GUARDS AGAINST CYCLES, NOT AGAINST NESTING, and at 8 it was about to start
    truncating real answers now that every response passes through here - a repr() where the caller
    expected structure. Raised to 24: deep enough that no node tree, interface or modifier stack
    reaches it, still shallow enough that a cyclic structure dies quickly rather than eating the
    stack. The deepest structure this repo records is 4.
    """
    if _depth > 24:
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
    # TWO SPELLINGS OF ONE PARAMETER, TWO DIFFERENT VALUES, is a caller who believes something
    # untrue - not a preference to be resolved by argument order. Found on 2026-09-04 by asking
    # add_modifier for a modifier named MifSub and getting Subsurf: there `name` aliases the
    # OBJECT and `modifier` names the modifier, so "MifSub" was read as an object name, lost the
    # tie to "object", and was dropped in silence. The reasoning is take_bool's, thirty lines
    # below - a word it does not know is a typo, not a false, because "a false is a decision; a
    # typo is not".
    #
    # EQUAL VALUES PASS. Sending both spellings on purpose is exactly the use this function's
    # docstring says aliases exist for, and refusing it would punish the careful caller.
    present = [n for n in names if n in params and params[n] is not None]
    if len(present) > 1:
        first = params[present[0]]
        clashing = [n for n in present[1:] if params[n] != first]
        if clashing:
            # NO PROMISE ABOUT STATE. take() is a shared reader with hundreds of call sites and no
            # idea whether one of them has already written; a "NOTHING was changed" here is the
            # defect that took audit_mutate_then_deny from 0 findings to 103.
            raise MifOpError(
                "'%s' and '%s' are two names for the same parameter and were given different "
                "values (%r and %r). Send one, or send the same value for both - this op cannot "
                "tell which you meant."
                % (present[0], clashing[0], first, params[clashing[0]]))

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


_TRUE_WORDS = ("1", "true", "yes", "on")
_FALSE_WORDS = ("0", "false", "no", "off")


def take_bool(params, *names, default=False):
    value = take(params, *names, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        # A WORD THIS DOES NOT KNOW IS A TYPO, NOT A FALSE. The test used to be
        # `word in _TRUE_WORDS`, so everything else - "ture", "flase", an empty string, a NUL -
        # came back False and the op carried on as though the caller had asked for it. Found by
        # sending sentinels to the parameters no payload covers: makeActive took a garbage string
        # and quietly meant "no". A false is a decision; a typo is not.
        if word in _FALSE_WORDS:
            return False
        raise MifOpError("'%s' must be a boolean - got %r. Accepted as text: %s for true, %s for "
                         "false. An unrecognised word is a typo rather than a no, so it is refused "
                         "instead of quietly meaning false."
                         % (names[0], value, "/".join(_TRUE_WORDS), "/".join(_FALSE_WORDS)))
    raise MifOpError("'%s' must be a boolean" % names[0])


# A SHARED READER MUST NOT PROMISE ABOUT STATE. These helpers refuse a bad value and say what is
# wrong with it - they do NOT end "NOTHING was changed", because they have no idea what their caller
# has already done. The same conclusion _socket_value reached earlier on 2026-09-04, and the reason
# it takes its tail from the caller: when take_float briefly did make that claim,
# audit_mutate_then_deny went from 0 findings to 103, every one of them an op that reads a parameter
# after a write - which is only a lie if the READER is the thing making the promise.


def take_float(params, *names, default=None, required=False):
    value = take(params, *names, default=default, required=required)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MifOpError("'%s' must be a number, got %r" % (names[0], value))
    # NaN AND INFINITY ARE FLOATS, and every guard in this addon let them through. Python's json
    # module PARSES NaN, Infinity and -Infinity by default, so a caller can send one over the bridge
    # and float() is perfectly happy with it. Blender is too: a NaN location is accepted, reads back
    # as nan, and poisons everything that touches the object's bounds - the viewport frames nothing,
    # physics goes unstable, an exporter writes nan into the file - while every field in the
    # response agrees the call worked. There is no request a non-finite number is the answer to.
    if not math.isfinite(number):
        raise MifOpError("'%s' must be a finite number, got %r. NaN and Infinity are accepted by "
                         "Blender and poison everything that reads the object's bounds afterwards, "
                         "silently." % (names[0], value))
    return number


# Blender's integer RNA properties are 32-bit signed. Anything outside this cannot be stored, and
# what happened instead was a raw ValueError out of the assignment - see take_int.
INT32_MIN = -2147483648
INT32_MAX = 2147483647


def take_int(params, *names, default=None, required=False):
    value = take(params, *names, default=default, required=required)
    if value is None:
        return None
    try:
        number = int(value)
    except OverflowError:
        # int(float("inf")) raises OverflowError, which the old clause did not catch, so an infinite
        # frame number came back as a raw exception rather than a sentence.
        raise MifOpError("'%s' must be a finite integer, got %r." % (names[0], value))
    except (TypeError, ValueError):
        raise MifOpError("'%s' must be an integer, got %r" % (names[0], value))
    # RANGE-CHECKED, because Blender's int properties are 32-bit and the failure was ugly. Measured
    # on 5.0.1: set_frame_range{start: 2**40} and set_render_settings{resolutionX: 2**40} both came
    # back as a raw ValueError from deep inside the assignment - "bpy_struct: item.attr = val:
    # Scene.frame_start expected an int type" - escaping this addon's refusal contract, where every
    # other refusal is a sentence naming the fix.
    if not (INT32_MIN <= number <= INT32_MAX):
        raise MifOpError("'%s' must be between %d and %d - Blender stores integers in 32 bits and "
                         "cannot hold %r." % (names[0], INT32_MIN, INT32_MAX, value))
    return number



def finite_float(value, key):
    """One caller-supplied value as a finite float, or a refusal naming it.

    The scalar twin of finite_floats, for the conversion sites that never see take_float: a node
    socket default, a material input, a keyframe value, a UV coordinate. Each of those takes a value
    out of the caller's dict and calls float() on it, and float("nan") succeeds.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MifOpError("'%s' must be a number, got %r." % (key, value))
    if not math.isfinite(number):
        raise MifOpError("'%s' must be a finite number, got %r. NaN and Infinity are accepted by "
                         "Blender and poison everything that reads it afterwards, silently."
                         % (key, value))
    return number


def finite_int(value, key):
    """One caller-supplied value as an int Blender can actually store, or a refusal naming it.

    The int twin of finite_float, for the conversion sites that never see take_int - create_primitive
    puts these straight into a bpy.ops operator's properties, and the operator converts them itself.
    segments: 2**40 came back as a raw ValueError from inside primitive_uv_sphere_add.
    """
    try:
        number = int(value)
    except OverflowError:
        raise MifOpError("'%s' must be a finite integer, got %r." % (key, value))
    except (TypeError, ValueError):
        raise MifOpError("'%s' must be an integer, got %r." % (key, value))
    if not (INT32_MIN <= number <= INT32_MAX):
        raise MifOpError("'%s' must be between %d and %d - Blender stores integers in 32 bits and "
                         "cannot hold %r." % (key, INT32_MIN, INT32_MAX, value))
    return number


def finite_floats(values, key):
    """Every element as a finite float, or a refusal naming the one that is not.

    THE VECTOR PARSERS BYPASSED take_float. They convert with a bare float(x), so the finiteness
    check added there did nothing for `location: [NaN, 0, 0]` - which Blender accepts, reads back as
    nan, and which poisons every later read of the object's bounds while the response agrees the
    call worked.

    Shared rather than repeated in each parser: the four differ legitimately (2D, 3D, defaults,
    refusal verb) and the VALIDATION is the part that was identical, exactly as check_axis_dict was.
    """
    out = []
    for i, raw in enumerate(values):
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise MifOpError("'%s'[%d] must be a number, got %r." % (key, i, raw))
        if not math.isfinite(number):
            raise MifOpError(
                "'%s'[%d] must be a finite number, got %r. NaN and Infinity are accepted by Blender "
                "and poison everything that reads the object's bounds afterwards, silently."
                % (key, i, raw))
        out.append(number)
    return out


def check_axis_dict(value, key, axes, tail="NOTHING was changed."):
    """A {x,y,z}-style dict names at least one real axis and nothing else, or it is refused.

    WHY THIS IS SHARED AND THE PARSERS ARE NOT. Six places in this addon read a vector from a dict
    and they legitimately differ - two axes for a node location, four for a quaternion, list or
    tuple returns, defaults that are sometimes zero and sometimes the object's current value.
    Forcing those into one function needs five parameters and is worse than the duplication.

    The VALIDATION is identical in all six, and on 2026-09-04 the same defect was in all six: a dict
    read with .get(axis, default) turned {"mif":"typo"} into the DEFAULT vector - the origin, or
    wherever the object already was - and reported success. In set_bone_pose a mistyped quaternion
    became a ZERO quaternion, which is not a rotation at all.

    A PARTIAL DICT STAYS LEGAL. {"z": 2} is a useful thing to write and the other axes keep their
    defaults; a dict that names NONE of them is a typo rather than a request.
    """
    unknown = sorted(set(value) - set(axes))
    if unknown or not (set(value) & set(axes)):
        raise MifOpError(
            "'%s' as an object takes %s - got %r. %s %s"
            % (key, "/".join(axes), value,
               ("Unrecognised: %s." % ", ".join(unknown)) if unknown
               else "It names none of them.", tail))

def check_output_path(raw, resolved, verb="written"):
    """Refuse an output path Blender cannot write, BEFORE the work that depends on it runs.

    Blender does not tell you until afterwards. A path containing a NUL or a control character
    collapses on the way to the filesystem: the render or bake or export RUNS, and then the save
    fails with a bare RuntimeError carrying a Python traceback - a raw exception escaping the op's
    contract, where every other refusal in this addon is a sentence.

    And where the format can be written to a relative path it does not fail at all. render_still
    silently produced a file called ".exr" - the extension alone - in the process's working
    directory, on every run of the version matrix, and it was noticed by a stray line in
    `git status` rather than by any check.

    SHARED, because the rule is identical at every op that writes a file and the failure is not
    local to any of them: set_render_settings STORES a path that render_still USES, so validating
    only at the point of use let one endpoint accept an unusable value, answer ok, and hand the
    failure to a different endpoint against a caller already told it worked.
    """
    if any(ord(ch) < 32 for ch in resolved):
        raise MifOpError("the output path contains a control character, which collapses to nothing "
                         "on the way to the filesystem - Blender does the work first and then fails "
                         "to save, or silently writes a file named after the extension alone in the "
                         "working directory. Got %r. NOTHING was %s." % (raw or resolved, verb))
    if not os.path.basename(resolved.rstrip("/\\")):
        raise MifOpError("the output path '%s' names a directory, not a file - Blender would write "
                         "a file called after the format's extension alone. Pass a full path "
                         "including a file name. NOTHING was %s." % (raw or resolved, verb))


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


def shared_data_note(obj):
    """A response fragment naming the OTHER objects an edit to this mesh also changed.

    Alt+D makes a linked duplicate: two objects, one mesh datablock. It is how anyone lays out
    repeated geometry - a row of crates, a fence, a set of windows - so a real scene is full of
    them, and editing the mesh through one object changes every object that shares it.

    Measured on 5.0.1: clean_mesh and set_shading each changed a second object, and nothing in
    either response mentioned it - no field, no phrase.

    REPORTED, NOT REFUSED, and the difference from apply_transform is deliberate. That op refuses
    outright because applying a transform MOVES the other objects, which is never what was asked
    for - its message says "one of which you did not ask about". Editing the shared mesh is the
    opposite case: changing one crate to change all of them is the entire point of a linked
    duplicate, so refusing would remove the feature. What was missing is the caller knowing.
    """
    data = getattr(obj, "data", None)
    users = getattr(data, "users", 1) or 1
    if users <= 1:
        return {}
    others = sorted(o.name for o in bpy.data.objects
                    if o.data is data and o.name != obj.name)
    return {
        "meshSharedWith": others,
        "alsoChangedCount": len(others),
        "sharedNote": ("'%s' shares its mesh data with %d other object(s) - %s - so this edit "
                       "changed them too. That is what a linked duplicate is for; it is reported "
                       "because nothing else would tell you."
                       % (obj.name, len(others), ", ".join(others[:6]) or "(unnamed)")),
    }


def require_editable(obj, what="changed"):
    """Refuse a write to a LIBRARY-LINKED datablock, which Blender can never save.

    Linked data belongs to another .blend and the local file may not modify it. Blender's RNA does
    not always stop you: measured on 5.0.1, transform_object moved a linked cube, reported the new
    location as fact, and the move is unsaveable and gone on the next reload. set_shading was worse
    in one way - it changed nothing at all and still answered ok.

    A LIBRARY OVERRIDE IS NOT LINKED DATA for this purpose. An override exists precisely so the
    local file can change selected properties, so obj.override_library being set means the write is
    legitimate. Checking only `.library` would refuse the case overrides were invented for.

    Nothing in this addon mentioned .library before 2026-09-04 - not once - and linking is how
    production files are assembled, so an agent driving a real scene meets this immediately.
    """
    lib = getattr(obj, "library", None)
    if lib is None or getattr(obj, "override_library", None) is not None:
        return
    raise MifOpError(
        "'%s' is LINKED from %s, so this file cannot %s it - Blender would either drop the change "
        "silently or lose it on the next reload. Make a library override for it, or open %s and "
        "edit it there. NOTHING was changed."
        % (getattr(obj, "name", "?"), getattr(lib, "filepath", "another .blend"), what,
           getattr(lib, "filepath", "the source file")))


def edit_mode_stale(obj):
    """A response fragment warning that this object's mesh data is not what the caller can see.

    WHY A READ NEEDS THIS AND A WRITE DOES NOT. A write in edit mode is refused outright by
    get_object(want_mesh=True), because the live geometry lives in the edit BMesh and anything
    written to mesh.polygons is discarded on the way out. A READ has the mirror problem and no
    guard: mesh.polygons still holds the state from the last time OBJECT mode was entered, so the
    answer is confidently wrong rather than lost.

    Measured on 5.0.1: delete a face in edit mode and face_info still reports 6 of them while the
    live mesh has 5. Nothing in the response said which number it was.

    NOT REFUSED, unlike the write path, and the difference is deliberate. This addon drives a LIVE
    editor where a person may well be in edit mode on something unrelated, and refusing every query
    for the duration would remove a capability to prevent a mistake the caller can now see. A read
    stays available and says what it is.
    """
    if getattr(obj, "mode", "OBJECT") == "OBJECT":
        return {}
    return {
        "editModeStale": True,
        "objectMode": obj.mode,
        "staleNote": ("'%s' is in %s mode, so these figures are the mesh as it was when OBJECT "
                      "mode was last left - Blender keeps live edits in a separate BMesh. Leave "
                      "edit mode and call again for the current state." % (obj.name, obj.mode)),
    }


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


# THE SHADOW FLAG HAS MOVED BETWEEN VERSIONS, so it is looked up rather than assumed. Newest name
# first, exactly as op_add_particles handles show_instancer_for_render - which is in this addon
# because writing the old name raised AttributeError and took down a live build.
SHADOW_ATTRS = ("use_shadow", "use_shadow_jitter")


def shadow_attr(holder):
    """The name this Blender uses for the light's shadow toggle, or None if it has none."""
    for attr in SHADOW_ATTRS:
        if hasattr(holder, attr):
            return attr
    return None


def refuse_unsupported_shadow(params, verb):
    """Refuse `shadow` up front on a Blender that has no such property, rather than ignoring it.

    create_light used to accept the key and write it only `if hasattr(data, attr)`, so on a build
    where the property had moved the caller asked for shadows off, got shadows on, and was told
    nothing. A key this addon ACCEPTS and does not apply is the invoke_editor_tab shape, and the
    house rule is that it is refused rather than silently reinterpreted.

    Asked of the RNA CLASS, not an instance, so it can run before anything is created.
    """
    if "shadow" not in params:
        return
    # `in`, not hasattr: bl_rna.properties is a collection keyed by property name, so hasattr on it
    # asks whether the COLLECTION has an attribute of that name, which is always False and would
    # have made this guard fire on every Blender. Caught by writing it wrong first.
    if any(a in bpy.types.Light.bl_rna.properties for a in SHADOW_ATTRS):
        return
    raise MifOpError("this Blender's Light has no shadow toggle this op knows how to write (tried "
                     "%s), so `shadow` would have been silently ignored. Control shadows through "
                     "the render engine's own settings instead. NOTHING was %s."
                     % (", ".join(SHADOW_ATTRS), verb))


def light_readback(obj, data):
    """What the light IS, off the datablock. One reader for create, set and list.

    Written once on purpose. Three near-identical response builders would drift, and a response
    that disagrees with itself between the op that made a light and the op that lists it is the
    kind of thing nobody notices until a caller diffs them.
    """
    out = {
        "name": obj.name,
        "dataName": data.name,
        "type": data.type,
        "location": rnd(list(obj.matrix_world.to_translation())),
        "rotationEuler": rnd(list(obj.matrix_world.to_euler())),
        "energy": round(float(data.energy), 6),
        "color": rnd(list(data.color)),
        "diffuseFactor": round(float(getattr(data, "diffuse_factor", 1.0)), 6),
        "specularFactor": round(float(getattr(data, "specular_factor", 1.0)), 6),
    }
    if data.type in ("POINT", "SPOT"):
        out["shadowSoftSize"] = round(float(data.shadow_soft_size), 6)
    if data.type == "SPOT":
        out["spotSize"] = round(float(data.spot_size), 6)
        out["spotBlend"] = round(float(data.spot_blend), 6)
    if data.type == "AREA":
        out["size"] = round(float(data.size), 6)
        out["sizeY"] = round(float(getattr(data, "size_y", data.size)), 6)
        out["shape"] = data.shape
    if data.type == "SUN":
        out["angle"] = round(float(data.angle), 6)
    # Reported when this Blender has the property at all, through the same lookup the writers use,
    # so the read and the write can never disagree about which attribute the shadow flag lives on.
    _sh = shadow_attr(data)
    if _sh is not None:
        out["shadow"] = bool(getattr(data, _sh))
        out["shadowAttr"] = _sh
    return out


def camera_readback(obj, cam):
    """What the camera IS, off the datablock. One reader for object_info, set_camera and
    list_cameras - the same rule light_readback follows, and for the same reason: three response
    builders for one object type drift, and nobody notices until a caller diffs two of them.

    FOV is DERIVED here rather than left to the caller. It is the number that actually determines
    framing, it depends on sensor fit as well as focal length, and getting it wrong is the usual
    reason a camera "looks right in the numbers" and wrong in the render.
    """
    dof = getattr(cam, "dof", None)
    fit = getattr(cam, "sensor_fit", "AUTO")
    sw = float(cam.sensor_width)
    sh = float(getattr(cam, "sensor_height", sw))
    # AUTO fits to the LARGER render dimension, which is why a portrait render silently changes
    # which sensor dimension the focal length is measured against.
    r = bpy.context.scene.render
    rx, ry = int(r.resolution_x), int(r.resolution_y)
    if fit == "VERTICAL" or (fit == "AUTO" and ry > rx):
        fov_sensor = sh if fit == "VERTICAL" else sw
    else:
        fov_sensor = sw
    fov = 2.0 * math.atan(fov_sensor / (2.0 * float(cam.lens))) if cam.lens else 0.0
    return {
        "type": cam.type,
        "lensMM": round(float(cam.lens), 6),
        "sensorWidthMM": round(sw, 6),
        "sensorHeightMM": round(sh, 6),
        "sensorFit": fit,
        "fovRadians": round(fov, 6),
        "fovDegrees": round(math.degrees(fov), 4),
        "clipStart": round(float(cam.clip_start), 6),
        "clipEnd": round(float(cam.clip_end), 6),
        "shiftX": round(float(cam.shift_x), 6),
        "shiftY": round(float(cam.shift_y), 6),
        "orthoScale": round(float(cam.ortho_scale), 6),
        "dofEnabled": bool(getattr(dof, "use_dof", False)),
        "dofDistance": round(float(getattr(dof, "focus_distance", 0.0)), 6),
        "dofFocusObject": (dof.focus_object.name
                           if dof is not None and getattr(dof, "focus_object", None) else None),
        "fStop": round(float(getattr(dof, "aperture_fstop", 0.0)), 6),
        # Whether THIS camera is the one a render will use. Obtainable nowhere else in the addon,
        # and the first thing anybody asks about a camera.
        "isSceneCamera": bpy.context.scene.camera is obj,
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
    # PER-TYPE DETAIL, because this early-returned for everything non-MESH until 2026-09-03 and this
    # is the addon's most-used read op. A light came back as a name, a type and a transform - none
    # of its energy, colour, cone or shadow - so nothing could diagnose a lighting rig it had not
    # just built. Six of the fourteen ops modules had no read op at all; this gate is why fixing
    # that anywhere else still left the general reader blind.
    #
    # The LIGHT branch calls the SAME light_readback that create_light, set_light and list_lights
    # use, rather than a fourth copy - which is the whole reason that reader moved into this module.
    if obj.type == "LIGHT" and obj.data is not None:
        info["light"] = light_readback(obj, obj.data)
        return info
    if obj.type == "CAMERA" and obj.data is not None:
        info["camera"] = camera_readback(obj, obj.data)
        return info
    if obj.type == "ARMATURE" and obj.data is not None:
        arm = obj.data
        info["armature"] = {
            "boneCount": len(arm.bones),
            "rootBones": [b.name for b in arm.bones if b.parent is None],
            "poseMode": obj.mode,
            "hasPose": obj.pose is not None and len(obj.pose.bones) > 0,
        }
        return info
    if obj.type in ("CURVE", "FONT", "SURFACE") and obj.data is not None:
        cu = obj.data
        info["curve"] = {
            "splineCount": len(getattr(cu, "splines", []) or []),
            "dimensions": getattr(cu, "dimensions", None),
            "bevelDepth": round(float(getattr(cu, "bevel_depth", 0.0)), 6),
            "extrude": round(float(getattr(cu, "extrude", 0.0)), 6),
            "body": getattr(cu, "body", None) if obj.type == "FONT" else None,
        }
        return info
    if obj.type == "EMPTY":
        info["empty"] = {
            "displayType": obj.empty_display_type,
            "displaySize": round(float(obj.empty_display_size), 6),
            # An empty that instances a collection is a whole scene branch, not a null.
            "instanceCollection": (obj.instance_collection.name
                                   if obj.instance_collection else None),
        }
        return info
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
        # WHICH ARMATURE DEFORMS THIS MESH, if any - the pairing nothing else in this addon
        # reported before ops_rig.py existed. An ARMATURE modifier's .object is the actual rig at
        # DEFORM TIME; obj.parent can point at an armature too (the common "parent to armature"
        # workflow), but parenting alone does not deform anything without the modifier, so only the
        # modifier is reported here - that is the field ops_rig.list_bones' object name lines up
        # against, not a guess from the object hierarchy.
        "armatureModifier": next(
            (m.object.name for m in obj.modifiers if m.type == "ARMATURE" and m.object), None),
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
