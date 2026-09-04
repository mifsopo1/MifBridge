"""Particle systems: emitters for smoke and steam, and hair for scattered debris.

WHY BOTH TYPES ARE ONE OP. Blender's two particle types are the same datablock with a switch, and
they answer two completely different questions - EMITTER throws particles over time (steam from a
pipe, sparks), HAIR instances geometry across a surface and does not move (grass, rubble, cables on
a wall). Splitting them into two ops would duplicate every shared setting; keeping them together
means the type-specific ones have to be REFUSED on the wrong type, which is the same discipline
create_light uses for SPOT and AREA.

THE INSTANCE OBJECT IS WHERE THIS GETS USEFUL, and it is also the part with a trap: setting
render_type to OBJECT without setting instance_object leaves a system that renders NOTHING and
reports no error at all. So they are validated together here rather than independently.
"""
import bpy

from .ops_common import MifOpError, get_object, reject_unknown, take, take_bool, take_float

_PS_KEYS = {
    "object", "name", "systemName", "type", "count", "seed",
    "frameStart", "frameEnd", "lifetime", "lifetimeRandom",
    "emitFrom", "distribution", "useModifierStack",
    "physicsType", "normalFactor", "randomFactor", "gravityFactor", "dampingFactor",
    "size", "sizeRandom", "renderType", "instanceObject", "instanceCollection",
    "hairLength", "childCount", "showEmitter", "rotationMode", "useRotations",
}
_LIST_KEYS = {"object", "name"}


def _enum(rna_type, prop):
    return {i.identifier for i in rna_type.bl_rna.properties[prop].enum_items}


def op_add_particles(params):
    """Add a particle system. Returns the settings read back off the datablock.

    params:
      object (str, required)   the emitter
      type                     EMITTER (default) | HAIR
      count (int)              number of particles
      frameStart / frameEnd    EMITTER only - the emission window
      lifetime                 EMITTER only
      hairLength / childCount  HAIR only
      emitFrom                 VERT | FACE | VOLUME
      physicsType              NO | NEWTON | KEYED | BOIDS | FLUID
      normalFactor / randomFactor / gravityFactor / dampingFactor
      size / sizeRandom
      renderType               NONE | HALO | PATH | OBJECT | COLLECTION
      instanceObject           the object to instance - REQUIRED when renderType is OBJECT
      showEmitter (bool)
    """
    reject_unknown(params, _PS_KEYS, "add_particles")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    kind = str(take(params, "type", default="EMITTER", kind=str)).upper()
    if kind not in ("EMITTER", "HAIR"):
        raise MifOpError("particle type must be EMITTER or HAIR, got '%s'. NOTHING was created."
                         % kind)

    # REFUSED BEFORE ANYTHING IS CREATED, the same way create_light does it - a typo should not
    # leave a half-built particle system on somebody's mesh.
    for keys, want, label in ((("frameStart", "frameEnd", "lifetime", "lifetimeRandom"),
                               "EMITTER", "frameStart/frameEnd/lifetime"),
                              (("hairLength", "childCount"), "HAIR", "hairLength/childCount")):
        present = [k for k in keys if k in params]
        if present and kind != want:
            raise MifOpError("%s applies to a %s system and this one is %s (%s given). NOTHING was "
                             "created." % (label, want, kind, ", ".join(present)))

    render_type = take(params, "renderType", default=None, kind=str)
    inst = take(params, "instanceObject", default=None, kind=str)
    if render_type and str(render_type).upper() == "OBJECT" and not inst:
        raise MifOpError("renderType OBJECT needs instanceObject - without it the system renders "
                         "NOTHING and Blender reports no error at all. NOTHING was created.")

    mod = obj.modifiers.new(name=str(take(params, "systemName", default="ParticleSystem",
                                          kind=str)), type="PARTICLE_SYSTEM")
    psys = obj.particle_systems[-1]
    st = psys.settings
    st.type = kind

    n = take_float(params, "count", default=None)
    if n is not None:
        st.count = int(n)
    sd = take_float(params, "seed", default=None)
    if sd is not None:
        psys.seed = int(sd)
    if kind == "EMITTER":
        fs = take_float(params, "frameStart", default=None)
        if fs is not None:
            st.frame_start = fs
        fe = take_float(params, "frameEnd", default=None)
        if fe is not None:
            st.frame_end = fe
        lt = take_float(params, "lifetime", default=None)
        if lt is not None:
            st.lifetime = lt
        lr = take_float(params, "lifetimeRandom", default=None)
        if lr is not None:
            st.lifetime_random = lr
    else:
        hl = take_float(params, "hairLength", default=None)
        if hl is not None:
            st.hair_length = hl
        cc = take_float(params, "childCount", default=None)
        if cc is not None:
            st.child_type = "SIMPLE"
            st.child_percent = int(cc)
            st.rendered_child_count = int(cc)

    ef = take(params, "emitFrom", default=None, kind=str)
    if ef:
        valid = _enum(bpy.types.ParticleSettings, "emit_from")
        if str(ef).upper() not in valid:
            raise MifOpError("unknown emitFrom '%s'. Valid: %s. The system WAS created."
                             % (ef, ", ".join(sorted(valid))))
        st.emit_from = str(ef).upper()
    dist = take(params, "distribution", default=None, kind=str)
    if dist:
        st.distribution = str(dist).upper()

    pt = take(params, "physicsType", default=None, kind=str)
    if pt:
        valid = _enum(bpy.types.ParticleSettings, "physics_type")
        if str(pt).upper() not in valid:
            raise MifOpError("unknown physicsType '%s'. Valid: %s. The system WAS created."
                             % (pt, ", ".join(sorted(valid))))
        st.physics_type = str(pt).upper()
    # WIRED 2026-09-03. These three were on the accept list and read NOWHERE: a caller could send
    # useModifierStack, useRotations or rotationMode, pass the guard, and have nothing happen. They
    # were invisible to audit_blender_dead_params because it blanked the reject_unknown CALL and not
    # the module-level constant the call names, which is where the literals live - so the audit
    # could not fail for any op with a named key set. Both are fixed together.
    if "useModifierStack" in params:
        # Emit from the EVALUATED mesh rather than the base one. With it off, a system on a
        # subdivided or displaced mesh emits from the undisplaced cage - particles that float above
        # or sink into the surface they were supposed to sit on, with every field reading correctly.
        st.use_modifier_stack = take_bool(params, "useModifierStack", default=True)
    if "useRotations" in params:
        st.use_rotations = take_bool(params, "useRotations", default=True)
    rot = take(params, "rotationMode", default=None, kind=str)
    if rot:
        valid = _enum(bpy.types.ParticleSettings, "rotation_mode")
        if str(rot).upper() not in valid:
            raise MifOpError("unknown rotationMode '%s'. Valid: %s. The system WAS created."
                             % (rot, ", ".join(sorted(valid))))
        st.rotation_mode = str(rot).upper()
        # ROTATION MODE DOES NOTHING WHILE use_rotations IS OFF, and it reads back perfectly either
        # way - so setting one without the other is a silent no-op. Turned on rather than refused,
        # because a caller naming a rotation mode has said what they want unambiguously.
        if not st.use_rotations:
            st.use_rotations = True

    for key, attr in (("normalFactor", "normal_factor"), ("randomFactor", "factor_random"),
                      ("gravityFactor", "effector_weights.gravity"), ("dampingFactor", "damping")):
        v = take_float(params, key, default=None)
        if v is None:
            continue
        if "." in attr:
            head, tail = attr.split(".", 1)
            setattr(getattr(st, head), tail, v)
        else:
            setattr(st, attr, v)

    sz = take_float(params, "size", default=None)
    if sz is not None:
        st.particle_size = sz
    szr = take_float(params, "sizeRandom", default=None)
    if szr is not None:
        st.size_random = szr

    if render_type:
        valid = _enum(bpy.types.ParticleSettings, "render_type")
        if str(render_type).upper() not in valid:
            raise MifOpError("unknown renderType '%s'. Valid: %s. The system WAS created."
                             % (render_type, ", ".join(sorted(valid))))
        st.render_type = str(render_type).upper()
    if inst:
        # SAYS THE SAME THING AS ITS SIBLING THREE LINES DOWN. get_object raises "no object named
        # 'X'. Present: ..." and stops there, so a typo'd instanceObject left the caller with a
        # particle system they were never told about, while the instanceCollection path directly
        # below has always ended "The system WAS created." Two adjacent paths, same situation,
        # different honesty - and the one that stayed quiet is the one a caller is more likely to
        # hit, because object names are typed more often than collection names.
        #
        # Reported as a mutate-then-refuse; it is not one. This op never claims nothing happened,
        # and the renderType refusal above already says the system was created. The defect is only
        # that this path forgot to.
        try:
            st.instance_object = get_object(inst)
        except MifOpError as exc:
            raise MifOpError("%s. The system WAS created." % str(exc).rstrip().rstrip("."))
        if st.render_type != "OBJECT":
            st.render_type = "OBJECT"
    coll = take(params, "instanceCollection", default=None, kind=str)
    if coll:
        c = bpy.data.collections.get(coll)
        if c is None:
            raise MifOpError("no collection named '%s'. The system WAS created." % coll)
        st.instance_collection = c
        st.render_type = "COLLECTION"
    if "showEmitter" in params:
        # THE FLAG MOVED OFF ParticleSettings. It was settings.use_render_emitter in 2.7x and is
        # now show_instancer_for_render / _viewport on the OBJECT - so writing the old name raises
        # AttributeError rather than being ignored. Found the hard way: it took down a live build
        # at the dust-emitter step, because the suite never passed showEmitter and so never
        # reached this line. Newest name first, with the old one as a fallback.
        want = take_bool(params, "showEmitter", default=True)
        wrote = []
        for holder, attr in ((obj, "show_instancer_for_render"),
                             (obj, "show_instancer_for_viewport"),
                             (st, "use_render_emitter")):
            if hasattr(holder, attr):
                setattr(holder, attr, want)
                wrote.append(attr)
        if not wrote:
            raise MifOpError("this Blender exposes no emitter-visibility flag this op knows "
                             "(tried show_instancer_for_render/_viewport and use_render_emitter), "
                             "so showEmitter would have been silently ignored. The system WAS "
                             "created.")

    return {
        "object": obj.name,
        "system": psys.name,
        "settings": st.name,
        "type": st.type,
        "count": int(st.count),
        "emitFrom": st.emit_from,
        "physicsType": st.physics_type,
        "renderType": st.render_type,
        "instanceObject": st.instance_object.name if st.instance_object else None,
        "frameRange": [round(float(st.frame_start), 3), round(float(st.frame_end), 3)]
                      if st.type == "EMITTER" else None,
        "hairLength": round(float(st.hair_length), 4) if st.type == "HAIR" else None,
        "systemsOnObject": len(obj.particle_systems),
        "showEmitter": bool(getattr(obj, "show_instancer_for_render", True)),
        "bakeNote": ("EMITTER particles are stepped forward from frame_start; a late frame shows "
                     "nothing until the sim has run through. bake_physics covers particle caches "
                     "too."),
    }


def op_list_particles(params):
    """Every particle system on an object, read off the datablocks - the verification half."""
    reject_unknown(params, _LIST_KEYS, "list_particles")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    out = []
    for psys in obj.particle_systems:
        st = psys.settings
        out.append({
            "system": psys.name, "settings": st.name, "type": st.type,
            "count": int(st.count), "renderType": st.render_type,
            "instanceObject": st.instance_object.name if st.instance_object else None,
            "physicsType": st.physics_type, "seed": int(psys.seed),
            "rendersNothing": (st.render_type == "OBJECT" and st.instance_object is None),
        })
    return {"object": obj.name, "systems": out, "count": len(out)}


OPS = {
    "add_particles": op_add_particles,
    "list_particles": op_list_particles,
}
