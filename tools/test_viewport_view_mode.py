"""View modes and show flags - the rendering-diagnosis surface.

THE QUESTION THIS ANSWERS is "why is it black" - because the material is broken, because nothing is
lit, or because the mesh is not there. Wireframe, Unlit and LightingOnly separate those three in one
call each, and an agent had no way to reach any of them. capture_viewport documented the hole in its
own error text.

T2402 IS THE ONE THAT WOULD HAVE BEEN A CRASH. FEngineShowFlags::SetSingleFlag ends its default
branch in checkNoEntry() (ShowFlags.cpp:194), so passing an index FindIndexByName did not recognise
ASSERTS - a dead editor, not an error return. Every flag name in the request is resolved and refused
BEFORE any of them is set, which also means a typo in the fifth flag cannot leave the first four
applied. This test proves the refusal AND that the editor is still answering afterwards, because a
failed guard here is a dead process rather than a bad response.

T2403 IS THE ORDERING TEST, and it is the one that would have looked like the endpoint ignoring a
parameter. SetViewMode internally runs ApplyViewMode, which REWRITES show flags. So a showFlags map
applied BEFORE the view mode is silently undone. The endpoint applies mode first and flags second;
this sets both in ONE call and asserts the flags survived.

A BUILD TRAP AVOIDED, recorded here because it cost nothing only by being read first:
GetViewModeName(EViewModeIndex) at ShowFlags.h:570 is declared WITHOUT ENGINE_API and defined in a
Private .cpp, so calling it from a plugin is an unresolved external on 5.3 and 5.7 alike. The names
come from StaticEnum<EViewModeIndex>() instead, which involves no linkage at all.

GAME VIEW is reported and settable in the same call on purpose: it is the single biggest "why does
my capture not match what I see" lever, since editor-only sprites, billboards and grids vanish under
it.

RESTORES WHAT IT CHANGED. The viewport is a shared, visible thing - the suite puts the view mode,
flags and game view back the way it found them, whatever happens.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def view():
    return M.call("get_viewport_camera", {})


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    start = view()
    check("T2400 get_viewport_camera succeeds", start.get("ok") is True, json.dumps(start)[:250])
    if not start.get("ok"):
        return 1

    original_mode = start.get("viewMode")
    original_game = start.get("gameView")
    original_flags = dict(start.get("showFlags") or {})
    print("        starting state: viewMode=%s gameView=%s" % (original_mode, original_game))

    try:
        # ------------------------------------------------------------------ T2400 the read half
        print("\n=== T2400: the rendering state is readable at all ===")
        check("T2400 it reports a view mode by NAME, not just an index",
              isinstance(start.get("viewMode"), str) and len(start.get("viewMode")) > 0,
              start.get("viewMode"))
        check("T2400 and the index beside it", isinstance(start.get("viewModeIndex"), (int, float)),
              start.get("viewModeIndex"))
        check("T2400 gameView is reported - the biggest 'why does my capture not match the screen' "
              "lever", isinstance(start.get("gameView"), bool), start.get("gameView"))
        check("T2400 realtime is reported", isinstance(start.get("realtime"), bool),
              start.get("realtime"))
        check("T2400 a useful default set of show flags comes back without asking",
              len(original_flags) >= 10, len(original_flags))

        allf = M.call("get_viewport_camera", {"showFlags": "all"})
        check("T2400 showFlags:'all' reports substantially more than the default set",
              len(allf.get("showFlags") or {}) > len(original_flags) * 2,
              "default=%d all=%d" % (len(original_flags), len(allf.get("showFlags") or {})))

        # ------------------------------------------------------------------ T2401 the write half
        print("\n=== T2401: setting a view mode, confirmed by reading it back ===")
        for mode in ("Wireframe", "Unlit", "Lit"):
            s = M.call("set_viewport_camera", {"viewMode": mode})
            check("T2401 set viewMode=%s succeeds" % mode, s.get("ok") is True,
                  json.dumps(s)[:200])
            # Read back through get_viewport_camera, not from set's own response.
            check("T2401 and the viewport really reports %s afterwards" % mode,
                  view().get("viewMode") == mode, view().get("viewMode"))

        # ------------------------------------------------------------------ T2402 the assert guard
        print("\n=== T2402: an unknown show flag must be REFUSED, not passed to the engine ===")
        bad = M.call("set_viewport_camera", {"showFlags": {"NoSuchFlagAtAll": True}})
        check("T2402 an unknown show flag is refused", bad.get("ok") is False, json.dumps(bad)[:250])
        check("T2402 and the refusal names checkNoEntry, so the reason is the crash not a style rule",
              "checkNoEntry" in (bad.get("error") or ""), bad.get("error"))
        # THE assertion: a failed guard here is a dead process, so the editor answering is the proof.
        alive = M.call("self_audit", {})
        check("T2402 - the editor is still alive afterwards", alive.get("ok") is True,
              "SetSingleFlag's default branch asserts; a failed guard is a dead editor")

        # And a mixed request must apply NONE of it, not the valid prefix.
        mixed = M.call("set_viewport_camera", {"showFlags": {"Fog": False,
                                                             "AlsoNotAFlag": True}})
        check("T2402 a request mixing a good flag with a bad one is refused whole",
              mixed.get("ok") is False, json.dumps(mixed)[:250])
        check("T2402 and the good flag was NOT applied - validation happens before any write",
              (view().get("showFlags") or {}).get("Fog") == original_flags.get("Fog"),
              "Fog is %s, started as %s" % ((view().get("showFlags") or {}).get("Fog"),
                                            original_flags.get("Fog")))

        badmode = M.call("set_viewport_camera", {"viewMode": "Purple"})
        check("T2402 an unknown viewMode is refused and the accepted list is given",
              badmode.get("ok") is False and "Lit" in (badmode.get("error") or ""),
              (badmode.get("error") or "")[:200])

        # ------------------------------------------------------------------ T2403 the ordering
        print("\n=== T2403: view mode first, show flags after - SetViewMode rewrites flags ===")
        both = M.call("set_viewport_camera", {"viewMode": "Unlit",
                                              "showFlags": {"Fog": False, "Bounds": True}})
        check("T2403 setting a mode and flags in ONE call succeeds", both.get("ok") is True,
              json.dumps(both)[:250])
        after = view()
        check("T2403 the view mode took", after.get("viewMode") == "Unlit", after.get("viewMode"))
        # THE assertion. SetViewMode runs ApplyViewMode, which rewrites show flags - so if the
        # endpoint applied flags first these would be gone, and it would look like the parameter
        # was ignored.
        check("T2403 AND the flags survived it - they are applied after the mode, not before",
              (after.get("showFlags") or {}).get("Fog") is False
              and (after.get("showFlags") or {}).get("Bounds") is True,
              json.dumps({k: (after.get("showFlags") or {}).get(k) for k in ("Fog", "Bounds")}))

        # ------------------------------------------------------------------ T2404 game view
        print("\n=== T2404: game view ===")
        gv = M.call("set_viewport_camera", {"gameView": True})
        check("T2404 gameView can be set", gv.get("ok") is True, json.dumps(gv)[:200])
        check("T2404 and reads back as on", view().get("gameView") is True, view().get("gameView"))
    finally:
        # RESTORE. The viewport is shared and visible; leaving it in Wireframe with fog off would be
        # a change nobody asked for that outlives this suite.
        M.call("set_viewport_camera", {"viewMode": original_mode or "Lit",
                                       "gameView": bool(original_game),
                                       "showFlags": original_flags})
        back = view()
        check("T2405 (cleanup) the viewport is back to how it was found",
              back.get("viewMode") == original_mode and back.get("gameView") == original_game,
              "viewMode=%s (was %s), gameView=%s (was %s)" % (back.get("viewMode"), original_mode,
                                                             back.get("gameView"), original_game))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
