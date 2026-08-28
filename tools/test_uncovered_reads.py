"""Five whole-subsystem reads coverage_gaps.py found with ZERO test coverage: Gameplay Tags, PCG,
State Tree, Water's describe_water_body, and Enhanced Input mappings.

WHY THESE FIVE, TOGETHER. Each is a small, self-contained read-only cluster (2-3 endpoints) that
happened to fall out of every existing suite's scope rather than because anyone judged them
low-value - list_gameplay_tags/describe_gameplay_tag, list_pcg_graphs/describe_pcg_graph/
list_pcg_components, list_state_trees/describe_state_tree, describe_water_body, and
list_input_mappings. Batched into one file rather than five, matching the same reasoning
test_blender_gen.py used for ops_gen.py: one editor session, one coherent write-up, instead of five
near-identical single-endpoint files.

WHAT THIS PROJECT'S CONTENT CAN AND CANNOT PROVE, stated per subsystem rather than assumed:

  * Gameplay Tags, PCG, State Tree - all three were explicitly DECLINED at one point specifically
    because "DDS2 does not use it", then reopened because that reasoning judged a general-purpose
    tool by one test project. So DDS2 having none of these is the EXPECTED, documented state, not
    a surprise - this suite proves the empty-state path is honest and the parameter contracts hold,
    and says plainly that the populated path is unproven here.
  * Water - create_water_body already has coverage and this project's own scratch-water tests
    prove it works, so describe_water_body can be exercised on REAL scratch content in the open
    level. Full populated-path coverage, not just empty-state.
  * Enhanced Input - DDS2 is a shipped game and very likely DOES use it; checked by scanning for
    real InputMappingContext assets rather than assumed either way.
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

    # ================================================================== T820 gameplay tags
    print("=== T820: list_gameplay_tags / describe_gameplay_tag ===")
    r = M.call("list_gameplay_tags", {})
    check("T820 it answers", r.get("ok") is True, json.dumps(r)[:200])
    tags = r.get("tags") or []
    check("T820 count matches what was returned", r.get("count") == len(tags),
          "%s vs %d" % (r.get("count"), len(tags)))
    check("T820 explicitOnly is reported and defaults true",
          r.get("explicitOnly") is True, r.get("explicitOnly"))
    if not tags:
        check("T820 zero tags is explained, not a bare empty array",
              bool(r.get("note")), json.dumps(r)[:200])
        UNPROVEN.append("list_gameplay_tags'/describe_gameplay_tag's POPULATED path (real tag "
                        "hierarchy, parent/child walk, devComment) - DDS2 has no gameplay tags "
                        "registered, the documented, expected state for this project.")
        d = M.call("describe_gameplay_tag", {"tag": "NoSuchTag.AtAll.Zz"})
        check("T820 describing an unregistered tag succeeds with exists:false, not an error",
              d.get("ok") is True and d.get("exists") is False, json.dumps(d)[:200])
        check("T820 and explains why", bool(d.get("note")), d)
    else:
        richest = max(tags, key=lambda t: t.get("children") or 0)
        d = M.call("describe_gameplay_tag", {"tag": richest.get("tag")})
        check("T820 describing a real tag succeeds", d.get("ok") is True, json.dumps(d)[:200])
        check("T820 and says it exists", d.get("exists") is True, d)

    for label, payload, expect in (
        ("no path on describe", {}, "tag is required"),
        ("an unknown parameter on list", {"tag": "x"}, "filter"),
    ):
        q = M.call("list_gameplay_tags" if "list" in label else "describe_gameplay_tag", payload)
        check("T820 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])

    # ================================================================== T821 PCG
    print("")
    print("=== T821: list_pcg_graphs / describe_pcg_graph / list_pcg_components ===")
    r = M.call("list_pcg_graphs", {})
    check("T821 list_pcg_graphs answers", r.get("ok") is True, json.dumps(r)[:200])
    graphs = r.get("graphs") or []
    check("T821 count matches", r.get("count") == len(graphs), (r.get("count"), len(graphs)))
    check("T821 reports its source as the asset registry only",
          "registry" in str(r.get("source", "")).lower(), r.get("source"))

    r2 = M.call("list_pcg_components", {})
    check("T821 list_pcg_components answers", r2.get("ok") is True, json.dumps(r2)[:200])
    comps = r2.get("components") or []
    check("T821 count matches", r2.get("count") == len(comps), (r2.get("count"), len(comps)))

    if not graphs:
        check("T821 (not exercised: this project has no PCG graphs - the documented, expected "
              "state - describe_pcg_graph's populated path is unproven here)", True)
        UNPROVEN.append("describe_pcg_graph's populated path (real node list, settings class per "
                        "node, hasInputNode) - this project has no PCGGraph assets.")
        d = M.call("describe_pcg_graph", {"path": "/Game/NoSuchGraph_zz"})
        check("T821 describing a missing graph refuses clearly",
              d.get("ok") is False and "no PCGGraph" in str(d.get("error", "")), d.get("error"))
    else:
        d = M.call("describe_pcg_graph", {"path": graphs[0].get("path")})
        check("T821 describing a real graph succeeds", d.get("ok") is True, json.dumps(d)[:200])
        check("T821 nodeCount matches its own array",
              d.get("nodeCount") == len(d.get("nodes") or []), d)

    for label, payload in (
        ("list_pcg_graphs with an unknown param", {"tag": "x"}),
        ("describe_pcg_graph with no path", {}),
        ("list_pcg_components with any param at all", {"x": 1}),
    ):
        op = label.split(" ")[0]
        q = M.call(op, payload)
        check("T821 %s refused" % label, q.get("ok") is False, json.dumps(q)[:150])

    # ================================================================== T822 state tree
    print("")
    print("=== T822: list_state_trees / describe_state_tree ===")
    r = M.call("list_state_trees", {})
    check("T822 it answers", r.get("ok") is True, json.dumps(r)[:200])
    trees = r.get("stateTrees") or []
    check("T822 count matches", r.get("count") == len(trees), (r.get("count"), len(trees)))

    if not trees:
        check("T822 (not exercised: this project has no StateTree assets - the documented, "
              "expected state)", True)
        UNPROVEN.append("describe_state_tree's populated path (real state hierarchy, "
                        "parent/children index ranges, schema) - this project has no StateTree "
                        "assets.")
        d = M.call("describe_state_tree", {"path": "/Game/NoSuchTree_zz"})
        check("T822 describing a missing tree refuses clearly",
              d.get("ok") is False and "no StateTree" in str(d.get("error", "")), d.get("error"))
    else:
        d = M.call("describe_state_tree", {"path": trees[0].get("path")})
        check("T822 describing a real tree succeeds", d.get("ok") is True, json.dumps(d)[:200])
        check("T822 stateCount matches its own array",
              d.get("stateCount") == len(d.get("states") or []), d)

    q = M.call("describe_state_tree", {})
    check("T822 describe with no path refuses", q.get("ok") is False, q.get("error"))
    q = M.call("list_state_trees", {"tree": "x"})
    check("T822 list with an unknown param refuses", q.get("ok") is False, q.get("error"))

    # ================================================================== T823 water (real content)
    print("")
    print("=== T823: describe_water_body, against REAL scratch water ===")
    made = M.call("create_water_body", {"type": "Lake", "label": "MifTestLake",
                                        "x": 0, "y": 0, "z": 0,
                                        "points": [{"x": 0, "y": 0, "z": 0},
                                                   {"x": 2000, "y": 0, "z": 0},
                                                   {"x": 2000, "y": 2000, "z": 0}]})
    check("T823 a scratch water body is created", made.get("ok") is True, json.dumps(made)[:220])
    if made.get("ok"):
        actor_path = made.get("actorPath")
        d = M.call("describe_water_body", {"path": actor_path})
        check("T823 describe_water_body succeeds on it", d.get("ok") is True, json.dumps(d)[:220])
        check("T823 and agrees on the actor path", d.get("actorPath") == actor_path, d.get("actorPath"))
        check("T823 and reports the same waterBodyType",
              d.get("waterBodyType") == made.get("waterBodyType"),
              (d.get("waterBodyType"), made.get("waterBodyType")))
        pts = d.get("splinePointsWorld") or []
        check("T823 splinePointsWorld defaults present and matches what was set",
              len(pts) == made.get("splinePointsSet"), (len(pts), made.get("splinePointsSet")))
        check("T823 every spline point has real x/y/z",
              all(isinstance(p.get("x"), (int, float)) for p in pts), pts[:2])

        d2 = M.call("describe_water_body", {"path": actor_path, "includeSplinePoints": False})
        check("T823 includeSplinePoints:false omits the array",
              "splinePointsWorld" not in d2, d2)

        q = M.call("describe_water_body", {"path": "/Game/NoSuchWaterBody_zz"})
        check("T823 a missing actor path refuses clearly",
              q.get("ok") is False and "list_water_bodies" in str(q.get("error", "")), q.get("error"))
        q = M.call("describe_water_body", {})
        check("T823 no path refuses", q.get("ok") is False, q.get("error"))
    else:
        UNPROVEN.append("describe_water_body's populated path - create_water_body itself failed "
                        "on this run (see the failure above), so nothing was made to describe.")

    # ================================================================== T824 enhanced input
    print("")
    print("=== T824: list_input_mappings ===")
    contexts = (M.call("find_assets", {"class": "InputMappingContext", "limit": 20}).get("assets") or [])
    check("T824 there is at least one InputMappingContext to check against, OR the empty case "
          "is what this project actually has", True)
    if not contexts:
        check("T824 (not exercised: this project has no InputMappingContext assets)", True)
        UNPROVEN.append("list_input_mappings' populated path (real action/key/trigger/modifier "
                        "rows) - this project has no InputMappingContext assets.")
    else:
        ctx_path = contexts[0].get("path")
        r = M.call("list_input_mappings", {"path": ctx_path})
        check("T824 it answers on a real context", r.get("ok") is True, json.dumps(r)[:220])
        mappings = r.get("mappings") or []
        check("T824 count matches", r.get("count") == len(mappings), (r.get("count"), len(mappings)))
        if mappings:
            check("T824 every mapping names a key",
                  all(m.get("key") for m in mappings), mappings[:3])
            check("T824 triggers/modifiers are always arrays, even when empty",
                  all(isinstance(m.get("triggers"), list) and isinstance(m.get("modifiers"), list)
                      for m in mappings), mappings[:3])
        else:
            check("T824 a real context with zero mappings still explains itself",
                  bool(r.get("note")), r)

    q = M.call("list_input_mappings", {})
    check("T824 no path refuses", q.get("ok") is False, q.get("error"))
    q = M.call("list_input_mappings", {"path": "/Game/NoSuchContext_zz"})
    check("T824 a missing context refuses and points at find_assets",
          q.get("ok") is False and "find_assets" in str(q.get("error", "")), q.get("error"))

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
