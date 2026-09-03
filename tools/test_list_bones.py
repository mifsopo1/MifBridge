"""list_bones - the bone hierarchy, which nothing in this bridge could reach before.

USkeleton::ReferenceSkeleton is a plain C++ member, not a UPROPERTY, so reflection cannot touch it.
get_property on a Skeleton reaches BoneTree, which holds per-bone retargeting MODES and no names.
describe_animation reports curves and notifies but no tracks. list_sockets reports sockets, which
attach TO bones without enumerating them. So "what bones does this have" had no answer at all.

T222 is the test with teeth, and it is a structural cross-check rather than a restatement: every
bone's reported parent NAME must agree with its reported parentIndex, every parentIndex must point at
a real bone, the depth must equal the actual number of steps to the root, and exactly one bone may be
the root. Those four together mean the tree is internally consistent - a mis-indexed parent lookup
passes a "did it return names" check and fails this.

T223 is a scale check across every skeleton in the project rather than one sample. It exists because
this endpoint's first run reported 161 bones for four different humanoid skeletons, which looked like
a bug and was not: they are all UE5-Mannequin-structure rigs. Reading a bicycle (9), a cube (2) and a
one-joint default mesh (1) back correctly is what settled that, so it is kept as a test rather than
thrown away as a debugging step.

T790/T791 cover the two siblings added alongside list_bones later: list_virtual_bones (USkeleton's
VirtualBones array - links between two REAL bones, not in the ReferenceSkeleton list_bones walks) and
list_morph_targets (SkeletalMesh morph target names via K2_GetAllMorphTargetNames()). T791 sweeps
EVERY SkeletalMesh in the project rather than a sample, because the property worth proving is crash
safety on real cooked content - the same class of failure analyze_skeletal_split's own postmortem
describes for a DIFFERENT editor-only accessor. See T791's own comment for what this project's content
can and cannot prove about the positive (real morph delta) path.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []
UNPROVEN = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # SKIP SCRATCH: two suites duplicate a real Skeleton into their own tree, so a scratch copy has
    # the SAME bone count as the original and can win the ranking below on a tie - then be deleted
    # by its owner while this suite is still reading subtrees out of it.
    skels = [a.get("path") for a in
             (M.call("find_assets", {"class": "Skeleton", "limit": 60}).get("assets") or [])
             if not M.is_scratch_fixture(a)]
    if not skels:
        print("no skeletons in this project")
        return 3
    # Deliberate, not sampled: the biggest skeleton exercises depth, filtering and subtree selection.
    ranked = []
    for p in skels:
        q = M.call("list_bones", {"path": p})
        ranked.append((q.get("boneCount") or 0, p))
    ranked.sort(reverse=True)
    biggest = ranked[0][1]
    print("skeletons: %d   richest: %s (%d bones)" % (len(skels), biggest.split("/")[-1], ranked[0][0]))

    r = M.call("list_bones", {"path": biggest})
    bones = r.get("bones") or []

    # ------------------------------------------------------------------ T220 the read
    print("\n=== T220: the read ===")
    check("T220 it answers", r.get("ok") is True, json.dumps(r)[:160])
    check("T220 boneCount matches what was returned", r.get("boneCount") == len(bones),
          "%s vs %d" % (r.get("boneCount"), len(bones)))
    check("T220 it says which reference skeleton it read",
          r.get("source") in ("skeleton", "skeletalMesh"), r.get("source"))
    check("T220 every bone has a name and an index",
          all(b.get("name") and isinstance(b.get("index"), (int, float)) for b in bones),
          "a bone is missing a name or index")
    check("T220 indices are contiguous from zero",
          [b.get("index") for b in bones] == list(range(len(bones))),
          "indices are not 0..N-1, so the listing is not the raw bone array")

    # ------------------------------------------------------------------ T221 no transforms by default
    print("\n=== T221: the reference pose is opt-in ===")
    check("T221 transforms are absent unless asked for",
          not any("refPose" in b for b in bones), "refPose appeared without includeTransforms")
    t = M.call("list_bones", {"path": biggest, "includeTransforms": True})
    tb = t.get("bones") or []
    # GUARDED FIRST. all([]) is True, so without this the assertion below passes when the call
    # returned no bones at all - which is the one outcome it is supposed to catch.
    check("T221 the includeTransforms call returned bones to check", len(tb) > 0,
          "bones=%d - every refPose assertion below would pass vacuously" % len(tb))
    check("T221 and present when asked for", all("refPose" in b for b in tb), "refPose missing")
    # PRESENCE IS NOT THE CONTRACT. A bone carrying refPose:{} or refPose:null satisfies the check
    # above and is useless - the same presence-vs-value mistake that let 301 mislabelled rows through
    # a green check in test_cooked_class_trap. Assert the shape that makes it usable.
    def _posed(b):
        rp = b.get("refPose") or {}
        return all(isinstance(rp.get(k), dict) and set("xyz") <= set(rp.get(k) or {})
                   for k in ("location", "rotation", "scale"))
    check("T221 and each pose carries location, rotation and scale as x/y/z",
          all(_posed(b) for b in tb),
          json.dumps([b.get("refPose") for b in tb[:2]])[:220])
    # A skeleton whose every bone sits at the origin with identity scale would satisfy the shape
    # check and mean the values were never read off the asset.
    check("T221 and the poses are not all identity, which would mean nothing was read",
          any((b.get("refPose") or {}).get("location", {}).get("z") not in (0, None)
              or (b.get("refPose") or {}).get("scale", {}).get("x") not in (1, None) for b in tb),
          "every bone reports an identity transform")
    # Parent-relative, and it has to SAY so - treating these as world space stacks everything on the root.
    check("T221 it warns the pose is parent-relative",
          "PARENT-RELATIVE" in (t.get("transformNote") or ""), (t.get("transformNote") or "")[:140])

    # ------------------------------------------------------------------ T222 the tree is consistent
    print("\n=== T222 [teeth]: the hierarchy is internally consistent ===")
    by_index = {b.get("index"): b for b in bones}
    names = {b.get("index"): b.get("name") for b in bones}

    bad_parent = [b.get("name") for b in bones
                  if b.get("parentIndex") != -1 and b.get("parentIndex") not in by_index]
    check("T222 every parentIndex points at a real bone", not bad_parent, str(bad_parent[:4]))

    # The name and the index are two independent lookups; if they disagree, one of them is wrong.
    mismatch = [(b.get("name"), b.get("parent"), names.get(b.get("parentIndex")))
                for b in bones
                if b.get("parentIndex") != -1 and b.get("parent") != names.get(b.get("parentIndex"))]
    check("T222 the parent NAME agrees with the parentIndex", not mismatch, str(mismatch[:3]))

    roots = [b for b in bones if b.get("parentIndex") == -1]
    check("T222 exactly one root", len(roots) == 1, str([b.get("name") for b in roots])[:120])
    check("T222 and it is flagged as the root", roots and roots[0].get("isRoot") is True,
          json.dumps(roots[0])[:120] if roots else "no root")
    check("T222 the root is at depth 0", roots and roots[0].get("depth") == 0,
          roots[0].get("depth") if roots else None)

    # Depth recomputed independently by walking parents - the handler walks too, but from its own data.
    wrong_depth = []
    for b in bones:
        d, cur, guard = 0, b, 0
        while cur.get("parentIndex") != -1 and guard <= len(bones):
            cur = by_index[cur.get("parentIndex")]
            d += 1
            guard += 1
        if d != b.get("depth"):
            wrong_depth.append((b.get("name"), b.get("depth"), d))
    check("T222 every depth equals the real number of steps to the root",
          not wrong_depth, str(wrong_depth[:3]))

    # ------------------------------------------------------------------ T223 scale sanity
    print("\n=== T223: it distinguishes skeletons of wildly different sizes ===")
    counts = {}
    for n, p in ranked:
        counts[p.split("/")[-1].split(".")[0]] = n
    small = {k: v for k, v in counts.items() if v <= 12}
    check("T223 at least one very small skeleton is read correctly", bool(small), json.dumps(counts)[:200])
    check("T223 no skeleton reports zero bones", all(v > 0 for v in counts.values()),
          str([k for k, v in counts.items() if v <= 0]))
    # The whole point: a constant answer everywhere would mean it is not reading per-asset data.
    check("T223 the counts genuinely differ between skeletons", len(set(counts.values())) > 3,
          "distinct bone counts: %s" % sorted(set(counts.values()))[:12])

    # ------------------------------------------------------------------ T224 filters
    print("\n=== T224: filtering and subtrees ===")
    f = M.call("list_bones", {"path": biggest, "nameContains": "spine"})
    check("T224 nameContains filters", 0 < (f.get("count") or 0) < len(bones),
          "%s of %d" % (f.get("count"), len(bones)))
    check("T224 and every result matches",
          all("spine" in (b.get("name") or "") for b in (f.get("bones") or [])), "a result does not match")
    check("T224 boneCount still reports the whole skeleton",
          f.get("boneCount") == r.get("boneCount"), "%s vs %s" % (f.get("boneCount"), r.get("boneCount")))

    # A subtree must contain its own root and nothing outside it.
    pick = next((b for b in bones if b.get("depth") == 2), bones[1])
    sub = M.call("list_bones", {"path": biggest, "root": pick.get("name")})
    sub_names = {b.get("name") for b in (sub.get("bones") or [])}
    check("T224 a subtree includes its own root", pick.get("name") in sub_names, pick.get("name"))
    descendants = set()
    for b in bones:
        cur, guard = b, 0
        while cur.get("parentIndex") != -1 and guard <= len(bones):
            if cur.get("name") == pick.get("name"):
                descendants.add(b.get("name"))
                break
            cur = by_index[cur.get("parentIndex")]
            guard += 1
    descendants.add(pick.get("name"))
    check("T224 and is exactly that bone plus its descendants",
          sub_names == descendants,
          "extra=%s missing=%s" % (sorted(sub_names - descendants)[:3],
                                   sorted(descendants - sub_names)[:3]))

    # ------------------------------------------------------------------ T225 guards
    print("\n=== T225: guards ===")
    # SKIP SCRATCH - see the identical guard in test_ik_rig and test_niagara_params.
    notskel = (M.pick_adoptable(M.call("find_assets", {"class": "Material",
                                                       "limit": 20}).get("assets")) or {}).get("path")
    for label, payload, expect in (
        ("no path", {}, "path is required"),
        ("missing asset", {"path": "/Game/NoSuchSkeleton_zz"}, "no asset at"),
        ("a non-skeleton asset", {"path": notskel}, "bone hierarchy"),
        ("unknown root bone", {"path": biggest, "root": "no_such_bone_zz"}, "no bone named"),
    ):
        q = M.call("list_bones", payload)
        check("T225 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T225 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])
    s = M.call("list_bones", {"path": biggest, "socket": "x"})
    check("T225 a socket parameter points at list_sockets",
          s.get("ok") is False and "list_sockets" in (s.get("error") or ""), (s.get("error") or "")[:150])

    # ------------------------------------------------------------------ T226 mesh vs skeleton
    print("\n=== T226: a mesh and its skeleton are not assumed to agree ===")
    # SKIP SCRATCH: test_socket_authoring mints a SkeletalMesh, and T226's whole point is that a mesh
    # and its skeleton are read separately and compared - a scratch mesh whose owner deletes it
    # mid-run turns that comparison into a setup failure charged to list_bones.
    sm = (M.pick_adoptable(M.call("find_assets", {"class": "SkeletalMesh",
                                                  "limit": 20}).get("assets")) or {}).get("path")
    m = M.call("list_bones", {"path": sm})
    check("T226 a SkeletalMesh is accepted", m.get("ok") is True, json.dumps(m)[:150])
    check("T226 and it says it read the MESH's reference skeleton",
          m.get("source") == "skeletalMesh", m.get("source"))
    check("T226 it still names the owning skeleton", bool(m.get("skeleton")), m.get("skeleton"))
    check("T226 and reports the skeleton's own count for comparison",
          isinstance(m.get("skeletonBoneCount"), (int, float)), m.get("skeletonBoneCount"))
    # list_sockets once shipped correct and useless by reading only the mesh; the note is what stops
    # this making the same mistake silently.
    if m.get("skeletonBoneCount") != m.get("boneCount"):
        check("T226 a disagreement is called out, not smoothed over",
              "sourceNote" in m, sorted(m.keys()))
    else:
        check("T226 mesh and skeleton agree on this asset, so no note is needed",
              "sourceNote" not in m, m.get("sourceNote"))

    # ------------------------------------------------------------------ T790 list_virtual_bones
    print("\n=== T790: list_virtual_bones - links a rigger added BETWEEN two real bones ===")
    v = M.call("list_virtual_bones", {"path": biggest})
    check("T790 it answers", v.get("ok") is True, json.dumps(v)[:160])
    vbones = v.get("virtualBones") or []
    check("T790 count matches what was returned", v.get("count") == len(vbones),
          "%s vs %d" % (v.get("count"), len(vbones)))
    check("T790 it names the skeleton it read", bool(v.get("skeleton")), v.get("skeleton"))
    if not vbones:
        check("T790 zero is explained rather than left as a bare empty array",
              bool(v.get("note")), json.dumps(v)[:160])
    else:
        real_names = {b.get("name") for b in bones}   # `bones` is list_bones' result on `biggest`, above
        check("T790 every source bone is a real bone on this skeleton",
              all(vb.get("source") in real_names for vb in vbones),
              [vb for vb in vbones if vb.get("source") not in real_names])
        check("T790 every target bone is a real bone on this skeleton",
              all(vb.get("target") in real_names for vb in vbones),
              [vb for vb in vbones if vb.get("target") not in real_names])
        check("T790 every virtual bone has its own generated name",
              all(vb.get("name") for vb in vbones), vbones[:3])

    # EXHAUSTIVE across every Skeleton in the project (21 at the time this was written), same reason
    # as T791's full sweep: crash safety on real content is worth proving at scale, not on one sample.
    any_real_vbones = False
    vb_failed = []
    for p in skels:
        q = M.call("list_virtual_bones", {"path": p}, timeout=60)
        if not q.get("ok"):
            vb_failed.append((p, q))
            continue
        if (q.get("count") or 0) > 0:
            any_real_vbones = True
    check("T790 every one of the %d skeletons in this project answered without failing" % len(skels),
          not vb_failed, [p for p, _ in vb_failed[:5]])
    if not any_real_vbones:
        check("T790 (POSITIVE path NOT exercised: no Skeleton in this project defines any virtual "
              "bone - confirmed across all %d, not just `biggest`)" % len(skels), True)
        UNPROVEN.append("list_virtual_bones' populated-array path (source/target cross-checked "
                        "against real bone names) - no Skeleton in this project (all %d scanned) "
                        "defines a virtual bone. Crash safety IS proven across all %d." % (len(skels), len(skels)))

    # A SkeletalMesh resolves through its assigned Skeleton, same as list_bones does.
    vm = M.call("list_virtual_bones", {"path": sm})
    check("T790 a SkeletalMesh path is accepted (resolves via GetSkeleton())",
          vm.get("ok") is True, json.dumps(vm)[:160])

    for label, payload, expect in (
        ("no path", {}, "path is required"),
        ("missing asset", {"path": "/Game/NoSuchSkeleton_zz"}, "no asset at"),
        ("a non-skeleton asset", {"path": notskel}, "virtual bones"),
        ("an unknown parameter", {"path": biggest, "bone": "x"}, "filter the result"),
    ):
        q = M.call("list_virtual_bones", payload)
        check("T790 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T790 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])

    # ------------------------------------------------------------------ T791 list_morph_targets
    print("\n=== T791: list_morph_targets - names nothing else could give ===")
    # EXHAUSTIVE, not sampled: every SkeletalMesh in the project (188 at the time this was written),
    # because the thing most worth proving here is the SAME thing analyze_skeletal_split's postmortem
    # already crashed the editor over once - does this handler survive being called on real COOKED
    # content, at scale, without exception. A sample could get lucky; a full sweep cannot.
    #
    # MEASURED, NOT ASSUMED: DDS2 turns out to have ZERO morph targets on ANY of its 188 SkeletalMesh
    # assets - this project's characters are not morph-target-driven. That is a genuine finding about
    # THIS project's content, not a reason to skip the check: every one of the 188 calls still had to
    # succeed cleanly (ok:true, a real note) for T791 below to pass, which is exactly the crash-safety
    # property this endpoint's own header comment claims and needs proving, not assuming.
    #
    # WHAT THIS MACHINE CANNOT REACH: hasDataForLod:true with a real vertexCount - the POSITIVE data
    # path - has no content on this project to exercise it against. Said here rather than implied by a
    # quiet pass, same discipline test_landscape_info.py uses for the World Partition branch it cannot
    # reach either.
    # SKIP SCRATCH, and it does not weaken the "without exception" above - it enforces it. The claim
    # is about THIS PROJECT'S content, and the 188 that was measured is the project's own count; a
    # scratch mesh another suite made is not part of that population, and one deleted by its owner
    # part-way through this scan would fail T791 for a reason that is not about morph targets.
    meshes = [a.get("path") for a in
              (M.call("find_assets", {"class": "SkeletalMesh", "limit": 500}).get("assets") or [])
              if not M.is_scratch_fixture(a)]
    check("T791 there is at least one SkeletalMesh to scan", len(meshes) > 0, len(meshes))
    ranked_mt = []
    scan_failed = []
    for p in meshes:
        q = M.call("list_morph_targets", {"path": p}, timeout=60)
        if not q.get("ok"):
            scan_failed.append((p, q))
            continue
        ranked_mt.append((q.get("count") or 0, p))
    ranked_mt.sort(reverse=True)
    richest_mt_count, richest_mt = ranked_mt[0] if ranked_mt else (0, None)
    print("SkeletalMeshes scanned: %d   richest in morph targets: %s (%d)"
          % (len(meshes), (richest_mt or "").split("/")[-1] if richest_mt else "-", richest_mt_count))
    check("T791 every one of the %d real assets answered without failing" % len(meshes),
          not scan_failed, [p for p, _ in scan_failed[:5]])

    if richest_mt_count == 0:
        # A real, sayable outcome for THIS project's content - not skipped silently.
        check("T791 (POSITIVE vertexCount path NOT exercised: no SkeletalMesh in this project has "
              "any morph target - confirmed across all %d, not just a sample)" % len(meshes), True)
        UNPROVEN.append("list_morph_targets' hasDataForLod:true / vertexCount path - no SkeletalMesh "
                        "in this project (all %d scanned) has any morph target to exercise it against. "
                        "Crash safety on real cooked content IS proven (every one of the %d calls "
                        "succeeded); reading a REAL delta count is not." % (len(meshes), len(meshes)))
        r0 = M.call("list_morph_targets", {"path": meshes[0]}) if meshes else {}
        check("T791 the zero-count path still answers cleanly and explains itself",
              r0.get("ok") is True and bool(r0.get("note")), json.dumps(r0)[:200])
    else:
        r = M.call("list_morph_targets", {"path": richest_mt})
        check("T791 it answers", r.get("ok") is True, json.dumps(r)[:160])
        mts = r.get("morphTargets") or []
        # GUARDED FIRST, same reason as T221's own guard above: all([]) is True, so every all(...)
        # check below would pass vacuously if this fresh call disagreed with the scan that chose
        # richest_mt (richest_mt_count > 0 came from an EARLIER, separate call - this one re-asks the
        # same endpoint on the same asset, and a real inconsistency between the two should fail loudly
        # here rather than let every check below pass on an empty list. Dormant on DDS2 today (this
        # branch never runs - see the UNPROVEN note above), which is exactly why a static audit
        # (audit_vacuous_checks.py) found it and live execution never had the chance to.
        check("T791 the fresh call actually returned morph targets to check",
              len(mts) > 0, "morphTargets=%d despite richest_mt_count=%d - every check below would "
              "pass vacuously" % (len(mts), richest_mt_count))
        check("T791 count matches what was returned", r.get("count") == len(mts),
              "%s vs %d" % (r.get("count"), len(mts)))
        check("T791 every target has a name and its own asset path",
              all(mt.get("name") and mt.get("path") for mt in mts),
              "a morph target is missing a name or path")
        check("T791 hasDataForLod is a real bool on every entry",
              all(isinstance(mt.get("hasDataForLod"), bool) for mt in mts),
              [mt.get("hasDataForLod") for mt in mts[:5]])
        # THE FIELD THIS EXISTS TO GET RIGHT: a target reporting data must give a real vertex count;
        # one reporting no data must not claim a count at all (that would be the confusing "0 either
        # way" this handler's own comment says it refuses to produce).
        check("T791 hasDataForLod:true always carries a real vertexCount",
              all((not mt.get("hasDataForLod")) or isinstance(mt.get("vertexCount"), (int, float))
                  for mt in mts),
              [mt for mt in mts if mt.get("hasDataForLod") and "vertexCount" not in mt])
        check("T791 hasDataForLod:false never carries a vertexCount",
              all(mt.get("hasDataForLod") or "vertexCount" not in mt for mt in mts),
              [mt for mt in mts if not mt.get("hasDataForLod") and "vertexCount" in mt])
        check("T791 at least one target on the richest mesh actually has LOD0 data",
              any(mt.get("hasDataForLod") for mt in mts), [mt.get("hasDataForLod") for mt in mts[:5]])

    for label, payload, expect in (
        ("no path", {}, "path is required"),
        ("missing asset", {"path": "/Game/NoSuchMesh_zz"}, "no SkeletalMesh at"),
        ("a Skeleton rather than a mesh", {"path": biggest}, "no SkeletalMesh at"),
        ("negative lod", {"path": meshes[0] if meshes else sm, "lod": -1}, "invalid"),
        ("an unknown parameter", {"path": sm, "name": "x"}, "filter the result"),
    ):
        q = M.call("list_morph_targets", payload)
        check("T791 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T791 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    if UNPROVEN:
        print("")
        print("NOT PROVEN BY THIS SUITE (green above does not cover these):")
        for u in UNPROVEN:
            print("  - %s" % u)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
