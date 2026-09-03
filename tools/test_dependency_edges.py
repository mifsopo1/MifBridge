"""Hard vs soft dependency edges - and the empty result that used to lie.

A HARD dependency must load before its source: it is what gets dragged into a cook and what breaks a
mod when absent. A SOFT one loads on demand, and a missing target is survivable. Both endpoints
answered with one undifferentiated list, so an agent asking "is this safe to delete" or "why is my
_P pak 400MB" could not tell them apart.

T5001 IS THE INVARIANT WORTH ASSERTING. hard:true and hard:false must PARTITION the edge set - every
edge is hard or it is not - so the two counts have to sum to the unfiltered total. That is a
property the implementation cannot fake by accident, unlike checking that each filter merely returns
something.

T5002 IS THE SAFETY FIX, and it matters more than the filtering.
FAssetRegistrySerializationOptions::bSerializeDependencies defaults to FALSE
(AssetRegistryState.h:56 - only InitForDevelopment turns it on), so a cooked project's runtime
registry typically carries NO package dependency edges at all. get_referencers on base-game content
therefore returned count:0 with packageExists:true - and count:0 is the standard justification for
deleting something. "The graph was never serialized" and "nothing points at this" were
indistinguishable. The existing existsNote block guards exactly that confusion for a MISTYPED path;
a container package had no such guard.

THREE STATES, NOT TWO, and the first version of this got it wrong. A package with no file on disk is
not necessarily cooked: a /Temp/ package, or an asset created this session and never saved, also has
no file. Calling that "a COOKED container" is confident wrongness of exactly the kind the note
exists to prevent, so packageSource distinguishes loose, container and inMemory, and the note text
differs per case.

RUNS AGAINST WHATEVER THE PROJECT HAS. It finds a loose package that really has edges rather than
painting a fixture, so the counts are real.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []


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

    # ------------------------------------------------------------------ find a real fixture
    # SKIP SCRATCH. `origin: "loose"` narrows to uncooked packages and does NOT exclude another
    # suite's leftovers - a scratch blueprint under /Game/_Mif is loose too, and the second loop has
    # no class filter at all, so it draws from a pool 90 suites in this directory contribute to. On
    # the second pass of a sweep this suite would measure dependency edges on somebody's fixture and
    # report it as "a loose package with real dependency edges". Found 2026-09-03 once
    # is_scratch_fixture learned to read `packageName`, which is the key find_assets rows carry.
    target = None
    for a in (M.call("find_assets", {"origin": "loose", "class": "Material",
                                     "limit": 15}).get("assets") or []):
        if M.is_scratch_fixture(a):
            continue
        if (M.call("get_dependencies", {"path": a["packageName"]}).get("count") or 0) > 1:
            target = a["packageName"]
            break
    if not target:
        for a in (M.call("find_assets", {"origin": "loose", "limit": 60}).get("assets") or []):
            if M.is_scratch_fixture(a):
                continue
            if (M.call("get_dependencies", {"path": a["packageName"]}).get("count") or 0) > 1:
                target = a["packageName"]
                break
    check("(setup) a loose package with real dependency edges", bool(target), target)
    if not target:
        print("SKIPPED - nothing loose in this project carries edges.")
        return 0
    print("        using %s" % target)

    # ------------------------------------------------------------------ T5000 per-edge detail
    print("\n=== T5000: what each edge actually is ===")
    r = M.call("get_dependencies", {"path": target, "includeProperties": True})
    check("T5000 get_dependencies succeeds", r.get("ok") is True, json.dumps(r)[:220])
    check("T5000 the flat array is still there for every existing caller",
          isinstance(r.get("dependencies"), list)
          and len(r["dependencies"]) == r.get("count"), json.dumps(r)[:220])
    edges = r.get("edges") or []
    check("T5000 and edges[] carries one row per dependency", len(edges) == r.get("count"),
          "%d edges vs count %s" % (len(edges), r.get("count")))
    if edges:
        e = edges[0]
        for f in ("package", "hard", "game", "build", "editorOnly"):
            check("T5000 each edge reports %s" % f, f in e, sorted(e))
        # editorOnly is the ABSENCE of Game, not a flag of its own - reading it as a flag is the
        # obvious mistake, so the endpoint derives it and the suite checks the derivation.
        check("T5000 editorOnly is the inverse of game, since the engine has no editor-only flag",
              all(row.get("editorOnly") == (not row.get("game")) for row in edges),
              json.dumps(edges[:2])[:220])
    check("T5000 the counts add up to the total",
          (r.get("hardCount") or 0) + (r.get("softCount") or 0) == r.get("count"),
          json.dumps({k: r.get(k) for k in ("hardCount", "softCount", "count")}))

    # ------------------------------------------------------------------ T5001 the partition
    print("\n=== T5001: hard and soft must PARTITION the edges ===")
    total = M.call("get_dependencies", {"path": target}).get("count")
    hard = M.call("get_dependencies", {"path": target, "hard": True}).get("count")
    soft = M.call("get_dependencies", {"path": target, "hard": False}).get("count")
    print("        total %s = hard %s + soft %s" % (total, hard, soft))
    # THE assertion. Every edge is hard or it is not, so the two filters must sum to the whole -
    # a property the implementation cannot satisfy by accident.
    check("T5001 hard-only plus soft-only equals the unfiltered total",
          hard + soft == total, "%d + %d != %d" % (hard, soft, total))
    check("T5001 and hard-only agrees with the hardCount from includeProperties",
          hard == r.get("hardCount"), "%s vs %s" % (hard, r.get("hardCount")))
    check("T5001 a filtered call reports edges without being asked, since a filter implies "
          "you care which is which",
          isinstance(M.call("get_dependencies", {"path": target, "hard": True}).get("edges"), list),
          "no edges[] on a filtered call")

    # ------------------------------------------------------------------ T5002 the empty result
    print("\n=== T5002: count:0 has three meanings, and they lead to opposite actions ===")
    good = M.call("get_referencers", {"path": target})
    check("T5002 a loose package reports its source as loose",
          good.get("packageSource") == "loose", good.get("packageSource"))
    check("T5002 and dependencyDataAvailable is true for it",
          good.get("dependencyDataAvailable") is True, good.get("dependencyDataAvailable"))

    cont = (M.call("find_assets", {"origin": "container", "limit": 1}).get("assets")
            or [{}])[0].get("packageName")
    if cont:
        c = M.call("get_referencers", {"path": cont})
        check("T5002 (setup) the container package is KNOWN to the registry - which is why the "
              "existing packageExists guard does not catch this case",
              c.get("packageExists") is True, c.get("packageExists"))
        if (c.get("count") or 0) == 0:
            # THE assertion. Without this an agent reads count:0 + packageExists:true as
            # "unreferenced" and deletes something that is in use.
            check("T5002 an empty result on a non-loose package sets dependencyDataAvailable FALSE",
                  c.get("dependencyDataAvailable") is False, json.dumps(c)[:250])
            check("T5002 and the note says the data was NOT RECORDED, not that nothing references it",
                  "NOT RECORDED" in (c.get("dependencyDataNote") or "")
                  or "nothing recorded" in (c.get("dependencyDataNote") or ""),
                  (c.get("dependencyDataNote") or "")[:220])
            check("T5002 the note names the real cause rather than guessing",
                  "bSerializeDependencies" in (c.get("dependencyDataNote") or "")
                  or "never saved" in (c.get("dependencyDataNote") or ""),
                  (c.get("dependencyDataNote") or "")[:220])
        else:
            print("  NOTE  this container package DOES carry edges, so the caveat is not exercised.")

    # An in-memory package must NOT be described as cooked - that was the first version's bug.
    mem = M.call("get_referencers", {"path": "/Temp/Untitled_1"})
    if mem.get("ok") and mem.get("packageSource") == "inMemory":
        check("T5002 an in-memory package is NOT called cooked - three states, not two",
              "COOKED" not in (mem.get("dependencyDataNote") or ""),
              (mem.get("dependencyDataNote") or "")[:200])

    # ------------------------------------------------------------------ T5003 vocabulary
    print("\n=== T5003: the parameters ===")
    bad = M.raw_post("get_dependencies", {"path": target, "category": "nonsense"})
    check("T5003 an unknown category is refused and the real ones listed",
          bad.get("ok") is False and "manage" in (bad.get("error") or ""),
          (bad.get("error") or "")[:200])
    softp = M.raw_post("get_dependencies", {"path": target, "soft": True})
    check("T5003 a `soft` parameter is refused, pointing at hard:false - one parameter with two "
          "states cannot disagree with itself",
          softp.get("ok") is False and "hard:false" in (softp.get("error") or ""),
          (softp.get("error") or "")[:200])
    for cat in ("package", "manage", "searchableName", "all"):
        cr = M.call("get_dependencies", {"path": target, "category": cat})
        check("T5003 category=%s is accepted" % cat, cr.get("ok") is True,
              json.dumps(cr)[:160])

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
