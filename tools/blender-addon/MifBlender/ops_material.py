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
import bpy

from .ops_common import (MifOpError, get_object, jsonable, reject_unknown, rnd, take,
                         take_bool, take_float, take_int)

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
    """
    reject_unknown(params, ("object", "name", "slot", "slotIndex", "faces"),
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
    if faces is None:
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
        "requested": total if faces is None else len(faces),
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
    "assign_material_to_faces": op_assign_material_to_faces,
}
