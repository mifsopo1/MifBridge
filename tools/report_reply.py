"""Post the outcome of a worked report back onto its GitHub issue.

This is the only part of the loop that reaches OUTWARD. Everything else reads, replays or edits locally;
this publishes under Andre's account to a repository other people can see. That difference is why it
has its own gate rather than being folded into report_repro.

THE GATE IS THE TRUST FILE, deliberately reusing the intake's security boundary rather than inventing a
second one. report_trust.json ships empty, so until Andre puts a login in it nothing is auto-processed
AND nothing is auto-replied to. One file, one decision, both directions - there is no state where the
loop is fixing reports silently but not telling anyone, and none where it is posting about reports it
never worked.

CLOSING. This used to refuse to close issues, on the argument that closing asserts the reporter's
problem is solved and that is their call - especially since a shape-only repro can fix the SHAPE of a
bug and miss the instance they actually hit.

Andre overruled that on 2026-08-27: "if you fix the issue, mark it as fixed yourself on git". His
repository, his call, and the first real report bore him out - issue #1 was fixed autonomously, and
then sat open until he closed it by hand and told the reporter himself. The loop was making a human do
its paperwork.

So --status fixed now closes, and the argument above survives as a NARROWER rule rather than being
thrown away:

  * status `fixed` closes as completed, and ONLY that status closes.
  * a SHAPE-ONLY repro never closes, whatever the status. That is precisely the case the old caution
    was about: the shape is fixed and the reporter's actual instance is untested. It comments and
    leaves the issue open.
  * `not-reproduced` and `needs-you` never close. Neither asserts the problem is solved.

Every close still posts the comment FIRST, so a reader sees what happened rather than an issue that
shut with no explanation.

Usage:
    python tools/report_reply.py --number 7 --status fixed --commit abc1234
    python tools/report_reply.py --number 7 --status not-reproduced
    python tools/report_reply.py --number 7 --status needs-you --detail "only repros with confirm"
    python tools/report_reply.py --number 7 --status fixed --commit abc1234 --dry-run
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRUST_FILE = os.path.join(HERE, "report_trust.json")

BODY = {
    "fixed": (
        "Worked automatically and fixed in `{commit}`.\n\n"
        "{detail}\n\n"
        "The fix was built, the DLL was verified to contain the change, and the full suite regression "
        "ran clean before it was committed.\n\n"
        "{shape}\n\n"
        "{closing}"
    ),
    "not-reproduced": (
        "Worked automatically; **could not reproduce**.\n\n"
        "{detail}\n\n"
        "{shape}\n\n"
        "That is not a rejection of the report - the most common cause is that the bug depends on the "
        "specific asset, which is deliberately never loaded here. If you can narrow it to something "
        "reproducible on a fresh scratch asset, that would make it workable unattended."
    ),
    "needs-you": (
        "Worked automatically; **needs a human**.\n\n"
        "{detail}\n\n"
        "{shape}"
    ),
}

# The two endings for a `fixed` comment. A PAIR, because the comment must match the action: text
# saying "leaving this open for you" on an issue the same script then closes reads as a tool that
# does not know what it did.
CLOSING_CLOSED = (
    "Closing this as fixed. If it still happens on your project, reopen it - a fix verified here is "
    "verified against a scratch reproduction, and yours is the copy that matters."
)
CLOSING_LEFT_OPEN = (
    "Leaving this OPEN deliberately. The reproduction here was shape-only, so the defect is fixed in "
    "the abstract and your actual asset is untested - you are the one who can confirm it. Close it "
    "when you have."
)

SHAPE_NOTE = (
    "_Note: your asset paths were rewritten into scratch space before anything ran, so this tested "
    "the shape of the problem rather than your asset. Your files were never opened._"
)


def gated():
    """Replying is allowed only once Andre has named at least one trusted reporter."""
    try:
        with open(TRUST_FILE, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("trusted"))
    except Exception:
        return False


def arg(name, default=None):
    if name in sys.argv:
        try:
            return sys.argv[sys.argv.index(name) + 1]
        except IndexError:
            return default
    return default


def main():
    number = arg("--number")
    status = arg("--status")
    if not number or status not in BODY:
        print("usage: report_reply.py --number N --status fixed|not-reproduced|needs-you "
              "[--commit SHA] [--detail TEXT] [--shape-only] [--dry-run]")
        return 2

    if not gated():
        print("report_trust.json names no trusted reporters, so the loop is not live and nothing is")
        print("posted. That is the same switch that gates intake - one file, both directions.")
        return 0

    shape_only = "--shape-only" in sys.argv
    # Decided BEFORE the body is built, so the wording and the action cannot disagree.
    should_close = (status == "fixed") and not shape_only

    body = BODY[status].format(
        commit=arg("--commit", "(no commit)"),
        detail=arg("--detail", ""),
        shape=SHAPE_NOTE if shape_only else "",
        closing=CLOSING_CLOSED if should_close else CLOSING_LEFT_OPEN,
    )
    # Collapse the blank runs left by empty optional sections.
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    body = body.strip()

    # Close only on a real, non-shape-only fix. See the module docstring for why those two
    # conditions and not just the first.
    #
    # shape_only is READ FROM argv here rather than assumed. The first version of this line referenced
    # a `shape_only` that did not exist - a NameError before dispatch, which is bit-for-bit the defect
    # infectedcoolpat-jpg reported as issue #1 (move_tree_widget's wrapper passing replaceRoot from an
    # undeclared replace_root). Caught by reading it back; worth recording that I wrote the same bug
    # an hour after fixing it.

    if "--dry-run" in sys.argv:
        print("--- would post to issue #%s ---" % number)
        print(body)
        print("--- would %s ---" % ("CLOSE it as completed" if should_close else "leave it OPEN"))
        return 0

    out = subprocess.run(["gh", "issue", "comment", str(number), "--body", body],
                         cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL, timeout=120)
    if out.returncode != 0:
        print("gh issue comment failed: %s" % (out.stderr or "").strip()[:300])
        return 1
    print("commented on #%s" % number)

    if not should_close:
        # Said out loud, because "it commented and did not close" should never look like a failure to
        # close. not-reproduced and needs-you assert nothing is solved; a shape-only fix leaves the
        # reporter's own instance untested.
        print("  left OPEN deliberately: %s" % (
            "the repro was shape-only, so their actual instance is untested"
            if shape_only else "status %r does not assert the problem is solved" % status))
        return 0

    # COMMENT FIRST, CLOSE SECOND - the order is deliberate. An issue that shuts with no explanation
    # is worse for the reporter than one left open, and if the close fails the explanation still
    # stands on its own.
    out = subprocess.run(["gh", "issue", "close", str(number), "--reason", "completed"],
                         cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL, timeout=120)
    if out.returncode != 0:
        # NOT a failure of this script's main job. The comment landed, which is the part the reporter
        # needs; the close is bookkeeping a human can finish.
        print("  commented, but the close failed: %s" % (out.stderr or "").strip()[:200])
        return 0
    print("  closed #%s as completed" % number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
