"""The success paths of the confirm-gated endpoints - previously untestable, and the riskiest ones.

Every suite written tonight ended with the same note: the SUCCESS path of some destructive verb is not
exercised, because it requires confirm=true and the audit harness strips `confirm` alongside `save` and
`force`. That guard is correct - it is why an unattended run cannot destroy a real asset - but the cost
had reached roughly eleven endpoints, and they are exactly the ones where a silent failure costs most.
A rename that misses its references, or a removal that leaves half the thing behind, is invisible from
the caller's side.

tools/scratch_confirm.py resolves it without weakening anything: confirm is sent only when EVERY path
in the payload lies under /Game/_Mif, which is checked mechanically and self-tested. `save` keeps no
exemption at all, because it is the one flag that would turn a disposable test artefact into a real
asset. A payload with no path is refused rather than allowed - absence of evidence is not evidence of
safety.

What each test asks is deliberately not "did it return ok". It is whether the thing that makes these
operations dangerous actually happened:

  T341  rename_variable  - do the GET and SET nodes follow the rename, or keep pointing at a name that
                           no longer exists?
  T342  rename_function  - does the graph really carry the new name afterwards?
  T343  remove_node      - is the node gone, and does the graph still compile without it?
  T344  remove_component - is it gone from the SCS, and did its child survive rather than vanish with
                           it?
  T345  enum removal     - do the SURVIVING entries keep their display names, or shift?
  T346  datatable rows   - does a written row read back with the value it was given?
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


def titles(graph):
    return [n.get("title") or "" for n in (M.call("list_nodes", {"graphId": graph}).get("nodes") or [])]


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T340 the guard itself
    print("\n=== T340: the scratch-only guard, before trusting it with anything ===")
    for label, payload in (("a real game asset", {"path": "/Game/Characters/Alisha"}),
                           ("no path at all", {}),
                           ("save, which has no exemption", {"path": "/Game/_MifX/A", "save": True}),
                           ("a real path nested inside", {"a": {"b": ["/Game/Real/Thing"]}})):
        try:
            SC.check(payload)
            check("T340 refuses %s" % label, False, "IT ALLOWED IT: %s" % json.dumps(payload))
        except SC.NotScratch:
            check("T340 refuses %s" % label, True)
    try:
        SC.check({"path": "/Game/_MifDT/T"})
        check("T340 and allows a scratch-only payload", True)
    except SC.NotScratch as e:
        check("T340 and allows a scratch-only payload", False, str(e))

    # ------------------------------------------------------------------ T340b the spawn proof
    print("\n=== T340b: the level-actor exemption must be PROOF, not the caller's word ===")
    # A level actor's path is in the open level's package, never under /Game/_Mif, so the prefix
    # check can only ever refuse it. spawn_tracked closes that by remembering what THIS process
    # watched being spawned. The whole control rests on there being no way to CLAIM membership, so
    # that is what these assert - the negative cases matter more than the positive one.
    for label, path in (("an actor it never saw spawned",
                         "/Game/Maps/Real.Real:PersistentLevel.SomebodyElsesActor"),
                        ("an actor path that merely looks scratch-ish",
                         "/Game/_MifNot/../Real.Real:PersistentLevel.A")):
        try:
            SC.check({"actorPath": path})
            check("T340b refuses %s" % label, False, "IT ALLOWED IT: %s" % path)
        except SC.NotScratch:
            check("T340b refuses %s" % label, True)

    check("T340b there is no public way to assert a path into the trusted set - proof, not honour",
          not any(n in dir(SC) for n in ("track", "trust", "add_spawned", "mark_spawned")),
          [n for n in dir(SC) if n in ("track", "trust", "add_spawned", "mark_spawned")])

    # And the positive half: an actor this process really did spawn becomes checkable.
    sp = SC.spawn_tracked("spawn_actor_in_level", {
        "class": "/Script/Engine.StaticMeshActor",
        "location": {"x": 1980000 + st, "y": 1980000 + st, "z": 60000},
        "label": "MifGuardProbe%d" % st})
    probe = ((sp.get("actor") or {}).get("actorPath")) or sp.get("actorPath")
    check("T340b (setup) a probe actor was spawned", bool(probe), json.dumps(sp)[:200])
    if probe:
        check("T340b spawned_here reports it", SC.spawned_here(probe) is True, probe)
        try:
            SC.check({"actorPath": probe})
            check("T340b and check() now accepts it - the guard can speak about level actors", True)
        except SC.NotScratch as e:
            check("T340b and check() now accepts it", False, str(e))
        # The delete goes THROUGH the guard rather than around it, which is the point of all this.
        d = M.cleanup_level_actor(probe, "guard probe")
        check("T340b the tracked actor deletes through confirm_call", d.get("ok") is True,
              json.dumps(d)[:200])
        still = M.call("list_level_actors", {"nameContains": "MifGuardProbe"}).get("actors") or []
        check("T340b and it is really gone",
              not any(a.get("actorPath") == probe for a in still), probe)

    # ------------------------------------------------------------------ T341 rename_variable
    print("\n=== T341: does a renamed variable take its nodes with it? ===")
    bp = "/Game/_MifCG/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bp, "parentClass": "Actor"}).get("blueprintId")
    M.call("add_variable", {"blueprintId": bid, "name": "Price", "type": "float"})
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    g = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    M.call("add_variable_get", {"graphId": g, "var": "Price", "x": 0, "y": 0})
    M.call("add_variable_set", {"graphId": g, "var": "Price", "x": 0, "y": 200})
    M.call("compile", {"blueprintId": bid})
    check("T341 the graph references the variable before renaming",
          any("Price" in t for t in titles(g)), str(titles(g)))

    r = SC.confirm_call("rename_variable", {"blueprintId": bid, "oldName": "Price", "newName": "Cost"})
    check("T341 the rename succeeds", r.get("ok") is True, json.dumps(r)[:170])
    now = titles(g)
    # THE assertion. A rename that updates the declaration and not the references leaves a graph full
    # of nodes pointing at a name that no longer exists, and it still compiles until something reads it.
    check("T341 the nodes followed the rename", any("Cost" in t for t in now), str(now))
    check("T341 and nothing still says the old name", not any("Price" in t for t in now), str(now))
    names = [v.get("name") for v in (M.call("list_variables", {"blueprintId": bid}).get("variables") or [])]
    check("T341 the variable itself is renamed", "Cost" in names and "Price" not in names, str(names))
    c = M.call("compile", {"blueprintId": bid})
    check("T341 and it compiles afterwards",
          c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T342 rename_function
    print("\n=== T342: renaming a function ===")
    fn = M.call("create_function", {"blueprintId": bid, "name": "GetPrice_%d" % st})
    check("T342 a function exists to rename", fn.get("ok") is True, json.dumps(fn)[:150])
    r = SC.confirm_call("rename_function", {"blueprintId": bid, "oldName": "GetPrice_%d" % st,
                                            "newName": "GetCost_%d" % st})
    check("T342 the rename succeeds", r.get("ok") is True, json.dumps(r)[:170])
    gnames = [x.get("name") for x in (M.call("list_graphs", {"blueprintId": bid}).get("graphs") or [])]
    check("T342 the graph carries the new name", "GetCost_%d" % st in gnames, str(gnames))
    check("T342 and not the old one", "GetPrice_%d" % st not in gnames, str(gnames))
    c = M.call("compile", {"blueprintId": bid})
    check("T342 and it compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T343 remove_node
    print("\n=== T343: removing a node ===")
    n = M.call("add_branch", {"graphId": g, "x": 600, "y": 0})
    guid = n.get("nodeGuid") or (n.get("node") or {}).get("guid")
    check("T343 a node exists to remove", bool(guid), json.dumps(n)[:150])
    r = SC.confirm_call("remove_node", {"graphId": g, "nodeGuid": guid})
    check("T343 the removal succeeds", r.get("ok") is True, json.dumps(r)[:170])
    gone = M.call("get_node", {"graphId": g, "nodeGuid": guid})
    check("T343 and the node is really gone", gone.get("ok") is False or not (gone.get("node") or {}),
          json.dumps(gone)[:150])
    c = M.call("compile", {"blueprintId": bid})
    check("T343 the graph still compiles without it",
          c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T344 remove_component
    print("\n=== T344: removing a component promotes its children ===")
    M.call("add_component", {"blueprintId": bid, "componentClass": "SceneComponent", "name": "Parent"})
    M.call("add_component", {"blueprintId": bid, "componentClass": "StaticMeshComponent",
                             "name": "Child", "parentName": "Parent"})
    M.call("compile", {"blueprintId": bid})
    have = [c.get("name") for c in (M.call("list_components", {"blueprintId": bid}).get("components") or [])]
    check("T344 both components exist", "Parent" in have and "Child" in have, str(have))
    r = SC.confirm_call("remove_component", {"blueprintId": bid, "name": "Parent"})
    check("T344 the parent is removed", r.get("ok") is True, json.dumps(r)[:170])
    have = [c.get("name") for c in (M.call("list_components", {"blueprintId": bid}).get("components") or [])]
    check("T344 and it is gone from the SCS", "Parent" not in have, str(have))
    # The handler uses RemoveNodeAndPromoteChildren, so the child must SURVIVE. A child vanishing with
    # its parent is the silent loss this asks about.
    check("T344 while its child survived rather than vanishing with it", "Child" in have, str(have))
    c = M.call("compile", {"blueprintId": bid})
    check("T344 and the blueprint compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T345 remove_enum_value
    print("\n=== T345: removing an enum entry leaves the others intact ===")
    epath = "/Game/_MifCG/E_%d" % st
    ep = M.call("create_enum", {"path": epath}).get("enumPath")
    for v in ("Common", "Rare", "Legendary"):
        M.call("add_enum_value", {"enum": ep, "value": v})
    before = [e.get("displayName") for e in (M.call("list_enum_values", {"enum": ep}).get("entries") or [])]
    check("T345 three entries exist", before == ["Common", "Rare", "Legendary"], str(before))
    r = SC.confirm_call("remove_enum_value", {"enum": ep, "value": "Rare"})
    check("T345 the middle entry is removed", r.get("ok") is True, json.dumps(r)[:170])
    after = [e.get("displayName") for e in (M.call("list_enum_values", {"enum": ep}).get("entries") or [])]
    # THE assertion: removing the middle one must not disturb the names of the others. Enum removal
    # shifts indices, and a display name following the wrong index is silent corruption.
    check("T345 and the survivors keep their own names", after == ["Common", "Legendary"], str(after))

    # ------------------------------------------------------------------ T346 datatable rows
    print("\n=== T346: a written row reads back with the value it was given ===")
    dt = None
    for cand in ("RichTextStyleRow", "RichImageRow"):
        made = M.call("create_datatable", {"path": "/Game/_MifCG/DT_%d" % st, "rowStruct": cand})
        if made.get("ok"):
            dt = made.get("dataTablePath")
            break
    check("T346 a table exists to write to", bool(dt), "no row struct was accepted")
    if dt:
        w = SC.confirm_call("write_datatable_rows", {"path": dt, "rows": [{"Name": "Row_A"}]})
        check("T346 the write succeeds", w.get("ok") is True, json.dumps(w)[:200])
        rd = M.call("read_datatable", {"path": dt})
        rows = rd.get("rows") or []
        # Verified by READING the table, not from the write's own count.
        check("T346 and the row is really in the table",
              any((x.get("Name") or x.get("name")) == "Row_A" for x in rows),
              json.dumps(rows)[:200])
        # rowNames, not rows: delete takes NAMES while write takes row OBJECTS. The endpoint
        # catches the confusion by name rather than failing vaguely, which is how this was found.
        d = SC.confirm_call("delete_datatable_rows", {"path": dt, "rowNames": ["Row_A"]})
        check("T346 the row can be deleted again", d.get("ok") is True, json.dumps(d)[:170])
        rows = M.call("read_datatable", {"path": dt}).get("rows") or []
        check("T346 and it is gone",
              not any((x.get("Name") or x.get("name")) == "Row_A" for x in rows),
              json.dumps(rows)[:170])
        # The DataTable asset itself was never deleted here, only emptied of rows - a real, separate
        # gap found via a full-suite regression sweep: test_node_spawns.py's T337 looks up "a real
        # DataTable" via find_assets with no pathPrefix filter, and when this suite runs first
        # (alphabetically "confirm" < "node") it can pick up THIS now-empty scratch table instead of
        # a genuine DDS2 one, producing an empty rowName and a node that fails to compile - a false
        # failure in a different suite entirely, caused by this suite's own incomplete cleanup.
        SC.confirm_call("delete_asset", {"path": dt})

    SC.confirm_call("delete_asset", {"path": bp})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
