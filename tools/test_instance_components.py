"""Instance components: the component family aimed at ONE placed actor, not the class.

"PUT A POINT LIGHT ON THIS ONE LAMP POST." Until now that meant editing the shared BP_LampPost
asset, which changes all ninety of them. Everything the component family did addressed a Blueprint's
SCS - the template every instance is built from - and there was no way to touch a single placed
actor.

AND IT IS THE COMPONENT ROUTE A COOKED PROJECT HAS. The SCS route needs a UBlueprint, which cooking
strips. Instance components are pure runtime object graph: no SCS, no source data. On a cooked
project this is not a second-best path, it is the only one.

ROUTED, NOT REWRITTEN. actorPath is an early branch in each of the four handlers; the Blueprint
paths are untouched. T2205 asserts that explicitly - a blueprintId call must still behave exactly as
before - because the risk of this change was never the new code, it was disturbing the old.

T2202 IS THE ONE THAT MATTERS. AActor::RemoveInstanceComponent only touches the InstanceComponents
array, so on a NATIVE or SCS-created component it is a SILENT NO-OP: the endpoint would report
success having removed nothing, on exactly the components an agent reaches for first. The endpoint
refuses those by name and says where to go instead, and this test proves the component is still
there afterwards rather than trusting the refusal message.

T2204 records something the live run found and reading would not have: adding a PointLightComponent
also creates the editor BILLBOARD sprite that visualises it, and destroying the light takes the
billboard with it - so componentsBefore 3 becomes remaining 1 for a single named removal. Correct
engine behaviour, and invisible as a gap between two counts, so the response now NAMES what else
went.

CLEANS UP: the scratch actor is deleted through mifaudit.cleanup_level_actor, since it spawns into
the persistent editor world that PIE stopping does not tear down.
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


def comps(path):
    return M.call("list_components", {"actorPath": path}).get("components") or []


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    base = 1030000 + st
    q = SC.spawn_tracked("spawn_actor_in_level", {"class": "/Script/Engine.StaticMeshActor",
                                        "location": {"x": base, "y": base, "z": 50000},
                                        "label": "MifInstComp%d" % st})
    actor = ((q.get("actor") or {}).get("actorPath")) or q.get("actorPath")
    check("T2200 (setup) a scratch actor is spawned far from real content", bool(actor),
          json.dumps(q)[:220])
    if not actor:
        return 1

    try:
        # ------------------------------------------------------------------ T2200 list
        print("\n=== T2200: list_components against a placed actor ===")
        l = M.call("list_components", {"actorPath": actor})
        check("T2200 list_components accepts actorPath", l.get("ok") is True, json.dumps(l)[:250])
        check("T2200 and says which kind of target it answered about",
              l.get("targetKind") == "levelActor", json.dumps(l)[:250])
        rows = l.get("components") or []
        check("T2200 it reports the actor's native component", len(rows) >= 1,
              [c.get("name") for c in rows])
        check("T2200 every row carries an origin - native / blueprintCreated / instance - because "
              "that is what decides whether it can be removed from one actor",
              all(c.get("origin") in ("native", "blueprintCreated", "instance") for c in rows),
              [(c.get("name"), c.get("origin")) for c in rows])
        check("T2200 and a componentPath usable directly as set_property's objectPath",
              all(c.get("componentPath") for c in rows), json.dumps(rows[:1])[:250])

        # ------------------------------------------------------------------ T2201 add
        print("\n=== T2201: add_component on the instance ===")
        ad = M.call("add_component", {"actorPath": actor,
                                      "componentClass": "/Script/Engine.PointLightComponent",
                                      "name": "MifTestLight"})
        check("T2201 add_component succeeds", ad.get("ok") is True, json.dumps(ad)[:300])
        made = ad.get("component") or {}
        check("T2201 the new component is an INSTANCE one - it exists on this actor, not the class",
              made.get("origin") == "instance", json.dumps(made)[:250])
        # RegisterComponent must have run: an unregistered component has no world transform and does
        # not render, so reporting one would be a number that is not yet true.
        check("T2201 and it is REGISTERED - an unregistered component renders nowhere",
              made.get("registered") is True, json.dumps(made)[:250])
        names = [c.get("name") for c in comps(actor)]
        check("T2201 read back through a second list_components call, not from add's own response",
              "MifTestLight" in names, names)

        dup = M.call("add_component", {"actorPath": actor,
                                       "componentClass": "/Script/Engine.PointLightComponent",
                                       "name": "MifTestLight"})
        check("T2201 a duplicate name is REFUSED rather than silently uniquified - a renamed "
              "component leaves you addressing one that does not exist",
              dup.get("ok") is False, json.dumps(dup)[:250])

        # ------------------------------------------------------------------ T2202 THE silent no-op
        print("\n=== T2202: removing a NATIVE component must refuse, not silently no-op ===")
        native = next((c.get("name") for c in comps(actor) if c.get("origin") == "native"), None)
        check("T2202 (setup) the actor has a native component to try", bool(native), native)
        if native:
            r = SC.confirm_call("remove_component", {"actorPath": actor, "name": native})
            check("T2202 removing a native component is REFUSED", r.get("ok") is False,
                  json.dumps(r)[:300])
            check("T2202 and the refusal explains RemoveInstanceComponent would do nothing",
                  "silently" in (r.get("error") or "").lower(), r.get("error"))
            # THE assertion. The refusal message could be right and the component gone anyway.
            check("T2202 and the component is still there afterwards",
                  native in [c.get("name") for c in comps(actor)],
                  [c.get("name") for c in comps(actor)])

        # ------------------------------------------------------------------ T2203/T2204 remove
        print("\n=== T2203-T2204: removing the instance component ===")
        nc = M.call("remove_component", {"actorPath": actor, "name": "MifTestLight"})
        check("T2203 removal without confirm is refused", nc.get("ok") is False,
              json.dumps(nc)[:250])

        before = len(comps(actor))
        d = SC.confirm_call("remove_component", {"actorPath": actor, "name": "MifTestLight"})
        check("T2203 remove_component succeeds", d.get("ok") is True, json.dumps(d)[:300])
        check("T2203 and it is really gone - read back independently",
              "MifTestLight" not in [c.get("name") for c in comps(actor)],
              [c.get("name") for c in comps(actor)])

        # Adding a light also creates its editor billboard sprite, and destroying the light takes
        # the billboard too - so more components go than were named. That is correct, and it must
        # not read as a gap between two counts.
        went = (d.get("componentsBefore") or 0) - (d.get("remaining") or 0)
        check("T2204 componentsRemoved is reported and matches the counts",
              d.get("componentsRemoved") == went,
              "componentsRemoved=%s before-after=%s" % (d.get("componentsRemoved"), went))
        if went > 1:
            check("T2204 when more than one component went, the extras are NAMED rather than left "
                  "as a difference between two numbers",
                  bool(d.get("alsoRemoved")) and bool(d.get("alsoRemovedNote")),
                  json.dumps(d)[:300])
        else:
            print("  NOTE  T2204 only one component went this time, so the collateral-removal "
                  "reporting is not exercised.")

        # ------------------------------------------------------------------ T2205 the old path
        print("\n=== T2205: the Blueprint path is undisturbed ===")
        both = M.call("add_component", {"actorPath": actor, "blueprintId": "/Game/Whatever",
                                        "componentClass": "/Script/Engine.PointLightComponent"})
        check("T2205 actorPath and blueprintId together are REFUSED - they mean different things "
              "and choosing for the caller would be wrong",
              both.get("ok") is False, json.dumps(both)[:250])
        # And the untouched Blueprint branch still answers as it always did: a bad blueprintId gets
        # the Blueprint-shaped error, not an actor-shaped one.
        bp = M.call("list_components", {"blueprintId": "/Game/NoSuchBlueprintAtAll"})
        check("T2205 a blueprintId-only call still takes the Blueprint path",
              bp.get("ok") is False and "actorPath" not in (bp.get("error") or ""),
              (bp.get("error") or "")[:200])
    finally:
        c = M.cleanup_level_actor(actor, "scratch instance-component actor")
        check("T2206 (cleanup) the scratch actor is removed from the level",
              c.get("ok") is True, json.dumps(c)[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
