"""ModularGameplay: add_game_framework_receiver, add_game_framework_component_request,
remove_game_framework_component_request.

Reopened 2026-08-28 once PIE was authorised. UGameFrameworkComponentManager is a
UGameInstanceSubsystem - unreachable from the plain editor world, which is exactly why this was
declined earlier the same night with a real, specific technical reason (see the spec's own
re-examination entry). Once Andre lifted the standing no-PIE rule and asked directly for live PIE
endpoint testing, the wall that had blocked this was simply gone.

Checked first, not assumed: no base engine Pawn/Character/Controller class calls
AddGameFrameworkComponentReceiver on itself (grepped Engine/Source/Runtime/Engine - zero hits), so the
request/receiver system only affects actors that opt in explicitly - a project pattern (Lyra's, for
example), not an ambient engine feature. Since neither DDS2 nor Curfew has adopted it,
add_game_framework_receiver exists as its own endpoint so a caller can register a specific actor by
hand rather than a request silently matching nothing.

T1400-T1401: the no-PIE refusal, checked BEFORE starting PIE - UGameFrameworkComponentManager genuinely
does not exist outside PIE/a packaged game, and the endpoint says so by name rather than crashing or
returning a confusing engine-internal error.

T1402-T1406: THE REAL FLOW, live, with real PIE and a real spawned actor. Register a scratch
StaticMeshActor as a receiver, request that every StaticMeshActor get an AudioComponent, then
INDEPENDENTLY verify the component actually exists - not via list_components (that tool reads Blueprint
component TEMPLATES, not live instances - checked and confirmed it is the wrong tool for this before
reaching for it) but via list_object_properties at the deterministic sub-object path
CreateComponentOnInstance actually creates (`<ActorPath>.<ComponentClassName>` - read straight from
GameFrameworkComponentManager.cpp's NewObject call, not guessed). Then remove the request and verify
the component is genuinely gone.

T1407: A REAL UE LIFECYCLE NUANCE, live-verified rather than assumed either way. Immediately after
removal, the component's sub-object path is STILL resolvable via list_object_properties -
DestroyComponent() detaches and marks an object transient/pending-kill but does not immediately
deallocate it; FindObject-style path resolution can still find a pending-kill object until an actual
garbage collection pass runs. Forcing one (`run_console {command: "obj gc"}`) makes the object
genuinely unresolvable. This suite checks BOTH states explicitly so this nuance stays documented rather
than silently misread as a bug (or the removal endpoint silently misread as broken).

T1408-T1411: refusals checked for the specific reason - a duplicate requestId while one is still
active, a componentClass that isn't an ActorComponent, removing a requestId that was never created.
"""
import json
import sys
import time

import mifaudit as M


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def wait_for_pie_state(target, timeout=30):
    # ONE definition now lives in mifaudit (2026-08-30). This suite had its own copy whose polls used
    # raw_post's 60s default inside a 30s outer budget, so the budget was never enforced and an
    # expiry raised an uncaught mifaudit.Timeout. That was fixed in test_pie_family.py on 2026-08-29
    # and not here - the copies are the reason, so they are gone.
    return M.wait_for_pie_state(target, timeout=timeout)

def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # PIE needs full write mode - the gate refuses start_pie in scratch/read, in the dispatcher,
    # before the handler runs. SKIP (exit 2) rather than report failures the gate caused correctly.
    if M.needs_full_write_mode("test_game_framework.py"):
        return 2

    st = int(time.time() % 100000)
    request_id = "MifTestRequest%d" % st

    # ------------------------------------------------------------------ T1400-T1401 no-PIE refusal
    print("\n=== T1400-T1401: refuses cleanly outside PIE, before anything else is tried ===")
    no_pie = M.call("add_game_framework_receiver", {"actorPath": "whatever"})
    check("T1400 add_game_framework_receiver refuses with no PIE running", no_pie.get("ok") is False, no_pie)
    check("T1400 the refusal names the real reason (no UGameInstance), not a generic error",
          "UGameInstance" in (no_pie.get("error") or ""), no_pie.get("error"))

    no_pie2 = M.call("add_game_framework_component_request", {
        "receiverClass": "/Script/Engine.StaticMeshActor", "componentClass": "/Script/Engine.AudioComponent"})
    check("T1401 add_game_framework_component_request refuses with no PIE running",
          no_pie2.get("ok") is False, no_pie2)

    # ------------------------------------------------------------------ T1402-T1406 the real flow, live
    print("\n=== T1402-T1406: the real flow - register a receiver, request a component, verify it, remove it ===")
    # start_pie/stop_pie are in mifaudit's own DENY list - a guard against a BLIND sweep starting PIE,
    # not against this: a deliberate, narrowly-scoped, immediately-paired start/stop.
    started = M.raw_post("start_pie", {})
    check("T1402 start_pie accepted", started.get("ok") is True, started)
    running_status = wait_for_pie_state("running")
    check("T1402 PIE actually reached state=running", running_status.get("state") == "running", running_status)

    if running_status.get("state") != "running":
        print("cannot continue without a running PIE session")
        return 3

    spawned = M.call("spawn_actor_in_pie", {
        "class": "StaticMeshActor", "location": {"x": 0, "y": 0, "z": 0}, "label": "MifGFTestActor%d" % st})
    check("T1402 (setup) scratch actor spawned in PIE", spawned.get("ok") is True, json.dumps(spawned)[:200])
    actor_path = spawned.get("actor", {}).get("actorPath")

    receiver = M.call("add_game_framework_receiver", {"actorPath": actor_path})
    check("T1403 add_game_framework_receiver succeeds", receiver.get("ok") is True, receiver)

    req = M.call("add_game_framework_component_request", {
        "receiverClass": "/Script/Engine.StaticMeshActor", "componentClass": "/Script/Engine.AudioComponent",
        "requestId": request_id})
    check("T1404 add_game_framework_component_request succeeds", req.get("ok") is True, req)
    check("T1404 it echoes back the requestId we gave it", req.get("requestId") == request_id, req)

    # NOT list_components - that tool reads Blueprint component TEMPLATES, not live PIE instances.
    # CreateComponentOnInstance (GameFrameworkComponentManager.cpp) names the new component after its
    # CLASS exactly (NewObject<UActorComponent>(ActorInstance, ComponentClass, ComponentClass->GetFName())),
    # so the sub-object path is deterministic.
    component_path = actor_path + ".AudioComponent"
    exists = M.call("list_object_properties", {"objectPath": component_path})
    check("T1405 the AudioComponent genuinely exists on the actor - independently verified, not just ok:true",
          exists.get("ok") is True and exists.get("class") == "AudioComponent",
          "path=%s ok=%s class=%s" % (component_path, exists.get("ok"), exists.get("class")))

    # ------------------------------------------------------------------ T1408 the request is NAMEABLE
    # Added 2026-08-31 with list_game_framework_component_requests. Until then add_ handed back an id
    # for a request that stays LIVE - injecting a component into every current and future actor of a
    # class - and nothing could enumerate them, so a lost id was a leaked request nothing could name.
    # This is the round trip that proves the reader: after add_ it must be there, after remove_ it
    # must be gone, and both halves matter.
    print("\n=== T1408: the live request can be listed, and stops being listed when removed ===")
    live = M.call("list_game_framework_component_requests", {})
    check("T1408 list_game_framework_component_requests answers", live.get("ok") is not False, live)
    rows = live.get("requests") or []
    mine = [r for r in rows if r.get("requestId") == request_id]
    check("T1408 the request we just made is in the list", len(mine) == 1,
          "ids=%s" % [r.get("requestId") for r in rows][:8])
    if mine:
        row = mine[0]
        # The two CLASSES are what make a listing usable - three unfamiliar ids and nothing else
        # would not tell a caller which one to release.
        check("T1408 and it reports what the request actually DOES",
              row.get("receiverClass", "").endswith("StaticMeshActor")
              and row.get("componentClass", "").endswith("AudioComponent"), row)
        # staleHandles is emitted ALWAYS, not only when nonzero - the same discipline as
        # invalidCount on set_blendspace_samples, and for the same reason: a caller can assert on a
        # number that is always there, but has to notice the ABSENCE of one that is not. Nothing
        # read it until now.
        check("T1408 staleHandles is always present, not only when something is stale",
              isinstance(live.get("staleHandles"), (int, float)), json.dumps(live)[:220])
        check("T1408 and it cannot exceed the number of rows it describes",
              (live.get("staleHandles") or 0) <= (live.get("count") or 0),
              "staleHandles=%s count=%s" % (live.get("staleHandles"), live.get("count")))
        # The count and the per-row flags describe the same set and must agree. staleNote is NOT
        # asserted: it appears only when a handle's owning manager has gone away with its world,
        # which needs a world teardown this suite does not perform.
        check("T1408 and it matches the rows that report handleValid false",
              (live.get("staleHandles") or 0)
              == len([r for r in rows if r.get("handleValid") is False]),
              "staleHandles=%s but %d row(s) report handleValid false"
              % (live.get("staleHandles"),
                 len([r for r in rows if r.get("handleValid") is False])))
        check("T1408 handleValid is a real bool, not absent",
              isinstance(row.get("handleValid"), bool), row.get("handleValid"))
    check("T1408 count agrees with the rows returned", live.get("count") == len(rows),
          "count=%s rows=%d" % (live.get("count"), len(rows)))
    check("T1408 it says the list is scoped to this editor session",
          "session" in str(live.get("scopeNote") or "").lower(), live.get("scopeNote"))

    removed = M.call("remove_game_framework_component_request", {"requestId": request_id})
    check("T1406 remove_game_framework_component_request succeeds", removed.get("ok") is True, removed)

    after = M.call("list_game_framework_component_requests", {})
    check("T1408 and after removal it is NO LONGER listed - measured, not assumed from ok:true",
          all(r.get("requestId") != request_id for r in (after.get("requests") or [])),
          "ids=%s" % [r.get("requestId") for r in (after.get("requests") or [])][:8])

    # ------------------------------------------------------------------ T1407 the real UE lifecycle nuance
    print("\n=== T1407: a destroyed component stays resolvable-by-path until an actual GC pass runs ===")
    still_findable = M.call("list_object_properties", {"objectPath": component_path})
    check("T1407 immediately after removal the path is STILL resolvable (DestroyComponent does not "
          "instantly deallocate) - this is expected, not a sign removal failed",
          still_findable.get("ok") is True, still_findable)

    # run_console is in mifaudit's own DENY list too ("drives an external process") - the same
    # documented, deliberate, narrowly-scoped bypass as start_pie/stop_pie above.
    M.raw_post("run_console", {"command": "obj gc"})
    time.sleep(1)
    gone = M.call("list_object_properties", {"objectPath": component_path})
    check("T1407 after forcing garbage collection the component is genuinely gone",
          gone.get("ok") is False, gone)

    # ------------------------------------------------------------------ T1408-T1411 refusals, exact reason
    print("\n=== T1408-T1411: refusals checked for the specific reason ===")
    req2 = M.call("add_game_framework_component_request", {
        "receiverClass": "/Script/Engine.StaticMeshActor", "componentClass": "/Script/Engine.AudioComponent",
        "requestId": request_id})
    check("T1408 (setup) a fresh request under the same id succeeds now that the old one was removed",
          req2.get("ok") is True, req2)

    dupe = M.call("add_game_framework_component_request", {
        "receiverClass": "/Script/Engine.StaticMeshActor", "componentClass": "/Script/Engine.AudioComponent",
        "requestId": request_id})
    check("T1408 a duplicate requestId while one is still active is refused", dupe.get("ok") is False, dupe)
    check("T1408 refusal names the id already in use", request_id in (dupe.get("error") or ""), dupe.get("error"))

    bad_component = M.call("add_game_framework_component_request", {
        "receiverClass": "/Script/Engine.StaticMeshActor", "componentClass": "/Script/Engine.StaticMeshActor",
        "requestId": "MifBadRequest%d" % st})
    check("T1409 a componentClass that is not an ActorComponent is refused", bad_component.get("ok") is False,
          bad_component)

    missing_remove = M.call("remove_game_framework_component_request", {"requestId": "MifNeverExisted%d" % st})
    check("T1410 removing a requestId that was never created is refused", missing_remove.get("ok") is False,
          missing_remove)

    missing_actor = M.call("add_game_framework_receiver", {"actorPath": "/Temp/NoSuchWorld.NoSuchActor"})
    check("T1411 registering a receiver for a nonexistent actor is refused", missing_actor.get("ok") is False,
          missing_actor)

    # cleanup - release the second request too
    M.call("remove_game_framework_component_request", {"requestId": request_id})

    M.raw_post("stop_pie", {})
    stopped_status = wait_for_pie_state("stopped")
    check("(cleanup) PIE stopped cleanly", stopped_status.get("state") == "stopped", stopped_status)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
