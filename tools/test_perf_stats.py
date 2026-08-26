"""get_perf_stats - a read whose wrong answer misleads instead of corrupting.

The mildest of the four uncovered endpoints, and the one whose failure is hardest to notice: nothing
breaks, a number is just wrong, and a decision gets made on it. QOLCrafting named it for the planned
hideout once it holds many furniture pieces, stations, effects and widgets - which is exactly the
situation where someone reads scene.actors or memory.usedPhysicalMB and concludes something.

THE ASSERTION THIS SUITE REFUSES TO MAKE. `ok: true` proves nothing here. RunEndpoint sets ok true
BEFORE the handler is dispatched (MifBridgeCommon.cpp:1173) and only Fail() flips it. A handler that
returned an empty object would still answer ok:true. So every check below reads an actual field, or a
relationship between fields, and the suite never treats ok as evidence of anything except "we were not
explicitly refused".

TWO PURITY PROPERTIES, both read back through OTHER endpoints. get_perf_stats is in the read-only
bucket, so RunEndpoint skips FScopedTransaction entirely - it must not push an undo entry. And its
census walks actors calling GetMaterial() and GetRenderData(), none of which may dirty a package. Both
are asserted by measuring before and after through list_transactions and list_dirty_packages rather
than by trusting the bucket declaration, because the bucket is a declaration and this is a test.

SAFETY: the read itself is completely clean - no transaction, no modal, no load, no spawn, nothing left
behind. The delta check spawns one actor, and only when the open level is scratch.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def dig(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T530 the parameter contract
    print("=== T530: it takes NO parameters - any key at all fails the whole call ===")
    r = M.call("get_perf_stats", {}, timeout=90)
    check("T530 an empty payload succeeds", r.get("ok") is True, json.dumps(r)[:200])
    for key, val in (("includePlayers", True), ("world", "X"), ("limit", 5)):
        q = M.call("get_perf_stats", {key: val}, timeout=60)
        check("T530 '%s' is refused" % key, q.get("ok") is False, json.dumps(q)[:200])
    # The handler carries two load-bearing notes; the PIE one is the difference between a number that
    # means what you think and one that does not.
    q = M.call("get_perf_stats", {"world": "X"}, timeout=60)
    err = (q.get("error") or "") + json.dumps(q.get("keyNotes") or q.get("notes") or "")
    check("T530 and the refusal explains it measures the EDITOR world",
          "pie" in err.lower() or "editor" in err.lower(), err[:220])

    # ------------------------------------------------------------------ T531 the payload is real
    print("")
    print("=== T531: every advertised field is present and numeric ===")
    r = M.call("get_perf_stats", {}, timeout=90)
    fields = ["editorTiming.lastFrameMs", "editorTiming.impliedFps",
              "rhi.drawCalls", "rhi.primitivesDrawn",
              "memory.usedPhysicalMB", "memory.peakUsedPhysicalMB", "memory.availablePhysicalMB",
              "scene.actors", "scene.primitiveComponents", "scene.staticMeshComponents",
              "scene.skeletalMeshComponents", "scene.lights"]
    for f in fields:
        v = dig(r, f)
        check("T531 %s is a number" % f, isinstance(v, (int, float)), "got %r" % (v,))

    # Relationships that a stubbed or half-wired handler would not satisfy.
    used = dig(r, "memory.usedPhysicalMB")
    peak = dig(r, "memory.peakUsedPhysicalMB")
    if isinstance(used, (int, float)) and isinstance(peak, (int, float)):
        check("T531 peak memory is at least current memory", peak >= used,
              "used=%s peak=%s" % (used, peak))
        # A real editor is hundreds of MB. Zero would mean the stat was never filled in.
        check("T531 and current memory is plausibly non-trivial", used > 50,
              "usedPhysicalMB=%s - suspiciously small for a running editor" % used)
    ms = dig(r, "editorTiming.lastFrameMs")
    fps = dig(r, "editorTiming.impliedFps")
    if isinstance(ms, (int, float)) and isinstance(fps, (int, float)) and ms > 0:
        check("T531 impliedFps is consistent with lastFrameMs", abs(fps - (1000.0 / ms)) < 1.0,
              "lastFrameMs=%s impliedFps=%s (expected ~%.2f)" % (ms, fps, 1000.0 / ms))

    subs = dig(r, "scene.staticMeshComponents")
    prims = dig(r, "scene.primitiveComponents")
    if isinstance(subs, (int, float)) and isinstance(prims, (int, float)):
        check("T531 static meshes are a subset of primitives", subs <= prims,
              "staticMeshComponents=%s primitiveComponents=%s" % (subs, prims))

    # ------------------------------------------------------------------ T532 purity
    print("")
    print("=== T532 [purity]: a read must not transact and must not dirty ===")
    before_tx = M.call("list_transactions", {"limit": 1}, timeout=60)
    before_dirty = M.call("list_dirty_packages", {}, timeout=60)
    for _ in range(3):
        M.call("get_perf_stats", {}, timeout=90)
    after_tx = M.call("list_transactions", {"limit": 1}, timeout=60)
    after_dirty = M.call("list_dirty_packages", {}, timeout=60)

    check("T532 three reads pushed no undo entry",
          before_tx.get("nextUndoTitle") == after_tx.get("nextUndoTitle")
          and before_tx.get("queueLength") == after_tx.get("queueLength"),
          "undo head %r -> %r, queue %s -> %s" % (before_tx.get("nextUndoTitle"),
                                                  after_tx.get("nextUndoTitle"),
                                                  before_tx.get("queueLength"),
                                                  after_tx.get("queueLength")))
    nb = len(before_dirty.get("packages") or [])
    na = len(after_dirty.get("packages") or [])
    # The census calls GetMaterial() and GetRenderData() on every mesh it walks; either could Modify()
    # something if the handler were written carelessly.
    check("T532 and dirtied nothing", nb == na, "dirty packages %d -> %d" % (nb, na))

    # ------------------------------------------------------------------ T533 the numbers MOVE
    print("")
    print("=== T533: scene counts track reality, rather than being decorative ===")
    world = (M.call("list_level_actors", {"limit": 1}, timeout=60).get("world") or "")
    if not (world.startswith("Untitled") or world.startswith("_Mif")):
        check("T533 (not exercised: the open level %r is not scratch)" % world, True)
    else:
        base = dig(M.call("get_perf_stats", {}, timeout=90), "scene.actors")
        sp = M.call("spawn_actor_in_level", {"actorClass": "StaticMeshActor",
                                             "location": {"x": 0, "y": 0, "z": 500},
                                             "label": "MifPerf_%d" % st}, timeout=90)
        if sp.get("ok"):
            after = dig(M.call("get_perf_stats", {}, timeout=90), "scene.actors")
            # A count that never changes would look perfectly plausible in a response and mean nothing.
            check("T533 spawning an actor increases scene.actors", after == base + 1,
                  "scene.actors %s -> %s after one spawn" % (base, after))
            # NESTED, not top level. spawn_actor_in_level answers
            #   {ok, labelRequested, labelActual, actor: {actorPath, name, label, class, ...}}
            # A lookup of sp["actorPath"] returns None and skips the cleanup SILENTLY, which is how
            # this branch quietly did nothing the first time it ran.
            path = (sp.get("actor") or {}).get("actorPath")
            check("T533 the spawn reports the actor path (nested under .actor)", bool(path),
                  json.dumps(sp)[:200])
            # CLEANUP IS NOT POSSIBLE HERE, and that is stated rather than skipped. Deleting a level
            # actor needs confirm=true, and scratch_confirm only grants confirm when every path in the
            # payload is under /Game/_Mif. A level actor lives at /Temp/<Level>.<Level>:PersistentLevel
            # ..., which is not scratch by that rule, so the guard refuses - correctly, since it cannot
            # tell this actor from one in a real level. The actor is left behind. That is acceptable
            # ONLY because this branch runs solely in an untitled scratch level that is never saved.
            if path:
                d = M.call("delete_level_actor", {"actorPath": path}, timeout=60)
                print("      . cleanup: %s (%s)" % (d.get("ok"), str(d.get("error"))[:90]))
                back = dig(M.call("get_perf_stats", {}, timeout=90), "scene.actors")
                if d.get("ok"):
                    check("T533 and deleting it brings the count back", back == base,
                          "scene.actors %s -> %s after delete" % (after, back))
                else:
                    print("      . the spawned actor could NOT be removed, so scene.actors stays at"
                          " %s - see issue J in docs/06_OPEN_ISSUES_FROM_USE.md" % back)
        else:
            check("T533 (not exercised: spawn refused: %s)" % json.dumps(sp)[:140], True)

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
