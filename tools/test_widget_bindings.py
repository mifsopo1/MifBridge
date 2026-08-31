"""list_widget_bindings - the read half of a family that could only write.

WHY THIS SUITE EXISTS. add_widget_binding and remove_widget_binding shipped months ago and nothing
could read a binding back. The gap was found on 2026-08-31 by audit_message_endpoints, which caught
remove_widget_binding advising the caller to run `list_widget_bindings` to see what a blueprint
actually has - an endpoint that did not exist. So the family had a documented reader, no reader, and
no test that would have noticed either.

WHAT IT ASKS. Two halves, and the first needs no fixture at all:

  * THE REFUSAL CONTRACT. Called with {} it must name the parameter, and called with a key it does
    not accept it must refuse rather than ignore. Those are the two failure shapes this bridge treats
    as worst - an ignored parameter returns ok:true and sends the caller to debug the wrong
    subsystem - and neither needs a WidgetBlueprint to exist.
  * THE SHAPE, against whatever WidgetBlueprint this project happens to have. bindingCount must
    agree with the row count when nothing is filtered, every row must carry the four identity fields,
    and widgetPresent must be a bool on every row rather than absent.

WHAT IT DOES NOT DO. It does not create a binding. add_widget_binding needs a widget that exists in
the tree and marks the blueprint structurally dirty, and the standing rule for audits in this project
is that they save nothing - a suite that dirties somebody's asset to prove a READ works has the wrong
shape. If this project has no WidgetBlueprint the shape half SKIPS and says so; the refusal half
still runs, because it is the half that catches the regressions that actually happen.

NOT YET RUN. Written 2026-08-31 against a DLL that compiled and linked but is not loaded - the
editor of the moment is running an older build. Whoever runs this first should expect to fix
something in it rather than in the endpoint.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    if "list_widget_bindings" not in set(M.endpoint_names()):
        print("SKIPPED - list_widget_bindings is not registered on this build. It was added to")
        print("  source on 2026-08-31; the loaded DLL predates it. Exit 2 means SKIPPED, distinct")
        print("  from 0 (passed) and 1 (failed).")
        return 2

    # ---------------------------------------------------------------- W900 the refusal contract
    print("\n=== W900: it refuses rather than guessing, and names what it wants ===")
    r = M.call("list_widget_bindings", {})
    err = str(r.get("error") or "")
    check("W900 {} is refused", r.get("ok") is False, json.dumps(r)[:250])
    # Naming the parameter is the whole difference between a usable refusal and 'not found: '.
    check("W900 and the refusal names blueprintId", "blueprintId" in err, err[:250])

    r = M.call("list_widget_bindings", {"blueprintId": "/Game/Nope", "bogusKey_zz": 1})
    check("W901 an unaccepted key is REFUSED, not ignored",
          r.get("ok") is False and "bogusKey_zz" in str(r.get("error") or ""),
          json.dumps(r)[:250])

    # An alias the endpoint documents must actually be accepted, or the KeyNote lies.
    r = M.call("list_widget_bindings", {"blueprintId": "/Game/Nope", "widget": "X"})
    check("W902 'widget' is refused with the correct spelling in the note",
          r.get("ok") is False and "widgetName" in str(r.get("error") or ""),
          json.dumps(r)[:250])

    # ---------------------------------------------------------------- W910 the shape
    print("\n=== W910: the shape, against a real WidgetBlueprint ===")
    found = M.call("find_assets", {"class": "WidgetBlueprint", "limit": 25}).get("assets") or []
    wbps = [a["path"] for a in found if not a["path"].startswith("/Game/_Mif")]
    check("(setup) the project has a WidgetBlueprint to read", len(wbps) > 0, len(wbps))
    if not wbps:
        print("  SKIPPED the shape half - no WidgetBlueprint in this project.")
        return report()

    # The first one that answers. A WidgetBlueprint with zero bindings is a fine subject for every
    # assertion below except the per-row ones, which is why the row checks are guarded separately.
    target, r = None, None
    for path in wbps:
        probe = M.call("list_widget_bindings", {"blueprintId": path})
        if probe.get("ok") is not False:
            target, r = path, probe
            break
    check("W910 it answers for a real WidgetBlueprint", r is not None and r.get("ok") is not False,
          json.dumps(r or {})[:250])
    if not r or r.get("ok") is False:
        return report()
    print("  subject: %s (%s binding(s))" % (target, r.get("bindingCount")))

    rows = r.get("bindings")
    check("W910 bindings is an ARRAY, present even when empty", isinstance(rows, list),
          type(rows).__name__)
    rows = rows or []
    check("W911 count agrees with the rows returned", r.get("count") == len(rows),
          "count=%s rows=%d" % (r.get("count"), len(rows)))
    # Unfiltered, the two counts are the same number. They diverge only under a filter, which is
    # exactly why both are reported - a caller can tell "this widget has none" from "this blueprint
    # has none".
    check("W911 and unfiltered, bindingCount == count",
          r.get("bindingCount") == r.get("count"),
          "bindingCount=%s count=%s" % (r.get("bindingCount"), r.get("count")))
    check("W911 filtered is false when nothing was filtered", r.get("filtered") is False,
          r.get("filtered"))

    if rows:
        need = ("widgetName", "propertyName", "functionName", "widgetPresent")
        missing = [k for k in need if any(k not in row for row in rows)]
        check("W912 every row carries the identity fields and widgetPresent",
              not missing, "missing on at least one row: %s" % missing)
        # PRESENCE is not enough here: widgetPresent absent and widgetPresent:null read the same to
        # `in`, and the whole value of the field is that it is a decided boolean.
        check("W912 widgetPresent is a real bool on every row",
              all(isinstance(row.get("widgetPresent"), bool) for row in rows),
              str([row.get("widgetPresent") for row in rows])[:180])
        check("W913 orphaned counts exactly the rows whose widget is gone",
              r.get("orphaned") == sum(1 for row in rows if row.get("widgetPresent") is False),
              "orphaned=%s rows-false=%d"
              % (r.get("orphaned"), sum(1 for row in rows if row.get("widgetPresent") is False)))
        if r.get("orphaned"):
            check("W913 and it explains what happens to them",
                  "compile" in str(r.get("orphanedNote") or "").lower(), r.get("orphanedNote"))

        # W914 the filter narrows, and says it narrowed.
        first = rows[0].get("widgetName")
        f = M.call("list_widget_bindings", {"blueprintId": target, "widgetName": first})
        check("W914 filtering by widgetName narrows to that widget only",
              f.get("ok") is not False
              and all(row.get("widgetName") == first for row in (f.get("bindings") or [])),
              json.dumps(f)[:250])
        check("W914 and filtered:true says the list is not the whole story",
              f.get("filtered") is True, f.get("filtered"))
        # bindingCount must stay the BLUEPRINT's total under a filter - that is the distinction the
        # two fields exist to draw.
        check("W914 bindingCount still reports the blueprint total under a filter",
              f.get("bindingCount") == r.get("bindingCount"),
              "filtered=%s unfiltered=%s" % (f.get("bindingCount"), r.get("bindingCount")))

        nope = M.call("list_widget_bindings",
                      {"blueprintId": target, "widgetName": "MifNoSuchWidget_zz"})
        check("W915 a filter matching nothing answers ok with an EMPTY list, not an error",
              nope.get("ok") is not False and nope.get("bindings") == [] and nope.get("count") == 0,
              json.dumps(nope)[:250])
    else:
        print("  (no bindings on this asset - per-row checks not exercised)")

    return report()


def report():
    print("")
    print("=" * 72)
    print("PASS %d  FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
