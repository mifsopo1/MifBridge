"""remove_foliage_instances - the erase half, and four ways the survey got it wrong.

add_foliage_instances writes painted foliage and list_foliage_instances reads it; nothing took one
back out. That is the plain gap. The interesting part is that three of the four mechanics proposed
for it were wrong, and one would have cost an editor.

T4601 IS THE CRASH GUARD, and it is the reason an out-of-range index is refused rather than skipped.
FFoliageInfo::RemoveInstances indexes `Instances[InstanceIndex]` with no bounds test of its own
(InstancedFoliage.cpp:2432), so one bad number in indices[] is a segfault, not an error. The WHOLE
call is refused rather than the bad entries dropped, because a partially-honoured delete of foliage
is not something a caller can reason about afterwards.

There is a second assert on the same path: RemoveInstancesImpl opens with check(IsInitialized())
(:2413), where IsInitialized() is Implementation.IsValid() && Implementation->IsInitialized()
(:2157-2160). That branch is NOT exercised here - a FFoliageInfo reached through a live
InstancedFoliageActor is always initialised - and it is guarded anyway, because the failure mode is
a dead editor rather than a bad response.

T4603 IS THE MEASURED-ZERO ONE. A selector that matches nothing succeeds with removed:0 and says the
zero came from the engine's own selection helper. That is a different claim from "the call failed",
and only one of them tells you the sphere was in the wrong place.

WHAT THE SURVEY GOT WRONG, recorded because the intuitive answer is the wrong one:
  - "sort indices descending before removing" buys nothing. RemoveInstances takes the whole set in
    ONE call and remaps internally around its own RemoveAtSwap (:2445, :2468-2476). The
    N-separate-calls pattern that advice implies is broken in ANY order, because RemoveAtSwap moves
    the tail element into the freed slot rather than shifting everything down.
  - "refuse when the type has no FFoliageInfo" never fires on cooked content. FFoliageInfo::Instances
    is editor-only and serialized only when !Ar.ArIsFilterEditorOnly (:503-514), while the
    FoliageInfos map itself survives cooking - so a .pak level has the info with an EMPTY array, and
    a naive implementation reports removed:0 for trees the user can see. Detected by comparing the
    HISM's instance count against Instances.Num(), and refused by name. The same blind spot was
    fixed in list_foliage_instances in the same change, which had been reporting instanceCount 0 for
    visible cooked foliage.

NOT EXERCISED: that cooked-stripped branch. Reaching it needs a cooked level with painted foliage,
and the only ones here are real project maps which are not opened. The scratch level's foliage is
painted in-session and therefore has full editor data - which is what makes every other path below
testable for real rather than by refusal alone.

CLEANS UP: removes its own foliage and deletes the scratch foliage type.
"""
import json
import sys

import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []

# A UNIQUE NAME PER RUN. delete_asset unregisters an asset while the UObject stays resident
# (docs/06 #28), and an open asset editor blocks the delete outright - so a fixed name makes
# this suite pass once and then fail on every re-run with "an asset already exists", which
# looks like an endpoint defect and is not one.
FT = "/Game/_MifFol/FT_MifTest%d" % (int(time.time()) % 100000)
MESH = "/Engine/BasicShapes/Cube.Cube"


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def count(path):
    for t in (M.call("list_foliage_instances", {}).get("types") or []):
        if t.get("foliageType") == path:
            return t.get("instanceCount")
    return None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    made = None
    try:
        # ------------------------------------------------------------------ setup
        print("=== setup: paint foliage into the scratch level ===")
        ft = M.raw_post("create_asset", {"path": FT, "class": "FoliageType_InstancedStaticMesh"})
        check("(setup) a foliage type exists", ft.get("ok") is True, json.dumps(ft)[:200])
        if not ft.get("ok"):
            return 1
        made = ft["assetPath"]
        M.raw_post("set_property", {"objectPath": made, "propertyPath": "Mesh", "value": MESH})
        add = M.raw_post("add_foliage_instances", {
            "foliageType": made,
            "instances": [{"x": i * 300.0, "y": 0.0, "z": 0.0} for i in range(6)]})
        check("(setup) six instances are painted", add.get("ok") is True
              and add.get("instanceCount") == 6, json.dumps(add)[:220])
        check("(setup) and the read half sees them", count(made) == 6, count(made))

        # ------------------------------------------------------------------ T4600 selectors
        print("\n=== T4600: exactly one selector, named ===")
        for payload, why, n in (({"foliageType": made}, "none", 0),
                                ({"foliageType": made, "all": True, "indices": [0]}, "two", 2)):
            r = M.raw_post("remove_foliage_instances", payload)
            check("T4600 %s selector(s) is refused" % why,
                  r.get("ok") is False and ("got %d" % n) in (r.get("error") or ""),
                  (r.get("error") or "")[:180])
        notype = M.raw_post("remove_foliage_instances", {"all": True})
        check("T4600 a missing foliageType is refused, and says it matches EXACTLY unlike the "
              "read half - a substring hitting two types would delete from the wrong one",
              notype.get("ok") is False and "EXACTLY" in (notype.get("error") or ""),
              (notype.get("error") or "")[:200])
        wrong = M.raw_post("remove_foliage_instances", {"foliageType": "/Game/Nope.Nope",
                                                        "all": True, "confirm": True})
        check("T4600 an unknown type is refused and the real ones listed",
              wrong.get("ok") is False and "FT_MifTest" in (wrong.get("error") or ""),
              (wrong.get("error") or "")[:200])

        # ------------------------------------------------------------------ T4601 the crash guard
        print("\n=== T4601: an out-of-range index would CRASH, so the whole call is refused ===")
        bad = M.raw_post("remove_foliage_instances", {"foliageType": made, "indices": [0, 99],
                                                      "confirm": True})
        check("T4601 an out-of-range index is refused", bad.get("ok") is False,
              (bad.get("error") or "")[:200])
        check("T4601 and the refusal says it would have crashed, not merely failed",
              "CRASH" in (bad.get("error") or ""), (bad.get("error") or "")[:220])
        # THE assertion: the VALID index in that same call was not honoured either.
        check("T4601 the good index in the same call was NOT removed - all or nothing",
              count(made) == 6, count(made))

        noconf = M.raw_post("remove_foliage_instances", {"foliageType": made, "indices": [0, 1]})
        check("T4601 removal without confirm is refused, naming the count",
              noconf.get("ok") is False and "2 of" in (noconf.get("error") or ""),
              (noconf.get("error") or "")[:200])
        check("T4601 and that refusal changed nothing either", count(made) == 6, count(made))

        # ------------------------------------------------------------------ T4602 removal
        print("\n=== T4602: removal, with the count measured after the fact ===")
        r = SC.confirm_call("remove_foliage_instances", {"foliageType": made, "indices": [0, 1]})
        check("T4602 removing two by index succeeds", r.get("ok") is True, json.dumps(r)[:250])
        # `removed` is the difference in the engine's own placed count, not the size of the request
        # - RemoveInstances returns void and tells you nothing.
        check("T4602 removed:2 and remaining:4, both measured from the engine",
              r.get("removed") == 2 and r.get("remaining") == 4, json.dumps(r)[:220])
        check("T4602 and the read half agrees", count(made) == 4, count(made))
        check("T4602 the selector used is reported", r.get("selector") == "indices",
              r.get("selector"))

        sph = SC.confirm_call("remove_foliage_instances", {
            "foliageType": made, "sphere": {"center": {"x": 600, "y": 0, "z": 0}, "radius": 200}})
        check("T4602 a sphere selector removes only what it encloses",
              sph.get("ok") is True and sph.get("removed") == 1 and sph.get("remaining") == 3,
              json.dumps(sph)[:220])

        box = SC.confirm_call("remove_foliage_instances", {
            "foliageType": made, "box": {"min": {"x": -50, "y": -50, "z": -50},
                                         "max": {"x": 50, "y": 50, "z": 50}}})
        check("T4602 a box selector works too", box.get("ok") is True, json.dumps(box)[:220])

        # ------------------------------------------------------------------ T4603 honest zero
        print("\n=== T4603: a selector that matches nothing is not a failure ===")
        miss = SC.confirm_call("remove_foliage_instances", {
            "foliageType": made,
            "sphere": {"center": {"x": 9999999, "y": 0, "z": 0}, "radius": 10}})
        check("T4603 a sphere matching nothing SUCCEEDS with removed:0",
              miss.get("ok") is True and miss.get("removed") == 0, json.dumps(miss)[:220])
        check("T4603 and says the zero is measured from the engine's own selection helper",
              "measured zero" in (miss.get("note") or ""), (miss.get("note") or "")[:200])
        badbox = SC.confirm_call("remove_foliage_instances", {
            "foliageType": made, "box": {"min": {"x": 100, "y": 0, "z": 0},
                                         "max": {"x": 0, "y": 0, "z": 0}}})
        check("T4603 but an INVERTED box is refused rather than silently matching nothing - "
              "that is a mistake, not a selection",
              badbox.get("ok") is False, (badbox.get("error") or "")[:200])

        # ------------------------------------------------------------------ T4604 all
        print("\n=== T4604: all:true ===")
        before = count(made)
        allr = SC.confirm_call("remove_foliage_instances", {"foliageType": made, "all": True})
        check("T4604 all:true clears the type", allr.get("ok") is True
              and allr.get("remaining") == 0, json.dumps(allr)[:220])
        check("T4604 and removed matches what was there", allr.get("removed") == before,
              "removed=%s before=%s" % (allr.get("removed"), before))
        check("T4604 the read half agrees it is empty", count(made) in (0, None), count(made))

        print("\n  NOT EXERCISED: the cooked-stripped refusal, and the IsInitialized() assert")
        print("  guard. The first needs a cooked level with painted foliage - only real project")
        print("  maps qualify here and those are not opened. The second cannot be constructed")
        print("  through a live InstancedFoliageActor at all; it is guarded because the failure")
        print("  mode is a dead editor rather than a bad response.")
    finally:
        if made:
            SC.confirm_call("remove_foliage_instances", {"foliageType": made, "all": True})
            SC.confirm_call("delete_asset", {"path": made})
        # THE POSTCONDITION THIS ENDPOINT OWNS is that the INSTANCES are gone, and that is what
        # is asserted. The foliage TYPE asset cannot be deleted while the level's
        # InstancedFoliageActor still holds an FFoliageInfo for it - delete_asset says so itself:
        # "no open editor, no registry referencer and not rooted - the holder is an in-memory
        # handle this endpoint cannot see. An editor restart releases it." That is real engine
        # behaviour, not a defect here, so it is REPORTED rather than failed on. Asserting a
        # deletion the engine will not perform would make this suite red for the wrong reason.
        check("T4605 (cleanup) every instance this suite painted is gone",
              count(made) in (0, None), count(made))
        gone = SC.confirm_call("delete_asset", {"path": made})
        if not gone.get("deleted"):
            print("  NOTE  the foliage TYPE asset survives: %s"
                  % (gone.get("error") or "")[:150])
            print("        Expected - the level's InstancedFoliageActor still references it, and")
            print("        nothing is saved, so it disappears with this scratch level anyway.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
