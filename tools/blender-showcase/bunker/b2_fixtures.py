"""Stage 2 - fixtures. The shell becomes a place, and each room starts reading as a different one.

WHAT THIS EARNS IN THE VIDEO: recognisability. Stage 1 is six identical concrete boxes off a tunnel;
after this one is a dormitory, one is a workshop, one is a hospital. That difference is what lets
stage 7 cut between them and have the cut mean something.

HAND-PLACED, AND ONLY THE THINGS THAT DEFINE A ROOM. Bunks, racks, benches, beds, tanks - the
objects a viewer names the room by. The hundreds of small props that make it look LIVED IN are stage
3's job and are scattered, not placed, for the reason the lab's stage 2b records: hand-placing 400
props proves the bridge can call create_primitive 400 times, while scattering them proves it drives
Blender's instancing system.

Run after b1_shell.py. It reads that file's geometry constants rather than restating them, because
two files disagreeing about where a wall is is the failure this whole build is arranged to avoid.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
sys.path.insert(0, HERE)
import stage as S       # noqa: E402
import b1_shell as B1   # noqa: E402  - the shell's numbers, imported not restated

CY = B1.CY
HALL_LEN = B1.HALL_LEN


class Room(object):
    """A room's interior, addressed by DOORWAY and BACK rather than by min and max y.

    WHY NOT y0/y1, which is what this was. The rooms alternate sides of the hall, so for a south
    room the doorway is at max y and for a north room it is at min y. Writing `y0 + 0.2` therefore
    means "against the back wall" in three rooms and "stacked across the entrance" in the other
    three - and it did: the medical bay's cabinets were built in the doorway, which is only visible
    if you stand in that particular room and look out.

    So the axis is named after what it means. `d(0)` is the doorway wall, `d(1)` is the back wall,
    and the arithmetic is identical on both sides of the hall.
    """

    def __init__(self, label):
        for lab, side, xc, depth in B1.ROOMS:
            if lab != label:
                continue
            self.label, self.side, self.depth = label, side, depth
            self.x0, self.x1 = xc - 4.15, xc + 4.15
            self.hall_y = (2 * CY) if side > 0 else 0.0
            self.back_y = self.hall_y + side * depth
            return
        raise KeyError("no room %r in b1_shell.ROOMS" % label)

    def d(self, t):
        """A y position t of the way from the doorway wall (0) to the back wall (1)."""
        return self.hall_y + (self.back_y - self.hall_y) * t

    def m(self, metres):
        """`metres` INTO the room from the doorway wall, whichever side of the hall it is on."""
        return self.hall_y + self.side * metres


def room_bounds(label):
    r = Room(label)
    return r.x0, r.x1, r.hall_y, r.back_y, r.side


# --------------------------------------------------------------------------------- furniture parts
def crate(name, x, y, z, w=0.7, d=0.7, h=0.55, mat=None):
    S.box(name, x - w / 2, x + w / 2, y - d / 2, y + d / 2, z, z + h)
    if mat:
        S.paint(name, mat)
    return name


def barrel(name, x, y, z=0.0, r=0.29, h=0.88, mat=None):
    S.call("create_primitive", {"kind": "cylinder", "name": name, "radius": r, "depth": h,
                                "vertices": 20, "location": {"x": x, "y": y, "z": z + h / 2}})
    if mat:
        S.paint(name, mat)
    return name


def bunk(prefix, x, y, mat_frame, mat_pad, along_x=True):
    """A two-tier bunk. Four posts, two decks, two mattresses - the silhouette, not the joinery."""
    L, W = (1.95, 0.85) if along_x else (0.85, 1.95)
    for i, (dx, dy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
        S.box("%s_Post%d" % (prefix, i),
              x + dx * L / 2 - 0.04, x + dx * L / 2 + 0.04,
              y + dy * W / 2 - 0.04, y + dy * W / 2 + 0.04, 0.0, 1.95)
        S.paint("%s_Post%d" % (prefix, i), mat_frame)
    for tier, z in enumerate((0.42, 1.30)):
        S.box("%s_Deck%d" % (prefix, tier), x - L / 2, x + L / 2, y - W / 2, y + W / 2, z, z + 0.06)
        S.paint("%s_Deck%d" % (prefix, tier), mat_frame)
        S.box("%s_Pad%d" % (prefix, tier), x - L / 2 + 0.05, x + L / 2 - 0.05,
              y - W / 2 + 0.05, y + W / 2 - 0.05, z + 0.06, z + 0.20)
        S.paint("%s_Pad%d" % (prefix, tier), mat_pad)


def rack(prefix, x0, x1, y, tiers=4, depth=0.55, top=2.3, mat=None):
    """Shelving. Uprights plus decks - the thing stage 3 will scatter bottles and boxes onto."""
    for i, x in enumerate((x0, x1)):
        S.box("%s_Up%d" % (prefix, i), x - 0.05, x + 0.05, y - depth / 2, y + depth / 2, 0.0, top)
        S.paint("%s_Up%d" % (prefix, i), mat)
    for t in range(tiers):
        z = 0.35 + t * (top - 0.5) / max(1, tiers - 1)
        n = "%s_Shelf%d" % (prefix, t)
        S.box(n, x0, x1, y - depth / 2, y + depth / 2, z, z + 0.045)
        S.paint(n, mat)


def bench(name, x0, x1, y, mat_top, mat_leg, h=0.92, depth=0.75):
    S.box(name, x0, x1, y - depth / 2, y + depth / 2, h, h + 0.07)
    S.paint(name, mat_top)
    for i, x in enumerate((x0 + 0.12, x1 - 0.12)):
        S.box("%s_Leg%d" % (name, i), x - 0.05, x + 0.05,
              y - depth / 2 + 0.06, y + depth / 2 - 0.06, 0.0, h)
        S.paint("%s_Leg%d" % (name, i), mat_leg)


def tank(name, x, y, r=0.85, h=2.2, mat=None):
    S.call("create_primitive", {"kind": "cylinder", "name": name, "radius": r, "depth": h,
                                "vertices": 28, "location": {"x": x, "y": y, "z": h / 2}})
    if mat:
        S.paint(name, mat)


def main():
    S.begin("STAGE 2 - fixtures: six rooms that a viewer can tell apart")

    steel = S.mat("Bunker_Steel", (0.26, 0.27, 0.29, 1.0), 0.85, 0.45)
    painted = S.mat("Bunker_PaintedMetal", (0.115, 0.155, 0.125, 1.0), 0.25, 0.62)
    wood = S.mat("Bunker_Wood", (0.155, 0.105, 0.062, 1.0), 0.0, 0.78)
    fabric = S.mat("Bunker_Fabric", (0.20, 0.185, 0.155, 1.0), 0.0, 0.92)
    clinical = S.mat("Bunker_Clinical", (0.62, 0.64, 0.66, 1.0), 0.05, 0.35)
    rubber = S.mat("Bunker_Rubber", (0.045, 0.046, 0.05, 1.0), 0.0, 0.88)

    # ---- living quarters: bunks down the hall's south wall ------------------------------------
    print("  living quarters - bunks along the hall")
    for i in range(6):
        x = 12.4 + i * 3.1
        if x > HALL_LEN - 5.0:
            break
        bunk("Bunk_%02d" % i, x, 1.35, steel, fabric, along_x=True)
        S.box("Locker_%02d" % i, x + 1.15, x + 1.62, 0.55, 1.05, 0.0, 1.85)
        S.paint("Locker_%02d" % i, painted)

    # ---- armoury ---------------------------------------------------------------------------------
    # EVERY y BELOW IS r.m(metres INTO the room). The doorway wall is 0 whichever side of the hall
    # the room is on, so the same line reads the same in all six and cannot land furniture in the
    # entrance of half of them.
    print("  armoury - weapon racks and ammunition")
    r = Room("Armoury")
    for i in range(3):
        rack("Arm_Rack%d" % i, r.x0 + 0.4 + i * 2.7, r.x0 + 2.6 + i * 2.7, r.m(r.depth - 0.5),
             tiers=4, depth=0.5, top=2.35, mat=painted)
    bench("Arm_Bench", r.x0 + 0.6, r.x0 + 4.2, r.m(1.3), wood, steel)
    for i in range(7):
        crate("Arm_Ammo%d" % i, r.x1 - 1.2 - (i % 3) * 0.8, r.m(2.4 + (i // 3) * 0.85),
              0.0 if i < 6 else 0.55, 0.68, 0.68, 0.52, painted)

    # ---- medical bay ------------------------------------------------------------------------------
    print("  medical bay - beds, cabinets, surgery table")
    r = Room("Medical")
    for i in range(3):
        bx = r.x0 + 1.2 + i * 2.4
        by = r.m(r.depth - 1.5)
        S.box("Med_Bed%d" % i, bx - 0.5, bx + 0.5, by - 0.95, by + 0.95, 0.55, 0.72)
        S.paint("Med_Bed%d" % i, clinical)
        for j, (dx, dy) in enumerate(((-0.42, -0.85), (0.42, -0.85), (-0.42, 0.85), (0.42, 0.85))):
            S.box("Med_Bed%dL%d" % (i, j), bx + dx - 0.03, bx + dx + 0.03,
                  by + dy - 0.03, by + dy + 0.03, 0.0, 0.55)
            S.paint("Med_Bed%dL%d" % (i, j), steel)
    sy = r.m(2.2)
    S.box("Med_Surgery", r.x1 - 2.6, r.x1 - 1.0, sy - 1.0, sy + 1.0, 0.62, 0.78)
    S.paint("Med_Surgery", clinical)
    # ALONG A SIDE WALL, NOT ACROSS THE DOORWAY. These were built at the entrance, and it took
    # standing inside this room and rendering to see it - from the hall they look like any other
    # cabinet against any other wall.
    for i in range(3):
        cy_ = r.m(1.4 + i * 1.2)
        S.box("Med_Cab%d" % i, r.x0 + 0.2, r.x0 + 0.75, cy_ - 0.52, cy_ + 0.52, 0.0, 2.05)
        S.paint("Med_Cab%d" % i, clinical)

    # ---- hydroponics -------------------------------------------------------------------------------
    print("  hydroponics - three tiers of grow trays")
    r = Room("Hydroponics")
    for row in range(2):
        y = r.m(2.0 + row * 2.9)
        for tier in range(3):
            z = 0.55 + tier * 0.78
            n = "Hydro_Tray_%d_%d" % (row, tier)
            S.box(n, r.x0 + 0.5, r.x1 - 0.5, y - 0.62, y + 0.62, z, z + 0.16)
            S.paint(n, painted)
        for i, x in enumerate((r.x0 + 0.7, (r.x0 + r.x1) / 2, r.x1 - 0.7)):
            S.box("Hydro_Leg_%d_%d" % (row, i), x - 0.05, x + 0.05, y - 0.6, y + 0.6, 0.0, 2.15)
            S.paint("Hydro_Leg_%d_%d" % (row, i), steel)
    tank("Hydro_Nutrient", r.x1 - 1.1, r.m(r.depth - 1.2), r=0.6, h=1.7, mat=painted)

    # ---- workshop -----------------------------------------------------------------------------------
    print("  workshop - benches, tool cabinets, a drill press")
    r = Room("Workshop")
    bench("Work_Bench1", r.x0 + 0.5, r.x0 + 4.0, r.m(r.depth - 1.0), wood, steel)
    bench("Work_Bench2", r.x1 - 4.0, r.x1 - 0.5, r.m(r.depth - 1.0), wood, steel)
    for i in range(4):
        cy_ = r.m(1.6 + i * 1.35)
        S.box("Work_Cab%d" % i, r.x0 + 0.2, r.x0 + 0.75, cy_ - 0.6, cy_ + 0.6, 0.0, 1.75)
        S.paint("Work_Cab%d" % i, painted)
    dy = r.m(r.depth - 3.0)
    S.box("Work_Drill_Base", r.x1 - 2.3, r.x1 - 1.5, dy - 0.4, dy + 0.4, 0.0, 0.18)
    S.paint("Work_Drill_Base", steel)
    S.call("create_primitive", {"kind": "cylinder", "name": "Work_Drill_Col", "radius": 0.075,
                                "depth": 1.9, "vertices": 14,
                                "location": {"x": r.x1 - 1.9, "y": dy, "z": 1.05}})
    S.paint("Work_Drill_Col", steel)

    # ---- mess hall ------------------------------------------------------------------------------------
    print("  mess hall - long tables and a galley")
    r = Room("Mess")
    for i in range(2):
        y = r.m(1.7 + i * 2.2)
        bench("Mess_Table%d" % i, r.x0 + 0.8, r.x1 - 0.8, y, wood, steel, h=0.76, depth=0.95)
        for j, off in enumerate((-0.78, 0.78)):
            S.box("Mess_Seat%d_%d" % (i, j), r.x0 + 0.9, r.x1 - 0.9,
                  y + off - 0.16, y + off + 0.16, 0.0, 0.45)
            S.paint("Mess_Seat%d_%d" % (i, j), wood)
    gy = r.m(r.depth - 0.65)
    S.box("Mess_Galley", r.x0 + 0.5, r.x1 - 0.5, gy - 0.35, gy + 0.35, 0.0, 0.95)
    S.paint("Mess_Galley", clinical)

    # ---- power plant -------------------------------------------------------------------------------------
    print("  power - generators, fuel, battery banks")
    r = Room("Power")
    for i in range(2):
        gx = r.x0 + 1.6 + i * 3.4
        gy_ = r.m(1.9)
        S.box("Pwr_Gen%d" % i, gx - 1.2, gx + 1.2, gy_ - 0.8, gy_ + 0.8, 0.0, 1.35)
        S.paint("Pwr_Gen%d" % i, painted)
        S.call("create_primitive", {"kind": "cylinder", "name": "Pwr_Exh%d" % i, "radius": 0.14,
                                    "depth": 1.7, "vertices": 14,
                                    "location": {"x": gx + 0.9, "y": r.m(1.4), "z": 2.2}})
        S.paint("Pwr_Exh%d" % i, rubber)
    tank("Pwr_Fuel0", r.x1 - 1.4, r.m(r.depth - 1.3), r=0.95, h=2.4, mat=painted)
    tank("Pwr_Fuel1", r.x1 - 1.4, r.m(r.depth - 3.4), r=0.95, h=2.4, mat=painted)
    for i in range(6):
        crate("Pwr_Batt%d" % i, r.x0 + 0.9 + (i % 3) * 0.95, r.m(r.depth - 0.9),
              (i // 3) * 0.62, 0.82, 0.62, 0.58, painted)

    # ---- the hall itself: stacked supply crates and drums --------------------------------------------
    print("  hall - supply crates and drums")
    for i in range(9):
        crate("Hall_Crate%02d" % i, 3.4 + (i % 3) * 0.85, 2 * CY - 3.6 - (i // 3) * 0.85,
              0.0 if i % 3 else 0.56, 0.8, 0.8, 0.55, wood)
    for i in range(5):
        barrel("Hall_Drum%02d" % i, 25.0 + i * 0.72, CY + 4.9, 0.0, 0.29, 0.88, painted)

    S.look((3.0, CY - 1.2, 1.65), (24.0, CY + 1.6, 1.5), lens=22.0)
    S.done("six rooms, each with the fixtures that name it; the small props are stage 3's job")


if __name__ == "__main__":
    main()
