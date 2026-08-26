"""list_transactions and redo_transactions - the other half of the undo story.

test_undo_integrity asks whether UNDO puts back what an endpoint changed, across nine endpoints. It
never asks whether REDO brings the change back again, and a modder who presses Ctrl+Z once too often
depends on exactly that. Undo that works and redo that quietly does nothing is a worse trap than
neither working, because the first one teaches you to trust the pair.

So the shape here is the round trip, asserted from the BLUEPRINT rather than from what the transaction
endpoints say about themselves:

    add a variable  -> it is there
    undo            -> it is gone
    redo            -> it is back

and the redo must NAME what it redid, because "redone: 1" with no title cannot be checked against what
you expected to come back.

list_transactions is the reader. Its numbers are only worth having if they MOVE: a currentIndex that
never changes and an undoCount that stays at zero would look perfectly plausible in a response and
tell you nothing, so both are asserted across an undo rather than merely being present.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    bid = M.call("create_blueprint", {"path": "/Game/_MifTx/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1

    var = "Redoable_%d" % st

    def has_var():
        return var in [v.get("name") for v in
                       (M.call("list_variables", {"blueprintId": bid}).get("variables") or [])]

    # ------------------------------------------------------------------ T470 the reader
    print("")
    print("=== T470: list_transactions reports numbers that actually move ===")
    lt = M.call("list_transactions", {})
    check("T470 it answers", lt.get("ok") is True, json.dumps(lt)[:200])
    check("T470 and reports where the undo cursor is",
          isinstance(lt.get("currentIndex"), (int, float)), json.dumps(lt)[:200])
    check("T470 and how long the queue is",
          isinstance(lt.get("queueLength"), (int, float)), json.dumps(lt)[:200])
    check("T470 and it can be paged", M.call("list_transactions", {"limit": 3}).get("ok") is True,
          "limit is an accepted parameter and must not break the call")

    # ------------------------------------------------------------------ T471 the round trip
    print("")
    print("=== T471 [the point]: add, undo, redo - and the change comes back ===")
    a = M.call("add_variable", {"blueprintId": bid, "name": var, "type": "float"})
    check("T471 the variable is added", a.get("ok") is True and has_var(), json.dumps(a)[:180])
    before = M.call("list_transactions", {})

    u = M.call("undo_transactions", {"count": 1})
    check("T471 the undo succeeds", u.get("ok") is True, json.dumps(u)[:200])
    check("T471 and reports how many steps it took", u.get("undone") == 1, json.dumps(u)[:200])
    # Asserted from the blueprint, not from the undo's own count.
    check("T471 the variable is really gone", not has_var(),
          "undo reported undone=%s but the variable is still there" % u.get("undone"))

    during = M.call("list_transactions", {})
    # A cursor that never moves would make every number in this response decorative.
    check("T471 and the undo cursor moved",
          during.get("undoCount") != before.get("undoCount")
          or during.get("currentIndex") != before.get("currentIndex"),
          "currentIndex %s -> %s, undoCount %s -> %s: nothing moved across an undo that worked"
          % (before.get("currentIndex"), during.get("currentIndex"),
             before.get("undoCount"), during.get("undoCount")))

    r = M.call("redo_transactions", {"count": 1})
    check("T471 the redo succeeds", r.get("ok") is True, json.dumps(r)[:220])
    check("T471 and reports how many it redid", r.get("redone") == 1, json.dumps(r)[:200])
    # THE assertion of the whole file.
    check("T471 the variable is BACK", has_var(),
          "redo reported redone=%s but the variable did not return - undo that works with redo that "
          "does not is worse than neither working" % r.get("redone"))
    # "redone: 1" with no title cannot be checked against what you expected.
    titles = r.get("titlesRedone") or []
    check("T471 and it names what it brought back", bool(titles), json.dumps(r)[:220])
    check("T471 with a title that matches the operation",
          any("add_variable" in str(t) for t in titles), json.dumps(titles)[:200])

    # ------------------------------------------------------------------ T472 nothing to redo
    print("")
    print("=== T472: redo with nothing left to redo is an answer, not an error ===")
    q = M.call("redo_transactions", {"count": 1})
    check("T472 it answers rather than failing", isinstance(q.get("ok"), bool), json.dumps(q)[:200])
    if q.get("ok"):
        check("T472 and reports that it redid nothing, or says it stopped early",
              (q.get("redone") in (0, None)) or q.get("stoppedEarly") is True,
              "redone=%s stoppedEarly=%s - claiming a redo happened when the stack was empty would be "
              "a false success" % (q.get("redone"), q.get("stoppedEarly")))
    check("T472 and the variable was not disturbed by the attempt", has_var(),
          "an empty redo changed the blueprint")

    # ------------------------------------------------------------------ T473 guards
    print("")
    print("=== T473: out-of-range requests are refused ===")
    q = M.call("undo_transactions", {"count": 0})
    check("T473 count:0 is refused", q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("undo_transactions", {"count": 9999})
    check("T473 an absurd count is refused rather than walking the whole history",
          q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("undo_transactions", {"count": 1, "toIndex": 3})
    check("T473 count and toIndex together are refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T473 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
