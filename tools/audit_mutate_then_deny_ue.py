"""Which refusals leave the asset DIRTY - the C++ side of "a refused call should be a no-op"?

tools/audit_mutate_then_deny.py asks the Blender addon whether a refusal that says "NOTHING was
changed" is telling the truth, and found seven ops that were not - one moved a camera, its lens and
its sensor while saying nothing had changed. The C++ handlers make the same promise in the same words
over seven hundred times and nothing checked them.

THE QUESTION IS NOT IDENTICAL HERE, and saying otherwise would overstate what this finds.
UObject::Modify() does not write a property. It opens a transaction and DIRTIES THE PACKAGE. So a
handler that calls Modify() and then refuses has usually told the truth about the property - what it
has not told you is that the asset is now dirty, and the next save-all writes a change nobody made
into an asset nobody edited, with an undo entry to match. The diff is unattributable. That is exactly
the cost audit_read_purity exists to prevent on the read side; this is the refusal side of it.

So the finding is "this refusal leaves the asset dirty", which is true of every hit. Where the verb
is `changed` it is ALSO a false sentence, because a dirty package is a change the caller cares about;
where the verb is narrower - remove_virtual_bone's "NOTHING was removed" is accurate, nothing was
removed - the sentence stands and the dirty package is still the defect. The summary counts the two
separately rather than blurring them.

NO PARSER, AND THE THREE THINGS THAT BUYS BACK. There is no clang here, so structure comes from
brace-matching over harvest_param_table's scrubber - the one comment-and-string blanker in this repo,
shared rather than reimplemented, and it preserves offsets so the raw text still lines up for reading
the message. That is enough for the three rules that decide precision:

  - ORDER. The mutation must come before the refusal.
  - EXCLUSIVITY. `if (...) { Mutate(); } else { Fail(...); return; }` is not a finding. `else` blocks
    are paired with their `if` while scanning, which brace depth alone cannot tell you.
  - TERMINALITY. A block ending in `return` separates its writes from every refusal below it. Almost
    every refusal here is `{ Fail(...); return; }`, so without this the audit reports each guard
    against every guard above it.

WHAT IT CANNOT SEE, stated rather than discovered later: a mutation inside a callee. The Blender
audit had the same hole and it was the one that mattered there, because its refusals live in shared
helpers. Here the refusals are inline - Fail() at the site - so the callee problem is smaller, but a
handler that delegates its writes to a local helper is invisible. --show-delegating lists them.

FINDINGS ARE NOT FIXED HERE. The editor holds UnrealEditor-MifBridge.dll, so nothing C++ can be
compiled or tested in this session, and an untested C++ edit is worth less than a filed one.
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harvest_param_table as H          # the one comment/string scrubber, shared not reimplemented
from audit_postconditions import handler_bodies, SILENT_APIS   # the one handler brace-matcher

HERE = os.path.dirname(os.path.abspath(__file__))

# Calls that reach real state. Modify and MarkPackageDirty dirty the package by themselves; the rest
# change the object. SILENT_APIS is audit_postconditions' curated list of UE mutators that cannot
# report failure - every one of them is a write, which is why it is reused rather than retyped.
MUTATORS = sorted(set([
    "Modify", "MarkPackageDirty", "PreEditChange", "PostEditChange", "PostEditChangeProperty",
    "SetActorLabel", "SetFolderPath", "SetActorTransform", "SetActorLocation", "SetWorldLocation",
    "SpawnActor", "DestroyActor", "AddComponent", "RegisterComponent", "AttachToComponent",
] + [name for name, _why in SILENT_APIS]))
MUTATOR_RE = re.compile(r"\b(%s)\s*\(" % "|".join(re.escape(m) for m in MUTATORS))

# How a handler refuses.
REFUSE_RE = re.compile(r"\b(Fail|FailScenario|Refuse|RefuseValue)\s*\(")

# The promise, and only when the sentence ENDS at the verb - "NOTHING was changed to the preview
# range" is a promise about one field. Same rule as the Blender audit, same reason.
CLAIM_RE = re.compile(r"(?:NOTHING|Nothing|nothing) was ([a-z]+)\s*[.;\"]")

# A promise about the OPERATION, not the scene. "NOTHING was traced" is true of a trace that never
# ran even if the handler dirtied something first - that is a leak question, not a broken promise.
OPERATION_VERBS = ("traced", "rendered", "written", "saved", "exported", "imported", "baked",
                   "cooked", "built", "compiled", "run", "executed")

TEXT_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def blocks(scrubbed):
    """Every brace block as (start, end, parent, is_else_of), scanning once.

    `is_else_of` pairs an else-block with its if-block. Brace depth cannot express that: the two are
    siblings at the same level, and treating them as sequential reports every guarded write against
    the refusal in its own else.
    """
    out, stack, prev_close = [], [], None
    for i, ch in enumerate(scrubbed):
        if ch == "{":
            gap = scrubbed[prev_close + 1:i] if prev_close is not None else ""
            partner = prev_close if re.match(r"^\s*else\b", gap) else None
            stack.append((i, stack[-1][0] if stack else None, partner))
        elif ch == "}":
            if not stack:
                continue
            start, parent, partner = stack.pop()
            out.append({"start": start, "end": i, "parent": parent, "elseOf": partner})
            prev_close = i
    out.sort(key=lambda b: b["start"])

    # AN ELSE-IF CHAIN IS ONE CHOICE, not a series of pairs. edit_container dispatches on the
    # operation through eight `else if` branches; pairing each block only with the one before it
    # made branch 1 and branch 4 look like unrelated siblings, and every Modify() in an earlier
    # branch was reported against every Fail() in a later one - 24 findings from one handler, none
    # of them real. Every block in the chain gets the same id, and two blocks sharing one can never
    # both run.
    by_end = dict((b["end"], b) for b in out)
    for blk in out:
        blk["chain"] = blk["start"]
    for blk in out:
        seen = set()
        node = blk
        while node["elseOf"] is not None and node["elseOf"] in by_end:
            node = by_end[node["elseOf"]]
            if id(node) in seen:
                break
            seen.add(id(node))
        blk["chain"] = node["start"]
    return out


def path_of(pos, blks):
    """The chain of blocks containing pos, outermost first."""
    return [b for b in blks if b["start"] < pos < b["end"]]


def ends_terminal(scrubbed, blk):
    """Does this block always leave the handler?"""
    inner = scrubbed[blk["start"] + 1:blk["end"]]
    tail = inner.rstrip().rstrip(";").rstrip()
    return bool(re.search(r"\b(return|continue|break|throw)\s*[^;]*$", tail))


def exclusive(mpath, rpath, scrubbed):
    """Can the mutation and the refusal both happen in one call?"""
    shared = 0
    for a, b in zip(mpath, rpath):
        if a["start"] != b["start"]:
            # Siblings. Exclusive when they are two arms of the same if/else-if chain.
            if a["chain"] == b["chain"]:
                return True
            break
        shared += 1
    for blk in mpath[shared:]:
        if ends_terminal(scrubbed, blk):
            return True
    return False


FLAG_SET = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*true\s*;")
FLAG_NEG = re.compile(r"!\s*([A-Za-z_][A-Za-z0-9_]*)\b")


def guarded_by_did_mutate(scrubbed, blks, rpos, flags):
    """Is this refusal behind an `if (!bDidMutate)`-style guard?

    edit_container ends with

        if (!bDidMutate)
        {
            Fail(Out, ... "reached the end without mutating anything. Nothing was changed.");
            return;
        }

    and every branch that calls Modify() sets bDidMutate. The refusal is unreachable once a mutation
    has run, but nothing about brace structure says so - the flag is a DATA correlation, and it is
    what produced all 24 of this handler's findings and most of the rest.

    Recognised narrowly: the guard has to NEGATE a name that is assigned `= true` somewhere in the
    handler. A positive test of the same flag is not this idiom and stays reportable.
    """
    inner = [b for b in blks if b["start"] < rpos < b["end"]]
    if not inner:
        return False
    blk = max(inner, key=lambda b: b["start"])
    head = scrubbed[max(0, blk["start"] - 220):blk["start"]]
    head = head[head.rfind("}") + 1:] if "}" in head else head
    head = head[head.rfind(";") + 1:] if ";" in head else head
    return any(name in flags for name in FLAG_NEG.findall(head))


def claim_at(raw, scrubbed, pos):
    """The promise this refusal makes, or None. Joins the literals of the whole call."""
    open_paren = scrubbed.find("(", pos)
    if open_paren < 0:
        return None
    close = H.match_paren(scrubbed, open_paren)
    if close is None or close < 0:
        return None
    joined = " ".join(m.group(1) for m in TEXT_LITERAL.finditer(raw[open_paren:close]))
    hit = CLAIM_RE.search(joined)
    if not hit:
        return None
    return " ".join(joined.split()), hit.group(1)


def handler_start_line(fname, endpoint):
    """Absolute line of `void H_<endpoint>(` so findings can be opened, not hunted for.

    handler_bodies yields the body text and not where it came from, and a line number relative to a
    handler is close to useless when the file is nine thousand lines long.
    """
    path = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private", fname)
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    except OSError:
        return 0
    needle = "void H_%s(" % endpoint
    idx = src.find(needle)
    return src[:idx].count("\n") if idx >= 0 else 0


_WRITERS_CACHE = {}
FUNC_DEF = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{)]*\)\s*(?:const\s*)?\{")
_NOT_FUNCTIONS = ("if", "for", "while", "switch", "catch", "else", "do", "return", "sizeof")


def writers_in(fname):
    """Functions defined in this file whose body calls a mutator.

    Coarse by design: it brace-matches anything shaped like a definition, so the keyword list is
    what keeps `if (...) {` out. Close enough for a list whose whole job is to say how big the
    unaudited edge is - and far better than the previous version, which matched each handler's own
    signature and reported eighty handlers as delegating to themselves.
    """
    if fname in _WRITERS_CACHE:
        return _WRITERS_CACHE[fname]
    path = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private", fname)
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    except OSError:
        _WRITERS_CACHE[fname] = set()
        return _WRITERS_CACHE[fname]
    scrubbed = H.blank_comments_and_strings(src)
    out = set()
    for m in FUNC_DEF.finditer(scrubbed):
        name = m.group(1)
        if name in _NOT_FUNCTIONS:
            continue
        open_brace = scrubbed.find("{", m.end() - 1)
        if open_brace < 0:
            continue
        depth, j = 0, open_brace
        while j < len(scrubbed):
            if scrubbed[j] == "{":
                depth += 1
            elif scrubbed[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if MUTATOR_RE.search(scrubbed[open_brace:j]):
            out.add(name)
    _WRITERS_CACHE[fname] = out
    return out


_FAILING_CACHE = {}
RETURN_FALSE = re.compile(r"\breturn\s+false\s*;")


def mutates_before_failing(fn_body):
    """Can this helper write and THEN report failure?

    A helper that validates everything first and writes last is not a hole, however many mutators it
    contains - WaterApplySpline is exactly that, and treating it as one produced the audit's only
    false finding after the helper reach landed.
    """
    blks = blocks(fn_body)
    muts = [m.start() for m in MUTATOR_RE.finditer(fn_body)]
    fails = [m.start() for m in RETURN_FALSE.finditer(fn_body)]
    for mpos in muts:
        mpath = path_of(mpos, blks)
        for fpos in fails:
            if fpos > mpos and not exclusive(mpath, path_of(fpos, blks), fn_body):
                return True
    return False


def failing_writers_in(fname):
    """The subset of writers_in that can mutate on the way to reporting failure."""
    if fname in _FAILING_CACHE:
        return _FAILING_CACHE[fname]
    path = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private", fname)
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    except OSError:
        _FAILING_CACHE[fname] = set()
        return _FAILING_CACHE[fname]
    scrubbed = H.blank_comments_and_strings(src)
    out = set()
    for name in writers_in(fname):
        for m in re.finditer(r"\b%s\s*\([^;{)]*\)\s*(?:const\s*)?\{" % re.escape(name),
                             scrubbed):
            open_brace = scrubbed.find("{", m.end() - 1)
            if open_brace < 0:
                continue
            depth, j = 0, open_brace
            while j < len(scrubbed):
                if scrubbed[j] == "{":
                    depth += 1
                elif scrubbed[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if mutates_before_failing(scrubbed[open_brace:j + 1]):
                out.add(name)
            break
    _FAILING_CACHE[fname] = out
    return out


def scan_body(fname, endpoint, body, base, findings, scoped, delegating):
    """One handler. Split out of scan() so --selftest can drive it with synthetic C++.

    Every rule in here was added because it removed real false positives, and a rule that cannot be
    demonstrated firing is the thing this repo keeps deleting. The self-test feeds each rule a case
    it must catch and a case it must not.
    """
    scrubbed = H.blank_comments_and_strings(body)
    blks = blocks(scrubbed)
    muts = [(m.start(), m.group(1)) for m in MUTATOR_RE.finditer(scrubbed)]
    # ...and calls to a helper IN THIS FILE that mutates. IKRig wraps every one of its writes in
    # IKMarkDirty, so eleven endpoints there had no direct mutator at all and the audit could not
    # see them. Handlers are skipped: H_ functions contain mutators by definition and a handler
    # calling another handler is not this question.
    local_writers = failing_writers_in(fname)
    for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", scrubbed):
        name = m.group(1)
        if name in local_writers and not name.startswith("H_") and name not in _NOT_FUNCTIONS:
            muts.append((m.start(), name))
    muts.sort()
    refs = []
    for m in REFUSE_RE.finditer(scrubbed):
        got = claim_at(body, scrubbed, m.start())
        if got:
            refs.append((m.start(), got[0], got[1]))
    if refs and not muts:
        # Only helpers that actually mutate, and never the handler's own signature. Listing the
        # first capitalised call in the body matched `H_<endpoint>(` itself and produced eighty rows
        # of `H_x -> H_x` - a blind-spot list that measured nothing at all.
        for call in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\s*\(", scrubbed):
            helper = call.group(1)
            if helper.startswith("H_") or helper in ("Fail", "TEXT", "FString", "Refuse"):
                continue
            if helper in writers_in(fname):
                delegating.append((fname, endpoint, helper))
                break
    flags = set(FLAG_SET.findall(scrubbed))
    for mpos, mname in muts:
        mpath = path_of(mpos, blks)
        for rpos, claim, verb in refs:
            if rpos <= mpos or exclusive(mpath, path_of(rpos, blks), scrubbed):
                continue
            if guarded_by_did_mutate(scrubbed, blks, rpos, flags):
                continue
            row = {"file": fname, "endpoint": endpoint, "call": mname, "verb": verb,
                   "line": base + body[:mpos].count("\n") + 1,
                   "refuseLine": base + body[:rpos].count("\n") + 1, "claim": claim[:100]}
            (scoped if verb in OPERATION_VERBS else findings).append(row)
            break


def scan():
    findings, scoped, delegating = [], [], []
    for fname, endpoint, body in handler_bodies():
        scan_body(fname, endpoint, body, handler_start_line(fname, endpoint),
                  findings, scoped, delegating)
    return findings, scoped, delegating


# Each case is (name, should_fire, C++ body). The pairs matter more than the cases: a rule that only
# ever suppresses is indistinguishable from a rule that suppresses everything.
SELFTEST = [
    ("plain mutate then deny", True, """
        Obj->Modify();
        if (Bad) { Fail(Out, TEXT("bad. NOTHING was changed.")); return; }
    """),
    ("refusal comes first", False, """
        if (Bad) { Fail(Out, TEXT("bad. NOTHING was changed.")); return; }
        Obj->Modify();
    """),
    ("if/else arms", False, """
        if (Ok) { Obj->Modify(); }
        else { Fail(Out, TEXT("bad. NOTHING was changed.")); return; }
    """),
    ("else-if chain, far arms", False, """
        if (Op == A) { Obj->Modify(); }
        else if (Op == B) { DoThing(); }
        else if (Op == C) { Fail(Out, TEXT("bad. NOTHING was changed.")); return; }
    """),
    ("did-mutate flag guard", False, """
        if (Ok) { Obj->Modify(); bDidMutate = true; }
        if (!bDidMutate) { Fail(Out, TEXT("nothing ran. NOTHING was changed.")); return; }
    """),
    ("guarded write, later unguarded refusal", True, """
        if (Ok) { Obj->Modify(); }
        if (Other) { Fail(Out, TEXT("bad. NOTHING was changed.")); return; }
    """),
    ("scoped promise is not a finding", False, """
        Obj->Modify();
        if (Bad) { Fail(Out, TEXT("bad. NOTHING was changed to the preview range.")); return; }
    """),
    ("operation verb is not a state finding", False, """
        Obj->Modify();
        if (Bad) { Fail(Out, TEXT("bad. NOTHING was rendered.")); return; }
    """),
]


# The helper rule is tested on its own because it reads whole files, which scan_body's synthetic
# bodies cannot supply. Both directions, for the usual reason.
HELPER_SELFTEST = [
    ("helper writes, then fails", True, """
    {
        Obj->Modify();
        if (Bad) { return false; }
        return true;
    }
    """),
    ("helper validates, then writes", False, """
    {
        if (Bad) { return false; }
        Obj->Modify();
        return true;
    }
    """),
    ("helper writes in the arm that succeeds", False, """
    {
        if (Ok) { Obj->Modify(); return true; }
        else { return false; }
    }
    """),
]


def selftest():
    bad = 0
    for name, expect, body in HELPER_SELFTEST:
        got = mutates_before_failing(body)
        ok = got == expect
        bad += 0 if ok else 1
        print("  %-4s %-42s expected %-5s got %s"
              % ("ok" if ok else "FAIL", name, expect, got))
    for name, should_fire, body in SELFTEST:
        findings, scoped, _ = [], [], []
        scan_body("selftest.cpp", "probe", "{" + body + "}", 0, findings, scoped, _)
        fired = bool(findings)
        ok = fired == should_fire
        bad += 0 if ok else 1
        print("  %-4s %-42s expected %-5s got %s"
              % ("ok" if ok else "FAIL", name, should_fire, fired))
    print("")
    print("selftest: %d case(s), %d failure(s)"
          % (len(SELFTEST) + len(HELPER_SELFTEST), bad))
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on any finding")
    ap.add_argument("--show-delegating", action="store_true",
                    help="handlers that promise but write through a helper this cannot follow")
    ap.add_argument("--by-endpoint", action="store_true", help="one line per endpoint")
    ap.add_argument("--selftest", action="store_true",
                    help="prove each precision rule can both fire and stay quiet")
    args = ap.parse_args()

    if args.selftest:
        return 1 if selftest() else 0

    findings, scoped, delegating = scan()
    print("audit_mutate_then_deny_ue: %d finding(s), %d operation-scoped, %d mutating calls known"
          % (len(findings), len(scoped), len(MUTATORS)))

    if args.by_endpoint:
        seen = {}
        for row in findings:
            seen.setdefault((row["file"], row["endpoint"]), []).append(row)
        for (fname, ep), rows in sorted(seen.items()):
            print("  %-34s %-28s %d site(s), first: %s at line %d"
                  % (fname, ep, len(rows), rows[0]["call"], rows[0]["line"]))
    else:
        for row in findings:
            print("")
            print("  %s :: %s" % (row["file"], row["endpoint"]))
            print("    line %-5d calls   %s()" % (row["line"], row["call"]))
            print("    line %-5d refuses %s" % (row["refuseLine"], row["claim"]))

    if scoped:
        print("")
        print("PROMISED ONLY ABOUT THE OPERATION - %d, not failures. See OPERATION_VERBS."
              % len(scoped))

    if args.show_delegating:
        print("")
        print("WRITES DELEGATED TO A HELPER - CHECKED, not a hole (%d):" % len(delegating))
        print("The helper writes only below its last failure path, so when it reports failure it")
        print("has not written. Listed because that is a property of the helper today, not a rule.")
        for fname, ep, helper in sorted(set(delegating))[:40]:
            print("  %-34s %-28s -> %s" % (fname, ep, helper))

    if findings:
        contradicted = [r for r in findings if r.get("verb") in ("changed", "created", "added")]
        print("")
        print("Each of these dirties the asset and then refuses. Modify() writes no property, so")
        print("most of these sentences are true about the property and silent about the package -")
        print("the asset is left dirty, and the next save-all produces a diff nobody can attribute.")
        print("")
        print("%d of the %d also CONTRADICT themselves: the verb is changed/created/added, and a"
              % (len(contradicted), len(findings)))
        print("dirty package is a change. The rest promise something narrower and keep it.")
    return 1 if (args.check and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
