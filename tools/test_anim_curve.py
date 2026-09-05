"""Anim curve authoring: add/set_keys/remove, and the checkf sitting in the doorway.

T4200 IS THE TEST THAT MATTERS, and it is the reason these three endpoints are mostly guard.
UAnimSequenceBase::GetController() calls ValidateModel(), and ValidateModel is
checkf(DataModelInterface != nullptr, ...) - a PROCESS TERMINATION, not an error return. A cooked
AnimSequence has no data model by construction, because ShouldDataModelBeValid() is literally
!GetOutermost()->HasAnyPackageFlags(PKG_Cooked). So reaching for the controller on cooked content
does not fail; it takes the editor down. This suite fires all three endpoints at a real cooked
sequence and then asks self_audit whether the editor is still answering - a failed guard here is a
dead process rather than a bad response, so that question IS the assertion.

AND THE OBVIOUS PROBE IS ITSELF THE CRASH. IsDataModelValid() looks like the safe check and is only
half safe:

    if (ShouldDataModelBeValid()) { ValidateModel(); return DataModelInterface != nullptr; }
    return false;

On a COOKED asset it short-circuits and is safe; on an UNCOOKED one it calls ValidateModel - the
checkf. It therefore cannot answer "is this safe to touch" without risking the very termination it
is being asked about. The endpoints use GetDataModelInterface() != nullptr instead, a plain pointer
read that is safe on every asset, which is the same probe run_retarget settled on.

WHAT IS NOT EXERCISED HERE, and why. Every one of this project's 514 AnimSequences is cooked, and
create_asset cannot produce a usable uncooked one (it needs a skeleton and sampled bone tracks). So
the SUCCESS path - actually creating a curve and writing keys - is unreachable on DDS2 by
construction, exactly as it is for run_retarget. Curfew (uncooked 5.7) is where that half runs. This
suite covers the guards, the refusals and the read half, and says plainly that it covers nothing
else rather than leaving the gap to be inferred from a green result.

T4202 covers a refusal the engine would not give you. type:"vector" is accepted by the engine and
then thrown away: FRawCurveTracks::VectorCurves is UPROPERTY(transient) with the engine's own note
that they are "not evaluated or used for anything else but transient data for modifying bone track"
and are deliberately not serialized (AnimCurveTypes.h:1024-1030). Authoring one would report success
and vanish on save.
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

    anims = [a["path"] for a in
             (M.call("find_assets", {"class": "AnimSequence", "limit": 10}).get("assets") or [])
             if not M.is_scratch_fixture(a)]
    check("(setup) the project has AnimSequences to point at", len(anims) > 0, len(anims))
    if not anims:
        print("SKIPPED - no AnimSequence in this project.")
        return 0
    target = anims[0]

    # COOKED-ONLY FROM HERE TO T4202, and SKIPPED rather than failed where nothing is cooked.
    #
    # Every assertion below is about what these three endpoints REFUSE on cooked content - the
    # data-model checkf that would terminate the editor rather than return an error. An
    # uncooked project cannot exercise any of it, and reporting nine failures there made an
    # uncooked run unreadable: a real 5.7 regression and a missing cooked fixture looked
    # identical in the summary.
    #
    # AND THE UNGUARDED VERSION WROTE TO PROJECT CONTENT. Measured on Curfew 2026-09-05: with
    # no cooked asset to refuse, add_anim_curve SUCCEEDED on a real MetaHuman animation and
    # remove_anim_curve then reported removed:true, keysDestroyed:1, curveCount 1173 -> 1172.
    # The suite is written for a project where these calls cannot land; on one where they can,
    # it edits somebody's asset to assert a refusal that never comes. Skipping is not only
    # about readability.
    #
    # `is not False` rather than truthiness: project_is_cooked returns None when the question
    # could not be asked, and an unanswerable question is not a No. On None these run exactly
    # as they did before this guard existed.
    COOKED = M.project_is_cooked()
    if COOKED is False:
        print("")
        print("=== T4200 / T4201 SKIPPED - nothing in this project is cooked ===")
        print("  These assert what add_anim_curve, set_anim_curve_keys and remove_anim_curve")
        print("  REFUSE on a cooked sequence, and refuse for the right reason. There is no")
        print("  cooked AnimSequence here to refuse, so the question cannot be asked - which is")
        print("  not the same as the guard being broken.")
        print("  They are also WRITES here: unguarded, they succeed against real project")
        print("  content. Run this against a cooked project for that half.")
    else:
        # ------------------------------------------------------------------ T4200 the checkf
        print("\n=== T4200: a checkf is a dead editor, not an error - so it is checked first ===")
        for endpoint, payload in (
                ("add_anim_curve", {"assetPath": target, "name": "MifTestCurve"}),
                ("set_anim_curve_keys", {"assetPath": target, "name": "MifTestCurve",
                                         "keys": [{"time": 0, "value": 1}]}),
                ("remove_anim_curve", {"assetPath": target, "name": "MifTestCurve",
                                       "confirm": True})):
            r = M.raw_post(endpoint, payload)
            check("T4200 %s refuses a cooked sequence" % endpoint, r.get("ok") is False,
                  json.dumps(r)[:250])
            check("T4200 %s names the data model as the reason" % endpoint,
                  "data model" in (r.get("error") or ""), (r.get("error") or "")[:180])

        one = M.raw_post("add_anim_curve", {"assetPath": target, "name": "MifTestCurve"})
        check("T4200 and the refusal explains that the alternative was TERMINATION, not an error",
              "TERMINATE" in (one.get("error") or ""), (one.get("error") or "")[:250])
        check("T4200 it identifies the package as cooked, so the caller knows it is not a typo",
              "COOKED" in (one.get("error") or ""), (one.get("error") or "")[:200])

        # THE assertion. Three calls that would each have hit ValidateModel's checkf have now been
        # made; a failed guard is a dead process, so the editor answering is the whole proof.
        alive = M.call("self_audit", {})
        check("T4200 - the editor is still alive after all three", alive.get("ok") is True,
              "GetController() -> ValidateModel() is a checkf; a failed guard terminates the process")

        # ------------------------------------------------------------------ T4201 the read half
        print("\n=== T4201: the read half describes the same object the write half would ===")
        d = M.call("describe_animation", {"assetPath": target})
        check("T4201 describe_animation still works on a cooked sequence", d.get("ok") is True,
              json.dumps(d)[:200])
        check("T4201 curves[] is a list and curveCount agrees with it",
              isinstance(d.get("curves"), list)
              and d.get("curveCount") == len(d.get("curves") or []),
              "count=%s len=%s" % (d.get("curveCount"), len(d.get("curves") or [])))
        # UPGRADED FROM BARE NAMES. A name alone cannot tell you whether a curve has keys, and a
        # keyless curve evaluates to nothing while looking identical to a working one in a name list.
        rows = d.get("curves") or []
        if rows:
            check("T4201 each curve reports name, type AND keyCount, not just a name",
                  all(isinstance(c, dict) and {"name", "type", "keyCount"} <= set(c) for c in rows),
                  json.dumps(rows[:2])[:250])
        else:
            print("  NOTE  no animation in this project has curves, so the populated shape of")
            print("        curves[] is unexercised here. The empty-list contract above still holds,")
            print("        and it is asserted rather than assumed.")

    # ------------------------------------------------------------------ T4202 vector
    print("\n=== T4202: a curve type the engine accepts and then discards ===")
    # Ordered AFTER the data-model guard on purpose - the cheapest and most dangerous check runs
    # first - so on this cooked-only project the cooked refusal is what comes back. Asserted as
    # such rather than pretending the vector branch was reached.
    # ALSO COOKED-ONLY, for a subtler reason than the block above: this asserts the data-model
    # guard answers BEFORE the vector-curve guard, and an ordering between two guards is only
    # observable when both can fire. On uncooked content the first one never does, so the assertion
    # is about an ordering that does not occur rather than about a defect. It is also a WRITE.
    if COOKED is False:
        print("  T4202's ordering half SKIPPED - the data-model guard cannot fire on uncooked")
        print("  content, so 'which guard answers first' has no answer here.")
    else:
        v = M.raw_post("add_anim_curve", {"assetPath": target, "name": "MifVec", "type": "vector"})
        check("T4202 it is refused", v.get("ok") is False, json.dumps(v)[:200])
        check("T4202 on THIS project the data-model guard answers first, which is the right order "
              "- the guard that prevents a crash outranks the one that prevents a useless curve",
              "data model" in (v.get("error") or ""), (v.get("error") or "")[:180])
        print("  NOT EXERCISED: the vector-curve refusal itself. It sits after the data-model")
        print("  guard, and every AnimSequence here is cooked, so the guard always answers first.")
        print("  Reaching it needs an uncooked sequence - Curfew, not DDS2.")

    bad = M.raw_post("add_anim_curve", {"assetPath": "/Game/_Mif/NoSuchAnim", "name": "X"})
    check("T4202 an unresolvable asset is refused before any engine call",
          bad.get("ok") is False and "not found" in (bad.get("error") or ""),
          (bad.get("error") or "")[:180])
    notanim = M.raw_post("add_anim_curve", {"assetPath": anims[0].rsplit("/", 1)[0], "name": "X"})
    check("T4202 a non-animation asset is refused by class",
          notanim.get("ok") is False, (notanim.get("error") or "")[:180])

    print("\n  NOT EXERCISED, and stated rather than implied: the SUCCESS path of all three")
    print("  endpoints. All 514 AnimSequences in this project are cooked and create_asset cannot")
    print("  produce a usable uncooked one, so creating a curve and writing keys is unreachable")
    print("  here by construction. Curfew (uncooked 5.7) is where that half runs.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
