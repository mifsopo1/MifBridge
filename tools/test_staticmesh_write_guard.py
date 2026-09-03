"""The StaticMesh rebuild assert, reachable from ANY set_property write.

UStaticMesh::PostEditChangeProperty calls Build() UNCONDITIONALLY (StaticMesh.cpp:4052), and the
build path contains checkf(Owner->IsMeshDescriptionValid(0)) (StaticMesh.cpp:3086). Cook strips the
editable MeshDescription, so a cooked mesh with source models and no description terminates the
editor on ANY property write - not a caught error, a process death with no MifBridge frame at the
top of the stack.

duplicate_asset has guarded this since it was hit live on DDS2's S_Volcano_02. set_property never
did, and it reaches the same Build() through PostEditChangeChainProperty.

WHAT THIS SUITE HONESTLY ESTABLISHES, and what it does not. The guard implements the literal
condition the assert tests - source models present AND GetMeshDescription(0) null - rather than "is
it cooked", because UStaticMesh::Build early-outs via CanBuild() when GetNumSourceModels() <= 0, so
a cooked mesh with no source models is safe and an uncooked one with a failed description is not.

THE GUARD DID NOT FIRE ON ANY OF THIS PROJECT'S MESHES. Twenty-five container-origin static meshes
were probed and every one has a valid MeshDescription(0), so the crash state could not be
constructed here. That means this suite proves the guard does NOT false-positive - reads still work,
writes to sound meshes still work - and does NOT prove the refusal branch, which is unexercised.
Saying otherwise would be claiming a fixed crash that was never reproduced.

Worth recording alongside: duplicate_asset's guard is class+cooked based and refuses S_Volcano_02,
while this precise test says writing to it is safe. Those are not in conflict - duplication rebuilds
a COPY whose description was never populated, which is a different object from the original whose
description is fine.

RESTORES WHAT IT TOUCHES. Every probe write is reverted to the value it found.
"""
import json
import sys

import mifaudit as M

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
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    # SKIP SCRATCH, the omission that already cost test_physics_asset a false failure and made it
    # add the same filter. This suite WRITES bAllowCPUAccess on every mesh it adopts and claims in
    # its log that it checked the project's meshes - a sample filled with GeometryScript boxes from
    # test_geometryscript can never reach the cooked-mesh state the guard exists to probe, so it
    # goes green while proving nothing and printing a false claim.
    meshes = [a["path"] for a in
              (M.call("find_assets", {"class": "StaticMesh", "limit": 40}).get("assets") or [])
              if not M.is_scratch_fixture(a)][:20]
    check("(setup) static meshes to probe", len(meshes) > 0, len(meshes))
    if not meshes:
        return 1

    # ------------------------------------------------------------------ T5200 reads unaffected
    print("=== T5200: reading a static mesh is always safe and stays safe ===")
    r = M.call("get_property", {"objectPath": meshes[0], "propertyPath": "LODGroup"})
    check("T5200 get_property on a static mesh works", r.get("ok") is True, json.dumps(r)[:200])
    tags = M.call("get_asset_tags", {"path": meshes[0]})
    check("T5200 and so does reading its registry tags, which load nothing at all",
          tags.get("ok") is True, json.dumps(tags)[:200])

    # ------------------------------------------------------------------ T5201 no false positives
    print("\n=== T5201: the guard must not refuse a mesh that is fine ===")
    probed = 0
    refused = 0
    reverted = 0
    for path in meshes:
        w = M.raw_post("set_property", {"objectPath": path,
                                        "propertyPath": "bAllowCPUAccess", "value": True})
        probed += 1
        if w.get("ok") is False and "MeshDescription" in (w.get("error") or ""):
            refused += 1
            continue
        if w.get("ok") and w.get("changed"):
            # RESTORE. These are real project assets; nothing is saved, but leaving a changed
            # value in memory is a change nobody asked for.
            back = M.raw_post("set_property", {"objectPath": path,
                                               "propertyPath": "bAllowCPUAccess", "value": False})
            if back.get("ok"):
                reverted += 1
    print("        probed %d mesh(es): %d refused by the guard, %d written and reverted"
          % (probed, refused, reverted))
    check("T5201 the guard did not refuse every mesh - it is not a blanket 'no static meshes'",
          refused < probed, "%d of %d refused" % (refused, probed))
    check("T5201 writes that were allowed really applied and were put back",
          reverted > 0 or refused == probed, "reverted %d" % reverted)

    # THE assertion that matters most: the editor survived every one of those writes. If the guard
    # were wrong in the permissive direction, this is where the process would have died.
    alive = M.call("self_audit", {})
    check("T5201 - the editor is still alive after %d property writes to static meshes" % probed,
          alive.get("ok") is True,
          "PostEditChangeProperty calls Build() unconditionally; the assert is a process death")

    if refused == 0:
        print("\n  NOT EXERCISED: the refusal branch. Every static mesh in this project has a valid")
        print("  MeshDescription(0), so the crash state cannot be constructed here - the guard")
        print("  correctly stayed out of the way on all %d. This suite therefore proves it does" % probed)
        print("  not false-positive; it does NOT prove the refusal, and claiming a fixed crash")
        print("  that was never reproduced would be overstating it.")
    else:
        one = None
        for path in meshes:
            w = M.raw_post("set_property", {"objectPath": path,
                                            "propertyPath": "bAllowCPUAccess", "value": True})
            if w.get("ok") is False and "MeshDescription" in (w.get("error") or ""):
                one = w
                break
        if one:
            check("T5201 the refusal names the assert rather than saying 'cannot write'",
                  "IsMeshDescriptionValid" in (one.get("error") or ""),
                  (one.get("error") or "")[:220])
            check("T5201 and says reading is still fine, so the caller knows what IS possible",
                  "READING this mesh is fine" in (one.get("error") or ""),
                  (one.get("error") or "")[:220])

    # ------------------------------------------------------------------ T5202 the scope note
    print("\n=== T5202: three of the four proposed LOD capabilities already exist ===")
    # LODGroup is a public UPROPERTY and PostEditChangeProperty special-cases it, calling
    # SetLODGroup - which resizes source models and retunes per-LOD reduction settings. So
    # set_property already adds, removes and retunes LODs through a group.
    d = M.call("describe_endpoint", {"name": "set_property"})
    check("T5202 set_property is registered and describable", d.get("ok") is True,
          json.dumps(d)[:180])
    lod = M.call("get_property", {"objectPath": meshes[0], "propertyPath": "LODGroup"})
    check("T5202 LODGroup is readable through the ordinary property path, which is why a "
          "dedicated setter was not built for it",
          lod.get("ok") is True, json.dumps(lod)[:200])
    nan = M.call("get_property", {"objectPath": meshes[0], "propertyPath": "NaniteSettings"})
    check("T5202 and so are the Nanite settings", nan.get("ok") is True, json.dumps(nan)[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
