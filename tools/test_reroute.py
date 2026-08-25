"""add_reroute, and the knot handling it finally makes testable.

Reroute nodes were readable but not writable: list_nodes has hideKnots, SerializePin resolves through
knot chains, SkipKnots tunnels them - and nothing in the surface could create one, so none of it had
ever run against a real knot. There is no paste/import endpoint either, so there was no other way to
conjure one.

T50-T52 cover the endpoint. T53-T55 are the point: they finally exercise the knot code.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def links(guid, pin):
    r = M.call("get_node", {"nodeGuid": guid})
    nd = r.get("node") if r.get("ok") else None
    if not nd:
        return None
    for p in nd.get("pins", []):
        if p.get("name", "").lower() == pin.lower():
            return sorted((l.get("node"), l.get("pin")) for l in (p.get("linkedTo") or []))
    return None


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    stamp = int(time.time() % 100000)
    root = "/Game/_MifReroute/BP_RR_%d" % stamp
    bp = M.call("create_blueprint", {"path": root, "parentClass": "Actor"})
    bpid, graph = bp.get("blueprintId"), bp.get("eventGraphId")
    if not graph:
        print("setup failed:", json.dumps(bp)[:300])
        return 3

    ev = None
    for nd in M.call("list_nodes", {"graphId": graph}).get("nodes", []):
        if "BeginPlay" in (nd.get("title") or ""):
            ev = nd.get("guid")
    p1 = M.call("add_function_call", {"graphId": graph, "function": "PrintString",
                                      "class": "KismetSystemLibrary", "x": 800, "y": 0}).get("nodeGuid")
    if not (ev and p1):
        print("could not build the exec chain")
        return 3
    M.call("connect_pins", {"srcNode": ev, "srcPin": "then", "dstNode": p1, "dstPin": "execute"})

    # ------------------------------------------------------------------ T50 bare reroute
    print("\n=== T50: a bare reroute can be created ===")
    r = M.call("add_reroute", {"graphId": graph, "x": 400, "y": 400})
    bare = r.get("nodeGuid")
    print("  ", json.dumps({k: v for k, v in r.items() if k != "node"})[:220])
    check("T50 created", r.get("ok") is True, json.dumps(r)[:220])
    check("T50 reports its pin names",
          r.get("inputPin") == "InputPin" and r.get("outputPin") == "OutputPin",
          "in=%s out=%s" % (r.get("inputPin"), r.get("outputPin")))

    # ------------------------------------------------------------------ T51 guards
    print("\n=== T51: the splice guards refuse before creating anything ===")
    before = len(M.call("list_nodes", {"graphId": graph}).get("nodes", []))
    r = M.call("add_reroute", {"graphId": graph, "srcNode": ev, "srcPin": "then", "x": 0, "y": 0})
    check("T51 half a splice is refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T51 says nothing was created", "Nothing was created" in (r.get("error") or ""),
          (r.get("error") or "")[:180])

    r = M.call("add_reroute", {"graphId": graph, "srcNode": ev, "srcPin": "then",
                               "dstNode": p1, "dstPin": "InString", "x": 0, "y": 0})
    check("T51 unconnected pins refused", r.get("ok") is False, json.dumps(r)[:200])
    check("T51 explains there is no wire", "not connected" in (r.get("error") or ""),
          (r.get("error") or "")[:180])
    after = len(M.call("list_nodes", {"graphId": graph}).get("nodes", []))
    check("T51 no nodes left behind by the refusals", after == before,
          "before=%d after=%d" % (before, after))

    # ------------------------------------------------------------------ T52 splice
    print("\n=== T52: splicing into a live wire rewires through the reroute ===")
    r = M.call("add_reroute", {"graphId": graph, "srcNode": ev, "srcPin": "then",
                               "dstNode": p1, "dstPin": "execute", "x": 400, "y": 0})
    knot = r.get("nodeGuid")
    print("  ", json.dumps({k: v for k, v in r.items() if k != "node"})[:240])
    check("T52 spliced", r.get("ok") is True and r.get("splicedInto") is True, json.dumps(r)[:240])
    ev_then = links(ev, "then")
    check("T52 the event now feeds the reroute", ev_then == [(knot, "InputPin")], str(ev_then))
    knot_out = links(knot, "OutputPin")
    check("T52 the reroute feeds the old target", knot_out == [(p1, "execute")], str(knot_out))

    # ------------------------------------------------------------------ T53 knot chain
    print("\n=== T53 [the point]: a two-knot chain, and hideKnots ===")
    r2 = M.call("add_reroute", {"graphId": graph, "srcNode": knot, "srcPin": "OutputPin",
                                "dstNode": p1, "dstPin": "execute", "x": 600, "y": 0})
    knot2 = r2.get("nodeGuid")
    check("T53 second reroute spliced", r2.get("ok") is True and r2.get("splicedInto") is True,
          json.dumps(r2)[:220])

    shown = M.call("list_nodes", {"graphId": graph})
    hidden = M.call("list_nodes", {"graphId": graph, "hideKnots": True})
    n_shown = len([n for n in shown.get("nodes", []) if "Knot" in (n.get("class") or "")])
    n_hidden = len([n for n in hidden.get("nodes", []) if "Knot" in (n.get("class") or "")])
    print("  knots listed: default=%d  hideKnots=%d" % (n_shown, n_hidden))
    # THREE: the bare reroute from T50 plus the two spliced into the exec chain. The first version
    # of this assertion said two and counted only the chain, which made a correct endpoint look wrong.
    check("T53 knots exist and are listed by default", n_shown == 3, str(n_shown))
    check("T53 hideKnots removes them from the listing", n_hidden == 0, str(n_hidden))
    check("T53 the response says it hid them",
          hidden.get("knotsHidden") is not None or "knotNote" in hidden,
          json.dumps({k: v for k, v in hidden.items() if k != "nodes"})[:220])

    # ------------------------------------------------------------------ T54 resolve through
    print("\n=== T54: with knots hidden, the wire is reported end to end ===")
    ev_row = None
    for n in hidden.get("nodes", []):
        if n.get("guid") == ev:
            ev_row = n
    if ev_row:
        then = [p for p in ev_row.get("pins", []) if p.get("name") == "then"]
        tgt = sorted((l.get("node"), l.get("pin")) for l in (then[0].get("linkedTo") or [])) if then else []
        print("  BeginPlay.then with hideKnots ->", tgt)
        check("T54 resolves through BOTH knots to the real target", tgt == [(p1, "execute")],
              "expected the print node, got %s" % (tgt,))
    else:
        check("T54 resolves through BOTH knots to the real target", False,
              "the event node was not in the hideKnots listing at all")

    # ------------------------------------------------------------------ T55 still compiles
    print("\n=== T55: a graph wired through reroutes compiles ===")
    c = M.call("compile", {"blueprintId": bpid})
    check("T55 compiles clean", c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s %s" % (c.get("numErrors"), json.dumps(c.get("messages", []))[:300]))

    M.call("delete_asset", {"path": root})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
