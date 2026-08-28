"""list_collision_profiles and set_collision.

The audit rated this a HIGH gap. Testing first narrowed it sharply, and that narrowing is the point of
this file: set_property ALREADY sets BodyInstance.CollisionProfileName and CollisionEnabled, so
"cannot configure collision" was wrong.

What was actually missing is VALIDATION, and T162 is the check that matters. set_property accepts the
profile name "NoSuchProfile_zz" and reads it straight back - leaving the component on whatever
collision it had before, configured-looking in every read path, and colliding with the wrong things. A
mod prop that does not stop the player and nothing anywhere saying why.

T163 covers the other half: a profile name alone does not tell you whether the thing blocks the
player, so the response reports the responses it RESOLVED to.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    stamp = int(time.time() % 100000)

    # ------------------------------------------------------------------ T160 the authority
    print("\n=== T160: the project's real collision profiles ===")
    r = M.call("list_collision_profiles", {})
    profs = r.get("profiles") or []
    names = [p.get("name") for p in profs]
    print("  %d profiles: %s ..." % (r.get("count"), ", ".join(names[:5])))
    check("T160 listed", r.get("ok") is True and (r.get("count") or 0) > 0, json.dumps(r)[:180])
    check("T160 the engine defaults are there",
          all(n in names for n in ("BlockAll", "NoCollision", "OverlapAll")), str(names[:8]))
    check("T160 each carries its enabled mode, object type and responses",
          all(p.get("collisionEnabled") and p.get("objectType") and p.get("responses") for p in profs),
          json.dumps(profs[:1])[:220])

    print("\n=== T160b: a NoCollision profile's responses are flagged as moot ===")
    # Without this the table reads as though NoCollision blocks WorldStatic, which it does not -
    # collisionEnabled decides that and the responses only apply once collision is on.
    nc = [p for p in profs if p.get("name") == "NoCollision"]
    check("T160b flagged", bool(nc) and nc[0].get("responsesAreMoot") is True,
          json.dumps(nc[:1])[:200])
    check("T160b and explained", bool(nc) and "never apply" in (nc[0].get("note") or ""),
          (nc[0].get("note") if nc else "")[:140])

    # ------------------------------------------------------------------ setup
    bp = "/Game/_MifColl/BP_T_%d" % stamp
    b = M.call("create_blueprint", {"path": bp, "parentClass": "Actor"})
    bid = b.get("blueprintId")
    M.call("add_component", {"blueprintId": bid, "componentClass": "StaticMeshComponent",
                             "name": "Mesh1"})
    tpl = [c.get("templatePath") for c in
           (M.call("list_components", {"blueprintId": bid}).get("components") or [])
           if c.get("name") == "Mesh1"]
    if not tpl:
        print("setup failed")
        return 3
    tpl = tpl[0]

    # ------------------------------------------------------------------ T161 set
    print("\n=== T161: setting a real profile ===")
    q = M.call("set_collision", {"objectPath": tpl, "profile": "BlockAll"})
    print("  ", json.dumps({k: v for k, v in q.items() if k != "responses"})[:200])
    check("T161 set", q.get("ok") is True and q.get("profile") == "BlockAll", json.dumps(q)[:200])
    check("T161 it reports the enabled mode it resolved to",
          q.get("collisionEnabled") == "QueryAndPhysics", q.get("collisionEnabled"))

    # ------------------------------------------------------------------ T162 THE point
    print("\n=== T162 [the point]: a bogus profile is REFUSED, where set_property accepts it ===")
    bad = M.call("set_collision", {"objectPath": tpl, "profile": "NoSuchProfile_zz"})
    check("T162 refused", bad.get("ok") is False, json.dumps(bad)[:180])
    check("T162 and it explains the silent failure it prevented",
          "previous collision" in (bad.get("error") or ""), (bad.get("error") or "")[:200])
    check("T162 and lists the real names", "BlockAll" in (bad.get("error") or ""),
          (bad.get("error") or "")[-140:])
    # The component must be untouched - a refusal that half-applied would be worse than no check.
    still = M.call("get_property", {"objectPath": tpl,
                                    "propertyPath": "BodyInstance.CollisionProfileName"})
    check("T162 the component still has the GOOD profile", "BlockAll" in str(still.get("value")),
          still.get("value"))
    # And the contrast that motivated the endpoint: the generic setter still takes anything.
    raw = M.call("set_property", {"objectPath": tpl,
                                  "propertyPath": "BodyInstance.CollisionProfileName",
                                  "value": "NoSuchProfile_zz"})
    check("T162 [contrast] set_property still accepts the bogus name, which is why this exists",
          raw.get("ok") is True, json.dumps(raw)[:150])
    M.call("set_collision", {"objectPath": tpl, "profile": "BlockAll"})

    # ------------------------------------------------------------------ T163 resolved responses
    print("\n=== T163: the response says what it BLOCKS, not just which profile ===")
    q = M.call("set_collision", {"objectPath": tpl, "profile": "BlockAll"})
    resp = q.get("responses") or {}
    blocks = [k for k, v in resp.items() if v == "Block"]
    check("T163 responses are reported", len(resp) > 0, json.dumps(resp)[:180])
    check("T163 and BlockAll actually blocks Pawn", "Pawn" in blocks, str(blocks[:6]))

    print("\n=== T164: guards ===")
    for name, payload, expect in (
        ("no objectPath", {"profile": "BlockAll"}, "required"),
        ("nothing to change", {"objectPath": tpl}, "profile and/or"),
        ("bad enabled mode", {"objectPath": tpl, "collisionEnabled": "Sometimes"}, "NoCollision"),
        ("not a primitive", {"objectPath": "/Game/NoSuch_zz", "profile": "BlockAll"}, "not found"),
    ):
        z = M.call("set_collision", payload)
        check("T164 %s refused" % name, z.get("ok") is False, json.dumps(z)[:150])
        check("T164 %s explains" % name, expect in (z.get("error") or ""), (z.get("error") or "")[:140])

    SC.confirm_call("delete_asset", {"path": bp})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
