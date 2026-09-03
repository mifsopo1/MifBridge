"""The PIE-family sweep: list_pie_actors, list_live_widgets, describe_live_widget, move_actor_to,
ui_scenario_start/activate/status/capture/stop, pie_load_level_instance, pie_unload_level_instance.

Reopened 2026-08-28 once PIE was authorised, same as MifBridgeGameFramework.cpp's own reopening earlier
the same night. coverage_gaps.py (refreshed via refresh_endpoints_snapshot.py after finding it 14
endpoints stale) flagged these 11 as never named in ANY test suite - not because anyone forgot them, but
because every one of them either drives a running game (list_pie_actors, move_actor_to,
ui_scenario_*, pie_load/unload_level_instance) or reads state that only exists once one is (list_live_
widgets, describe_live_widget against real HUD instances), and the standing no-PIE rule made all of that
untestable until now.

TWO ENDPOINTS ARE IN mifaudit's OWN DENY LIST (pie_load_level_instance, pie_unload_level_instance -
"long-running or blocking; PIE in particular defers to the game thread") on top of the usual start_pie/
stop_pie - the same documented, deliberate, narrowly-scoped M.raw_post bypass this project has used all
night, not a new pattern.

ui_scenario_start/ui_scenario_activate both REQUIRE confirm:true - M.call's own guarded_payload silently
STRIPS the "confirm" key (mifaudit.py's FORBIDDEN_KEYS, a guard against a blind fuzz sweep accidentally
confirming something) so both calls go through M.raw_post directly, exactly like sequencer's
add_sequence_possessable/add_sequence_track already do for the same reason.

T1600: the no-PIE refusal, checked before starting PIE at all - move_actor_to names the real reason
(needs a running PIE session, AI controllers only exist at runtime), not a generic error.

T1601-T1604: list_pie_actors against a real 130+-actor PIE world, then list_live_widgets /
describe_live_widget against the game's own real HUD widgets (MainPlayerHUD, not anything this test
spawned) - the exact live data the report that motivated MifBridgeLiveWidgets.cpp was written for.

T1605-T1606: move_actor_to - reads the player pawn's location from list_pie_actors BEFORE the call,
issues a real AI SimpleMoveToLocation, waits, and reads it again - ok:true alone would only prove the
call was accepted, not that anything moved. A REAL FINDING, checked rather than assumed either way: in
this project's MifBridge test sandbox level (Untitled_1), the pawn's location never changes at all, even
a fraction of a unit, over several seconds. Confirmed the actual root cause by reading
UAIBlueprintHelperLibrary::SimpleMoveToLocation (AIBlueprintHelperLibrary.cpp) rather than guessing: it
resolves a real UPathFollowingComponent for ANY controller (not just AAIController - it builds one on
the fly via InitNavigationControl if the controller doesn't already have one), so this is NOT a
"wrong controller type" problem. The move request genuinely reaches the engine's navigation system and
is declined there because this sandbox level has no navigation data to path across - a property of the
TEST LEVEL, not a defect in this endpoint. move_actor_to's own contract (resolve the actor, resolve its
controller, call SimpleMoveToLocation) is verified correct up to the boundary this bridge can observe;
genuine physical movement is NOT independently verified here because no navmeshed level was available
to test it against without loading a full production map (out of scope for this sweep).

T1606 originally tried to DETECT the no-navigation-data case via list_pie_actors
{classFilter:"NavMeshBoundsVolume"} count==0, treating a nonzero count as proof pathing SHOULD work.
Live-caught being wrong, in both directions, by run_all_suites.py's own double-pass sweep (2026-08-29):
a completely unrelated suite (test_uncovered_reads7.py's T958) had been leaving a NavMeshBoundsVolume
behind in the persistent EDITOR world on every run with no cleanup, so the count was nonzero here too -
even though that volume was parked a million units from the pawn and provided no real coverage. Fixed
the leak at its source (test_uncovered_reads7.py now deletes it), but ALSO stopped trusting the count
either way here: a volume's mere existence was never proof a NavMesh was actually built inside it
either. T1606 now just reports what happened (moved, or didn't) rather than gating pass/fail on a proxy
signal that turned out unreliable in both directions.

T1607-T1611: THE FULL ui_scenario STATE MACHINE, live, end to end - position the real player pawn near a
real StaticMeshActor, deliver a real 'F' keypress through UGameViewportClient::InputKey (not Slate's
generic focused-widget routing), poll ui_scenario_status until the state machine reports READY on its
own, capture the game viewport to a real PNG, and independently verify the file exists on disk with a
real size rather than trusting wroteFile:true alone.

T1612-T1613: pie_load_level_instance / pie_unload_level_instance against a real small sublevel
(DDS2Casino's OldBoss_Office, tempPackage:true, parked at z=5000 so it cannot visually or physically
interfere with the main level) - independently verified via list_sublevels{world:"pie"} on both ends,
not just the load/unload calls' own ok:true.

T1614-T1620: refusals checked for the specific reason - ui_scenario_start/activate with confirm omitted,
an unknown target actor, activate/capture called out of sequence, an unknown streaming-level instance
name on unload, and the nameOverride collision guard on load (verified live: loading the SAME
nameOverride twice in a row is refused, naming the colliding package).
"""
import json
import sys
import time

import mifaudit as M


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def wait_for_pie_state(target, timeout=60):
    # Moved into mifaudit 2026-08-30 so the two other suites that had their own unfixed copies of
    # this loop share the fix instead of each carrying the bug. The reasoning that produced it is
    # preserved in full at mifaudit.wait_for_pie_state.
    return M.wait_for_pie_state(target, timeout=timeout)

def wait_for_sublevel(instance_name, want_present, timeout=30, want_state=None):
    def check_once():
        r = M.call("list_sublevels", {"world": "pie", "netMode": "server"})
        entry = next((s for s in r.get("sublevels", []) if s.get("packageName") == instance_name), None)
        hit = entry is not None
        if want_state is not None:
            settled = hit and entry.get("state") == want_state
        else:
            settled = hit == want_present
        return r, hit, settled

    start = time.time()
    r, hit, settled = check_once()
    while not settled and time.time() - start < timeout:
        time.sleep(1)
        r, hit, settled = check_once()
    return r, hit


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # PIE needs full write mode - the gate refuses start_pie in scratch/read, in the dispatcher,
    # before the handler runs. SKIP (exit 2) rather than report failures the gate caused correctly.
    if M.needs_full_write_mode("test_pie_family.py"):
        return 2

    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T1600 no-PIE refusal
    print("\n=== T1600: move_actor_to refuses cleanly outside PIE, before anything else is tried ===")
    no_pie = M.call("move_actor_to", {"actorPath": "whatever", "location": {"x": 0, "y": 0, "z": 0}})
    check("T1600 move_actor_to refuses with no PIE running", no_pie.get("ok") is False, no_pie)
    check("T1600 the refusal names the real reason (AI controllers only exist at runtime)",
          "runtime" in (no_pie.get("error") or "").lower(), no_pie.get("error"))

    # ------------------------------------------------------------------ start PIE for everything else
    # EVERYTHING PAST HERE RUNS INSIDE A try/finally, and the finally stops PIE. Without it any
    # failure between start_pie and the cleanup at the bottom - an exception, a missing key, the
    # `return 3` two lines down - leaves the editor in a PLAY SESSION for whatever runs next, and
    # for whoever is sitting in front of it. Same shape as the current-level contamination fixed in
    # reads7, with a worse blast radius: a stranded play session changes what every later read sees
    # AND takes the editor away from the person using it.
    #
    # The spec entry that reopened this family asked for exactly this - "starts PIE inside a try and
    # stops it in a finally, and asserts pie_status is back to state==stopped" - and the suite had
    # been written without it. Found by reading the file before running it against Andre's live
    # editor, which is why it cost nothing instead of costing him a session.
    # T1610: the multiplayer-only options are refused on a single-player session rather than
    # silently ignored. oneProcess/width/height are read INSIDE the multiplayer block, so
    # {"width":1280} with players=1 and no netMode opened a window at the editor's own size and the
    # response did not even echo the value back to disagree with.
    #
    # RUN BEFORE THE REAL start_pie, INSIDE THE SAME try, deliberately. This asserts that NOTHING
    # STARTED, and the failure mode if the guard is broken is a PIE session nobody asked for - so it
    # sits where the finally below will stop it, rather than in a suite that never expects PIE.
    print("\n=== T1610: multiplayer-only options are refused on a single-player session ===")
    try:
        mp = M.raw_post("start_pie", {"width": 1280, "height": 720})
        check("T1610 width/height on a single-player session are refused",
              mp.get("ok") is False, json.dumps(mp)[:220])
        check("T1610 and the refusal says HOW to make it multiplayer, not just that it is not",
              "players > 1" in (mp.get("error") or "") and "netMode" in (mp.get("error") or ""),
              (mp.get("error") or "")[:240])
        # The postcondition is the assertion that matters: a handler that refused and started
        # anyway would satisfy both checks above.
        after = M.raw_post("pie_status", {}).get("state")
        check("T1610 and NOTHING was started - judged by pie_status, not by the error string",
              after in ("stopped", "none", None, ""), "pie_status.state=%r" % after)
    except M.Timeout:
        check("T1610 start_pie answered the refusal rather than hanging", False,
              "start_pie did not respond - the refusal path must not reach RequestPlaySession")
    finally:
        # NOTHING SHOULD BE RUNNING HERE - the call above is supposed to refuse before
        # RequestPlaySession is ever reached. This finally exists for the case where it does NOT,
        # which is the exact failure this test was written to catch, and a test that catches a
        # stranded-session bug by stranding a session is not worth having. audit_suite_teardown
        # flagged the first draft for having no finally at all and it was right to: it cannot know
        # a start_pie is meant to be refused, and neither can the next person to edit this.
        _st = (M.raw_post("pie_status", {}) or {}).get("state")
        if _st not in ("stopped", "none", None, ""):
            print("  NOTE  the guard let a session start; stopping it.")
            M.raw_post("stop_pie", {})
            _back = wait_for_pie_state("stopped")
            check("T1610 the session that broken guard started was stopped again",
                  _back.get("state") == "stopped", _back)

    try:
        started = M.raw_post("start_pie", {})
        check("(setup) start_pie accepted", started.get("ok") is True, started)
        running_status = wait_for_pie_state("running")
        check("(setup) PIE actually reached state=running", running_status.get("state") == "running", running_status)
        if running_status.get("state") != "running":
            print("cannot continue without a running PIE session")
            return 3

        # ------------------------------------------------------------------ T1601 list_pie_actors
        print("\n=== T1601: list_pie_actors against the real running PIE world ===")
        actors = M.call("list_pie_actors", {"netMode": "server", "limit": 500})
        check("T1601 list_pie_actors succeeds", actors.get("ok") is True, json.dumps(actors)[:200])
        check("T1601 it reports a real, non-trivial actor count", actors.get("count", 0) > 50, actors.get("count"))

        target_actor = None
        for a in actors.get("actors", []):
            cls = a.get("class", "")
            if "StaticMeshActor" in cls:
                target_actor = a
                break
        check("T1601 a real StaticMeshActor was found to use as a scenario target later", target_actor is not None,
              "none found among %d actors" % actors.get("count", 0))

        pawn_before = None
        for a in actors.get("actors", []):
            if "PlayerCharacter" in a.get("class", ""):
                pawn_before = a
                break
        check("T1601 the real player pawn is present in the actor list", pawn_before is not None, actors.get("count"))

        # ------------------------------------------------------------------ T1602-T1603 live widgets
        print("\n=== T1602-T1603: list_live_widgets / describe_live_widget against the game's own real HUD ===")
        widgets = M.call("list_live_widgets", {"netMode": "server"})
        check("T1602 list_live_widgets succeeds", widgets.get("ok") is True, json.dumps(widgets)[:200])
        check("T1602 it reports at least one real top-level widget (the game's own HUD)",
              widgets.get("count", 0) > 0, widgets.get("count"))
        hud = None
        for w in widgets.get("widgets", []):
            if "HUD" in w.get("class", ""):
                hud = w
                break
        if hud is None and widgets.get("widgets"):
            hud = widgets["widgets"][0]
        check("T1602 (setup) a widget to describe was found", hud is not None, widgets.get("widgets"))

        if hud:
            desc = M.call("describe_live_widget", {"path": hud["path"]})
            check("T1603 describe_live_widget succeeds against a real instance path", desc.get("ok") is True,
                  json.dumps(desc)[:200])
            check("T1603 it returns a real nested tree, not an empty shell",
                  bool(desc.get("tree", {}).get("class")), json.dumps(desc)[:300])

        unknown_widget = M.call("describe_live_widget", {"path": "/Temp/NoSuchWidgetInstance"})
        check("T1603b an unknown widget instance path is refused", unknown_widget.get("ok") is False, unknown_widget)

        # ------------------------------------------------------------------ T1604-T1606 move_actor_to, verified
        print("\n=== T1604-T1606: move_actor_to - verified by reading real location before/after, not just ok:true ===")
        pawn_path = pawn_before.get("actorPath")
        loc_before = pawn_before.get("location", {})
        goal = {"x": loc_before.get("x", 0) + 800, "y": loc_before.get("y", 0) + 800, "z": loc_before.get("z", 0)}

        moved = M.call("move_actor_to", {"actorPath": pawn_path, "location": goal})
        check("T1604 move_actor_to accepts a real pawn path and goal", moved.get("ok") is True, json.dumps(moved)[:200])
        check("T1604 it reports moving:true", moved.get("moving") is True, moved)
        check("T1604 it reports the real controller class actually driving this pawn",
              moved.get("controller") == "BP_DDS2_PlayerController_C", moved)

        time.sleep(3)
        actors_after = M.call("list_pie_actors", {"netMode": "server", "limit": 500})
        pawn_after = None
        for a in actors_after.get("actors", []):
            if a.get("actorPath") == pawn_path:
                pawn_after = a
                break
        check("T1605 (setup) the pawn is still findable after the move", pawn_after is not None, pawn_path)
        if pawn_after:
            loc_after = pawn_after.get("location", {})
            dx = abs(loc_after.get("x", 0) - loc_before.get("x", 0))
            dy = abs(loc_after.get("y", 0) - loc_before.get("y", 0))
            # NOT gated on "does a NavMeshBoundsVolume exist" any more - that proxy was live-caught being
            # wrong TWICE, in opposite directions, both found by run_all_suites.py's own double-pass sweep
            # (2026-08-29): a leftover volume from an UNRELATED suite (test_uncovered_reads7.py's T958,
            # which spawned one straight into the persistent EDITOR world with no cleanup, so it survived
            # every later PIE session) made "count == 0" false here even though it provided no real
            # coverage - parked a million units from the pawn. And a volume genuinely covering the pawn
            # still would not prove pathing works, because a bounds volume existing is not the same as a
            # NavMesh actually having been BUILT inside it. Existence was never the right question either
            # direction. This world's actual navigation state is genuinely unknown to this test without a
            # dedicated navmesh-build step this suite does not perform (out of scope - PIE-family is about
            # exercising the endpoints, not authoring a level's navigation), so the honest thing is to
            # report what happened, not guess whether it SHOULD have.
            # WHAT IS ASSERTED HERE, corrected 2026-08-30. The branch below used to read
            #     check("T1606 the pawn's location genuinely changed ...", True, ...)
            # - a literal True as the condition, so it could not fail, while the else branch asserted
            # nothing at all. Between them move_actor_to had no failing postcondition in either
            # direction, and the unfailable one still counted as a PASS, which inflates the suite's own
            # score with a check that verifies nothing. That is precisely the shape audit_vacuous_checks
            # exists to find, written by hand.
            #
            # The reasoning that produced it was sound - this world's navigation state is genuinely
            # unknown, so FAILING on "did not move" would be a false negative. The fix is not to assert
            # movement; it is to stop pretending an observation is an assertion. Both outcomes are now
            # reported as notes, and what IS deterministic gets a real check: the location read-back
            # itself, which would break silently if list_pie_actors ever stopped reporting location.
            check("T1606 the pawn's location is readable both before and after the move - the "
                  "instrumentation this whole check depends on actually works",
                  isinstance(loc_before.get("x"), (int, float)) and isinstance(loc_after.get("x"), (int, float)),
                  "before=%s after=%s" % (loc_before, loc_after))
            if (dx + dy) > 5.0:
                print("  NOTE  T1606 pawn location changed by %.1f units after move_actor_to - pathing "
                      "worked in this world. Reported, not asserted: the opposite outcome is equally "
                      "legitimate here (see below) and a check that passes either way is not a check."
                      % (dx + dy))
            else:
                print("  NOTE  T1606 pawn location did not change after move_actor_to - this world's "
                      "navigation state (a built NavMesh actually covering this location) is not something "
                      "this suite verifies, so this is reported rather than failed. The endpoint's own "
                      "contract (resolve the actor, resolve its controller, issue the real engine call) is "
                      "still proven by T1604 above regardless of whether pathing itself succeeds here.")

        unknown_actor = M.call("move_actor_to", {"actorPath": "/Temp/NoSuchWorld.NoSuchActor",
                                                  "location": {"x": 0, "y": 0, "z": 0}})
        check("T1606b move_actor_to refuses an unknown actor path", unknown_actor.get("ok") is False, unknown_actor)

        # ------------------------------------------------------------------ T1607-T1611 the full ui_scenario pipeline
        print("\n=== T1607-T1611: the full ui_scenario state machine, live, end to end ===")
        no_confirm = M.call("ui_scenario_start", {
            "targetActorPath": target_actor["actorPath"], "playerLocation": {"x": 0, "y": 0, "z": 200}})
        check("T1607a ui_scenario_start without confirm is refused (M.call strips the key on purpose)",
              no_confirm.get("ok") is False, no_confirm)

        bad_target = M.raw_post("ui_scenario_start", {
            "targetActorPath": "/Temp/NoSuchActor", "playerLocation": {"x": 0, "y": 0, "z": 200}, "confirm": True})
        check("T1607b an unknown targetActorPath is refused", bad_target.get("ok") is False, bad_target)

        scen_start = M.raw_post("ui_scenario_start", {
            "targetActorPath": target_actor["actorPath"], "playerLocation": {"x": 0, "y": 0, "z": 200}, "confirm": True})
        check("T1607 ui_scenario_start succeeds against a real target actor", scen_start.get("ok") is True,
              json.dumps(scen_start)[:250])
        check("T1607 state moves to POSITIONED", scen_start.get("status", {}).get("state") == "POSITIONED", scen_start)

        early_capture = M.call("ui_scenario_capture", {})
        check("T1607c capturing before the scenario is READY is refused", early_capture.get("ok") is False, early_capture)

        scen_activate = M.raw_post("ui_scenario_activate", {"activationKey": "F", "timeoutSeconds": 8,
                                                             "stableFrames": 3, "confirm": True})
        check("T1608 ui_scenario_activate delivers a real keypress and succeeds", scen_activate.get("ok") is True,
              json.dumps(scen_activate)[:250])
        check("T1608 state moves to WAITING_FOR_STABLE_UI",
              scen_activate.get("status", {}).get("state") == "WAITING_FOR_STABLE_UI", scen_activate)

        settled = None
        for _ in range(12):
            time.sleep(1)
            settled = M.call("ui_scenario_status", {})
            if settled.get("state") != "WAITING_FOR_STABLE_UI":
                break
        check("T1609 polling ui_scenario_status reaches a terminal state on its own", settled is not None
              and settled.get("state") in ("READY", "TIMED_OUT", "FAILED"), settled)
        check("T1609 the state machine settled at READY specifically", settled is not None
              and settled.get("state") == "READY", settled)

        if settled and settled.get("state") == "READY":
            cap_name = "MifPieFamilyTest%d" % st
            capture = M.call("ui_scenario_capture", {"name": cap_name})
            check("T1610 ui_scenario_capture succeeds", capture.get("ok") is True, json.dumps(capture)[:250])
            check("T1610 it reports wroteFile:true and exists:true", capture.get("wroteFile") is True
                  and capture.get("exists") is True, capture)
            check("T1610 it reports a real, non-trivial resolution", capture.get("width", 0) > 100
                  and capture.get("height", 0) > 100, capture)
            import os
            real_path = capture.get("path", "")
            check("T1610 the PNG genuinely exists on disk with real bytes, independently of the endpoint's "
                  "own exists:true claim", os.path.isfile(real_path) and os.path.getsize(real_path) > 1000,
                  real_path)

        scen_stop = M.call("ui_scenario_stop", {})
        check("T1611 ui_scenario_stop succeeds", scen_stop.get("ok") is True, scen_stop)
        check("T1611 it reports wasActive:true", scen_stop.get("wasActive") is True, scen_stop)
        status_after_stop = M.call("ui_scenario_status", {})
        check("T1611 status afterward reports STOPPED / inactive", status_after_stop.get("active") is False, status_after_stop)

        stop_when_idle = M.call("ui_scenario_stop", {})
        check("T1611b stopping again when nothing is active is a harmless no-op, not an error",
              stop_when_idle.get("ok") is True and stop_when_idle.get("wasActive") is False, stop_when_idle)

        # ------------------------------------------------------------------ T1612-T1613 level instance streaming
        print("\n=== T1612-T1613: pie_load_level_instance / pie_unload_level_instance, verified via list_sublevels ===")
        source_path = "/DDS2Casino/Sub_Levels/OldBoss_Office"
        name_override = "MifPieFamily%d" % st
        instance_name = "/Temp/DDS2Casino/Sub_Levels/UEDPIE_0_%s" % name_override

        load1 = M.raw_post("pie_load_level_instance", {
            "path": source_path, "location": {"x": 0, "y": 0, "z": 5000}, "nameOverride": name_override,
            "tempPackage": True, "visible": True, "netMode": "server"})
        check("T1612 pie_load_level_instance accepts a real sublevel", load1.get("ok") is True, json.dumps(load1)[:250])
        check("T1612 it reports the expected instanceName", load1.get("instanceName") == instance_name,
              "got=%s want=%s" % (load1.get("instanceName"), instance_name))

        sub_after_load, present = wait_for_sublevel(instance_name, True, want_state="LoadedVisible")
        check("T1612 independently verified via list_sublevels: the instance actually finished loading with "
              "real actors in it, not just requested:true", present, json.dumps(sub_after_load)[:300])
        if present:
            entry = next(s for s in sub_after_load["sublevels"] if s.get("packageName") == instance_name)
            check("T1612 the loaded instance reports state LoadedVisible with a real actorCount",
                  entry.get("state") == "LoadedVisible" and entry.get("actorCount", 0) > 0, entry)

        collide = M.raw_post("pie_load_level_instance", {
            "path": source_path, "location": {"x": 0, "y": 0, "z": 6000}, "nameOverride": name_override,
            "tempPackage": True})
        check("T1612b loading the SAME nameOverride again is refused with the colliding package named",
              collide.get("ok") is False and name_override in (collide.get("error") or ""), collide)

        unload1 = M.raw_post("pie_unload_level_instance", {"instanceName": instance_name, "netMode": "server"})
        check("T1613 pie_unload_level_instance accepts the real instance name", unload1.get("ok") is True,
              json.dumps(unload1)[:250])

        sub_after_unload, still_present = wait_for_sublevel(instance_name, False)
        check("T1613 independently verified via list_sublevels: the instance is genuinely gone after teardown, "
              "not just requested:true", not still_present, json.dumps(sub_after_unload)[:300])

        unload_unknown = M.raw_post("pie_unload_level_instance", {"instanceName": "/Temp/NoSuchInstanceAtAll"})
        check("T1613b unloading an unknown instance name is refused", unload_unknown.get("ok") is False, unload_unknown)

        # ------------------------------------------------------------------ cleanup
        # NOT the only stop - the finally below stops it too, and this one is kept because it is the
        # only place the CLEAN case can be asserted. stop_pie is idempotent against a stopped session.
        M.raw_post("stop_pie", {})
        stopped_status = wait_for_pie_state("stopped")
        check("(cleanup) PIE stopped cleanly", stopped_status.get("state") == "stopped", stopped_status)

    finally:
        # ui_scenario_stop FIRST, and it is NOT redundant with the stop_pie below. Nothing in the
        # plugin clears GScenario.bActive when PIE ends - there is no EndPIE hook anywhere in it -
        # so ending the play session leaves the bridge still believing a scenario is running, and
        # every later ui_scenario_start is refused with "already active". The scenario's state
        # lives on the BRIDGE side, not in the PIE world, which is exactly why the world dying
        # does not release it. (MifBridgeUIScenario.cpp now reaps a scenario whose world is gone,
        # so this is belt and braces rather than the only defence - but the suite should not
        # depend on a fix in the thing it is testing.)
        #
        # It needs no PIE world of its own: H_ui_scenario_stop never resolves one, and it answers
        # ok with wasActive:false when nothing is active, so calling it unconditionally is safe.
        M.raw_post("ui_scenario_stop", {})

        # UNCONDITIONAL, and it asserts rather than assumes. stop_pie returning ok proves the
        # request was accepted, not that the session ended - so the state is read back, and a
        # session still running after the stop is reported as a FAILURE rather than left silent.
        M.raw_post("stop_pie", {})
        _final = wait_for_pie_state("stopped")
        if (_final or {}).get("state") != "stopped":
            check("(finally) PIE was left RUNNING - the editor is still in a play session", False,
                  _final)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
