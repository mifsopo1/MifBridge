"""Turn audit_findings.jsonl into something a person can act on.

The raw file is append-only and repeats an endpoint once per probe, which is right for a crash-safe
log and wrong for reading. This groups by endpoint, ranks by severity, and - the part that matters -
separates findings that have been TRIAGED from findings that are still just observations.

A fuzzer's output is not a defect list. Of the first run's findings, `create_blueprint` "succeeded
against a nonexistent path" was the probe's own false positive (creation endpoints are supposed to),
`describe_package` was honest (it reports existsOnDisk:false), and `find_assets` returning nothing
for a prefix that matches nothing is a search working correctly. Three of the first six were noise.
Anything printed here still needs reading before it is believed.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FINDINGS = os.path.join(HERE, "audit_findings.jsonl")

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Findings already read and dispositioned, so a re-run does not re-raise settled questions.
TRIAGED = {
    ("GHOST_OK", "create_blueprint"):
        "NOT A BUG - a creation endpoint is supposed to accept a path that does not exist yet. "
        "Fuzzer false positive; the ghost probe now skips create_/add_/spawn_/import_ endpoints.",
    ("GHOST_OK", "describe_package"):
        "NOT A BUG - it reports existsOnDisk:false, which is the honest answer to 'describe this path'.",
    ("GHOST_OK", "find_assets"):
        "NOT A BUG - a search that matches nothing correctly returns nothing.",
    ("GHOST_OK", "check_overlaps"):
        "FIXED - a not-found actorPath silently became a whole-scene audit, because FindActorInWorld "
        "returns null both for 'not asked for' and 'not found'. Now refused, nothing tested.",
    ("GHOST_OK", "audit_unused"):
        "FIXED - scanned:0/unusedCount:0 for a prefix matching nothing now carries scanNote saying "
        "the prefix found nothing rather than reading as a clean bill of health.",
    ("GHOST_OK", "get_referencers"):
        "FIXED - count:0 for an unknown package read as 'unreferenced', which is the usual "
        "justification for deleting something. Now reports packageExists + existsNote.",
    ("GHOST_OK", "get_dependencies"):
        "FIXED - same as get_referencers.",
    ("CRASH", "describe_animation"):
        "MISATTRIBUTED - the guard refuses a 64KB string on this endpoint and the editor survives; "
        "reproduced by hand. The preceding HANGs mean the editor was already gone, killed by "
        "something earlier in the run. The fuzzer now re-checks liveness before classifying a hang.",
    ("HANG", "describe_animation"):
        "SAME CAUSE as the CRASH row above - an already-unresponsive editor, not this endpoint.",
    ("GHOST_OK", "invoke_editor_tab"):
        "NOT A BUG - the response says manager:'global', and `asset` is only consulted when "
        "manager='assetEditor'. The ghost value was correctly irrelevant to the call that ran.",
    ("HANG", "recipe_reset_and_loop"):
        "UNCONFIRMED - one observation, recorded by the OLD hang logic that did not retry. The "
        "handler has no unbounded loop (it iterates MacroGraphs, 24 entries) and most likely paid "
        "for a synchronous StandardMacros load. Needs a re-run under the confirming-retry logic "
        "before it is believed. Separately: it hardcodes StandardMacros for ForEachLoop, the same "
        "brittle pattern fixed in add_macro_instance - harmless today because ForEachLoop really is "
        "there, but it will rot the same way.",
    ("LEAK", "sculpt_landscape"):
        "NOT A LEAK - same pre-narrowed detector. The response is the module working as designed: "
        "'amount is only used by mode raise/lower; mode flatten would have ignored it'.",
    ("LEAK", "select_level_actors"):
        "NOT A LEAK - same. The response explicitly reports the ignored parameter: 'clear was given "
        "an object, which is not a boolean - it was IGNORED and the default was used instead'.",
    ("LEAK", "snap_actors_to_ground"):
        "NOT A LEAK - same. 'pass actorPaths[], or folder/labelContains, or all:true - refusing to "
        "guess the target set' is a good error, not a leak.",
    ("GHOST_OK", "trigger_cook"):
        "NOT A BUG - it answers executed:false with a note saying it is plan-only and does not cook "
        "from inside the editor. Honest.",
    ("GHOST_OK", "trace_ground"):
        "MINOR, not fixed - the ghost value went to ignoreActor, and an actor that does not exist is "
        "nothing to ignore, so the trace is still correct. Worth a warning naming the unresolved "
        "actor, but it cannot produce a wrong answer.",
    ("GHOST_OK", "select_level_actors"):
        "REAL but not yet fixed - selected:0 for paths that do not resolve reads the same as "
        "'these actors exist and none matched'. A caller doing select-then-operate-on-selection gets "
        "an empty selection and no reason. Should name the unresolved paths. Deferred so the six "
        "changesets already written get built and tested first.",
    ("LEAK", "list_editor_commands"):
        "NOT A LEAK - the detector matched '.cpp:' against this module's deliberate habit of citing "
        "the engine header that explains a limitation. Detector narrowed to markers that cannot "
        "appear on purpose (Assertion failed, EXCEPTION_, Stack:).",
}


def main():
    if not os.path.exists(FINDINGS):
        print("no findings file at", FINDINGS)
        return 0

    rows = []
    for ln in open(FINDINGS, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass

    by_ep = collections.defaultdict(list)
    for r in rows:
        by_ep[(r.get("kind"), r.get("endpoint"))].append(r)

    triaged, open_items = [], []
    for key, group in by_ep.items():
        (triaged if key in TRIAGED else open_items).append((key, group))

    def rank(item):
        (kind, ep), group = item
        sev = min(SEVERITY_ORDER.get(g.get("severity", "medium"), 2) for g in group)
        return (sev, kind, ep)

    open_items.sort(key=rank)
    triaged.sort(key=rank)

    print("=" * 78)
    print("MifBridge audit report - %d raw findings, %d distinct endpoint/kind pairs"
          % (len(rows), len(by_ep)))
    print("=" * 78)

    print("\nNEEDS READING (%d)" % len(open_items))
    if not open_items:
        print("  none")
    for (kind, ep), group in open_items:
        sev = min(group, key=lambda g: SEVERITY_ORDER.get(g.get("severity", "medium"), 2))
        print("\n  [%s] %s  -  %s  (%d observation%s)"
              % (sev.get("severity", "?").upper(), kind, ep, len(group), "" if len(group) == 1 else "s"))
        print("      %s" % group[0].get("detail", "")[:200])
        probes = sorted({g.get("probe", "?") for g in group})
        print("      probes: %s" % ", ".join(probes))
        sample = group[0].get("sample")
        if sample:
            print("      sample: %s" % sample[:190])

    print("\n\nALREADY TRIAGED (%d) - kept so a re-run does not re-raise them" % len(triaged))
    for (kind, ep), group in triaged:
        print("  %-11s %-24s %s" % (kind, ep, TRIAGED[(kind, ep)][:150]))

    print("\n" + "=" * 78)
    counts = collections.Counter(r.get("kind") for r in rows)
    print("raw counts: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
