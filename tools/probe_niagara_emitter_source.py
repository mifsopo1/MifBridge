"""Can this project supply a SOURCE UNiagaraEmitter that add_niagara_emitter will accept?

WHY THIS EXISTS. `set_niagara_emitter` tells its caller, as fact, that `set_property` on
`EmitterHandles[N].bIsEnabled` is enough to DISABLE an emitter but not to ENABLE one, because it
skips the RefreshFromExternalChanges and InvalidateCompileResults that endpoint does
(MifBridgeNiagara2.cpp:655). Nothing has ever compared the two - `audit_cross_endpoint_claims` found
it, and that tool exits 0 either way so its reading list had gone unread.

Testing that claim needs a NiagaraSystem with at least one emitter, and whether this project can
produce one was ASSUMED to be impossible ("DDS2's Niagara is cooked") before the source was read.
Reading it narrowed the question to one step:

  - Creating the system is FINE. create_asset special-cases UNiagaraSystem and runs
    UNiagaraSystemFactoryNew::InitializeSystem (MifBridgeUserTypes.cpp:1128), because a bare
    NewObject<UNiagaraSystem> crashes the editor - found live 2026-08-29.
  - duplicate_asset REFUSES a cooked Niagara asset (MifBridgeAssetOps.cpp:917). That is a crash
    guard, so the source cannot be obtained by duplicating one.
  - add_niagara_emitter requires `emitter`, a SOURCE UNiagaraEmitter to copy, and its own failure
    path is "the source emitter '%s' has no editor data". Cooked assets are exactly what lacks
    editor data.

So: does this project contain ANY UNiagaraEmitter with editor data? This answers that, and records
the actual response either way. A "no" is a real finding - it means the equivalence claim is out of
reach HERE and is ordinary work on an uncooked project - but only once measured, which is the whole
point of writing it down rather than asserting it.

Creates at most one scratch system under /Game/_MifNiagaraProbe and deletes it through
scratch_confirm. Saves nothing.

  python tools/probe_niagara_emitter_source.py
Exit: 0 answered (either way)   2 could not run   1 the probe itself broke
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mifaudit as M
import scratch_confirm as SC

ROOT = "/Game/_MifNiagaraProbe"


def main():
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("REFUSING TO RUN:", why)
        print("this probe needs a live editor - it asks the asset registry a question about THIS")
        print("project's content, which no amount of source reading can answer.")
        return 2
    print("target:", why, "\n")

    # ---------------------------------------------------------------- 1. is there a source at all?
    # `class`, not `classNames` - find_assets accepts class/className/type, pathPrefix,
    # nameContains, origin, recursiveClasses, limit, tags, includeTags (MifBridgeCooked.cpp).
    # Checked against the handler's own accept-list rather than guessed; the guess was wrong.
    found = M.call("find_assets", {"class": "NiagaraEmitter", "limit": 25})
    emitters = found.get("assets") or []
    print("UNiagaraEmitter assets in this project: %d" % len(emitters))
    if found.get("ok") is False:
        print("find_assets FAILED, so this proves nothing:", json.dumps(found)[:220])
        return 1
    if not emitters:
        print("\nANSWER: NO source emitter exists in this project at all.")
        print("The equivalence claim in set_niagara_emitter's whyNotSetProperty cannot be tested")
        print("here. That is a measured fact now, not an assumption - record it against the spec")
        print("item and do the work on an uncooked project.")
        return 0
    for a in emitters[:5]:
        print("   %s" % a.get("path"))

    # ---------------------------------------------------------------- 2. a system to add it to
    stamp = int(time.time() % 100000)
    sys_path = "%s/NS_Probe%d" % (ROOT, stamp)
    made = M.call("create_asset", {"path": sys_path, "class": "NiagaraSystem"})
    if made.get("ok") is False:
        print("\ncreate_asset(NiagaraSystem) refused:", json.dumps(made)[:300])
        print("That contradicts MifBridgeUserTypes.cpp:1128 - read it before believing this probe.")
        return 1
    print("\ncreated scratch system: %s" % sys_path)

    verdict = 1
    try:
        # ------------------------------------------------------------ 3. THE question
        src = emitters[0].get("path")
        add = M.call("add_niagara_emitter", {"path": sys_path, "emitter": src, "name": "ProbeEmitter"})
        print("\nadd_niagara_emitter(source=%s):" % src)
        print("   %s" % json.dumps(add)[:400])
        if add.get("ok") is False:
            err = str(add.get("error") or "")
            print("\nANSWER: NO - the source emitter was refused.")
            if "editor data" in err.lower():
                print("Refused for exactly the predicted reason: cooked assets have no editor data.")
            else:
                print("Refused for a DIFFERENT reason than predicted - read it, the prediction was")
                print("that cooked assets lack editor data and this says something else.")
            print("So the set_niagara_emitter / set_property equivalence claim is out of reach HERE.")
        else:
            print("\nANSWER: YES - a usable source emitter exists and the handle was added.")
            print("The equivalence claim IS testable in this project. Write the suite: flip")
            print("bIsEnabled through set_property, then through set_niagara_emitter, and compare")
            print("what the system actually reports - both directions, since the claim is that")
            print("disabling works and enabling does not.")
        verdict = 0
    finally:
        # The system is scratch and under /Game/_Mif*, so scratch_confirm accepts it. A refusal is
        # REPORTED rather than swallowed - a confirm:True retry through M.call cannot work, since
        # mifaudit strips confirm whatever its value.
        try:
            SC.confirm_call("delete_asset", {"path": sys_path})
            print("\ncleaned up %s" % sys_path)
        except Exception as exc:
            print("\nCLEANUP FAILED for %s: %s" % (sys_path, str(exc)[:160]))
            print("It is unsaved scratch, so closing the editor discards it - but say so rather")
            print("than leaving it silently.")
    return verdict


if __name__ == "__main__":
    sys.exit(main())
