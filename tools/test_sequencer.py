"""list_level_sequences and describe_level_sequence - the first Sequencer coverage.

Nothing in the ~290 endpoints before these could enumerate a cutscene, let alone describe one. This is
the first family added under the parity push, and it is deliberately READ-ONLY: reads are safe, testable
against real content, and immediately useful, while a write into a MovieScene needs a rollback story
this project has not built yet.

TWO THINGS THIS SUITE GUARDS THAT ARE EASY TO GET WRONG.

The first is TIME. Sequencer has two frame rates and conflating them is the classic mistake: tick
resolution is the internal integer frame space (24000/1 by default) and display rate is what the UI
shows (30/1). A frame number means nothing without saying which one it is in. T580 asserts BOTH are
reported and that the seconds values are internally consistent with the ticks - a duration computed
against the wrong rate is wrong by a factor of 800, which is the kind of error that looks like data
corruption rather than a unit mistake.

The second is that a REGISTRY QUERY CAN LIE BY OMISSION. At editor startup the Asset Registry is still
discovering assets, and GetAssetsByClass returns a partial set while returning true - so "no sequences"
and "not finished looking" are indistinguishable. The endpoint reports registryStillScanning for exactly
that reason and T581 asserts the field exists, because a caller that does not check it will eventually
conclude an asset is missing when it is merely not found yet.

SAFETY: read-only. Nothing is created, nothing is loaded by list_level_sequences at all, and
describe_level_sequence loads the sequence asset without modifying it. Runs against whatever the project
already ships; if it ships no LevelSequence, the suite says so rather than passing vacuously.
"""
import json
import sys

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T580 the listing
    print("=== T580: enumerate the project's cutscenes ===")
    r = M.call("list_level_sequences", {}, timeout=180)
    check("T580 the listing succeeds", r.get("ok") is True, json.dumps(r)[:200])
    seqs = r.get("sequences") or []
    check("T580 count agrees with the array it returned", r.get("count") == len(seqs),
          "count=%s but %d rows - a count disagreeing with its own payload is worse than no count"
          % (r.get("count"), len(seqs)))
    # matched is the TRUE total; count is what survived the limit. Conflating them is how a caller
    # concludes it has everything.
    check("T580 matched is present and at least count",
          isinstance(r.get("matched"), (int, float)) and r.get("matched") >= len(seqs),
          json.dumps({k: r.get(k) for k in ("count", "matched", "truncated")}))
    # THE assertion about registry honesty. Without this field a zero count is ambiguous forever.
    check("T580 it reports whether the registry was still scanning",
          isinstance(r.get("registryStillScanning"), bool), json.dumps(r)[:200])

    for s in seqs[:5]:
        check("T580 %s carries an object path" % (s.get("name") or "?"),
              str(s.get("objectPath", "")).startswith("/"), json.dumps(s)[:160])
        check("T580 %s carries a package name" % (s.get("name") or "?"),
              bool(s.get("packageName")), json.dumps(s)[:160])

    # ------------------------------------------------------------------ T581 contracts
    print("")
    print("=== T581: the parameter contract points callers at the right endpoint ===")
    q = M.call("list_level_sequences", {"path": "/Game/x"}, timeout=60)
    check("T581 'path' is refused here", q.get("ok") is False, json.dumps(q)[:180])
    check("T581 and the refusal names describe_level_sequence",
          "describe_level_sequence" in (q.get("error") or ""), (q.get("error") or "")[:190])
    q = M.call("describe_level_sequence", {"filter": "x"}, timeout=60)
    check("T581 'filter' is refused there", q.get("ok") is False, json.dumps(q)[:180])
    check("T581 and the refusal names list_level_sequences",
          "list_level_sequences" in (q.get("error") or ""), (q.get("error") or "")[:190])
    q = M.call("describe_level_sequence", {"path": "/Game/NoSuchSequence_zz.NoSuchSequence_zz"}, timeout=90)
    check("T581 a sequence that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])
    check("T581 and the refusal shows the object-path shape",
          "LS_" in (q.get("error") or "") or "list_level_sequences" in (q.get("error") or ""),
          (q.get("error") or "")[:190])

    # ------------------------------------------------------------------ T582 the description
    print("")
    print("=== T582 [the units trap]: two frame rates, and seconds consistent with ticks ===")
    if not seqs:
        check("T582 (not exercised: this project ships no LevelSequence)", True)
    else:
        target = seqs[0].get("objectPath")
        d = M.call("describe_level_sequence", {"path": target}, timeout=180)
        check("T582 the description succeeds", d.get("ok") is True, json.dumps(d)[:200])
        check("T582 it echoes the object path", d.get("objectPath") == target,
              "%s vs %s" % (d.get("objectPath"), target))

        # BOTH rates, always. Reporting one and calling it "the frame rate" is the bug this guards.
        check("T582 tickResolution is reported", bool(d.get("tickResolution")), json.dumps(d)[:200])
        check("T582 displayRate is reported", bool(d.get("displayRate")), json.dumps(d)[:200])
        check("T582 and they are DIFFERENT things, reported separately",
              d.get("tickResolution") != d.get("displayRate")
              or d.get("tickResolution") is None,
              "both are %r - if a project really uses one rate for both this is not a failure, but it "
              "is worth a look" % d.get("tickResolution"))
        fps = d.get("displayRateFps")
        check("T582 displayRateFps is numeric", isinstance(fps, (int, float)), json.dumps(d)[:200])

        # The consistency check. If seconds were computed against the display rate instead of the tick
        # resolution the answer is wrong by a factor of ~800, which reads as corruption rather than a
        # unit error - so it is asserted rather than eyeballed.
        st, en = d.get("playbackStartTick"), d.get("playbackEndTick")
        s0, s1 = d.get("playbackStartTime"), d.get("playbackEndTime")
        dur = d.get("durationSeconds")
        if all(isinstance(x, (int, float)) for x in (st, en, s0, s1, dur)):
            tick = str(d.get("tickResolution") or "0/1").split("/")
            rate = float(tick[0]) / float(tick[1]) if len(tick) == 2 and float(tick[1]) else 0.0
            check("T582 start seconds match start ticks at the TICK rate",
                  rate > 0 and abs(s0 - (st / rate)) < 0.001,
                  "startTick=%s startTime=%s tickRate=%s" % (st, s0, rate))
            check("T582 duration equals end minus start", abs(dur - (s1 - s0)) < 0.001,
                  "duration=%s but end-start=%s" % (dur, s1 - s0))
            check("T582 and the duration is not absurd", 0 <= dur < 60 * 60 * 24,
                  "durationSeconds=%s" % dur)

        counts = d.get("counts") or {}
        for k in ("bindings", "possessables", "spawnables", "rootTracks", "sections"):
            check("T582 counts.%s is a number" % k, isinstance(counts.get(k), (int, float)),
                  json.dumps(counts)[:180])
        # possessables reference actors that must already exist; spawnables carry their own template.
        # Their sum cannot exceed the bindings that hold them.
        if all(isinstance(counts.get(k), (int, float)) for k in ("bindings", "possessables", "spawnables")):
            check("T582 possessables + spawnables never exceed bindings",
                  counts["possessables"] + counts["spawnables"] <= counts["bindings"],
                  json.dumps(counts))
        check("T582 hasCameraCutTrack is a bool", isinstance(d.get("hasCameraCutTrack"), bool),
              json.dumps(d)[:200])

    check("T582 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
