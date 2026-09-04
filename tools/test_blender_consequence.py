"""The addon's response fields that report what an op DESTROYED, and nothing was reading them.

WHY THIS FILE EXISTS. The UE half spent a day on this class: 64 response fields that report a
consequence the caller did not ask for and cannot otherwise see, and the ones nothing asserted were
the ones most likely to be wrong when it mattered. Asked of the Blender half on 2026-08-31, the
answer was worse in proportion - 11 consequence-shaped keys in the addon, ONE of them read by any
suite:

    clean_mesh      vertsRemoved, edgesRemoved, facesRemoved, discardedCustomSplitNormals
    decimate_mesh   trisRemoved
    normalize_weights  influencesDropped
    bevel_edges     edgeIndicesTruncated, seamVertsRemoved   (extrude_skirt: both too)
    extrude_skirt   seamVertsRemoved
    clear_scene     removed, removedCount

Every one of them reports geometry or data that is GONE. A caller who does not read them cannot tell
a clean run from one that quietly ate half the mesh.

WHAT THIS SUITE ASSERTS, and the shape is the same throughout: the reported number must agree with an
INDEPENDENT before/after measurement of the object, never just be present. A count that is merely
present proves the field exists; a count that matches the mesh proves it was measured.

AND THEN CALL IT AGAIN. That is the standing rule of this file - and it is NOT original to it, which
is worth saying because the UE half got there first and the two should not drift apart.
tools/test_idempotence.py opens on the same premise: "a setup script gets re-run ... so what does the
second identical call do is a question every add_* endpoint has to answer, and the answers were not
the same". It found add_component quietly making Turret1, Turret2 for a caller who did not compare.
This file learned the same lesson from the other end - a REPORT that repeats rather than an object
that duplicates:
where a second identical call cannot change anything, its report must say so. The before/after
cross-check is necessary and NOT sufficient - it is one measurement, and a count recomputed from the
request rather than from the mesh agrees with it perfectly the first time and then repeats itself
forever. normalize_weights did exactly that: run 1 correctly dropped 32 influences, run 2 changed
nothing and reported dropping 32 again, along with verticesLimited 8 and maxInfluencesSeenBefore 8.
The cross-check passed both times. Only the repeat exposed it (C107, fixed in ops_rig.py).

Not every op is idempotent in EFFECT - decimating twice legitimately removes more - so the rule is
narrower than "call everything twice": where the effect cannot repeat, the REPORT must not either.

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

    # ---------------------------------------------------------------- C108 the merge repeat
    print("")
    print("=== C108: merging an already-merged mesh must report NOTHING, not the same work twice ===")
    # The second instance of the rule above, on a different op. C100 proved the first merge's count
    # against object_info; this proves the count is measured from the MESH rather than recomputed
    # from the request - the exact defect C107 found in normalize_weights, asked of clean_mesh.
    m_before = counts("MifC_Merge")
    twice = B.call("clean_mesh", {"object": "MifC_Merge", "mergeDistance": 3.0})
    check("C108 the repeat call succeeds", twice.get("ok") is not False, json.dumps(twice)[:220])
    m_after = counts("MifC_Merge")
    check("C108 and the mesh really is unchanged by it",
          (m_after.get("verts") or -1) == (m_before.get("verts") or -2),
          "verts %s -> %s" % (m_before.get("verts"), m_after.get("verts")))
    again_merged = (twice.get("steps") or {}).get("merged") or {}
    check("C108 so vertsRemoved must be 0 the second time",
          again_merged.get("vertsRemoved") == 0,
          "reported %r on a mesh that did not move" % again_merged.get("vertsRemoved"))
    check("C108 and changedAnything must be FALSE",
          twice.get("changedAnything") is False, json.dumps(twice)[:240])

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

    # ---------------------------------------------------------------- C107 influencesDropped
    print("")
    print("=== C107: influencesDropped - weights Unreal would have thrown away silently ===")
    # WHY THIS FIELD IS THE MOST CONSEQUENTIAL OF THE ELEVEN, in the op's own words: Unreal's GPU
    # skin cache supports a bounded number of influences per vertex, and the FBX importer DROPS the
    # smallest weights past that limit and renormalises SILENTLY. A mesh that deforms correctly in
    # Blender deforms differently in Unreal and neither tool says why. normalize_weights is where a
    # caller finds out first - if they read the number.
    #
    # The fixture needs authored vertex weights, which no addon op can create from nothing:
    # transfer_weights needs a source that already has them and normalize_weights only edits what is
    # there. run_python is the way, and run_blender_suites enables it by default (see serve()'s
    # comment on the preference and the empty-prefs workaround). A refusal here is therefore a real
    # finding about the runner rather than a reason to skip, so it FAILS rather than passing quietly.
    setup = B.call("run_python", {"code": (
        "import bpy\n"
        "bpy.ops.mesh.primitive_cube_add(size=2, location=(0,0,0))\n"
        "o = bpy.context.active_object\n"
        "o.name = 'MifC_Weights'\n"
        "for i in range(8):\n"
        "    g = o.vertex_groups.new(name='B%d' % i)\n"
        "    g.add(range(len(o.data.vertices)), 0.125, 'REPLACE')\n"
    )})
    check("C107 (setup) run_python authored the weights - the runner enables it by default, so a "
          "refusal here is a finding about the runner, not a reason to skip",
          setup.get("ok") is not False, json.dumps(setup)[:240])

    if setup.get("ok") is not False:
        def influence_total(name):
            """Sum of weightedVertexCount across groups - the total number of influences.

            Computed from list_vertex_groups, which is a DIFFERENT op that knows nothing about
            normalize_weights' counters. That is the point: influencesDropped has to agree with a
            number the op under test did not produce.
            """
            r = B.call("list_vertex_groups", {"object": name})
            return sum((g.get("weightedVertexCount") or 0) for g in (r.get("vertexGroups") or []))

        t_before = influence_total("MifC_Weights")
        check("C107 (setup) every vertex really carries 8 influences",
              t_before == 8 * 8, "total influences = %r, expected 64 (8 verts x 8 groups)" % t_before)

        norm = B.call("normalize_weights", {"object": "MifC_Weights", "maxInfluences": 4})
        check("C107 normalize_weights succeeds", norm.get("ok") is not False, json.dumps(norm)[:240])
        check("C107 and it reports how many influences it dropped",
              isinstance(norm.get("influencesDropped"), (int, float)), json.dumps(norm)[:240])
        t_after = influence_total("MifC_Weights")
        check("C107 influencesDropped AGREES with list_vertex_groups before/after",
              norm.get("influencesDropped") == (t_before - t_after),
              "reported %r, the mesh lost %r (%d -> %d influences)"
              % (norm.get("influencesDropped"), t_before - t_after, t_before, t_after))
        check("C107 and something really was dropped - a 0 == 0 match proves nothing",
              (t_before - t_after) > 0, "influences did not move from %d" % t_before)
        check("C107 and verticesLimited names how many vertices were touched",
              (norm.get("verticesLimited") or 0) > 0, json.dumps(norm)[:240])

        # IDEMPOTENCE, which is the assertion a fabricated number cannot survive. Nothing is left
        # above the cap, so a second identical call must report dropping NOTHING - a count that
        # simply echoes the request, or recomputes from the cap rather than from the mesh, would
        # report the same figure twice.
        again = B.call("normalize_weights", {"object": "MifC_Weights", "maxInfluences": 4})
        check("C107 a second identical call drops NOTHING - the count is measured, not echoed",
              again.get("influencesDropped") == 0,
              "second run reported %r after the first already capped it"
              % again.get("influencesDropped"))
        B.call("delete_object", {"object": "MifC_Weights"})

    # ---------------------------------------------------------------- C108 seamVertsRemoved
    print("")
    print("=== C108: seamVertsRemoved - the bucket that stops movedOffSeam:0 reading as CLEAN ===")
    # THE FIELD EXISTS FOR A MISREADING. _seam_verdict sorts every vertex it tracked on a seam
    # plane into buckets: destroyed by the op (seamVertsRemoved), survived but drifted off
    # (movedOffSeam), or survived in place. A caller who reads only movedOffSeam sees 0 and
    # concludes the seam is intact - when the truthful reading may be that there is nothing LEFT
    # to have moved. ops_mesh.py:618 says so in as many words. Nothing asserted it.
    #
    # It is NESTED, under seamPlanarity[axisLetter], which is the other half of why it went
    # unread: a suite looking for a top-level key finds nothing and reports clean.
    B.call("create_primitive", {"kind": "cube", "name": "MifC_Seam", "size": 2})
    bev = B.call("bevel_edges", {"object": "MifC_Seam", "allEdges": True, "offset": 0.1})
    check("C108 bevel_edges succeeds", bev.get("ok") is not False, json.dumps(bev)[:220])
    seam = bev.get("seamPlanarity") or {}
    check("C108 the report is nested under seamPlanarity, per axis",
          sorted(seam.keys()) == ["X", "Y", "Z"], sorted(seam.keys()))

    # THE INVARIANT, on every axis. The buckets partition the TRACKED set, so the two reported
    # ones can never between them exceed the population they were drawn from. If they can, the
    # numbers are computed from the request rather than counted off the mesh.
    for letter in sorted(seam):
        row = seam[letter] or {}
        before = row.get("onSeamBefore") or 0
        gone = row.get("seamVertsRemoved")
        moved = row.get("movedOffSeam")
        check("C108 %s: removed+moved cannot exceed the set they were drawn from" % letter,
              isinstance(gone, int) and isinstance(moved, int) and gone + moved <= before,
              "onSeamBefore=%r seamVertsRemoved=%r movedOffSeam=%r" % (before, gone, moved))
        # AND THE CHECK MUST BE REACHED. An invariant over an empty tracked set holds vacuously,
        # which is how a field goes on being unmeasured while a suite reports it green.
        check("C108 %s: and the tracked set was NOT empty, so that check could fail" % letter,
              before > 0, "onSeamBefore=%r" % before)

    x = seam.get("X") or {}
    # THE DOCUMENTED CLAIM, pinned. ops_mesh.py:620 states the measurement: this bevel destroys
    # all 8 cube corners and rebuilds 8 in the same places. If that ever stops being true the
    # comment becomes a lie about the field's own worked example.
    check("C108 the bevel really did destroy tracked seam verts",
          (x.get("seamVertsRemoved") or 0) > 0, json.dumps(x))
    check("C108 and it destroyed ALL of them, as the source comment claims",
          x.get("seamVertsRemoved") == x.get("onSeamBefore"),
          "removed=%r onSeamBefore=%r" % (x.get("seamVertsRemoved"), x.get("onSeamBefore")))
    # THIS IS THE WHOLE POINT OF THE FIELD, asserted directly rather than described: the two
    # numbers a caller might read say opposite things about the same run.
    check("C108 movedOffSeam reads 0 on that very run - which alone would say CLEAN",
          x.get("movedOffSeam") == 0 and (x.get("seamVertsRemoved") or 0) > 0,
          "movedOffSeam=%r seamVertsRemoved=%r" % (x.get("movedOffSeam"), x.get("seamVertsRemoved")))
    check("C108 and the plane is repopulated, so 'removed' never meant 'plane is now empty'",
          (x.get("onSeamAfter") or 0) > 0, "onSeamAfter=%r" % x.get("onSeamAfter"))

    # THE NO-PLANT, through the OTHER op that emits this field. A count that is recomputed from
    # the request instead of the mesh agrees with a positive case perfectly and then reports the
    # same thing forever - so the field is only proven once something that destroys nothing
    # reports zero. extrude_skirt adds geometry below the boundary and moves no original vertex,
    # so every bucket must be empty while the tracked set stays non-empty.
    B.call("create_primitive", {"kind": "plane", "name": "MifC_Skirt", "size": 2})
    skirt = B.call("extrude_skirt", {"object": "MifC_Skirt", "boundaryOnly": True, "depth": 0.5})
    check("C108 extrude_skirt succeeds", skirt.get("ok") is not False, json.dumps(skirt)[:260])
    s_seam = skirt.get("seamPlanarity") or {}
    check("C108 extrude_skirt reports the same nested block", sorted(s_seam.keys()) == ["X", "Y", "Z"],
          sorted(s_seam.keys()))
    for letter in sorted(s_seam):
        row = s_seam[letter] or {}
        check("C108 %s: a skirt destroys no tracked vert, so the count is 0" % letter,
              row.get("seamVertsRemoved") == 0,
              "seamVertsRemoved=%r onSeamBefore=%r" % (row.get("seamVertsRemoved"),
                                                       row.get("onSeamBefore")))
        check("C108 %s: and it had verts to destroy, so the 0 is measured not vacuous" % letter,
              (row.get("onSeamBefore") or 0) > 0, "onSeamBefore=%r" % row.get("onSeamBefore"))

    # ---------------------------------------------------------------- cleanup
    print("")
    for n in ("MifC_Merge", "MifC_Noop", "MifC_Dec", "MifC_Edges", "MifC_Keep",
              "MifC_Degen", "MifC_Seam", "MifC_Skirt"):
        B.call("delete_object", {"object": n})
    survivors = [o.get("name") for o in (B.call("list_objects").get("objects") or [])]
    check("C199 (cleanup) no MifC_* object is left behind",
          not [n for n in survivors if str(n).startswith("MifC_")], survivors)

    # ------------------------------------------------------------------
    # C110  A STALE READ IS A WRONG ANSWER, AND IT IS NOW LABELLED
    # ------------------------------------------------------------------
    # Blender keeps live edits in a separate BMesh, so mesh.polygons answers with the state from the
    # last time OBJECT mode was left. face_info read it and reported that number as current:
    # measured on 5.0.1, deleting a face in edit mode left it saying 6 while the live mesh had 5.
    #
    # NOT REFUSED, unlike the WRITE path - set_shading in edit mode is refused outright because a
    # write there is silently discarded on the way out. A read stays available and says what it is,
    # because this addon drives a LIVE editor where somebody may simply be in edit mode on
    # something else, and refusing every query for the duration would remove a capability to
    # prevent a mistake the caller can now see.
    print("")
    print("=== C110: face_info says when its figures are the last OBJECT-mode state ===")
    B.call("create_primitive", {"kind": "cube", "name": "MifC_Stale", "size": 2})
    clean = B.call("face_info", {"object": "MifC_Stale"})
    check("C110 face_info in OBJECT mode does NOT claim staleness - the negative control, without "
          "which a flag that was always on would pass this whole section",
          not clean.get("editModeStale"), json.dumps(clean)[:200])

    # EDIT MODE AND A REAL EDIT. Entering it is the one thing no op does - deliberately, since
    # every mesh op requires OBJECT mode - so this needs run_python, and skips politely without it,
    # the same way A101's aim check does.
    edit = B.call("run_python", {"code": (
        "import bpy, bmesh\n"
        "ob = bpy.data.objects['MifC_Stale']\n"
        "bpy.context.view_layer.objects.active = ob\n"
        "ob.select_set(True)\n"
        "bpy.ops.object.mode_set(mode='EDIT')\n"
        "bm = bmesh.from_edit_mesh(ob.data)\n"
        "bm.faces.ensure_lookup_table()\n"
        "bmesh.ops.delete(bm, geom=[bm.faces[0]], context='FACES')\n"
        "bmesh.update_edit_mesh(ob.data)\n"
        "result = len(bmesh.from_edit_mesh(ob.data).faces)\n")})
    if edit.get("ok") is False:
        check("C110 (skipped) the stale-read check needs run_python, which is disabled here",
              True, str(edit.get("error"))[:120])
    else:
        live = edit.get("result")
        dirty = B.call("face_info", {"object": "MifC_Stale"})
        B.call("run_python", {"code": "import bpy\nbpy.ops.object.mode_set(mode='OBJECT')\n"})
        check("C110 face_info in EDIT mode flags editModeStale and says the figures are the last "
              "OBJECT-mode state - the count really is wrong (%s live vs %s reported), so the flag "
              "is the only thing between the caller and a confident wrong answer"
              % (live, dirty.get("faceCount")),
              bool(dirty.get("editModeStale"))
              and "OBJECT mode was last left" in (dirty.get("staleNote") or "")
              and dirty.get("faceCount") != live,
              json.dumps(dirty)[:220])

    # ------------------------------------------------------------------
    # C111  AN EDIT TO A SHARED MESH CHANGES OBJECTS THE CALLER NEVER NAMED
    # ------------------------------------------------------------------
    # Alt+D makes a linked duplicate: two objects, one mesh datablock. It is how anyone lays out
    # repeated geometry, so a real scene is full of them - and editing the mesh through one object
    # changes every object that shares it. Measured on 5.0.1: clean_mesh and set_shading each
    # changed a second object and NOTHING in either response mentioned it.
    #
    # REPORTED, NOT REFUSED, unlike apply_transform, which refuses this outright because applying a
    # transform MOVES the others - "one of which you did not ask about". Editing the shared mesh is
    # the opposite case: changing one crate to change all of them is the entire point of a linked
    # duplicate. What was missing is the caller knowing it happened.
    print("")
    print("=== C111: an edit to a SHARED mesh says which other objects it changed ===")
    B.call("create_primitive", {"kind": "cube", "name": "MifC_ShareA", "size": 2})
    solo = B.call("set_shading", {"object": "MifC_ShareA", "smooth": True})
    check("C111 a single-user mesh does NOT claim to be shared - the negative control, without "
          "which a note that was always present would pass",
          not solo.get("meshSharedWith"), json.dumps(solo)[:200])

    # No op makes a linked duplicate - there is no reason for one - so this needs run_python, and
    # skips politely without it, the same way A101's aim check does.
    dup = B.call("run_python", {"code": (
        "import bpy\n"
        "a = bpy.data.objects['MifC_ShareA']\n"
        "b = bpy.data.objects.new('MifC_ShareB', a.data)\n"
        "bpy.context.scene.collection.objects.link(b)\n"
        "result = a.data.users\n")})
    if dup.get("ok") is False:
        check("C111 (skipped) the shared-mesh check needs run_python, which is disabled here",
              True, str(dup.get("error"))[:120])
    else:
        r = B.call("set_shading", {"object": "MifC_ShareA", "smooth": False})
        shared = r.get("meshSharedWith") or []
        check("C111 set_shading on a shared mesh names the OTHER object it changed (users=%s, "
              "named %s) - the edit really does land on both, so silence here is the caller "
              "believing they touched one" % (dup.get("result"), shared),
              "MifC_ShareB" in shared and r.get("alsoChangedCount") == 1,
              json.dumps(r)[:240])

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
