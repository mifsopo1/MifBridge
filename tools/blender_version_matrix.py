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
VALUE_BASELINE = os.path.join(HERE, "blender_version_matrix_values.json")
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
    "object_info": {"object": "MifProbe"},
    # THE ROUND TRIP. Written to a throwaway temp dir; {TMP} is substituted inside Blender.
    "export_mesh": {"object": "MifCutter", "file": "{TMP}/mif_rt.fbx"},
    "export_scene": {"objects": ["MifCutter"], "file": "{TMP}/mif_rt.obj"},
    "import_mesh": {"file": "{TMP}/mif_rt.fbx"},
    "import_scene": {"file": "{TMP}/mif_rt.obj"},
    "save_file": {"filepath": "{TMP}/mif_rt.blend"},
    # THE READ-ONLY QUERIES GET THEIR OWN PRISTINE CUBE, and this is not tidiness.
    #
    # They used to read the shared Cube, which by the time the alphabetical sweep reaches 'c' has
    # been bisected at 'b', given a SUBSURF at 'a', constrained, and marked an active rigid body.
    # closest_point_on_mesh from [0,0,5] should be an exact 4.0 and was instead 4.042868 on two
    # builds and 2.948219 on the other two, with faceIndex 11 vs 7 - a geometry query apparently
    # disagreeing across Blender versions, entirely because the thing it measured was a different
    # shape on each. Run alone it returns 4.0 on all four.
    #
    # MifProbe sits at x=32 where nothing else is built, and NOTHING mutates it. The mutating ops
    # below keep the shared Cube, which is what it is for.
    "face_info": {"object": "MifProbe"},
    "ray_cast": {"origin": [32, 0, 5], "direction": [0, 0, -1]},
    "closest_point_on_mesh": {"object": "MifProbe", "point": [32, 0, 5]},
    "uv_info": {"object": "MifProbe"},
    "mesh_stats": {"object": "MifProbe"},
    "select_faces": {"object": "Cube", "axis": "Z"},
    "objects_overlap": {"a": "Cube", "b": "MifCutter"},
    "set_shading": {"object": "Cube", "smooth": True},
    "bisect_plane": {"object": "Cube", "planeCo": [0, 0, 0], "axis": "Z"},
    "set_vertex_color": {"object": "Cube", "name": "MifWear", "color": [1, 0, 0]},
    "rename_object": {"object": "MifSweepCube", "to": "MifRenamed"},
    "uv_unwrap": {"object": "Cube"},
    # MifCutter, NOT MifGrid - a grid is COPLANAR and the hull audit correctly refuses it with
    # "4 boundary edge(s)", which is the audit working and a useless payload. A collision hull
    # needs a solid.
    "create_collision_hull": {"object": "MifCutter", "index": 3},
    "list_modifiers": {"object": "MifSpare"},
    "list_vertex_groups": {"object": "Cube"},
    "list_shape_keys": {"object": "MifKeys"},
    "list_constraints": {"object": "MifSpare"},
    "list_animation_data": {"object": "Cube"},
    "list_keyframes": {"object": "Cube"},
    "list_custom_properties": {"object": "Cube"},
    "list_particles": {"object": "Cube"},
    "list_bones": {"object": "MifRig"},
    "set_object_visibility": {"object": "Cube", "hideRender": False},
    "set_light": {"object": "Light", "energy": 100.0},
    "set_camera": {"object": "Camera", "lens": 50.0},
    # TARGET IS A NAME, NOT A VECTOR - "'target' must be str, got list" is what the first guess got.
    "aim_object": {"object": "Camera", "target": "Cube"},
    "set_light_ies": {"object": "Light", "clear": True},
    "set_light_linking": {"object": "Light", "clearReceivers": True},
    # ONLY THE UNIVERSAL KEYS - contact shadows, cycles.cast_shadow and the jitter
    # group each exist on some builds and not others, and the op correctly REFUSES
    # what a build lacks. A payload naming them would test the refusal rather than
    # the write, on whichever builds happen to lack them.
    "set_light_shadow": {"object": "Light", "enabled": True, "softSize": 0.3},
    # THE CAMERA MUST BE PANO FIRST - the op refuses on a PERSP camera because the settings
    # "would be stored and never used", which is the op working. A fixture retypes MifPano.
    "set_camera_panorama": {"object": "MifPano", "panoramaType": "EQUIRECTANGULAR"},
    "create_primitive": {"kind": "cube", "name": "MifSweepCube"},
    "create_light": {"type": "POINT"},
    "create_camera": {},
    "create_empty": {},
    "create_text": {"body": "Mif"},
    "create_curve": {"points": [[0, 0, 0], [1, 0, 0]]},
    "create_armature": {"name": "MifRig2",
                        "bones": [{"name": "root", "head": [0, 0, 0], "tail": [0, 0, 1]}]},
    "create_collection": {"name": "MifSweepColl"},
    "link_objects": {"collection": "MifSweepColl", "object": "Cube"},
    "unlink_objects": {"collection": "MifMatrixColl", "object": "MifSpare",
                       "allowOrphans": True},
    "set_collection_visibility": {"collection": "MifMatrixColl", "hideRender": True},
    "create_view_layer": {"name": "MifMatrixVL"},
    "set_view_layer": {"enablePasses": ["z"]},
    "set_world": {"strength": 0.5},
    "set_compositing": {"enabled": True},
    "compositor_info": {},
    "set_render_settings": {"samples": 4},
    "set_color_management": {"exposure": 0.0},
    "add_constraint": {"object": "Cube", "type": "TRACK_TO", "target": "Camera"},
    "remove_constraint": {"object": "MifSpare", "constraintName": "Track To"},
    "add_modifier": {"object": "Cube", "type": "SUBSURF"},
    "remove_modifier": {"object": "MifSpare", "modifier": "Subsurf"},
    "apply_modifier": {"object": "MifApply", "modifier": "Subsurf"},
    # THE ANIMATION FAMILY, all of which need the f-curve the fixtures lay down first.
    "set_keyframe": {"object": "Cube", "location": [0, 0, 1], "frame": 20},
    # FRAME 1, NOT 10. bake_to_keyframes runs earlier alphabetically and rekeys Cube across
    # 1-5, so the frame-10 key the fixture laid down is gone by the time this runs.
    "delete_keyframe": {"object": "Cube", "path": "location", "frame": 1},
    "edit_fcurve": {"object": "Cube", "path": "location", "index": 2,
                    "extrapolation": "LINEAR"},
    "move_keyframes": {"object": "Cube", "path": "location", "offset": 2},
    "add_fcurve_modifier": {"object": "Cube", "path": "location", "index": 2, "type": "CYCLES"},
    "evaluate_at_frame": {"object": "Cube", "paths": ["location"], "frame": 5},
    # ITS OWN ANIMATED OBJECT, because neither of the two obvious fixtures was a real test.
    #
    # Pointed at the shared Cube it reported motionPreserved:False, maxPositionError 0.009362, on
    # every build - and 4.4 flipped to True on about one run in three. By the time the sweep reaches
    # 'b', Cube is carrying a TRACK_TO constraint, an ACTIVE rigid body, a SUBSURF, keyframes, a
    # CYCLES f-curve modifier and a driver, all piled on by earlier ops, and a live rigid body is
    # not deterministic. Sixth instance of the shared-Cube problem.
    #
    # Run in ISOLATION it reported motionPreserved:True with error 0.0 - and that was VACUOUS, not
    # a pass: without the sweep, nothing has keyed Cube at all, so the op baked a static object and
    # trivially preserved its lack of motion. A green that cannot fail.
    #
    # MifBake is keyed in FIXTURES, moves 4 units over 4 frames, and nothing else touches it. Now
    # motionPreserved has something to be wrong about.
    "bake_to_keyframes": {"object": "MifBake", "frameStart": 1, "frameEnd": 5},
    "add_driver": {"object": "Cube", "path": "scale", "index": 0, "expression": "1.0"},
    "remove_driver": {"object": "Cube", "path": "scale", "index": 0},
    "add_nla_strip": {"object": "MifAnim", "action": "MifMatrixAction"},
    # ITS OWN OBJECT. Pointed at Cube this REPLACED the action holding the f-curve fixture, so
    # delete_keyframe, edit_fcurve and move_keyframes - all later alphabetically - then refused
    # with "no fcurve". The same shape as join_objects eating MifSpare, one family over.
    "assign_action": {"object": "MifAnim", "action": "MifMatrixAction"},
    "set_frame_range": {"start": 1, "end": 10},
    "create_action": {"name": "MifMatrixAction"},
    "list_actions": {},
    "set_marker": {"name": "MifMark2", "frame": 7},
    "list_markers": {},
    "add_particles": {"object": "Cube", "count": 10},
    "add_rigid_body": {"object": "Cube", "type": "ACTIVE"},
    # CLOTH REFUSES 8 VERTICES - "a quad has nothing to bend" - so it gets the grid.
    "add_cloth": {"object": "MifGrid"},
    "add_collision": {"object": "Cube"},
    "physics_info": {},
    # 32x32 AT ONE SAMPLE, to a throwaway. The postcondition worth reaching is wroteFile,
    # which is stat'd off disk because bpy.ops.render.render() returns FINISHED whether or
    # not a file appeared.
    "render_still": {"filePath": "{TMP}/mif_still.png", "resolutionX": 32,
                     "resolutionY": 32, "samples": 1},
    "create_material": {"name": "MifSweepMat"},
    "set_material_properties": {"material": "MifMatrixMat", "baseColor": [1, 0, 0]},
    "describe_material": {"material": "MifMatrixMat"},
    # allowResize, because CHANGING THE COUNT re-indexes every polygon material_index and the op
    # refuses to do that silently - which is the op working, not a payload to route around.
    "set_material_slots": {"object": "MifSpare", "slots": ["MifMatrixMat"], "allowResize": True},
    "assign_material_to_faces": {"object": "Cube", "faces": [0], "slot": 0},
    "create_node_group": {"name": "MifSweepGroup"},
    # AN OBJECT SOCKET, ON PURPOSE. Until 2026-09-04 every non-list socket value went through
    # float(), so a datablock socket could not be written at all and this payload would have
    # refused. The value comparison watches inputsApplied, so the pointer resolving to a real
    # object is checked on every build every run.
    "add_group_node": {"group": "MifMatrixGroup", "type": "GeometryNodeObjectInfo",
                       "inputs": {"Object": "MifProbe"}},
    "add_group_interface": {"group": "MifMatrixGroup", "name": "Amount",
                            "socketType": "NodeSocketFloat"},
    "link_group_nodes": {"group": "MifMatrixGroup", "fromNode": "MifNodeA",
                         "fromSocket": "Geometry", "toNode": "MifNodeB",
                         "toSocket": "Geometry"},
    "list_group_nodes": {"group": "MifMatrixGroup"},
    "assign_node_group": {"object": "MifSpare", "group": "MifMatrixGroup"},
    "set_viewport_shading": {"shading": "SOLID"},
    "transform_object": {"object": "Cube", "location": [1, 0, 0]},
    "apply_transform": {"object": "Cube"},
    # mode, NOT "to" - the accept list is location, mode, name, object.
    "set_origin": {"object": "Cube", "mode": "geometry"},
    # EVERY STEP OFF IS REFUSED outright: "clean_mesh was asked to do nothing".
    "clean_mesh": {"object": "Cube", "recalcNormals": True},
    "decimate_mesh": {"object": "MifSpare", "ratio": 0.5},
    # offset, NOT width, and a selector is required.
    "bevel_edges": {"object": "MifSpare", "allEdges": True, "offset": 0.02, "segments": 2},
    "select_edges": {"object": "Cube", "allEdges": True},
    "extrude_skirt": {"object": "MifGrid", "boundaryOnly": True, "depth": 0.1},
    "separate_mesh": {"object": "MifSpare", "mode": "LOOSE"},
    # solver:"fast" ON PURPOSE. It is the name that stopped existing at 5.0, where the enum
    # became FLOAT, EXACT, MANIFOLD - so this payload raised TypeError there and left a live
    # BOOLEAN modifier on the target. The value comparison now watches the solver field, so
    # the alias resolving to FAST on 3.6/4.2/4.4 and FLOAT on 5.0 is checked every run.
    "boolean_op": {"target": "MifBoolA", "cutter": "MifBoolB", "operation": "DIFFERENCE",
                   "deleteCutter": False, "solver": "fast"},
    # ITS OWN THROWAWAY. Pointed at MifSpare, this MERGED the shared fixture into Cube and every
    # op after it alphabetically that referenced MifSpare - list_modifiers, remove_modifier,
    # separate_mesh, set_material_slots, unlink_objects, list_constraints - then refused for a
    # missing object. A mutating sweep needs throwaways of its own, not shared ones.
    "join_objects": {"target": "MifJoinA", "objects": ["MifJoinB"]},
    "transfer_weights": {"source": "MifCutter", "destination": "Cube"},
    "set_vertex_weights": {"object": "Cube", "group": "MifSweepGroup",
                           "vertices": [0], "weight": 0.25},
    "add_shape_key": {"object": "MifCutter", "name": "MifSweepKey"},
    "normalize_weights": {"object": "Cube"},
    "rename_bones": {"object": "MifRig", "map": {"tip": "tip_renamed"}},
    "set_bone_pose": {"object": "MifRig", "bone": "root", "location": [0, 0, 0.1]},
    "set_shape_key": {"object": "MifKeys", "shapeKey": "MifDent", "value": 0.5},
    "set_custom_property": {"object": "Cube", "key": "mif_probe2", "value": 2},
    "list_collections": {},
    "list_view_layers": {},
    "world_info": {},
    "render_info": {},
    "render_status": {},
    "file_info": {},
    "scene_info": {},
    "list_objects": {},
    "list_lights": {},
    "list_cameras": {},
    "list_materials": {},
    "frame_viewport": {},
    "set_viewport_view": {"azimuth": 45.0, "elevation": 30.0, "distance": 10.0},
    "ping": {},
}

# FIXTURES, RUN BEFORE THE SWEEP AND IN THIS ORDER.
#
# THE SWEEP IS ALPHABETICAL, which is fine for finding drift and useless for building state:
# add_group_node runs before create_node_group, so it refused with "no node group named ..." on
# every build and its bpy calls were never reached. Same for every op needing an armature, a second
# mesh, an f-curve or a material slot. Forty ops were refused at the door for want of a fixture,
# and an op refused at the door hides an API break exactly as well as no test at all - which is not
# hypothetical, because the compositor bug lived PAST the first guard.
#
# Order matters here and nowhere else in this file. A failure is recorded and does not stop the run:
# a fixture that cannot be built on one version is itself worth seeing, and stopping would turn one
# missing prerequisite into a blank report.
# RUN AFTER THE SWEEP, on throwaways of their own. The DESTRUCTIVE ops were skipped because they
# delete what later ops need - true while they ran inside an alphabetical sweep, and not a reason to
# leave them untested. They have real postconditions (did the thing actually go, and what did it
# take with it) and had never been exercised on any build.
#
# clear_scene runs LAST of all, because it is the one that empties everything.
TEARDOWN = [
    ("create_primitive", {"kind": "cube", "name": "MifDoomed", "location": [28, 0, 0]}),
    ("create_collection", {"name": "MifDoomedColl"}),
    ("create_view_layer", {"name": "MifDoomedVL"}),
    ("delete_object", {"object": "MifDoomed"}),
    ("delete_collection", {"collection": "MifDoomedColl"}),
    ("delete_view_layer", {"name": "MifDoomedVL"}),
    ("bake_physics", {"start": 1, "end": 2}),
    ("clear_scene", {}),
    # THE ROUND TRIP, and it has to be last: open_file replaces bpy.data wholesale, so
    # anything after it would be looking at a different scene. save_file wrote this at
    # 's' during the sweep, and until something reads it back the only thing proven is
    # that a write did not raise. discardUnsaved is REQUIRED rather than tidy -
    # bpy.data.is_dirty is always True under --background, so the guard always fires.
    ("open_file", {"filepath": "{TMP}/mif_rt.blend", "discardUnsaved": True}),
]

FIXTURES = [
    ("create_node_group", {"name": "MifMatrixGroup"}),
    ("add_group_node", {"group": "MifMatrixGroup", "type": "GeometryNodeSetPosition",
                        "name": "MifNodeA"}),
    ("add_group_node", {"group": "MifMatrixGroup", "type": "GeometryNodeSetPosition",
                        "name": "MifNodeB"}),
    ("create_armature", {"name": "MifRig",
                         "bones": [{"name": "root", "head": [0, 0, 0], "tail": [0, 0, 1]},
                                   {"name": "tip", "head": [0, 0, 1], "tail": [0, 0, 2],
                                    "parent": "root"}]}),
    # A SECOND MESH, for the ops that need something to combine with - and a GRID rather than a cube
    # for cloth, which refuses 8 vertices outright: "a quad has nothing to bend".
    ("create_primitive", {"kind": "cube", "name": "MifCutter", "location": [0.5, 0.5, 0.5]}),
    ("create_primitive", {"kind": "cube", "name": "MifSpare", "location": [4, 0, 0]}),
    # READ ONLY, AND NOTHING BELOW MAY TOUCH IT. The query ops measure this and only
    # this, so their numbers mean the same thing on every build.
    ("create_primitive", {"kind": "cube", "name": "MifProbe", "location": [32, 0, 0]}),
    ("create_primitive", {"kind": "grid", "name": "MifGrid", "location": [0, 4, 0]}),
    ("create_material", {"name": "MifMatrixMat"}),
    ("set_material_slots", {"object": "Cube", "slots": ["MifMatrixMat"]}),
    ("create_collection", {"name": "MifMatrixColl"}),
    ("link_objects", {"collection": "MifMatrixColl", "object": "MifSpare"}),
    # AN F-CURVE, without which the whole animation-editing family refuses for want of one.
    ("set_keyframe", {"object": "Cube", "location": [0, 0, 0], "frame": 1}),
    ("set_keyframe", {"object": "Cube", "location": [0, 0, 2], "frame": 10}),
    ("set_marker", {"name": "MifMark", "frame": 5}),
    ("add_modifier", {"object": "MifSpare", "type": "SUBSURF"}),
    ("add_constraint", {"object": "MifSpare", "type": "TRACK_TO", "target": "Camera"}),
    ("set_custom_property", {"object": "Cube", "key": "mif_probe", "value": 1}),
    # THE THREE OPS THE MATRIX COULD NOT REACH needed these, and nothing could make
    # either until set_vertex_weights and add_shape_key existed.
    ("set_vertex_weights", {"object": "Cube", "group": "MifWeights",
                            "vertices": [0, 1, 2, 3], "weight": 0.5}),
    # ALL EIGHT, not two. A partially weighted source makes transfer_weights produce a group with
    # every weight at zero - which the op now refuses, correctly - so a half-weighted fixture would
    # test the refusal rather than the transfer.
    ("set_vertex_weights", {"object": "MifCutter", "group": "MifWeights",
                            "vertices": [0, 1, 2, 3, 4, 5, 6, 7], "weight": 1.0}),
    # ON THEIR OWN OBJECT. A boolean modifier CANNOT be applied to a mesh with shape keys -
    # Blender refuses outright - and boolean_op runs after add_shape_key alphabetically, so
    # keying Cube made boolean_op unreachable. Third fixture collision of the same kind.
    ("create_primitive", {"kind": "cube", "name": "MifKeys", "location": [20, 0, 0]}),
    ("add_shape_key", {"object": "MifKeys", "name": "MifBasis"}),
    ("add_shape_key", {"object": "MifKeys", "name": "MifDent"}),
    ("create_primitive", {"kind": "cube", "name": "MifBoolA", "location": [24, 0, 0]}),
    # OFFSET ON ALL THREE AXES, and that is the whole point of the numbers. At [24.5, 0, 0]
    # the two unit cubes share EXACTLY COPLANAR faces in y and z - the degenerate case every
    # boolean solver is unstable on. Blender 5.0's FLOAT solver returned 12 faces on one run
    # and 14 on the next from identical input, which made the value comparison flaky, and a
    # check that goes red at random is worse than no check. The fixture was degenerate from
    # the day it was written; nothing looked at the OUTPUT until now.
    ("create_primitive", {"kind": "cube", "name": "MifBoolB",
                          "location": [24.53, 0.31, 0.22]}),
    ("create_action", {"name": "MifMatrixAction"}),
    ("create_primitive", {"kind": "cube", "name": "MifJoinA", "location": [8, 0, 0]}),
    ("create_primitive", {"kind": "cube", "name": "MifJoinB", "location": [8.5, 0, 0]}),
    ("create_primitive", {"kind": "cube", "name": "MifApply", "location": [12, 0, 0]}),
    ("add_modifier", {"object": "MifApply", "type": "SUBSURF"}),
    ("create_camera", {"name": "MifPano"}),
    ("create_primitive", {"kind": "cube", "name": "MifAnim", "location": [16, 0, 0]}),
    # KEYED HERE so bake_to_keyframes has real motion to reproduce. Two keys four frames
    # apart and nothing else driving it - no constraint, no rigid body, no modifier.
    ("create_primitive", {"kind": "cube", "name": "MifBake", "location": [36, 0, 0]}),
    ("set_keyframe", {"object": "MifBake", "location": [36, 0, 0], "frame": 1}),
    ("set_keyframe", {"object": "MifBake", "location": [36, 0, 4], "frame": 5}),
    ("set_camera", {"object": "MifPano", "type": "PANO"}),
]

# Ops deliberately NOT run, with the reason. Each would leave the throwaway process doing something
# slow or pointless rather than testing an API.
# {TMP} IN A PAYLOAD is replaced inside Blender with a per-run temp directory. That is what lets
# the export/import ROUND TRIP be tested at all - and it is the actual Unreal path, so leaving it
# untested because "it writes a file" was a weaker reason than it looked once this became a
# throwaway --factory-startup process that touches nothing.
#
# ALPHABETICAL ORDER MAKES THE ROUND TRIP WORK: export_mesh and export_scene run at 'e', before
# import_mesh and import_scene at 'i', so the files exist by the time they are read.
SKIP = {
    "run_python": "executes arbitrary code - nothing to learn and everything to go wrong",
    "render_animation": "spawns a SECOND Blender per version",
    # THESE FOUR STAY IN SKIP so the ALPHABETICAL sweep does not run them - clear_scene sits at
    # 'c' and emptied the scene before almost every other op, which took reach from 124 to 70 the
    # moment they were merely un-skipped. They are run by TEARDOWN instead, after the sweep, on
    # throwaways of their own, and the teardown overwrites this 'skipped' status with the real one.
    "delete_object": "destructive - run by TEARDOWN after the sweep instead",
    "delete_collection": "destructive - run by TEARDOWN after the sweep instead",
    "delete_view_layer": "destructive - run by TEARDOWN after the sweep instead",
    "clear_scene": "empties everything - run LAST by TEARDOWN",
    # IT MOVES THE SHARED CUBE. add_rigid_body marks Cube ACTIVE at 'a', so baking at
    # 'b' drops it under gravity and every later op reading Cube's geometry sees a
    # fallen one. closest_point_on_mesh went from an exact 4.0 to 4.042868 on two
    # builds and 2.948219 on the other two - a GEOMETRY QUERY disagreeing across
    # versions for no reason but this. Fourth time a mutating op has been put in the
    # sweep beside the fixtures it wrecks; TEARDOWN is where those go.
    "bake_physics": "moves the shared Cube under gravity - run by TEARDOWN instead",
    # SAME TREATMENT AS THE DELETES, and for the same reason. Taking it out of SKIP
    # entirely put it into the ALPHABETICAL sweep at 'o', where it ran with no payload and
    # refused for a missing filepath - which then masked the teardown result. It belongs in
    # both places: skipped here so the sweep leaves it alone, run by TEARDOWN with a real
    # file to open.
    "open_file": "replaces the scene wholesale - run LAST of all by TEARDOWN instead",
    # STILL SKIPPED, and the reason is a real fixture rather than the cost: a bake
    # needs a material carrying an ACTIVE image-texture node, which no op here can
    # build. Without one bpy.ops.object.bake returns FINISHED over an untouched
    # image - the exact silent success that op is arranged around - so a payload
    # that skipped the fixture would test the failure it exists to catch.
    "bake_texture": "needs a material with an ACTIVE image texture node - no op here can build one",
    "gen_asset": "reaches an external generator over the network",
    "gen_image": "reaches an external generator over the network",
    "gen_mesh": "reaches an external generator over the network",
    "gen_texture": "reaches an external generator over the network",
    "gen_status": "reaches an external generator over the network",
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

import os, tempfile
_TMP = tempfile.mkdtemp(prefix="mifmatrix_").replace("\\", "/")

def _sub(value):
    if isinstance(value, str):
        return value.replace("{TMP}", _TMP)
    if isinstance(value, list):
        return [_sub(v) for v in value]
    if isinstance(value, dict):
        return dict((k, _sub(v)) for k, v in value.items())
    return value

# WHAT A RESULT IS REDUCED TO BEFORE IT CROSSES THE PROCESS BOUNDARY.
#
# The whole payload is too much - names, coordinates and paths differ per run for reasons that are
# nobody's bug - and the status alone was too little, which is the hole this closes: uv_unwrap
# returned ok on all four builds while producing DIFFERENT UVs, and the only reason anybody found
# out was a fingerprint hand-rolled inside that one op. Nothing here kept enough to notice.
#
# So: scalar leaves by dotted path, lists reduced to their LENGTH, floats rounded, and the two
# things that legitimately differ every run normalised out - this build's version string and the
# per-run temp directory. Everything left is a value that has no business changing between builds.
# FIELDS WITH NO CROSS-BUILD MEANING, dropped rather than baselined. A process id, a wall-clock
# timing and the interpreter version differ every run by construction - baselining them would park
# three permanent entries in the accepted list that say nothing and can never be reviewed usefully.
# Everything else stays, including file sizes, which differ for real reasons worth accepting once.
_NOISE = ("pid", "python", "elapsedSeconds")

def _digest(value, prefix="", into=None):
    if into is None:
        into = {}
    if prefix and prefix.rsplit(".", 1)[-1] in _NOISE:
        return into
    if isinstance(value, dict):
        for k, v in value.items():
            _digest(v, ("%%s.%%s" %% (prefix, k)) if prefix else str(k), into)
    elif isinstance(value, (list, tuple)):
        into[prefix + "[]"] = len(value)
    elif isinstance(value, float):
        into[prefix] = round(value, 6)
    elif isinstance(value, str):
        s = value.replace(_TMP, "<tmp>").replace(_TMP.replace("/", os.sep), "<tmp>")
        if s == bpy.app.version_string:
            s = "<version>"
        into[prefix] = s[:120]
    elif isinstance(value, (bool, int)) or value is None:
        into[prefix] = value
    else:
        into[prefix] = type(value).__name__
    return into

payloads = _sub(json.loads(r"""%(payloads)s"""))
skip = json.loads(r"""%(skip)s""")
only = json.loads(r"""%(only)s""")
fixtures = _sub(json.loads(r"""%(fixtures)s"""))

# FIXTURES FIRST, IN ORDER. The sweep below is alphabetical, which builds no state - add_group_node
# runs before create_node_group ever makes a group. A failure here is recorded and does NOT stop the
# run: a fixture that cannot be built on one build is itself worth seeing, and stopping would turn
# one missing prerequisite into a blank report.
out["fixtures"] = []
for fname, fparams in fixtures:
    if fname not in table:
        out["fixtures"].append({"op": fname, "status": "absent"})
        continue
    try:
        table[fname](dict(fparams))
        out["fixtures"].append({"op": fname, "status": "ok"})
    except MifOpError as exc:
        out["fixtures"].append({"op": fname, "status": "refused", "detail": str(exc)[:200]})
    except Exception as exc:
        out["fixtures"].append({"op": fname, "status": "RAISED",
                                "detail": "%%s: %%s" %% (type(exc).__name__, str(exc)[:200])})

for name in sorted(table):
    if only and name not in only:
        continue
    if name in skip:
        out["results"][name] = {"status": "skipped", "detail": skip[name]}
        continue
    try:
        _r = table[name](dict(payloads.get(name, {})))
        out["results"][name] = {"status": "ok", "value": _digest(_r)}
    except MifOpError as exc:
        out["results"][name] = {"status": "refused", "detail": str(exc)[:220]}
    except Exception as exc:
        out["results"][name] = {"status": "RAISED",
                                "detail": "%%s: %%s" %% (type(exc).__name__, str(exc)[:220])}
# TEARDOWN, after every op has been swept. Recorded in results like anything else, so a delete that
# raises is a finding rather than a quiet end to the run.
teardown = _sub(json.loads(r"""%(teardown)s"""))
for tname, tparams in teardown:
    if tname not in table or (only and tname not in only):
        continue
    try:
        table[tname](dict(tparams))
        out["results"].setdefault(tname, {"status": "ok"})
        if out["results"][tname].get("status") == "skipped":
            out["results"][tname] = {"status": "ok"}
    except MifOpError as exc:
        out["results"][tname] = {"status": "refused", "detail": str(exc)[:220]}
    except Exception as exc:
        out["results"][tname] = {"status": "RAISED",
                                 "detail": "%%s: %%s" %% (type(exc).__name__, str(exc)[:220])}

print("MIFMATRIX" + json.dumps(out))
'''


# EXCEPTION TYPES THAT SHOULD NEVER APPEAR INSIDE A REFUSAL MESSAGE.
#
# A refusal is not a finding - unless it is a raw exception WEARING a refusal's clothes. add_driver
# called driver_add(data_path=..., index=...) and driver_add takes NO keyword arguments on any
# Blender from 3.6 to 5.0, so the op had never worked on any build. It shipped green because the
# TypeError was caught and re-raised as a MifOpError reading "Blender refused to add a driver", and
# every check that asks only "did it refuse politely" agreed with it.
#
# That is the same shape as the compositor: correct-looking at every layer that was inspected. The
# catch is not wrong - a caller SHOULD get a sentence rather than a traceback - but a message
# carrying a Python exception type is evidence about the ADDON, not about the caller's arguments.
SUSPECT_IN_REFUSAL = ("TypeError", "AttributeError", "KeyError", "IndexError", "NameError",
                      "ValueError:", "takes no keyword arguments", "unexpected keyword argument",
                      "object has no attribute", "is not defined")


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
        "fixtures": json.dumps(FIXTURES),
        "teardown": json.dumps(TEARDOWN),
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
    # WHY THE REFUSAL TEXT IS WORTH A FLAG: the ops that refuse on every build are the
    # untested ones, and the message says exactly which payload key is missing. Guessing
    # a second time is how the first twelve payloads came out wrong.
    ap.add_argument("--show-refusals", action="store_true",
                    help="print the refusal message for ops refused on every build")
    ap.add_argument("--update-value-baseline", action="store_true",
                    help="accept the current cross-build value differences")
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
    # SUSPECT REFUSALS - a refusal quoting a Python exception is the addon meeting an API that is
    # not what it expected, dressed as a polite decline. See SUSPECT_IN_REFUSAL.
    suspect = []
    for op in ops:
        for r in reports:
            e = r.get("results", {}).get(op, {})
            if e.get("status") != "refused":
                continue
            detail = e.get("detail", "")
            hit = next((s for s in SUSPECT_IN_REFUSAL if s in detail), None)
            if hit:
                suspect.append((op, r.get("version", "?"), hit, detail))
                break
    if suspect:
        print("")
        print("SUSPECT REFUSALS - a refusal quoting a Python exception is a raw break wearing a")
        print("refusal's clothes. add_driver had NEVER worked on any build and looked like this:")
        for op, v, hit, detail in suspect:
            print("  %-26s %-12s [%s] %s" % (op, v, hit, detail[:90]))

    bad_fixtures = []
    for r in reports:
        for f in r.get("fixtures", []):
            if f.get("status") in ("RAISED", "refused", "absent"):
                bad_fixtures.append((r.get("version", "?"), f.get("op"), f.get("status"),
                                     f.get("detail", "")))
    if bad_fixtures:
        print("")
        print("FIXTURES THAT DID NOT BUILD - every op depending on one of these then refuses, so")
        print("read these BEFORE reading the reach numbers below:")
        for v, op, status, detail in bad_fixtures:
            print("  %-12s %-24s %-8s %s" % (v, op, status, detail[:90]))
    # VALUE DRIFT. The status comparison above asks whether an op still SUCCEEDS on every build.
    # This asks whether it still succeeds with the SAME ANSWER, which is a different question and
    # the one that was going unasked: uv_unwrap returned ok on all four builds while producing
    # different UVs, and the only reason anybody noticed was a fingerprint hand-rolled inside that
    # one op. Nothing in this harness kept enough of a result to compare.
    #
    # RATCHETED ON (op, field), NOT on the values. Plenty of fields differ for reasons that are
    # Blender's history rather than anybody's bug - the Principled socket renames at 4.0, Filmic
    # becoming AgX, BLENDER_EEVEE becoming BLENDER_EEVEE_NEXT and back. Those are accepted once, by
    # a person, and the finding is a field that starts differing when it did not before. Baselining
    # the VALUES instead would go red on every legitimate move, which is the opposite of a ratchet.
    value_diffs = []
    for op in ops:
        per = {}
        for r in reports:
            entry = r.get("results", {}).get(op, {})
            if entry.get("status") != "ok":
                per = None
                break
            per[r.get("version")] = entry.get("value") or {}
        if not per or len(per) < len(reports):
            continue
        for field in sorted({k for d in per.values() for k in d}):
            seen = {v: d.get(field, "<absent>") for v, d in per.items()}
            if len({json.dumps(x, sort_keys=True, default=str) for x in seen.values()}) > 1:
                value_diffs.append((op, field, seen))

    try:
        with io.open(VALUE_BASELINE, encoding="utf-8") as fh:
            accepted = {tuple(row) for row in json.load(fh)}
    except Exception:
        accepted = set()
    new_diffs = [d for d in value_diffs if (d[0], d[1]) not in accepted]

    if args.update_value_baseline:
        with io.open(VALUE_BASELINE, "w", encoding="utf-8", newline="\r\n") as fh:
            json.dump(sorted([op, field] for op, field, _ in value_diffs), fh, indent=1)
        print("\nvalue baseline updated: %d accepted (op, field) pair(s)" % len(value_diffs))

    print("")
    if new_diffs:
        print("VALUE DRIFT - these ops still SUCCEED on every build and no longer AGREE:")
        for op, field, seen in new_diffs:
            print("  %-26s %s" % (op, field))
            print("      " + "   ".join("%s=%s" % (v, str(x)[:26]) for v, x in sorted(seen.items())))
        print("")
        print("  A field that starts differing is either a Blender change worth knowing about or a")
        print("  bug in the op. Read it, then accept it with --update-value-baseline.")
    else:
        print("no NEW cross-build value drift (%d known difference(s) accepted in %s)."
              % (len(value_diffs), os.path.basename(VALUE_BASELINE)))

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
    if refused_everywhere and args.show_refusals:
        print("")
        print("  WHY EACH REFUSED (first build reporting one):")
        for op in refused_everywhere:
            detail = ""
            for r in reports:
                d = r.get("results", {}).get(op, {})
                if d.get("status") == "refused":
                    detail = d.get("detail", "")
                    break
            print("    %-26s %s" % (op, detail[:150]))
    if skipped:
        print("")
        print("  DELIBERATELY NOT RUN - %d op(s), each with a reason in SKIP: %s"
              % (len(skipped), ", ".join(skipped)))
    print("")
    print("A refusal is NOT a finding - an op declining because the default scene has no armature")
    print("is the op working. Raw exceptions and divergences are the findings; REACH is how much of")
    print("the table those findings actually cover.")
    return 1 if (raised or fatal or suspect or new_diffs) else 0


if __name__ == "__main__":
    sys.exit(main())
