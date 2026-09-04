"""Scene interchange - glTF, OBJ, USD, Alembic, STL, PLY. The formats a Blender user actually ships.

WHY THIS IS NOT AN EXTENSION OF export_mesh. Measured 2026-09-03: import_mesh accepts .fbx, .gltf
and .glb, and export_mesh writes .fbx AND NOTHING ELSE. So glTF could come IN and not go OUT, and
USD - the interchange format of every film and Omniverse pipeline - was absent in both directions,
as were OBJ, Alembic, STL and PLY. That is the round-trip bias the standing rule names: FBX is what
the Unreal path needs, and it had become the whole of what the addon could write.

export_mesh is not the place to fix it. It is FBX-specific to its bones - FBX_EXPORT_ARGS, the
axis and bone-axis overrides, and an object_types filter whose semantics are the FBX exporter's -
and it says so itself, refusing CAMERA and LIGHT with "exporting a whole scene including lights and
cameras is a different job than export_mesh". This is that job. export_mesh keeps the UE round trip
and its skeletal-export handling; this writes interchange.

=============================================================================
THE OPERATOR NAMES MOVED, AND NOT ALL AT ONCE
=============================================================================
Blender rewrote its OBJ, STL and PLY exporters in C++ and moved them from export_scene.* /
export_mesh.* to wm.*_export, at DIFFERENT versions - OBJ and PLY at 4.0, STL at 4.2. This addon
supports 3.6 through 5.0, so on any given build some of these live at the new name and some at the
old. Each format therefore carries a LIST of candidates tried in order, the same shape as the
fcurve accessor in ops_anim and the group interface in ops_nodes.

AND THE SELECTION KEYWORD IS DIFFERENT FOR EVERY ONE OF THEM: use_selection, export_selected_objects,
selected_objects_only, selected. Guessing one and passing it to the wrong operator raises, or worse
is silently ignored on an operator that takes **kwargs loosely - so it is spelled per format rather
than assumed.

=============================================================================
ok:true IS NOT A FILE
=============================================================================
An exporter returns {'FINISHED'} whether or not anything appeared, and a leftover file from an
earlier run passes any existence check. So the mtime is taken BEFORE the call and the file must be
newer than that as well as non-empty - the same rule render_animation applies per frame.
"""
import os
import time

import bpy

from .ops_common import MifOpError, get_object, reject_unknown, take, take_bool, take_int

_EXPORT_KEYS = {"file", "filepath", "path", "objects", "object", "name", "selectedOnly",
                "applyModifiers", "frameStart", "frameEnd", "overwrite", "replaceExisting",
                "animation"}

# extension -> (candidate operators in order, selection kwarg, extra kwargs, human label)
#
# EVERY ENTRY WAS WRITTEN FROM THE OPERATOR'S OWN SIGNATURE rather than from memory, because the
# selection keyword differs for all of them and a wrong one is either a raise or a silent no-op.
_EXPORTERS = {
    ".glb":  (("export_scene.gltf",), "use_selection", {"export_format": "GLB"}, "glTF binary"),
    ".gltf": (("export_scene.gltf",), "use_selection", {"export_format": "GLTF_SEPARATE"},
              "glTF + separate resources"),
    ".obj":  (("wm.obj_export", "export_scene.obj"),
              {"wm.obj_export": "export_selected_objects", "export_scene.obj": "use_selection"},
              {}, "Wavefront OBJ"),
    ".usd":  (("wm.usd_export",), "selected_objects_only", {}, "USD"),
    ".usda": (("wm.usd_export",), "selected_objects_only", {}, "USD ASCII"),
    ".usdc": (("wm.usd_export",), "selected_objects_only", {}, "USD binary"),
    ".usdz": (("wm.usd_export",), "selected_objects_only", {}, "USDZ"),
    ".abc":  (("wm.alembic_export",), "selected", {}, "Alembic"),
    ".stl":  (("wm.stl_export", "export_mesh.stl"),
              {"wm.stl_export": "export_selected_objects", "export_mesh.stl": "use_selection"},
              {}, "STL"),
    ".ply":  (("wm.ply_export", "export_mesh.ply"),
              {"wm.ply_export": "export_selected_objects", "export_mesh.ply": "use_selection"},
              {}, "PLY"),
}

# Formats that carry TIME. Passing a frame range to one that does not is refused rather than
# dropped, because "I exported an animation" and "I exported frame 1" look identical afterwards.
_ANIMATED = {".abc", ".usd", ".usda", ".usdc", ".usdz", ".glb", ".gltf"}

_FRAME_KWARG = {".abc": ("start", "end"), ".usd": ("start", "end"), ".usda": ("start", "end"),
                ".usdc": ("start", "end"), ".usdz": ("start", "end")}


def _op_exists(dotted):
    """Is bpy.ops.<a>.<b> actually present on this build?

    An exporter can be a DISABLED ADD-ON, in which case the operator simply is not there. Asking
    first turns "AttributeError: 'BPyOps' object has no attribute" into a sentence naming the
    format and the add-on, which is the difference between a message and a traceback.
    """
    mod, _, name = dotted.partition(".")
    group = getattr(bpy.ops, mod, None)
    if group is None:
        return False
    try:
        return hasattr(group, name)
    except Exception:                       # noqa: BLE001
        return False


def resolve_exporter(ext):
    """(dotted operator, selection kwarg, extra kwargs, label) for a file extension.

    PURE, and separated for that reason: everything else here needs a real Blender, while the
    version-drift logic - which name exists on this build, which selection keyword goes with it - is
    a table lookup and is where the mistakes live.
    """
    entry = _EXPORTERS.get(ext)
    if entry is None:
        raise MifOpError("no exporter for '%s'. This op writes: %s. FBX is written by export_mesh, "
                         "which also handles skeletal export for the Unreal round trip. NOTHING "
                         "was written." % (ext, ", ".join(sorted(_EXPORTERS))))
    candidates, sel, extra, label = entry
    for dotted in candidates:
        if _op_exists(dotted):
            sel_kwarg = sel[dotted] if isinstance(sel, dict) else sel
            return dotted, sel_kwarg, dict(extra), label
    raise MifOpError("this Blender (%s) has no %s exporter - tried %s. It is usually an add-on and "
                     "may be disabled in Preferences > Add-ons. NOTHING was written."
                     % (bpy.app.version_string, label, " and ".join(candidates)))


def op_export_scene(params):
    """Write glTF, OBJ, USD, Alembic, STL or PLY - everything except the FBX export_mesh owns.

    params:
      file / filepath / path (str)   required. The FORMAT COMES FROM THE EXTENSION.
      objects / object (list|str)    export only these. Default the whole scene.
      selectedOnly (bool)            export the current selection instead of naming objects
      applyModifiers (bool)          where the format's exporter supports it
      frameStart / frameEnd (int)    for the formats that carry time. Refused on those that do not.
      animation (bool)               glTF only - whether to write animation at all
      overwrite (bool)               default true
    """
    reject_unknown(params, _EXPORT_KEYS, "export_scene")
    raw = take(params, "file", "filepath", "path", required=True, kind=str)
    path = bpy.path.abspath(str(raw))
    ext = os.path.splitext(path)[1].lower()
    if not ext:
        raise MifOpError("'%s' has no file extension, and the format is taken from it. Give one of: "
                         "%s. NOTHING was written." % (raw, ", ".join(sorted(_EXPORTERS))))
    dotted, sel_kwarg, kwargs, label = resolve_exporter(ext)

    if os.path.exists(path) and not take_bool(params, "overwrite", "replaceExisting", default=True):
        raise MifOpError("%s already exists and overwrite:false. NOTHING was written." % path)

    start = take_int(params, "frameStart", default=None)
    end = take_int(params, "frameEnd", default=None)
    if (start is not None or end is not None) and ext not in _ANIMATED:
        raise MifOpError("%s carries no animation, so a frame range would have been accepted and "
                         "silently ignored - 'I exported an animation' and 'I exported one frame' "
                         "look identical afterwards. Drop frameStart/frameEnd, or write %s instead. "
                         "NOTHING was written."
                         % (label, " or ".join(sorted(_ANIMATED))))
    if start is not None and end is not None and end < start:
        raise MifOpError("frameEnd %d is before frameStart %d. NOTHING was written." % (end, start))

    # THE OUTPUT DIRECTORY IS CHECKED BY WRITING TO IT. os.access reports the DACL on Windows and
    # not the effective permission, so it answers yes for directories a write then fails on.
    outdir = os.path.dirname(path) or "."
    try:
        if not os.path.isdir(outdir):
            os.makedirs(outdir)
        probe = os.path.join(outdir, ".mif_export_probe")
        with open(probe, "wb") as fh:
            fh.write(b"x")
        os.remove(probe)
    except Exception as exc:                # noqa: BLE001
        raise MifOpError("the output directory '%s' cannot be written (%s). NOTHING was written."
                         % (outdir, exc))

    names = take(params, "objects", "object", "name")
    if isinstance(names, str):
        names = [names]
    selected_only = take_bool(params, "selectedOnly", default=False)
    if names is not None and selected_only:
        raise MifOpError("pass objects OR selectedOnly, not both - one names a set and the other "
                         "uses whatever happens to be selected. NOTHING was written.")
    if names is not None and not isinstance(names, (list, tuple)):
        raise MifOpError("'objects' must be a list of names. NOTHING was written.")

    targets = []
    if names:
        # RESOLVED BEFORE ANYTHING IS SELECTED, so a typo cannot leave the scene's selection
        # rearranged as a side effect of a failed export.
        targets = [get_object(n) for n in names]

    kwargs["filepath"] = path
    if names or selected_only:
        kwargs[sel_kwarg] = True
    if start is not None or end is not None:
        sk, ek = _FRAME_KWARG.get(ext, (None, None))
        if sk:
            kwargs[sk] = int(start if start is not None else bpy.context.scene.frame_start)
            kwargs[ek] = int(end if end is not None else bpy.context.scene.frame_end)
        else:
            # glTF takes its range from the SCENE rather than from arguments, so the scene is set
            # and restored instead of a kwarg being invented that the operator would reject.
            pass
    if ext in (".glb", ".gltf") and params.get("animation") is not None:
        kwargs["export_animations"] = take_bool(params, "animation", default=True)
    if params.get("applyModifiers") is not None:
        # SPELLED PER FORMAT, and refused where the exporter has no such option rather than dropped.
        # An applyModifiers that is accepted and ignored is the worst outcome here: the file is
        # written from unevaluated geometry and looks like a successful export of the wrong thing.
        if ext in (".glb", ".gltf"):
            kwargs["export_apply"] = take_bool(params, "applyModifiers", default=True)
        elif ext == ".obj" and dotted == "wm.obj_export":
            kwargs["apply_modifiers"] = take_bool(params, "applyModifiers", default=True)
        else:
            raise MifOpError("applyModifiers is not something the %s exporter on this build takes. "
                             "NOTHING was written." % label)

    from .ops_common import selection_restore, selection_snapshot
    snap = selection_snapshot()
    scene = bpy.context.scene
    frames_before = (scene.frame_start, scene.frame_end)
    started = time.time()
    try:
        if names:
            for obj in bpy.context.view_layer.objects:
                obj.select_set(False)
            for obj in targets:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = targets[0]
        if ext in (".glb", ".gltf") and (start is not None or end is not None):
            scene.frame_start = int(start if start is not None else scene.frame_start)
            scene.frame_end = int(end if end is not None else scene.frame_end)
        mod, _, opname = dotted.partition(".")
        try:
            getattr(getattr(bpy.ops, mod), opname)(**kwargs)
        except TypeError as exc:
            raise MifOpError("the %s exporter on this build does not accept one of %s (%s). This "
                             "is version drift in the operator's own signature. NOTHING was "
                             "written." % (label, sorted(kwargs), exc))
        except RuntimeError as exc:
            raise MifOpError("%s export failed: %s" % (label, exc))
    finally:
        scene.frame_start, scene.frame_end = frames_before
        selection_restore(snap)

    # ok:true IS NOT A FILE. Existence alone passes on a leftover from an earlier run, so the mtime
    # has to have moved as well - the same rule render_animation applies per frame.
    if not os.path.isfile(path):
        raise MifOpError("the %s exporter reported success and no file is at %s." % (label, path))
    size = os.path.getsize(path)
    mtime = os.path.getmtime(path)
    if mtime < started - 1.0:
        raise MifOpError("the %s exporter reported success but %s was last written %.1fs before "
                         "this call started - it is a LEFTOVER from an earlier run, not this "
                         "export." % (label, path, started - mtime))
    if size == 0:
        raise MifOpError("the %s exporter reported success and wrote a ZERO-BYTE file at %s."
                         % (label, path))

    return {
        "ok": True,
        "file": path,
        "format": label,
        "operator": dotted,
        "bytes": size,
        "objectsNamed": sorted(o.name for o in targets) if targets else None,
        "wholeScene": not (names or selected_only),
        "frameRange": [kwargs.get(_FRAME_KWARG.get(ext, ("", ""))[0]),
                       kwargs.get(_FRAME_KWARG.get(ext, ("", ""))[1])] if ext in _ANIMATED else None,
        "note": ("FBX is written by export_mesh, which also handles the skeletal case - an armature "
                 "must be in the SELECTION there or the mesh is written frozen in its current pose."),
    }


OPS = {"export_scene": op_export_scene}
