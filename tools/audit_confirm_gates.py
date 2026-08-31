"""Every endpoint that ACCEPTS a confirm flag must READ it.

A declared `confirm` is a promise. The caller believes the destructive thing cannot happen without
it, and an endpoint that lists `confirm` in RejectUnknownParams and never reads the value has made
that promise and does not keep it. The failure is silent in the worst possible direction: the caller
who forgets confirm gets exactly the destruction they were guarding against, and the response looks
like an ordinary success.

STATIC ON PURPOSE, and this is not a limitation. Testing it live means handing valid arguments to a
destructive endpoint to see whether it stops - the one experiment you cannot afford to have answered
"no". delete_asset, consolidate_assets, break_level_instance and pcg_cleanup are all on this list.

TWO SCRUBBING MISTAKES were made writing this, and both are recorded because the second is the
interesting one:

  Searching a body scrubbed by harvest_param_table.blank_comments_and_strings for `TEXT("confirm")`
  found nothing, because that scrubber BLANKS STRING LITERALS - and here the string content IS the
  evidence. It reported 63 of 65 endpoints as unguarded, which is how it was caught: an implausible
  ratio, not a careful reading. Scrubbing is not free and not always right; the question is whether
  a string is data or evidence, and for five other tools tonight it was data.

  Then `JBool(In, TEXT("confirm"), false)` alone left one apparent failure, move_tree_widget, which
  reads the flag through JBoolAny(In, { TEXT("replaceRoot"), TEXT("confirm") }, false) - confirm as
  an alias. One endpoint out of 57 is exactly the size of finding that turns out to be a missing
  idiom rather than a defect.

Comments are blanked and strings kept: a handler that DISCUSSES confirm in its refusal text has not
read it, and every one of these discusses it at length.
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PRIV = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")

HANDLER = re.compile(r"void\s+H_([A-Za-z0-9_]+)\s*\(\s*const\s+TSharedRef<FJsonObject>&\s*In")

# Every way this module reads the flag. A new idiom belongs here, not in an exception list.
READS = re.compile(
    r'JBool\s*\(\s*In\s*,\s*TEXT\(\s*"confirm"\s*\)'          # the ordinary form
    # NOT [^)]* here: the first ")" is inside TEXT("replaceRoot"), so a negated-paren class stops
    # before it ever reaches confirm. Bounded and non-greedy across anything but a statement end.
    r'|JBoolAny\s*\((?:[^;]{0,240}?)TEXT\(\s*"confirm"\s*\)'   # confirm as one of several spellings
    r'|RequireConfirm|MifRequireConfirm|bConfirm',
    re.S)


def blank_comments(text):
    """Comment CONTENT -> spaces, string literals untouched. Offsets preserved."""
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


def scan():
    accept, read, bad = 0, 0, []
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
            if 'TEXT("confirm")' not in brace_block(body, rj.end() - 1):
                continue
            accept += 1
            if READS.search(body):
                read += 1
            else:
                bad.append((m.group(1), fn))
    return accept, read, bad


def main():
    accept, read, bad = scan()
    print("handlers whose accepted-parameter list contains confirm: %d" % accept)
    print("of those, that READ the flag:                            %d" % read)
    print("")
    if not bad:
        print("OK  every endpoint that accepts confirm enforces it")
        return 0
    print("ACCEPTS confirm AND NEVER READS IT: %d" % len(bad))
    print("The caller believes they are gated. Read the handler before assuming a defect - the")
    print("flag may be read through an idiom this scan does not know, in which case ADD THE IDIOM")
    print("to READS rather than adding the endpoint to an exception list.")
    for name, fn in sorted(bad):
        print("   %-34s %s" % (name, fn))
    return 1


if __name__ == "__main__":
    sys.exit(main())
