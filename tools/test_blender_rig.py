"""ops_rig.py - list_bones / list_shape_keys / list_vertex_groups / list_modifiers, the
mesh-construction-state reads the Blender addon had NONE of before this file - plus object_info's
new armatureModifier field (ops_common.py), which closes the gap of knowing which armature
actually deforms a mesh.

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
    """Delegates to blender_audit_common - a real framed PING, never a bare connect.

    This was a private socket.connect() that returned True for anything accepting a connection. On
    this machine a UE editor holds MifBlender's port 8792 (docs/06 issue 15), so it answered True
    with no Blender running and the suite ran its whole body against the wrong protocol - PASS 12
    FAIL 4 where the honest answer was SKIPPED. A false failure is worse than a false pass: it
    teaches the reader to ignore the suite.
    """
    import blender_audit_common as _B
    _B.HOST, _B.PORT = HOST, PORT
    return _B.reachable()

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
solid = mesh_obj.modifiers.new(name="Solidify", type='SOLIDIFY')
solid.thickness = 0.25
solid.show_render = False
"""


def check_modifier_tables():
    """The read and write modifier tables must describe the same TYPES.

    ops_rig has two: _MODIFIER_FIELDS (getter lambdas, used by list_modifiers) and
    _MODIFIER_WRITES (setters + coercion, used by add_modifier). They are separate on purpose - a
    write table needs setters and type coercion that a getter table cannot express, so folding them
    into one description makes both halves worse. What must NOT drift is which types each knows
    about: a type added to the read side and forgotten on the write side is an asymmetry nobody
    notices until someone tries to set a field that reads back fine.

    This runs in-process against the addon source rather than over the socket, because it is a
    property of the CODE, not of a running Blender.
    """
    import os as _os
    import re as _re
    src = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                        "blender-addon", "MifBlender", "ops_rig.py")
    try:
        text = io_open(src)
    except Exception as exc:  # noqa: BLE001
        check("R900 (setup) ops_rig.py is readable for the table-sync check", False, str(exc))
        return

    def keys_of(table_name):
        m = _re.search(table_name + r"\s*=\s*\{(.*?)\n\}", text, _re.S)
        if not m:
            return None
        return set(_re.findall(r'"([A-Z_]+)"\s*:', m.group(1)))

    read_keys = keys_of("_MODIFIER_FIELDS")
    write_keys = keys_of("_MODIFIER_WRITES")
    check("R900 both modifier tables are found in ops_rig.py",
          bool(read_keys) and bool(write_keys),
          "read=%s write=%s" % (read_keys, write_keys))
    if not read_keys or not write_keys:
        return
    check("R901 the read and write modifier tables describe the SAME types - a type on one side "
          "and not the other is a silent asymmetry",
          read_keys == write_keys,
          "read-only: %s   write-only: %s" % (sorted(read_keys - write_keys),
                                              sorted(write_keys - read_keys)))


def io_open(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main():
    check_modifier_tables()

    print("MifBlender rig ops (ops_rig.py) - %s:%d" % (HOST, PORT))
    if not reachable():
        print("")
        print("SKIPPED - nothing was verified.")
        print("  Blender is not listening on %s:%d, so no addon op was exercised." % (HOST, PORT))
        print("  Start Blender with the MifBlender addon enabled and run this again.")
        return 2

    # This suite REBUILDS an empty scene to self-heal, so it writes even when it looks like a read.
    # _B is imported inside reachable() rather than at module scope, so import it again here.
    import blender_audit_common as _BC
    _BC.HOST, _BC.PORT = HOST, PORT
    stop = _BC.require_headless(
        "test_blender_rig", lambda op, params=None: call(op, **(params or {})))
    if stop is not None:
        return stop

    print("")

    # ------------------------------------------------------------------ T810 parameter contracts
    print("=== T810: parameter contracts, all four ops ===")
    for op in ("list_bones", "list_shape_keys", "list_vertex_groups", "list_modifiers"):
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
    # NOT a safe assumption on a long-lived Blender session: "Cube" is only there on a pristine
    # factory-startup scene, and run_all_suites.py's own full sweep shares ONE Blender process across
    # every suite in the run - test_blender_mesh.py's own clear_scene call (T769) empties the scene,
    # and anything alphabetically after it inherits that emptiness. Live-caught, not assumed: this
    # exact sequence failed 8/48 checks with "no object named 'Cube'. Present: <scene is empty>"
    # before this fix, in a full run_all_suites.py pass where test_blender_mesh.py ran first.
    # Self-heals when possible (run_python is available and this session's own scene really is
    # missing Cube) rather than just skipping, because "prove nothing" is a worse answer than "prove
    # it after restoring the one precondition this suite needs" when restoring it is cheap, safe and
    # scoped to exactly the primitive being tested against. probe is reused below by T812 so the
    # availability check only ever runs once.
    probe = call("run_python", code="pass")
    run_python_available = probe.get("ok") is not False
    cube_probe = call("object_info", object="Cube")
    cube_present = cube_probe.get("ok") is not False
    if not cube_present and run_python_available:
        fixed = call("run_python", code="import bpy\nbpy.ops.mesh.primitive_cube_add()\n"
                                         "bpy.context.active_object.name = 'Cube'")
        check("T811 (setup) the scene had no 'Cube' - a factory-default one was rebuilt so this "
              "suite does not depend on incidental state left by an earlier suite in the same "
              "Blender session", fixed.get("ok") is not False, fixed.get("error"))
        cube_present = fixed.get("ok") is not False

    if not cube_present:
        check("T811 (not exercised: the scene has no 'Cube' and run_python is disabled, so there "
              "is no safe way to restore it)", True)
        UNPROVEN.append("T811's empty-state/type-mismatch checks - the scene had no 'Cube' object "
                        "(likely cleared by an earlier suite sharing this same Blender session) and "
                        "run_python is disabled, so this suite could not safely rebuild the one "
                        "precondition it needs.")
    else:
        r = call("list_bones", object="Cube")
        check("T811 list_bones refuses a MESH (not an ARMATURE)",
              r.get("ok") is False and "ARMATURE" in str(r.get("error", "")), r.get("error"))

        r = call("list_modifiers", object="Cube")
        check("T811 list_modifiers on a mesh with none succeeds with an empty stack",
              r.get("ok") is True and r.get("count") == 0 and r.get("modifiers") == [], r)

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
    if probe.get("ok") is False:
        check("T812 (not exercised: run_python is disabled, the correct default - "
              "nothing here builds real rig content to adopt instead)", True)
        UNPROVEN.append("the POPULATED path for all four ops - armature-space bone positions "
                        "and parent linkage, a shape key's basis/relative pairing, a vertex "
                        "group's weighted vertex count, a decoded per-type modifier settings "
                        "dict - and object_info's armatureModifier field, which needs a real "
                        "Armature modifier to report anything but null. Needs run_python "
                        "enabled (Edit > Preferences > Add-ons > MifBlender > 'Allow "
                        "run_python') to build test content; this suite does not flip that "
                        "preference itself.")
    else:
        r = call("run_python", code=RIG_CODE)
        check("T812 building the test rig succeeded", r.get("ok") is not False, r.get("error"))

        b = call("list_bones", object="MifTestArmature")
        check("T812 list_bones succeeds on the real armature", b.get("ok") is True, json.dumps(b)[:200])
        bones = {row["name"]: row for row in (b.get("bones") or [])}

        # ------------------------------------------------------------------ T809 set_shape_key
        print("=== T809: set_shape_key, and the clamp Blender does silently ===")
        sk_ok = call("set_shape_key", object="MifTestMesh", key="Smile", value=0.5)
        check("T809 setting a shape key value succeeds", sk_ok.get("ok") is not False,
              json.dumps(sk_ok)[:200])
        check("T809 and the value read back is the one asked for",
              abs((sk_ok.get("value") or 0) - 0.5) < 1e-6, sk_ok.get("value"))
        check("T809 and it is not reported as clamped", sk_ok.get("clamped") is False,
              json.dumps(sk_ok)[:200])

        # THE ASSERTION THAT MATTERS. Blender clamps to the slider range and says NOTHING - asking for
        # 2.0 on a 0..1 key leaves 1.0 with no error anywhere. Without `clamped` a caller reads the
        # success and moves on, and finds out in a render. The op has to name the difference.
        sk_hi = call("set_shape_key", object="MifTestMesh", key="Smile", value=2.0)
        check("T809 asking for 2.0 on a 0..1 key does NOT silently succeed at 2.0",
              abs((sk_hi.get("value") or 0) - 1.0) < 1e-6, sk_hi.get("value"))
        check("T809 and the clamp is REPORTED rather than left to be found in a render",
              sk_hi.get("clamped") is True, json.dumps(sk_hi)[:200])
        check("T809 with the value actually asked for still reported alongside it",
              abs((sk_hi.get("requestedValue") or 0) - 2.0) < 1e-6, sk_hi.get("requestedValue"))
        call("set_shape_key", object="MifTestMesh", key="Smile", value=0.0)

        print("")

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

        # ------------------------------------------------------------- T814 the modifier stack
        print("")
        print("=== T814: list_modifiers reports the full stack, decoded per type ===")
        lm = call("list_modifiers", object="MifTestMesh")
        check("T814 list_modifiers succeeds", lm.get("ok") is True, json.dumps(lm)[:200])
        mods = {row["type"]: row for row in (lm.get("modifiers") or [])}
        check("T814 both modifiers are present, in stack order",
              [m["type"] for m in (lm.get("modifiers") or [])] == ["ARMATURE", "SOLIDIFY"],
              [m.get("type") for m in (lm.get("modifiers") or [])])
        if "ARMATURE" in mods:
            check("T814 ARMATURE settings name the real armature",
                  (mods["ARMATURE"].get("settings") or {}).get("object") == "MifTestArmature",
                  mods["ARMATURE"])
            check("T814 ARMATURE is enabled in viewport and render (the default)",
                  mods["ARMATURE"]["showViewport"] is True and mods["ARMATURE"]["showRender"] is True,
                  mods["ARMATURE"])
        if "SOLIDIFY" in mods:
            check("T814 SOLIDIFY settings report the real thickness",
                  abs((mods["SOLIDIFY"].get("settings") or {}).get("thickness", -1) - 0.25) < 1e-6,
                  mods["SOLIDIFY"])
            check("T814 SOLIDIFY's showRender:false round-trips (it is present but INERT at "
                  "render/export, not absent)",
                  mods["SOLIDIFY"]["showRender"] is False and mods["SOLIDIFY"]["showViewport"] is True,
                  mods["SOLIDIFY"])

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
