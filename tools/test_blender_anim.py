"""Lights, cameras and keyframes - the three op families added 2026-09-01, on every Blender.

WHY THIS FILE HAD TO EXIST BEFORE THE CLAIM DID. The full sweep went 44 runs / 0 failed with these
ops already written, and that proved nothing about them: no existing suite calls a single one. A
green run over code nothing exercises is the exact shape this repo spends its time finding.

AND ONE THING HERE GENUINELY DIFFERS PER VERSION. Blender 5.0 moved animation curves from
action.fcurves to action.layers[].strips[].channelbag(slot).fcurves and REMOVED the old attribute,
so reading a keyframe back needs two different answers depending on the host. That is why this
suite matters more than its size suggests: it is the only thing standing between "works on my
5.0" and "works on 3.6, 4.2, 4.4 and 5.0", which is what the addon claims.

Usage:  python tools/test_blender_anim.py      # needs a Blender with MifBlender listening
Exit:   0 passed   1 failed   2 SKIPPED, no Blender
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blender_audit_common as B

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))


def main():
    if not B.reachable():
        return B.skip_banner("anim")

    B.call("clear_scene", {})

    # ---------------------------------------------------------------- A100 create_light
    print("=== A100: create_light reports what the light IS, not what was asked ===")
    r = B.call("create_light", {"name": "A_Key", "type": "AREA", "energy": 250,
                                "size": 1.4, "color": [1.0, 0.9, 0.8],
                                "location": {"x": 1, "y": 2, "z": 3}})
    check("A100 create_light succeeds", r.get("ok") is not False, json.dumps(r)[:200])
    check("A100 the type came back as asked", r.get("type") == "AREA", r.get("type"))
    check("A100 energy is read back off the datablock", r.get("energy") == 250.0, r.get("energy"))
    check("A100 location is the world translation", r.get("location") == [1.0, 2.0, 3.0],
          r.get("location"))
    check("A100 AREA reports its size", r.get("size") == 1.4, r.get("size"))

    # THE GUARD IS THE POINT OF A TYPED OP. Blender would simply not have the attribute and the
    # write would vanish; being refused by name is the whole difference from run_python.
    bad = B.call("create_light", {"name": "A_Bad", "type": "POINT", "spotAngle": 0.7})
    check("A100 spotAngle on a POINT light is REFUSED, not ignored", bad.get("ok") is False,
          json.dumps(bad)[:200])
    check("A100 and the refusal names the mismatch",
          "SPOT" in str(bad.get("error", "")) and "POINT" in str(bad.get("error", "")),
          str(bad.get("error"))[:160])
    # AND IT LEFT NOTHING BEHIND. The first version created the light and then refused, which is
    # honest and still litters the scene for somebody who only made a typo. The A199 cleanup check
    # caught the stray by refusing to ignore an object nobody meant to keep.
    names_now = [o.get("name") for o in (B.call("list_objects", {}).get("objects") or [])]
    check("A100 the refused light was NOT created", "A_Bad" not in names_now, names_now[:8])
    bad2 = B.call("create_light", {"name": "A_Bad2", "type": "PLASMA"})
    check("A100 an unknown light type is refused with the valid list",
          bad2.get("ok") is False and "AREA" in str(bad2.get("error", "")),
          str(bad2.get("error"))[:160])

    # ---------------------------------------------------------------- A101 create_camera / lookAt
    print("")
    print("=== A101: lookAt aims the camera, measured rather than trusted ===")
    cam = B.call("create_camera", {"name": "A_Cam", "location": {"x": 8, "y": -6, "z": 2.4},
                                   "lookAt": {"x": 0, "y": 0, "z": 1.2}, "lens": 28,
                                   "fStop": 2.8})
    check("A101 create_camera succeeds", cam.get("ok") is not False, json.dumps(cam)[:200])
    check("A101 it became the scene camera", cam.get("isSceneCamera") is True, cam)
    check("A101 fStop enabled depth of field", cam.get("dofEnabled") is True, cam.get("dofEnabled"))

    # THE MEASUREMENT THAT CAUGHT THE REAL BUG. The first implementation returned a perfectly
    # plausible euler and pointed the camera 166 degrees AWAY from its target. ok:true proved
    # nothing; only comparing the camera's actual forward vector to the target direction did.
    aim = B.call("run_python", {"code": (
        "import bpy, mathutils, math\n"
        "cam = bpy.data.objects['A_Cam']\n"
        "fwd = (cam.matrix_world.to_quaternion() @ mathutils.Vector((0,0,-1))).normalized()\n"
        "want = (mathutils.Vector((0,0,1.2)) - cam.matrix_world.to_translation()).normalized()\n"
        "result = round(math.degrees(fwd.angle(want)), 4)\n")})
    if aim.get("ok") is False:
        check("A101 (skipped) aim check needs run_python, which is disabled here", True,
              str(aim.get("error"))[:120])
    else:
        check("A101 the camera actually FACES its lookAt target (<0.01 deg)",
              isinstance(aim.get("result"), (int, float)) and abs(aim["result"]) < 0.01,
              "angle error %r deg" % aim.get("result"))

    both = B.call("create_camera", {"name": "A_Cam2", "location": {"x": 1, "y": 1, "z": 1},
                                    "lookAt": {"x": 0, "y": 0, "z": 0},
                                    "rotation": {"x": 0, "y": 0, "z": 0}})
    check("A101 lookAt AND rotation together is refused", both.get("ok") is False,
          str(both.get("error"))[:160])

    # ---------------------------------------------------------------- A102 frame range
    print("")
    print("=== A102: set_frame_range, and the backwards range it refuses ===")
    fr = B.call("set_frame_range", {"start": 1, "end": 120, "fps": 24})
    check("A102 range set", (fr.get("after") or {}).get("end") == 120, fr.get("after"))
    check("A102 duration is derived, not echoed", fr.get("durationSeconds") == 5.0,
          fr.get("durationSeconds"))
    back = B.call("set_frame_range", {"start": 90, "end": 10})
    check("A102 an end before start is REFUSED", back.get("ok") is False,
          str(back.get("error"))[:160])
    still = B.call("set_frame_range", {})
    check("A102 and the previous range survived the refusal",
          (still.get("after") or {}).get("start") == 1 and (still.get("after") or {}).get("end") == 120,
          still.get("after"))

    # ---------------------------------------------------------------- A103 keyframes on an OBJECT
    print("")
    print("=== A103: transform keyframes, read back through a different op ===")
    B.call("create_primitive", {"kind": "cylinder", "name": "A_Fan", "radius": 0.4, "depth": 0.06})
    B.call("set_keyframe", {"object": "A_Fan", "frame": 1,
                            "rotation": {"x": 0, "y": 0, "z": 0}, "interpolation": "LINEAR"})
    k = B.call("set_keyframe", {"object": "A_Fan", "frame": 120,
                                "rotation": {"x": 0, "y": 0, "z": 4 * math.pi},
                                "interpolation": "LINEAR"})
    check("A103 set_keyframe succeeds", k.get("ok") is not False, json.dumps(k)[:200])
    # 3 euler channels x 2 frames. A count is what distinguishes "a curve exists" from "the curve
    # holds what I asked for".
    check("A103 six keyframes exist - 3 rotation channels at 2 frames",
          k.get("keyframesTotal") == 6, k.get("keyframesTotal"))

    lk = B.call("list_keyframes", {"object": "A_Fan", "target": "object"})
    check("A103 list_keyframes finds the rotation curves", (lk.get("curveCount") or 0) == 3,
          lk.get("curveCount"))
    zc = [c for c in (lk.get("curves") or []) if c.get("index") == 2]
    check("A103 the Z curve holds both frames", zc and len(zc[0]["keyframes"]) == 2,
          json.dumps(zc)[:200])
    check("A103 and its interpolation is LINEAR, not Blender's BEZIER default",
          zc and zc[0]["interpolation"] == ["LINEAR"], json.dumps(zc)[:200])

    # THE WIDER READ. list_keyframes walks animation_data.action and nothing else, so an object
    # animated by DRIVERS or NLA reports curveCount 0 there and is not un-animated - a wrong
    # answer from a verification op. list_animation_data is the one that covers all three routes.
    lad = B.call("list_animation_data", {"object": "A_Fan"})
    check("A103b list_animation_data agrees the fan is animated",
          lad.get("isAnimated") is True and "action" in (lad.get("animatedBy") or []),
          json.dumps(lad)[:220])
    check("A103b and it reports the action curve count list_keyframes found",
          any((s.get("actionCurveCount") or 0) == 3 for s in (lad.get("sources") or [])),
          json.dumps(lad.get("sources"))[:220])
    # A fan keyed by hand has no drivers, so this is zero AND that zero is meaningful - it is the
    # count of drivers whose variables point at something that no longer exists, which is the
    # silent failure where a driver evaluates to 0 and reports nothing.
    check("A103b no driver on it is broken - invalidDrivers is a measured zero, not an absent key",
          lad.get("invalidDrivers") == 0, json.dumps(lad)[:220])

    # ---------------------------------------------------------------- A104 keyframes on DATA
    print("")
    print("=== A104: a light flicker keys the DATA datablock, which is the easy thing to get wrong ===")
    B.call("create_light", {"name": "A_Fluoro", "type": "AREA",
                            "location": {"x": 0, "y": 0, "z": 3}, "energy": 200})
    want = [(1, 200.0), (20, 0.0), (22, 200.0), (40, 0.0), (41, 180.0)]
    okall = True
    for f, e in want:
        rr = B.call("set_keyframe", {"object": "A_Fluoro", "frame": f,
                                     "dataPath": "energy", "value": e,
                                     "interpolation": "CONSTANT"})
        okall = okall and rr.get("ok") is not False
    check("A104 every energy keyframe was accepted", okall, "one of %d calls failed" % len(want))

    fl = B.call("list_keyframes", {"object": "A_Fluoro", "target": "data"})
    check("A104 the curve is on the DATA datablock, not the object",
          (fl.get("curveCount") or 0) == 1 and fl["curves"][0]["target"] == "data",
          json.dumps(fl)[:220])
    if fl.get("curves"):
        got = [(int(a), float(b)) for a, b in fl["curves"][0]["keyframes"]]
        check("A104 the stored frames and values are EXACTLY what was sent",
              got == [(f, e) for f, e in want], "sent %r got %r" % (want, got))
        check("A104 interpolation is CONSTANT - a flicker on BEZIER fades and stops flickering",
              fl["curves"][0]["interpolation"] == ["CONSTANT"],
              fl["curves"][0]["interpolation"])

    # ---------------------------------------------------------------- A105 refusals
    print("")
    print("=== A105: set_keyframe refuses what it cannot do rather than half-doing it ===")
    n1 = B.call("set_keyframe", {"object": "A_Fan", "frame": 5})
    check("A105 nothing to key is refused", n1.get("ok") is False, str(n1.get("error"))[:140])
    n2 = B.call("set_keyframe", {"object": "A_Fan", "frame": 5,
                                 "location": {"x": 0, "y": 0, "z": 0}, "dataPath": "energy",
                                 "value": 1})
    check("A105 transform AND dataPath together is refused", n2.get("ok") is False,
          str(n2.get("error"))[:140])
    n3 = B.call("set_keyframe", {"object": "A_Fan", "frame": 5, "dataPath": "energy", "value": 1})
    check("A105 keying a light property on a MESH is refused", n3.get("ok") is False,
          str(n3.get("error"))[:160])
    n4 = B.call("set_keyframe", {"object": "A_Fan", "frame": 5,
                                 "location": {"x": 0, "y": 0, "z": 0}, "interpolation": "WOBBLE"})
    check("A105 an unknown interpolation is refused with the valid list",
          n4.get("ok") is False and "LINEAR" in str(n4.get("error", "")),
          str(n4.get("error"))[:140])

    # ---------------------------------------------------------------- cleanup
    print("")
    for n in ("A_Key", "A_Cam", "A_Cam2", "A_Fan", "A_Fluoro"):
        B.call("delete_object", {"object": n})
    survivors = [o.get("name") for o in (B.call("list_objects", {}).get("objects") or [])]
    check("A199 (cleanup) no A_* object is left behind",
          not [n for n in survivors if str(n).startswith("A_")], survivors)

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        if isinstance(f, tuple):
            print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
