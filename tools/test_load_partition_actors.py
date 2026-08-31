"""load_partition_actors - the write half of list_partition_actors, and a void call read back.

T2600 IS THE ONE THAT JUSTIFIES THE SHAPE. PinActors returns void, and the engine body is:

    void UWorldPartition::PinActors(const TArray<FGuid>& ActorGuids)
    { if (PinnedActors) { PinnedActors->AddActors(ActorGuids); } }

When PinnedActors is null it does NOTHING - no log, no return value, nothing that distinguishes it
from success. So the endpoint reads the result back through IsActorPinned(), which answers "the pin
took" separately from "the actor happens to be in memory". Those are different questions: an actor
already loaded for another reason would satisfy an IsLoaded() check while the pin silently did
nothing. This suite asserts the state MOVED, then asserts that pinning the same actor again reports
changed:false - a guard that only ever reports success would pass a test that only checks success.

WHAT IS NOT PROVEN HERE, and it is stated rather than implied. The bounds mode loads a region
through LoadLastLoadedRegions. Every check below about it passes - it accepts a box, refuses an
empty one, counts from the descriptors rather than from the call, and declares itself irreversible -
but no actor was observed being LOADED by it, because every unloaded descriptor in this project's
map is a WorldPartitionHLOD and the region adapter does not pick those up. Pinning loads them fine,
which is how we know it is not "HLOD cannot load". The endpoint reported newlyLoaded:0 truthfully
rather than claiming success, which is the behaviour under test; that a bounds load moves an
ordinary actor needs a map with ordinary unloaded actors.

A BOUNDS LOAD IS ONE-WAY and this suite therefore runs it only against the smallest box it can, on
the understanding that the editor world here is a scratch /Temp level discarded at restart.
LoadLastLoadedRegions leaves a persistent user-created loader adapter with no handle returned, so
nothing here or in the endpoint can remove it.
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

    listing = M.call("list_partition_actors", {"limit": 400})
    if listing.get("partitioned") is not True:
        print("SKIPPED - the open level is not World Partitioned, so there are no descriptors to")
        print("  pin and NOTHING was verified. Open a partitioned map to run this.")
        return 2

    rows = listing.get("actors") or []
    unloaded = [a for a in rows if a.get("loaded") is False and a.get("guid")]
    print("descriptors: %s   unloaded: %d" % (listing.get("scanned"), len(unloaded)))
    if not unloaded:
        # A PRECONDITION, NOT A PASS. With everything already in memory there is no state to move,
        # and every assertion below would pass without proving anything.
        print("\nSKIPPED - every descriptor in this map is already loaded, so pinning cannot")
        print("  change anything and NOTHING would be verified. Exit 2 means skipped.")
        return 2

    target = unloaded[0]
    guid = target["guid"]
    print("target: %s (%s)" % (target.get("label"), target.get("class")))

    try:
        # ------------------------------------------------------------------ T2600 the void call
        print("\n=== T2600: a void engine call, judged by reading the state back ===")
        pin = M.raw_post("load_partition_actors", {"guids": [guid]})
        check("T2600 pinning succeeds and reports the mode it took",
              pin.get("ok") is True and pin.get("mode") == "pin", json.dumps(pin)[:250])
        # THE assertion. PinActors reports nothing; IsActorPinned is what says it took.
        check("T2600 the pinned state actually MOVED - read back through IsActorPinned, since "
              "PinActors returns void and does nothing at all when the partition has no pinned "
              "container",
              pin.get("changed") is True and guid in (pin.get("stateChanged") or []),
              json.dumps(pin)[:280])
        check("T2600 and the actor is now in memory, reported by its actorSoftPath - the handle "
              "every other endpoint takes",
              bool(pin.get("nowLoaded")), json.dumps(pin.get("nowLoaded"))[:220])
        again = M.raw_post("load_partition_actors", {"guids": [guid]})
        # A guard that only ever reports success passes a test that only checks success.
        check("T2600 pinning it AGAIN reports changed:false rather than claiming a second success",
              again.get("ok") is True and again.get("changed") is False
              and again.get("pinnedNow") == 1, json.dumps(again)[:250])
        # WHICH ones did not move, not just THAT nothing did. `changed` is one bool for the whole
        # call, so on a multi-guid request it says nothing about any individual actor - and this
        # endpoint is built to take many. stateUnchanged is the per-actor half of the same answer
        # and nothing read it until now. The two must agree: an empty stateChanged and a guid in
        # stateUnchanged is what changed:false MEANS, and a response where they disagree is one
        # where a caller cannot tell which of its actors moved.
        check("T2600 and stateUnchanged names the actor that did not move",
              guid in (again.get("stateUnchanged") or []),
              "stateUnchanged=%s stateChanged=%s"
              % (again.get("stateUnchanged"), again.get("stateChanged")))
        check("T2600 and the per-actor arrays agree with the single `changed` bool",
              bool(again.get("stateChanged")) == again.get("changed"),
              "changed=%r but stateChanged=%s - the summary and the detail disagree about one call"
              % (again.get("changed"), again.get("stateChanged")))
        check("T2600 and every requested guid is accounted for in exactly one of them",
              sorted((again.get("stateChanged") or []) + (again.get("stateUnchanged") or []))
              == [guid],
              "requested [%s], got changed=%s unchanged=%s"
              % (guid, again.get("stateChanged"), again.get("stateUnchanged")))

        # ------------------------------------------------------------------ T2601 reversible
        print("\n=== T2601: unpin, because a load with no release is a one-way door ===")
        un = M.raw_post("load_partition_actors", {"guids": [guid], "unpin": True})
        check("T2601 unpinning succeeds and the state moves back",
              un.get("ok") is True and un.get("mode") == "unpin"
              and un.get("changed") is True and un.get("pinnedNow") == 0, json.dumps(un)[:250])
        readback = M.call("list_partition_actors", {"limit": 400})
        still = [a for a in (readback.get("actors") or []) if a.get("guid") == guid]
        check("T2601 and list_partition_actors - a DIFFERENT endpoint - still describes the actor",
              bool(still), "the descriptor vanished after unpinning")

        # ------------------------------------------------------------------ T2602 the guards
        print("\n=== T2602: the refusals ===")
        ghost = M.raw_post("load_partition_actors",
                           {"guids": ["DEADBEEF00004444DEADBEEF00004444"]})
        check("T2602 a guid matching no descriptor is refused, saying guids are per-MAP",
              ghost.get("ok") is False and "per-MAP" in (ghost.get("error") or ""),
              (ghost.get("error") or "")[:220])
        bad = M.raw_post("load_partition_actors", {"guids": ["not-a-guid"]})
        check("T2602 a malformed guid is refused outright rather than silently skipped",
              bad.get("ok") is False and "not guids at all" in (bad.get("error") or ""),
              (bad.get("error") or "")[:220])
        both = M.raw_post("load_partition_actors",
                          {"guids": [guid],
                           "bounds": {"min": {"x": 0, "y": 0, "z": 0},
                                      "max": {"x": 1, "y": 1, "z": 1}}})
        check("T2602 guids AND bounds together is refused - they have different lifetimes, and "
              "one is reversible while the other is not",
              both.get("ok") is False and "different lifetimes" in (both.get("error") or ""),
              (both.get("error") or "")[:220])
        neither = M.raw_post("load_partition_actors", {})
        check("T2602 neither is refused rather than treated as a successful no-op",
              neither.get("ok") is False, (neither.get("error") or "")[:200])
        empty = M.raw_post("load_partition_actors",
                           {"bounds": {"min": {"x": 0, "y": 0, "z": 0},
                                       "max": {"x": 0, "y": 0, "z": 0}}})
        check("T2602 a zero-volume box is refused rather than loading nothing and reporting success",
              empty.get("ok") is False and "no volume" in (empty.get("error") or ""),
              (empty.get("error") or "")[:200])
        unknown = M.raw_post("load_partition_actors", {"guids": [guid], "actorPath": "/x"})
        check("T2602 actorPath is refused BY NAME, pointing out that an unloaded actor has no path "
              "yet - which is the whole reason this endpoint takes guids",
              unknown.get("ok") is False and "no path yet" in (unknown.get("error") or ""),
              (unknown.get("error") or "")[:220])

        # ------------------------------------------------------------------ T2603 bounds
        print("\n=== T2603: the bounds mode, and what it does NOT prove here ===")
        b = target.get("bounds") or {}
        if b.get("min") and b.get("max"):
            reg = M.raw_post("load_partition_actors", {"bounds": {"min": b["min"], "max": b["max"]}})
            check("T2603 a bounds load succeeds and counts from the DESCRIPTORS, since "
                  "LoadLastLoadedRegions returns void and reports nothing about what it loaded",
                  reg.get("ok") is True and reg.get("mode") == "bounds"
                  and isinstance(reg.get("loadedBefore"), (int, float))
                  and isinstance(reg.get("loadedAfter"), (int, float)), json.dumps(reg)[:280])
            check("T2603 and it declares itself IRREVERSIBLE, because it leaves a persistent "
                  "user-created loader adapter with no handle to remove it",
                  reg.get("reversible") is False and "ONE-WAY" in (reg.get("note") or ""),
                  (reg.get("note") or "")[:220])
            # REPORTED, NOT ASSERTED. Every unloaded descriptor in this map is a WorldPartitionHLOD
            # and the region adapter does not pick those up - pinning loads them fine, so this is
            # not "HLOD cannot load". newlyLoaded:0 is the endpoint being truthful, not failing.
            print("  NOTE  newlyLoaded=%s. This map's only unloaded descriptors are"
                  % reg.get("newlyLoaded"))
            print("        WorldPartitionHLOD, which the region adapter does not load, so that a")
            print("        bounds load MOVES an ordinary actor is UNPROVEN here and reported")
            print("        rather than passed. The endpoint reported 0 truthfully.")
        else:
            print("  NOTE  the target descriptor has no bounds, so the bounds mode is unexercised.")

        # ------------------------------------------------------------------ T2604 the read filter
        print("\n=== T2604: the READ half's bounds filter, and the actors matching ANY box ===")
        flat = M.call("list_partition_actors", {"limit": 400})
        check("T2604 an unfiltered listing reports boundsFiltered:false",
              flat.get("boundsFiltered") is False, json.dumps(flat)[:200])

        tb = target.get("bounds") or {}
        if tb.get("min") and tb.get("max"):
            near = M.call("list_partition_actors",
                          {"limit": 400, "bounds": {"min": tb["min"], "max": tb["max"]}})
            check("T2604 a bounds query narrows the result and says it filtered",
                  near.get("boundsFiltered") is True
                  and (near.get("matched") or 0) < (flat.get("matched") or 0),
                  "flat %s -> bounded %s" % (flat.get("matched"), near.get("matched")))
            labels = [a.get("label") for a in (near.get("actors") or [])]
            check("T2604 and the actor whose own bounds were used comes back in the region",
                  target.get("label") in labels, json.dumps(labels[:6]))

        far = M.call("list_partition_actors",
                     {"limit": 400,
                      "bounds": {"min": {"x": 9.0e7, "y": 9.0e7, "z": 9.0e7},
                                 "max": {"x": 9.1e7, "y": 9.1e7, "z": 9.1e7}}})
        # THE assertion that stops a correct answer being misread. A box far outside the world still
        # returns the DirectionalLight, because the engine gives an actor with no spatial extent
        # bounds of +/-2^42 and it genuinely intersects everything. Right, and misleading unless it
        # is called out - someone would read it as a broken filter, or as the light being local.
        if (far.get("matched") or 0) > 0:
            check("T2604 a box far outside the world still matches globally-bounded actors, and "
                  "every one of them is NAMED in matchedAnyBox rather than passed off as being "
                  "in the region",
                  bool(far.get("matchedAnyBox"))
                  and far.get("matched") == len(far.get("matchedAnyBox") or []),
                  json.dumps({"matched": far.get("matched"),
                              "matchedAnyBox": far.get("matchedAnyBox")})[:250])
            check("T2604 and the note explains WHY they match everything",
                  "no meaningful spatial extent" in (far.get("boundsNote") or ""),
                  (far.get("boundsNote") or "")[:220])
        else:
            print("  NOTE  no globally-bounded actor in this map, so the matchedAnyBox arm is")
            print("        unexercised here. Reported rather than passed.")

        zero = M.raw_post("list_partition_actors",
                          {"bounds": {"min": {"x": 0, "y": 0, "z": 0},
                                      "max": {"x": 0, "y": 0, "z": 0}}})
        check("T2604 a zero-volume box is refused on the read half too - 'no actors here' would "
              "be a wrong answer rather than an empty one",
              zero.get("ok") is False and "no volume" in (zero.get("error") or ""),
              (zero.get("error") or "")[:220])

        check("T2603 - the editor is still alive",
              M.call("self_audit", {"summaryOnly": True}).get("ok") is True,
              "pinning touches World Partition editor state")
    finally:
        # Leave nothing pinned that this suite pinned. The bounds adapter cannot be removed by
        # anything here, which is why the note above says so out loud.
        M.raw_post("load_partition_actors", {"guids": [guid], "unpin": True})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
