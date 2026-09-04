"""Run EVERY addon op on EVERY installed Blender, headless, and find the version drift.

WHY THIS EXISTS, and it is not a hypothetical. On 2026-09-03 the entire compositor family shipped
with thirteen offline checks passing and was DEAD on Blender 5.0: scene.node_tree does not exist
there, and every static gate in this repo was green. It was found by chance, because the live addon
happened to come up and a read op returned an AttributeError.

That is the shape this file is built to catch, and the reason the offline suite could not:
test_blender_refusals runs against a STUB I WROTE FROM THE SAME ASSUMPTIONS AS THE CODE. A stub is a
mirror, not a check. It agrees with whatever the author believed, including the wrong parts.

=============================================================================
WHY IT IS SAFE TO RUN EVERY OP, INCLUDING THE MUTATING ONES
=============================================================================
Each version is launched `--background --factory-startup`. That is a throwaway process with a fresh
default scene - Cube, Light, Camera - and nothing it does touches a file, a running Blender, or
anybody's session. No .blend is opened and none is saved. So unlike the live bridge, where a GUI
session may belong to someone else, here the whole op table can be exercised.

=============================================================================
WHAT COUNTS AS A FINDING
=============================================================================
A MifOpError is NOT a finding. An op refusing because the default scene has no armature, or because
a required parameter was not supplied, is the op working - that is B111's rule applied to a real
Blender rather than a stub.

The findings are:

  RAW EXCEPTION    anything that is not a MifOpError. AttributeError, TypeError, KeyError, a
                   RuntimeError out of bpy.ops - these are the addon meeting an API that is not
                   what it expected, which is exactly the compositor bug.
  DIVERGENCE       an op that behaves differently ACROSS versions - ok on 4.4 and raising on 5.0, or
                   refusing on one and succeeding on another. This is the strongest signal in the
                   file, because it needs no judgement about whether a refusal was reasonable: the
                   same call on the same fixture should not change its mind between builds.

Usage:
    python tools/blender_version_matrix.py                 # every op, every install
    python tools/blender_version_matrix.py --ops a,b,c     # just these
    python tools/blender_version_matrix.py --versions 5.0  # just this build
"""
import argparse
import glob
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_PARENT = os.path.join(HERE, "blender-addon")

INSTALL_GLOBS = (
    r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
    r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
)

# Payloads that let an op get PAST its required-parameter refusals and actually touch bpy. The point
# is reach, not correctness of the result: an op that refuses for a missing argument has told us
# nothing about whether its bpy calls are valid on this build.
#
# Anything absent from here is still called with {} - the refusal is recorded and is not a finding.
PAYLOADS = {
    "object_info": {"object": "Cube"},
    "face_info": {"object": "Cube"},
    "select_faces": {"object": "Cube", "axis": "Z"},
    "ray_cast": {"origin": [0, 0, 5], "direction": [0, 0, -1]},
    "closest_point_on_mesh": {"object": "Cube", "point": [0, 0, 5]},
    "set_shading": {"object": "Cube", "smooth": True},
    "bisect_plane": {"object": "Cube", "planeCo": [0, 0, 0], "axis": "Z"},
    "uv_info": {"object": "Cube"},
    "uv_unwrap": {"object": "Cube"},
    "list_modifiers": {"object": "Cube"},
    "list_vertex_groups": {"object": "Cube"},
    "list_shape_keys": {"object": "Cube"},
    "list_constraints": {"object": "Cube"},
    "list_animation_data": {"object": "Cube"},
    "list_keyframes": {"object": "Cube"},
    "list_custom_properties": {"object": "Cube"},
    "list_particles": {"object": "Cube"},
    "set_object_visibility": {"object": "Cube", "hideRender": False},
    "set_light": {"object": "Light", "energy": 100.0},
    "set_camera": {"object": "Camera", "lens": 50.0},
    "aim_object": {"object": "Camera", "target": [0, 0, 0]},
    "set_light_ies": {"object": "Light", "clear": True},
    "set_light_linking": {"object": "Light", "clearReceivers": True},
    "set_camera_panorama": {"object": "Camera", "type": "EQUIRECTANGULAR"},
    "create_primitive": {"kind": "cube"},
    "create_light": {"type": "POINT"},
    "create_camera": {},
    "create_empty": {},
    "create_text": {"body": "Mif"},
    "create_curve": {"points": [[0, 0, 0], [1, 0, 0]]},
    "create_armature": {"bones": [{"name": "root", "head": [0, 0, 0], "tail": [0, 0, 1]}]},
    "create_collection": {"name": "MifMatrixColl"},
    "link_objects": {"collection": "MifMatrixColl", "object": "Cube"},
    "set_collection_visibility": {"collection": "MifMatrixColl", "hideRender": True},
    "create_view_layer": {"name": "MifMatrixVL"},
    "set_view_layer": {"enablePasses": ["z"]},
    "set_world": {"strength": 0.5},
    "set_compositing": {"enabled": True},
    "set_render_settings": {"samples": 4},
    "set_color_management": {"exposure": 0.0},
    "add_constraint": {"object": "Cube", "type": "TRACK_TO", "target": "Camera"},
    "add_modifier": {"object": "Cube", "type": "SUBSURF"},
    "set_keyframe": {"object": "Cube", "path": "location", "frame": 1},
    "set_frame_range": {"start": 1, "end": 10},
    "create_action": {"name": "MifMatrixAction"},
    "add_particles": {"object": "Cube", "count": 10},
    "add_rigid_body": {"object": "Cube", "type": "ACTIVE"},
    "add_cloth": {"object": "Cube"},
    "add_collision": {"object": "Cube"},
    "create_material": {"name": "MifMatrixMat"},
    "set_material_properties": {"material": "MifMatrixMat", "baseColor": [1, 0, 0]},
    "describe_material": {"material": "MifMatrixMat"},
    "assign_material_to_faces": {"object": "Cube", "material": "MifMatrixMat", "faces": [0]},
    "create_node_group": {"name": "MifMatrixGroup"},
    "add_group_node": {"group": "MifMatrixGroup", "type": "GeometryNodeSetPosition"},
    "list_group_nodes": {"group": "MifMatrixGroup"},
    "set_viewport_shading": {"shading": "SOLID"},
    "transform_object": {"object": "Cube", "location": [1, 0, 0]},
    "apply_transform": {"object": "Cube"},
    "set_origin": {"object": "Cube", "to": "GEOMETRY"},
    "clean_mesh": {"object": "Cube"},
    "decimate_mesh": {"object": "Cube", "ratio": 0.5},
    "bevel_edges": {"object": "Cube", "width": 0.02},
    "select_edges": {"object": "Cube"},
    "rename_bones": {"object": "Cube", "map": {}},
}

# Ops deliberately NOT run, with the reason. Each would leave the throwaway process doing something
# slow or pointless rather than testing an API.
SKIP = {
    "run_python": "executes arbitrary code - nothing to learn and everything to go wrong",
    "render_still": "renders a frame; minutes per version for no API information",
    "render_animation": "spawns a SECOND Blender per version",
    "save_file": "writes a .blend",
    "open_file": "replaces the scene mid-run and invalidates every later op",
    "export_mesh": "writes a file",
    "export_scene": "writes a file",
    "import_mesh": "needs a fixture file",
    "import_scene": "needs a fixture file",
    "bake_texture": "slow, and writes an image",
    "bake_physics": "slow",
    "gen_asset": "reaches an external generator over the network",
    "gen_image": "reaches an external generator over the network",
    "gen_mesh": "reaches an external generator over the network",
    "gen_texture": "reaches an external generator over the network",
    "gen_status": "reaches an external generator over the network",
    "clear_scene": "deletes the fixture every later op depends on",
    "delete_object": "deletes the fixture every later op depends on",
    "delete_collection": "runs before its fixture exists in table order",
    "delete_view_layer": "runs before its fixture exists in table order",
}

# DIVERGENCES THAT ARE THE ADDON WORKING, with the reason. An op that correctly refuses a feature
# a build does not have IS a divergence by the definition above, and it is not a defect - so it is
# named here rather than reported forever. A check that always prints the same finding is a check
# people learn to scroll past, which is the objection this repo raises about every ungated audit.
#
# The bar for an entry is that the refusal names the version and says nothing was changed. Anything
# vaguer belongs in the report, because "it behaves differently and we think that is fine" is
# exactly the sentence that hides a real one.
EXPECTED_DIVERGENCE = {
    "set_light_linking": (
        "light_linking arrived on Object in Blender 4.2. On 3.6 the op refuses with 'it arrived in "
        "4.2, and this build is 3.6.23 ... NOTHING was changed', which is the correct answer rather "
        "than a silent no-op - verified 2026-09-03 by reading the message it actually returns."),
}

# The script that runs INSIDE Blender. Kept as a module-level constant rather than a temp file
# written per version, so what runs is readable here.
INNER = r'''
import json, sys, traceback
sys.path.insert(0, r"%(addon_parent)s")
import bpy

out = {"version": bpy.app.version_string, "results": {}}
try:
    from MifBlender import server as S
    from MifBlender.ops_common import MifOpError
    table = S._op_table()
except Exception:
    out["fatal"] = traceback.format_exc()
    print("MIFMATRIX" + json.dumps(out))
    raise SystemExit(0)

payloads = json.loads(r"""%(payloads)s""")
skip = json.loads(r"""%(skip)s""")
only = json.loads(r"""%(only)s""")

for name in sorted(table):
    if only and name not in only:
        continue
    if name in skip:
        out["results"][name] = {"status": "skipped", "detail": skip[name]}
        continue
    try:
        table[name](dict(payloads.get(name, {})))
        out["results"][name] = {"status": "ok"}
    except MifOpError as exc:
        out["results"][name] = {"status": "refused", "detail": str(exc)[:220]}
    except Exception as exc:
        out["results"][name] = {"status": "RAISED",
                                "detail": "%%s: %%s" %% (type(exc).__name__, str(exc)[:220])}
print("MIFMATRIX" + json.dumps(out))
'''


def installs(wanted=None):
    found = []
    for pat in INSTALL_GLOBS:
        for exe in sorted(glob.glob(pat)):
            label = os.path.basename(os.path.dirname(exe)).replace("Blender ", "")
            if wanted and label not in wanted:
                continue
            found.append((label, exe))
    return found


def run_one(label, exe, only, scratch):
    script = os.path.join(scratch, "mif_matrix_%s.py" % label.replace(".", "_"))
    io.open(script, "w", encoding="utf-8").write(INNER % {
        "addon_parent": ADDON_PARENT,
        "payloads": json.dumps(PAYLOADS),
        "skip": json.dumps(SKIP),
        "only": json.dumps(sorted(only or [])),
    })
    try:
        proc = subprocess.run([exe, "--background", "--factory-startup", "--python", script],
                              capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"version": label, "results": {}, "fatal": "timed out after 900s"}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("MIFMATRIX"):
            return json.loads(line[len("MIFMATRIX"):])
    return {"version": label, "results": {},
            "fatal": "no MIFMATRIX line; last stderr: %s" % (proc.stderr or "")[-400:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="", help="comma-separated op names")
    ap.add_argument("--versions", default="", help="comma-separated version labels")
    args = ap.parse_args()
    only = {o.strip() for o in args.ops.split(",") if o.strip()}
    want_v = {v.strip() for v in args.versions.split(",") if v.strip()}

    found = installs(want_v or None)
    if not found:
        print("no Blender installs found")
        return 2
    scratch = os.environ.get("TEMP") or HERE
    reports = []
    for label, exe in found:
        print("running %s ..." % label, flush=True)
        reports.append(run_one(label, exe, only, scratch))

    fatal = [r for r in reports if r.get("fatal")]
    for r in fatal:
        print("\nFATAL on %s:\n%s" % (r.get("version"), r["fatal"][:900]))

    ops = sorted({op for r in reports for op in r.get("results", {})})
    raised, diverged, expected = [], [], []
    for op in ops:
        row = {r["version"]: r["results"].get(op, {}) for r in reports}
        statuses = {v: e.get("status") for v, e in row.items()}
        for v, e in row.items():
            if e.get("status") == "RAISED":
                raised.append((op, v, e.get("detail", "")))
        # DIVERGENCE IS THE STRONGEST SIGNAL and needs no judgement: the same call on the same
        # fixture should not change its mind between builds.
        real = {s for s in statuses.values() if s and s != "skipped"}
        if len(real) > 1 and op not in EXPECTED_DIVERGENCE:
            diverged.append((op, statuses))
        elif len(real) > 1:
            expected.append((op, statuses))

    print("")
    print("=" * 78)
    print("versions: %s   ops: %d" % (", ".join(r.get("version", "?") for r in reports), len(ops)))
    print("=" * 78)
    if raised:
        print("\nRAW EXCEPTIONS - the addon met an API that is not what it expected:")
        for op, v, detail in raised:
            print("  %-28s %-10s %s" % (op, v, detail[:120]))
    else:
        print("\nno raw exceptions on any build.")
    if diverged:
        print("\nDIVERGENCE - the same call behaves differently across builds:")
        for op, statuses in diverged:
            print("  %-28s %s" % (op, "  ".join("%s=%s" % (v, s) for v, s in sorted(statuses.items()))))
    else:
        print("no UNEXPECTED op changed its behaviour between builds.")
    if expected:
        print("")
        print("expected divergences - the addon correctly refusing a feature a build lacks:")
        for op, statuses in expected:
            print("  %-28s %s" % (op, "  ".join("%s=%s" % (v, s)
                                                for v, s in sorted(statuses.items()))))
            print("      %s" % EXPECTED_DIVERGENCE[op][:150])
    # ------------------------------------------------------------------ REACH
    # "ZERO RAW EXCEPTIONS" IS WEAKER THAN IT SOUNDS IF HALF THE TABLE REFUSED. A refusal is the op
    # working, but it is NOT coverage: an op that declines for a missing parameter never reached its
    # bpy calls, so this run says nothing about whether those calls are valid on this build. The
    # compositor bug lived past the first guard, and an op refused at the door would have hidden it.
    #
    # So the reach is reported beside the findings. An op that refuses on EVERY build is untested
    # here, whatever the headline says, and is listed by name rather than counted - a number invites
    # rounding it off, a list of names invites fixing it.
    reached, refused_everywhere = {}, []
    for op in ops:
        row = {r["version"]: r["results"].get(op, {}).get("status") for r in reports}
        real = [s for s in row.values() if s and s != "skipped"]
        if not real:
            continue
        for v, s in row.items():
            if s == "ok":
                reached.setdefault(v, []).append(op)
        if real and all(s == "refused" for s in real):
            refused_everywhere.append(op)
    skipped = sorted({op for r in reports for op, e in r.get("results", {}).items()
                      if e.get("status") == "skipped"})
    print("")
    print("REACH - how far the calls actually got, which is not the same as the findings above:")
    for r in reports:
        v = r.get("version", "?")
        got = len(reached.get(v, []))
        print("  %-12s %3d op(s) reached their bpy calls and returned ok" % (v, got))
    if refused_everywhere:
        print("")
        print("  REFUSED ON EVERY BUILD - %d op(s). These were exercised as far as their guards and"
              % len(refused_everywhere))
        print("  NO FURTHER, so this run says nothing about whether their bpy calls are valid.")
        print("  Give them a payload in PAYLOADS to close the gap:")
        for i in range(0, len(refused_everywhere), 4):
            print("    " + "  ".join("%-26s" % o for o in refused_everywhere[i:i + 4]))
    if skipped:
        print("")
        print("  DELIBERATELY NOT RUN - %d op(s), each with a reason in SKIP: %s"
              % (len(skipped), ", ".join(skipped)))
    print("")
    print("A refusal is NOT a finding - an op declining because the default scene has no armature")
    print("is the op working. Raw exceptions and divergences are the findings; REACH is how much of")
    print("the table those findings actually cover.")
    return 1 if (raised or fatal) else 0


if __name__ == "__main__":
    sys.exit(main())
