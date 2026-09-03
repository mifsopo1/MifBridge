"""Render settings and a still render - the gap that turns the other three into a picture.

WHY THIS ONE NEXT. Lights, cameras and keyframes were closed first because they gate everything;
rendering is what makes them visible. Without it an agent can build and light a scene and still
cannot produce a single image without leaving the typed path.

=============================================================================
RENDERING BLOCKS THE MAIN THREAD, AND THAT IS NOT A DETAIL
=============================================================================
This addon dispatches on Blender's main thread, so a render holds it for the whole exposure -
Blender is frozen and the bridge answers nothing until it finishes. A Cycles render at default
samples on a real scene is minutes, which will hit the addon's job timeout and look like a hung
bridge rather than a slow render.

So: samples and resolution are FIRST-CLASS parameters rather than buried, the response reports how
long it actually took, and the docstring says plainly to start small. A caller who wants a
production frame should set the settings here and press F12 themselves.

=============================================================================
ok:true IS NOT AN IMAGE
=============================================================================
bpy.ops.render.render(write_still=True) returns {'FINISHED'} whether or not a file appeared - a bad
path, a permissions problem or a disabled file format all still report FINISHED. So this stats the
file afterwards and reports its real size, the same way the UE arm's ui_scenario_capture does:
"wroteFile" is a measurement, not the operator's opinion.
"""
import os
import time

import bpy

from .ops_common import MifOpError, reject_unknown, take, take_bool, take_float

_SETTINGS_KEYS = {
    "engine", "resolutionX", "resolutionY", "percentage", "samples",
    "filePath", "output", "fileFormat", "filmTransparent", "colorMode",
    "useDenoising", "exposure",
}
_RENDER_KEYS = {"filePath", "output", "frame", "samples", "resolutionX", "resolutionY",
                "percentage", "writeStill"}


def _engine_ids():
    return {i.identifier for i in
            bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}


def _apply_common(sc, params):
    """Settings shared by set_render_settings and the overrides render_still accepts."""
    applied = {}
    rx = take_float(params, "resolutionX", default=None)
    ry = take_float(params, "resolutionY", default=None)
    pct = take_float(params, "percentage", default=None)
    samples = take_float(params, "samples", default=None)

    # WHERE THE SAMPLE COUNT LIVES IS RESOLVED BEFORE ANYTHING IS WRITTEN. It lives in a different
    # place per engine, and writing the wrong one is a silent no-op: setting cycles.samples on
    # EEVEE changes nothing and reports nothing. That refusal used to fire AFTER resolution,
    # resolutionY and percentage had already been written - and in render_still it also runs before
    # the frame and output-path checks, so a call ending "NOTHING was rendered" had permanently
    # changed the scene's resolution on the way past. Deciding the target first costs nothing:
    # which attribute exists is a property of the engine, not of the value being written.
    samples_target = None
    if samples is not None:
        if "CYCLES" in sc.render.engine and hasattr(sc, "cycles"):
            samples_target = (sc.cycles, "samples", "cycles.samples")
        elif hasattr(sc, "eevee"):
            for attr in ("taa_render_samples", "taa_samples"):
                if hasattr(sc.eevee, attr):
                    samples_target = (sc.eevee, attr, "eevee.%s" % attr)
                    break
        if samples_target is None:
            raise MifOpError("this Blender's %s engine exposes no sample count this op knows how "
                             "to write, so `samples` would have been silently ignored. Set it "
                             "through the engine's own property instead. NOTHING was changed."
                             % sc.render.engine)

    # COMMIT. Nothing below can refuse.
    if rx is not None:
        sc.render.resolution_x = int(rx)
        applied["resolutionX"] = sc.render.resolution_x
    if ry is not None:
        sc.render.resolution_y = int(ry)
        applied["resolutionY"] = sc.render.resolution_y
    if pct is not None:
        sc.render.resolution_percentage = int(pct)
        applied["percentage"] = sc.render.resolution_percentage
    if samples_target is not None:
        holder, attr, label = samples_target
        setattr(holder, attr, int(samples))
        applied["samples"] = int(getattr(holder, attr))
        applied["samplesOn"] = label
    return applied


def op_set_render_settings(params):
    """Configure the render, and report what the scene ACTUALLY holds afterwards."""
    reject_unknown(params, _SETTINGS_KEYS, "set_render_settings")
    sc = bpy.context.scene
    before = {"engine": sc.render.engine,
              "resolution": [sc.render.resolution_x, sc.render.resolution_y],
              "percentage": sc.render.resolution_percentage,
              "filePath": sc.render.filepath,
              "fileFormat": sc.render.image_settings.file_format}

    engine = take(params, "engine", default=None, kind=str)
    if engine:
        want = str(engine).upper()
        valid = _engine_ids()
        if want not in valid:
            # Blender renamed EEVEE's identifier between versions - BLENDER_EEVEE became
            # BLENDER_EEVEE_NEXT and back again - so a friendly alias is resolved against what THIS
            # build actually offers rather than against a remembered name.
            alias = {"EEVEE": [e for e in valid if "EEVEE" in e],
                     "CYCLES": [e for e in valid if "CYCLES" in e],
                     "WORKBENCH": [e for e in valid if "WORKBENCH" in e]}.get(want, [])
            if alias:
                want = sorted(alias)[0]
            else:
                raise MifOpError("unknown render engine '%s' for this Blender. Valid: %s. NOTHING "
                                 "was changed." % (engine, ", ".join(sorted(valid))))
        sc.render.engine = want

    applied = _apply_common(sc, params)

    out_path = take(params, "filePath", "output", default=None, kind=str)
    if out_path:
        sc.render.filepath = bpy.path.abspath(str(out_path))
    fmt = take(params, "fileFormat", default=None, kind=str)
    if fmt:
        valid = {i.identifier for i in
                 bpy.types.ImageFormatSettings.bl_rna.properties["file_format"].enum_items}
        if str(fmt).upper() not in valid:
            raise MifOpError("unknown fileFormat '%s'. Valid: %s." % (fmt, ", ".join(sorted(valid))))
        sc.render.image_settings.file_format = str(fmt).upper()
    cm = take(params, "colorMode", default=None, kind=str)
    if cm:
        sc.render.image_settings.color_mode = str(cm).upper()
    if "filmTransparent" in params:
        sc.render.film_transparent = take_bool(params, "filmTransparent", default=False)
    ex = take_float(params, "exposure", default=None)
    if ex is not None:
        sc.view_settings.exposure = ex
    if "useDenoising" in params:
        want = take_bool(params, "useDenoising", default=True)
        if hasattr(sc, "cycles") and "CYCLES" in sc.render.engine:
            sc.cycles.use_denoising = want
        elif hasattr(sc, "eevee") and hasattr(sc.eevee, "use_taa_reprojection"):
            pass  # EEVEE has no denoiser of the same kind; not silently pretended otherwise
        applied["denoisingRequested"] = want

    return {
        "before": before,
        "after": {"engine": sc.render.engine,
                  "resolution": [sc.render.resolution_x, sc.render.resolution_y],
                  "percentage": sc.render.resolution_percentage,
                  "filePath": sc.render.filepath,
                  "fileFormat": sc.render.image_settings.file_format,
                  "filmTransparent": bool(sc.render.film_transparent)},
        "applied": applied,
        "hasCamera": bpy.context.scene.camera is not None,
        "cameraNote": (None if bpy.context.scene.camera is not None else
                       "there is NO scene camera, so a render would fail - create_camera with "
                       "makeActive, or set one, before rendering."),
    }


def op_render_still(params):
    """Render the current frame to a file, and report whether a file actually appeared.

    BLOCKS Blender for the whole render. Start with small resolution/samples; a heavy frame will
    exceed the addon's main-thread job timeout and read as a hung bridge.
    """
    reject_unknown(params, _RENDER_KEYS, "render_still")
    sc = bpy.context.scene
    if sc.camera is None:
        raise MifOpError("there is no scene camera, so there is nothing to render from. Create one "
                         "with create_camera (makeActive defaults true). NOTHING was rendered.")

    applied = _apply_common(sc, params)
    frame = take_float(params, "frame", default=None)
    if frame is not None:
        sc.frame_set(int(frame))

    out_path = take(params, "filePath", "output", default=None, kind=str)
    if out_path:
        sc.render.filepath = bpy.path.abspath(str(out_path))
    target = bpy.path.abspath(sc.render.filepath)
    if not target:
        raise MifOpError("no output path is set - pass filePath, or set one with "
                         "set_render_settings. NOTHING was rendered.")
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise MifOpError("cannot create the output directory %s: %s. NOTHING was rendered."
                             % (parent, exc))

    write_still = take_bool(params, "writeStill", default=True)

    # WHERE BLENDER SAYS IT WILL WRITE, asked rather than guessed. frame_path() applies the format
    # extension, the frame numbering and any # padding in the path - all three of which make
    # `target + file_extension` wrong, in both directions, for a path ending in a separator or
    # carrying a # run. Kept as the first candidate with the guesses behind it.
    candidates = []
    try:
        fp = sc.render.frame_path(frame=sc.frame_current)
        if fp:
            candidates.append(fp)
    except (AttributeError, TypeError, RuntimeError):
        pass                      # older API or an unusual output path; the guesses still apply
    candidates.append(target)
    ext = sc.render.file_extension or ""
    if ext and not target.lower().endswith(ext.lower()):
        candidates.append(target + ext)
    _seen = set()
    candidates = [c for c in candidates if not (c in _seen or _seen.add(c))]

    # EVERY CANDIDATE IS STAT'D BEFORE THE RENDER, not just `target`. The previous version captured
    # before_size for `target` alone and then tested `cand != target or st != before_size` - and
    # for the usual winning candidate, target + ext, `cand != target` is TRUE, so the freshness
    # test was satisfied by the candidate's NAME rather than by anything about the file. A stale
    # render from a previous run therefore reported wroteFile:true with its old byte count, which
    # is the one thing this measurement exists to rule out.
    before = {}
    for c in candidates:
        try:
            if os.path.isfile(c):
                before[c] = (os.path.getsize(c), os.path.getmtime(c))
        except OSError:
            pass

    t0 = time.time()
    bpy.ops.render.render(write_still=write_still)
    elapsed = time.time() - t0

    # THE MEASUREMENT. render() returns FINISHED whether or not anything reached disk, so the file
    # is stat'd rather than trusted - and compared against what was there before.
    wrote, size, stale = None, -1, None
    for cand in candidates:
        if not os.path.isfile(cand):
            continue
        try:
            st, mt = os.path.getsize(cand), os.path.getmtime(cand)
        except OSError:
            continue
        was = before.get(cand)
        # Fresh means: it did not exist, or it changed. mtime is the primary signal because a
        # re-render of the same scene legitimately produces the same byte count.
        fresh = was is None or mt > was[1] or st != was[0]
        if st > 0 and fresh:
            wrote, size, stale = cand, st, False
            break
        if wrote is None:
            wrote, size, stale = cand, st, True

    return {
        "rendered": True,
        "frame": sc.frame_current,
        "engine": sc.render.engine,
        "resolution": [int(sc.render.resolution_x * sc.render.resolution_percentage / 100.0),
                       int(sc.render.resolution_y * sc.render.resolution_percentage / 100.0)],
        "camera": sc.camera.name,
        "elapsedSeconds": round(elapsed, 3),
        "filePath": wrote or target,
        # FRESH, not merely present. `stale is False` means the file changed across the render;
        # a file that was already there and did not change is reported as NOT written, with
        # staleFileFound naming it, because "there is a png at that path" and "this call rendered
        # one" are different claims and only the second is what a caller asked about.
        "wroteFile": bool(wrote) and size > 0 and stale is False,
        "fileBytes": size,
        "staleFileFound": bool(wrote) and stale is True,
        "applied": applied,
        "sizeNote": ("wroteFile and fileBytes are stat'd off disk. render() returns FINISHED even "
                     "when nothing was written - a bad path or a disabled format both look like "
                     "success from the operator alone."),
        "blockingNote": ("this held Blender's main thread for %.1fs; the bridge answered nothing "
                         "during it." % elapsed),
    }


OPS = {
    "set_render_settings": op_set_render_settings,
    "render_still": op_render_still,
}
