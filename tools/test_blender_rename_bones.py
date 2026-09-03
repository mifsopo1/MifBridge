"""rename_bones - the collision that silently unbinds a skin.

WHAT THIS OP IS FOR, and it is not what the backlog entry proposed. That entry wanted vertex groups
renamed alongside bones and a refusal when constraints or drivers reference the bone. Both were
measured against a live Blender 4.4 before any code was written, and Blender already does all three:
setting bone.name renames the matching vertex group on every skinned mesh, updates constraint
subtargets, and updates driver bone targets.

THE REAL HAZARD IS THE CASE WHERE THAT SYNC FAILS, which is a name collision:

    bones ['Hips','Spine']  vgroups ['Hips','Spine']
    bones['Spine'].name = 'Hips'
    bones ['Hips','Hips.001']  vgroups ['Hips','Spine']

The bone is silently suffixed to a name nobody asked for AND the vertex group keeps its old name,
now matching no bone - so that part of the mesh stops deforming and nothing says so. T5502 asserts
the refusal.

SKIPS WITHOUT AN ARMATURE, and that is the headless case. The addon deliberately cannot create an
armature, and run_python - which is how test_blender_rig builds its fixture - is an addon preference
that defaults OFF as a security choice. Weakening that for a test would be the wrong trade, so this
suite reports SKIPPED rather than pretending. It was verified live during development against a
Blender started with a fixture script, which builds the rig outside the addon and disables nothing;
that transcript is in the commit. Run against a GUI Blender with a rig loaded and every check below
executes.
"""
import json
import sys

from blender_audit_common import call, reachable, skip_banner

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def find_armature(own=None):
    # SKIP ANOTHER SUITE'S FIXTURE. Blender objects have no /Game path, so the convention that
    # identifies scratch here is the NAME: every suite prefixes its objects Mif (MifTestArmature,
    # MifC_Merge, MifA_Fixture, MifRB_*). These suites share one Blender when run against a live
    # instance, and adopting a neighbour's half-built object means asserting about their fixture.
    r = call("list_objects", {"type": "ARMATURE"})
    for o in (r.get("objects") or []):
        name = o.get("name")
        if not name:
            continue
        # `own` is THIS suite's fixture, which is Mif-prefixed like everyone else's and must not be
        # filtered out by the rule that skips everyone else's. Leaving that out made the suite skip
        # itself: it built MifRenameFixture, then refused to find it, and reported "verified
        # nothing" - which is worse than the adoption it was meant to prevent.
        if name != own and name.startswith("Mif"):
            continue
        if (call("list_bones", {"object": name}).get("boneCount") or 0) >= 2:
            return name
    return None


# A 2-bone armature, built OUTSIDE the addon, for the case where the scene has none.
#
# WHY THIS IS HERE AT ALL. This suite used to skip whenever no rig was loaded, saying the addon
# cannot create an armature and run_python is "a security choice this suite will not work around".
# Both halves of that are still true and neither has been weakened: this does not enable anything.
# It PROBES - exactly as test_blender_rig's T811 does - and builds the fixture only where run_python
# is already available, which is the runner's own throwaway headless Blender or a session whose
# owner turned the hatch on deliberately. Where it is off, the skip below is reached unchanged.
#
# The reasoning is lifted from test_blender_rig, which had already settled it: "prove nothing is a
# worse answer than prove it after restoring the one precondition this suite needs, when restoring
# it is cheap, safe and scoped to exactly the thing being tested against". Fourteen assertions had
# never executed in automation on any Blender - the rename contract this file exists to protect was
# covered by a hand-run transcript from development and nothing else.
ARMATURE_CODE = """
import bpy
arm_data = bpy.data.armatures.new("MifRenameFixture")
arm_obj = bpy.data.objects.new("MifRenameFixture", arm_data)
bpy.context.collection.objects.link(arm_obj)
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode='EDIT')
b1 = arm_data.edit_bones.new("root")
b1.head = (0, 0, 0)
b1.tail = (0, 0, 1)
b2 = arm_data.edit_bones.new("child")
b2.head = (0, 0, 1)
b2.tail = (0, 0, 2)
b2.parent = b1
bpy.ops.object.mode_set(mode='OBJECT')
result = arm_obj.name
"""


def build_armature():
    """Build the fixture if run_python is available. Returns the armature name, or None."""
    if call("run_python", {"code": "pass"}).get("ok") is False:
        return None
    r = call("run_python", {"code": ARMATURE_CODE})
    if r.get("ok") is False:
        return None
    return find_armature(own="MifRenameFixture")


def main():
    if not reachable():
        return skip_banner("rename_bones")

    arm = find_armature()
    built = False
    if not arm:
        arm = build_armature()
        built = arm is not None
    print("armature: %s%s" % (arm, " (built by this suite)" if built else ""))
    if not arm:
        print("")
        print("SKIPPED - no ARMATURE with at least two bones is in this scene, and one could not")
        print("  be built: the addon cannot create an armature, and run_python - the only way to")
        print("  make one from here - is off. That is a deliberate security default and this suite")
        print("  does not turn it on; it only USES it where it is already available. Load a rig,")
        print("  enable the hatch, or use run_blender_suites.py, which enables it inside its own")
        print("  headless Blender. Exit 2 means SKIPPED, distinct from 0 (passed) and 1 (failed).")
        return 2

    bones = [b["name"] for b in (call("list_bones", {"object": arm}).get("bones") or [])]
    print("bones: %s" % bones[:8])
    a, b = bones[0], bones[1]

    # ------------------------------------------------------------------ T5500 dryRun
    print("\n=== T5500: dryRun answers and writes nothing ===")
    dry = call("rename_bones", {"object": arm, "renames": {a: "MifRenamed"}, "dryRun": True})
    check("T5500 dryRun reports what it would do", dry.get("ok") is not False
          and dry.get("dryRun") is True and dry.get("changed") is False, json.dumps(dry)[:250])
    after = [x["name"] for x in (call("list_bones", {"object": arm}).get("bones") or [])]
    # Measured from the armature, not trusted from the response - "writes nothing" is a claim
    # about the scene, so read the scene.
    check("T5500 and the bone names are untouched afterwards", after == bones,
          "%s -> %s" % (bones[:6], after[:6]))

    # ------------------------------------------------------------------ T5501 the guards
    print("\n=== T5501: the refusals, each for its own reason ===")
    nosuch = call("rename_bones", {"object": arm, "renames": {"MifNoSuchBone": "x"}})
    check("T5501 an unknown bone is refused AND the real ones are listed",
          nosuch.get("ok") is False and bones[0] in (nosuch.get("error") or ""),
          (nosuch.get("error") or "")[:220])
    same = call("rename_bones", {"object": arm, "renames": {a: "MifDup", b: "MifDup"}})
    check("T5501 two bones renamed to the SAME name is refused - it collides with itself "
          "whichever order it runs in",
          same.get("ok") is False and "collides with itself" in (same.get("error") or ""),
          (same.get("error") or "")[:220])
    empty = call("rename_bones", {"object": arm, "renames": {}})
    check("T5501 an empty rename map is refused rather than reported as a successful no-op",
          empty.get("ok") is False, (empty.get("error") or "")[:200])

    # ------------------------------------------------------------------ T5502 THE hazard
    print("\n=== T5502: the collision that silently unbinds a skin ===")
    clash = call("rename_bones", {"object": arm, "renames": {b: a}})
    check("T5502 renaming a bone onto a name another bone holds is REFUSED",
          clash.get("ok") is False, json.dumps(clash)[:250])
    # THE assertion. The refusal has to explain the silent half - that Blender would not error, it
    # would suffix the bone and orphan the vertex group.
    check("T5502 and the refusal says Blender would SILENTLY suffix the bone and leave the vertex "
          "group matching nothing, which is why this is refused rather than attempted",
          "silently suffix" in (clash.get("error") or "")
          and "stops deforming" in (clash.get("error") or ""),
          (clash.get("error") or "")[:300])
    after = [x["name"] for x in (call("list_bones", {"object": arm}).get("bones") or [])]
    check("T5502 and NOTHING was written - the refusal is pre-flight, not a rollback",
          after == bones, "%s -> %s" % (bones[:6], after[:6]))

    # ------------------------------------------------------------------ T5503 the round trip
    print("\n=== T5503: a real rename, a swap, and the vertex group that follows ===")
    r1 = call("rename_bones", {"object": arm, "renames": {a: "MifRenamedA"}})
    check("T5503 a clean rename succeeds and is read back off the armature",
          r1.get("ok") is not False and "MifRenamedA" in (r1.get("boneNames") or []),
          json.dumps(r1)[:250])
    # Blender renames the group itself; this confirms it HAPPENED rather than assuming it, which is
    # the whole reason the response carries both lists.
    groups = (r1.get("vertexGroupsAfter") or {})
    if groups:
        followed = any("MifRenamedA" in v for v in groups.values())
        had_old = any(a in v for v in (r1.get("vertexGroupsBefore") or {}).values())
        check("T5503 a vertex group named after the bone FOLLOWED the rename" if had_old
              else "T5503 (no vertex group was named after this bone, so nothing had to follow)",
              followed or not had_old, json.dumps(groups)[:250])
    check("T5503 orphaned vertex groups are reported as a list, even when empty - an empty one is "
          "the positive result that Blender's own rename worked",
          isinstance(r1.get("orphanedVertexGroups"), dict),
          json.dumps(r1.get("orphanedVertexGroups"))[:200])

    sw = call("rename_bones", {"object": arm,
                               "renames": {"MifRenamedA": b, b: "MifRenamedA"}})
    # A swap cannot be done in either order directly - whichever runs first collides - so this is
    # the check that the two-pass temp-name path exists and works.
    check("T5503 a SWAP of two bone names succeeds, which no single-pass rename could do",
          sw.get("ok") is not False and "MifRenamedA" in (sw.get("boneNames") or [])
          and b in (sw.get("boneNames") or []), json.dumps(sw)[:250])

    back = call("rename_bones", {"object": arm, "renames": {b: a, "MifRenamedA": b}})
    check("(cleanup) the original bone names are restored",
          back.get("ok") is not False
          and sorted(back.get("boneNames") or []) == sorted(bones),
          json.dumps(back.get("boneNames"))[:220])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
