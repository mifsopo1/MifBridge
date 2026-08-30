"""PhysicsAsset authoring: bodies, constraints, and the body-pair collision table.

WHY describe_physics_asset EXISTS AT ALL, since almost everything about a PhysicsAsset is already
reachable. SkeletalBodySetups, ConstraintSetup and every FKAggregateGeom inside them are ordinary
UPROPERTYs, and ResolvePropertyPathEx crosses object pointers, so get_property walks the lot today.
Building a second reader for the same fields would be the parallel-system mistake this spec has
declined before (FEATURE_PARITY_SPEC.md:2686). It earns its place on exactly two things reflection
CANNOT give:

  1. disabledPairs. CollisionDisableTable (PhysicsAsset.h:245) is a bare TMap<FRigidBodyIndexPair,bool>
     with NO UPROPERTY, so no get_property call can reach it - and it is the single most confusing
     part of a ragdoll. T2900 reads a real project asset that has 105 disabled pairs, none of which
     were visible from any endpoint before.
  2. The INDEX numbering that every write verb here consumes, and which SHIFTS on every removal.

T2902 IS THE CRASH TEST, and it is the reason this suite exists at all. TWO engine functions take an
index and never check it:

    void DestroyConstraint(UPhysicsAsset* PhysAsset, int32 ConstraintIndex)
    {
        check(PhysAsset);                                  // validates the ASSET, not the index
        PhysAsset->ConstraintSetup.RemoveAt(ConstraintIndex);
    }

DestroyBody (PhysicsAssetUtils.cpp:1229) ends in the same bare RemoveAt. An out-of-range index from a
caller is an editor crash, not an error return - so both are bounds-checked in the handler before the
engine is touched. This test passes index 99 to each and then asks self_audit whether the editor is
still answering, because a failed guard here is a dead process rather than a bad response.

NOT OFFERED, and named rather than silently missing: the per-PRIMITIVE collision variant.
UPhysicsAsset::SetPrimitiveCollision (PhysicsAsset.cpp:305) carries a hard check() on the body index
AND an ensure() that compares a per-TYPE PrimitiveIndex against GetElementCount(), which is the TOTAL
across spheres, boxes, capsules and convex hulls. That check is simply wrong: PrimitiveType=Box with
PrimitiveIndex=3, on a body holding 5 elements of which 1 is a box, passes the ensure and then indexes
BoxElems[3] out of range. Guarding it correctly means validating against the per-type array the engine
failed to check - its own piece of work, filed rather than half-done. The body-PAIR table used here
has no such defect.

WRITES ONLY TO A SCRATCH ASSET. The project's real PhysicsAssets are read (T2900) and never modified.

CLEANS UP: the scratch asset is deleted at the end. Nothing is saved.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    PA = "/Game/_MifPhys/PA_MifTest%d" % st
    made = False

    try:
        # ------------------------------------------------------------------ T2900 the read half
        print("=== T2900: the one thing reflection cannot reach - the disable table ===")
        # SCRATCH ASSETS ARE EXCLUDED, and skipping this cost a false failure once. find_assets
        # returns /Game/_Mif* leftovers alongside real content and sorts them first, so taking [0]
        # picked up a two-body probe asset with no disabled pairs and failed an assertion about
        # real ragdolls. The asset this reads must be one the PROJECT authored.
        real = [a for a in (M.call("find_assets", {"class": "PhysicsAsset", "limit": 25})
                            .get("assets") or [])
                if not a["path"].startswith("/Game/_Mif")]
        check("T2900 (setup) the project has a real PhysicsAsset to read", len(real) > 0, len(real))
        if real:
            d = M.call("describe_physics_asset", {"assetPath": real[0]["path"]})
            check("T2900 describe_physics_asset reads a real (cooked) asset", d.get("ok") is True,
                  json.dumps(d)[:250])
            # ASSERTS THE VALUES, not that the keys exist. "index" being present says nothing;
            # what the write verbs actually depend on is that the indices are the real positional
            # ones - 0..n-1, in order - and that every body names a bone.
            bodies = d.get("bodies") or []
            check("T2900 it reports bodies whose indices really are 0..n-1 in order, which is what "
                  "the write verbs consume",
                  len(bodies) > 0
                  and [b.get("index") for b in bodies] == list(range(len(bodies)))
                  and all(isinstance(b.get("boneName"), str) and b["boneName"] for b in bodies),
                  json.dumps(bodies[:2])[:250])
            # THE justification for this endpoint. CollisionDisableTable has no UPROPERTY, so this
            # is data no get_property call can produce.
            check("T2900 and disabledPairs - a real ragdoll has many, and nothing else could see them",
                  (d.get("disabledPairCount") or 0) > 0, d.get("disabledPairCount"))
            check("T2900 each disabled pair names both bones, not just indices",
                  all(p.get("boneA") and p.get("boneB") for p in (d.get("disabledPairs") or [])[:5])
                  and len(d.get("disabledPairs") or []) > 0,
                  json.dumps((d.get("disabledPairs") or [])[:2])[:200])
            # It must NOT claim to be the only way to read a PhysicsAsset.
            check("T2900 and it points at get_property for everything reflection already covers",
                  "get_property" in (d.get("note") or ""), (d.get("note") or "")[:200])

        # ------------------------------------------------------------------ T2901 bodies
        print("\n=== T2901: creating bodies on a scratch asset ===")
        c = M.raw_post("create_asset", {"path": PA, "class": "PhysicsAsset"})
        check("T2901 (setup) a scratch PhysicsAsset can be created", c.get("ok") is True,
              json.dumps(c)[:250])
        if not c.get("ok"):
            return 1
        made = True

        a = M.raw_post("add_physics_body", {"assetPath": PA, "boneName": "root",
                                            "geomType": "sphere"})
        check("T2901 a body can be added", a.get("ok") is True, json.dumps(a)[:250])
        check("T2901 and reports the index the write verbs use", a.get("bodyIndex") == 0
              and a.get("bodyCount") == 1, json.dumps(a)[:250])
        # THE honest note. CreateNewBody makes the setup and fits NO geometry, so a caller who is
        # not told this believes they have a working collision body and does not.
        check("T2901 it says the body has no collision primitives yet, rather than implying a "
              "working ragdoll", a.get("primitiveCount") == 0
              and "does not fit geometry" in (a.get("note") or ""), json.dumps(a)[:280])

        M.raw_post("add_physics_body", {"assetPath": PA, "boneName": "spine", "geomType": "box"})
        dup = M.raw_post("add_physics_body", {"assetPath": PA, "boneName": "root"})
        check("T2901 a second body on the same bone is refused", dup.get("ok") is False,
              json.dumps(dup)[:250])
        badgeom = M.raw_post("add_physics_body", {"assetPath": PA, "boneName": "neck",
                                                  "geomType": "blob"})
        check("T2901 an unknown geomType is refused and the real ones listed",
              badgeom.get("ok") is False and "sphyl" in (badgeom.get("error") or ""),
              (badgeom.get("error") or "")[:200])
        check("T2901 neither refusal changed the body count",
              M.call("describe_physics_asset", {"assetPath": PA}).get("bodyCount") == 2,
              M.call("describe_physics_asset", {"assetPath": PA}).get("bodyCount"))

        # ------------------------------------------------------------------ T2902 the crash guards
        print("\n=== T2902: two engine calls take an index and never check it ===")
        b99 = M.raw_post("remove_physics_body", {"assetPath": PA, "index": 99, "confirm": True})
        check("T2902 an out-of-range BODY index is refused", b99.get("ok") is False,
              json.dumps(b99)[:250])
        check("T2902 and the refusal names the unguarded RemoveAt, so the reason is the crash",
              "unguarded RemoveAt" in (b99.get("error") or ""), (b99.get("error") or "")[:220])
        c99 = M.raw_post("remove_physics_constraint", {"assetPath": PA, "index": 99,
                                                       "confirm": True})
        check("T2902 an out-of-range CONSTRAINT index is refused", c99.get("ok") is False,
              json.dumps(c99)[:250])
        check("T2902 and names why check(PhysAsset) does not help - it validates the asset pointer",
              "ASSET pointer" in (c99.get("error") or ""), (c99.get("error") or "")[:220])
        # THE assertion: a failed guard is a dead editor, so it answering is the proof.
        alive = M.call("self_audit", {})
        check("T2902 - the editor is still alive after both", alive.get("ok") is True,
              "DestroyBody/DestroyConstraint end in unguarded RemoveAt; a failed guard is a crash")

        # ------------------------------------------------------------------ T2903 constraints
        print("\n=== T2903: constraints, which must join two bodies that exist ===")
        nobody = M.raw_post("add_physics_constraint", {"assetPath": PA, "bone1": "root",
                                                       "bone2": "nosuchbone"})
        check("T2903 a constraint onto a bone with no body is refused", nobody.get("ok") is False,
              json.dumps(nobody)[:250])
        check("T2903 and says so rather than creating one that joins nothing",
              "join nothing" in (nobody.get("error") or ""), (nobody.get("error") or "")[:200])
        same = M.raw_post("add_physics_constraint", {"assetPath": PA, "bone1": "root",
                                                     "bone2": "root"})
        check("T2903 a constraint from a bone to itself is refused", same.get("ok") is False,
              (same.get("error") or "")[:180])

        con = M.raw_post("add_physics_constraint", {"assetPath": PA, "bone1": "root",
                                                    "bone2": "spine"})
        check("T2903 a valid constraint is created", con.get("ok") is True, json.dumps(con)[:250])
        # CreateNewConstraint makes an EMPTY template that knows nothing about its bones - wiring
        # them is what makes it a constraint rather than a placeholder.
        check("T2903 and it really carries both bones - an unwired template would do nothing",
              con.get("bone1") == "root" and con.get("bone2") == "spine", json.dumps(con)[:250])
        d = M.call("describe_physics_asset", {"assetPath": PA})
        check("T2903 describe_physics_asset reports it - the two halves agree",
              d.get("constraintCount") == 1
              and (d.get("constraints") or [{}])[0].get("bone2") == "spine",
              json.dumps(d.get("constraints"))[:250])

        # ------------------------------------------------------------------ T2904 the pair table
        print("\n=== T2904: the body-pair collision table, measured not assumed ===")
        off = M.raw_post("set_physics_body_collision", {"assetPath": PA, "boneA": "root",
                                                        "boneB": "spine", "enabled": False})
        check("T2904 a pair can be disabled", off.get("ok") is True and off.get("enabled") is False,
              json.dumps(off)[:250])
        check("T2904 and the pair shows up in disabledPairs",
              any(p.get("boneA") == "root" and p.get("boneB") == "spine"
                  for p in (off.get("disabledPairs") or [])), json.dumps(off)[:250])
        on = M.raw_post("set_physics_body_collision", {"assetPath": PA, "boneA": "root",
                                                       "boneB": "spine", "enabled": True})
        check("T2904 and re-enabled", on.get("ok") is True and on.get("enabled") is True
              and on.get("disabledPairCount") == 0, json.dumps(on)[:250])
        # `changed` is measured against the state BEFORE, so a no-op is visible. CreateNewBody
        # disables collisions by default, which is exactly why this must not be assumed.
        check("T2904 `changed` is measured, so a no-op set is distinguishable from a real one",
              isinstance(off.get("changed"), bool) and on.get("changed") is True,
              "off.changed=%s on.changed=%s" % (off.get("changed"), on.get("changed")))
        selfpair = M.raw_post("set_physics_body_collision", {"assetPath": PA, "boneA": "root",
                                                             "boneB": "root", "enabled": False})
        check("T2904 a body against itself is refused", selfpair.get("ok") is False,
              (selfpair.get("error") or "")[:180])
        noflag = M.raw_post("set_physics_body_collision", {"assetPath": PA, "boneA": "root",
                                                           "boneB": "spine"})
        check("T2904 omitting `enabled` is refused rather than guessing or toggling",
              noflag.get("ok") is False, (noflag.get("error") or "")[:180])

        # ------------------------------------------------------------------ T2905 removal
        print("\n=== T2905: removal, and the renumbering it causes ===")
        nc = M.raw_post("remove_physics_constraint", {"assetPath": PA, "index": 0})
        check("T2905 removing a constraint without confirm is refused", nc.get("ok") is False,
              json.dumps(nc)[:250])
        rc = M.raw_post("remove_physics_constraint", {"assetPath": PA, "index": 0,
                                                      "confirm": True})
        check("T2905 with confirm it is removed and the count drops",
              rc.get("ok") is True and rc.get("constraintCount") == 0, json.dumps(rc)[:250])

        rb = M.raw_post("remove_physics_body", {"assetPath": PA, "boneName": "spine",
                                                "confirm": True})
        check("T2905 a body can be removed by BONE NAME, not only by a shifting index",
              rb.get("ok") is True and rb.get("bodyCount") == 1, json.dumps(rb)[:250])
        # The renumbering warning is the difference between a caller re-reading and a caller
        # deleting the wrong body next.
        check("T2905 and the response warns that held indices are now wrong",
              "shifted down" in (rb.get("renumberNote") or ""), (rb.get("renumberNote") or "")[:200])
        check("T2905 the asset really holds one body afterwards",
              M.call("describe_physics_asset", {"assetPath": PA}).get("bodyCount") == 1,
              M.call("describe_physics_asset", {"assetPath": PA}).get("bodyCount"))

        gone = M.raw_post("remove_physics_body", {"assetPath": PA, "boneName": "nosuchbone",
                                                  "confirm": True})
        check("T2905 an unknown bone is refused and the real ones listed",
              gone.get("ok") is False and "root" in (gone.get("error") or ""),
              (gone.get("error") or "")[:200])
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": PA})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
