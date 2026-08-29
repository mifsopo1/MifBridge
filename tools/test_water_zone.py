"""create_water_zone - the endpoint create_water_body had been advising people to call.

WHY THIS EXISTS. create_water_body's parameter help said, in as many words, "create the zone
separately with create_water_zone". No such endpoint existed. tools/audit_message_endpoints.py found
it by asking mechanically whether every endpoint named in a user-facing message is real.

It was not a typo. Since UE 5.1 a water body that overlaps NO AWaterZone does not render at all, so
the write half of the water family could author water that could never be seen - and its own response
note said so, while offering nothing that could fix it. The advice had nowhere to send anyone.

WHAT THIS COVERS, and the one test that matters is T735/T736: the coverage report. Creating a zone is
not the point; making bodies visible is. So the endpoint asks every AWaterBody afterwards whether it
now belongs to this zone, and these two tests prove that number is observed rather than assumed - one
where the zone covers the body, one where it does not and the body is NAMED as still invisible.

SKIPS CLEANLY when the Water plugin is not enabled, with a distinct exit code, because a suite that
silently passes without exercising anything is worse than none. Water lives in
Engine/Plugins/Experimental/Water and is off by default.

Everything is built far from the origin in /Temp/Untitled_1, which is never saved.

WHY IT PLACES ITS LAKES BY PROBING RATHER THAN AT FIXED COORDINATES. The first version used fixed
positions, passed 21/21 standalone, and FAILED on the second run inside run_all_suites - which is
precisely what that runner's second pass is for. This suite cannot delete what it creates (deleting
an actor needs confirm:true), so zones accumulate in the level, and a later run's lake landed inside
an earlier run's zone. It therefore already had a zone, and the zone the test then created reported
bodiesNowCovered:0 - because a body belonging to some OTHER zone is neither covered-by-this-one nor
orphaned.

Both failures described the LEVEL, not the endpoint. place_unzoned_lake below moves until it finds a
position no existing zone reaches, and says so plainly if it cannot. A suite that only passes on a
clean level is a suite that passes once.

Usage:
    python tools/test_water_zone.py

Exit codes:
    0  ran and passed
    1  ran and something failed
    2  SKIPPED - the Water plugin is not enabled, nothing was verified
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []
STAMP = int(time.time() % 100000)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)[:240]))


def place_unzoned_lake(label, x0, y0, tries=6, step=400000.0):
    """Create a lake that belongs to NO water zone, and say where it ended up.

    WHY THIS IS NOT JUST create_water_body AT A FIXED SPOT. The level accumulates zones - this suite
    cannot delete them, because deleting an actor needs confirm:true - so a fixed position eventually
    lands inside a zone an EARLIER RUN created. When that happened the body already had a zone, and
    the zone this test then creates reported bodiesNowCovered:0, because the body belongs to the
    older zone and is therefore neither covered-by-this-one nor orphaned.

    Both failures were about the LEVEL rather than the endpoint, they only appeared on the second
    run, and the two-pass suite runner is exactly what surfaced them. The first standalone run passed
    21/21 and proved nothing about this.

    Returns (response, (x, y)) or (None, None) when every candidate was already inside a zone.
    """
    last = {}
    for i in range(tries):
        x = x0 + i * step
        last = M.call("create_water_body", {"type": "Lake", "x": x, "y": y0, "z": 0.0,
                                            "label": "%s_%d" % (label, i)})
        if last.get("ok") is not True:
            return None, None
        if not last.get("waterZone"):
            return last, (x, y0)
    return None, None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    probe = M.call("list_water_bodies", {})
    if probe.get("ok") is not True:
        print("")
        print("SKIPPED - nothing was verified.")
        print("  The Water plugin is not enabled on this build, so no water endpoint can run.")
        print("  Reason given: %s" % str(probe.get("error"))[:180])
        print("  Exit code 2 means SKIPPED, distinct from 0 (passed) on purpose.")
        return 2

    world = M.call("list_level_actors", {"limit": 1}).get("world") or ""
    if not (world.startswith("Untitled") or world.startswith("_Mif")):
        print("")
        print("SKIPPED - nothing was verified.")
        print("  The open level is '%s', which is not a scratch level. This suite SPAWNS actors" % world)
        print("  and will not do that in a real map.")
        return 2

    print("")
    print("=== T730: extentX and extentY are both-or-neither ===")
    # One axis from the caller and the other from an engine default is a shape nobody asked for, and
    # it would look deliberate afterwards.
    r = M.call("create_water_zone", {"extentX": 5000})
    check("T730 one axis alone is refused", r.get("ok") is False, json.dumps(r)[:220])
    check("T730 and the refusal says NOTHING was created",
          "NOTHING was created" in str(r.get("error")), str(r.get("error"))[:200])

    print("")
    print("=== T731: a zero or negative extent covers nothing ===")
    r = M.call("create_water_zone", {"extentX": -5, "extentY": 10})
    check("T731 a negative extent is refused", r.get("ok") is False, json.dumps(r)[:220])
    check("T731 and the message reports both numbers it was given",
          "-5.00" in str(r.get("error")) and "10.00" in str(r.get("error")), str(r.get("error"))[:200])

    print("")
    print("=== T732: an unknown parameter is refused, not ignored ===")
    r = M.call("create_water_zone", {"extent": 5000})
    check("T732 'extent' is refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T732 and it says to pass extentX and extentY",
          "extentX" in str(r.get("error")), str(r.get("error"))[:200])

    print("")
    print("=== T733: the extent is READ BACK off the actor, not echoed ===")
    zx, zy = 300000.0 + STAMP, 300000.0
    label = "MifZone_%d" % STAMP
    r = M.call("create_water_zone", {"x": zx, "y": zy, "z": 0.0,
                                     "extentX": 40000, "extentY": 40000, "label": label})
    check("T733 the zone was created", r.get("ok") is True, json.dumps(r)[:240])
    check("T733 it reports an actorPath", bool(r.get("actorPath")), json.dumps(r)[:200])
    ext = r.get("zoneExtent") or {}
    check("T733 zoneExtent comes back as the applied size",
          ext.get("x") == 40000 and ext.get("y") == 40000, json.dumps(ext))
    check("T733 no extentWarning when it applied cleanly", "extentWarning" not in r,
          r.get("extentWarning"))
    check("T733 the label was applied", r.get("label") == label, r.get("label"))

    print("")
    print("=== T735: a body created BEFORE a zone is invisible, and the zone SEES it ===")
    # The order is the point. A body finds its zone by OVERLAP, so a body authored first belongs to
    # no zone and renders nothing - which is the failure this endpoint exists to end.
    b, where = place_unzoned_lake("MifLake_%d" % STAMP, 620000.0 + STAMP, 620000.0)
    check("T735 a lake was placed somewhere no existing zone reaches", b is not None,
          "every candidate position was already inside a zone from an earlier run - the level is "
          "saturated. Restart the editor to clear /Temp/Untitled_1.")
    if b is not None:
        bx, by = where
        check("T735 and it belongs to NO zone, so it renders nothing",
              not b.get("waterZone"),
              "waterZone=%r - the helper is supposed to guarantee this" % (b.get("waterZone"),))

        r = M.call("create_water_zone", {"x": bx, "y": by, "z": 0.0,
                                         "extentX": 60000, "extentY": 60000,
                                         "label": "MifZoneOver_%d" % STAMP})
        check("T735 the covering zone was created", r.get("ok") is True, json.dumps(r)[:240])
        check("T735 and it reports the body it picked up",
              (r.get("bodiesNowCovered") or 0) >= 1,
              "bodiesNowCovered=%r - the number is observed by asking every body, so 0 here means "
              "the zone did not actually cover it" % (r.get("bodiesNowCovered"),))
        check("T735 no coverageWarning when nothing is left uncovered",
              "coverageWarning" not in r or (r.get("bodiesStillWithoutZone") or 0) > 0,
              r.get("coverageWarning"))

    print("")
    print("=== T736: a body OUTSIDE the new zone is named, not just counted ===")
    # Same helper, same reason: an orphan that an earlier run's zone already covers is not an orphan,
    # and the assertions below would fail for a fact about the level rather than about the endpoint.
    b2, where2 = place_unzoned_lake("MifOrphan_%d" % STAMP, 2400000.0 + STAMP, 2400000.0)
    check("T736 an uncovered lake was placed", b2 is not None,
          "no candidate position was outside every existing zone")
    if b2 is not None:
        ox, oy = where2
        orphan = b2.get("label") or ""
        # A zone somewhere else entirely. The orphan must still be reported as invisible. Placed far
        # enough away that its 20000 extent cannot reach the lake.
        r = M.call("create_water_zone", {"x": ox - 900000.0, "y": oy, "z": 0.0,
                                         "extentX": 20000, "extentY": 20000,
                                         "label": "MifZoneElsewhere_%d" % STAMP})
        check("T736 that zone was created", r.get("ok") is True, json.dumps(r)[:220])
        check("T736 the uncovered body is COUNTED", (r.get("bodiesStillWithoutZone") or 0) >= 1,
              "bodiesStillWithoutZone=%r" % (r.get("bodiesStillWithoutZone"),))
        check("T736 and NAMED, so nobody has to go hunting for it",
              orphan in [str(x) for x in (r.get("stillWithoutZone") or [])],
              "looking for %r in stillWithoutZone=%s"
              % (orphan, json.dumps(r.get("stillWithoutZone"))[:180]))
        check("T736 and a coverageWarning says they will not render",
              "render" in str(r.get("coverageWarning", "")), r.get("coverageWarning"))

    print("")
    print("=== T737 [found 2026-08-29 by audit_postconditions.py]: a padded label is trimmed, not lied about ===")
    # SetActorLabel is void and can silently refuse or trim - create_water_body/create_water_zone
    # both called it raw. The `label` field already read the real name back either way, so nobody was
    # ever told a wrong name - but nothing called out a mismatch, so noticing one meant diffing the
    # request against the response by hand. Fixed with the same SetActorLabelChecked house pattern
    # already proven for duplicate_actors/spawn_many; this proves it landed here too.
    padded = "  MifZonePadded_%d  " % STAMP
    r = M.call("create_water_zone", {"x": 500000.0 + STAMP, "y": 500000.0,
                                     "extentX": 5000, "extentY": 5000, "label": padded})
    check("T737 the zone is created", r.get("ok") is True, json.dumps(r)[:220])
    if r.get("ok"):
        check("T737 the trimmed label is what actually landed",
              r.get("label") == padded.strip(), r.get("label"))
        check("T737 and labelNote explains it was trimmed, not silent",
              "trimmed" in str(r.get("labelNote", "")).lower(), r.get("labelNote"))

    print("")
    print("=== T737b: create_water_body gets the same labelNote treatment ===")
    padded_body = "  MifLakePadded_%d  " % STAMP
    r = M.call("create_water_body", {"type": "Lake", "x": 500000.0 + STAMP, "y": 900000.0, "z": 0.0,
                                     "label": padded_body})
    check("T737b the body is created", r.get("ok") is True, json.dumps(r)[:220])
    if r.get("ok"):
        check("T737b the trimmed label is what actually landed",
              r.get("label") == padded_body.strip(), r.get("label"))
        check("T737b and labelNote explains it was trimmed",
              "trimmed" in str(r.get("labelNote", "")).lower(), r.get("labelNote"))

    print("")
    print("=== T738: an ordinary label needs no note at all ===")
    r = M.call("create_water_zone", {"x": 500000.0 + STAMP, "y": 700000.0,
                                     "extentX": 5000, "extentY": 5000,
                                     "label": "MifZoneOrdinary_%d" % STAMP})
    check("T738 the zone is created", r.get("ok") is True, json.dumps(r)[:220])
    if r.get("ok"):
        check("T738 no labelNote when nothing needed explaining",
              "labelNote" not in r, json.dumps(list(r.keys()))[:200])

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % (f[0],))
        print("          %s" % (f[1],))
    print("zones and lakes left in /Temp/Untitled_1 on purpose - it is never saved, and deleting an")
    print("actor would mean sending confirm:true, which the audit rules do not do.")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
