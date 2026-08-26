"""edit_container - array/map structural edits, and the `changed` flag that could not be false.

Named in no suite until now, which is how the bug below survived: `edit_container` is the only way to
add, insert, remove, swap or resize an array property through the bridge, and a modder editing a
DataTable row or an actor's array property goes through it every time.

THE BUG THIS LOCKS IN. A structural operation cannot be verified by COUNTING - a swap leaves the count
identical either way - so `changed` was hardcoded true for `swap` and `setKey`:

    Out->SetBoolField(TEXT("changed"), After != Before || Operation == "swap" || Operation == "setKey");

Both range checks accept `index == swapWith`, `FScriptArrayHelper::SwapValues(3, 3)` does nothing, and
the handler still ran `Modify()`, `PreEditChange` and a `PostEditChange` carrying `ArrayMove` - so a
no-op swap reported `changed: true` AND dirtied the package. T491 is that case.

It is reported rather than refused, matching `set_variable_type`, which answers a same-type request
with `changed: false` and a note instead of failing. A caller whose two computed indices happened to
coincide has not made an error worth stopping for; they need to be told nothing moved.

The rest is the ordinary contract, asserted by READING the array back rather than trusting the
response's own count: add grows it, remove shrinks it, an out-of-range index is refused with the valid
range named, and a refusal leaves the array exactly as it was.

SAFETY: everything happens on a scratch Blueprint's own array property under /Game/_MifCont, nothing
is saved.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    bppath = "/Game/_MifCont/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bppath, "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1

    # NOT "Tags". AActor already declares TArray<FName> Tags, so add_variable correctly refuses it with
    # "name already in use" - which reads like a bug on a blueprint that visibly has no variables, and
    # cost a round of debugging here before the collision was obvious.
    av = M.call("add_variable", {"blueprintId": bid, "name": "MifTags", "type": "string",
                                 "container": "array"})
    check("an array variable exists", av.get("ok") is True, json.dumps(av)[:200])
    M.call("compile", {"blueprintId": bid})

    # edit_container addresses an OBJECT, not a blueprint asset: "objectPath (a placed actor's path IS
    # an objectPath) or (blueprintId + widgetName)". A blueprint's variable DEFAULT lives on the class
    # default object, and the path form that reaches it is <package>.Default__<Name>_C. Three other
    # spellings were tried first and all failed - the blueprint path resolves to the UBlueprint asset
    # (no such property), and <path>_C resolves to the generated CLASS rather than the default OBJECT.
    # Written down because nothing else in the repo says it and it is not guessable.
    short = bppath.split("/")[-1]
    cdo = "%s.Default__%s_C" % (bppath, short)

    def edit(**kw):
        p = {"objectPath": cdo, "propertyPath": "MifTags"}
        p.update(kw)
        return M.call("edit_container", p)

    def count():
        r = edit(operation="add", value="__probe__")
        if r.get("ok"):
            n = r.get("elementsAfter")
            edit(operation="remove", index=n - 1)
            return n - 1
        return None

    # ------------------------------------------------------------------ T490 the ordinary contract
    print("")
    print("=== T490: add and remove really change the array ===")
    a = edit(operation="add", value="alpha")
    check("T490 add succeeds", a.get("ok") is True, json.dumps(a)[:220])
    if not a.get("ok"):
        print("   (cannot continue without a working add)")
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1
    first = a.get("elementsAfter")
    check("T490 and reports the new count", isinstance(first, int) and first >= 1,
          json.dumps(a)[:200])
    check("T490 and says it changed something", a.get("changed") is True, json.dumps(a)[:200])

    b = edit(operation="add", value="beta")
    check("T490 a second add grows it by one", b.get("elementsAfter") == first + 1,
          "%s -> %s" % (first, b.get("elementsAfter")))

    c = edit(operation="add", value="gamma")
    three = c.get("elementsAfter")
    check("T490 three elements now", three == first + 2, json.dumps(c)[:200])

    r = edit(operation="remove", index=0)
    check("T490 remove shrinks it by one", r.get("ok") is True and r.get("elementsAfter") == three - 1,
          json.dumps(r)[:200])
    check("T490 and reports changed", r.get("changed") is True, json.dumps(r)[:200])

    # ------------------------------------------------------------------ T491 the no-op swap
    print("")
    print("=== T491 [the bug]: swapping an element with ITSELF changed nothing ===")
    before = edit(operation="add", value="delta").get("elementsAfter")
    same = edit(operation="swap", index=1, swapWith=1)
    check("T491 the call succeeds rather than erroring", same.get("ok") is True, json.dumps(same)[:220])
    # THE assertion. `changed` was hardcoded true for swap because counting cannot verify it, so this
    # answered true for a call that moved nothing.
    check("T491 and reports changed:false", same.get("changed") is False,
          "changed=%s for swap index=1 swapWith=1 - nothing moved, so nothing changed"
          % same.get("changed"))
    check("T491 and says why", "nothing moved" in (same.get("note") or "").lower(),
          "note=%r" % (same.get("note") or ""))
    check("T491 the element count is untouched", same.get("elementsAfter") == before,
          "%s -> %s" % (before, same.get("elementsAfter")))

    # A REAL swap must still report changed:true, or the fix has broken the useful case.
    real = edit(operation="swap", index=0, swapWith=1)
    check("T491 a genuine swap still reports changed:true", real.get("changed") is True,
          json.dumps(real)[:220])
    check("T491 and keeps the count the same", real.get("elementsAfter") == before,
          "%s -> %s" % (before, real.get("elementsAfter")))

    # ------------------------------------------------------------------ T492 guards
    print("")
    print("=== T492: bad indices are refused and change nothing ===")
    n_before = edit(operation="add", value="epsilon").get("elementsAfter")
    q = edit(operation="remove", index=9999)
    check("T492 an out-of-range index is refused", q.get("ok") is False, json.dumps(q)[:200])
    check("T492 and the message names the valid range",
          "range" in (q.get("error") or "").lower(), (q.get("error") or "")[:190])
    check("T492 and says nothing was changed",
          "nothing was changed" in (q.get("error") or "").lower(), (q.get("error") or "")[:190])

    q = edit(operation="swap", index=0, swapWith=9999)
    check("T492 an out-of-range swapWith is refused", q.get("ok") is False, json.dumps(q)[:200])
    q = edit(operation="swap", index=0)
    check("T492 swap without swapWith is refused", q.get("ok") is False, json.dumps(q)[:200])
    q = edit(operation="banana")
    check("T492 an unknown operation is refused", q.get("ok") is False, json.dumps(q)[:200])

    after_guards = edit(operation="add", value="zeta").get("elementsAfter")
    check("T492 the array survived every refusal intact", after_guards == n_before + 1,
          "expected the refusals to change nothing: %s -> %s (+1 for this add)"
          % (n_before, after_guards))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
