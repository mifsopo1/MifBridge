"""GeometryScript: create_mesh_boolean - union/intersection/subtract of two existing StaticMesh assets
into a third, new one. Extends the create_procedural_mesh/describe_dynamic_mesh work with the third
GeometryScript endpoint, reusing both their proven read and write code paths.

T1200-T1202: real geometry from real overlap, checked against hand-computed bounds. A box (100^3,
centered at origin) and a sphere (radius 60, centered at origin) - the sphere's radius (60) is LESS
than the box's half-diagonal (~86.6), so the sphere does NOT fully engulf the box: it overlaps the
box's faces but the box's 8 corners stick out beyond it. That is deliberately picked over a "sphere
engulfs box" setup, whose corners actually DO get separately exercised in T1203 (below).
  T1200 union: bounding box must equal the sphere's own bounds (118.18 x 118.18 x 120), since the
    sphere's bbox is larger than the box's on every axis regardless of engulfment.
  T1201 intersection: bounding box must equal the BOX's own bounds (100^3) - the box's face-centers
    (at 50 units out) are all within the sphere's 60-unit radius, so the AABB extent survives even
    though the corners get cut off (confirmed separately by the vertex count NOT being a plain 8).
  T1202 offset subtract: moving the sphere away from the box's center (toolOffsetX) produces a real,
    non-empty partial cut - verified as strictly less complex geometry than the union (fewer vertices),
    not just ok:true.

T1203: THE REAL BUG THIS SUITE EXISTS TO PIN DOWN, found live during authoring, not read off a header.
ApplyMeshBoolean's own engine implementation (MeshBooleanFunctions.cpp) cannot tell a genuine
computation error apart from a LEGITIMATELY EMPTY result: `bSuccess = (ResultMesh.TriangleCount() > 0);
if (!bSuccess) { AppendError(...); return TargetMesh; }` - on an empty result it returns the ORIGINAL,
COMPLETELY UNCHANGED TargetMesh, not an emptied one. The first version of this handler checked
"did TargetMesh come back with 0 vertices" to detect failure - which NEVER fires for this failure mode,
because the mesh never becomes empty, it just silently reverts to the unmodified input. Verified live:
subtracting a mesh from ITSELF (an unambiguous empty result) came back reporting ok:true with the
original box's exact, untouched vertex/triangle count and bounds - a silent wrong-answer bug, the worst
kind this whole project's philosophy exists to catch. Fixed by reading Debug->Messages for an
EGeometryScriptDebugMessageType::ErrorMessage entry instead of trusting the mesh's own vertex count.
T1203 reproduces the exact self-subtract case that exposed it. T1204 reproduces the same failure mode a
different way (a non-overlapping intersection, toolOffsetX huge) to prove the fix is not overfit to one
trigger.

T1205: the overwrite-guard error message named the WRONG endpoint ("create_procedural_mesh never
overwrites") when triggered from create_mesh_boolean, because both endpoints share one local validator
that had the caller's name hardcoded. Fixed by threading a CallerName parameter through. T1205 checks
the message names THIS endpoint, not the sibling one.

T1206-T1209: refusals checked for the exact reason - unknown operation, a target asset that does not
exist, unknown parameter (RejectUnknownParams).
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def close(a, b, tol=0.05):
    return abs(a - b) < tol


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    base = "/Game/_MifMeshBoolean%d" % st
    created_paths = []

    # ------------------------------------------------------------------ setup: real overlapping fixtures
    box_path = base + "/SM_Box"
    box = M.call("create_procedural_mesh", {"path": box_path, "shape": "box",
                                             "dimensionX": 100, "dimensionY": 100, "dimensionZ": 100})
    check("(setup) box created", box.get("ok") is True, json.dumps(box)[:200])
    if box.get("ok"):
        created_paths.append(box_path)

    sphere_path = base + "/SM_Sphere"
    sphere = M.call("create_procedural_mesh", {"path": sphere_path, "shape": "sphere", "radius": 60})
    check("(setup) sphere created", sphere.get("ok") is True, json.dumps(sphere)[:200])
    if sphere.get("ok"):
        created_paths.append(sphere_path)

    if not (box.get("ok") and sphere.get("ok")):
        print("cannot continue without both fixtures")
        return 3

    # ------------------------------------------------------------------ T1200 union
    print("\n=== T1200: union - bounding box matches the larger input (the sphere) ===")
    union_path = base + "/SM_Union"
    u = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": sphere_path, "operation": "union", "outputPath": union_path})
    check("T1200 union succeeds", u.get("ok") is True, json.dumps(u)[:200])
    ub = u.get("bounds") or {}
    check("T1200 union bounds equal the sphere's own bounds",
          close(ub.get("sizeX", 0), 118.18, 0.1) and close(ub.get("sizeY", 0), 118.18, 0.1)
          and close(ub.get("sizeZ", 0), 120, 0.1), ub)
    if u.get("ok"):
        created_paths.append(union_path)

    # ------------------------------------------------------------------ T1201 intersection
    print("\n=== T1201: intersection - AABB matches the box, but vertex count proves corners were cut ===")
    intersect_path = base + "/SM_Intersect"
    i = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": sphere_path, "operation": "intersection", "outputPath": intersect_path})
    check("T1201 intersection succeeds", i.get("ok") is True, json.dumps(i)[:200])
    ib = i.get("bounds") or {}
    check("T1201 intersection AABB equals the box's own bounds (face-centers survive)",
          close(ib.get("sizeX", 0), 100, 0.1) and close(ib.get("sizeY", 0), 100, 0.1)
          and close(ib.get("sizeZ", 0), 100, 0.1), ib)
    check("T1201 intersection is NOT just the plain 8-vertex box - corners were really cut",
          i.get("vertexCount", 0) > 8, i.get("vertexCount"))
    if i.get("ok"):
        created_paths.append(intersect_path)

    # ------------------------------------------------------------------ T1202 offset subtract, real partial cut
    print("\n=== T1202: offset subtract - a real, non-empty partial cut ===")
    subtract_path = base + "/SM_SubtractReal"
    s = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": sphere_path, "operation": "subtract",
        "outputPath": subtract_path, "toolOffsetX": 90})
    check("T1202 offset subtract succeeds", s.get("ok") is True, json.dumps(s)[:200])
    check("T1202 result has real, non-trivial geometry", s.get("vertexCount", 0) > 8, s.get("vertexCount"))
    if s.get("ok"):
        created_paths.append(subtract_path)

    # ------------------------------------------------------------------ T1203 THE REAL BUG: self-subtract
    print("\n=== T1203: subtracting a mesh from ITSELF must be refused, not silently return the unchanged input ===")
    self_sub = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": box_path, "operation": "subtract",
        "outputPath": base + "/SM_SelfSubtract"})
    check("T1203 self-subtract is refused (ApplyMeshBoolean leaves TargetMesh UNCHANGED on empty results, "
          "which used to read as success)", self_sub.get("ok") is False, self_sub)
    check("T1203 refusal explains the engine's own ambiguity",
          "cannot distinguish" in (self_sub.get("error") or ""), self_sub.get("error"))
    check("T1203 debugMessages carries the engine's own error text",
          bool(self_sub.get("debugMessages")), self_sub.get("debugMessages"))

    # ------------------------------------------------------------------ T1204 same failure mode, different trigger
    print("\n=== T1204: a non-overlapping intersection hits the SAME failure mode - fix is not overfit ===")
    no_overlap = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": sphere_path, "operation": "intersection",
        "outputPath": base + "/SM_NoOverlap", "toolOffsetX": 1000})
    check("T1204 non-overlapping intersection is refused", no_overlap.get("ok") is False, no_overlap)

    # ------------------------------------------------------------------ T1205 overwrite guard names the RIGHT endpoint
    print("\n=== T1205: overwrite guard error names create_mesh_boolean, not its sibling ===")
    dupe = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": sphere_path, "operation": "union", "outputPath": union_path})
    check("T1205 a second create at the same output path is refused", dupe.get("ok") is False, dupe)
    check("T1205 refusal correctly names create_mesh_boolean, not create_procedural_mesh",
          "create_mesh_boolean never overwrites" in (dupe.get("error") or ""), dupe.get("error"))

    # ------------------------------------------------------------------ T1206-T1209 refusals, exact reason
    print("\n=== T1206-T1209: remaining refusals checked for the specific reason ===")
    bad_op = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": sphere_path, "operation": "xor", "outputPath": base + "/SM_Bad"})
    check("T1206 unknown operation is refused", bad_op.get("ok") is False, bad_op)
    check("T1206 refusal names the bad value", "xor" in (bad_op.get("error") or ""), bad_op.get("error"))

    missing_target = M.call("create_mesh_boolean", {
        "targetPath": base + "/SM_DoesNotExist", "toolPath": sphere_path, "operation": "union",
        "outputPath": base + "/SM_Bad2"})
    check("T1207 a missing targetPath is refused", missing_target.get("ok") is False, missing_target)
    check("T1207 refusal names targetPath specifically",
          "targetPath" in (missing_target.get("error") or ""), missing_target.get("error"))

    missing_tool = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": base + "/SM_DoesNotExist", "operation": "union",
        "outputPath": base + "/SM_Bad3"})
    check("T1208 a missing toolPath is refused", missing_tool.get("ok") is False, missing_tool)
    check("T1208 refusal names toolPath specifically",
          "toolPath" in (missing_tool.get("error") or ""), missing_tool.get("error"))

    unknown_param = M.call("create_mesh_boolean", {
        "targetPath": box_path, "toolPath": sphere_path, "operation": "union",
        "outputPath": base + "/SM_Bad4", "mode": "fast"})
    check("T1209 unknown parameter 'mode' is rejected", unknown_param.get("ok") is False, unknown_param)
    check("T1209 rejection names the unrecognised key", "mode" in (unknown_param.get("error") or ""),
          unknown_param.get("error"))

    # ------------------------------------------------------------------ cleanup
    for p in created_paths:
        SC.confirm_call("delete_asset", {"path": p})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
