"""World, rendering, physics, particles and geometry-node authoring - the five families added 2026-09-01.

WHY THIS EXISTS, and it is the same reason test_blender_anim does. Five capability families were
added and the full sweep went green over every one of them without calling a single op, because no
suite knew they existed. A green run over unexercised code is the exact failure this repo spends
its time finding, and having just written that sentence into the spec entry it would be poor form
to leave it true here.

WHAT IS DELIBERATELY SMALL. render_still blocks Blender's main thread, so the render here is
160x90 at 4 samples - enough to prove a file reaches disk with real bytes, not enough to slow the
sweep. The same call with production numbers is the same code path.

Usage:  python tools/test_blender_scene.py     # needs a Blender with MifBlender listening
Exit:   0 passed   1 failed   2 SKIPPED, no Blender
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blender_audit_common as B

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))


def main():
    if not B.reachable():
        return B.skip_banner("scene")

    B.call("clear_scene", {})

    # ---------------------------------------------------------------- S100 world
    print("=== S100: set_world, and the two inputs it refuses together ===")
    w = B.call("set_world", {"color": [0.02, 0.03, 0.04], "strength": 0.05})
    check("S100 set_world succeeds", w.get("ok") is not False, json.dumps(w)[:200])
    after = w.get("after") or {}
    check("S100 strength is read back off the shader node", after.get("strength") == 0.05,
          after.get("strength"))
    check("S100 and the colour with it", after.get("color") == [0.02, 0.03, 0.04],
          after.get("color"))
    both = B.call("set_world", {"color": [1, 1, 1], "hdri": "C:/nope/none.exr"})
    check("S100 an hdri AND a colour together is refused - the texture would override the colour",
          both.get("ok") is False, str(both.get("error"))[:150])
    missing = B.call("set_world", {"hdri": "C:/definitely/not/here.exr"})
    check("S100 a missing HDRI file is refused rather than loading a broken image",
          missing.get("ok") is False, str(missing.get("error"))[:150])

    # ---------------------------------------------------------------- S101 geometry nodes
    print("")
    print("=== S101: authoring a node tree - the gap that was not 'attach a modifier' ===")
    g = B.call("create_node_group", {"name": "S_Tree"})
    check("S101 create_node_group succeeds", g.get("ok") is not False, json.dumps(g)[:200])
    check("S101 it comes with Group Input and Output", g.get("nodeCount") == 2, g.get("nodeCount"))
    iface = {i["name"] + "/" + i["inOut"] for i in (g.get("interface") or [])}
    check("S101 and a Geometry socket each way, or it cannot drive a modifier",
          {"Geometry/INPUT", "Geometry/OUTPUT"} <= iface, sorted(iface))

    n = B.call("add_group_node", {"group": "S_Tree", "type": "GeometryNodeMeshCube",
                                  "name": "Cube", "inputs": {"Size": [0.4, 0.4, 0.4]}})
    check("S101 add_group_node succeeds", n.get("ok") is not False, json.dumps(n)[:200])
    check("S101 and the input default was applied", (n.get("inputsApplied") or {}).get("Size"),
          n.get("inputsApplied"))
    badsock = B.call("add_group_node", {"group": "S_Tree", "type": "GeometryNodeMeshCube",
                                        "name": "C2", "inputs": {"NoSuchSocket": 1}})
    check("S101 a socket name that does not exist is refused with the real ones",
          badsock.get("ok") is False and "Size" in str(badsock.get("error", "")),
          str(badsock.get("error"))[:160])
    badtype = B.call("add_group_node", {"group": "S_Tree", "type": "GeometryNodeNotAThing"})
    check("S101 an unknown node type is refused", badtype.get("ok") is False,
          str(badtype.get("error"))[:140])

    # THE READ-BACK THAT CAUGHT A REAL FALSE NEGATIVE. The first version compared RNA references
    # with `is`, which is False for the same node because bpy hands back a new proxy each access -
    # so three links that had demonstrably been made reported linked:false.
    lk = B.call("link_group_nodes", {"group": "S_Tree", "fromNode": "Cube",
                                     "toNode": "Group Output", "fromSocket": "Mesh",
                                     "toSocket": "Geometry"})
    check("S101 link_group_nodes reports the link it just made", lk.get("linked") is True,
          json.dumps(lk)[:220])
    check("S101 and the link count really went up",
          lk.get("linkCountAfter") == (lk.get("linkCountBefore") or 0) + 1,
          "%r -> %r" % (lk.get("linkCountBefore"), lk.get("linkCountAfter")))
    badlink = B.call("link_group_nodes", {"group": "S_Tree", "fromNode": "Cube",
                                          "toNode": "Group Output", "fromSocket": "Mesh",
                                          "toSocket": "NoSuchSocket"})
    check("S101 an unknown socket is refused by name", badlink.get("ok") is False,
          str(badlink.get("error"))[:140])

    li = B.call("list_group_nodes", {"group": "S_Tree"})
    check("S101 list_group_nodes sees the output as reachable", li.get("outputReachable") is True,
          json.dumps(li)[:200])

    # An UNLINKED tree must report unreachable - the check that stops a modifier silently doing
    # nothing. This is the plant: without it, outputReachable could return True unconditionally.
    B.call("create_node_group", {"name": "S_Empty"})
    le = B.call("list_group_nodes", {"group": "S_Empty"})
    check("S101 an unwired tree reports outputReachable FALSE, so the check can fail",
          le.get("outputReachable") is False, json.dumps(le)[:200])
    check("S101 and says what that means", bool(le.get("reachabilityNote")),
          le.get("reachabilityNote"))

    B.call("create_primitive", {"kind": "plane", "name": "S_Ground", "size": 6})
    B.call("add_group_interface", {"group": "S_Tree", "name": "Amount",
                                   "socketType": "NodeSocketFloat", "default": 3.0})
    asg = B.call("assign_node_group", {"object": "S_Ground", "group": "S_Tree",
                                       "inputs": {"Amount": 7.0}})
    check("S101 assign_node_group attaches the modifier", asg.get("ok") is not False,
          json.dumps(asg)[:200])
    check("S101 and resolves the exposed input by NAME, not identifier",
          (asg.get("inputsApplied") or {}).get("Amount") == 7.0, asg.get("inputsApplied"))
    check("S101 an input that is not exposed is REPORTED rather than dropped",
          "Nope" in (B.call("assign_node_group",
                            {"object": "S_Ground", "group": "S_Tree",
                             "inputs": {"Nope": 1}}).get("inputsRefused") or {}),
          "refused map should name the unknown input")

    # ---------------------------------------------------------------- S102 physics
    print("")
    print("=== S102: rigid bodies, and the collision distinction that is silently wrong ===")
    B.call("create_primitive", {"kind": "cube", "name": "S_Crate", "size": 0.5,
                                "location": {"x": 0, "y": 0, "z": 3}})
    rb = B.call("add_rigid_body", {"object": "S_Crate", "type": "ACTIVE", "mass": 8.0,
                                   "bounciness": 0.3, "collisionShape": "BOX"})
    check("S102 add_rigid_body succeeds", rb.get("ok") is not False, json.dumps(rb)[:200])
    check("S102 mass is read back off the body", rb.get("mass") == 8.0, rb.get("mass"))
    check("S102 and the collision shape with it", rb.get("collisionShape") == "BOX",
          rb.get("collisionShape"))
    check("S102 the rigid body world actually holds it", (rb.get("worldObjectCount") or 0) >= 1,
          rb.get("worldObjectCount"))
    passive = B.call("add_rigid_body", {"object": "S_Ground", "type": "PASSIVE"})
    check("S102 a PASSIVE floor is accepted", passive.get("ok") is not False,
          json.dumps(passive)[:160])
    massfail = B.call("add_rigid_body", {"object": "S_Ground", "type": "PASSIVE", "mass": 5})
    check("S102 mass on a PASSIVE body is refused - it is never moved by the sim",
          massfail.get("ok") is False, str(massfail.get("error"))[:150])

    coll = B.call("add_collision", {"object": "S_Ground", "thickness": 0.04})
    check("S102 add_collision succeeds", coll.get("ok") is not False, json.dumps(coll)[:160])
    check("S102 and warns that RIGID BODIES do not use it - the silent mistake",
          "RIGID" in str(coll.get("scopeNote", "")).upper(), coll.get("scopeNote"))

    thin = B.call("add_cloth", {"object": "S_Crate"})
    check("S102 cloth on a mesh too coarse to drape is refused", thin.get("ok") is False,
          str(thin.get("error"))[:150])

    bake = B.call("bake_physics", {"start": 1, "end": 12})
    check("S102 bake_physics succeeds", bake.get("ok") is not False, json.dumps(bake)[:200])
    check("S102 and reports a cache that really holds frames",
          (bake.get("cacheCount") or 0) >= 1, json.dumps(bake)[:220])

    # ---------------------------------------------------------------- S103 particles
    print("")
    print("=== S103: particles, and the setting that renders nothing without complaining ===")
    B.call("create_primitive", {"kind": "cube", "name": "S_Mote", "size": 0.05,
                                "location": {"x": -4, "y": -4, "z": 0}})
    ps = B.call("add_particles", {"object": "S_Ground", "type": "EMITTER", "count": 50,
                                  "frameStart": 1, "frameEnd": 20, "lifetime": 15,
                                  "renderType": "OBJECT", "instanceObject": "S_Mote"})
    check("S103 add_particles succeeds", ps.get("ok") is not False, json.dumps(ps)[:200])
    check("S103 count is read back off the settings", ps.get("count") == 50, ps.get("count"))
    check("S103 and the instance object resolved", ps.get("instanceObject") == "S_Mote",
          ps.get("instanceObject"))

    # renderType OBJECT with no instance renders NOTHING and Blender says nothing about it.
    noinst = B.call("add_particles", {"object": "S_Ground", "renderType": "OBJECT"})
    check("S103 renderType OBJECT without instanceObject is refused, not silently empty",
          noinst.get("ok") is False, str(noinst.get("error"))[:160])
    wrongtype = B.call("add_particles", {"object": "S_Ground", "type": "HAIR", "lifetime": 5})
    check("S103 an EMITTER-only setting on a HAIR system is refused",
          wrongtype.get("ok") is False, str(wrongtype.get("error"))[:150])

    lp = B.call("list_particles", {"object": "S_Ground"})
    check("S103 list_particles reads the system back", (lp.get("count") or 0) >= 1,
          json.dumps(lp)[:200])

    # ---------------------------------------------------------------- S104 render
    print("")
    print("=== S104: render settings and a still that is stat'd off disk ===")
    rs = B.call("set_render_settings", {"engine": "EEVEE", "resolutionX": 160,
                                        "resolutionY": 90, "samples": 4})
    check("S104 set_render_settings succeeds", rs.get("ok") is not False, json.dumps(rs)[:200])
    check("S104 the engine alias resolved to whatever THIS build calls EEVEE",
          "EEVEE" in str((rs.get("after") or {}).get("engine", "")), (rs.get("after") or {}))
    check("S104 and it names the property the sample count actually went to",
          bool((rs.get("applied") or {}).get("samplesOn")), rs.get("applied"))
    badeng = B.call("set_render_settings", {"engine": "RAYTRACE9000"})
    check("S104 an unknown engine is refused with the valid list", badeng.get("ok") is False,
          str(badeng.get("error"))[:140])

    nocam = B.call("render_still", {"filePath": os.path.join(tempfile.gettempdir(), "s_no.png")})
    check("S104 rendering with no scene camera is refused, not attempted",
          nocam.get("ok") is False, str(nocam.get("error"))[:150])

    B.call("create_camera", {"name": "S_Cam", "location": {"x": 5, "y": -5, "z": 4},
                             "lookAt": {"x": 0, "y": 0, "z": 0}})
    B.call("create_light", {"name": "S_Sun", "type": "SUN", "energy": 3})
    out = os.path.join(tempfile.gettempdir(), "mif_scene_suite.png")
    if os.path.isfile(out):
        os.remove(out)
    rr = B.call("render_still", {"filePath": out}, timeout=600.0)
    check("S104 render_still succeeds", rr.get("ok") is not False, json.dumps(rr)[:200])
    # THE MEASUREMENT. render() returns FINISHED whether or not a file appeared.
    check("S104 wroteFile is TRUE and stat'd, not the operator's opinion",
          rr.get("wroteFile") is True, json.dumps(rr)[:240])
    check("S104 and the file really exists on disk, checked independently",
          os.path.isfile(rr.get("filePath") or ""), rr.get("filePath"))
    check("S104 with real bytes in it", (rr.get("fileBytes") or 0) > 500, rr.get("fileBytes"))
    check("S104 at the resolution asked for", rr.get("resolution") == [160, 90],
          rr.get("resolution"))
    # FRESH, NOT MERELY PRESENT. Before 2026-09-03 the freshness test was satisfied by the
    # candidate path's NAME - `cand != target` is true for target+ext - so a leftover render from a
    # previous run reported wroteFile:true with its old byte count. staleFileFound is the other
    # half of that answer and must be False when the render really wrote.
    check("S104 and it is not a stale file from a previous run",
          rr.get("staleFileFound") is False, json.dumps(rr)[:240])

    # THE RE-RENDER, which is where a freshness check goes wrong in the OTHER direction. The file
    # now exists and a second render of the same scene produces the same byte count, so anything
    # comparing size alone would call a real render stale. mtime is what separates them, and this
    # is the case that proves it - deliberately WITHOUT deleting the file first.
    rr2 = B.call("render_still", {"filePath": out}, timeout=600.0)
    check("S104 re-rendering OVER an existing identical file still counts as written",
          rr2.get("wroteFile") is True, json.dumps(rr2)[:240])
    check("S104 and the re-render is not reported as stale either",
          rr2.get("staleFileFound") is False, json.dumps(rr2)[:240])
    try:
        os.remove(rr.get("filePath"))
    except OSError:
        pass

    # ---------------------------------------------------------------- S105 constraints
    print("")
    print("=== S105: a constraint, and the invalid one that looks identical ===")
    con = B.call("add_constraint", {"object": "S_Cam", "type": "TRACK_TO",
                                    "target": "S_Crate", "constraintName": "S_Track"})
    check("S105 adding a TRACK_TO constraint succeeds", con.get("ok") is not False,
          json.dumps(con)[:220])
    # MEASURED THROUGH THE DEPSGRAPH. A constraint does not touch obj.matrix_world, so the ONLY
    # evidence it does anything is the evaluated matrix moving. A camera at (5,-5,4) aimed at a
    # crate is a real rotation, so this must be non-zero - and if this assertion ever passes
    # trivially it means the op went back to reading the base object.
    check("S105 and it MOVED the camera - measured on the evaluated matrix, which is the only "
          "place a constraint is visible at all",
          con.get("hadEffect") is True, json.dumps(con)[:220])
    check("S105 the constraint reads back as valid",
          ((con.get("constraint") or {}).get("isValid")) is True, json.dumps(con)[:220])

    lc = B.call("list_constraints", {"object": "S_Cam"})
    check("S105 list_constraints finds it", (lc.get("count") or 0) == 1, json.dumps(lc)[:220])
    check("S105 and reports none invalid while the target exists",
          lc.get("invalidCount") == 0 and lc.get("invalid") == [], json.dumps(lc)[:220])

    # THE SILENT FAILURE THIS FIELD EXISTS FOR. Delete the target and the constraint STAYS, with
    # its type, influence and target name all still reading fine.
    #
    # AND is_valid IS NOT THE TELL, which is what this check spent its life proving while nobody
    # read the result. Measured on 5.0.1: created with no target, Blender's is_valid is correctly
    # False for 19 of the 20 target-taking constraint types. Have the target DELETED afterwards
    # and it stays TRUE - through view_layer.update(), update_tag(), a depsgraph update, and on
    # the evaluated copy. It is never recomputed. So the flag works in every case except the one
    # this block is about, and the addon now derives the answer instead of trusting it.
    B.call("delete_object", {"object": "S_Crate"})
    lc2 = B.call("list_constraints", {"object": "S_Cam"})
    check("S105 with the target DELETED the constraint is still on the stack",
          (lc2.get("count") or 0) == 1, json.dumps(lc2)[:220])
    check("S105 and it is now reported INVALID - the whole reason invalidCount exists, because "
          "every other field still looks healthy",
          lc2.get("invalidCount") == 1 and "S_Track" in (lc2.get("invalid") or []),
          json.dumps(lc2)[:220])

    row2 = (lc2.get("constraints") or [{}])[0]
    check("S105 and targetMissing says WHICH kind of invalid it is - re-point it, or ask why the "
          "target was deleted, are different fixes and one boolean cannot carry both",
          row2.get("targetMissing") is True and row2.get("target") is None,
          json.dumps(row2)[:220])

    # THE NEGATIVE CONTROL, and it is a real distinction rather than a formality. PIVOT is the one
    # target-taking type whose is_valid is TRUE with no target, because a Pivot constraint with no
    # target pivots around the object's own point. Folding it in with the other nineteen would
    # report a correctly configured constraint as broken - and a false failure is worse than a
    # false pass, because it teaches the reader to ignore the field.
    B.call("add_constraint", {"object": "S_Cam", "type": "PIVOT", "constraintName": "S_Pivot"})
    lc3 = B.call("list_constraints", {"object": "S_Cam"})
    pivot = [c for c in (lc3.get("constraints") or []) if c.get("name") == "S_Pivot"]
    check("S105 a PIVOT with no target is NOT reported missing one - it pivots around the "
          "object's own point, so demanding a target would fail correct configuration",
          len(pivot) == 1 and pivot[0].get("targetMissing") is False
          and pivot[0].get("isValid") is True,
          json.dumps(pivot)[:220])
    B.call("remove_constraint", {"object": "S_Cam", "constraintName": "S_Pivot"})

    # ---------------------------------------------------------------- S107 the render comes back
    print("")
    print("=== S107: a render returns an image, so something can LOOK at it ===")
    # THE POINT OF THE WHOLE FEATURE. Seven render ops and none of them returned a picture, so an
    # agent could light and frame and render a scene and had never once seen one. The first frame
    # returned during development showed the default cube occluding the subject - a composition
    # problem no numeric check in this repo would ever have reported.
    import base64 as _b64

    _shot = os.path.join(tempfile.gettempdir(), "mif_s106_preview.png")
    pv = B.call("render_still", {"filePath": _shot, "resolutionX": 320, "resolutionY": 240,
                                 "samples": 4, "returnImage": True, "previewMaxPx": 128},
                timeout=600.0)
    check("S107 the render itself still succeeds with returnImage on",
          pv.get("wroteFile") is True, json.dumps(pv)[:200])

    _img = pv.get("image")
    _raw = b""
    if isinstance(_img, str):
        try:
            _raw = _b64.b64decode(_img)
        except Exception as exc:                                    # noqa: BLE001
            _raw = b""
            print("       base64 did not decode: %s" % exc)
    check("S107 an image comes back and it DECODES - a string that is not valid base64 would pass "
          "any check that only asked whether the field was present",
          len(_raw) > 0, "imageError=%r bytes=%d" % (pv.get("imageError"), len(_raw)))
    check("S107 and the bytes are really a PNG, not an empty file or the wrong format",
          _raw[:8] == b"\x89PNG\r\n\x1a\n", repr(_raw[:12]))

    # THE DOWNSCALE IS REPORTED, BOTH WAYS. A preview that silently claimed the rendered size would
    # let a caller measure framing against dimensions the file does not have.
    check("S107 the returned image is the DOWNSCALED size, not the rendered size",
          pv.get("imageWidth") == 128 and pv.get("imageHeight") == 96,
          "returned %sx%s, rendered %sx%s" % (pv.get("imageWidth"), pv.get("imageHeight"),
                                              pv.get("renderedWidth"), pv.get("renderedHeight")))
    check("S107 and the RENDERED size is reported too, so the preview cannot be mistaken for the "
          "artifact on disk",
          pv.get("renderedWidth") == 320 and pv.get("renderedHeight") == 240
          and pv.get("downscaledFrom") == [320, 240], json.dumps(pv)[:220])

    # LOADING A PNG TO RESCALE IT CREATES A DATABLOCK. Leaving it behind would be this op quietly
    # changing the file to answer a question about it.
    check("S107 and the scratch image datablock was removed rather than left in the file",
          not pv.get("previewDatablockLeaked"), pv.get("previewDatablockLeaked"))

    # THE NEGATIVE CONTROL. Without this, S107 proves the flag works and says nothing about the
    # default - and the default is what every existing caller gets.
    plain = B.call("render_still", {"filePath": _shot, "resolutionX": 320, "resolutionY": 240,
                                    "samples": 4}, timeout=600.0)
    check("S107 with returnImage OMITTED no image is returned - the default must not hand every "
          "caller a megabyte of base64 they never asked for",
          plain.get("image") is None and "imageBytes" not in plain,
          json.dumps({k: v for k, v in plain.items() if "image" in k.lower()})[:160])

    rm = B.call("remove_constraint", {"object": "S_Cam", "constraintName": "S_Track"})
    check("S105 removing it counts the stack rather than trusting the call",
          rm.get("countsAgree") is True and rm.get("constraintCountAfter") == 0,
          json.dumps(rm)[:220])

    # ---------------------------------------------------------------- S106 custom properties
    print("")
    print("=== S106: custom properties, and the type Blender quietly changes ===")
    cp = B.call("set_custom_property", {"object": "S_Cam", "key": "S_Rig", "value": 0.25,
                                        "min": 0.0, "max": 1.0,
                                        "description": "suite probe"})
    check("S106 setting a custom property succeeds", cp.get("ok") is not False,
          json.dumps(cp)[:220])
    check("S106 and the UI range was accepted - a slider without one is not a control",
          cp.get("uiRangeSet") is True, json.dumps(cp)[:220])
    check("S106 the stored type is reported, because Blender coerces silently",
          bool(cp.get("storedType")), json.dumps(cp)[:220])

    lcp = B.call("list_custom_properties", {"object": "S_Cam"})
    keys = [r.get("key") for r in (lcp.get("properties") or [])]
    check("S106 list_custom_properties finds it", "S_Rig" in keys, json.dumps(lcp)[:220])
    # THE FILTER IS PART OF THE ANSWER. Blender's own cycles settings live in the same namespace,
    # so a count that folded them in would be wrong in a confusing way - and one that dropped them
    # silently would leave a caller unable to tell an empty object from a filtered one.
    check("S106 and a user key is NOT counted among the internal ones it filtered",
          "S_Rig" not in (lcp.get("skippedInternalKeys") or []),
          json.dumps(lcp.get("skippedInternalKeys")))
    check("S106 with the filtered internal keys named rather than silently dropped",
          isinstance(lcp.get("skippedInternalKeys"), list),
          json.dumps(lcp)[:220])

    gone = B.call("set_custom_property", {"object": "S_Cam", "key": "S_Rig", "delete": True})
    check("S106 deleting it reports the remaining set rather than a bare ok",
          gone.get("deleted") is True
          and "S_Rig" not in [r.get("key") for r in (gone.get("properties") or [])],
          json.dumps(gone)[:220])

    # ---------------------------------------------------------------- cleanup
    print("")
    for n in ("S_Ground", "S_Crate", "S_Mote", "S_Cam", "S_Sun"):
        B.call("delete_object", {"object": n})
    survivors = [o.get("name") for o in (B.call("list_objects", {}).get("objects") or [])]
    check("S199 (cleanup) no S_* object is left behind",
          not [x for x in survivors if str(x).startswith("S_")], survivors)

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        if isinstance(f, tuple):
            print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
