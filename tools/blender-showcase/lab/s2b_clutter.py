"""STAGE 2b - density. The reference frames are FULL, and 101 hand-placed props are not.

WHY THIS IS SCATTERED AND NOT PLACED. The honest gap after stage 2 was density: the reference
photographs have bottles filling every shelf, jugs banked along the walls, litter across the whole
floor. Reaching that by hand means several hundred more create_primitive calls and a script nobody
can adjust afterwards - change your mind about how full a shelf is and you rewrite a loop.

Scattering is both denser and more useful: each surface gets a HAIR particle system instancing a
real object, so the count is a NUMBER on the modifier. Ten bottles or eighty is one field, and the
distribution stays plausible because it follows the surface rather than a list of coordinates
somebody typed.

It is also the honest demonstration. Hand-placing 400 props proves the bridge can call
create_primitive 400 times; scattering them proves it can drive Blender's instancing system, which
is the thing that was missing before 0.8.0.

THE TRAP THIS STAGE EXISTS TO AVOID, and add_particles refuses it outright: renderType OBJECT with
no instanceObject renders NOTHING and Blender reports no error at all. Every scatter here names its
instance object, and list_particles is read back afterwards so a system that would render nothing
is visible in the output rather than discovered in a render.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, box, cyl, paint, mat, look

# name, surface span, what to scatter, how many, hair length (scale of the instance)
SCATTERS = [
    # the shelving - bottles and jugs, packed
    ("Clutter_ShelfW", (0.58, 1.02, 3.7, 7.3), "Bottle_Src", 46, 0.10),
    ("Clutter_ShelfE", (16.63, 17.07, 3.1, 6.3), "Jug_Src", 26, 0.13),
    # bench tops - glassware and small kit
    ("Clutter_BenchMain", (1.1, 6.3, 1.0, 1.85), "Bottle_Src", 34, 0.09),
    ("Clutter_BenchBack", (9.7, 14.7, 9.05, 9.85), "Jug_Src", 18, 0.12),
    # the floor - litter, everywhere, which is what makes a room read as abandoned
    ("Clutter_FloorA", (0.5, 8.8, 0.5, 10.4), "Litter_Src", 120, 0.06),
    ("Clutter_FloorB", (9.0, 17.4, 0.5, 10.4), "Litter_Src", 110, 0.06),
]


def sources():
    """The instanced objects, parked outside the room.

    NOT DELETED after use - ObjectInfo and the particle system both need the object to exist, and a
    deleted instance source scatters nothing while reporting success. Parking them at negative
    coordinates keeps them out of every camera shot without removing them.
    """
    mat("Bottle_Glass", (0.40, 0.52, 0.44), roughness=0.15)
    mat("Jug_Plastic", (0.60, 0.59, 0.54), roughness=0.55)
    mat("Litter", (0.30, 0.28, 0.24), roughness=0.92)

    b = cyl("Bottle_Src", -8.0, -8.0, 0.0, 0.22, 0.035, 10)
    paint(b, "Bottle_Glass")
    neck = cyl("Bottle_Src_Neck", -8.0, -8.0, 0.22, 0.28, 0.014, 8)
    paint(neck, "Bottle_Glass")
    call("join_objects", {"target": "Bottle_Src", "objects": ["Bottle_Src_Neck"]})

    j = cyl("Jug_Src", -8.6, -8.0, 0.0, 0.30, 0.10, 10)
    paint(j, "Jug_Plastic")

    l = box("Litter_Src", -9.2, -9.06, -8.06, -7.94, 0.0, 0.012)
    paint(l, "Litter")
    return ["Bottle_Src", "Jug_Src", "Litter_Src"]


def build():
    begin("STAGE 2b  density - scattered clutter, driven by a count rather than a coordinate list")
    look((12.0, 2.4, 1.6), (3.0, 6.0, 1.0))
    srcs = sources()

    total, systems = 0, 0
    for name, (x0, x1, y0, y1), src, count, size in SCATTERS:
        # A thin plate to scatter across. Separate from the shelf or bench itself so the furniture
        # can still be selected, moved and re-materialled without dragging its clutter along.
        z = 0.0
        surf = box(name, x0, x1, y0, y1, z + 0.001, z + 0.003)
        paint(surf, "Litter")
        call("add_particles", {
            "object": surf, "type": "HAIR", "count": count,
            "seed": (hash(name) & 0xFFFF),
            "emitFrom": "FACE", "distribution": "RAND",
            "hairLength": size, "renderType": "OBJECT", "instanceObject": src,
            "size": 1.0, "sizeRandom": 0.45, "showEmitter": False,
        })
        # READ IT BACK. rendersNothing is the field that catches the silent case.
        info = call("list_particles", {"object": surf})
        row = (info.get("systems") or [{}])[0]
        if row.get("rendersNothing"):
            raise RuntimeError("%s would render nothing: %s" % (name, row))
        total += int(row.get("count") or 0)
        systems += 1

    # The scatter plates for the shelving and benches sit on the FLOOR by default; lift them onto
    # the surfaces they belong to. Done as an explicit move so the numbers above stay readable.
    for name, z in (("Clutter_ShelfW", 1.20), ("Clutter_ShelfE", 1.05),
                    ("Clutter_BenchMain", 0.93), ("Clutter_BenchBack", 0.93)):
        call("transform_object", {"object": name, "location": {
            "x": sum(SCATTERS[[s[0] for s in SCATTERS].index(name)][1][:2]) / 2.0,
            "y": sum(SCATTERS[[s[0] for s in SCATTERS].index(name)][1][2:]) / 2.0,
            "z": z}})

    done("%d instances across %d scatter systems, every count a field on the modifier"
         % (total, systems))


if __name__ == "__main__":
    build()
