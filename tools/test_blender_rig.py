"""ops_rig.py - list_bones / list_shape_keys / list_vertex_groups, the character-rigging reads
the Blender addon had NONE of before this file - plus object_info's new armatureModifier field
(ops_common.py), which closes the last gap: knowing which armature actually deforms a mesh.

WHY THIS EXISTS. Andre's ask for full depth on the Blender side surfaced this while auditing
ops_common.object_info(): it reports transform/bounds/materials/UVs for a MESH and NOTHING for
an ARMATURE beyond its bare transform, and shape keys and vertex groups are absent even for a
mesh. That is a real gap on a character-driven pipeline - the UE side can already read a
skeleton's bones, virtual bones and morph targets (MifBridgeSkeleton.cpp, added the same day),
and nothing on the BLENDER side, where a rigger actually AUTHORS that data, could read any of it
back until now.

WHAT THIS MACHINE CAN AND CANNOT PROVE. A factory-startup Blender scene (Camera, Cube, Light) has
no armature, no shape key, no vertex group to read - there is nothing to ADOPT the way
test_blender_ops.py adopts an existing mesh when run_python is off. Building real rig content
needs run_python, which is DISABLED BY DEFAULT (a deliberate security choice - it executes
arbitrary code inside Blender with the user's privileges) and this suite does not flip that
preference itself; that is a decision for a person at the keyboard, same reasoning MifBridge's UE
side uses for live_coding_compile and Live Coding.

So this suite ALWAYS proves the parameter contracts and the EMPTY-STATE paths - list_bones
refusing a non-armature object, list_shape_keys/list_vertex_groups reporting "none" gracefully
rather than erroring, every op's reject_unknown guard. When run_python IS enabled (whoever runs
this suite turned it on), it also builds a real 2-bone armature, a mesh with a vertex group and a
shape key, and proves the POPULATED path against them - armature-space bone positions, a
child bone reporting its parent, a vertex group's weighted count, a shape key's basis/relative
pairing. Either way the suite passes; which branch ran, and what remains unproven, is stated
rather than left to be discovered by reading the source.
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
UNPROVEN = []


def call(op, timeout=30.0, **params):
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
    (PASS if cond else FAIL).append(name)
    print("  %-4s %s" % ("PASS" if cond else "FAIL", name))
    if not cond and detail:
        print("       %s" % str(detail)[:220])


def reachable():
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect((HOST, PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


RIG_CODE = """
import bpy

arm_data = bpy.data.armatures.new("MifTestArmature")
arm_obj = bpy.data.objects.new("MifTestArmature", arm_data)
bpy.context.collection.objects.link(arm_obj)
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode='EDIT')
b1 = arm_data.edit_bones.new("root")
b1.head = (0, 0, 0)
b1.tail = (0, 0, 1)
b2 = arm_data.edit_bones.new("child")
b2.head = (0, 0, 1)
b2.tail = (0, 0, 2)
b2.parent = b1
bpy.ops.object.mode_set(mode='OBJECT')

bpy.ops.mesh.primitive_cube_add()
mesh_obj = bpy.context.active_object
mesh_obj.name = "MifTestMesh"
vg = mesh_obj.vertex_groups.new(name="root")
vg.add([0, 1, 2, 3], 1.0, 'REPLACE')
mesh_obj.shape_key_add(name="Basis")
sk = mesh_obj.shape_key_add(name="Smile")
sk.slider_min = 0.0
sk.slider_max = 1.0
mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
mod.object = arm_obj
"""


def main():
    print("MifBlender rig ops (ops_rig.py) - %s:%d" % (HOST, PORT))
    if not reachable():
        print("")
        print("SKIPPED - nothing was verified.")
        print("  Blender is not listening on %s:%d, so no addon op was exercised." % (HOST, PORT))
        print("  Start Blender with the MifBlender addon enabled and run this again.")
        return 2

    print("")

    # ------------------------------------------------------------------ T810 parameter contracts
    print("=== T810: parameter contracts, all three ops ===")
    for op in ("list_bones", "list_shape_keys", "list_vertex_groups"):
        r = call(op)
        check("T810 %s with no object refuses" % op, r.get("ok") is False, r.get("error"))
        r = call(op, object="NoSuchThing_zz")
        check("T810 %s on a missing object refuses and lists what exists" % op,
              r.get("ok") is False and "Present" in str(r.get("error", "")) or "no object named"
              in str(r.get("error", "")), r.get("error"))
        r = call(op, object="Cube", nonsenseParam=True)
        check("T810 %s rejects an unknown parameter" % op, r.get("ok") is False, r.get("error"))

    # ------------------------------------------------------------------ T811 empty-state / type paths
    print("")
    print("=== T811: empty-state and object-type handling on the factory scene ===")
    r = call("list_bones", object="Cube")
    check("T811 list_bones refuses a MESH (not an ARMATURE)",
          r.get("ok") is False and "ARMATURE" in str(r.get("error", "")), r.get("error"))

    r = call("list_shape_keys", object="Cube")
    check("T811 list_shape_keys on a mesh with none succeeds", r.get("ok") is True, r)
    check("T811 and says hasShapeKeys:false with a note",
          r.get("hasShapeKeys") is False and bool(r.get("note")), r)
    check("T811 and count/array agree at zero",
          r.get("count") == 0 and r.get("shapeKeys") == [], r)

    r = call("list_vertex_groups", object="Cube")
    check("T811 list_vertex_groups on a mesh with none succeeds", r.get("ok") is True, r)
    check("T811 and count/array agree at zero with a note",
          r.get("count") == 0 and r.get("vertexGroups") == [] and bool(r.get("note")), r)

    r = call("object_info", object="Cube")
    check("T811 object_info on an unrigged mesh reports armatureModifier:null",
          r.get("ok") is True and (r.get("object") or {}).get("armatureModifier") is None, r)

    # ------------------------------------------------------------------ T812 the populated path
    print("")
    print("=== T812: the POPULATED path - needs run_python (off by default) ===")
    probe = call("run_python", code="pass")
    if probe.get("ok") is False:
        check("T812 (not exercised: run_python is disabled, the correct default - "
              "nothing here builds real rig content to adopt instead)", True)
        UNPROVEN.append("the POPULATED path for all three ops - armature-space bone positions "
                        "and parent linkage, a shape key's basis/relative pairing, a vertex "
                        "group's weighted vertex count - and object_info's armatureModifier "
                        "field, which needs a real Armature modifier to report anything but "
                        "null. Needs run_python enabled (Edit > Preferences > Add-ons > "
                        "MifBlender > 'Allow run_python') to build test content; this suite "
                        "does not flip that preference itself.")
    else:
        r = call("run_python", code=RIG_CODE)
        check("T812 building the test rig succeeded", r.get("ok") is not False, r.get("error"))

        b = call("list_bones", object="MifTestArmature")
        check("T812 list_bones succeeds on the real armature", b.get("ok") is True, json.dumps(b)[:200])
        bones = {row["name"]: row for row in (b.get("bones") or [])}
        check("T812 both bones are present", set(bones) == {"root", "child"}, sorted(bones))
        check("T812 boneCount matches", b.get("boneCount") == 2, b.get("boneCount"))
        if "root" in bones and "child" in bones:
            check("T812 root has no parent and IS flagged root",
                  bones["root"]["parent"] is None and bones["root"]["isRoot"] is True, bones["root"])
            check("T812 child's parent is root",
                  bones["child"]["parent"] == "root" and bones["child"]["isRoot"] is False,
                  bones["child"])
            check("T812 child's armature-space head matches root's armature-space tail "
                  "(they were built joined at z=1)",
                  abs(bones["child"]["headArmatureSpaceBU"][2] - 1.0) < 1e-4
                  and abs(bones["root"]["tailArmatureSpaceBU"][2] - 1.0) < 1e-4,
                  (bones["root"]["tailArmatureSpaceBU"], bones["child"]["headArmatureSpaceBU"]))
            check("T812 root reports one child", bones["root"]["childCount"] == 1, bones["root"])

        k = call("list_shape_keys", object="MifTestMesh")
        check("T812 list_shape_keys succeeds on the real mesh", k.get("ok") is True, json.dumps(k)[:200])
        check("T812 hasShapeKeys is true", k.get("hasShapeKeys") is True, k)
        keys = {row["name"]: row for row in (k.get("shapeKeys") or [])}
        check("T812 both keys are present", set(keys) == {"Basis", "Smile"}, sorted(keys))
        if "Basis" in keys and "Smile" in keys:
            check("T812 Basis is flagged isBasis", keys["Basis"]["isBasis"] is True, keys["Basis"])
            check("T812 Smile is NOT flagged isBasis", keys["Smile"]["isBasis"] is False, keys["Smile"])
            check("T812 Smile is relative to Basis", keys["Smile"]["relativeTo"] == "Basis", keys["Smile"])
            check("T812 Smile's slider range round-trips",
                  keys["Smile"]["sliderMin"] == 0.0 and keys["Smile"]["sliderMax"] == 1.0,
                  keys["Smile"])

        vg = call("list_vertex_groups", object="MifTestMesh")
        check("T812 list_vertex_groups succeeds on the real mesh", vg.get("ok") is True, json.dumps(vg)[:200])
        groups = {row["name"]: row for row in (vg.get("vertexGroups") or [])}
        check("T812 the 'root' group is present", "root" in groups, sorted(groups))
        if "root" in groups:
            check("T812 4 vertices were assigned, and it shows",
                  groups["root"]["weightedVertexCount"] == 4, groups["root"])
            check("T812 influencesGeometry is true", groups["root"]["influencesGeometry"] is True,
                  groups["root"])

        # ------------------------------------------------------------- T813 mesh<->armature linkage
        # object_info's own new field (ops_common.py), not ops_rig.py - the pairing that closes the
        # loop between "what bones does this armature have" and "which armature deforms this mesh".
        print("")
        print("=== T813: object_info reports which armature deforms the mesh ===")
        oi = call("object_info", object="MifTestMesh")
        check("T813 object_info succeeds", oi.get("ok") is True, json.dumps(oi)[:200])
        obj_info = oi.get("object") or {}
        check("T813 armatureModifier names the real armature",
              obj_info.get("armatureModifier") == "MifTestArmature", obj_info.get("armatureModifier"))

        oi2 = call("object_info", object="Camera")
        check("T813 a non-mesh object has no armatureModifier field at all (not null - absent)",
              "armatureModifier" not in (oi2.get("object") or {}), oi2)

        call("delete_object", objects=["MifTestArmature", "MifTestMesh"])

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    if UNPROVEN:
        print("")
        print("NOT PROVEN BY THIS SUITE (green above does not cover these):")
        for u in UNPROVEN:
            print("  - %s" % u)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
