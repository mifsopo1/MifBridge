"""set_data_layer_visibility and set_data_layer_loaded_in_editor - the Data Layers WRITE half.

The read half (list_data_layers) shipped first and this was recorded in the spec as blocked on a
Build.cs dependency the agent was not authorised to add. Andre authorised it on 2026-08-26, so the
blocker is gone and these are the first two writes.

WHAT THIS SUITE IS REALLY GUARDING: SetDataLayerVisibility RETURNS VOID.

That is the precise shape behind docs/06 issue 14 and behind three earlier defects in this project -
call an engine API that cannot fail loudly, then report ok because nothing threw. Both endpoints
therefore READ THE STATE BACK after writing and report `verified` separately from `changed`:

  * changed  - did the WORLD change?    (before != after)
  * verified - did the REQUEST take?    (after == what you asked for)

Those are different questions and conflating them is the bug. Setting a layer to the value it already
held is changed:false AND verified:true - a successful no-op, not a failure. T612 pins exactly that,
because a no-op reporting changed:true is the defect edit_container had.

HONEST LIMITATION, stated rather than hidden: the write path is only exercised if the loaded world
actually HAS Data Layers. Data Layers exist only in World Partition maps, the scratch world used for
testing has none, and the standing rule is not to open Andre's real maps. So on a bare world this suite
asserts the CONTRACTS - which is where drift actually shows up - and reports the write path as not
exercised rather than passing vacuously. It does not pretend to cover what it did not run.

SAFETY: no saving. Any write performed is EDITOR STATE (visibility / editor-loaded), not content, and
the suite restores whatever it changed and asserts it dirtied nothing.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T610 registered
    print("=== T610: both writes are registered ===")
    eps = M.endpoint_names()
    for e in ("set_data_layer_visibility", "set_data_layer_loaded_in_editor"):
        check("T610 %s is registered" % e, e in eps, "%d endpoints, this one missing" % len(eps))

    # ------------------------------------------------------------------ T611 contracts
    print("")
    print("=== T611: the contracts, which is where drift shows up first ===")
    q = M.call("set_data_layer_visibility", {"name": "zzNoSuchLayer_zz", "visible": True}, timeout=120)
    check("T611 an unknown layer is refused", q.get("ok") is False, json.dumps(q)[:180])
    # Listing what IS present is the difference between a dead end and a next step: the usual cause is
    # a short-name versus FName mismatch.
    check("T611 and the refusal lists what IS present",
          "Present:" in (q.get("error") or ""), (q.get("error") or "")[:190])

    q = M.call("set_data_layer_visibility", {"name": "x"}, timeout=90)
    check("T611 omitting `visible` is refused, not defaulted", q.get("ok") is False, json.dumps(q)[:180])
    check("T611 and it says list_data_layers is the read",
          "list_data_layers" in (q.get("error") or ""), (q.get("error") or "")[:190])

    q = M.call("set_data_layer_loaded_in_editor", {"name": "x"}, timeout=90)
    check("T611 omitting `loaded` is refused", q.get("ok") is False, json.dumps(q)[:180])

    # The two endpoints are easy to confuse and mean genuinely different things, so each points at the
    # other by name rather than just rejecting the key.
    q = M.call("set_data_layer_visibility", {"name": "x", "visible": True, "loaded": True}, timeout=90)
    check("T611 `loaded` on the visibility endpoint is refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T611 and it names set_data_layer_loaded_in_editor",
          "set_data_layer_loaded_in_editor" in (q.get("error") or ""), (q.get("error") or "")[:190])

    q = M.call("set_data_layer_loaded_in_editor", {"name": "x", "loaded": True, "visible": True},
               timeout=90)
    check("T611 `visible` on the loading endpoint is refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T611 and it names set_data_layer_visibility",
          "set_data_layer_visibility" in (q.get("error") or ""), (q.get("error") or "")[:190])

    # ------------------------------------------------------------------ T612 the write path
    print("")
    print("=== T612 [void-return guard]: verified is read back, not assumed ===")
    r = M.call("list_data_layers", {}, timeout=180)
    check("T612 list_data_layers still answers", r.get("ok") is True, json.dumps(r)[:200])
    layers = r.get("dataLayers") or r.get("layers") or []
    names = [(l.get("name") if isinstance(l, dict) else l) for l in layers]

    if not names:
        # Reported, not skipped silently. A suite that says nothing here would let a reader assume the
        # write path is covered when it never ran.
        print("  (this world has no Data Layers - the WRITE PATH IS NOT EXERCISED.")
        print("   Data Layers exist only in World Partition maps; the scratch world has none and the")
        print("   standing rule is not to open real maps. Contracts above are still asserted.)")
        check("T612 (write path not exercised: no Data Layers in this world)", True)
    else:
        target = names[0]
        dirty_before = len(M.call("list_dirty_packages", {}, timeout=90).get("packages") or [])

        first = M.call("set_data_layer_visibility", {"name": target, "visible": True}, timeout=120)
        check("T612 the write succeeds", first.get("ok") is True, json.dumps(first)[:200])
        for f in ("before", "after", "changed", "verified", "effectiveVisible"):
            check("T612 it reports %s" % f, isinstance(first.get(f), bool), json.dumps(first)[:200])
        # THE assertion this endpoint exists to make safe. SetDataLayerVisibility returns void, so the
        # only honest way to claim it worked is to read the state back.
        check("T612 verified reflects the read-back, not the call",
              first.get("verified") == (first.get("after") is True),
              json.dumps(first)[:220])

        # Writing the value that is already there must not claim a change - and must still verify.
        again = M.call("set_data_layer_visibility", {"name": target, "visible": True}, timeout=120)
        check("T612 a repeat write reports changed:false", again.get("changed") is False,
              "before=%s after=%s changed=%s" % (again.get("before"), again.get("after"),
                                                 again.get("changed")))
        check("T612 but still reports verified:true - a no-op is a success",
              again.get("verified") is True, json.dumps(again)[:200])
        check("T612 and before == after on a no-op", again.get("before") == again.get("after"),
              json.dumps(again)[:180])

        # Restore whatever the original state was, so the suite leaves the world as it found it.
        orig = first.get("before")
        if isinstance(orig, bool):
            back = M.call("set_data_layer_visibility", {"name": target, "visible": orig}, timeout=120)
            check("T612 the original visibility is restored", back.get("verified") is True,
                  json.dumps(back)[:200])

        dirty_after = len(M.call("list_dirty_packages", {}, timeout=90).get("packages") or [])
        # Visibility is EDITOR state, not content. If this dirties a package, the endpoint is doing
        # more than it claims.
        check("T612 and visibility did not dirty a package - it is editor state, not content",
              dirty_after <= dirty_before,
              "dirty packages %d -> %d" % (dirty_before, dirty_after))

    check("T612 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
