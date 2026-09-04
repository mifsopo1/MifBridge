"""Material creation, inspection and per-face assignment.

WHAT WAS MISSING. The addon had exactly one material verb, set_material_slots, and it
assigns NAMES to slots on purpose - its own docstring says "a material's content is
Unreal's business". That was the right call for a round trip, where the material really
does live in Unreal and Blender only has to keep the slot ORDER lined up. It is the wrong
shape for building an asset IN Blender, which is what Andre asked for on 2026-08-30: there
was no way to create a material, set a shading value, or read back what a material holds.
There was no material READ op of any kind - object_info reports slot names and nothing
about what is in them.

=============================================================================
THE HARD PART IS NOT THE NODES, IT IS THE VERSION SPREAD
=============================================================================
This addon supports Blender 3.6 through 5.0, and Blender RENAMED Principled BSDF inputs
across that range:

    3.6              4.0+                       what it is
    "Specular"       "Specular IOR Level"       the specular level
    "Emission"       "Emission Color"           the emission colour
    "Transmission"   "Transmission Weight"      transmission
    "Sheen"          "Sheen Weight"             sheen
    "Clearcoat"      "Coat Weight"              clearcoat

A node input is looked up by string, and `node.inputs["Specular"]` on 4.x raises KeyError -
but the far worse failure is the one that does NOT raise: writing to an input that exists
under a different name on another version means the value silently lands nowhere on that
version, and the material looks subtly wrong with nothing to read. So every property here
resolves through an ALIAS LIST, and a name that resolves on NO alias is REFUSED by name
with the inputs that actually exist on this Blender. Never skipped, never defaulted.

set_material_properties reports `resolvedInputs` - the real socket name each requested
property landed on - so a caller can see which spelling this Blender used rather than
having to know.
"""
import os

import bpy

from .ops_common import (MifOpError, get_object, jsonable, reject_unknown, rnd, take,
                         take_bool, take_float, take_int,
                         select_only, selection_restore, selection_snapshot)

# Requested name -> the socket names it may be called on some supported Blender.
# Order matters only for reporting; the first that EXISTS on this build wins.
# normalStrength is DELIBERATELY ABSENT. It is not a Principled socket - normal strength lives on a
# separate Normal Map node - so it cannot be written here. It was briefly listed with an empty
# alias tuple and then filtered out of the write loop, which meant reject_unknown ACCEPTED it and
# the handler silently ignored it: the exact silent no-op this module's docstring calls the worst
# outcome. Leaving it out means a caller passing it gets a refusal naming the real properties.
PRINCIPLED_ALIASES = {
    "baseColor":    ("Base Color",),
    "metallic":     ("Metallic",),
    "roughness":    ("Roughness",),
    "specular":     ("Specular IOR Level", "Specular"),
    "ior":          ("IOR",),
    "alpha":        ("Alpha",),
    "emissive":     ("Emission Color", "Emission"),
    "emissiveStrength": ("Emission Strength",),
    "transmission": ("Transmission Weight", "Transmission"),
    "sheen":        ("Sheen Weight", "Sheen"),
    "clearcoat":    ("Coat Weight", "Clearcoat"),
    "anisotropic":  ("Anisotropic",),
}

# A UNION OF TWO LITERALS, not a computed tuple. parity_check.py can resolve `{...} | set(NAME)`
# where NAME is a module-level dict literal, and refuses anything it cannot read statically - which
# is right, because an unverifiable parameter list is where drift hides.
SET_MATERIAL_PROPERTY_PARAMS = {"material", "name"} | set(PRINCIPLED_ALIASES)


COLOR_PROPS = ("baseColor", "emissive")


def _principled(mat):
    """The Principled BSDF node, or a refusal naming what the material actually has."""
    if not mat.use_nodes or mat.node_tree is None:
        raise MifOpError(
            "material '%s' has no node tree (use_nodes is off), so it has no Principled BSDF "
            "to write to. create_material makes one; a material imported without nodes has to "
            "be converted by hand." % mat.name)
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    kinds = sorted({n.type for n in mat.node_tree.nodes})
    raise MifOpError(
        "material '%s' has no Principled BSDF node - its tree holds: %s. This op writes "
        "Principled inputs; a material built on a different shader needs its nodes edited "
        "directly." % (mat.name, ", ".join(kinds) or "(nothing)"))


def _resolve_input(node, prop):
    """The real socket for a requested property on THIS Blender, or None."""
    for alias in PRINCIPLED_ALIASES.get(prop, ()):
        if alias in node.inputs:
            return node.inputs[alias]
    return None


def _material_json(mat, deep=False):
    out = {
        "name": mat.name,
        "users": mat.users,
        "useNodes": bool(mat.use_nodes),
        "blendMethod": getattr(mat, "blend_method", None),
    }
    if not mat.use_nodes or mat.node_tree is None:
        out["diffuseColor"] = rnd(list(mat.diffuse_color))
        out["note"] = ("this material has no node tree, so it has no shading inputs to report. "
                       "Its viewport diffuse_color is all there is.")
        return out
    nodes = mat.node_tree.nodes
    out["nodeCount"] = len(nodes)
    out["nodeTypes"] = sorted({n.type for n in nodes})

    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        props = {}
        resolved = {}
        for prop in PRINCIPLED_ALIASES:
            sock = _resolve_input(bsdf, prop)
            if sock is None:
                continue
            resolved[prop] = sock.name
            try:
                val = sock.default_value
                props[prop] = rnd(list(val)) if hasattr(val, "__len__") else round(float(val), 6)
            except Exception:  # noqa: BLE001
                continue
        out["principled"] = props
        # The socket NAMES this Blender used. Reported because they differ by version and a
        # caller writing them back needs to know which spelling landed.
        out["resolvedInputs"] = resolved
    else:
        out["principled"] = None
        out["note"] = "no Principled BSDF in this material's node tree"

    # IMAGE TEXTURES AND THEIR PATHS. This is the field an Unreal-side import has to resolve,
    # so it is reported whether or not `deep` was asked for.
    textures = []
    for n in nodes:
        if n.type == "TEX_IMAGE" and n.image is not None:
            entry = {
                "node": n.name,
                "image": n.image.name,
                "filepath": n.image.filepath,
                "size": list(n.image.size),
                "packed": bool(n.image.packed_file),
            }
            linked = []
            for outsock in n.outputs:
                for link in outsock.links:
                    linked.append("%s.%s" % (link.to_node.name, link.to_socket.name))
            entry["linkedTo"] = linked
            textures.append(entry)
    out["textures"] = textures
    out["textureCount"] = len(textures)

    if deep:
        out["links"] = [
            {"from": "%s.%s" % (l.from_node.name, l.from_socket.name),
             "to": "%s.%s" % (l.to_node.name, l.to_socket.name)}
            for l in mat.node_tree.links
        ]
    return out


# ---------------------------------------------------------------------------
def op_create_material(params):
    """Create a material with a Principled BSDF, and report the name Blender gave it.

    THE NAME IS ECHOED FROM THE MATERIAL, NOT FROM THE REQUEST. bpy.data.materials.new() silently
    appends .001 on a collision, so a caller who asked for "Wood" can end up with "Wood.003" and
    never know. Reporting the requested name back would be wrong roughly as often as a scene has a
    name clash in it, which is often.

    `reuse` RETURNS THE EXISTING MATERIAL rather than a numbered copy - the idempotent-create shape
    a pipeline uses by default. It APPLIES the inline values too. An earlier version returned early
    on that path, silently discarding baseColor/metallic/roughness while attaching a note claiming
    "the end state you asked for is already in place" - which was false precisely when a shading
    value had been passed, on the most common call shape there is.

    REUSE ALSO REPAIRS use_nodes. bpy.data.materials.new() leaves nodes OFF on 3.6, 4.2 and 4.4 and
    ON only on 5.0, and set_material_slots creates bare materials with no node tree at all - so a
    reused material may have no Principled BSDF, contradicting this op's whole contract. It is
    switched on and the BSDF verified, rather than handing back something set_material_properties
    would then refuse.

    NOTHING IS CREATED UNTIL EVERY VALUE VALIDATES. The material used to be created first, so a
    refusal left a new datablock behind while the message said "NOTHING was changed".
    """
    reject_unknown(params, ("name", "reuse", "baseColor", "metallic", "roughness"),
                   "create_material")
    name = take(params, "name", required=True, kind=str)
    reuse = take_bool(params, "reuse", default=False)
    inline = {p: params[p] for p in ("baseColor", "metallic", "roughness")
              if p in params and params[p] is not None}

    existing = bpy.data.materials.get(name)
    mat = None
    created = False
    if existing is not None and reuse:
        mat = existing
        if not mat.use_nodes or mat.node_tree is None:
            mat.use_nodes = True
    else:
        # DEFERRED until the values are known-good: see the docstring.
        pass

    if mat is None:
        # Validate what we can before creating anything. The socket check needs a BSDF, so the
        # remaining validation happens against the new material and is rolled back on failure.
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        created = True

    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        if created:
            bpy.data.materials.remove(mat)
        raise MifOpError(
            "material '%s' has no Principled BSDF node%s. NOTHING usable was produced."
            % (name, " even with use_nodes on, which should not happen on any supported Blender"
               if created else " - it was created without one, and this op cannot add it"))

    applied = {}
    try:
        # RESOLVE AND COERCE EVERYTHING FIRST, then write. A bad value must not leave a
        # half-configured material, and must not leave a new one behind at all.
        staged = []
        for prop, val in inline.items():
            sock = _resolve_input(bsdf, prop)
            if sock is None:
                raise MifOpError(
                    "this Blender's Principled BSDF has no input for '%s' (tried: %s). Available "
                    "inputs: %s. NOTHING was changed."
                    % (prop, ", ".join(PRINCIPLED_ALIASES[prop]) or "(none)",
                       ", ".join(i.name for i in bsdf.inputs)))
            _check_unlinked(sock, prop)
            staged.append((prop, sock, _coerce_socket(sock, prop, val)))
        for prop, sock, value in staged:
            sock.default_value = value
            applied[prop] = sock.name
    except MifOpError:
        if created:
            bpy.data.materials.remove(mat)
        raise

    out = _material_json(mat)
    out["created"] = created
    out["requestedName"] = name
    if applied:
        out["resolvedInputs"] = applied
        out["applied"] = sorted(applied)
    if not created:
        out["note"] = (
            "a material with that name already existed and reuse was requested, so it was NOT "
            "recreated." + (" The values you passed were applied to it - see applied[]."
                            if applied else " No shading values were passed, so nothing about it "
                                            "changed."))
    elif mat.name != name:
        out["nameNote"] = ("Blender renamed this to '%s' because '%s' was already taken - new() "
                           "never overwrites and never fails, it appends a number. Pass reuse:true "
                           "to get the existing material instead." % (mat.name, name))
    return out


def _coerce_socket(sock, prop, val):
    """Validate a value for a socket and return what would be written. Writes NOTHING.

    SPLIT OUT FROM THE WRITE so every value in a call can be checked before any is applied. An
    earlier version validated inside the write loop, so a bad value on the fifth property left the
    first four already written - while the docstring promised the opposite.
    """
    if prop in COLOR_PROPS or hasattr(sock.default_value, "__len__"):
        if not hasattr(val, "__len__") or isinstance(val, str):
            raise MifOpError("'%s' takes a colour as [r,g,b] or [r,g,b,a], got %r" % (prop, val))
        vals = [float(x) for x in val]
        want = len(sock.default_value)
        if len(vals) == 3 and want == 4:
            vals.append(1.0)          # alpha defaults to opaque rather than to zero
        if len(vals) != want:
            raise MifOpError("'%s' expects %d components on this Blender, got %d"
                             % (prop, want, len(vals)))
        return vals
    return float(val)


def _check_unlinked(sock, prop):
    """A LINKED socket ignores default_value entirely, so writing one changes nothing that renders.

    Blender evaluates the incoming link and never reads default_value on a connected input. Writing
    it anyway succeeds at the Python level and shows up when read back, so the op would report the
    new value while the material rendered and exported exactly as before - a silent no-op that
    confirms itself. Refused instead, naming what is driving the socket.
    """
    if sock.is_linked:
        src = sock.links[0].from_node if sock.links else None
        raise MifOpError(
            "'%s' is LINKED to %s, and a connected input ignores its default value completely - "
            "Blender evaluates the link instead. Writing it would change nothing that renders or "
            "exports while still reading back as the new value. Disconnect the link first, or edit "
            "the node driving it. NOTHING was changed."
            % (prop, ("node '%s'" % src.name) if src else "another node"))


def op_set_material_properties(params):
    """Write Principled BSDF values on an existing material, resolving names per version.

    EVERY PROPERTY IS RESOLVED THROUGH AN ALIAS LIST, and one that resolves on NO alias is
    REFUSED rather than skipped. Blender renamed these inputs between 3.6 and 4.0 -
    "Specular" became "Specular IOR Level", "Emission" became "Emission Color" - and this
    addon supports both. Writing to a name that does not exist on the running version would
    land nowhere and leave a material that looks wrong with nothing to read, which is the
    exact silent-failure shape the rest of this project refuses.

    `resolvedInputs` reports the real socket each property landed on, so a caller can see
    which spelling this Blender used.
    """
    reject_unknown(params, SET_MATERIAL_PROPERTY_PARAMS, "set_material_properties")
    mat_name = take(params, "material", "name", required=True, kind=str)
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        have = sorted(m.name for m in bpy.data.materials)[:25]
        raise MifOpError("no material named '%s'. This file has: %s"
                         % (mat_name, ", ".join(have) or "(none)"))
    bsdf = _principled(mat)

    wanted = {k: params[k] for k in PRINCIPLED_ALIASES if k in params}
    if not wanted:
        raise MifOpError(
            "no property to set. Accepted: %s. NOTHING was changed."
            % ", ".join(sorted(PRINCIPLED_ALIASES)))

    resolved = {}
    unresolved = []
    # RESOLVE EVERY NAME BEFORE WRITING ANY, so a bad one in the middle cannot leave the
    # material half-written - the same all-or-nothing rule the UE side's show flags use.
    for prop in wanted:
        sock = _resolve_input(bsdf, prop)
        if sock is None:
            unresolved.append(prop)
        else:
            resolved[prop] = sock
    if unresolved:
        raise MifOpError(
            "this Blender (%s) has no Principled input for: %s. Tried these spellings: %s. "
            "The inputs that DO exist are: %s. Nothing was written - the whole call is "
            "refused so a rename cannot half-apply."
            % (bpy.app.version_string, ", ".join(sorted(unresolved)),
               "; ".join("%s -> %s" % (p, "/".join(PRINCIPLED_ALIASES[p]) or "(none)")
                         for p in sorted(unresolved)),
               ", ".join(i.name for i in bsdf.inputs)))

    # EVERY VALUE COERCED AND EVERY SOCKET LINK-CHECKED BEFORE THE FIRST WRITE. The names were
    # already resolved above; doing the values in the same pass is what makes "nothing was written"
    # true on a refusal rather than merely claimed.
    staged = {}
    for prop, sock in resolved.items():
        _check_unlinked(sock, prop)
        staged[prop] = _coerce_socket(sock, prop, wanted[prop])
    for prop, sock in resolved.items():
        sock.default_value = staged[prop]

    out = _material_json(mat)
    out["applied"] = sorted(wanted)
    out["resolvedInputs"] = {p: s.name for p, s in resolved.items()}
    out["blenderVersion"] = bpy.app.version_string
    return out


def op_list_materials(params):
    """Every material in the file, with its user count. The read half that did not exist.

    `unused` (users == 0) matters for export: a material with no users is not written to an
    FBX at all, so a material that was created and never assigned silently does not arrive
    in Unreal.
    """
    reject_unknown(params, ("nameContains", "usedOnly"), "list_materials")
    contains = take(params, "nameContains", kind=str)
    used_only = take_bool(params, "usedOnly", default=False)

    rows = []
    for mat in bpy.data.materials:
        if contains and contains.lower() not in mat.name.lower():
            continue
        if used_only and mat.users == 0:
            continue
        rows.append({
            "name": mat.name,
            "users": mat.users,
            "useNodes": bool(mat.use_nodes),
            "hasPrincipled": bool(
                mat.use_nodes and mat.node_tree
                and any(n.type == "BSDF_PRINCIPLED" for n in mat.node_tree.nodes)),
        })
    unused = [r["name"] for r in rows if r["users"] == 0]
    out = {"materials": rows, "count": len(rows), "unused": unused}
    if unused:
        out["unusedNote"] = ("%d material(s) have no users. A material with no users is NOT "
                             "written to an FBX, so one created and never assigned will not "
                             "arrive in Unreal." % len(unused))
    return out


def op_describe_material(params):
    """One material in full: Principled values, node tree shape, and texture file paths.

    The texture paths are the point for a pipeline - they are what an Unreal-side import has
    to resolve, and nothing in the addon reported them before.
    """
    reject_unknown(params, ("material", "name", "links"), "describe_material")
    mat_name = take(params, "material", "name", required=True, kind=str)
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        have = sorted(m.name for m in bpy.data.materials)[:25]
        raise MifOpError("no material named '%s'. This file has: %s"
                         % (mat_name, ", ".join(have) or "(none)"))
    return _material_json(mat, deep=take_bool(params, "links", default=False))


_BAKE_TYPES = {
    "AO": "AO",
    "NORMAL": "NORMAL",
    "DIFFUSE": "DIFFUSE",
    "COMBINED": "COMBINED",
    "ROUGHNESS": "ROUGHNESS",
    "EMIT": "EMIT",
    "GLOSSY": "GLOSSY",
    "SHADOW": "SHADOW",
}


def _pixel_signature(image):
    """(min, max, mean) over the raw float buffer - the cheapest honest 'did anything change'."""
    px = image.pixels[:]
    if not px:
        return (0.0, 0.0, 0.0)
    return (round(min(px), 6), round(max(px), 6), round(sum(px) / len(px), 6))


def op_bake_texture(params):
    """Bake AO / normal / diffuse and the rest into an image, and prove the image actually moved.

    THE FAILURE THIS IS ARRANGED AROUND IS A SILENT SUCCESS, measured rather than assumed. With no
    ACTIVE image-texture node in the material, bpy.ops.object.bake returns {'FINISHED'} and writes
    nothing at all - no error, no warning, an untouched image and a call that looks like it worked.
    That is the worst outcome available here, because the caller then saves a blank PNG and wires it
    into a material.

    So success is judged from the IMAGE: is_dirty plus a before/after pixel signature. A bake that
    reports FINISHED over an unchanged buffer is reported as a failure, which is what it is.

    WHAT IS LOUD ALREADY, and therefore not re-implemented: a missing UV layer. The operator raises
    "No active UV layer found in the object" itself. It is still checked up front, because a
    pre-flight refusal names the fix and costs nothing, and because the entry that asked for this
    predicted that case would be silent - it is not, and that is worth recording where the next
    person will look.

    THE SETUP IS RESTORED. Render engine, device, sample count and the whole selection state belong
    to whoever is using this Blender; a bake that leaves the scene on CYCLES with 4096 samples is a
    side effect nobody asked for.
    """
    reject_unknown(params, ("object", "name", "type", "bakeType", "width", "height", "imageName",
                            "filepath", "uvLayer", "margin", "samples", "keepNode", "device"),
                   "bake_texture")
    obj = get_object(take(params, "object", "name", required=True, kind=str), want_mesh=True)

    raw_type = take(params, "type", "bakeType") or "AO"
    bake_type = str(raw_type).strip().upper()
    if bake_type not in _BAKE_TYPES:
        raise MifOpError("unknown bake type %r - use one of %s. NOTHING was baked."
                         % (raw_type, ", ".join(sorted(_BAKE_TYPES))))

    mesh = obj.data
    if not mesh.polygons:
        raise MifOpError("'%s' has no faces, so there is nothing to bake. NOTHING was baked."
                         % obj.name)

    # PRE-FLIGHT, even though the operator raises for this itself. Its message names the object;
    # this one names the fix.
    uv_name = take(params, "uvLayer")
    if not mesh.uv_layers:
        raise MifOpError(
            "'%s' has NO UV layer, and a bake writes through UVs - there is nowhere for the result "
            "to land. uv_unwrap creates one. (The operator raises for this too rather than failing "
            "silently, so this refusal is only about naming the fix.) NOTHING was baked."
            % obj.name)
    if uv_name:
        layer = mesh.uv_layers.get(uv_name)
        if layer is None:
            raise MifOpError("'%s' has no UV layer named %r. It has: %s. NOTHING was baked."
                             % (obj.name, uv_name, ", ".join(u.name for u in mesh.uv_layers)))
        mesh.uv_layers.active = layer

    width = take_int(params, "width", default=512)
    height = take_int(params, "height", default=512)
    if width < 1 or height < 1 or width > 8192 or height > 8192:
        raise MifOpError("width/height must be between 1 and 8192; got %dx%d" % (width, height))
    samples = take_int(params, "samples", default=16)
    if samples < 1 or samples > 4096:
        raise MifOpError("samples must be between 1 and 4096; got %d" % samples)
    margin = take_int(params, "margin", default=4)
    image_name = take(params, "imageName") or ("MifBake_%s_%s" % (obj.name, bake_type))
    filepath = take(params, "filepath")
    keep_node = bool(take(params, "keepNode"))

    # A material is where the bake TARGET lives, so one is required. Created rather than refused,
    # and reported - an AO bake on an unmaterialed mesh is a perfectly reasonable request.
    created_material = None
    if not obj.data.materials or all(sl.material is None for sl in obj.material_slots):
        mat = bpy.data.materials.new("%s_BakeMat" % obj.name)
        mat.use_nodes = True
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
        created_material = mat.name

    materials = [sl.material for sl in obj.material_slots if sl.material]
    for mat in materials:
        if not mat.use_nodes:
            mat.use_nodes = True

    # A SENTINEL FILL, because "the image did not change" is NOT the same question as "the bake
    # wrote nothing". A fresh image is black, and a legitimately black bake result - AO on a face
    # with nothing to occlude it, an EMIT pass on an unlit material - leaves the buffer identical
    # to an untouched one. is_dirty does not separate them either: it goes true merely from the
    # bake touching the image. Filling with magenta first means ANY value the bake writes differs
    # from the start state, so "unchanged" means untouched and nothing else.
    image = bpy.data.images.new(image_name, width=width, height=height)
    image.generated_color = (1.0, 0.0, 1.0, 1.0)

    # NON-COLOUR BAKES MUST NOT CARRY AN sRGB TRANSFER, and images.new() defaults to sRGB. Nothing
    # here set colorspace_settings before 2026-09-03, so every NORMAL, ROUGHNESS, AO and SHADOW map
    # this op has ever written went to disk with a gamma curve applied to data that is not colour -
    # a surface direction, a scalar mask. Unreal, Unity, Godot and glTF all read those channels
    # linearly, so the result is wrong everywhere in the same direction: normals too shallow,
    # roughness too bright.
    #
    # It is invisible in review because the map still LOOKS like a normal map, and invisible in this
    # op's own postcondition because the magenta-sentinel check asks whether the buffer CHANGED, not
    # whether what landed in it is right. A pixel signature cannot catch a transfer curve.
    #
    # DIFFUSE, COMBINED, EMIT and GLOSSY are radiometric colour and stay sRGB. The split is by what
    # the channel MEANS, which is why it is a table rather than a rule about scalars.
    _NON_COLOUR_BAKES = {"NORMAL", "ROUGHNESS", "AO", "SHADOW"}
    colour_space = None
    if bake_type in _NON_COLOUR_BAKES:
        # Named by the OCIO config, not remembered: 'Non-Color' on stock Blender, but a studio
        # config may spell it differently, and silently leaving sRGB is the bug being fixed.
        for want in ("Non-Color", "Non-Colour", "Raw", "Generic Data"):
            try:
                image.colorspace_settings.name = want
                colour_space = want
                break
            except (TypeError, ValueError):
                continue
        if colour_space is None:
            raise MifOpError(
                "a %s bake must not be written with an sRGB transfer, and this Blender's colour "
                "config offers none of Non-Color/Non-Colour/Raw/Generic Data to say so. Writing it "
                "anyway would produce a map that is wrong in every engine while looking correct. "
                "NOTHING was baked." % bake_type)
    else:
        colour_space = image.colorspace_settings.name
    if image.name != image_name:
        # Blender uniquifies silently, and a caller who then looks up image_name finds the OLD one.
        note_renamed = image.name
    else:
        note_renamed = None

    scene = bpy.context.scene
    prev = {
        "engine": scene.render.engine,
        "samples": getattr(getattr(scene, "cycles", None), "samples", None),
        "device": getattr(getattr(scene, "cycles", None), "device", None),
        "active": bpy.context.view_layer.objects.active,
    }
    snap = selection_snapshot()
    added_nodes = []
    try:
        scene.render.engine = "CYCLES"
        if hasattr(scene, "cycles"):
            scene.cycles.samples = samples
            # CPU by default: a headless box may have no configured GPU device at all, and a bake
            # that silently falls back is a bake nobody can reason about.
            scene.cycles.device = str(take(params, "device") or "CPU").upper()

        for mat in materials:
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            node.name = "MifBakeTarget"
            added_nodes.append((mat, node))
            for other in mat.node_tree.nodes:
                other.select = False
            node.select = True
            mat.node_tree.nodes.active = node

        # SELECTION IS PART OF THE CONTRACT: bake reads the selected objects and writes into the
        # ACTIVE one. Leaving a stray selection from earlier work is one of the ways this produces
        # nothing while reporting FINISHED.
        select_only([obj])
        bpy.context.view_layer.objects.active = obj

        before = _pixel_signature(image)
        kwargs = {"type": bake_type, "margin": margin, "use_clear": True}
        if bake_type == "DIFFUSE":
            kwargs["pass_filter"] = {"COLOR"}
        bpy.ops.object.bake(**kwargs)
        after = _pixel_signature(image)
    except Exception as exc:
        for mat, node in added_nodes:
            try:
                mat.node_tree.nodes.remove(node)
            except Exception:  # noqa: BLE001
                pass
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass
        raise MifOpError("bake failed on '%s' (%s): %s: %s. NOTHING was written."
                         % (obj.name, bake_type, type(exc).__name__, exc))
    finally:
        scene.render.engine = prev["engine"]
        if hasattr(scene, "cycles"):
            if prev["samples"] is not None:
                scene.cycles.samples = prev["samples"]
            if prev["device"] is not None:
                scene.cycles.device = prev["device"]
        selection_restore(snap)
        try:
            bpy.context.view_layer.objects.active = prev["active"]
        except Exception:  # noqa: BLE001
            pass

    # THE POSTCONDITION. bake returns {'FINISHED'} with no active image-texture node and writes
    # nothing whatsoever - measured, not feared. So the image is asked whether it moved.
    # CAPTURED BEFORE THE IMAGE IS REMOVED. The first version read image.is_dirty in the message
    # AFTER bpy.data.images.remove(image), which is a use-after-free on freed RNA - the same
    # mistake boolean_op made against a modifier earlier the same night, and Blender says so:
    # "ReferenceError: StructRNA of type Image has been removed".
    was_dirty = image.is_dirty
    if not was_dirty or before == after:
        for mat, node in added_nodes:
            try:
                mat.node_tree.nodes.remove(node)
            except Exception:  # noqa: BLE001
                pass
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass
        raise MifOpError(
            "the bake reported FINISHED and the image is still the magenta sentinel it was "
            "filled with (signature %s, dirty=%s) - so nothing was written. That is "
            "bpy.ops.object.bake's silent-success case: with no active image-texture node to bake "
            "into it returns FINISHED, touches nothing and reports no error at all. The sentinel "
            "exists so this is distinguishable from a legitimately BLACK result, which a fresh "
            "image would have matched exactly. The image was discarded rather than handed back "
            "blank." % (before, was_dirty))

    saved = None
    if filepath:
        image.filepath_raw = str(filepath)
        ext = os.path.splitext(str(filepath))[1].lower()
        image.file_format = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
                             ".tga": "TARGA", ".exr": "OPEN_EXR"}.get(ext, "PNG")
        image.save()
        saved = str(filepath)
        if not os.path.isfile(saved):
            raise MifOpError("image.save() reported no error but '%s' is not on disk. The bake "
                             "itself succeeded." % saved)

    if not keep_node:
        for mat, node in added_nodes:
            try:
                mat.node_tree.nodes.remove(node)
            except Exception:  # noqa: BLE001
                pass

    result = {
        "object": obj.name,
        "bakeType": bake_type,
        "image": image.name,
        # WHICH TRANSFER THE FILE CARRIES. A normal or roughness map written as sRGB is wrong in
        # every engine and looks fine to the eye, so the caller is told rather than left to assume.
        # Read back off the image, not from the table, so it reports what was actually set.
        "colorSpace": image.colorspace_settings.name,
        "colorSpaceNote": ("NORMAL/ROUGHNESS/AO/SHADOW are data, not colour, and are written with "
                           "no sRGB transfer. DIFFUSE/COMBINED/EMIT/GLOSSY are colour and keep it."),
        "width": width,
        "height": height,
        "samples": samples,
        "margin": margin,
        "uvLayer": mesh.uv_layers.active.name if mesh.uv_layers.active else None,
        "materials": [m.name for m in materials],
        "createdMaterial": created_material,
        "signatureBefore": list(before),
        "signatureAfter": list(after),
        "changed": True,
        "savedTo": saved,
        "targetNodeKept": keep_node,
    }
    if note_renamed:
        result["imageRenamed"] = note_renamed
        result["renameNote"] = (
            "an image named %r already existed, so Blender uniquified this one to %r. Look it up "
            "by the returned name, not the one you asked for." % (image_name, note_renamed))
    result["note"] = (
        "the image is IN MEMORY%s. Nothing else references it - the bake target node was %s - so "
        "it is lost on file reload unless it was saved."
        % ("" if saved else " and NOT saved; pass filepath to write it to disk",
           "kept" if keep_node else "removed"))
    return result


def op_assign_material_to_faces(params):
    """Point a range of polygons at one of the object's material SLOTS.

    set_material_slots decides WHICH materials a mesh has and in what order; this decides
    which faces use which. Multi-material meshes built in Blender need both, and only the
    first existed.

    ADDRESSED BY SLOT INDEX, not by material name, because the slot index is what a polygon
    actually stores (`polygon.material_index`) and what Unreal's FStaticMaterial array lines
    up against on import. A name would have to be resolved to an index anyway, and would be
    ambiguous the moment two slots hold the same material.

    `faces` omitted means EVERY polygon. A face index past the end is REFUSED rather than
    ignored, and `changed` reports how many polygons actually moved - a selection that
    matches nothing is otherwise indistinguishable from success.

    `fromSlot` selects every polygon CURRENTLY on that slot, which is the operation you
    want after set_material_slots reorders or resizes the slot list. Without it that costs
    a read, a client-side filter and a write of an explicit index list. It is REFUSED when
    no polygon uses that slot, unlike an empty `faces` list: asking for nothing is a
    request, but believing faces live on an empty slot is a wrong assumption about the
    mesh, and changed:0 would let it pass as success.
    """
    reject_unknown(params, ("object", "name", "slot", "slotIndex", "faces", "fromSlot"),
                   "assign_material_to_faces")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    slot = take_int(params, "slot", "slotIndex", required=True)
    mesh = obj.data

    if not obj.material_slots:
        raise MifOpError(
            "'%s' has no material slots, so there is nothing to assign to. set_material_slots "
            "creates them." % obj.name)
    if slot < 0 or slot >= len(obj.material_slots):
        raise MifOpError(
            "slot %d is out of range - '%s' has %d slot(s) (0..%d). Faces store the slot INDEX, "
            "so an out-of-range one would render as the last slot with no error at all."
            % (slot, obj.name, len(obj.material_slots), len(obj.material_slots) - 1))

    total = len(mesh.polygons)
    if total == 0:
        raise MifOpError(
            "'%s' has NO polygons, so there is nothing to assign a material to. Without this check "
            "the loop below would simply not run and the op would report changed:0 with no error - "
            "the exact indistinguishable-from-success case it exists to prevent. NOTHING was "
            "changed." % obj.name)
    faces = params.get("faces")
    from_slot = params.get("fromSlot")
    if faces is not None and from_slot is not None:
        raise MifOpError(
            "pass EITHER faces OR fromSlot, not both - they are two ways of naming the same "
            "selection and combining them would silently pick one. NOTHING was changed.")

    if from_slot is not None:
        # A slot-to-slot remap: every polygon CURRENTLY on from_slot moves to slot. This is the
        # operation you want after set_material_slots reorders or resizes the list.
        if not isinstance(from_slot, int) or isinstance(from_slot, bool) \
                or from_slot < 0 or from_slot >= len(obj.material_slots):
            raise MifOpError(
                "fromSlot %r is out of range - '%s' has %d slot(s) (0..%d). NOTHING was changed."
                % (from_slot, obj.name, len(obj.material_slots), len(obj.material_slots) - 1))
        targets = [p.index for p in mesh.polygons if p.material_index == from_slot]
        if not targets:
            # DIFFERENT FROM faces:[] ON PURPOSE. An empty faces list is a caller asking for
            # nothing; an empty fromSlot is a caller who believes faces live on a slot that is
            # empty - a wrong assumption about the mesh, and changed:0 would let it pass.
            raise MifOpError(
                "no polygon on '%s' is currently using slot %d, so this would move nothing and "
                "report changed:0 as though it had worked. The slots in use are: %s. NOTHING was "
                "changed."
                % (obj.name, from_slot,
                   ", ".join("%d" % i for i in sorted({p.material_index for p in mesh.polygons}))))
    elif faces is None:
        targets = range(total)
    else:
        if not hasattr(faces, "__len__") or isinstance(faces, str):
            raise MifOpError("faces must be a list of polygon indices, or omitted for all")
        if len(faces) == 0:
            raise MifOpError(
                "faces:[] is empty - that assigns nothing and would report changed:0 as though it "
                "had worked. Omit `faces` to assign every polygon. NOTHING was changed.")
        bad = [f for f in faces if not isinstance(f, int) or f < 0 or f >= total]
        if bad:
            raise MifOpError(
                "face index/indices out of range: %s - this mesh has %d polygon(s) (0..%d). "
                "Refused rather than skipped, because a silently ignored index leaves the mesh "
                "looking assigned when it is not." % (bad[:10], total, total - 1))
        targets = faces

    changed = 0
    for i in targets:
        if mesh.polygons[i].material_index != slot:
            mesh.polygons[i].material_index = slot
            changed += 1
    mesh.update()

    counts = {}
    for poly in mesh.polygons:
        counts[poly.material_index] = counts.get(poly.material_index, 0) + 1
    return {
        "object": obj.name,
        "slot": slot,
        "slotMaterial": (obj.material_slots[slot].material.name
                         if obj.material_slots[slot].material else None),
        # len(targets), NOT the mesh size. `faces is None` is true on the fromSlot branch
        # too, where targets is only the polygons currently on that slot - so moving 12
        # faces off a slot on a 121-polygon mesh reported requested:121 changed:12 and read
        # as 109 silently skipped, when every face asked for had landed. fromSlot exists to
        # repair slot order, which is exactly when somebody is counting.
        "requested": len(targets),
        # MEASURED, not requested: assigning a face to the slot it already had is a no-op,
        # and reporting the request as the result would hide that.
        "changed": changed,
        "polygonCount": total,
        "facesPerSlot": {str(k): v for k, v in sorted(counts.items())},
    }


OPS = {
    "create_material": op_create_material,
    "set_material_properties": op_set_material_properties,
    "list_materials": op_list_materials,
    "describe_material": op_describe_material,
    "bake_texture": op_bake_texture,
    "assign_material_to_faces": op_assign_material_to_faces,
}
