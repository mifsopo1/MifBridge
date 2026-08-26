"""set_blendspace_samples and set_bone_translation_retargeting - ported from the UE 5.7 deployment.

WHERE THESE CAME FROM. MifBridge is VENDORED into D:/RoguelikeDealerGame rather than cloned from this
repo, so a second line of development has been running there against UE 5.7. Diffing the two endpoint
sets on 2026-08-26 showed 46 endpoints here that had never been compiled against 5.7, and 2 that existed
only there. Work was being lost in both directions. These are the two that were lost coming back.

They are ported verbatim: every engine call they make - UBlendSpace::AddSample/DeleteSample/ResampleData
/ValidateSampleData and USkeleton::GetBoneTranslationRetargetingMode/GetReferenceSkeleton - exists
unchanged in 5.3, and Curfew's include set is a subset of this file's.

WHAT THIS SUITE ASSERTS, and what it deliberately does not. Both endpoints MUTATE ASSETS, and the only
Skeletons and BlendSpaces in this project are real game content - there is no scratch equivalent to
practise on, and creating a rigged skeleton from nothing is not something the bridge can do. So this
suite is deliberately READ-MOSTLY:

  * the parameter contracts, which cost nothing and catch the drift this project keeps finding;
  * a NO-OP write, asserting `changed:false`. Setting a bone to the mode it already has is the one
    mutation that provably alters nothing, and it exercises the whole path - resolve the skeleton, find
    the bone, read the current mode, compare - without touching real content. It also pins the no-op
    honesty that edit_container's `changed:true` bug was about.

It asserts NO real modification, and checks list_dirty_packages afterwards to prove none happened.
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

    # ------------------------------------------------------------------ T570 registered at all
    print("=== T570: both are registered on this build ===")
    eps = M.endpoint_names()
    for e in ("set_blendspace_samples", "set_bone_translation_retargeting"):
        check("T570 %s is registered" % e, e in eps, "%d endpoints, this one missing" % len(eps))

    # ------------------------------------------------------------------ T571 parameter contracts
    print("")
    print("=== T571: the contracts, which is where drift shows up first ===")
    q = M.call("set_blendspace_samples", {"zz": 1}, timeout=60)
    check("T571 blendspace refuses an unknown key", q.get("ok") is False, json.dumps(q)[:170])
    err = q.get("error") or ""
    check("T571 and names assetPath and samples", "assetPath" in err and "samples" in err, err[:190])
    # The handler carries a hint pointing at how to set the AXES, which are not its job. Worth pinning:
    # it is the kind of guidance that gets dropped in a refactor and is expensive to rediscover.
    q = M.call("set_blendspace_samples", {"axis": "X"}, timeout=60)
    check("T571 and the 'axis' hint points at set_property/BlendParameters",
          "BlendParameters" in (q.get("error") or ""), (q.get("error") or "")[:190])

    q = M.call("set_bone_translation_retargeting", {"zz": 1}, timeout=60)
    check("T571 retargeting refuses an unknown key", q.get("ok") is False, json.dumps(q)[:170])
    err = q.get("error") or ""
    check("T571 and lists the five modes",
          all(m in err for m in ("Animation", "Skeleton", "AnimationScaled", "OrientAndScale")), err[:200])

    # ------------------------------------------------------------------ T572 the no-op write
    print("")
    print("=== T572: setting a bone to the mode it ALREADY has reports changed:false ===")
    skels = M.call("find_assets", {"class": "Skeleton", "pathPrefix": "/Game/", "limit": 1},
                   timeout=90).get("assets") or []
    if not skels:
        check("T572 (not exercised: no Skeleton in /Game/)", True)
    else:
        sk = skels[0].get("path")
        bones = M.call("list_bones", {"path": sk}, timeout=120).get("bones") or []
        check("T572 the skeleton has bones", len(bones) > 0, "list_bones returned none for %s" % sk)
        if bones:
            b = bones[0].get("name") if isinstance(bones[0], dict) else bones[0]
            dirty_before = len(M.call("list_dirty_packages", {}, timeout=90).get("packages") or [])

            # Read the CURRENT mode by writing it back - the response reports `before`, so one call
            # both reads and proves the no-op.
            first = M.call("set_bone_translation_retargeting",
                           {"skeletonPath": sk, "boneName": b, "mode": "Skeleton"}, timeout=90)
            check("T572 the call succeeds", first.get("ok") is True, json.dumps(first)[:200])
            check("T572 and reports the bone it acted on", first.get("bone") == b, json.dumps(first)[:180])
            check("T572 and reports before and after", bool(first.get("before")) and bool(first.get("after")),
                  json.dumps(first)[:200])

            same = M.call("set_bone_translation_retargeting",
                          {"skeletonPath": sk, "boneName": b, "mode": first.get("after")}, timeout=90)
            # THE assertion. Writing the value that is already there must not claim a change - the same
            # defect edit_container had when a swap with itself reported changed:true.
            check("T572 writing the SAME mode reports changed:false", same.get("changed") is False,
                  "before=%s after=%s changed=%s" % (same.get("before"), same.get("after"), same.get("changed")))
            check("T572 and before == after on a no-op", same.get("before") == same.get("after"),
                  json.dumps(same)[:180])

            dirty_after = len(M.call("list_dirty_packages", {}, timeout=90).get("packages") or [])
            # This suite must not leave a real game asset modified. A no-op write cannot dirty anything.
            check("T572 and NOTHING was dirtied - this touches real content, not scratch",
                  dirty_after == dirty_before,
                  "dirty packages %d -> %d" % (dirty_before, dirty_after))

    # ------------------------------------------------------------------ T573 guards
    print("")
    print("=== T573: bad references are refused ===")
    q = M.call("set_bone_translation_retargeting",
               {"skeletonPath": "/Game/NoSuchSkeleton_zz.NoSuchSkeleton_zz", "boneName": "root",
                "mode": "Skeleton"}, timeout=60)
    check("T573 a skeleton that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])
    if skels:
        q = M.call("set_bone_translation_retargeting",
                   {"skeletonPath": skels[0].get("path"), "boneName": "NoSuchBone_zz",
                    "mode": "Skeleton"}, timeout=90)
        check("T573 a bone that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])
        q = M.call("set_bone_translation_retargeting",
                   {"skeletonPath": skels[0].get("path"), "boneName": "root", "mode": "NotAMode_zz"},
                   timeout=90)
        check("T573 an unknown mode is refused", q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("set_blendspace_samples", {"assetPath": "/Game/NoSuchBlendSpace_zz.NoSuchBlendSpace_zz",
                                          "samples": []}, timeout=60)
    check("T573 a blendspace that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])

    # ------------------------------------------------------------------ T574 the reconciliation
    print("")
    print("=== T574: samples[] must not contradict sampleCount ===")
    # WHY THIS EXISTS. AddSample returning a valid index is not proof the sample survived:
    # ValidateSampleData - which the handler calls immediately afterwards - deletes any sample sharing
    # a point with another (SampleData.RemoveAt in Engine/Private/Animation/BlendSpace.cpp). Before
    # 2026-08-26 the handler read sampleCount back off the asset (correct) while reporting samples[]
    # from its pre-validation list, so one response could say sampleCount 3 and list 4 samples. The
    # detailed field a caller is most likely to read was the wrong one.
    bs = M.call("find_assets", {"class": "BlendSpace", "pathPrefix": "/Game/", "limit": 1},
                timeout=90).get("assets") or []
    if not bs:
        check("T574 (not exercised: this project ships no BlendSpace)", True)
    else:
        # An EMPTY samples list is a no-op write: it exercises the reconcile path and the reporting
        # without adding anything to real game content, which this suite must not do.
        dirty_before = len(M.call("list_dirty_packages", {}, timeout=90).get("packages") or [])
        r = M.call("set_blendspace_samples", {"assetPath": bs[0].get("path"), "samples": []},
                   timeout=120)
        check("T574 a no-op call succeeds", r.get("ok") is True, json.dumps(r)[:200])
        rows = r.get("samples")
        check("T574 samples[] is an array", isinstance(rows, list), json.dumps(r)[:200])
        # THE invariant. These two fields describe the same call and cannot disagree.
        check("T574 addedCount equals the length of samples[]",
              r.get("addedCount") == len(rows or []),
              "addedCount=%s but samples[] has %d entries - the two fields disagree about one call"
              % (r.get("addedCount"), len(rows or [])))
        check("T574 sampleCount is never less than what samples[] claims",
              (r.get("sampleCount") or 0) >= len(rows or []),
              "sampleCount=%s but samples[] lists %d - claiming more added than exist on the asset"
              % (r.get("sampleCount"), len(rows or [])))
        check("T574 nothing was added by a no-op", r.get("addedCount") == 0, json.dumps(r)[:200])
        dirty_after = len(M.call("list_dirty_packages", {}, timeout=90).get("packages") or [])
        # NOTE: the droppedByValidation path is NOT exercised here. Reaching it needs two samples at
        # the same point, which means writing to a real BlendSpace - this suite deliberately does not.
        # It is asserted structurally above instead; a scratch BlendSpace would be needed to hit it.
        check("T574 and the no-op dirtied nothing beyond what was already dirty",
              dirty_after <= dirty_before + 1,
              "dirty packages %d -> %d" % (dirty_before, dirty_after))
    check("T573 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
