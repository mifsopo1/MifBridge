"""Inherited components - a family with a postmortem and, until now, no regression test.

PM-007 records that a FAILED override_inherited_component permanently added an override:
`{ok:false}` came back, and get_inherited_component then reported `overrideExists: true`. The child
silently shadowed its parent for that component, with no undo entry to reverse it - because the
handler minted the ICH override template BEFORE validating the property values, and a cancelled
transaction does not roll anything back (FTransaction::Cancel discards the undo entry without calling
Apply).

The fix is in the handler today: every value is type-checked against the parent's template before any
override is minted, and the failure message says "NOTHING WAS CREATED OR MODIFIED". But nothing tested
it. A documented fix with no regression test is a fix that can be quietly undone by the next person
who reorders that function, and the symptom is invisible from the caller's side - the call correctly
says it failed.

T291 is that regression test and the reason this file exists. It does not assert that a bad call
fails; it asserts that a bad call leaves NO OVERRIDE BEHIND, which is a different question and the
one PM-007 was about.

The fixtures build a real parent/child pair rather than borrowing a game asset: the whole family only
applies to a component inherited from a parent BLUEPRINT's SCS, and a native C++ component takes a
different route entirely (the handler says so and points elsewhere).
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def inherited(child, comp="Body"):
    return M.call("get_inherited_component", {"blueprintId": child, "component": comp})


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    parent_path = "/Game/_MifIC/BP_P_%d" % st
    child_path = "/Game/_MifIC/BP_C_%d" % st
    parent = M.call("create_blueprint", {"path": parent_path, "parentClass": "Actor"}).get("blueprintId")
    if not parent:
        print("setup failed: no parent blueprint")
        return 3
    M.call("add_component", {"blueprintId": parent, "componentClass": "StaticMeshComponent",
                             "name": "Body"})
    M.call("compile", {"blueprintId": parent})
    child = M.call("create_blueprint", {"path": child_path, "parentClass": parent}).get("blueprintId")
    if not child:
        print("setup failed: no child blueprint")
        return 3
    print("parent %s\nchild  %s" % (parent, child))

    # ------------------------------------------------------------------ T290 the read
    print("\n=== T290: reading an inherited component ===")
    g = inherited(child)
    check("T290 the child can see the inherited component", g.get("ok") is True, json.dumps(g)[:180])
    check("T290 it knows the component came from the parent's SCS",
          g.get("origin") == "parentBlueprintSCS", g.get("origin"))
    check("T290 and names the parent that owns it",
          parent_path.split("/")[-1] in (g.get("ownerClass") or ""), g.get("ownerClass"))
    check("T290 it reports the component class", "StaticMeshComponent" in (g.get("componentClass") or ""),
          g.get("componentClass"))
    check("T290 no override exists yet", g.get("overrideExists") is False, g.get("overrideExists"))
    check("T290 and it says an override is possible", g.get("canOverride") is True, g.get("canOverride"))
    # The template path is what set_property needs; a read that omits it sends the caller hunting.
    check("T290 it hands back the parent template path to work against",
          bool(g.get("parentTemplatePath")), g.get("parentTemplatePath"))

    # ------------------------------------------------------------------ T291 the PM-007 regression
    print("\n=== T291 [PM-007]: a FAILED override must leave nothing behind ===")
    # FOUR shapes, because there are two independent rejection paths and each has a partial form.
    # A value can fail the PRE-FLIGHT type check - PM-007's path, message "are invalid ... NOTHING WAS
    # CREATED OR MODIFIED" - or pass pre-flight and be refused by the ENGINE at apply time, message
    # "did not apply", which promises nothing about what was left behind. The partial forms matter
    # most: one good property alongside one bad is the shape a whole-batch check could wave through
    # while leaving a half-applied override.
    cases = [
        ("pre-flight, unknown property", {"NoSuchProperty_zz": "1"}, True),
        ("pre-flight, text into a float", {"MinDrawDistance": "not-a-number"}, True),
        ("pre-flight, PARTIAL", {"MinDrawDistance": "50.0", "bVisible": "(X=1,Y=2)"}, True),
        ("engine-apply, garbage vector", {"RelativeScale3D": "@@@"}, False),
        ("engine-apply, PARTIAL", {"MinDrawDistance": "75.0", "RelativeScale3D": "@@@"}, False),
    ]
    for label, props, preflight in cases:
        r = M.call("override_inherited_component",
                   {"blueprintId": child, "component": "Body", "properties": props})
        check("T291 %s is refused" % label, r.get("ok") is False, json.dumps(r)[:170])
        # THE assertion, for every shape. PM-007's symptom was ok:false followed by
        # overrideExists:true, and a partial batch is where that would hide.
        st = inherited(child)
        check("T291 %s leaves NO override (PM-007)" % label,
              st.get("overrideExists") is False and (st.get("existingOverrideCount") or 0) == 0,
              "overrideExists=%s count=%s - a failed call left an override behind, which is PM-007 "
              "reopening" % (st.get("overrideExists"), st.get("existingOverrideCount")))
        err = r.get("error") or ""
        if preflight:
            # Only the pre-flight path promises this, and it should keep promising it.
            check("T291 %s says nothing was modified" % label,
                  "NOTHING WAS CREATED OR MODIFIED" in err, err[:200])
        else:
            # The engine-apply path names what did not stick rather than promising cleanliness.
            check("T291 %s names the property that did not apply" % label,
                  "did not apply" in err and list(props)[-1] in err, err[:200])

    # ------------------------------------------------------------------ T292 the success path
    print("\n=== T292: a valid override applies and is visible ===")
    good = M.call("override_inherited_component",
                  {"blueprintId": child, "component": "Body",
                   "properties": {"RelativeLocation": "(X=10.000000,Y=20.000000,Z=30.000000)"}})
    check("T292 a valid override succeeds", good.get("ok") is True, json.dumps(good)[:220])
    if good.get("ok"):
        now = inherited(child)
        check("T292 and the override is now visible", now.get("overrideExists") is True,
              json.dumps(now)[:200])
        check("T292 the override count went up",
              (now.get("existingOverrideCount") or 0) >= 1, now.get("existingOverrideCount"))
        check("T292 and an ICH now exists on the child",
              bool(now.get("inheritableComponentHandlerPath")),
              now.get("inheritableComponentHandlerPath"))
        c = M.call("compile", {"blueprintId": child})
        check("T292 the child still compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
              "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T293 revert
    print("\n=== T293: reverting removes it again ===")
    # CONFIRM-GATED, so only the refusal is reachable from here: the audit harness strips `confirm`
    # from every payload alongside `save` and `force`, and bypassing that on an unattended run would
    # defeat the point of having it. The success path is a stated coverage gap, like the DataTable
    # writes.
    rev = M.call("revert_inherited_component", {"blueprintId": child, "component": "Body"})
    check("T293 revert refuses without confirm", rev.get("ok") is False, json.dumps(rev)[:200])
    check("T293 and says why it is gated",
          "confirm" in (rev.get("error") or "") and "EVERY property" in (rev.get("error") or ""),
          (rev.get("error") or "")[:200])
    # The refusal must not have touched the override it declined to remove.
    back = inherited(child)
    check("T293 the override survives a refused revert",
          back.get("overrideExists") is True, json.dumps(back)[:180])
    c = M.call("compile", {"blueprintId": child})
    check("T293 and the child still compiles",
          c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T294 guards
    print("\n=== T294: guards ===")
    q = M.call("get_inherited_component", {"blueprintId": child})
    check("T294 a missing component name is refused", q.get("ok") is False, json.dumps(q)[:150])
    check("T294 and says it is required", "required" in (q.get("error") or "").lower(),
          (q.get("error") or "")[:150])

    # An unknown name is NOT a refusal here, and should not be. The query ran; the answer is that the
    # component is not there, and the response says so with origin:"notFound" and canOverride:false.
    # That is the same shape as get_referencers reporting packageExists:false - qualifying an answer
    # rather than failing. Asserting a refusal here was wrong.
    g = M.call("get_inherited_component", {"blueprintId": child, "component": "NoSuch_zz"})
    check("T294 an unknown component is ANSWERED, not refused", g.get("ok") is True, json.dumps(g)[:150])
    check("T294 and the answer says it was not found", g.get("origin") == "notFound", g.get("origin"))
    check("T294 and that it cannot be overridden", g.get("canOverride") is False, g.get("canOverride"))
    # A component declared in THIS blueprint is not inherited - the handler must send you elsewhere
    # rather than minting a meaningless override.
    M.call("add_component", {"blueprintId": child, "componentClass": "SceneComponent", "name": "Own"})
    M.call("compile", {"blueprintId": child})
    own = M.call("override_inherited_component",
                 {"blueprintId": child, "component": "Own", "properties": {"bVisible": "false"}})
    check("T294 a component owned by this blueprint is refused with the right advice",
          own.get("ok") is False and "set_property" in (own.get("error") or ""),
          (own.get("error") or "")[:200])

    M.call("delete_asset", {"path": child_path})
    M.call("delete_asset", {"path": parent_path})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("COVERAGE GAP, deliberate: revert_inherited_component's SUCCESS path is not exercised,")
    print("because it requires confirm=true and the audit harness strips confirm. Close it with the")
    print("guard relaxed against a scratch blueprint, not by weakening the guard.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
