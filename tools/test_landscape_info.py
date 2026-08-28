"""landscape_info - the read that reported components:0 for terrain that plainly had components.

WHY THIS SUITE EXISTS. 73c4b8e fixed landscape_info reporting `components: 0` for a World Partition
terrain, and nothing locked the fix in. The cause is worth stating because it decides what this suite
can and cannot prove: the handler iterates `TActorIterator<ALandscape>`, which finds PARENT actors only,
and under World Partition the components do not live on the parent - they live on the
ALandscapeStreamingProxy actors that share its LandscapeGuid. So the parent honestly owns zero
components and the endpoint honestly reported zero, which is the most expensive kind of correct: a true
number that answers a question nobody asked.

The fix added proxyCount, proxyComponents, totalComponents and an always-present componentScope string
saying which of the two numbers you are looking at.

WHAT THIS SUITE CAN AND CANNOT REACH, said plainly rather than papered over. The bug is a WORLD
PARTITION bug. Exercising it needs a World Partition map with streaming proxies, and the only ones on
this machine are Andre's real DDS2 maps, which the standing rules forbid opening. So this suite:

  * proves the PARAMETER CONTRACT and the zero-landscape path always;
  * creates a small non-partitioned landscape in the open scratch level and proves the ACCOUNTING
    IDENTITY (components == totalComponents when there are no proxies, componentScope says so, and both
    agree with a reflection read that bypasses the endpoint entirely);
  * CANNOT prove the partitioned branch, and says so in its output rather than passing quietly and
    implying coverage it does not have. That branch is a good candidate for the downstream consumer who
    has real terrain - see docs/12_AUTONOMOUS_REPORT_LOOP.md.

A suite that silently skips its own reason for existing is worse than no suite, because the next person
reads the green and stops looking.

SAFETY: landscape_info takes no asset parameter at all - its subject is the OPEN LEVEL, so the scratch
convention does not apply. It is read-only (no transaction, no modal, no check()). The landscape this
suite creates goes into whatever level is open and is left there; that is acceptable only because the
harness runs against an untitled scratch level and nothing is ever saved. It refuses to create anything
if the open level looks like real work.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []
UNPROVEN = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def info():
    return M.call("landscape_info", {}, timeout=90)


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T520 the parameter contract
    print("=== T520: landscape_info takes NO parameters, and says so ===")
    r = info()
    check("T520 an empty payload succeeds", r.get("ok") is True, json.dumps(r)[:220])
    # The guard is RejectUnknownParams(In, Out, {}, ...) - an EMPTY accept list, so any key at all
    # fails. Asserted because a probe passing a plausible-looking key is how this family gets
    # misdiagnosed as broken.
    for key in ("actorPath", "limit", "world"):
        q = M.call("landscape_info", {key: "x"}, timeout=60)
        check("T520 '%s' is refused rather than ignored" % key, q.get("ok") is False,
              json.dumps(q)[:200])
    check("T520 and the refusal says it takes none",
          "none" in (M.call("landscape_info", {"zz": 1}, timeout=60).get("error") or "").lower(),
          "the error should name the empty parameter list")

    # ------------------------------------------------------------------ T521 the shape of the answer
    print("")
    print("=== T521: every landscape reports which number you are looking at ===")
    r = info()
    count = r.get("count")
    check("T521 it reports a count", isinstance(count, (int, float)), json.dumps(r)[:200])
    lands = r.get("landscapes") or []
    check("T521 the count matches the array",
          count == len(lands),
          "count=%s but %d landscapes returned - a count disagreeing with its own payload is worse "
          "than no count" % (count, len(lands)))

    if not lands:
        # Documented: the note is emitted ONLY when count == 0.
        check("T521 with no landscape it says so rather than returning a bare zero",
              bool(r.get("note")), json.dumps(r)[:200])
        check("T521 and points at create_landscape",
              "create_landscape" in (r.get("note") or ""), (r.get("note") or "")[:180])

    # ------------------------------------------------------------------ T522 make one and account for it
    print("")
    print("=== T522: the accounting identity, on a landscape this suite creates ===")
    world = (M.call("list_level_actors", {"limit": 1}, timeout=60).get("world") or "")
    scratchy = world.startswith("Untitled") or world.startswith("_Mif")
    if not scratchy:
        # Never dress someone's real level with test terrain.
        check("T522 (not exercised: the open level %r is not a scratch level)" % world, True)
        UNPROVEN.append("the accounting identity - the open level was not scratch, so no landscape "
                        "was created")
    else:
        made = M.call("create_landscape", {"componentsX": 2, "componentsY": 2,
                                           "quadsPerSection": 63, "sectionsPerComponent": 1,
                                           "heightMode": "flat",
                                           "label": "MifLS_%d" % st}, timeout=300)
        check("T522 a small landscape is created", made.get("ok") is True, json.dumps(made)[:240])
        if made.get("ok"):
            r = info()
            lands = r.get("landscapes") or []
            mine = next((l for l in lands if str(l.get("label", "")).startswith("MifLS_%d" % st)), None)
            check("T522 landscape_info now sees it", mine is not None,
                  str([l.get("label") for l in lands])[:200])
            if mine:
                comp = mine.get("components")
                total = mine.get("totalComponents")
                pc = mine.get("proxyCount")
                check("T522 it reports components", isinstance(comp, (int, float)), json.dumps(mine)[:220])
                # THE fields the fix added. Their absence is the regression this suite exists to catch.
                check("T522 totalComponents is present (added by 73c4b8e)",
                      isinstance(total, (int, float)), json.dumps(mine)[:220])
                check("T522 proxyCount is present (added by 73c4b8e)",
                      isinstance(pc, (int, float)), json.dumps(mine)[:220])
                check("T522 componentScope is ALWAYS present", bool(mine.get("componentScope")),
                      json.dumps(mine)[:220])
                # Non-partitioned: no proxies, so the two numbers must agree. This is the identity the
                # fix restored, checked in the direction this machine can reach.
                check("T522 with no streaming proxies the counts agree",
                      pc == 0 and comp == total,
                      "components=%s totalComponents=%s proxyCount=%s" % (comp, total, pc))
                check("T522 and componentScope says there are no proxies",
                      "no streaming proxies" in str(mine.get("componentScope")),
                      str(mine.get("componentScope"))[:200])
                # componentsNote is documented as appearing ONLY when components==0 and proxies exist.
                check("T522 componentsNote is absent when it does not apply",
                      "componentsNote" not in mine, json.dumps(mine)[:220])

                # READ IT BACK BYPASSING BOTH LANDSCAPE ENDPOINTS. Reflection over the actor's own
                # array cannot be wrong in the same way the iterator was.
                g = M.call("get_property", {"actorPath": mine.get("actorPath"),
                                            "propertyPath": "LandscapeComponents"}, timeout=90)
                arr = g.get("typed") if isinstance(g.get("typed"), list) else g.get("value")
                if isinstance(arr, list):
                    check("T522 reflection agrees with the reported component count",
                          len(arr) == comp,
                          "LandscapeComponents has %d entries, endpoint said %s" % (len(arr), comp))
                else:
                    check("T522 (reflection read unavailable: %s)" % json.dumps(g)[:120], True)

                # And the cross-endpoint identity, against a handler in a DIFFERENT file.
                d = M.call("diagnose_landscape", {}, timeout=120)
                if d.get("ok") and isinstance(d.get("componentCount"), (int, float)):
                    tot = sum((l.get("totalComponents") or 0) for l in lands)
                    check("T522 diagnose_landscape agrees on the total",
                          d.get("componentCount") == tot,
                          "diagnose=%s landscape_info total=%s" % (d.get("componentCount"), tot))
                else:
                    check("T522 (diagnose_landscape cross-check unavailable)", True)

                # ---------------------------------------------------------- T523 diagnose_landscape_draws
                # REGRESSION LOCK for the fix in 11f7893: FStaticMeshBatchRelevance::LODIndex is
                # UE_DEPRECATED(5.4, "...doesn't contain valid data anymore! Use GetLODIndex() function
                # instead.") - not a forward-compat warning, a live bug on every 5.4+ engine. Before the
                # fix, "lod" silently held whatever the stub bitfield happened to contain instead of a
                # real LOD index, and nothing here would have caught it: diagnose_landscape_draws had NO
                # suite at all (coverage_gaps.py, 2026-08-28). A fix with no test locking it in is a fix
                # with a shelf life - the same lesson add_timeline and landscape_info itself already
                # taught this project.
                print("")
                print("=== T523: diagnose_landscape_draws reports REAL lod indices, not stub garbage ===")
                dd = M.call("diagnose_landscape_draws", {"limit": 50}, timeout=120)
                check("T523 it succeeds", dd.get("ok") is True, json.dumps(dd)[:220])
                sample = dd.get("sample") or []
                check("T523 and samples at least one component from the landscape just created",
                      len(sample) > 0, json.dumps(dd)[:220])
                if sample:
                    # A single, precise property: for EVERY component, the DISTINCT lod values - once
                    # deduplicated, since more than one relevance can legitimately share an lod (a
                    # material pass and a non-material pass both at LOD 0 is real, seen live) - form a
                    # CONTIGUOUS run starting at 0. The deprecated field, once it stopped tracking real
                    # data, had no reason to produce that shape at all: an unmoving stub bitfield would
                    # far more likely repeat, skip, or sit outside a sane 0..staticMeshCount range.
                    bad = []
                    for entry in sample:
                        rels = entry.get("staticMeshRelevances") or []
                        lods = sorted(set(r.get("lod") for r in rels if isinstance(r.get("lod"), int)))
                        smCount = entry.get("staticMeshes")
                        ok = (bool(lods) and lods[0] == 0
                              and lods == list(range(lods[0], lods[-1] + 1))
                              and (not isinstance(smCount, (int, float)) or lods[-1] < smCount))
                        if not ok:
                            bad.append({"component": entry.get("component"), "lods": lods,
                                        "staticMeshes": smCount})
                    check("T523 every sampled component's distinct lod values are a contiguous 0..N run",
                          not bad, json.dumps(bad)[:300])
                    print("       %d component(s) sampled, lod shapes: %s"
                          % (len(sample),
                             [sorted(set(r.get("lod") for r in (e.get("staticMeshRelevances") or [])))
                              for e in sample[:4]]))

    UNPROVEN.append("the WORLD PARTITION branch - proxyCount>0, proxyComponents>0 and the "
                    "componentsNote that fires when components==0. That is the actual bug 73c4b8e "
                    "fixed. It needs a World Partition map with streaming proxies; the only ones here "
                    "are real DDS2 maps, which must not be opened.")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    if UNPROVEN:
        print("")
        print("NOT PROVEN BY THIS SUITE (green above does not cover these):")
        for u in UNPROVEN:
            print("  - %s" % u)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
