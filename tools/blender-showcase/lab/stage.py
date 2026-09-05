"""Shared plumbing for the staged lab build - one stage per file, so a recording has chapters.

WHY STAGED RATHER THAN ONE SCRIPT. Andre is recording this. A single script that runs for four
minutes and then reveals a finished room shows nothing about which capability did what; six stages
that each visibly change the viewport show the bridge actually working. It is also how you find out
WHICH stage broke something, which one script never tells you.

EVERY CALL IS A TYPED OP. run_python is not used anywhere in this build - not because it would not
work, but because the whole point is to demonstrate the typed surface. The op counter at the end of
each stage prints what was used, so the claim is measured rather than asserted.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import blender_audit_common as _B

USED = {}
_T0 = [None]


def call(op, payload=None, timeout=180.0):
    USED[op] = USED.get(op, 0) + 1
    r = _B.call(op, payload or {}, timeout=timeout)
    if r.get("ok") is False:
        raise RuntimeError("%s failed: %s\n   payload=%r" % (op, r.get("error"), payload))
    return r


def begin(title):
    _T0[0] = time.time()
    print("=" * 74)
    print(title)
    print("=" * 74)


def done(note=""):
    objs = call("list_objects", {}).get("objects") or []
    print("")
    print("  objects in scene : %d" % len(objs))
    if note:
        print("  %s" % note)
    print("  ops used         : %s" % ", ".join("%s x%d" % (k, v)
                                                for k, v in sorted(USED.items(),
                                                                   key=lambda kv: -kv[1])))
    print("  run_python used  : %s" % ("YES" if "run_python" in USED else "no - typed ops only"))
    print("  elapsed          : %.1fs" % (time.time() - _T0[0]))


# ---------------------------------------------------------------- geometry helpers
def box(name, x0, x1, y0, y1, z0, z1):
    """A cube placed by its two corners, with the scale baked and the origin left at its centre.

    Scale only - baking LOCATION too would move every origin to the world origin, which renders
    identically and is miserable to select and drag. Learned on the house build.
    """
    sx, sy, sz = (x1 - x0), (y1 - y0), (z1 - z0)
    call("create_primitive", {"kind": "cube", "name": name, "size": 1.0,
                              "location": {"x": (x0 + x1) / 2.0, "y": (y0 + y1) / 2.0,
                                           "z": (z0 + z1) / 2.0}})
    call("transform_object", {"object": name, "scale": {"x": sx, "y": sy, "z": sz}})
    call("apply_transform", {"object": name, "scale": True, "location": False, "rotation": False})
    return name


def cyl(name, x, y, z0, z1, r, verts=16, axis="z"):
    import math
    call("create_primitive", {"kind": "cylinder", "name": name, "radius": r,
                              "depth": (z1 - z0), "vertices": verts,
                              "location": {"x": x, "y": y, "z": (z0 + z1) / 2.0}})
    if axis != "z":
        # RADIANS. transform_object writes rotation_euler straight through, and passing degrees
        # here put a roof at 82 degrees on the house build.
        rot = {"x": math.pi / 2.0, "y": 0.0, "z": 0.0} if axis == "y" else \
              {"x": 0.0, "y": math.pi / 2.0, "z": 0.0}
        call("transform_object", {"object": name, "rotation": rot})
    return name


def cut(target, cutter_name, x0, x1, y0, y1, z0, z1):
    box(cutter_name, x0, x1, y0, y1, z0, z1)
    call("boolean_op", {"target": target, "cutter": cutter_name,
                        "operation": "difference", "deleteCutter": True})


def paint(obj, mat):
    call("set_material_slots", {"object": obj, "slots": [mat], "allowResize": True})


def mat(name, rgb, metallic=0.0, roughness=0.6):
    call("create_material", {"name": name, "reuse": True, "baseColor": list(rgb),
                             "metallic": metallic, "roughness": roughness})
    return name


# The room's interior, so a viewpoint can be CHECKED rather than hoped for. Kept here because
# every stage needs it and none of them should be re-deriving it.
#
# SETTABLE AS OF 2026-09-05, and it had to become so before anything else could reuse this file.
# It was a bare constant holding the LAB's interior, and look() checks the camera eye against it -
# so a second showcase importing stage.py would have every legitimate viewpoint of ITS room refused,
# while eyes outside its walls sailed through. The bounds check is the valuable part of look(); a
# bounds check against the wrong room is worse than none, because it reads as having been checked.
#
# Defaults to exactly what it always was, so the lab stages are unaffected.
ROOM = {"x": (0.32, 17.68), "y": (0.32, 10.68), "z": (0.05, 3.55)}


def set_room(x, y, z):
    """Declare the interior the camera must stay inside. Call it before the first look()."""
    ROOM["x"], ROOM["y"], ROOM["z"] = tuple(x), tuple(y), tuple(z)
    return ROOM


def look(eye, target, lens=24.0, settle=0.4):
    """Stand at `eye`, look at `target`. BOTH are world positions, and the eye must be INDOORS.

    WHY THIS TAKES AN EYE POSITION AND NOT AN ORBIT. The first version took a focus point, a
    distance and two angles - which is how you inspect an object from outside it, and is exactly
    wrong for standing in a room. focus (9, 5.5, 1.2) at distance 26 put the eye at about
    (-4.4, -14.1, 11.8): fourteen metres behind the south wall and eight metres above the ceiling,
    watching a building get built from a field. Andre saw it immediately; the arithmetic was there
    to be checked and I had not checked it.

    THE BOUNDS CHECK IS THE POINT. An interior viewpoint outside the walls is not a matter of taste,
    it is wrong, so it raises here rather than quietly producing a shot of the outside of a box.
    """
    import time
    for axis, v in zip("xyz", eye):
        lo, hi = ROOM[axis]
        if not (lo <= v <= hi):
            raise RuntimeError(
                "camera eye %r is OUTSIDE the room on %s (%.2f not in %.2f..%.2f). An interior "
                "shot from outside the walls looks like watching a building from a field."
                % (list(eye), axis, v, lo, hi))
    call("set_viewport_view", {"lookFrom": {"x": eye[0], "y": eye[1], "z": eye[2]},
                               "focus": {"x": target[0], "y": target[1], "z": target[2]},
                               "lens": lens, "perspective": "PERSP"})
    if settle:
        time.sleep(settle)
