"""find_assets and the cooked-blueprint class trap.

WHAT THE TRAP IS. On a COOKED project a Blueprint asset is registered under its GENERATED CLASS -
BlueprintGeneratedClass, WidgetBlueprintGeneratedClass, AnimBlueprintGeneratedClass - and not as
Blueprint at all. Asking for the obvious class name does not fail. It returns a SMALL NUMBER, with
ok:true, and nothing to suggest the answer was anywhere else.

Measured on DDS2 when this was written:

    /Game/Blueprints   class:"Blueprint"                 26
    /Game/Blueprints   class:"BlueprintGeneratedClass"  915

Under 3%, delivered confidently. And the few that DO come back are mostly assets the bridge created
itself in the session, because anything newly authored is uncooked - so the caller gets an answer
composed almost entirely of their own scratch.

HOW IT WAS FOUND, which is the part worth keeping. Trying to settle whether DDS2 has any Chaos
vehicles, `find_assets {class:"Blueprint", nameContains:"VehicleBoat"}` returned 0. The same query
against BlueprintGeneratedClass returned 15. The 0 had already been written into the spec as evidence
that a plugin dependency had nothing to operate on.

bRecursiveClasses does NOT rescue this, and that is worth stating because it looks like it should:
UBlueprint and UBlueprintGeneratedClass are different hierarchies, not parent and child.

WHAT THE FIX IS. find_assets re-runs the count against the generated-class spelling and reports
generatedClassCount plus cookedClassNote - but ONLY when it is genuinely bigger, so an uncooked
project pays one extra registry query and hears nothing.

SKIPS CLEANLY on an uncooked project, where there is no trap to demonstrate.

Usage:
    python tools/test_cooked_class_trap.py

Exit codes:
    0  ran and passed
    1  ran and something failed
    2  SKIPPED - nothing cooked here, so the trap cannot be exercised
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)[:240]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # PROJECT-WIDE IS THE WRONG COMPARISON, and this guard got it wrong first. DDS2 reports 1752
    # Blueprint against 1475 BlueprintGeneratedClass, because the plain count sweeps up every
    # uncooked asset in the project plus everything the suites have authored this session - so the
    # trap looks absent while /Game/Blueprints alone is 26 against 915.
    #
    # So the guard hunts for a family where the difference is visible WITHOUT a project-specific
    # path, and skips only if none of the three shows it anywhere.
    family = None
    for fam in ("WidgetBlueprint", "AnimBlueprint", "Blueprint"):
        a = M.call("find_assets", {"class": fam, "limit": 1})
        b = M.call("find_assets", {"class": fam + "GeneratedClass", "limit": 1})
        print("  %-18s plain=%-6s generated=%s" % (fam, a.get("count"), b.get("count")))
        if family is None and (b.get("count") or 0) > (a.get("count") or 0):
            family = fam
    if family is None:
        print("")
        print("SKIPPED - nothing was verified.")
        print("  No blueprint family here has more generated-class assets than plain ones, so this")
        print("  project is not cooked in the way the trap needs. Nothing to demonstrate.")
        return 2
    print("demonstrating with: %s" % family)

    print("")
    print("=== T750: the misleading query now carries its own correction ===")
    r = M.call("find_assets", {"class": family, "limit": 3})
    check("T750 it still answers", r.get("ok") is True, json.dumps(r)[:200])
    check("T750 and reports the generated-class count beside it",
          isinstance(r.get("generatedClassCount"), (int, float)),
          "no generatedClassCount - the old binary is loaded, or the note did not fire")
    check("T750 which is HIGHER than what the caller asked for",
          (r.get("generatedClassCount") or 0) > (r.get("count") or 0),
          "count=%r generatedClassCount=%r" % (r.get("count"), r.get("generatedClassCount")))
    note = str(r.get("cookedClassNote") or "")
    check("T750 a note explains why", bool(note), "no cookedClassNote")
    check("T750 it names the class to use instead",
          (family + "GeneratedClass") in note, note[:200])
    check("T750 and says the few returned are the UNCOOKED ones",
          "uncooked" in note, note[:200])

    print("")
    print("=== T750b: the query that actually put a wrong 0 into the spec ===")
    # DDS2-specific and therefore conditional. This is the real story: find_assets for
    # class:"Blueprint" nameContains:"VehicleBoat" returned 0, and that 0 was written down as
    # evidence that a plugin dependency had nothing to operate on. The truth was 15.
    r = M.call("find_assets", {"class": "Blueprint", "nameContains": "VehicleBoat", "limit": 3})
    if (r.get("generatedClassCount") or 0) > 0:
        check("T750b the 0 now arrives with the real number attached",
              r.get("count") == 0 and (r.get("generatedClassCount") or 0) > 0,
              "count=%r generatedClassCount=%r" % (r.get("count"), r.get("generatedClassCount")))
        check("T750b and a note rather than a bare zero", bool(r.get("cookedClassNote")),
              json.dumps(r)[:200])
    else:
        print("  (no VehicleBoat assets here - this project is not DDS2, not exercised)")

    print("")
    print("=== T751: no note when the caller already asked correctly ===")
    # A note on a correct query is noise, and noise is how a warning stops being read.
    r = M.call("find_assets", {"class": family + "GeneratedClass", "limit": 3})
    check("T751 the right query finds them", (r.get("count") or 0) > 0, r.get("count"))
    check("T751 and carries no note", "cookedClassNote" not in r, r.get("cookedClassNote"))
    check("T751 nor a generatedClassCount", "generatedClassCount" not in r,
          r.get("generatedClassCount"))

    print("")
    print("=== T752: unrelated classes are left alone ===")
    r = M.call("find_assets", {"class": "StaticMesh", "limit": 1})
    check("T752 StaticMesh is untouched", "cookedClassNote" not in r, r.get("cookedClassNote"))
    r = M.call("find_assets", {"class": "DataTable", "limit": 1})
    check("T752 DataTable is untouched", "cookedClassNote" not in r, r.get("cookedClassNote"))

    print("")
    print("=== T753: the note respects the filters it was given ===")
    # The re-count must apply nameContains and pathPrefix too, or it would report a project-wide
    # number against a narrow query and look like nonsense.
    r = M.call("find_assets", {"class": family, "pathPrefix": "/Game", "limit": 1})
    alt = M.call("find_assets", {"class": family + "GeneratedClass",
                                 "pathPrefix": "/Game", "limit": 1})
    if (alt.get("count") or 0) > (r.get("count") or 0):
        check("T753 the reported count matches the same query on the other class",
              r.get("generatedClassCount") == alt.get("count"),
              "reported=%r  actual=%r - the re-count ignored pathPrefix"
              % (r.get("generatedClassCount"), alt.get("count")))
    else:
        print("  (/Game shows no difference for %s - not exercised)" % family)

    print("")
    print("=== T754: the widget and anim families too ===")
    for fam in ("WidgetBlueprint", "AnimBlueprint"):
        r = M.call("find_assets", {"class": fam, "limit": 1})
        g = M.call("find_assets", {"class": fam + "GeneratedClass", "limit": 1})
        if (g.get("count") or 0) > (r.get("count") or 0):
            check("T754 %s is covered" % fam, "cookedClassNote" in r,
                  "%s=%s %sGeneratedClass=%s but no note"
                  % (fam, r.get("count"), fam, g.get("count")))
        else:
            print("  (%s: %s vs %s - no trap here, not exercised)"
                  % (fam, r.get("count"), g.get("count")))

    print("")
    print("=== T755: list_blueprints had the SAME bug, and it mattered more ===")
    # find_assets is a general query; list_blueprints is THE discovery endpoint. It queried
    # UBlueprint alone and returned 1818 on DDS2 - a large, entirely plausible number - while
    # filter:"VehicleBoat" returned 0 against 15 that exist. 1409 blueprints were invisible to any
    # agent that started here, and nothing in the answer suggested it.
    r = M.call("list_blueprints", {})
    check("T755 it reports how many are cooked", isinstance(r.get("cookedCount"), (int, float)),
          "no cookedCount - the old binary is loaded")
    check("T755 and cooked ones are actually listed", (r.get("cookedCount") or 0) > 0,
          "cookedCount=%r on a project with cooked blueprints" % (r.get("cookedCount"),))
    check("T755 the total exceeds the uncooked-only count it used to return",
          (r.get("count") or 0) > (r.get("uncookedRegistered") or 0),
          "count=%r uncookedRegistered=%r" % (r.get("count"), r.get("uncookedRegistered")))
    note = str(r.get("cookedNote") or "")
    check("T755 a note says what cooked COSTS you", "strip Blueprint graphs" in note, note[:200])
    check("T755 and points at the way to read them anyway",
          "mif.kr.Reconstruct" in note and "create_editable_child" in note, note[:240])

    rows = r.get("blueprints") or []
    check("T755 every row carries a cooked flag",
          all("cooked" in b for b in rows[:200]) if rows else False,
          "some rows have no cooked field")

    # THE FLAG MUST BE RIGHT, not merely present. The check above passed while every cooked WIDGET
    # and ANIM blueprint was labelled cooked:false - the rows were all there and the label was wrong,
    # because the first implementation compared AssetClassPath against UBlueprintGeneratedClass
    # exactly and WidgetBlueprintGeneratedClass is a subclass living in /Script/UMG.
    by_pkg = {b.get("package"): b for b in rows}
    for gen_cls in ("WidgetBlueprintGeneratedClass", "AnimBlueprintGeneratedClass",
                    "BlueprintGeneratedClass"):
        f = M.call("find_assets", {"class": gen_cls, "limit": 8})
        sample = [a for a in (f.get("assets") or [])][:5]
        if not sample:
            continue
        listed = [a for a in sample if a.get("packageName") in by_pkg]
        check("T755 %s assets are listed at all" % gen_cls, len(listed) == len(sample),
              "%d of %d missing from list_blueprints" % (len(sample) - len(listed), len(sample)))
        # A package registered under BOTH spellings is legitimately uncooked, so only assert the
        # flag on those the registry knows ONLY as a generated class.
        plain = gen_cls.replace("GeneratedClass", "")
        gen_only = []
        for a in listed:
            # ADOPTION-OK: only p's COUNT is read; the identifier comes from `listed`, not from p
            p = M.call("find_assets", {"class": plain, "nameContains": a.get("name", "")[:-2],
                                       "limit": 3})
            if (p.get("count") or 0) == 0:
                gen_only.append(a)
        if gen_only:
            wrong = [a.get("name") for a in gen_only if not by_pkg[a["packageName"]].get("cooked")]
            check("T755 %s ones are flagged cooked:true" % gen_cls, not wrong,
                  "listed but labelled uncooked: %s" % wrong[:3])
        else:
            print("  (%s: every sample also exists uncooked - flag not asserted)" % gen_cls)

    print("")
    print("=== T756: no blueprint is listed twice ===")
    # An UNCOOKED blueprint is registered under BOTH spellings, so merging the two queries without
    # a key would double-count it - wrong in the other direction, and harder to notice.
    ids = [b.get("package") for b in rows]
    check("T756 packages are unique across the merged list", len(ids) == len(set(ids)),
          "%d rows, %d distinct packages" % (len(ids), len(set(ids))))

    print("")
    print("=== T757: the filter reaches cooked blueprints ===")
    r = M.call("list_blueprints", {"filter": "VehicleBoat"})
    if (r.get("count") or 0) > 0:
        check("T757 a cooked-only name is findable", (r.get("count") or 0) > 0, r.get("count"))
        check("T757 and comes back marked cooked",
              any(b.get("cooked") for b in (r.get("blueprints") or [])),
              json.dumps(r.get("blueprints"))[:200])
    else:
        print("  (no VehicleBoat here - not DDS2, not exercised)")

    print("")
    print("=== T758: a COOKED macro library and a typo no longer read the same ===")
    # add_macro_instance said "macro library not found" for both - a path with nothing at it, and a
    # cooked library that exists and cannot be used. Byte-identical errors for two problems with
    # different fixes, so the caller went hunting for a typo that was not there.
    #
    # The refusal is correct either way: cooking strips MacroGraphs, so there is nothing to instance.
    # Only the reason was wrong.
    import time as _t
    bp = M.call("create_blueprint", {"path": "/Game/_MifMacro/BP_MT_%d" % int(_t.time() % 100000),
                                     "parentClass": "Actor"})
    graph = bp.get("eventGraphId")
    if not graph:
        print("  (could not make a scratch blueprint - not exercised)")
    else:
        cooked_bp = None
        for b in (M.call("list_blueprints", {"filter": "/Game/"}).get("blueprints") or []):
            if b.get("cooked"):
                cooked_bp = b.get("package")
                break
        if not cooked_bp:
            print("  (no cooked blueprint here - not exercised)")
        else:
            hit = M.call("add_macro_instance", {"graphId": graph, "macroLibrary": cooked_bp,
                                                "macroName": "Anything"})
            miss = M.call("add_macro_instance", {"graphId": graph,
                                                 "macroLibrary": "/Game/MifNope/DoesNotExist",
                                                 "macroName": "Anything"})
            check("T758 both are still refused", hit.get("ok") is False and miss.get("ok") is False,
                  "cooked=%r missing=%r" % (hit.get("ok"), miss.get("ok")))
            check("T758 but they no longer say the same thing",
                  str(hit.get("error")) != str(miss.get("error")),
                  "identical errors for a cooked library and a nonexistent path")
            check("T758 the cooked one says COOKED", "cooked" in str(hit.get("error")),
                  str(hit.get("error"))[:200])
            check("T758 and explains a macro library cannot be recovered",
                  "MacroGraphs" in str(hit.get("error")), str(hit.get("error"))[:260])
            check("T758 the missing one says no package",
                  "no package" in str(miss.get("error")), str(miss.get("error"))[:200])
            check("T758 and does NOT get the macro-library advice, which would be nonsense",
                  "MacroGraphs" not in str(miss.get("error")), str(miss.get("error"))[:200])

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % (f[0],))
        print("          %s" % (f[1],))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
