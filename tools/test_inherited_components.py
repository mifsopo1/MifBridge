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
import scratch_confirm as SC

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

    # ------------------------------------------------------------------ T295 the batch counts
    print("\n=== T295: a MIXED batch is refused WHOLE, and the counts say so ===")
    # WHY THIS AND NOT JUST THE ERROR TEXT. T291 already asserts the message names the property that
    # did not apply. What nothing asserted was the four COUNTS - propertiesRequested, Applied,
    # Failed, Unchanged - which are how a caller with a twenty-property batch learns that NONE of it
    # landed rather than reading a sentence and guessing. They were among 48 consequence-reporting
    # response fields that no suite named, found 2026-08-31.
    #
    # The property being pinned is ATOMICITY. PreflightProperties type-checks every value against the
    # parent archetype BEFORE any override is minted, so one bad value costs the whole batch. That is
    # a promise worth a test: a caller who sees propertiesApplied 0 can retry the corrected batch
    # without wondering which half already took.
    before = inherited(child)
    before_count = before.get("existingOverrideCount") or 0
    mixed = M.call("override_inherited_component",
                   {"blueprintId": child, "component": "Body",
                    "properties": {"RelativeLocation": "(X=1.000000,Y=2.000000,Z=3.000000)",
                                   "NoSuchProperty_zz": "irrelevant"}})
    check("T295 a batch with one bad value is REFUSED - not partially applied",
          mixed.get("ok") is False, json.dumps(mixed)[:240])
    check("T295 propertiesRequested counts BOTH", mixed.get("propertiesRequested") == 2,
          "propertiesRequested=%r" % mixed.get("propertiesRequested"))
    check("T295 propertiesApplied is 0 - the valid one did NOT land, which is the whole point of "
          "type-checking before minting anything",
          mixed.get("propertiesApplied") == 0, "propertiesApplied=%r" % mixed.get("propertiesApplied"))
    check("T295 propertiesFailed names the one that was bad", mixed.get("propertiesFailed") == 1,
          "propertiesFailed=%r" % mixed.get("propertiesFailed"))
    check("T295 and propertiesUnchanged is 0 - a batch that touched nothing must not report values "
          "as 'unchanged', which would read as 'already correct'",
          mixed.get("propertiesUnchanged") == 0,
          "propertiesUnchanged=%r" % mixed.get("propertiesUnchanged"))
    check("T295 nothingModified is stated as a FIELD, not left to the prose",
          mixed.get("nothingModified") is True, json.dumps(mixed)[:220])
    check("T295 and the outcome names the stage that rejected it",
          "preflight" in (mixed.get("outcome") or ""), "outcome=%r" % mixed.get("outcome"))
    rows = mixed.get("properties") or []
    check("T295 properties[] carries a per-property reason, so a twenty-property batch does not "
          "need the error sentence parsed",
          any("NoSuchProperty_zz" in json.dumps(r) for r in rows), json.dumps(rows)[:240])
    after = inherited(child)
    check("T295 and the blueprint is exactly as it was - the override count did not move",
          (after.get("existingOverrideCount") or 0) == before_count,
          "before=%s after=%s" % (before_count, after.get("existingOverrideCount")))

    # ------------------------------------------------------------------ T293 revert
    print("\n=== T293: reverting removes it again ===")
    # THE NOTE THAT USED TO STAND HERE CALLED THE SUCCESS PATH A PERMANENT COVERAGE GAP, on the
    # grounds that the harness strips `confirm` from every payload and bypassing that unattended
    # would defeat the point of the guard. Both halves were true and the conclusion was not:
    # scratch_confirm.py sends confirm ONLY for a payload whose every path is under /Game/_Mif, and
    # this suite's fixtures are exactly that - it already imports SC for the same reason elsewhere.
    # The gap was permanent only until something safe existed, which is the same correction
    # test_widget_tree and test_uncovered_reads5 already carry.
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

    # ---- T293b the SUCCESS path, through the sanctioned route
    print("\n=== T293b: the real revert, and the two fields that describe what it removed ===")
    real = SC.confirm_call("revert_inherited_component",
                           {"blueprintId": child, "component": "Body", "confirm": True})
    check("T293b the revert succeeds with confirm", real.get("ok") is True, json.dumps(real)[:240])
    # reverted and removedTemplatePath are the endpoint's account of a DESTRUCTIVE act - it discards
    # every property overridden on that component in one step - and nothing read either of them.
    # removedTemplatePath matters more than it looks: the note on this response says the removed
    # template is MarkAsGarbage'd and that the flag is NOT transaction-recorded, so Ctrl-Z will not
    # bring it back. The path is the only record of what existed.
    check("T293b and reports reverted:true rather than a bare ok",
          real.get("reverted") is True, json.dumps(real)[:240])
    check("T293b and NAMES the template it removed",
          isinstance(real.get("removedTemplatePath"), str)
          and "Body" in real.get("removedTemplatePath", ""),
          "removedTemplatePath=%r" % real.get("removedTemplatePath"))
    check("T293b and says how many overrides are left",
          real.get("remainingOverrideCount") == 0,
          "remainingOverrideCount=%r" % real.get("remainingOverrideCount"))
    check("T293b and names the parent template it now falls back to",
          isinstance(real.get("fallsBackTo"), str) and real.get("fallsBackTo"),
          "fallsBackTo=%r" % real.get("fallsBackTo"))
    # THE POSTCONDITION, read through a different endpoint. The response's own word is not evidence.
    gone = inherited(child)
    check("T293b and get_inherited_component agrees the override is gone",
          gone.get("overrideExists") is False, json.dumps(gone)[:200])
    check("T293b the claim and the read-back do not disagree",
          (real.get("reverted") is True) == (gone.get("overrideExists") is False),
          "reverted=%r overrideExists=%r" % (real.get("reverted"), gone.get("overrideExists")))

    # ---- T293c reverting what is not there reports reverted:FALSE, not a bare failure
    print("\n=== T293c: nothing to revert - the flag says so instead of only the error ===")
    again = SC.confirm_call("revert_inherited_component",
                            {"blueprintId": child, "component": "Body", "confirm": True})
    check("T293c a second revert is refused", again.get("ok") is False, json.dumps(again)[:240])
    # A caller that branches on the flag rather than parsing prose needs the flag to be present on
    # BOTH outcomes. It is, and this is the only assertion of the false case anywhere.
    check("T293c and reverted is present and FALSE, not absent",
          again.get("reverted") is False,
          "reverted=%r - an absent field would read as 'not false' to `is not False`"
          % again.get("reverted"))
    check("T293c and it still names the parent template being read from",
          isinstance(again.get("fallsBackTo"), str) and again.get("fallsBackTo"),
          json.dumps(again)[:220])
    c = M.call("compile", {"blueprintId": child})
    check("T293c and the child compiles after the revert",
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

    SC.confirm_call("delete_asset", {"path": child_path})
    SC.confirm_call("delete_asset", {"path": parent_path})
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
