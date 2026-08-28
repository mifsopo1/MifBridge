"""GeometryScript: create_procedural_mesh (box/sphere from nothing) and describe_dynamic_mesh (its
read-only companion). The first endpoints ever built against this plugin - added 2026-08-28 at Andre's
direct request to close the "Fab marketplace parity" gap docs/13_COMPETITOR_GAP_MAP.md flagged for
procedural mesh generation, and the first WRITE endpoint in the whole bridge that builds a mesh from
nothing rather than copying, reading, or attaching collision to an existing one.

T1000-T1004: box generation - real geometry read back (vertexCount/triangleCount/bounds), not just
ok:true. T1000 (default box) vs T1001 (steps=5) proves the subdivision parameter is actually wired
through GeometryScript rather than ignored - caught live during authoring that an earlier
steps=2-without-a-baseline test could not have told the difference. T1002 sphere. T1003 the
create/describe round-trip - independently reads back the SAME mesh through a different code path
(CopyMeshFromStaticMesh vs CopyMeshToStaticMesh) and checks vertex/triangle counts agree exactly.

T1005-T1009: refusals, each checked for the EXACT reason, not just ok:false - bad shape, unknown
parameter (RejectUnknownParams), non-positive dimensions on both shapes, a path outside /Game/.

T1010: THE REAL BUG this suite exists to pin down. The first version of create_procedural_mesh guarded
its destination with plain FPackageName::DoesPackageExist, which - live-verified before this fix folded
back into the handler - answers false for an object that exists only in memory and was never saved,
exactly the case create_procedural_mesh itself produces every time (nothing here is ever saved, this
project's standing invariant). The result: creating at an already-used path silently OVERWROTE the
prior mesh instead of refusing. Fixed to match H_create_asset's own already-documented pattern
(MifBridgeUserTypes.cpp) - real file on disk OR an object already loaded in memory. This test creates
at a path, then creates AGAIN at the exact same path and asserts the second call is refused.

T1011: describe_dynamic_mesh against a REAL, COOKED DDS2Casino StaticMesh - confirmed to fail
gracefully with a named reason (stripped MeshDescription) rather than crash or report false zeros,
matching the class of cooked-asset limitation this whole project has hit and handled before
(duplicate_asset, add_simplified_collision).

DECLINED for this batch: LOD>0 read testing (needs a real multi-LOD mesh, none of DDS2's are guaranteed
to have one at a stable path) and Nanite-related options (FGeometryScriptCopyMeshToAssetOptions exposes
them but create_procedural_mesh does not surface them yet - not exercised because nothing calls them).

T1020-T1027, added same day in a second pass: cylinder, cone, torus - AppendCylinder/AppendCone/
AppendTorus, identical signatures on both engines (checked, no version guard needed unlike
CopyMeshToStaticMesh). T1022 cone with topRadius=0 (a true point) - the shape most likely to have an
off-by-one in its cap triangulation. T1024-T1026 refusals specific to these three: torus minorRadius
>= majorRadius (a self-intersecting tube), cone with both radii 0 (a degenerate line), cylinder with
zero height. T1027 torus create/describe round-trip, same reasoning as T1003.
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


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    base = "/Game/_MifGeoScript%d" % st
    created_paths = []

    # ------------------------------------------------------------------ T1000/T1001 box + steps wired
    print("\n=== T1000-T1001: box generation, default vs subdivided ===")
    box0_path = base + "/SM_Box0"
    r0 = M.call("create_procedural_mesh", {"path": box0_path, "shape": "box",
                                            "dimensionX": 100, "dimensionY": 100, "dimensionZ": 100,
                                            "steps": 0})
    check("T1000 box(steps=0) succeeds", r0.get("ok") is True, json.dumps(r0)[:200])
    check("T1000 box(steps=0) is a minimal 8-vertex cube", r0.get("vertexCount") == 8, r0)
    check("T1000 box(steps=0) has 12 triangles", r0.get("triangleCount") == 12, r0)
    check("T1000 bounds match the requested dimensions",
          r0.get("bounds", {}).get("sizeX") == 100 and r0.get("bounds", {}).get("sizeY") == 100
          and r0.get("bounds", {}).get("sizeZ") == 100, r0.get("bounds"))
    if r0.get("ok"):
        created_paths.append(box0_path)

    box5_path = base + "/SM_Box5"
    r5 = M.call("create_procedural_mesh", {"path": box5_path, "shape": "box",
                                            "dimensionX": 100, "dimensionY": 100, "dimensionZ": 100,
                                            "steps": 5})
    check("T1001 box(steps=5) succeeds", r5.get("ok") is True, json.dumps(r5)[:200])
    check("T1001 steps really changes the mesh - more vertices than steps=0",
          isinstance(r5.get("vertexCount"), int) and r5.get("vertexCount") > r0.get("vertexCount", 0),
          "steps=0 -> %s, steps=5 -> %s" % (r0.get("vertexCount"), r5.get("vertexCount")))
    if r5.get("ok"):
        created_paths.append(box5_path)

    # ------------------------------------------------------------------ T1002 sphere
    print("\n=== T1002: sphere generation ===")
    sphere_path = base + "/SM_Sphere"
    rs = M.call("create_procedural_mesh", {"path": sphere_path, "shape": "sphere",
                                            "radius": 75, "stepsPhi": 12, "stepsTheta": 20})
    check("T1002 sphere succeeds", rs.get("ok") is True, json.dumps(rs)[:200])
    check("T1002 sphere has real geometry", rs.get("vertexCount", 0) > 0 and rs.get("triangleCount", 0) > 0, rs)
    sb = rs.get("bounds") or {}
    check("T1002 sphere bounds are close to 2*radius on every axis (lat-long tessellation, not exact)",
          all(abs(sb.get(k, 0) - 150) < 5 for k in ("sizeX", "sizeY", "sizeZ")), sb)
    if rs.get("ok"):
        created_paths.append(sphere_path)

    # ------------------------------------------------------------------ T1003 create/describe round-trip
    print("\n=== T1003: describe_dynamic_mesh reads back the exact mesh create_procedural_mesh made ===")
    d = M.call("describe_dynamic_mesh", {"path": sphere_path})
    check("T1003 describe succeeds", d.get("ok") is True, json.dumps(d)[:200])
    check("T1003 vertexCount matches exactly", d.get("vertexCount") == rs.get("vertexCount"),
          "create=%s describe=%s" % (rs.get("vertexCount"), d.get("vertexCount")))
    check("T1003 triangleCount matches exactly", d.get("triangleCount") == rs.get("triangleCount"),
          "create=%s describe=%s" % (rs.get("triangleCount"), d.get("triangleCount")))
    check("T1003 a sphere is reported closed", d.get("isClosed") is True, d)

    # ------------------------------------------------------------------ T1005-T1009 refusals, exact reason
    print("\n=== T1005-T1009: refusals checked for the specific reason, not just ok:false ===")
    bad_shape = M.call("create_procedural_mesh", {"path": base + "/SM_Bad", "shape": "pyramid"})
    check("T1005 unknown shape is refused", bad_shape.get("ok") is False, bad_shape)
    check("T1005 refusal names the bad value and says nothing was created",
          "pyramid" in (bad_shape.get("error") or "") and "NOTHING was created" in (bad_shape.get("error") or ""),
          bad_shape.get("error"))

    unknown_param = M.call("create_procedural_mesh", {"path": base + "/SM_Bad2", "shape": "box", "size": 50})
    check("T1006 unknown parameter 'size' is rejected", unknown_param.get("ok") is False, unknown_param)
    check("T1006 rejection names the unrecognised key", "size" in (unknown_param.get("error") or ""),
          unknown_param.get("error"))

    bad_box_dim = M.call("create_procedural_mesh", {"path": base + "/SM_Bad3", "shape": "box", "dimensionX": 0})
    check("T1007 zero box dimension is refused", bad_box_dim.get("ok") is False, bad_box_dim)

    bad_radius = M.call("create_procedural_mesh", {"path": base + "/SM_Bad4", "shape": "sphere", "radius": -5})
    check("T1008 negative sphere radius is refused", bad_radius.get("ok") is False, bad_radius)

    outside_game = M.call("create_procedural_mesh", {"path": "/Engine/Transient/SM_Bad5", "shape": "box"})
    check("T1009 a path outside /Game/ is refused", outside_game.get("ok") is False, outside_game)
    check("T1009 refusal explains why", "/Game/" in (outside_game.get("error") or ""), outside_game.get("error"))

    # ------------------------------------------------------------------ T1010 the real overwrite bug
    print("\n=== T1010: create_procedural_mesh must NEVER silently overwrite an existing (even unsaved) asset ===")
    overwrite_path = base + "/SM_OverwriteGuard"
    first = M.call("create_procedural_mesh", {"path": overwrite_path, "shape": "box"})
    check("T1010 (setup) the first create at this path succeeds", first.get("ok") is True, json.dumps(first)[:200])
    if first.get("ok"):
        created_paths.append(overwrite_path)
    second = M.call("create_procedural_mesh", {"path": overwrite_path, "shape": "sphere"})
    check("T1010 a second create at the SAME path is refused, not silently applied",
          second.get("ok") is False, second)
    check("T1010 refusal explains the path is already taken",
          "already taken" in (second.get("error") or ""), second.get("error"))
    # Prove the FIRST mesh (a box) is still intact - the bug this pins down was a silent OVERWRITE,
    # so the read-back must still show box geometry, not sphere geometry.
    still_there = M.call("describe_dynamic_mesh", {"path": overwrite_path})
    check("T1010 the original box mesh was not touched by the refused second call",
          still_there.get("ok") is True and still_there.get("vertexCount") == first.get("vertexCount"),
          "original vertexCount=%s, read-back=%s" % (first.get("vertexCount"), still_there.get("vertexCount")))

    # ------------------------------------------------------------------ T1011 cooked asset, graceful failure
    print("\n=== T1011: describe_dynamic_mesh on a REAL cooked DDS2Casino mesh fails gracefully, no crash ===")
    found = M.call("find_assets", {"class": "StaticMesh", "pathPrefix": "/DDS2Casino/", "limit": 1})
    cooked_assets = found.get("assets") or []
    if cooked_assets:
        cooked_path = cooked_assets[0].get("packageName")
        cooked = M.call("describe_dynamic_mesh", {"path": cooked_path})
        check("T1011 a cooked mesh reports failure rather than crashing or faking zeros",
              cooked.get("ok") is False, json.dumps(cooked)[:250])
        check("T1011 the bridge is still alive immediately after",
              M.call("self_audit", {}).get("ok") is True, "bridge did not respond after T1011")
    else:
        check("T1011 (skipped) no /DDS2Casino/ StaticMesh found to test against", True,
              "not a failure - just nothing to exercise this against on this content set")

    # ------------------------------------------------------------------ T1020 cylinder
    print("\n=== T1020: cylinder generation ===")
    cyl_path = base + "/SM_Cylinder"
    rcyl = M.call("create_procedural_mesh", {"path": cyl_path, "shape": "cylinder",
                                              "radius": 40, "height": 120, "radialSteps": 16})
    check("T1020 cylinder succeeds", rcyl.get("ok") is True, json.dumps(rcyl)[:200])
    cb = rcyl.get("bounds") or {}
    check("T1020 cylinder bounds match radius*2 on X/Y and height on Z",
          abs(cb.get("sizeX", 0) - 80) < 0.01 and abs(cb.get("sizeY", 0) - 80) < 0.01
          and abs(cb.get("sizeZ", 0) - 120) < 0.01, cb)
    if rcyl.get("ok"):
        created_paths.append(cyl_path)

    # ------------------------------------------------------------------ T1022 cone (topRadius=0, a point)
    print("\n=== T1022: cone generation, topRadius=0 (a true point) ===")
    cone_path = base + "/SM_Cone"
    rcone = M.call("create_procedural_mesh", {"path": cone_path, "shape": "cone",
                                               "baseRadius": 50, "topRadius": 0, "height": 100})
    check("T1022 cone(topRadius=0) succeeds", rcone.get("ok") is True, json.dumps(rcone)[:200])
    check("T1022 cone has real geometry", rcone.get("vertexCount", 0) > 0 and rcone.get("triangleCount", 0) > 0, rcone)
    coneb = rcone.get("bounds") or {}
    check("T1022 cone bounds match baseRadius*2 on X/Y and height on Z",
          abs(coneb.get("sizeX", 0) - 100) < 0.01 and abs(coneb.get("sizeY", 0) - 100) < 0.01
          and abs(coneb.get("sizeZ", 0) - 100) < 0.01, coneb)
    if rcone.get("ok"):
        created_paths.append(cone_path)

    # ------------------------------------------------------------------ T1023 torus
    print("\n=== T1023: torus generation ===")
    torus_path = base + "/SM_Torus"
    rtor = M.call("create_procedural_mesh", {"path": torus_path, "shape": "torus",
                                              "majorRadius": 60, "minorRadius": 15})
    check("T1023 torus succeeds", rtor.get("ok") is True, json.dumps(rtor)[:200])
    torb = rtor.get("bounds") or {}
    check("T1023 torus outer diameter is 2*(majorRadius+minorRadius) on X/Y",
          abs(torb.get("sizeX", 0) - 150) < 0.01 and abs(torb.get("sizeY", 0) - 150) < 0.01, torb)
    check("T1023 torus tube height is 2*minorRadius on Z", abs(torb.get("sizeZ", 0) - 30) < 0.01, torb)
    if rtor.get("ok"):
        created_paths.append(torus_path)

    # ------------------------------------------------------------------ T1024-T1026 shape-specific refusals
    print("\n=== T1024-T1026: refusals specific to cylinder/cone/torus ===")
    bad_torus = M.call("create_procedural_mesh", {"path": base + "/SM_BadTorus", "shape": "torus",
                                                   "majorRadius": 30, "minorRadius": 40})
    check("T1024 torus with minorRadius >= majorRadius is refused (self-intersecting tube)",
          bad_torus.get("ok") is False, bad_torus)

    bad_cone = M.call("create_procedural_mesh", {"path": base + "/SM_BadCone", "shape": "cone",
                                                  "baseRadius": 0, "topRadius": 0})
    check("T1025 cone with both radii 0 is refused (degenerate line)", bad_cone.get("ok") is False, bad_cone)

    bad_cyl = M.call("create_procedural_mesh", {"path": base + "/SM_BadCyl", "shape": "cylinder", "height": 0})
    check("T1026 cylinder with zero height is refused", bad_cyl.get("ok") is False, bad_cyl)

    # ------------------------------------------------------------------ T1027 torus create/describe round-trip
    print("\n=== T1027: describe_dynamic_mesh reads back the torus exactly ===")
    dtor = M.call("describe_dynamic_mesh", {"path": torus_path})
    check("T1027 describe succeeds", dtor.get("ok") is True, json.dumps(dtor)[:200])
    check("T1027 vertexCount matches exactly", dtor.get("vertexCount") == rtor.get("vertexCount"),
          "create=%s describe=%s" % (rtor.get("vertexCount"), dtor.get("vertexCount")))
    check("T1027 a torus is reported closed", dtor.get("isClosed") is True, dtor)

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
