"""Regenerate tools/endpoints_current.json from a LIVE editor's self_audit.

WHY THIS EXISTS. The snapshot is documented (README.md, FEATURE_PARITY_SPEC.md) as "regenerated from
the live editor's self_audit", but until 2026-08-28 nothing actually did that regeneration - it was a
one-off hand-written file from 2026-08-26 that coverage_gaps.py trusted forever. It went stale by 60
added and 12 removed/renamed endpoints across two days of real feature work, and every coverage
judgement made against it in that window was computed over the wrong universe, silently.
coverage_gaps.py now WARNS loudly when the snapshot disagrees with source (see its own docstring for
why that check is a static extraction rather than another live call) - this script is what you run
when it does.

WHY SELF_AUDIT AND NOT A STATIC MIF_DECL GREP. A MIF_DECL is a declared handler prototype; a MIF_BIND
is what actually wires it into the dispatch table. They agree today (parity_check.py cross-checks
this every run), but the documented authority is deliberately "the ones actually dispatching" -
self_audit's own words - which only a live editor can answer with certainty. Right now that
distinction is theoretical; the snapshot's CONTRACT should not depend on it staying that way.

Needs a running editor with the bridge up - either DDS2's or the disposable probe both work equally
well, since this reads the plugin's registry, not project content. Defaults to port 8791
(mifaudit.BRIDGE_PORT); pass --port to target a probe deliberately started on a different one.

Usage:
    python tools/refresh_endpoints_snapshot.py [--port 8801]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import mifaudit as M

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=M.BRIDGE_PORT,
                     help="bridge port to read from (default %d, DDS2's - mifaudit.BRIDGE_PORT)" % M.BRIDGE_PORT)
    args = ap.parse_args()

    base = "http://127.0.0.1:%d/api" % args.port
    print("reading self_audit from %s ..." % base)
    try:
        # summaryOnly turns OFF includeEndpoints too by default (H_self_audit:
        # bIncludeList = JBoolAny(In, {"includeEndpoints"}, !bSummaryOnly)) - asked for explicitly so
        # the compact response still carries the one thing this script actually needs.
        req = urllib.request.Request(
            base + "/self_audit", data=b'{"summaryOnly":true,"includeEndpoints":true}',
            headers={"X-Mif-Token": "dev", "Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except (urllib.error.URLError, OSError) as exc:
        print("could not reach a bridge on port %d: %s" % (args.port, exc))
        print("start an editor first (DDS2's, or a probe with MIF_BRIDGE_PORT=%d set explicitly - "
              "an unset MIF_BRIDGE_PORT falls back to %d, DDS2's own default, which is how two "
              "editors end up silently sharing one port) and retry." % (args.port, M.BRIDGE_PORT))
        return 1

    if not r.get("ok"):
        print("self_audit itself refused: %s" % json.dumps(r)[:200])
        return 1

    # summaryOnly still returns the full endpoint NAME list - only the per-endpoint detail rows are
    # dropped (see H_self_audit's own doc comment) - so this is the right call, not a compromise.
    endpoints = r.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        print("self_audit answered but carried no endpoints array: %s" % json.dumps(r)[:200])
        return 1

    names = sorted(endpoints)
    snapshot_path = os.path.join(HERE, "endpoints_current.json")
    old = []
    if os.path.exists(snapshot_path):
        try:
            old = json.load(open(snapshot_path, encoding="utf-8"))
        except Exception:
            old = []
    added = sorted(set(names) - set(old))
    removed = sorted(set(old) - set(names))

    # ALWAYS CRLF - this project's convention (docs/18_START_HERE.md), and JSON snapshots are not
    # exempt just because they are data rather than code.
    with open(snapshot_path, "wb") as f:
        text = json.dumps(names, indent=1) + "\n"
        f.write(text.replace("\n", "\r\n").encode("utf-8"))

    print("wrote %d endpoint(s) to %s" % (len(names), os.path.relpath(snapshot_path, HERE)))
    if added:
        print("  +%d newly present: %s%s" % (len(added), ", ".join(added[:10]),
              (" ...and %d more" % (len(added) - 10)) if len(added) > 10 else ""))
    if removed:
        print("  -%d no longer present: %s%s" % (len(removed), ", ".join(removed[:10]),
              (" ...and %d more" % (len(removed) - 10)) if len(removed) > 10 else ""))
    if not added and not removed:
        print("  unchanged - the snapshot already matched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
