"""Endpoint families that can WRITE a thing and not READ it back, or the reverse.

WHY THIS EXISTS. On 2026-08-31 add_widget_binding and remove_widget_binding turned out to have no
reader - months old, and found only because a refusal happened to advise `list_widget_bindings`, an
endpoint that did not exist. That is a terrible way to find a capability hole: it needed a wrong
message to exist first. Sweeping for the shape directly found a second one the same day, and that
one mattered more - add_game_framework_component_request hands back a requestId for a request that
stays LIVE, injecting a component into every current and future actor of a class, and nothing could
enumerate them. A lost id was a leaked request nothing could name.

THE PART THAT MAKES THIS USABLE is the verification, not the grouping. Grouping by noun reported 18
write-only families and 15 of them were wrong, because a reader usually lives under a DIFFERENT
noun: describe_animation reports sync markers, anim curves and anim notifies; list_nodes reports
pins; describe_physics_asset emits `bodies`; describe_mvvm_view emits `viewModels`. So the real
question is never "is there a list_<noun>" but "does any response CARRY this thing", which is
answerable from what the handlers emit. That check took 18 candidates to 1.

THE READ DIRECTION IS QUIETER and mostly correct. Six families read with no same-noun writer, and
five write under another verb (add_sequence_track for level_sequence, add_pcg_node for pcg_graph,
set_plugin_enabled for plugins). The sixth, StateTree, is deliberate and already documented in
FEATURE_PARITY_SPEC: StateTreeEditorData is editor-module-only, so it is correctly one-directional.
That is what ACCEPTED below is for - a family that is one-directional ON PURPOSE, with the reason
written down, is not a finding and must not be reported as one every run.

NOT A GATE. It exits 0 always. Every remaining hit needs a person to decide whether the missing
half is worth building, and a tool that failed the build over a judgement call would just get
switched off.

Usage:
    python tools/audit_family_asymmetry.py            # both directions
    python tools/audit_family_asymmetry.py --read     # only the read-with-no-write direction
"""
import collections
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PRIV = os.path.join(ROOT, "Source", "MifBridge", "Private")

WRITE = ("add_", "set_", "remove_", "create_", "delete_", "apply_", "assign_", "move_",
         "rename_", "duplicate_", "connect_", "disconnect_", "compile_", "clear_", "import_",
         "reset_", "snap_", "spawn_", "wrap_", "replace_", "toggle_", "enable_", "disable_",
         "bake_", "recompile_", "rebuild_", "insert_", "attach_", "detach_", "override_",
         "promote_", "start_", "stop_", "run_", "save_", "load_", "open_", "close_", "patch_",
         "paint_", "extrude_", "bevel_", "decimate_", "unwrap_", "join_", "separate_",
         "normalize_", "transfer_", "retarget_", "mirror_", "align_", "layout_", "focus_",
         "select_")
READ = ("list_", "get_", "describe_", "find_", "read_", "query_", "inspect_", "preview_",
        "verify_", "validate_", "audit_", "check_", "count_", "dump_", "analyze_", "analyse_",
        "disassemble_", "classify_", "measure_", "diff_", "search_", "resolve_", "peek_",
        "sample_", "trace_", "watch_", "capture_", "export_", "stat_", "summarize_")

# ONE-DIRECTIONAL ON PURPOSE, with the reason. An entry here is a decision somebody made and wrote
# down, not a backlog item - see FEATURE_PARITY_SPEC for the long form of each.
ACCEPTED = {
    "state_tree": "StateTreeEditorData/StateTreeEditorModule is editor-module-only; correctly "
                  "one-directional and recorded in FEATURE_PARITY_SPEC",
    "live_widget": "live widgets are RUNTIME objects read out of a running PIE session; writing "
                   "one is set_property against the instance, not a widget-authoring endpoint",
    "game_feature_plugin": "set_plugin_enabled is the write half, under the 'plugin' noun",
}

FIELD = re.compile(r'Set(?:Array|Object|String|Number|Bool)Field\(\s*TEXT\("([A-Za-z0-9_]+)"')


def endpoints():
    src = io.open(os.path.join(PRIV, "MifBridgeCommon.cpp"),
                  encoding="utf-8", errors="replace").read()
    return sorted(set(re.findall(r"MIF_BIND\(([a-z0-9_]+)\)", src)))


def response_fields():
    """Lower-cased on purpose. describe_mvvm_view emits `viewModels` and the family noun is
    mvvm_viewmodel, so a case-sensitive comparison called a readable family unreadable - the first
    run of this tool did exactly that."""
    out = set()
    for fn in sorted(os.listdir(PRIV)):
        if fn.endswith(".cpp"):
            out |= set(FIELD.findall(io.open(os.path.join(PRIV, fn),
                                             encoding="utf-8", errors="replace").read()))
    return {f.lower() for f in out}


def noun(name, first, second):
    for p in first + second:
        if name.startswith(p):
            return name[len(p):]
    return None


def singular(s):
    return s[:-1] if s.endswith("s") and not s.endswith("ss") else s


def spellings(n):
    """Every response-field name a family called n plausibly emits."""
    parts = n.split("_")
    lower = parts[0] + "".join(p.capitalize() for p in parts[1:])
    out = {lower, lower + "s", parts[-1], parts[-1] + "s"}
    for base in (lower, parts[-1]):
        if base.endswith("y"):
            out.add(base[:-1] + "ies")       # body -> bodies, the one the first version missed
        out.add(base + "es")
    return {s.lower() for s in out}


# A reader does not have to start with a read verb. pie_status is the read half of start_pie and
# stop_pie, and reads as a noun-first name rather than list_/get_. Missing these made `pie` look
# write-only on the first run.
READ_SUFFIX = ("_status", "_info", "_statistics", "_state", "_report")


def families(names):
    fam = collections.defaultdict(lambda: {"w": [], "r": []})
    for n in names:
        nn = noun(n, READ, WRITE)
        if nn is not None:
            fam[singular(nn)]["r" if n.startswith(READ) else "w"].append(n)
            continue
        for suf in READ_SUFFIX:
            if n.endswith(suf):
                fam[singular(n[: -len(suf)])]["r"].append(n)
                break
    return fam



# ---------------------------------------------------------------------------- the addon half
# THE PORT SPLITS IN TWO AND ONLY ONE HALF WORKS. Established 2026-09-05 by building both.
#
#   WRITERS WITH NO READER    - what this tool asks of the C++. Ports cleanly to the addon once the
#                               match is against the VALUES a reader emits rather than its field
#                               KEYS. 4 raw candidates, 0 survive, and every suppression is
#                               inspectable: driver -> "drivers", mesh -> "MESH", node_group ->
#                               "nodeGroup"/"node_groups", texture -> "textures".
#
#   CONSUMERS WITH NO CREATOR - the direction that actually paid four times here (nothing could
#                               create a COLLECTION, an EMPTY, a VERTEX GROUP while ops required
#                               them). NOT shipped, and not for want of trying: keyed on parameter
#                               names it produces 161 candidates, nearly all option flags - align,
#                               angle, color, dry_run. The signal needed is "this parameter names a
#                               DATABLOCK", which a name alone does not carry. That is worse than
#                               the 13 false findings the first port was reverted for, so it is left
#                               out on the same rule.
#
# So the addon is no longer wholly unread - it is read in one direction, and REACH says which.
ADDON_DIR = os.path.join(HERE, "blender-addon", "MifBlender")


def addon_families():
    """(families, source-text) for the addon, or (None, None) if it cannot be read."""
    try:
        sys.path.insert(0, HERE)
        import parity_check as PC
        table = PC.load_addon_ops([])
    except Exception:                                  # noqa: BLE001 - absent addon is not a finding
        return None, None
    if not table:
        return None, None
    src = ""
    try:
        for fn in sorted(os.listdir(ADDON_DIR)):
            if fn.endswith(".py"):
                src += io.open(os.path.join(ADDON_DIR, fn), encoding="utf-8",
                               errors="replace").read()
    except OSError:
        return None, None
    return families(sorted(table.keys())), src


def emitted_spellings(fam, src):
    """The spellings of `fam` that some addon op can EMIT as a quoted string value.

    THE FIELD-KEY TEST CANNOT WORK HERE, which is why the first port failed. The addon's readers are
    generic - physics_info reads cloth AND collision AND rigid bodies - so the thing they report
    appears as a VALUE ("CLOTH" in a modifier type) rather than as a response-field key. Matching
    values is what takes this from 13 nearly-all-false findings to 0 explainable ones.

    A plain substring test for the quoted token, deliberately: a regex here needs escaped quote
    classes and this repo has lost time to exactly that escaping more than once.
    """
    parts = fam.split("_")
    camel = parts[0] + "".join(x.capitalize() for x in parts[1:])
    base = {fam, camel, parts[-1]}
    cand = set()
    for b in base:
        cand |= {b, b + "s", b.upper(), b.capitalize()}
        if b.endswith("y"):
            cand.add(b[:-1] + "ies")
    hits = []
    for c in sorted(cand):
        if ('"' + c + '"') in src or ("'" + c + "'") in src:
            hits.append(c)
    return hits


def addon_report():
    """Print the addon half. Returns (raw, survivors) or (None, None) when the addon is unreadable."""
    fam, src = addon_families()
    if fam is None:
        return None, None
    raw = [(f, d["w"]) for f, d in sorted(fam.items()) if len(d["w"]) >= 2 and not d["r"]]
    survivors = []
    for f, w in raw:
        hits = emitted_spellings(f, src)
        if not hits:
            survivors.append((f, w))
            print("  ADDON  %-22s %d writer(s), no reader and nothing emits the noun: %s"
                  % (f, len(w), ", ".join(w[:4])))
    return raw, survivors


def main():
    only_read = "--read" in sys.argv
    names = endpoints()
    fam = families(names)
    fields = response_fields()
    print("%d endpoints, %d families, %d distinct response fields"
          % (len(names), len(fam), len(fields)))

    findings = 0
    if not only_read:
        print("")
        print("WRITE WITH NO READ - the thing can be changed and never read back:")
        shown = 0
        for k, v in sorted(fam.items()):
            if not v["w"] or v["r"] or len(v["w"]) < 2 or k in ACCEPTED:
                continue
            hits = sorted(f for f in fields if f in spellings(k))
            if hits:
                continue          # a reader exists under a different noun - not a finding
            shown += 1
            findings += 1
            print("  %-34s %s" % (k, ", ".join(sorted(v["w"]))))
        if not shown:
            print("  none - every family with two or more writers is readable somewhere")

    print("")
    print("READ WITH NO WRITE - the thing can be inspected and never changed:")
    shown = 0
    for k, v in sorted(fam.items()):
        if not v["r"] or v["w"] or len(v["r"]) < 2 or k in ACCEPTED:
            continue
        # A WRITER UNDER ANOTHER VERB COUNTS, and matching it needs token overlap rather than
        # substring: level_sequence is written by add_sequence_track and pcg_graph by add_pcg_node,
        # and neither noun CONTAINS the family name. Comparing token sets catches both. The first
        # version used `in` and reported both families as read-only when they are not.
        my_tokens = set(k.split("_")) - {"level", "asset", "data"}
        if any(my_tokens & (set(other.split("_")) - {"level", "asset", "data"})
               for other in (n[n.index("_") + 1:] for n in names
                             if n.startswith(WRITE) and "_" in n)):
            continue
        shown += 1
        findings += 1
        print("  %-34s %s" % (k, ", ".join(sorted(v["r"]))))
    if not shown:
        print("  none beyond the accepted ones")

    if ACCEPTED:
        print("")
        print("ACCEPTED - one-directional on purpose:")
        for k in sorted(ACCEPTED):
            print("  %-22s %s" % (k, ACCEPTED[k]))

    print("")
    # REACH, NOT GREEN. Every audit here prints how much of its surface it can judge, and this one
    # printed a total and a verdict with no denominator: "453 endpoints ... none - every family with
    # two or more writers is readable somewhere" is true of the C++ and says nothing about the
    # addon, which this tool cannot see at all. A reader gets "none" and concludes the product is
    # clean.
    #
    # The count is IMPORTED rather than recounted. A second implementation of "how many ops are
    # there" is precisely what let one prose line read "20 ops" long after it was 68.
    ops = 0
    try:
        sys.path.insert(0, HERE)
        from make_release import blender_op_count
        ops = blender_op_count()
    except Exception:
        pass
    print("")
    print("THE ADDON HALF, one direction of it:")
    a_raw, a_surv = addon_report()
    if a_raw is None:
        print("  the addon could not be read - no verdict about it either way")
    else:
        print("  %d family/families with 2+ writers and no reader in the family; %d survive after"
              % (len(a_raw), len(a_surv)))
        print("  suppressing the ones a reader can EMIT as a value.")

    print("")
    print("REACH - what this audit can and cannot judge:")
    print("  covered      %d UE endpoints, read from MIF_BIND in the C++" % len(names))
    if ops:
        print("  PARTLY       %d Blender addon ops - read for writers-without-a-reader, NOT read"
              % ops)
    else:
        print("  PARTLY       the Blender addon - read for writers-without-a-reader, NOT read")
    print("               for the CONSUMER-WITHOUT-CREATOR direction. The writer-without-reader")
    print("               direction above now DOES cover them.")
    print("  THE DIRECTION STILL MISSING IS THE ONE THAT PAID, and that is the honest shape of this")
    print("  gap. All four hand-found cases on 2026-09-03/04 were consumers with no creator -")
    print("  nothing could create a COLLECTION, an EMPTY, a CURVE, an ARMATURE, a VERTEX GROUP or a")
    print("  SHAPE KEY while ops required every one. Keying that on parameter NAMES was built and")
    print("  measured on 2026-09-05: 161 candidates, nearly all option flags (align, angle, color,")
    print("  dry_run). The signal needed is 'this parameter names a DATABLOCK', which a name does")
    print("  not carry - so it is left out on the same rule that reverted the first port.")
    print("")
    print("A hit is a READING LIST entry, not a defect. Deciding whether the missing half is worth")
    print("building is a judgement call, which is why this exits 0 either way.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
