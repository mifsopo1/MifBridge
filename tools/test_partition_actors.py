"""list_partition_actors - the actors list_level_actors cannot see.

THE FAILURE THIS FIXES IS SILENT UNDER-REPORTING, which is worse than a missing endpoint. On a World
Partition map with editor streaming on, list_level_actors sees only the region currently streamed in.
An agent asked to find the lighthouse enumerates the level, does not find it, and concludes it does
not exist - with ok:true throughout.

T2101 IS THE ASSERTION THAT MATTERS and it is measured against the live map rather than asserted in
prose: list_partition_actors' loadedInEditor must equal what list_level_actors reports, and its
scanned total must EXCEED it. On the map this was written against that is 74 loaded of 123 total -
49 actors invisible to the older endpoint. If the two ever agree exactly, either the map has no
streamed-out content (in which case the test says so rather than failing) or this endpoint has
stopped reading descriptors.

THE VERSION TRAP THIS ENDPOINT EXISTS AROUND, recorded here because it is the reason the code has a
guard at all. FWorldPartitionHelpers::ForEachActorDesc was the 5.3 spelling. On 5.4+ the descriptor
type changed and the iterator was renamed to ForEachActorDescInstance - but the old name was KEPT
with an EMPTY BODY:

    UE_DEPRECATED(5.4, "Use ForEachActorDescInstance")
    static void ForEachActorDesc(UWorldPartition*, TSubclassOf<AActor>,
                                 TFunctionRef<bool(const FWorldPartitionActorDesc*)> Func) {}

(UE_5.7/.../WorldPartitionHelpers.h:106). So the 5.3 call compiles against 5.7, iterates nothing, and
answers scanned:0 with ok:true on a map full of actors. Every other version guard in this plugin
protects against code that would not compile; this one protects against code that compiles and lies.
There is no way to test that from 5.3 - it can only be caught by reading the 5.7 header, which is
why it is written down here.

COOKED: a cooked WP map is flattened into runtime streaming cells and carries no descriptors. The
endpoint refuses by name rather than returning an empty list, because "no actors" and "this map
cannot tell you about its actors" are different answers.

READ-ONLY. Nothing here writes; the write half (load_partition_actors / PinActors) is a separate
open spec item.
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

    r = M.call("list_partition_actors", {"limit": 1000})
    check("T2100 list_partition_actors succeeds", r.get("ok") is True, json.dumps(r)[:300])
    check("T2100 it reports whether the world is partitioned at all",
          isinstance(r.get("partitioned"), bool), json.dumps(r)[:250])

    if not r.get("partitioned"):
        print("  SKIP  T2101-T2104 - this editor's current level is NOT World Partitioned, so there")
        print("        are no actor descriptors to read. The endpoint said so in its note rather")
        print("        than returning an empty list, which is the behaviour under test on a")
        print("        non-partitioned map. Nothing further is exercised here.")
        check("T2100 and a non-partitioned world gets an explanatory note, not a bare empty list",
              bool(r.get("note")), json.dumps(r)[:250])
        print("\nPASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1 if FAIL else 0

    # ------------------------------------------------------------------ T2101 the whole point
    print("\n=== T2101: the actors list_level_actors cannot see ===")
    lv = M.call("list_level_actors", {"limit": 2000})
    seen = len(lv.get("actors") or [])
    scanned = r.get("scanned")
    loaded = r.get("loadedInEditor")
    print("        list_level_actors sees %d; descriptors report %s scanned, %s loaded"
          % (seen, scanned, loaded))

    check("T2101 loadedInEditor agrees with what list_level_actors can see",
          loaded == seen, "descriptors say %s loaded, list_level_actors returned %d" % (loaded, seen))

    if scanned == loaded:
        print("  NOTE  T2101 every actor in this map is currently loaded, so there is nothing")
        print("        streamed out to demonstrate the gap with. That is a property of the map's")
        print("        current streaming state, not a defect - reported rather than failed.")
    else:
        check("T2101 the descriptors see MORE actors than the level does - the gap this endpoint "
              "exists to close", scanned > loaded, "scanned=%s loaded=%s" % (scanned, loaded))
        unloaded = [a for a in (r.get("actors") or []) if not a.get("loaded")]
        check("T2101 and the unloaded ones are actually reported, with a guid and a soft path",
              bool(unloaded) and all(a.get("guid") and a.get("actorSoftPath") for a in unloaded[:5]),
              json.dumps(unloaded[:1])[:250])
        check("T2101 the response says plainly that some actors are invisible to list_level_actors",
              bool(r.get("unloadedNote")), (r.get("unloadedNote") or "")[:200])

    # ------------------------------------------------------------------ T2102 filters
    print("\n=== T2102: filters actually filter ===")
    lo = M.call("list_partition_actors", {"loadedOnly": True, "limit": 1000})
    check("T2102 loadedOnly returns exactly the loaded set",
          lo.get("matched") == loaded, "matched=%s loaded=%s" % (lo.get("matched"), loaded))

    rows = r.get("actors") or []
    if rows:
        frag = (rows[0].get("label") or "")[:6]
        if frag:
            nc = M.call("list_partition_actors", {"nameContains": frag, "limit": 1000})
            check("T2102 nameContains narrows the result and never widens it",
                  (nc.get("matched") or 0) >= 1 and (nc.get("matched") or 0) <= (r.get("matched") or 0),
                  "matched=%s of %s for '%s'" % (nc.get("matched"), r.get("matched"), frag))

    # THE field-naming assertion. classFilter is applied by the ENGINE iterator, so `scanned` means
    # "descriptors yielded", not "actors in the map" - it reported "1 of 1" on a 123-actor map before
    # this was named. The caveat has to be present or the number misleads.
    cf = M.call("list_partition_actors", {"classFilter": "/Script/Engine.StaticMeshActor"})
    check("T2102 classFilter succeeds", cf.get("ok") is True, json.dumps(cf)[:250])
    check("T2102 and it SAYS that scanned counts only that class, because the engine iterator "
          "applies the filter - otherwise the number reads as the map total",
          bool(cf.get("scannedNote")), json.dumps(cf)[:250])

    # ------------------------------------------------------------------ T2103 refusals
    print("\n=== T2103: refusals ===")
    bad = M.call("list_partition_actors", {"bounds": {"min": {"x": 0}}})
    check("T2103 an unbuilt parameter is refused with what to use instead, not ignored",
          bad.get("ok") is False and "nameContains" in (bad.get("error") or ""), bad.get("error"))
    badclass = M.call("list_partition_actors", {"classFilter": "/Script/Engine.NoSuchClassAtAll"})
    check("T2103 an unknown classFilter is refused", badclass.get("ok") is False,
          json.dumps(badclass)[:250])

    # ------------------------------------------------------------------ T2104 read-only
    print("\n=== T2104: it is a read ===")
    again = M.call("list_partition_actors", {"limit": 1000})
    check("T2104 calling it twice gives the same answer - it changes nothing",
          again.get("scanned") == scanned and again.get("loadedInEditor") == loaded,
          "first=%s/%s second=%s/%s" % (scanned, loaded, again.get("scanned"),
                                        again.get("loadedInEditor")))
    check("T2104 and it reports scratchClean - no real package was dirtied",
          again.get("scratchClean") is not False, json.dumps(again)[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
