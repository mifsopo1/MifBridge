"""list_layers / set_layer_visibility / modify_actor_layers - the legacy ULayers system.

FILED AND WRITTEN 2026-08-31, after refreshing endpoints_current.json - which was 82 endpoints stale
and therefore blind to most of the surface. With it current, these three were named in NO suite at
all: 445 endpoints, 420 named somewhere, and this was three of the twenty-five.

WHY IT MATTERS MORE THAN "three uncovered endpoints" SUGGESTS. There are two unrelated systems here
with confusingly similar names: the legacy ULayers (this) and World Partition Data Layers
(list_data_layers, covered by test_data_layer_writes). list_layers' own didYouMean points at the
other one, which is the tell that callers mix them up - and a partitioned map is exactly where
someone reaches for the wrong one, because that is where Data Layers exist. So L104 asserts the
refusal that redirects, not just that a bad key is rejected.

EVERY WRITE IS READ BACK THROUGH list_layers, a DIFFERENT endpoint, rather than through the writer's
own report. modify_actor_layers returns its own opinion of what it did; the layer list is the thing
that decides whether it happened.
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


def layer_named(name):
    """The layer row from list_layers, or None. Read back through the READER, always."""
    for row in (M.call("list_layers", {"limit": 400}).get("layers") or []):
        if row.get("name") == name:
            return row
    return None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    st = int(time.time()) % 100000
    layer = "MifTestLayer%d" % st
    actor = None
    created = False
    try:
        # ------------------------------------------------------------------ L100 the read
        print("\n=== L100: list_layers, and the field that says which system you are in ===")
        base = M.call("list_layers", {})
        check("L100 list_layers answers", base.get("ok") is True, json.dumps(base)[:220])
        check("L100 it reports counts and whether the level is PARTITIONED - which is what tells "
              "you whether you wanted Data Layers instead",
              isinstance(base.get("count"), (int, float))
              and base.get("levelIsPartitioned") is not None, json.dumps(base)[:250])
        check("L100 and does NOT include member actors by default - that is the expensive part",
              all("actors" not in (r or {}) for r in (base.get("layers") or [])),
              json.dumps(base.get("layers"))[:200])

        # ------------------------------------------------------------------ L101 create
        print("\n=== L101: create a layer, judged by the LISTER rather than the writer ===")
        made = M.call("modify_actor_layers", {"operation": "create", "layer": layer})
        check("L101 create succeeds", made.get("ok") is True, json.dumps(made)[:250])
        created = made.get("ok") is True
        row = layer_named(layer)
        check("L101 and list_layers - a DIFFERENT endpoint - now reports it",
              row is not None, "layer %r not in the list afterwards" % layer)

        # ------------------------------------------------------------------ L102 membership
        print("\n=== L102: an actor joins the layer, and the layer says so ===")
        q = SC.spawn_tracked("spawn_actor_in_level", {
            "class": "/Script/Engine.StaticMeshActor",
            "location": {"x": 1970000 + st, "y": 1970000 + st, "z": 50000},
            "label": "MifLayerActor%d" % st})
        actor = ((q.get("actor") or {}).get("actorPath")) or q.get("actorPath")
        check("L102 (setup) a scratch actor exists", bool(actor), json.dumps(q)[:200])
        # THE LEVEL THE ACTOR LANDS IN, not the world. AActor::SupportsLayers reads
        # GetLevel()->bIsPartitioned, so a classic streaming sublevel made current inside a
        # partitioned world holds layer members fine. Branching on levelIsPartitioned (the
        # PERSISTENT level) asserted a refusal that did not happen the moment another suite
        # left a sublevel current - three failures, and the endpoint was right each time.
        partitioned = base.get("currentLevelIsPartitioned") is True
        print("  editing level is %s (persistent level partitioned=%s)"
              % ("WORLD PARTITIONED" if partitioned else "classic",
                 base.get("levelIsPartitioned")))
        if actor and partitioned:
            # CLASSIC LAYERS CANNOT HOLD AN ACTOR IN A PARTITIONED WORLD - AActor::SupportsLayers
            # returns false for every actor in one. The refusal IS the behaviour worth testing
            # here, and it is the whole reason list_layers reports levelIsPartitioned at all.
            add = M.raw_post("modify_actor_layers", {"operation": "add", "actorPaths": [actor],
                                                     "layer": layer})
            check("L102 on a WORLD PARTITIONED level, adding an actor to a classic layer is "
                  "REFUSED rather than silently doing nothing",
                  add.get("ok") is False, json.dumps(add)[:250])
            check("L102 and the refusal names the engine predicate that decides it, so the caller "
                  "learns it is the WORLD that is wrong, not the layer",
                  "SupportsLayers" in (add.get("error") or ""), (add.get("error") or "")[:260])
            check("L102 the layer is still there and still empty - a refused add changed nothing",
                  (layer_named(layer) or {}).get("actorCount") in (0, None),
                  json.dumps(layer_named(layer))[:220])
        elif actor:
            add = M.call("modify_actor_layers", {"operation": "add", "actorPaths": [actor],
                                                 "layer": layer})
            check("L102 adding the actor succeeds", add.get("ok") is True, json.dumps(add)[:250])
            withactors = M.call("list_layers", {"includeActors": True, "limit": 400})
            mine = [r for r in (withactors.get("layers") or []) if r.get("name") == layer]
            # Membership read off the layer, not off the call that claimed to add it.
            check("L102 and the layer lists that actor as a member - read from list_layers, not "
                  "from the writer's own report",
                  bool(mine) and actor in (mine[0].get("actors") or []),
                  json.dumps(mine)[:300])
            check("L102 includeActors also reports a member count",
                  bool(mine) and isinstance(mine[0].get("actorCount"), (int, float)),
                  json.dumps(mine)[:200])

            rem = M.call("modify_actor_layers", {"operation": "remove", "actorPaths": [actor],
                                                 "layer": layer})
            check("L102 removing it succeeds", rem.get("ok") is True, json.dumps(rem)[:220])
            after = M.call("list_layers", {"includeActors": True, "limit": 400})
            mine2 = [r for r in (after.get("layers") or []) if r.get("name") == layer]
            check("L102 and the layer no longer lists it",
                  not mine2 or actor not in (mine2[0].get("actors") or []),
                  json.dumps(mine2)[:250])

        # ------------------------------------------------------------------ L105 implicit create
        print("\n=== L105: a call that changes NOTHING must not leave a layer behind ===")
        # WHY THIS IS THE ONE WORTH PINNING. `add` CREATES a layer name that does not exist -
        # deliberately, because that is what the Outliner does when you drag onto a new name. So the
        # order of two guards decides whether a wholly failed call has a permanent side effect: if
        # the per-name creation loop ran before actor resolution, then `add` with a typo in BOTH the
        # layer name and the actor path would resolve nothing, change nothing, report a failure -
        # and still leave a real empty layer in the level. There is no error for that and no undo
        # step; the only defence is that resolution happens first.
        #
        # Probed against the live editor before it was written: the layer really is not created.
        # This exists so it stays that way.
        ghost = "MifGhostLayer%d" % st
        pre = [r.get("name") for r in (M.call("list_layers", {"limit": 400}).get("layers") or [])]
        check("L105 (setup) the layer name is not already in use", ghost not in pre, ghost)
        dud = M.raw_post("modify_actor_layers", {
            "operation": "add", "layer": ghost,
            "actorPaths": ["/Game/NoSuchActor_zz.NoSuchActor_zz"]})
        check("L105 an add whose actors do not resolve is REFUSED", dud.get("ok") is False,
              json.dumps(dud)[:250])
        check("L105 and the refusal says NOTHING was changed, which is a claim about the LEVEL and "
              "not just about the actors",
              "NOTHING was changed" in (dud.get("error") or ""), (dud.get("error") or "")[:240])
        post = [r.get("name") for r in (M.call("list_layers", {"limit": 400}).get("layers") or [])]
        check("L105 and no layer by that name exists afterwards - actor resolution runs BEFORE the "
              "creation loop, so a doubly-mistyped call leaves no permanent empty layer",
              ghost not in post,
              "the refused call created %r anyway - %d layers now" % (ghost, len(post)))

        # ------------------------------------------------------------------ L103 visibility
        print("\n=== L103: visibility, measured off the layer ===")
        was = (layer_named(layer) or {}).get("visible")
        hide = M.call("set_layer_visibility", {"layer": layer, "visible": False})
        check("L103 hiding the layer succeeds", hide.get("ok") is True, json.dumps(hide)[:220])
        check("L103 and list_layers reports it hidden - the write is judged by the reader",
              (layer_named(layer) or {}).get("visible") is False,
              "visible=%r (was %r)" % ((layer_named(layer) or {}).get("visible"), was))
        show = M.call("set_layer_visibility", {"layer": layer, "visible": True})
        check("L103 showing it again succeeds and the list agrees",
              show.get("ok") is True and (layer_named(layer) or {}).get("visible") is True,
              json.dumps(show)[:220])

        # ------------------------------------------------------------------ L104 the guards
        print("\n=== L104: the refusals, including the one that redirects ===")
        # THE one worth having. Two systems, similar names, and the partitioned map where someone
        # is most likely to want the OTHER one is exactly where this one still exists.
        wrong = M.raw_post("list_layers", {"dataLayers": True})
        check("L104 `dataLayers` on list_layers is refused BY NAME and points at "
              "list_data_layers - two different systems with confusable names",
              wrong.get("ok") is False and "list_data_layers" in (wrong.get("error") or ""),
              (wrong.get("error") or "")[:250])
        novis = M.raw_post("set_layer_visibility", {"layer": layer})
        check("L104 set_layer_visibility with no `visible` is refused rather than defaulted",
              novis.get("ok") is False, (novis.get("error") or "")[:220])
        hidden = M.raw_post("set_layer_visibility", {"layer": layer, "hidden": True})
        check("L104 `hidden` is refused and told it is `visible`, inverted",
              hidden.get("ok") is False and "inverted" in (hidden.get("error") or ""),
              (hidden.get("error") or "")[:220])
        badop = M.raw_post("modify_actor_layers", {"operation": "obliterate", "layer": layer})
        check("L104 an unknown operation is refused and names the real ones",
              badop.get("ok") is False and "create" in (badop.get("error") or ""),
              (badop.get("error") or "")[:220])
        nodelete = M.raw_post("modify_actor_layers", {"operation": "delete", "layer": layer})
        # Deleting a layer is destructive, so it is gated - and the gate must actually gate.
        check("L104 delete without confirm is REFUSED, and the layer survives",
              nodelete.get("ok") is False and layer_named(layer) is not None,
              json.dumps(nodelete)[:220])
    finally:
        if created:
            # scratch_confirm REFUSES this, and correctly: a layer is addressed by NAME, so the
            # payload carries no /Game/_Mif... path and nothing proves the target is scratch. The
            # guard cannot tell this layer from one somebody cares about. Widening it to trust a
            # name prefix would weaken the single mechanism that keeps confirm:true away from real
            # content, to tidy up after a test - so the layer is left behind KNOWINGLY instead.
            try:
                gone = SC.confirm_call("modify_actor_layers", {"operation": "delete",
                                                               "layer": layer})
                check("(cleanup) the scratch layer is deleted and gone from list_layers",
                      gone.get("ok") is not False and layer_named(layer) is None,
                      json.dumps(gone)[:220])
            except SC.NotScratch:
                print("  NOTE  '%s' was left behind. Deleting a layer needs confirm:true, and"
                      % layer)
                print("        scratch_confirm cannot prove a layer NAME is scratch - there is no")
                print("        asset path in the payload. It lives in the unsaved /Temp level and")
                print("        goes away with the next editor restart. Reported, not worked around.")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
