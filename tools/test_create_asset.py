"""create_asset - instantiate a data-asset class at a /Game path.

Closes an asymmetry: create_blueprint could author a UDataAsset subclass that nothing was then able to
instantiate.

T141 is the check that matters, and it is not "did the call succeed". An unregistered object answers
get_property and set_property perfectly, never appears in find_assets or save_dirty_packages, and
evaporates on restart - a whole session reporting ok:true and losing everything. So the test asserts
the asset is in the REGISTRY and its package is DIRTY, not merely that a path came back.

Two guards exist because of what they prevent rather than for tidiness:
  * an abstract class yields an asset that loads in the editor and fails in the cooked game, with no
    complaint until runtime;
  * the destination check had two real bugs found by testing rather than reasoning, both recorded in
    T143's comments - they are the interesting part of this file.
"""
import json
import random
import sys

import mifaudit as M

PASS, FAIL = [], []
CONCRETE = "/Script/AIModule.BlackboardData"     # concrete, cheap, and independently verifiable


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    path = "/Game/_MifAsset/DA_%d" % random.randint(100000, 999999)

    # ------------------------------------------------------------------ T140 create
    print("\n=== T140: a concrete data-asset class can be instantiated ===")
    r = M.call("create_asset", {"path": path, "class": CONCRETE})
    print("  ", json.dumps({k: v for k, v in r.items() if k != "note"})[:230])
    check("T140 created", r.get("ok") is True, json.dumps(r)[:220])
    check("T140 it reports the object path", (r.get("assetPath") or "").startswith(path),
          r.get("assetPath"))
    check("T140 and the class it used", r.get("class") == CONCRETE, r.get("class"))

    # ------------------------------------------------------------------ T141 the one that matters
    print("\n=== T141 [not just ok:true]: it is REGISTERED, not merely in memory ===")
    ap = r.get("assetPath")
    # A HIGH limit, not 10. Scratch assets accumulate within an editor session - nothing here is
    # saved, so they only vanish on restart - and with ten already present from earlier runs the new
    # asset fell off the end of the page and the test reported the registry could not see it. The
    # assertion was about registration, and it was measuring pagination.
    f = M.call("find_assets", {"pathPrefix": "/Game/_MifAsset/", "limit": 500})
    found = [a.get("path") for a in (f.get("assets") or [])]
    # Without FAssetRegistryModule::AssetCreated the object works perfectly and is invisible here.
    check("T141 the asset registry can see it", ap in found,
          "registry has %s - an unregistered asset answers every read and vanishes on restart" % found)
    d = M.call("list_dirty_packages", {})
    check("T141 and its package is marked dirty",
          any("_MifAsset" in (x.get("name") or "") for x in (d.get("packages") or [])),
          "without MarkPackageDirty save_dirty_packages would never write it")
    check("T141 it says registered", r.get("registered") is True, r.get("registered"))
    # It should be a real, usable asset of that class - not just a named object.
    k = M.call("list_blackboard_keys", {"path": ap})
    check("T141 and it is genuinely an asset of that class", k.get("ok") is True, json.dumps(k)[:180])
    check("T141 the note warns it is not saved yet", "NOT saved" in (r.get("note") or ""),
          (r.get("note") or "")[:120])

    # ------------------------------------------------------------------ T142 abstract
    print("\n=== T142: an abstract class is refused ===")
    # PrimaryDataAsset and DataAsset are both abstract; an asset of one loads in the editor and fails
    # in the cooked game, which is the worst time to find out.
    for cls in ("PrimaryDataAsset", "DataAsset"):
        q = M.call("create_asset", {"path": path + "_x", "class": cls})
        check("T142 %s refused" % cls, q.get("ok") is False, json.dumps(q)[:160])
        check("T142 %s explains the cooked-game consequence" % cls,
              "ABSTRACT" in (q.get("error") or "") and "cooked game" in (q.get("error") or ""),
              (q.get("error") or "")[:160])

    # ------------------------------------------------------------------ T143 destination
    print("\n=== T143: the destination check, which had two real bugs ===")
    # BUG 1: plain FPackageName::DoesPackageExist consults the IoDispatcher, and in this
    # CookedEditorModKit setup /Game resolves through a pak container - so it answered TRUE for any
    # well-formed /Game path and refused EVERY creation. Fixed with the FileSystem filter.
    # BUG 2: FindObject(nullptr, <package path>) resolves the UPACKAGE, which exists in memory the
    # moment anything touches that path - including a previous failed attempt in the same session.
    # The question is whether an ASSET is there, so it now looks for Package.AssetName.
    fresh = "/Game/_MifAsset/DA_%d" % random.randint(100000, 999999)
    q = M.call("create_asset", {"path": fresh, "class": CONCRETE})
    check("T143 a fresh path is NOT reported as taken", q.get("ok") is True,
          "%s - this is the container/DoesPackageExist bug" % json.dumps(q)[:170])
    again = M.call("create_asset", {"path": fresh, "class": CONCRETE})
    check("T143 but a genuinely taken one IS", again.get("ok") is False, json.dumps(again)[:160])
    # The refusal actually comes from ValidateNewUserTypePath, which guards taken destinations before
    # create_asset's own check runs. That is fine and worth recording: the destination is covered
    # twice, by the shared path validator and by the disk/loaded check here, and the validator's
    # message is the one a caller sees for the ordinary case.
    check("T143 and it names the path it refused",
          fresh in (again.get("error") or ""), (again.get("error") or "")[:170])

    # ------------------------------------------------------------------ T144 wrong kinds
    print("\n=== T144: classes that are not assets are refused by kind ===")
    for cls, expect in (("StaticMeshActor", "placed"),
                        ("StaticMeshComponent", "placed")):
        q = M.call("create_asset", {"path": path + "_y", "class": cls})
        check("T144 %s refused" % cls, q.get("ok") is False, json.dumps(q)[:150])
        check("T144 %s explains" % cls, expect in (q.get("error") or ""), (q.get("error") or "")[:140])
    q = M.call("create_asset", {"path": path + "_z"})
    check("T144 a missing class is refused", q.get("ok") is False, json.dumps(q)[:150])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f2 in FAIL:
        print("  FAILED: %s\n          %s" % f2)
    print("scratch left under /Game/_MifAsset - never saved, and removing it would mean confirm:true")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
