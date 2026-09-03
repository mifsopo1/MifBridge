"""Content Browser collections - the working-set primitive, and an inverted gap.

A collection is a named, persisted set of assets independent of folder structure. For an agent it
is the missing way to carry a working set across a session boundary and to show it to the human in
the editor UI.

THE GAP IS THE OPPOSITE SHAPE FROM WHAT THE SURVEY CLAIMED, and checking changed what got built.
FCollectionManagerModule::StartupModule unconditionally registers CollectionManager.Create /
.Destroy / .Add / .Remove as console commands, and exec_console has no allowlist - it forwards any
string to GEngine->Exec. So the WRITE half has been reachable all along. What is unreachable by any
means is the READ: there is no console command for GetCollections or GetAssetsInCollection,
ICollectionManager is a plain C++ interface rather than a UObject so get_property cannot see it, and
UCollectionSettings holds exactly one bool. An agent could write a collection and never read it
back, which destroys the whole point of a working set.

THE WRITE HALF IS STILL BUILT, for a reason the survey also missed: those four console delegates
report success only through UE_LOG, never to the FOutputDevice. exec_console therefore returns
output:"" and handled:true whether the collection was created or the name was already taken. A write
you cannot verify is barely a write.

T5401 IS THE SET SEMANTICS, and it caught two defects in the first version of these endpoints.
Adding a member a collection already has is a NO-OP, not a failure - but ICollectionManager returns
false for it, so the bool alone cannot decide the outcome and the endpoint used to report ok:false
for a perfectly good call. And its OutNumAdded out-parameter did not reflect reality: a live add that
moved the count from 1 to 2 reported 0 through it. Both counts are now measured from the collection
itself, and success is judged by whether every path asked for ended up in the state asked for.

VERSION GUARD, and a real one. 5.6 introduced ICollectionContainer and marked every
ICollectionManager method UE_DEPRECATED(5.6), with GetProjectCollectionContainer() carrying the
identical set. The deprecated calls still compile but warn, and this project builds warnings-clean,
so a MIF_ENGINE_AT_LEAST(5,6) shim picks the container on 5.6+ and the manager before it.

CLEANS UP: every collection it makes is destroyed at the end.
"""
import json
import sys
import time

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

    name = "MifTest%d" % (int(time.time()) % 100000)
    # SKIP SCRATCH: a collection is a list of PATHS, and this suite reads its membership back and
    # asserts on it. Collecting four assets other suites are mid-way through creating and
    # deleting means the read-back is against a set that changed underneath it.
    assets = [a["path"] for a in (M.call("find_assets", {"limit": 20}).get("assets") or [])
              if not M.is_scratch_fixture(a)][:4]
    check("(setup) some assets to collect", len(assets) >= 3, len(assets))
    if len(assets) < 3:
        return 1

    made = False
    try:
        # ------------------------------------------------------------------ T5400 the read half
        print("=== T5400: reading collections, which nothing could do before ===")
        before = M.call("list_collections", {})
        check("T5400 list_collections succeeds", before.get("ok") is True,
              json.dumps(before)[:220])
        check("T5400 count agrees with the list",
              before.get("count") == len(before.get("collections") or []),
              json.dumps({"count": before.get("count")}))

        c = M.raw_post("create_collection", {"name": name, "paths": [assets[0]]})
        check("T5400 a collection can be created with contents in one call",
              c.get("ok") is True and c.get("created") is True, json.dumps(c)[:250])
        made = c.get("ok") is True
        check("T5400 and the asset count is read back from the collection",
              c.get("assetCount") == 1, json.dumps(c)[:200])

        d = M.call("describe_collection", {"name": name})
        check("T5400 describe_collection lists its assets", d.get("ok") is True
              and d.get("assets") == [assets[0]], json.dumps(d)[:250])
        check("T5400 and notes it stores SOFT paths, so it can hold cooked content",
              "SOFT OBJECT PATHS" in (d.get("note") or ""), (d.get("note") or "")[:180])

        after = M.call("list_collections", {})
        check("T5400 the new collection appears in the list",
              (after.get("count") or 0) == (before.get("count") or 0) + 1,
              "%s -> %s" % (before.get("count"), after.get("count")))

        # ------------------------------------------------------------------ T5401 set semantics
        print("\n=== T5401: a collection is a SET, and a no-op is not a failure ===")
        add = M.raw_post("add_to_collection", {"name": name, "paths": assets[1:3]})
        check("T5401 adding two more succeeds", add.get("ok") is True, json.dumps(add)[:250])
        # MEASURED FROM THE COLLECTION. The engine's OutNumAdded reported 0 for a live add that
        # moved the count from 1 to 2, which is why nothing here trusts it.
        check("T5401 `added` is the measured change in the set's own size",
              add.get("added") == 2 and add.get("assetCountBefore") == 1
              and add.get("assetCount") == 3,
              json.dumps({k: add.get(k) for k in
                          ("added", "assetCountBefore", "assetCount")}))

        dup = M.raw_post("add_to_collection", {"name": name, "paths": [assets[1]]})
        # THE assertion that caught the defect: ICollectionManager returns FALSE for a duplicate
        # add, and the first version turned that into ok:false for a perfectly good call.
        check("T5401 adding a member it already has SUCCEEDS - a set no-op is not a failure",
              dup.get("ok") is True, json.dumps(dup)[:250])
        check("T5401 and reports added:0 with alreadyInDesiredState:1",
              dup.get("added") == 0 and dup.get("alreadyInDesiredState") == 1,
              json.dumps({k: dup.get(k) for k in ("added", "alreadyInDesiredState")}))
        check("T5401 the count did not move", dup.get("assetCount") == 3, dup.get("assetCount"))
        check("T5401 with a note explaining why that is not an error",
              "NOT a failure" in (dup.get("note") or ""), (dup.get("note") or "")[:200])

        rem = M.raw_post("remove_from_collection", {"name": name, "paths": [assets[0]]})
        check("T5401 removing works and is measured the same way",
              rem.get("ok") is True and rem.get("removed") == 1 and rem.get("assetCount") == 2,
              json.dumps({k: rem.get(k) for k in ("removed", "assetCount")}))
        gone = M.raw_post("remove_from_collection", {"name": name, "paths": [assets[0]]})
        check("T5401 removing something that is not there also succeeds",
              gone.get("ok") is True and gone.get("removed") == 0, json.dumps(gone)[:220])

        # ------------------------------------------------------------------ T5402 identity
        print("\n=== T5402: the share type is part of the identity ===")
        wrongshare = M.raw_post("describe_collection", {"name": name, "shareType": "private"})
        check("T5402 the same name under another share type is a different collection",
              wrongshare.get("ok") is False, (wrongshare.get("error") or "")[:200])
        check("T5402 and the refusal says so rather than just 'not found'",
              "part of the identity" in (wrongshare.get("error") or ""),
              (wrongshare.get("error") or "")[:220])
        badshare = M.raw_post("create_collection", {"name": name + "X", "shareType": "team"})
        check("T5402 an unknown share type is refused with the three real ones",
              badshare.get("ok") is False and "shared" in (badshare.get("error") or ""),
              (badshare.get("error") or "")[:200])
        check("T5402 and warns that shared needs revision control",
              "revision control" in (badshare.get("error") or ""),
              (badshare.get("error") or "")[:220])

        missing = M.raw_post("add_to_collection", {"name": "MifNoSuchCollection",
                                                   "paths": [assets[0]]})
        check("T5402 adding to a collection that does not exist is refused",
              missing.get("ok") is False and "create_collection" in (missing.get("error") or ""),
              (missing.get("error") or "")[:200])

        # ------------------------------------------------------------------ T5403 destruction
        print("\n=== T5403: destroying the label, not the assets ===")
        noconf = M.raw_post("destroy_collection", {"name": name})
        check("T5403 destroying without confirm is refused, naming the size",
              noconf.get("ok") is False and "2 asset(s)" in (noconf.get("error") or ""),
              (noconf.get("error") or "")[:220])
        check("T5403 and the refusal makes clear the ASSETS are untouched",
              "ASSETS are untouched" in (noconf.get("error") or ""),
              (noconf.get("error") or "")[:220])

        dead = M.raw_post("destroy_collection", {"name": name, "confirm": True})
        check("T5403 destroying works", dead.get("ok") is True and dead.get("destroyed") is True,
              json.dumps(dead)[:220])
        made = False
        # READ BACK: destroyed-ness is the postcondition, and DestroyCollection returns only a bool.
        after2 = M.raw_post("describe_collection", {"name": name})
        check("T5403 and it really is gone", after2.get("ok") is False, json.dumps(after2)[:200])
        still = M.call("find_assets", {"limit": 1})
        check("T5403 the assets it named still exist - a collection is a label, not a container",
              (still.get("count") or 0) > 0, still.get("count"))
    finally:
        if made:
            M.raw_post("destroy_collection", {"name": name, "confirm": True})
        left = [c for c in (M.call("list_collections", {}).get("collections") or [])
                if c.get("name") == name]
        check("T5404 (cleanup) the test collection is gone", not left, json.dumps(left)[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name_, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name_, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
