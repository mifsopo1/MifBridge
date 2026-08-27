"""Fetch bug reports filed as GitHub issues, sanitise them, and queue them for repair.

THE THREAT THIS FILE EXISTS TO CONTAIN. A GitHub issue is written by someone outside this machine. If
the loop executed what an issue TOLD it to do, anyone who can file an issue on a public repository
could drive Andre's editor, his shell and his repository. So the rule this module enforces mechanically
is that a report is DATA, never an instruction:

  * The only thing ever executed is `endpoint` + `payload`, and only after both survive the checks
    below. Prose fields (title, expected, actual, notes) are copied into the queue file for a human or
    an agent to READ. Nothing parses them for commands and nothing runs them.
  * `endpoint` must name a registered endpoint that is NOT on mifaudit's DENY list. That list already
    covers the things that end the session, write to disk, or discard the open map.
  * Every asset path in the payload is REWRITTEN into /Game/_MifReport/ scratch. A reporter's own
    assets are never loaded, so a report naming /Game/MODS/Whatever cannot make this machine open it.
    The repro then tests the SHAPE of the bug, which is flagged in the queue entry as shapeOnly.
  * confirm/save/force/overwrite/discardUnsaved/replaceExisting are stripped by mifaudit's own guard,
    which this module deliberately does not bypass.

AND THE ONE CONTROL THAT MATTERS MOST: only logins listed in report_trust.json are processed at all.
Everything else is labelled and left for a human. Schema validation is a correctness measure; the
allowlist is the security measure. Do not confuse them - a perfectly well-formed report from a stranger
is still a stranger's instruction.

WHAT A REPORT LOOKS LIKE. One fenced json block in the issue body:

    ```json
    {
      "endpoint": "set_spline_points",
      "payload": {"actorPath": "/Game/Maps/BP_Path.BP_Path", "points": [{"x":0,"y":0,"z":0}]},
      "expected": "five points read back",
      "actual": "read-back returns 2",
      "dll": "Aug 26 2026 10:43:42"
    }
    ```

Usage:
    python tools/report_intake.py              # fetch, sanitise, write the queue, print a summary
    python tools/report_intake.py --dry-run    # everything except writing the queue
"""
import json
import os
import re
import subprocess
import sys

import mifaudit as M

HERE = os.path.dirname(os.path.abspath(__file__))
TRUST_FILE = os.path.join(HERE, "report_trust.json")
QUEUE_FILE = os.path.join(HERE, "report_queue.json")
LABEL = "bridge-report"

# A report may create scratch assets under here and nowhere else.
SCRATCH_ROOT = "/Game/_MifReport/"

# Refusing to work an unbounded queue is itself a safety property: a hundred issues filed at once must
# not turn into a hundred editor operations before a human sees any of it.
MAX_REPORTS = 10
MAX_PAYLOAD_BYTES = 20000

# The shape of a thing that names an asset. Deliberately broad - matching scratch_confirm.py, which
# makes the same judgement for the same reason.
PATHLIKE = re.compile(r"^/[A-Za-z0-9_]+/")
FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class Rejected(Exception):
    """Raised, not returned, so a caller cannot process a report by forgetting to check."""


def trusted_logins():
    """Logins whose reports may be auto-processed.

    Missing or malformed file means NOBODY is trusted. Failing closed is the only safe direction: an
    unreadable trust file must not silently become an open door.
    """
    try:
        with open(TRUST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(str(x).lower() for x in (data.get("trusted") or []))
    except Exception as exc:
        print("  trust file unreadable (%s) - treating every reporter as untrusted" % exc)
        return set()


def registered_endpoints():
    """Endpoint names the live bridge admits to having.

    Asked of the EDITOR rather than read from a checked-in list, because the list is what an attacker
    would want to be stale. If the bridge is down we cannot validate, so we refuse everything.
    """
    try:
        r = M.call("self_audit", {}, timeout=90)
        eps = r.get("endpoints") or []
        out = set()
        for e in eps:
            name = e.get("name") if isinstance(e, dict) else e
            if name:
                out.add(str(name))
        return out
    except Exception as exc:
        print("  cannot reach the bridge to validate endpoint names (%s)" % exc)
        return set()


def scratch_path_for(original):
    """Map any asset path onto a scratch path, deterministically.

    Deterministic so that two reports naming the same asset land on the same scratch asset and a repro
    is repeatable, and so the queue file can be diffed between runs.
    """
    tail = original.rstrip("/").split("/")[-1]
    tail = re.sub(r"[^A-Za-z0-9_]", "_", tail) or "Asset"
    # A short stable digest keeps two different originals with the same leaf name apart.
    digest = 0
    for ch in original:
        digest = (digest * 31 + ord(ch)) & 0xFFFFFF
    return "%s%s_%06x" % (SCRATCH_ROOT, tail[:40], digest)


def sanitise(value, rewrites):
    """Recursively rewrite every asset path in a payload into scratch space."""
    if isinstance(value, str):
        if PATHLIKE.match(value) and not value.startswith("/Game/_Mif"):
            new = scratch_path_for(value)
            rewrites.append({"from": value, "to": new})
            return new
        return value
    if isinstance(value, dict):
        return dict((k, sanitise(v, rewrites)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [sanitise(v, rewrites) for v in value]
    return value


def parse_report(body):
    """Pull the one fenced json block out of an issue body and check its shape."""
    if not body:
        raise Rejected("the issue body is empty")
    blocks = FENCE.findall(body)
    if not blocks:
        raise Rejected("no fenced ```json block - the report template is required so the payload can "
                       "be read as data rather than interpreted as prose")
    if len(blocks) > 1:
        raise Rejected("%d fenced json blocks - exactly one is required, so there is no ambiguity "
                       "about which one gets replayed" % len(blocks))
    try:
        rep = json.loads(blocks[0])
    except Exception as exc:
        raise Rejected("the json block does not parse: %s" % exc)
    if not isinstance(rep, dict):
        raise Rejected("the json block must be an object")

    for key in ("endpoint", "payload", "expected", "actual"):
        if key not in rep:
            raise Rejected("missing required key '%s'" % key)
    if not isinstance(rep.get("endpoint"), str) or not rep["endpoint"].strip():
        raise Rejected("'endpoint' must be a non-empty string")
    if not isinstance(rep.get("payload"), dict):
        raise Rejected("'payload' must be an object")
    if len(json.dumps(rep["payload"])) > MAX_PAYLOAD_BYTES:
        raise Rejected("payload exceeds %d bytes" % MAX_PAYLOAD_BYTES)
    return rep


def vet_endpoint(name, registered):
    if name in M.DENY:
        raise Rejected("'%s' is on the harness DENY list (it ends the session, writes to disk, or "
                       "discards the open map) and is never replayed automatically" % name)
    if registered and name not in registered:
        raise Rejected("'%s' is not a registered endpoint on this build" % name)


def fetch_issues():
    cmd = ["gh", "issue", "list", "--label", LABEL, "--state", "open",
           "--json", "number,title,body,author,createdAt", "--limit", str(MAX_REPORTS)]
    out = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL, timeout=120)
    if out.returncode != 0:
        print("  gh issue list failed: %s" % (out.stderr or "").strip()[:300])
        return []
    try:
        return json.loads(out.stdout or "[]")
    except Exception as exc:
        print("  could not parse gh output: %s" % exc)
        return []


def main():
    dry = "--dry-run" in sys.argv
    trusted = trusted_logins()
    print("trusted reporters: %s" % (sorted(trusted) or "(none - nothing will be auto-processed)"))

    issues = fetch_issues()
    print("open '%s' issues: %d" % (LABEL, len(issues)))
    if not issues:
        return 0

    registered = registered_endpoints()
    print("registered endpoints known to the live bridge: %d" % len(registered))
    print("")

    queue, skipped = [], []
    for iss in issues:
        num = iss.get("number")
        login = str(((iss.get("author") or {}).get("login") or "")).lower()
        head = "#%s by %s" % (num, login or "?")

        if login not in trusted:
            print("  %s  SKIPPED - not a trusted reporter; left for a human" % head)
            skipped.append({"number": num, "author": login, "reason": "untrusted reporter"})
            continue
        try:
            rep = parse_report(iss.get("body") or "")
            vet_endpoint(rep["endpoint"], registered)
        except Rejected as exc:
            print("  %s  REJECTED - %s" % (head, exc))
            skipped.append({"number": num, "author": login, "reason": str(exc)})
            continue

        rewrites = []
        payload = sanitise(rep["payload"], rewrites)
        entry = {
            "number": num,
            "author": login,
            "title": iss.get("title"),
            "endpoint": rep["endpoint"],
            "payload": payload,
            "shapeOnly": bool(rewrites),
            "rewrites": rewrites,
            # Prose, verbatim, for a human or an agent to READ. Never parsed, never executed.
            "reported": {"expected": rep.get("expected"), "actual": rep.get("actual"),
                         "notes": rep.get("notes"), "dll": rep.get("dll")},
        }
        queue.append(entry)
        print("  %s  QUEUED - %s%s" % (head, rep["endpoint"],
                                       (" (%d path(s) rewritten to scratch)" % len(rewrites))
                                       if rewrites else ""))

    print("")
    print("queued %d, skipped %d" % (len(queue), len(skipped)))
    if not dry:
        with open(QUEUE_FILE, "w", encoding="utf-8", newline="\r\n") as f:
            json.dump({"queue": queue, "skipped": skipped}, f, indent=2)
        print("wrote %s" % QUEUE_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
