"""Rigid bodies, cloth, collision and softbody - simulation, and the bake that makes it real.

=============================================================================
RIGID BODIES NEED A WORLD, AND blender's OWN OPERATOR IS THE ONLY THING THAT MAKES ONE
=============================================================================
An object cannot simply be given a rigid_body: the setting lives on the object but the SIMULATION
lives in a scene-level RigidBodyWorld with its own collection, and without it nothing falls.
bpy.ops.rigidbody.object_add() creates that world on demand; assigning obj.rigid_body directly is
not possible at all. So this op goes through the operator, with the context override that makes it
work when nothing is selected - which is the normal state for a bridge call.

=============================================================================
A SIMULATION THAT HAS NOT BEEN BAKED DOES NOT EXIST AT A GIVEN FRAME
=============================================================================
Physics is evaluated by stepping FORWARD from the start frame. Jumping straight to frame 200 shows
the object at its rest position, not where it would have fallen to, and a render of that frame is
simply wrong. Nothing about the scene says so. So bake_physics is here as its own op, and the
docstrings say plainly that setting up a sim without baking it leaves you with a scene that looks
right in the viewport only if you happened to scrub through it.
"""
import bpy

from .ops_common import (MifOpError, get_object, reject_unknown, rnd, take, take_bool, take_float,
                         take_int)

_RB_KEYS = {"object", "name", "type", "mass", "friction", "bounciness", "restitution",
            "collisionShape", "kinematic", "margin", "linearDamping", "angularDamping"}
_CLOTH_KEYS = {"object", "name", "quality", "mass", "stiffness", "damping", "gravity",
               "usePressure", "pressure", "collisionQuality", "selfCollision"}
_COLL_KEYS = {"object", "name", "damping", "friction", "thickness", "remove"}
# NO "type". It sat on this list and was read nowhere: bake_physics goes through
# bpy.ops.ptcache.bake_all, which bakes EVERY cache in the scene and takes no filter, so
# there was nothing for a type to select. A caller could send type:"CLOTH", pass the guard,
# and have every cache baked anyway - a parameter that appears to narrow the work and does
# not. Per-type baking is a different implementation and is filed in the spec.
_BAKE_KEYS = {"start", "end", "clear"}


def _ctx_override(obj):
    """A context the physics operators will accept from a headless bridge call.

    bpy.ops.rigidbody.* reads the ACTIVE object and the current view layer, neither of which a
    socket call has set up. temp_override is the supported way to supply them; without it the
    operator raises a poll failure that says nothing about the real problem.
    """
    win = bpy.context.window
    return bpy.context.temp_override(
        window=win,
        scene=bpy.context.scene,
        view_layer=bpy.context.view_layer,
        object=obj,
        active_object=obj,
        selected_objects=[obj],
        selected_editable_objects=[obj],
    )


def op_add_rigid_body(params):
    """Make an object an ACTIVE (falls) or PASSIVE (is fallen onto) rigid body.

    params:
      object (str, required)
      type            ACTIVE | PASSIVE, default ACTIVE
      mass (float)              kg, ACTIVE only
      friction / bounciness     0..1  (restitution is an alias for bounciness)
      collisionShape            CONVEX_HULL | MESH | BOX | SPHERE | CAPSULE | CYLINDER | CONE
      kinematic (bool)          animated rather than simulated - keyframes drive it
      margin, linearDamping, angularDamping
    """
    reject_unknown(params, _RB_KEYS, "add_rigid_body")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    kind = str(take(params, "type", default="ACTIVE", kind=str)).upper()
    if kind not in ("ACTIVE", "PASSIVE"):
        raise MifOpError("rigid body type must be ACTIVE or PASSIVE, got '%s'. NOTHING was changed."
                         % kind)

    try:
        with _ctx_override(obj):
            bpy.ops.rigidbody.object_add(type=kind)
    except RuntimeError as exc:
        raise MifOpError("Blender refused to add a rigid body to '%s': %s. This is usually a mesh "
                         "with no faces, or a scene whose rigid body world could not be created. "
                         "NOTHING was changed." % (obj.name, exc))

    rb = obj.rigid_body
    if rb is None:
        raise MifOpError("the operator reported success and '%s' still has no rigid_body - so the "
                         "scene has no usable RigidBodyWorld. NOTHING usable was created."
                         % obj.name)

    mass = take_float(params, "mass", default=None)
    if mass is not None:
        if kind != "ACTIVE":
            raise MifOpError("mass applies to an ACTIVE rigid body; this one is PASSIVE and is "
                             "never moved by the simulation. The body WAS created.")
        rb.mass = mass
    fr = take_float(params, "friction", default=None)
    if fr is not None:
        rb.friction = fr
    bo = take_float(params, "bounciness", "restitution", default=None)
    if bo is not None:
        rb.restitution = bo
    shape = take(params, "collisionShape", default=None, kind=str)
    if shape:
        valid = {i.identifier for i in
                 bpy.types.RigidBodyObject.bl_rna.properties["collision_shape"].enum_items}
        if str(shape).upper() not in valid:
            raise MifOpError("unknown collisionShape '%s'. Valid: %s. The body WAS created."
                             % (shape, ", ".join(sorted(valid))))
        rb.collision_shape = str(shape).upper()
    if "kinematic" in params:
        rb.kinematic = take_bool(params, "kinematic", default=False)
    mg = take_float(params, "margin", default=None)
    if mg is not None:
        rb.use_margin = True
        rb.collision_margin = mg
    ld = take_float(params, "linearDamping", default=None)
    if ld is not None:
        rb.linear_damping = ld
    ad = take_float(params, "angularDamping", default=None)
    if ad is not None:
        rb.angular_damping = ad

    return {
        "object": obj.name,
        "type": rb.type,
        "mass": round(float(rb.mass), 6),
        "friction": round(float(rb.friction), 6),
        "restitution": round(float(rb.restitution), 6),
        "collisionShape": rb.collision_shape,
        "kinematic": bool(rb.kinematic),
        "worldObjectCount": (len(bpy.context.scene.rigidbody_world.collection.objects)
                             if bpy.context.scene.rigidbody_world
                             and bpy.context.scene.rigidbody_world.collection else 0),
        "bakeNote": ("a rigid body is stepped forward from the start frame. Jumping to a late "
                     "frame shows it at REST, not where it would have fallen - call bake_physics "
                     "before rendering that frame."),
    }


def op_add_cloth(params):
    """Add a cloth simulation. The object needs enough subdivisions to drape at all."""
    reject_unknown(params, _CLOTH_KEYS, "add_cloth")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    if len(obj.data.vertices) < 16:
        raise MifOpError("'%s' has %d vertices, which cannot drape - cloth deforms the mesh it is "
                         "given and a quad has nothing to bend. Subdivide it first. NOTHING was "
                         "added." % (obj.name, len(obj.data.vertices)))
    mod = obj.modifiers.new(name="Cloth", type="CLOTH")
    st = mod.settings
    q = take_float(params, "quality", default=None)
    if q is not None:
        st.quality = int(q)
    m = take_float(params, "mass", default=None)
    if m is not None:
        st.mass = m
    sf = take_float(params, "stiffness", default=None)
    if sf is not None:
        for attr in ("tension_stiffness", "compression_stiffness", "shear_stiffness"):
            if hasattr(st, attr):
                setattr(st, attr, sf)
    dp = take_float(params, "damping", default=None)
    if dp is not None:
        for attr in ("tension_damping", "compression_damping", "shear_damping"):
            if hasattr(st, attr):
                setattr(st, attr, dp)
    if "usePressure" in params:
        st.use_pressure = take_bool(params, "usePressure", default=False)
    pr = take_float(params, "pressure", default=None)
    if pr is not None:
        st.use_pressure = True
        st.uniform_pressure_force = pr
    gv = take_float(params, "gravity", default=None)
    if gv is not None:
        # Cloth gravity is a WEIGHT on the scene's gravity, not an acceleration - 1.0 is normal,
        # 0.0 makes the cloth float. It was in the accept list and unread until param_reach said so.
        st.effector_weights.gravity = gv
    cq = take_float(params, "collisionQuality", default=None)
    if cq is not None:
        mod.collision_settings.collision_quality = int(cq)
    if "selfCollision" in params:
        mod.collision_settings.use_self_collision = take_bool(params, "selfCollision", default=False)
    return {"object": obj.name, "modifier": mod.name, "quality": int(st.quality),
            "mass": round(float(st.mass), 6), "vertices": len(obj.data.vertices),
            "gravityWeight": round(float(st.effector_weights.gravity), 6),
            "selfCollision": bool(mod.collision_settings.use_self_collision),
            "bakeNote": "cloth is stepped forward like any sim - bake_physics before a late frame."}


def op_add_collision(params):
    """Make an object collide with cloth, softbody and particles - NOT with rigid bodies.

    Rigid bodies collide through the rigid body world, not this modifier, and giving a floor a
    Collision modifier and expecting a cube to bounce off it is a common and silent mistake. Say so
    rather than let it look configured.
    """
    reject_unknown(params, _COLL_KEYS, "add_collision")
    obj = get_object(take(params, "object", "name", required=True), want_mesh=True)
    if take_bool(params, "remove", default=False):
        existing = [m for m in obj.modifiers if m.type == "COLLISION"]
        for m in existing:
            obj.modifiers.remove(m)
        return {"object": obj.name, "removed": len(existing),
                "hasCollision": any(m.type == "COLLISION" for m in obj.modifiers)}
    mod = next((m for m in obj.modifiers if m.type == "COLLISION"), None)
    if mod is None:
        mod = obj.modifiers.new(name="Collision", type="COLLISION")
    st = obj.collision
    d = take_float(params, "damping", default=None)
    if d is not None:
        st.damping = d
    f = take_float(params, "friction", default=None)
    if f is not None:
        st.cloth_friction = f
    th = take_float(params, "thickness", default=None)
    if th is not None:
        st.thickness_outer = th
    return {"object": obj.name, "modifier": mod.name,
            "damping": round(float(st.damping), 6),
            "thicknessOuter": round(float(st.thickness_outer), 6),
            "scopeNote": ("this collides with cloth, softbody and particles. RIGID BODIES do NOT "
                          "use it - they collide through the rigid body world, so a floor for a "
                          "falling crate needs add_rigid_body {type:PASSIVE} instead.")}


def op_bake_physics(params):
    """Bake the point caches so a given frame shows the simulated state, not the rest state.

    BLOCKS for the length of the bake, like any main-thread work here.
    """
    reject_unknown(params, _BAKE_KEYS, "bake_physics")
    sc = bpy.context.scene
    start = take_float(params, "start", default=None)
    end = take_float(params, "end", default=None)
    if start is not None or end is not None:
        rbw = sc.rigidbody_world
        if rbw and rbw.point_cache:
            if start is not None:
                rbw.point_cache.frame_start = int(start)
            if end is not None:
                rbw.point_cache.frame_end = int(end)

    if take_bool(params, "clear", default=False):
        try:
            with bpy.context.temp_override(scene=sc, view_layer=bpy.context.view_layer):
                bpy.ops.ptcache.free_bake_all()
        except RuntimeError as exc:
            raise MifOpError("could not clear the caches: %s" % exc)
        return {"cleared": True, "baked": False}

    try:
        with bpy.context.temp_override(scene=sc, view_layer=bpy.context.view_layer):
            bpy.ops.ptcache.bake_all(bake=True)
    except RuntimeError as exc:
        raise MifOpError("bake failed: %s. Nothing in the scene may have a point cache - a rigid "
                         "body world, cloth or softbody is needed before there is anything to "
                         "bake." % exc)

    # READ BACK which caches actually hold frames. bake_all reports FINISHED with nothing to do.
    caches = []
    rbw = sc.rigidbody_world
    if rbw and rbw.point_cache:
        caches.append({"kind": "rigidbody", "isBaked": bool(rbw.point_cache.is_baked),
                       "frames": [rbw.point_cache.frame_start, rbw.point_cache.frame_end]})
    for ob in bpy.data.objects:
        for m in ob.modifiers:
            pc = getattr(getattr(m, "point_cache", None), "is_baked", None)
            if pc is not None:
                caches.append({"kind": m.type.lower(), "object": ob.name, "isBaked": bool(pc),
                               "frames": [m.point_cache.frame_start, m.point_cache.frame_end]})
    return {"baked": True, "caches": caches, "cacheCount": len(caches),
            "emptyNote": (None if caches else
                          "bake_all reported success and NOTHING has a point cache - there was no "
                          "simulation to bake. This is a no-op reported as a success by Blender.")}


_INFO_KEYS = {"object", "name"}

# The modifier types that carry a simulation. Named explicitly rather than detected by the presence
# of a point_cache, because DYNAMIC_PAINT has one and is not a physics sim in the sense meant here,
# and a list is readable where a duck-type is not.
_SIM_MODIFIERS = ("CLOTH", "SOFT_BODY", "COLLISION", "FLUID", "DYNAMIC_PAINT")


def _cache_row(pc, scene):
    """A point cache described, including whether its range still COVERS the scene's.

    THE STALE-BAKE TRAP. A cache baked before the frame range was extended is baked, valid, and
    short - is_baked stays true, and the frames past its end silently fall back to the rest state.
    Nothing announces it, so the coverage comparison is made here rather than left to a caller who
    would have to know to make it.
    """
    if pc is None:
        return None
    row = {
        "isBaked": bool(pc.is_baked),
        "frameStart": int(pc.frame_start),
        "frameEnd": int(pc.frame_end),
    }
    row["coversSceneRange"] = (pc.frame_start <= scene.frame_start
                               and pc.frame_end >= scene.frame_end)
    return row


def op_physics_info(params):
    """What the physics setup IS - the read half of a family that could only write.

    add_rigid_body, add_cloth, add_collision and bake_physics all set, and until 2026-09-03 nothing
    anywhere reported what they had set. scene_info carries no physics at all. So a caller could not
    ask what mass a rigid body has, which collision shape it uses, whether an object is kinematic,
    or - the one that decides whether a render is right - whether the cache is baked.

    A rigid body is NOT a modifier: it lives on obj.rigid_body, so list_modifiers cannot see it and
    there was no route to it by any op.

    THE INERT STATE THIS EXISTS TO CATCH. An object can carry a fully configured obj.rigid_body -
    mass, friction, shape, all reading back perfectly - and still not simulate at all, because the
    simulation is driven by the scene's RigidBodyWorld and only acts on objects in ITS COLLECTION.
    Remove the object from that collection and every field on it stays correct while it hangs in
    the air. That is reported as `inSimulation` per object, not inferred from the settings.

    params:
      object / name (str)   report one object only. Default every object with physics.
    """
    reject_unknown(params, _INFO_KEYS, "physics_info")
    sc = bpy.context.scene
    only = take(params, "object", "name", kind=str)
    if only:
        objects = [get_object(only)]
    else:
        objects = list(bpy.data.objects)

    rbw = getattr(sc, "rigidbody_world", None)
    rbw_names = set()
    if rbw is not None and getattr(rbw, "collection", None) is not None:
        rbw_names = {o.name for o in rbw.collection.objects}

    world = {
        "exists": rbw is not None,
        "collection": (rbw.collection.name
                       if rbw is not None and getattr(rbw, "collection", None) else None),
        "objectCount": len(rbw_names),
        "enabled": bool(getattr(rbw, "enabled", False)) if rbw is not None else None,
        "substeps": getattr(rbw, "substeps_per_frame", None) if rbw is not None else None,
        "solverIterations": getattr(rbw, "solver_iterations", None) if rbw is not None else None,
        "pointCache": _cache_row(getattr(rbw, "point_cache", None), sc) if rbw is not None else None,
    }

    rows, blockers = [], []
    for obj in objects:
        rb = getattr(obj, "rigid_body", None)
        mods = [m for m in getattr(obj, "modifiers", []) if m.type in _SIM_MODIFIERS]
        if rb is None and not mods:
            continue
        row = {"object": obj.name, "type": obj.type}
        if rb is not None:
            row["rigidBody"] = {
                "type": rb.type,
                "mass": round(float(rb.mass), 6),
                "friction": round(float(rb.friction), 6),
                "restitution": round(float(rb.restitution), 6),
                "collisionShape": rb.collision_shape,
                "kinematic": bool(rb.kinematic),
                "collisionMargin": round(float(rb.collision_margin), 6),
                "linearDamping": round(float(rb.linear_damping), 6),
                "angularDamping": round(float(rb.angular_damping), 6),
                "enabled": bool(getattr(rb, "enabled", True)),
            }
            # THE MEASUREMENT THAT SETTINGS CANNOT GIVE. Membership of the RigidBodyWorld
            # collection is what decides whether any of the above does anything.
            row["inSimulation"] = obj.name in rbw_names
            if not row["inSimulation"]:
                blockers.append("'%s' has a fully configured rigid body and is NOT in the "
                                "RigidBodyWorld collection%s, so it will not simulate at all - "
                                "every setting on it reads back correctly and it hangs in the air."
                                % (obj.name,
                                   " (there is no RigidBodyWorld)" if rbw is None else ""))
            elif rb.type == "ACTIVE" and rb.kinematic:
                blockers.append("'%s' is an ACTIVE rigid body with kinematic ON, which means it is "
                                "driven by its animation rather than by the sim - the usual "
                                "accident when a keyframed object refuses to fall." % obj.name)
        if mods:
            row["simModifiers"] = [
                {"name": m.name, "type": m.type, "showRender": bool(m.show_render),
                 "showViewport": bool(m.show_viewport),
                 "pointCache": _cache_row(getattr(m, "point_cache", None), sc)}
                for m in mods]
            for m in mods:
                if not m.show_render:
                    blockers.append("'%s' on '%s' is disabled in the RENDER, so it is visible in "
                                    "the viewport and absent from the picture."
                                    % (m.name, obj.name))
        rows.append(row)

    # CACHE STATE ACROSS EVERYTHING, which is the question that decides whether a frame is right.
    caches = []
    if world["pointCache"] is not None:
        caches.append(dict(world["pointCache"], kind="rigidbody"))
    for row in rows:
        for m in row.get("simModifiers", []):
            if m["pointCache"] is not None:
                caches.append(dict(m["pointCache"], kind=m["type"].lower(), object=row["object"]))
    unbaked = [c for c in caches if not c["isBaked"]]
    short = [c for c in caches if c["isBaked"] and not c["coversSceneRange"]]
    if unbaked:
        blockers.append("%d point cache(s) are NOT baked. Physics is evaluated by stepping forward "
                        "from the start frame, so jumping straight to a late frame shows the REST "
                        "state and a render of it is simply wrong. Run bake_physics."
                        % len(unbaked))
    if short:
        blockers.append("%d cache(s) are baked but their range does NOT cover the scene's %d-%d - "
                        "a bake made before the range was extended stays valid and short, and the "
                        "frames past its end silently fall back to the rest state."
                        % (len(short), sc.frame_start, sc.frame_end))

    return {
        "ok": True,
        "scene": sc.name,
        "sceneFrameRange": [sc.frame_start, sc.frame_end],
        "rigidBodyWorld": world,
        "objectsWithPhysics": len(rows),
        "objects": rows,
        "caches": caches,
        "cacheCount": len(caches),
        "blockers": blockers,
        "readyToRender": not blockers,
    }


_PHYSWORLD_KEYS = {"gravity", "useGravity", "substeps", "solverIterations", "timeScale",
                   "splitImpulse", "enabled", "cacheStart", "cacheEnd", "cacheStep"}

# The keys that need a RIGID BODY WORLD to exist, as opposed to living on the scene itself.
_NEEDS_WORLD = {"substeps", "solverIterations", "timeScale", "splitImpulse", "enabled",
                "cacheStart", "cacheEnd", "cacheStep"}


def op_set_physics_world(params):
    """The scene-level simulation settings - gravity, substeps, solver iterations, time scale.

    THE ASYMMETRY THIS CLOSES. physics_info has always reported substeps and solverIterations and
    nothing could write either, so the addon could add rigid bodies, bake them, and report exactly
    how the solver was configured while being unable to change any of it. Those two are the knobs
    that fix a simulation which jitters or tunnels through a floor, and scene gravity drives every
    rigid body in the file - none of it was reachable.

    A GRAVITY VECTOR IS INERT WHILE use_gravity IS OFF. Blender stores the vector either way, so
    setting it alone changes three numbers and not the simulation, and every field reads back
    exactly as written. Passing gravity turns the toggle on unless useGravity says otherwise - the
    same shape as cutoffDistance on a light and a focus object on a camera, and the response says
    when it happened.

    CHANGING A SOLVER SETTING DOES NOT RE-SIMULATE. An existing bake stays on disk and stays marked
    valid, so the next frame you look at was computed with the OLD substeps - a stale cache is
    exactly as convincing as a fresh one. is_baked and is_outdated are reported afterwards, and a
    bake that is now stale is named rather than left to be discovered as "the change did nothing".

    params:
      gravity [x,y,z] (list)      metres per second squared. Blender's default is [0,0,-9.81].
      useGravity (bool)
      substeps (int)              substeps_per_frame - raise this when bodies tunnel or jitter
      solverIterations (int)      constraint solver iterations per substep
      timeScale (float)           simulation speed multiplier; 0 freezes the sim
      splitImpulse (bool)         use_split_impulse - reduces bounce on stacked bodies
      enabled (bool)              the whole rigid body world on or off
      cacheStart / cacheEnd / cacheStep (int)   the point cache's frame range
    """
    reject_unknown(params, _PHYSWORLD_KEYS, "set_physics_world")
    sc = bpy.context.scene
    asked = [k for k in _PHYSWORLD_KEYS if params.get(k) is not None]
    if not asked:
        raise MifOpError("nothing to do - pass at least one of %s. NOTHING was changed."
                         % ", ".join(sorted(_PHYSWORLD_KEYS)))

    rbw = getattr(sc, "rigidbody_world", None)
    needs = [k for k in asked if k in _NEEDS_WORLD]
    if needs and rbw is None:
        # REFUSED RATHER THAN CREATED. A rigid body world is scene-wide state, and conjuring one
        # from a settings call would be a side effect nobody asked for - the same reason
        # set_compositing refuses to switch use_nodes on from a read.
        raise MifOpError(
            "this scene has no rigid body world, so %s cannot be set. One appears when the first "
            "rigid body is added - call add_rigid_body on any object first. Creating a scene-wide "
            "simulation from a settings call would be a side effect nobody asked for. NOTHING was "
            "changed." % ", ".join(sorted(needs)))

    # PARSED IN FULL BEFORE ANYTHING IS WRITTEN. Both halves of this were defects in this op as
    # first written on 2026-09-04, found by turning the day's own lenses on the day's own code:
    #
    #   {"gravity": ["a","b","c"]}           the isinstance check only proved it was a list of
    #                                        three, so float("a") raised a RAW ValueError out of the
    #                                        op - the shape blender_version_matrix's suspect-refusal
    #                                        detector exists to catch, escaping a handler's contract.
    #   {"gravity": [...], "substeps": "x"}  gravity was written and THEN take_int refused.
    #                                        Measured: -9.81 became -1.5 and the call failed. A
    #                                        half-applied settings call, which set_light_influence
    #                                        and set_material_settings both avoid by validating up
    #                                        front - this one did not, on the same day.
    applied = {}
    pending_scene, pending_world, pending_cache = {}, {}, {}

    grav = params.get("gravity")
    if grav is not None:
        if not isinstance(grav, (list, tuple)) or len(grav) < 3:
            raise MifOpError("'gravity' must be [x, y, z] in m/s^2, got %r. NOTHING was changed."
                             % (grav,))
        try:
            pending_scene["gravity"] = [float(v) for v in grav[:3]]
        except (TypeError, ValueError):
            raise MifOpError("'gravity' must be three NUMBERS in m/s^2, got %r. NOTHING was "
                             "changed." % (grav,))
    if params.get("useGravity") is not None:
        pending_scene["useGravity"] = take_bool(params, "useGravity", default=True)

    if rbw is not None:
        for key, attr, cast in (("substeps", "substeps_per_frame", int),
                                ("solverIterations", "solver_iterations", int),
                                ("timeScale", "time_scale", float),
                                ("splitImpulse", "use_split_impulse", bool),
                                ("enabled", "enabled", bool)):
            if params.get(key) is None:
                continue
            pending_world[attr] = (take_bool(params, key, default=True) if cast is bool
                                   else (take_int(params, key) if cast is int
                                         else take_float(params, key)))
            applied[key] = pending_world[attr]
        if getattr(rbw, "point_cache", None) is not None:
            for key, attr in (("cacheStart", "frame_start"), ("cacheEnd", "frame_end"),
                              ("cacheStep", "frame_step")):
                if params.get(key) is None:
                    continue
                pending_cache[attr] = take_int(params, key)
                applied[key] = pending_cache[attr]

    # COMMIT. Nothing below can refuse - and unlike the same sentence in ops_lightcam before today,
    # that is true here because every parse above happens before the first write.
    if "gravity" in pending_scene:
        sc.gravity = pending_scene["gravity"]
        applied["gravity"] = pending_scene["gravity"]
    if "useGravity" in pending_scene:
        sc.use_gravity = pending_scene["useGravity"]
        applied["useGravity"] = sc.use_gravity

    # THE TOGGLE THAT MAKES THE VECTOR MEAN ANYTHING.
    auto_gravity = False
    if "gravity" in applied and "useGravity" not in applied and not sc.use_gravity:
        sc.use_gravity = True
        auto_gravity = True

    if rbw is not None:
        for attr, value in pending_world.items():
            setattr(rbw, attr, value)
        pc = getattr(rbw, "point_cache", None)
        if pc is not None:
            for attr, value in pending_cache.items():
                setattr(pc, attr, value)

    # READ BACK. substeps and solver_iterations are CLAMPED by Blender rather than refused, so
    # echoing the request would report a value the solver does not have.
    after = {"gravity": rnd(list(sc.gravity)), "useGravity": bool(sc.use_gravity)}
    clamped = {}
    if rbw is not None:
        after.update({
            "substeps": int(rbw.substeps_per_frame),
            "solverIterations": int(rbw.solver_iterations),
            "timeScale": round(float(rbw.time_scale), 6),
            "splitImpulse": bool(rbw.use_split_impulse),
            "enabled": bool(rbw.enabled),
        })
        for key in ("substeps", "solverIterations", "timeScale"):
            if key in applied and after.get(key) != applied[key]:
                clamped[key] = {"requested": applied[key], "stored": after[key]}

    pc = getattr(rbw, "point_cache", None) if rbw is not None else None
    was_baked = bool(getattr(pc, "is_baked", False)) if pc is not None else False
    solver_touched = [k for k in applied
                      if k in ("substeps", "solverIterations", "timeScale", "splitImpulse",
                               "gravity", "useGravity")]
    return {
        "ok": True,
        "scene": sc.name,
        "hasRigidBodyWorld": rbw is not None,
        "applied": after,
        "clamped": clamped or None,
        "gravityWasEnabledAutomatically": auto_gravity,
        # THE CACHE STATE, because a solver change does not re-simulate and the old bake stays
        # marked valid. is_outdated is Blender's own answer to "does this need re-baking".
        "cacheIsBaked": was_baked,
        "cacheIsOutdated": bool(getattr(pc, "is_outdated", False)) if pc is not None else None,
        "cacheFrameRange": ([int(pc.frame_start), int(pc.frame_end)]
                            if pc is not None else None),
        "note": ("use_gravity was OFF, so the gravity vector would have been stored and ignored - "
                 "it has been turned ON. Pass useGravity:false to store it without applying it."
                 if auto_gravity else
                 ("%s changed and this scene already has a BAKED cache. Changing a solver setting "
                  "does not re-simulate - the existing bake stays on disk and stays convincing, so "
                  "every frame you look at was computed with the OLD settings. Re-run bake_physics "
                  "with clear:true and then bake again."
                  % ", ".join(sorted(solver_touched))) if (was_baked and solver_touched) else None),
    }

OPS = {
    "add_rigid_body": op_add_rigid_body,
    "add_cloth": op_add_cloth,
    "add_collision": op_add_collision,
    "bake_physics": op_bake_physics,
    "physics_info": op_physics_info,
    "set_physics_world": op_set_physics_world,
}
