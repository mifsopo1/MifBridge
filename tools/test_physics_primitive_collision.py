"""set_physics_primitive_collision - routing around two stacked engine defects.

WHY THIS ENDPOINT DOES NOT CALL UPhysicsAsset::SetPrimitiveCollision. That function is two defects
stacked, and guarding one while calling into the other would not have been safe.

DEFECT 1 - the wrong bound, on both engines:

    check(SkeletalBodySetups.IsValidIndex(BodyIndex));       // hard check - a crash, not an error
    ensure(PrimitiveIndex < AggGeom->GetElementCount());     // and this is the wrong number

GetElementCount() is the TOTAL across SphereElems, BoxElems, SphylElems and ConvexElems, while
PrimitiveIndex is per-TYPE. T3001 exercises the exact case that gets through it: primitiveType
"sphere", primitiveIndex 0, on a body that has ZERO spheres and ONE capsule. 0 < 1, so the ensure
passes.

DEFECT 2 - and it differs BY ENGINE, which is what makes it worth writing down. On 5.3
FKAggregateGeom::GetElement is a switch whose cases have NO break:

    case EAggCollisionShape::Sphere:
        if (ensure(SphereElems.IsValidIndex(Index))) { return &SphereElems[Index]; }
    case EAggCollisionShape::Box:                       // <- reached when the ensure above fails
        if (ensure(BoxElems.IsValidIndex(Index))) { return &BoxElems[Index]; }

So when the per-type index is out of range, 5.3 falls through and returns whichever LATER array
happens to accept it. The T3001 call would have returned the capsule and silently turned its
collision off - the caller asked about a sphere and changed something else. 5.7 (AggregateGeom.h:159)
HAS the breaks, so it returns nullptr and SetPrimitiveCollision dereferences it with no null check:
the same input is a dead editor there. Silent wrong data on one engine, a crash on the other.

So the endpoint resolves the per-type array itself, range-checks against THAT array, and sets the
field directly - SetPrimitiveCollision's whole body is one GetElement()->SetCollisionEnabled() call,
and SetCollisionEnabled is an inline setter (ShapeElem.h:105). Identical result, no reachable path
into either defect, correct on both engines. Reads in describe_physics_asset go the same way rather
than through GetPrimitiveCollision, which carries both defects too.

WORKS ON A DUPLICATE, not on real content. The project's PhysicsAssets have real primitives and
create_asset cannot produce one that does (SphereElems is EditFixedSize, so edit_container cannot
grow it either), so the suite duplicates a real asset into /Game/_Mif and edits the copy.

CLEANS UP: the duplicate is deleted at the end. Nothing is saved.
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
    DST = "/Game/_MifPhys/PA_PrimTest%d" % st
    made = False

    try:
        # ------------------------------------------------------------------ setup
        print("=== setup: a scratch COPY of a real PhysicsAsset, since it needs real primitives ===")
        src = None
        for a in (M.call("find_assets", {"class": "PhysicsAsset", "limit": 20}).get("assets") or []):
            if not a["path"].startswith("/Game/_Mif"):
                d0 = M.call("describe_physics_asset", {"assetPath": a["path"]})
                if any(b.get("primitives") for b in (d0.get("bodies") or [])):
                    src = a["path"]
                    break
        check("(setup) a real PhysicsAsset with primitives exists to copy", bool(src), src)
        if not src:
            print("SKIPPED - no PhysicsAsset with primitives in this project.")
            return 0

        dup = M.raw_post("duplicate_asset", {"path": src, "newPath": DST})
        check("(setup) it duplicates into scratch", dup.get("ok") is True, json.dumps(dup)[:250])
        if not dup.get("ok"):
            return 1
        made = True

        d = M.call("describe_physics_asset", {"assetPath": DST})
        target = None
        for b in (d.get("bodies") or []):
            if b.get("primitives"):
                target = b
                break
        check("(setup) the copy has a body with primitives", bool(target),
              json.dumps((d.get("bodies") or [])[:1])[:250])
        if not target:
            return 1
        bone = target["boneName"]
        prim = target["primitives"][0]
        print("        using body '%s', %s[%d]" % (bone, prim["type"], prim["index"]))

        # ------------------------------------------------------------------ T3000 the read half
        print("\n=== T3000: per-primitive collision is readable, per TYPE ===")
        check("T3000 each primitive reports its type, its per-type index and its collision state",
              all(p.get("type") and isinstance(p.get("index"), (int, float))
                  and p.get("collisionEnabled") for p in target["primitives"])
              and len(target["primitives"]) > 0, json.dumps(target["primitives"])[:250])
        # The index must be per-TYPE, which is what the setter consumes. If two capsules exist they
        # are 0 and 1, not 3 and 4 because boxes came first.
        by_type = {}
        for p in target["primitives"]:
            by_type.setdefault(p["type"], []).append(p["index"])
        check("T3000 indices restart per type - they are what the setter takes, not global offsets",
              all(v == list(range(len(v))) for v in by_type.values()) and len(by_type) > 0,
              json.dumps(by_type))

        # ------------------------------------------------------------------ T3001 THE defect
        print("\n=== T3001: the exact call that passes the engine's own ensure ===")
        counts = {"sphere": target.get("sphereCount"), "box": target.get("boxCount"),
                  "capsule": target.get("capsuleCount"), "convex": target.get("convexCount")}
        empty_type = next((t for t, n in counts.items() if not n), None)
        check("T3001 (setup) the body has at least one primitive type with ZERO of it",
              bool(empty_type) and target.get("primitiveCount", 0) > 0,
              "%s, total=%s" % (json.dumps(counts), target.get("primitiveCount")))
        if empty_type:
            # 0 < GetElementCount() is TRUE here, so the engine's ensure passes and lets this
            # through - on 5.3 into a DIFFERENT primitive, on 5.7 into a nullptr deref.
            bug = M.raw_post("set_physics_primitive_collision", {
                "assetPath": DST, "boneName": bone, "primitiveType": empty_type,
                "primitiveIndex": 0, "collisionEnabled": "NoCollision"})
            check("T3001 index 0 for a type the body has NONE of is refused",
                  bug.get("ok") is False, json.dumps(bug)[:250])
            check("T3001 and the refusal says it checked the PER-TYPE array, not the total",
                  "PER-TYPE" in (bug.get("error") or ""), (bug.get("error") or "")[:250])
            check("T3001 and names both engine behaviours it is preventing",
                  "5.3" in (bug.get("error") or "") and "5.7" in (bug.get("error") or ""),
                  (bug.get("error") or "")[:280])
            # THE postcondition that proves nothing was touched: the primitive that 5.3 WOULD have
            # modified must be exactly as it was.
            after = M.call("describe_physics_asset", {"assetPath": DST})
            row = next(b for b in after["bodies"] if b["boneName"] == bone)
            check("T3001 and the primitive 5.3 would have silently modified is untouched",
                  row["primitives"][0]["collisionEnabled"] == prim["collisionEnabled"],
                  "%s -> %s" % (prim["collisionEnabled"],
                                row["primitives"][0]["collisionEnabled"]))

        over = M.raw_post("set_physics_primitive_collision", {
            "assetPath": DST, "boneName": bone, "primitiveType": prim["type"],
            "primitiveIndex": 99, "collisionEnabled": "NoCollision"})
        check("T3001 a plainly out-of-range index is refused too", over.get("ok") is False,
              json.dumps(over)[:250])
        # A failed guard on 5.7 is a nullptr deref, so the editor answering is the proof.
        alive = M.call("self_audit", {})
        check("T3001 - the editor is still alive after both", alive.get("ok") is True,
              "on 5.7 GetElement returns nullptr and SetPrimitiveCollision derefs it")

        # ------------------------------------------------------------------ T3002 the write
        print("\n=== T3002: the valid write, verified by reading it back ===")
        w = M.raw_post("set_physics_primitive_collision", {
            "assetPath": DST, "boneName": bone, "primitiveType": prim["type"],
            "primitiveIndex": prim["index"], "collisionEnabled": "NoCollision"})
        check("T3002 a valid primitive can be set", w.get("ok") is True, json.dumps(w)[:250])
        check("T3002 it reports the before and after states, so a no-op is visible",
              w.get("collisionEnabledBefore") == prim["collisionEnabled"]
              and w.get("collisionEnabled") == "NoCollision", json.dumps(w)[:250])
        # THE postcondition, through the read endpoint rather than the setter's own response.
        after = M.call("describe_physics_asset", {"assetPath": DST})
        row = next(b for b in after["bodies"] if b["boneName"] == bone)
        hit = next(p for p in row["primitives"]
                   if p["type"] == prim["type"] and p["index"] == prim["index"])
        check("T3002 describe_physics_asset reports the new state - the halves agree",
              hit["collisionEnabled"] == "NoCollision", json.dumps(hit))

        again = M.raw_post("set_physics_primitive_collision", {
            "assetPath": DST, "boneName": bone, "primitiveType": prim["type"],
            "primitiveIndex": prim["index"], "collisionEnabled": "NoCollision"})
        check("T3002 setting the same value again succeeds and reports changed:false",
              again.get("ok") is True and again.get("changed") is False, json.dumps(again)[:250])

        bad = M.raw_post("set_physics_primitive_collision", {
            "assetPath": DST, "boneName": bone, "primitiveType": prim["type"],
            "primitiveIndex": prim["index"], "collisionEnabled": "Maybe"})
        check("T3002 an unknown collisionEnabled value is refused with the four real ones",
              bad.get("ok") is False and "QueryAndPhysics" in (bad.get("error") or ""),
              (bad.get("error") or "")[:200])
        badtype = M.raw_post("set_physics_primitive_collision", {
            "assetPath": DST, "boneName": bone, "primitiveType": "pyramid",
            "primitiveIndex": 0, "collisionEnabled": "NoCollision"})
        check("T3002 an unknown primitiveType is refused", badtype.get("ok") is False,
              (badtype.get("error") or "")[:200])
        # `enabled` is the body-PAIR endpoint's parameter; collision here is four-valued.
        wrongparam = M.raw_post("set_physics_primitive_collision", {
            "assetPath": DST, "boneName": bone, "primitiveType": prim["type"],
            "primitiveIndex": prim["index"], "enabled": False})
        check("T3002 `enabled` is refused with a pointer to the four-valued parameter",
              wrongparam.get("ok") is False and "four-valued" in (wrongparam.get("error") or ""),
              (wrongparam.get("error") or "")[:200])
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": DST})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
