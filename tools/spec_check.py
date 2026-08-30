"""Find spec items that are open but already done, and items written down twice.

WHY THIS EXISTS. Four times on 2026-08-27 an item was built, ticked, and kept surfacing as "next" -
PCG twice, Sequencer twice, Niagara once, and the write half of each. The cause is always the same
shape:

    a decline written in two places gets reopened in two places and then ticked in ONE

The copy nobody edited stays `- [ ]` forever and keeps being offered as the next thing to do. Each
time I closed it by hand, noticed the pattern, and closed the next one by hand as well.

So this checks it instead. Two rules, and both are advisory - it never edits the spec, because "this
looks done" is a judgement and a tool that ticks items on a name match would quietly hide real work.

  1. AN OPEN ITEM WHOSE ENDPOINTS EXIST. Matches an open `- [ ]` item's TITLE against MIF_DECL names
     in the header. Deliberately conservative: it needs a full endpoint name, so a vague item is not
     flagged on a coincidence.
     TITLE, NOT BODY, since 2026-08-30 - and the change is a narrowing, made after filing 56 surveyed
     gaps at once produced three false positives in a row. The bug this rule exists for is an item
     that says BUILD X while X exists, and X is named in the title; a body naturally names related
     endpoints as context ("...unlike find_assets, which...") without asking for any of them. Two
     further exemptions for the same reason: an item whose title starts with an extend/expand verb
     legitimately names the endpoint it extends - this codebase deliberately prefers a parameter on
     an existing endpoint over a new name, so "extend add_sequence_track with root:true" is open work
     whose title MUST mention add_sequence_track - and an item whose body is marked **BUILT is
     recording its own closure.
  2. TWO ITEMS WITH THE SAME BOLD TITLE. `- [ ] **PCG**` twice is the exact failure above. Reported
     whatever their checkbox state, since a `- [x]` and a `- [ ]` for one title is precisely the mess.
  3. A TICKED ITEM WHOSE OWN BODY SAYS IT IS NOT DONE. Rules 1 and 2 only ever look at the checkbox,
     and an item can contradict itself underneath one. Found on 2026-08-27: the write-mode dropdown
     read `- [x] ... DONE 2026-08-27` and then, four lines down, `Designed, NOT built. ... Next up.`
     Both halves had been true, at different times, and nothing said which was current - so one entry
     was simultaneously evidence that the work was finished and that it was the next thing to do.
     Advisory like the others, and deliberately loose: an item legitimately saying 'NOT built on 5.3'
     is flagged too. A false flag costs a glance; a self-contradicting tracker costs a rebuild.

Usage:
    python tools/spec_check.py            # report
    python tools/spec_check.py --quiet    # exit code only: 0 clean, 1 something to look at
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "FEATURE_PARITY_SPEC.md")
HEADER = os.path.join(HERE, "..", "Source", "MifBridge", "Private", "MifBridgeHandlers.h")


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")


def built_endpoints():
    try:
        return set(re.findall(r"MIF_DECL\(([a-z_0-9]+)\)", read(HEADER)))
    except Exception:
        return set()


def items(text):
    """(line number, checkbox, title, full text) for every spec item."""
    out = []
    for i, line in enumerate(text.split("\n")):
        m = re.match(r"^\s*-\s*\[([ x~])\]\s*(.*)$", line)
        if not m:
            continue
        body = m.group(2)
        title = re.match(r"\*\*(.+?)\*\*", body)
        out.append((i + 1, m.group(1), title.group(1) if title else body[:60], body))
    return out


# Phrases that mean 'this is not finished'. Matched case-insensitively against the CONTINUATION
# lines of a ticked item, never its first line - a DONE line often says what it replaced.
STALE = (
    "not built",
    "not started",
    "next up",
    "not yet built",
    "not implemented",
)


def bodies(text):
    """(line number, checkbox, title, continuation text) for every item.

    An item's body is every following line indented past the bullet and not itself a bullet. Kept
    separate from items() rather than folded into it, because rules 1 and 2 want the FIRST line only
    and would start matching on quoted history if they saw the whole entry.
    """
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^\s*-\s*\[([ x~])\]\s*(.*)$", line)
        if not m:
            continue
        title = re.match(r"\*\*(.+?)\*\*", m.group(2))
        body = []
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                break
            if re.match(r"^\s*-\s*\[[ x~]\]", nxt) or not nxt.startswith(" "):
                break
            body.append(nxt)
        out.append((i + 1, m.group(1), title.group(1) if title else m.group(2)[:60],
                    chr(10).join(body)))
    return out


def main():
    quiet = "--quiet" in sys.argv
    try:
        text = read(SPEC)
    except Exception as exc:
        print("could not read the spec: %s" % exc)
        return 2

    built = built_endpoints()
    all_items = items(text)
    problems = []

    # 1. open, but the endpoints it names already exist
    #
    # THE FALSE-POSITIVE CLASS, found 2026-08-30 by filing 56 surveyed gaps at once. This codebase
    # deliberately prefers "add a parameter to an existing endpoint" over "add a new endpoint name",
    # so a perfectly good OPEN item routinely NAMES an endpoint that exists - that is the whole
    # point of it. "extend add_sequence_track with root:true" is open work whose title must mention
    # add_sequence_track, and flagging it as already-built is exactly backwards.
    #
    # The rule still has to fire on the case it was built for: an item that says "build X" where X
    # now exists. So the exemption is narrow - an item whose title opens with an extend/expand verb,
    # or that is explicitly marked BUILT with a date. Anything else naming a live endpoint is still
    # reported.
    for lineno, box, title, body in all_items:
        if box != " ":
            continue
        if "**BUILT" in body:
            continue

        # Every endpoint-SHAPED token in the title: lowercase, snake_case, long enough that a
        # coincidence is implausible. Deliberately not every word - `saveConfig` and `root:true`
        # are parameter names, not endpoints, and matching them would flag on punctuation.
        tokens = set(re.findall(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", title))
        tokens = {t for t in tokens if len(t) > 8}
        named = sorted(tokens & set(built))
        missing = tokens - set(built)

        # FLAG ONLY WHEN NOTHING IN THE TITLE IS STILL MISSING. Narrowed 2026-08-30 after filing 56
        # surveyed gaps at once, which produced five false positives in a row and one earlier
        # attempt at a phrase-marker exemption that kept needing another phrase.
        #
        # The bug this rule exists for is an open item asking to BUILD something that already
        # exists - `- [ ] **create_water_zone**` when create_water_zone is bound. In that case every
        # endpoint the title names exists, and there is nothing left to do.
        #
        # But this codebase deliberately prefers extending an existing endpoint over adding a new
        # name, so a great many legitimate open items name BOTH: the new thing and the endpoint it
        # attaches to. "add_sequence_section + set_sequence_keys (plus sections reported back by
        # list_sequence_bindings)" names one built endpoint and two that do not exist - the open
        # work is real and the item must not be flagged. If ANY named endpoint is still missing, the
        # item has work left, whatever else it mentions.
        # AND it must not be an EXTEND item. The two conditions catch different halves of the same
        # preference: this codebase would rather add a parameter to an existing endpoint than add a
        # new name, so an open item routinely names endpoints that exist. Where it names a new one
        # too, `missing` covers it; where the whole item IS an extension ("extend
        # get_viewport_camera with viewMode"), every name in the title necessarily exists and only
        # the leading verb distinguishes it from a stale build-X item.
        low = title.strip().lower()
        if any(low.startswith(v) for v in ("extend ", "expand ", "extending ")):
            continue

        if named and not missing:
            problems.append(
                "L%-5d OPEN but built: %s\n           names %s, which exist in MifBridgeHandlers.h"
                % (lineno, title[:58], ", ".join(sorted(named)[:3])))

    # 2. the same title written twice
    seen = {}
    for lineno, box, title, _ in all_items:
        key = title.strip().lower()
        seen.setdefault(key, []).append((lineno, box))
    for key, where in sorted(seen.items()):
        if len(where) < 2:
            continue
        boxes = "".join(b for _, b in where)
        # Two [~] declines of one thing is untidy but harmless. A MIX is the failure that keeps
        # resurfacing: one copy ticked, another still open.
        if len(set(boxes)) > 1:
            problems.append(
                "       DUPLICATE with mixed state: '%s' at %s\n"
                "           one copy was ticked and another was not - the untouched one keeps "
                "surfacing as 'next'" % (key[:52], ", ".join("L%d[%s]" % w for w in where)))

    # 3. ticked, but its own body says otherwise
    for lineno, box, title, body in bodies(text):
        if box != "x" or not body:
            continue
        # Per LINE, not per body, so a nearby marker can speak for the phrase beside it - and so an
        # entry can say 'NOT BUILT, deliberately' or record its own history without being flagged
        # forever. An escape hatch a reader can SEE beats a rule too timid to fire.
        hits = []
        for bl in body.splitlines():
            low = bl.lower()
            if "deliberately" in low or "history" in low or "previously read" in low:
                continue
            hits += [w for w in STALE if w in low and w not in hits]
        if hits:
            problems.append(
                "L%-5d TICKED but its body says otherwise: %s\n"
                "           contains %s - if that is history, say so on the line itself"
                % (lineno, title[:58], ", ".join("'%s'" % h for h in hits)))

    if not problems:
        if not quiet:
            print("spec OK - %d items, no open-but-built, no mixed duplicates" % len(all_items))
        return 0
    if not quiet:
        print("spec: %d thing(s) to look at" % len(problems))
        for p in problems:
            print("  " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
