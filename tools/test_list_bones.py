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
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    skels = [a.get("path") for a in
             (M.call("find_assets", {"class": "Skeleton", "limit": 60}).get("assets") or [])]
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
    check("T221 and present when asked for", all("refPose" in b for b in tb), "refPose missing")
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
    notskel = (M.call("find_assets", {"class": "Material", "limit": 1}).get("assets") or [{}])[0].get("path")
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
    sm = (M.call("find_assets", {"class": "SkeletalMesh", "limit": 1}).get("assets") or [{}])[0].get("path")
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

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
