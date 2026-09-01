"""STAGE 7 - the 55-second cinematic, beat for beat.

The timeline is the one from the benchmark proposal, kept verbatim rather than reinterpreted, so
the result can be checked against what was asked for:

    00:00  lights turn on
    00:05  a ventilation fan starts spinning
    00:08  the computer boots
    00:12  one fluorescent begins flickering
    00:15  steam begins coming from a pipe
    00:20  the camera moves through the laboratory
    00:30  a warning light begins flashing
    00:35  something falls from a shelf
    00:40  the camera reaches the main workstation
    00:45  the computer monitor changes
    00:50  the lights suddenly shut off
    00:52  emergency lighting activates
    00:55  the camera pulls back

EVERY BEAT IS A SECOND, AND SECONDS ARE NOT FRAMES. At 24 fps this is 1320 frames, and writing 20
where 480 was meant is the easiest possible mistake to make silently - the render just looks wrong.
So beats are declared in SECONDS and converted in one place, and the conversion is asserted against
the scene's real fps rather than a constant typed here.

WHAT MAKES THIS A TEST RATHER THAN A DEMO. It uses six of the nine capability families at once -
lights, keyframes, physics, particles, cameras, world - and every one has to agree about the same
timeline. A keyframe on the wrong datablock, a simulation that was never baked, or a light keyed
with the wrong interpolation all produce a file that opens fine and plays wrong.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, box, cyl, paint, mat, look

FPS = 24
END_S = 56.0

# The beats, in SECONDS. Converted once, below.
BEAT = {
    "lights_on": 0.0, "fan_start": 5.0, "computer_boot": 8.0, "flicker_start": 12.0,
    "steam_start": 15.0, "camera_move": 20.0, "warning_start": 30.0, "fall": 35.0,
    "reach_desk": 40.0, "monitor_change": 45.0, "blackout": 50.0,
    "emergency": 52.0, "pull_back": 55.0,
}


def f(seconds):
    """Seconds -> frame. One place, so a beat can never be read as a frame number by mistake."""
    return int(round(seconds * FPS)) + 1


def key(obj, sec, path=None, value=None, interp="CONSTANT", **kw):
    p = {"object": obj, "frame": f(sec), "interpolation": interp}
    if path is not None:
        p["dataPath"] = path
        p["value"] = value
    p.update(kw)
    return call("set_keyframe", p)


def build():
    begin("STAGE 7  the 55-second cinematic - 13 beats across six capability families")

    r = call("set_frame_range", {"start": 1, "end": f(END_S), "fps": FPS})
    after = r.get("after") or {}
    # ASSERTED, not assumed. If the scene's fps is not what this file converts with, every beat
    # lands somewhere else and the only symptom is that the timing looks wrong.
    if after.get("fps") != FPS:
        raise RuntimeError("scene fps is %r, this file converts seconds at %d - every beat would "
                           "land on the wrong frame." % (after.get("fps"), FPS))
    print("  %d frames at %d fps = %.1fs" % (after.get("end"), FPS, r.get("durationSeconds")))

    FLUOROS = ["Fluoro_01", "Fluoro_02", "Fluoro_03", "Fluoro_04", "Fluoro_05"]
    WATTS = {"Fluoro_01": 320.0, "Fluoro_02": 320.0, "Fluoro_03": 300.0,
             "Fluoro_04": 280.0, "Fluoro_05": 280.0}

    # 00:00 lights come up, staggered so it reads as a circuit warming rather than a switch
    for i, nm in enumerate(FLUOROS):
        key(nm, 0.0, "energy", 0.0)
        key(nm, 0.6 + i * 0.35, "energy", WATTS[nm] * 0.35)
        key(nm, 0.9 + i * 0.35, "energy", 0.0)
        key(nm, 1.2 + i * 0.35, "energy", WATTS[nm])
    key("Window_Shaft", 0.0, "energy", 4200.0)

    # 00:05 the fan spins up, LINEAR or it eases and looks switched rather than running
    for i in range(4):
        nm = "Vent_Fan_Blade%d" % (i + 1)
        base = i * math.pi / 4.0
        key(nm, BEAT["fan_start"], rotation={"x": 0.0, "y": base, "z": 0.0}, interp="LINEAR")
        key(nm, END_S, rotation={"x": 0.0, "y": base + 26 * math.pi, "z": 0.0}, interp="LINEAR")

    # 00:08 the computer boots - the monitor practical, off then on
    key("Monitor_Light", 0.0, "energy", 0.0)
    key("Monitor_Light", BEAT["computer_boot"], "energy", 0.0)
    key("Monitor_Light", BEAT["computer_boot"] + 0.3, "energy", 40.0)
    key("Monitor_Light", BEAT["computer_boot"] + 0.5, "energy", 110.0)

    # 00:12 Fluoro_02 starts dying. CONSTANT - on BEZIER it fades and stops being a flicker.
    t = BEAT["flicker_start"]
    for off, e in ((0.0, 320), (0.1, 0), (0.22, 320), (0.5, 0), (0.6, 300),
                   (4.0, 320), (4.15, 0), (4.3, 320), (9.0, 320), (9.2, 0), (9.5, 315)):
        key("Fluoro_02", t + off, "energy", float(e))

    # 00:15 steam from the ceiling pipe
    src = box("Steam_Source", 2.85, 3.15, 5.2, 5.6, 3.05, 3.10)
    paint(src, "Grime")
    mat("Steam", (0.82, 0.84, 0.86), roughness=0.95)
    puff = cyl("Steam_Puff", -9.0, -9.0, 0.0, 0.05, 0.045, 8)
    paint(puff, "Steam")
    call("add_particles", {
        "object": src, "type": "EMITTER", "count": 700,
        "frameStart": f(BEAT["steam_start"]), "frameEnd": f(END_S), "lifetime": 90,
        "lifetimeRandom": 0.5, "emitFrom": "FACE", "physicsType": "NEWTON",
        "gravityFactor": -0.06, "normalFactor": 0.35, "randomFactor": 0.25,
        "size": 1.0, "sizeRandom": 0.6,
        "renderType": "OBJECT", "instanceObject": "Steam_Puff", "showEmitter": False,
    })

    # 00:30 a warning lamp starts flashing, red, on its own rhythm
    call("create_light", {"name": "Warning_Lamp", "type": "POINT", "radius": 0.12,
                          "location": {"x": 16.4, "y": 9.6, "z": 2.7},
                          "energy": 0.0, "color": [1.0, 0.12, 0.06]})
    key("Warning_Lamp", 0.0, "energy", 0.0)
    tw = BEAT["warning_start"]
    while tw < BEAT["blackout"]:
        key("Warning_Lamp", tw, "energy", 0.0)
        key("Warning_Lamp", tw + 0.35, "energy", 260.0)
        key("Warning_Lamp", tw + 0.75, "energy", 0.0)
        tw += 1.4

    # 00:35 something falls off a shelf - a REAL rigid body, not a keyframed drop
    faller = box("Falling_Can", 0.62, 0.78, 6.9, 7.06, 1.28, 1.52)
    paint(faller, "Rusted_Metal")
    call("add_rigid_body", {"object": faller, "type": "ACTIVE", "mass": 1.2,
                            "bounciness": 0.35, "collisionShape": "BOX"})
    call("add_rigid_body", {"object": "Floor", "type": "PASSIVE", "friction": 0.9})
    # It has to HOLD until the beat, then let go. kinematic means keyframes drive it; turning that
    # off at the beat hands it to the simulation, which is what makes the fall real.
    # THE PATH IS DOTTED because kinematic lives on obj.rigid_body, not on the object. Writing it
    # as "kinematic" fails with "'Object' object has no attribute 'kinematic'" - which names the
    # leaf and hides that the walk was the problem.
    key(faller, 0.0, "rigid_body.kinematic", True, target="object")
    key(faller, BEAT["fall"], "rigid_body.kinematic", True, target="object")
    key(faller, BEAT["fall"] + 0.05, "rigid_body.kinematic", False, target="object")

    # 00:45 the monitor changes - a colour shift on its practical
    key("Monitor_Light", BEAT["monitor_change"], "color", [0.30, 0.95, 0.72])
    key("Monitor_Light", BEAT["monitor_change"] + 0.1, "color", [0.95, 0.35, 0.15])

    # 00:50 blackout, 00:52 emergency lighting
    for nm in FLUOROS:
        key(nm, BEAT["blackout"] - 0.05, "energy", WATTS[nm])
        key(nm, BEAT["blackout"], "energy", 0.0)
    key("Bench_Practical", BEAT["blackout"], "energy", 0.0)
    key("Monitor_Light", BEAT["blackout"], "energy", 0.0)
    key("Window_Shaft", BEAT["blackout"], "energy", 900.0)

    # PLACED WHERE THE CAMERA IS LOOKING AT 00:53, not where a real building would put it.
    # The first version sat at (9, 5.5, 3.2) - a sensible spot for an emergency light and directly
    # BEHIND the camera at that beat, which by then is at about (9.6, 2.6, 1.8) looking toward
    # (5.3, 4.6, 1.3). The beat measured as a pure black frame, and the light was working
    # perfectly: rendering the same frame from a static camera showed peak luminance 0.63.
    #
    # Worth stating because it cost a real detour: every op reported correctly, the keyframe was on
    # the right datablock at the right frame, and 100x the wattage changed nothing - because the
    # problem was never brightness. A light nobody is looking at is not a lighting bug.
    call("create_light", {"name": "Emergency_Lamp", "type": "SPOT", "spotAngle": 2.0,
                          "spotBlend": 0.45,
                          "location": {"x": 5.2, "y": 4.6, "z": 3.15},
                          "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                          "energy": 0.0, "color": [1.0, 0.30, 0.16]})
    # A second one further down the room, so the pull-back at 00:55 has something to reveal.
    call("create_light", {"name": "Emergency_Lamp_B", "type": "SPOT", "spotAngle": 2.0,
                          "spotBlend": 0.45,
                          "location": {"x": 11.6, "y": 6.2, "z": 3.15},
                          "energy": 0.0, "color": [1.0, 0.30, 0.16]})
    key("Emergency_Lamp_B", 0.0, "energy", 0.0)
    key("Emergency_Lamp_B", BEAT["emergency"] + 0.25, "energy", 0.0)
    key("Emergency_Lamp_B", BEAT["emergency"] + 0.4, "energy", 380.0)
    key("Emergency_Lamp", 0.0, "energy", 0.0)
    key("Emergency_Lamp", BEAT["emergency"], "energy", 0.0)
    key("Emergency_Lamp", BEAT["emergency"] + 0.15, "energy", 900.0)

    # 00:20 -> 00:55 the camera: hold, travel, arrive, pull back
    PATH = [
        (0.0, (16.6, 1.5, 1.72), (6.0, 5.0, 1.30)),
        (BEAT["camera_move"], (16.6, 1.5, 1.72), (6.0, 5.0, 1.30)),
        (30.0, (12.4, 2.6, 1.68), (5.0, 4.2, 1.20)),
        (BEAT["reach_desk"], (8.4, 3.2, 1.62), (2.6, 1.5, 1.05)),
        (BEAT["monitor_change"], (6.0, 3.0, 1.58), (2.4, 1.3, 1.02)),
        (BEAT["pull_back"], (13.0, 2.2, 1.95), (6.0, 5.4, 1.40)),
        (END_S, (16.2, 1.8, 2.10), (7.0, 5.6, 1.50)),
    ]
    # This stage REPLACES stage 5's camera when there is one and stands alone when there is not,
    # so it can be run on top of a bare lit scene. Asking list_objects rather than catching the
    # failure: a delete that is allowed to fail hides a delete that failed for another reason.
    present = {o.get("name") for o in (call("list_objects", {}).get("objects") or [])}
    if "Cam_Main" in present:
        call("delete_object", {"object": "Cam_Main"})
    first = PATH[0]
    call("create_camera", {"name": "Cam_Main",
                           "location": {"x": first[1][0], "y": first[1][1], "z": first[1][2]},
                           "lookAt": {"x": first[2][0], "y": first[2][1], "z": first[2][2]},
                           "lens": 26, "fStop": 2.4, "dofDistance": 7.0})
    for sec, loc, tgt in PATH:
        key("Cam_Main", sec, location={"x": loc[0], "y": loc[1], "z": loc[2]}, interp="BEZIER")
        probe = call("create_camera", {"name": "_Aim",
                                       "location": {"x": loc[0], "y": loc[1], "z": loc[2]},
                                       "lookAt": {"x": tgt[0], "y": tgt[1], "z": tgt[2]},
                                       "makeActive": False})
        rot = probe.get("rotationEuler")
        call("delete_object", {"object": probe.get("name")})
        key("Cam_Main", sec, rotation={"x": rot[0], "y": rot[1], "z": rot[2]}, interp="BEZIER")

    # The falling can is stepped forward from frame 1; without this it sits at rest at frame 850.
    bake = call("bake_physics", {"start": 1, "end": f(END_S)}, timeout=900.0)
    print("  physics baked: %d cache(s)" % (bake.get("cacheCount") or 0))

    lk = call("list_keyframes", {"object": "Cam_Main"})
    done("13 beats over %.0fs (%d frames), camera %d curves / %d keys, %d cache(s) baked"
         % (END_S, f(END_S), lk.get("curveCount"), lk.get("keyframeTotal"),
            bake.get("cacheCount") or 0))


if __name__ == "__main__":
    build()
