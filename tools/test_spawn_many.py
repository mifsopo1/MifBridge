"""spawn_many - bulk actor placement, and the two ways it used to lose work quietly.

Named in no suite until now, which is how both bugs below survived. QOLCrafting named it for dressing a
planned hideout and Junkyard, so it is on a real consumer's path.

THE TWO FIXES THIS LOCKS IN.

1. AN UNLOADABLE MESH WAS SWALLOWED TWICE. The load is LOAD_NoWarn|LOAD_Quiet, which kills the engine's
   own log line, and the assignment is guarded by `if (Mesh && ...)`. So a misspelled mesh path produced
   N actors with NO mesh and a response cheerfully reporting spawned:N. For someone placing props that
   is the entire job silently not done, and it looks like success. It now refuses before the spawn loop.

2. mesh/material WERE ACCEPTED ON EVERY PATH AND APPLIED ON ONE. They are only ever assigned inside a
   `Cast<AStaticMeshActor>`, so asking for a mesh while spawning any other class was accepted and
   dropped. Now reported per item, rather than failing the whole call - the actor itself spawned fine
   and the caller may simply have passed a shared default that does not apply to this row.

3. [T546-T547, added 2026-08-29] TWO PER-ITEM HALVES OF THE SAME TODO, closed together. The top-level
   RejectUnknownParams guard (Batch D.1) only ever covered TOP-LEVEL keys - a typo'd key INSIDE one
   items[] entry (e.g. "rot" instead of "rotation", "meshPath" instead of "mesh") was silently
   ignored, and a non-object entry in items[] was counted in `failed` with nothing in errors[]
   explaining why - indistinguishable from a spawn that failed for some unrelated engine reason. Both
   are now refused by name, per item, without failing the rest of the batch.

TWO THINGS THIS SUITE IS CAREFUL ABOUT.

  * THE GLOBAL UNDO STACK. spawn_many is in the transacted bucket, so RunEndpoint wraps every call in
    FScopedTransaction on the editor's single shared undo buffer. This suite READS list_transactions and
    never calls undo_transactions - an undo here would pop whatever another suite just pushed, which is
    exactly the cross-contamination that produced five phantom failures earlier in this project.
  * IT LEAVES ACTORS BEHIND, and cannot help it. Cleanup needs confirm=true and a level actor's
    /Temp/<Level> path is not scratch, so the guard refuses (issue J). Acceptable only because the
    harness runs in an untitled level that is never saved. The suite refuses to run the spawning half
    at all if the open level is not scratch.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def sm_count():
    """How many StaticMeshActors the level holds. `matched`, not `count` - count is capped by limit."""
    r = M.call("list_level_actors", {"classFilter": "StaticMeshActor", "limit": 5000}, timeout=90)
    v = r.get("matched")
    return v if isinstance(v, (int, float)) else len(r.get("actors") or [])


def undo_head():
    return (M.call("list_transactions", {"limit": 1}, timeout=60) or {}).get("nextUndoTitle") or ""


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    world = (M.call("list_level_actors", {"limit": 1}, timeout=60).get("world") or "")
    if not (world.startswith("Untitled") or world.startswith("_Mif")):
        print("the open level %r is not a scratch level. spawn_many places actors in whatever level is"
              % world)
        print("open and they cannot be cleaned up (issue J), so this suite refuses to run.")
        return 0

    # ------------------------------------------------------------------ T540 the parameter contract
    print("=== T540: the parameter contract, checked before anything is placed ===")
    q = M.call("spawn_many", {"count": 3}, timeout=60)
    check("T540 'count' is refused rather than silently ignored", q.get("ok") is False,
          json.dumps(q)[:200])
    q = M.call("spawn_many", {"actors": [{"x": 0}]}, timeout=60)
    check("T540 'actors' is refused (it is an OUTPUT field, not an input)", q.get("ok") is False,
          json.dumps(q)[:200])
    q = M.call("spawn_many", {}, timeout=60)
    check("T540 a call with no items is refused", q.get("ok") is False, json.dumps(q)[:200])

    # ------------------------------------------------------------------ T541 the unloadable mesh
    print("")
    print("=== T541 [fix 1]: an unloadable mesh refuses the call instead of placing meshless actors ===")
    before = sm_count()
    head_before = undo_head()
    items = [{"x": 0, "y": 0, "z": 900}, {"x": 200, "y": 0, "z": 900}, {"x": 400, "y": 0, "z": 900}]
    r = M.call("spawn_many", {"items": items, "labelPrefix": "MifBad_%d" % st,
                              "mesh": "/Game/_MifScratch/SM_DoesNotExist.SM_DoesNotExist"}, timeout=120)
    check("T541 the call is refused", r.get("ok") is False, json.dumps(r)[:240])
    check("T541 and the error names the mesh path",
          "SM_DoesNotExist" in (r.get("error") or ""), (r.get("error") or "")[:220])
    check("T541 and says nothing was spawned",
          "othing was spawned" in (r.get("error") or ""), (r.get("error") or "")[:220])
    # Read the LEVEL back, not the response's own claim. This is the assertion the fix exists for.
    after = sm_count()
    check("T541 and the level really gained no actors", after == before,
          "StaticMeshActor count %s -> %s across a refused call" % (before, after))
    # RunEndpoint cancels the transaction when a handler fails, so no entry should be left behind.
    check("T541 and no undo entry was left behind", undo_head() == head_before,
          "undo head %r -> %r" % (head_before, undo_head()))

    print("")
    print("=== T541 [fix 1, material arm]: same for an unloadable material ===")
    before = sm_count()
    r = M.call("spawn_many", {"items": items[:1], "labelPrefix": "MifBadM_%d" % st,
                              "mesh": "/Engine/BasicShapes/Cube.Cube",
                              "material": "/Game/_MifScratch/M_DoesNotExist.M_DoesNotExist"},
               timeout=120)
    check("T541 an unloadable material is refused", r.get("ok") is False, json.dumps(r)[:240])
    check("T541 and the error names the material path",
          "M_DoesNotExist" in (r.get("error") or ""), (r.get("error") or "")[:220])
    check("T541 and the level is unchanged", sm_count() == before,
          "count moved across a refused call")

    # ------------------------------------------------------------------ T542 no false positive
    print("")
    print("=== T542: a VALID mesh must still work, or the guard was written too tightly ===")
    before = sm_count()
    r = M.call("spawn_many", {"items": items, "labelPrefix": "MifOk_%d" % st,
                              "mesh": "/Engine/BasicShapes/Cube.Cube"}, timeout=120)
    check("T542 three actors spawn", r.get("ok") is True and r.get("spawned") == 3,
          json.dumps(r)[:240])
    check("T542 and none failed", r.get("failed") in (0, None), json.dumps(r)[:200])
    # errors is documented as emitted ONLY when non-empty; a clean call must not carry the key.
    check("T542 and the errors key is ABSENT on a clean call", "errors" not in r,
          json.dumps(r)[:240])
    check("T542 the level really gained three", sm_count() == before + 3,
          "count %s -> %s" % (before, sm_count()))
    rows = r.get("actors") or []
    check("T542 and one row per spawned actor", len(rows) == 3, json.dumps(rows)[:200])
    check("T542 each row carries a usable actorPath",
          all(str(x.get("actorPath", "")).startswith("/") for x in rows), json.dumps(rows)[:240])

    # ------------------------------------------------------------------ T543 the silent ignore
    print("")
    print("=== T543 [fix 2]: mesh on a non-StaticMeshActor is REPORTED, not dropped ===")
    r = M.call("spawn_many", {"items": [{"x": 600, "y": 0, "z": 900, "label": "MifBare_%d" % st}],
                              "actorClass": "Actor",
                              "mesh": "/Engine/BasicShapes/Cube.Cube"}, timeout=120)
    check("T543 the actor still spawns", r.get("ok") is True and r.get("spawned") == 1,
          json.dumps(r)[:240])
    errs = json.dumps(r.get("errors") or [])
    check("T543 and an error row explains the mesh was ignored", "IGNORED" in errs, errs[:260])
    check("T543 naming the class that cannot hold one", "Actor" in errs, errs[:260])
    check("T543 and naming the actor", ("MifBare_%d" % st) in errs, errs[:260])

    # ------------------------------------------------------------------ T544 per-item validation
    print("")
    print("=== T544: a bad item is named by index and does not take the batch with it ===")
    r = M.call("spawn_many", {"items": [{"x": 0, "y": 0, "z": 900},
                                        {"x": "not-a-number", "y": 0, "z": 900}],
                              "labelPrefix": "MifIdx_%d" % st}, timeout=120)
    if r.get("ok"):
        errs = json.dumps(r.get("errors") or [])
        check("T544 the bad item is reported by index", "items[1]" in errs, errs[:240])
        check("T544 and the good one still spawned", r.get("spawned") == 1, json.dumps(r)[:220])
    else:
        check("T544 a bad item is refused with the index named",
              "items[1]" in (r.get("error") or ""), (r.get("error") or "")[:220])

    # ------------------------------------------------------------------ T545 labelNotes accumulate
    print("")
    print("=== T545 [issue K]: a per-item note must not overwrite the previous one ===")
    # FINDING A TRIGGER IS THE HARD PART, and it is worth writing down. SetActorLabelChecked emits a
    # note only when the label the actor ENDS UP with differs from the trimmed request. UE permits
    # duplicate actor labels, and it accepts newlines, tabs, control characters and 300-character
    # names unchanged - all of those were tried and produced no note at all. A WHITESPACE-ONLY label
    # is the case the editor actually refuses, leaving the actor called "StaticMeshActor".
    r = M.call("spawn_many", {"items": [{"x": 0, "y": 300, "z": 1400, "label": "   "},
                                        {"x": 200, "y": 300, "z": 1400, "label": "  "},
                                        {"x": 400, "y": 300, "z": 1400, "label": "    "}],
                              "mesh": "/Engine/BasicShapes/Cube.Cube"}, timeout=120)
    check("T545 all three still spawn", r.get("ok") is True and r.get("spawned") == 3,
          json.dumps(r)[:220])
    notes = r.get("labelNotes") or []
    # THE assertion. This was a single-valued top-level field written from inside the loop, so three
    # refused labels reported one note - the last - and the caller read a single oddity where there
    # was a pattern.
    check("T545 one note per refused label, not just the last", len(notes) == 3,
          "labelNotes has %d entries for 3 refused labels: %s" % (len(notes), json.dumps(notes)[:220]))
    # bool(notes) first: all() over an empty range is vacuously True, which would print a
    # misleading PASS right next to T545's own len(notes)==3 FAIL if labelNotes ever came back
    # empty - the exact trap audit_vacuous_checks.py exists to catch (found live, 2026-08-29).
    check("T545 and each note carries its item index",
          bool(notes) and all(("items[%d]" % i) in str(notes[i]) for i in range(min(3, len(notes)))),
          json.dumps(notes)[:260])
    # The old single-valued field must be gone, or callers keep reading the one that lied.
    check("T545 the old single-valued labelNote field is gone", "labelNote" not in r,
          json.dumps(list(r.keys()))[:200])

    # ------------------------------------------------------------------ T546-T547 per-item guard
    print("")
    print("=== T546: an unrecognised key INSIDE one item is refused by name, not silently ignored ===")
    before = sm_count()
    r = M.call("spawn_many", {"items": [{"x": 0, "y": 0, "z": 900},
                                        {"x": 200, "y": 0, "z": 900, "rot": 90}],
                              "labelPrefix": "MifBadKey_%d" % st}, timeout=120)
    check("T546 the call still succeeds overall - one bad item does not take the batch with it",
          r.get("ok") is True, json.dumps(r)[:220])
    check("T546 only the good item spawned", r.get("spawned") == 1, json.dumps(r)[:220])
    check("T546 the bad one is counted in failed", r.get("failed") == 1, json.dumps(r)[:220])
    errs = json.dumps(r.get("errors") or [])
    check("T546 the error names the item index", "items[1]" in errs, errs[:260])
    check("T546 and names the actual bad key - not a generic message", "rot" in errs, errs[:260])
    check("T546 the level really gained only one actor", sm_count() == before + 1,
          "count %s -> %s" % (before, sm_count()))

    print("")
    print("=== T547: a non-object item explains itself in errors[], not just a bare failed count ===")
    before = sm_count()
    r = M.call("spawn_many", {"items": [{"x": 0, "y": 100, "z": 900}, "not-an-object", 12345],
                              "labelPrefix": "MifNonObj_%d" % st}, timeout=120)
    check("T547 the call still succeeds overall", r.get("ok") is True, json.dumps(r)[:220])
    check("T547 only the real item spawned", r.get("spawned") == 1, json.dumps(r)[:220])
    check("T547 both non-object entries are counted in failed", r.get("failed") == 2, json.dumps(r)[:220])
    errs547 = r.get("errors") or []
    check("T547 errors[] carries an explanation for EACH non-object entry, not zero",
          len(errs547) >= 2, json.dumps(errs547)[:260])
    check("T547 the explanations name the item indices",
          "items[1]" in json.dumps(errs547) and "items[2]" in json.dumps(errs547),
          json.dumps(errs547)[:260])
    check("T547 the level really gained only one actor", sm_count() == before + 1,
          "count %s -> %s" % (before, sm_count()))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("actors placed by this suite are left in the level - see issue J. Harmless in an untitled")
    print("scratch level that is never saved.")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
