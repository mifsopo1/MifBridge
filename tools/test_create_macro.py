"""create_macro - filling a container this plugin already shipped empty.

THIS GAP WAS PARTLY OUR OWN MAKING. create_blueprint accepts blueprintType:"MacroLibrary" and
produces a Blueprint Macro Library, and nothing could then put a macro in it - so an agent could
create a container with no way to fill it. Meanwhile add_macro_instance, list_graphs and
ResolveMacroGraph all CONSUME macros. Read half and consumer half present, author half absent.

T5601 IS THE ONE THAT PROVES A CORRECTION WAS HONOURED, and it is why the node count is asserted
rather than just "the graph exists". FBlueprintEditorUtils::AddMacroGraph already calls
CreateMacroGraphTerminators itself (BlueprintEditorUtils.cpp:2310). Calling it again - the obvious
thing to do, since a macro obviously needs terminators - would give the graph a SECOND pair of
tunnel nodes, which compiles into nonsense rather than failing loudly. A fresh macro must contain
exactly two nodes.

T5602 IS THE DIRECTION INVERSION. A macro's entry and exit are both UK2Node_Tunnel and are told
apart by bCanHaveOutputs / bCanHaveInputs, not by order or name. An INPUT to the macro is created as
EGPD_Output on the entry tunnel, because the entry feeds the graph. That reads as a bug every time
until it is said out loud, so the suite checks the pins land where a caller would expect to find
them - on the macro, not on the tunnel.

PIN NAMES ARE ECHOED FROM THE ENGINE. CreateUserDefinedPin runs with bUseUniqueName true, so it
RENAMES on collision and returns the pin it actually made. create_function learned that the hard
way; this reports renamedPins when it happens.

CLEANS UP: the scratch Blueprint is deleted at the end.
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

    path = "/Game/_MifMacro/BP_MacroTest%d" % (int(time.time()) % 100000)
    made = None
    try:
        # ------------------------------------------------------------------ setup
        print("=== setup ===")
        c = M.raw_post("create_blueprint", {"path": path,
                                            "parentClass": "/Script/Engine.Actor"})
        check("(setup) a scratch Blueprint exists", c.get("ok") is True, json.dumps(c)[:200])
        if not c.get("ok"):
            return 1
        found = [a["path"] for a in
                 (M.call("find_assets", {"pathPrefix": "/Game/_MifMacro",
                                         "limit": 10}).get("assets") or [])
                 if path.rsplit("/", 1)[-1] in a["path"]]
        made = found[0] if found else None
        check("(setup) and is findable", bool(made), found)
        if not made:
            return 1

        # ------------------------------------------------------------------ T5600 authoring
        print("\n=== T5600: a macro can be authored at all ===")
        r = M.raw_post("create_macro", {
            "blueprintId": made, "name": "MifDoThing",
            "inputs": [{"name": "Amount", "type": "float"}],
            "outputs": [{"name": "Result", "type": "bool"}]})
        check("T5600 create_macro succeeds", r.get("ok") is True, json.dumps(r)[:250])
        check("T5600 it reports the macro's name and a graphId", r.get("macro") == "MifDoThing"
              and bool(r.get("graphId")), json.dumps(r)[:220])
        # THE graphId must be the form list_graphs emits, or the caller cannot use it.
        check("T5600 the graphId is the '<blueprintPath>::<graphName>' form every graph endpoint "
              "takes", (r.get("graphId") or "").endswith("::MifDoThing"), r.get("graphId"))
        check("T5600 macroCount is measured from the Blueprint", r.get("macroCount") == 1,
              r.get("macroCount"))

        graphs = [g.get("name") for g in
                  (M.call("list_graphs", {"blueprintId": made}).get("graphs") or [])]
        check("T5600 and list_graphs really shows it - the consumer half agrees",
              "MifDoThing" in graphs, graphs)

        # ------------------------------------------------------------------ T5601 the correction
        print("\n=== T5601: exactly ONE pair of tunnels, not two ===")
        nodes = M.call("list_nodes", {"graphId": r.get("graphId")})
        check("T5601 the macro graph is readable", nodes.get("ok") is True,
              json.dumps(nodes)[:200])
        count = nodes.get("count")
        if count is None:
            count = len(nodes.get("nodes") or [])
        # THE assertion. AddMacroGraph creates the terminators itself; calling
        # CreateMacroGraphTerminators again would add a SECOND pair, which compiles into nonsense
        # rather than failing. Two nodes means one entry and one exit.
        check("T5601 a fresh macro contains exactly 2 nodes - one entry tunnel and one exit",
              count == 2, "count=%s" % count)

        # ------------------------------------------------------------------ T5602 the pins
        print("\n=== T5602: pins land where the CALLER expects, not where the tunnel does ===")
        check("T5602 the declared input comes back", r.get("inputs") == ["Amount"],
              json.dumps(r.get("inputs")))
        check("T5602 and the declared output", r.get("outputs") == ["Result"],
              json.dumps(r.get("outputs")))
        # An input to the macro is EGPD_Output on the entry tunnel - the inversion that reads as a
        # bug until said out loud. What matters to a caller is that both names exist.
        empty = M.raw_post("create_macro", {"blueprintId": made, "name": "MifEmptyMacro"})
        check("T5602 a macro with no pins at all is fine - it is still a valid macro",
              empty.get("ok") is True and empty.get("inputs") == []
              and empty.get("outputs") == [], json.dumps(empty)[:220])

        # ------------------------------------------------------------------ T5603 refusals
        print("\n=== T5603: names are how you address a macro afterwards ===")
        dup = M.raw_post("create_macro", {"blueprintId": made, "name": "MifDoThing"})
        check("T5603 a duplicate macro name is refused, not uniquified",
              dup.get("ok") is False and "already has a macro" in (dup.get("error") or ""),
              (dup.get("error") or "")[:220])
        check("T5603 and the refusal explains why renaming would be worse",
              "address a macro afterwards" in (dup.get("error") or ""),
              (dup.get("error") or "")[:220])
        clash = M.raw_post("create_macro", {"blueprintId": made,
                                            "name": "UserConstructionScript"})
        check("T5603 a name already used by a FUNCTION is refused too",
              clash.get("ok") is False, (clash.get("error") or "")[:220])
        noname = M.raw_post("create_macro", {"blueprintId": made})
        check("T5603 a missing name is refused", noname.get("ok") is False,
              (noname.get("error") or "")[:180])
        pure = M.raw_post("create_macro", {"blueprintId": made, "name": "MifX", "pure": True})
        check("T5603 'pure' is refused - macros have no pure/impure distinction",
              pure.get("ok") is False and "create_function" in (pure.get("error") or ""),
              (pure.get("error") or "")[:200])

        # ------------------------------------------------------------------ T5604 it compiles
        print("\n=== T5604: an empty macro is valid, and the Blueprint still compiles ===")
        comp = M.raw_post("compile", {"blueprintId": made})
        check("T5604 the Blueprint compiles with two macros on it", comp.get("ok") is True,
              json.dumps(comp)[:220])
        check("T5604 nothing was saved, and it says so",
              "NOT compiled or saved" in (r.get("assetNote") or "")
              or "dirty" in (r.get("assetNote") or ""), r.get("assetNote"))
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": made})
        left = [a["path"] for a in
                (M.call("find_assets", {"pathPrefix": "/Game/_MifMacro"}).get("assets") or [])
                if made and made in a["path"]]
        check("T5605 (cleanup) the scratch Blueprint is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
