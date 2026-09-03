"""Fourth batch of coverage_gaps.py's zero-coverage list: describe_animation, list_animations,
get_collision, list_foliage_instances, perf_heavy_actors, list_sequence_bindings, live_coding_status,
live_coding_compile.

live_coding_status/live_coding_compile are a special case worth calling out: BOTH were used
extensively this same session (verifying a Live Coding hot-patch in front of Andre while reviewing
the Skeletal Split panel live), so their real behaviour is already well understood first-hand - they
just never had a committed test locking that understanding in. live_coding_compile's populated path
(an actual hot-patch compile) is deliberately NOT exercised here: starting Live Coding for a session
changes how the editor holds its DLLs for the REST of that session, which the endpoint's own guard
treats as a decision for a person at the keyboard, not something a routine regression sweep should
trigger as a side effect. This suite proves the refusal paths - no confirm, Live Coding not started -
which are the parts safe and correct to prove unconditionally.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []
UNPROVEN = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ================================================================== T850 describe_animation / list_animations
    print("=== T850: list_animations / describe_animation ===")
    r = M.call("list_animations", {"limit": 5})
    check("T850 list_animations answers", r.get("ok") is True, json.dumps(r)[:200])
    anims = r.get("animations") or []
    check("T850 count matches its own array", r.get("count") == len(anims), (r.get("count"), len(anims)))

    if anims:
        a = M.call("describe_animation", {"assetPath": anims[0].get("assetPath")})
        check("T850 describe_animation succeeds on a real asset", a.get("ok") is True, json.dumps(a)[:220])
        check("T850 and reports playLength as a real number",
              isinstance(a.get("playLength"), (int, float)), a.get("playLength"))
        check("T850 and reports its class", bool(a.get("class")), a.get("class"))
    else:
        UNPROVEN.append("describe_animation's populated path - list_animations found no animation "
                        "assets on this run.")

    q = M.call("describe_animation", {})
    check("T850 describe_animation with no path refuses", q.get("ok") is False, q.get("error"))
    q = M.call("describe_animation", {"assetPath": "x", "skeleton": "y"})
    check("T850 describe_animation rejects skeleton (an output-only field, not an input)",
          q.get("ok") is False, q.get("error"))
    q = M.call("list_animations", {"nameContains": "x"})
    check("T850 list_animations rejects nameContains (the real filter key is 'filter')",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T851 get_collision
    print("")
    print("=== T851: get_collision ===")
    # SKIP SCRATCH: a GeometryScript box from test_geometryscript has no simple collision and no
    # collision complexity worth reading, so T851 would be asking get_collision about an object
    # that has nothing to report and calling the empty answer a pass.
    meshes = [a for a in (M.call("find_assets", {"class": "StaticMesh",
                                                 "limit": 20}).get("assets") or [])
              if not M.is_scratch_fixture(a)]
    check("T851 (setup) there is at least one StaticMesh to test against", len(meshes) > 0, len(meshes))
    if meshes:
        r = M.call("get_collision", {"path": meshes[0].get("path")})
        check("T851 it answers on a real mesh", r.get("ok") is True, json.dumps(r)[:220])
        for key in ("simpleCollisionCount", "convexCollisionCount"):
            check("T851 %s is a real number" % key, isinstance(r.get(key), (int, float)), r.get(key))
        check("T851 collisionComplexity is a real, non-empty string",
              bool(r.get("collisionComplexity")), r.get("collisionComplexity"))

    q = M.call("get_collision", {})
    check("T851 no path refuses", q.get("ok") is False, q.get("error"))
    q = M.call("get_collision", {"path": meshes[0].get("path") if meshes else "x", "profile": "y"})
    check("T851 profile (a project-wide-list param, belongs to list_collision_profiles) is refused",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T852 list_foliage_instances
    print("")
    print("=== T852: list_foliage_instances ===")
    r = M.call("list_foliage_instances", {})
    check("T852 it answers", r.get("ok") is True, json.dumps(r)[:220])
    types = r.get("types") or []
    check("T852 typeCount matches its own array",
          r.get("typeCount") == len(types), (r.get("typeCount"), len(types)))
    check("T852 instanceCount is a real number",
          isinstance(r.get("instanceCount"), (int, float)), r.get("instanceCount"))
    if not types:
        check("T852 zero types is explained rather than a bare empty array",
              bool(r.get("note")), r)
        UNPROVEN.append("list_foliage_instances' populated path - no InstancedFoliageActor exists "
                        "in the currently open level on this run.")

    q = M.call("list_foliage_instances", {"actorPath": "x"})
    check("T852 actorPath (foliage is not addressed per-actor) is refused", q.get("ok") is False, q.get("error"))

    # ================================================================== T853 perf_heavy_actors
    print("")
    print("=== T853: perf_heavy_actors ===")
    r = M.call("perf_heavy_actors", {"limit": 10})
    check("T853 it answers", r.get("ok") is True, json.dumps(r)[:220])
    check("T853 reports actorsExamined", isinstance(r.get("actorsExamined"), (int, float)), r.get("actorsExamined"))
    check("T853 reports totals", isinstance(r.get("totals"), dict), r.get("totals"))
    actors = r.get("actors") or []
    check("T853 returns at most the requested limit", len(actors) <= 10, len(actors))

    q = M.call("perf_heavy_actors", {"fps": True})
    check("T853 fps (this measures static cost, not frame time) is refused", q.get("ok") is False, q.get("error"))

    # ================================================================== T854 list_sequence_bindings
    print("")
    print("=== T854: list_sequence_bindings ===")
    seqs = (M.call("find_assets", {"class": "LevelSequence", "limit": 1}).get("assets") or [])
    check("T854 (setup) there is at least one LevelSequence to test against", len(seqs) > 0, len(seqs))
    if seqs:
        r = M.call("list_sequence_bindings", {"path": seqs[0].get("path")})
        check("T854 it answers on a real sequence", r.get("ok") is True, json.dumps(r)[:220])
        bindings = r.get("bindings") or []
        check("T854 count matches its own array",
              r.get("count") == len(bindings), (r.get("count"), len(bindings)))
        if bindings:
            check("T854 every binding has a guid and a kind",
                  all(b.get("guid") and b.get("kind") for b in bindings), bindings[:2])
    else:
        UNPROVEN.append("list_sequence_bindings' populated path - no LevelSequence assets found.")

    q = M.call("list_sequence_bindings", {})
    check("T854 no path refuses", q.get("ok") is False, q.get("error"))
    q = M.call("list_sequence_bindings", {"path": seqs[0].get("path") if seqs else "x", "binding": "y"})
    check("T854 binding (this lists ALL bindings) is refused", q.get("ok") is False, q.get("error"))

    # ================================================================== T855 live_coding_status / live_coding_compile
    print("")
    print("=== T855: live_coding_status / live_coding_compile ===")
    r = M.call("live_coding_status", {})
    check("T855 live_coding_status answers", r.get("ok") is True, json.dumps(r)[:220])
    check("T855 available is a real bool", isinstance(r.get("available"), bool), r.get("available"))
    if r.get("available"):
        check("T855 blocksBuilds is a real bool", isinstance(r.get("blocksBuilds"), bool), r.get("blocksBuilds"))
        check("T855 started is a real bool", isinstance(r.get("started"), bool), r.get("started"))
    else:
        check("T855 unavailable is explained", bool(r.get("note")), r)

    q = M.call("live_coding_status", {"enable": True})
    check("T855 enable (this only reads state) is refused", q.get("ok") is False, q.get("error"))

    q = M.call("live_coding_compile", {})
    check("T855 live_coding_compile with no confirm refuses, NOTHING compiled",
          q.get("ok") is False and "confirm" in str(q.get("error", "")).lower(), q.get("error"))
    # NOT M.call below - guarded_payload strips "confirm" from every payload it sends (a guard
    # against a blind sweep accidentally confirming something), which is exactly why the no-confirm
    # probe above works. live_coding_compile has no asset path at all, so scratch_confirm.confirm_call
    # cannot be used either - it requires a /Game/_Mif... path in the payload to prove scratch-ness,
    # and there is nothing here for it to check. M.raw_post is the same narrow, deliberate bypass
    # mifaudit's own module docstring documents for exactly this shape of call. Found live 2026-08-29
    # during a full run_all_suites.py sweep: with confirm silently stripped, both calls below were
    # refused for the generic "needs confirm:true" reason instead of the SPECIFIC one each check
    # claims to verify - the wait-specific and not-started-specific refusals were never actually
    # reached.
    q = M.raw_post("live_coding_compile", {"confirm": True, "wait": True})
    check("T855 wait (deliberately not offered - would take the bridge off the air) is refused",
          q.get("ok") is False, q.get("error"))
    check("T855 and refused for the SPECIFIC reason (wait), not because confirm was never sent",
          "wait" in str(q.get("error", "")).lower(), q.get("error"))

    if not r.get("started"):
        # The state this whole session's editor launches actually start in: Live Coding is not
        # running until a person turns it on, so confirm:true alone still refuses.
        q2 = M.raw_post("live_coding_compile", {"confirm": True})
        check("T855 confirm:true alone still refuses when Live Coding has not been started "
              "(starting it is a person's decision, not this call's)",
              q2.get("ok") is False and "not been started" in str(q2.get("error", "")), q2.get("error"))
    else:
        UNPROVEN.append("live_coding_compile's 'Live Coding not started' refusal - Live Coding was "
                        "already running when this suite checked, so that specific guard path was "
                        "not exercised this run. The actual hot-patch path is never exercised by "
                        "this suite regardless, deliberately - see the file header.")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    if UNPROVEN:
        print("")
        print("NOT PROVEN BY THIS SUITE (green above does not cover these):")
        for u in UNPROVEN:
            print("  - %s" % u)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
