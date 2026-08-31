"""Calling the same add_* twice: does the second one refuse, or quietly make a second thing?

A setup script gets re-run. That is not a hypothetical - it is the normal way this bridge is used: a
recipe is edited and replayed, a suite runs twice in one editor session, a step is retried after a
failure somewhere further down. So "what does the second identical call do" is a question every
add_* endpoint has to answer, and the answers were not the same.

add_variable, create_function and add_event_dispatcher all refuse a name that is taken, and say so.
add_component did not. It ran the requested name through the engine's GenerateNewComponentName, which
returns "Turret1" when "Turret" is taken, and reported ok:true - for a component the caller never
asked for. The response does carry the real name, but nothing in it says the name differs from the
request, so a caller who does not compare believes they have "Turret". Run the script twice and the
Blueprint quietly grows Turret, Turret1, Turret2, each one a component nobody asked for.

That is the same repeat-run trap that broke five suites in one night: state surviving between runs,
invisible on the first pass.

WHAT EACH CASE ASSERTS. Not "the second call failed" - that is a policy, and refusing is not the only
defensible one. What matters is:

  1. the COUNT did not grow. Whatever the second call answers, it must not leave a second thing behind.
  2. if it refused, the message names the collision, because "failed to add" with no reason sends the
     caller looking in the wrong place.
  3. the Blueprint still compiles, since a duplicate is only interesting because of what it breaks.

And the omitted-name path is checked separately and must KEEP auto-naming. "name" is optional and
means "engine, pick one" - tightening the explicit-name case must not take that away.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def twice(label, ep, payload, counter):
    """Call an endpoint twice with identical arguments and assert nothing was duplicated."""
    first = M.call(ep, payload)
    check("%s the first call succeeds" % label, first.get("ok") is True, json.dumps(first)[:170])
    n1 = counter()
    second = M.call(ep, payload)
    n2 = counter()

    # THE assertion. Refuse or no-op are both fine; a second thing is not.
    check("%s a second identical call adds nothing" % label, n2 == n1,
          "count went %s -> %s: the second call left another one behind" % (n1, n2))
    if second.get("ok") is False:
        check("%s and the refusal explains the collision" % label,
              any(w in (second.get("error") or "").lower()
                  for w in ("already", "in use", "exists", "taken")),
              (second.get("error") or "")[:170])
    else:
        # Succeeded without duplicating - acceptable, but it must not be reporting a DIFFERENT name
        # than the one asked for without saying so.
        asked = payload.get("name")
        got = second.get("component") or second.get("name")
        check("%s and it did not silently substitute a different name" % label,
              asked is None or got is None or asked == got,
              "asked for '%s', got '%s', and the response does not flag the difference" % (asked, got))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    bid = M.call("create_blueprint", {"path": "/Game/_MifIdem/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1

    nvars = lambda: len(M.call("list_variables", {"blueprintId": bid}).get("variables") or [])
    ncomp = lambda: len(M.call("list_components", {"blueprintId": bid}).get("components") or [])
    ngraf = lambda: len(M.call("list_graphs", {"blueprintId": bid}).get("graphs") or [])
    ndisp = lambda: len(M.call("list_dispatchers", {"blueprintId": bid}).get("dispatchers") or [])

    print("")
    print("=== T380: the add_* family must agree with itself ===")
    twice("T380 add_variable", "add_variable",
          {"blueprintId": bid, "name": "Price", "type": "float"}, nvars)
    twice("T380 add_component", "add_component",
          {"blueprintId": bid, "componentClass": "SceneComponent", "name": "Turret"}, ncomp)
    twice("T380 create_function", "create_function",
          {"blueprintId": bid, "name": "Recalc"}, ngraf)
    twice("T380 add_event_dispatcher", "add_event_dispatcher",
          {"blueprintId": bid, "name": "OnSold"}, ndisp)

    print("")
    print("=== T381: an OMITTED name still means 'engine, pick one' ===")
    # The explicit-name tightening must not reach this path. Two components of the same class with no
    # name given are a normal thing to want, and they must still both appear.
    before = ncomp()
    a = M.call("add_component", {"blueprintId": bid, "componentClass": "StaticMeshComponent"})
    b = M.call("add_component", {"blueprintId": bid, "componentClass": "StaticMeshComponent"})
    check("T381 both unnamed components are added", a.get("ok") is True and b.get("ok") is True,
          "%s / %s" % (json.dumps(a)[:80], json.dumps(b)[:80]))
    check("T381 and the count grew by two", ncomp() == before + 2,
          "%s -> %s" % (before, ncomp()))
    check("T381 with distinct engine-chosen names", a.get("component") != b.get("component"),
          "%s vs %s" % (a.get("component"), b.get("component")))

    print("")
    print("=== T383: an unrecognised `scope` is refused, not silently reinterpreted ===")
    # add_variable and set_variable_type both did Scope.Equals("local") and treated EVERYTHING else as
    # member. So scope:"loca1" silently created a MEMBER variable - and add_variable then echoed the
    # request straight back, answering scope:"loca1" for a variable that was nothing of the sort. The
    # documented values are member|local; anything else is a caller mistake worth naming.
    ok_member = M.call("add_variable", {"blueprintId": bid, "name": "ScopeOk_%d" % st,
                                        "type": "float", "scope": "member"})
    check("T383 scope:member still works", ok_member.get("ok") is True, json.dumps(ok_member)[:170])
    # The response must report the RESOLVED scope, not the string it was handed.
    check("T383 and reports the resolved scope", ok_member.get("scope") == "member",
          "scope=%r" % ok_member.get("scope"))

    for bad in ("loca1", "banana", "function"):
        q = M.call("add_variable", {"blueprintId": bid, "name": "Bad_%s_%d" % (bad, st),
                                    "type": "float", "scope": bad})
        check("T383 add_variable refuses scope:%r" % bad, q.get("ok") is False,
              "it was accepted and quietly became a member variable: %s" % json.dumps(q)[:150])
        check("T383 and names the values that work", "member" in (q.get("error") or "")
              and "local" in (q.get("error") or ""), (q.get("error") or "")[:170])

    # set_variable_type shares the same parameter and had the same hole.
    M.call("add_variable", {"blueprintId": bid, "name": "Retype_%d" % st, "type": "float"})
    q = M.call("set_variable_type", {"blueprintId": bid, "name": "Retype_%d" % st,
                                     "type": "int", "scope": "loca1"})
    check("T383 set_variable_type refuses it too", q.get("ok") is False, json.dumps(q)[:170])

    print("")
    print("=== T384: the same trap INSIDE one call - two parameters asking for one name ===")
    # This suite's premise is that a caller who does not compare believes they got the name they
    # asked for, and add_component was the example: "Turret" becomes "Turret1" with ok:true. The
    # same thing happens WITHIN a single create_function call, because CreateUserDefinedPin runs
    # with bUseUniqueName true - so two outputs both called "Same" come back as Same and Same1, and
    # nothing about the call failed.
    #
    # It is reported, and reported well: pinsRenamed names the mapping and pinsRenamedNote says
    # outright "Wire the names in inputNames/outputNames, not the ones you asked for". Nothing read
    # either field until now, which is the part that made it worth asserting - advice a caller never
    # reads is the same as advice that was never written.
    dup = M.call("create_function", {"blueprintId": bid, "name": "MifDupOut%d" % st,
                                     "outputs": [{"name": "Same", "type": "int"},
                                                 {"name": "Same", "type": "int"}]})
    check("T384 the call SUCCEEDS - the collision is resolved, not refused",
          dup.get("ok") is True, json.dumps(dup)[:220])
    names = dup.get("outputNames") or []
    check("T384 and both outputs exist", len(names) == 2, names)
    # THE ASSERTION THAT MATTERS. The caller asked for two pins called Same and has one that is not.
    check("T384 but the second is NOT the name that was asked for",
          "Same" in names and names != ["Same", "Same"], names)
    check("T384 and pinsRenamed names the mapping rather than leaving the caller to diff",
          "Same" in str(dup.get("pinsRenamed") or ""), dup.get("pinsRenamed"))
    check("T384 and the note tells the caller which names to actually wire",
          "outputNames" in str(dup.get("pinsRenamedNote") or ""),
          str(dup.get("pinsRenamedNote"))[:220])
    # And duplicatePinsRemoved must stay ABSENT: the engine renamed rather than duplicating, so
    # there was nothing for the self-healing pass to remove. Its presence here would mean two pins
    # really were made with one name.
    check("T384 and nothing was deduplicated, because nothing was duplicated",
          dup.get("duplicatePinsRemoved") is None,
          "duplicatePinsRemoved=%r - two pins shared a name after all"
          % dup.get("duplicatePinsRemoved"))

    print("=== T382: none of it broke the blueprint ===")
    c = M.call("compile", {"blueprintId": bid})
    check("T382 the blueprint compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s warnings=%s" % (c.get("numErrors"), c.get("numWarnings")))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
