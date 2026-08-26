"""trace and draw_debug — the two genuine parity gaps in MifBridgeSpatial.

That file's header states the rule these serve: NUMBERS FOR CORRECTNESS, PIXELS FOR TASTE.
`trace_ground` only ever fires straight down and takes the first GROUND hit, which answers exactly one
question. `trace` answers the rest, and `draw_debug` is how an agent SHOWS the answer instead of
describing it.

The checks that matter are not "did it return ok". They are:

  * T100 the hit is REAL - it names the actor, the impact point and the distance, so a caller can act
    on it rather than just know something was there;
  * T102 an unresolvable ignoreActors entry is REFUSED. trace_ground shipped with the skip-silently
    version and returned confident hits against the very actors a caller had excluded. Repeating that
    here would have been inexcusable, since it was fixed the same day;
  * T103 a malformed vector component is REPORTED, not defaulted. ReadVectorField returns
    EJsonRead{Absent,Read,Invalid} precisely so {"x":1,"y":"oops"} cannot read as "absent" and fire a
    ray from somewhere nobody asked for;
  * T105 draw_debug reports WHICH WORLD it drew into. A shape drawn into the editor world is invisible
    during PIE and vice versa, and the call succeeds either way - without this field, "ok:true and
    nothing on screen" has no explanation.
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

    # ------------------------------------------------------------------ T100 a real hit
    print("\n=== T100: a line trace returns an ACTIONABLE hit, not just a boolean ===")
    r = M.call("trace", {"start": {"x": 0, "y": 0, "z": 50000},
                         "end": {"x": 0, "y": 0, "z": -50000}})
    hits = r.get("hits") or []
    print("  ", json.dumps({k: v for k, v in r.items() if k != "hits"})[:230])
    check("T100 traced", r.get("ok") is True, json.dumps(r)[:200])
    check("T100 it hit the ground", r.get("hit") is True and len(hits) == 1, json.dumps(r)[:200])
    h = hits[0] if hits else {}
    check("T100 the hit names the actor", bool(h.get("actorPath")) and bool(h.get("label")),
          json.dumps(h)[:200])
    check("T100 and carries impact point, normal and distance",
          all(k in h for k in ("impactPoint", "normal", "distance")), json.dumps(h)[:200])
    check("T100 it echoes the ray it actually fired",
          (r.get("traced") or {}).get("start", {}).get("z") == 50000,
          json.dumps(r.get("traced"))[:120])

    # ------------------------------------------------------------------ T101 sweeps
    print("\n=== T101: shape sweeps - 'does this fit' rather than 'is something there' ===")
    for shape, extra in (("sphere", {"radius": 200}),
                         ("capsule", {"radius": 60, "halfHeight": 150}),
                         ("box", {"halfExtent": {"x": 100, "y": 100, "z": 100}})):
        q = dict({"start": {"x": 0, "y": 0, "z": 50000}, "end": {"x": 0, "y": 0, "z": -50000},
                  "shape": shape}, **extra)
        s = M.call("trace", q)
        check("T101 %s sweep works" % shape, s.get("ok") is True and s.get("shape") == shape,
              json.dumps(s)[:180])

    print("\n=== T101b: direction + distance is equivalent to an explicit end ===")
    a = M.call("trace", {"start": {"x": 0, "y": 0, "z": 5000}, "end": {"x": 0, "y": 0, "z": -5000}})
    b = M.call("trace", {"start": {"x": 0, "y": 0, "z": 5000},
                         "direction": {"x": 0, "y": 0, "z": -1}, "distance": 10000})
    check("T101b both forms agree on the hit",
          a.get("hit") == b.get("hit") and a.get("hitCount") == b.get("hitCount"),
          "explicit=%s/%s direction=%s/%s" % (a.get("hit"), a.get("hitCount"),
                                              b.get("hit"), b.get("hitCount")))

    # ------------------------------------------------------------------ T102 the silent-ignore lesson
    print("\n=== T102 [the lesson]: an ignore that does not resolve is REFUSED ===")
    r = M.call("trace", {"start": {"x": 0, "y": 0, "z": 1000}, "end": {"x": 0, "y": 0, "z": -1000},
                         "ignoreActors": ["NoSuchActor_zzz"]})
    check("T102 refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T102 and it explains the consequence",
          "without ignoring" in (r.get("error") or "").lower(), (r.get("error") or "")[:180])

    # ------------------------------------------------------------------ T103 vectors
    print("\n=== T103: a malformed vector component is reported, not defaulted ===")
    r = M.call("trace", {"start": {"x": 0, "y": "oops", "z": 0}, "end": {"x": 0, "y": 0, "z": -1}})
    check("T103 refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T103 and it names the offending component", "start.y" in (r.get("error") or ""),
          (r.get("error") or "")[:180])

    print("\n=== T104: the remaining guards ===")
    for name, payload, expect in (
        ("unknown channel", {"start": {"x": 0, "y": 0, "z": 0}, "end": {"x": 0, "y": 0, "z": -1},
                             "channel": "nope"}, "worldStatic"),
        ("no end or direction", {"start": {"x": 0, "y": 0, "z": 0}}, "direction"),
        ("zero-length direction", {"start": {"x": 0, "y": 0, "z": 0},
                                   "direction": {"x": 0, "y": 0, "z": 0}}, "zero-length"),
        ("unknown shape", {"start": {"x": 0, "y": 0, "z": 0}, "end": {"x": 0, "y": 0, "z": -1},
                           "shape": "banana"}, "line, sphere"),
    ):
        q = M.call("trace", payload)
        check("T104 %s refused" % name, q.get("ok") is False, json.dumps(q)[:160])
        check("T104 %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:140])

    # ------------------------------------------------------------------ T105 draw_debug
    print("\n=== T105: every debug shape draws, and says which world it drew into ===")
    shapes = [
        ("line", {"start": {"x": 0, "y": 0, "z": 200}, "end": {"x": 500, "y": 0, "z": 200}}),
        ("arrow", {"start": {"x": 0, "y": 0, "z": 500}, "end": {"x": 0, "y": 400, "z": 500}}),
        ("sphere", {"center": {"x": 0, "y": 0, "z": 300}, "radius": 150}),
        ("box", {"center": {"x": 300, "y": 300, "z": 200}, "extent": {"x": 100, "y": 100, "z": 100}}),
        ("point", {"center": {"x": 0, "y": 0, "z": 400}}),
        ("string", {"center": {"x": 0, "y": 0, "z": 600}, "text": "MifBridge"}),
    ]
    for shape, extra in shapes:
        d = M.call("draw_debug", dict({"shape": shape, "duration": 3}, **extra))
        check("T105 %s drew" % shape, d.get("ok") is True and d.get("drawn") is True,
              json.dumps(d)[:160])
        check("T105 %s names its world" % shape,
              bool(d.get("world")) and d.get("pieRunning") is not None,
              json.dumps(d)[:160])

    print("\n=== T106: draw_debug guards ===")
    for name, payload, expect in (
        ("line without end", {"shape": "line", "start": {"x": 0, "y": 0, "z": 0}}, "start"),
        ("sphere without center", {"shape": "sphere", "radius": 50}, "center"),
        ("string without text", {"shape": "string", "center": {"x": 0, "y": 0, "z": 0}}, "text"),
        ("zero duration", {"shape": "point", "center": {"x": 0, "y": 0, "z": 0}, "duration": 0},
         "invisible"),
        ("unknown shape", {"shape": "banana", "center": {"x": 0, "y": 0, "z": 0}}, "line, sphere"),
    ):
        q = M.call("draw_debug", payload)
        check("T106 %s refused" % name, q.get("ok") is False, json.dumps(q)[:160])
        check("T106 %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:140])

    # The 'persistent' hint is worth its own check: it is refused ON PURPOSE, because there is no
    # endpoint to clear a persistent shape and one would survive until the level reloads.
    q = M.call("draw_debug", {"shape": "point", "center": {"x": 0, "y": 0, "z": 0}, "persistent": True})
    check("T106 'persistent' is refused with the reason",
          q.get("ok") is False and "no endpoint to clear it" in (q.get("error") or ""),
          (q.get("error") or "")[:180])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
