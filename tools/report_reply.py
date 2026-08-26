"""Post the outcome of a worked report back onto its GitHub issue.

This is the only part of the loop that reaches OUTWARD. Everything else reads, replays or edits locally;
this publishes under Andre's account to a repository other people can see. That difference is why it
has its own gate rather than being folded into report_repro.

THE GATE IS THE TRUST FILE, deliberately reusing the intake's security boundary rather than inventing a
second one. report_trust.json ships empty, so until Andre puts a login in it nothing is auto-processed
AND nothing is auto-replied to. One file, one decision, both directions - there is no state where the
loop is fixing reports silently but not telling anyone, and none where it is posting about reports it
never worked.

WHAT IT WILL NOT DO. It does not close issues. Closing asserts the reporter's problem is solved, and
that is their call to make after they have retested on their own project - especially since a
shape-only repro can fix the shape of a bug and miss the instance they actually hit. The comment says
what was done and invites them to close it.

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
        "Leaving this open for you to close - a fix verified here is verified against a scratch "
        "reproduction, and you are the one who can confirm it against the asset you actually hit it on."
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

    body = BODY[status].format(
        commit=arg("--commit", "(no commit)"),
        detail=arg("--detail", ""),
        shape=SHAPE_NOTE if "--shape-only" in sys.argv else "",
    )
    # Collapse the blank runs left by empty optional sections.
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    body = body.strip()

    if "--dry-run" in sys.argv:
        print("--- would post to issue #%s ---" % number)
        print(body)
        return 0

    out = subprocess.run(["gh", "issue", "comment", str(number), "--body", body],
                         cwd=HERE, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=120)
    if out.returncode != 0:
        print("gh issue comment failed: %s" % (out.stderr or "").strip()[:300])
        return 1
    print("commented on #%s" % number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
