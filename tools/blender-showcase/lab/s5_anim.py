"""STAGE 5 - animation: the fan spins, one fluorescent flickers, the camera travels.

THE FLICKER IS THE ONE THAT LOOKS WRONG IF DONE CARELESSLY. Blender's default interpolation is
BEZIER, which eases between keys - a light keyed 200/0/200 on BEZIER FADES up and down and reads as
a pulsing lamp, not a failing tube. CONSTANT makes it snap, and the difference on camera is the
difference between "animated" and "broken fluorescent".

Three fixtures, three different patterns, because identical flicker on every light reads as a
global effect rather than as one dying tube.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, look

FPS = 24
END = 24 * 24          # 24 seconds

# frame -> energy, per fixture. 01/04/05 are healthy, 02 is the dying one, 03 stutters twice.
#
# EVERY PATTERN STARTS ON. The first version keyed 02 and 03 to 0 at frame 1, so a still frame -
# which is what anyone looking at the scene sees before pressing play - showed one lit tube and
# black. A flicker has to be a CHANGE from lit, or it just reads as a broken scene.
PATTERNS = {
    "Fluoro_01": [(1, 320), (END, 320)],
    "Fluoro_04": [(1, 280), (END, 280)],
    "Fluoro_05": [(1, 280), (END, 280)],
    "Fluoro_02": [(1, 320), (26, 320), (28, 0), (30, 320), (33, 0), (35, 310), (96, 320),
                  (99, 0), (101, 320), (150, 320), (153, 0), (156, 320), (END, 320)],
    "Fluoro_03": [(1, 300), (60, 300), (62, 0), (64, 300), (200, 300), (203, 0), (206, 300),
                  (END, 300)],
}


def build():
    begin("STAGE 5  animation - fan, per-fixture flicker patterns, camera move")
    call("set_frame_range", {"start": 1, "end": END, "fps": FPS})

    look((14.60, 5.00, 2.00), (17.20, 7.60, 2.60))
    # ---- the extract fan: LINEAR, or it eases at both ends and looks like it is being switched
    # on and off rather than running.
    for i in range(4):
        name = "Vent_Fan_Blade%d" % (i + 1)
        call("set_keyframe", {"object": name, "frame": 1,
                              "rotation": {"x": 0.0, "y": i * math.pi / 4.0, "z": 0.0},
                              "interpolation": "LINEAR"})
        call("set_keyframe", {"object": name, "frame": END,
                              "rotation": {"x": 0.0, "y": i * math.pi / 4.0 + 12 * math.pi,
                                           "z": 0.0},
                              "interpolation": "LINEAR"})

    look((10.40, 4.00, 1.90), (8.20, 7.40, 2.90))
    # ---- flicker, keyed on the light DATA rather than the object -------------------------
    keys = 0
    for light, pattern in PATTERNS.items():
        for frame, energy in pattern:
            call("set_keyframe", {"object": light, "frame": frame, "dataPath": "energy",
                                  "value": float(energy), "interpolation": "CONSTANT"})
            keys += 1

    look((16.00, 1.60, 1.70), (3.00, 4.00, 1.20))
    # ---- the camera: a slow push through the room, ending on the workstation -------------
    call("create_camera", {"name": "Cam_Main", "location": {"x": 16.4, "y": 1.4, "z": 1.75},
                           "lookAt": {"x": 3.0, "y": 4.0, "z": 1.2}, "lens": 28,
                           "fStop": 2.2, "dofDistance": 9.0})
    path = [(1, (16.4, 1.4, 1.75), (3.0, 4.0, 1.2)),
            (240, (11.0, 2.6, 1.70), (3.0, 3.4, 1.15)),
            (420, (6.4, 3.4, 1.62), (2.4, 1.6, 1.05)),
            (END, (4.6, 3.2, 1.58), (2.2, 1.3, 1.00))]
    for frame, loc, _ in path:
        call("set_keyframe", {"object": "Cam_Main", "frame": frame,
                              "location": {"x": loc[0], "y": loc[1], "z": loc[2]},
                              "interpolation": "BEZIER"})

    # The aim is keyed too, by re-deriving the rotation at each waypoint through create_camera's
    # own lookAt maths - done here by setting rotation explicitly from a throwaway camera, so the
    # travel keeps pointing where it should instead of sliding off as the position changes.
    for frame, loc, tgt in path:
        probe = call("create_camera", {"name": "_Aim", "location": {"x": loc[0], "y": loc[1],
                                                                    "z": loc[2]},
                                       "lookAt": {"x": tgt[0], "y": tgt[1], "z": tgt[2]},
                                       "makeActive": False})
        rot = probe.get("rotationEuler")
        call("delete_object", {"object": probe.get("name")})
        call("set_keyframe", {"object": "Cam_Main", "frame": frame,
                              "rotation": {"x": rot[0], "y": rot[1], "z": rot[2]},
                              "interpolation": "BEZIER"})

    lk = call("list_keyframes", {"object": "Cam_Main"})
    done("%d flicker keys, fan LINEAR over %d frames, camera %d curves / %d keys"
         % (keys, END, lk.get("curveCount"), lk.get("keyframeTotal")))


if __name__ == "__main__":
    build()
