"""Collections - how a Blender scene is actually organised, and the hole under set_light_linking.

WHY THIS EXISTS, and it is not "for completeness". Before 2026-09-03 the ONLY place in this entire
addon that touched bpy.data.collections was a four-line private helper inside set_light_linking,
which creates an EMPTY collection when it cannot find one by name. Nothing else could make a
collection, list one, put an object in one, or take one out.

That is not merely a missing feature - it makes a SHIPPED op unable to reach its own success state.
Light linking restricts a light to the objects inside a receiver collection, so an empty receiver
collection means the light illuminates NOTHING AT ALL. set_light_linking is honest about it and
returns `litsNothing: true`. But the caller then had no typed way to fix it, because putting an
object into the collection was not expressible - the only escape was run_python, i.e. the
arbitrary-code switch a user may well have turned off. An op whose only reachable outcome is the
broken one is worse than an absent op.

=============================================================================
A COLLECTION THAT IS NOT LINKED INTO THE SCENE TREE DOES NOTHING
=============================================================================
bpy.data.collections.new() creates a collection that belongs to NO scene. Objects inside it are not
in the view layer, do not render, and do not appear in the outliner. Every field on it reads
perfectly - name, objects, children - and the whole thing is inert. This is the same shape
02_GOTCHAS calls "right-looking and inert", and it is the default outcome of the obvious API call.

So create_collection LINKS by default, list_collections reports `inScene` per collection, and an
orphan is called out rather than left to be discovered at render time.

=============================================================================
THE EXCLUDE CHECKBOX IS NOT ON THE COLLECTION
=============================================================================
The four visibility controls people mean when they say "hide a collection" live in two different
places and two of them are PER VIEW LAYER:

  collection.hide_viewport        global, the monitor icon. Same for every view layer.
  collection.hide_render          global, the camera icon.
  layer_collection.exclude        PER VIEW LAYER, the checkbox. Excluded means the objects are not
                                  even evaluated - they leave the depsgraph entirely.
  layer_collection.hide_viewport  PER VIEW LAYER, the EYE icon - and this is the one people click.

Writing `collection.hide_viewport` when the caller meant the eye is a silent wrong answer, because
both are real properties that accept the write. So set_collection_visibility takes them by their
distinct names, says which view layer a per-layer write landed on, and reads all four back.
"""
import bpy

from .ops_common import MifOpError, get_object, reject_unknown, take, take_bool

_CREATE_KEYS = {"name", "collection", "parent", "objects", "link", "colorTag"}
_LIST_KEYS = {"viewLayer", "withObjects"}
_LINK_KEYS = {"collection", "name", "objects", "object", "move"}
_UNLINK_KEYS = {"collection", "name", "objects", "object", "allowOrphans"}
_VIS_KEYS = {"collection", "name", "viewLayer", "hideViewport", "hideRender", "exclude",
             "hideInViewLayer", "indirectOnly", "holdout"}
_DELETE_KEYS = {"collection", "name", "deleteObjects", "reparentTo"}


def _find_collection(name, verb):
    """A collection by name, or a refusal that lists what exists."""
    coll = bpy.data.collections.get(str(name))
    if coll is None:
        known = sorted(c.name for c in bpy.data.collections)[:30]
        raise MifOpError("no collection named '%s'. Present: %s. NOTHING was changed."
                         % (name, ", ".join(known) if known else "<none>"))
    return coll


def _scene_root():
    return bpy.context.scene.collection


def _walk(coll, seen=None):
    """Every collection reachable from `coll`, itself included. Cycle-safe.

    Blender's own API will not let you build a cycle, but a collection CAN be linked under two
    parents at once, so a naive walk visits it twice and a naive depth count is wrong. The seen-set
    is what makes `inScene` a set-membership question rather than a traversal that might not
    terminate on data this addon did not create.
    """
    if seen is None:
        seen = set()
    if coll.name in seen:
        return seen
    seen.add(coll.name)
    for child in coll.children:
        _walk(child, seen)
    return seen


def _in_scene_names():
    """Names of every collection actually reachable from the scene's root collection."""
    return _walk(_scene_root()) - {_scene_root().name}


def _layer_collection(name, view_layer):
    """The per-view-layer LayerCollection for a collection, or None.

    THIS IS A DIFFERENT OBJECT FROM THE COLLECTION and it is where exclude and the eye icon live.
    It exists only for collections linked into that view layer, which is itself the answer to "why
    did excluding it do nothing" - an orphaned collection has no LayerCollection to exclude.
    """
    def rec(lc):
        if lc.collection.name == name:
            return lc
        for child in lc.children:
            got = rec(child)
            if got is not None:
                return got
        return None
    return rec(view_layer.layer_collection)


def _object_names(params, *keys):
    """A list of object names from either a list key or a single-object key."""
    raw = take(params, *keys)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, (list, tuple)):
        raise MifOpError("'%s' must be a list of object names or a single name, got %s"
                         % (keys[0], type(raw).__name__))
    return [str(x) for x in raw]


def _collections_of(obj):
    return sorted(c.name for c in bpy.data.collections if obj.name in c.objects)


def op_create_collection(params):
    """Make a collection and LINK IT, because an unlinked one is invisible and renders nothing.

    bpy.data.collections.new() alone produces a collection in no scene: its objects are outside the
    view layer, outside the depsgraph and outside the render, while every field on it reads
    correctly. That is the default outcome of the obvious call and it is why `link` defaults to
    true here rather than mirroring the API.

    params:
      name / collection (str)  required
      parent (str)             collection to nest under. Default the scene's root collection.
      objects (list[str])      objects to link in at creation - the common case, and the one that
                               keeps a light-linking receiver from being born empty.
      link (bool)              default true. false makes a deliberately unlinked collection and the
                               response says inScene:false so it cannot be mistaken for a live one.
      colorTag (str)           COLOR_01..COLOR_08, or NONE.
    """
    reject_unknown(params, _CREATE_KEYS, "create_collection")
    name = take(params, "name", "collection", required=True, kind=str)
    if bpy.data.collections.get(name) is not None:
        raise MifOpError("a collection named '%s' already exists - link objects into it with "
                         "link_objects rather than creating a second one. NOTHING was changed."
                         % name)
    parent_name = take(params, "parent", kind=str)
    link = take_bool(params, "link", default=True)
    tag = take(params, "colorTag", kind=str)
    names = _object_names(params, "objects")
    # RESOLVE EVERY OBJECT BEFORE CREATING ANYTHING. A refusal must fire before a mutation, and a
    # typo in the fourth name would otherwise leave a half-populated collection behind.
    objs = [get_object(n) for n in names]

    parent = _find_collection(parent_name, "create_collection") if parent_name else _scene_root()
    if parent_name and link is False:
        raise MifOpError("pass a parent OR link:false, not both - an unlinked collection has no "
                         "parent by definition. NOTHING was changed.")

    coll = bpy.data.collections.new(name)
    if tag:
        try:
            coll.color_tag = str(tag).upper()
        except (TypeError, ValueError) as exc:
            bpy.data.collections.remove(coll)
            raise MifOpError("colorTag '%s' is not valid (%s) - use NONE or COLOR_01..COLOR_08. "
                             "The collection was removed again, NOTHING was changed." % (tag, exc))
    if link:
        parent.children.link(coll)
    for obj in objs:
        coll.objects.link(obj)

    # THE POSTCONDITION IS REACHABILITY, not existence. `bpy.data.collections.get(name)` would be
    # true for an orphan too, which is exactly the state this op exists to avoid producing by
    # accident, so it is measured by walking the scene tree instead.
    # THE NAME BLENDER GAVE IT, not the one that was asked for. Blender sanitises a name -
    # control characters removed, 63-character truncation - so the two differ more often than
    # "a clash got a .001 suffix". Checking the REQUESTED name made this postcondition report
    # a false failure for every adjusted name: the collection was linked and reachable, under
    # a slightly different string, and the op refused anyway AND left it behind. Found by the
    # matrix leak pass on its first run, not by reading.
    made_name = coll.name
    in_scene = made_name in _in_scene_names()
    if link and not in_scene:
        # AND IT IS REMOVED. This refusal used to leave the collection in the file while
        # saying it had failed - the colorTag path above always cleaned up and this one did
        # not, which is what per-site cleanup looks like when a second site appears.
        bpy.data.collections.remove(coll)
        raise MifOpError("created '%s' and linked it under '%s', but it is NOT reachable from the "
                         "scene collection afterwards - it would be invisible and render nothing. "
                         "It was removed again, NOTHING was changed."
                         % (made_name, parent.name))
    return {
        "ok": True,
        "collection": made_name,
        "requestedName": name,
        # BLENDER ADJUSTS A NAME SILENTLY. Anything looking this collection up by the string
        # it asked for needs to know it will not find it.
        "nameWasAdjusted": made_name != name,
        # The canonical spelling, alongside - see the note in create_action. Same boolean, the name
        # ten other ops use.
        "nameWasSuffixed": made_name != name,
        "parent": parent.name if link else None,
        "inScene": in_scene,
        "objectCount": len(coll.objects),
        "objects": sorted(o.name for o in coll.objects),
        "colorTag": getattr(coll, "color_tag", None),
        "note": (None if link else
                 "link:false was requested, so this collection is in NO scene: its objects are "
                 "outside the view layer and the render, and every field on it still reads "
                 "correctly. Link it later by creating a parent relationship."),
    }


def op_list_collections(params):
    """The collection tree, with the four visibility states that are kept in two different places.

    Reports `inScene` per collection because an orphan is indistinguishable from a live collection
    by every other field, and reports the PER-VIEW-LAYER states separately from the global ones
    because "hidden" is four different properties and people mean the eye icon.

    params:
      viewLayer (str)      which view layer the per-layer states come from. Default the active one.
      withObjects (bool)   include each collection's object names. Default false - a scene with
                           thousands of objects makes this response enormous.
    """
    reject_unknown(params, _LIST_KEYS, "list_collections")
    with_objects = take_bool(params, "withObjects", default=False)
    vl_name = take(params, "viewLayer", kind=str)
    if vl_name:
        vl = bpy.context.scene.view_layers.get(vl_name)
        if vl is None:
            known = sorted(v.name for v in bpy.context.scene.view_layers)
            raise MifOpError("no view layer named '%s' in scene '%s'. Present: %s."
                             % (vl_name, bpy.context.scene.name, ", ".join(known)))
    else:
        vl = bpy.context.view_layer

    live = _in_scene_names()
    rows = []
    for coll in sorted(bpy.data.collections, key=lambda c: c.name):
        lc = _layer_collection(coll.name, vl)
        row = {
            "name": coll.name,
            "inScene": coll.name in live,
            "objectCount": len(coll.objects),
            "childCollections": sorted(c.name for c in coll.children),
            "colorTag": getattr(coll, "color_tag", None),
            # GLOBAL, the monitor and camera icons - the same for every view layer.
            "hideViewport": bool(coll.hide_viewport),
            "hideRender": bool(coll.hide_render),
            # PER VIEW LAYER. None means this collection is not in that view layer at all, which is
            # a different answer from "not excluded" and is the reason excluding an orphan does
            # nothing.
            "exclude": bool(lc.exclude) if lc else None,
            "hideInViewLayer": bool(lc.hide_viewport) if lc else None,
            "indirectOnly": bool(lc.indirect_only) if lc else None,
            "holdout": bool(lc.holdout) if lc else None,
        }
        if with_objects:
            row["objects"] = sorted(o.name for o in coll.objects)
        rows.append(row)

    orphans = [r["name"] for r in rows if not r["inScene"]]
    # OBJECTS IN NO COLLECTION AT ALL. They exist in bpy.data, they are in no scene, and they are
    # invisible everywhere - the object-level version of the same trap, and the state unlink_objects
    # refuses to create.
    homeless = sorted(o.name for o in bpy.data.objects if not _collections_of(o))
    return {
        "ok": True,
        "viewLayer": vl.name,
        "sceneRoot": _scene_root().name,
        "count": len(rows),
        "collections": rows,
        "orphanCollections": orphans,
        "objectsInNoCollection": homeless,
        "note": ("exclude and hideInViewLayer are PER VIEW LAYER and are reported for '%s' only. "
                 "hideViewport and hideRender are global. A null in a per-layer field means the "
                 "collection is not in that view layer at all." % vl.name),
    }


def op_link_objects(params):
    """Put objects into a collection - the operation that makes light linking usable at all.

    An object can be in MANY collections at once; linking adds, it does not move. Pass move:true to
    unlink it from every other collection, which is what a caller usually means by "put it in".

    params:
      collection / name (str)   required
      objects (list[str])       or `object` for one. Required.
      move (bool)               unlink from all OTHER collections first. Default false.
    """
    reject_unknown(params, _LINK_KEYS, "link_objects")
    coll = _find_collection(take(params, "collection", "name", required=True, kind=str),
                            "link_objects")
    names = _object_names(params, "objects", "object")
    if not names:
        raise MifOpError("'objects' is required - a list of object names, or 'object' for one. "
                         "NOTHING was changed.")
    objs = [get_object(n) for n in names]        # resolve all before mutating any
    move = take_bool(params, "move", default=False)

    before = len(coll.objects)
    already, moved_from = [], {}
    for obj in objs:
        if move:
            others = [c for c in bpy.data.collections if c is not coll and obj.name in c.objects]
            for other in others:
                other.objects.unlink(obj)
            # THE SCENE ROOT IS A COLLECTION TOO and is not in bpy.data.collections, so a
            # move that ignored it would leave the object linked at the top level and the
            # "moved" claim would be false.
            root = _scene_root()
            if obj.name in root.objects:
                root.objects.unlink(obj)
                others.append(root)
            if others:
                moved_from[obj.name] = sorted(c.name for c in others)
        if obj.name in coll.objects:
            already.append(obj.name)
            continue
        coll.objects.link(obj)

    # MEASURED ON THE COLLECTION, not on the return of link(). Every requested object must be in
    # there afterwards or this op is reporting a success it did not achieve.
    missing = [o.name for o in objs if o.name not in coll.objects]
    if missing:
        raise MifOpError("linked into '%s' but %d object(s) are not in it afterwards: %s. The "
                         "collection now holds %d object(s)."
                         % (coll.name, len(missing), ", ".join(missing[:10]), len(coll.objects)))
    return {
        "ok": True,
        "collection": coll.name,
        "linked": sorted(o.name for o in objs if o.name not in already),
        "alreadyPresent": sorted(already),
        "movedFrom": moved_from,
        "objectCountBefore": before,
        "objectCount": len(coll.objects),
        "inScene": coll.name in _in_scene_names(),
        # THE POINT OF THE WHOLE MODULE. If the collection is an orphan, filling it changes nothing
        # anybody can see, and that has to be said here rather than discovered at render time.
        "note": (None if coll.name in _in_scene_names() else
                 "this collection is NOT linked into the scene, so its objects are outside the "
                 "view layer and the render no matter what is in it. It is still a valid light "
                 "linking receiver or blocker, which is the one case where that is fine."),
    }


def op_unlink_objects(params):
    """Take objects out of a collection, refusing to strand one in no collection at all.

    AN OBJECT IN ZERO COLLECTIONS STILL EXISTS in bpy.data and is in NO scene: invisible in the
    viewport, absent from the render, and gone from the outliner. It is not deleted, so nothing
    warns, and it survives a save. That is the object-level twin of an orphaned collection and it is
    refused here rather than produced silently - pass allowOrphans to mean it.

    params:
      collection / name (str)   required
      objects (list[str])       or `object` for one. Required.
      allowOrphans (bool)       permit leaving an object in no collection. Default false.
    """
    reject_unknown(params, _UNLINK_KEYS, "unlink_objects")
    coll = _find_collection(take(params, "collection", "name", required=True, kind=str),
                            "unlink_objects")
    names = _object_names(params, "objects", "object")
    if not names:
        raise MifOpError("'objects' is required - a list of object names, or 'object' for one. "
                         "NOTHING was changed.")
    objs = [get_object(n) for n in names]
    allow = take_bool(params, "allowOrphans", default=False)

    absent = [o.name for o in objs if o.name not in coll.objects]
    if absent:
        raise MifOpError("%d object(s) are not in '%s' to begin with: %s. NOTHING was changed."
                         % (len(absent), coll.name, ", ".join(absent[:10])))

    # COMPUTED BEFORE THE UNLINK, on every object, so the refusal fires before any mutation rather
    # than partway through the list.
    would_strand = []
    root = _scene_root()
    for obj in objs:
        homes = set(_collections_of(obj))
        if obj.name in root.objects:
            homes.add(root.name)
        if homes - {coll.name}:
            continue
        would_strand.append(obj.name)
    if would_strand and not allow:
        raise MifOpError("this would leave %d object(s) in NO collection at all: %s. They would "
                         "still exist but be in no scene - invisible, unrendered and absent from "
                         "the outliner, with nothing to warn you. Link them somewhere else first, "
                         "delete them with delete_object, or pass allowOrphans:true to mean it. "
                         "NOTHING was changed."
                         % (len(would_strand), ", ".join(would_strand[:10])))

    before = len(coll.objects)
    for obj in objs:
        coll.objects.unlink(obj)
    still = [o.name for o in objs if o.name in coll.objects]
    if still:
        raise MifOpError("unlinked from '%s' but %d object(s) are still in it: %s."
                         % (coll.name, len(still), ", ".join(still[:10])))
    stranded = [o.name for o in objs if not _collections_of(o) and o.name not in root.objects]
    return {
        "ok": True,
        "collection": coll.name,
        "unlinked": sorted(o.name for o in objs),
        "objectCountBefore": before,
        "objectCount": len(coll.objects),
        "nowInNoCollection": sorted(stranded),
        "note": (None if not stranded else
                 "%d object(s) are now in NO collection: they exist but are in no scene. This was "
                 "requested with allowOrphans." % len(stranded)),
    }


def op_set_collection_visibility(params):
    """The four different things "hide this collection" can mean, taken by their real names.

    Two are global properties on the collection and two are PER VIEW LAYER properties on a
    LayerCollection, and writing the wrong one is a silent no-op that reads back as a success on the
    property that WAS written. So each is a distinctly named parameter, the response says which view
    layer a per-layer write landed on, and all four are read back.

    EXCLUDE IS NOT HIDING. An excluded collection leaves the depsgraph entirely: its objects are not
    evaluated, so constraints targeting them, drivers reading them and modifiers depending on them
    all change behaviour. That is reported as a distinct field rather than folded in with the rest.

    params:
      collection / name (str)   required
      viewLayer (str)           which view layer the per-layer writes apply to. Default active.
      hideViewport (bool)       GLOBAL monitor icon
      hideRender (bool)         GLOBAL camera icon
      exclude (bool)            PER VIEW LAYER checkbox - removes it from evaluation entirely
      hideInViewLayer (bool)    PER VIEW LAYER eye icon - the one people mean
      indirectOnly (bool)       PER VIEW LAYER - contributes only bounce light
      holdout (bool)            PER VIEW LAYER - punches alpha through the render
    """
    reject_unknown(params, _VIS_KEYS, "set_collection_visibility")
    coll = _find_collection(take(params, "collection", "name", required=True, kind=str),
                            "set_collection_visibility")
    vl_name = take(params, "viewLayer", kind=str)
    if vl_name:
        vl = bpy.context.scene.view_layers.get(vl_name)
        if vl is None:
            known = sorted(v.name for v in bpy.context.scene.view_layers)
            raise MifOpError("no view layer named '%s' in scene '%s'. Present: %s. NOTHING was "
                             "changed." % (vl_name, bpy.context.scene.name, ", ".join(known)))
    else:
        vl = bpy.context.view_layer

    per_layer = {"exclude": "exclude", "hideInViewLayer": "hide_viewport",
                 "indirectOnly": "indirect_only", "holdout": "holdout"}
    wanted_layer = {k: params[k] for k in per_layer if params.get(k) is not None}
    # CHECKED AS BOOLEANS FIRST. These went straight to setattr, and Blender coerces - a dict
    # became True, so a caller who sent garbage hid the collection from every render and was told
    # it had worked. take_bool refuses anything that is not a boolean, a number or a yes/no string.
    for _k in ("hideViewport", "hideRender"):
        if params.get(_k) is not None:
            take_bool(params, _k)
    wanted_global = {k: params[k] for k in ("hideViewport", "hideRender")
                     if params.get(k) is not None}
    if not wanted_layer and not wanted_global:
        raise MifOpError("nothing to do - pass at least one of hideViewport, hideRender, exclude, "
                         "hideInViewLayer, indirectOnly or holdout. NOTHING was changed.")

    lc = _layer_collection(coll.name, vl)
    if wanted_layer and lc is None:
        raise MifOpError("'%s' is not in view layer '%s' at all, so exclude, hideInViewLayer, "
                         "indirectOnly and holdout have nowhere to be written - those live on a "
                         "LayerCollection, which only exists for a collection linked into the "
                         "scene. Link it first. NOTHING was changed." % (coll.name, vl.name))

    if "hideViewport" in wanted_global:
        coll.hide_viewport = bool(wanted_global["hideViewport"])
    if "hideRender" in wanted_global:
        coll.hide_render = bool(wanted_global["hideRender"])
    for key, attr in per_layer.items():
        if key in wanted_layer:
            setattr(lc, attr, bool(wanted_layer[key]))

    lc = _layer_collection(coll.name, vl)     # re-fetch: excluding rebuilds the layer tree
    after = {
        "hideViewport": bool(coll.hide_viewport),
        "hideRender": bool(coll.hide_render),
        "exclude": bool(lc.exclude) if lc else None,
        "hideInViewLayer": bool(lc.hide_viewport) if lc else None,
        "indirectOnly": bool(lc.indirect_only) if lc else None,
        "holdout": bool(lc.holdout) if lc else None,
    }
    # EVERY REQUESTED WRITE VERIFIED INDIVIDUALLY. Four of these are on a different datablock from
    # the other two, and a version that silently wrote the collection-level property when the caller
    # meant the eye icon would read back as a perfect success on the wrong field.
    wrong = {k: (v, after.get(k)) for k, v in list(wanted_global.items()) + list(wanted_layer.items())
             if after.get(k) != bool(v)}
    if wrong:
        raise MifOpError("wrote %s to '%s' but it reads back as %s afterwards"
                         % ({k: v[0] for k, v in wrong.items()}, coll.name,
                            {k: v[1] for k, v in wrong.items()}))
    return {
        "ok": True,
        "collection": coll.name,
        "viewLayer": vl.name,
        "perViewLayerWrites": sorted(wanted_layer),
        "globalWrites": sorted(wanted_global),
        "objectCount": len(coll.objects),
        "excludedFromEvaluation": bool(after["exclude"]),
        "note": ("exclude removes the collection from the depsgraph entirely for view layer '%s' - "
                 "its objects stop being evaluated, so constraints, drivers and modifiers that "
                 "depend on them change behaviour too." % vl.name) if after["exclude"] else None,
        **after,
    }


def op_delete_collection(params):
    """Remove a collection, and say what happened to the objects that were in it.

    bpy.data.collections.remove() deletes the COLLECTION and leaves its objects alone - which sounds
    safe and is how objects end up in no collection at all: still in bpy.data, in no scene,
    invisible everywhere and surviving the save. So this decides that explicitly rather than by
    default. Objects that would be stranded are moved to the scene root unless reparentTo names
    somewhere else, or deleted outright if deleteObjects is set.

    Child collections are re-linked to the parent rather than orphaned, for the same reason.

    params:
      collection / name (str)   required
      deleteObjects (bool)      delete the objects too, instead of rehoming them. Default false.
      reparentTo (str)          where to rehome objects and child collections. Default scene root.
    """
    reject_unknown(params, _DELETE_KEYS, "delete_collection")
    name = take(params, "collection", "name", required=True, kind=str)
    coll = _find_collection(name, "delete_collection")
    if coll is _scene_root():
        raise MifOpError("that is the scene's root collection and cannot be deleted. NOTHING was "
                         "changed.")
    delete_objects = take_bool(params, "deleteObjects", default=False)
    to_name = take(params, "reparentTo", kind=str)
    target = _find_collection(to_name, "delete_collection") if to_name else _scene_root()
    if target is coll:
        raise MifOpError("reparentTo names the collection being deleted. NOTHING was changed.")

    members = list(coll.objects)
    children = list(coll.children)
    # WHICH OBJECTS WOULD BE STRANDED, computed before anything is removed. An object in another
    # collection as well is fine and must not be moved - doing so would silently reorganise a scene.
    stranded = [o for o in members
                if not (set(_collections_of(o)) - {coll.name}) and o.name not in _scene_root().objects]

    deleted, rehomed = [], []
    if delete_objects:
        for obj in list(members):
            deleted.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
    else:
        for obj in stranded:
            target.objects.link(obj)
            rehomed.append(obj.name)
    relinked = []
    for child in children:
        if child.name not in {c.name for c in target.children}:
            target.children.link(child)
            relinked.append(child.name)

    bpy.data.collections.remove(coll)

    # POSTCONDITION ON THE THING THAT MATTERS, which is not that the collection is gone - it is that
    # nothing was stranded by its going. Both are checked.
    if bpy.data.collections.get(name) is not None:
        raise MifOpError("removed '%s' but it is still in bpy.data.collections afterwards." % name)
    homeless = sorted(o.name for o in bpy.data.objects
                      if not _collections_of(o) and o.name not in _scene_root().objects)
    return {
        "ok": True,
        "deletedCollection": name,
        "objectsDeleted": sorted(deleted),
        "objectsRehomedTo": target.name if rehomed else None,
        "objectsRehomed": sorted(rehomed),
        "childCollectionsRelinked": sorted(relinked),
        "objectsInNoCollection": homeless,
        "note": ("%d object(s) are in no collection after this. That is not something this op did "
                 "on purpose - check them." % len(homeless)) if homeless else None,
    }


OPS = {
    "create_collection": op_create_collection,
    "list_collections": op_list_collections,
    "link_objects": op_link_objects,
    "unlink_objects": op_unlink_objects,
    "set_collection_visibility": op_set_collection_visibility,
    "delete_collection": op_delete_collection,
}
