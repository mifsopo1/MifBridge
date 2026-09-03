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
    """The name of some MESH in the scene that is not another suite's fixture, or None."""
    # SKIP ANOTHER SUITE'S FIXTURE. Blender objects have no /Game path, so the convention that
    # identifies scratch here is the NAME: every suite prefixes its objects Mif (MifTestArmature,
    # MifC_Merge, MifA_Fixture, MifRB_*). These suites share one Blender when run against a live
    # instance, and adopting a neighbour's half-built object means asserting about their fixture.
    listing = call("list_objects")
    for o in (listing.get("objects") or []):
        if str(o.get("name") or "").startswith("Mif"):
            continue
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


GLTF_FIXTURE = """
import bpy, os
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(size=2.0)
o = bpy.context.active_object
o.name = "MifGlbSource"
o.scale = (0.5, 1.0, 1.5)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.ops.export_scene.gltf(filepath=r"%s", export_format='GLB', use_selection=False)
result = os.path.getsize(r"%s")
"""


def gltf_checks(call, check, tmp_glb):
    """import_mesh's glTF support, added 2026-08-31.

    A 1 x 2 x 3 box, asymmetric on every axis so an axis swap shows up as a permutation of the
    dimensions - a cube would round-trip through a Y/Z swap unchanged and prove nothing.

    SKIPS rather than fails when run_python is unavailable: building the fixture needs it, and the
    addon deliberately cannot create a GLB any other way. That is the same shape test_blender_rig
    uses, and a false failure would be worse than a gap.
    """
    probe = call("run_python", code="pass")
    if probe.get("ok") is False:
        print("  SKIPPED the glTF checks - run_python is off, so the fixture cannot be built.")
        return
    made = call("run_python", code=GLTF_FIXTURE % (tmp_glb, tmp_glb))
    if made.get("ok") is False or not os.path.isfile(tmp_glb):
        print("  SKIPPED the glTF checks - the fixture export did not produce a file.")
        return

    r = call("import_mesh", file=tmp_glb, clearScene=True)
    check("M900 import_mesh accepts a .glb", r.get("ok") is not False, json.dumps(r)[:200])
    imported = (r.get("imported") or [{}])[0]
    dims = imported.get("dimensionsBU") or []
    # DIMENSIONS, not vertex counts. glTF de-indexes per corner and the count legitimately changes.
    check("M900 a 1x2x3 box comes back 1x2x3 - axis and unit preserved",
          len(dims) == 3 and all(abs(a - b) < 1e-3 for a, b in zip(dims, [1.0, 2.0, 3.0])),
          "dimensions %s" % dims)
    # The de-index is real and the caller is TOLD, because a vertex count that jumped without
    # explanation reads as corruption.
    check("M900 and the response warns that glTF de-indexes vertices",
          any("de-index" in w for w in (r.get("warnings") or [])),
          str(r.get("warnings"))[:200])

    bad = call("import_mesh", file=tmp_glb, useCustomNormals=True)
    check("M901 useCustomNormals is REFUSED for glTF, not silently ignored",
          bad.get("ok") is False and "useCustomNormals" in str(bad.get("error") or ""),
          json.dumps(bad)[:200])

    # M902 THE REGRESSION THIS EXACT CHANGE CAUSED, kept as a test because it was silent.
    # _check_format is shared by import_mesh and export_mesh. Widening one tuple to add glTF IMPORT
    # widened export too, and export_mesh does not dispatch on extension - it always calls
    # export_scene.fbx. So export_mesh {file:"x.glb"} answered ok:true and wrote a file starting
    # "Kaydara FBX Binary". A .glb no glTF loader will open, and nothing said a word.
    out_glb = tmp_glb.replace(".glb", "_export.glb")
    exp = call("export_mesh", object="MifGlbSource", file=out_glb)
    check("M902 export_mesh REFUSES a .glb path - it writes FBX only",
          exp.get("ok") is False, json.dumps(exp)[:200])
    # The refusal must explain the ASYMMETRY, because import taking glTF while export does not is
    # exactly the kind of thing a caller reads as a bug in the refusal rather than a real boundary.
    check("M902 and the refusal says import_mesh DOES take glTF, so the asymmetry reads as chosen",
          "import_mesh" in str(exp.get("error") or ""), str(exp.get("error"))[:200])
    check("M902 and nothing was written", not os.path.isfile(out_glb), out_glb)
    try:
        os.remove(out_glb)
    except OSError:
        pass


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

    # T769 calls clear_scene, and this suite never asked whose Blender answered.
    try:
        import blender_audit_common as _BC
        _BC.HOST, _BC.PORT = HOST, PORT          # this suite reads the address itself; keep them one
        stop = _BC.require_headless(
            "test_blender_mesh", lambda op, params=None: call(op, **(params or {})))
        if stop is not None:
            return stop
    except ImportError:                # never let the guard's absence break the suite in silence
        print("WARNING: blender_audit_common not importable - running WITHOUT the headless guard.")

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

    # ---- T763b the flag that made a SECOND export possible
    print("")
    print("=== T763b: overwrite - re-exporting to a path that already exists ===")
    # THE DEFAULT IS TRUE, WHICH IS THE OPPOSITE OF WHAT THIS TEST FIRST ASSUMED. ops_mesh.py:321
    # reads take_bool(params, "overwrite", "replaceExisting", default=True), so an existing path is
    # CLOBBERED unless the caller says not to. That makes the parameter a brake, not an accelerator -
    # and until 2026-08-31 bl_export_mesh had no overwrite argument at all, so over MCP there was no
    # way to protect a file. Found by param_reach once it stopped counting alias spellings as lost
    # capability, and written up backwards until the suite said so.
    #
    # It also depends on this evening's mifaudit fix: FORBIDDEN_KEYS strips `overwrite` from every
    # payload, so overwrite:false used to be deleted on the way out and the file clobbered anyway.
    # Falsey values now reach the handler, which is exactly what this asserts.
    before_size = os.path.getsize(out) if os.path.isfile(out) else -1
    again = call("export_mesh", object=name, file=out)
    check("T763b re-exporting with no flag overwrites - the default is permissive",
          again.get("ok") is not False, json.dumps(again)[:200])
    guarded = call("export_mesh", object=name, file=out, overwrite=False)
    check("T763b overwrite:false REFUSES rather than clobbering",
          guarded.get("ok") is False, json.dumps(guarded)[:220])
    check("T763b and the refusal names the flag and the alternative",
          "overwrite" in str(guarded.get("error", "")).lower(), str(guarded.get("error"))[:220])
    # A POSTCONDITION, not the response's word: a refused export must leave the existing file ALONE.
    # An implementation that truncated first and refused second would report exactly the same error.
    after_size = os.path.getsize(out) if os.path.isfile(out) else -1
    check("T763b and the refused export left the existing file untouched",
          after_size == before_size and after_size > 1000,
          "%s was %d bytes, now %d" % (out, before_size, after_size))

    print("")
    print("=== T764: refusals name what is wrong ===")
    r = call("export_mesh", object=name, file=out.replace(".fbx", ".obj"))
    check("T764 a non-FBX extension is refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T764 and says why FBX is the only one",
          "axis" in str(r.get("error", "")).lower(), str(r.get("error"))[:220])
    # AND THE LIST IT RECITES MUST BE THIS CALLER'S LIST. _check_format is shared by import_mesh and
    # export_mesh, and this branch described the IMPORT capability set to whoever called it - so an
    # export caller was told glTF/GLB were supported, and got the helper's OTHER branch saying "FBX
    # only" the moment they believed it. The verb name was right and the sentence after it was not,
    # which is harder to notice than a wrong refusal.
    # THE CLAUSE, not the substring. A first attempt here asserted glTF was absent from the message
    # entirely and failed against the CORRECT text, because naming glTF to say "this verb does not
    # take it" is the helpful half. What must be true is narrower: the list of SUPPORTED formats
    # names only what this verb can actually write.
    err = str(r.get("error", ""))
    check("T764 and the supported-format list it recites is EXPORT's, which is FBX alone",
          "supported formats are FBX (" in err, err[:300])
    imp = str(call("import_mesh", file=out.replace(".fbx", ".obj")).get("error", ""))
    check("T764 while import, which really does take glTF, is told the wider list",
          "supported formats are FBX and glTF/GLB (" in imp, imp[:300])
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
    # boundaryOnly, not allEdges: by T768's point in the suite the mesh (post-T767's forced
    # allowNonBoundary extrude) has BOTH boundary and interior edges, and bevel_edges correctly
    # REFUSES a selection that mixes the two - they need different Blender bevel algorithms
    # (affect='VERTICES' for boundary edges, affect='EDGES' for the rest; affect='EDGES' is a
    # silent no-op on a pure boundary edge - VERIFIED 2026-08-27/28, see ops_mesh.py's
    # op_bevel_edges). boundaryOnly is a PURE selection (guaranteed by _select_edges' own
    # filtering) and additionally exercises the exact case that was broken until this fix.
    b0 = verts(name)
    r = call("bevel_edges", object=name, offset=0.01, boundaryOnly=True)
    check("T768 with a selector it runs", r.get("ok") is not False, r.get("error"))
    b1 = verts(name)
    check("T768 and bevelling adds geometry", isinstance(b1, (int, float)) and b1 >= b0,
          "%s -> %s" % (b0, b1))

    print("")
    print("=== T770: decimate_mesh refuses every ambiguous request ===")
    # Six guards, each for a request that has no single correct interpretation. All of these were
    # exercised against a real 1169-triangle Unreal mesh before the op was trusted.
    name = first_mesh()
    for label, kw in (
            ("both ratio and targetTris", {"ratio": 0.5, "targetTris": 4}),
            ("neither of them", {}),
            ("a ratio above 1", {"ratio": 1.5}),
            ("a ratio of 0", {"ratio": 0}),
            ("targetTris not below the current count", {"targetTris": 999999}),
            ("an unknown mode", {"ratio": 0.5, "mode": "SQUISH"}),
            ("ratio on a mode that has no ratio", {"ratio": 0.5, "mode": "DISSOLVE"}),
    ):
        r = call("decimate_mesh", object=name, **kw)
        check("T770 %s is refused" % label, r.get("ok") is False, json.dumps(r)[:170])
    r = call("decimate_mesh", object=name, ratio=0.5, nonsense=True)
    check("T770 an unknown parameter is refused, not ignored", r.get("ok") is False,
          json.dumps(r)[:170])

    print("")
    print("=== T771: dryRun reports and changes nothing ===")
    t0 = (call("object_info", object=name) or {})
    r = call("decimate_mesh", object=name, ratio=0.5, dryRun=True)
    check("T771 dryRun succeeds", r.get("ok") is not False, r.get("error"))
    check("T771 it says it is a dry run", r.get("dryRun") is True, json.dumps(r)[:170])
    check("T771 it reports the current triangle count",
          isinstance(r.get("trisBefore"), (int, float)), r.get("trisBefore"))
    check("T771 and no trisAfter, because nothing happened", r.get("trisAfter") is None,
          r.get("trisAfter"))
    check("T771 the mesh is untouched",
          verts(name) == (t0.get("object") or t0).get("vertexCount", verts(name)),
          "dryRun changed the vertex count")

    print("")
    print("=== T772: it reports what HAPPENED, not what was asked ===")
    tris_before = r.get("trisBefore")
    d = call("decimate_mesh", object=name, ratio=0.5)
    check("T772 decimate succeeded", d.get("ok") is not False, d.get("error"))
    check("T772 it reports both before and after",
          isinstance(d.get("trisBefore"), (int, float))
          and isinstance(d.get("trisAfter"), (int, float)),
          json.dumps(d)[:200])
    check("T772 and the requested ratio separately from the ACHIEVED one",
          "ratioRequested" in d and "ratioAchieved" in d,
          "a collapse decimate cannot split a triangle to land exactly on a target, so echoing "
          "the request back would be a number that is not true: %s" % json.dumps(d)[:170])
    check("T772 triangles actually went down, or it said nothing was removed",
          d.get("trisAfter") < d.get("trisBefore") or d.get("nothingRemoved") is True,
          "%s -> %s with no nothingRemoved flag" % (d.get("trisBefore"), d.get("trisAfter")))
    print("       %s -> %s triangles (asked %s, got %s)"
          % (d.get("trisBefore"), d.get("trisAfter"),
             d.get("ratioRequested"), d.get("ratioAchieved")))

    print("")
    print("=== T773: DISSOLVE removes only what was already flat ===")
    before_d = verts(name)
    r = call("decimate_mesh", object=name, mode="DISSOLVE", angleLimit=5.0)
    check("T773 dissolve succeeded", r.get("ok") is not False, r.get("error"))
    # On a tight mesh this legitimately removes nothing, and the op must SAY so rather than
    # returning ok with two identical counts.
    if r.get("trisAfter") == r.get("trisBefore"):
        check("T773 removing nothing is stated in words, not left to be spotted",
              r.get("nothingRemoved") is True and bool(r.get("note")), json.dumps(r)[:200])
    else:
        check("T773 it removed coplanar geometry", r.get("trisAfter") < r.get("trisBefore"),
              "%s -> %s" % (r.get("trisBefore"), r.get("trisAfter")))

    print("")
    print("=== T774: uv_unwrap refuses what has no single meaning ===")
    name = first_mesh()
    for label, kw in (
            ("an unknown method", {"method": "MAGIC"}),
            ("angleLimitDeg on a method without one", {"method": "LIGHTMAP", "angleLimitDeg": 45}),
            ("an angle outside 0-90", {"angleLimitDeg": 120}),
            ("a margin outside 0-1", {"islandMargin": 2.0}),
    ):
        r = call("uv_unwrap", object=name, **kw)
        check("T774 %s is refused" % label, r.get("ok") is False, json.dumps(r)[:170])
    r = call("uv_unwrap", object=name, nonsense=True)
    check("T774 an unknown parameter is refused, not ignored", r.get("ok") is False,
          json.dumps(r)[:170])

    print("")
    print("=== T775: it will not overwrite somebody's UVs by default ===")
    info = call("object_info", object=name) or {}
    existing = ((info.get("object") or info).get("uvLayers") or [])
    if existing:
        r = call("uv_unwrap", object=name, uvLayer=existing[0])
        check("T775 an existing layer name is refused", r.get("ok") is False, json.dumps(r)[:170])
        check("T775 and the refusal names replace:true as the way to mean it",
              "replace" in str(r.get("error", "")), str(r.get("error"))[:190])
        r = call("uv_unwrap", object=name, uvLayer=existing[0], replace=True)
        check("T775 replace:true is accepted", r.get("ok") is not False, r.get("error"))
    else:
        print("       (mesh has no UV layers - overwrite guard not exercised)")

    print("")
    print("=== T776: dryRun reports and changes nothing ===")
    r = call("uv_unwrap", object=name, dryRun=True)
    check("T776 dryRun succeeds", r.get("ok") is not False, r.get("error"))
    check("T776 it says it is a dry run", r.get("dryRun") is True, json.dumps(r)[:170])
    check("T776 and no uvLayersAfter, because nothing happened",
          r.get("uvLayersAfter") is None, r.get("uvLayersAfter"))

    print("")
    print("=== T777: LIGHTMAP lands on the channel it was told to ===")
    # The one that matters for Unreal. A lightmap belongs on a SECOND UV channel, and the layer
    # has to be made active BEFORE the unwrap or the operator writes into whichever was active -
    # which is how a lightmap lands on top of the base UVs and nobody notices until the bake.
    #
    # AGAINST A FRESH IMPORT OF THE ORIGINAL CUBE, not `name` - by this point `name` has been
    # through extrude_skirt (T767, forced split via allowNonBoundary), bevel_edges (T768), a
    # COLLAPSE decimate (T772) and a DISSOLVE decimate that merges coplanar faces into n-gons
    # (T773). VERIFIED 2026-08-28: Blender 3.6.23's own built-in uv.lightmap_pack throws
    # ZeroDivisionError (uvcalc_lightmap.py prettyface.__init__, box_fit_2d projecting a
    # degenerate n-gon to zero width) on THAT battle-scarred mesh specifically - and does NOT on
    # the same cube fresh, confirmed by hand against a factory-startup instance. That is a real,
    # narrow Blender 3.6 limitation on pathological n-gon geometry, not something an ordinary
    # LIGHTMAP call on non-mangled geometry hits, and not something this suite should be
    # reporting as "LIGHTMAP is broken on 3.6" - `out` is still the untouched FBX T763 exported
    # before any of those edits ran, so re-importing it gives LIGHTMAP the same kind of input a
    # real caller actually gives it.
    fresh = call("import_mesh", file=out, clearScene=False, rename="MifLightmapFreshCube")
    fresh_name = ((fresh.get("imported") or [{}])[0]).get("name") if fresh.get("ok") is not False else None
    check("T777 setup: a fresh copy of the original cube imported", bool(fresh_name),
          json.dumps(fresh)[:200])
    r = call("uv_unwrap", object=fresh_name, method="LIGHTMAP", uvLayer="MifLightmap")
    check("T777 lightmap unwrap succeeded", r.get("ok") is not False, r.get("error"))
    check("T777 it created the named layer", r.get("createdLayer") == "MifLightmap",
          json.dumps(r)[:200])
    check("T777 and made it the active one", r.get("activeLayer") == "MifLightmap",
          r.get("activeLayer"))
    check("T777 the layer list GREW rather than being overwritten",
          len(r.get("uvLayersAfter") or []) > len(r.get("uvLayersBefore") or []),
          "%s -> %s" % (r.get("uvLayersBefore"), r.get("uvLayersAfter")))
    print("       %s -> %s" % (json.dumps(r.get("uvLayersBefore")),
                               json.dumps(r.get("uvLayersAfter"))))
    if fresh_name:
        call("delete_object", object=fresh_name)

    print("")
    print("=== T778: ANGLE without seams is warned about, not silently wrong ===")
    r = call("uv_unwrap", object=name, method="ANGLE", uvLayer="MifAngle")
    check("T778 it still runs", r.get("ok") is not False, r.get("error"))
    check("T778 and warns that there are no seams",
          any("seam" in str(w).lower() for w in (r.get("warnings") or [])),
          "no seam warning: %s - without seams the mesh flattens as one unusable island"
          % json.dumps(r.get("warnings")))

    print("")
    print("=== T779: markSeams closes the gap T778 reports, and pack/transform ride along ===")
    # T778 asserts the endpoint WARNS that ANGLE has no seams. Nothing could mark one - the
    # endpoint offered a method its callers could not use. This is the other half.
    amb = call("uv_unwrap", object=name, markSeams=True, uvLayer="MifSeamAmb")
    check("T779 markSeams:true is refused as ambiguous rather than guessing between "
          "'every edge' and 'the sharp ones', which are different meshes",
          amb.get("ok") is False and "ambiguous" in (amb.get("error") or ""),
          (amb.get("error") or "")[:200])

    none_match = call("uv_unwrap", object=name, markSeams={"minAngleDeg": 179},
                      uvLayer="MifSeamNone")
    check("T779 a criterion matching NO edge is refused - it would mark nothing and the unwrap "
          "would behave as though seams had never been asked for",
          none_match.get("ok") is False and "matched NO edges" in (none_match.get("error") or ""),
          (none_match.get("error") or "")[:220])

    seamed = call("uv_unwrap", object=name, markSeams={"minAngleDeg": 40}, method="ANGLE",
                  uvLayer="MifSeamed")
    check("T779 marking seams by dihedral angle succeeds and counts them off the MESH",
          seamed.get("ok") is not False and (seamed.get("seams") or {}).get("marked", 0) > 0
          and (seamed.get("seams") or {}).get("seamEdgesNow", 0) > 0,
          json.dumps(seamed.get("seams"))[:220])
    # THE assertion. T778's warning is the endpoint saying it cannot do its job; with seams marked
    # in the same call it stops saying it.
    check("T779 and the ANGLE no-seams warning is GONE - the method T778 shows as unusable is "
          "now reachable in one call",
          not any("seam" in str(w).lower() for w in (seamed.get("warnings") or [])),
          json.dumps(seamed.get("warnings"))[:220])

    packed = call("uv_unwrap", object=name, method="SMART", uvPack=True, uvLayer="MifPacked")
    check("T779 uvPack runs after the unwrap and reports that it did",
          packed.get("ok") is not False and packed.get("packed") is True,
          json.dumps(packed)[:200])

    xf = call("uv_unwrap", object=name, method="SMART", uvLayer="MifXf",
              uvTransform={"scale": 0.5, "offset": [0.25, 0.25]})
    t = xf.get("uvTransform") or {}
    check("T779 uvTransform reports the bounds BEFORE and AFTER, read back off the layer",
          xf.get("ok") is not False and t.get("boundsBefore") and t.get("boundsAfter"),
          json.dumps(t)[:250])
    # Measured, not assumed: a half-scale transform must halve the span.
    if t.get("boundsBefore") and t.get("boundsAfter"):
        span_before = t["boundsBefore"]["max"][0] - t["boundsBefore"]["min"][0]
        span_after = t["boundsAfter"]["max"][0] - t["boundsAfter"]["min"][0]
        check("T779 and a 0.5 scale really halved the U span - measured from the layer, not "
              "trusted from the request",
              abs(span_after - span_before * 0.5) < 1e-4,
              "before %.5f after %.5f" % (span_before, span_after))
    bad = call("uv_unwrap", object=name, uvTransform={"rotate": 45}, uvLayer="MifXfBad")
    check("T779 an unsupported uvTransform key is refused and says why rotation is not offered",
          bad.get("ok") is False and "Rotation is not offered" in (bad.get("error") or ""),
          (bad.get("error") or "")[:220])

    print("")
    print("=== T769: clear_scene empties it ===")
    r = call("clear_scene")
    check("T769 clear_scene succeeded", r.get("ok") is not False, r.get("error"))
    check("T769 and no mesh remains", first_mesh() is None,
          "a mesh survived clear_scene: %r" % first_mesh())

    # ------------------------------------------------------------------ M900 glTF/GLB import
    print("\n=== M900: import_mesh takes a .glb, and says what glTF changes ===")
    tmp_glb = os.path.join(tempfile.gettempdir(), "mif_suite_gltf.glb")
    try:
        gltf_checks(call, check, tmp_glb)
    finally:
        try:
            os.remove(tmp_glb)
        except OSError:
            pass

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
