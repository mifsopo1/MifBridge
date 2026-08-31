"""project_dependency_graph and project_asset_distribution - the brainmap data.

Andre asked for the competitor's Project Dashboard equivalent (dependency graph, complexity heatmap,
asset distribution, inheritance tree). These are the first two data endpoints; the graph WIDGET is a
separate, much larger piece of Slate work and is on the backlog. The data is worth having on its own -
it answers over MCP with no widget at all, and a graph widget with no data source is unverifiable.

WHAT THIS SUITE IS REALLY GUARDING: THE BOUNDS, AND THAT THEY ARE REPORTED.

GetReferencers runs PER ASSET. DDS2 has 32,265 assets. An unbounded dependency graph is not a slow
call - it is a stopped game thread, and a handler that blocks the game thread takes the entire bridge
offline for its duration. A caller can retry an error; it cannot cancel a stall.

So three properties matter more than the payload:

  T641 the pathPrefix guard REFUSES a mount root rather than trying and hanging;
  T642 a capped result reports `truncated` AND `matched` - silent truncation reading as "I covered
       everything" is the single most repeated defect in this project's history;
  T643 the cheap endpoint stays cheap - project_asset_distribution never touches referencers, which is
       precisely why it is allowed to accept a bare /Game where the graph endpoint is not.

SAFETY: read-only. Pure Asset Registry queries; nothing is loaded, created or modified.
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

    print("=== T640: both are registered ===")
    eps = M.endpoint_names()
    for e in ("project_dependency_graph", "project_asset_distribution"):
        check("T640 %s is registered" % e, e in eps, "%d endpoints" % len(eps))

    # ------------------------------------------------------------------ T641 the guard
    print("")
    print("=== T641 [the bound that matters]: a mount root is REFUSED, not attempted ===")
    t0 = time.time()
    r = M.call("project_dependency_graph", {"pathPrefix": "/Game"}, timeout=180)
    elapsed = time.time() - t0
    check("T641 /Game is refused", r.get("ok") is False, json.dumps(r)[:200])
    # It must refuse FAST. A guard that refuses after doing the work is not a guard.
    check("T641 and refused quickly rather than after walking the project", elapsed < 20.0,
          "took %.1fs - if this is slow, the guard is running AFTER the expensive part" % elapsed)
    check("T641 and the refusal says why and what to do",
          "two segments" in (r.get("error") or "") and "per asset" in (r.get("error") or ""),
          (r.get("error") or "")[:200])
    # It should also point at the cheap alternative rather than just saying no.
    check("T641 and names the cheap endpoint as the alternative",
          "project_asset_distribution" in (r.get("error") or ""), (r.get("error") or "")[:220])

    for bad in ("/Game/", "/"):
        q = M.call("project_dependency_graph", {"pathPrefix": bad}, timeout=120)
        check("T641 '%s' is refused too" % bad, q.get("ok") is False, json.dumps(q)[:170])

    # ------------------------------------------------------------------ T642 truncation honesty
    print("")
    print("=== T642 [truncation honesty]: a capped graph must not read as a complete one ===")
    g = M.call("project_dependency_graph",
               {"pathPrefix": "/Game/Blueprints", "maxNodes": 10}, timeout=600)
    check("T642 a real prefix succeeds", g.get("ok") is True, json.dumps(g)[:200])
    if g.get("ok"):
        nodes = g.get("nodes") or []
        check("T642 nodeCount agrees with the array", g.get("nodeCount") == len(nodes),
              "nodeCount=%s but %d nodes" % (g.get("nodeCount"), len(nodes)))
        check("T642 the cap was honoured", len(nodes) <= 10, "%d nodes for maxNodes=10" % len(nodes))
        # THE assertion. matched is the real total; without it a caller cannot tell 10-of-10 from
        # 10-of-1181.
        check("T642 matched reports the REAL total, not the capped one",
              isinstance(g.get("matched"), (int, float)) and g.get("matched") >= len(nodes),
              json.dumps({k: g.get(k) for k in ("nodeCount", "matched", "truncated")}))
        if (g.get("matched") or 0) > len(nodes):
            check("T642 truncated is true when it truncated", g.get("truncated") is True,
                  json.dumps({k: g.get(k) for k in ("nodeCount", "matched", "truncated")}))
            check("T642 and the note says it is a PREFIX, not a sample",
                  "PREFIX" in str(g.get("note") or ""), str(g.get("note"))[:200])

        for n in nodes[:5]:
            nm = str(n.get("name") or "?")
            check("T642 %s has a package" % nm, str(n.get("package", "")).startswith("/"),
                  json.dumps(n)[:160])
            # BOTH directions. dependsOn answers "what does this need"; referencedBy answers "what
            # breaks if I delete it". A heatmap wants the second, and reporting only one would make
            # half the dashboard impossible.
            for fld in ("dependsOn", "referencedBy"):
                check("T642 %s reports %s" % (nm, fld), isinstance(n.get(fld), (int, float)),
                      json.dumps(n)[:180])

        edges = g.get("edges") or []
        check("T642 edgeCount agrees with the array", g.get("edgeCount") == len(edges),
              "edgeCount=%s but %d edges" % (g.get("edgeCount"), len(edges)))
        pkgs = {str(n.get("package")) for n in nodes}
        # Every edge must start at a node we actually returned, or the graph cannot be drawn.
        dangling = [e for e in edges if str(e.get("from")) not in pkgs]
        check("T642 every edge starts at a returned node", not dangling,
              "%d edges start outside the node set: %s" % (len(dangling), dangling[:2]))
        check("T642 every edge says whether it leaves the prefix",
              all(isinstance(e.get("external"), bool) for e in edges[:20]),
              json.dumps(edges[:2]))

    # ------------------------------------------------------------------ T643 the cheap one
    print("")
    print("=== T643: the distribution endpoint is cheap enough for a bare /Game ===")
    t0 = time.time()
    d = M.call("project_asset_distribution", {}, timeout=600)
    elapsed = time.time() - t0
    check("T643 a bare /Game is ALLOWED here", d.get("ok") is True, json.dumps(d)[:200])
    check("T643 and it answered without stalling", elapsed < 120.0,
          "took %.1fs - it is supposed to never touch referencers" % elapsed)
    if d.get("ok"):
        check("T643 totalAssets is a real number", (d.get("totalAssets") or 0) > 0, json.dumps(d)[:200])
        for arr, key in (("byClass", "class"), ("byFolder", "folder")):
            rows = d.get(arr) or []
            check("T643 %s is populated" % arr, len(rows) > 0, json.dumps(d)[:200])
            check("T643 %s rows carry %s and count" % (arr, key),
                  all(key in r and "count" in r for r in rows[:5]), json.dumps(rows[:2]))
            # Sorted descending - a "top 25" that is not the top 25 is worse than no ordering.
            counts = [r.get("count", 0) for r in rows]
            check("T643 %s is sorted descending" % arr, counts == sorted(counts, reverse=True),
                  str(counts[:6]))
        # distinct* alongside the capped lists is what makes a top-N view visibly a top-N view.
        check("T643 distinctClasses is reported beside the capped list",
              isinstance(d.get("distinctClasses"), (int, float)), json.dumps(d)[:200])
        check("T643 and classesTruncated says whether the list is complete",
              isinstance(d.get("classesTruncated"), bool), json.dumps(d)[:200])
        if isinstance(d.get("distinctClasses"), (int, float)):
            check("T643 classesTruncated agrees with the numbers",
                  d.get("classesTruncated") == (d["distinctClasses"] > len(d.get("byClass") or [])),
                  json.dumps({k: d.get(k) for k in ("distinctClasses", "classesTruncated")}))
        # A low count during a scan is indistinguishable from a low count.
        check("T643 registryStillScanning is reported",
              isinstance(d.get("registryStillScanning"), bool), json.dumps(d)[:200])

    check("T643 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    # ------------------------------------------------------------------ T644 mermaid export
    print("")
    print("=== T644 [mermaid export]: additive, not a mode switch ===")
    # Default (mermaid omitted) must be byte-for-byte the old shape - no `mermaid` key at all, so an
    # existing caller reading only nodes/edges never sees a change.
    m_off = M.call("project_dependency_graph",
                   {"pathPrefix": "/Game/Blueprints", "maxNodes": 10}, timeout=180)
    check("T644 mermaid omitted -> no mermaid field", "mermaid" not in m_off, json.dumps(m_off)[:200])

    m = M.call("project_dependency_graph",
               {"pathPrefix": "/Game/Blueprints", "maxNodes": 10, "mermaid": True}, timeout=180)
    check("T644 mermaid:true still succeeds", m.get("ok") is True, json.dumps(m)[:200])
    if m.get("ok"):
        text = m.get("mermaid")
        check("T644 mermaid is a string", isinstance(text, str), repr(text)[:120])
        # nodes/edges are still present alongside it - additive, never a replacement.
        check("T644 nodes/edges are still present too",
              isinstance(m.get("nodes"), list) and isinstance(m.get("edges"), list),
              json.dumps({k: type(m.get(k)).__name__ for k in ("nodes", "edges", "mermaid")}))
        if isinstance(text, str):
            lines = text.splitlines()
            check("T644 starts with the flowchart declaration",
                  bool(lines) and lines[0].strip() == "flowchart TD", lines[:2])
            node_lines = [ln for ln in lines if ln.strip().startswith("N") and "[\"" in ln]
            arrow_lines = [ln for ln in lines if "-->" in ln]
            nodes = m.get("nodes") or []
            edges = m.get("edges") or []
            # FOUND LIVE, NOT ASSUMED: with maxNodes truncating the outer node walk, an edge's "to"
            # can be a package that IS under pathPrefix (so "external":false, includeExternal never
            # applies to it) but was never itself walked as a node, because the walk broke at maxNodes
            # before reaching it - InPrefix is built from the FULL unfiltered Assets scan, not from
            # the truncated Nodes list. So "every edge target is a returned node" is only true when
            # nothing was truncated; a first draft of this test assumed it always held and was wrong.
            # The endpoint's own T642 above only ever asserted the STARTS-at side for exactly this
            # reason. The mermaid builder handles it correctly (labels any first-seen target however
            # it got there) - what this checks is that node_lines accounts for precisely those extra,
            # unreturned-but-referenced targets, not zero of them and not an unexplained number.
            returned_pkgs = {str(n.get("package")) for n in nodes}
            extra_targets = {str(e.get("to")) for e in edges} - returned_pkgs
            expected_node_lines = len(nodes) + len(extra_targets)
            check("T644 node lines = returned nodes + first-seen-only edge targets",
                  len(node_lines) == expected_node_lines,
                  "%d node lines, expected %d nodes + %d extra targets = %d: %s" %
                  (len(node_lines), len(nodes), len(extra_targets), expected_node_lines, node_lines[:3]))
            check("T644 one arrow per edge", len(arrow_lines) == len(edges),
                  "%d arrows for %d edges" % (len(arrow_lines), len(edges)))
            # ids must be unique and stable within the one response - same package, same id, every time.
            ids = [ln.strip().split("[", 1)[0] for ln in node_lines]
            check("T644 node ids are unique", len(ids) == len(set(ids)), ids[:10])

    # includeExternal + mermaid together must not crash, and now CAN legitimately carry more node
    # lines than nodeCount (the first-seen-external-target case the handler documents).
    me = M.call("project_dependency_graph",
                {"pathPrefix": "/Game/Blueprints", "maxNodes": 10,
                 "includeExternal": True, "mermaid": True}, timeout=180)
    check("T644 includeExternal + mermaid together succeeds", me.get("ok") is True,
          json.dumps(me)[:200])
    if me.get("ok"):
        check("T644 and mermaid is still a non-empty string",
              isinstance(me.get("mermaid"), str) and len(me["mermaid"]) > 0, repr(me.get("mermaid"))[:120])

    check("T644 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    # ------------------------------------------------------------------ T646 truncation
    print("")
    print("=== T646: foldersTruncated / classesTruncated TRACK the limit ===")
    # WHY A FLAG NEEDS TESTING AT ALL. A caller who reads `folders` without noticing it was capped
    # acts on a partial answer and has no way to know - that is the whole job of the flag, and it was
    # asserted nowhere. Both fields were among 48 consequence-reporting response fields that no suite
    # named, found 2026-08-31.
    #
    # ASSERTED IN BOTH DIRECTIONS on purpose. This project truncates at the DEFAULT limit, so
    # "foldersTruncated is True" passes just as well against a field hardcoded to true - the
    # deprecated-but-CONSTANT trap docs/02 records, where HasLayersContent() became `return true;` on
    # 5.7 and every presence check still passed. A flag is only proven by watching it go false.
    small = M.call("project_asset_distribution", {"topFolders": 1, "topClasses": 1}, timeout=150)
    big = M.call("project_asset_distribution", {"topFolders": 5000, "topClasses": 5000}, timeout=150)
    check("T646 both calls answer", small.get("ok") is True and big.get("ok") is True,
          json.dumps({"small": small.get("ok"), "big": big.get("ok")}))

    def rows(d, *names):
        for n in names:
            if isinstance(d.get(n), list):
                return d[n]
        return []

    check("T646 a limit of 1 returns 1 folder, not 'about one'",
          len(rows(small, "folders", "byFolder")) == 1,
          "got %d" % len(rows(small, "folders", "byFolder")))
    check("T646 and says so - foldersTruncated is TRUE when the cap bit",
          small.get("foldersTruncated") is True, json.dumps(small)[:200])
    check("T646 same for classes", small.get("classesTruncated") is True, json.dumps(small)[:200])

    check("T646 raised past the real total, foldersTruncated goes FALSE - which is what proves the "
          "flag tracks the cap rather than being a constant",
          big.get("foldersTruncated") is False, json.dumps(big)[:200])
    check("T646 and classesTruncated goes false too",
          big.get("classesTruncated") is False, json.dumps(big)[:200])
    check("T646 the uncapped answer really is bigger - otherwise the two calls proved nothing "
          "about the cap",
          len(rows(big, "folders", "byFolder")) > len(rows(small, "folders", "byFolder")),
          "small=%d big=%d" % (len(rows(small, "folders", "byFolder")),
                               len(rows(big, "folders", "byFolder"))))

    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
