"""Suite calls that pass a parameter the endpoint REFUSES - tests that exercise their own typo.

WHY THIS EXISTS. test_audit_fixes' T44 called add_enum_literal with `enum`. The endpoint refuses that
by name - "spell it enumName here - list_enum_values takes either, this endpoint reads only enumName"
- so every run failed on the parameter name, took the `if r.get("ok") is False` branch, and that
branch asserted literally `check("T44 bad enumerator refused outright", True)`. Green for weeks,
testing nothing but its own spelling.

That failure is invisible to every other check in this directory. coverage_gaps sees the endpoint
NAMED in a suite. audit_suite_reach sees the assertions RUN. Both are satisfied by a call that never
reaches the endpoint's body.

THE THIRD SIDE OF A TRIANGLE. Two tools already watch the parameter contract, and neither can see
this one - mcp_sends_unknown says so itself: "the python test suites call M.call() directly with
their own payloads rather than going through server.py".

    param_reach.py         endpoint ACCEPTS a key no MCP tool sends    - costs a capability
    mcp_sends_unknown.py   an MCP tool SENDS a key the endpoint rejects - costs the whole call
    this file              a SUITE sends a key the endpoint rejects     - costs the whole TEST,
                           silently, because the suite then asserts against the refusal it caused

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
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")
sys.path.insert(0, HERE)
import param_reach as PR

CALL = re.compile(
    r'(?:M\.call|M\.raw_post|SC\.confirm_call|post)\s*\(\s*["\']([a-z0-9_]+)["\']\s*,\s*\{')

# Name only - no dict required. A call with no payload can still name an endpoint that does not exist.
CALL_ANY = re.compile(
    r'(?:M\.call|M\.raw_post|SC\.confirm_call)\s*\(\s*["\']([a-z0-9_]+)["\']')

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


def bound_endpoints():
    """Every name passed to MIF_BIND, read from the source rather than from a live editor."""
    priv = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")
    out = set()
    for fn in os.listdir(priv):
        if fn.endswith(".cpp"):
            src = io.open(os.path.join(priv, fn), encoding="utf-8", errors="replace").read()
            out |= set(re.findall(r"MIF_BIND\s*\(\s*([a-z0-9_]+)\s*\)", src))
    return out


def unknown_endpoint_calls(bound):
    """Suite calls to a name nothing binds - refused 100% of the time, for everyone.

    The sibling of the wrong-key check and found the same night: three sites called
    `compile_blueprint`, which does not exist - the endpoint is `compile`. All three were
    fire-and-forget, so no assertion went red; the blueprint simply was never recompiled before the
    next line spawned an actor from its generated class.

    coverage_gaps cannot see this. It maps suite mentions ONTO the registry, so a name matching no
    endpoint contributes nothing and is silently ignored.

    kr_* is skipped: those come from an external provider and are not in this module's MIF_BIND list.
    """
    rows = []
    for f in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        src = io.open(f, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        for m in CALL_ANY.finditer(src):
            ep = m.group(1)
            if ep in bound or ep.startswith("kr_"):
                continue
            rows.append((os.path.basename(f), src[:m.start()].count("\n") + 1, ep))
    return rows



# --------------------------------------------------------------- calls that CANNOT succeed
#
# mifaudit strips `confirm` from every payload - the guard that makes an unattended overnight run
# safe. So a call through M.call to a confirm-gated endpoint is refused, always, by design. Most of
# those are deliberate and good: 23 sites probe the refusal, and the stripping is precisely what
# makes the probe honest. test_node_spawns says so at the call site - "a plain M.call never carries
# confirm, so this proves the guard itself without touching scratch_confirm at all".
#
# What makes one a DEFECT is discarding the answer. audit_roundtrip's cleanup was
#
#     M.call("delete_asset", {"path": root})     # confirm is stripped by the harness; best effort
#
# accurate, and it means the line never worked. Every run left a scratch blueprint in whatever editor
# it was pointed at, and nothing said so - a call that could only ever fail, reading as cleanup that
# succeeded. Found by checking a live session's leftovers, not by any tool.
#
# BOTH conditions are needed and neither alone is worth reporting: confirm-gated alone is a false
# positive 23 times over, and a discarded response alone is usually setup. Together it says "this
# call cannot succeed and nobody is looking", which is exactly one bug.
CONFIRM_GATED = re.compile(r'JBool\w*\(In, TEXT\("confirm"\)')


def confirm_required_endpoints():
    """Endpoints whose handler reads confirm AND says it requires it."""
    out = set()
    for fn in sorted(os.listdir(PRIV)):
        if not fn.endswith(".cpp"):
            continue
        src = io.open(os.path.join(PRIV, fn), encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"void H_([a-z0-9_]+)\(", src):
            nxt = src.find("void H_", m.end())
            body = src[m.start(): nxt if nxt > 0 else len(src)]
            if CONFIRM_GATED.search(body) and "requires confirm" in body:
                out.add(m.group(1))
    return out


def cannot_succeed():
    """(file, line, endpoint) for M.call to a confirm-gated endpoint whose result is thrown away."""
    req = confirm_required_endpoints()
    rows = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".py"):
            continue
        try:
            tree = ast.parse(io.open(os.path.join(HERE, fn),
                                     encoding="utf-8", errors="replace").read(), filename=fn)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            f = node.value.func
            if not (isinstance(f, ast.Attribute) and f.attr == "call"
                    and isinstance(f.value, ast.Name) and f.value.id == "M"):
                continue
            if not node.value.args:
                continue
            a0 = node.value.args[0]
            if isinstance(a0, ast.Constant) and a0.value in req:
                rows.append((fn, node.lineno, a0.value))
    return rows


def main():
    accepts = PR.endpoint_accepts()
    print("endpoints with a parsed accept-list: %d" % len(accepts))

    bound = bound_endpoints()
    ghosts = unknown_endpoint_calls(bound)
    print("suite calls to an endpoint nothing binds: %d" % len(ghosts))
    for fn, line, ep in sorted(ghosts):
        print("   %-34s:%-5d calls %r, which is not a MIF_BIND name" % (fn, line, ep))

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
    stuck = cannot_succeed()
    if stuck:
        print("")
        print("CANNOT SUCCEED - a confirm-gated endpoint called through the stripping harness, with")
        print("the answer thrown away. The call is refused every time and nothing looks:")
        for sfn, sln, sep in stuck:
            print("  %s:%d  %s" % (sfn, sln, sep))
        print("  Route it through scratch_confirm - it refuses non-scratch paths, so the guard is")
        print("  satisfied rather than bypassed - or check the result and say when it fails.")
        return 1

    if not rows and not ghosts:
        print("")
        print("OK  every suite call names a real endpoint and only keys it accepts")
        return 0
    if not rows:
        return 1
    print("")
    print("Each of these fails on the PARAMETER NAME, so whatever it asserts is about the refusal")
    print("rather than the behaviour. Read the endpoint's accepted list before assuming a typo -")
    print("an alias this scan cannot see would look identical from here.")
    for fn, line, ep, k in sorted(rows):
        print("   %-34s:%-5d %-28s passes %r" % (fn, line, ep, k))
    return 1


if __name__ == "__main__":
    sys.exit(main())
