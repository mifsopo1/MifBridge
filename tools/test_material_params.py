"""list_material_parameters — the one confirmed HIGH gap from the verified audit.

The case that motivated it, verified here against real shipped content: every DDS2 master material is
COOKED, cooking strips the expression graph, and so `list_material_expressions` correctly reports
`numExpressions: 0, cooked: true` on all of them. Before this endpoint there was no way to ask a
shipped material what knobs it exposes, and instance authoring was guesswork.

FMaterialCachedParameters survives cook, which is why this works where the expression listing cannot.
T120 asserts exactly that contrast on a real asset rather than describing it.

T122 covers the correctness detail the audit called out: association and index are not decoration. A
layer parameter reported as a global makes every later set_material_parameter build the wrong
FMaterialParameterInfo, get false back, and lead a caller to conclude the parameter does not exist.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []

# The cooked master material is DISCOVERED in main() - see the note there. This used to name one
# DDS2 asset, which made the suite unrunnable anywhere else.
COOKED_MASTER = None


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # A COOKED MATERIAL, FOUND RATHER THAN NAMED. This suite's whole claim is that parameters
    # resolve where list_material_expressions is blind, and "blind" means COOKED - an uncooked
    # material has an expression graph and T120 would assert nothing. So the fixture is discovered
    # with that requirement rather than weakened to any material, and a project with no cooked
    # content SKIPS with a reason instead of failing its setup and returning an error.
    global COOKED_MASTER
    COOKED_MASTER, _found_params = M.discover_material(cooked=True, min_params=1)
    if not COOKED_MASTER:
        print("SKIPPED - no COOKED material in this project, so there is nothing for which")
        print("  list_material_expressions is blind and list_material_parameters is not.")
        print("  That is the entire premise of this suite. Nothing was verified.")
        return 0
    print("cooked master: %s" % COOKED_MASTER)

    # ------------------------------------------------------------------ T120 the whole point
    print("\n=== T120 [the point]: parameters resolve where EXPRESSIONS cannot, on cooked content ===")
    ex = M.call("list_material_expressions", {"path": COOKED_MASTER})
    pr = M.call("list_material_parameters", {"path": COOKED_MASTER})
    print("  expressions=%s cooked=%s || parameters=%s %s"
          % (ex.get("numExpressions"), ex.get("cooked"), pr.get("count"), json.dumps(pr.get("byType"))))
    check("T120 the material really is cooked", ex.get("cooked") is True, json.dumps(ex)[:180])
    check("T120 and its expression graph is therefore empty", ex.get("numExpressions") == 0,
          "numExpressions=%s" % ex.get("numExpressions"))
    # This is the gap closed: same asset, same instant, one endpoint blind and the other not.
    check("T120 but its PARAMETERS are readable", pr.get("ok") is True and (pr.get("count") or 0) > 0,
          "count=%s - if this is 0 the endpoint adds nothing over the expression listing"
          % pr.get("count"))
    check("T120 it says the table survives cook", pr.get("survivesCook") is True, json.dumps(pr)[:160])
    check("T120 and identifies it as a Material", pr.get("kind") == "Material", pr.get("kind"))

    # ------------------------------------------------------------------ T121 shape
    print("\n=== T121: every parameter is actionable ===")
    params = pr.get("parameters") or []
    check("T121 each has a name and a type",
          all(p.get("name") and p.get("type") for p in params), json.dumps(params[:1])[:200])
    check("T121 each carries a value", all("value" in p for p in params), json.dumps(params[:1])[:200])
    # AND THAT THE KEY IS FILLED. Emitting "value": null on every parameter satisfies the check
    # above while reporting nothing at all - presence standing in for value.
    check("T121 and the values are populated, not a key emitted empty",
          any(p.get("value") is not None for p in params),
          "every one of %d parameters reports value:null" % len(params))
    check("T121 byType agrees with the parameter list",
          sum((pr.get("byType") or {}).values()) == len(params),
          "%s vs %s" % (pr.get("byType"), len(params)))

    # ------------------------------------------------------------------ T122 the correctness detail
    print("\n=== T122 [audit's warning]: association and index are always reported ===")
    check("T122 every parameter names its association",
          all(p.get("association") in ("global", "layer", "blend") for p in params),
          json.dumps([p.get("association") for p in params[:5]]))
    check("T122 and its index",
          all(isinstance(p.get("index"), (int, float)) for p in params),
          json.dumps([p.get("index") for p in params[:5]]))

    # ------------------------------------------------------------------ T123 instances
    print("\n=== T123: on an INSTANCE, own overrides are distinguishable from inherited ===")
    # Pick the instance with the MOST parameters rather than whichever comes first. A small instance
    # can legitimately have every parameter overridden, and the first version of this test sampled
    # arbitrarily and failed the "two kinds differ" check the day it happened to draw a 5-parameter
    # one. A deep instance inherits most of its parameters, which is what makes the mix observable.
    best, best_n = None, -1
    for a in (M.call("find_assets", {"class": "MaterialInstanceConstant", "pathPrefix": "/Game/",
                                     "limit": 20}).get("assets") or []):
        n = M.call("list_material_parameters", {"path": a.get("path")}).get("count") or 0
        if n > best_n:
            best, best_n = a.get("path"), n
    mi = best
    q = M.call("list_material_parameters", {"path": mi})
    ip = q.get("parameters") or []
    print("  %s -> %s params, parent=%s" % ((mi or "")[-34:], q.get("count"), (q.get("parent") or "")[-30:]))
    check("T123 read", q.get("ok") is True and (q.get("count") or 0) > 0, json.dumps(q)[:180])
    check("T123 it is identified as an instance", q.get("kind") == "MaterialInstance", q.get("kind"))
    check("T123 and names its parent", bool(q.get("parent")), q.get("parent"))
    # Whether a value is this instance's own or inherited decides whether resetting it does anything.
    check("T123 every entry says whether THIS instance overrides it",
          all(isinstance(p.get("overriddenOnThisInstance"), bool) for p in ip),
          json.dumps(ip[:1])[:200])
    # On a DEEP instance the two kinds must both appear - that is what proves the flag reflects this
    # instance rather than being hardcoded. On a shallow one, all-overridden is legitimate, so the
    # assertion is conditioned on having found a deep instance rather than silently weakened.
    n_over = sum(1 for p in ip if p.get("overriddenOnThisInstance"))
    if len(ip) >= 20:
        check("T123 on a deep instance the two kinds actually differ",
              len({p.get("overriddenOnThisInstance") for p in ip}) == 2,
              "overridden=%d of %d - all-or-nothing would mean the flag is not reading the instance"
              % (n_over, len(ip)))
    else:
        check("T123 the flag is populated (instance too shallow to expect a mix)",
              all(isinstance(p.get("overriddenOnThisInstance"), bool) for p in ip),
              "%d parameters" % len(ip))

    # ------------------------------------------------------------------ T124 filters and guards
    print("\n=== T124: filters, and telling 'none' apart from 'filtered out' ===")
    only = M.call("list_material_parameters", {"path": mi, "types": ["scalar"]})
    check("T124 the type filter narrows",
          only.get("ok") is True and (only.get("count") or 0) < (q.get("count") or 0),
          "%s vs %s" % (only.get("count"), q.get("count")))
    check("T124 and returns only that type",
          all(p.get("type") == "scalar" for p in (only.get("parameters") or [])),
          str({p.get("type") for p in (only.get("parameters") or [])}))
    none = M.call("list_material_parameters", {"path": mi, "types": ["nonsense"]})
    check("T124 a filter matching nothing SAYS it was the filter",
          none.get("count") == 0 and "filter" in (none.get("note") or ""),
          (none.get("note") or "")[:140])

    print("\n=== T125: guards ===")
    for name, payload, expect in (
        ("missing path", {}, "required"),
        ("nonexistent asset", {"path": "/Game/NoSuchMaterial_zz"}, "not found"),
        # The PACKAGE path rather than the object path, derived from whatever was discovered.
        ("not a material", {"path": COOKED_MASTER.rsplit(".", 1)[0]}, ""),
    ):
        r = M.call("list_material_parameters", payload)
        if expect:
            check("T125 %s refused" % name, r.get("ok") is False, json.dumps(r)[:150])
            check("T125 %s explains" % name, expect in (r.get("error") or ""), (r.get("error") or "")[:130])
    bt = (M.call("find_assets", {"class": "BehaviorTree", "limit": 1}).get("assets") or [{}])[0].get("path")
    r = M.call("list_material_parameters", {"path": bt})
    check("T125 a non-material is refused by class name",
          r.get("ok") is False and "BehaviorTree" in (r.get("error") or ""), (r.get("error") or "")[:150])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
