"""STAGE 4 - lighting: fluorescents, the window shaft, the monitor glow, and a near-black world.

THE WORLD STRENGTH IS THE WHOLE LOOK. The reference images are almost entirely dark with small
pools of light, and the usual mistake is a world strength around 1.0, which is roughly overcast
daylight and turns an abandoned basement into a lit room with props in it. 0.015 here - the world
exists so the corners are not pure black, and nothing more.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, box, cyl, paint, mat, look

# x, y-centre. THE BEAMS RUN ALONG X AT y = 2.0, 5.6 and 9.2, each 0.32 deep and occupying
# z 3.26..3.60. The first version put the fittings at z 3.44..3.56 spanning y 4.6..6.6, which is
# INSIDE the middle beam in both axes - Andre spotted it on screen as light bars buried in the
# steel. They now hang in the BAYS between beams, below them, on short drop rods.
# FIVE fixtures, not three - three left the room lit in isolated pools with black between them.
FLUOROS = [("Fluoro_01", 3.4, 3.8, 320.0), ("Fluoro_02", 8.2, 7.4, 320.0),
           ("Fluoro_03", 13.0, 3.8, 300.0), ("Fluoro_04", 16.0, 7.4, 280.0),
           ("Fluoro_05", 6.0, 9.6, 280.0)]
FIT_Z0, FIT_Z1 = 3.04, 3.16          # under the beams, which stop at 3.26
DROP_TOP = 3.60


def build():
    begin("STAGE 4  lighting - fluorescents, window shaft, monitor glow, near-black world")
    # RENDERED FIRST, so the lighting is visible AS IT IS CREATED rather than after the fact.
    # SOLID shading ignores lamps entirely, which is why the first run of this stage looked like
    # nothing was happening - three fixtures and a window shaft were built correctly into a grey
    # viewport. MATERIAL is not enough either: it uses a studio light and ignores scene lamps.
    call("set_viewport_shading", {"shading": "RENDERED", "useSceneLights": True,
                                  "useSceneWorld": True})
    mat("Tube_Glow", (0.88, 0.94, 1.00), roughness=0.3)

    # Near-black, cold. See the header on strength.
    # SETTLED BY MEASUREMENT, not by eye. Three renders at 480x270, each with a luminance
    # histogram off the resulting PNG:
    #   world .030 / 420 W -> mean .28,  9% near black  - a lit room, not an abandoned one
    #   world .018 / 300 W -> mean .17, 14% near black, p90 .41   <- this
    #   world .012 / 240 W -> mean .11, 19% near black, p90 .27   - dark WITHOUT highlights
    # The first attempt was world .015 with 60 W tubes, which measured 95% near black: Andre's
    # screenshot was almost entirely empty and he was right to call it. Contrast comes from the
    # practicals being bright against a dark room, not from raising the ambient.
    call("set_world", {"color": [0.05, 0.06, 0.08], "strength": 0.018})
    call("set_render_settings", {"exposure": -0.3})

    look((16.20, 2.20, 1.70), (5.00, 6.00, 2.40))
    made = 0
    for name, x, yc, watts in FLUOROS:
        y0, y1 = yc - 0.9, yc + 0.9
        housing = box("%s_Housing" % name, x - 0.18, x + 0.18, y0, y1, FIT_Z0, FIT_Z1)
        paint(housing, "Painted_Metal")
        for i, dy in enumerate((y0 + 0.18, y1 - 0.18)):
            rod = box("%s_Rod%d" % (name, i + 1), x - 0.02, x + 0.02, dy - 0.02, dy + 0.02,
                      FIT_Z1, DROP_TOP)
            paint(rod, "Rusted_Metal")
        tube = cyl("%s_Tube" % name, x, yc, y0 + 0.06, y1 - 0.06, 0.035, 12, axis="y")
        call("transform_object", {"object": tube, "location": {"x": x, "y": yc,
                                                               "z": FIT_Z0 - 0.02}})
        paint(tube, "Tube_Glow")
        # The emitter, just under the tube. A narrow AREA light reads like a strip; a POINT light
        # in the same place reads like a bulb, which is the giveaway that it was faked.
        call("create_light", {"name": name, "type": "AREA", "shape": "RECTANGLE",
                              "size": 0.12, "sizeY": 1.7,
                              "location": {"x": x, "y": yc, "z": FIT_Z0 - 0.05},
                              "energy": watts, "color": [0.82, 0.90, 1.0]})
        made += 5

    look((7.00, 3.20, 1.70), (12.70, 10.40, 2.40))
    # The window shaft - the brightest thing in every reference frame, and cold against the
    # fluorescents so the two read as different sources.
    call("create_light", {"name": "Window_Shaft", "type": "AREA", "shape": "RECTANGLE",
                          "size": 3.4, "sizeY": 1.2,
                          "location": {"x": 12.7, "y": 10.75, "z": 2.5},
                          "rotation": {"x": 1.5708, "y": 0.0, "z": 0.0},
                          "energy": 4200.0, "color": [0.62, 0.74, 1.0]})

    look((2.20, 8.60, 1.60), (12.00, 4.00, 1.40))
    # Small practicals: the monitor, and a low amber bounce off the chemistry bench.
    call("create_light", {"name": "Monitor_Light", "type": "AREA", "size": 0.5,
                          "location": {"x": 12.4, "y": 9.1, "z": 1.42},
                          "rotation": {"x": 1.2, "y": 0.0, "z": 3.1416},
                          "energy": 110.0, "color": [0.30, 0.95, 0.72]})
    call("create_light", {"name": "Bench_Practical", "type": "POINT", "radius": 0.25,
                          "location": {"x": 2.6, "y": 1.4, "z": 1.9},
                          "energy": 260.0, "color": [1.0, 0.74, 0.42]})

    done("%d fixtures + window shaft + 2 practicals, world 0.018, exposure -0.3" % made)


if __name__ == "__main__":
    build()
