"""list_ik_rig - a read that CHECKS, and the reason the IK endpoints exist at all.

set_property can already write every field an IKRigDefinition holds. That is not an argument against
these endpoints, it is the argument FOR them: writing the fields by hand produces an asset that reads
back perfectly and is broken, with ok:true on every write. FRetargetDefinition::RootBone and
::BoneChains are in fact private with `friend class UIKRigController` (IKRigDefinition.h:169-180);
reflection bypasses C++ access control, which is the only reason the hand-written path works at all.

So this suite is built the wrong-way-round on purpose: it BREAKS a rig by hand, one defect at a time,
and requires the validator to name each one. Every mutation below returns ok:true from set_property.

T232 is the one with teeth. A chain is a path DOWN the hierarchy, so its end bone must be a descendant
of its start bone. Two plausible-looking bone names side by side that are not in that relationship
form no chain at all, and nothing else in the engine or the bridge will tell you - the asset saves,
loads and retargets nothing. The test asserts BOTH directions: a correct chain is accepted and its
inversion is refused, so the check cannot be passing by always saying no.

T234 covers the positive path deliberately, because a validator that only ever reports problems is
indistinguishable from one that is broken. The valid rig there is assembled by hand rather than with
set_ik_rig_mesh, which is a real limitation of this suite and is why refPoseGlobal is written
explicitly: it exercises the validator, not the authoring path.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def xform(n):
    one = "(Rotation=(X=0,Y=0,Z=0,W=1),Translation=(X=0,Y=0,Z=0),Scale3D=(X=1,Y=1,Z=1))"
    return "(" + ",".join([one] * n) + ")"


def make_rig(stamp, tag):
    p = "/Game/_MifIK/Rig_%s_%d" % (tag, stamp)
    c = M.call("create_asset", {"path": p, "class": "IKRigDefinition"})
    return c.get("assetPath") if c.get("ok") else None


def set_skeleton(rig, names, parents, refpose=True):
    """A four-bone chain: root -> pelvis -> spine_01 -> spine_02."""
    v = '(BoneNames=(%s),ParentIndices=(%s)%s)' % (
        ",".join('"%s"' % n for n in names),
        ",".join(str(i) for i in parents),
        (",RefPoseGlobal=" + xform(len(names))) if refpose else "")
    return M.call("set_property", {"objectPath": rig, "propertyPath": "Skeleton", "value": v})


def set_chains(rig, chains):
    v = "(" + ",".join(
        '(ChainName="%s",StartBone=(BoneName="%s"),EndBone=(BoneName="%s"))' % c for c in chains) + ")"
    return M.call("set_property", {"objectPath": rig, "propertyPath": "RetargetDefinition.BoneChains",
                                   "value": v})


def problems(rig):
    r = M.call("list_ik_rig", {"path": rig})
    return r, " || ".join(r.get("problems") or [])


BONES = ["root", "pelvis", "spine_01", "spine_02"]
PARENTS = [-1, 0, 1, 2]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    stamp = int(time.time() % 100000)

    probe = M.call("list_ik_rig", {"path": "/Game/None"})
    if "unavailable" in (probe.get("error") or ""):
        # The whole point of registering these on every engine: this answer is possible.
        print("IK Rig is unavailable on this engine build - the endpoint says so, which is the")
        print("designed behaviour. Nothing further can be tested here.")
        print("  " + (probe.get("error") or "")[:200])
        return 0

    # ------------------------------------------------------------------ T230 a fresh rig
    print("\n=== T230: a freshly created rig is honestly reported as unusable ===")
    rig = make_rig(stamp, "empty")
    if not rig:
        print("could not create a scratch IKRigDefinition")
        return 3
    r, p = problems(rig)
    check("T230 it reads", r.get("ok") is True, json.dumps(r)[:160])
    check("T230 an empty rig is NOT valid", r.get("valid") is False, r.get("valid"))
    check("T230 it says the mesh is missing", "no mesh has been assigned" in p, p[:150])
    check("T230 it says the retarget root is missing", "no retarget root" in p, p[:150])
    check("T230 it says there are no chains", "no retarget chains" in p, p[:150])
    # Each problem names the endpoint that fixes it - a validator that only says "invalid" is a riddle.
    check("T230 each problem names what to do about it",
          "set_ik_rig_mesh" in p and "add_ik_retarget_chain" in p, p[:200])

    # ------------------------------------------------------------------ T231 skeleton integrity
    print("\n=== T231: a hand-written skeleton is caught ===")
    rig = make_rig(stamp, "skel")
    w = set_skeleton(rig, BONES, PARENTS, refpose=False)
    check("T231 set_property accepted the hand-written skeleton", w.get("ok") is True,
          "if this fails the premise of the suite is wrong")
    r, p = problems(rig)
    # The solver needs the reference pose. Nothing reads back as wrong without it.
    check("T231 the missing reference pose is caught", "reference pose is missing" in p, p[:170])
    check("T231 and it says how many it found vs expected",
          "0 transforms for 4 bones" in p, p[:170])
    check("T231 refPoseCount is reported so a caller can see it",
          r.get("refPoseCount") == 0 and r.get("boneCount") == 4,
          "refPose=%s bones=%s" % (r.get("refPoseCount"), r.get("boneCount")))

    rig = make_rig(stamp, "ragged")
    set_skeleton(rig, BONES, [-1, 0, 1], refpose=False)   # 4 names, 3 parents
    r, p = problems(rig)
    check("T231 parallel arrays that have drifted are caught",
          "4 bone names but 3 parent indices" in p, p[:180])

    # ------------------------------------------------------------------ T232 the chain check
    print("\n=== T232 [teeth]: a chain must actually be a chain ===")
    rig = make_rig(stamp, "chain")
    set_skeleton(rig, BONES, PARENTS)
    M.call("set_property", {"objectPath": rig, "propertyPath": "RetargetDefinition.RootBone",
                            "value": "pelvis"})
    # spine_02 IS a descendant of spine_01, so this one is a real chain.
    set_chains(rig, [("Spine", "spine_01", "spine_02")])
    r, p = problems(rig)
    ch = (r.get("chains") or [{}])[0]
    check("T232 a genuine chain is accepted", ch.get("valid") is True, json.dumps(ch))

    # Inverted: root is spine_01's ANCESTOR, not its descendant. Same two bone names, no chain.
    set_chains(rig, [("Spine", "spine_01", "root")])
    r, p = problems(rig)
    ch = (r.get("chains") or [{}])[0]
    check("T232 an INVERTED chain is refused", ch.get("valid") is False, json.dumps(ch))
    check("T232 and it says why in words",
          "not a descendant of" in (ch.get("problem") or ""), (ch.get("problem") or "")[:170])

    # Unrelated bones on different branches would be caught by the same walk.
    set_chains(rig, [("Spine", "spine_02", "pelvis")])
    r, p = problems(rig)
    check("T232 a chain running the wrong way up the hierarchy is refused",
          (r.get("chains") or [{}])[0].get("valid") is False, json.dumps(r.get("chains"))[:180])

    # ------------------------------------------------------------------ T233 names and roots
    print("\n=== T233: names, roots and duplicates ===")
    rig = make_rig(stamp, "names")
    set_skeleton(rig, BONES, PARENTS)
    M.call("set_property", {"objectPath": rig, "propertyPath": "RetargetDefinition.RootBone",
                            "value": "not_a_bone"})
    set_chains(rig, [("Spine", "nope_a", "nope_b")])
    r, p = problems(rig)
    check("T233 a retarget root that is not a bone is caught",
          "retarget root 'not_a_bone' is not a bone" in p, p[:180])
    check("T233 a chain naming absent bones is caught",
          "start bone 'nope_a' is not in this skeleton" in p, p[:200])

    rig = make_rig(stamp, "dupes")
    set_skeleton(rig, BONES, PARENTS)
    M.call("set_property", {"objectPath": rig, "propertyPath": "RetargetDefinition.RootBone",
                            "value": "pelvis"})
    set_chains(rig, [("Spine", "spine_01", "spine_02"), ("Spine", "root", "pelvis")])
    r, p = problems(rig)
    # Two chains with one name makes any retargeter mapping that names it ambiguous.
    check("T233 duplicate chain names are caught", "duplicate chain name 'Spine'" in p, p[:200])

    # ------------------------------------------------------------------ T234 the positive path
    print("\n=== T234: a correct rig is reported VALID ===")
    rig = make_rig(stamp, "good")
    set_skeleton(rig, BONES, PARENTS)
    M.call("set_property", {"objectPath": rig, "propertyPath": "RetargetDefinition.RootBone",
                            "value": "pelvis"})
    set_chains(rig, [("Spine", "pelvis", "spine_02"), ("Lower", "root", "pelvis")])
    r, p = problems(rig)
    # A validator that only ever says no is indistinguishable from a broken one.
    check("T234 a correct rig is valid", r.get("valid") is True, p[:250])
    check("T234 with no problems listed", len(r.get("problems") or []) == 0, json.dumps(r.get("problems")))
    check("T234 every chain is individually valid",
          all(c.get("valid") for c in (r.get("chains") or [])), json.dumps(r.get("chains"))[:200])
    check("T234 and no validNote is attached to a healthy rig", "validNote" not in r, r.get("validNote"))
    check("T234 it reports the counts it checked",
          r.get("boneCount") == 4 and r.get("chainCount") == 2 and r.get("refPoseCount") == 4,
          "bones=%s chains=%s refpose=%s" % (r.get("boneCount"), r.get("chainCount"),
                                             r.get("refPoseCount")))

    # ------------------------------------------------------------------ T235 guards
    print("\n=== T235: guards ===")
    notrig = (M.call("find_assets", {"class": "Material", "limit": 1}).get("assets") or [{}])[0].get("path")
    for label, payload, expect in (
        ("no path", {}, "path is required"),
        ("missing asset", {"path": "/Game/NoSuchRig_zz"}, "no asset at"),
        ("a non-rig asset", {"path": notrig}, "not an IKRigDefinition"),
    ):
        q = M.call("list_ik_rig", payload)
        check("T235 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T235 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])
    q = M.call("list_ik_rig", {"path": rig, "retargeter": "x"})
    check("T235 a retargeter parameter points at the right endpoint",
          q.get("ok") is False and "different asset" in (q.get("error") or ""), (q.get("error") or "")[:170])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("NOTE: scratch rigs were left under /Game/_MifIK/. Nothing was saved.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
