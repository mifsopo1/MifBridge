"""Third batch of coverage_gaps.py's zero-coverage list: validate, nav_status, focus_viewport,
blueprint_inheritance_tree, scene_report, list_mounted_containers.

Same reasoning as the first two batches - individually-untested general-purpose reads (and one
compile-without-save) scattered across five files, grouped for one editor session. All either
read-only or, for validate, a dry-run compile that never writes to disk (its own guard message
says so explicitly).

close_asset_editors, from the same coverage list, is deliberately NOT here: exercising its
populated path needs an asset editor actually open first (open_blueprint/open_asset_editor,
themselves uncovered), which is its own separate piece of setup rather than a one-line addition
to this batch.
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


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ================================================================== T840 validate
    print("=== T840: validate (compile without saving) ===")
    bps = (M.call("find_assets", {"class": "Blueprint", "limit": 1}).get("assets") or [])
    check("T840 (setup) there is at least one Blueprint to validate", len(bps) > 0, len(bps))
    if bps:
        bp_path = bps[0].get("path")
        r = M.call("validate", {"blueprintId": bp_path})
        check("T840 it answers", r.get("ok") is True, json.dumps(r)[:200])
        check("T840 dryRun is always true", r.get("dryRun") is True, r.get("dryRun"))
        for key in ("numErrors", "numWarnings"):
            check("T840 reports %s as a real number" % key,
                  isinstance(r.get(key), (int, float)), r.get(key))
        check("T840 messages is a real array", isinstance(r.get("messages"), list), r.get("messages"))

    for label, payload in (("no blueprint", {}), ("an unknown parameter", {"blueprintId": "x", "save": True})):
        q = M.call("validate", payload)
        check("T840 %s refused" % label, q.get("ok") is False, q.get("error"))

    # ================================================================== T840b the claim, on a
    # blueprint that does NOT compile clean
    print("")
    print("=== T840b: compile and validate agree on a blueprint with real messages ===")
    # compile's own summary claims "validate {blueprintId} is the dry-run form and returns the same
    # messages". Until 2026-08-31 that was only ever checked on a CLEAN blueprint, where both sides
    # report 0 errors, 0 warnings and [] - a 0 == 0 comparison that proves nothing about the claim.
    # Confirmed by grep at the time, not assumed: every numErrors reference in all 163 suites
    # asserted it equals ZERO. Not one suite had ever seen a compile produce a message.
    #
    # FIVE ROUTES TO A BROKEN BLUEPRINT WERE TRIED FIRST AND ALL COMPILED CLEAN - removing a
    # variable a getter reads, removing a dispatcher a call node uses, an unwritten function output,
    # a cast to an unrelated class, and retyping a wired variable to an incompatible type. That last
    # one is the instructive one: it leaves the getter with TWO pins of the same name, a new one
    # with no link and the original still holding the connection, so the compiler follows the new
    # pin and finds nothing wired. The breakage is INVISIBLE to the compiler rather than absent.
    #
    # WHAT WORKS IS AN EVENT BOUND TO A COMPONENT THAT IS THEN REMOVED. The node survives the
    # removal and names a component that is no longer there, which the compiler cannot resolve and
    # cannot quietly route around.
    st = int(time.time() % 100000)
    cvpath = "/Game/_MifReads3/BP_CV%d" % st
    cvbid = M.call("create_blueprint", {"path": cvpath, "parentClass": "Actor"}).get("blueprintId")
    check("T840b (setup) a scratch blueprint", bool(cvbid), cvpath)
    if cvbid:
        M.call("add_component", {"blueprintId": cvbid, "componentClass": "StaticMeshComponent",
                                 "name": "Mesh1"})
        M.call("compile", {"blueprintId": cvbid})
        ev = M.call("add_component_bound_event", {"blueprintId": cvbid, "component": "Mesh1",
                                                  "event": "OnComponentHit"})
        check("T840b (setup) an event bound to that component", ev.get("ok") is True,
              json.dumps(ev)[:200])
        clean = M.call("compile", {"blueprintId": cvbid})
        check("T840b (setup) and it compiles clean while the component exists",
              clean.get("numErrors") == 0 and clean.get("numWarnings") == 0,
              json.dumps(clean)[:200])
        rmc = SC.confirm_call("remove_component", {"blueprintId": cvbid, "name": "Mesh1",
                                                   "confirm": True})
        check("T840b (setup) the component is removed out from under the event",
              rmc.get("ok") is True, json.dumps(rmc)[:200])

        c = M.call("compile", {"blueprintId": cvbid})
        v = M.call("validate", {"blueprintId": cvbid})
        cm, vm = (c.get("messages") or []), (v.get("messages") or [])
        # THE ASSERTION THAT MAKES THE REST MEAN ANYTHING. Without it every comparison below is
        # 0 == 0 again, which is the whole reason this claim sat unverified.
        check("T840b compile produces a REAL message - not the empty list every other suite sees",
              len(cm) > 0 and c.get("numWarnings", 0) > 0,
              "errors=%r warnings=%r msgs=%r" % (c.get("numErrors"), c.get("numWarnings"), cm))
        check("T840b and it names the node, so a caller can act on it",
              bool(cm) and bool(cm[0].get("nodeGuid")), json.dumps(cm[:1])[:220])
        # THE CLAIM ITSELF.
        check("T840b validate returns the SAME messages compile does - the claim its summary makes",
              cm == vm, "compile=%s\n          validate=%s"
              % (json.dumps(cm)[:200], json.dumps(vm)[:200]))
        check("T840b and the same counts", c.get("numErrors") == v.get("numErrors")
              and c.get("numWarnings") == v.get("numWarnings"),
              "compile %r/%r vs validate %r/%r" % (c.get("numErrors"), c.get("numWarnings"),
                                                   v.get("numErrors"), v.get("numWarnings")))
        check("T840b validate still says dryRun", v.get("dryRun") is True, v.get("dryRun"))

        # ------------------------------------------------------------ T840c graphStructureChanged
        # compile reports that the SOURCE graphs changed underneath it, and its own structureNote
        # warns that any node snapshot taken before the call is stale - "re-read with list_nodes".
        # That is exactly the read-back an agent depends on, and until 2026-08-31 nothing asserted
        # it, because the consequence classifier could not see a field named for a CHANGE.
        #
        # ASSERTED AGAINST ITS OWN ARITHMETIC RATHER THAN AGAINST THIS FIXTURE. The handler sets it
        # to `NodesBefore != NodesAfter || GuidsAdded > 0` (MifBridgeIntrospect.cpp:2329), so the
        # flag and the three counts beside it are one statement said twice. Checking them against
        # each other holds whatever this particular compile does, which is what makes it a real
        # check rather than a snapshot of one run - and it catches the failure that actually
        # matters: a flag that stops agreeing with the numbers a caller would act on.
        before, after = c.get("graphNodesBefore"), c.get("graphNodesAfter")
        added = c.get("newNodeGuids")
        check("T840c compile reports the three graph counts as real numbers",
              all(isinstance(x, (int, float)) for x in (before, after, added)),
              "before=%r after=%r newGuids=%r" % (before, after, added))
        if all(isinstance(x, (int, float)) for x in (before, after, added)):
            expected = (before != after) or (added > 0)
            check("T840c and graphStructureChanged AGREES with them - it is the same statement twice",
                  c.get("graphStructureChanged") is expected,
                  "flag=%r but before=%r after=%r newGuids=%r implies %r"
                  % (c.get("graphStructureChanged"), before, after, added, expected))
            # The note is emitted on exactly the same condition, so its presence is the third telling
            # of the same fact and must not disagree with the other two either.
            check("T840c and structureNote is present exactly when the flag is true",
                  bool(c.get("structureNote")) is expected,
                  "flag=%r note=%r" % (c.get("graphStructureChanged"),
                                       (c.get("structureNote") or "")[:80]))
        SC.confirm_call("delete_asset", {"path": cvpath, "confirm": True})

    # ================================================================== T841 nav_status
    print("")
    print("=== T841: nav_status ===")
    r = M.call("nav_status", {})
    check("T841 it answers", r.get("ok") is True, json.dumps(r)[:200])
    for key in ("hasNavSystem", "building", "ready"):
        check("T841 %s is a real bool" % key, isinstance(r.get(key), bool), r.get(key))
    for key in ("boundsVolumes", "navMeshActors", "tiles"):
        check("T841 %s is a real number" % key, isinstance(r.get(key), (int, float)), r.get(key))
    check("T841 reports which world it read", bool(r.get("world")), r.get("world"))
    if r.get("tiles") == 0 and not r.get("building"):
        check("T841 ready is false when there are zero tiles", r.get("ready") is False, r)

    q = M.call("nav_status", {"world": "pie"})
    check("T841 an unknown parameter (world, not supported here) is refused",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T842 focus_viewport
    print("")
    print("=== T842: focus_viewport ===")
    r = M.call("focus_viewport", {})
    check("T842 framing the whole level answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T842 reports how many actors were framed",
          isinstance(r.get("actorCount"), (int, float)), r.get("actorCount"))
    for key in ("location", "rotation"):
        v = r.get(key) or {}
        check("T842 %s has real x/y/z" % key,
              all(isinstance(v.get(a), (int, float)) for a in ("x", "y", "z")), v)

    actors = (M.call("list_level_actors", {"limit": 1}).get("actors") or [])
    if actors:
        r2 = M.call("focus_viewport", {"actorPath": actors[0].get("actorPath")})
        check("T842 framing one real actor answers", r2.get("ok") is True, json.dumps(r2)[:200])
        check("T842 and framed exactly one actor", r2.get("actorCount") == 1, r2.get("actorCount"))

    q = M.call("focus_viewport", {"actorPath": "/Game/NoSuchActor_zz"})
    check("T842 a missing actor refuses", q.get("ok") is False, q.get("error"))
    q = M.call("focus_viewport", {"path": "x"})
    check("T842 an unknown parameter (path, belongs elsewhere) is refused",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T843 blueprint_inheritance_tree
    print("")
    print("=== T843: blueprint_inheritance_tree ===")
    r = M.call("blueprint_inheritance_tree", {})
    check("T843 the whole-project tree answers", r.get("ok") is True, json.dumps(r)[:200])
    roots = r.get("roots") or []
    check("T843 blueprintCount is a real number (there is no 'count' field - roots and "
          "blueprintCount count different things)",
          isinstance(r.get("blueprintCount"), (int, float)), r.get("blueprintCount"))
    native_roots = r.get("nativeRoots") or []
    check("T843 reports at least one native root (every blueprint derives from something native "
          "eventually)", len(native_roots) > 0, native_roots[:10])

    # REGRESSION LOCK for a real bug found by this suite and fixed the same session:
    # ChildrenOf was keyed by the FULL native class path ("/Script/Engine.Actor") while
    # nativeRoots advertised the SHORT name ("Actor") a caller was told to pass back in - so
    # every value this endpoint itself recommended for `root` was refused. Rooting at EVERY
    # advertised native root, not just the first, since the fix walks ChildrenOf's keys by
    # short name and a narrower test could pass by luck on one entry while others still break.
    for name in native_roots:
        r2 = M.call("blueprint_inheritance_tree", {"root": name})
        check("T843 rooting at advertised native root %r answers (not the nativeRoots/root "
              "mismatch bug)" % name, r2.get("ok") is True, json.dumps(r2)[:200])
        if r2.get("ok"):
            check("T843 %r actually has root(s) under it" % name, len(r2.get("roots") or []) > 0, r2)

    q = M.call("blueprint_inheritance_tree", {"blueprintId": "x"})
    check("T843 an unknown parameter (blueprintId, belongs elsewhere) is refused",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T844 scene_report
    print("")
    print("=== T844: scene_report ===")
    r = M.call("scene_report", {})
    check("T844 it answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T844 reports actorCount", isinstance(r.get("actorCount"), (int, float)), r.get("actorCount"))
    for key in ("floating", "sunken"):
        check("T844 %s is a real array" % key, isinstance(r.get(key), list), r.get(key))

    q = M.call("scene_report", {"nameContains": "x"})
    check("T844 an unknown parameter (nameContains, belongs to check_overlaps) is refused",
          q.get("ok") is False, q.get("error"))

    # ================================================================== T845 list_mounted_containers
    print("")
    print("=== T845: list_mounted_containers ===")
    r = M.call("list_mounted_containers", {})
    check("T845 it answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T845 reports ioDispatcherInitialized as a real bool",
          isinstance(r.get("ioDispatcherInitialized"), bool), r.get("ioDispatcherInitialized"))
    containers = r.get("containers") or []
    check("T845 containerCount matches its own array",
          r.get("containerCount") == len(containers), (r.get("containerCount"), len(containers)))
    if containers:
        check("T845 every container row has a filesystem path, not a /Game/ package",
              all(c.get("filePath") and not str(c.get("filePath", "")).startswith("/Game/")
                  for c in containers), containers[:2])
    check("T845 reports assetCounts", isinstance(r.get("assetCounts"), dict), r.get("assetCounts"))

    q = M.call("list_mounted_containers", {"path": "x"})
    check("T845 it takes no parameters at all - any key is refused", q.get("ok") is False, q.get("error"))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
