"""Arrange a blueprint graph from the client side - no C++, no build, no plugin.

WHY THIS EXISTS. MifBridge can AUTHOR a blueprint graph and cannot ARRANGE one. Every node lands
where the caller put it or at a hardcoded offset (PlaceAndInit uses things like
`EntryLike->NodePosX + 800`), so an agent can build a correct graph that is unreadable to the person
who opens it, and their first act is to tidy it by hand. Andre raised it on 2026-08-31.

WHY IT IS PYTHON AND NOT AN ENDPOINT. Because it does not need to be one, which took three passes to
notice:

  * the engine ships NO blueprint graph layout - nothing named AutoArrange, LayoutGraph,
    ArrangeNodes or AutoLayout in Editor/BlueprintGraph, Editor/Kismet or Editor/GraphEditor. The
    material equivalent MifBridge exposes is one line delegating to
    UMaterialEditingLibrary::LayoutMaterialExpressions, so there was nothing to reuse.
  * Blueprint Assist does it well and needs a graph OPEN IN A TAB - FBAGraphHandler is constructed
    from an SGraphEditor - because that is the only way to measure real node extents.
  * but `list_nodes` already returns the WHOLE topology in one call (SerializeNode with
    bIncludePins), and `move_node` already writes NodePosX/NodePosY. So the layout is computable
    here, applied through endpoints that already exist, on any project and any engine version.

WHAT IT CANNOT DO, said plainly. UEdGraphNode::NodeWidth/NodeHeight are "only used when the node can
be resized" - comment boxes and a few others. An ordinary K2 node has NO stored size; its extent is
measured by the SGraphNode widget at paint time. So extents here are ESTIMATED from pin count and
title length, and the estimate is wrong in one of two directions: too small and rows crowd, too
large and the graph sprawls. Generous spacing is chosen deliberately - sprawl is legible, overlap is
not.

DRY RUN BY DEFAULT. It prints the plan and changes nothing. --apply moves the nodes.

Usage:
    python tools/layout_graph.py <graphId>            # plan only, nothing moves
    python tools/layout_graph.py <graphId> --apply    # actually move the nodes
    python tools/layout_graph.py <graphId> --comment  # also plan a comment box per event chain
    python tools/layout_graph.py --self-test          # prove the algorithm, no editor needed

Exit codes:  0 planned or applied   1 could not read the graph   2 bridge not up
"""
import json
import sys
from collections import defaultdict

import mifaudit as M

# Spacing, in graph units. Deliberately generous - see the estimation note above.
COL_GAP = 420      # between layers, left to right
ROW_GAP = 90       # between nodes within a layer
PIN_HEIGHT = 26    # per pin, for the height estimate
NODE_BASE = 70     # title bar and padding


def estimate_height(node):
    """A node is roughly its pin count. Not exact - nothing here can be."""
    return NODE_BASE + PIN_HEIGHT * len(node.get("pins") or [])


def exec_pins(node, direction):
    return [p for p in (node.get("pins") or [])
            if (p.get("type") or {}).get("category") == "exec"
            and (p.get("direction") or "") == direction]


def build_graph(nodes):
    """(successors, predecessors) over EXEC flow, falling back to data links.

    Exec first because it is what a reader follows. A pure-data node - a getter, a literal - has no
    exec pins at all, so it is attached to whatever consumes it and lands one column to its left.
    """
    by_guid = {n.get("guid"): n for n in nodes}
    succ, pred = defaultdict(set), defaultdict(set)
    for n in nodes:
        g = n.get("guid")
        for p in (n.get("pins") or []):
            if (p.get("direction") or "") != "output":
                continue
            for link in (p.get("linkedTo") or []):
                other = link.get("node")
                if other and other in by_guid and other != g:
                    succ[g].add(other)
                    pred[other].add(g)
    return by_guid, succ, pred


def assign_layers(by_guid, succ, pred):
    """Longest-path layering: a node sits one column right of its furthest predecessor.

    Cycles are possible in a blueprint (a loop back into an earlier exec pin), so this is iterative
    with a visit cap rather than a topological sort that would not terminate.
    """
    layer = {g: 0 for g in by_guid}
    for _ in range(len(by_guid) + 1):
        changed = False
        for g in by_guid:
            if pred[g]:
                want = max(layer[p] for p in pred[g]) + 1
                if want > layer[g]:
                    layer[g] = want
                    changed = True
        if not changed:
            break
    return layer


def order_within_layers(by_guid, layer, pred, succ):
    """Barycentre ordering - a node sits near the average row of what it connects to.

    One pass is enough for a prototype. The point is to stop wires crossing gratuitously, not to
    reach an optimum nothing here can verify.
    """
    columns = defaultdict(list)
    for g, col in layer.items():
        columns[col].append(g)
    for col in sorted(columns):
        columns[col].sort(key=lambda g: (by_guid[g].get("y") or 0, g))
    rank = {g: i for col in columns for i, g in enumerate(columns[col])}
    for col in sorted(columns):
        if col == 0:
            continue
        def bary(g):
            near = [rank[p] for p in pred[g] if p in rank] or [rank[g]]
            return sum(near) / float(len(near))
        columns[col].sort(key=bary)
        for i, g in enumerate(columns[col]):
            rank[g] = i
    return columns


def plan(nodes):
    by_guid, succ, pred = build_graph(nodes)
    layer = assign_layers(by_guid, succ, pred)
    columns = order_within_layers(by_guid, layer, pred, succ)
    positions = {}
    for col in sorted(columns):
        y = 0
        for g in columns[col]:
            positions[g] = (col * COL_GAP, y)
            y += estimate_height(by_guid[g]) + ROW_GAP
    return by_guid, positions, columns


# Comment boxes, which Andre asked for alongside the sorting: "if we can get mifbridge to auto sort
# and even comment boxes of stuff it makes would be lovely".
#
# add_comment takes graphId, x, y, width, height and text - pure geometry - so a box is drawn by
# computing the bounding rectangle of a group and padding it. UE treats whatever falls inside a
# comment's rectangle as its members, so there is nothing to "attach": get the rectangle right and
# the membership follows.
#
# GROUPED BY EVENT CHAIN, because that is how a person comments a graph - "BeginPlay setup",
# "OnHit handling" - and because it is the grouping this tool can actually compute. Every node
# reachable from one root goes in that root's box.
# ERR LARGE, NEVER SMALL, and this is not aesthetics - it is AutoSizeComments' semantics.
#
# UE comment membership is GEOMETRIC: a node inside the rectangle is contained, one outside is not.
# AutoSizeComments (installed here, and on plenty of other projects) exposes ResizeToFit, which
# resizes a comment to fit the nodes it CONTAINS. So the two error directions are not symmetric:
#
#   box too LARGE   ASC shrinks it to fit, and the result is correct
#   box too SMALL   the excluded node was never a member, so ASC fits the box to what IS inside
#                   and the mistake is locked in rather than corrected
#
# Since the extents feeding these boxes are estimates (an ordinary K2 node has no stored size), one
# of the two WILL happen. Generous padding chooses the recoverable one.
COMMENT_PAD = 140
NODE_WIDTH_MIN = 260
TITLE_CHAR_W = 9


def estimate_width(node):
    """Width is mostly the title. Same estimate caveat as the height - see the module docstring."""
    title = node.get("title") or node.get("class") or ""
    return max(NODE_WIDTH_MIN, NODE_BASE + TITLE_CHAR_W * len(title))


def roots_of(by_guid, pred):
    """Nodes nothing flows INTO - the events and entry points a reader starts from."""
    return [g for g in by_guid if not pred[g]]


def reachable(start, succ):
    seen, stack = set(), [start]
    while stack:
        g = stack.pop()
        if g in seen:
            continue
        seen.add(g)
        stack.extend(succ[g])
    return seen


def comment_boxes(by_guid, positions, succ, pred):
    """[(x, y, w, h, text)] - one box per event chain, skipping trivial ones.

    A box around a SINGLE node is noise, not documentation, so groups of one are dropped. Chains
    that share nodes would produce overlapping boxes, so a node is claimed by the first root that
    reaches it - overlapping comments nest visually in UE and read as a mistake.
    """
    boxes, claimed = [], set()
    for root in sorted(roots_of(by_guid, pred),
                       key=lambda g: (positions[g][0], positions[g][1])):
        group = [g for g in reachable(root, succ) if g not in claimed]
        if len(group) < 2:
            continue
        claimed.update(group)
        xs = [positions[g][0] for g in group]
        ys = [positions[g][1] for g in group]
        x2 = max(positions[g][0] + estimate_width(by_guid[g]) for g in group)
        y2 = max(positions[g][1] + estimate_height(by_guid[g]) for g in group)
        title = by_guid[root].get("title") or by_guid[root].get("class") or "Group"
        boxes.append((min(xs) - COMMENT_PAD, min(ys) - COMMENT_PAD,
                      (x2 - min(xs)) + COMMENT_PAD * 2, (y2 - min(ys)) + COMMENT_PAD * 2,
                      title))
    return boxes


SELF_TEST_GRAPH = [
    {"guid": "A", "title": "Event BeginPlay", "x": 0, "y": 0, "pins": [
        {"name": "then", "direction": "output", "type": {"category": "exec"},
         "linkedTo": [{"node": "B", "pin": "execute"}]}]},
    {"guid": "B", "title": "Branch", "x": 0, "y": 0, "pins": [
        {"name": "execute", "direction": "input", "type": {"category": "exec"}, "linkedTo": []},
        {"name": "Condition", "direction": "input", "type": {"category": "bool"}, "linkedTo": []},
        {"name": "then", "direction": "output", "type": {"category": "exec"},
         "linkedTo": [{"node": "C", "pin": "execute"}]}]},
    {"guid": "C", "title": "Print String", "x": 0, "y": 0, "pins": [
        {"name": "execute", "direction": "input", "type": {"category": "exec"}, "linkedTo": []}]},
    {"guid": "D", "title": "Get Health", "x": 0, "y": 0, "pins": [
        {"name": "Health", "direction": "output", "type": {"category": "float"},
         "linkedTo": [{"node": "B", "pin": "Condition"}]}]},
    # A CYCLE, because blueprints have them - a loop body wiring back into an earlier exec pin.
    # assign_layers is iterative with a visit cap for exactly this; a topological sort would hang.
    {"guid": "E", "title": "Loop Back", "x": 0, "y": 0, "pins": [
        {"name": "then", "direction": "output", "type": {"category": "exec"},
         "linkedTo": [{"node": "B", "pin": "execute"}]},
        {"name": "execute", "direction": "input", "type": {"category": "exec"}, "linkedTo": []}]},
]


def self_test():
    """Prove the ALGORITHM with no bridge, no editor and no session.

    The layout is pure computation over what list_nodes returns, so it is testable offline - which
    is worth having because the only other way to check it is to move somebody's nodes and look.
    """
    fails = []

    def check(name, cond, detail=""):
        print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))
        if not cond:
            fails.append(name)

    by_guid, positions, columns = plan(SELF_TEST_GRAPH)
    col_of = {g: c for c in columns for g in columns[c]}

    check("exec flows LEFT TO RIGHT - the branch is right of the event",
          col_of["B"] > col_of["A"], "A=%d B=%d" % (col_of["A"], col_of["B"]))
    check("and the print is right of the branch",
          col_of["C"] > col_of["B"], "B=%d C=%d" % (col_of["B"], col_of["C"]))
    # THE ONE THAT IS EASY TO GET WRONG: a pure-data node has no exec pins and must still land to
    # the LEFT of whatever consumes it, not stacked at the origin with the other sources.
    check("a data-only node sits LEFT of its consumer",
          col_of["D"] < col_of["B"], "D=%d B=%d" % (col_of["D"], col_of["B"]))
    check("a CYCLE terminates instead of hanging", "E" in col_of, sorted(col_of))
    # Overlap is the failure a reader notices first, so it is asserted rather than assumed.
    per_col = {}
    for g, (x, y) in positions.items():
        per_col.setdefault(x, []).append((y, estimate_height(by_guid[g]), g))
    overlaps = []
    for x, rows in per_col.items():
        rows.sort()
        for (y1, h1, g1), (y2, _, g2) in zip(rows, rows[1:]):
            if y1 + h1 > y2:
                overlaps.append((g1, g2))
    check("no two nodes in a column overlap, by their estimated heights", not overlaps, overlaps)

    # ---- comment boxes ------------------------------------------------------------------
    _, succ2, pred2 = build_graph(SELF_TEST_GRAPH)
    boxes = comment_boxes(by_guid, positions, succ2, pred2)
    check("a multi-node chain gets a comment box", len(boxes) >= 1, boxes)
    if boxes:
        check("and it is labelled with the event that starts the chain",
              any("BeginPlay" in t for _, _, _, _, t in boxes), [t for *_, t in boxes])
        # THE ONE THAT MATTERS. Three of the five test nodes are roots, so a naive one-box-per-root
        # would draw three boxes on top of each other - which nests visually in UE and reads as a
        # mistake. The claiming pass exists to stop that, so it is asserted rather than trusted.
        bad = []
        for i, (x1, y1, w1, h1, _) in enumerate(boxes):
            for (x2, y2, w2, h2, _) in boxes[i + 1:]:
                if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                    bad.append((i, x1, y1))
        check("and no two comment boxes overlap", not bad, bad)

    print("")
    print("self-test: %d failed" % len(fails))
    return 1 if fails else 0


def main():
    if "--self-test" in sys.argv:
        return self_test()
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    graph_id = sys.argv[1]
    apply_it = "--apply" in sys.argv

    if not M.wait_for_bridge(timeout=300):
        print("bridge never came up")
        return 2

    # hideKnots FALSE on purpose: a knot is a real node that occupies space, and laying the graph
    # out as if it were not there is how you get a wire through a node.
    r = M.call("list_nodes", {"graphId": graph_id, "hideKnots": False}, timeout=180)
    nodes = r.get("nodes") or []
    if not nodes:
        print("no nodes returned for %s: %s" % (graph_id, json.dumps(r)[:200]))
        return 1

    by_guid, positions, columns = plan(nodes)
    _, succ, pred = build_graph(nodes)
    boxes = comment_boxes(by_guid, positions, succ, pred) if "--comment" in sys.argv else []
    print("%d node(s) in %d column(s)" % (len(nodes), len(columns)))
    for col in sorted(columns):
        names = [(by_guid[g].get("title") or by_guid[g].get("class") or "?")[:28]
                 for g in columns[col]]
        print("  col %-2d  %s" % (col, ", ".join(names)))

    for x, y, w, h, text in boxes:
        print("  box  %-28s %dx%d at (%d,%d)" % (text[:28], w, h, x, y))

    moved = 0
    if not apply_it:
        print("\nDRY RUN - nothing moved. Pass --apply to move the nodes.")
        return 0

    for g, (x, y) in positions.items():
        before = (by_guid[g].get("x"), by_guid[g].get("y"))
        if before == (x, y):
            continue
        mv = M.call("move_node", {"graphId": graph_id, "nodeGuid": g, "x": x, "y": y}, timeout=120)
        if mv.get("ok") is False:
            print("  move_node refused for %s: %s" % (g[:8], (mv.get("error") or "")[:120]))
            continue
        moved += 1
    print("\nmoved %d of %d node(s)" % (moved, len(positions)))

    # POSTCONDITION, not the call's word for it. move_node reporting ok is not the graph having
    # changed, and this whole repo exists because those are different things.
    after = {n.get("guid"): (n.get("x"), n.get("y"))
             for n in (M.call("list_nodes", {"graphId": graph_id, "hideKnots": False},
                              timeout=180).get("nodes") or [])}
    wrong = [g for g, want in positions.items() if after.get(g) != want]
    if wrong:
        print("%d node(s) are NOT where they were placed - read the graph before trusting this"
              % len(wrong))
        return 1
    print("read back: every node is where it was placed")

    # Comments LAST, so the boxes are drawn around where the nodes ended up rather than where they
    # were planned to go. If a move was refused, the box would otherwise enclose empty space.
    for x, y, w, h, text in boxes:
        c = M.call("add_comment", {"graphId": graph_id, "x": x, "y": y,
                                   "width": w, "height": h, "text": text}, timeout=120)
        if c.get("ok") is False:
            print("  add_comment refused for %r: %s" % (text[:24], (c.get("error") or "")[:120]))
    if boxes:
        print("added %d comment box(es)" % len(boxes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
