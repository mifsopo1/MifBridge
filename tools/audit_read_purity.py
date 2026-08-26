"""Does an endpoint that LOOKS like a read leave a mark?

A `list_`, `get_`, `describe_` or `find_` endpoint is one a caller reaches for freely - to look
around, to check a result, to answer a question mid-task. Nobody budgets for a side effect from one.
So if such a call dirties a package, the cost is paid much later and by someone else: a save-all
writes a change nobody made, into an asset nobody edited, and the diff is unattributable.

This is not hypothetical in Unreal. Plenty of engine getters are GetOrCreate underneath, editor-only
data is lazily built on first access, and a stray Modify() in a read path dirties a package just as
thoroughly as a real edit. None of that shows up in the response: the endpoint answers the question
correctly and reports ok:true either way.

HOW IT IS MEASURED. Dirty packages, via list_dirty_packages, before and after each call. A package
that was clean and is now dirty is the finding. That is a much better instrument than comparing
asset state by hand: it is what the editor itself uses to decide what a save would write, which is
exactly the consequence in question.

THE BUCKET NAME IS NOT THE QUESTION. self_audit reports 88 endpoints as `readOnly`, but that bucket
means "RunEndpoint does not wrap this in the blanket transaction" - and `compile` and `build_navmesh`
are deliberately in it. This asks the different, plainer question: does a thing NAMED like a read
behave like one.

VACUITY IS REPORTED, NOT HIDDEN. Most of these endpoints need a real argument, and a call that fails
on a missing parameter proves nothing about purity. The report separates endpoints that were actually
exercised from those that were only attempted, because "0 findings" across mostly-failed calls is not
a clean result, it is an untested one. That distinction is the difference between this being evidence
and being decoration.

SAFETY. Read-only by intent: `confirm` is never sent, the DENY list applies, nothing is saved. The
irony of a purity audit dirtying something is not lost - it never sends a mutating endpoint.
"""
import json
import os
import sys

import mifaudit as M

# Prefixes that promise a read. A caller reading this list should agree that a side effect from any
# of them would be a surprise.
READ_PREFIXES = ("list_", "get_", "describe_", "find_", "read_", "diff_", "inspect_",
                 "resolve_", "check_", "diagnose_", "audit_", "thumbnail_capabilities",
                 "shader_compile_status", "pie_status", "nav_status", "landscape_info",
                 "self_audit", "parity", "search_")

# Endpoints that are named like reads but are known to do real work, and are excluded deliberately
# rather than silently. Each one is here for a stated reason.
EXCLUDE = {
    # Scans a mount root and waits on the asset registry; it is DECLARED blocking and can take
    # minutes. Purity is not the interesting question about it.
    "audit_unused",
    # A render/flush, and it is in the declared-blocking table.
    "diagnose_landscape_draws",
}

BY_CLASS = [
    ("Blueprint", ("blueprintId", "blueprint", "path", "assetPath")),
    ("Material", ("material", "materialPath", "path", "assetPath")),
    ("StaticMesh", ("mesh", "staticMesh", "path", "assetPath")),
    ("SkeletalMesh", ("mesh", "skeletalMesh", "path", "assetPath")),
    ("DataTable", ("dataTable", "table", "path", "assetPath")),
    ("Texture2D", ("texture", "path", "assetPath")),
    ("UserDefinedStruct", ("struct", "structPath", "path")),
    ("UserDefinedEnum", ("enum", "enumPath", "path")),
    # Added after the first run left list_widget_animations, list_tree_widgets and describe_animation
    # unexercised - they need a subject of their own class and no generic sample supplied one.
    ("WidgetBlueprint", ("blueprintid", "blueprintId", "path")),
    ("AnimSequence", ("animation", "asset", "assetPath", "path")),
]


def build_context():
    """Real arguments for the endpoints that need something a class-sample cannot supply.

    Without this, 26 of 64 endpoints were never exercised - including describe_class, get_node,
    get_property and list_object_properties, which are precisely the ones that reach into editor-only
    data and are therefore the most likely to build something on first access. A sweep that reports
    "0 findings" while never calling those is not a clean result, it is an untested one.

    The scratch Blueprint here is created BEFORE the dirty baseline is taken, so its own package being
    dirty does not read as a finding.
    """
    import time
    st = int(time.time() % 100000)
    ctx = {"class": "Actor", "cvar": "r.ScreenPercentage"}
    bid = M.call("create_blueprint", {"path": "/Game/_MifPurity/BP_%d" % st,
                                      "parentClass": "Actor"}).get("blueprintId")
    if not bid:
        return ctx
    ctx["blueprintId"] = bid
    graphs = M.call("list_graphs", {"blueprintId": bid}).get("graphs") or []
    g = next((x.get("graphId") for x in graphs if "EventGraph" in (x.get("name") or "")), None)
    if g:
        ctx["graphId"] = g
        n = M.call("add_branch", {"graphId": g, "x": 0, "y": 0})
        ctx["nodeGuid"] = n.get("nodeGuid") or (n.get("node") or {}).get("guid")
    M.call("add_variable", {"blueprintId": bid, "name": "PurityProbe", "type": "float"})
    M.call("compile", {"blueprintId": bid})
    return ctx


def special_payloads(ep, acc, ctx, assets):
    """Payloads for endpoints the generic by-class guesser cannot satisfy."""
    out = []
    g, bid = ctx.get("graphId"), ctx.get("blueprintId")
    node = ctx.get("nodeGuid")
    if "graphId" in acc and g:
        if "nodeGuid" in acc and node:
            out.append({"graphId": g, "nodeGuid": node})
        out.append({"graphId": g})
    if ep in ("describe_class", "list_class_properties") and "class" in acc:
        out.append({"class": ctx["class"]})
    if ep == "describe_endpoint" and "name" in acc:
        out.append({"name": "list_variables"})
    if ep == "get_cvar" and "name" in acc:
        out.append({"name": ctx["cvar"]})
    # resolve_struct takes a struct NAME, not a path - the by-class guesser was handing it a path and
    # getting a refusal every time.
    if ep == "resolve_struct" and "name" in acc:
        for cand in ("Vector", "Transform", "Rotator"):
            out.append({"name": cand})
    if ep in ("get_property", "list_object_properties", "describe_property",
              "diff_properties_vs_default") and bid:
        for key in ("object", "objectPath", "path", "target"):
            if key in acc:
                base = {key: bid}
                if "property" in acc and ep in ("get_property", "describe_property"):
                    # A property that exists on every Actor CDO. bReplicates was refused, which is why
                    # get_property stayed unexercised on the first run.
                    base["property"] = "PurityProbe"
                out.append(base)
                break
    if ep == "get_datatable_row" and assets.get("DataTable"):
        dt = assets["DataTable"][0]
        rows = M.call("read_datatable", {"path": dt}).get("rows") or []
        if rows:
            name = rows[0].get("Name") or rows[0].get("name")
            for key in ("path", "dataTable", "table"):
                if key in acc and name:
                    out.append({key: dt, "rowName": name})
                    break
    return out


def dirty_set():
    r = M.call("list_dirty_packages", {}, timeout=60)
    return {p.get("name") for p in (r.get("packages") or []) if p.get("name")}


def sample_assets():
    out = {}
    for cls, _ in BY_CLASS:
        r = M.call("find_assets", {"class": cls, "pathPrefix": "/Game/", "limit": 2})
        for a in (r.get("assets") or []):
            if a.get("path"):
                out.setdefault(cls, []).append(a["path"])
    return out


def main():
    ok, why = M.require_sdk_bridge(force=True)
    if not ok:
        print("refusing to run: %s" % why)
        return 2
    print("target: %s" % why)

    assets = sample_assets()
    print("samples: %s" % ", ".join("%s=%d" % (k, len(v)) for k, v in sorted(assets.items())))

    ctx = build_context()
    print("context: %s" % ", ".join(sorted(k for k in ctx if ctx.get(k))))

    names = [n for n in sorted(M.endpoint_names())
             if n.startswith(READ_PREFIXES) and n not in EXCLUDE and n not in M.DENY]
    print("%d endpoints are named like reads\n" % len(names))

    baseline = dirty_set()
    print("%d packages are already dirty; only NEW ones count\n" % len(baseline))

    exercised, attempted, findings = [], [], []
    known = set(baseline)

    for ep in names:
        acc = set(M.call("describe_endpoint", {"name": ep}).get("acceptedParams") or [])

        # Build the most likely-to-succeed payload: no args first, then a real asset by class.
        trials = [{}] + special_payloads(ep, acc, ctx, assets)
        for cls, params in BY_CLASS:
            for p in params:
                if p in acc and assets.get(cls):
                    trials.append({p: assets[cls][0]})
                    break

        got_one = False
        for payload in trials:
            try:
                r = M.call(ep, payload, timeout=60)
            except Exception:
                continue
            if not isinstance(r, dict):
                continue
            if r.get("ok") is True:
                got_one = True
            after = dirty_set()
            new = after - known
            if new:
                findings.append((ep, json.dumps(payload)[:70], sorted(new)))
                print("  DIRTIED  %-32s %s" % (ep, ", ".join(sorted(new))[:90]))
                print("           payload %s" % json.dumps(payload)[:90])
                known |= new          # do not re-report the same package for every later endpoint
            if got_one:
                break
        (exercised if got_one else attempted).append(ep)

    print("")
    print("=" * 78)
    print("READ PURITY")
    print("  exercised (at least one call returned ok)  %3d" % len(exercised))
    print("  attempted only (never got a valid call)    %3d" % len(attempted))
    if attempted:
        print("     %s" % ", ".join(attempted)[:400])
        print("     Those needed an argument this sweep could not guess. They are NOT evidence of")
        print("     purity - they were never exercised.")
    print("  endpoints that dirtied a package           %3d" % len(findings))
    for ep, payload, pkgs in findings:
        print("     %-30s %s" % (ep, ", ".join(pkgs)[:80]))
    if not findings:
        print("")
        print("  Nothing named like a read dirtied a package, across %d exercised endpoints."
              % len(exercised))
    print("=" * 78)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
