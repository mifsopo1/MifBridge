"""snap_actors_to_ground: the missed-112-of-303 bug, and the two behaviours that must survive fixing it.

The bug: the handler traced once with LineTraceMultiByChannel and searched the results for a landscape,
believing a MULTI trace sees everything along the ray. From World.h: "Only the single closest blocking
result will be generated, no tests will be done after that." Every static mesh blocks WorldStatic, so
for any actor standing over another actor the results held one hit - the prop - and the ground
underneath was never in them. Those actors were reported missed.

T60-T62 are the regression. T63-T65 guard the things the fix must NOT break: it would be easy to
"fix" this by snapping onto the first thing hit, which is the original bug the multi-trace was added
to stop (a palm snapping onto a shack roof, the scene walking upward a layer per call).

Everything is built far from the origin in a throwaway column. The level here is /Temp/Untitled_1 and
is never saved; the props are left behind deliberately rather than sending confirm:true to delete
them.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []

CUBE = "/Engine/BasicShapes/Cube.Cube"
HALF = 50.0          # the engine cube is 100 units; scale 1 means a half-height of 50
FLOOR_TOP = 50.0     # floor spawned at Z=0 with Z-scale 1

# Unique per run. Re-running against the previous run's props would match a stale label, and the
# subjects from last time are already sitting at their snapped Z rather than up in the air.
STAMP = int(time.time() % 100000)


def lbl(base):
    return "%s_%d" % (base, STAMP)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def spawn(label, x, y, z, sx=1.0, sy=1.0, sz=1.0):
    r = M.call("spawn_actor_in_level", {
        "actorClass": "StaticMeshActor", "mesh": CUBE, "label": label,
        "location": {"x": x, "y": y, "z": z},
        "scale": {"x": sx, "y": sy, "z": sz}})
    if not r.get("ok"):
        raise RuntimeError("spawn failed for %s: %s" % (label, json.dumps(r)[:300]))
    return r["actor"]["actorPath"]


def actor_z(label):
    """Read one actor's Z back, through get_level_actor, cross-checked against the lister.

    This helper is why get_level_actor exists. Its first version called that endpoint on the
    assumption it was there, got route_handler_not_found, returned None, and turned five real
    assertions into None == None - they only failed loudly because the expected values were concrete
    numbers. The endpoint was then added (243 endpoints), so the helper now uses it.

    It still cross-checks against list_level_actors: two independent reads that must agree, so a bug
    in either shows up here rather than being quietly trusted. Raise rather than return None - a test
    helper that quietly answers "no idea" is how a vacuous suite passes.
    """
    one = M.call("get_level_actor", {"actorPath": label})
    if not one.get("ok"):
        raise RuntimeError("get_level_actor could not read %s: %s" % (label, json.dumps(one)[:300]))
    z = one["actor"]["location"]["z"]

    listed = M.call("list_level_actors", {"nameContains": label})
    match = [a for a in (listed.get("actors") or []) if a.get("label") == label]
    if len(match) != 1:
        raise RuntimeError("the lister found %d actors labelled %s, not 1" % (len(match), label))
    if abs(match[0]["location"]["z"] - z) > 0.001:
        raise RuntimeError("get_level_actor and list_level_actors disagree on %s: %s vs %s"
                           % (label, z, match[0]["location"]["z"]))
    return z


def put_z(path, z, x, y):
    M.call("set_actor_transform", {"actorPath": path, "location": {"x": x, "y": y, "z": z}})


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ---------------------------------------------------------------- scene A: one blocker
    AX, AY = 210000.0 + STAMP * 10.0, 210000.0
    L_FLOORA, L_OVER = lbl("MifSnapFloorA"), lbl("MifSnapOverBlocker")
    L_OPEN, L_BURIED, L_FLOORB = lbl("MifSnapOpenAir"), lbl("MifSnapBuried"), lbl("MifSnapFloorB")
    floor = spawn(L_FLOORA, AX, AY, 0.0, 20, 20, 1)
    spawn(lbl("MifSnapBlockerA"), AX, AY, 400.0, 4, 4, 1)      # spans 350..450
    over = spawn(L_OVER, AX, AY, 1200.0)
    openair = spawn(L_OPEN, AX + 900.0, AY, 1200.0)            # above the floor, nothing between
    print("scene A built at (%.0f, %.0f)" % (AX, AY))

    # ---------------------------------------------------------------- T60 the regression
    print("\n=== T60: an actor standing over a prop still finds the ground under it ===")
    r = M.call("snap_actors_to_ground", {"actorPaths": [over, openair], "groundActor": L_FLOORA})
    print("  ", json.dumps({k: v for k, v in r.items() if k != "moved"})[:400])
    check("T60 both actors snapped, neither missed",
          r.get("snapped") == 2 and r.get("missed") == 0,
          "snapped=%s missed=%s  (old code missed the one over the blocker)"
          % (r.get("snapped"), r.get("missed")))

    check("T61 the response reports piercing a blocker",
          isinstance(r.get("blockersPierced"), (int, float)) and r.get("blockersPierced") >= 1,
          "blockersPierced=%r - absent means the old binary is loaded" % (r.get("blockersPierced"),))

    z_over, z_open = actor_z(L_OVER), actor_z(L_OPEN)
    want = FLOOR_TOP + HALF
    check("T62 it landed on the FLOOR, not on the blocker",
          z_over is not None and abs(z_over - want) < 1.0,
          "z=%s want %.1f (blocker top would be 500)" % (z_over, want))
    check("T62 the unobstructed actor landed there too",
          z_open is not None and abs(z_open - want) < 1.0, "z=%s want %.1f" % (z_open, want))

    # ---------------------------------------------------------------- T63 anti-stacking preserved
    print("\n=== T63 [must not regress]: with no landscape and no groundActor, it still refuses ===")
    put_z(over, 1200.0, AX, AY)
    r = M.call("snap_actors_to_ground", {"actorPaths": [over]})
    print("  ", json.dumps({k: v for k, v in r.items() if k != "moved"})[:340])
    check("T63 refuses rather than stacking onto the prop",
          r.get("snapped") == 0 and r.get("missed") == 1,
          "snapped=%s missed=%s - snapping here is the ORIGINAL bug"
          % (r.get("snapped"), r.get("missed")))
    check("T63 left the actor where it was", abs((actor_z(L_OVER) or 0) - 1200.0) < 1.0, actor_z(L_OVER))

    # ---------------------------------------------------------------- T64 allowAnyHit unchanged
    print("\n=== T64 [must not regress]: allowAnyHit still takes the FIRST hit ===")
    put_z(over, 1200.0, AX, AY)
    r = M.call("snap_actors_to_ground", {"actorPaths": [over], "allowAnyHit": True})
    z = actor_z(L_OVER)
    check("T64 snapped onto the blocker, not through it",
          r.get("snapped") == 1 and z is not None and abs(z - 500.0) < 1.0,
          "snapped=%s z=%s want 500 (blocker top 450 + half height)" % (r.get("snapped"), z))
    check("T64 nothing was pierced on that path", r.get("blockersPierced") == 0,
          "blockersPierced=%r" % (r.get("blockersPierced"),))

    # ---------------------------------------------------------------- T65 the bound
    print("\n=== T65: a stack deeper than the budget gives up, and says so ===")
    BX, BY = 220000.0 + STAMP * 10.0, 220000.0
    spawn(L_FLOORB, BX, BY, 0.0, 20, 20, 1)
    for i in range(40):
        spawn(lbl("MifSnapStack_%02d" % i), BX, BY, 600.0 + i * 20.0, 1, 1, 0.1)
    buried = spawn(L_BURIED, BX, BY, 2000.0)
    r = M.call("snap_actors_to_ground", {"actorPaths": [buried], "groundActor": L_FLOORB})
    print("  ", json.dumps({k: v for k, v in r.items() if k != "moved"})[:460])
    check("T65 gave up instead of digging forever",
          r.get("snapped") == 0 and r.get("missed") == 1, json.dumps(r)[:220])
    check("T65 reported it as buried, not as 'nothing below'",
          r.get("missedUnderDeepStack") == 1 and "deepStackNote" in r,
          "missedUnderDeepStack=%r" % (r.get("missedUnderDeepStack"),))
    check("T65 left the buried actor alone", abs((actor_z(L_BURIED) or 0) - 2000.0) < 1.0, actor_z(L_BURIED))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("props left in /Temp/Untitled_1 on purpose - it is never saved, and deleting an actor")
    print("would mean sending confirm:true, which the audit rules do not do.")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
