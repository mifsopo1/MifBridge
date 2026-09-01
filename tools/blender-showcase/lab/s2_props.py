"""STAGE 2 - the lab itself: benches, shelving, drums, glassware, a computer, services.

Built from reusable create_* functions, as the benchmark asks. Each returns the objects it made so
the count at the end is measured rather than claimed, and every part stays individually named and
selectable - which is the thing a merged mesh would throw away.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, box, cyl, cut, paint, mat, look


def materials():
    mat("Wood_Worn", (0.24, 0.16, 0.09), roughness=0.85)
    mat("Glass_Lab", (0.70, 0.80, 0.76), roughness=0.06)
    mat("Plastic_Dark", (0.10, 0.10, 0.11), roughness=0.45)
    mat("Plastic_White", (0.62, 0.61, 0.56), roughness=0.55)
    mat("Monitor_Glow", (0.16, 0.52, 0.40), roughness=0.25)
    mat("Chem_Amber", (0.42, 0.24, 0.05), roughness=0.25)
    mat("Paper_Old", (0.55, 0.52, 0.45), roughness=0.9)


def create_workbench(name, x0, x1, y0, y1, ztop=0.92):
    made = [box("%s_Top" % name, x0, x1, y0, y1, ztop - 0.07, ztop)]
    paint(made[0], "Wood_Worn")
    for i, (lx, ly) in enumerate(((x0 + .09, y0 + .09), (x1 - .09, y0 + .09),
                                  (x0 + .09, y1 - .09), (x1 - .09, y1 - .09))):
        leg = box("%s_Leg%d" % (name, i + 1), lx - .04, lx + .04, ly - .04, ly + .04, 0, ztop - .07)
        paint(leg, "Painted_Metal")
        made.append(leg)
    sh = box("%s_Shelf" % name, x0 + .12, x1 - .12, y0 + .07, y1 - .07, 0.24, 0.28)
    paint(sh, "Painted_Metal")
    made.append(sh)
    return made


def create_shelf(name, x0, x1, y0, y1, levels=4, top=2.2):
    made = []
    for i in range(levels):
        z = 0.34 + i * (top - 0.34) / max(1, levels - 1)
        s = box("%s_L%d" % (name, i + 1), x0, x1, y0, y1, z, z + 0.04)
        paint(s, "Rusted_Metal")
        made.append(s)
    for i, (lx, ly) in enumerate(((x0 + .04, y0 + .04), (x1 - .04, y0 + .04),
                                  (x0 + .04, y1 - .04), (x1 - .04, y1 - .04))):
        u = box("%s_Post%d" % (name, i + 1), lx - .035, lx + .035, ly - .035, ly + .035, 0, top + .06)
        paint(u, "Rusted_Metal")
        made.append(u)
    return made


def create_barrel(name, x, y, z0=0.0, r=0.30, h=0.90, colour="Rusted_Metal"):
    made = [cyl(name, x, y, z0, z0 + h, r, 20)]
    paint(made[0], colour)
    for i, f in enumerate((0.24, 0.74)):
        rib = cyl("%s_Rib%d" % (name, i + 1), x, y, z0 + h * f - .03, z0 + h * f + .03, r * 1.07, 20)
        paint(rib, "Painted_Metal")
        made.append(rib)
    return made


def create_jug(name, x, y, z0=0.0, r=0.13, h=0.34):
    made = [cyl(name, x, y, z0, z0 + h, r, 12)]
    paint(made[0], "Plastic_White")
    cap = cyl("%s_Cap" % name, x, y, z0 + h, z0 + h + 0.05, r * 0.42, 10)
    paint(cap, "Plastic_Dark")
    made.append(cap)
    return made


def create_flask(name, x, y, z0, r=0.05, h=0.24, filled=True):
    made = [cyl(name, x, y, z0, z0 + h * 0.72, r, 12)]
    paint(made[0], "Glass_Lab")
    neck = cyl("%s_Neck" % name, x, y, z0 + h * 0.72, z0 + h, r * 0.40, 10)
    paint(neck, "Glass_Lab")
    made.append(neck)
    if filled:
        liq = cyl("%s_Liquid" % name, x, y, z0 + 0.012, z0 + h * 0.40, r * 0.88, 12)
        paint(liq, "Chem_Amber")
        made.append(liq)
    return made


def create_pipe(name, x, y0, y1, z, r=0.10):
    p = cyl(name, x, (y0 + y1) / 2.0, (y0 + y1) / 2.0 - (y1 - y0) / 2.0,
            (y0 + y1) / 2.0 + (y1 - y0) / 2.0, r, 14, axis="y")
    call("transform_object", {"object": p, "location": {"x": x, "y": (y0 + y1) / 2.0, "z": z}})
    paint(p, "Rusted_Metal")
    made = [p]
    for i, yy in enumerate((y0 + 0.5, y1 - 0.5)):
        f = cyl("%s_Flange%d" % (name, i + 1), x, yy, z - .05, z + .05, r * 1.5, 14, axis="y")
        call("transform_object", {"object": f, "location": {"x": x, "y": yy, "z": z}})
        paint(f, "Painted_Metal")
        made.append(f)
    return made


def create_computer(name, x, y, z0):
    made = []
    t = box("%s_Tower" % name, x - .20, x + .20, y - .24, y + .24, z0, z0 + 0.44)
    paint(t, "Plastic_Dark")
    made.append(t)
    m = box("%s_Screen" % name, x - .28, x + .28, y - .03, y + .03, z0 + .52, z0 + .92)
    paint(m, "Monitor_Glow")
    made.append(m)
    s = box("%s_Stand" % name, x - .06, x + .06, y - .11, y + .11, z0 + .44, z0 + .52)
    paint(s, "Plastic_Dark")
    made.append(s)
    kb = box("%s_Keyboard" % name, x - .22, x + .22, y + .10, y + .28, z0, z0 + 0.025)
    paint(kb, "Plastic_Dark")
    made.append(kb)
    return made


def create_fan(name, x, y, z, r=0.36):
    import math
    ring = cyl("%s_Ring" % name, x, y, z - .07, z + .07, r, 24, axis="y")
    call("transform_object", {"object": ring, "location": {"x": x, "y": y, "z": z}})
    paint(ring, "Rusted_Metal")
    hole = cyl("_fanhole", x, y, z - .12, z + .12, r * 0.85, 24, axis="y")
    call("transform_object", {"object": hole, "location": {"x": x, "y": y, "z": z}})
    call("boolean_op", {"target": ring, "cutter": hole, "operation": "difference",
                        "deleteCutter": True})
    hub = cyl("%s_Hub" % name, x, y, z - .05, z + .05, r * .15, 12, axis="y")
    call("transform_object", {"object": hub, "location": {"x": x, "y": y, "z": z}})
    paint(hub, "Painted_Metal")
    blades = []
    for i in range(4):
        bl = box("%s_Blade%d" % (name, i + 1), x - r * .78, x + r * .78, y - .012, y + .012,
                 z - .10, z + .10)
        call("transform_object", {"object": bl, "rotation": {"x": 0.0, "y": i * math.pi / 4.0,
                                                             "z": 0.0}})
        call("transform_object", {"object": bl, "location": {"x": x, "y": y, "z": z}})
        paint(bl, "Painted_Metal")
        blades.append(bl)
    return [ring, hub] + blades


def build():
    begin("STAGE 2  the lab - benches, shelving, drums, glassware, computer, services")
    materials()
    made = 0

    look((6.60, 3.40, 1.50), (2.00, 1.20, 0.95))
    made += len(create_workbench("Bench_Main", 1.0, 6.4, 0.9, 1.9))
    made += len(create_workbench("Bench_Back", 9.6, 14.8, 9.0, 9.9))
    look((3.20, 4.60, 1.50), (0.80, 6.40, 1.30))
    made += len(create_shelf("Shelf_West", 0.55, 1.05, 3.6, 7.4, levels=4, top=2.3))
    made += len(create_shelf("Shelf_East", 16.6, 17.1, 3.0, 6.4, levels=3, top=2.0))
    look((14.60, 6.60, 1.50), (12.40, 9.40, 1.20))
    made += len(create_computer("Computer", 12.4, 9.4, 0.92))

    look((12.40, 5.00, 1.60), (8.00, 2.20, 0.80))
    for i, (x, y, c) in enumerate(((7.6, 2.0, "Rusted_Metal"), (8.5, 2.4, "Painted_Metal"),
                                   (16.0, 1.6, "Rusted_Metal"), (15.2, 8.8, "Rusted_Metal"),
                                   (2.4, 9.4, "Painted_Metal"))):
        made += len(create_barrel("Drum_%02d" % (i + 1), x, y, colour=c))
    for i, (x, y) in enumerate(((9.4, 1.2), (9.9, 1.5), (10.4, 1.2), (6.9, 8.4), (7.4, 8.7),
                                (3.2, 7.9), (3.7, 8.2))):
        made += len(create_jug("Jug_%02d" % (i + 1), x, y))
    look((4.40, 2.60, 1.15), (2.40, 1.20, 1.00))
    for i, x in enumerate((1.6, 2.0, 2.4, 2.9, 3.4, 3.9)):
        made += len(create_flask("Flask_%02d" % (i + 1), x, 1.15 + (i % 2) * 0.30, 0.92,
                                 r=.045 + .012 * (i % 3), h=.20 + .06 * (i % 3),
                                 filled=(i % 3 != 1)))
    look((3.00, 6.00, 1.90), (12.00, 5.50, 3.10))
    for i, (x, z) in enumerate(((3.0, 3.20), (3.0, 2.92), (14.0, 3.20))):
        made += len(create_pipe("Pipe_%02d" % (i + 1), x, 0.4, 10.6, z, r=.10 if i != 1 else .06))
    made += len(create_fan("Vent_Fan", 17.0, 7.6, 2.6))

    # scattered paperwork and rubbish, on surfaces and on the floor
    for i, (x, y, z) in enumerate(((4.6, 1.4, 0.92), (5.2, 1.1, 0.92), (8.2, 5.4, 0.004),
                                   (11.6, 3.2, 0.004), (6.0, 7.2, 0.004), (13.4, 6.6, 0.004))):
        p = box("Paper_%02d" % (i + 1), x - .11, x + .11, y - .15, y + .15, z, z + 0.002)
        paint(p, "Paper_Old")
        made += 1
    for i, (x, y) in enumerate(((10.8, 7.6), (5.4, 4.2), (15.4, 4.6), (2.2, 5.8))):
        t = box("Debris_%02d" % (i + 1), x - .14, x + .14, y - .10, y + .10, 0, 0.16)
        paint(t, "Grime")
        made += 1

    done("%d prop objects, every one individually named and selectable" % made)


if __name__ == "__main__":
    build()
