"""Every endpoint that ACCEPTS a promise flag must READ it.

A flag like `confirm` or `dryRun` is a PROMISE. The caller believes the destructive thing cannot
happen without confirm, or will not happen at all under dryRun, and an endpoint that lists one in
RejectUnknownParams and never reads the value has made that promise and does not keep it. The
failure is silent in the worst direction: the caller gets exactly what they were guarding against,
and the response looks like an ordinary success.

FOUR FLAGS, because they fail the same way. This file was renamed from `audit_confirm_gates.py` when
the second flag was added - a checker called "confirm gates" that checks four things is the
title-outlives-its-revision drift docs/02 records, and it took twenty minutes to earn that name.

    confirm     57 endpoints - the destructive thing happens when it was meant to be gated
    save         7 - writes to disk when the caller asked it not to
    dryRun       5 - MUTATES when explicitly asked only to report
    allOrFail    1 - applies partially when atomicity was requested

STATIC ON PURPOSE, and that is not a limitation. Testing this live means handing valid arguments to
a destructive endpoint to see whether it stops - the one experiment you cannot afford to have
answered "no". delete_asset, consolidate_assets, break_level_instance and pcg_cleanup are all here.

TWO SCRUBBING MISTAKES were made writing this, and the second is the interesting one:

  Searching a body scrubbed by harvest_param_table.blank_comments_and_strings for TEXT("confirm")
  found nothing, because that scrubber BLANKS STRING LITERALS - and here the string content IS the
  evidence. It reported 63 of 65 endpoints unguarded, and what caught it was the implausible ratio
  rather than a careful reading. Scrubbing is not free and not always right: the question is whether
  a string is DATA or EVIDENCE, and for five other tools fixed the same night it was data.

  Then matching only `JBool(In, TEXT("confirm")` left one apparent failure - move_tree_widget, which
  reads it through JBoolAny(In, { TEXT("replaceRoot"), TEXT("confirm") }, false), confirm as an
  alias. The first JBoolAny pattern used [^)]* and STILL missed it, because the first ")" is inside
  TEXT("replaceRoot"). One failure out of 57 is exactly the size of finding that turns out to be a
  missing idiom, which is why this file says to add the idiom rather than an exception list.

Comments are blanked and strings are kept: a handler that DISCUSSES a flag in its refusal text has
not read it, and every one of these discusses it at length.
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PRIV = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")

HANDLER = re.compile(r"void\s+H_([A-Za-z0-9_]+)\s*\(\s*const\s+TSharedRef<FJsonObject>&\s*In")

FLAGS = ("confirm", "save", "dryRun", "allOrFail")


def reads_re(flag):
    """Every way this module reads a flag. A NEW IDIOM BELONGS HERE, not in an exception list.

    Not `[^)]*` between the call and the flag name: the first ")" can be inside an earlier
    TEXT("..."), which is how move_tree_widget's JBoolAny form survived the first attempt. Bounded
    and non-greedy across anything except a statement end.
    """
    extra = r"|RequireConfirm|MifRequireConfirm|bConfirm" if flag == "confirm" else ""
    return re.compile(
        r'JBool\w*\s*\((?:[^;]{0,240}?)TEXT\(\s*"' + re.escape(flag) + r'"\s*\)' + extra,
        re.S)


def blank_comments(text):
    """Comment CONTENT -> spaces, string literals untouched. Byte offsets preserved."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            q = c
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            if text[i + 1] == "/":
                j = text.find("\n", i)
                j = n if j < 0 else j
            else:
                j = text.find("*/", i + 2)
                j = n if j < 0 else j + 2
            for k in range(i, j):
                if text[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def brace_block(text, start):
    j = text.find("{", start)
    if j < 0:
        return ""
    depth, k = 0, j
    while k < len(text):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
        k += 1
    return text[j:]


def scan(flag):
    """(accepted, read, [(endpoint, file)]) for one flag."""
    reads, accept, read, bad = reads_re(flag), 0, 0, []
    for fn in sorted(os.listdir(PRIV)):
        if not fn.endswith(".cpp"):
            continue
        src = io.open(os.path.join(PRIV, fn), encoding="utf-8",
                      errors="replace").read().replace("\r\n", "\n")
        for m in HANDLER.finditer(src):
            body = blank_comments(brace_block(src, m.end()))
            rj = re.search(r"\bRejectUnknownParams\s*\(", body)
            if not rj:
                continue
            if ('TEXT("%s")' % flag) not in brace_block(body, rj.end() - 1):
                continue
            accept += 1
            if reads.search(body):
                read += 1
            else:
                bad.append((m.group(1), fn))
    return accept, read, bad


def main():
    bad = []
    for flag in FLAGS:
        a, r, b = scan(flag)
        print("  %-10s accepted by %3d handler(s), read by %3d" % (flag, a, r))
        bad += [(flag, n, f) for n, f in b]
    print("")
    if not bad:
        print("OK  every endpoint that accepts a promise flag enforces it")
        return 0
    print("ACCEPTS A FLAG AND NEVER READS IT: %d" % len(bad))
    print("The caller believes they are gated. Read the handler before assuming a defect - the flag")
    print("may be read through an idiom this scan does not know, in which case ADD THE IDIOM to")
    print("reads_re() rather than adding the endpoint to an exception list.")
    for flag, name, fn in sorted(bad):
        print("   %-10s %-32s %s" % (flag, name, fn))
    return 1


if __name__ == "__main__":
    sys.exit(main())
