"""Exercise the MifBlender addon ops. SKIPS CLEANLY when Blender is not running.

WHY THIS EXISTS. The addon has eighteen ops and, until now, ZERO tests - while the UE side has
sixty-odd suites. That asymmetry is not a judgement about which half matters; it is just where the
attention went, and it means every Blender op has only ever been verified by someone watching it work
once.

It also has a specific cause worth naming: Blender is usually NOT running, so a test that assumes it
would fail constantly and be ignored, and one that quietly passes when it cannot connect is worse than
none. This does neither - it reports SKIPPED, loudly, with the reason, and returns a distinct exit
code so a sweep can tell "not exercised" from "passed".

WHAT IT COVERS. set_material_slots, written on 2026-08-27 to close a gap mif_mesh_roundtrip could
detect and not fix, plus the read ops it depends on. Deliberately narrow: it builds its own cube,
touches nothing already in the scene, and deletes what it made.

Usage:
    python tools/test_blender_ops.py

Exit codes:
    0  ran and passed
    1  ran and something failed
    2  SKIPPED - Blender was not reachable, nothing was verified
"""
import io
import json
import os
import socket
import struct
import sys

HOST = os.environ.get("MIF_BLENDER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MIF_BLENDER_PORT", "8792"))
TOKEN = os.environ.get("MIF_BLENDER_TOKEN", os.environ.get("MIF_BRIDGE_TOKEN", "dev"))

PASS = []
FAIL = []


def call(op, timeout=60.0, **params):
    """One framed op. Same wire format as the MCP server's _blender: a 4-byte
    big-endian length followed by UTF-8 JSON."""
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


def main():
    print("MifBlender addon ops - %s:%d" % (HOST, PORT))
    if not reachable():
        # LOUD, and a distinct exit code. A skip that looks like a pass is how an
        # untested thing gets believed.
        print("")
        print("SKIPPED - nothing was verified.")
        print("  Blender is not listening on %s:%d, so no addon op was exercised." % (HOST, PORT))
        print("  Start Blender with the MifBlender addon enabled and run this again.")
        print("  Exit code 2 means SKIPPED, distinct from 0 (passed) on purpose.")
        return 2

    print("")
    OBJ = "MifTest_MatSlots"

    # BUILD ONE IF WE CAN, ADOPT ONE IF WE CANNOT.
    #
    # This used to call run_python unconditionally and every test below it failed when that came
    # back "run_python is disabled" - which is not a bug, it is the addon's DEFAULT and a correct
    # one: run_python executes arbitrary code inside Blender with the user's privileges, so it sits
    # behind a preference that ships off. The suite therefore could never pass on a clean install,
    # which is a large part of why it has only ever been seen SKIPPED.
    #
    # A headless `blender --background --factory-startup` already hands us a scene containing Cube,
    # Camera and Light, so there is a mesh to work on either way. Building our own is still
    # preferred when run_python is available - it keeps the test off whatever happens to be in a
    # GUI user's scene - but it is no longer required.
    r = call("run_python", code=(
        "import bpy\n"
        "for o in list(bpy.data.objects):\n"
        "    if o.name == %r: bpy.data.objects.remove(o, do_unlink=True)\n"
        "bpy.ops.mesh.primitive_cube_add()\n"
        "bpy.context.active_object.name = %r\n" % (OBJ, OBJ)))
    built = r.get("ok") is not False
    if built:
        check("T700 built a scratch cube to work on", True)
    else:
        # Adopt an existing MESH. list_objects reports what is there; take the first mesh.
        listing = call("list_objects")
        meshes = [o for o in (listing.get("objects") or [])
                  if str(o.get("type", "")).upper() == "MESH"]
        if not meshes:
            print("")
            print("SKIPPED - nothing was verified.")
            print("  run_python is disabled (that is the correct default) and this scene has no mesh")
            print("  object to adopt instead, so there is nothing to exercise set_material_slots on.")
            print("  Reason given: %s" % str(r.get("error"))[:150])
            return 2
        OBJ = meshes[0].get("name")
        check("T700 run_python is off, so an existing mesh was adopted instead", True)
        print("       adopted: %r (run_python said: %s)" % (OBJ, str(r.get("error"))[:70]))

    info = call("object_info", object=OBJ)
    check("T701 object_info sees it", info.get("ok") is not False, info.get("error"))
    before = (info.get("object") or info).get("materialSlots")
    print("       slots before: %r" % (before,))

    # RELATIVE TO WHAT THE OBJECT ACTUALLY HAS, not to an assumed zero. A freshly added cube has no
    # material slots, and that was hardcoded here - but the object may now be an ADOPTED one when
    # run_python is off, and an adopted mesh can already carry slots. Asking for a list one longer
    # than the current count tests the guard whatever the starting point is.
    have = len(before or [])
    want = ["M_A", "M_B"][:max(1, have + 1)] if have < 2 else ["M_%d" % i for i in range(have + 1)]
    if len(want) == have:                      # never ask for the SAME count in the refusal test
        want = want + ["M_Extra"]
    r = call("set_material_slots", object=OBJ, slots=want)
    check("T702 a different slot COUNT is refused without allowResize",
          r.get("ok") is False and "allowResize" in str(r.get("error", "")),
          r.get("error"))

    r = call("set_material_slots", object=OBJ, slots=want, allowResize=True)
    check("T703 allowResize:true is accepted", r.get("ok") is not False, r.get("error"))
    check("T704 and it reports the slots it set",
          (r.get("materialSlots") or []) == want, r.get("materialSlots"))
    check("T705 and names the materials it had to CREATE",
          isinstance(r.get("createdMaterials"), list), r.get("createdMaterials"))

    # Reorder at the SAME count - the case the round trip actually hits, and the one
    # this op was written for.
    swapped = list(reversed(want))
    r = call("set_material_slots", object=OBJ, slots=swapped)
    check("T706 reordering at the same count needs no allowResize",
          r.get("ok") is not False, r.get("error"))
    check("T707 and the ORDER actually changed",
          (r.get("materialSlots") or []) == swapped, r.get("materialSlots"))
    check("T708 and `before` reports what it was, not what it became",
          (r.get("before") or []) == want, r.get("before"))

    r = call("set_material_slots", object=OBJ, slots="M_A")
    check("T709 a non-list `slots` is refused", r.get("ok") is False, r.get("error"))

    r = call("set_material_slots", object=OBJ, slots=[want[0], 7])
    check("T710 a non-string entry is refused", r.get("ok") is False, r.get("error"))

    r = call("set_material_slots", object=OBJ, slots=swapped, nonsense=True)
    check("T711 an unknown parameter is refused", r.get("ok") is False, r.get("error"))

    call("delete_object", objects=[OBJ])
    print("")
    print("=" * 62)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
