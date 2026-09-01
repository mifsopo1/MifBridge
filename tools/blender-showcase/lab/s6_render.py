"""STAGE 6 - a dust emitter, and the render that proves the whole thing produced a picture.

Deliberately last and deliberately small. render_still BLOCKS Blender's main thread for the whole
exposure, so the settings here are a preview: 960x540 at 32 samples takes seconds. A production
frame is the same call with bigger numbers and a much longer wait, and the response says how long
it actually held the thread.

wroteFile is stat'd off disk rather than taken from the operator. bpy.ops.render.render returns
FINISHED whether or not a file appeared - a bad path, a permissions problem and a disabled format
all look identical from the return value alone.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, box, paint, mat

OUT = "D:/DDS2SDK/Game/Plugins/MifBridge/tools/blender-showcase/lab_preview.png"


def build():
    begin("STAGE 6  dust motes, render settings, and a still")
    mat("Dust", (0.55, 0.53, 0.48), roughness=0.9)

    # A dust emitter under the window shaft - the thing that makes a light beam visible without
    # volumetrics. HAIR would sit still; EMITTER with almost no gravity drifts.
    src = box("Dust_Emitter", 10.6, 14.8, 8.4, 10.4, 3.30, 3.32)
    paint(src, "Dust")
    mote = box("Dust_Mote", -0.012, 0.012, -0.012, 0.012, 0.0, 0.024)
    paint(mote, "Dust")
    call("transform_object", {"object": mote, "location": {"x": -6.0, "y": -3.0, "z": 0.0}})
    call("add_particles", {"object": src, "type": "EMITTER", "count": 900,
                           "frameStart": 1, "frameEnd": 500, "lifetime": 260,
                           "lifetimeRandom": 0.4, "emitFrom": "FACE",
                           "physicsType": "NEWTON", "gravityFactor": 0.012,
                           "normalFactor": 0.0, "randomFactor": 0.06,
                           "size": 1.0, "sizeRandom": 0.6,
                           "renderType": "OBJECT", "instanceObject": "Dust_Mote",
                           "showEmitter": False})
    ps = call("list_particles", {"object": src})
    print("  particles: %s" % ps.get("systems"))

    call("set_render_settings", {"engine": "EEVEE", "resolutionX": 960, "resolutionY": 540,
                                 "percentage": 100, "samples": 32,
                                 "filePath": OUT, "fileFormat": "PNG",
                                 "exposure": 0.2})

    # Frame 300 - past the flicker patterns and part way through the camera move, so the still
    # shows the room mid-travel rather than at its start pose.
    r = call("render_still", {"frame": 300}, timeout=900.0)
    print("  render: %s  %s bytes  %.2fs  %s"
          % (r.get("filePath"), r.get("fileBytes"), r.get("elapsedSeconds"), r.get("resolution")))
    if not r.get("wroteFile"):
        raise RuntimeError("render reported success and no file reached disk: %s" % r)

    done("preview at %s" % OUT)


if __name__ == "__main__":
    build()
