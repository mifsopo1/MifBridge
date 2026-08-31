"""Suite calls that pass a parameter the endpoint REFUSES - tests that exercise their own typo.

WHY THIS EXISTS. test_audit_fixes' T44 called add_enum_literal with `enum`. The endpoint refuses that
by name - "spell it enumName here - list_enum_values takes either, this endpoint reads only enumName"
- so every run failed on the parameter name, took the `if r.get("ok") is False` branch, and that
branch asserted literally `check("T44 bad enumerator refused outright", True)`. Green for weeks,
testing nothing but its own spelling.

That failure is invisible to every other check in this directory. coverage_gaps sees the endpoint
NAMED in a suite. audit_suite_reach sees the assertions RUN. Both are satisfied by a call that never
reaches the endpoint's body.

WHAT IS COMPARED. Suite call sites - M.call / M.raw_post / SC.confirm_call and the module-level
post() helpers - against the accepted-key list in each handler's RejectUnknownParams, read from the
SOURCE by param_reach.endpoint_accepts(). Aliases are included there, so a suite using any accepted
spelling is fine.

DELIBERATELY-REFUSED KEYS ARE THE POINT OF SOME TESTS. A suite that asserts an unknown parameter is
rejected must pass an unknown parameter. Those are recognised two ways: a key matching the obvious
fuzz spellings, and any call whose surrounding line mentions refuse/reject/unknown/unrecognised. What
survives is a call that MEANT to work and does not.
"""
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import param_reach as PR

CALL = re.compile(
    r'(?:M\.call|M\.raw_post|SC\.confirm_call|post)\s*\(\s*["\']([a-z0-9_]+)["\']\s*,\s*\{')

# Keys a test passes ON PURPOSE to see them refused.
JUNK = re.compile(r"^(zzz|__|nope|bogus|bad|junk|xyzzy|notaparam|unknown)", re.I)
# A test that MEANS to be refused says so in one of these ways. The list grew after the first run:
# of five candidates, four were deliberate and only ONE said "refused" - the others read "points at
# the real key", "the 'axis' hint points at set_property", and "points at the write half". A refusal
# test is usually written as advice-checking, not as refusal-checking.
INTENT = re.compile(
    r"refus|reject|unknown|unrecognis|unrecogniz|not accepted|guard|typo"
    r"|points at|hint|advice|names the real|spell it|redirect|instead", re.I)

# Keys the harness itself strips or adds, never the suite's business.
HARNESS = {"confirm", "save", "force", "overwrite", "replaceexisting", "discardunsaved"}


def strip_py_comments(text):
    """Drop # comments, keeping strings. The INTENT match must read the ASSERTION, not the prose.

    Learned by mutation-testing this file. Reintroducing the read_datatable defect did NOT trip the
    check, because the comment written above the fixed line says "read_datatable refuses `limit` by
    name" - and `refus` is an INTENT word. The explanation of a bug suppressed the detector for that
    bug. Same root cause as the five C++ scanners fixed the same night: a grep for a word finds the
    places that USE it and the places that DISCUSS it.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            q = c
            j = i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "#":
            j = text.find(chr(10), i)
            i = n if j < 0 else j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def dict_span(text, open_brace):
    depth, i = 0, open_brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace:i + 1]
        i += 1
    return ""


def top_level_keys(blob):
    """Keys at brace depth 1 - a nested {"x":..} value must not contribute its own keys."""
    out, depth, i = [], 0, 0
    while i < len(blob):
        c = blob[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c in "\"'" and depth == 1:
            q, j = c, i + 1
            while j < len(blob) and blob[j] != q:
                j += 2 if blob[j] == "\\" else 1
            k = blob[i + 1:j]
            after = blob[j + 1:j + 3]
            if ":" in after and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                out.append(k)
            i = j
        i += 1
    return out


def main():
    accepts = PR.endpoint_accepts()
    print("endpoints with a parsed accept-list: %d" % len(accepts))

    rows = []
    for f in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        src = io.open(f, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        for m in CALL.finditer(src):
            ep = m.group(1)
            allowed = accepts.get(ep)
            if not allowed:
                continue                      # no parsed guard - parity_check's problem, not this one
            blob = dict_span(src, src.index("{", m.end() - 1))
            line = src[:m.start()].count("\n") + 1
            # Generous AFTER the call: the assertion that reveals intent is the NEXT statement, and
            # a two-line call with a timeout= argument pushed it past a 120-char window on the first
            # run - test_data_layer_writes' "is refused" check missed by a few characters.
            context = strip_py_comments(src[max(0, m.start() - 240):
                                             m.start() + len(blob) + 400])
            for k in top_level_keys(blob):
                low = k.lower()
                if low in allowed or low in HARNESS or JUNK.match(k):
                    continue
                if INTENT.search(context):
                    continue                  # the test is about the refusal
                rows.append((os.path.basename(f), line, ep, k))

    print("suite calls passing a key the endpoint refuses: %d" % len(rows))
    if not rows:
        print("")
        print("OK  no suite call names a parameter its endpoint would reject")
        return 0
    print("")
    print("Each of these fails on the PARAMETER NAME, so whatever it asserts is about the refusal")
    print("rather than the behaviour. Read the endpoint's accepted list before assuming a typo -")
    print("an alias this scan cannot see would look identical from here.")
    for fn, line, ep, k in sorted(rows):
        print("   %-34s:%-5d %-28s passes %r" % (fn, line, ep, k))
    return 1


if __name__ == "__main__":
    sys.exit(main())
