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
    bx, by = 620000.0 + STAMP, 620000.0
    b = M.call("create_water_body", {"type": "Lake", "x": bx, "y": by, "z": 0.0,
                                     "label": "MifLake_%d" % STAMP})
    check("T735 the lake was created", b.get("ok") is True, json.dumps(b)[:240])
    check("T735 and it belongs to NO zone, so it renders nothing",
          not b.get("waterZone"),
          "waterZone=%r - expected empty; this test needs a body with no zone" % (b.get("waterZone"),))

    r = M.call("create_water_zone", {"x": bx, "y": by, "z": 0.0,
                                     "extentX": 60000, "extentY": 60000,
                                     "label": "MifZoneOver_%d" % STAMP})
    check("T735 the covering zone was created", r.get("ok") is True, json.dumps(r)[:240])
    check("T735 and it reports the body it picked up",
          (r.get("bodiesNowCovered") or 0) >= 1,
          "bodiesNowCovered=%r - the number is observed by asking every body, so 0 here means the "
          "zone did not actually cover it" % (r.get("bodiesNowCovered"),))
    check("T735 no coverageWarning when nothing is left uncovered",
          "coverageWarning" not in r or (r.get("bodiesStillWithoutZone") or 0) > 0,
          r.get("coverageWarning"))

    print("")
    print("=== T736: a body OUTSIDE the new zone is named, not just counted ===")
    ox, oy = 900000.0 + STAMP, 900000.0
    orphan = "MifOrphan_%d" % STAMP
    b2 = M.call("create_water_body", {"type": "Lake", "x": ox, "y": oy, "z": 0.0, "label": orphan})
    check("T736 the far-away lake was created", b2.get("ok") is True, json.dumps(b2)[:200])

    # A zone somewhere else entirely. The orphan must still be reported as invisible.
    r = M.call("create_water_zone", {"x": ox - 400000.0, "y": oy, "z": 0.0,
                                     "extentX": 20000, "extentY": 20000,
                                     "label": "MifZoneElsewhere_%d" % STAMP})
    check("T736 that zone was created", r.get("ok") is True, json.dumps(r)[:220])
    check("T736 the uncovered body is COUNTED", (r.get("bodiesStillWithoutZone") or 0) >= 1,
          "bodiesStillWithoutZone=%r" % (r.get("bodiesStillWithoutZone"),))
    check("T736 and NAMED, so nobody has to go hunting for it",
          orphan in [str(x) for x in (r.get("stillWithoutZone") or [])],
          "stillWithoutZone=%s" % json.dumps(r.get("stillWithoutZone"))[:200])
    check("T736 and a coverageWarning says they will not render",
          "render" in str(r.get("coverageWarning", "")), r.get("coverageWarning"))

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
