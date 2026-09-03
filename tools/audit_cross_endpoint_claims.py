"""When a response tells the caller to go call another endpoint, does anything check that it is true?

WHY THIS EXISTS. On 2026-08-31 override_inherited_component's refusal was found to carry this note:

    "list_components on this blueprint returns the same set, plus the template path and the exact
     endpoint to call for each row"

That is a claim about a DIFFERENT endpoint, made in prose, and nothing compared the two. It happens
to be true. The source comment beside it records that an EARLIER version of the same note was wrong
in exactly that way - it pointed at a list which structurally could not contain an inherited or
native row, and "looked complete, said so, and was the very thing added to stop a caller guessing at
what exists". A promise about another endpoint is only worth making if something compares them.

HOW THIS DIFFERS FROM audit_advice_gaps.py, which is the neighbouring tool and must not be
duplicated. That one asks whether the advice names an operation that EXISTS - a verb no endpoint and
no addon op provides. This one asks the opposite half: the named endpoint exists, so is the CLAIM
about it ever exercised? Those are different failures. An endpoint that does not exist is a broken
sentence; an endpoint that exists and does not do what the sentence says is a lie a caller will act
on, and it survives every check in this folder.

WHAT IT MEASURES, and it is a proxy rather than a proof. A claim in prose cannot be verified
mechanically. What CAN be checked is whether any single suite drives both endpoints - the speaker and
the one it names - because a suite that never calls both cannot possibly have compared them. That is
a necessary condition, not a sufficient one: two endpoints appearing in one file proves nothing about
whether the claim was tested. Read the hits; do not count them.

DELIBERATELY NOT A GATE. It exits 0 always, the same rule audit_advice_gaps runs on and for the same
reason: a tool that failed the build over prose would be satisfied by deleting the prose, which makes
the source worse. This is a reading list.

Usage:
    python tools/audit_cross_endpoint_claims.py              # equivalence/completeness claims
    python tools/audit_cross_endpoint_claims.py --navigation # every mention, including hints
    python tools/audit_cross_endpoint_claims.py --all        # show the paired ones too

Talks to nothing. Source and suites, both static.
"""
import argparse
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "Source", "MifBridge", "Private")
HANDLERS_H = os.path.join(SRC, "MifBridgeHandlers.h")

sys.path.insert(0, HERE)
import harvest_param_table as H          # the one function-body walk and comment scrubber

DECL = re.compile(r"^\s*MIF_DECL\((\w+)\)", re.M)
# Only string LITERALS, and only the ones a caller actually reads back: notes, warnings and the text
# handed to Fail(). A name inside a comment is documentation for the next maintainer, not a promise
# to a caller, and mixing the two is how audit_blocking spent a day red on prose.
# ADJACENT LITERALS ARE THE HOUSE STYLE, and this pattern assumed ONE fragment until
# 2026-08-31, when audit_detectors_fire caught this tool ASLEEP against a planted claim. The
# plant was written the way the module actually writes refusals -
#
#     TEXT("probeSameZz - list_blueprints returns the same set "
#          "as this endpoint, so either will do. ")
#
# - and the old pattern needed the closing paren right after ONE closing quote, so a
# two-fragment literal matched nothing at all and every multi-line claim in the module was
# invisible. audit_editor_fatal_guards had this IDENTICAL bug, was fixed, and the fix never
# reached here - its own header even records the lesson. The line below is copied from that
# tool character-for-character so grepping one finds both.
TEXT_LIT = re.compile(r'TEXT\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\)')

# The capture now holds every fragment WITH its quotes, so they are stripped and joined - the
# same concatenation the C++ compiler performs.
FRAGMENT = re.compile(r'"((?:[^"\\]|\\.)*)"')

# Names too short or too generic to be evidence of anything. `batch` and `compile` appear inside
# ordinary English; `describe_endpoint` and `self_audit` are named by dozens of handlers as the
# generic "go look it up" pointer rather than as a claim about behaviour.
IGNORE = {"batch", "compile", "describe_endpoint", "self_audit", "ping"}

# THE FILTER THAT MAKES THIS READABLE, and getting it wrong first is what taught the shape. The
# unfiltered scan found 546 mentions and 219 with no suite driving both sides - a list nobody reads,
# because most of it is NAVIGATION rather than assertion: "save_package to persist", "set it
# afterwards with set_property", "list_bones reports the bones". Those tell a caller where to go
# next. They promise nothing, so there is nothing to be wrong about.
#
# What CAN be wrong is a claim of EQUIVALENCE or COMPLETENESS - "returns the same set", "lists them
# all", "is uncapped", "returns everything". Those assert a property of the other endpoint's output,
# and that is precisely the sentence that was already found wrong once here: an earlier version of
# override_inherited_component's note pointed at a list which structurally could not contain an
# inherited or native row and said it was the same set.
#
# Filtering on the claim rather than on the named endpoint also keeps the useful hits that a
# name-based ignore list would drop - "list_bones lists them all" is a completeness claim and stays.
CLAIM_SHAPES = [
    "same set", "the same", "all of them", "lists them all", "reports them all",
    "uncapped", "not capped", "everything", "the real set", "the whole set", "the full set",
    "complete list", "the complete", "is the same", "identical",
]


# A DENIAL OF EQUIVALENCE IS NOT AN EQUIVALENCE CLAIM, and reading only the shapes made one.
# set_data_layer_visibility's note says an unloaded layer "is not the same as hidden" - it exists to
# tell you the two endpoints DIFFER, which is the opposite of the thing worth checking, and it was
# sitting on the list as one of sixteen claims to verify.
#
# The negation has to be IMMEDIATELY before the shape. "returns the same set, not a subset" is a
# positive claim with a trailing denial and must stay; a window of a few words is what separates
# them.
NEGATED = re.compile(r'\b(?:not|never|isn\'t|aren\'t|rather than)\s+(?:\w+\s+){0,2}$')


def asserts_equivalence(text):
    """True when the text claims two endpoints AGREE, rather than denying that they do."""
    low = text.lower()
    for shape in CLAIM_SHAPES:
        at = low.find(shape)
        while at >= 0:
            if not NEGATED.search(low[max(0, at - 28):at]):
                return True
            at = low.find(shape, at + 1)
    return False


def endpoint_names():
    text = io.open(HANDLERS_H, encoding="utf-8", errors="replace").read()
    return {m.group(1) for m in DECL.finditer(text)} - IGNORE


def claims(names):
    """[(speaker, named, file, line, quote)] - a handler's own text naming another endpoint."""
    rows = []
    ordered = sorted(names, key=len, reverse=True)     # longest first, so add_cast never eats add_c
    for path in sorted(glob.glob(os.path.join(SRC, "*.cpp"))):
        raw = io.open(path, encoding="utf-8", errors="replace").read()
        scrubbed = H.blank_comments_and_strings(raw)
        base = os.path.basename(path)
        for fn, start, end in H.function_spans(raw, scrubbed):
            if not fn.startswith("H_"):
                continue
            speaker = fn[2:]
            for m in TEXT_LIT.finditer(raw, start, end):
                body = "".join(FRAGMENT.findall(m.group(1)))
                for other in ordered:
                    if other == speaker:
                        continue
                    # Word-boundaried: `remove_pin` must not match inside `remove_pins_zz`.
                    if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(other), body):
                        line = raw.count("\n", 0, m.start()) + 1
                        rows.append((speaker, other, base, line, body.strip()[:140]))
                        break                          # one claim per literal is enough to read
    return rows


def suite_endpoints():
    """suite file -> the endpoint names it calls."""
    out = {}
    call = re.compile(r'["\'](\w+)["\']')
    for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        text = io.open(path, encoding="utf-8", errors="replace").read()
        out[os.path.basename(path)] = set(call.findall(text))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="list every mention, not just the unpaired")
    ap.add_argument("--navigation", action="store_true",
                    help="include the navigational hints too - 219 of them, mostly not claims")
    args = ap.parse_args()

    names = endpoint_names()
    if len(names) < 200:
        print("SELF-CHECK FAILED: only %d MIF_DECL names found - the extraction has drifted."
              % len(names))
        return 2
    rows = claims(names)
    if not rows:
        print("SELF-CHECK FAILED: no handler text names any other endpoint, which is not credible "
              "in a module whose refusals routinely point elsewhere.")
        return 2

    suites = suite_endpoints()
    print("endpoints: %d   cross-endpoint claims in handler text: %d" % (len(names), len(rows)))

    if not args.navigation:
        shaped = [r for r in rows if asserts_equivalence(r[4])]
        print("of those, ones asserting EQUIVALENCE or COMPLETENESS: %d   (the rest are "
              "navigation - 'save_package to persist' promises nothing)" % len(shaped))
        rows = shaped

    unpaired = []
    for speaker, other, base, line, quote in rows:
        together = [s for s, eps in suites.items() if speaker in eps and other in eps]
        if not together:
            unpaired.append((speaker, other, base, line, quote))
        elif args.all:
            print("  paired   %-32s -> %-30s %s" % (speaker, other, ", ".join(sorted(together)[:2])))

    print("claims NO single suite exercises both sides of: %d" % len(unpaired))
    print("")
    for speaker, other, base, line, quote in unpaired:
        print("  %s  says  %s" % (speaker, other))
        print("      %s:%d  %s" % (base, line, quote))
    print("")
    print("A pair appearing in one suite proves only that both were CALLED there, never that the")
    print("claim was compared. This is a reading list - read the hits, do not count them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
