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
HALL_PENDANT_W = 210.0
ROOM_LAMP_W = 320.0

# Colour is what makes the rooms readable, and each one is a real lamp somebody would install.
#   (room, rgb, watts, height)
# THESE WENT UP 5x AND CAME STRAIGHT BACK DOWN, and the round trip is worth recording because the
# wattage was never the problem. Every lamp was sealed inside its own emissive bulb sphere - see
# LAMP_DROP below - so the rooms were black at 260 W and still black at 26000 W. Raising the power
# was treating a symptom; these figures are what actually lights the rooms once the lamps are
# outside their glass.
#
# The reason it went unnoticed for two stages is the more useful half: --measure only ever rendered
# ONE view, down the hall, and I read "mean 0.209, the target" as a statement about the bunker. It
# was a statement about the hall. Six rooms were tuned against nothing at all, and the three-view
# measurement below exists so that cannot happen again.
ROOM_LIGHT = {
    "Armoury":     ((0.82, 0.86, 1.00), 260.0, 2.85),   # cold fluorescent
    "Medical":     ((0.90, 0.96, 1.00), 70.0, 2.85),    # clinical - small room, pale surfaces, needs least
    "Hydroponics": ((0.72, 0.32, 1.00), 430.0, 2.30),   # purple carries less luminance per watt
    "Workshop":    ((1.00, 0.86, 0.62), 300.0, 2.85),   # warm work lamps
    "Mess":        ((1.00, 0.80, 0.52), 260.0, 2.85),   # domestic tungsten
    "Power":       ((1.00, 0.62, 0.24), 280.0, 2.85),   # amber, and it is the room that fails first
}


def vault_ceiling_at(y):
    """How high the vault's INNER surface is at this y. The hall is not a box.

    The vault is a half-cylinder of inner radius VAULT_R_IN centred on the hall's axis, so the
    ceiling drops to nothing at the walls: at y = 13.1, one metre from the springing line, it is
    2.39 m - not the 6.55 m it is on the centre line.

    This exists because the emergency lamps were placed at z = 3.5 on that line and were therefore
    INSIDE THE ROCK. They lit nothing, and raising them from 260 W to 2600 W moved the frame's mean
    from 0.0010 to 0.0015 - the same "ten times the power changes nothing" signature as a lamp
    sealed in its own bulb, and for the same underlying reason: the light was inside geometry.
    """
    dy = abs(y - CY)
    if dy >= B1.VAULT_R_IN:
        return 0.0
    return (B1.VAULT_R_IN ** 2 - dy ** 2) ** 0.5


def assert_inside_vault(name, x, y, z):
    """A lamp outside the vault is a lamp that does not light the hall. Refuse rather than dim."""
    ceil = vault_ceiling_at(y)
    if z >= ceil:
        raise RuntimeError(
            "%s would sit at z=%.2f where the vault's inner surface is only %.2f high (y=%.2f, "
            "%.2f m off the centre line). It would be embedded in the rock and light nothing - "
            "which looks exactly like a lamp that is merely too dim."
            % (name, z, ceil, y, abs(y - CY)))


def lamp(name, kind, x, y, z, rgb, watts, radius=0.12, size=None):
    """A lamp. `size` only means anything for AREA, and is passed at CREATION for a reason.

    set_light CANNOT convert a POINT to an AREA with a size in one call: it applies `size` to the
    light that is still a PointLight and dies with a raw AttributeError -
    "'PointLight' object has no attribute 'size'" - so the type never changes. Creating the light as
    the type it needs to be sidesteps an ordering bug that has no workaround from the caller's side.
    """
    p = {"kind": kind, "name": name, "color": list(rgb), "energy": watts,
         "location": {"x": x, "y": y, "z": z}}
    # radius IS POINT/SPOT ONLY and create_light refuses it on an AREA rather than ignoring it:
    # "radius (the soft-shadow size) only applies to a POINT or SPOT light and this one is AREA
    # (radius given). NOTHING was created." Refusing beats silently dropping the value, because a
    # dropped soft-shadow size looks like a lighting choice.
    if kind in ("POINT", "SPOT"):
        p["radius"] = radius
    if size is not None:
        p["size"] = size
    S.call("create_light", p)
    return name


# HOW FAR THE LAMP HANGS BELOW ITS BULB, and this constant exists because of a real defect.
#
# bulb() put an OPAQUE sphere at exactly the lamp's coordinates, so every lamp in this scene was
# sealed inside its own glass. Measured in the medical bay, same camera, same frame:
#
#     six 2000 W lamps, bulb spheres present   mean 0.0024
#     the same six lamps, spheres deleted      mean 0.8330
#
# 350x. It cost most of an evening because every intermediate symptom pointed elsewhere: the rooms
# were black at 1300 W and still black at 26000 W, which reads as a culling threshold; a 6000 W
# probe dropped into the same room lit it instantly, which reads as a wattage problem; and switching
# to AREA lamps changed nothing, which reads as a light-type problem. Every one of those was a
# consequence of the light being inside a box.
#
# The emissive sphere still has to be there - a lamp with no visible source is light from nowhere -
# so the LAMP moves down out of it instead.
LAMP_DROP = 0.16


def bulb(name, x, y, z, rgb, strength=6.0, r=0.09):
    """The visible source. A lamp alone is light from nowhere - and a lamp INSIDE this is nothing."""
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

    # A DARK WORLD. The lab's finding in one line: contrast comes from bright practicals against
    # darkness, not from raising the ambient.
    #
    # THIS COMMENT USED TO SAY THE KNOB WAS DEAD, and that was wrong. world_info reported
    # contributesLight false with a blocker claiming the Background node was not connected to the
    # world output - on a completely normal world where it demonstrably was. The addon compared a
    # NodeLink's .to_node with `is`, and bpy re-wraps non-ID sub-structs, so identity failed for the
    # same node. Fixed in the addon on 2026-09-05; WORLD_STRENGTH has always worked.
    #
    # The check below is KEPT, because a genuinely disconnected world is a real state and this is
    # the only place that would notice. It now stays quiet, which is what a check should do when
    # nothing is wrong.
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
        assert_inside_vault("Lamp_Hall%02d" % i, x, CY, 4.55)
        lamp("Lamp_Hall%02d" % i, "POINT", x, CY, 4.55 - LAMP_DROP, (1.0, 0.90, 0.74),
             HALL_PENDANT_W, 0.18)
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
                # AREA, NOT POINT, and it is not a stylistic choice. Six POINT lamps in this
                # room rendered mean 0.0048 at every wattage from 1300 to 26000 - identical to four
                # decimal places - and then 0.4448 at 30000. A 15% power increase producing a 93x
                # luminance jump, reproducibly, is a culling threshold rather than physics, and it
                # leaves no usable value between "black" and "blown out". An area lamp is also what
                # strip lighting in a room like this actually is.
                lamp("Lamp_%s_%d_%d" % (label, i, j), "AREA", x, y, z - LAMP_DROP, rgb,
                     watts, 0.14, size=1.7)
                bulb("Bulb_%s_%d_%d" % (label, i, j), x, y, z, rgb, 7.0, 0.075)

    # ---- emergency lamps, lit but dim, for the blackout beat in stage 7 ------------------------------
    # ON NOW, at a fraction of their power. Stage 7 raises them when the mains fail. Building them
    # dark and switching them on later is the shape the lab warns about: the still frame before
    # anyone presses play then shows a room with no emergency lighting at all, which reads as a
    # modelling omission rather than as a beat waiting to happen.
    print("  emergency lamps (on, dim - stage 7 raises them)")
    EMG_Y = 2 * CY - 2.2      # in from the wall, where the vault is still tall enough
    EMG_Z = 2.55
    for i in range(5):
        x = 4.0 + i * (HALL_LEN - 8.0) / 4.0
        assert_inside_vault("Emg_Lamp%02d" % i, x, EMG_Y, EMG_Z)
        lamp("Emg_Lamp%02d" % i, "POINT", x, EMG_Y, EMG_Z - LAMP_DROP, (1.0, 0.16, 0.10),
             22.0, 0.10)
        bulb("Emg_Bulb%02d" % i, x, EMG_Y, EMG_Z, (1.0, 0.16, 0.10), 3.0, 0.075)

    S.look((3.2, CY - 1.4, 1.7), (26.0, CY + 1.2, 1.7), lens=22.0)
    S.done("world %.3f, %d hall pendants at %.0f W, six rooms in their own colour"
           % (WORLD_STRENGTH, n_pend, HALL_PENDANT_W))

    if a.measure:
        measure()


def measure(passes=5):
    """Render the same frame N times and report the luminance band, not a single number.

    THE BAND IS KEPT, AND MY FIRST EXPLANATION FOR IT WAS WRONG. Worth recording both.

    Measured three times on the same unchanged scene, this reported mean 0.2104, 0.1466, 0.1238 with
    near-black at 17.8%, 36.9%, 26.0%, and I concluded that EEVEE does not repeat - render_info
    does say engine BLENDER_EEVEE with samples on eevee.taa_render_samples, which made a temporal
    accumulation story fit.

    IT WAS NOT THE ENGINE. Those frames were nearly black, because every lamp was sealed inside its
    own bulb sphere (see LAMP_DROP). The mean of a crushed frame is dominated by noise in values the
    view transform has already flattened, so it wanders. With the lamps outside their glass the same
    five renders now come back at 0.265, 0.265, 0.265, 0.265, 0.265 - identical to three decimals.

    So the instability was a symptom of the defect, not a property of the renderer, and "EEVEE is
    unreliable" would have been a permanent piece of folklore in this file explaining away a bug.

    The band stays anyway. It costs four extra renders, it would have caught this sooner rather than
    later, and a measurement that reports its own spread is one you can argue with.
    """
    import base64
    import io as _io
    # NOT INTO THE REPO. The first version wrote _measure.png next to the source, which is a
    # generated artefact in a version-controlled tools directory - the kind of thing that gets
    # committed by accident once and then lives there forever.
    out = os.path.join(os.environ.get("TEMP") or "/tmp", "mif_bunker_measure.png")
    # MORE THAN ONE VIEW, because one view measured one room. See the wattage comment above: the
    # hall reported "mean 0.209, the target" while six rooms rendered black, and a yardstick that
    # only looks down the corridor cannot say anything about the rooms off it.
    VIEWS = [("hall", (3.2, CY - 1.4, 1.7), (26.0, CY + 1.2, 1.7), 22.0),
             ("hydroponics", (20.0, -0.9, 1.55), (20.0, -5.2, 1.15), 28.0),
             ("medical", (7.0, 2 * CY + 1.0, 1.6), (7.0, 2 * CY + 6.0, 1.2), 28.0)]
    S.call("set_render_settings", {"filmTransparent": False, "samples": 160, "useDenoising": True,
                                   "resolutionX": 800, "resolutionY": 450})
    try:
        from PIL import Image
    except ImportError:
        print("  (install Pillow to see the histogram)")
        return

    print("")
    print("  LUMINANCE per view, %d render(s) each, reported as a band." % passes)
    print("  (the band is near-zero now. It was NOT when the lamps were trapped in their bulbs -")
    print("   a crushed frame's mean wanders, which I first mistook for the renderer.)")
    print("  the lab's answer was mean 0.17, near-black 14%")
    worst = None
    for label, eye, tgt, lens in VIEWS:
        S.call("create_camera", {"name": "Measure_Cam", "location": {"x": eye[0], "y": eye[1],
                                                                    "z": eye[2]},
                                 "lookAt": {"x": tgt[0], "y": tgt[1], "z": tgt[2]},
                                 "lens": lens, "makeActive": True})
        means, blacks = [], []
        for k in range(passes):
            r = S.call("render_still", {"filePath": out, "returnImage": True, "previewMaxPx": 800},
                       timeout=900.0)
            if not r.get("image"):
                continue
            _io.open(out, "wb").write(base64.b64decode(r["image"]))
            raw = Image.open(out).convert("RGB").tobytes()
            v = sorted((0.2126 * raw[i] + 0.7152 * raw[i + 1] + 0.0722 * raw[i + 2]) / 255.0
                       for i in range(0, len(raw), 3))
            means.append(sum(v) / len(v))
            blacks.append(sum(1 for x in v if x < 0.02) / float(len(v)))
        S.call("delete_object", {"object": "Measure_Cam", "purgeOrphans": True})
        if not means:
            print("    %-12s nothing rendered" % label)
            continue
        avg = sum(means) / len(means)
        verdict = ("TOO DARK" if avg < 0.12 else
                   "washed out" if avg > 0.24 else "on target")
        print("    %-12s mean %.3f  (%.3f..%.3f)  near-black %2.0f%%   %s"
              % (label, avg, min(means), max(means), 100 * sum(blacks) / len(blacks), verdict))
        if worst is None or avg < worst[1]:
            worst = (label, avg)
    if worst:
        print("    -> darkest view is %s at %.3f" % worst)
    return
    # BANDS TAKEN FROM THE LAB'S OWN TABLE, not invented. It measured 0.28 mean / 9% near-black and
    # called it "a lit room, not an abandoned one"; 0.11 / 19% was "dark WITHOUT highlights - flat";
    # 0.17 / 14% was the answer. My first thresholds passed anything under 0.30, which would have
    # called the lab's own rejected setting a success.

if __name__ == "__main__":
    main()
