"""add_timeline - the endpoint that reported success while creating nothing at all.

WHY THIS SUITE EXISTS. eea334a records that add_timeline NEVER CREATED A TIMELINE, and nothing locked
the fix in afterwards. A fix without a test has a shelf life; this one especially, because the broken
version was not obviously broken from the outside.

THE ORIGINAL BUG, because the assertions below only make sense against it. The handler was written
node-first, on the belief that spawning a UK2Node_Timeline runs PostPlacedNewNode and that the node
creates its own UTimelineTemplate. UK2Node_Timeline has no PostPlacedNewNode override at all - its only
Post* override is PostPasteNode - so no template was ever created. Blueprint->Timelines stayed empty,
FindTimelineTemplateByVariableName always returned null, every call fell into the null-template failure
branch, and length/autoPlay/loop/floatTracks were all silently discarded. The error text blamed a NAME
COLLISION, on a brand-new blueprint where no collision was possible, which is the detail that makes this
a good story: the endpoint failed 100% of the time and explained itself with a reason that could not be
true.

The fix calls FBlueprintEditorUtils::AddNewTimeline explicitly - the same call the editor's own Add
Timeline action makes - and points the node at the template by name AND guid, because DestroyNode looks
the template up by name to clean it up and an inconsistent pair orphans it.

TWO THINGS THAT MAKE THIS FAMILY EASY TO TEST WRONGLY, both learned by reading the handler:

  1. ASSERT ON THE TEMPLATE, NEVER ON THE NODE. UK2Node_Timeline::bAutoPlay and bLoop are
     UPROPERTY(Transient) caches refreshed only inside AllocateDefaultPins, and the handler only calls
     ReconstructNode when at least one track was added. So for a track-less autoPlay:true call the
     NODE's cached flag is false while the TEMPLATE's is true. A suite asserting node.bAutoPlay would
     pass with tracks and fail without, for no real reason, and someone would "fix" the handler.
  2. get_property takes objectPath, NOT blueprintId. The blueprintId string IS the object path, so it
     goes in as objectPath - a distinction that has cost time elsewhere in this repo.

SAFETY: scratch blueprints under /Game/_MifTL only, nothing saved.
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

    bppath = "/Game/_MifTL/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bppath, "parentClass": "Actor"}).get("blueprintId")
    check("a scratch blueprint exists", bool(bid), "create_blueprint returned nothing")
    if not bid:
        return 1

    def prop(path):
        # objectPath, not blueprintId - the blueprintId string IS the object path.
        return M.call("get_property", {"objectPath": bid, "propertyPath": path}, timeout=60)

    # ------------------------------------------------------------------ T510 the bug itself
    print("")
    print("=== T510 [the bug]: a timeline is really CREATED, not merely reported ===")
    r = M.call("add_timeline", {"blueprintId": bid, "name": "TLProgress", "floatTracks": ["Alpha"],
                                "length": 2.5, "autoPlay": True, "loop": True, "x": 400, "y": 300},
               timeout=90)
    check("T510 the call succeeds", r.get("ok") is True, json.dumps(r)[:260])
    if not r.get("ok"):
        print("   (nothing else can be checked without a timeline)")
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1
    check("T510 and it reports the timeline's name", r.get("timeline") == "TLProgress",
          json.dumps(r)[:220])
    guid = r.get("nodeGuid")
    check("T510 and a node guid", bool(guid), json.dumps(r)[:200])

    # THE assertion of the whole file. Pre-fix this array was ALWAYS empty, for every call, forever.
    tl = prop("Timelines")
    val = tl.get("value")
    check("T510 the blueprint's Timelines array is NOT empty",
          bool(val) and len(val if isinstance(val, list) else [val]) >= 1,
          "Timelines=%s - this was always empty before eea334a" % json.dumps(val)[:200])
    check("T510 and it names this timeline's template",
          "TLProgress" in json.dumps(val), json.dumps(val)[:240])

    # The lookup key DestroyNode uses. If it disagrees with the node, cleanup orphans the template.
    vn = prop("Timelines[0].VariableName")
    check("T510 the template's VariableName matches the node's timeline name",
          str(vn.get("value")) == "TLProgress", json.dumps(vn)[:200])

    # ------------------------------------------------------------------ T511 config lands on the template
    print("")
    print("=== T511: length/autoPlay/loop reach the TEMPLATE (they used to be discarded) ===")
    def truthy(g):
        """A uint8 bool comes back as the STRING "True", not a JSON boolean.

        get_property reports `type: "uint8"` for a bool UPROPERTY and stringifies the value, so the
        answer is "True" with a capital T. A check written as `value in (True, "true", 1)` fails on a
        property that is correctly set - which is what happened the first time this suite ran, and is
        the same wrong-shaped-probe mistake that has been misread as a silent failure repeatedly in
        this project. Prefer `typed` when the endpoint provides it, since that is the parsed form.
        """
        if isinstance(g.get("typed"), bool):
            return g["typed"]
        v = g.get("value")
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1")

    for path, why in (("Timelines[0].bAutoPlay", "autoPlay"),
                      ("Timelines[0].bLoop", "loop")):
        g = prop(path)
        check("T511 %s is set on the template" % why, truthy(g) is True,
              "%s=%s" % (path, json.dumps(g)[:200]))
    g = prop("Timelines[0].TimelineLength")
    try:
        got = float(g.get("value"))
    except Exception:
        got = None
    check("T511 length reached the template", got is not None and abs(got - 2.5) < 0.001,
          "TimelineLength=%s (asked for 2.5)" % json.dumps(g)[:160])

    # ------------------------------------------------------------------ T512 the track pin
    print("")
    print("=== T512: a float track produces a real output pin ===")
    node = M.call("get_node", {"nodeGuid": guid}, timeout=60).get("node") or {}
    pins = node.get("pins") or []
    names = [p.get("name") for p in pins]
    # This pin exists ONLY if the template was found AND the track was registered in TrackDisplayOrder,
    # so it proves template + FloatTracks.Add + AddDisplayTrack together in one assertion.
    check("T512 the track's pin exists on the node", "Alpha" in names, str(names)[:240])
    alpha = next((p for p in pins if p.get("name") == "Alpha"), None)
    if alpha:
        check("T512 and it is an OUTPUT", str(alpha.get("direction", "")).lower().startswith("out"),
              json.dumps(alpha)[:200])
    # The fixed pins exist with or without a template, so they are a weaker check - but their absence
    # would mean the node itself is wrong.
    for fixed in ("Play", "Stop", "Update", "Finished"):
        check("T512 the standard '%s' pin is present" % fixed, fixed in names, str(names)[:200])

    # ------------------------------------------------------------------ T513 absent is not empty
    print("")
    print("=== T513: a track-less timeline OMITS floatTracks rather than reporting an empty one ===")
    r2 = M.call("add_timeline", {"blueprintId": bid, "name": "TLBare_%d" % st, "autoPlay": True},
                timeout=90)
    check("T513 a timeline with no tracks is still created", r2.get("ok") is True, json.dumps(r2)[:220])
    # Documented behaviour: the key is absent, not []. A test treating missing as empty would pass
    # today and hide the key appearing later with the wrong contents.
    check("T513 and the floatTracks key is ABSENT", "floatTracks" not in r2, json.dumps(r2)[:220])
    # And the reason the whole suite asserts on the template: the node's cached flag is FALSE here,
    # because ReconstructNode only runs when a track was added.
    tl2 = prop("Timelines")
    check("T513 the second timeline is on the blueprint too",
          "TLBare_%d" % st in json.dumps(tl2.get("value")), json.dumps(tl2.get("value"))[:240])

    # ------------------------------------------------------------------ T514 guards
    print("")
    print("=== T514: the wrong spellings are refused with a pointer to the right one ===")
    q = M.call("add_timeline", {"graphId": "%s::EventGraph" % bid, "name": "Nope_%d" % st}, timeout=60)
    check("T514 graphId is refused", q.get("ok") is False, json.dumps(q)[:220])
    check("T514 and the refusal names blueprintId",
          "blueprintId" in (q.get("error") or ""), (q.get("error") or "")[:200])
    q = M.call("add_timeline", {"blueprintId": bid, "timelineName": "Nope2_%d" % st}, timeout=60)
    check("T514 'timelineName' is refused rather than silently ignored", q.get("ok") is False,
          json.dumps(q)[:220])
    q = M.call("add_timeline", {"blueprintId": bid, "name": "Nope3_%d" % st, "tracks": ["A"]}, timeout=60)
    check("T514 'tracks' is refused rather than silently ignored", q.get("ok") is False,
          json.dumps(q)[:220])
    q = M.call("add_timeline", {"blueprintId": bid, "name": "Nope4_%d" % st, "floatTracks": ["", "B"]},
               timeout=60)
    check("T514 an empty track name is refused naming the index", q.get("ok") is False
          and "floatTracks[0]" in (q.get("error") or ""), (q.get("error") or "")[:200])

    # A duplicate name is the one real failure AddNewTimeline can produce, and after the fix it is a
    # checked, nothing-created refusal rather than a post-hoc guess.
    q = M.call("add_timeline", {"blueprintId": bid, "name": "TLProgress"}, timeout=60)
    check("T514 a duplicate timeline name is refused", q.get("ok") is False, json.dumps(q)[:220])

    c = M.call("compile", {"blueprintId": bid}, timeout=120)
    check("T514 the blueprint still compiles",
          c.get("ok") is True and c.get("numErrors", 1) == 0,
          "errors=%s messages=%s" % (c.get("numErrors"), json.dumps(c.get("messages"))[:180]))

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
