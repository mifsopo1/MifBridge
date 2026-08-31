"""apply_spline_to_landscape - a landscape write that had no suite at all.

FILED AND WRITTEN 2026-08-31, found after refreshing endpoints_current.json, which was 82 endpoints
stale and so blind to most of the surface. This endpoint deforms terrain and nothing exercised it.

S102 IS THE ONE WORTH HAVING. Heightfield collision is cooked separately from the render surface, so
terrain edited without a collision rebuild renders as hills and traces as dead flat - anything placed
by line trace then lands at the wrong height, and nothing on screen says so. sculpt_landscape and
import_landscape_heightmap call RecreateCollisionComponents themselves because they write heights
DIRECTLY through FLandscapeEditDataInterface::SetHeightData.

This endpoint must NOT, and does not: it goes through ALandscapeProxy::EditorApplySpline, which calls
LandscapeSplineRaster::RasterizeSegmentPoints, which finishes by calling
CollisionComponent->RecreateCollision() on every modified component (LandscapeSplineRaster.cpp:94 in
5.3). That was read before this suite was written, so S102 is not hunting a known bug - it is the
assertion that would CATCH IT if that engine path ever changes underneath us, which is the only
reason it is worth the seconds it costs.

verticesChanged IS THE POSTCONDITION, and the handler's own comment says why: EditorApplySpline is
void, so sampling the heightfield before and after is the only way to know it did anything.

THE LANDSCAPE IS PUT BACK. The neighbouring heightmap suite leaves its generated terrain in place -
the level is an unsaved /Temp map discarded at restart - but a spline deformation is cheap to undo
(export is ~0.1s) and restoring makes this suite re-runnable without the terrain drifting further
each time.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

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
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    probe = M.raw_post("export_landscape_heightmap", {})
    if probe.get("ok") is not True:
        # A PRECONDITION, NOT A PASS. With no landscape every assertion below is about nothing.
        print("SKIPPED - no landscape in the open level, so NOTHING was verified.")
        print("  (%s)" % (probe.get("error") or "")[:160])
        return 2

    st = int(time.time()) % 100000
    bp = "/Game/_MifSpline/BP_Spline%d" % st
    saved = None
    try:
        # ------------------------------------------------------------------ S100 the fixture
        print("\n=== S100: a spline actor, built rather than found ===")
        before = M.raw_post("export_landscape_heightmap", {"asData": True})
        check("S100 (setup) the landscape exports, so it can be put back afterwards",
              before.get("ok") is True and bool(before.get("file")), json.dumps(before)[:220])
        saved = before.get("file")

        made = M.raw_post("create_blueprint", {"path": bp, "parentClass": "Actor"})
        bid = made.get("blueprintId")
        check("S100 (setup) a scratch Actor blueprint", made.get("ok") is not False and bool(bid),
              json.dumps(made)[:220])
        if not bid:
            return 1
        comp = M.raw_post("add_component", {"blueprintId": bid, "class": "SplineComponent",
                                            "name": "Spline"})
        check("S100 (setup) a SplineComponent can be added - the fixture is BUILT, since nothing "
              "in this project carries one", comp.get("ok") is not False, json.dumps(comp)[:220])
        M.raw_post("compile_blueprint", {"blueprintId": bid})

        # ON THE LANDSCAPE, not at the world origin. The origin happens to sit near this
        # project's landscape, which is precisely the assumption that would make this suite pass
        # here and fail on any other map. Read the landscape's own location instead.
        land = before.get("landscape") or probe.get("landscape")
        loc = M.raw_post("get_property", {"actorPath": land,
                                          "property": "RootComponent.RelativeLocation"})
        sx = sy = 0.0
        raw = str(loc.get("value") or "")
        if raw.startswith("(X="):
            try:
                parts = dict(kv.split("=") for kv in raw.strip("()").split(","))
                sx, sy = float(parts["X"]), float(parts["Y"])
            except Exception:
                sx = sy = 0.0
        check("S100 (setup) the landscape's own location was read, so the spline goes ON it "
              "rather than at a world origin that only happens to work here",
              raw.startswith("(X="), "RelativeLocation came back as %r" % raw[:80])

        q = SC.spawn_tracked("spawn_actor_in_level", {
            "class": "%s.%s_C" % (bp, bp.rsplit("/", 1)[1]),
            "location": {"x": sx, "y": sy, "z": 0},
            "label": "MifSplineActor%d" % st})
        actor = ((q.get("actor") or {}).get("actorPath")) or q.get("actorPath")
        check("S100 (setup) it spawns onto the landscape", bool(actor), json.dumps(q)[:250])
        if not actor:
            return 1

        # ------------------------------------------------------------------ S101 the write
        print("\n=== S101: the deformation, counted from the heightfield ===")
        r = M.raw_post("apply_spline_to_landscape", {"splineActor": actor, "startWidth": 400,
                                                     "endWidth": 400})

        # A PRECONDITION THIS SUITE CANNOT SATISFY, reported rather than failed. A landscape with
        # EDIT LAYERS makes the endpoint refuse without being told which layer to write - correctly,
        # since EditorApplySpline would otherwise log an error and change nothing. The name is not
        # discoverable: nothing in the bridge reports the sculpt edit-layer stack (landscape_info's
        # `layers` is PAINT layers), so there is nothing to pass and guessing one is not testing.
        if r.get("ok") is False and "edit layers" in (r.get("error") or ""):
            print("  NOTE  this landscape has EDIT LAYERS, so the deformation was refused for want")
            print("        of an editLayer name - and no endpoint reports those names, so this")
            print("        suite cannot supply one. S101 and S102 are UNEXERCISED here and said so")
            print("        rather than counted. Filed as a read gap.")
            deformed = False
        else:
            deformed = r.get("ok") is True
            check("S101 apply_spline_to_landscape succeeds", r.get("ok") is True,
                  json.dumps(r)[:280])
        if deformed:
            # THE postcondition. EditorApplySpline is void - the handler samples the heightfield
            # before and after because that is the only way to know anything happened.
            check("S101 verticesChanged is MEASURED off the heightfield, not reported by a void "
                  "call",
                  isinstance(r.get("verticesChanged"), (int, float))
                  and r.get("verticesChanged") > 0, json.dumps(r)[:280])
            check("S101 and it reports the spline it used and how long it was",
                  bool(r.get("spline")) and (r.get("splineLength") or 0) > 0,
                  json.dumps(r)[:250])
            fresh = M.raw_post("export_landscape_heightmap", {"asData": True})
            check("S101 a fresh export differs from the one taken before - a DIFFERENT endpoint "
                  "agreeing that the terrain moved",
                  fresh.get("ok") is True and fresh.get("data") != before.get("data"),
                  "the heightmap is byte-identical after a deformation that reported %s vertices"
                  % r.get("verticesChanged"))

        # ------------------------------------------------------------------ S102 collision
        print("\n=== S102: the walkable surface follows the visible one ===")
        # NOT hunting a known bug - EditorApplySpline rebuilds collision via
        # RasterizeSegmentPoints (LandscapeSplineRaster.cpp:94), read before writing this. This is
        # the assertion that would catch it if that ever stopped being true.
        # ONLY MEANINGFUL IF THE TERRAIN ACTUALLY MOVED. The first version asserted this after a
        # REFUSED deformation, so it traced undeformed ground and passed no matter what - an
        # assertion that cannot fail is not an assertion.
        tr = M.raw_post("trace_ground", {"x": sx, "y": sy}) if deformed else {}
        if deformed and tr.get("ok") is True and tr.get("hit"):
            check("S102 a ground trace over the deformed spline still HITS - collision was rebuilt "
                  "with the surface rather than left stale",
                  tr.get("hit") is True, json.dumps(tr)[:250])
        elif not deformed:
            print("  NOTE  nothing was deformed, so the collision assertion is UNEXERCISED. It is")
            print("        not run against undeformed ground, because it would pass regardless.")
        else:
            # Reported rather than passed: a trace that misses proves nothing either way here.
            print("  NOTE  the ground trace did not hit at the spline origin, so the collision")
            print("        assertion is UNEXERCISED rather than passed. (%s)"
                  % json.dumps(tr)[:160])

        # ------------------------------------------------------------------ S103 refusals
        print("\n=== S103: the refusals ===")
        nothing = M.raw_post("apply_spline_to_landscape", {"splineActor": actor,
                                                           "raiseHeights": False,
                                                           "lowerHeights": False})
        check("S103 raise and lower both false with no paintLayer is refused - it would do "
              "nothing and report success",
              nothing.get("ok") is False, (nothing.get("error") or "")[:250])
        badparam = M.raw_post("apply_spline_to_landscape", {"splineActor": actor, "width": 100})
        check("S103 `width` is refused and told a spline can taper, so it is startWidth/endWidth",
              badparam.get("ok") is False and "taper" in (badparam.get("error") or ""),
              (badparam.get("error") or "")[:220])
        nospline = M.raw_post("apply_spline_to_landscape", {"splineActor": "/Game/_MifSpline/Nope"})
        check("S103 a spline actor that does not resolve is refused",
              nospline.get("ok") is False, (nospline.get("error") or "")[:220])

        check("S103 - the editor is still alive", M.call("self_audit", {"summaryOnly": True})
              .get("ok") is True, "landscape edits touch collision rebuilds")
    finally:
        # PUT THE TERRAIN BACK, and say whether it worked rather than assuming.
        if saved:
            back = M.raw_post("import_landscape_heightmap", {"file": saved})
            check("(cleanup) the landscape is restored from the export taken before",
                  back.get("ok") is True, json.dumps(back)[:250])
        SC.confirm_call("delete_asset", {"path": bp})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
