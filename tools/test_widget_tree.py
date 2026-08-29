"""Widget TREE topology - move, duplicate, wrap, remove. The four with no suite.

add_tree_widget, list_tree_widgets and rename_tree_widget are covered elsewhere. The four that
rearrange the tree were not, and they are the ones where a subtree can quietly go missing: every one
of them moves a widget that may be carrying children, and children are exactly what a response can
forget to mention.

UMG is worth the attention. The one field bug reported against this plugin was a widget bug - removing
and recreating a WidgetAnimation with the same name killed the editor - and DDS2 mods lean on UMG
heavily.

What each test asks is about the CHILDREN, because that is the part a caller cannot see from an
ok:true:

  T430  move    - does the moved widget arrive with its subtree still attached?
  T431  duplicate - does it clone the whole subtree, and does it NAME what it created? A copy that
                  silently renames is the shape that bit add_component and AddRetargetChain.
  T432  wrap    - does the wrapper end up BETWEEN the widget and its old parent, with the wrapped
                  widget's own children intact?
  T433  remove  - RemoveWidget is always recursive. Removing one container and removing a container
                  holding twelve widgets used to be indistinguishable answers (`removed: true`), so
                  the response now reports removedCount and removedWidgets. That disclosure is what
                  T433 protects. Also confirm-gated as of 2026-08-29 (see below) - the family's own
                  remaining inconsistency, closed on Andre's explicit call rather than fixed silently.

CONFIRM GATE, added 2026-08-29: remove_tree_widget had no confirm=true requirement while
remove_component, remove_variable, remove_function and remove_event_dispatcher all do - flagged for
days in tools/FEATURE_PARITY_SPEC.md's "Deliberately not pursuing" section specifically because adding
it is a judgement call that could break an existing caller's script, not a bug to fix unilaterally.
Asked; Andre said add it. T433 now checks the refusal path first (and that the widget is genuinely
still there afterward) before exercising the real removal with confirm=true.

A note on setup that cost a first run: a fresh WidgetBlueprint ALREADY HAS a CanvasPanel_0 root, so
add_tree_widget with asRoot:true is correctly refused. Build under the existing root instead - the
endpoint says so plainly if you read its error rather than truncating it.
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

    bid = M.call("create_blueprint", {"path": "/Game/_MifTree/WBP_%d" % st,
                                      "parentClass": "UserWidget",
                                      "blueprintType": "WidgetBlueprint"}).get("blueprintId")
    check("a widget blueprint exists", bool(bid), "create_blueprint returned no blueprintId")
    if not bid:
        return 1

    listing = M.call("list_tree_widgets", {"blueprintId": bid})
    root = listing.get("root")
    check("it comes with a root panel already", bool(root), json.dumps(listing)[:180])

    def build(cls, name, parent):
        r = M.call("add_tree_widget", {"blueprintId": bid, "widgetClass": cls,
                                       "name": name, "parentName": parent})
        if not r.get("ok"):
            check("setup: add %s" % name, False, (r.get("error") or "")[:170])
        return r

    def tree():
        return {x.get("name"): x.get("parent")
                for x in (M.call("list_tree_widgets", {"blueprintId": bid}).get("widgets") or [])}

    build("VerticalBox", "Box", root)
    build("Button", "Btn", "Box")
    build("TextBlock", "Inner", "Btn")
    t = tree()
    check("the starting tree is the shape the tests assume",
          t.get("Box") == root and t.get("Btn") == "Box" and t.get("Inner") == "Btn", json.dumps(t))

    # ------------------------------------------------------------------ T430 move
    print("")
    print("=== T430: a moved widget arrives with its children ===")
    m = M.call("move_tree_widget", {"blueprintId": bid, "widgetName": "Btn", "parentName": root})
    check("T430 the move succeeds", m.get("ok") is True, json.dumps(m)[:200])
    check("T430 and reports where it came from", m.get("fromParent") == "Box", json.dumps(m)[:180])
    t = tree()
    check("T430 the widget really moved", t.get("Btn") == root, json.dumps(t))
    # THE assertion: a move that drops the subtree is the silent loss worth catching.
    check("T430 and its child came with it", t.get("Inner") == "Btn", json.dumps(t))
    check("T430 and it says a compile is needed to apply", m.get("needsCompileToApply") is True,
          json.dumps(m)[:180])

    # ------------------------------------------------------------------ T431 duplicate
    print("")
    print("=== T431: duplicating clones the subtree, and NAMES what it made ===")
    d = M.call("duplicate_tree_widget", {"blueprintId": bid, "widgetName": "Btn"})
    check("T431 the duplicate succeeds", d.get("ok") is True, json.dumps(d)[:220])
    created = d.get("created") or []
    # A copy necessarily gets a different name - the tree cannot hold two of one name. What matters is
    # that the response SAYS which names it used, so the caller can address the copy. Reporting only
    # ok:true here would be the add_component bug in a different subsystem.
    check("T431 it names every widget it created", len(created) >= 2, json.dumps(d)[:220])
    check("T431 and names the primary one", bool(d.get("primary")), json.dumps(d)[:180])
    check("T431 and the count matches the source subtree",
          d.get("clonedCount") == d.get("sourceSubtreeSize"),
          "clonedCount=%s sourceSubtreeSize=%s" % (d.get("clonedCount"), d.get("sourceSubtreeSize")))
    t = tree()
    for name in created:
        check("T431 %s really exists in the tree" % name, name in t, json.dumps(t)[:200])
    # The clone's own child must hang off the clone, not off the original.
    primary = d.get("primary")
    clone_kids = [n for n, p in t.items() if p == primary]
    check("T431 the clone carries its own copy of the child", len(clone_kids) >= 1,
          "nothing is parented to %s: %s" % (primary, json.dumps(t)))

    # ------------------------------------------------------------------ T432 wrap
    print("")
    print("=== T432: wrapping inserts a parent without losing anything ===")
    before_parent = tree().get("Box")
    w = M.call("wrap_tree_widget", {"blueprintId": bid, "widgetName": "Box",
                                    "wrapperClass": "SizeBox"})
    check("T432 the wrap succeeds", w.get("ok") is True, json.dumps(w)[:220])
    wrapper = w.get("wrapper")
    check("T432 and names the wrapper it created", bool(wrapper), json.dumps(w)[:180])
    t = tree()
    check("T432 the wrapper took the widget's old place", t.get(wrapper) == before_parent,
          "wrapper %s is under %s, expected %s" % (wrapper, t.get(wrapper), before_parent))
    check("T432 and the widget now sits under the wrapper", t.get("Box") == wrapper, json.dumps(t))

    # ------------------------------------------------------------------ T433 remove discloses
    print("")
    print("=== T433 [the point]: removal is recursive, and must say how much it took ===")
    build("TextBlock", "Solo_%d" % st, root)
    no_confirm = M.call("remove_tree_widget", {"blueprintId": bid, "widgetName": "Solo_%d" % st})
    check("T433 without confirm is refused - added 2026-08-29, same gate every other remove_* endpoint has",
          no_confirm.get("ok") is False, json.dumps(no_confirm)[:200])
    still_there = tree()
    check("T433 the refusal left the widget in place - nothing removed",
          "Solo_%d" % st in still_there, still_there)

    # NOT M.call - guarded_payload strips "confirm" from every payload, which is exactly why the
    # no_confirm probe above genuinely gets refused. SC.confirm_call bypasses that strip deliberately
    # and narrowly, after checking the target is provably scratch (bid is this test's own throwaway
    # WidgetBlueprint, created above).
    leaf = SC.confirm_call("remove_tree_widget", {"blueprintId": bid, "widgetName": "Solo_%d" % st})
    check("T433 removing a leaf succeeds with confirm=true", leaf.get("ok") is True, json.dumps(leaf)[:200])
    check("T433 and reports exactly one removed", leaf.get("removedCount") == 1,
          json.dumps(leaf)[:200])

    # A container with descendants. RemoveWidget always takes the subtree; `removed: true` alone made
    # that indistinguishable from removing a single widget.
    build("VerticalBox", "Doomed", root)
    build("TextBlock", "D1", "Doomed")
    build("Button", "D2", "Doomed")
    build("TextBlock", "D3", "D2")
    r = SC.confirm_call("remove_tree_widget", {"blueprintId": bid, "widgetName": "Doomed"})
    check("T433 removing a container succeeds", r.get("ok") is True, json.dumps(r)[:200])
    check("T433 and reports the WHOLE subtree it took", r.get("removedCount") == 4,
          "removedCount=%s, expected 4 (Doomed + D1 + D2 + D3)" % r.get("removedCount"))
    names = r.get("removedWidgets") or []
    for n in ("Doomed", "D1", "D2", "D3"):
        check("T433 %s is named in removedWidgets" % n, n in names, json.dumps(names)[:200])
    check("T433 and explains that removal is always recursive",
          "recursive" in (r.get("note") or "").lower(), (r.get("note") or "")[:180])
    t = tree()
    check("T433 and they are really gone", not any(n in t for n in ("Doomed", "D1", "D2", "D3")),
          json.dumps(t)[:200])

    # ------------------------------------------------------------------ T434 guards
    print("")
    print("=== T434: bad arguments are refused ===")
    for ep, payload in (("move_tree_widget", {"blueprintId": bid, "widgetName": "NoSuch_zz",
                                              "parentName": root}),
                        ("duplicate_tree_widget", {"blueprintId": bid, "widgetName": "NoSuch_zz"}),
                        ("wrap_tree_widget", {"blueprintId": bid, "widgetName": "NoSuch_zz",
                                              "wrapperClass": "SizeBox"})):
        q = M.call(ep, payload)
        check("T434 %s refuses an unknown widget" % ep, q.get("ok") is False, json.dumps(q)[:170])
    # NOT M.call in this shared loop above - guarded_payload strips "confirm" from every payload
    # regardless of endpoint, so a plain M.call here would refuse for the WRONG reason (missing
    # confirm, not the unknown widget this test is actually about). SC.confirm_call bypasses the
    # strip the same narrow, provably-safe way T433 already does.
    rq = SC.confirm_call("remove_tree_widget", {"blueprintId": bid, "widgetName": "NoSuch_zz"})
    check("T434 remove_tree_widget refuses an unknown widget", rq.get("ok") is False, json.dumps(rq)[:170])
    q = M.call("wrap_tree_widget", {"blueprintId": bid, "widgetName": "Box",
                                    "wrapperClass": "TextBlock"})
    check("T434 wrapping in a non-panel class is refused", q.get("ok") is False,
          "a TextBlock cannot hold children; accepting it would silently lose the wrapped widget: %s"
          % json.dumps(q)[:150])

    c = M.call("compile", {"blueprintId": bid})
    check("T434 the widget blueprint still compiles",
          c.get("ok") is True and c.get("numErrors", 1) == 0, "errors=%s" % c.get("numErrors"))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
