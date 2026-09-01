"""STAGE 1 - the room shell: modular damaged concrete, columns, ceiling beams, a broken window.

Modular on purpose. The walls are repeated panels rather than four long boxes, so damage varies per
panel and any one can be swapped without touching its neighbours - which is what "modular
architecture" in the benchmark actually means, and it shows on camera as the room assembling itself
piece by piece rather than appearing whole.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, box, cyl, cut, paint, mat, look

# The room. Wide and low, like a basement unit - the reference images are all long horizontals.
RX0, RX1 = 0.0, 18.0
RY0, RY1 = 0.0, 11.0
RZ0, RZ1 = 0.0, 3.6
WT = 0.32
PANEL = 3.0


def materials():
    mat("Concrete", (0.30, 0.29, 0.27), roughness=0.95)
    mat("Concrete_Stained", (0.19, 0.19, 0.17), roughness=0.88)
    mat("Rusted_Metal", (0.26, 0.12, 0.06), metallic=0.7, roughness=0.88)
    mat("Painted_Metal", (0.24, 0.27, 0.28), metallic=0.6, roughness=0.5)
    mat("Grime", (0.10, 0.10, 0.08), roughness=0.98)


def build():
    begin("STAGE 1  the shell - modular damaged concrete, columns, beams")
    call("clear_scene", {})
    # Solid + framed to start: the shell is geometry, and geometry reads better in SOLID than in a
    # near-black RENDERED view. Stage 4 switches to RENDERED when there is something to light.
    call("set_viewport_shading", {"shading": "SOLID", "showOverlays": True})
    materials()

    look((16.60, 9.80, 2.40), (4.00, 2.00, 1.00))
    floor = box("Floor", RX0 - WT, RX1 + WT, RY0 - WT, RY1 + WT, -0.30, 0.0)
    paint(floor, "Concrete_Stained")
    ceil = box("Ceiling", RX0 - WT, RX1 + WT, RY0 - WT, RY1 + WT, RZ1, RZ1 + 0.30)
    paint(ceil, "Concrete")

    # ---- modular wall panels, each bitten into so no two read the same -------------------
    look((1.40, 1.20, 1.70), (9.00, 6.00, 1.60))
    n = 0
    for i in range(int((RX1 - RX0) / PANEL)):
        x0 = RX0 + i * PANEL
        x1 = min(x0 + PANEL, RX1)
        for tag, y0, y1 in (("N", RY1 - WT, RY1), ("S", RY0, RY0 + WT)):
            name = box("Wall_%s_%02d" % (tag, i + 1), x0, x1, y0, y1, RZ0, RZ1)
            # Deterministic bites - a rebuild is reproducible, which matters when it is being
            # filmed twice.
            for k in range(3):
                h = (hash((name, k)) & 0xFFFF) / 65535.0
                g = (hash((name, k, 2)) & 0xFFFF) / 65535.0
                sx = (x1 - x0) * (0.06 + 0.12 * h)
                sz = (RZ1 - RZ0) * (0.05 + 0.10 * g)
                cx = x0 + (x1 - x0 - sx) * g
                cz = RZ0 + (RZ1 - RZ0 - sz) * h
                cut(name, "_b_%s_%d" % (name, k), cx, cx + sx,
                    y0 - 0.02, y0 + (y1 - y0) * 0.5, cz, cz + sz)
            paint(name, "Concrete")
            n += 1
    for i in range(int((RY1 - RY0) / PANEL) + 1):
        y0 = RY0 + WT + i * PANEL
        y1 = min(y0 + PANEL, RY1 - WT)
        if y1 - y0 < 0.3:
            continue
        for tag, x0, x1 in (("W", RX0, RX0 + WT), ("E", RX1 - WT, RX1)):
            name = box("Wall_%s_%02d" % (tag, i + 1), x0, x1, y0, y1, RZ0, RZ1)
            for k in range(2):
                h = (hash((name, k)) & 0xFFFF) / 65535.0
                sz = (RZ1 - RZ0) * (0.05 + 0.09 * h)
                cz = RZ0 + (RZ1 - RZ0 - sz) * h
                cut(name, "_b_%s_%d" % (name, k), x0 - 0.02, x0 + (x1 - x0) * 0.5,
                    y0 + (y1 - y0) * 0.2, y0 + (y1 - y0) * 0.5, cz, cz + sz)
            paint(name, "Concrete")
            n += 1

    look((9.00, 3.00, 1.80), (12.70, 10.40, 2.40))
    # ---- the boarded window on the north wall, the light source in every reference shot ----
    win = box("Window_Opening", 11.0, 14.4, RY1 - WT - 0.02, RY1 + 0.02, 1.9, 3.1)
    call("boolean_op", {"target": "Wall_N_05", "cutter": win,
                        "operation": "difference", "deleteCutter": True})
    for i in range(5):
        yb = box("Window_Board_%02d" % (i + 1), 11.0, 14.4,
                 RY1 - WT + 0.04, RY1 - WT + 0.10, 1.95 + i * 0.24, 1.95 + i * 0.24 + 0.16)
        paint(yb, "Rusted_Metal")

    look((2.00, 9.60, 1.50), (14.00, 4.00, 3.20))
    # ---- columns and ceiling beams -------------------------------------------------------
    for i, x in enumerate((4.5, 9.0, 13.5)):
        c = box("Column_%02d" % (i + 1), x - 0.26, x + 0.26, 5.4, 5.92, RZ0, RZ1)
        paint(c, "Concrete")
    for i, y in enumerate((2.0, 5.6, 9.2)):
        bm = box("Beam_%02d" % (i + 1), RX0, RX1, y - 0.16, y + 0.16, RZ1 - 0.34, RZ1)
        paint(bm, "Painted_Metal")

    call("frame_viewport", {"all": True})
    done("18 x 11 x 3.6 m room, %d wall panels, boarded window, 3 columns, 3 beams" % n)


if __name__ == "__main__":
    build()
