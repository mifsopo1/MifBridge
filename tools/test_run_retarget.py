"""run_retarget - the OUTPUT half of the IK Retargeter, and the guards that make it survivable.

THIS SUITE NEVER SENDS confirm:true, IN ANY MODE. run_retarget creates and SAVES animation assets,
and not where the caller chooses: DuplicateAndRetarget hard-codes the destination to the TARGET
SKELETAL MESH's package (IKRetargetBatchOperation.cpp:107), so a successful run writes files into
whatever folder that mesh lives in - real project content, on any project where the target mesh is
real. There is no test worth that, so the success path is deliberately unexercised and this says so
rather than leaving it looking covered.

WHAT IS EXERCISED IS EVERY GUARD, which is where the work in this endpoint actually is:

T3200  the safety gate. run_retarget is on UnsafeEndpoints() beside save_package, because it
       persists to disk at a location no path check can constrain.
T3201  the preconditions RunRetarget only whispers about. It bails on missing assets, missing
       retargeter, missing source rig and missing target rig with a bare UE_LOG(LogTemp, Warning)
       and returns (IKRetargetBatchOperation.cpp:494-518), and DuplicateAndRetarget swallows that
       and hands back an empty array. Without pre-validation a caller gets created:[] and silence.
T3202  THE ONE THAT PREVENTS AN EDITOR KILL. Retargeting writes bone tracks into each duplicate
       through the editor-only data model; a cooked source has no UAnimDataModel and the write
       terminates the editor. Every source is checked first.

THE PROBE IS NOT THE OBVIOUS ONE, and that matters. IsDataModelValid() looks like the right call and
is not: on an asset that should have a model it runs ValidateModel() (AnimSequenceBase.h:315-320),
and ValidateModel IS the checkf - the probe would trigger the very crash it exists to prevent. The
endpoint reads GetDataModelInterface() != nullptr instead, which is a plain pointer read.

WHY T3202 IS SAFE TO RUN AT ALL, since it needs a fully configured retargeter: the cooked check runs
BEFORE the confirm check, so a call with no confirm has TWO independent barriers. Even if the cooked
guard failed completely, the missing confirm would still stop the batch before the engine was
touched. The test relies on that ordering rather than on the guard it is testing.

CLEANS UP: the scratch retargeter and IK rigs are deleted at the end. Nothing is saved.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

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

    st = int(time.time() % 100000)
    RTG = "/Game/_MifRT/RTG_Test%d" % st
    RIGS = ["/Game/_MifRT/Rig%dA%d" % (st, i) for i in (0, 1)]
    made = []
    mode = M.write_mode()
    print("write mode: %s" % mode)

    try:
        # ------------------------------------------------------------------ T3200 the gate
        print("\n=== T3200: it persists to disk, so the gate must own it ===")
        gated = M.raw_post("run_retarget", {"retargeter": "/Game/_MifRT/NoSuch",
                                            "animations": ["/Game/x"]})
        if mode != "full":
            check("T3200 run_retarget is refused outright in '%s' mode" % mode,
                  gated.get("ok") is False and "safety gate" in (gated.get("error") or ""),
                  json.dumps(gated)[:250])
            print("\n  NOT EXERCISED in this mode: every guard below sits behind the gate, so they")
            print("  cannot be reached from scratch or read mode. That is the gate working, not a")
            print("  gap - re-run with MIF_BRIDGE_WRITE_MODE=full to cover them.")
            print("\n" + "=" * 72)
            print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
            return 1 if FAIL else 0

        # In full mode the gate permits it, so the guards themselves are what stands between a
        # caller and a write. That is what the rest of this suite is for.
        check("T3200 in full mode the gate permits it and the HANDLER answers instead",
              gated.get("ok") is False and "safety gate" not in (gated.get("error") or ""),
              json.dumps(gated)[:250])
        check("T3200 an unresolvable retargeter is refused by name",
              "no asset at" in (gated.get("error") or ""), (gated.get("error") or "")[:180])

        # ------------------------------------------------------------------ setup
        print("\n=== setup: a scratch retargeter wired to two scratch IK rigs ===")
        meshes = [a["path"] for a in
                  (M.call("find_assets", {"class": "SkeletalMesh", "limit": 25}).get("assets") or [])
                  if not M.is_scratch_fixture(a)][:2]
        check("(setup) two distinct SkeletalMeshes exist", len(meshes) == 2, meshes)
        if len(meshes) < 2:
            return 1
        c = M.raw_post("create_asset", {"path": RTG, "class": "IKRetargeter"})
        check("(setup) a scratch IKRetargeter is created", c.get("ok") is True, json.dumps(c)[:200])
        if not c.get("ok"):
            return 1
        made.append(RTG)

        # ------------------------------------------------------------------ T3201 preconditions
        print("\n=== T3201: the preconditions RunRetarget only whispers about ===")
        norig = M.raw_post("run_retarget", {"retargeter": RTG, "animations": ["/Game/x"]})
        check("T3201 a retargeter with no SOURCE rig is refused", norig.get("ok") is False,
              json.dumps(norig)[:250])
        check("T3201 and the refusal explains what would otherwise happen - a log line and an "
              "empty list", "empty list" in (norig.get("error") or ""),
              (norig.get("error") or "")[:220])

        for i, m in enumerate(meshes):
            r = M.raw_post("create_asset", {"path": RIGS[i], "class": "IKRigDefinition"})
            if r.get("ok"):
                made.append(RIGS[i])
            M.raw_post("set_property", {"objectPath": RIGS[i] + "." + RIGS[i].rsplit("/", 1)[-1],
                                        "propertyPath": "PreviewSkeletalMesh", "value": m})
        sr = M.raw_post("set_retarget_rigs", {"path": RTG, "sourceRig": RIGS[0],
                                              "targetRig": RIGS[1]})
        check("(setup) both rigs are wired onto the retargeter", sr.get("ok") is True,
              json.dumps(sr)[:200])

        empty = M.raw_post("run_retarget", {"retargeter": RTG, "animations": []})
        check("T3201 an empty animations[] is refused rather than run as a no-op",
              empty.get("ok") is False and "non-empty" in (empty.get("error") or ""),
              (empty.get("error") or "")[:180])

        same = M.raw_post("run_retarget", {"retargeter": RTG, "animations": ["/Game/x"],
                                           "sourceMesh": meshes[0], "targetMesh": meshes[0]})
        check("T3201 the same mesh for source and target is refused - IsValid() rejects it",
              same.get("ok") is False and "SAME asset" in (same.get("error") or ""),
              (same.get("error") or "")[:200])

        notanim = M.raw_post("run_retarget", {"retargeter": RTG, "animations": [RIGS[0]]})
        check("T3201 a non-animation asset is skipped with its real class named",
              notanim.get("ok") is False
              and "not an animation asset" in json.dumps(notanim.get("skipped") or []),
              json.dumps(notanim)[:250])

        # `destination` must be refused as a PARAMETER, not silently ignored - the engine cannot
        # honour it, and accepting it would mean writing somewhere other than where it says.
        dest = M.raw_post("run_retarget", {"retargeter": RTG, "animations": ["/Game/x"],
                                           "destination": "/Game/_MifRT"})
        check("T3201 `destination` is refused with the reason, not quietly dropped",
              dest.get("ok") is False and "hard-codes the destination" in (dest.get("error") or ""),
              (dest.get("error") or "")[:220])

        # ------------------------------------------------------------------ T3202 the editor kill
        print("\n=== T3202: the cooked check - the guard that stops an editor kill ===")
        anims = [a["path"] for a in
                 (M.call("find_assets", {"class": "AnimSequence", "limit": 5}).get("assets") or [])
                 if not M.is_scratch_fixture(a)][:2]
        check("T3202 (setup) the project has AnimSequences to point at", len(anims) > 0, len(anims))
        if anims:
            # NO confirm, deliberately. The cooked check runs BEFORE the confirm check, so this call
            # has two independent barriers and does not depend on the guard it is testing.
            cooked = M.raw_post("run_retarget", {"retargeter": RTG, "animations": anims})
            check("T3202 cooked sources are refused", cooked.get("ok") is False,
                  json.dumps(cooked)[:250])
            skipped = cooked.get("skipped") or []
            check("T3202 every cooked asset is named individually, not just counted",
                  len(skipped) == len(anims)
                  and all("COOKED" in (s.get("reason") or "") for s in skipped),
                  json.dumps(skipped)[:300])
            check("T3202 and the reason says what would have happened - the editor terminating",
                  any("terminate the editor" in (s.get("reason") or "") for s in skipped),
                  json.dumps(skipped[:1])[:250])
            # A partial batch is the dangerous shape: 39 of 40 retargeted and reported as success.
            check("T3202 the WHOLE batch is refused, never partially run",
                  (cooked.get("createdCount") is None) and not cooked.get("created"),
                  json.dumps(cooked)[:250])

        alive = M.call("self_audit", {})
        check("T3202 - the editor is still alive", alive.get("ok") is True,
              "a failed cooked guard is a terminated editor, not an error response")

        print("\n  NOT EXERCISED, on purpose: a SUCCESSFUL retarget. It writes new assets into the")
        print("  TARGET MESH's package - real content on this project - and no test is worth that.")
        print("  Every asset here is cooked anyway (514 AnimSequences, all from containers), so the")
        print("  success path cannot be reached on DDS2 at all. Curfew (uncooked 5.7) is where it")
        print("  would run for real.")
    finally:
        # Rigs before the retargeter that references them; delete_asset refuses the other order.
        for path in reversed(made):
            r = SC.confirm_call("delete_asset", {"path": path})
            if not r.get("ok"):
                print("        cleanup: %s -> %s" % (path, (r.get("error") or "")[:140]))
        left = [a["path"] for a in (M.call("find_assets", {"pathPrefix": "/Game/_MifRT"})
                                    .get("assets") or [])
                if any(a["path"].startswith(m) for m in made)]
        check("T3203 (cleanup) what this run made is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
