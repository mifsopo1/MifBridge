"""duplicate_asset must REFUSE a cooked asset it cannot safely duplicate, not attempt it.

Found live 2026-08-28: duplicating a real DDS2 static mesh (S_Volcano_02, Brushify content) took the
whole editor down - a hard engine assertion inside UStaticMesh::Build
("Assertion failed: Owner->IsMeshDescriptionValid(0)", StaticMesh.cpp:3086), triggered by the
post-duplicate rebuild step reaching for MeshDescription bulk data that cook had already stripped.
Exact same root cause, different subsystem, as the ALREADY-DOCUMENTED cooked-Niagara crash
(docs/02_GOTCHAS.md section 6c) - which had a comment in the source and a mention in test_niagara.py's
docstring, but no test that ever actually DROVE the refusal path. Neither gap existed by design; both
were just never exercised.

Fixed in MifBridgeAssetOps.cpp by extending the existing Niagara-only guard to also cover a cooked
StaticMesh, checked by class name (same reasoning the Niagara guard already used: recognising an asset
in order to REFUSE it should not require a hard dependency on that asset type's whole module). Verified
with a real Build.bat on BOTH engines this plugin targets (DDS2's actual 5.3.2 and the 5.7 probe) before
this suite was written, not inferred from the source.

THE ASSERTION IS THAT THE EDITOR SURVIVES, same discipline as test_set_struct_member.py's T153 crash
guard for a cooked UserDefinedStruct. A failed guard here is a fatal assertion, not an error return -
self_audit answering afterward is the real proof, not just the refusal's own ok:false.

T940: cooked StaticMesh refusal (the exact case that crashed the editor).
T941: cooked NiagaraSystem refusal (the guard this one was modelled on - had a comment, never had a
      driven test).
T942: a normal, NOT-cooked scratch Blueprint still duplicates successfully - the guard must not have
      widened into refusing every asset of a type it checks, only cooked ones.
"""
import json
import sys
import time

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
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T940 cooked StaticMesh
    print("\n=== T940: duplicate_asset refuses a cooked StaticMesh - the exact crash reproduction ===")
    meshes = M.call("find_assets", {"class": "StaticMesh", "pathPrefix": "/Game/", "limit": 5}).get("assets") or []
    real_mesh = next((a.get("path") for a in meshes if "_Mif" not in (a.get("path") or "")), None)
    check("T940 (setup) a real StaticMesh exists to try", bool(real_mesh), real_mesh)
    if real_mesh:
        r = M.call("duplicate_asset", {"path": real_mesh, "newPath": "/Game/_MifDupGuard/SM_%d" % st})
        check("T940 the duplicate is refused, not attempted", r.get("ok") is False, json.dumps(r)[:200])
        check("T940 and explains the real reason (cooked, crashes UStaticMesh::Build)",
              "COOKED" in (r.get("error") or "") and "StaticMesh" in (r.get("error") or ""),
              r.get("error"))
        # THE assertion - see module docstring. A failed guard here is a fatal engine assertion, not
        # an error return, so the editor answering at all afterward is the real proof.
        alive = M.call("self_audit", {})
        check("T940 the editor is still alive afterward", alive.get("ok") is True,
              "a failed guard here is a fatal assertion, not an error return")

    # ------------------------------------------------------------------ T941 cooked NiagaraSystem
    print("\n=== T941: duplicate_asset refuses a cooked NiagaraSystem - the guard this one was modelled on ===")
    systems = M.call("find_assets", {"class": "NiagaraSystem", "pathPrefix": "/Game/", "limit": 5}).get("assets") or []
    real_ns = next((a.get("path") for a in systems if "_Mif" not in (a.get("path") or "")), None)
    check("T941 (setup) a real NiagaraSystem exists to try", bool(real_ns), real_ns)
    if real_ns:
        r = M.call("duplicate_asset", {"path": real_ns, "newPath": "/Game/_MifDupGuard/NS_%d" % st})
        check("T941 the duplicate is refused, not attempted", r.get("ok") is False, json.dumps(r)[:200])
        check("T941 and explains the real reason (cooked, crashes Niagara's PostLoad)",
              "COOKED" in (r.get("error") or "") and "Niagara" in (r.get("error") or ""), r.get("error"))
        alive = M.call("self_audit", {})
        check("T941 the editor is still alive afterward", alive.get("ok") is True,
              "a failed guard here is a fatal exception, not an error return")

    # ------------------------------------------------------------------ T942 a normal duplication still works
    print("\n=== T942: a normal, NOT-cooked scratch Blueprint still duplicates successfully ===")
    src = "/Game/_MifDupGuard/BP_Src_%d" % st
    dst = "/Game/_MifDupGuard/BP_Dst_%d" % st
    made = M.call("create_blueprint", {"path": src, "parentClass": "Actor"})
    check("T942 (setup) a scratch source blueprint is created", made.get("ok") is True, json.dumps(made)[:200])
    if made.get("ok"):
        r = M.call("duplicate_asset", {"path": src, "newPath": dst})
        check("T942 the duplicate succeeds - the guard did not widen to refuse everything",
              r.get("ok") is True, json.dumps(r)[:200])
        # NOT r.get("duplicated") - that is duplicate_asset asserting its own success, the exact
        # "verify through a path that cannot observe the thing" shape this project has already
        # shipped a real bug behind (create_asset checked the global UObject hash its own NewObject
        # had just written to, under a comment claiming it checked the registry). Ask the ASSET
        # REGISTRY instead: a ghost asset - present in memory, invisible to find_assets, gone on
        # restart - passes the old check and fails this one, which is the entire point.
        found = M.call("find_assets", {"pathPrefix": dst, "limit": 5}).get("assets") or []
        check("T942 and the new asset really exists - confirmed against the asset registry, not "
              "against duplicate_asset's own response",
              any((a.get("path") or "").startswith(dst) for a in found),
              json.dumps({"looked_for": dst, "registry_returned": [a.get("path") for a in found]})[:250])
        SC.confirm_call("delete_asset", {"path": src})
        SC.confirm_call("delete_asset", {"path": dst})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
