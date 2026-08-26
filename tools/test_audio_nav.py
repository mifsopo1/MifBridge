"""audition_sound, nav_project_point and nav_find_path.

Two unrelated HIGH-value gaps, both small.

The theme across all three is that an EMPTY ANSWER has to say which empty it is. "Nothing walkable
near this point", "this world has no nav mesh at all", and "the query itself failed" are three
different problems with three different fixes, and a bare false or null cannot tell them apart. Same
for audio: a silent editor and a quiet asset look identical unless the endpoint says the preview
device is missing.

T173 is the one with real teeth. A PARTIAL path stops at the closest reachable point and still comes
back looking like a path - reporting that as reachable is exactly how "the NPC can get there" becomes
a lie in a tool that is supposed to answer that question.

HONEST LIMITATION. The scratch level these run in (/Temp/Untitled_1) has no navigable surface. A nav
volume was built over a spawned cube floor and nav_status reported 8 tiles, but nothing projects onto
it - the tiles generate empty, presumably because a bare spawned StaticMeshActor is not contributing
to navigation. So the POSITIVE branches here (a point that IS on the mesh, a path that IS reachable,
a path that is genuinely PARTIAL) are structurally present and not currently exercised, and the
assertions that do run are the negative ones plus the invariant that partial and reachable are never
both true. Running this against a real DDS2 level would exercise the rest; that is worth doing before
trusting nav_find_path's positive answers.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T170 audition
    print("\n=== T170: audition a real sound ===")
    snd = (M.call("find_assets", {"class": "SoundWave", "pathPrefix": "/Game/",
                                  "limit": 1}).get("assets") or [{}])[0].get("path")
    r = M.call("audition_sound", {"path": snd})
    print("  ", json.dumps({k: v for k, v in r.items() if k != "note"})[:220])
    check("T170 playing", r.get("ok") is True and r.get("playing") is True, json.dumps(r)[:200])
    check("T170 it identifies the sound and its class",
          r.get("sound") == snd and r.get("class") == "SoundWave", json.dumps(r)[:180])
    # Duration is the cheap proof it resolved a real asset rather than a name.
    check("T170 and reports a real duration", (r.get("duration") or 0) > 0, r.get("duration"))
    s = M.call("audition_sound", {"stop": True})
    check("T170 stop works", s.get("ok") is True and s.get("playing") is False, json.dumps(s)[:150])

    print("\n=== T171: audition guards ===")
    for name, payload, expect in (
        ("no path", {}, "required"),
        ("missing asset", {"path": "/Game/NoSuchSound_zz"}, "not found"),
    ):
        q = M.call("audition_sound", payload)
        check("T171 %s refused" % name, q.get("ok") is False, json.dumps(q)[:150])
        check("T171 %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:130])
    # A non-sound asset must be named, not silently ignored.
    notsound = (M.call("find_assets", {"class": "Material", "limit": 1}).get("assets") or [{}])[0].get("path")
    q = M.call("audition_sound", {"path": notsound})
    check("T171 a non-sound is refused by class name",
          q.get("ok") is False and "not a USoundBase" in (q.get("error") or ""),
          (q.get("error") or "")[:150])

    # ------------------------------------------------------------------ T172 nav project
    print("\n=== T172: projecting a point onto the nav mesh ===")
    p = M.call("nav_project_point", {"point": {"x": 0, "y": 0, "z": 100}})
    print("  ", json.dumps(p)[:220])
    check("T172 answered", p.get("ok") is True, json.dumps(p)[:180])
    check("T172 it says whether the point is on the mesh",
          isinstance(p.get("onNavMesh"), bool), json.dumps(p)[:180])
    check("T172 and echoes the point it was asked about",
          (p.get("queried") or {}).get("z") == 100, json.dumps(p.get("queried")))
    if p.get("onNavMesh"):
        # movedBy is the useful number: 2cm off and 300cm off are different problems.
        check("T172 a hit reports how far it moved",
              isinstance(p.get("movedBy"), (int, float)), p.get("movedBy"))
    else:
        # A miss must distinguish "nothing walkable near" from "no nav mesh built".
        check("T172 a miss explains which kind of empty it is",
              "not been built" in (p.get("note") or "") or "nothing walkable" in (p.get("note") or ""),
              (p.get("note") or "")[:160])

    print("\n=== T172b: nav_project_point guards ===")
    for name, payload, expect in (
        ("no point", {}, "required"),
        ("malformed point", {"point": {"x": 0, "y": "oops", "z": 0}}, "point.y"),
    ):
        q = M.call("nav_project_point", payload)
        check("T172b %s refused" % name, q.get("ok") is False, json.dumps(q)[:150])
        check("T172b %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:130])

    # ------------------------------------------------------------------ T173 pathing
    print("\n=== T173 [teeth]: partial is NOT reachable ===")
    f = M.call("nav_find_path", {"start": {"x": 0, "y": 0, "z": 100},
                                 "end": {"x": 500, "y": 500, "z": 100}})
    print("  ", json.dumps({k: v for k, v in f.items() if k != "points"})[:230])
    if f.get("ok"):
        # The invariant that matters, whatever this world happens to contain: a partial path can
        # never be reported as reachable.
        check("T173 partial and reachable are never both true",
              not (f.get("partial") and f.get("reachable")),
              "partial=%s reachable=%s" % (f.get("partial"), f.get("reachable")))
        check("T173 it reports length and point count",
              isinstance(f.get("pathLength"), (int, float)) and isinstance(f.get("pointCount"), (int, float)),
              json.dumps({k: v for k, v in f.items() if k != "points"})[:180])
        if f.get("partial"):
            check("T173 and a partial path says so in words",
                  "NOT reachable" in (f.get("note") or ""), (f.get("note") or "")[:150])
    else:
        # No navmesh in this world is a legitimate outcome, and the error must say that rather than
        # implying the destination is unreachable.
        check("T173 a query failure is reported as such, not as 'unreachable'",
              "query failure" in (f.get("error") or "") or "no navigation system" in (f.get("error") or ""),
              (f.get("error") or "")[:170])

    print("\n=== T173b: nav_find_path guards ===")
    for name, payload, expect in (
        ("no start", {"end": {"x": 1, "y": 1, "z": 1}}, "start"),
        ("no end", {"start": {"x": 1, "y": 1, "z": 1}}, "end"),
    ):
        q = M.call("nav_find_path", payload)
        check("T173b %s refused" % name, q.get("ok") is False, json.dumps(q)[:150])
        check("T173b %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:130])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
