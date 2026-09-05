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

T145, added 2026-08-29: create_asset's genericness is itself a finding worth pinning down, not just
using. tools/capability_gaps.py's own weak name-match heuristic had been flagging 18 classes (curves,
AnimMontage, ParticleSystem, sound classes, UserDefinedEnum, PCGGraph and more) as having no author
endpoint - true by NAME, since none has a dedicated one, but false as a capability claim, since every
one of them is reachable through this generic endpoint. Verified live before writing the assertion,
not assumed from the class list alone.
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

    print("\n=== T145 [found 2026-08-29]: create_asset covers classes tools/capability_gaps.py missed ===")
    # capability_gaps.py's own weak name-match heuristic flagged 18 classes as having "no write
    # endpoint by name" - CurveFloat, AnimMontage, ParticleSystem, SoundClass, UserDefinedEnum,
    # PCGGraph and others. None of them has a DEDICATED author endpoint, which is why the heuristic
    # missed them, but every one of them is a concrete, non-Actor, non-Blueprint UObject subclass -
    # exactly what create_asset is generic over. Live-tested by hand before this suite existed (9 of
    # 11 spot-checked succeeded outright; the other 2 correctly refused as abstract, covered below).
    # This locks that finding in as regression coverage rather than leaving it as a one-off finding
    # that could silently stop being true.
    for cls in ("CurveFloat", "AnimMontage", "ParticleSystem", "SoundClass", "UserDefinedEnum",
                "PCGGraph", "CurveVector", "SubsurfaceProfile"):
        gp = M.call("create_asset", {"path": path + "_generic_" + cls, "class": cls})
        check("T145 %s creates via the generic path" % cls, gp.get("ok") is True, json.dumps(gp)[:200])
        check("T145 %s is registered, not just in memory" % cls, gp.get("registered") is True,
              json.dumps(gp)[:200])
    # The other half of the same finding: an abstract class in this same "no dedicated endpoint"
    # bucket is refused the SAME informative way T142 already proved for PrimaryDataAsset/DataAsset -
    # not a silent failure, and not a different code path for classes nobody wrote a dedicated
    # endpoint for.
    for cls in ("NavigationData",):
        na = M.call("create_asset", {"path": path + "_abstract_" + cls, "class": cls})
        check("T145 %s (abstract) is refused, not silently broken" % cls, na.get("ok") is False,
              json.dumps(na)[:200])
        check("T145 %s explains the cooked-game consequence" % cls,
              "ABSTRACT" in (na.get("error") or "") and "cooked game" in (na.get("error") or ""),
              (na.get("error") or "")[:200])

        # ------------------------------------------------------------------ T146 the crash this session found
    print("\n=== T146 [CRASH found live 2026-08-29]: create_asset{class:NiagaraSystem} took the editor down ===")
    # Found while checking whether create_asset's generic breadth (T145) extended to NiagaraSystem
    # too - it does not, on its own: a bare NewObject<UNiagaraSystem> crashed the editor mid-call
    # (the crash journal showed a "start" for this exact create_asset with no matching "end"). The
    # stock "New Niagara System" factory does the same NewObject and then ONE more call,
    # UNiagaraSystemFactoryNew::InitializeSystem, which MifBridgeUserTypes.cpp now also makes - the
    # same shape as the ULevelSequence::Initialize() fix a few lines above it in that file. THE
    # assertion here is not just ok:true - it is that the bridge answers AT ALL afterward, because a
    # suite that only checks the response would pass just as happily against an editor that had
    # already died one call earlier (same discipline as test_anim_nodes.py's T550).
    niagara_path = path + "_niagara_crash_fix"
    nr = M.call("create_asset", {"path": niagara_path, "class": "NiagaraSystem"}, timeout=90)
    check("T146 create_asset succeeds", nr.get("ok") is True, json.dumps(nr)[:220])
    check("T146 THE EDITOR IS STILL ALIVE",
          M.bridge_responsive() is True,
          "the bridge stopped answering - create_asset{class:NiagaraSystem} crashed the editor again")
    if nr.get("ok"):
        # Not just "did not crash" - genuinely well-formed, the same standard T141 holds create_asset
        # to generally: read back through a REAL Niagara-aware endpoint, not just re-asked of itself.
        desc = M.call("describe_niagara_system", {"path": niagara_path}, timeout=60)
        check("T146 the created system is genuinely usable, not just non-crashing",
              desc.get("ok") is True, json.dumps(desc)[:220])
        check("T146 and reports the expected empty-system shape (0 emitters, none added yet)",
              desc.get("emitterCount") == 0, json.dumps(desc)[:220])

        print("\n=== T147: AnimSequence is REFUSED, because creating one terminates the editor ===")
    # THIS IS A CRASH REGRESSION TEST, not a contract test. On 2026-08-31 a single
    # create_asset {class: AnimSequence} took a running editor down:
    #
    #     Assertion failed: MovieScene
    #     [AnimSequencerDataModel.cpp:947] No Movie Scene found for SequencerDataModel
    #
    # A plain NewObject leaves the sequencer data model without its MovieScene; the engine's own flow
    # builds it in UAnimSequenceFactory, which requires a target skeleton create_asset has no parameter
    # for. The assert fires on whatever touches the asset NEXT - about a third of a second later, long
    # after this endpoint has answered ok:true - which is why the refusal has to come BEFORE
    # construction rather than being repaired after it like UUserDefinedEnum and UNiagaraSystem are.
    r = M.call("create_asset", {"path": path + "_animseq", "class": "AnimSequence"}, timeout=90)
    check("T147 AnimSequence is refused rather than created", r.get("ok") is False, json.dumps(r)[:220])
    check("T147 and the refusal names the assert, so the next reader does not have to rediscover it",
          "AnimSequencerDataModel.cpp:947" in (r.get("error") or ""), (r.get("error") or "")[:260])
    check("T147 and says why the endpoint cannot do it - no skeleton parameter",
          "skeleton" in (r.get("error") or "").lower(), (r.get("error") or "")[:260])
    # THE ASSERTION THAT MATTERS MOST, and it is the cheapest one here.
    check("T147 and the editor is still alive after the call", M.bridge_responsive() is True,
          "the refusal did not hold - this is the crash reopening")

    # AND THE OTHER DIRECTION, because the first version of this refusal was TOO WIDE. It matched
    # UAnimSequenceBase, which swept in AnimMontage - a class T145 above proves is creatable and
    # registers correctly. The suite caught it within a minute of the build. UAnimMontage and
    # UAnimComposite are UAnimCompositeBase: they reference other animations rather than owning bone
    # tracks, and do not build the data model whose absence is fatal. "Over-matching is safe" only holds
    # when the thing being over-matched does nothing useful.
    m = M.call("create_asset", {"path": path + "_animmontage", "class": "AnimMontage"}, timeout=90)
    check("T147 but AnimMontage is NOT swept up by the refusal - it works and a suite proves it",
          m.get("ok") is True, json.dumps(m)[:220])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f2 in FAIL:
        print("  FAILED: %s\n          %s" % f2)
    print("scratch left under /Game/_MifAsset - never saved, and removing it would mean confirm:true")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
