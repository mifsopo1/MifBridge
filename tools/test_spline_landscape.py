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

    # NAME THE LANDSCAPE, rather than letting the endpoint pick one.
    #
    # Every landscape call here omitted `landscape`, so FindLandscape(World, "") returned whichever
    # ALandscape the engine iterated first. In the scratch level these sweeps run in, the only
    # landscape present is usually one ANOTHER suite created and left - test_landscape_heightmap and
    # test_landscape_info both build one - so this suite deformed, asserted against, and then
    # rewrote a fixture it did not own. Its own docstring already admitted "the neighbouring
    # heightmap suite leaves its generated terrain in place".
    #
    # Worse than the mutation: create_landscape leaves edit layers OFF while the project's real
    # landscape has them, and the S101 branch below is chosen by exactly that - so whose landscape
    # got adopted silently decided which half of this suite ran.
    _pick = M.pick_adoptable(M.call("landscape_info", {}).get("landscapes"))
    TARGET = (_pick or {}).get("actorPath")
    if TARGET:
        print("  targeting the level's own landscape: %s" % (_pick.get("label") or TARGET))
    else:
        print("  no non-scratch landscape in this level - falling back to the engine's choice")

    def _land(payload=None):
        """A payload naming TARGET, so every call in this suite addresses the SAME landscape."""
        d = dict(payload or {})
        if TARGET:
            d.setdefault("landscape", TARGET)
        return d

    probe = M.raw_post("export_landscape_heightmap", _land())
    if probe.get("ok") is not True:
        # A PRECONDITION, NOT A PASS. With no landscape every assertion below is about nothing.
        print("SKIPPED - no landscape in the open level, so NOTHING was verified.")
        print("  (%s)" % (probe.get("error") or "")[:160])
        return 2

    st = int(time.time()) % 100000
    bp = "/Game/_MifSpline/BP_Spline%d" % st
    saved = None
    edit_layers = []
    payload = {}
    try:
        # ------------------------------------------------------------------ S100 the fixture
        print("\n=== S100: a spline actor, built rather than found ===")
        before = M.raw_post("export_landscape_heightmap", _land({"asData": True}))
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
        # `compile`, not `compile_blueprint` - there is no such endpoint, so this call was
        # refused every run and the blueprint was never recompiled before the spawn below
        # took its generated class. Fire-and-forget, so nothing went red. Found 2026-08-31
        # by checking suite call sites against the MIF_BIND list.
        M.raw_post("compile", {"blueprintId": bid})

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

        # GIVE THE SPLINE A SHAPE. add_component makes one with UE's default two points 100 units
        # apart, and the suite used to leave it that way - so S101 asked the endpoint to carve a
        # 100uu line with a 400uu brush across a 12600uu landscape, got verticesChanged 0, and that
        # zero was the endpoint being honest about a meaningless request. It went unseen because
        # S101 was skipped entirely until landscape_info could report an editLayer name.
        #
        # snapToGround puts the points ON the terrain rather than at z=0 under it, which is what
        # the engine's spline deformation expects. skipPostEditChange because the owning blueprint
        # would otherwise re-run its construction script and rebuild the spline we just set.
        #
        # groundOffset IS LOad-BEARING, not decoration. apply_spline_to_landscape raises the terrain
        # TO the spline. Points snapped exactly onto the surface ask it to move each vertex to where
        # that vertex already is, so verticesChanged is 0 and the endpoint is right to say so - a
        # no-op by construction, and indistinguishable from a broken endpoint if you did not know
        # the spline was flush. Lifting it 600uu gives the deformation something to actually do.
        pts = [{"x": sx + 2000.0, "y": sy + 2000.0, "z": 0.0},
               {"x": sx + 4000.0, "y": sy + 2000.0, "z": 0.0},
               {"x": sx + 6000.0, "y": sy + 2000.0, "z": 0.0}]
        sp = M.raw_post("set_spline_points", {"actorPath": actor, "component": "Spline",
                                              "points": pts, "space": "world",
                                              "pointType": "linear", "snapToGround": True,
                                              "groundOffset": 600.0,
                                              "skipPostEditChange": True})
        check("S100 (setup) the spline is given real points spanning the landscape",
              sp.get("ok") is True, json.dumps(sp)[:280])

        # ---------------------------------------------------------------- S100b skippedPostEditChange
        # THE FIELD THAT SAYS WHETHER THE ACTOR-WIDE REBUILD RAN, and until now nothing read it -
        # including this suite, which has passed skipPostEditChange:True since it was written and
        # never checked that the endpoint agreed.
        #
        # It matters because PostEditChange() re-runs the owning actor's construction script, and on
        # every DDS2 blueprint that rebuilds its own spline - BP_CarRoadSpline, BP_SplineSidewalk,
        # BP_QuestNPCWalkPath, BP_SegmentedPathTaskMarker - that DISCARDS the points just written.
        # The call still reports pointCount:N and an immediate read-back returns 2. This flag is the
        # only thing in the response that distinguishes the two cases.
        #
        # BOTH DIRECTIONS, because a field hardcoded to the value this suite happens to send would
        # pass a one-sided check forever. The scratch blueprint here has no construction script that
        # rebuilds the spline, so what is asserted is the REPORTING, not the discard - naming the
        # blueprints above rather than pretending this reaches them.
        check("S100b the response reports the skip that was requested",
              sp.get("skippedPostEditChange") is True,
              "asked skipPostEditChange:True, response says %r" % sp.get("skippedPostEditChange"))
        check("S100b and pointCount is read back from the component, matching what was sent",
              sp.get("pointCount") == len(pts),
              "pointCount=%s pointsRequested=%s sent=%d"
              % (sp.get("pointCount"), sp.get("pointsRequested"), len(pts)))
        check("S100b and pointsRequested reports the INTENT alongside it",
              sp.get("pointsRequested") == len(pts),
              "pointsRequested=%s sent=%d" % (sp.get("pointsRequested"), len(pts)))

        # The other direction. Safe on this actor precisely because its construction script does not
        # rebuild the spline - on one of the blueprints named above this call is what loses the work.
        nopec = M.raw_post("set_spline_points", {"actorPath": actor, "component": "Spline",
                                                 "points": pts, "space": "world",
                                                 "pointType": "linear", "snapToGround": True,
                                                 "groundOffset": 600.0})
        check("S100b without the flag the endpoint says it did NOT skip",
              nopec.get("ok") is True and nopec.get("skippedPostEditChange") is False,
              "ok=%r skippedPostEditChange=%r - a value that never changes is not a report"
              % (nopec.get("ok"), nopec.get("skippedPostEditChange")))
        check("S100b and the points survived here, as they must on a blueprint whose construction "
              "script does not rebuild its spline",
              nopec.get("pointCount") == len(pts),
              "pointCount=%s after PostEditChange ran" % nopec.get("pointCount"))

        # Put the actor back the way S101 needs it: with the skip, so nothing downstream inherits a
        # spline that a construction script may have touched.
        sp = M.raw_post("set_spline_points", {"actorPath": actor, "component": "Spline",
                                              "points": pts, "space": "world",
                                              "pointType": "linear", "snapToGround": True,
                                              "groundOffset": 600.0,
                                              "skipPostEditChange": True})
        check("S100b (restore) the spline is back to the S101 precondition",
              sp.get("ok") is True and sp.get("pointCount") == len(pts), json.dumps(sp)[:220])

        # ------------------------------------------------------------------ S101 the write
        print("\n=== S101: the deformation, counted from the heightfield ===")

        # THE PRECONDITION, ESTABLISHED FROM A READ RATHER THAN GUESSED. A landscape with edit
        # layers makes the writer refuse unless told which layer to write, correctly - the engine
        # would log an error and change nothing. Until landscape_info reported editLayers[] there
        # was no way to learn a valid name, and this suite skipped S101/S102 rather than guess.
        #
        # Feeding the reader's output straight into the writer is also the strongest proof that
        # the new field is RIGHT: a name this suite invented could match by luck, a name read from
        # landscape_info either resolves in the engine or the deformation refuses.
        info = M.call("landscape_info", {})
        lrow = next((x for x in (info.get("landscapes") or [])
                     if x.get("actorPath") == land or x.get("label") == land), None)
        if lrow is None:
            # THE FALLBACK IS AN ADOPTION, and it survived the 2026-09-02 sweep of this suite
            # because the line above LOOKS like an exact-identity lookup. It is - until it misses,
            # and then this takes whatever is first, which on the second pass of a sweep is another
            # suite's leftover create_landscape output. S101 would then read editLayers[] off a
            # landscape this suite never touched.
            lrow = M.pick_adoptable(info.get("landscapes")) or {}
        edit_layers = lrow.get("editLayers")
        check("S101 landscape_info reports editLayers[] at all - the read that apply_spline's own "
              "refusal tells you to make",
              isinstance(edit_layers, list), json.dumps(lrow)[:260])
        check("S101 and it says which list it is, because `layers` next to it is the unrelated "
              "paint/weightmap one",
              "editLayers[]" in (lrow.get("editLayersNote") or "")
              or "no sculpt edit layers" in (lrow.get("editLayersNote") or ""),
              json.dumps(lrow.get("editLayersNote"))[:260])

        payload = {"splineActor": actor, "startWidth": 400, "endWidth": 400}
        if edit_layers:
            # Prefer one that can actually be written: a locked layer is refused by the engine.
            usable = [e for e in edit_layers if not e.get("locked")] or edit_layers
            picked = usable[0].get("name")
            check("S101 every reported edit layer is NAMED - a nameless entry would be unusable "
                  "as the parameter this exists to supply",
                  all(e.get("name") for e in edit_layers), json.dumps(edit_layers)[:260])
            print("  editLayers: %s -> passing %r"
                  % ([e.get("name") for e in edit_layers], picked))
            payload["editLayer"] = picked
        else:
            print("  this landscape has NO edit layers, so no editLayer is passed")

        r = M.raw_post("apply_spline_to_landscape", _land(payload))

        # THE FIXTURE, ASSERTED BEFORE ITS RESULT IS TRUSTED. Without this a spline too short to
        # move anything reports verticesChanged 0 and reads as an endpoint defect - the suite
        # cannot fail, it can only mislead. 4000uu against a 400uu brush is comfortably enough to
        # move vertices on any landscape whose resolution this endpoint supports.
        check("S101 (fixture) the spline the endpoint measured is long enough for the brush to "
              "reach any vertices at all - a 100uu default would make verticesChanged:0 mean "
              "nothing about the endpoint",
              (r.get("splineLength") or 0) > 1000,
              "splineLength=%s with startWidth 400 - the FIXTURE is at fault here, not "
              "apply_spline_to_landscape" % r.get("splineLength"))

        if r.get("ok") is False and "edit layers" in (r.get("error") or ""):
            # Only reachable if the name landscape_info gave does not resolve in the engine, which
            # would mean the new read is WRONG rather than missing. Worth failing loudly for.
            check("S101 the editLayer name taken from landscape_info RESOLVES in the writer - if "
                  "it did not, the new read would be reporting names the engine does not have",
                  False, json.dumps(r)[:300])
            deformed = False
        else:
            deformed = r.get("ok") is True
            check("S101 apply_spline_to_landscape succeeds", r.get("ok") is True,
                  json.dumps(r)[:280])
        # THE COUNT AND THE WORLD MUST AGREE, and this is the assertion that caught the real bug.
        #
        # verticesChanged was 0 while a re-export one second later differed from the export taken
        # beforehand. Neither number is suspicious alone - 0 is a legitimate answer for a spline
        # that changes nothing - but the CONTRADICTION could only mean the endpoint sampled before
        # the edit layer composite had landed, which is exactly what it was doing.
        #
        # Waiting first, on purpose: a settled re-read is the honest comparison, and if the endpoint
        # ever regresses to sampling early this goes red instead of the vaguer assertions below.
        if r.get("ok") is True:
            time.sleep(2)
            settled = M.raw_post("export_landscape_heightmap", {"asData": True})
            moved = settled.get("data") != before.get("data")
            counted = (r.get("verticesChanged") or 0) > 0
            check("S101 verticesChanged AGREES with an independent settled re-export - a 0 beside "
                  "a changed heightmap means the endpoint measured before the edit layer composite "
                  "landed, and its whole purpose is that EditorApplySpline returns void",
                  counted == moved,
                  "verticesChanged=%s (counted=%s) but the settled heightmap moved=%s"
                  % (r.get("verticesChanged"), counted, moved))

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
        #
        # On a landscape with EDIT LAYERS it cannot be put back this way, and the endpoint now says
        # so instead of pretending. import_landscape_heightmap writes the merged heightmap with no
        # editing-layer scope, so the next composite regenerates it from the layers and discards the
        # write - ok:true, zero mismatches, and byte-identical to the pre-import state two seconds
        # later. This cleanup used to read that ok:true and report a restore that never happened.
        if saved:
            # _land() here too: the restore MUST go back to the same landscape the deform hit.
            back = M.raw_post("import_landscape_heightmap", _land({"file": saved}))
            if edit_layers:
                check("(cleanup) restoring a LAYERED landscape by heightmap import is REFUSED "
                      "rather than silently reverted a moment later",
                      back.get("ok") is False, json.dumps(back)[:250])
                check("(cleanup) and the refusal names the edit layers, so the caller is not left "
                      "guessing which landscape state blocked it",
                      "EDIT LAYERS" in (back.get("error") or ""), (back.get("error") or "")[:260])
                print("  NOTE  the terrain is left deformed. The deformation went INTO the "
                      "'%s' edit" % payload.get("editLayer"))
                print("        layer, which persists, and the only writer that could undo it is the")
                print("        one just refused. This is an unsaved /Temp map, so it goes away with")
                print("        the editor. Reported rather than worked around.")
            else:
                check("(cleanup) the landscape is restored from the export taken before",
                      back.get("ok") is True, json.dumps(back)[:250])
        SC.confirm_call("delete_asset", {"path": bp})

    # ---------------------------------------------------------------- S110 the control
    print("\n=== S110: sculpt_landscape - the control this endpoint's zero-change note cites ===")
    # WHY IT IS HERE. apply_spline_to_landscape's zero-change note argues that NOT ONE height sample
    # changing is a real result rather than a dead harness, and its evidence is another endpoint:
    # "sculpt_landscape moved 736 vertices through the same interface in the same session".
    # audit_cross_endpoint_claims flagged that as compared by NO suite - this suite mentioned
    # sculpt_landscape only in its docstring - so the argument rested on a number nobody re-checked.
    #
    # Measured by hand 2026-09-05 on a purpose-built landscape: 335 samples moved. The assertion
    # here is NON-ZERO rather than any particular count, because the count is a function of radius,
    # falloff and the landscape's resolution and would be a brittle thing to pin.
    #
    # IT DEFORMS THE LANDSCAPE, to the same standard as the spline writes above: the level is not
    # saved, so the change dies with the editor. That is the whole suite's existing bargain, not a
    # new one taken here.
    sc = M.raw_post("sculpt_landscape", _land({"center": {"x": 0, "y": 0}, "radius": 2000,
                                               "mode": "raise", "amount": 250, "falloff": 0.5}))
    check("S110 sculpt_landscape answers on the same landscape this suite drives",
          sc.get("ok") is True, json.dumps(sc)[:250])
    if sc.get("ok") is True:
        touched = sc.get("verticesTouched")
        check("S110 and it moves a NON-ZERO number of height samples, which is the evidence "
              "apply_spline_to_landscape's zero-change note stands on",
              isinstance(touched, (int, float)) and touched > 0,
              "verticesTouched=%r; keys=%s" % (touched, sorted(sc.keys())))
        # THE WARNING IS PART OF THE ANSWER. On a landscape WITH edit layers this endpoint writes
        # the merged heightmap and says the write may be discarded by the next composite - so a
        # non-zero count is not by itself proof the terrain kept the change.
        if sc.get("editLayerWarning"):
            print("  NOTE  this landscape HAS edit layers, so sculpt_landscape warns its write may "
                  "be discarded by the next composite - the count above is what it touched, not "
                  "what survived.")
            print("        %s" % str(sc.get("editLayerWarning"))[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
