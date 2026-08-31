"""The addon's response fields that report what an op DESTROYED, and nothing was reading them.

WHY THIS FILE EXISTS. The UE half spent a day on this class: 64 response fields that report a
consequence the caller did not ask for and cannot otherwise see, and the ones nothing asserted were
the ones most likely to be wrong when it mattered. Asked of the Blender half on 2026-08-31, the
answer was worse in proportion - 11 consequence-shaped keys in the addon, ONE of them read by any
suite:

    clean_mesh      vertsRemoved, edgesRemoved, facesRemoved, discardedCustomSplitNormals
    decimate_mesh   trisRemoved
    normalize_weights  influencesDropped
    export_mesh     seamVertsRemoved
    bevel_edges     edgeIndicesTruncated        (and two more ops)
    clear_scene     removed, removedCount

Every one of them reports geometry or data that is GONE. A caller who does not read them cannot tell
a clean run from one that quietly ate half the mesh.

WHAT THIS SUITE ASSERTS, and the shape is the same throughout: the reported number must agree with an
INDEPENDENT before/after measurement of the object, never just be present. A count that is merely
present proves the field exists; a count that matches the mesh proves it was measured.

Usage:
    python tools/test_blender_consequence.py     # needs a Blender with MifBlender listening

Exit codes:  0 passed   1 failed   2 SKIPPED, no Blender
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blender_audit_common as B

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)[:240]))


def counts(name):
    """verts/edges/faces/tris read through object_info - a different op from the one under test.

    THE COUNTS LIVE UNDER `object`, and reading the top level instead cost two wrong verdicts in one
    evening: create_primitive nests the same block, and both times the missing key read as None and
    turned a real comparison into None == None. mesh_counts (ops_common.py:205) is what fills it -
    verts, edges, faces, tris - and object_info returns it whole rather than flattening.
    """
    r = B.call("object_info", {"object": name})
    return (r.get("object") or {}) if r.get("ok") is not False else {}


def main():
    if not B.reachable():
        return B.skip_banner("consequence")

    B.call("clear_scene", {})

    # ---------------------------------------------------------------- C100 clean_mesh
    print("=== C100: clean_mesh reports what it REMOVED, and the numbers must agree ===")
    made = B.call("create_primitive", {"kind": "cube", "name": "MifC_Merge", "size": 2})
    check("C100 (setup) a cube exists", made.get("ok") is True, json.dumps(made)[:200])
    before = counts("MifC_Merge")
    check("C100 (setup) it has 8 verts to lose", (before.get("verts") or 0) == 8, before)

    # A merge distance larger than the cube collapses corners into each other. That is a destructive
    # request made on purpose - the point is whether the response says how destructive.
    r = B.call("clean_mesh", {"object": "MifC_Merge", "mergeDistance": 3.0})
    check("C100 clean_mesh succeeds", r.get("ok") is not False, json.dumps(r)[:220])
    steps = r.get("steps") or {}
    merged = steps.get("merged") or {}
    check("C100 and it reports a merged step with vertsRemoved",
          isinstance(merged.get("vertsRemoved"), (int, float)), json.dumps(steps)[:220])
    after = counts("MifC_Merge")
    # THE ASSERTION WITH TEETH. The reported number has to match what the MESH lost, measured through
    # object_info. A field that is present but wrong is worse than absent: it reads as a measurement.
    lost = (before.get("verts") or 0) - (after.get("verts") or 0)
    check("C100 vertsRemoved AGREES with an independent object_info before/after",
          merged.get("vertsRemoved") == lost,
          "reported %r, the mesh lost %r (%s -> %s verts)"
          % (merged.get("vertsRemoved"), lost, before.get("verts"), after.get("verts")))
    check("C100 and something really was removed - a 0 == 0 match proves nothing",
          lost > 0, "the cube kept all %s verts, so this comparison is vacuous" % after.get("verts"))
    check("C100 and changedAnything says so", r.get("changedAnything") is True, json.dumps(r)[:220])

    # ---------------------------------------------------------------- C101 the no-op arm
    print("")
    print("=== C101: a clean that changes nothing says so, rather than reporting a cheerful ok ===")
    B.call("create_primitive", {"kind": "cube", "name": "MifC_Noop", "size": 2})
    noop = B.call("clean_mesh", {"object": "MifC_Noop", "mergeDistance": 0.0001})
    check("C101 the call succeeds", noop.get("ok") is not False, json.dumps(noop)[:200])
    check("C101 and changedAnything is FALSE on a mesh with nothing to merge",
          noop.get("changedAnything") is False, json.dumps(noop)[:240])
    n_after = counts("MifC_Noop")
    check("C101 and the mesh really is untouched - 8 verts still",
          (n_after.get("verts") or 0) == 8, n_after)

    # ---------------------------------------------------------------- C102 recalcNormals
    print("")
    print("=== C102: discardedCustomSplitNormals - the field that says authored data was thrown away ===")
    # On a mesh WITHOUT custom split normals the answer must be False rather than absent, so a caller
    # can branch on it either way. The guard for the True case is asserted below instead of forced:
    # authoring custom split normals needs run_python, and the refusal is the half that protects
    # somebody's shading work.
    rn = B.call("clean_mesh", {"object": "MifC_Noop", "recalcNormals": True})
    check("C102 recalcNormals succeeds on a plain mesh", rn.get("ok") is not False,
          json.dumps(rn)[:220])
    step = (rn.get("steps") or {}).get("recalcNormals") or {}
    check("C102 and discardedCustomSplitNormals is present as a real bool, not absent",
          isinstance(step.get("discardedCustomSplitNormals"), bool), json.dumps(step)[:220])
    check("C102 and it is FALSE, because this mesh had none to discard",
          step.get("discardedCustomSplitNormals") is False, json.dumps(step)[:220])

    # ---------------------------------------------------------------- C103 decimate_mesh
    print("")
    print("=== C103: trisRemoved must match the triangles the mesh actually lost ===")
    B.call("create_primitive", {"kind": "uvsphere", "name": "MifC_Dec", "radius": 1,
                                "segments": 32, "ringCount": 16})
    d_before = counts("MifC_Dec")
    check("C103 (setup) the sphere has real triangle count", (d_before.get("tris") or 0) > 200,
          d_before)
    dec = B.call("decimate_mesh", {"object": "MifC_Dec", "ratio": 0.5})
    check("C103 decimate succeeds", dec.get("ok") is not False, json.dumps(dec)[:220])
    d_after = counts("MifC_Dec")
    lost_tris = (d_before.get("tris") or 0) - (d_after.get("tris") or 0)
    check("C103 and it really removed triangles", lost_tris > 0,
          "%s -> %s tris" % (d_before.get("tris"), d_after.get("tris")))
    check("C103 trisRemoved AGREES with object_info before/after",
          dec.get("trisRemoved") == lost_tris,
          "reported %r, the mesh lost %r" % (dec.get("trisRemoved"), lost_tris))

    # ---------------------------------------------------------------- C106 dissolveDegenerate
    print("")
    print("=== C106: facesRemoved and edgesRemoved - the OTHER two counts clean_mesh reports ===")
    # C100 asserted vertsRemoved from the merge step and stopped there; the derived audit
    # (audit_blender_consequence_fields.py) then named these two as still unread, which is exactly
    # what a derived number is for - a hand-picked assertion covers what the author happened to
    # notice, and the tool covers the rest.
    #
    # dissolve_degenerate removes zero-area faces and zero-length edges. Collapsing a cube's corners
    # onto each other with a huge merge distance manufactures exactly that, so the two steps run in
    # one call and each reports its own losses.
    B.call("create_primitive", {"kind": "uvsphere", "name": "MifC_Degen", "radius": 1,
                                "segments": 16, "ringCount": 8})
    g_before = counts("MifC_Degen")
    deg = B.call("clean_mesh", {"object": "MifC_Degen", "mergeDistance": 0.35,
                                "dissolveDegenerate": True})
    check("C106 clean_mesh with dissolveDegenerate succeeds", deg.get("ok") is not False,
          json.dumps(deg)[:220])
    dstep = (deg.get("steps") or {}).get("dissolvedDegenerate") or {}
    check("C106 it reports a dissolvedDegenerate step with BOTH counts",
          isinstance(dstep.get("facesRemoved"), (int, float))
          and isinstance(dstep.get("edgesRemoved"), (int, float)),
          json.dumps(deg.get("steps"))[:260])
    g_after = counts("MifC_Degen")
    # The two steps both remove geometry, so neither count alone equals the mesh's total loss - what
    # CAN be asserted is that the reported losses never exceed it, and that the mesh really shrank.
    # An over-report is the failure mode worth catching: a count larger than the mesh lost is a
    # number measured from the wrong thing.
    lost_faces = (g_before.get("faces") or 0) - (g_after.get("faces") or 0)
    lost_edges = (g_before.get("edges") or 0) - (g_after.get("edges") or 0)
    check("C106 and the mesh really lost faces and edges", lost_faces > 0 and lost_edges > 0,
          "faces %s -> %s, edges %s -> %s" % (g_before.get("faces"), g_after.get("faces"),
                                              g_before.get("edges"), g_after.get("edges")))
    check("C106 facesRemoved never exceeds what the mesh actually lost",
          (dstep.get("facesRemoved") or 0) <= lost_faces,
          "reported %r, the mesh lost %r" % (dstep.get("facesRemoved"), lost_faces))
    check("C106 edgesRemoved never exceeds what the mesh actually lost",
          (dstep.get("edgesRemoved") or 0) <= lost_edges,
          "reported %r, the mesh lost %r" % (dstep.get("edgesRemoved"), lost_edges))
    check("C106 and the response says the call changed something",
          deg.get("changedAnything") is True, json.dumps(deg)[:220])

    # ---------------------------------------------------------------- C104 edgeIndicesTruncated
    print("")
    print("=== C104: edgeIndicesTruncated - the caller is reading a PARTIAL list and must know ===")
    # The most dangerous field of the eleven, because a truncated array looks exactly like a short
    # one. select_edges caps edgeIndices at maxReported (default 512) and reports whether it cut -
    # so `count` and `len(edgeIndices)` are INDEPENDENT numbers, and a caller who assumes they agree
    # is silently working from a partial answer.
    B.call("create_primitive", {"kind": "cube", "name": "MifC_Edges", "size": 2})
    cut = B.call("select_edges", {"object": "MifC_Edges", "allEdges": True, "maxReported": 4})
    check("C104 select_edges succeeds with a small maxReported", cut.get("ok") is not False,
          json.dumps(cut)[:220])
    idx = cut.get("edgeIndices") or []
    check("C104 the array really was capped at maxReported", len(idx) == 4, len(idx))
    check("C104 and count reports the TRUE total, not the array length",
          (cut.get("count") or 0) == 12,
          "count=%r on a cube, which has 12 edges" % cut.get("count"))
    check("C104 and edgeIndicesTruncated says the list is partial",
          cut.get("edgeIndicesTruncated") is True, json.dumps(cut)[:240])
    # THE INVARIANT. Truncation must be exactly the disagreement between the two numbers - if the
    # flag and the lengths can disagree, the flag is decoration.
    check("C104 truncated is TRUE precisely when the array is shorter than the count",
          cut.get("edgeIndicesTruncated") == (len(idx) < (cut.get("count") or 0)),
          "flag=%r len(edgeIndices)=%d count=%r"
          % (cut.get("edgeIndicesTruncated"), len(idx), cut.get("count")))

    whole = B.call("select_edges", {"object": "MifC_Edges", "allEdges": True, "maxReported": 100})
    w_idx = whole.get("edgeIndices") or []
    check("C104 and with room to spare the flag is FALSE",
          whole.get("edgeIndicesTruncated") is False, json.dumps(whole)[:240])
    check("C104 and then the array and the count agree",
          len(w_idx) == (whole.get("count") or 0),
          "len=%d count=%r" % (len(w_idx), whole.get("count")))

    # ---------------------------------------------------------------- C105 removedCount
    print("")
    print("=== C105: removedCount must match its own list AND the scene ===")
    for n in ("MifC_Del1", "MifC_Del2", "MifC_Keep"):
        B.call("create_primitive", {"kind": "cube", "name": n, "size": 1})
    gone = B.call("delete_object", {"objects": ["MifC_Del1", "MifC_Del2"]})
    check("C105 delete_object succeeds", gone.get("ok") is not False, json.dumps(gone)[:220])
    removed = gone.get("removed") or []
    check("C105 removedCount matches the length of its own list",
          gone.get("removedCount") == len(removed),
          "removedCount=%r removed=%s" % (gone.get("removedCount"), removed))
    check("C105 and the list NAMES what went, not just how many",
          sorted(str(x) for x in removed) == ["MifC_Del1", "MifC_Del2"], removed)
    # THE POSTCONDITION, through a different op. A response can report anything; the scene is what
    # the caller will actually find.
    survivors = [o.get("name") for o in (B.call("list_objects").get("objects") or [])]
    check("C105 and the named objects are really gone from the scene",
          not [n for n in ("MifC_Del1", "MifC_Del2") if n in survivors], survivors)
    check("C105 and the one NOT named survived - a delete that took more than it reported "
          "would look identical in the count",
          "MifC_Keep" in survivors, survivors)

    # ---------------------------------------------------------------- cleanup
    print("")
    for n in ("MifC_Merge", "MifC_Noop", "MifC_Dec", "MifC_Edges", "MifC_Keep",
              "MifC_Degen"):
        B.call("delete_object", {"object": n})
    survivors = [o.get("name") for o in (B.call("list_objects").get("objects") or [])]
    check("C199 (cleanup) no MifC_* object is left behind",
          not [n for n in survivors if str(n).startswith("MifC_")], survivors)

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
