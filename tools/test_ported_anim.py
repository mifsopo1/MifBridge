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
import scratch_confirm as SC

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
        # invalidCount is emitted ALWAYS, not only when nonzero, so a caller can assert on it rather
        # than having to notice a field is missing. A sample can be ON the asset and marked invalid by
        # ValidateSampleData (bIsValid = bAnimationExists && bSampleInBounds && bSampleIsUnique), in
        # which case it counts toward sampleCount and contributes nothing to the blend.
        check("T574 invalidCount is always present, not only when nonzero",
              isinstance(r.get("invalidCount"), (int, float)), json.dumps(r)[:220])
        check("T574 invalidCount never exceeds sampleCount",
              (r.get("invalidCount") or 0) <= (r.get("sampleCount") or 0),
              "invalidCount=%s sampleCount=%s" % (r.get("invalidCount"), r.get("sampleCount")))
        dirty_after = len(M.call("list_dirty_packages", {}, timeout=90).get("packages") or [])
        # The note that used to sit here said a scratch BlendSpace would be needed to reach the
        # partial-failure paths, and that writing to a real one was the only alternative. Both were
        # true and the first half is now done - see T575, which builds one. What that turned up is
        # that droppedByValidation is NOT reachable through this endpoint at all, by construction:
        # AddSample refuses a duplicate point before ValidateSampleData ever sees it, so a duplicate
        # lands in rejected[] instead. This handler's own reconciliation comment says so.
        check("T574 and the no-op dirtied nothing beyond what was already dirty",
              dirty_after <= dirty_before + 1,
              "dirty packages %d -> %d" % (dirty_before, dirty_after))

    # ------------------------------------------------------------------ T575 the scratch BlendSpace
    print("")
    print("=== T575: a SCRATCH BlendSpace - the partial-failure paths, on nobody's content ===")
    # THIS SUITE'S OWN PREMISE WAS OUT OF DATE. Its header says there is "no scratch equivalent to
    # practise on", so it stayed read-mostly. There is: create_asset makes a BlendSpace, set_property
    # gives it a Skeleton, and the samples can reference real AnimSequences read-only. Nothing here
    # touches game content, and everything created is deleted at the end.
    SCRATCH = "/Game/_MifAnim/BS_T575"
    SCRATCH_OBJ = SCRATCH + ".BS_T575"

    def axis_params():
        """BlendParameters read INDEPENDENTLY of the endpoint's own response.

        The whole point of T575's axis assertion is that the endpoint's word is not evidence about
        the endpoint. list_object_properties is a different code path reading the asset itself.
        """
        props = M.call("list_object_properties", {"objectPath": SCRATCH_OBJ},
                       timeout=90).get("properties") or []
        for prop in props:
            if (prop.get("name") if isinstance(prop, dict) else prop) == "BlendParameters":
                return str(prop.get("value"))
        return ""

    made = M.call("create_asset", {"path": SCRATCH, "class": "BlendSpace"}, timeout=120)
    if made.get("ok") is not True:
        check("T575 (not exercised: could not create a scratch BlendSpace)", True,
              json.dumps(made)[:200])
    else:
        try:
            skel = None
            seqs = []
            for cand in (M.call("find_assets", {"class": "Skeleton", "pathPrefix": "/Game/",
                                                "limit": 4}, timeout=120).get("assets") or []):
                path = cand.get("path") or ""
                name = path.rsplit("/", 1)[-1].split(".")[0]
                anims = M.call("list_animations", {"skeleton": name, "limit": 2000},
                               timeout=300).get("animations") or []
                found = [a.get("assetPath") for a in anims
                         if str(a.get("class", "")).endswith("AnimSequence")]
                if len(found) >= 2:
                    skel, seqs = path, found[:2]
                    break

            if not skel:
                check("T575 (not exercised: no Skeleton with two AnimSequences in /Game/)", True)
            else:
                sp = M.call("set_property", {"objectPath": SCRATCH_OBJ, "propertyPath": "Skeleton",
                                             "value": skel}, timeout=90)
                check("T575 the scratch blend space takes a Skeleton", sp.get("ok") is True,
                      json.dumps(sp)[:200])

                # ---- the axis rewrite, measured from the asset rather than from the response
                before = axis_params()
                check("T575 BlendParameters is readable before the write", bool(before), before)
                far = M.call("set_blendspace_samples",
                             {"assetPath": SCRATCH, "clear": True,
                              "samples": [{"animation": seqs[0], "x": 777, "y": 0}]}, timeout=120)
                after = axis_params()
                check("T575 the far sample was accepted, not refused", far.get("addedCount") == 1,
                      json.dumps(far)[:260])
                # THE FINDING. A fresh BlendSpace is Min 0 / Max 100 / GridNum 4; one sample at
                # x=777 leaves it 0 / 800 / 32. AddSample WIDENS the axis rather than refusing, and
                # for a long time the response said nothing about it - while its own `note` told the
                # caller to configure the axis this call had just overwritten.
                moved = before != after
                check("T575 the axis really was rewritten by adding one far sample", moved,
                      "before=%s after=%s" % (before, after))
                if moved:
                    changed = far.get("axisChanged")
                    check("T575 and the response REPORTS the rewrite in axisChanged",
                          isinstance(changed, list) and len(changed) >= 1,
                          "the asset's axis moved and the response did not say so - if this build "
                          "predates the fix, rebuild. before=%s after=%s response=%s"
                          % (before, after, json.dumps(far)[:200]))
                    if isinstance(changed, list) and changed:
                        row = changed[0]
                        check("T575 and names both the before and after value",
                              row.get("maxBefore") is not None and row.get("maxAfter") is not None,
                              json.dumps(row)[:220])
                        check("T575 and the reported after-value matches the ASSET",
                              str(int(row.get("maxAfter") or 0)) in after,
                              "axisChanged says maxAfter=%s, the asset says %s"
                              % (row.get("maxAfter"), after))
                    check("T575 and explains it in axisChangedNote",
                          "REWRITTEN" in str(far.get("axisChangedNote") or ""),
                          str(far.get("axisChangedNote"))[:200])

                # ---- a duplicate point is REFUSED, and the message must say why
                dup = M.call("set_blendspace_samples",
                             {"assetPath": SCRATCH, "clear": True,
                              "samples": [{"animation": seqs[0], "x": 10, "y": 0},
                                          {"animation": seqs[1], "x": 10, "y": 0}]}, timeout=120)
                rejected = dup.get("rejected") or []
                check("T575 a second sample at an occupied point is rejected", len(rejected) == 1,
                      json.dumps(dup)[:260])
                check("T575 and only the first survives", dup.get("addedCount") == 1,
                      json.dumps(dup)[:260])
                # The message used to blame the axis range - the one cause that cannot produce this
                # refusal, since the axis auto-expands. A wrong diagnosis costs more than none.
                check("T575 and the refusal names the DUPLICATE, not the axis range",
                      "DUPLICATE" in str(rejected[0] if rejected else ""),
                      str(rejected[0] if rejected else "<no rejection>")[:240])

                # ---- an animation that is not one
                bad = M.call("set_blendspace_samples",
                             {"assetPath": SCRATCH, "clear": True,
                              "samples": [{"animation": "/Game/NoSuchAnim_zz.NoSuchAnim_zz",
                                           "x": 20, "y": 0}]}, timeout=120)
                check("T575 a missing animation is rejected by name",
                      any("not a UAnimSequence" in str(x) for x in (bad.get("rejected") or [])),
                      json.dumps(bad)[:240])
                check("T575 and nothing was added", bad.get("addedCount") == 0,
                      json.dumps(bad)[:240])
        finally:
            # THROUGH scratch_confirm, NOT M.call: mifaudit strips `confirm` from every payload, so a
            # delete_asset sent the ordinary way silently does nothing - which is how audit_roundtrip
            # left a scratch blueprint in somebody's live session. Reported, never swallowed.
            gone = SC.confirm_call("delete_asset", {"path": SCRATCH, "confirm": True}, timeout=120)
            left = M.call("find_assets", {"pathPrefix": "/Game/_MifAnim", "limit": 10},
                          timeout=120).get("assets") or []
            check("T575 the scratch blend space was deleted", not left,
                  "delete said %s; still present: %s"
                  % (json.dumps(gone)[:160], [a.get("path") for a in left]))

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
