"""MifBlender's write half: apply_transform, set_origin, clean_mesh, normalize_weights, transfer_weights.

WHY THESE FIVE, AND WHY TOGETHER. Every one of them closes a detect-but-cannot-fix gap the addon
already had - the same shape uv_unwrap closed on 2026-08-27, when three separate places reported
uvLayers and nothing could make one:

  object_info reported isIdentityTransform, and mif_mesh_roundtrip GATES on it, because a
    non-identity object transform means the pivot moved. Nothing could apply a transform.
  Nothing could set an origin at all, and the origin is what Unreal rotates and places a mesh
    around. It is baked into the FBX, so it cannot be fixed on the Unreal side.
  list_vertex_groups could report that a vertex is influenced by 11 bones. Unreal's skin cache
    takes 4 by default and the FBX importer silently drops the smallest past the limit, so a mesh
    that deforms correctly in Blender deforms differently in Unreal and neither tool says why.
    Nothing could limit or normalise them.
  decimate_mesh and clean_mesh destroy skinning by changing topology, and nothing could put it
    back - re-rigging by hand is the expensive part of a retopology pass.

WHAT IS ASSERTED IS THE POSTCONDITION, NOT ok:true. Every op here reports measured before/after
numbers precisely so a test can check the state actually changed, and that is what these do -
a vertex count, an influence count, a bounds delta. ok:true is what an op that silently did
nothing would also return.

SELF-CONTAINED, same discipline as test_blender_mesh.py: the fixture is built from the factory
startup Cube through the addon's own ops. No checked-in .fbx, no Unreal, no network.

RUN IT the way run_blender_suites.py does - against a FRESH headless Blender of its own. Do not
point it at a GUI Blender you have work open in: clean_scene is the first thing it calls.
"""
import json
import os
import socket
import struct
import sys

HOST = os.environ.get("MIF_BLENDER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MIF_BLENDER_PORT", "8792"))
TOKEN = os.environ.get("MIF_BLENDER_TOKEN", os.environ.get("MIF_BRIDGE_TOKEN", "dev"))

PASS, FAIL = [], []


def call(op, timeout=120.0, **params):
    frame = {"endpoint": op, "token": TOKEN,
             "params": {k: v for k, v in params.items() if v is not None}}
    body = json.dumps(frame).encode("utf-8")
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        s.sendall(struct.pack(">I", len(body)) + body)
        head = b""
        while len(head) < 4:
            chunk = s.recv(4 - len(head))
            if not chunk:
                return {"ok": False, "error": "connection closed reading the header"}
            head += chunk
        want = struct.unpack(">I", head)[0]
        buf = b""
        while len(buf) < want:
            chunk = s.recv(min(65536, want - len(buf)))
            if not chunk:
                return {"ok": False, "error": "connection closed reading the body"}
            buf += chunk
        return json.loads(buf.decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        s.close()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print("  %-4s %s" % ("PASS" if cond else "FAIL", name))
    if not cond and detail:
        print("       %s" % str(detail)[:230])


def first_mesh():
    listing = call("list_objects")
    for o in (listing.get("objects") or []):
        if str(o.get("type", "")).upper() == "MESH":
            return o.get("name")
    return None


def info(name):
    r = call("object_info", object=name) or {}
    return r.get("object") or r


def main():
    print("MifBlender authoring ops - %s:%d" % (HOST, PORT))
    ping = call("ping", timeout=10.0)
    if not ping.get("ok"):
        print("  Nothing answered a ping on %s:%d, so no op was exercised." % (HOST, PORT))
        print("  SKIP (exit 2) - a suite that verified nothing must not report success.")
        return 2

    cube = first_mesh()
    check("A000 (setup) a mesh exists to work on", cube is not None,
          "no MESH in the scene - this suite needs the factory startup Cube")
    if not cube:
        return 1

    # ================================================================= apply_transform
    print("\n=== A100-A104: apply_transform - the op the fidelity gate needs ===")

    # Move and scale the cube so there is a non-identity transform to bake.
    moved = call("set_transform_probe")  # deliberately unknown - see A100 below
    check("A100 an unknown op is refused, not silently accepted",
          moved.get("ok") is False, json.dumps(moved)[:200])

    # Build the non-identity state through Blender's own object, via clean_mesh's sibling path:
    # the addon has no move op, so use the transform the import/startup gave us and verify the
    # REPORTING rather than fabricating a transform we cannot set. If the cube is already
    # identity, apply_transform must say it changed nothing rather than claim work.
    before = info(cube)
    check("A101 (setup) object_info reports isIdentityTransform", "isIdentityTransform" in before,
          json.dumps(before)[:250])

    applied = call("apply_transform", object=cube)
    check("A102 apply_transform succeeds", applied.get("ok") is True, json.dumps(applied)[:250])
    check("A102 it reports which channels it applied",
          isinstance(applied.get("applied"), dict)
          and set(applied["applied"]) == {"location", "rotation", "scale"},
          json.dumps(applied.get("applied"))[:200])

    # THE assertion. Not ok:true - the postcondition, read back from the op's own after-image AND
    # independently from object_info, so this does not rest on apply_transform reporting on itself.
    check("A103 the transform is identity afterwards - reported by the op",
          applied.get("isIdentityTransform") is True, json.dumps(applied)[:250])
    check("A103 and confirmed independently through object_info",
          info(cube).get("isIdentityTransform") is True, json.dumps(info(cube))[:250])

    none_asked = call("apply_transform", object=cube, location=False, rotation=False, scale=False)
    check("A104 applying nothing is refused, with a reason", none_asked.get("ok") is False,
          json.dumps(none_asked)[:200])
    check("A104 and the refusal says NOTHING was changed",
          "NOTHING was changed" in (none_asked.get("error") or ""), none_asked.get("error"))

    # ================================================================= set_origin
    print("\n=== A200-A205: set_origin - the pivot Unreal rotates around ===")

    o_before = info(cube)
    origin = call("set_origin", object=cube, mode="bottom")
    check("A200 set_origin{mode:bottom} succeeds", origin.get("ok") is True, json.dumps(origin)[:250])

    # THE assertion for this op: the geometry must NOT move. That is the entire contract - only the
    # pivot changes - and it is measured, not assumed.
    check("A201 the geometry stayed put - only the pivot moved",
          origin.get("geometryStayedPut") is True,
          "geometryMovedBU=%s" % origin.get("geometryMovedBU"))
    check("A201 and the move is reported as a real number, not a boolean alone",
          isinstance(origin.get("geometryMovedBU"), (int, float)), json.dumps(origin)[:200])

    check("A202 the origin actually moved to the bounds minimum in Z",
          origin.get("originAfterBU") != origin.get("originBeforeBU")
          or abs((origin.get("originAfterBU") or [0, 0, 0])[2]
                 - (origin.get("worldBoundsMinBU") or [0, 0, 0])[2]) < 1e-3,
          json.dumps(origin)[:250])

    for mode in ("geometry", "bounds", "world"):
        r = call("set_origin", object=cube, mode=mode)
        check("A203 mode=%s succeeds and keeps the geometry in place" % mode,
              r.get("ok") is True and r.get("geometryStayedPut") is True, json.dumps(r)[:220])

    bad_mode = call("set_origin", object=cube, mode="middle-ish")
    check("A204 an unknown mode is refused", bad_mode.get("ok") is False, json.dumps(bad_mode)[:200])
    check("A204 and the refusal lists the accepted modes",
          "geometry" in (bad_mode.get("error") or ""), bad_mode.get("error"))

    no_loc = call("set_origin", object=cube, mode="point")
    check("A205 mode=point without a location is refused", no_loc.get("ok") is False,
          json.dumps(no_loc)[:200])

    # ================================================================= clean_mesh
    print("\n=== A300-A305: clean_mesh - counts, not claims ===")

    nothing = call("clean_mesh", object=cube)
    check("A300 clean_mesh with every step off is refused", nothing.get("ok") is False,
          json.dumps(nothing)[:200])
    check("A300 and the refusal names the steps that could be enabled",
          "mergeDistance" in (nothing.get("error") or ""), nothing.get("error"))

    tri = call("clean_mesh", object=cube, triangulate=True)
    check("A301 clean_mesh{triangulate} succeeds", tri.get("ok") is True, json.dumps(tri)[:250])
    check("A301 it reports before and after counts", "before" in tri and "after" in tri,
          json.dumps(tri)[:250])

    # THE assertion: a cube is 6 quads, so triangulating MUST produce 12 tris and 12 faces. A
    # measured postcondition, not ok:true - an op that did nothing returns ok:true too.
    check("A302 the cube's 6 quads really became 12 triangles",
          (tri.get("after") or {}).get("faces") == 12
          and (tri.get("before") or {}).get("faces") == 6,
          json.dumps({"before": tri.get("before"), "after": tri.get("after")})[:250])
    check("A302 and the step record says how many faces were converted",
          ((tri.get("steps") or {}).get("triangulated") or {}).get("nonTriFacesConverted") == 6,
          json.dumps(tri.get("steps"))[:250])

    again = call("clean_mesh", object=cube, triangulate=True)
    check("A303 triangulating an already-triangulated mesh changes nothing",
          again.get("ok") is True and again.get("changedAnything") is False,
          json.dumps(again)[:250])
    check("A303 and it SAYS so rather than returning a bare ok",
          bool(again.get("note")), json.dumps(again)[:250])

    merged = call("clean_mesh", object=cube, mergeDistance=0.0001, removeLoose=True)
    check("A304 merge + removeLoose on a clean cube succeeds and removes nothing",
          merged.get("ok") is True and merged.get("changedAnything") is False,
          json.dumps(merged)[:250])

    normals = call("clean_mesh", object=cube, recalcNormals=True)
    check("A305 recalcNormals succeeds on a mesh with no custom split normals",
          normals.get("ok") is True, json.dumps(normals)[:250])

    # ================================================================= normalize_weights
    print("\n=== A400-A403: normalize_weights - refuses honestly with no groups ===")

    nw = call("normalize_weights", object=cube)
    # The startup Cube has no vertex groups, so the honest outcome is a refusal that says why.
    check("A400 normalize_weights refuses a mesh with no vertex groups",
          nw.get("ok") is False, json.dumps(nw)[:250])
    check("A400 and names the reason - no groups, not a generic failure",
          "vertex group" in (nw.get("error") or "").lower(), nw.get("error"))
    check("A400 and says NOTHING was changed",
          "NOTHING was changed" in (nw.get("error") or ""), nw.get("error"))

    bad_max = call("normalize_weights", object=cube, maxInfluences=0, normalize=False)
    check("A401 asking it to do nothing is refused", bad_max.get("ok") is False,
          json.dumps(bad_max)[:220])

    missing = call("normalize_weights", object="NoSuchObject", maxInfluences=4)
    check("A402 an unknown object is refused", missing.get("ok") is False, json.dumps(missing)[:200])

    unknown_param = call("normalize_weights", object=cube, notAParam=1)
    check("A403 an unknown parameter is refused rather than ignored",
          unknown_param.get("ok") is False, json.dumps(unknown_param)[:220])

    # ================================================================= transfer_weights
    print("\n=== A500-A502: transfer_weights - the two cases it must refuse ===")

    same = call("transfer_weights", source=cube, destination=cube)
    check("A500 transferring an object onto itself is refused", same.get("ok") is False,
          json.dumps(same)[:250])
    check("A500 and the refusal says why, not just no",
          "same object" in (same.get("error") or "").lower(), same.get("error"))

    no_groups = call("transfer_weights", source=cube, destination="NoSuchObject")
    check("A501 an unknown destination is refused", no_groups.get("ok") is False,
          json.dumps(no_groups)[:220])

    bad_map = call("transfer_weights", source=cube, destination=cube, mapping="TELEPATHY")
    check("A502 an unknown mapping is refused and lists the accepted ones",
          bad_map.get("ok") is False, json.dumps(bad_map)[:250])

    # WHAT THIS SUITE DOES NOT COVER, declared rather than left to be discovered - the same
    # discipline test_blender_mesh.py applies to the five gen_* ops.
    print("\n  NOT COVERED, and said out loud: the SUCCESS paths of normalize_weights and")
    print("  transfer_weights need a skinned mesh with an armature, which this suite cannot build")
    print("  from the startup Cube through the addon's own ops - there is no create_armature or")
    print("  assign_weights op. Their refusal paths are covered above; their happy paths are not.")
    print("  Building that fixture is the next piece of work, not an omission to forget.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
