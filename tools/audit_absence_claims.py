"""Which documents still claim an endpoint does not exist, when it does?

WHY THIS EXISTS. Four separate claims were disproved on 2026-08-31 by calling the thing they
described, and every one had been filed from a table or a plan and never re-checked against what
shipped:

  * docs/06 issue 27 ended "Still open: add_gameplay_tag" - built the SAME DAY that note was written,
    and it has 19 passing checks. The note was two days stale.
  * the spec said "11 asset types can only be created UNCONFIGURED" - create_asset's own response
    says "set its properties with set_property", and both an InputAction enum and a CurveFloat's
    keys configure fine.
  * the CRLF item said 26 files and said fixing them costs blame; it is 93 and costs nothing.
  * a postmortem said `manage_layers` throughout. There is no manage_layers.

The shape is always the same: a document asserts an ABSENCE, the absence is filled, and nothing
re-reads the document. A claim of absence is the one kind of claim that rots silently, because the
work that falsifies it is exactly the work nobody thinks to come back and re-file.

WHAT IT CHECKS. Sentences that assert something is missing, and name an endpoint-shaped token near
that assertion. If the token is in the LIVE registry, the claim is stale. self_audit is the source of
truth for what exists - never a name list in another document, which is the failure being checked.

Needs a live bridge. Exits 0 when it cannot reach one, because "could not check" is not "is wrong".
"""
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = "http://127.0.0.1:8791/api/"
HDR = {"X-Mif-Token": "dev", "Content-Type": "application/json"}

# DIRECTION IS THE WHOLE DIFFICULTY, and the first version got it wrong in a way worth recording.
#
# It searched a 240-character window AFTER every absence phrase and reported 75 claims, nearly all
# false. The authors of these documents are careful, so an absence is almost always followed by a
# CONTRAST LIST of what does exist: "Does not exist. The DataTable surface is rows-only:
# read_datatable, write_datatable_rows, get_datatable_row..." and "Nothing creates the asset. Note
# the bridge DOES create other user types - create_struct, create_enum, create_blueprint...". Every
# name in those lists was reported as a stale claim. The tool was matching the prose ADJACENT to the
# claim rather than the claim - the same mistake it exists to catch, made by the thing catching it.
#
# So the phrases are split by where their SUBJECT sits, and the window is short enough that a
# following sentence cannot be swept in.
LEADING = re.compile(                      # subject FOLLOWS the phrase
    r"(\bMISSING:|no endpoint\b|there is no\b|nothing (?:creates|adds|exposes|offers|provides)\b|"
    r"\bstill open:|\bno way to\b)", re.I)
TRAILING = re.compile(                     # subject PRECEDES the phrase
    r"(\bdoes not exist\b|\bis not an endpoint\b|\bwas never built\b|\bis not offered\b|"
    r"\bdoes not exist yet\b)", re.I)

ENDPOINTISH = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+){1,4})\b")

# Tokens that look like endpoints and are not.
NOISE = {"pull_request", "text_eol", "no_text", "line_endings", "read_only", "self_audit_modes"}

# Claims that name a live endpoint and are still FAIR COMMENT, each with why. Grammar cannot tell
# these from a stale claim - the difference is what the sentence is about - so they are judged once
# and recorded, rather than the tool being blunted until it says nothing.
ACCEPTED = {
    ("docs/audit/work/P1_graph_layout.md", "run_console"):
        "the absent thing is a ROUTE, not the endpoint: no plugin in that family registers a console "
        "command, so run_console cannot reach them. True, and about capability.",
    ("tools/FEATURE_PARITY_SPEC.md", "list_bones"):
        "the absent thing is 'that case' - the import use-case for IK retargeting - and list_bones is "
        "the endpoint that MEASURED its absence. Named as the instrument, not the subject.",
}

WINDOW = 34           # short on purpose: long enough for "MISSING: create_curve", short enough
                      # that the next sentence - usually the contrast list - cannot reach in.


def live_endpoints():
    req = urllib.request.Request(BASE + "self_audit", json.dumps({}).encode(), HDR)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return set(json.loads(r.read().decode("utf-8", "replace")).get("endpoints") or [])
    except Exception:
        return None


def docs():
    out = [os.path.join(HERE, "FEATURE_PARITY_SPEC.md")]
    for base, _d, files in os.walk(os.path.join(ROOT, "docs")):
        for fn in files:
            if fn.endswith(".md"):
                out.append(os.path.join(base, fn))
    return [p for p in out if os.path.isfile(p)]


def main():
    live = live_endpoints()
    if live is None:
        print("no bridge - cannot check absence claims against the live registry.")
        print("This exits 0 deliberately: 'could not check' is not 'is wrong'.")
        return 0
    print("live endpoints: %d" % len(live))

    hits = []
    for path in docs():
        text = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        spots = ([(m.group(0), text[m.end(): m.end() + WINDOW]) for m in LEADING.finditer(text)]
                 + [(m.group(0), text[max(0, m.start() - WINDOW): m.start()])
                    for m in TRAILING.finditer(text)])
        for phrase, seg in spots:
            # A claim does not carry across a sentence or a paragraph break.
            seg = seg.split("\n\n")[0].split(". ")[0]
            for tok in set(ENDPOINTISH.findall(seg)):
                if tok in NOISE or tok not in live:
                    continue
                line = text[:text.find(seg)].count("\n") + 1 if seg else 0
                hits.append((os.path.relpath(path, ROOT).replace("\\", "/"), line,
                             tok, phrase, " ".join(seg.split())[:150]))

    # One row per (file, endpoint): the same stale claim is often restated a few lines apart.
    seen, rows = set(), []
    for h in hits:
        key = (h[0], h[2])
        if key in seen:
            continue
        seen.add(key)
        rows.append(h)

    known = [r for r in rows if (r[0], r[2]) in ACCEPTED]
    rows = [r for r in rows if (r[0], r[2]) not in ACCEPTED]
    print("absence claims naming a LIVE endpoint: %d new, %d accepted" % (len(rows), len(known)))
    if not rows:
        print("")
        print("OK  no document claims a registered endpoint is missing")
        return 0
    print("")
    print("Each of these says something does not exist, and names something that does. Read it -")
    print("the claim may be about a PARAMETER or a MODE of that endpoint, which is fair comment.")
    for path, line, tok, phrase, seg in sorted(rows):
        print("")
        print("  %s:%d   names %s" % (path, line, tok))
        print("      after %r: %s" % (phrase, seg))
    return 1


if __name__ == "__main__":
    sys.exit(main())
