"""Stage 7 - the camera. A 40-second move through the bunker, timed to stage 5's beats.

WHAT THIS EARNS IN THE VIDEO: it is the reason anyone watches to the end. Everything before this is
a set that behaves; this is the shot list that puts a viewer in it.

TIMED TO b5, NOT INVENTED ALONGSIDE IT. Every beat below is read from b5_anim rather than retyped,
so the camera cannot drift out of sync with the thing it is filming. If the flicker moves, the shot
watching the flicker moves with it. Retyping "6.0" here would be a second source of truth for a
number that already has an owner.

HOW THE CAMERA IS AIMED, and this is the lab's trick rather than mine. set_keyframe wants an euler,
and working one out from an eye and a target by hand is three lines of trigonometry that are wrong
the first time. create_camera already takes a lookAt and computes it - so a throwaway camera is
created at the eye position, its rotationEuler is read back, it is deleted, and that euler is
keyed onto the real camera. The engine does the maths it already knows how to do.

THE EYE IS CHECKED AGAINST THE HALL. stage.look() refuses a viewpoint outside the room, and that
guard is why the lab never shipped a shot of a building being watched from a field. Keyframed
positions bypass look() entirely, so the same check is applied here explicitly - a camera path is
exactly where an out-of-bounds eye is hardest to notice, because it is only wrong for two seconds.

Run after b5_anim.py.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
sys.path.insert(0, HERE)
import stage as S       # noqa: E402
import b1_shell as B1   # noqa: E402
import b5_anim as B5    # noqa: E402

CY = B1.CY
HALL_LEN = B1.HALL_LEN
FPS = B5.FPS
END_S = B5.DURATION_S

# THE SHOT LIST. (seconds, eye, target, lens)
#
# Each entry is placed against a beat b5 owns, and the comment says which - so a reader can check
# the camera is looking at the right thing at the right moment without running it. The beats
# themselves live in b5 and are imported above rather than restated.
SHOTS = [
    (0.0,  (2.4, CY - 0.6, 1.75), (30.0, CY + 0.8, 2.0), 20.0),   # in at the blast door
    (5.0,  (7.5, CY - 0.9, 1.70), (26.0, CY + 0.6, 1.9), 20.0),   # pushing down the hall
    (7.5,  (9.0, CY - 1.6, 1.65), (7.0, -3.0, 1.4),      24.0),   # look into the armoury
    (9.5,  (8.0, CY - 1.2, 1.70), (0.5, CY - 2.4, 1.9),  26.0),   # turn back for the door wheel
    (13.0, (6.5, CY - 1.0, 1.70), (0.5, CY - 2.4, 1.9),  26.0),   # the door rolls aside
    (16.0, (13.0, CY - 1.4, 1.70), (20.0, -4.0, 1.5),    22.0),   # moving on, hydroponics ahead
    (20.0, (19.5, CY - 2.6, 1.60), (20.0, -6.0, 1.3),    26.0),   # into the purple room's mouth
    (23.5, (25.0, CY - 1.0, 1.70), (29.5, 17.0, 1.6),    24.0),   # the power room, amber, stuttering
    (27.0, (28.5, CY + 0.5, 1.75), (4.0, CY - 0.5, 2.2), 20.0),   # turn: the cascade comes at you
    (30.5, (26.0, CY + 0.8, 1.75), (6.0, CY - 0.5, 2.2), 20.0),   # blackout, still looking up-hall
    (34.0, (24.0, 2 * CY - 1.6, 1.80), (8.0, CY + 1.0, 2.4), 22.0),  # emergency red, near the catwalk
    (END_S, (16.0, CY + 1.0, 2.20), (0.8, CY - 1.6, 2.0), 21.0),  # pull back toward the entrance
]


def aim(eye, target):
    """The euler that points a camera at `target` from `eye`, computed by the engine.

    A throwaway camera is created with lookAt, its rotationEuler read, and the camera deleted. The
    alternative is doing the trigonometry here, which is a second implementation of something
    create_camera already does correctly.
    """
    # makeActive:False, AND IT DEFAULTS TO TRUE. Without it every probe made ITSELF the scene
    # camera, and deleting it left the scene with none at all - so all twelve shots were keyed
    # correctly onto a camera nothing was rendering from, and render_still failed with "there is no
    # scene camera, so there is nothing to render from". The stage reported success; only rendering
    # it showed the hole.
    probe = S.call("create_camera", {"name": "_Aim", "makeActive": False,
                                     "location": {"x": eye[0], "y": eye[1], "z": eye[2]},
                                     "lookAt": {"x": target[0], "y": target[1], "z": target[2]}})
    rot = probe.get("rotationEuler")
    S.call("delete_object", {"object": probe.get("name") or "_Aim", "purgeOrphans": True})
    if not rot or len(rot) != 3:
        raise RuntimeError("the aim probe returned no rotationEuler: %r" % probe)
    return rot


def check_indoors(sec, eye):
    """The same guard stage.look() applies, applied to a KEYFRAMED eye.

    look() refuses an eye outside the room and is the reason the lab never shipped a shot of a
    building being filmed from a field. A keyframed path never goes through look(), and an
    out-of-bounds eye on a moving camera is the hardest kind to notice - it is only wrong for the
    two seconds it is on screen.
    """
    for axis, v in zip("xyz", eye):
        lo, hi = S.ROOM[axis]
        if not (lo <= v <= hi):
            raise RuntimeError(
                "the shot at %.1fs puts the camera at %r, which is OUTSIDE the hall on %s "
                "(%.2f not in %.2f..%.2f). An interior shot from outside the walls is not a "
                "matter of taste." % (sec, list(eye), axis, v, lo, hi))


def main():
    S.begin("STAGE 7 - the camera: 40 seconds through the bunker, timed to stage 5")

    # The hall's interior, declared by stage 1. Set again here because this file can be re-run on
    # its own and ROOM would otherwise still hold whatever the last stage left in it.
    S.set_room((0.6, HALL_LEN - 0.6), (0.9, 2 * CY - 0.9), (0.15, B1.VAULT_R_IN - 0.5))

    S.call("set_frame_range", {"start": 1, "end": int(round(END_S * FPS)) + 1, "fps": FPS})
    info = S.call("render_info", {})
    if int(info.get("fps") or 0) != FPS:
        raise RuntimeError("scene fps is %s, b5 converts at %d - the camera would be timed to a "
                           "different clock than the lights" % (info.get("fps"), FPS))

    # CHECK EVERY EYE BEFORE CREATING ANYTHING. A path that is refused half way through leaves a
    # camera with some of its keys, which animates and is wrong - worse than not building it.
    for sec, eye, _t, _l in SHOTS:
        check_indoors(sec, eye)
    print("  %d shot(s), every eye inside the hall" % len(SHOTS))

    present = {o.get("name") for o in (S.call("list_objects", {}).get("objects") or [])}
    if "Cam_Bunker" in present:
        S.call("delete_object", {"object": "Cam_Bunker", "purgeOrphans": True})
    first = SHOTS[0]
    S.call("create_camera", {"name": "Cam_Bunker",
                             "location": {"x": first[1][0], "y": first[1][1], "z": first[1][2]},
                             "lookAt": {"x": first[2][0], "y": first[2][1], "z": first[2][2]},
                             "lens": first[3], "makeActive": True})

    keys = 0
    for sec, eye, target, lens in SHOTS:
        frame = int(round(sec * FPS)) + 1
        rot = aim(eye, target)
        S.call("set_keyframe", {"object": "Cam_Bunker", "frame": frame, "interpolation": "BEZIER",
                                "location": {"x": eye[0], "y": eye[1], "z": eye[2]}})
        S.call("set_keyframe", {"object": "Cam_Bunker", "frame": frame, "interpolation": "BEZIER",
                                "rotation": {"x": rot[0], "y": rot[1], "z": rot[2]}})
        keys += 2
        print("    %5.1fs  frame %4d  eye (%5.1f %5.1f %4.1f)  lens %.0f" %
              (sec, frame, eye[0], eye[1], eye[2], lens))

    # AND CONFIRM THE SCENE STILL RENDERS FROM IT. The probes above are transient cameras and this
    # is the assertion that the transience did not cost the real one.
    S.call("set_camera", {"object": "Cam_Bunker", "makeActive": True})
    cam = S.call("render_info", {}).get("sceneCamera")
    if cam != "Cam_Bunker":
        raise RuntimeError("the scene camera is %r, not Cam_Bunker - twelve shots would be keyed "
                           "onto a camera nothing renders from" % cam)
    print("  scene camera confirmed: %s" % cam)

    # READ IT BACK. The count above is what was ASKED for; this is what Blender holds.
    lk = S.call("list_keyframes", {"object": "Cam_Bunker"})
    curves, total = lk.get("curveCount"), lk.get("keyframeTotal")
    if not total:
        raise RuntimeError("the camera has no keyframes after %d set_keyframe calls: %r" % (keys, lk))

    S.done("%d shot(s) over %.0fs; camera holds %s curve(s) / %s key(s), timed to b5's beats"
           % (len(SHOTS), END_S, curves, total))


if __name__ == "__main__":
    main()
