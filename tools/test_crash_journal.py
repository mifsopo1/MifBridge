"""The crash journal - does it actually record what the bridge was doing?

WHY IT EXISTS. add_anim_node crash-killed this editor on 2026-08-26 (PM-013). There was no in-editor
signal and no record of which call did it, so the culprit had to be reconstructed from what had recently
been attempted. That reconstruction cost far more than the fix.

THE PROPERTY UNDER TEST, and it is a property about ORDERING, not about content: the `start` record must
be on disk BEFORE the handler runs. A journal written after a call completes describes every call
EXCEPT the one that killed the process - which is the only one anybody wanted. So the diagnostic is an
ABSENCE: a `start` with no matching `end` names the call that died.

That is why this cannot ride on UE_LOG. FOutputDeviceFile hands lines to a background FAsyncWriter ring
buffer and does not flush per line without -FORCELOGFLUSH, so a hard kill loses precisely the tail that
matters. The journal holds one FArchive open and Flush()es per record.

WHAT THIS SUITE CAN AND CANNOT PROVE. It proves the records exist, that they pair up, that they carry
the fields mifwatch.py depends on, and that a call this suite makes itself shows up. It does NOT kill
the editor - a suite that takes the editor down mid-run would be indistinguishable from a suite that
crashed it, and run_all_suites treats that as the headline failure. The hard-kill case is verified once,
by hand, and recorded in the commit rather than pretended at here.

SAFETY: read-only. It reads a file under Saved/ and calls one cheap endpoint.
"""
import io
import json
import os
import sys

import mifaudit as M

PASS, FAIL = [], []

# THE RUNNING EDITOR'S JOURNAL, NOT THIS CHECKOUT'S. This was computed from mifaudit.py's own
# location - "../../../Saved" - which resolves to the DDS2 tree whichever editor is answering. Run
# against Curfew on 5.7 it read 676,934 records of old 5.3 sessions and reported that nothing was
# being recorded, while the journal under test worked perfectly. Five red assertions, all of them
# about a file the editor never touches.
#
# Resolved lazily, because the answer comes from the live process and there is none at import time.
def journal_path():
    saved = M.live_saved_dir()
    return os.path.join(saved, "MifBridge", "journal.jsonl") if saved else None


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def read_journal():
    jp = journal_path()
    if not jp or not os.path.isfile(jp):
        return None
    out = []
    with io.open(jp, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"ev": "unparseable", "raw": line[:100]})
    return out


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T620 it exists
    print("=== T620: the journal exists and is well-formed ===")
    recs = read_journal()
    if recs is None:
        check("T620 the journal file exists", False,
              "%s missing - the plugin writes it on StartServer; mif.BridgeJournal defaults to on"
              % (journal_path() or "the running editor's project could not be determined"))
        print("")
        print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
        return 1
    check("T620 the journal file exists", True)
    check("T620 it has records", len(recs) > 0, "%d records" % len(recs))
    # Every line must parse. A single malformed line makes the whole journal unreadable at exactly the
    # moment somebody needs it, which is why the writer escapes quotes, backslashes and newlines.
    bad = [r for r in recs if r.get("ev") == "unparseable"]
    check("T620 every line is valid JSON", not bad,
          "%d unparseable line(s): %s" % (len(bad), bad[:2]))

    # ------------------------------------------------------------------ T621 session header
    print("")
    print("=== T621: the session header identifies WHICH editor ===")
    sessions = [r for r in recs if r.get("ev") == "session"]
    check("T621 at least one session record", len(sessions) > 0, "%d records total" % len(recs))
    if sessions:
        s = sessions[-1]
        # The pid is what makes an append-only journal readable when two editors share a project -
        # without it, interleaved sessions are one unusable stream.
        check("T621 it carries a pid", isinstance(s.get("pid"), (int, float)), json.dumps(s)[:160])
        check("T621 it carries the port it is serving", isinstance(s.get("port"), (int, float)),
              json.dumps(s)[:160])
        check("T621 it carries the engine version", bool(s.get("engine")), json.dumps(s)[:160])
        check("T621 and a timestamp", bool(s.get("t")), json.dumps(s)[:160])

    # ------------------------------------------------------------------ T622 a call we make appears
    print("")
    print("=== T622 [the ordering property]: a call this suite makes shows up as start+end ===")
    before = len(recs)
    # describe_endpoint is cheap, read-only and unmistakable in the journal.
    r = M.call("describe_endpoint", {"name": "describe_endpoint"}, timeout=120)
    check("T622 the probe call succeeded", r.get("ok") is True, json.dumps(r)[:160])

    after = read_journal() or []
    check("T622 the journal grew", len(after) > before,
          "%d -> %d records - if this did not grow, nothing is being recorded" % (before, len(after)))
    new = after[before:]
    starts = [x for x in new if x.get("ev") == "start"]
    ends = [x for x in new if x.get("ev") == "end"]
    check("T622 a start record was written", len(starts) > 0, json.dumps(new)[:220])
    check("T622 an end record was written", len(ends) > 0, json.dumps(new)[:220])
    mine = [x for x in starts if x.get("ep") == "describe_endpoint"]
    check("T622 and the start names the endpoint we called", len(mine) > 0,
          "starts seen: %s" % [x.get("ep") for x in starts][:5])
    if ends:
        e = ends[-1]
        check("T622 the end record carries elapsed ms", isinstance(e.get("ms"), (int, float)),
              json.dumps(e)[:160])
        check("T622 and an ok flag", isinstance(e.get("ok"), bool), json.dumps(e)[:160])
        # Sanity, not precision: a read-only describe taking over a minute would mean the timer is
        # measuring something other than the call.
        check("T622 the elapsed time is plausible", 0 <= float(e.get("ms") or 0) < 60000,
              "ms=%s" % e.get("ms"))

    # ------------------------------------------------------------------ T623 mifwatch agrees
    print("")
    print("=== T623: mifwatch reads it and reports the current session as live ===")
    try:
        import mifwatch
        parsed = mifwatch.analyse(after)
        check("T623 mifwatch parses the journal", isinstance(parsed, list) and len(parsed) > 0,
              "%d session(s)" % (len(parsed) if isinstance(parsed, list) else -1))
        if parsed:
            cur = parsed[-1]
            check("T623 the newest session has no shutdown yet - the editor is still running",
                  cur.get("ended") is None, json.dumps({k: cur.get(k) for k in ("pid", "ended")}))
            check("T623 and it counted the calls", (cur.get("calls") or 0) > 0, json.dumps(cur)[:180])
            # THE assertion that matters for the live case: while the editor is healthy, every call
            # that started also finished. An entry here on a healthy editor means a call hung.
            check("T623 no call in the live session started without finishing",
                  not cur.get("unfinished"),
                  "unfinished: %s - on a HEALTHY editor this should be empty; an entry means a call "
                  "never returned" % (cur.get("unfinished"),))
    except ImportError as exc:
        check("T623 mifwatch imports", False, str(exc))

    check("T623 the bridge is still answering", M.bridge_responsive() is True, "bridge died")

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
