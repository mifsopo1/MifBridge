"""add_foliage_instances - two modes, and the difference between them.

The endpoint has been called add_foliage_instances since Batch D while touching the Foliage system not
at all: it spawned a bare actor with a HierarchicalInstancedStaticMeshComponent. Good for what it did,
but it meant DDS2's own 42 FoliageType_InstancedStaticMesh assets were unreachable, and foliage added
this way inherits none of a type's cull distance, density or scaling - so it matches at 2 metres and
culls wrongly at 100.

T202 is the test with teeth. It does not ask "did the call succeed", it asks whether the instances
really landed in the level's AInstancedFoliageActor: the IFA has to be findable as an actor in the
world afterwards, and totalForType has to ACCUMULATE across two calls into the same FFoliageInfo. A
mode that quietly built a second holder actor would pass a naive check and fail both of those.

T203 re-tests PM-007 on the new branch. The old code spawned the holder actor and then parsed, trusting
a transaction cancel that does not roll spawns back - FTransaction discards the undo entry without
calling Apply. The parse happens first for both modes now, and this proves it for the one just added.

SAFETY. This mutates the loaded level, so it refuses to run against anything that is not a scratch
level. Nothing is saved.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def grid(n, z=200.0, step=150.0):
    return [{"x": (i % 5) * step, "y": (i // 5) * step, "z": z} for i in range(n)]


def ifa_actors():
    r = M.call("list_level_actors", {"classFilter": "InstancedFoliageActor", "limit": 50})
    return [a.get("path") or a.get("actorPath") for a in (r.get("actors") or [])]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ---- safety: this writes into the loaded level ---------------------------------------
    lvl = M.call("list_level_actors", {"limit": 1}) or {}
    world = lvl.get("world") or ""
    name = json.dumps(lvl)
    if not lvl.get("ok") or ("Untitled" not in world and "_Mif" not in world):
        print("REFUSING to run: this mutates the loaded level and it does not look like a scratch one.")
        print("  " + name[:300])
        return 2
    print("scratch level confirmed: %s" % world)

    ft = (M.call("find_assets", {"class": "FoliageType_InstancedStaticMesh",
                                 "limit": 1}).get("assets") or [{}])[0].get("path")
    sm = (M.call("find_assets", {"class": "StaticMesh", "limit": 1}).get("assets") or [{}])[0].get("path")
    if not ft or not sm:
        print("setup failed: foliageType=%s staticMesh=%s" % (ft, sm))
        return 3
    print("foliage type: %s" % ft)

    # ---------------------------------------------------------------- T200 mode selection
    print("\n=== T200: choosing a mode ===")
    for label, payload, expect in (
        ("neither", {"instances": grid(2)}, "one of mesh or foliageType is required"),
        ("both", {"instances": grid(2), "mesh": sm, "foliageType": ft}, "alternatives, not a pair"),
        ("a mesh passed as a type", {"instances": grid(2), "foliageType": sm}, "NOT the static mesh it"),
    ):
        q = M.call("add_foliage_instances", payload)
        check("T200 %s refused" % label, q.get("ok") is False, json.dumps(q)[:160])
        check("T200 %s explains" % label, expect in (q.get("error") or ""), (q.get("error") or "")[:220])
        check("T200 %s created nothing" % label, "NOTHING was created" in (q.get("error") or ""),
              (q.get("error") or "")[:160])

    # ---------------------------------------------------------------- T201 mesh mode regression
    print("\n=== T201: mesh mode still works, and now admits what it is ===")
    m = M.call("add_foliage_instances", {"mesh": sm, "instances": grid(6), "label": "MifFolMesh"})
    print("  ", json.dumps({k: v for k, v in m.items() if not k.endswith("Note")})[:230])
    check("T201 mesh mode succeeds", m.get("ok") is True, json.dumps(m)[:180])
    check("T201 it reports which mode ran", m.get("mode") == "instancedMeshActor", m.get("mode"))
    check("T201 all six instances landed", m.get("instanceCount") == 6, m.get("instanceCount"))
    # The endpoint name has implied Foliage since Batch D. It now says otherwise in the response.
    check("T201 and it says plainly it is NOT the Foliage system",
          "NOT the" in (m.get("modeNote") or "") and "Foliage system" in (m.get("modeNote") or ""),
          (m.get("modeNote") or "")[:200])
    check("T201 a mesh-mode actor is NOT an InstancedFoliageActor",
          (m.get("actorPath") or "") not in ifa_actors(), m.get("actorPath"))

    # ---------------------------------------------------------------- T202 the actual point
    print("\n=== T202 [the point]: foliageType mode lands in the level's real IFA ===")
    a = M.call("add_foliage_instances", {"foliageType": ft, "instances": grid(8)})
    print("  ", json.dumps({k: v for k, v in a.items() if not k.endswith("Note")})[:280])
    check("T202 foliage mode succeeds", a.get("ok") is True, json.dumps(a)[:200])
    check("T202 it reports which mode ran", a.get("mode") == "foliageSystem", a.get("mode"))
    check("T202 all eight instances were accepted", a.get("instanceCount") == 8,
          "requested=%s accepted=%s" % (a.get("requested"), a.get("instanceCount")))
    check("T202 it echoes the foliage type it used", (a.get("foliageType") or "").endswith(ft.split(".")[-1]),
          a.get("foliageType"))
    # NOT "the first call created it" - that is only true against a level which does not already
    # carry this foliage type, and the scratch level keeps whatever a previous run put there until the
    # editor restarts. The durable property is that createdFoliageInfo tells the TRUTH about whether it
    # had to create one, which is checkable either way.
    check("T202 createdFoliageInfo is reported", isinstance(a.get("createdFoliageInfo"), bool),
          a.get("createdFoliageInfo"))
    if a.get("createdFoliageInfo") is False:
        print("   (this foliage type was already in the level from an earlier run - fine)")
    # Proof it is really in the level rather than another holder actor wearing the name.
    live = ifa_actors()
    check("T202 an InstancedFoliageActor now exists in the world", len(live) >= 1, str(live)[:200])
    check("T202 and it is the one the response named",
          any((a.get("foliageActorPath") or "x") == p for p in live),
          "%s not in %s" % (a.get("foliageActorPath"), str(live)[:160]))

    # Accumulation is the half that a second holder actor would fail.
    b = M.call("add_foliage_instances", {"foliageType": ft, "instances": grid(5)})
    # This one IS durable regardless of prior state: whatever happened on the call above, the info
    # exists by now, so a further call must reuse it rather than create a second.
    check("T202 a second call reuses the SAME FoliageInfo", b.get("createdFoliageInfo") is False,
          b.get("createdFoliageInfo"))
    check("T202 and totalForType accumulates rather than restarting",
          b.get("totalForType") == (a.get("totalForType") or 0) + 5,
          "first=%s second=%s" % (a.get("totalForType"), b.get("totalForType")))
    check("T202 the same IFA was reused, not a second one",
          b.get("foliageActorPath") == a.get("foliageActorPath"),
          "%s vs %s" % (b.get("foliageActorPath"), a.get("foliageActorPath")))
    check("T202 and the world still holds exactly one IFA", len(ifa_actors()) == len(live), str(ifa_actors())[:160])

    # ---------------------------------------------------------------- T203 PM-007 on the new branch
    print("\n=== T203: nothing half-applies (PM-007, re-tested on the new branch) ===")
    before = b.get("totalForType")
    bad = grid(4)
    bad[2]["z"] = "not a number"
    q = M.call("add_foliage_instances", {"foliageType": ft, "instances": bad})
    check("T203 a bad transform fails the whole call", q.get("ok") is False, json.dumps(q)[:170])
    check("T203 and names the offending entry", "instances[2]" in (q.get("error") or ""),
          (q.get("error") or "")[:190])
    after = M.call("add_foliage_instances", {"foliageType": ft, "instances": grid(1)})
    check("T203 no instances from the failed call survived",
          after.get("totalForType") == before + 1,
          "was %s, expected %s after adding 1, got %s" % (before, before + 1, after.get("totalForType")))

    # ---------------------------------------------------------------- T204 ignored params are reported
    print("\n=== T204: parameters that do not apply are reported, not swallowed ===")
    L = M.call("add_foliage_instances", {"foliageType": ft, "instances": grid(2),
                                         "label": "Ignored", "folder": "Ignored"})
    check("T204 label and folder are accepted but reported as ignored",
          "ignored" in (L.get("labelNote") or "").lower(), (L.get("labelNote") or "")[:200])
    check("T204 and the instances still went in", L.get("instanceCount") == 2, L.get("instanceCount"))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("NOTE: this left foliage and a holder actor in the scratch level. Nothing was saved.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
