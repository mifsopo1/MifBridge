"""Find parameters an endpoint ACCEPTS and nothing ever reads.

WHY THIS EXISTS. RejectUnknownParams is this bridge's answer to the silent-parameter-ignore class:
send a name the endpoint does not know and it refuses, loudly, with a hint. That guard is why
spawn_actor_in_level no longer swallows a `mesh` it never applied.

It has a blind spot, and it is the worse half. A name ON the accepted list passes the guard by
definition. If nothing then reads it, the call succeeds, reports ok, and does exactly nothing with
the thing the caller asked for - which is the same silent-wrong-result the guard was built to end,
arriving through the door the guard holds open.

WHAT IT CHECKS. Every name in a RejectUnknownParams accepted list must appear as TEXT("name")
somewhere else in the module - in its own handler, in a shared resolver like ResolveActor, anywhere.
Module-wide scope is deliberately permissive: it will not notice a name read by the WRONG endpoint's
helper. It does not need to. The regression this guards against is someone adding a parameter to an
accepted list and never wiring it up, and such a name appears nowhere else at all.

RUN 2026-08-27, first run: ZERO. Four names looked dead at a tighter scope and all four were fine -
three `actor` aliases read through ResolveActor in another file, and focus_viewport's `all`, which is
accepted and ignored ON PURPOSE because it names the default behaviour and the header comment tells
callers to pass it. That last one is the reason this tool reports rather than edits.

DELIBERATELY IGNORED PARAMETERS ARE FINE, and there is one in the codebase already. If you add
another, say so in a comment beside it - this tool will not flag it (the name still appears in the
list only), but the next reader has no other way to tell it from an oversight.

Usage:
    python tools/audit_dead_params.py            # report
    python tools/audit_dead_params.py --quiet    # exit code only: 0 clean, 1 something to look at
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "..", "Source", "MifBridge", "Private")

LITERAL = re.compile(r'TEXT\("([^"]*)"\)')
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
HANDLER = re.compile(r"void H_(\w+)\(")


def sources():
    return sorted(f for f in os.listdir(PRIVATE) if f.endswith(".cpp"))


def read_lines(fn):
    return io.open(os.path.join(PRIVATE, fn), encoding="utf-8", errors="replace").read().split("\n")


def span_of_call(lines, start):
    """Line index one past the RejectUnknownParams call that begins at `start`, by paren depth."""
    depth, i = 0, start
    while i < len(lines):
        depth += lines[i].count("(") - lines[i].count(")")
        if i > start and depth <= 0:
            return i
        i += 1
    return len(lines) - 1



def handler_spans(lines):
    """[(name, first, last)] for each `void H_x(...)` body, by brace depth.

    Conservative on purpose: a handler whose braces never balance is NOT recorded, so its text
    stays in the shared pool and the check stays permissive there. Under-narrowing costs a missed
    finding; over-narrowing costs a false one, and a false one is what ends a tool's credibility.
    """
    spans = []
    for i, line in enumerate(lines):
        m = HANDLER.search(line)
        if not m:
            continue
        depth, j, opened = 0, i, False
        while j < len(lines):
            depth += lines[j].count("{") - lines[j].count("}")
            if lines[j].count("{"):
                opened = True
            if opened and depth <= 0:
                spans.append((m.group(1), i, j))
                break
            j += 1
    return spans


def literal_pools():
    """(shared, per_handler) - the two sets a name can legitimately be read in.

    WHY THE OLD SCOPE WAS TOO WIDE. names_read_anywhere() unioned the literals of every .cpp in the
    plugin, so a name read by ANY handler counted as read by ALL of them. The header calls that
    "deliberately permissive", and it is - but the one defect it cannot see is a parameter accepted
    by endpoint A and only ever read by endpoint B. That is not hypothetical: _check_format in the
    Blender addon did exactly this with two callers and one shared format list, and told an export
    caller that glTF was supported right up until it refused them for asking.

    THE INVERSION THAT MAKES IT CHEAP. Asking "what can handler H see" for each of 438 handlers
    across ~60 files is O(handlers x files) and rebuilds the same strings hundreds of times. But
    the answer is always the same two pieces - everything OUTSIDE any handler, plus H's own body -
    so both are computed ONCE here and unioned per handler at the point of use.

    Everything outside a handler body stays shared, which is what the tool's own history requires:
    its first run found four names that looked dead at a tighter scope and were all fine, three of
    them `actor` aliases read through ResolveActor IN ANOTHER FILE. ResolveActor is a free
    function, not an H_ handler, so it lives in the shared pool and those three still resolve.
    """
    shared, per = set(), {}
    for fn in sources():
        lines = read_lines(fn)
        masked = list(lines)
        i = 0
        while i < len(lines):
            if "RejectUnknownParams(" in lines[i]:
                end = span_of_call(lines, i)
                for j in range(i, end + 1):
                    masked[j] = ""
                i = end
            i += 1
        spans = handler_spans(lines)
        covered = set()
        for name, lo, hi in spans:
            body = "\n".join(masked[lo:hi + 1])
            per.setdefault(name, set()).update(LITERAL.findall(body))
            covered.update(range(lo, hi + 1))
        outside = "\n".join(masked[k] for k in range(len(masked)) if k not in covered)
        shared |= set(LITERAL.findall(outside))
    return shared, per


def names_read_anywhere():
    """Every TEXT literal in the module that is NOT inside a RejectUnknownParams call.

    The accepted lists are masked out first, or every parameter would count as read by the very list
    that declares it - a check that cannot fail.
    """
    seen = set()
    for fn in sources():
        lines = read_lines(fn)
        masked = list(lines)
        i = 0
        while i < len(lines):
            if "RejectUnknownParams(" in lines[i]:
                end = span_of_call(lines, i)
                for j in range(i, end + 1):
                    masked[j] = ""
                i = end
            i += 1
        seen |= set(LITERAL.findall("\n".join(masked)))
    return seen


def accepted_lists():
    """(handler, file, line, [accepted names]) for every RejectUnknownParams call."""
    out = []
    for fn in sources():
        lines = read_lines(fn)
        for i, line in enumerate(lines):
            m = HANDLER.search(line)
            if not m:
                continue
            start = None
            for j in range(i, min(i + 20, len(lines))):
                if "RejectUnknownParams(" in lines[j]:
                    start = j
                    break
            if start is None:
                continue
            call = "\n".join(lines[start:span_of_call(lines, start) + 1])
            # The FIRST braced list is the accepted set; the last one holds the hint pairs.
            b = call.find("{")
            c = call.find("}", b)
            if b < 0 or c < 0:
                continue
            out.append((m.group(1), fn, i + 1,
                        [x for x in LITERAL.findall(call[b:c]) if IDENT.match(x)]))
    return out


def main():
    quiet = "--quiet" in sys.argv
    shared, per_handler = literal_pools()
    if not shared:
        print("could not read the module sources")
        return 2
    lists = accepted_lists()
    # A name counts as read if it appears in this handler's OWN body, or anywhere outside every
    # handler - free functions, resolvers, tables. Another handler's private body no longer vouches
    # for this one, which is the whole narrowing.
    dead = [(h, fn, ln, [a for a in acc
                         if a not in shared and a not in per_handler.get(h, ())])
            for h, fn, ln, acc in lists]
    dead = [d for d in dead if d[3]]
    total = sum(len(acc) for _, _, _, acc in lists)
    if not dead:
        if not quiet:
            print("params OK - %d accepted parameter(s) across %d endpoint(s), every one is read "
                  "somewhere" % (total, len(lists)))
        return 0
    if not quiet:
        print("%d endpoint(s) accept a parameter that NOTHING reads:" % len(dead))
        for h, fn, ln, names in dead:
            print("  %-32s %-26s %s" % (h[:32], "%s:%d" % (fn, ln), ", ".join(names)))
        print("")
        print("An accepted-but-unread parameter passes RejectUnknownParams by definition, so the call")
        print("succeeds and silently does nothing with what was asked for. Wire it up, drop it from")
        print("the list, or - if it names the default on purpose - say so in a comment beside it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
