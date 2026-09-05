"""Stage 3 - density. Hundreds of props, scattered rather than placed.

WHAT THIS EARNS IN THE VIDEO: the bunker stops looking like a showroom. Stage 2 put the furniture a
viewer names each room by; this is the litter, the bottles, the crates and the cable that make it
look inhabited.

SCATTERED, AND THE LAB'S STAGE 2b SAYS WHY BETTER THAN THIS CAN. Hand-placing 400 props proves the
bridge can call create_primitive 400 times. Scattering them proves it drives Blender's INSTANCING
system - and it means "how full is that shelf" is one number on a modifier rather than a rewritten
loop. Change your mind about the mess and you change a count.

EVERY SYSTEM IS READ BACK. list_particles reports `rendersNothing`, which is the field that catches
the silent case: a particle system with no instance object, or a count of zero, is a modifier that
exists, reports success, and puts nothing on screen. This raises instead.

THE SOURCE PROPS LIVE OFF THE SET. They are real objects that must exist to be instanced, and they
are parked well outside the bunker so they are never in shot. They are not hidden - a hidden emitter
source is one more thing to get wrong, and a camera that never points there does not need one.

Run after b2_fixtures.py.
"""
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
PARK = 80.0   # where the instance sources live, well outside the bunker


def sources():
    """The props that get instanced. Built once, parked off the set, never in shot."""
    steel = S.mat("Bunker_Steel", (0.26, 0.27, 0.29, 1.0), 0.85, 0.45)
    glass = S.mat("Prop_Glass", (0.16, 0.26, 0.19, 1.0), 0.0, 0.18)
    card = S.mat("Prop_Card", (0.30, 0.22, 0.13, 1.0), 0.0, 0.88)
    paper = S.mat("Prop_Paper", (0.62, 0.60, 0.55, 1.0), 0.0, 0.95)
    green = S.mat("Prop_Foliage", (0.10, 0.32, 0.09, 1.0), 0.0, 0.72)

    S.call("create_primitive", {"kind": "cylinder", "name": "Src_Bottle", "radius": 0.038,
                                "depth": 0.26, "vertices": 10,
                                "location": {"x": PARK, "y": 0, "z": 0}})
    S.paint("Src_Bottle", glass)

    S.call("create_primitive", {"kind": "cube", "name": "Src_Box", "size": 0.22,
                                "location": {"x": PARK + 1, "y": 0, "z": 0}})
    S.paint("Src_Box", card)

    S.call("create_primitive", {"kind": "cylinder", "name": "Src_Can", "radius": 0.033,
                                "depth": 0.12, "vertices": 8,
                                "location": {"x": PARK + 2, "y": 0, "z": 0}})
    S.paint("Src_Can", steel)

    S.call("create_primitive", {"kind": "cube", "name": "Src_Paper", "size": 0.20,
                                "location": {"x": PARK + 3, "y": 0, "z": 0}})
    S.call("transform_object", {"object": "Src_Paper", "scale": {"x": 1.0, "y": 1.4, "z": 0.02}})
    S.call("apply_transform", {"object": "Src_Paper", "scale": True,
                               "location": False, "rotation": False})
    S.paint("Src_Paper", paper)

    S.call("create_primitive", {"kind": "icosphere", "name": "Src_Plant", "radius": 0.13,
                                "subdivisions": 1, "location": {"x": PARK + 4, "y": 0, "z": 0}})
    S.paint("Src_Plant", green)
    return ("Src_Bottle", "Src_Box", "Src_Can", "Src_Paper", "Src_Plant")


def scatter(name, x0, x1, y0, y1, z, src, count, size, seed):
    """A thin plate at height z, with `count` instances of `src` scattered over it.

    The plate is a SEPARATE object from the furniture underneath on purpose - the lab's reason, and
    it is a good one: the shelf can still be selected, moved and re-materialled without dragging its
    clutter along.
    """
    plate = S.box(name, x0, x1, y0, y1, z, z + 0.004)
    S.call("add_particles", {
        "object": plate, "type": "HAIR", "count": count, "seed": seed,
        "emitFrom": "FACE", "distribution": "RAND",
        "hairLength": size, "renderType": "OBJECT", "instanceObject": src,
        "size": 1.0, "sizeRandom": 0.5, "showEmitter": False,
    })
    # READ IT BACK. rendersNothing is the field that catches a system that exists, reports success
    # and puts nothing on screen - which is indistinguishable from a tidy room.
    row = ((S.call("list_particles", {"object": plate}).get("systems") or [{}]))[0]
    if row.get("rendersNothing"):
        raise RuntimeError("%s would render nothing: %r" % (name, row))
    return int(row.get("count") or 0)


def main():
    S.begin("STAGE 3 - density: hundreds of props, scattered rather than placed")

    bottle, boxsrc, can, paper, plant = sources()
    total, systems = 0, 0

    # ---- the armoury shelves, and the floor under them --------------------------------------------
    r = Room("Armoury")
    for i in range(3):
        x0 = r.x0 + 0.45 + i * 2.7
        for t, z in enumerate((0.35, 0.97, 1.60, 2.22)):
            total += scatter("Clut_Arm%d_%d" % (i, t), x0, x0 + 2.1,
                             r.m(r.depth - 0.75), r.m(r.depth - 0.25), z + 0.05,
                             boxsrc, 7, 0.10, 1100 + i * 10 + t)
            systems += 1
    total += scatter("Clut_ArmFloor", r.x0 + 0.4, r.x1 - 0.4, r.m(0.9), r.m(r.depth - 1.6),
                     0.02, can, 40, 0.06, 1200)
    systems += 1
    print("  armoury: shelves and floor")

    # ---- hydroponics: the trays are FULL, which is the point of the room ----------------------------
    r = Room("Hydroponics")
    for row in range(2):
        y = r.m(2.0 + row * 2.9)
        for tier in range(3):
            z = 0.55 + tier * 0.78 + 0.16
            total += scatter("Clut_Hydro%d_%d" % (row, tier), r.x0 + 0.55, r.x1 - 0.55,
                             y - 0.58, y + 0.58, z, plant, 34, 0.16, 1300 + row * 10 + tier)
            systems += 1
    print("  hydroponics: six trays of planting")

    # ---- the mess: bottles and cans on the tables ----------------------------------------------------
    r = Room("Mess")
    for i in range(2):
        y = r.m(1.7 + i * 2.2)
        total += scatter("Clut_Mess%d" % i, r.x0 + 0.9, r.x1 - 0.9, y - 0.42, y + 0.42,
                         0.84, bottle, 16, 0.13, 1400 + i)
        systems += 1
    print("  mess: bottles on the tables")

    # ---- the workshop bench, and paper everywhere -----------------------------------------------------
    r = Room("Workshop")
    total += scatter("Clut_WorkBench", r.x0 + 0.6, r.x0 + 3.9, r.m(r.depth - 1.3),
                     r.m(r.depth - 0.7), 1.00, can, 18, 0.08, 1500)
    total += scatter("Clut_WorkFloor", r.x0 + 0.5, r.x1 - 0.5, r.m(1.0), r.m(r.depth - 2.0),
                     0.02, paper, 46, 0.05, 1501)
    systems += 2
    print("  workshop: bench clutter and paper on the floor")

    # ---- the hall floor: litter down its whole length ---------------------------------------------------
    total += scatter("Clut_HallFloor", 2.0, HALL_LEN - 2.0, CY - 5.2, CY + 3.4, 0.02,
                     paper, 120, 0.05, 1600)
    total += scatter("Clut_HallCans", 3.0, HALL_LEN - 3.0, CY - 4.4, CY + 2.6, 0.02,
                     can, 70, 0.06, 1601)
    systems += 2
    print("  hall: litter down its whole length")

    S.look((4.0, CY - 2.0, 1.55), (26.0, CY + 1.0, 1.2), lens=24.0)
    S.done("%d instances across %d particle system(s), every one read back and none rendering nothing"
           % (total, systems))


if __name__ == "__main__":
    main()
