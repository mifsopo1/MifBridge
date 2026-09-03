"""check_consolidate_assets / consolidate_assets - the write half delete_asset dead-ends into.

delete_asset reports blockedBy.registryReferencers and then offers no operation that can clear
them. This is that operation: repoint every referencer of N sources at one target.

SPLIT IN TWO, by the rule settled twice already today: the safety gate classifies whole ENDPOINTS,
so a dryRun flag inside a gated endpoint is unreachable in the mode where you most want to ask.
check_consolidate_assets runs the whole ladder and touches nothing; consolidate_assets acts and is
gated. They share one ladder, so the preview cannot drift from the act.

T5102 IS THE TRAP, and it is why the open-editor list is snapshotted rather than checked per source.
ObjectTools.cpp:1443 calls CloseAllAssetEditors() unconditionally in a live editor - ALL editors,
not just the sources' - and if any refuses to close it returns an EMPTY, ERROR-FREE
FConsolidationResults. So "aborted at the close gate" and "there were no referencers" are the same
response. Comparing the referencer count against the dirtied count is the only way to tell them
apart, and the endpoint fails loudly with the editor count when they disagree. A per-source
open-editor pre-check - the obvious shape - would not catch it, because the gate is about every
OTHER editor too.

T5101 IS THE COOKED REFUSAL, which is what this project can actually exercise. A reference inside a
mounted pak cannot be rewritten or re-saved, so consolidating would report success for edits that
vanish on the next restart. Note the test has to be name-based: IsCookedOrContainerPackage takes a
loaded UPackage*, and GetReferencers hands back UNLOADED package FNames.

NOT EXERCISED: a successful consolidation. Every material in this project is cooked, so every
referencer is container content and the ladder refuses before the engine is reached - which is the
correct outcome, and the reason this suite tests the ladder rather than the act. An uncooked project
is where the act runs.
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

    # CONTAINER-ORIGIN materials on purpose. Cooked referencers are what makes the T5101 branch
    # reachable at all on this project, and an unfiltered pick quietly returned loose engine
    # materials instead - the branch then reported itself unexercised for no good reason.
    mats = [a["path"] for a in
            (M.call("find_assets", {"class": "Material", "origin": "container",
                                    "limit": 6}).get("assets") or [])]
    if len(mats) < 2:
        # SKIP SCRATCH on the fallback. The branch above is safe by construction - container
        # origin means a package with no loose file, which a scratch asset can never be - but
        # this one is unscoped, and consolidating another suite's two Materials is exactly the
        # blast radius T5100 is written to measure.
        mats = [a["path"] for a in
                (M.call("find_assets", {"class": "Material", "limit": 20}).get("assets") or [])
                if not M.is_scratch_fixture(a)]
    tex = [a["path"] for a in
           (M.call("find_assets", {"class": "Texture2D", "limit": 1}).get("assets") or [])]
    check("(setup) two materials and a texture to work with", len(mats) >= 2 and len(tex) >= 1,
          "%d materials, %d textures" % (len(mats), len(tex)))
    if len(mats) < 2 or not tex:
        print("SKIPPED - not enough assets.")
        return 0

    # ------------------------------------------------------------------ T5100 the ladder
    print("=== T5100: every check runs before anything is touched ===")
    r = M.call("check_consolidate_assets", {"target": mats[0], "sources": [mats[1]]})
    check("T5100 the preview succeeds", r.get("ok") is True, json.dumps(r)[:230])
    check("T5100 it reports whether the consolidation could proceed at all",
          isinstance(r.get("canConsolidate"), bool), r.get("canConsolidate"))
    blocked = r.get("blockedBy") or {}
    for f in ("notFound", "classMismatch", "rootedSources", "targetDependsOnSource",
              "cookedReferencers"):
        check("T5100 blockedBy names %s separately" % f, f in blocked, sorted(blocked))
    # Separate lists, not one "blocked" flag: these lead to different fixes, and collapsing them
    # is what delete_asset's own comment says burned agents before.
    check("T5100 and the referencer set is reported so the blast radius is visible",
          isinstance(r.get("referencers"), list)
          and r.get("referencersFound") == len(r.get("referencers") or []),
          json.dumps({"found": r.get("referencersFound")}))

    same = M.raw_post("check_consolidate_assets", {"target": mats[0], "sources": [mats[0]]})
    check("T5100 an asset that is both target and source is refused",
          same.get("ok") is False and "both the target and a source" in (same.get("error") or ""),
          (same.get("error") or "")[:200])

    mismatch = M.call("check_consolidate_assets", {"target": mats[0], "sources": [tex[0]]})
    check("T5100 a class mismatch is caught, naming BOTH classes rather than just refusing",
          (mismatch.get("blockedBy") or {}).get("classMismatch")
          and (mismatch["blockedBy"]["classMismatch"][0].get("sourceClass")
               != mismatch["blockedBy"]["classMismatch"][0].get("targetClass")),
          json.dumps((mismatch.get("blockedBy") or {}).get("classMismatch"))[:220])
    check("T5100 and a mismatch makes canConsolidate false",
          mismatch.get("canConsolidate") is False, mismatch.get("canConsolidate"))

    nosrc = M.raw_post("check_consolidate_assets", {"target": mats[0], "sources": []})
    check("T5100 an empty sources[] is refused rather than treated as a no-op",
          nosrc.get("ok") is False, (nosrc.get("error") or "")[:180])
    notgt = M.raw_post("check_consolidate_assets", {"sources": [mats[1]]})
    check("T5100 a missing target is refused", notgt.get("ok") is False,
          (notgt.get("error") or "")[:180])

    # ------------------------------------------------------------------ T5101 cooked
    print("\n=== T5101: a reference inside a pak cannot be rewritten ===")
    cooked = None
    for i in range(len(mats)):
        for j in range(len(mats)):
            if i == j:
                continue
            c = M.call("check_consolidate_assets", {"target": mats[i], "sources": [mats[j]]})
            if (c.get("blockedBy") or {}).get("cookedReferencers"):
                cooked = c
                break
        if cooked:
            break
    if cooked:
        check("T5101 a cooked referencer blocks the consolidation",
              cooked.get("canConsolidate") is False
              and len(cooked["blockedBy"]["cookedReferencers"]) > 0,
              json.dumps(cooked["blockedBy"]["cookedReferencers"])[:220])
        # THE assertion: it names WHICH package, not just that something was cooked.
        check("T5101 and names the offending package, so the caller can see what stopped it",
              cooked["blockedBy"]["cookedReferencers"][0].startswith("/"),
              cooked["blockedBy"]["cookedReferencers"][0])
        check("T5101 with a note saying a success there would vanish on restart",
              "restart" in (cooked.get("cookedNote") or ""), (cooked.get("cookedNote") or "")[:200])
    else:
        print("  NOTE  no cooked referencer found among these materials, so that branch is")
        print("        unexercised in this run.")

    # ------------------------------------------------------------------ T5102 the split + trap
    print("\n=== T5102: the preview is never gated; the act always is ===")
    mode = M.write_mode()

    # confirm:true IS ONLY SENT WHERE THE GATE IS KNOWN TO REFUSE IT, and the ordering is the whole
    # point. This used to call consolidate_assets with confirm:true BEFORE looking at the mode, so in
    # FULL mode the call went through against whatever two materials find_assets returned - and its
    # result was never asserted on, because the full-mode branch below only tests the no-confirm
    # refusal. It did no harm on this project by luck: find_assets with no pathPrefix returns
    # /Engine/ content first, and the endpoint reports blockedBy.rootedSources for engine material
    # held in memory. On a project whose first two materials are ordinary /Game/ content that passes
    # the ladder, the same code consolidates one real material into another and deletes the source.
    #
    # A suite that would destroy real content on someone else's project is a defect whether or not it
    # ever fires here, and consolidating saves packages, which this work is not supposed to do at all.
    if mode != "full":
        w = M.raw_post("consolidate_assets", {"target": mats[0], "sources": [mats[1]],
                                              "confirm": True})
        check("T5102 in '%s' mode the act is refused by the gate" % mode,
              w.get("ok") is False and "safety gate" in (w.get("error") or ""),
              (w.get("error") or "")[:200])
        again = M.call("check_consolidate_assets", {"target": mats[0], "sources": [mats[1]]})
        check("T5102 - and the PREVIEW still works in the same mode, which is the whole reason "
              "these are two endpoints rather than one with a dryRun flag",
              again.get("ok") is True, json.dumps(again)[:200])
    else:
        # FULL MODE TESTS WHAT IT CAN TEST WITHOUT ACTING. The gate is off here by definition, so
        # there is no refusal to assert and no safe way to prove the act - only that the confirm
        # requirement itself still stands, and that the preview is unaffected by the mode.
        noconf = M.raw_post("consolidate_assets", {"target": mats[0], "sources": [mats[1]]})
        check("T5102 acting without confirm is refused", noconf.get("ok") is False,
              (noconf.get("error") or "")[:220])
        preview = M.call("check_consolidate_assets", {"target": mats[0], "sources": [mats[1]]})
        check("T5102 - and the PREVIEW still works, which is the whole reason these are two "
              "endpoints rather than one with a dryRun flag",
              preview.get("ok") is True, json.dumps(preview)[:200])
        print("  NOTE  the gated-refusal arm is UNEXERCISED in full mode, and deliberately so:")
        print("        proving it needs confirm:true, and in full mode nothing would stop that")
        print("        from consolidating two real assets. Run in scratch mode to cover it.")
        # WHICH GATE FIRED MATTERS, and asserting the confirm wording unconditionally made this
        # test ORDER-DEPENDENT. consolidate_assets refuses on the most fundamental failure first,
        # so when find_assets happens to hand back /Engine materials - where the source is ROOTED
        # and can never be consolidated at all - the ladder stops there and never reaches the
        # missing-confirm check. That is correct behaviour being reported as a test failure.
        blocked = noconf.get("blockedBy") or {}
        earlier = {k: v for k, v in blocked.items() if v}
        if earlier:
            print("  NOTE  the ladder stopped at an EARLIER gate than confirm (%s), which is"
                  % ", ".join(sorted(earlier)))
            print("        correct - the most fundamental refusal wins. Asserting that instead.")
            check("T5102 the earlier refusal names which check failed and on which asset, so it "
                  "is actionable rather than just negative",
                  all(isinstance(v, list) and v for v in earlier.values())
                  and noconf.get("canConsolidate") is False,
                  json.dumps(blocked)[:250])
        else:
            check("T5102 and the refusal warns it CLOSES EVERY OPEN ASSET EDITOR",
                  "CLOSES EVERY OPEN ASSET EDITOR" in (noconf.get("error") or ""),
                  (noconf.get("error") or "")[:250])

    # The open-editor snapshot is what makes the silent abort diagnosable, so it must be present
    # even when nothing is open.
    check("T5102 the preview always reports the open asset editors, even when there are none",
          isinstance(r.get("openAssetEditors"), list), type(r.get("openAssetEditors")).__name__)
    dry = M.raw_post("check_consolidate_assets", {"target": mats[0], "sources": [mats[1]],
                                                  "dryRun": True})
    check("T5102 dryRun on the preview is refused - it IS the dry run",
          dry.get("ok") is False and "IS the dry run" in (dry.get("error") or ""),
          (dry.get("error") or "")[:200])

    check("T5102 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
          "consolidation closes editors and can open modals")

    print("\n  NOT EXERCISED: a successful consolidation, and the silent-abort detection that goes")
    print("  with it. Every material here is cooked, so every referencer is container content and")
    print("  the ladder refuses before the engine is reached - which is the correct outcome. An")
    print("  uncooked project is where the act runs.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
