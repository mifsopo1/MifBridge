"""Material GRAPH authoring - create, add expressions, wire them, bind a property, recompile.

test_material_write covers instances and parameters. The graph half - eight endpoints - was named in
no suite, and it is the half with a documented way to kill the editor: UMaterialExpression is
UCLASS(Optional), so cooked packages have NO expression graph, and UMaterial::GetExpressions() derefs
GetEditorOnlyData() with no null check. Calling it on a cooked material is a crash, not an empty list.

T351 is that test, and it is the one the adversarial sweep could not do. The sweep hands every
endpoint a GHOST path - something that does not exist - so it never asked what happens against a real
COOKED asset, which is the actual hazard. DDS2 is a cooked game: nearly every material a modder
touches is cooked.

The rest is the authoring loop end to end, asserted by reading the graph back rather than by trusting
the calls: an expression that reports an index but does not appear in list_material_expressions, or a
connection that reports success and leaves connectionCount at zero, is the failure worth catching.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def graph(path):
    return M.call("list_material_expressions", {"path": path})


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T351 the cooked hazard
    print("\n=== T351 [the hazard]: a real COOKED material must not crash the editor ===")
    cooked = (M.call("find_assets", {"class": "Material", "pathPrefix": "/Game/", "limit": 1})
              .get("assets") or [{}])[0].get("path")
    check("T351 a cooked material was found to test against", bool(cooked), cooked)
    if cooked:
        print("   using %s" % cooked)
        # The READ must degrade honestly rather than refuse or die: a modder needs to know a cooked
        # material has no graph, and that is different from the asset being missing.
        r = graph(cooked)
        check("T351 reading it answers instead of dying", r.get("ok") is True, json.dumps(r)[:180])
        check("T351 and says the material is cooked", r.get("cooked") is True, r.get("cooked"))
        check("T351 the editor survived the read", M.bridge_responsive() is True,
              "the bridge stopped answering - GetExpressions on a cooked material is a crash")
        # The WRITES must refuse, because there is no graph to write into.
        for ep, payload in (
            ("add_material_expression", {"material": cooked,
                                         "expressionClass": "MaterialExpressionConstant"}),
            ("layout_material_expressions", {"material": cooked}),
        ):
            q = M.call(ep, payload)
            check("T351 %s refuses on a cooked material" % ep, q.get("ok") is False,
                  json.dumps(q)[:150])
            check("T351 %s explains that the graph was stripped" % ep,
                  "cooked" in (q.get("error") or ""), (q.get("error") or "")[:170])
            check("T351 the editor survived %s" % ep, M.bridge_responsive() is True,
                  "the bridge stopped answering")

    # ------------------------------------------------------------------ T352 the authoring loop
    print("\n=== T352: authoring a graph on a fresh material ===")
    mpath = "/Game/_MifMat/M_%d" % st
    c = M.call("create_material", {"path": mpath})
    check("T352 a material is created", c.get("ok") is True, json.dumps(c)[:180])
    mp = c.get("materialPath")
    check("T352 it returns the material path", bool(mp), json.dumps(c)[:150])
    check("T352 and starts with no expressions", c.get("numExpressions") == 0, c.get("numExpressions"))
    if not mp:
        return 1
    check("T352 a fresh material is NOT cooked", graph(mp).get("cooked") is False,
          "a material created in this session cannot be cooked, and the flag must say so")

    col = M.call("add_material_expression", {"material": mp, "x": -400, "y": 0,
                                             "expressionClass": "MaterialExpressionConstant3Vector"})
    check("T352 an expression is added", col.get("ok") is True, json.dumps(col)[:180])
    name = col.get("expressionName")
    # Verified through the GRAPH, not from the add's own answer.
    g = graph(mp)
    check("T352 and it really appears in the graph",
          any(e.get("name") == name for e in (g.get("expressions") or [])),
          json.dumps(g.get("expressions"))[:200])
    check("T352 the count agrees", g.get("numExpressions") == 1, g.get("numExpressions"))

    mul = M.call("add_material_expression", {"material": mp, "x": -200, "y": 0,
                                             "expressionClass": "MaterialExpressionMultiply"})
    check("T352 a second expression is added", mul.get("ok") is True, json.dumps(mul)[:170])
    mulname = mul.get("expressionName")

    # ------------------------------------------------------------------ T353 wiring
    print("\n=== T353: wiring the graph ===")
    w = M.call("connect_material_expressions",
               {"material": mp, "fromExpression": name, "toExpression": mulname, "toInput": "A"})
    check("T353 two expressions connect", w.get("ok") is True, json.dumps(w)[:200])
    g = graph(mp)
    # A connection that reports success while connectionCount stays at zero is the failure worth
    # catching, and only the graph read can tell.
    check("T353 and the graph records the connection", (g.get("connectionCount") or 0) >= 1,
          "connectionCount=%s after a successful connect" % g.get("connectionCount"))

    b = M.call("connect_material_property",
               {"material": mp, "fromExpression": mulname, "property": "BaseColor"})
    check("T353 an expression binds to a material property", b.get("ok") is True, json.dumps(b)[:200])
    g = graph(mp)
    check("T353 and the binding is visible in the graph",
          bool(g.get("propertyBindings")), json.dumps(g.get("propertyBindings"))[:200])

    # ------------------------------------------------------------------ T354 recompile
    print("\n=== T354: recompiling ===")
    rc = M.call("recompile_material", {"material": mp})
    check("T354 the material recompiles", rc.get("ok") is True, json.dumps(rc)[:200])
    # Shader compilation is asynchronous, so the poll endpoint must exist and answer.
    sc = M.call("shader_compile_status", {})
    check("T354 and the async status can be polled", sc.get("ok") is True, json.dumps(sc)[:170])

    # ------------------------------------------------------------------ T355 delete and guards
    print("\n=== T355: deleting an expression, and guards ===")
    before = graph(mp).get("numExpressions")
    d = M.call("delete_material_expression", {"material": mp, "expression": name})
    if d.get("ok"):
        after = graph(mp).get("numExpressions")
        check("T355 deleting an expression removes it", after == before - 1,
              "%s -> %s" % (before, after))
    else:
        # Confirm-gated like the other destructive verbs; the refusal is what is reachable.
        check("T355 deletion is refused without confirm and says so",
              "confirm" in (d.get("error") or ""), (d.get("error") or "")[:170])
        check("T355 and the expression survives the refusal",
              graph(mp).get("numExpressions") == before, graph(mp).get("numExpressions"))

    # ------------------------------------------------------------------ T356 all=true means EMPTY
    # Reported 2026-08-27 from Curfew on stock 5.7: delete_material_expression(all=True) returned
    # ok and left three expressions behind. The engine's own DeleteAllMaterialExpressions iterates
    # a TConstArrayView over the LIVE array while removing from it, so each removal shifts the
    # remainder down and the loop steps past every other element. SOME survive, not none - which is
    # much harder to spot than a clean no-op, and is why it went unnoticed.
    #
    # This asserts the POSTCONDITION rather than the status, because ok:true was exactly what the
    # broken version returned. An all-clear that leaves anything behind is a failed clear.
    print("")
    print("=== T356: all=true has to actually empty the graph ===")
    for i in range(4):
        M.call("add_material_expression",
               {"material": mp, "expressionClass": "MaterialExpressionConstant",
                "x": -300, "y": i * 90})
    seeded = graph(mp).get("numExpressions") or 0
    check("T356 the graph has several expressions to clear", seeded >= 4, "numExpressions=%s" % seeded)

    c = M.call("delete_material_expression", {"material": mp, "all": True})
    left = graph(mp).get("numExpressions")
    # The endpoint now REFUSES a partial clear rather than reporting success, so either it emptied
    # the graph and said ok, or it did not and said so. Both are acceptable; ok:true with survivors
    # is the one thing that must never happen again.
    if c.get("ok") is True:
        check("T356 ok:true means the graph is actually empty", left == 0,
              "reported ok with numExpressions=%s" % left)
    else:
        check("T356 a partial clear is reported as a failure, not a success",
              "survived" in (c.get("error") or ""), (c.get("error") or "")[:200])
        check("T356 and the failure names what is left",
              str(left) in (c.get("error") or ""), (c.get("error") or "")[:200])
    check("T356 the clear removed something rather than nothing",
          (left or 0) < seeded, "%s -> %s" % (seeded, left))
    for label, payload, expect in (
        ("unknown expression class", {"material": mp, "expressionClass": "NoSuchExpr_zz"}, ""),
        ("missing material", {"material": "/Game/NoSuchMat_zz",
                              "expressionClass": "MaterialExpressionConstant"}, ""),
    ):
        q = M.call("add_material_expression", payload)
        check("T355 %s is refused" % label, q.get("ok") is False, json.dumps(q)[:150])
        check("T355 %s says something usable" % label, len(q.get("error") or "") > 15,
              (q.get("error") or "")[:150])
    q = M.call("connect_material_expressions",
               {"material": mp, "fromExpression": "NoSuchExpr_zz", "toExpression": mulname,
                "toInput": "A"})
    check("T355 connecting from an expression that does not exist is refused",
          q.get("ok") is False, json.dumps(q)[:160])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
