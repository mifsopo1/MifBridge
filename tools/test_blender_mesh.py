"""The MifBlender mesh pipeline: ping, scene_info, clear_scene, export/import, select, edit.

WHY THIS EXISTS. Coverage was measured on 2026-08-27 and it was 5 ops of 18 - delete_object,
list_objects, object_info, run_python and set_material_slots. The whole mesh pipeline, which is the
reason the Blender backend exists at all, had NO test. It had been exercised by hand exactly once,
during a round trip, and that is the state this project calls "verified by someone watching it work".

SELF-CONTAINED ON PURPOSE. The fixture is made by EXPORTING the factory-startup Cube and importing
it back, so the suite needs no Unreal, no network, and no checked-in .fbx that could drift from what
the exporter actually writes today. It also means import and export are tested against each other:
if either end breaks the vertex count stops surviving the trip.

WHAT IT DELIBERATELY DOES NOT COVER. The five gen_* ops - gen_status, gen_image, gen_mesh,
gen_texture, gen_asset - call an EXTERNAL generation service over the network. A test that needs
somebody's API key and a working internet connection is a test that fails for reasons unrelated to
this repo, and one that silently skips is worse. They are named here so the gap is a decision rather
than an oversight.

  after this suite:  13 of 18 ops covered, 5 external and declared

Run it against a served Blender:

    python tools/blender_probe.py --serve 5.0     # prints the headless serve command
    python tools/test_blender_mesh.py

Exit codes:
    0  ran and passed
    1  ran and something failed
    2  SKIPPED - Blender was not reachable, nothing was verified
"""
import json
import os
import socket
import struct
import sys
import tempfile

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
    """The name of some MESH in the scene, or None."""
    listing = call("list_objects")
    for o in (listing.get("objects") or []):
        if str(o.get("type", "")).upper() == "MESH":
            return o.get("name")
    return None


def verts(name):
    info = call("object_info", object=name) or {}
    obj = info.get("object") or info
    for k in ("vertexCount", "verts", "vertices", "numVertices"):
        if k in obj:
            return obj[k]
    return None


def main():
    print("MifBlender mesh pipeline - %s:%d" % (HOST, PORT))

    # READINESS IS A PING, NOT A CONNECT. A bare connect() succeeded twice during this work against
    # a port whose owner had already gone, and the very next real call got ECONNREFUSED.
    hello = call("ping", timeout=3)
    if hello.get("ok") is not True:
        print("")
        print("SKIPPED - nothing was verified.")
        print("  Nothing answered a ping on %s:%d, so no op was exercised." % (HOST, PORT))
        print("  Start one with:  python tools/blender_probe.py --serve <version>")
        print("  Reason: %s" % str(hello.get("error"))[:150])
        return 2

    print("")
    print("=== T760: the handshake reports what it is ===")
    check("T760 ping answers", hello.get("pong") is True, json.dumps(hello)[:200])
    check("T760 and names the addon", hello.get("addon") == "MifBlender", hello.get("addon"))
    check("T760 with a protocol version", hello.get("protocolVersion") is not None,
          json.dumps(hello)[:160])
    # The Blender version is reported by PING, not by scene_info. Asserted on the wrong op first
    # and the suite said so - scene_info answers about the SCENE, which is what its name promises.
    check("T760 and the Blender version", bool(hello.get("blenderVersionString")),
          json.dumps(hello)[:200])
    check("T760 as a version tuple too", isinstance(hello.get("blenderVersion"), list),
          hello.get("blenderVersion"))
    print("       serving: Blender %s" % hello.get("blenderVersionString"))
    echoed = call("ping", echo="mifecho")
    check("T760 echo comes back", "mifecho" in json.dumps(echoed), json.dumps(echoed)[:180])

    print("")
    print("=== T761: scene_info describes the running Blender ===")
    s = call("scene_info")
    check("T761 scene_info answers", s.get("ok") is not False, s.get("error"))
    check("T761 it names the scene", bool(s.get("sceneName")), json.dumps(s)[:200])
    check("T761 and counts what is in it", isinstance(s.get("objectCount"), (int, float)),
          s.get("objectCount"))
    check("T761 broken down by type", isinstance(s.get("objectsByType"), dict),
          s.get("objectsByType"))
    check("T761 and says it is running headless", s.get("background") is True, s.get("background"))
    print("       scene %r, %s objects %s"
          % (s.get("sceneName"), s.get("objectCount"), json.dumps(s.get("objectsByType"))))

    print("")
    print("=== T762: there is a mesh to work with ===")
    name = first_mesh()
    if not name:
        print("       no MESH in the scene - trying clear_scene + factory Cube is not possible")
        print("       headlessly without run_python, so this suite cannot proceed.")
        check("T762 a mesh exists to test against", False,
              "scene has no MESH object; start Blender with --factory-startup")
        return 1
    check("T762 found a mesh", True)
    v0 = verts(name)
    check("T762 and it reports a vertex count", isinstance(v0, (int, float)), v0)
    print("       object %r, %s vertices" % (name, v0))

    print("")
    print("=== T763: export_mesh writes a real FBX ===")
    out = os.path.join(tempfile.gettempdir(), "mifblender_test_export.fbx")
    if os.path.isfile(out):
        os.remove(out)
    r = call("export_mesh", object=name, file=out)
    check("T763 export succeeded", r.get("ok") is not False, r.get("error"))
    size = os.path.getsize(out) if os.path.isfile(out) else -1
    check("T763 and the file exists with real content", size > 1000,
          "%s is %d bytes - an FBX header alone is bigger than nothing but not a mesh" % (out, size))
    print("       wrote %d bytes" % size)

    print("")
    print("=== T764: refusals name what is wrong ===")
    r = call("export_mesh", object=name, file=out.replace(".fbx", ".obj"))
    check("T764 a non-FBX extension is refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T764 and says why FBX is the only one",
          "axis" in str(r.get("error", "")).lower(), str(r.get("error"))[:220])
    r = call("export_mesh", object="MifNoSuchObject", file=out)
    check("T764 an unknown object is refused", r.get("ok") is False, json.dumps(r)[:180])
    r = call("import_mesh", file=os.path.join(tempfile.gettempdir(), "mif_does_not_exist.fbx"))
    check("T764 importing a missing file is refused", r.get("ok") is False, json.dumps(r)[:180])
    r = call("export_mesh", object=name, file=out, nonsense=True)
    check("T764 an unknown parameter is refused, not ignored", r.get("ok") is False,
          json.dumps(r)[:180])

    print("")
    print("=== T765: import_mesh reads back what export_mesh wrote ===")
    # The two ops test each other. If either end breaks, the vertex count stops surviving the trip -
    # which is a stronger statement than either op answering ok on its own.
    r = call("import_mesh", file=out, clearScene=True)
    check("T765 import succeeded", r.get("ok") is not False, r.get("error"))
    back = first_mesh()
    check("T765 a mesh arrived", bool(back), "nothing in the scene after import")
    if back:
        v1 = verts(back)
        check("T765 and the vertex count SURVIVED the round trip", v1 == v0,
              "%s out, %s back - the FBX axis/scale pinning is what keeps these equal" % (v0, v1))
        print("       %s -> %s vertices" % (v0, v1))

    print("")
    print("=== T766: select_edges refuses to guess, then selects ===")
    name = first_mesh()
    r = call("select_edges", object=name)
    check("T766 no selector is refused", r.get("ok") is False, json.dumps(r)[:180])
    check("T766 and the refusal names THIS op, not a sibling",
          "select_edges" in str(r.get("error", "")),
          "a shared helper used to blame bevel_edges for all three of its callers: %s"
          % str(r.get("error"))[:170])
    r = call("select_edges", object=name, boundaryOnly=True)
    check("T766 boundaryOnly is accepted", r.get("ok") is not False, r.get("error"))
    r = call("select_edges", object=name, allEdges=True)
    check("T766 allEdges is accepted", r.get("ok") is not False, r.get("error"))
    check("T766 and it reports how many it selected",
          any(k in r for k in ("selected", "count", "edgeCount", "edges")), json.dumps(r)[:200])

    print("")
    print("=== T767: extrude_skirt changes the geometry ===")
    before = verts(name)
    # A CLOSED CUBE CANNOT HAVE A SKIRT, and extrude_skirt refuses it TWO different correct ways.
    # Both are worth pinning, because both are guards against a silently meaningless result:
    #   boundaryOnly -> 0 of 12 edges match, since a closed manifold has no boundary
    #   allEdges     -> all 12 are interior, and extruding those SPLITS the mesh along a seam
    #                   that is invisible from outside rather than adding a skirt
    # The hand-run that first exercised this used an open Unreal gizmo, which is why it worked.
    r = call("extrude_skirt", object=name, depth=2.0, boundaryOnly=True)
    check("T767 a closed mesh has no boundary to skirt, and it says so",
          r.get("ok") is False and "0" in str(r.get("error", "")), str(r.get("error"))[:190])
    r = call("extrude_skirt", object=name, depth=2.0, allEdges=True)
    check("T767 and interior edges are refused rather than silently splitting it",
          r.get("ok") is False and "boundary" in str(r.get("error", "")).lower(),
          str(r.get("error"))[:190])
    check("T767 the refusal names the escape hatch", "allowNonBoundary" in str(r.get("error", "")),
          str(r.get("error"))[:190])
    # The documented way to mean it. This is the positive path for a closed mesh.
    r = call("extrude_skirt", object=name, depth=2.0, allEdges=True, allowNonBoundary=True)
    check("T767 extrude succeeded once told to mean it", r.get("ok") is not False, r.get("error"))
    after = verts(name)
    check("T767 and the mesh actually GREW", isinstance(after, (int, float))
          and isinstance(before, (int, float)) and after > before,
          "%s -> %s - an op that reports ok and changes nothing is the failure this checks for"
          % (before, after))
    print("       %s -> %s vertices" % (before, after))

    print("")
    print("=== T768: bevel_edges, the other consumer of the shared selector ===")
    # bevel_edges validates its OFFSET before it reaches the shared selector, so a bare call is
    # refused for the offset - not the selector. Both refusals are worth pinning, in the order
    # they actually happen.
    r = call("bevel_edges", object=name)
    check("T768 a bare call is refused", r.get("ok") is False, json.dumps(r)[:180])
    check("T768 for the missing offset, which it checks first",
          "offset" in str(r.get("error", "")), str(r.get("error"))[:180])
    r = call("bevel_edges", object=name, offset=0.01)
    check("T768 with an offset but no selector, the SELECTOR is refused",
          r.get("ok") is False and "selector" in str(r.get("error", "")),
          str(r.get("error"))[:180])
    check("T768 and that refusal names bevel_edges, not a sibling",
          "bevel_edges" in str(r.get("error", "")), str(r.get("error"))[:180])
    b0 = verts(name)
    r = call("bevel_edges", object=name, offset=0.01, allEdges=True)
    check("T768 with a selector it runs", r.get("ok") is not False, r.get("error"))
    b1 = verts(name)
    check("T768 and bevelling adds geometry", isinstance(b1, (int, float)) and b1 >= b0,
          "%s -> %s" % (b0, b1))

    print("")
    print("=== T769: clear_scene empties it ===")
    r = call("clear_scene")
    check("T769 clear_scene succeeded", r.get("ok") is not False, r.get("error"))
    check("T769 and no mesh remains", first_mesh() is None,
          "a mesh survived clear_scene: %r" % first_mesh())

    try:
        os.remove(out)
    except OSError:
        pass

    print("")
    print("=" * 70)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % (f[0],))
        print("          %s" % (f[1],))
    print("NOT COVERED, and deliberately: gen_status / gen_image / gen_mesh / gen_texture /")
    print("gen_asset all call an external generation service over the network.")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
