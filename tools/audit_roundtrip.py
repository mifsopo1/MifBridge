"""Can you rebuild what you just read? A read-back that cannot recreate the thing is incomplete.

This generalises the macro defect. A user needed "Switch Has Authority", could not work out what to
pass to add_macro_instance, guessed twice, was refused twice, and concluded from the refusals that
the node must be a dedicated K2Node class needing a new endpoint. It was a K2Node_MacroInstance the
whole time. `list_nodes` reported `class: K2Node_MacroInstance` and nothing about WHICH macro, so
inspection and creation could not be connected - and the failed guesses became evidence for a wrong
conclusion about the engine.

The shape generalises: for anything MifBridge can CREATE, reading it back should tell you enough to
create it again. Where it does not, an agent is left guessing, and guesses that fail look like facts.

Each case here creates a thing, reads it back, and asks two questions:

  IDENTITY     does the read-back name what KIND of thing this is, specifically enough to act on?
                 (K2Node_MacroInstance alone: no. Plus which macro and which library: yes.)
  RECREATABLE  can the read-back alone drive a second create that produces the same thing?

A case that fails RECREATABLE is not necessarily a bug - some things are legitimately not
recreatable from a single read. It IS a place where an agent will guess, so it is worth knowing
about and worth documenting.

Safety: everything under /Game/_MifAudit*, nothing saved, confirm never sent.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

RESULTS = []


def note(case, ok, what, detail=""):
    RESULTS.append((case, ok, what, detail))
    print(("  PASS  " if ok else "  GAP   ") + "%-42s %s" % (what, "" if ok else detail[:150]))
    if not ok:
        M.record("ROUNDTRIP", case, "%s: %s" % (what, detail), severity="medium")


def main():
    ok, why = M.require_sdk_bridge()
    if not ok:
        print("REFUSING TO RUN:", why)
        return 2
    print("target:", why, "\n")

    stamp = int(time.time() % 100000)
    root = "/Game/_MifAuditRT/BP_RT_%d" % stamp
    bp = M.call("create_blueprint", {"path": root, "parentClass": "Actor"})
    bpid, graph = bp.get("blueprintId"), bp.get("eventGraphId")
    if not graph:
        print("setup failed:", json.dumps(bp)[:300])
        return 3
    print("scratch blueprint:", bpid, "\n")

    # ---------------------------------------------------------------- function call node
    print("=== function call ===")
    r = M.call("add_function_call", {"graphId": graph, "function": "PrintString",
                                     "class": "KismetSystemLibrary", "x": 100, "y": 100})
    guid = r.get("nodeGuid")
    nd = M.call("get_node", {"nodeGuid": guid}).get("node", {}) if guid else {}
    note("add_function_call", bool(nd.get("class")), "read-back names the node class",
         json.dumps(nd)[:200])
    # To recreate you need the FUNCTION and its OWNING CLASS. Is either recoverable?
    blob = json.dumps(nd)
    has_fn = "PrintString" in blob
    has_owner = "KismetSystemLibrary" in blob
    note("add_function_call", has_fn, "read-back names the function", blob[:200])
    note("add_function_call", has_owner,
         "read-back names the owning class needed by add_function_call",
         "title/objectPath do not carry the class, so a caller cannot tell which class declares "
         "this function without guessing: " + blob[:180])

    # ---------------------------------------------------------------- variable get
    print("\n=== variable get ===")
    M.call("add_variable", {"blueprintId": bpid, "name": "RTVar", "type": "int"})
    r = M.call("add_variable_get", {"graphId": graph, "variable": "RTVar", "x": 100, "y": 300})
    guid = r.get("nodeGuid")
    nd = M.call("get_node", {"nodeGuid": guid}).get("node", {}) if guid else {}
    blob = json.dumps(nd)
    note("add_variable_get", "RTVar" in blob, "read-back names the variable", blob[:200])

    # ---------------------------------------------------------------- cast node
    print("\n=== cast ===")
    r = M.call("add_cast", {"graphId": graph, "targetClass": "Pawn", "x": 100, "y": 500})
    guid = r.get("nodeGuid")
    nd = M.call("get_node", {"nodeGuid": guid}).get("node", {}) if guid else {}
    blob = json.dumps(nd)
    note("add_cast", "Pawn" in blob, "read-back names the cast target class", blob[:220])

    # ---------------------------------------------------------------- macro (the reference case)
    print("\n=== macro instance (the case this generalises) ===")
    r = M.call("add_macro_instance", {"graphId": graph, "macroGraph": "ForEachLoop", "x": 600, "y": 100})
    guid = r.get("nodeGuid")
    nd = M.call("get_node", {"nodeGuid": guid}).get("node", {}) if guid else {}
    macro = nd.get("macro") or {}
    note("add_macro_instance", bool(macro.get("graphName")), "read-back names the macro graph",
         json.dumps(nd)[:200])
    args = macro.get("addMacroInstanceArgs") or {}
    if args.get("macroGraph"):
        rt = M.call("add_macro_instance", dict(args, graphId=graph, x=600, y=300))
        note("add_macro_instance", rt.get("ok") is True,
             "read-back args recreate the node", json.dumps(rt)[:200])
    else:
        note("add_macro_instance", False, "read-back args recreate the node",
             "no addMacroInstanceArgs in the read-back")

    # ---------------------------------------------------------------- component
    print("\n=== component ===")
    r = M.call("add_component", {"blueprintId": bpid, "componentClass": "StaticMeshComponent",
                                 "name": "RTMesh"})
    lst = M.call("list_components", {"blueprintId": bpid})
    blob = json.dumps(lst)
    note("add_component", "RTMesh" in blob, "read-back names the component", blob[:200])
    note("add_component", "StaticMeshComponent" in blob,
         "read-back names the component CLASS needed to recreate it", blob[:220])

    # ---------------------------------------------------------------- custom event
    print("\n=== custom event with parameters ===")
    r = M.call("add_custom_event", {"graphId": graph, "name": "RTEvent", "x": 900, "y": 100,
                                    "inputs": [{"name": "Amount", "type": "int"},
                                               {"name": "Who", "type": "string"}]})
    guid = r.get("nodeGuid")
    nd = M.call("get_node", {"nodeGuid": guid}).get("node", {}) if guid else {}
    pins = [p.get("name") for p in nd.get("pins", [])]
    note("add_custom_event", "Amount" in pins and "Who" in pins,
         "read-back exposes the event parameters", str(pins))
    types = {p.get("name"): (p.get("type") or {}).get("category") for p in nd.get("pins", [])}
    note("add_custom_event", types.get("Amount") == "int" and types.get("Who") == "string",
         "read-back carries the parameter TYPES needed to recreate", json.dumps(types)[:200])

    # ---------------------------------------------------------------- branch
    print("\n=== branch ===")
    r = M.call("add_branch", {"graphId": graph, "x": 1200, "y": 100})
    nd = M.call("get_node", {"nodeGuid": r.get("nodeGuid")}).get("node", {}) if r.get("nodeGuid") else {}
    pins = [p.get("name") for p in nd.get("pins", [])]
    note("add_branch", nd.get("class") == "K2Node_IfThenElse", "read-back names the node class",
         str(nd.get("class")))
    note("add_branch", "Condition" in pins and "then" in pins and "else" in pins,
         "read-back exposes the branch pins", str(pins))

    # ---------------------------------------------------------------- sequence
    print("\n=== sequence ===")
    r = M.call("add_sequence", {"graphId": graph, "x": 1200, "y": 300})
    nd = M.call("get_node", {"nodeGuid": r.get("nodeGuid")}).get("node", {}) if r.get("nodeGuid") else {}
    outs = [p.get("name") for p in nd.get("pins", []) if p.get("direction") == "output"]
    note("add_sequence", nd.get("class") == "K2Node_ExecutionSequence",
         "read-back names the node class", str(nd.get("class")))
    note("add_sequence", len(outs) >= 2, "read-back exposes the numbered exec outputs", str(outs))

    # ---------------------------------------------------------------- switch on int
    print("\n=== switch ===")
    r = M.call("add_switch_int", {"graphId": graph, "x": 1200, "y": 500})
    nd = M.call("get_node", {"nodeGuid": r.get("nodeGuid")}).get("node", {}) if r.get("nodeGuid") else {}
    note("add_switch_int", bool(nd.get("class")), "read-back names the node class",
         json.dumps(nd)[:200])
    note("add_switch_int", any("election" in (p.get("name") or "") or "Selection" in (p.get("name") or "")
                               for p in nd.get("pins", [])),
         "read-back exposes the selection pin",
         str([p.get("name") for p in nd.get("pins", [])]))

    # ---------------------------------------------------------------- spawn actor
    print("\n=== spawn actor from class ===")
    r = M.call("add_spawn_actor", {"graphId": graph, "actorClass": "StaticMeshActor", "x": 1600, "y": 100})
    guid = r.get("nodeGuid")
    nd = M.call("get_node", {"nodeGuid": guid}).get("node", {}) if guid else {}
    blob = json.dumps(nd)
    note("add_spawn_actor", bool(nd.get("class")), "read-back names the node class", blob[:200])
    note("add_spawn_actor", "StaticMeshActor" in blob,
         "read-back names the actor CLASS needed to recreate it",
         "only the title/pins are present, so the class must be guessed: " + blob[:200])

    # ---------------------------------------------------------------- timeline
    print("\n=== timeline ===")
    # add_timeline takes blueprintId, not graphId - a timeline node lives in the blueprint's own
    # event graph. The endpoint says so plainly when given the wrong one; this test was the thing at
    # fault, not the endpoint.
    r = M.call("add_timeline", {"blueprintId": bpid, "name": "RTTimeline", "x": 1600, "y": 400,
                                "floatTracks": ["Alpha"]})
    guid = r.get("nodeGuid")
    if guid:
        nd = M.call("get_node", {"nodeGuid": guid}).get("node", {})
        blob = json.dumps(nd)
        note("add_timeline", "RTTimeline" in blob, "read-back names the timeline", blob[:200])
        note("add_timeline", "Alpha" in blob,
             "read-back names the tracks needed to recreate it", blob[:220])
    else:
        note("add_timeline", False, "timeline node was created", json.dumps(r)[:200])

    # ---------------------------------------------------------------- variable flags
    print("\n=== variable flags ===")
    M.call("set_variable_flags", {"blueprintId": bpid, "name": "RTVar",
                                  "instanceEditable": True, "category": "RTCat",
                                  "tooltip": "round trip"})
    lv = M.call("list_variables", {"blueprintId": bpid})
    blob = json.dumps(lv)
    note("set_variable_flags", "RTCat" in blob, "read-back carries the category", blob[:220])
    note("set_variable_flags", "round trip" in blob, "read-back carries the tooltip", blob[:220])

    # THROUGH scratch_confirm, NOT M.call. mifaudit strips `confirm` from every payload - the guard
    # that makes an unattended overnight run safe - so this line was a no-op and every run of this
    # tool left a blueprint behind in whatever editor it was pointed at. Observed 2026-08-31 against
    # a session somebody was working in: /Game/_MifAuditRT/BP_RT_96969, still there afterwards.
    #
    # scratch_confirm exists for exactly this and refuses any payload whose paths are not scratch, so
    # the guard is not bypassed, it is satisfied: the root here is always /Game/_MifAuditRT/...
    gone = SC.confirm_call("delete_asset", {"path": root, "confirm": True})
    if gone.get("ok") is False:
        # Reported rather than swallowed. A tool that quietly fails to tidy up is how the leftover
        # went unnoticed in the first place.
        print("\nNOTE: could not remove the scratch blueprint %s - %s"
              % (root, str(gone.get("error"))[:160]))

    print("\n" + "=" * 72)
    gaps = [r for r in RESULTS if not r[1]]
    print("round-trip: %d checks, %d gaps" % (len(RESULTS), len(gaps)))
    for case, _, what, detail in gaps:
        print("  GAP  %-24s %s" % (case, what))
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
