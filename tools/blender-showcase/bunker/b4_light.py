"""Stage 4 - light. Where it stops looking like grey boxes and starts looking like a bunker.

WHAT THIS EARNS IN THE VIDEO: colour, and therefore rooms you can tell apart at a glance. Every
room so far is the same concrete under the same white lamp. After this the hydroponics bay is
purple, the power plant is amber, the medical bay is clinical, and a cut between two shots reads as
a cut between two places.

THE LAB SETTLED ITS NUMBERS BY MEASUREMENT AND SO DOES THIS. Its README records the whole argument:
world 0.030 with 420 W tubes gave "a lit room, not an abandoned one"; world 0.012 with 240 W gave
"dark WITHOUT highlights - flat"; 0.018 with 320 W in an 18 x 11 x 3.6 m room was the answer.
Contrast comes from making the practicals bright against a dark room, not from raising the ambient.

This hall is 34 x 14 x 7 m - roughly four times the floor area and twice the height - so the lab's
wattages do not transfer and the ratio does. `--measure` renders and prints the luminance histogram
so the numbers here can be argued with rather than believed.

EVERY FIXTURE IS TWO THINGS: a lamp, which lights the room, and a small emissive box, which is the
bulb you can see. A lamp with no visible source reads as light from nowhere; emissive geometry with
no lamp lights nothing. Both, or neither looks right.

EVERY PATTERN STARTS ON. Stage 5 makes them flicker, and the lab's hardest-won lighting note is that
a flicker keyed to 0 at frame 1 means the still frame anyone sees before pressing play is a dark
room with one tube lit. A flicker is a CHANGE from lit.

Run after b1 and b2. Safe to re-run: it deletes its own lamps first.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
sys.path.insert(0, HERE)
import stage as S       # noqa: E402
import b1_shell as B1   # noqa: E402
from b2_fixtures import Room  # noqa: E402

CY = B1.CY
HALL_LEN = B1.HALL_LEN

# THE RATIO, NOT THE LAB'S WATTAGE. Its 320 W lit an 18 x 11 room to mean 0.17; this hall is about
# four times the floor and twice the height, and light falls off with the square of distance from a
# point source hung twice as high. These are the starting point --measure was used to settle.
WORLD_STRENGTH = 0.020
HALL_PENDANT_W = 780.0
ROOM_LAMP_W = 320.0

# Colour is what makes the rooms readable, and each one is a real lamp somebody would install.
#   (room, rgb, watts, height)
ROOM_LIGHT = {
    "Armoury":     ((0.82, 0.86, 1.00), 300.0, 2.85),   # cold fluorescent
    "Medical":     ((0.90, 0.96, 1.00), 420.0, 2.85),   # clinical, the brightest room
    "Hydroponics": ((0.72, 0.32, 1.00), 260.0, 2.30),   # grow lamps, and the reason this room reads
    "Workshop":    ((1.00, 0.86, 0.62), 340.0, 2.85),   # warm work lamps
    "Mess":        ((1.00, 0.80, 0.52), 300.0, 2.85),   # domestic tungsten
    "Power":       ((1.00, 0.62, 0.24), 300.0, 2.85),   # amber, and it is the room that fails first
}


def lamp(name, kind, x, y, z, rgb, watts, radius=0.12):
    S.call("create_light", {"kind": kind, "name": name, "color": list(rgb), "energy": watts,
                            "radius": radius, "location": {"x": x, "y": y, "z": z}})
    return name


def bulb(name, x, y, z, rgb, strength=6.0, r=0.09):
    """The visible source. A lamp alone is light from nowhere."""
    S.call("create_primitive", {"kind": "uvsphere", "name": name, "radius": r,
                                "location": {"x": x, "y": y, "z": z}})
    m = "Emit_%s" % name
    S.call("create_material", {"name": m, "reuse": True, "baseColor": list(rgb) + [1.0],
                               "roughness": 1.0})
    S.call("set_material_properties", {"material": m, "emissive": list(rgb) + [1.0],
                                       "emissiveStrength": strength})
    S.paint(name, m)
    return name


def clear_previous():
    """Re-runnable. Deleting our own lamps first means stage 4 can be tuned without rebuilding 1-3.

    Deliberately NOT clear_scene: that is stage 1's job and calling it here would silently discard
    two stages of work whenever somebody re-ran the lighting to try a different colour.
    """
    objs = S.call("list_objects", {}).get("objects") or []
    # Measure_ IS IN THIS LIST BECAUSE IT LEAKED. --measure creates a camera every run and Blender
    # renames a clash rather than replacing it, so repeated tuning passes left Measure_Cam.001,
    # .002, .003 behind - the object count crept 301, 303, 304 while the scene looked identical.
    # Harmless to the render and exactly the kind of drift that makes a "same scene" comparison a
    # lie later on.
    doomed = [o["name"] for o in objs
              if o.get("name", "").startswith(("Lamp_", "Bulb_", "Emg_", "Peek_", "Measure_"))]
    # purgeOrphans IS THE WHOLE FIX, and leaving it off cost stage 5 outright.
    #
    # delete_object frees the OBJECT and leaves its light DATA-BLOCK behind with no users. The next
    # create_light asking for "Lamp_Hall02" then finds that name taken and gets Lamp_Hall02.001 -
    # so seven tuning re-runs of this stage produced Lamp_Hall02.007, and b5_anim, which keyframes
    # lamps BY NAME, failed with "no object named 'Lamp_Hall02'" against a scene that visibly had
    # seven hall pendants in it.
    #
    # Nothing reported a problem at any point. Every delete succeeded, every create succeeded, the
    # render looked identical, and the op census counted 44 lights. The names drifted silently and
    # the only symptom was a later stage failing to find something that was plainly there.
    #
    # ONE CALL WITH A LIST, not one per object - delete_object takes `objects`, and 96 round trips
    # for a re-run is most of this stage's runtime.
    if doomed:
        S.call("delete_object", {"objects": doomed, "purgeOrphans": True})
        print("  removed %d lamp(s)/bulb(s) from a previous run, orphan data purged with them"
              % len(doomed))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--measure", action="store_true",
                    help="render and print the luminance histogram, the lab's way of settling this")
    a = ap.parse_args()

    S.begin("STAGE 4 - light: colour per room, practicals bright against a dark hall")
    clear_previous()

    # A DARK WORLD - AND ON THIS BUILD THE KNOB IS DEAD. set_world accepts the colour and the
    # strength, returns ok:true, and world_info reports the result:
    #
    #   contributesLight: false
    #   blockers: ["the Background node exists but is NOT connected to the world output, so every
    #              value on it is inert - it accepts writes and changes no light."]
    #
    # useAsLight:true does not connect it either. So the two endpoints disagree - one claims the
    # write succeeded, the other says the write cannot matter - and the ambient in this scene is
    # zero however this line is tuned. Filed in FEATURE_PARITY_SPEC.
    #
    # It is still called, because when the connection is fixed these are the values that should
    # apply, and because the check below is what turns a silent nothing into a printed one.
    S.call("set_world", {"color": [0.030, 0.033, 0.042], "strength": WORLD_STRENGTH})
    w = S.call("world_info", {})
    if not w.get("contributesLight"):
        print("  !! THE WORLD IS INERT and set_world reported success anyway:")
        for b in (w.get("blockers") or ["(world_info gave no reason)"]):
            print("  !! %s" % b)
        print("  !! Every lamp below is doing all the lighting. Not fatal - the lab's own advice is")
        print("  !! that contrast comes from bright practicals rather than ambient - but the")
        print("  !! WORLD_STRENGTH constant in this file is currently a knob that turns nothing.")

    # ---- the hall: pendants down the centre line ------------------------------------------------
    print("  hall pendants")
    n_pend = 7
    for i in range(n_pend):
        x = 2.6 + i * (HALL_LEN - 5.2) / (n_pend - 1)
        lamp("Lamp_Hall%02d" % i, "POINT", x, CY, 4.55, (1.0, 0.90, 0.74), HALL_PENDANT_W, 0.18)
        bulb("Bulb_Hall%02d" % i, x, CY, 4.55, (1.0, 0.90, 0.74), 8.0, 0.10)
        # The drop rod, so the bulb hangs from the vault rather than floating in it. It has to
        # REACH the ceiling: at the hall's centre line the vault's inner surface is at z = the bore
        # radius, and a rod stopping short leaves the bulb hanging off a stub in mid-air, which is
        # visible in every wide shot and in none of the close ones.
        S.box("Bulb_Hall%02d_Rod" % i, x - 0.02, x + 0.02, CY - 0.02, CY + 0.02,
              4.64, B1.VAULT_R_IN)

    # ---- each room, in its own colour --------------------------------------------------------------
    for label, (rgb, watts, z) in ROOM_LIGHT.items():
        r = Room(label)
        print("  %-12s %s" % (label, "purple grow lamps" if label == "Hydroponics" else "practicals"))
        n = 2 if r.depth <= 6.5 else 3
        for i in range(n):
            y = r.m(1.4 + i * (r.depth - 2.6) / max(1, n - 1))
            for j, x in enumerate((r.x0 + 2.2, r.x1 - 2.2)):
                lamp("Lamp_%s_%d_%d" % (label, i, j), "POINT", x, y, z, rgb, watts, 0.14)
                bulb("Bulb_%s_%d_%d" % (label, i, j), x, y, z, rgb, 7.0, 0.075)

    # ---- emergency lamps, lit but dim, for the blackout beat in stage 7 ------------------------------
    # ON NOW, at a fraction of their power. Stage 7 raises them when the mains fail. Building them
    # dark and switching them on later is the shape the lab warns about: the still frame before
    # anyone presses play then shows a room with no emergency lighting at all, which reads as a
    # modelling omission rather than as a beat waiting to happen.
    print("  emergency lamps (on, dim - stage 7 raises them)")
    for i in range(5):
        x = 4.0 + i * (HALL_LEN - 8.0) / 4.0
        lamp("Emg_Lamp%02d" % i, "POINT", x, 2 * CY - 0.9, 3.5, (1.0, 0.16, 0.10), 22.0, 0.10)
        bulb("Emg_Bulb%02d" % i, x, 2 * CY - 0.95, 3.5, (1.0, 0.16, 0.10), 3.0, 0.075)

    S.look((3.2, CY - 1.4, 1.7), (26.0, CY + 1.2, 1.7), lens=22.0)
    S.done("world %.3f, %d hall pendants at %.0f W, six rooms in their own colour"
           % (WORLD_STRENGTH, n_pend, HALL_PENDANT_W))

    if a.measure:
        measure()


def measure(passes=5):
    """Render the same frame N times and report the luminance band, not a single number.

    THE REASON THIS AVERAGES, and it took three experiments to establish rather than assume.

    First the whole scene was rebuilt and measured three times: mean 0.205, 0.293, 0.130. That could
    have been the rebuild. So the same scene was rendered three times with nothing rebuilt at all:
    mean 0.2104, 0.1466, 0.1238, near-black 17.8%, 36.9%, 26.0%. The RENDER is what varies.

    render_info names the cause: engine BLENDER_EEVEE, samples on eevee.taa_render_samples. EEVEE
    accumulates temporally and does not land on the same image twice here. Cycles would be
    reproducible and this Blender does not have it - set_render_settings refuses the switch by name
    ("unknown render engine 'CYCLES' for this Blender. Valid: BLENDER_EEVEE. NOTHING was changed"),
    which is the guard doing its job and closing the obvious escape route.

    So a single render cannot settle a wattage. It swings +-40% and would have had me tuning against
    its own noise - which is worse than not measuring, because it looks like data and the lab's
    README is built on exactly this kind of table being trustworthy.

    What is reported instead is the BAND. A change to the lighting is only believable if it moves the
    band clear of where it was; anything inside the spread is noise wearing a decimal point.
    """
    import base64
    import io as _io
    # NOT INTO THE REPO. The first version wrote _measure.png next to the source, which is a
    # generated artefact in a version-controlled tools directory - the kind of thing that gets
    # committed by accident once and then lives there forever.
    out = os.path.join(os.environ.get("TEMP") or "/tmp", "mif_bunker_measure.png")
    S.call("create_camera", {"name": "Measure_Cam", "location": {"x": 3.2, "y": CY - 1.4, "z": 1.7},
                             "lookAt": {"x": 26.0, "y": CY + 1.2, "z": 1.7},
                             "lens": 22.0, "makeActive": True})
    S.call("set_render_settings", {"filmTransparent": False, "samples": 160, "useDenoising": True,
                                   "resolutionX": 800, "resolutionY": 450})
    try:
        from PIL import Image
    except ImportError:
        print("  (install Pillow to see the histogram)")
        return

    means, blacks = [], []
    for k in range(passes):
        r = S.call("render_still", {"filePath": out, "returnImage": True, "previewMaxPx": 800},
                   timeout=900.0)
        if not r.get("image"):
            print("  render %d produced no image - not counted" % k)
            continue
        _io.open(out, "wb").write(base64.b64decode(r["image"]))
        raw = Image.open(out).convert("RGB").tobytes()
        v = sorted((0.2126 * raw[i] + 0.7152 * raw[i + 1] + 0.0722 * raw[i + 2]) / 255.0
                   for i in range(0, len(raw), 3))
        means.append(sum(v) / len(v))
        blacks.append(sum(1 for x in v if x < 0.02) / float(len(v)))
    if not means:
        print("  nothing rendered - cannot measure")
        return

    avg = sum(means) / len(means)
    print("")
    print("  LUMINANCE over %d render(s). EEVEE does not repeat, so this is a BAND." % len(means))
    print("    mean        %.3f   (%.3f .. %.3f, spread %.3f)"
          % (avg, min(means), max(means), max(means) - min(means)))
    print("    near black  %.0f%%     (%.0f%% .. %.0f%%)"
          % (100 * sum(blacks) / len(blacks), 100 * min(blacks), 100 * max(blacks)))
    print("    the lab's answer was mean 0.17, near-black 14%, p90 0.41")
    # BANDS TAKEN FROM THE LAB'S OWN TABLE, not invented. It measured 0.28 mean / 9% near-black and
    # called it "a lit room, not an abandoned one"; 0.11 / 19% was "dark WITHOUT highlights - flat";
    # 0.17 / 14% was the answer. My first thresholds passed anything under 0.30, which would have
    # called the lab's own rejected setting a success.
    if max(means) - min(means) > 0.06:
        print("    -> the spread is wider than the difference worth tuning. Raise `passes`, or")
        print("       change the lighting by more than this band before believing the result.")
    print("    -> %s" % ("too dark - raise the practicals, not the world" if avg < 0.12 else
                         "a lit room, not an abandoned one - dim the practicals" if avg > 0.24 else
                         "pools of light against darkness, which is the target"))


if __name__ == "__main__":
    main()
