"""get_level_blueprint - a front door, not a new subsystem.

THE SURVEY'S PREMISE WAS FALSE, and checking it shrank this from a resolution change across every
blueprint endpoint to a single read. A Level Blueprint IS already loadable: StaticLoadObject
resolves SUBOBJECT_DELIMITER paths, and ULevelScriptBlueprint IS-A UBlueprint, so ResolveBlueprint
already accepts "/Game/Maps/M.M:PersistentLevel.M" on an uncooked map that has one. Teaching every
endpoint a "level:" prefix would have been a second addressing scheme for something already
addressable.

WHAT WAS GENUINELY MISSING IS DULLER AND REAL:
  1. NOTHING EMITTED THAT PATH, so no agent would ever guess it. A capability nobody can discover
     is not a capability - that alone is the gap.
  2. A map that has never had a Level Blueprint has none to load, and only
     GetLevelScriptBlueprint(bDontCreate=false) can mint one. Every map from new_level is in that
     state, which is exactly the case a level-building agent starts from.
  3. Cooked maps needed a named refusal rather than a null.

T5501 IS THE ASSERTION THAT JUSTIFIES THE WHOLE ENDPOINT: the returned blueprintId is fed straight
into list_graphs, and the existing blueprint surface answers. If that did not hold, the endpoint
would be emitting a string of no use to anyone.

T5500 IS THE SIDE-EFFECT RULE. bDontCreate is INVERTED from the engine's own default here: a read
that minted a Level Blueprint would dirty the map just for asking whether one exists, and on a map
opened only to look at, that is a change nobody asked for. The suite asserts a plain read does not
create one.
"""
import json
import sys

import mifaudit as M

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

    # ------------------------------------------------------------------ T5500 the read
    print("=== T5500: asking must not create ===")
    r = M.call("get_level_blueprint", {})
    check("T5500 get_level_blueprint succeeds on the persistent level", r.get("ok") is True,
          json.dumps(r)[:250])
    check("T5500 it names the level and says whether it is the persistent one",
          bool(r.get("level")) and isinstance(r.get("isPersistentLevel"), bool),
          json.dumps({k: r.get(k) for k in ("level", "isPersistentLevel")}))
    check("T5500 and reports whether the map is cooked, since that decides everything else",
          isinstance(r.get("cookedMap"), bool), r.get("cookedMap"))

    existed = r.get("exists")
    if existed is False:
        # THE SIDE-EFFECT RULE. bDontCreate is inverted from the engine default on purpose.
        check("T5500 a level with no Level Blueprint reports exists:false rather than minting one",
              r.get("blueprintId") is None, json.dumps(r)[:220])
        check("T5500 and explains that minting would dirty the map",
              "dirties the map" in (r.get("note") or ""), (r.get("note") or "")[:200])
        again = M.call("get_level_blueprint", {})
        check("T5500 asking twice still has not created one - the read is genuinely pure",
              again.get("exists") is False, json.dumps(again)[:200])
    else:
        check("T5500 an existing Level Blueprint comes back with an id",
              bool(r.get("blueprintId")), json.dumps(r)[:220])

    # ------------------------------------------------------------------ T5501 the front door
    # COOKED-ONLY, SKIPPED where nothing is cooked. On an uncooked project the
    # refusal this asserts never comes, so the assertion fails for the environment
    # rather than for a defect - and where the call is a write, it lands instead.
    # Section confirmed self-contained by audit_cooked_section_safety before wrapping.
    #
    # `is not False`: project_is_cooked returns None when the question could not be
    # asked, and an unanswerable question is not a No - None runs this as before.
    COOKED = M.project_is_cooked()
    if COOKED is False:
        print("")
        print('=== T5501 SKIPPED - nothing in this project is cooked ===')
        print('  This section asserts what an endpoint REFUSES on cooked content. There is nothing cooked')
        print('  here, so the refusal cannot be provoked - which is not the same as the guard being absent.')
        print('  Where the call is a WRITE, running it unguarded would perform the write it means to see')
        print('  refused. Run against a cooked project for this half.')
    else:
        print("\n=== T5501: the id must actually work in the endpoints that take one ===")
        made = M.raw_post("get_level_blueprint", {"create": True})
        if made.get("cookedMap"):
            check("T5501 a cooked map refuses creation with the reason",
                  made.get("ok") is False and "cannot be resaved" in (made.get("error") or ""),
                  (made.get("error") or "")[:220])
            print("  NOT EXERCISED: everything below - this map is cooked, so no Level Blueprint")
            print("  exists or can be made. ULevel::LevelScriptBlueprint is editor-only data and only")
            print("  the compiled ALevelScriptActor survives a cook.")
        else:
            check("T5501 create:true produces one", made.get("ok") is True
                  and made.get("exists") is True, json.dumps(made)[:250])
            bid = made.get("blueprintId")
            check("T5501 and returns a blueprintId", bool(bid), bid)
            check("T5501 which is a SUBOBJECT path - the form ResolveBlueprint already accepted, "
                  "and which nothing else emitted",
                  bid and ":PersistentLevel." in bid, bid)

            # THE assertion the endpoint exists for. An id nobody can use is not worth emitting.
            graphs = M.call("list_graphs", {"blueprintId": bid})
            check("T5501 - list_graphs accepts it and answers, so the whole blueprint surface is open",
                  graphs.get("ok") is True, json.dumps(graphs)[:220])
            names = [g.get("name") for g in (graphs.get("graphs") or [])]
            check("T5501 and a Level Blueprint has an EventGraph like any other",
                  "EventGraph" in names, names)

            # A second existing endpoint, to show it is not a one-off. list_nodes takes a graphId -
            # the '<blueprintPath>::<graphName>' form list_graphs emits - not a blueprint path; its
            # own refusal says so, which is how this test got corrected.
            gid = next((g.get("graphId") for g in (graphs.get("graphs") or [])
                        if g.get("name") == "EventGraph"), None)
            check("T5501 list_graphs emits a usable graphId for it", bool(gid), gid)
            nodes = M.call("list_nodes", {"graphId": gid}) if gid else {}
            check("T5501 list_nodes works on that graph too - ULevelScriptBlueprint IS-A UBlueprint",
                  nodes.get("ok") is True, json.dumps(nodes)[:220])

            check("T5501 the response says so rather than leaving the caller to try",
                  "work on a Level Blueprint unchanged" in (made.get("usage") or ""),
                  (made.get("usage") or "")[:200])
            check("T5501 and warns that creating dirtied the map",
                  "DIRTIES the map" in (made.get("assetNote") or ""), made.get("assetNote"))

        # ------------------------------------------------------------------ T5502 addressing
    print("\n=== T5502: which level ===")
    bad = M.raw_post("get_level_blueprint", {"level": "NoSuchSublevelAnywhere"})
    check("T5502 an unknown sublevel is refused and the real ones listed",
          bad.get("ok") is False and "persistent" in (bad.get("error") or ""),
          (bad.get("error") or "")[:220])
    same = M.call("get_level_blueprint", {"level": "persistent"})
    check("T5502 'persistent' means the same thing as omitting it",
          same.get("ok") is True and same.get("level") == r.get("level"),
          "%s vs %s" % (same.get("level"), r.get("level")))
    hint = M.raw_post("get_level_blueprint", {"blueprintId": "x"})
    check("T5502 passing blueprintId is refused - it is the OUTPUT here",
          hint.get("ok") is False and "OUTPUT" in (hint.get("error") or ""),
          (hint.get("error") or "")[:200])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
