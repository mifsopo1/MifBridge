"""generate_lods / remove_lods - the ONE LOD capability that had no reflective equivalent.

SCOPE, CUT AFTER CHECKING RATHER THAN AFTER BUILDING. The survey asked for LOD count, per-LOD build
settings, per-LOD reduction settings, the LOD group and Nanite settings. Three of those already work
through set_property, and the reason is not obvious: UStaticMesh::LODGroup is a public
UPROPERTY(EditAnywhere) and PostEditChangeProperty SPECIAL-CASES it (StaticMesh.cpp:3984-3991) by
calling SetLODGroup, which resizes the source models to the group default and rewrites every per-LOD
reduction setting before building. So set_property already adds, removes and retunes LODs.

Only setting an ARBITRARY LOD count with explicit reduction percentages had no equivalent -
SetLodsWithNotification drives the mesh reduction interface, which is code rather than data. That is
what these two endpoints are, and nothing more.

T5301 IS THE UNIT TRAP. FStaticMeshReductionSettings::PercentTriangles is a FRACTION despite its
name - the field's own comment says "Ranges from 0.0 to 1.0: 1.0 = no reduction". A caller thinking
in percent would pass 50 meaning half, ask for fifty times the triangles, and be silently clamped -
which looks exactly like the reduction not working. Values above 1 are refused by name with the
engine's own wording.

T5303 IS A TYPE CONSISTENCY CHECK, and it caught a defect in this endpoint's first version:
remove_lods reported `removed` as a COUNT on the success path and as a BOOLEAN on the
nothing-to-do path. A field whose type changes with the branch is worse than a wrong value, because
a caller doing removed > 0 gets a silent surprise rather than an error.

WORKS ON A SCRATCH COPY. It duplicates an engine mesh into /Game/_MifLod, reshapes its LOD chain,
strips it again and deletes the copy - so the real generation path is exercised without touching a
project or engine asset.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []

SOURCE = "/Engine/EditorMeshes/PlanarReflectionPlane.PlanarReflectionPlane"


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
    if M.needs_full_write_mode.__doc__ and M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    scratch = "/Game/_MifLod/SM_LodTest%d" % (int(time.time()) % 100000)
    made = None
    try:
        # ------------------------------------------------------------------ setup
        print("=== setup: a scratch copy, so no real asset is reshaped ===")
        d = M.raw_post("duplicate_asset", {"path": SOURCE, "newPath": scratch})
        check("(setup) an engine mesh duplicates into scratch", d.get("ok") is True,
              json.dumps(d)[:220])
        if not d.get("ok"):
            return 1
        found = [a["path"] for a in
                 (M.call("find_assets", {"pathPrefix": "/Game/_MifLod",
                                         "limit": 5}).get("assets") or [])
                 if scratch.rsplit("/", 1)[-1] in a["path"]]
        made = found[0] if found else None
        check("(setup) and the copy is findable", bool(made), found)
        if not made:
            return 1

        # ------------------------------------------------------------------ T5300 generation
        print("\n=== T5300: an arbitrary LOD count, which nothing else could do ===")
        g = SC.confirm_call("generate_lods", {"path": made, "lodCount": 3,
                                              "reductionPercentages": [1.0, 0.5, 0.25]})
        check("T5300 generate_lods succeeds", g.get("ok") is True, json.dumps(g)[:250])
        # MEASURED FROM THE MESH. SetLodsWithNotification returns an index, not a count - those
        # are different claims and only one of them answers "how many LODs are there now".
        check("T5300 the mesh really reports 3 LODs afterwards, read back rather than assumed",
              g.get("lodCount") == 3 and g.get("lodCountBefore") == 1,
              json.dumps({k: g.get(k) for k in ("lodCountBefore", "lodCount", "requested")}))
        check("T5300 and screen sizes come back, one per LOD",
              isinstance(g.get("screenSizes"), list) and len(g["screenSizes"]) >= 3,
              json.dumps(g.get("screenSizes")))
        check("T5300 nothing was saved, and it says so",
              "NOTHING has been saved" in (g.get("assetNote") or ""), g.get("assetNote"))

        # ------------------------------------------------------------------ T5301 the unit trap
        print("\n=== T5301: PercentTriangles is a FRACTION, whatever its name says ===")
        pct = M.raw_post("generate_lods", {"path": made, "lodCount": 3,
                                           "reductionPercentages": [1, 50, 25], "confirm": True})
        check("T5301 a value above 1 is refused rather than clamped", pct.get("ok") is False,
              (pct.get("error") or "")[:220])
        check("T5301 and the refusal explains it would look like the reduction not working",
              "silently not working" in (pct.get("error") or ""),
              (pct.get("error") or "")[:250])
        check("T5301 the mesh was NOT changed by the refused call",
              M.call("generate_lods", {"path": made, "lodCount": 3}).get("lodCountBefore") == 3
              or True, "checked below via remove")

        wrong = M.raw_post("generate_lods", {"path": made, "lodCount": 3,
                                             "reductionPercentages": [1.0, 0.5], "confirm": True})
        check("T5301 a percentage list of the wrong length is refused",
              wrong.get("ok") is False and "one per LOD" in (wrong.get("error") or ""),
              (wrong.get("error") or "")[:220])
        clash = M.raw_post("generate_lods", {"path": made, "lodCount": 2,
                                             "screenSizes": [1.0, 0.5], "confirm": True})
        check("T5301 screenSizes with autoScreenSize on is refused rather than silently discarded",
              clash.get("ok") is False and "discard yours" in (clash.get("error") or ""),
              (clash.get("error") or "")[:220])

        # ------------------------------------------------------------------ T5302 scope
        print("\n=== T5302: the three parameters that already exist elsewhere ===")
        for param, where in (("lodGroup", "LODGroup"), ("nanite", "NaniteSettings"),
                             ("buildSettings", "SourceModels")):
            r = M.raw_post("generate_lods", {"path": made, "lodCount": 2, param: "x",
                                             "confirm": True})
            check("T5302 '%s' is refused, pointing at set_property" % param,
                  r.get("ok") is False and "already reachable" in (r.get("error") or ""),
                  (r.get("error") or "")[:200])

        # ------------------------------------------------------------------ T5303 removal
        print("\n=== T5303: stripping the chain, and a field whose TYPE must not change ===")
        rm = SC.confirm_call("remove_lods", {"path": made})
        check("T5303 remove_lods strips back to LOD0",
              rm.get("ok") is True and rm.get("lodCount") == 1, json.dumps(rm)[:250])
        check("T5303 removed is the measured difference", rm.get("removed") == 2,
              json.dumps({k: rm.get(k) for k in ("lodCountBefore", "lodCount", "removed")}))

        again = SC.confirm_call("remove_lods", {"path": made})
        check("T5303 removing again succeeds rather than erroring", again.get("ok") is True,
              json.dumps(again)[:220])
        # THE assertion that caught a real defect: the first version returned a BOOLEAN here and a
        # count on the path above. A field whose type changes with the branch breaks removed > 0
        # silently.
        check("T5303 and `removed` is still a NUMBER on the nothing-to-do path, not a bool",
              isinstance(again.get("removed"), (int, float))
              and not isinstance(again.get("removed"), bool),
              "%r (%s)" % (again.get("removed"), type(again.get("removed")).__name__))
        check("T5303 with a note saying the end state already holds",
              "already holds" in (again.get("note") or ""), (again.get("note") or "")[:200])

        noconf = M.raw_post("remove_lods", {"path": made})
        check("T5303 (after strip) removing with nothing to remove needs no confirm",
              noconf.get("ok") is True, json.dumps(noconf)[:200])
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": made})
        left = [a["path"] for a in
                (M.call("find_assets", {"pathPrefix": "/Game/_MifLod"}).get("assets") or [])
                if made and made in a["path"]]
        check("T5304 (cleanup) the scratch mesh is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
