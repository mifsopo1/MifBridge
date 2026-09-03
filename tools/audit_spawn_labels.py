"""Every actor spawned into the EDITOR world must carry a Mif-prefixed label.

WHY THIS EXISTS, and it is not a style rule. `mifaudit.is_scratch_fixture` decides whether a level
object is somebody's scratch - so that a suite hunting for something to ADOPT skips it - and for a
level actor it has exactly two things to go on: a label starting with "Mif", or a path under
/Game/_Mif. A spawned actor's path is a level path (...PersistentLevel.BP_Foo_C_UAID_...), so the
LABEL is the only signal there is. An actor spawned without one, or with one that does not carry the
prefix, is indistinguishable from the project's own content, and anything doing unscoped discovery
can adopt it. That is the bug that made test_landscape_heightmap measure the wrong terrain on
2026-09-01, reporting a 1590uu error against a perfectly good endpoint.

is_scratch_fixture said so itself, in a comment, and then said the convention was holding: "Every
spawn_actor_in_level call in tools/ passes a label, and every one of those labels is Mif-prefixed ...
The hole is in the code path, not in the suite set." Measured across all 32 spawner call sites on
2026-09-03, that was FALSE at two of them - audit_read_purity.py spawned "PureSpline_%d" and
"PureWaterProbe_%d", and leaked both, because its teardown removed only the collection. The suites
were fine. The audit was not, and nothing was looking at audits because the eye goes to test_*.py.

So this is the check that the comment admitted did not exist: "the thing keeping it shut is a naming
convention, not a check."

AST, NOT REGEX. A payload dict spans lines and a regex over it guesses. Purely local - reads source
and talks to nothing.
"""
import argparse
import ast
import glob
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Endpoints that put an actor into the EDITOR world (World->SpawnActor against ActiveWorld(), not a
# PIE-scoped one), so what they make is NOT torn down when PIE stops - the list mifaudit's own
# cleanup_level_actor docstring names, plus spawn_actor_in_level itself.
SPAWNERS = ("spawn_actor_in_level", "duplicate_actors", "add_nav_volume",
            "create_water_body", "create_landscape")

LABEL_KEYS = ("label", "labelPrefix", "labelSuffix")
PREFIX = "Mif"

# A head that is itself a format placeholder tells us nothing about the real prefix: for
# `"%s_%d" % (label, i)` the leftmost literal is the FORMAT STRING, not the value. The first version
# of this reported that site as a violation, and it was not - the caller passes a Mif label in.
PLACEHOLDER_HEADS = ("%s", "%d", "%r", "%i", "{")

CLEARED = "SPAWN-LABEL-OK:"


def _lit(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def label_head(node):
    """Leftmost string literal of a label expression, WITHOUT dataflow.

    Deliberately shallow. Resolving `spawn(label, ...)` back to its callers needs interprocedural
    dataflow, and a dataflow narrowing is what put audit_fixture_adoption to sleep on 2026-09-03 -
    it broke one code shape while both its ground truth and its own plant stayed green. What cannot
    be resolved here is REPORTED as unresolved and never assumed safe.
    """
    lit = _lit(node)
    if lit is not None:
        return lit
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return label_head(node.left)
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            return _lit(value)
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        return label_head(node.func.value)
    return None


def _payload(call):
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        if isinstance(arg, ast.Dict):
            return arg
    return None


def scan_file(path):
    """(line, endpoint, kind, detail) per spawner call site. Kinds: NO_LABEL, NOT_MIF, UNRESOLVED."""
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []
    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        endpoint = None
        for arg in node.args:
            if _lit(arg) in SPAWNERS:
                endpoint = _lit(arg)
                break
        if endpoint is None:
            continue
        # An explicit clearance, the same escape hatch every other detector here carries. It must
        # say WHY on the same line, because an unexplained exemption is a bug somebody ignored.
        window = "\n".join(lines[max(0, node.lineno - 3):node.lineno + 2])
        if CLEARED in window:
            continue
        payload = _payload(node)
        if payload is None:
            out.append((node.lineno, endpoint, "UNRESOLVED", "no dict literal payload"))
            continue
        keys = {_lit(k): v for k, v in zip(payload.keys, payload.values) if _lit(k)}
        key = next((k for k in LABEL_KEYS if k in keys), None)
        if key is None:
            out.append((node.lineno, endpoint, "NO_LABEL", "no label key in the payload"))
            continue
        # labelSuffix belongs to duplicate_actors, which INHERITS the source actor's label and
        # appends - so the prefix comes from whatever was duplicated, not from here.
        if key == "labelSuffix":
            out.append((node.lineno, endpoint, "OK", "labelSuffix; prefix inherited from the source"))
            continue
        head = label_head(keys[key])
        if head is None or head.startswith(PLACEHOLDER_HEADS):
            out.append((node.lineno, endpoint, "UNRESOLVED", "label is computed; needs dataflow"))
        elif not head.startswith(PREFIX):
            out.append((node.lineno, endpoint, "NOT_MIF", "label starts %r" % head[:32]))
        else:
            out.append((node.lineno, endpoint, "OK", "label starts %r" % head[:32]))
    return out


def plant():
    """Planted spawns in a temp dir - never in tools/, which is the real corpus.

    EVERY SHAPE THE RULE CLAIMS, not one. A plant that exercises one arm of a three-arm rule proves
    a third of it, which is exactly how audit_fixture_adoption went to sleep the same day this was
    written. So: a missing label, a bare non-Mif literal, and a non-Mif head behind a `%` - plus two
    NEGATIVE controls, because a detector that flags everything is as useless as one that flags
    nothing.
    """
    tmp = tempfile.mkdtemp(prefix="mif_spawnlabel_plant_")
    try:
        victim = os.path.join(tmp, "test_planted_spawns.py")
        with io.open(victim, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(u'import mifaudit as M\n\n\n'
                     u'def no_label():\n'
                     u'    M.call("spawn_actor_in_level", {"actorClass": "StaticMeshActor"})\n'
                     u'\n\ndef bare_bad():\n'
                     u'    M.call("create_water_body", {"type": "Lake", "label": "ProbeLake"})\n'
                     u'\n\ndef formatted_bad():\n'
                     u'    M.call("add_nav_volume", {"label": "Probe_%d" % 1})\n'
                     u'\n\ndef good_literal():\n'
                     u'    M.call("spawn_actor_in_level", {"label": "MifGood"})\n'
                     u'\n\ndef good_formatted():\n'
                     u'    M.call("create_landscape", {"label": "MifLand_%d" % 2})\n')
        # Findings only. scan_file also returns an OK row per passing site so the headline can count
        # the whole corpus, and an OK row on a control line is the detector working, not a false
        # alarm - conflating the two made this plant go red against a correct detector.
        hits = [h for h in scan_file(victim) if h[2] != "OK"]
        kinds = sorted(k for _l, _e, k, _d in hits)
        want = ["NOT_MIF", "NOT_MIF", "NO_LABEL"]
        seen_all = kinds == sorted(want)
        # The negative controls must NOT be FLAGGED. Checked by line, so a detector that happens to
        # produce the right COUNT for the wrong reasons still fails here.
        good_lines = {n for n, line in enumerate(
            io.open(victim, encoding="utf-8").read().splitlines(), 1) if "MifGood" in line
            or "MifLand_" in line}
        false_alarms = [h for h in hits if h[0] in good_lines]
        ok = seen_all and not false_alarms
        print("PLANT  kinds seen: %s   expected: %s" % (kinds, sorted(want)))
        print("PLANT  false alarms on the two Mif-prefixed controls: %d" % len(false_alarms))
        print("")
        print("%s" % ("PLANT SEEN FOR THE RIGHT REASON - a clean run is worth something" if ok
                      else "PLANT NOT SEEN AS MINE - a clean run would mean NOTHING"))
        return 0 if ok else 1
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plant", action="store_true", help="self-test against a known instance")
    ap.add_argument("--all", action="store_true", help="also list the unresolved sites")
    args = ap.parse_args()

    if args.plant:
        return plant()

    real, unresolved, clean, sites = [], [], 0, 0
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        base = os.path.basename(path)
        if base == os.path.basename(__file__):
            continue
        for line, endpoint, kind, detail in scan_file(path):
            sites += 1
            if kind == "OK":
                clean += 1
            elif kind == "UNRESOLVED":
                unresolved.append((base, line, endpoint, kind, detail))
            else:
                real.append((base, line, endpoint, kind, detail))

    # EVERY SITE COUNTED, including the ones that pass. The first version of this counted only the
    # rows scan_file returned, which were the problems alone - so it announced "6 call sites
    # examined" against a corpus of 32 and the headline understated its own coverage by five sixths.
    # A count a reader acts on has to be over the whole corpus.
    print("spawner call sites: %d   Mif-prefixed: %d   findings: %d   unresolved: %d"
          % (sites, clean, len(real), len(unresolved)))
    print("")

    if real:
        print("ACTORS THE ADOPT-GUARD CANNOT SEE - each is spawned into the editor world with no")
        print("Mif-prefixed label, so mifaudit.is_scratch_fixture reads it as project content:")
        for base, line, endpoint, kind, detail in real:
            print("  %-34s %-22s %-9s %s" % ("%s:%d" % (base, line), endpoint, kind, detail))
        print("")
        print("Fix by giving the spawn a Mif-prefixed label, or clear the site with a")
        print("`# %s <reason>` comment beside it." % CLEARED)
    else:
        print("OK  every resolvable spawn into the editor world carries a Mif-prefixed label.")

    # REPORTED, NEVER FAILED ON. These need interprocedural dataflow to judge, and a check that goes
    # red on something it cannot decide is one somebody switches off - the same reason the release
    # badge is not in make_release.py --gates. They are a reading list, and the count is printed
    # even without --all so a growing blind spot cannot hide.
    if unresolved:
        print("")
        print("%d site(s) whose label is computed - NOT judged here, and not assumed safe:"
              % len(unresolved))
        if args.all:
            for base, line, endpoint, _k, detail in unresolved:
                print("  %-34s %-22s %s" % ("%s:%d" % (base, line), endpoint, detail))
        else:
            print("  (run with --all to list them)")

    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())
