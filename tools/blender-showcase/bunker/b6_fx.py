"""Stage 6 - atmosphere. Dust in the light, steam off the generators, sparks when a tube fails.

WHAT THIS EARNS IN THE VIDEO: it makes the light visible. A pendant in clean air is a bright dot; the
same pendant with dust drifting through it has a beam, and a beam is most of what makes an
underground shot read as underground.

THE SPARKS ARE TIED TO b5's BEATS, not sprinkled through the timeline. frameStart and frameEnd on a
particle system are emission windows, so the sparks under the dying pendant only exist while it is
actually failing - which is the difference between an effect and a decoration. The beat times are
imported from b5_anim rather than retyped, for the same reason the camera imports them.

EVERY SYSTEM IS READ BACK, exactly as stage 3 does: list_particles reports rendersNothing, which
catches the case where a system exists, reports success, and renders nothing at all.

Run after b5_anim.py.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
sys.path.insert(0, HERE)
import stage as S       # noqa: E402
import b1_shell as B1   # noqa: E402
import b5_anim as B5    # noqa: E402
from b2_fixtures import Room  # noqa: E402

CY = B1.CY
HALL_LEN = B1.HALL_LEN
FPS = B5.FPS
END_F = B5.sec(B5.DURATION_S)
PARK = 90.0


def mote(name, radius, rgb, emissive=0.0):
    """A single tiny thing to instance. Parked off the set - it is a source, not a prop."""
    S.call("create_primitive", {"kind": "icosphere", "name": name, "radius": radius,
                                "subdivisions": 1,
                                "location": {"x": PARK, "y": 0, "z": 0}})
    m = "FX_%s" % name
    S.call("create_material", {"name": m, "reuse": True, "baseColor": list(rgb) + [1.0],
                               "roughness": 0.9})
    if emissive:
        S.call("set_material_properties", {"material": m, "emissive": list(rgb) + [1.0],
                                           "emissiveStrength": emissive})
    S.paint(name, m)
    return name


def emitter(name, x0, x1, y0, y1, z, src, count, seed, life=180, gravity=0.01,
            normal=0.0, f0=1, f1=None, size=1.0):
    """A plane that throws `count` instances of `src` between frames f0 and f1."""
    plate = S.box(name, x0, x1, y0, y1, z, z + 0.004)
    S.call("add_particles", {
        "object": plate, "type": "EMITTER", "count": count, "seed": seed,
        "emitFrom": "FACE", "distribution": "RAND",
        "lifetime": life, "lifetimeRandom": 0.5,
        "physicsType": "NEWTON", "gravityFactor": gravity, "normalFactor": normal,
        "frameStart": f0, "frameEnd": (END_F if f1 is None else f1),
        "renderType": "OBJECT", "instanceObject": src,
        "size": size, "sizeRandom": 0.6, "showEmitter": False,
    })
    row = ((S.call("list_particles", {"object": plate}).get("systems") or [{}]))[0]
    if row.get("rendersNothing"):
        raise RuntimeError("%s would render nothing: %r" % (name, row))
    return int(row.get("count") or 0)


def main():
    S.begin("STAGE 6 - atmosphere: dust in the light, steam, and sparks on the beat")

    # RE-RUNNABLE. Without this, a second run built a second set of emitter plates on top of the
    # first - the object count went 343 to 351 and the particle totals printed exactly the same,
    # because each run only counts what IT made. Tuning an effect by re-running the stage would
    # have quietly doubled it every time.
    objs = S.call("list_objects", {}).get("objects") or []
    doomed = [o["name"] for o in objs
              if o.get("name", "").startswith(("FX_Dust", "FX_Steam", "FX_Spark", "Src_Mote",
                                               "Src_Steam", "Src_Spark"))]
    if doomed:
        S.call("delete_object", {"objects": doomed, "purgeOrphans": True})
        print("  removed %d object(s) from a previous run" % len(doomed))

    dust_src = mote("Src_Mote", 0.012, (0.55, 0.53, 0.48))
    steam_src = mote("Src_Steam", 0.09, (0.70, 0.71, 0.74))
    spark_src = mote("Src_Spark", 0.014, (1.00, 0.72, 0.30), emissive=22.0)

    total, systems = 0, 0

    # ---- dust down the whole hall ------------------------------------------------------------
    # High in the vault and falling slowly. gravityFactor 0.01 rather than 0 because motionless
    # dust reads as a texture; barely-falling dust reads as air.
    print("  hall dust")
    total += emitter("FX_Dust_Hall", 2.0, HALL_LEN - 2.0, CY - 4.0, CY + 4.0, 5.2,
                     dust_src, 1400, 2100, life=260, gravity=0.010)
    systems += 1

    # ---- steam off the generators ----------------------------------------------------------------
    # normalFactor pushes along the emitter's normal, which is up, so this rises instead of falling.
    print("  steam off the generators")
    r = Room("Power")
    for i in range(2):
        gx = r.x0 + 1.6 + i * 3.4
        total += emitter("FX_Steam_%d" % i, gx - 0.35, gx + 0.35,
                         r.m(1.1), r.m(1.7), 2.3, steam_src, 220, 2200 + i,
                         life=90, gravity=-0.02, normal=1.6, size=1.4)
        systems += 1

    # ---- sparks under the dying pendant, ONLY while it is dying ------------------------------------
    # Lamp_Hall04 is b5's failing tube. Its bad moments are at 6.0-6.8s and 17.0-18.4s, so the
    # emission windows are those and nothing else - the sparks do not exist for the other 38 seconds.
    print("  sparks under the failing pendant, on b5's beats")
    x_dying = 2.6 + 4 * (HALL_LEN - 5.2) / 6.0
    for k, (a, b) in enumerate(((5.9, 7.0), (16.9, 18.6))):
        total += emitter("FX_Spark_%d" % k, x_dying - 0.25, x_dying + 0.25,
                         CY - 0.25, CY + 0.25, 4.35, spark_src, 90, 2300 + k,
                         life=26, gravity=0.9, f0=B5.sec(a), f1=B5.sec(b), size=1.0)
        systems += 1

    # BAKE, OR NONE OF THIS EXISTS AT A GIVEN FRAME. EMITTER particles with NEWTON physics are
    # SIMULATED forward from the start of the range; jumping straight to frame 155 to look at the
    # sparks shows an unsimulated system, which renders nothing.
    #
    # Measured before the bake was added: the spark-ish pixel count at 6.4s (inside a failure
    # window), 16.6s (outside both) and 17.7s (inside the second) came back as 23, 23 and 23 -
    # identical, because all three were static background and no spark existed in any frame. The
    # stage had already printed "sparks emit only during b5's two failure windows", which was a
    # claim about the parameters I had passed rather than about anything on screen.
    print("  baking physics over the whole range - unbaked particles render nothing at a jumped frame")
    bake = S.call("bake_physics", {"start": 1, "end": END_F}, timeout=1800.0)
    # THIS USED TO SAY bake_physics DID NOT BAKE THE PARTICLES. That was wrong and the correction
    # is worth keeping. It reported cacheCount 1, kind "rigidbody", with the five systems this stage
    # had just created missing - but ptcache.bake_all had baked them all along. The op's READBACK
    # walked ob.modifiers[].point_cache, and a particle system's cache is not there; it hangs off
    # ob.particle_systems[i].point_cache. Fixed in the addon on 2026-09-05.
    #
    # The check below is kept, because a bake that genuinely did nothing is still worth catching -
    # it just no longer fires for the wrong reason.
    baked = [c for c in (bake.get("caches") or []) if c.get("kind") == "particles"]
    print("  particle caches reported: %d, of which baked: %d"
          % (len(baked), sum(1 for c in baked if c.get("isBaked"))))
    for c in baked:
        if c.get("isBaked") and str(c.get("object", "")).startswith("FX_Spark"):
            print("    %s cache covers frames %s - its emission window plus particle lifetime"
                  % (c["object"], c.get("frames")))
    if not baked:
        print("  !! bake_physics reported NO particle caches at all. A still at a jumped frame will")
        print("  !! show nothing, because EMITTER/NEWTON particles simulate forward from the start.")
    print("    %s" % str({k: v for k, v in bake.items()
                          if k not in ("ok", "endpoint", "elapsedMs")})[:200])

    S.look((5.0, CY - 1.8, 1.7), (26.0, CY + 0.8, 2.4), lens=22.0)
    # WHAT IS AND IS NOT VERIFIED HERE, said plainly rather than implied. The counts and the
    # emission windows are read back from Blender. Whether a spark is VISIBLE at a given second is
    # not checked, and cannot be from a jumped frame while bake_physics leaves particles unbaked -
    # an earlier version of this line claimed "sparks emit only during b5's two failure windows",
    # which was a claim about the parameters passed rather than about anything on screen. The
    # spark-ish pixel count at 6.4s, 16.6s and 17.7s was 23, 23 and 23: all background.
    # WHAT IS AND IS NOT VERIFIED, now that the addon reports particle caches. The counts, the
    # emission windows AND the baked state are all read back from Blender - the spark caches report
    # frame ranges matching their declared beats, which is the check this line used to say it could
    # not do. What is still not asserted is whether a 1.4cm mote is VISIBLE at a given camera and
    # exposure; that is a composition question and a render is the only judge of it.
    S.done("%d particle(s) across %d system(s). Counts, windows and baked state read back from "
           "Blender; per-camera visibility is a composition question and is not asserted here."
           % (total, systems))


if __name__ == "__main__":
    main()
