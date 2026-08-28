"""Do the node-creation endpoints actually put a usable node in the graph?

`add_*` is the largest block that no suite names - 33 endpoints when this was written, and node
creation is what this bridge is mostly FOR. The failure worth hunting is not a crash: it is an endpoint
that answers ok:true with a node guid while the graph gains nothing usable, which is invisible until a
compile much later blames something else.

DRIVEN FROM THE LIVE REGISTRY, not a hand-written list. It asks describe_endpoint for each add_*
endpoint's acceptedParams and drives every one that needs nothing beyond a graph and coordinates. So a
node endpoint added next month is covered the day it lands, without anyone remembering to add it here -
which is the specific way this file would otherwise go stale, since the 33 uncovered ones got that way
by being added one at a time.

Every node is checked three ways, because ok:true is the thing under suspicion:
  * the response carries a node guid,
  * get_node can resolve that guid in the graph afterwards - a guid that resolves to nothing is the
    exact silent failure being hunted,
  * and the blueprint still compiles with every node present.

The endpoints needing real arguments (a struct, a class, an enum) are driven explicitly further down,
because a generated argument would test the guess rather than the endpoint.

T334/T335 (added 2026-08-28, from a coverage_gaps.py sweep) are more of the same "needs a real
argument" family - a class cast, a format string, switch/enum/subsystem/literal/InputAction nodes -
plus add_blackboard_key on its own scratch BlackboardData asset. T335 was FIRST written as confirm-
gate-only, on the mistaken assumption that mifaudit's FORBIDDEN_KEYS strip of `confirm` was
unconditional across this whole harness. It is not: scratch_confirm.py (used already by
test_confirm_gated.py) bypasses that strip narrowly, for any payload whose every path is provably
under /Game/_Mif via M.raw_post. add_blackboard_key is addressed by a `path` naming the BlackboardData
asset, so it genuinely can be unblocked this way. T335 now exercises the real success path, a real
duplicate-name refusal, and a real bad-type refusal, not just the gate.

T333/T333b (rewritten the same pass): remove_node and rename_event were ALSO first framed as a
permanent gap here, on the same mistaken assumption - they are addressed primarily by nodeGuid, and
it is easy to stop there. But both also accept an optional graphId, and the graphId this bridge
returns is itself a full object path ("/Game/_MifX/BP_1.BP_1::EventGraph"), which scratch_confirm.py
accepts when the owning blueprint is scratch - confirmed live, not assumed. T333 now exercises
remove_node's real removal (on a disposable throwaway node, not one anything else here depends on);
T333b adds rename_event's coverage from scratch, since nothing in this repo tested it at all before -
not even its refusal.

T336-T340 (same sweep) finish the "needs a real argument" family with the ones needing HEAVIER
setup: add_parent_call (Actor's own ReceiveBeginPlay, no extra setup needed), add_get_data_table_row
and add_create_widget (against REAL DataTable/WidgetBlueprint assets already in this project, read-
only references - not fabricated ones), add_component_bound_event (needs a real component added to
the scratch blueprint first - a SphereComponent's OnComponentBeginOverlap), and add_widget_binding
(needs its OWN scratch WidgetBlueprint with a real tree widget, since a binding lives inside that
blueprint - a plain Actor blueprint has no widget tree to bind against).
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS, FAIL = [], []

# Anything in here is either cosmetic or has a usable default, so an endpoint accepting only these can
# be driven with nothing but a graph.
COSMETIC = {"graphId", "x", "y", "width", "height", "text", "outputs", "numInputs",
            "comment", "title", "purity", "pure"}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def node_exists(graph, guid):
    """True when the graph can still resolve this guid - the assertion ok:true cannot make."""
    r = M.call("get_node", {"graphId": graph, "nodeGuid": guid})
    return bool(r.get("ok")) and bool((r.get("node") or {}).get("guid"))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)
    bpath = "/Game/_MifNodes/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    if not bid:
        print("setup failed")
        return 3
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    graph = next((g.get("graphId") for g in graphs if "EventGraph" in (g.get("name") or "")), None)
    if not graph:
        print("setup failed: no event graph")
        return 3

    # ------------------------------------------------------------------ T330 registry-driven
    print("\n=== T330: every node endpoint that needs only a graph ===")
    simple = []
    for ep in sorted(n for n in M.endpoint_names() if n.startswith("add_")):
        acc = set(M.call("describe_endpoint", {"name": ep}).get("acceptedParams") or [])
        if acc and "graphId" in acc and acc <= COSMETIC:
            simple.append(ep)
    # If this ever finds nothing, the suite is vacuous and should say so rather than pass.
    check("T330 the registry yielded endpoints to drive", len(simple) >= 5,
          "only %d found - describe_endpoint may have changed shape, and this suite is then vacuous"
          % len(simple))
    print("   driving: %s" % ", ".join(simple))

    placed, y = [], 0
    for ep in simple:
        y += 150
        r = M.call(ep, {"graphId": graph, "x": 0, "y": y})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T330 %s reports success" % ep, r.get("ok") is True, json.dumps(r)[:150])
        check("T330 %s returns a node guid" % ep, bool(guid), json.dumps(r)[:150])
        if guid:
            # THE assertion. ok:true plus a guid that resolves to nothing is the failure being hunted.
            check("T330 %s's node is really in the graph" % ep, node_exists(graph, guid),
                  "guid %s does not resolve - the call said it created a node and the graph has none"
                  % guid)
            placed.append((ep, guid))

    # ------------------------------------------------------------------ T331 endpoints with arguments
    print("\n=== T331: node endpoints that need a real argument ===")
    M.call("add_variable", {"blueprintId": bid, "name": "Amount", "type": "float"})
    # A USER-DEFINED struct for the make/break pair. FVector breaks fine and cannot be MADE - the
    # engine refuses with "no BP-visible members", because breaking needs only read access while
    # making needs every member writable from Blueprint. Using it for both would have tested that
    # asymmetry rather than the endpoint, and the refusal is correct behaviour worth not mistaking
    # for a bug.
    spath = "/Game/_MifNodes/S_%d" % st
    sres = M.call("create_struct", {"path": spath})
    struct_name = sres.get("name") or ("S_%d" % st)
    M.call("add_struct_member", {"struct": sres.get("structPath") or spath,
                                 "name": "Price", "type": "float"})
    specific = [
        ("add_variable_get", {"graphId": graph, "var": "Amount", "x": 400, "y": 0}),
        ("add_variable_set", {"graphId": graph, "var": "Amount", "x": 400, "y": 150}),
        ("add_custom_event", {"graphId": graph, "name": "MifTestEvent_%d" % st, "x": 400, "y": 300}),
        ("add_cast", {"graphId": graph, "castTo": "Pawn", "x": 400, "y": 450}),
        ("add_make_struct", {"graphId": graph, "structName": struct_name, "x": 400, "y": 600}),
        ("add_break_struct", {"graphId": graph, "structName": "Vector", "x": 400, "y": 750}),
    ]
    for ep, payload in specific:
        r = M.call(ep, payload)
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        ok = r.get("ok") is True
        check("T331 %s succeeds" % ep, ok, (r.get("error") or json.dumps(r))[:170])
        if ok and guid:
            check("T331 %s's node is really in the graph" % ep, node_exists(graph, guid),
                  "guid %s does not resolve" % guid)
            placed.append((ep, guid))

    # ------------------------------------------------------------------ T332 they survive together
    print("\n=== T332: the graph holds them all and still compiles ===")
    listed = M.call("list_nodes", {"graphId": graph}).get("nodes") or []
    guids = {n.get("guid") for n in listed}
    missing = [ep for ep, g in placed if g not in guids]
    # A node can resolve individually and still be absent from the listing - two different reads, and
    # disagreement between them is worth catching.
    check("T332 every placed node appears in list_nodes", not missing,
          "created but not listed: %s" % missing)
    check("T332 the listing is not suspiciously short", len(listed) >= len(placed),
          "%d listed vs %d placed" % (len(listed), len(placed)))
    c = M.call("compile", {"blueprintId": bid})
    # Unconnected nodes are legal, so a clean compile is the right expectation here.
    check("T332 the blueprint compiles with all of them",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))

    # ------------------------------------------------------------------ T333 removal
    print("\n=== T333: remove_node - the refusal, AND (via scratch_confirm) the real removal ===")
    if placed:
        ep, guid = placed[0]
        # First, the refusal: a plain M.call never carries confirm (mifaudit strips it), so this
        # proves the guard itself without touching scratch_confirm at all.
        rm = M.call("remove_node", {"graphId": graph, "nodeGuid": guid})
        check("T333 remove_node refuses without confirm", rm.get("ok") is False, json.dumps(rm)[:170])
        check("T333 and says confirm is what is missing", "confirm" in (rm.get("error") or ""),
              (rm.get("error") or "")[:150])
        # The refusal must leave the node it declined to remove.
        check("T333 the node survives a refused removal", node_exists(graph, guid),
              "the node vanished on a refused call")
        ghost = M.call("remove_node", {"graphId": graph,
                                       "nodeGuid": "DEADBEEF00004444DEADBEEF00004444"})
        check("T333 removing a node that does not exist is refused",
              ghost.get("ok") is False, json.dumps(ghost)[:150])
        c = M.call("compile", {"blueprintId": bid})
        check("T333 and the blueprint still compiles",
              c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

        # Now the REAL removal. remove_node is addressed by nodeGuid, but graphId disambiguates a
        # reused guid and - since the graphId this bridge returns is itself a full object path -
        # scratch_confirm.check() accepts it when the owning blueprint is scratch (confirmed live;
        # this was wrongly framed as a permanent gap before that was checked - see
        # scratch_confirm.py's module docstring). A throwaway node, not one of the ones already
        # placed and counted, so nothing else in this file has to account for its removal.
        thr = M.call("add_branch", {"graphId": graph, "x": 900, "y": 1500})
        thr_guid = thr.get("nodeGuid") or (thr.get("node") or {}).get("guid")
        check("T333 (setup) a throwaway node exists to really remove", bool(thr_guid), json.dumps(thr)[:150])
        if thr_guid:
            real = SC.confirm_call("remove_node", {"graphId": graph, "nodeGuid": thr_guid})
            check("T333 the real removal succeeds", real.get("ok") is True, json.dumps(real)[:170])
            check("T333 and the node is really gone", not node_exists(graph, thr_guid),
                  "guid %s still resolves after a confirmed removal" % thr_guid)
            c = M.call("compile", {"blueprintId": bid})
            check("T333 and the blueprint still compiles after a real removal",
                  c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T333b rename_event
    # ZERO prior coverage anywhere in this repo - not even a refusal check existed. Same graphId-
    # carries-a-scratch-path reasoning as T333's real removal above.
    print("\n=== T333b: rename_event - no prior coverage at all, refusal AND real rename ===")
    ev = M.call("add_custom_event", {"graphId": graph, "name": "MifRenameProbe_%d" % st, "x": 900, "y": 1650})
    ev_guid = ev.get("nodeGuid") or (ev.get("node") or {}).get("guid")
    check("T333b (setup) a custom event exists to rename", bool(ev_guid), json.dumps(ev)[:150])
    if ev_guid:
        rf = M.call("rename_event", {"graphId": graph, "nodeGuid": ev_guid, "newName": "ShouldNotApply"})
        check("T333b rename_event refuses without confirm", rf.get("ok") is False, json.dumps(rf)[:170])
        still = M.call("get_node", {"graphId": graph, "nodeGuid": ev_guid})
        check("T333b the refusal left the original name in place",
              (still.get("node") or {}).get("title") == "MifRenameProbe_%d" % st,
              (still.get("node") or {}).get("title"))

        rn = SC.confirm_call("rename_event",
                             {"graphId": graph, "nodeGuid": ev_guid, "newName": "MifRenamedProbe_%d" % st})
        check("T333b the real rename succeeds", rn.get("ok") is True, json.dumps(rn)[:170])
        after = M.call("get_node", {"graphId": graph, "nodeGuid": ev_guid})
        check("T333b and the node really carries the new name",
              (after.get("node") or {}).get("title") == "MifRenamedProbe_%d" % st,
              (after.get("node") or {}).get("title"))
        c = M.call("compile", {"blueprintId": bid})
        check("T333b and the blueprint still compiles after a real rename",
              c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))
        placed.append(("add_custom_event", ev_guid))

    # ------------------------------------------------------------------ T334 more node endpoints
    # needing a real argument the registry sweep and T331 do not already cover - found by comparing
    # coverage_gaps.py's list against what T330's own "driving:" line actually swept: three names on
    # that list (add_get_array_item, add_make_map, add_self) turned out to be dynamically covered by
    # T330 already - coverage_gaps.py cannot see a name that is never typed as a literal string, only
    # produced by iterating describe_endpoint's live registry. These are the ones genuinely missing.
    print("\n=== T334: more node endpoints needing a real argument ===")
    y2 = 900
    node_specific = [
        ("add_class_cast", {"graphId": graph, "targetClass": "Pawn"}),
        ("add_format_text", {"graphId": graph, "format": "Hello {Name}, you have {Count}"}),
        ("add_switch_int", {"graphId": graph, "cases": 3}),
        ("add_switch_string", {"graphId": graph, "cases": ["Open", "Closed", "Locked"]}),
        # A stock engine enum, not a project one, so this passes on any project this bridge runs
        # against - the same reasoning list_enum_values' own tests use a stock enum for.
        ("add_switch_enum", {"graphId": graph, "enumName": "ECollisionChannel"}),
        ("add_get_subsystem", {"graphId": graph, "subsystemClass": "EditorActorSubsystem"}),
        # An object-reference literal needs a real asset path; the scratch blueprint itself is one.
        ("add_literal", {"graphId": graph, "object": bpath}),
    ]
    for ep, payload in node_specific:
        payload = dict(payload)
        y2 += 150
        payload["x"], payload["y"] = 700, y2
        r = M.call(ep, payload)
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        ok = r.get("ok") is True
        check("T334 %s succeeds" % ep, ok, (r.get("error") or json.dumps(r))[:200])
        if ok and guid:
            check("T334 %s's node is really in the graph" % ep, node_exists(graph, guid),
                  "guid %s does not resolve" % guid)
            placed.append((ep, guid))

    # add_enhanced_input_action needs a REAL InputAction asset - DDS2 is a real shipped game and has
    # real Enhanced Input content (confirmed earlier this session, test_uncovered_reads.py T824), so
    # this is exercised against real content rather than a fabricated one.
    actions = M.call("find_assets", {"class": "InputAction", "limit": 1}).get("assets") or []
    if actions:
        y2 += 150
        r = M.call("add_enhanced_input_action",
                   {"graphId": graph, "inputAction": actions[0].get("path"), "x": 700, "y": y2})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T334 add_enhanced_input_action succeeds on a real InputAction",
              r.get("ok") is True, (r.get("error") or json.dumps(r))[:200])
        if r.get("ok") and guid:
            check("T334 add_enhanced_input_action's node is really in the graph",
                  node_exists(graph, guid), "guid %s does not resolve" % guid)
            placed.append(("add_enhanced_input_action", guid))
    else:
        print("  SKIP  add_enhanced_input_action - no InputAction asset found on this project")

    c = M.call("compile", {"blueprintId": bid})
    check("T334 the blueprint still compiles with all of T334's nodes too",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))

    q = M.call("add_class_cast", {"graphId": graph})
    check("T334 add_class_cast with no target class refuses", q.get("ok") is False, q.get("error"))
    q = M.call("add_get_subsystem", {"graphId": graph, "subsystemClass": "Actor"})
    check("T334 add_get_subsystem refuses a non-Subsystem class",
          q.get("ok") is False and "not a Subsystem" in (q.get("error") or ""), q.get("error"))
    q = M.call("add_switch_enum", {"graphId": graph, "enumName": "NoSuchEnum_zz_definitely_not_real"})
    check("T334 add_switch_enum refuses an unknown enum name", q.get("ok") is False, q.get("error"))

    # ------------------------------------------------------------------ T335 add_blackboard_key
    # Unlike remove_node/rename_event (guid-only, no path - a permanent gap scratch_confirm.py itself
    # documents), add_blackboard_key names its target by a `path` to the BlackboardData asset, so a
    # scratch-only payload can genuinely satisfy scratch_confirm.check() and reach the real handler,
    # confirm gate and all. This drives the actual success path, not just the refusal.
    print("\n=== T335: add_blackboard_key - real success/duplicate/bad-type via scratch_confirm ===")
    bbpath = "/Game/_MifNodes/BB_%d" % st
    made = M.call("create_asset", {"path": bbpath, "class": "BlackboardData"})
    check("T335 (setup) a scratch BlackboardData is created", made.get("ok") is True, json.dumps(made)[:200])
    if made.get("ok"):
        r = SC.confirm_call("add_blackboard_key", {"path": bbpath, "name": "TestFlag", "type": "Bool"})
        check("T335 a well-formed add succeeds for real", r.get("ok") is True,
              (r.get("error") or json.dumps(r))[:200])

        keys = M.call("list_blackboard_keys", {"path": bbpath}).get("keys") or []
        check("T335 the key is really on the blackboard afterward",
              any(k.get("name") == "TestFlag" for k in keys), keys)

        dup = SC.confirm_call("add_blackboard_key", {"path": bbpath, "name": "TestFlag", "type": "Bool"})
        check("T335 a duplicate name is refused", dup.get("ok") is False, json.dumps(dup)[:200])

        bad = SC.confirm_call("add_blackboard_key", {"path": bbpath, "name": "OtherFlag", "type": "NoSuchType_zz"})
        check("T335 an unknown key type is refused", bad.get("ok") is False, json.dumps(bad)[:200])

        # A fresh BlackboardData is not empty either - same pattern as WidgetBlueprint's auto-created
        # root: it auto-creates its own SelfActor key (confirmed live), so the baseline to compare
        # against is {SelfActor, TestFlag}, not {TestFlag} alone.
        keys2 = M.call("list_blackboard_keys", {"path": bbpath}).get("keys") or []
        check("T335 the refused calls added nothing beyond the one real key",
              sorted(k.get("name") for k in keys2) == ["SelfActor", "TestFlag"], keys2)

        SC.confirm_call("delete_asset", {"path": bbpath})

    # ------------------------------------------------------------------ T336 add_parent_call
    print("\n=== T336: add_parent_call - no extra setup, Actor already declares a real function ===")
    r = M.call("add_parent_call", {"graphId": graph, "function": "ReceiveBeginPlay"})
    guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
    check("T336 calling Actor's own ReceiveBeginPlay succeeds", r.get("ok") is True,
          (r.get("error") or json.dumps(r))[:200])
    if r.get("ok") and guid:
        check("T336 the node is really in the graph", node_exists(graph, guid),
              "guid %s does not resolve" % guid)
        placed.append(("add_parent_call", guid))
    q = M.call("add_parent_call", {"graphId": graph, "function": "NoSuchFunction_zz"})
    check("T336 an unknown function name refuses", q.get("ok") is False, q.get("error"))

    # ------------------------------------------------------------------ T337 add_get_data_table_row
    print("\n=== T337: add_get_data_table_row - against a REAL DataTable, not fabricated ===")
    tables = M.call("find_assets", {"class": "DataTable", "limit": 1}).get("assets") or []
    if tables:
        tpath = tables[0].get("path")
        rows = M.call("read_datatable", {"path": tpath}).get("rows") or []
        # read_datatable's "rows" is UE's OWN GetTableAsJSON() export - an array of row objects each
        # carrying "Name" (capitalised, UE's DataTableJSON convention), not a caller-invented shape.
        row_name = None
        if rows and isinstance(rows[0], dict):
            row_name = rows[0].get("Name")
        r = M.call("add_get_data_table_row",
                   {"graphId": graph, "dataTable": tpath, "rowName": row_name or ""})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T337 succeeds against a real table", r.get("ok") is True, (r.get("error") or json.dumps(r))[:200])
        if r.get("ok") and guid:
            check("T337 the node is really in the graph", node_exists(graph, guid),
                  "guid %s does not resolve" % guid)
            placed.append(("add_get_data_table_row", guid))
            if row_name:
                # rowNameApplied is UE's own pin default-value export text for the row-name pin, not a
                # bare echo of the input - confirmed live it comes back "<RowName>|None|" (an FName-ish
                # export-text suffix this suite has not fully explained). startswith is the honest
                # check: it proves the real name landed on the pin without asserting an exact format
                # this suite does not own.
                check("T337 the real row name was accepted onto the pin",
                      (r.get("rowNameApplied") or "").startswith(row_name), r.get("rowNameApplied"))
    else:
        print("  SKIP  add_get_data_table_row - no DataTable asset found on this project")

    # ------------------------------------------------------------------ T338 add_create_widget
    print("\n=== T338: add_create_widget - against a REAL WidgetBlueprint, not fabricated ===")
    widgets = M.call("find_assets", {"class": "WidgetBlueprint", "limit": 1}).get("assets") or []
    if widgets:
        r = M.call("add_create_widget", {"graphId": graph, "widgetClass": widgets[0].get("path")})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T338 succeeds against a real widget class", r.get("ok") is True,
              (r.get("error") or json.dumps(r))[:200])
        if r.get("ok") and guid:
            check("T338 the node is really in the graph", node_exists(graph, guid),
                  "guid %s does not resolve" % guid)
            placed.append(("add_create_widget", guid))
    else:
        print("  SKIP  add_create_widget - no WidgetBlueprint asset found on this project")
    q = M.call("add_create_widget", {"graphId": graph, "widgetClass": "Actor"})
    check("T338 a non-UserWidget class is refused", q.get("ok") is False, q.get("error"))

    c = M.call("compile", {"blueprintId": bid})
    check("T336-338 the blueprint still compiles with everything placed so far",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:200]))

    # ------------------------------------------------------------------ T339 add_component_bound_event
    print("\n=== T339: add_component_bound_event - needs a real component on the scratch blueprint ===")
    comp = M.call("add_component",
                  {"blueprintId": bid, "componentClass": "SphereComponent", "name": "TestSphere"})
    check("T339 (setup) a SphereComponent is added to the scratch blueprint",
          comp.get("ok") is True, json.dumps(comp)[:200])
    if comp.get("ok"):
        r = M.call("add_component_bound_event",
                   {"blueprintId": bid, "component": "TestSphere", "dispatcher": "OnComponentBeginOverlap"})
        guid = r.get("nodeGuid") or (r.get("node") or {}).get("guid")
        check("T339 binding a real delegate on a real component succeeds",
              r.get("ok") is True, (r.get("error") or json.dumps(r))[:200])
        if r.get("ok") and guid:
            check("T339 the bound-event node is really in the graph", node_exists(graph, guid),
                  "guid %s does not resolve" % guid)
            placed.append(("add_component_bound_event", guid))

        q = M.call("add_component_bound_event",
                   {"blueprintId": bid, "component": "TestSphere", "dispatcher": "NoSuchDelegate_zz"})
        check("T339 an unknown delegate name refuses", q.get("ok") is False, q.get("error"))
        q = M.call("add_component_bound_event",
                   {"blueprintId": bid, "component": "NoSuchComponent_zz", "dispatcher": "OnComponentBeginOverlap"})
        check("T339 an unknown component name refuses", q.get("ok") is False, q.get("error"))

        c = M.call("compile", {"blueprintId": bid})
        check("T339 the blueprint still compiles with the bound event too",
              c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    # Deliberately LAST of the T337 checks, after every "still compiles" assertion above (T336-338's
    # and T339's): this node is left unconfigured on purpose (no dataTable/rowName), and an
    # unconfigured Get Data Table Row node correctly fails to compile - real UE behaviour, not a bug.
    # Asserting a clean compile anywhere while it sits in the graph would have been this suite's own
    # mistake, so nothing after this point checks compile again.
    q = M.call("add_get_data_table_row", {"graphId": graph})
    check("T337 with no dataTable/rowName still succeeds (both optional - the node is placed "
          "untyped)", q.get("ok") is True, json.dumps(q)[:200])
    if q.get("ok"):
        g2 = q.get("nodeGuid") or (q.get("node") or {}).get("guid")
        if g2:
            check("T337 the untyped node is really in the graph", node_exists(graph, g2),
                  "guid %s does not resolve" % g2)
            placed.append(("add_get_data_table_row", g2))

    # ------------------------------------------------------------------ T340 add_widget_binding
    print("\n=== T340: add_widget_binding - own scratch WidgetBlueprint with a real tree widget ===")
    wpath = "/Game/_MifNodes/WBP_%d" % st
    wmade = M.call("create_blueprint", {"path": wpath, "blueprintType": "WidgetBlueprint"})
    wbid = wmade.get("blueprintId")
    check("T340 (setup) a scratch WidgetBlueprint is created", wmade.get("ok") is True and bool(wbid),
          json.dumps(wmade)[:200])
    if wbid:
        # A fresh WidgetBlueprint is NOT an empty tree: create_blueprint already auto-creates a root
        # CanvasPanel_0 (confirmed live via list_tree_widgets), so asRoot:True is refused as "tree
        # already has a root" - the TextBlock goes in as that root's child instead.
        tw = M.call("add_tree_widget",
                    {"blueprintId": wbid, "widgetClass": "TextBlock", "name": "TestText",
                     "parentName": "CanvasPanel_0"})
        check("T340 (setup) a TextBlock is added under the auto-created root", tw.get("ok") is True, json.dumps(tw)[:200])
        if tw.get("ok"):
            r = M.call("add_widget_binding",
                       {"blueprintId": wbid, "widgetName": "TestText",
                        "propertyName": "Text", "functionName": "GetTestText"})
            check("T340 binding a property on a real tree widget succeeds",
                  r.get("ok") is True, (r.get("error") or json.dumps(r))[:200])
            check("T340 and reports a real bindingCount",
                  isinstance(r.get("bindingCount"), (int, float)) and r.get("bindingCount") > 0,
                  r.get("bindingCount"))

            q = M.call("add_widget_binding",
                       {"blueprintId": wbid, "widgetName": "NoSuchWidget_zz",
                        "propertyName": "Text", "functionName": "GetTestText"})
            check("T340 an unknown widget name refuses (would be dropped silently on compile)",
                  q.get("ok") is False, q.get("error"))
        # delete_asset is confirm-gated too, and a plain M.call here would silently no-op the same way
        # the pre-scratch_confirm draft of every cleanup in this file did (mifaudit strips confirm, the
        # call "succeeds" with ok:false, and the scratch asset is quietly left behind in memory - never
        # saved, so not a disk leak, but not actually cleaned up either). Route through scratch_confirm
        # so cleanup is real.
        SC.confirm_call("delete_asset", {"path": wpath})

    SC.confirm_call("delete_asset", {"path": bpath})
    SC.confirm_call("delete_asset", {"path": spath})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
