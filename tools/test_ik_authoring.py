"""IK Rig / IK Retargeter authoring - the full loop, and the three engine calls that lie.

Every endpoint here wraps a controller call that reports success while doing something other than what
was asked. All three were found by reading the implementations, not the headers:

  set_ik_rig_retarget_root   SetRetargetRoot given a bone that is not in the skeleton sets the root to
                             NAME_None and returns TRUE (IKRigController.cpp:391-403). Ask for a root
                             before assigning a mesh and you get success and no root. T241.

  add_ik_retarget_chain      AddRetargetChain runs the requested name through a uniquifier that
                             appends a number on collision (IKRigController.cpp:204) and returns the
                             name actually used. Ask for "Spine" twice and the second is "Spine_1"
                             with no warning, so a mapping written against "Spine" targets the first.
                             It also checks only that both bones EXIST (lines 183-193) - a chain whose
                             end bone is not a descendant of its start bone is stored and spans
                             nothing. T242.

  set_retarget_rigs          SetIKRig is not an assignment: it copies the preview mesh off the rig,
                             rebuilds the chain mapping and runs AutoMapChains(Fuzzy)
                             (IKRetargeterController.cpp:52-82). Writing SourceIKRigAsset with
                             set_property does none of it. T244.

The fixtures are deliberately CROSS-SPECIES - a 161-bone UE5 Mannequin onto a 53-bone Akita - because
that is the case an IK Rig actually exists for, and because two identical skeletons would let a broken
mapping pass. It also produced a real finding: the Akita's Spine_01 is a SIBLING branch off Spine_base,
not part of the spine, so a plausible-looking Spine_01 -> Spine_05 chain spans nothing. T242 uses that
real pair rather than an invented one.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []
HUMAN = "/Game/Characters/Mannequins/Meshes/SKM_Manny.SKM_Manny"
DOG = "/Game/SkeletalMeshes/AssetPacks/Dogs_Big_pack/Meshes/Akita/Mesh_Akita.Mesh_Akita"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def new_asset(cls, tag, st):
    r = M.call("create_asset", {"path": "/Game/_MifIK/%s_%d" % (tag, st), "class": cls})
    return r.get("assetPath")


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    probe = M.call("list_ik_rig", {"path": "/Game/None"})
    if "unavailable" in (probe.get("error") or ""):
        print("IK Rig is unavailable on this engine build; the endpoints say so. Nothing to test.")
        return 0
    for m in (HUMAN, DOG):
        if not (M.call("list_bones", {"path": m}).get("ok")):
            print("fixture mesh missing: %s" % m)
            return 3

    # ------------------------------------------------------------------ T240 building the rig
    print("\n=== T240: set_ik_rig_mesh BUILDS the skeleton ===")
    rig = new_asset("IKRigDefinition", "T240", st)
    before = M.call("list_ik_rig", {"path": rig})
    check("T240 a fresh rig has no skeleton", before.get("boneCount") == 0, before.get("boneCount"))
    r = M.call("set_ik_rig_mesh", {"path": rig, "mesh": HUMAN})
    check("T240 assigning the mesh succeeds", r.get("ok") is True, json.dumps(r)[:180])
    # The point: the bones came OUT of the mesh. A pointer assignment would leave this at zero.
    mesh_bones = M.call("list_bones", {"path": HUMAN}).get("boneCount")
    check("T240 the rig's bone count matches the mesh's",
          r.get("boneCount") == mesh_bones, "%s vs %s" % (r.get("boneCount"), mesh_bones))
    check("T240 and the reference pose was built too",
          r.get("refPoseCount") == mesh_bones, "%s vs %s" % (r.get("refPoseCount"), mesh_bones))
    v = M.call("list_ik_rig", {"path": rig})
    check("T240 list_ik_rig agrees", v.get("boneCount") == mesh_bones, v.get("boneCount"))
    check("T240 the mesh-missing problem is gone",
          not any("no mesh has been assigned" in p for p in (v.get("problems") or [])),
          json.dumps(v.get("problems"))[:150])

    print("\n=== T240b: guards ===")
    for label, payload, expect in (
        ("no mesh", {"path": rig}, "mesh is required"),
        ("a Skeleton instead of a mesh",
         {"path": rig, "mesh": "/Game/Characters/Mannequins/Meshes/SK_Mannequin.SK_Mannequin"},
         "not a SkeletalMesh"),
        ("missing rig", {"path": "/Game/NoSuchRig_zz", "mesh": HUMAN}, "no asset at"),
    ):
        q = M.call("set_ik_rig_mesh", payload)
        check("T240b %s refused" % label, q.get("ok") is False, json.dumps(q)[:140])
        check("T240b %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:170])
        check("T240b %s says nothing changed" % label, "NOTHING was changed" in (q.get("error") or ""),
              (q.get("error") or "")[:120])

    # ------------------------------------------------------------------ T241 the silent clear
    print("\n=== T241 [teeth]: the retarget root cannot be silently cleared ===")
    ok = M.call("set_ik_rig_retarget_root", {"path": rig, "bone": "pelvis"})
    check("T241 a real bone is accepted", ok.get("ok") is True and ok.get("retargetRoot") == "pelvis",
          json.dumps(ok)[:150])
    bad = M.call("set_ik_rig_retarget_root", {"path": rig, "bone": "no_such_bone_zz"})
    check("T241 a bone that does not exist is REFUSED", bad.get("ok") is False, json.dumps(bad)[:150])
    check("T241 and the refusal names the engine behaviour it is preventing",
          "silently set" in (bad.get("error") or ""), (bad.get("error") or "")[:200])
    # THE assertion. The raw engine call would have set the root to None and returned true.
    after = M.call("list_ik_rig", {"path": rig})
    check("T241 the existing root SURVIVED the refused call",
          after.get("retargetRoot") == "pelvis",
          "root is now '%s' - the engine call would have cleared it" % after.get("retargetRoot"))

    empty = new_asset("IKRigDefinition", "T241b", st)
    q = M.call("set_ik_rig_retarget_root", {"path": empty, "bone": "pelvis"})
    check("T241 setting a root before a mesh is refused, not silently dropped",
          q.get("ok") is False and "no skeleton yet" in (q.get("error") or ""),
          (q.get("error") or "")[:170])

    # ------------------------------------------------------------------ T242 chains
    print("\n=== T242 [teeth]: chains are validated and renames are reported ===")
    a = M.call("add_ik_retarget_chain", {"path": rig, "name": "Spine",
                                         "startBone": "spine_01", "endBone": "spine_05"})
    check("T242 a real chain is created", a.get("ok") is True and a.get("name") == "Spine",
          json.dumps(a)[:170])
    check("T242 and it is not reported as renamed", a.get("renamed") is False, a.get("renamed"))

    # The silent rename. The engine returns "Spine_1"; a mapping written against "Spine" would hit
    # the FIRST chain, and nothing would say so.
    dup = M.call("add_ik_retarget_chain", {"path": rig, "name": "Spine",
                                           "startBone": "spine_02", "endBone": "spine_04"})
    check("T242 a duplicate name is accepted by the engine", dup.get("ok") is True, json.dumps(dup)[:150])
    check("T242 but the rename is REPORTED", dup.get("renamed") is True, json.dumps(dup)[:170])
    check("T242 with the name actually used", dup.get("name") != "Spine", dup.get("name"))
    check("T242 and the name that was requested", dup.get("requestedName") == "Spine",
          dup.get("requestedName"))
    check("T242 and an explanation of why it matters",
          "would refer to the other chain" in (dup.get("renameNote") or ""),
          (dup.get("renameNote") or "")[:180])
    M.call("remove_ik_retarget_chain", {"path": rig, "name": dup.get("name")})

    # The hierarchy check the engine does not do. spine_01 IS a descendant of spine_05's ancestor
    # chain, so inverting it must fail.
    inv = M.call("add_ik_retarget_chain", {"path": rig, "name": "Inverted",
                                           "startBone": "spine_05", "endBone": "spine_01"})
    check("T242 an inverted chain is refused", inv.get("ok") is False, json.dumps(inv)[:150])
    check("T242 and it says why", "not a descendant of" in (inv.get("error") or ""),
          (inv.get("error") or "")[:190])
    check("T242 and creates nothing", "NOTHING was created" in (inv.get("error") or ""),
          (inv.get("error") or "")[:150])

    # A real asset where the names mislead: the Akita's Spine_01 is a SIBLING of the spine chain.
    dogrig = new_asset("IKRigDefinition", "T242dog", st)
    M.call("set_ik_rig_mesh", {"path": dogrig, "mesh": DOG})
    trap = M.call("add_ik_retarget_chain", {"path": dogrig, "name": "Spine",
                                            "startBone": "Spine_01", "endBone": "Spine_05"})
    check("T242 a real misleading-name case is caught (Akita Spine_01 is a sibling branch)",
          trap.get("ok") is False and "not a descendant" in (trap.get("error") or ""),
          (trap.get("error") or "")[:180])
    good = M.call("add_ik_retarget_chain", {"path": dogrig, "name": "Spine",
                                            "startBone": "Spine_base", "endBone": "Spine_05"})
    check("T242 and the correct one is accepted", good.get("ok") is True, json.dumps(good)[:150])

    for label, payload, expect in (
        ("absent bone", {"path": rig, "name": "X", "startBone": "nope_zz", "endBone": "spine_05"},
         "is not a bone"),
        ("no name", {"path": rig, "startBone": "spine_01", "endBone": "spine_05"}, "are all required"),
    ):
        q = M.call("add_ik_retarget_chain", payload)
        check("T242b %s refused" % label, q.get("ok") is False, json.dumps(q)[:140])
        check("T242b %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:160])

    # ------------------------------------------------------------------ T243 removal
    print("\n=== T243: removing a chain ===")
    n0 = M.call("list_ik_rig", {"path": rig}).get("chainCount")
    d = M.call("remove_ik_retarget_chain", {"path": rig, "name": "Spine"})
    check("T243 an existing chain is removed", d.get("ok") is True, json.dumps(d)[:150])
    check("T243 and the remaining count is reported",
          d.get("remainingChains") == n0 - 1, "%s from %s" % (d.get("remainingChains"), n0))
    q = M.call("remove_ik_retarget_chain", {"path": rig, "name": "NotThere_zz"})
    check("T243 an unknown chain is refused", q.get("ok") is False, json.dumps(q)[:140])
    # Listing what IS there turns a dead end into a next step.
    check("T243 and the error lists the chains that DO exist",
          "It has:" in (q.get("error") or ""), (q.get("error") or "")[:170])

    # ------------------------------------------------------------------ T244 the retargeter
    print("\n=== T244 [teeth]: setting a rig is not an assignment ===")
    hrig, drig = new_asset("IKRigDefinition", "T244h", st), new_asset("IKRigDefinition", "T244d", st)
    M.call("set_ik_rig_mesh", {"path": hrig, "mesh": HUMAN})
    M.call("set_ik_rig_retarget_root", {"path": hrig, "bone": "pelvis"})
    M.call("set_ik_rig_mesh", {"path": drig, "mesh": DOG})
    M.call("set_ik_rig_retarget_root", {"path": drig, "bone": "Spine_base"})
    for nm, s, e in (("Spine", "spine_01", "spine_05"), ("LeftLeg", "thigh_l", "foot_l"),
                     ("Head", "neck_01", "head")):
        M.call("add_ik_retarget_chain", {"path": hrig, "name": nm, "startBone": s, "endBone": e})
    for nm, s, e in (("Spine", "Spine_base", "Spine_05"), ("LeftLeg", "thigh_b_L", "foot_b_L"),
                     ("Head", "neck", "head"), ("Tail", "tail_01", "tail_05")):
        M.call("add_ik_retarget_chain", {"path": drig, "name": nm, "startBone": s, "endBone": e})

    ret = new_asset("IKRetargeter", "T244r", st)
    empty_map = M.call("list_retarget_chain_mapping", {"path": ret})
    check("T244 a fresh retargeter is not valid", empty_map.get("valid") is False,
          json.dumps(empty_map.get("problems"))[:150])
    check("T244 and it names both missing rigs",
          any("SOURCE" in p for p in (empty_map.get("problems") or []))
          and any("TARGET" in p for p in (empty_map.get("problems") or [])),
          json.dumps(empty_map.get("problems"))[:200])

    sr = M.call("set_retarget_rigs", {"path": ret, "source": hrig, "target": drig})
    check("T244 both rigs are set", sr.get("ok") is True, json.dumps(sr)[:170])
    # THE assertion: the mapping exists purely as a side effect of setting the rigs.
    check("T244 chains were auto-mapped as a SIDE EFFECT of setting the rigs",
          (sr.get("chainCount") or 0) > 0, "chainCount=%s" % sr.get("chainCount"))
    check("T244 and the response says that happened",
          "auto-mapped" in (sr.get("note") or ""), (sr.get("note") or "")[:180])
    # The mapping is keyed by the TARGET rig's chains, not the source's.
    tgt_names = {c.get("name") for c in (M.call("list_ik_rig", {"path": drig}).get("chains") or [])}
    map_names = {m.get("targetChain") for m in (sr.get("mapping") or [])}
    check("T244 the mapping is keyed by the TARGET rig's chains", map_names == tgt_names,
          "%s vs %s" % (sorted(map_names), sorted(tgt_names)))

    bad = M.call("set_retarget_rigs", {"path": ret, "source": hrig, "target": "/Game/NoSuchRig_zz"})
    check("T244 a bad second rig refuses without applying the first",
          bad.get("ok") is False and "neither rig was applied" in (bad.get("error") or ""),
          (bad.get("error") or "")[:180])

    # ------------------------------------------------------------------ T245 auto-map
    print("\n=== T245: auto-mapping ===")
    orphan = new_asset("IKRetargeter", "T245", st)
    q = M.call("auto_map_retarget_chains", {"path": orphan})
    check("T245 auto-map with no rigs is refused", q.get("ok") is False, json.dumps(q)[:140])
    # The engine's whole body sits inside a target-rig check, so it would do nothing and say nothing.
    check("T245 and the refusal names what the engine would have done",
          "reported success" in (q.get("error") or ""), (q.get("error") or "")[:200])

    # clear must IMPLY remapExisting. Without it the engine skips every chain that already has a
    # source - exactly the set being cleared - and reports success having done nothing. That is also
    # why this parameter is not called "force": the audit harness strips "force" from every payload
    # alongside "confirm" and "save", so force=True arrived as False and the endpoint half-worked.
    c = M.call("auto_map_retarget_chains", {"path": ret, "mode": "clear"})
    check("T245 clear unmaps everything even without remapExisting",
          c.get("ok") is True and c.get("unmappedCount") == c.get("chainCount"),
          "unmapped=%s of %s" % (c.get("unmappedCount"), c.get("chainCount")))
    check("T245 and it says it applied remapExisting for you",
          "implies remapExisting" in (c.get("clearNote") or ""), (c.get("clearNote") or "")[:170])
    e = M.call("auto_map_retarget_chains", {"path": ret, "mode": "exact", "remapExisting": True})
    check("T245 remapExisting is honoured (it is NOT called force for this reason)",
          e.get("remapExisting") is True, e.get("remapExisting"))
    check("T245 exact maps the identically-named chains",
          (e.get("chainCount") or 0) - (e.get("unmappedCount") or 0) >= 3,
          "mapped=%s of %s" % ((e.get("chainCount") or 0) - (e.get("unmappedCount") or 0),
                               e.get("chainCount")))
    # Tail has no human equivalent, so an EXACT pass must leave it alone rather than invent one.
    tail = next((m for m in (e.get("mapping") or []) if m.get("targetChain") == "Tail"), {})
    check("T245 exact does not invent a match for Tail", tail.get("mapped") is False, json.dumps(tail))
    check("T245 the unmapped chain is called out with its consequence",
          "not retargeted" in (e.get("unmappedNote") or ""), (e.get("unmappedNote") or "")[:160])

    q = M.call("auto_map_retarget_chains", {"path": ret, "mode": "sideways"})
    check("T245 an unknown mode is refused with the valid ones",
          q.get("ok") is False and "fuzzy" in (q.get("error") or "") and "exact" in (q.get("error") or ""),
          (q.get("error") or "")[:180])
    nf = M.call("auto_map_retarget_chains", {"path": ret, "mode": "fuzzy"})
    check("T245 without force, the note explains why nothing may change",
          "already mapped were left alone" in (nf.get("forceNote") or ""),
          (nf.get("forceNote") or "")[:170])

    # ------------------------------------------------------------------ T246 manual mapping
    print("\n=== T246: mapping one chain by hand ===")
    s = M.call("set_retarget_chain_mapping", {"path": ret, "targetChain": "Tail",
                                              "sourceChain": "Spine"})
    check("T246 a valid pair is accepted", s.get("ok") is True, json.dumps(s)[:150])
    row = next((m for m in (s.get("mapping") or []) if m.get("targetChain") == "Tail"), {})
    check("T246 and the mapping reflects it", row.get("sourceChain") == "Spine", json.dumps(row))
    u = M.call("set_retarget_chain_mapping", {"path": ret, "targetChain": "Tail"})
    row = next((m for m in (u.get("mapping") or []) if m.get("targetChain") == "Tail"), {})
    check("T246 an empty source unmaps it", row.get("mapped") is False, json.dumps(row))

    # Both ends validated separately - SetSourceChain returns only a bool, so a caller could not
    # otherwise tell which end was wrong.
    q = M.call("set_retarget_chain_mapping", {"path": ret, "targetChain": "NotAChain_zz",
                                              "sourceChain": "Spine"})
    check("T246 an unknown TARGET chain is refused by name",
          q.get("ok") is False and "TARGET rig has no chain" in (q.get("error") or ""),
          (q.get("error") or "")[:170])
    q = M.call("set_retarget_chain_mapping", {"path": ret, "targetChain": "Spine",
                                              "sourceChain": "NotAChain_zz"})
    check("T246 an unknown SOURCE chain is refused by name",
          q.get("ok") is False and "SOURCE rig has no chain" in (q.get("error") or ""),
          (q.get("error") or "")[:170])
    check("T246 and the error lists what IS available", "It has:" in (q.get("error") or ""),
          (q.get("error") or "")[:190])

    # ------------------------------------------------------------------ T247 the final read
    print("\n=== T247: the finished retargeter reads as valid ===")
    M.call("auto_map_retarget_chains", {"path": ret, "mode": "exact", "remapExisting": True})
    f = M.call("list_retarget_chain_mapping", {"path": ret})
    check("T247 a fully configured retargeter is valid", f.get("valid") is True,
          json.dumps(f.get("problems"))[:200])
    check("T247 it names both rigs", f.get("sourceRig") and f.get("targetRig"),
          "%s / %s" % (f.get("sourceRig"), f.get("targetRig")))
    # The deprecated-property warning is the kind of thing that costs an afternoon.
    check("T247 and it warns that ChainMapping is the deprecated property",
          "DEPRECATED" in (f.get("sourceNote") or ""), (f.get("sourceNote") or "")[:170])

    same = new_asset("IKRetargeter", "T247same", st)
    M.call("set_retarget_rigs", {"path": same, "source": hrig, "target": hrig})
    q = M.call("list_retarget_chain_mapping", {"path": same})
    check("T247 source and target being the same rig is called out",
          any("SAME asset" in p for p in (q.get("problems") or [])),
          json.dumps(q.get("problems"))[:180])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("NOTE: scratch assets left under /Game/_MifIK/. Nothing was saved.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
