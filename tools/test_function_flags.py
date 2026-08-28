"""Function flags - and the gap between "authored" and "in effect".

set_function_flags writes to the function's ENTRY NODE. What executes, and what describe_class
reflects, is the GENERATED CLASS - and that does not change until the blueprint is compiled. So for a
window after every call, two readers of the same fact disagree:

    set pure:true      -> set_function_flags says flags.pure = true      (the entry node, immediately)
    ask describe_class -> isPure = false                                 (the generated class, stale)
    compile            -> isPure = true                                  (they agree)

Measured in that order, and that is exactly what a caller does: set the flag, then check it took. Read
without knowing about the compile step, the second line looks like the write silently failed - the
failure this project keeps finding, arrived at from the opposite direction. Nothing was wrong; the
answer was just early.

The endpoint now reports needsCompileToApply, the same way the widget-tree endpoints do for the same
situation, and T452 is what keeps it saying so.

The rest is the ordinary contract: a flag that reports set must BE set, an invalid value must be
refused rather than quietly dropped, and the response's flag block must reflect what the function
actually carries rather than echoing the request.
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
    st = int(time.time() % 100000)

    bid = M.call("create_blueprint", {"path": "/Game/_MifFn/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1
    fn = M.call("create_function", {"blueprintId": bid, "name": "Calc",
                                    "inputs": [{"name": "In", "type": "float"}],
                                    "outputs": [{"name": "Out", "type": "float"}]})
    check("a function exists to flag", fn.get("ok") is True, json.dumps(fn)[:180])
    M.call("compile", {"blueprintId": bid})

    def described(field="isPure"):
        d = M.call("describe_class", {"class": bid})
        for f in (d.get("functions") or []):
            if f.get("name") == "Calc":
                return f.get(field)
        return None

    # ------------------------------------------------------------------ T450 list_functions
    print("")
    print("=== T450: the function is visible before anything is changed ===")
    lf = M.call("list_functions", {"blueprintId": bid})
    names = [f.get("name") for f in (lf.get("functions") or [])]
    check("T450 list_functions answers", lf.get("ok") is True, json.dumps(lf)[:180])
    check("T450 and includes the new function", "Calc" in names, str(names))
    check("T450 and gives a graphId for it",
          any(f.get("graphId") for f in (lf.get("functions") or []) if f.get("name") == "Calc"),
          json.dumps(lf)[:220])

    # ------------------------------------------------------------------ T451 flags apply
    print("")
    print("=== T451: a flag that reports set must be set ===")
    r = M.call("set_function_flags", {"blueprintId": bid, "name": "Calc", "pure": True,
                                      "access": "protected", "category": "Math"})
    check("T451 setting flags succeeds", r.get("ok") is True, json.dumps(r)[:220])
    flags = r.get("flags") or {}
    # The response must report the function's ACTUAL flags, not echo the request - an echo would be
    # true no matter what happened.
    check("T451 the response reports the whole flag block", isinstance(flags, dict) and len(flags) >= 4,
          json.dumps(flags)[:200])
    check("T451 pure is set", flags.get("pure") is True, json.dumps(flags)[:200])
    check("T451 access is set", flags.get("access") == "protected", json.dumps(flags)[:200])
    # A second read through the same endpoint, changing nothing, must agree with the first.
    again = M.call("set_function_flags", {"blueprintId": bid, "name": "Calc", "pure": True})
    check("T451 and reading it back agrees", (again.get("flags") or {}).get("pure") is True,
          json.dumps(again.get("flags"))[:200])

    # ------------------------------------------------------------------ T452 authored vs effective
    print("")
    print("=== T452 [the point]: authored now, effective after a compile - and it says so ===")
    check("T452 the response says a compile is needed", r.get("needsCompileToApply") is True,
          "without this, a caller who checks describe_class next sees the OLD value and concludes the "
          "write failed")

    fresh = M.call("create_function", {"blueprintId": bid, "name": "Calc2_%d" % st})
    if fresh.get("ok"):
        M.call("compile", {"blueprintId": bid})

        def described2():
            d = M.call("describe_class", {"class": bid})
            for f in (d.get("functions") or []):
                if f.get("name") == "Calc2_%d" % st:
                    return f.get("isPure")
            return None

        before = described2()
        s2 = M.call("set_function_flags", {"blueprintId": bid, "name": "Calc2_%d" % st, "pure": True})
        mid = described2()
        check("T452 the setter reports it immediately", (s2.get("flags") or {}).get("pure") is True,
              json.dumps(s2.get("flags"))[:180])
        # This is the disagreement the flag exists to explain. It is NOT asserted as a failure - it is
        # asserted as the documented behaviour, so that if it ever stops being true the note becomes
        # wrong and someone finds out here.
        check("T452 describe_class still shows the pre-compile value", mid is False,
              "before=%s mid=%s - if describe_class now updates without a compile, "
              "needsCompileToApply is over-reporting and the comment should change" % (before, mid))
        M.call("compile", {"blueprintId": bid})
        check("T452 and after a compile the two agree", described2() is True,
              "describe_class still says %s after compiling" % described2())

    # ------------------------------------------------------------------ T453 guards
    print("")
    print("=== T453: values that cannot be applied are refused, not dropped ===")
    q = M.call("set_function_flags", {"blueprintId": bid, "name": "Calc", "static": True})
    check("T453 a read-only flag is refused rather than ignored", q.get("ok") is False,
          json.dumps(q)[:200])
    check("T453 and explains why", "read-only" in (q.get("error") or "").lower()
          or "not editable" in (q.get("error") or "").lower(), (q.get("error") or "")[:190])

    q = M.call("set_function_flags", {"blueprintId": bid, "name": "Calc", "access": "banana"})
    check("T453 an invalid access level is refused", q.get("ok") is False, json.dumps(q)[:190])
    q = M.call("set_function_flags", {"blueprintId": bid, "name": "NoSuchFunction_zz", "pure": True})
    check("T453 an unknown function is refused", q.get("ok") is False, json.dumps(q)[:190])

    c = M.call("compile", {"blueprintId": bid})
    check("T453 the blueprint still compiles",
          c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    SC.confirm_call("delete_asset", {"path": "/Game/_MifFn/BP_%d" % st})
    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("NOTED, not a defect: list_functions returns name and graphId only, so a function's FLAGS")
    print("cannot be audited without calling the setter. describe_class shows isPure/isStatic but only")
    print("post-compile, and neither shows access, category or the replication settings.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
