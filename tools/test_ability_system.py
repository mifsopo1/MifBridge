"""describe_ability_system - and what a reflective read genuinely cannot do.

T9100 IS THE JUSTIFICATION, MEASURED. The backlog entry claimed several things were unreachable by
property path; the vetter struck two of them, and checking settled it:

    get_property {actorPath, "ASC.ActivatableAbilities"}   FAILS - an SCS component is not a
                                                           UPROPERTY on the actor by that name.
    get_property {objectPath "<actor>.ASC", "..."}         WORKS, returning EXPORT TEXT:
                                                           "(Items=,Owner=\"ASC\",ArrayReplicationKey=1)"

So this endpoint is not about unreachable data. It is about structured rows instead of export text,
and about the attribute NUMBERS, which reflection cannot produce at all: GetAllAttributes,
GetNumericAttributeBase and GetNumericAttribute are FUNCTION CALLS and no property walk makes a
call. T9100 asserts both halves of that - that reflection returns text, and that this returns typed
rows.

THE FIXTURE IS BUILT, NOT FOUND, and that is the correction that unblocked this item. This project
has zero GAS content - 0 AttributeSets, GameplayAbilities, GameplayEffects and ASCs, measured with
find_assets `class` plus recursiveClasses. But GameplayAbilities is ENABLED and an
AbilitySystemComponent can be added to a scratch Actor blueprint, which spawns into a live actor
carrying a live ASC. Checking whether assets EXISTED without checking whether one could be CREATED
is what kept this item deferred for a whole session.

WHAT STAYS UNEXERCISED, reported rather than skipped: a populated ASC. Attributes come from an
AttributeSet the owner spawns, abilities are granted at runtime and effects applied at runtime, so
an editor-spawned ASC answers every read and holds nothing. T9102 asserts the endpoint SAYS that
rather than returning bare zeroes, because rows of zeroes read as "this character has no ability
system" - a different and wrong conclusion.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    st = int(time.time()) % 100000
    bp = "/Game/_MifGAS/BP_ASCFix%d" % st
    actor = None
    try:
        made = M.raw_post("create_blueprint", {"path": bp, "parentClass": "Actor"})
        bid = made.get("blueprintId")
        check("(setup) a scratch Actor blueprint", made.get("ok") is not False and bool(bid),
              json.dumps(made)[:220])
        if not bid:
            return 1
        comp = M.raw_post("add_component", {"blueprintId": bid,
                                            "class": "AbilitySystemComponent", "name": "ASC"})
        check("(setup) an AbilitySystemComponent can be added - the fixture is BUILT, not found, "
              "which is what makes this endpoint testable on a project with zero GAS content",
              comp.get("ok") is not False, json.dumps(comp)[:220])
        M.raw_post("compile_blueprint", {"blueprintId": bid})

        # The FULL class path. The bare asset path is refused, and finding that out cost a probe.
        q = SC.spawn_tracked("spawn_actor_in_level", {
            "class": "%s.%s_C" % (bp, bp.rsplit("/", 1)[1]),
            "location": {"x": 1962000 + st, "y": 1962000 + st, "z": 50000},
            "label": "MifASCTest%d" % st})
        actor = ((q.get("actor") or {}).get("actorPath")) or q.get("actorPath")
        check("(setup) it spawns into the level as a live actor", bool(actor), json.dumps(q)[:250])
        if not actor:
            return 1

        # ------------------------------------------------------------------ T9100 the justification
        print("\n=== T9100: what reflection reaches, and what it cannot ===")
        viaactor = M.raw_post("get_property", {"actorPath": actor,
                                               "property": "ASC.ActivatableAbilities"})
        check("T9100 get_property CANNOT reach the component from the actor - an SCS component is "
              "not a UPROPERTY by that name",
              viaactor.get("ok") is False, json.dumps(viaactor)[:200])
        viacomp = M.raw_post("get_property", {"objectPath": actor + ".ASC",
                                              "property": "ActivatableAbilities"})
        check("T9100 addressed as the component it DOES reach it - so this endpoint is not about "
              "unreachable data",
              viacomp.get("ok") is not False, json.dumps(viacomp)[:200])
        # THE distinction. Reflection hands back export text; a caller would have to parse it.
        check("T9100 but reflection returns EXPORT TEXT, not structured data",
              isinstance(viacomp.get("value"), str) and "(" in str(viacomp.get("value")),
              json.dumps(viacomp.get("value"))[:200])

        d = M.raw_post("describe_ability_system", {"actorPath": actor})
        check("T9100 describe_ability_system finds the component", d.get("ok") is True,
              json.dumps(d)[:250])
        check("T9100 and returns TYPED collections rather than a string",
              isinstance(d.get("abilities"), list) and isinstance(d.get("attributes"), list)
              and isinstance(d.get("ownedTags"), list), json.dumps(d)[:250])
        check("T9100 it says which route found the component, because 'via the interface' and "
              "'found a component' are different facts about the actor",
              d.get("foundVia") in ("IAbilitySystemInterface", "FindComponentByClass",
                                    "the path names the component itself"), d.get("foundVia"))

        # ------------------------------------------------------------------ T9101 the guards
        print("\n=== T9101: the refusals ===")
        plain = M.raw_post("create_blueprint", {"path": "/Game/_MifGAS/BP_NoASC%d" % st,
                                                "parentClass": "Actor"})
        pb = plain.get("blueprintId")
        M.raw_post("compile_blueprint", {"blueprintId": pb})
        q2 = SC.spawn_tracked("spawn_actor_in_level", {
            "class": "/Game/_MifGAS/BP_NoASC%d.BP_NoASC%d_C" % (st, st),
            "location": {"x": 1963000 + st, "y": 1963000 + st, "z": 50000},
            "label": "MifNoASC%d" % st})
        noasc = ((q2.get("actor") or {}).get("actorPath")) or q2.get("actorPath")
        if noasc:
            r = M.raw_post("describe_ability_system", {"actorPath": noasc})
            check("T9101 an actor with no ASC is refused, and told it is simply not part of the "
                  "ability system rather than that something failed",
                  r.get("ok") is False and "not part of the ability system" in (r.get("error") or ""),
                  (r.get("error") or "")[:220])
        ghost = M.raw_post("describe_ability_system", {"actorPath": "/Game/_MifGAS/NoSuchThing"})
        check("T9101 a path that resolves to nothing is refused, saying it takes a LIVE object path",
              ghost.get("ok") is False and "LIVE" in (ghost.get("error") or ""),
              (ghost.get("error") or "")[:220])
        asasset = M.raw_post("describe_ability_system", {"blueprintId": bid})
        check("T9101 blueprintId is refused BY NAME - a Blueprint asset has no runtime state",
              asasset.get("ok") is False and "runtime" in (asasset.get("error") or "").lower(),
              (asasset.get("error") or "")[:220])

        # ------------------------------------------------------------------ T9102 the empty case
        print("\n=== T9102: an empty ASC is explained, not reported as zeroes ===")
        # THE assertion. Rows of zeroes read as "this character has no ability system", which is a
        # different and wrong conclusion from "it has not been initialised yet".
        check("T9102 the component answered every read", d.get("ok") is True
              and d.get("attributeCount") is not None and d.get("abilityCount") is not None,
              json.dumps(d)[:250])
        if (d.get("attributeCount") or 0) == 0 and (d.get("abilityCount") or 0) == 0:
            check("T9102 and an empty result SAYS it is uninitialised rather than absent, and "
                  "names PIE as where populated state comes from",
                  "not 'no ability system'" in (d.get("note") or "")
                  and "PIE" in (d.get("note") or ""), (d.get("note") or "")[:280])
        else:
            print("  NOTE  this ASC is populated, so the empty-case note is unexercised here.")

        check("T9102 - the editor is still alive",
              M.call("self_audit", {"summaryOnly": True}).get("ok") is True,
              "this reads live gameplay component state")
    finally:
        for path in (bp, "/Game/_MifGAS/BP_NoASC%d" % st):
            SC.confirm_call("delete_asset", {"path": path})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
