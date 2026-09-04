"""Saving, opening and describing the .blend itself - the thing that made every other op temporary.

WHY THIS EXISTS, and why a 2026-09-03 coverage review put it first ahead of every feature. Nothing
in this addon called wm.save_mainfile. Not once. So every light, camera, world, keyframe, physics
setup, node group and material it authored lived only inside a running Blender and DIED WITH THE
PROCESS. The only artefacts MifBlender could produce were a mesh FBX, a baked texture and exactly
one rendered frame.

For a UE round trip that does not bite, because the FBX is the deliverable and the .blend is
scratch. For everyone else - film, archviz, motion graphics, print, anyone using Blender as
Blender - the deliverable IS the .blend, and the tool could not make one. That is the difference
between a mesh-conditioning pipeline and a Blender automation tool, and it is one module wide.

SAVE-A-COPY IS THE DEFAULT, deliberately. wm.save_as_mainfile normally REPOINTS the session at the
new path, so a subsequent save silently writes somewhere the caller never named, and an agent that
saved a scratch copy has quietly adopted it as the working file. `copy=True` writes the file and
leaves bpy.data.filepath alone. A caller who wants the session repointed asks for it by name.

THE POSTCONDITION THAT MATTERS IS NOT "the file exists". It is WHAT THE SAVE DESTROYED. Blender
purges datablocks with no users and no fake user when it writes, so an action that has been unlinked
from its object, an image with nothing pointing at it, a node group nobody instanced - all of them
are gone the moment you save, permanently and without a word. That is data loss caused BY the
successful operation, and it is invisible unless somebody counts first. So this counts first, and
reports what went.
"""
import os

import bpy

from .ops_common import check_output_path, MifOpError, reject_unknown, take, take_bool

# The ID collections a save can purge. Not every bpy.data collection - only the ones that carry
# authored work somebody would miss, which is the same judgement clear_scene's purge list makes.
_PURGEABLE = ("actions", "images", "materials", "meshes", "node_groups", "textures",
              "armatures", "cameras", "lights", "worlds", "curves", "collections", "objects")


def _orphans():
    """Datablocks a save is about to delete: no users, no fake user.

    Reported BEFORE the write, because afterwards they do not exist to be counted. This is the
    difference between "the save worked" and "the save worked and took your unlinked walk cycle
    with it" - and Blender says nothing about the second.
    """
    out = {}
    total = 0
    for coll_name in _PURGEABLE:
        coll = getattr(bpy.data, coll_name, None)
        if coll is None:
            continue
        names = []
        for db in coll:
            try:
                if db.users == 0 and not db.use_fake_user:
                    names.append(db.name)
            except AttributeError:
                continue          # not every ID type carries both; skip rather than guess
        if names:
            out[coll_name] = sorted(names)
            total += len(names)
    return out, total


def _counts():
    """How much of each thing this file holds. The cheap census a caller needs to resume work."""
    out = {}
    for coll_name in _PURGEABLE:
        coll = getattr(bpy.data, coll_name, None)
        if coll is not None:
            out[coll_name] = len(coll)
    return out


def op_file_info(params):
    """What file this is, whether it has unsaved work, and what it holds.

    No parameters. Cheap enough to call before anything else, and it answers the two questions that
    decide whether a session can be resumed at all: is there a file on disk behind this, and is
    there work in memory that is not in it.
    """
    # An empty TUPLE, not set(). parity_check resolves the accepted-key set statically and is
    # fail-closed by design - it reads a set/list/tuple literal and refuses a set() CALL, because
    # a call could be anything. reject_unknown only does `k not in accepted` and sorted(accepted),
    # both of which a tuple satisfies. Taking no parameters is still a contract worth declaring.
    reject_unknown(params, (), "file_info")
    orphans, orphan_total = _orphans()
    path = bpy.data.filepath or ""
    return {
        "filepath": path,
        "isSaved": bool(path),
        "isDirty": bool(bpy.data.is_dirty),
        "fileExists": bool(path) and os.path.isfile(path),
        "fileBytes": os.path.getsize(path) if path and os.path.isfile(path) else -1,
        "blenderVersion": ".".join(str(v) for v in bpy.app.version),
        "counts": _counts(),
        "orphanCount": orphan_total,
        "orphans": orphans,
        "orphanNote": ("Datablocks with no users and no fake user. A SAVE DELETES THESE - "
                       "permanently and silently. Give one a fake user to keep it."),
    }


def op_save_file(params):
    """Write the .blend to disk. Reports what the save destroyed, not just that it worked.

    params:
      filepath (str, required)   where to write. .blend is appended if absent.
      overwrite (bool)           default False. An existing file is REFUSED without this.
      repointSession (bool)      default False. When false the session keeps its current filepath
                                 and this is a save-a-copy; when true, subsequent saves go here.
      compress (bool)            default False, Blender's own default.

    REFUSES BEFORE WRITING in every case, so a rejected call leaves the disk untouched.
    """
    reject_unknown(params, {"filepath", "path", "overwrite", "repointSession", "compress"},
                   "save_file")
    raw_path = take(params, "filepath", "path", default=None, kind=str)
    if not raw_path:
        raise MifOpError("'filepath' is required - where to write the .blend. NOTHING was saved.")
    path = os.path.abspath(os.path.expanduser(str(raw_path)))
    # SAME CHECK THE FOUR OTHER WRITERS USE. This one already refused with a sentence rather than a
    # raw exception, because save_as_mainfile fails immediately and there is no expensive work to
    # waste - but a caller gets a clearer answer from the shared message than from Blender's, and an
    # op that writes a file and skips the shared check is exactly what audit_output_paths is for.
    check_output_path(raw_path, path, "saved")
    if not path.lower().endswith(".blend"):
        path += ".blend"

    overwrite = take_bool(params, "overwrite", default=False)
    existed = os.path.isfile(path)
    if existed and not overwrite:
        raise MifOpError("'%s' already exists and overwrite was not asked for. NOTHING was saved."
                         % path)

    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise MifOpError("cannot create the directory %s: %s. NOTHING was saved."
                             % (parent, exc))

    # COUNTED BEFORE THE WRITE, because afterwards they are gone and uncountable. See _orphans.
    orphans, orphan_total = _orphans()
    dirty_before = bool(bpy.data.is_dirty)
    was_filepath = bpy.data.filepath or ""
    repoint = take_bool(params, "repointSession", default=False)

    try:
        bpy.ops.wm.save_as_mainfile(filepath=path,
                                    copy=not repoint,
                                    compress=take_bool(params, "compress", default=False))
    except (RuntimeError, OSError) as exc:
        raise MifOpError("Blender refused to save to '%s': %s. Nothing was written unless the "
                         "error says otherwise - check the path and permissions." % (path, exc))

    # MEASURED OFF DISK. save_as_mainfile raises on most failures but a zero-byte or missing file
    # is still the question worth asking, and the answer is a stat rather than the absence of an
    # exception.
    on_disk = os.path.isfile(path)
    size = os.path.getsize(path) if on_disk else -1
    if not on_disk or size <= 0:
        raise MifOpError("save reported no error but '%s' is %s. Treat the file as not written."
                         % (path, "empty" if on_disk else "not on disk"))

    return {
        "filepath": path,
        "fileBytes": size,
        "overwrote": existed,
        "repointedSession": repoint,
        "sessionFilepath": bpy.data.filepath or "",
        "sessionFilepathUnchanged": (bpy.data.filepath or "") == was_filepath,
        "dirtyBefore": dirty_before,
        "dirtyAfter": bool(bpy.data.is_dirty),
        # THE PART BLENDER NEVER TELLS YOU. These no longer exist in the saved file.
        "purgedOrphans": orphan_total,
        "purgedOrphansByType": orphans,
        "purgeNote": ("A save DELETES datablocks with no users and no fake user. The counts above "
                      "are what this save removed - an unlinked action, an unused image, a node "
                      "group nobody instanced. Give one a fake user first to keep it."),
        "copyNote": ("copy=True unless repointSession was asked for, so bpy.data.filepath is "
                     "unchanged and the next save still goes where it did before. This is a "
                     "save-a-copy, not a Save As."),
    }


def op_open_file(params):
    """Open a .blend, discarding whatever is in memory.

    params:
      filepath (str, required)
      discardUnsaved (bool)   required to be true when the current session is DIRTY. Without it an
                              open over unsaved work is refused, because there is no undo for it.

    DESTRUCTIVE AND UNRECOVERABLE. Everything in the current session is gone - this is the one op
    here that can lose work that was never on disk, which is why the dirty check is not optional.
    """
    reject_unknown(params, {"filepath", "path", "discardUnsaved"}, "open_file")
    path = take(params, "filepath", "path", default=None, kind=str)
    if not path:
        raise MifOpError("'filepath' is required. NOTHING was opened.")
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        raise MifOpError("no file at '%s'. NOTHING was opened." % path)

    if bpy.data.is_dirty and not take_bool(params, "discardUnsaved", default=False):
        raise MifOpError(
            "this session has UNSAVED changes and opening '%s' would discard them permanently - "
            "there is no undo across a file load. Save first with save_file, or pass "
            "discardUnsaved:true if the work is genuinely disposable. NOTHING was opened." % path)

    try:
        bpy.ops.wm.open_mainfile(filepath=path)
    except (RuntimeError, OSError) as exc:
        raise MifOpError("Blender could not open '%s': %s. The previous session may or may not "
                         "still be loaded - call file_info to find out." % (path, exc))

    # READ BACK. open_mainfile replaces bpy.data wholesale, so the check is that we are now looking
    # at the file we asked for rather than at whatever survived a partial load.
    now = bpy.data.filepath or ""
    if os.path.normcase(now) != os.path.normcase(path):
        raise MifOpError("asked to open '%s' but the session reports '%s' afterwards. Do not trust "
                         "this state - call file_info." % (path, now))
    # NO STATIC WARNING FIELD HERE. A first version returned a discardedNote saying the previous
    # session was gone, and audit_blender_consequence_fields flagged it: a constant string in a
    # response is something no suite can check and no caller can act on differently, and the only
    # honest way to test it would be to have a suite actually destroy its own session mid-run.
    # The warning belongs where it is read BEFORE the call - the tool help and this docstring - not
    # in the reply that arrives after the work is already irreversible.
    return {
        "filepath": now,
        "opened": True,
        "isDirty": bool(bpy.data.is_dirty),
        "counts": _counts(),
    }


OPS = {
    "file_info": op_file_info,
    "save_file": op_save_file,
    "open_file": op_open_file,
}
