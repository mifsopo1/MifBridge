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
    # 2026-09-01. BOTH TABLES ARE NEEDED and adding only one proves nothing: EXTRA_CLASSES decides
    # what gets SAMPLED, this decides what gets TRIED. Adding these two to EXTRA_CLASSES alone left
    # both endpoints still reported as never exercised, which is what said the first edit was
    # incomplete - the re-run, not the reasoning.
    ("PhysicsAsset", ("assetPath", "asset", "path")),
    ("PCGGraph", ("graph", "assetPath", "path")),
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


def scratch_fixtures():
    """Create the few scratch assets that let two-argument reads actually be exercised.

    WHY THIS EXISTS. The generic guesser sends ONE parameter at a time - {param: someAsset} - so an
    endpoint needing two required arguments can never succeed, and fifteen reads were reported as
    "attempted only" on every run. That is not a purity result, it is an untested gap, and the summary
    said so; it just never got closed.

    Scratch assets rather than shipped ones, deliberately. The only UserDefinedStructs under /Game/ are
    COOKED, and list_struct_members correctly refuses those ("its editor-only data was stripped"), so a
    fixture drawn from game content can never exercise it. Creating one is the only way to reach the
    branch. Everything made here is under /Game/_MifPure and is never saved.

    AND THAT LAST SENTENCE IS THE WHOLE CLEANUP STRATEGY, which is why describe_collection is NOT
    fed from here even though it is the obvious next one to add. Nothing in this function is
    cleaned up, and nothing needs to be: an unsaved asset under /Game/_MifPure dies with the
    editor. A COLLECTION does not. create_collection with the default shareType writes a real file
    under Content/Collections that outlives the process, so feeding describe_collection would mean
    this sweep leaving a permanent artifact behind on every single run - and it would accumulate,
    because the names are timestamped.

    Doing it properly needs a teardown phase this function does not have, and that teardown would
    have to run in a `finally` for the same reason audit_suite_teardown was written. That is a
    structural change, not a fixture, so it is filed rather than smuggled in here.
    """
    import time
    st = int(time.time() % 100000)
    fx = {}
    bp = "/Game/_MifPure/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bp, "parentClass": "Actor"}, timeout=90).get("blueprintId")
    if bid:
        M.call("add_variable", {"blueprintId": bid, "name": "PureVar", "type": "float"}, timeout=60)
        M.call("compile", {"blueprintId": bid}, timeout=90)
        short = bp.split("/")[-1]
        fx["cdo"] = "%s.Default__%s_C" % (bp, short)
        fx["blueprintId"] = bid
        # The ASSET path, not the CDO and not the blueprintId. check_consolidate_assets takes asset
        # path strings on both target and sources[] (MifBridgeAssetOps.cpp:1455-1490), and it was
        # the only read left that needed TWO real assets rather than one.
        fx["assetA"] = "%s.%s" % (bp, short)
    sp = "/Game/_MifPure/S_%d" % st
    c = M.call("create_struct", {"path": sp}, timeout=90)
    if c.get("ok"):
        M.call("add_struct_member", {"struct": c.get("structPath") or sp, "name": "PureMember",
                                     "type": "float"}, timeout=60)
        fx["struct"] = c.get("structPath") or sp
    # A DataTable with a real row. rows is an ARRAY of objects each carrying a Name - a dict keyed by
    # row name is refused. The write needs confirm, which scratch_confirm grants because every path in
    # the payload is under /Game/_Mif.
    import scratch_confirm as SC
    dtp = "/Game/_MifPure/DT_%d" % st
    if M.call("create_datatable", {"path": dtp, "rowStruct": "RichTextStyleRow"}, timeout=90).get("ok"):
        full = "%s.DT_%d" % (dtp, st)
        if SC.confirm_call("write_datatable_rows", {"path": full, "rows": [{"Name": "PureRow"}]}).get("ok"):
            fx["dataTable"] = full
    # An actor carrying a USplineComponent. spawn_actor_in_level needs the GENERATED CLASS path
    # (<path>.<Name>_C); the blueprint asset path alone is refused with "class not found".
    sbp = "/Game/_MifPure/BPSpline_%d" % st
    sbid = M.call("create_blueprint", {"path": sbp, "parentClass": "Actor"}, timeout=90).get("blueprintId")
    if sbid:
        M.call("add_component", {"blueprintId": sbid, "componentClass": "SplineComponent",
                                 "name": "PureSpline"}, timeout=90)
        M.call("compile", {"blueprintId": sbid}, timeout=90)
        sp = M.call("spawn_actor_in_level", {"actorClass": "%s.BPSpline_%d_C" % (sbp, st),
                                             "location": {"x": 0, "y": 0, "z": 1800},
                                             "label": "PureSpline_%d" % st}, timeout=90)
        ap = (sp.get("actor") or {}).get("actorPath")
        if ap:
            fx["splineActor"] = ap
        # The second asset for check_consolidate_assets. Same class as assetA deliberately -
        # consolidating across classes is a different question and not one a purity sweep should
        # be asking; this only needs the check to REACH its real work.
        fx["assetB"] = "%s.BPSpline_%d" % (sbp, st)
    acts = M.call("list_level_actors", {"limit": 3}, timeout=60).get("actors") or []
    if acts:
        fx["actorPath"] = acts[0].get("path") or acts[0].get("actorPath")
    # describe_water_body is addressed by a PLACED actor's path, not an asset - find_assets cannot
    # supply one, and list_water_bodies on a fresh/unpopulated level correctly reports zero. Spawning
    # one here is the only way to exercise it at all; found unexercised 2026-08-28 alongside the
    # /Game/-only pathPrefix bug above.
    wb = M.call("create_water_body", {"type": "River", "label": "PureWaterProbe_%d" % st,
                                      "x": 900000, "y": 900000, "z": 900000,
                                      "points": [{"x": 900000, "y": 900000, "z": 900000},
                                                 {"x": 900100, "y": 900000, "z": 900000}]}, timeout=90)
    if wb.get("actorPath"):
        fx["waterBodyActor"] = wb["actorPath"]
    # list_ik_rig/list_retarget_chain_mapping: this project has zero real IKRigDefinition/IKRetargeter
    # assets (confirmed 2026-08-28), so EXTRA_CLASSES sampling alone can never feed them. A bare,
    # unconfigured asset via create_asset is enough - both endpoints answer real (if empty) data
    # against one rather than refusing, which is all read-purity needs.
    ikr = M.call("create_asset", {"path": "/Game/_MifPure/IKR_%d" % st, "class": "IKRigDefinition"},
                timeout=60)
    if ikr.get("assetPath"):
        fx["ikRig"] = ikr["assetPath"]
    ikrt = M.call("create_asset", {"path": "/Game/_MifPure/IKRT_%d" % st, "class": "IKRetargeter"},
                 timeout=60)
    if ikrt.get("assetPath"):
        fx["ikRetargeter"] = ikrt["assetPath"]

    # A COLLECTION, AND THE ONLY FIXTURE HERE THAT NEEDS TEARING DOWN. Everything else above is an
    # unsaved asset under /Game/_MifPure and dies with the editor - that IS the cleanup strategy,
    # and it is why this function has no teardown at all. A collection does not play by it:
    # create_collection writes a real file under Content/Collections that outlives the process, and
    # the name is timestamped, so without a matching destroy this sweep would drop one artifact per
    # run and accumulate them forever. teardown_fixtures below removes it, from a finally.
    cname = "MifPure_%d" % st
    if M.call("create_collection", {"name": cname, "shareType": "local"},
              timeout=60).get("ok") is not False:
        fx["collection"] = cname
    return fx


def teardown_fixtures(fx):
    """Undo the fixtures that do NOT die with the editor. Returns what it removed, and what it could not.

    Only the collection qualifies today. It is separated from scratch_fixtures rather than folded
    into it because a teardown that runs alongside creation is a teardown that never runs when
    something in between raises - which is the whole reason audit_suite_teardown exists.
    """
    removed, failed = [], []
    cname = fx.get("collection")
    if cname:
        try:
            import scratch_confirm as SC
            # NOT confirm_call: check() wants an asset PATH and a collection has only a name, so it
            # refuses for want of something to look at. destroy_collection_if_scratch proves it the
            # other way, by the name.
            r = SC.destroy_collection_if_scratch(cname)
            (removed if r.get("ok") is not False else failed).append(cname)
        except Exception as exc:                      # noqa: BLE001 - reported, never swallowed
            failed.append("%s (%s)" % (cname, exc))
    return removed, failed


def special_payloads(ep, acc, ctx, assets):
    """Payloads for endpoints the generic by-class guesser cannot satisfy."""
    out = []
    # VERIFIED fixtures - each of these was confirmed to return ok against the live editor before
    # being written down, rather than guessed from the parameter name. The previous attempt used
    # {"property": "PurityProbe"}, which is not a property of anything, so get_property stayed
    # unexercised while looking like it had been covered.
    fx = ctx.get("_fixtures") or {}
    if fx.get("cdo") and ep in ("get_property", "describe_property"):
        out.append({"objectPath": fx["cdo"], "propertyPath": "PureVar"})
    if fx.get("struct") and ep == "list_struct_members":
        out.append({"struct": fx["struct"]})
    if fx.get("actorPath") and ep in ("get_actor_bounds", "get_level_actor"):
        out.append({"actorPath": fx["actorPath"]})
    if fx.get("collection") and ep == "describe_collection":
        out.append({"name": fx["collection"]})
    if fx.get("blueprintId") and ep == "get_inherited_component":
        out.append({"blueprintId": fx["blueprintId"], "name": "DefaultSceneRoot"})
    # check_consolidate_assets ASKS whether a consolidation would be safe and changes nothing - its
    # own guard says "nothing here changes anything, so there is nothing to confirm" - so a purity
    # sweep can call it freely. It just needed two assets, and the sweep had been building two
    # scratch blueprints all along without ever handing them over.
    if fx.get("assetA") and fx.get("assetB") and ep == "check_consolidate_assets":
        out.append({"target": fx["assetA"], "sources": [fx["assetB"]]})
    # These three take `path`, NOT a name matching their subject - tree/blackboard/rig were all tried
    # first and all refused.
    for cls, name in (("BehaviorTree", "describe_behavior_tree"),
                      ("BlackboardData", "list_blackboard_keys")):
        if ep == name and assets.get(cls):
            out.append({"path": assets[cls][0]})
    for cls, name, key in (("IKRigDefinition", "list_ik_rig", "rig"),
                           ("IKRetargeter", "list_retarget_chain_mapping", "retargeter"),
                           ("NiagaraSystem", "list_niagara_user_parameters", "system"),
                           # These four take `path`. They were added 2026-08-26 and were reported as
                           # "attempted only" until they were listed here - a new read is not covered
                           # by this sweep unless it is fed, and silence looks identical to purity.
                           ("NiagaraSystem", "describe_niagara_system", "path"),
                           ("NiagaraSystem", "list_niagara_emitters", "path"),
                           ("LevelSequence", "describe_level_sequence", "path"),
                           # Added 2026-08-28, same reason and same shape - list_sequence_bindings
                           # already had a real LevelSequence sampled (EXTRA_CLASSES), it was just
                           # never wired to receive one. list_input_mappings/describe_metasound needed
                           # their OWN classes added to EXTRA_CLASSES above as well as this wiring.
                           ("LevelSequence", "list_sequence_bindings", "path"),
                           ("InputMappingContext", "list_input_mappings", "path"),
                           ("MetaSoundSource", "describe_metasound", "path")):
        if ep == name and assets.get(cls):
            out.append({key: assets[cls][0]})
    if ep == "describe_gameplay_tag" and "tag" in acc:
        # Addressed by TAG STRING, not an asset path - find_assets cannot supply one. A tag actually
        # registered in this project answers the real branch; list_gameplay_tags is itself a read, so
        # asking it here does not cost this sweep a mutating call.
        tags = M.call("list_gameplay_tags", {"limit": 1}).get("tags") or []
        if tags and tags[0].get("tag"):
            out.append({"tag": tags[0]["tag"]})
    if ep == "describe_game_feature_plugin":
        # Addressed by plugin NAME, not asset path. MifBridge itself always exists and is deliberately
        # NOT a game feature, which exercises the answered-not-refused branch.
        out.append({"name": "MifBridge"})
    if fx.get("dataTable") and ep == "get_datatable_row":
        out.append({"path": fx["dataTable"], "rowName": "PureRow"})
    if fx.get("splineActor") and ep == "get_spline_points":
        out.append({"actorPath": fx["splineActor"]})
    if fx.get("waterBodyActor") and ep == "describe_water_body":
        out.append({"path": fx["waterBodyActor"]})
    # Fixture-based fallback for list_ik_rig/list_retarget_chain_mapping: tried FIRST against a real
    # sampled asset (the loop below), a scratch one only if this project has none of its own.
    if fx.get("ikRig") and ep == "list_ik_rig":
        out.append({"rig": fx["ikRig"]})
    if fx.get("ikRetargeter") and ep == "list_retarget_chain_mapping":
        out.append({"retargeter": fx["ikRetargeter"]})
    if ep == "get_cvar":
        out.append({"name": "r.ScreenPercentage"})
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



# ---------------------------------------------------------------------------
# WHY "attempted only" WAS NOT ONE BUCKET
# ---------------------------------------------------------------------------
# It printed "Those needed an argument this sweep could not guess", which was true of some of them
# and wrong about most. Three quite different things were being averaged into one line:
#
#   1. it needs a LIVE SESSION this sweep must not start - list_pie_actors and describe_live_widget
#      need PIE, and the autopilot rules forbid starting it. Nothing about the fixture is missing.
#   2. this PROJECT contains no asset of the class the endpoint reads. DDS2 has no PhysicsAsset, no
#      StateTree, no PCGGraph and no LevelSnapshot - so the sweep is correct and complete here, and
#      the same run against a project that HAS them would exercise them with no change at all.
#   3. the sweep could BUILD the fixture and does not. Only this one is a to-do.
#
# Averaging them made the whole bucket read as a backlog, so it was never worked: nine tenths of it
# was not actionable and the tenth was invisible. This is the same defect as the two polarity-
# opposite skip buckets in audit_detectors_fire, found the same night - one label over several
# causes, where the label happens to describe only one of them.
#
# CAUSE 2 IS MEASURED, NOT LISTED. Asking find_assets whether the class exists is the difference
# between a report and a guess, and it is the only way this stays true on a project that is not
# this one. A general UE5 tool cannot hardcode what DDS2 happens to contain.
NEEDS_LIVE_SESSION = {
    "list_pie_actors": "a running PIE session",
    "describe_live_widget": "a running PIE session with a real widget instance",
    "describe_livelink_subject": "a LiveLink subject pushed within the staleness window",
    "describe_ability_system": "an actor with an AbilitySystemComponent in the open level",
}

# endpoint -> the asset class it reads. Checked LIVE, so this stays honest on any project.
READS_ASSET_CLASS = {
    "describe_physics_asset": "PhysicsAsset",
    "describe_state_tree": "StateTree",
    "describe_pcg_graph": "PCGGraph",
    "describe_level_snapshot": "LevelSnapshot",
}


def classify_attempted(attempted):
    """(live, absent, todo) - and `absent` is confirmed by asking the registry, never assumed."""
    live, absent, todo = [], [], []
    for ep in attempted:
        if ep in NEEDS_LIVE_SESSION:
            live.append((ep, NEEDS_LIVE_SESSION[ep]))
            continue
        cls = READS_ASSET_CLASS.get(ep)
        if cls:
            r = M.call("find_assets", {"class": cls, "limit": 1}, timeout=60)
            found = r.get("count")
            if found is None:
                found = len(r.get("assets") or [])
            if not found:
                absent.append((ep, cls))
                continue
            # The class DOES exist here and the endpoint still never got a valid call - that is a
            # real gap in the sampler, not a property of the project, so it belongs in the to-do.
            todo.append((ep, "%s assets exist (%d) and it still never got a valid call" % (cls, found)))
            continue
        todo.append((ep, "no fixture rule - the generic by-class guesser could not satisfy it"))
    return live, absent, todo


def dirty_set():
    r = M.call("list_dirty_packages", {}, timeout=60)
    return {p.get("name") for p in (r.get("packages") or []) if p.get("name")}


# Classes the by-class table does not cover but that specific reads need. Without these,
# describe_behavior_tree, list_blackboard_keys, list_ik_rig, list_retarget_chain_mapping and
# list_niagara_user_parameters can never be exercised and are reported as "attempted only" forever.
# InputMappingContext and MetaSoundSource added 2026-08-28 for list_input_mappings/describe_metasound,
# found unexercised the same day as the /Game/-only pathPrefix bug above - both take a real asset of
# their own class and neither had one sampled at all before this.
# PhysicsAsset and PCGGraph added 2026-09-01. Both were sitting in the "attempted only" bucket
# under the blanket line "needed an argument this sweep could not guess" - and this project has 164
# PhysicsAssets and 11 PCGGraphs. Nothing was missing but a row here. They surfaced the moment that
# bucket was split by CAUSE and the sweep started ASKING the registry whether the class exists
# instead of leaving the reader to assume it did not.
EXTRA_CLASSES = ("BehaviorTree", "BlackboardData", "IKRigDefinition", "IKRetargeter",
                 "NiagaraSystem", "LevelSequence", "InputMappingContext", "MetaSoundSource",
                 "PhysicsAsset", "PCGGraph")


def sample_assets():
    # NO pathPrefix restriction. This used to hardcode "/Game/", which silently missed every real
    # asset living under a DIFFERENT mount point - DDS2Casino content in this project, and whatever
    # a different project's own content plugin is called. Found 2026-08-28: get_collision,
    # list_input_mappings, list_material_parameters, list_sequence_bindings and list_sockets all had
    # real, exercisable content sitting under /DDS2Casino/ the whole time and were reported
    # "attempted only" purely because the sampler never looked there. A general UE5 tool cannot
    # assume its own project's mount point name, so this now searches everywhere and lets the class
    # filter do the narrowing.
    out = {}
    for cls in EXTRA_CLASSES:
        r = M.call("find_assets", {"class": cls, "limit": 2})
        for a in (r.get("assets") or []):
            if a.get("path"):
                out.setdefault(cls, []).append(a["path"])
    for cls, _ in BY_CLASS:
        r = M.call("find_assets", {"class": cls, "limit": 2})
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
    ctx["_fixtures"] = scratch_fixtures()
    print("context: %s" % ", ".join(sorted(k for k in ctx if ctx.get(k))))
    try:
        return _sweep(ctx, assets)
    finally:
        # IN A FINALLY, for the reason audit_suite_teardown was written: a teardown that only runs
        # on the happy path is a teardown that does not run on the day it matters.
        gone, stuck = teardown_fixtures(ctx.get("_fixtures") or {})
        if gone:
            print("\nteardown: removed %s" % ", ".join(gone))
        if stuck:
            print("\nTEARDOWN FAILED for %s - these persist on disk and need removing by hand."
                  % ", ".join(stuck))


def _sweep(ctx, assets):

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
        live, absent, todo = classify_attempted(attempted)
        print("     NONE of these are evidence of purity - they were never exercised. But they are")
        print("     not one problem, and only the last group is work:")
        if live:
            print("")
            print("     needs a live session this sweep must not start:")
            for ep, why in live:
                print("       %-28s %s" % (ep, why))
        if absent:
            print("")
            print("     this PROJECT has no asset of the class they read - CONFIRMED against the")
            print("     registry just now, not assumed. On a project that has them, the same sweep")
            print("     exercises them with no change:")
            for ep, cls in absent:
                print("       %-28s no %s in this project" % (ep, cls))
        if todo:
            print("")
            print("     ACTIONABLE - the sweep could satisfy these and does not:")
            for ep, why in todo:
                print("       %-28s %s" % (ep, why))
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
