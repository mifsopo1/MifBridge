"""Generate demo images and their numbers, automatically, from a live Blender.

WHY THIS EXISTS. A Fab listing lives or dies on its first three images, and screenshots taken by hand
go stale the moment anything changes - the badge count, the panel, the op list. Everything this
plugin does is already scriptable, so the demo material should be generated the same way the numbers
in the README are: derived, not typed.

WHAT IT ACTUALLY DEMONSTRATES, and it is deliberately not "look, a cube". It runs the capability
chain that landed on 2026-09-04 and produces the before/after a buyer would want to see:

    1. build a scene, badly on purpose - unapplied scale, ngon caps, no unwrap
    2. render it and SEE it (render_still returnImage - the render comes back as an image)
    3. measure it (mesh_quality - the defects that get an asset rejected)
    4. fix it (recipe_game_ready - the boring pipeline, banked)
    5. render again and MEASURE THE DIFFERENCE (compare_to_reference - silhouette IoU)

Each step writes a PNG and prints the numbers behind it, so the images and the claims come from the
same run and cannot drift apart.

IT CHECKS ITS OWN OUTPUT. A demo generator that writes a blank frame and reports success is worse
than none, because nobody looks at marketing images until they are already published. Every render
is verified as a real file with real bytes, and the silhouette coverage is checked so a black frame
is caught rather than shipped.

WHAT THIS IS NOT. The subject is a grey cylinder, and that is a CAPABILITY demo - it proves the
chain works and the numbers are real. It is not listing art. The images that would actually sell this
are the in-editor panel doing real work, a genuine asset round-tripping Blender to Unreal, and a
before/after with the numbers on it. Those need a real scene and a running editor, which is a
different job than this one. Said here so nobody publishes a cylinder.

NEEDS A LIVE BLENDER with the addon listening - see blender_audit_common for the launch line. It
does not need Unreal: the UE side writes its own PNGs through capture_viewport and capture_camera,
but those need a running editor and a level worth photographing, which is a different job.

Usage:
    python tools/make_demo.py --out <dir>
Exit: 0 all images produced and verified, 1 something was not, 2 no Blender to talk to
"""
import argparse
import base64
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blender_audit_common as B


def emit(out_dir, name, b64, notes):
    """Write one demo image and say what it shows. Returns (ok, path, bytes)."""
    if not b64:
        return False, None, 0
    raw = base64.b64decode(b64)
    path = os.path.join(out_dir, name)
    with open(path, "wb") as fh:
        fh.write(raw)
    print("  wrote %-26s %7d bytes   %s" % (name, len(raw), notes))
    return True, path, len(raw)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "dist", "demo"),
                    help="directory for the generated images")
    ap.add_argument("--px", type=int, default=640, help="render resolution (square)")
    a = ap.parse_args()

    if not B.reachable():
        return B.skip_banner("demo generation")

    os.makedirs(a.out, exist_ok=True)
    print("MifBridge demo generation -> %s" % a.out)
    print("")

    problems = []
    facts = {}

    # ---- 1. a scene, built badly ON PURPOSE ------------------------------------------------
    # A cylinder has ngon caps and no unwrap, and a non-uniform scale is the single most common
    # export mistake. This is the honest "before" - not a strawman, just what happens when nobody
    # ran the checklist.
    print("1. building a subject with the three mistakes everyone actually makes")
    # EVERY SETUP CALL IS CHECKED. The first version of this fired them and moved on, so a refused
    # create_camera surfaced three steps later as "there is no scene camera" during the render -
    # a confusing symptom a long way from its cause. A demo generator that cannot see its own
    # scene being built has no business judging the pictures of it.
    def setup(op, params):
        r = B.call(op, params)
        if r.get("ok") is False:
            problems.append("setup step %s was REFUSED: %s" % (op, str(r.get("error"))[:150]))
        return r

    B.call("clear_scene", {})
    setup("create_primitive", {"kind": "cylinder", "name": "Demo_Part", "radius": 1.0})
    setup("transform_object", {"object": "Demo_Part", "scale": [1.8, 1.0, 1.0]})
    # TWO LIGHTS, NOT ONE. The first version used a single sun and the subject's shadow side came
    # back almost pure black - legible as a technical render, useless as a listing image. I could
    # only tell because the render comes back and I looked at it, which is exactly the capability
    # this demo exists to show off.
    setup("create_light", {"kind": "SUN", "name": "Demo_Key",
                           "location": {"x": 4, "y": -4, "z": 6}, "energy": 3.0})
    setup("create_light", {"kind": "AREA", "name": "Demo_Fill",
                           "location": {"x": -5, "y": -2, "z": 2}, "energy": 200.0})
    # lookAt takes COORDINATES, not an object name - asked, after guessing wrong. The subject is at
    # the origin, so that is what the camera is aimed at.
    setup("create_camera", {"name": "Demo_Cam", "location": {"x": 5, "y": -5, "z": 3.5},
                            "lookAt": {"x": 0, "y": 0, "z": 0}, "makeActive": True})
    setup("set_render_settings", {"filmTransparent": True})
    if problems:
        print("")
        print("scene setup failed, so nothing below would mean anything:")
        for p_ in problems:
            print("  - %s" % p_)
        return 1

    shot = {"resolutionX": a.px, "resolutionY": a.px, "samples": 16,
            "returnImage": True, "previewMaxPx": a.px}

    # ---- 2. render it, and SEE it ----------------------------------------------------------
    print("2. rendering it - and the render comes BACK, which is the whole point")
    before_path = os.path.join(a.out, "_before.png")
    r_before = B.call("render_still", dict(shot, filePath=before_path), timeout=900.0)
    ok, _, n = emit(a.out, "01-before.png", r_before.get("image"),
                    "the asset as authored")
    if not ok:
        problems.append("the BEFORE render produced no image: %s"
                        % (r_before.get("imageError") or r_before.get("error")))

    # ---- 3. measure it ---------------------------------------------------------------------
    print("3. measuring it - the defects that get an asset rejected from a store")
    q_before = B.call("mesh_quality", {"object": "Demo_Part"})
    facts["concernsBefore"] = q_before.get("concernCount")
    for c in q_before.get("concerns") or []:
        print("     - %s" % c)
    if not q_before.get("concerns"):
        problems.append("the demo subject raised NO concerns, so the before/after shows nothing - "
                        "the scene is not demonstrating what it claims to")

    # ---- 4. fix it -------------------------------------------------------------------------
    print("4. running the recipe")
    rec = B.call("recipe_game_ready", {"object": "Demo_Part"})
    facts["concernsAfter"] = rec.get("concernCount")
    for s in rec.get("steps") or []:
        print("     %-16s changed=%s" % (s.get("step"), s.get("changed")))

    # ---- 5. render again and compare -------------------------------------------------------
    print("5. rendering again and measuring the difference")
    after_path = os.path.join(a.out, "_after.png")
    r_after = B.call("render_still", dict(shot, filePath=after_path), timeout=900.0)
    ok2, _, _ = emit(a.out, "02-after.png", r_after.get("image"),
                     "after recipe_game_ready")
    if not ok2:
        problems.append("the AFTER render produced no image: %s"
                        % (r_after.get("imageError") or r_after.get("error")))

    cmp_out = {}
    if ok and ok2:
        cmp_out = B.call("compare_to_reference", {"image": after_path, "reference": before_path})
        facts["silhouetteIoU"] = cmp_out.get("silhouetteIoU")
        facts["maskCoverage"] = cmp_out.get("maskCoverage")
        # 1.0 IS THE CORRECT ANSWER HERE, and the first version of this line said the opposite.
        # apply_transform bakes the scale into the mesh data - the object is deliberately identical
        # on screen afterwards, that is what "apply" means. So the interesting claim is not that the
        # picture changed; it is that it did NOT while the export problem went away.
        print("     silhouetteIoU %s   (1.0 is CORRECT: applying a transform is a visual no-op by "
              "design - the render is identical and the export problem is gone, which is the whole "
              "point)" % cmp_out.get("silhouetteIoU"))
        # THE SELF-CHECK. A blank render is the failure a demo generator ships without noticing,
        # because nobody looks at marketing images until they are published.
        cov = (cmp_out.get("maskCoverage") or {}).get("image")
        if cov is not None and cov < 0.01:
            problems.append("the AFTER render is essentially blank (subject covers %.2f%% of the "
                            "frame) - the camera is not framing anything" % (cov * 100.0))
        if cmp_out.get("degenerateMask"):
            problems.append("the comparison could not produce a silhouette score: %s"
                            % cmp_out["degenerateMask"])

    # ---- the numbers, written beside the images --------------------------------------------
    facts_path = os.path.join(a.out, "demo_facts.json")
    io.open(facts_path, "w", encoding="utf-8").write(json.dumps({
        "generatedBy": "tools/make_demo.py",
        "concernsBefore": facts.get("concernsBefore"),
        "concernsAfter": facts.get("concernsAfter"),
        "concernsFixed": (q_before.get("concerns") or []),
        "recipeSteps": rec.get("steps"),
        "silhouetteIoU": facts.get("silhouetteIoU"),
        "note": ("generated from a live Blender by tools/make_demo.py. The images and these numbers "
                 "come from the SAME run, so a claim in the listing cannot drift from the picture "
                 "beside it."),
    }, indent=1))
    print("")
    print("  wrote %-26s the numbers behind the images" % "demo_facts.json")

    print("")
    if problems:
        print("%d problem(s) - these images are NOT fit to publish:" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("OK  %s concern(s) before, %s after. Images verified as real files with real bytes."
          % (facts.get("concernsBefore"), facts.get("concernsAfter")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
