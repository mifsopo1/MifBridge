"""The recipe endpoints - compositions, and what they leave behind when they fail half way.

A recipe spawns SEVERAL nodes and wires them. That makes the interesting question not "did it work"
but "what is in the graph if it didn't", because a composition that half-applies leaves the caller
with a graph they never asked for and did not see. PM-007 is why this cannot be assumed away:
FTransaction::Cancel discards the undo ENTRY without applying anything, so a failed handler does NOT
get a free rollback, and a self-managed endpoint's transaction has usually committed already.

WHAT THIS SUITE ASSERTS, and it is deliberately not "nothing is left behind".

recipe_add_debug_print, given an afterNode that does not exist, DOES leave the created Print node in
the graph - and says so, in the error, in as many words: "WHAT IS LEFT BEHIND: the Print String node
HAS been created in the graph, unwired, and is not removed by this failure ... Remove it with
remove_node." That is a deliberate choice (the handler calls it Batch M option (c)) and it is the
honest one: the alternative shape - report ok:true with a warning and a floating node - is the silent
failure, because the node exists so a later list_nodes check passes while the print never runs.

So the contract worth protecting is not "leaves nothing" but "TELLS YOU what it left". A future change
that quietly stops saying so would turn a declared consequence into a silent one, and nothing else
would catch it.

(Worth recording how this suite came to exist: a probe read that same error truncated to 220
characters, saw a node count go up after an ok:false, and called it a silent failure. The endpoint had
been saying exactly what happened all along, past the truncation. Hence T421 asserts the SENTENCE, and
hence this suite prints errors in full.)
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
    st = int(time.time() % 100000)

    bid = M.call("create_blueprint", {"path": "/Game/_MifRec/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    g = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    check("its event graph resolves", bool(g), str([x.get("name") for x in graphs]))
    if not g:
        return 1

    def nodes():
        return len(M.call("list_nodes", {"graphId": g}).get("nodes") or [])

    # ------------------------------------------------------------------ T420 the happy path
    print("")
    print("=== T420: the composition actually composes ===")
    before = nodes()
    r = M.call("recipe_add_debug_print", {"graphId": g, "message": "probe_%d" % st, "x": 0, "y": 400})
    check("T420 the recipe succeeds", r.get("ok") is True, json.dumps(r)[:250])
    check("T420 and a node really appeared", nodes() > before, "%d -> %d" % (before, nodes()))
    check("T420 it names the function it called or created", bool(r.get("functionName")),
          json.dumps(r)[:200])
    # A recipe that leaves the blueprint uncompilable has not helped anyone.
    c = M.call("compile", {"blueprintId": bid})
    check("T420 and the blueprint still compiles",
          c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    # ------------------------------------------------------------------ T421 the declared leftover
    print("")
    print("=== T421 [the point]: a half-applied recipe must SAY what it left behind ===")
    before = nodes()
    q = M.call("recipe_add_debug_print",
               {"graphId": g, "message": "orphan_%d" % st,
                "afterNode": "00000000-0000-0000-0000-000000000000", "x": 0, "y": 800})
    after = nodes()
    err = q.get("error") or ""
    print("      full error: %s" % err)
    check("T421 the call fails", q.get("ok") is False, json.dumps(q)[:200])

    # The node IS left behind, deliberately. What must never regress is the disclosure.
    left = after > before
    check("T421 and the error states what was left behind" if left
          else "T421 nothing was left behind (and nothing to disclose)",
          ("LEFT BEHIND" in err.upper()) if left else True,
          "a node was created (%d -> %d) and the error does not say so - that turns a declared "
          "consequence into a silent one" % (before, after))
    if left:
        # remove_node, not delete_node. This assertion pinned the WRONG name for as long as the
        # message carried it: delete_node has never been an endpoint, so the advice sent the caller
        # to "not an endpoint on this build" and this test held that in place. Found by
        # tools/audit_message_endpoints.py, which checks every endpoint named in user-facing text.
        check("T421 and tells the caller how to clean it up, by a name that EXISTS",
              "remove_node" in err, err[:220])
        check("T421 and explains why it is not rolled back",
              "PM-007" in err or "transaction" in err.lower(), err[:220])

    # ------------------------------------------------------------------ T422 guards
    print("")
    print("=== T422: bad arguments are refused ===")
    q = M.call("recipe_add_debug_print", {"graphId": "no::such::graph_zz", "message": "x"})
    check("T422 an unknown graph is refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T422 and says something usable", len(q.get("error") or "") > 15, (q.get("error") or "")[:150])

    # blueprintId instead of graphId is the mistake the handler's own alias note anticipates.
    q = M.call("recipe_add_debug_print", {"blueprintId": bid, "message": "x"})
    check("T422 blueprintId instead of graphId is refused with a hint",
          q.get("ok") is False and "graphId" in (q.get("error") or ""), (q.get("error") or "")[:200])

    # ------------------------------------------------------------------ T423 the other recipes answer
    print("")
    print("=== T423: every recipe answers rather than hanging ===")
    # recipe_reset_and_loop was once reported as a hang and proven to be a busy editor rather than a
    # defect. Cheap to keep checking, since a hang here costs the whole bridge.
    for ep, payload in (
        ("recipe_reset_and_loop", {"graphId": g, "arrayVar": "NoSuchArray_zz"}),
        ("recipe_argmax_over_components", {"graphId": g}),
        ("recipe_splice_before_parent", {"graphId": g}),
    ):
        try:
            r = M.call(ep, payload, timeout=60)
            check("T423 %s came back at all" % ep, isinstance(r.get("ok"), bool), json.dumps(r)[:150])
        except M.Timeout:
            check("T423 %s came back at all" % ep, False,
                  "no response in 60s - that is the modal/blocking hang, and it takes the bridge with it")
    check("T423 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    SC.confirm_call("delete_asset", {"path": "/Game/_MifRec/BP_%d" % st})
    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
