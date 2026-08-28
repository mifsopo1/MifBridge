"""add_simplified_collision must REFUSE a cooked StaticMesh, not attempt it - EVERY shape crashes.

Found live 2026-08-28, the SAME day and the SAME investigation thread as duplicate_asset's cooked-
StaticMesh crash (docs/01_POSTMORTEMS.md), but a genuinely different bug in a genuinely different
endpoint - not a repeat discovery of the same one. Testing add_simplified_collision{shape:"box"} on a
real DDS2 mesh (S_Volcano_02) took the whole editor down a second time: EXCEPTION_ACCESS_VIOLATION
reading address 0x50, inside UnrealEditor-MeshDescription.dll. Reading the ENGINE's own source
(GeomFitUtils.cpp) rather than guessing found the exact line: `GenerateBoxAsSimpleCollision` calls
`StaticMesh->GetMeshDescription(0)->ComputeBoundingBox()` with NO NULL CHECK. On a cooked mesh,
GetMeshDescription(0) returns null (the editor-only bulk data is stripped), and the arrow-dereference
crashes immediately. Every OTHER shape (sphere, capsule, the k-DOP family) needs the same real
geometry to fit a shape against, and gets it the same way - confirmed by re-running all four shape
families against the SAME crashing mesh after the fix and getting a clean refusal every time, not by
reading only the one function that happened to crash first.

Fixed in MifBridgeCollision.cpp by checking `Mesh->GetMeshDescription(0)` directly, BEFORE any
generator runs - checked against the literal condition that is about to be dereferenced, not inferred
from a PKG_Cooked flag the way duplicate_asset's guard is (a deliberately different, more precise
technique for a case where the precise condition is cheap to check directly).

remove_collision is NOT affected - confirmed by reading its own handler, not assumed from the shared
"the crash was in collision code" framing: it only calls BS->RemoveSimpleCollision() on the existing
AggGeom array, which needs no mesh geometry at all. Live-verified during the same investigation that
fixing add_simplified_collision required (self_audit answered immediately after both a refused
add_simplified_collision call and a real remove_collision call on the same real mesh).

T932's remove_collision check does a REAL removal against real content, same as this session's
established precedent for T900's backup_blueprint (copies a real asset's file) and every scratch-actor
test spawned far from real content this session - nothing here is ever saved, so it reverts completely
on the next editor restart, and there is no create_static_mesh endpoint to build a genuinely disposable
mesh instead (duplicate_asset now correctly refuses to COPY a cooked one, for the same crash-class
reason this whole file exists). Removing simple collision is reversible in exactly the sense every
other real-content touch this session already relies on: unsaved, gone on restart, not a lasting
change to anything Andre would see persist.

T930: every shape refuses cleanly on a cooked mesh, and the editor survives each one - the assertion
that matters, same discipline as test_duplicate_cooked_guard.py and test_set_struct_member.py's T153.
T931: the refusal names the real reason, not a generic failure.
T932: remove_collision's refusal (no confirm), then a real removal, verifying self_audit survives it.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T930/T931 the crash reproduction
    print("\n=== T930/T931: add_simplified_collision refuses EVERY shape on a cooked mesh ===")
    meshes = M.call("find_assets", {"class": "StaticMesh", "pathPrefix": "/Game/", "limit": 5}).get("assets") or []
    real_mesh = next((a.get("path") for a in meshes if "_Mif" not in (a.get("path") or "")), None)
    check("T930 (setup) a real StaticMesh exists to try", bool(real_mesh), real_mesh)
    if real_mesh:
        for shape in ("box", "sphere", "capsule", "10dop-x", "18dop", "26dop"):
            r = M.call("add_simplified_collision", {"path": real_mesh, "shape": shape})
            check("T930 shape=%s is refused, not attempted" % shape, r.get("ok") is False,
                  json.dumps(r)[:200])
            check("T931 shape=%s explains the real reason (no MeshDescription)" % shape,
                  "MeshDescription" in (r.get("error") or ""), r.get("error"))
            # THE assertion. A failed guard here is a fatal engine access violation, not an error
            # return, so the editor answering at all afterward is the real proof it held.
            alive = M.call("self_audit", {})
            check("T930 shape=%s - the editor is still alive afterward" % shape, alive.get("ok") is True,
                  "a failed guard here is a fatal access violation, not an error return")

    # ------------------------------------------------------------------ T932 remove_collision
    print("\n=== T932: remove_collision - the refusal, then a real removal, editor survives ===")
    if real_mesh:
        rc = M.call("remove_collision", {"path": real_mesh})
        check("T932 refuses without confirm", rc.get("ok") is False, json.dumps(rc)[:200])
        check("T932 and says confirm is what is missing", "confirm" in (rc.get("error") or ""),
              (rc.get("error") or "")[:150])

        # A real removal on real content - see the module docstring for why this is consistent with
        # this session's established "nothing saved, reverts on restart" precedent, and why no scratch
        # alternative exists here.
        real = M.raw_post("remove_collision", {"path": real_mesh, "confirm": True})
        check("T932 the real removal succeeds", real.get("ok") is True, json.dumps(real)[:250])
        check("T932 and reports both fields (whatever they were)",
              "hadCollision" in real and "removedPrimitives" in real, json.dumps(real)[:200])
        alive = M.call("self_audit", {})
        check("T932 the editor is still alive after a real removal", alive.get("ok") is True,
              "remove_collision only touches BodySetup, never MeshDescription - should always be safe")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
