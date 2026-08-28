"""Six more previously-untested READ endpoints from coverage_gaps.py: get_actor_bounds, get_cvar,
get_dependencies, list_editor_commands, describe_property, diff_properties_vs_default.

Second batch of the same sweep test_uncovered_reads.py started - these six did not fit that file's
"subsystem nothing had ever exercised" theme (Gameplay Tags/PCG/State Tree/Water/Input), they are
just individually-untested general-purpose reads scattered across MifBridgeSpatial.cpp,
MifBridgeConsole.cpp, MifBridgeAssetOps.cpp, MifBridgeUI.cpp and MifBridgeDetails.cpp. Grouped here
for the same reason: one editor session instead of six.

describe_property and diff_properties_vs_default are the two with real teeth - they are the
Details-panel introspection this bridge is otherwise blind without (property FLAGS, metadata,
EditCondition, and "what does this object actually override from its archetype"). Both read
REAL placed actors from the open level rather than fabricated paths, so the assertions are
checking real engine behaviour, not just that a call returns something.
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

    actors = (M.call("list_level_actors", {"limit": 30}).get("actors") or [])
    check("T830 (setup) there is at least one placed actor to test against", len(actors) > 0,
          len(actors))
    actor_path = actors[0].get("actorPath") if actors else None

    # ================================================================== T830 get_actor_bounds
    print("=== T830: get_actor_bounds ===")
    if actor_path:
        r = M.call("get_actor_bounds", {"actorPath": actor_path})
        check("T830 it answers on a real actor", r.get("ok") is True, json.dumps(r)[:200])
        for key in ("origin", "extent", "size", "min", "max"):
            v = r.get(key) or {}
            check("T830 %s has real x/y/z" % key,
                  all(isinstance(v.get(a), (int, float)) for a in ("x", "y", "z")), v)
        size = r.get("size") or {}
        extent = r.get("extent") or {}
        check("T830 size is exactly double extent on every axis",
              all(abs(size.get(a, 0) - 2 * extent.get(a, 0)) < 1e-3 for a in ("x", "y", "z")),
              (extent, size))
        mn, mx, origin = r.get("min") or {}, r.get("max") or {}, r.get("origin") or {}
        check("T830 min/max bracket the origin on every axis",
              all(mn.get(a, 1) <= origin.get(a, 0) <= mx.get(a, -1) for a in ("x", "y", "z")),
              (mn, origin, mx))
        check("T830 hasBounds is a real bool", isinstance(r.get("hasBounds"), bool), r.get("hasBounds"))

    for label, payload, expect in (
        ("no actor", {}, None),
        ("a missing actor", {"actorPath": "/Game/NoSuchActor_zz"}, "not found"),
        ("an unknown parameter", {"actorPath": actor_path or "x", "assetPath": "y"}, None),
    ):
        q = M.call("get_actor_bounds", payload)
        check("T830 %s refused" % label, q.get("ok") is False, q.get("error"))

    # ================================================================== T831 get_cvar
    print("")
    print("=== T831: get_cvar ===")
    r = M.call("get_cvar", {"name": "r.ScreenPercentage"})
    check("T831 a real engine cvar answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T831 value/asInt/asFloat/asBool are all present",
          all(k in r for k in ("value", "asInt", "asFloat", "asBool")), r)
    check("T831 name is echoed back", r.get("name") == "r.ScreenPercentage", r.get("name"))

    q = M.call("get_cvar", {"name": "mif.NoSuchCvar_zz_definitely_not_real"})
    check("T831 an unknown cvar refuses clearly",
          q.get("ok") is False and "no console variable" in str(q.get("error", "")), q.get("error"))
    q = M.call("get_cvar", {})
    check("T831 no name refuses", q.get("ok") is False, q.get("error"))
    q = M.call("get_cvar", {"name": "r.ScreenPercentage", "value": "50"})
    check("T831 passing value (a write param) is refused, not silently ignored",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T832 get_dependencies
    print("")
    print("=== T832: get_dependencies ===")
    asset = (M.call("find_assets", {"class": "StaticMesh", "limit": 1}).get("assets") or [{}])[0]
    asset_path = asset.get("path")
    check("T832 (setup) there is at least one asset to test against", bool(asset_path), asset)
    if asset_path:
        r = M.call("get_dependencies", {"path": asset_path})
        check("T832 it answers on a real asset", r.get("ok") is True, json.dumps(r)[:200])
        deps = r.get("dependencies") or []
        check("T832 count matches its own array", r.get("count") == len(deps), (r.get("count"), len(deps)))
        check("T832 packageExists is true for a real asset", r.get("packageExists") is True, r)
        check("T832 package and packageName agree",
              r.get("package") == r.get("packageName"), (r.get("package"), r.get("packageName")))

    r2 = M.call("get_dependencies", {"path": "/Game/NoSuchPackage_zz"})
    check("T832 an unknown package succeeds with count:0, not an error",
          r2.get("ok") is True and r2.get("count") == 0, json.dumps(r2)[:200])
    check("T832 and packageExists:false explains the zero rather than claiming no dependencies",
          r2.get("packageExists") is False and bool(r2.get("existsNote")), r2)
    q = M.call("get_dependencies", {})
    check("T832 no path refuses", q.get("ok") is False, q.get("error"))

    # ================================================================== T833 list_editor_commands
    print("")
    print("=== T833: list_editor_commands ===")
    r = M.call("list_editor_commands", {"limit": 50})
    check("T833 it answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T833 reports a contextCount", isinstance(r.get("contextCount"), (int, float)), r.get("contextCount"))
    check("T833 reports commandListSource", isinstance(r.get("commandListSource"), dict), r.get("commandListSource"))
    check("T833 truncated is a real bool", isinstance(r.get("truncated"), bool), r.get("truncated"))

    # A requested context that matches NOTHING is a REFUSAL, not a quiet zero - the handler
    # explicitly checks WantContext.IsEmpty() && ContextArr.Num()==0 to catch exactly the "typo
    # silently returns nothing" trap this codebase guards against everywhere else too.
    r2 = M.call("list_editor_commands", {"context": "NoSuchContext_zz_definitely_not_real"})
    check("T833 an unknown context is refused, not silently zero",
          r2.get("ok") is False, json.dumps(r2)[:200])
    check("T833 and points back at calling with no context to enumerate the real ones",
          "no context" in str(r2.get("error", "")).lower(), r2.get("error"))

    q = M.call("list_editor_commands", {"tabId": "x"})
    check("T833 an unknown parameter (tabId, belongs elsewhere) is refused",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T834 describe_property
    print("")
    print("=== T834: describe_property ===")
    r = M.call("describe_property", {"class": "StaticMeshActor", "limit": 10})
    check("T834 class-only form answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T834 form is 'class'", r.get("form") == "class", r.get("form"))
    props = r.get("properties") or []
    check("T834 count matches its own array", r.get("count") == len(props), (r.get("count"), len(props)))
    if props:
        check("T834 every property row has a name and a type",
              all(p.get("name") and p.get("type") for p in props), props[:2])

    if actor_path:
        r2 = M.call("describe_property", {"actorPath": actor_path, "limit": 10})
        check("T834 survey form on a real actor answers", r2.get("ok") is True, json.dumps(r2)[:200])
        check("T834 form is 'survey'", r2.get("form") == "survey", r2.get("form"))

        rc = M.call("describe_property", {"actorPath": actor_path, "propertyPath": "RootComponent"})
        check("T834 a single real property path answers", rc.get("ok") is True, json.dumps(rc)[:200])
        check("T834 form is 'property'", rc.get("form") == "property", rc.get("form"))
        check("T834 the property object is present", isinstance(rc.get("property"), dict), rc)

    q = M.call("describe_property", {"class": "NoSuchClass_zz_definitely_not_real"})
    check("T834 an unknown class refuses clearly", q.get("ok") is False, q.get("error"))
    q = M.call("describe_property", {})
    check("T834 no target at all refuses", q.get("ok") is False, q.get("error"))

    # ================================================================== T835 diff_properties_vs_default
    print("")
    print("=== T835: diff_properties_vs_default ===")
    if actor_path:
        r = M.call("diff_properties_vs_default", {"actorPath": actor_path, "limit": 200})
        check("T835 it answers on a real actor", r.get("ok") is True, json.dumps(r)[:200])
        for key in ("inspected", "differing", "matching", "skippedTransient", "expanded"):
            check("T835 reports %s as a real number" % key,
                  isinstance(r.get(key), (int, float)), r.get(key))
        # THE INVARIANT the file's own comment says is emitted, not implied.
        total = ((r.get("differing") or 0) + (r.get("matching") or 0)
                 + (r.get("skippedTransient") or 0))
        check("T835 inspected == differing + matching + skippedTransient (top-level, not recursive)",
              r.get("inspected") == total, (r.get("inspected"), total))
        check("T835 recursive defaults false", r.get("recursive") is False, r.get("recursive"))
        check("T835 expanded is 0 when not recursive", r.get("expanded") == 0, r.get("expanded"))
        check("T835 reports the archetype it diffed against", bool(r.get("archetype")), r.get("archetype"))

        r2 = M.call("diff_properties_vs_default", {"actorPath": actor_path, "recursive": True, "limit": 200})
        check("T835 recursive:true is accepted", r2.get("ok") is True, json.dumps(r2)[:200])
        check("T835 and recursive is echoed true", r2.get("recursive") is True, r2.get("recursive"))

    q = M.call("diff_properties_vs_default", {})
    check("T835 no target refuses", q.get("ok") is False, q.get("error"))
    # JBool's own contract (MifBridgeCommon.cpp): a wrong-type value is never silently defaulted -
    # it is recorded as a violation and RunEndpoint's generic wrapper turns any recorded violation
    # into a hard refusal, naming the field in ignoredParameters. Checked precisely rather than just
    # "it refused", since a handler-level guard refusing for an unrelated reason would look the same.
    q = M.call("diff_properties_vs_default", {"actorPath": actor_path or "x", "deep": "notabool"})
    check("T835 a wrong-type deep is refused, not silently defaulted",
          q.get("ok") is False, q.get("error"))
    check("T835 and names the offending field via ignoredParameters",
          "deep" in str(q.get("ignoredParameters", "")), q.get("ignoredParameters"))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
