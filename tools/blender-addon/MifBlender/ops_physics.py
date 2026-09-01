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

from .ops_common import (MifOpError, get_object, reject_unknown, take, take_bool,
                         take_float)

_RB_KEYS = {"object", "name", "type", "mass", "friction", "bounciness", "restitution",
            "collisionShape", "kinematic", "margin", "linearDamping", "angularDamping"}
_CLOTH_KEYS = {"object", "name", "quality", "mass", "stiffness", "damping", "gravity",
               "usePressure", "pressure", "collisionQuality", "selfCollision"}
_COLL_KEYS = {"object", "name", "damping", "friction", "thickness", "remove"}
_BAKE_KEYS = {"start", "end", "type", "clear"}


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


OPS = {
    "add_rigid_body": op_add_rigid_body,
    "add_cloth": op_add_cloth,
    "add_collision": op_add_collision,
    "bake_physics": op_bake_physics,
}
