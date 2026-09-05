"""Stage 5 - movement. The flicker, the blast door, and the power failing.

WHAT THIS EARNS IN THE VIDEO: the flicker is the money shot. Everything before this is a lit set;
this is the first stage where the bunker behaves like somewhere the power is going out.

THREE THINGS THE LAB LEARNED THE EXPENSIVE WAY AND THIS OBEYS:

  EVERY PATTERN STARTS ON. Its first version keyed two of three fixtures to 0 at frame 1, so the
  still frame anyone sees before pressing play showed one tube and blackness. A flicker is a CHANGE
  from lit; keyed the other way round it just reads as a broken scene.

  CONSTANT INTERPOLATION FOR A FLICKER. Blender's default is BEZIER, which eases - a light keyed
  320/0/320 on BEZIER fades up and down and reads as a pulsing lamp, not a failing tube. Ramps that
  are MEANT to be smooth (the grow lamps, the emergency lighting coming up) stay BEZIER on purpose,
  and the difference between the two is the whole point of choosing per key rather than globally.

  BEATS ARE DECLARED IN SECONDS AND CONVERTED IN ONE PLACE. Writing 20 where 480 was meant is the
  easiest mistake to make silently, because the render just looks wrong rather than failing. The
  conversion is asserted against the scene's real fps rather than against the number this file
  hoped for.

Run after b4_light.py - it animates the lamps that stage creates, and says so rather than creating
its own.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
sys.path.insert(0, HERE)
import stage as S       # noqa: E402
import b1_shell as B1   # noqa: E402
import b4_light as B4   # noqa: E402

CY = B1.CY
FPS = 24
DURATION_S = 40.0

# The mains wattage each fixture family sits at, taken from stage 4 rather than restated, so a
# tuning pass there cannot leave this file keying lights back to a number that no longer exists.
HALL_W = B4.HALL_PENDANT_W
EMG_DIM = 22.0
EMG_FULL = 260.0


def sec(t):
    """Seconds -> frame. ONE conversion, asserted against the scene's actual fps."""
    return int(round(t * FPS)) + 1


def key(obj, t, value=None, interp="CONSTANT", **kw):
    p = {"object": obj, "frame": sec(t), "interpolation": interp}
    if value is not None:
        p["dataPath"] = "energy"
        p["value"] = float(value)
    p.update(kw)
    return S.call("set_keyframe", p)


def main():
    S.begin("STAGE 5 - movement: the flicker, the door, and the power going out")

    S.call("set_frame_range", {"start": 1, "end": sec(DURATION_S), "fps": FPS})
    # ASSERTED, NOT ASSUMED. If the scene is running at a different fps than this file converts
    # with, every beat below lands somewhere else and the only symptom is that the timing looks
    # wrong - which is indistinguishable from bad timing.
    info = S.call("render_info", {})
    if int(info.get("fps") or 0) != FPS:
        raise RuntimeError("scene fps is %s but the beats convert at %d - every beat would land in "
                           "the wrong place and the only symptom would be that it looks badly timed"
                           % (info.get("fps"), FPS))
    print("  fps confirmed at %d, timeline %d frames (%.0fs)" % (FPS, sec(DURATION_S), DURATION_S))

    keys = 0

    # ---- the hall pendants: two are failing from the start ----------------------------------------
    # 0 and 6 are healthy and stay lit until the cascade. 2 stutters early, 4 is the dying one.
    print("  hall pendants - two are already failing")
    STUTTER = [(0.0, HALL_W), (3.2, 0.0), (3.28, HALL_W), (3.5, 0.0), (3.6, HALL_W),
               (12.0, HALL_W), (12.1, 0.0), (12.16, HALL_W)]
    DYING = [(0.0, HALL_W), (6.0, 0.0), (6.2, HALL_W * 0.55), (6.5, 0.0), (6.8, HALL_W),
             (17.0, HALL_W), (17.15, 0.0), (17.5, HALL_W * 0.4), (17.9, 0.0), (18.4, HALL_W)]
    for idx, pattern in ((2, STUTTER), (4, DYING)):
        for t, w in pattern:
            key("Lamp_Hall%02d" % idx, t, w)
            keys += 1

    # ---- the blast door: spins, then rolls aside ---------------------------------------------------
    # It was laid down along X in stage 1, so its rotation_euler already carries y = pi/2. Spinning
    # it means adding rotation about X - the wheel turning - and the roll is a translation after it.
    print("  blast door - the wheel turns, then it rolls aside")
    import math
    for t, ang, x in ((0.0, 0.0, 0.55), (9.0, 0.0, 0.55), (11.5, math.pi * 2.2, 0.55),
                      (12.0, math.pi * 2.2, 0.55), (14.5, math.pi * 2.2, -1.9)):
        key("Blast_Door", t, None, "BEZIER",
            rotation={"x": ang, "y": math.pi / 2.0, "z": 0.0},
            location={"x": x, "y": CY - 2.45, "z": 1.85})
        keys += 1

    # ---- hydroponics: the grow lamps breathe -------------------------------------------------------
    # BEZIER ON PURPOSE. These are supposed to ease - a grow light on a cycle, not a failing tube -
    # and using CONSTANT here would make the one deliberate smooth ramp in the scene stutter.
    print("  hydroponics - grow lamps on a slow cycle (BEZIER, deliberately)")
    grow = B4.ROOM_LIGHT["Hydroponics"][1]
    for name in ("Lamp_Hydroponics_0_0", "Lamp_Hydroponics_0_1",
                 "Lamp_Hydroponics_1_0", "Lamp_Hydroponics_1_1"):
        for t, f in ((0.0, 1.0), (7.0, 0.55), (14.0, 1.0), (21.0, 0.6), (28.0, 1.0)):
            key(name, t, grow * f, "BEZIER")
            keys += 1

    # ---- the power room fails first, and loudly ------------------------------------------------------
    print("  power plant - amber stutters, then goes")
    amber = B4.ROOM_LIGHT["Power"][1]
    for name in ("Lamp_Power_0_0", "Lamp_Power_0_1", "Lamp_Power_1_0", "Lamp_Power_1_1"):
        for t, w in ((0.0, amber), (20.0, 0.0), (20.12, amber), (20.4, 0.0), (20.55, amber),
                     (24.0, amber), (24.1, 0.0), (24.3, amber * 0.3), (24.7, 0.0), (25.2, amber)):
            key(name, t, w)
            keys += 1

    # ---- the cascade, then the blackout ----------------------------------------------------------------
    # Pendants drop one at a time from the far end back toward the door, which reads as something
    # travelling rather than as a switch being thrown.
    print("  cascade from the far end, then blackout at 30s")
    for i in range(B4.__dict__.get("n_pend", 7) if False else 7):
        drop = 26.0 + (6 - i) * 0.55
        key("Lamp_Hall%02d" % i, 25.5, HALL_W)
        key("Lamp_Hall%02d" % i, drop, 0.0)
        keys += 2
    for label in B4.ROOM_LIGHT:
        watts = B4.ROOM_LIGHT[label][1]
        r_n = 2 if label in ("Mess", "Power") else 3
        for i in range(r_n):
            for j in range(2):
                n = "Lamp_%s_%d_%d" % (label, i, j)
                key(n, 29.6, watts if label != "Power" else watts)
                key(n, 30.0, 0.0)
                keys += 2

    # ---- emergency lighting comes up ----------------------------------------------------------------
    # BEZIER, and it starts from the dim value stage 4 already gave them rather than from zero, so
    # the blackout is a two-second hole rather than a cut to a title card.
    print("  emergency lamps ramp up at 32s")
    for i in range(5):
        n = "Emg_Lamp%02d" % i
        key(n, 0.0, EMG_DIM, "BEZIER")
        key(n, 31.8, EMG_DIM, "BEZIER")
        key(n, 33.4, EMG_FULL, "BEZIER")
        key(n, DURATION_S, EMG_FULL, "BEZIER")
        keys += 4

    # ---- what actually landed ----------------------------------------------------------------------
    # COUNTED FROM BLENDER, not from the loop above. A keyframe the op refused still increments a
    # local counter, and "125 keys written" is the kind of claim this whole build exists to measure.
    total, curves = 0, 0
    for probe in ("Lamp_Hall02", "Lamp_Hall04", "Blast_Door", "Lamp_Hydroponics_0_0",
                  "Lamp_Power_0_0", "Emg_Lamp00"):
        lk = S.call("list_keyframes", {"object": probe})
        total += int(lk.get("keyframeTotal") or 0)
        curves += int(lk.get("curveCount") or 0)
        print("    %-22s %s curve(s), %s key(s)" % (probe, lk.get("curveCount"),
                                                   lk.get("keyframeTotal")))

    S.look((3.0, CY - 1.5, 1.7), (27.0, CY + 1.0, 1.8), lens=21.0)
    S.done("%d keys issued; %d curve(s) / %d key(s) confirmed on six sampled objects"
           % (keys, curves, total))


if __name__ == "__main__":
    main()
