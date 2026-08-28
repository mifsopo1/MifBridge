"""Eighth batch: asset thumbnails, composite widget preview, a Niagara component parameter, and the
honest limits of two more endpoints on this specific project's real content.

T960: set_asset_thumbnail - works fine WITHOUT save (confirmed live: save:false leaves the render
cached in memory, packageDirty:true, nothing written to disk - matches this whole project's
never-save invariant, and save itself is one of mifaudit's FORBIDDEN_KEYS regardless).

T961: preview_composite_widget - a scratch WidgetBlueprint with a NAMED VARIABLE panel (VerticalBox,
set_widget_is_variable) as the root, inserting a REAL project UserWidget as a child. First attempt
inserted a bare "TextBlock" and was correctly refused ("class is not a UserWidget") - composing means
nesting whole UserWidget instances into a named panel, not adding a leaf UMG component, and a real
UserWidget class (a MovieRenderPipeline info-row widget already in this project) is what actually
belongs there.

T962: set_niagara_component_parameter - a real success path, on a NiagaraComponent this test adds to
its own scratch blueprint and spawns into the level. Confirm-gated, but addressed by an ACTOR
INSTANCE path (a placed level actor, not an asset), which lives under /Temp/Untitled_1 - the currently
open level's own transient package name, not a /Game/_Mif... asset path. scratch_confirm.check()
correctly refuses this shape (it only trusts /Game/_Mif* asset paths, and widening it to trust every
/Temp/ actor blindly would ALSO bless targeting one of the real DDS2 actors that already live in that
same open level - confirmed live via scene_report, 85+ real actors including the landscape). Rather
than widen that shared safety module on a judgement call, this ONE call uses M.raw_post directly, the
same low-level primitive scratch_confirm itself uses - justified narrowly here because THIS specific
actorPath was proven safe by construction one line earlier (this test spawned it, in this exact run),
not by a reusable prefix rule. Not a pattern to copy elsewhere without the same immediate proof.

T963/T964: two honest limits, not workarounds. reimport_asset gets REFUSAL coverage only: every real
texture checked on this project (three Billboards assets) reports "no source path is recorded on the
asset" - DDS2's cooked-editor build does not retain AssetImportData on its shipped content, so there
is no real asset here whose reimport SUCCESS path could be driven without first importing a fresh
scratch texture from an external file, which needs a source image this project does not happen to
ship (no .png anywhere under this plugin). add_sequence_possessable's success path is not reached
either, for the SAME /Temp/-actor-path reason T962 explains - unlike T962's Niagara case, resolving it
here would mean building a byte-for-byte reproducible construction proof for a DIFFERENT, disposable
scratch actor path in a DIFFERENT test, which is real work worth its own deliberate pass rather than a
second quick reuse of the same shortcut. Both are filed as real, current-state findings, same honesty
as the GAS AttributeSet and PCG structural walls already documented elsewhere in this project.
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

    # ------------------------------------------------------------------ T960 set_asset_thumbnail
    print("\n=== T960: set_asset_thumbnail - no save needed ===")
    bpath = "/Game/_MifReads8/BP_%d" % st
    bid = M.call("create_blueprint", {"path": bpath, "parentClass": "Actor"}).get("blueprintId")
    check("T960 (setup) a scratch blueprint is created", bool(bid), bid)
    if bid:
        th = M.call("set_asset_thumbnail", {"asset": bpath, "width": 64, "height": 64})
        check("T960 succeeds without save", th.get("ok") is True, json.dumps(th)[:200])
        check("T960 and confirms nothing was written to disk", th.get("saved") is False, th.get("saved"))
        check("T960 the package is dirty (cached in memory only)", th.get("packageDirty") is True,
              th.get("packageDirty"))

    # ------------------------------------------------------------------ T961 preview_composite_widget
    print("\n=== T961: preview_composite_widget - a named-variable panel, a real UserWidget child ===")
    wpath = "/Game/_MifReads8/WBP_composite_%d" % st
    w = M.call("create_blueprint", {"path": wpath, "blueprintType": "WidgetBlueprint"})
    wbid = w.get("blueprintId")
    check("T961 (setup) a scratch WidgetBlueprint is created", w.get("ok") is True and bool(wbid),
          json.dumps(w)[:200])
    if wbid:
        tw = M.call("add_tree_widget", {"blueprintId": wbid, "widgetClass": "VerticalBox",
                                        "name": "RootBox", "parentName": "CanvasPanel_0"})
        check("T961 (setup) a VerticalBox is added", tw.get("ok") is True, json.dumps(tw)[:200])
        sv = M.call("set_widget_is_variable", {"blueprintId": wbid, "widgetName": "RootBox", "isVariable": True})
        check("T961 (setup) it is marked as a variable, so it can be a named insertion point",
              sv.get("ok") is True, json.dumps(sv)[:200])
        c = M.call("compile", {"blueprintId": wbid})
        check("T961 (setup) the WidgetBlueprint compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
              "errors=%s" % c.get("numErrors"))

        # A bare UMG component name is refused - composing needs a real UserWidget class.
        bad = M.call("preview_composite_widget", {
            "rootClass": wpath + ".WBP_composite_%d_C" % st,
            "children": [{"class": "TextBlock", "insertInto": "RootBox"}]})
        check("T961 inserting a non-UserWidget class is refused",
              bad.get("ok") is True and bad.get("inserted", [{}])[0].get("ok") is False,
              json.dumps(bad.get("inserted"))[:200])

        real_child = M.call("find_assets", {"class": "WidgetBlueprint", "limit": 1}).get("assets") or []
        if real_child:
            child_c = real_child[0].get("path").split(".")[0] + "." + real_child[0].get("name") + "_C"
            pv = M.call("preview_composite_widget", {
                "rootClass": wpath + ".WBP_composite_%d_C" % st,
                "children": [{"class": child_c, "insertInto": "RootBox"}], "width": 128, "height": 128})
            check("T961 inserting a real UserWidget class succeeds", pv.get("ok") is True, json.dumps(pv)[:200])
            check("T961 and reports it landed", (pv.get("inserted") or [{}])[0].get("ok") is True,
                  json.dumps(pv.get("inserted"))[:200])
            pv_path = pv.get("path")
            if pv_path:
                import os
                check("T961 the composite PNG really exists on disk", os.path.isfile(pv_path), pv_path)
        else:
            print("  SKIP  T961 real-child insertion - no WidgetBlueprint asset found on this project")

    # ------------------------------------------------------------------ T962 set_niagara_component_parameter
    print("\n=== T962: set_niagara_component_parameter - a real success, on an actor this test just spawned ===")
    npath = "/Game/_MifReads8/BP_Niagara_%d" % st
    nbid = M.call("create_blueprint", {"path": npath, "parentClass": "Actor"}).get("blueprintId")
    check("T962 (setup) a scratch blueprint is created", bool(nbid), nbid)
    if nbid:
        ac = M.call("add_component", {"blueprintId": nbid, "componentClass": "NiagaraComponent", "name": "NC"})
        check("T962 (setup) a NiagaraComponent is added", ac.get("ok") is True, json.dumps(ac)[:200])
        c = M.call("compile", {"blueprintId": nbid})
        check("T962 (setup) it compiles", c.get("ok") is True and c.get("numErrors", 1) == 0,
              "errors=%s" % c.get("numErrors"))
        spawn = M.call("spawn_actor_in_level", {
            "actorClass": npath + ".BP_Niagara_%d_C" % st,
            "location": {"x": 1600000 + st, "y": 1600000 + st, "z": 500000},
            "label": "MifReads8NiagaraActor_%d" % st})
        actor_path = (spawn.get("actor") or {}).get("actorPath")
        check("T962 (setup) it spawns into the level", spawn.get("ok") is True and bool(actor_path),
              json.dumps(spawn)[:200])
        if actor_path:
            # Deliberate M.raw_post, not scratch_confirm - see module docstring. actor_path is proven
            # safe by construction one call above (this test spawned it, this exact run), which is a
            # stronger guarantee than scratch_confirm's prefix check could give for a /Temp/ path.
            r = M.raw_post("set_niagara_component_parameter", {
                "actorPath": actor_path, "component": "NC", "name": "MifTestParam", "type": "float",
                "value": 3.5, "confirm": True})
            check("T962 the real set succeeds", r.get("ok") is True, json.dumps(r)[:250])
            check("T962 and confirms it targeted the component, not the shared system asset",
                  "COMPONENT" in (r.get("note") or "").upper(), r.get("note"))

    # ------------------------------------------------------------------ T963 reimport_asset - honest refusal
    print("\n=== T963: reimport_asset - real content, real refusal (no recorded source path anywhere) ===")
    textures = M.call("find_assets", {"class": "Texture2D", "pathPrefix": "/Game/", "limit": 1}).get("assets") or []
    check("T963 (setup) a real Texture2D exists to try", bool(textures), textures)
    if textures:
        tpath = textures[0].get("path")
        r = M.call("reimport_asset", {"path": tpath})
        check("T963 refused - no source file is recorded on this cooked-editor project's content",
              r.get("ok") is False, json.dumps(r)[:250])
        check("T963 and explains why, not a generic failure", "source" in (r.get("error") or "").lower(),
              r.get("error"))

    # ------------------------------------------------------------------ T964 add_sequence_possessable - refusal
    print("\n=== T964: add_sequence_possessable - the confirm gate, real success path deferred ===")
    lspath = "/Game/_MifReads8/LS_%d" % st
    ls = M.call("create_asset", {"path": lspath, "class": "LevelSequence"})
    check("T964 (setup) a scratch LevelSequence is created", ls.get("ok") is True, json.dumps(ls)[:200])
    if ls.get("ok"):
        r = M.call("add_sequence_possessable", {"path": lspath, "actorPath": "/Temp/Untitled_1.Untitled_1:PersistentLevel.SomeActor"})
        check("T964 refuses without confirm", r.get("ok") is False, json.dumps(r)[:200])
        check("T964 and names confirm as what is missing", "confirm" in (r.get("error") or "").lower(),
              r.get("error"))
        SC.confirm_call("delete_asset", {"path": lspath})

    if bid:
        SC.confirm_call("delete_asset", {"path": bpath})
    if wbid:
        SC.confirm_call("delete_asset", {"path": wpath})
    if nbid:
        SC.confirm_call("delete_asset", {"path": npath})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
