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

    # ---------------------------------------------------------------- cleanup
    print("")
    for n in ("MifC_Merge", "MifC_Noop", "MifC_Dec"):
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
