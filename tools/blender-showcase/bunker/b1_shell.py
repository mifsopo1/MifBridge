"""Stage 1 - the bunker shell. A barrel-vaulted hall cut into rock, with rooms off it.

WHAT THIS EARNS IN THE VIDEO: the room appears. It is the single most watchable moment in the whole
build and it takes about twenty seconds, which is why it is stage one and why nothing else shares
the file.

THE VAULT IS A REAL HOLLOW SHELL, not a curved backdrop. An outer cylinder minus an inner one gives
a tube; subtracting everything below the springing line leaves a half-tube sitting on the floor. It
is built that way because the camera goes INSIDE it and because the side doorways are cut THROUGH
it - both of which a backdrop would fail at, and the second would fail silently.

EVERY CALL IS A TYPED OP. run_python is not used here; the census at the end says so, measured
rather than asserted, exactly as the lab does it.

Run it first - it clears the scene.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
import stage as S  # noqa: E402  - the lab's plumbing, imported rather than forked

# ---------------------------------------------------------------------------- the bunker's numbers
# ONE PLACE, because stage 2 onwards has to agree with stage 1 about where the walls are and the
# lab's own lesson is that a number re-derived per file is a number that will disagree.
HALL_LEN = 34.0          # x: 0 .. 34
VAULT_R_OUT = 7.0        # the rock cut
VAULT_R_IN = 6.55        # the finished interior
CY = 7.0                 # vault centre in y, so the hall is 14 m across at the floor
SPRING_Z = 0.0           # the vault springs at floor level - a Nissen section, like the concept art

# The interior the camera must stay inside. look() checks the eye against this and would otherwise
# be checking against the lab's room, which is the whole reason ROOM became settable.
S.set_room((0.6, HALL_LEN - 0.6), (0.9, 2 * CY - 0.9), (0.15, VAULT_R_IN - 0.5))

# Doorways off the hall, and the rooms behind them. Named for the concept board's build list, which
# is what makes cuts between them read as different places rather than different corners.
#   (label, side, x centre, room depth)
ROOMS = [
    ("Armoury",     -1, 7.0,  7.0),
    ("Medical",     +1, 7.0,  7.0),
    ("Hydroponics", -1, 20.0, 8.0),
    ("Workshop",    +1, 20.0, 8.0),
    ("Mess",        -1, 29.5, 6.0),
    ("Power",       +1, 29.5, 6.0),
]
DOOR_W = 3.2
DOOR_H = 2.7


def rotate_along_x(name):
    """Lay a cylinder down so its axis runs along world X.

    RADIANS, and the lab's stage.py records why in one line - transform_object writes rotation_euler
    straight through, and passing degrees put a roof at 82 degrees on the house build.
    """
    S.call("transform_object", {"object": name, "rotation": {"x": 0.0, "y": math.pi / 2.0, "z": 0.0}})


def main():
    S.begin("STAGE 1 - the shell: a vaulted hall cut into rock, and the rooms off it")

    S.call("clear_scene", {})

    concrete = S.mat("Bunker_Concrete", (0.20, 0.20, 0.205, 1.0), 0.0, 0.85)
    rock = S.mat("Bunker_Rock", (0.085, 0.082, 0.078, 1.0), 0.0, 0.95)
    steel = S.mat("Bunker_Steel", (0.26, 0.27, 0.29, 1.0), 0.85, 0.45)

    # ---- the floor slab ---------------------------------------------------------------------
    print("  floor slab")
    S.box("Hall_Floor", -0.6, HALL_LEN + 0.6, -0.6, 2 * CY + 0.6, -0.35, 0.0)
    S.paint("Hall_Floor", concrete)

    # ---- the vault: outer cylinder minus inner, then everything below the springing line ------
    print("  vault shell (outer - inner, then cut below the springing line)")
    S.call("create_primitive", {"kind": "cylinder", "name": "Vault", "radius": VAULT_R_OUT,
                                "depth": HALL_LEN, "vertices": 56,
                                "location": {"x": HALL_LEN / 2.0, "y": CY, "z": SPRING_Z}})
    rotate_along_x("Vault")
    # LONGER THAN THE HALL on purpose. A cutter exactly as long as its target leaves coincident end
    # faces, and coplanar geometry is where booleans produce shards rather than an opening.
    S.call("create_primitive", {"kind": "cylinder", "name": "Vault_Bore", "radius": VAULT_R_IN,
                                "depth": HALL_LEN + 4.0, "vertices": 56,
                                "location": {"x": HALL_LEN / 2.0, "y": CY, "z": SPRING_Z}})
    rotate_along_x("Vault_Bore")
    S.call("boolean_op", {"target": "Vault", "cutter": "Vault_Bore",
                          "operation": "difference", "deleteCutter": True})
    S.cut("Vault", "Vault_Underside",
          -2.0, HALL_LEN + 2.0, CY - VAULT_R_OUT - 2.0, CY + VAULT_R_OUT + 2.0,
          -VAULT_R_OUT - 2.0, SPRING_Z)
    S.paint("Vault", rock)

    # ---- the two ends -------------------------------------------------------------------------
    # Plain slabs. They overshoot the arch, and that is fine and deliberate: every shot in this
    # build is from INSIDE, so overshoot is behind the rock where no camera goes. Intersecting them
    # with the vault volume would be three more booleans for something nobody can see.
    print("  end walls, and the blast-door opening")
    S.box("End_Wall_Near", -0.5, 0.0, -0.5, 2 * CY + 0.5, 0.0, VAULT_R_IN + 0.5)
    S.box("End_Wall_Far", HALL_LEN, HALL_LEN + 0.5, -0.5, 2 * CY + 0.5, 0.0, VAULT_R_IN + 0.5)
    S.paint("End_Wall_Near", concrete)
    S.paint("End_Wall_Far", concrete)
    S.cut("End_Wall_Near", "Blast_Opening",
          -1.0, 0.5, CY - 2.1, CY + 2.1, 0.0, 3.6)

    # ---- the blast door itself ----------------------------------------------------------------
    # A round vault door, parked open against the wall - stage 5 is what closes it. It is here in
    # stage 1 because the entrance is the establishing shot and an empty hole is not one.
    S.call("create_primitive", {"kind": "cylinder", "name": "Blast_Door", "radius": 1.95,
                                "depth": 0.35, "vertices": 40,
                                "location": {"x": 0.55, "y": CY - 2.45, "z": 1.85}})
    rotate_along_x("Blast_Door")
    S.paint("Blast_Door", steel)
    S.box("Blast_Frame_L", -0.55, 0.05, CY - 2.35, CY - 2.05, 0.0, 3.7)
    S.box("Blast_Frame_R", -0.55, 0.05, CY + 2.05, CY + 2.35, 0.0, 3.7)
    S.box("Blast_Frame_T", -0.55, 0.05, CY - 2.35, CY + 2.35, 3.4, 3.7)
    for n in ("Blast_Frame_L", "Blast_Frame_R", "Blast_Frame_T"):
        S.paint(n, steel)

    # ---- the rooms off the hall ----------------------------------------------------------------
    made = 0
    for label, side, xc, depth in ROOMS:
        print("  room: %-12s %s side" % (label, "north" if side > 0 else "south"))
        near_y = (2 * CY) if side > 0 else 0.0
        far_y = near_y + side * depth
        y0, y1 = min(near_y, far_y), max(near_y, far_y)

        # The shell, then hollowed. A room built as four walls is four times the objects and looks
        # identical; one box minus a smaller box is two calls and always closes.
        shell = "Room_%s" % label
        S.box(shell, xc - 4.6, xc + 4.6, y0 - 0.45, y1 + 0.45, -0.35, 3.5)
        S.cut(shell, "%s_Void" % shell, xc - 4.15, xc + 4.15, y0, y1, 0.0, 3.05)
        S.paint(shell, concrete)

        # THE DOORWAY HAS TO GO THROUGH BOTH, and cutting only the vault is a mistake I made and
        # only found by standing in the room and rendering: the frame came back PURE BLACK. The
        # vault is the hall's wall, so the cut opened the rock - and the room shell keeps its own
        # 0.45 m near wall between the void and the hall, which sealed it again. Two solid surfaces
        # separate a room from a hall here, and removing one of them looks exactly like success from
        # every angle inside the hall.
        #
        # ONE SET OF COORDINATES, TWO CUTS. The cutter spans well past both faces on y, so the same
        # numbers open the rock and the room wall; deriving them twice is how they drift.
        for target in ("Vault", shell):
            S.cut(target, "Door_%s_%s" % (label, target),
                  xc - DOOR_W / 2.0, xc + DOOR_W / 2.0,
                  CY - VAULT_R_OUT - 1.5, CY + VAULT_R_OUT + 1.5,
                  0.0, DOOR_H)
        made += 1

    # ---- catwalk down the north side, and stairs up to it ---------------------------------------
    print("  catwalk and stairs")
    S.box("Catwalk", 4.0, HALL_LEN - 4.0, 2 * CY - 2.4, 2 * CY - 0.9, 3.05, 3.20)
    S.paint("Catwalk", steel)
    for i in range(9):
        S.box("Stair_%02d" % i, 4.0 + i * 0.42, 4.42 + i * 0.42,
              2 * CY - 2.4, 2 * CY - 0.9, 0.0, 0.34 + i * 0.34)
        S.paint("Stair_%02d" % i, steel)
    for i in range(11):
        x = 5.0 + i * 2.4
        if x > HALL_LEN - 4.5:
            break
        S.box("Rail_%02d" % i, x, x + 0.09, 2 * CY - 2.42, 2 * CY - 2.33, 3.20, 4.25)
        S.paint("Rail_%02d" % i, steel)
    S.box("Rail_Top", 4.0, HALL_LEN - 4.0, 2 * CY - 2.44, 2 * CY - 2.31, 4.15, 4.25)
    S.paint("Rail_Top", steel)
    # POSTS, because the first render of this had the catwalk hanging in mid-air off nothing. It is
    # only obvious from underneath - from the hall floor looking along it, the deck hides its own
    # want of support - and the shot that showed it was one taken from inside a room, which is the
    # argument for rendering from more than one place before believing a stage.
    for i in range(7):
        x = 5.6 + i * 3.9
        if x > HALL_LEN - 5.0:
            break
        S.box("Post_%02d" % i, x, x + 0.16, 2 * CY - 1.75, 2 * CY - 1.59, 0.0, 3.05)
        S.paint("Post_%02d" % i, steel)

    # ---- stand in the hall and look down it -----------------------------------------------------
    # An interior viewpoint, checked against the room bounds declared at the top. The lab's look()
    # refuses an eye outside them, which is the guard that caught a camera fourteen metres behind a
    # wall watching a building get built from a field.
    S.look((2.5, CY - 0.4, 1.75), (HALL_LEN - 6.0, CY + 0.6, 2.2), lens=20.0)

    S.done("%d room(s) off the hall; vault is a hollow shell with %d doorways cut through it"
           % (made, made))


if __name__ == "__main__":
    main()
