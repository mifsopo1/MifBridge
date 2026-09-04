"""Does a refusal that says "NOTHING was changed" fire AFTER something was already changed?

THE SENTENCE THIS ADDON IS HELD TO. Every refusal in MifBlender ends with a promise about state -
"NOTHING was created", "NOTHING was changed" - and callers are told to trust it: a refused op is
supposed to be a no-op, so the caller can retry, fall back, or give up without first re-reading the
scene to find out what half happened. When that sentence is false the caller is not merely
uninformed, it has been actively misled by the one line it was told to rely on.

THE CONCRETE DEFECT, found by hand on 2026-09-04. op_set_light parsed its location vector one line
BELOW `data.type = new_type`:

    if new_type is not None:
        data.type = new_type                       # committed
    if "location" in params:
        obj.location = _vec3(params, "location")   # _vec3 CAN refuse

so set_light({type: "SPOT", location: "garbage"}) retyped the light to SPOT and then answered
"NOTHING was created". The same shape was in ops_create's _place - shared by five ops, so five
carried it - and in ops_lightcam's camera writes.

WHY THIS IS STATIC AND THE DYNAMIC PASS COULD NOT DO IT. blender_version_matrix's corrupted-payload
pass fingerprints scene state around every refusal, which sounds like the same question and is not.
That pass runs AFTER the sweep has already applied each op's good payload, so corrupting one key and
re-calling an idempotent `set_*` op re-applies values that are ALREADY set: `data.type = "SPOT"` on a
light that is already SPOT changes nothing, the fingerprint holds, and the check correctly reports no
finding while the defect sits three lines away. Measured, not assumed - the check was written, the
set_light defect was restored underneath it, and it reported zero. It was removed rather than kept as
decoration. Reading the source has no such blind spot: the write is above the raise or it is not.

WHAT COUNTS AS A MUTATION. An attribute assignment (`obj.location = ...`, `data.type = ...`), a
`.new()` on a bpy.data collection, or a `.link()`. Deliberately NOT every call named new/remove/clear:
`bmesh.new()` builds a scratch mesh that touches nothing until `to_mesh`, and counting it would bury
the real findings in noise. Subscript targets (`out["x"] = ...`) are locals by construction.

MUTUALLY EXCLUSIVE BRANCHES ARE NOT FINDINGS. This is the check's main precision feature and the
reason it is an AST walk rather than a grep:

    if isinstance(v, (list, tuple)):
        obj.location = v
    else:
        raise MifOpError("... NOTHING was changed.")

the raise is lexically after the write and can never follow it. Every node is tagged with the
branch path it sits under, and a mutation/raise pair that diverges at any `if`, `try` or `for` is
skipped. Without this the audit reported dozens of pairs that cannot co-occur.

WHAT IT CANNOT SEE. A mutation inside a callee - `_place(obj, params)` hid exactly this defect until
the parse was split out - because that needs whole-program flow this does not attempt. Cross-function
reach is the known hole; ops that delegate their writes are listed by --show-delegating so the gap is
visible rather than silent.
"""
import argparse
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

# The promise, exactly as the addon words it. Both spellings are in use.
CLAIMS = ("NOTHING was", "Nothing was")

# ...and only when the sentence ENDS there. "NOTHING was changed." is a promise about the whole op;
# "NOTHING was changed to the preview range." is a promise about one field and is not this check's
# business. The trailing [.;] is the entire difference between the two. See _claim_text.
# The verb is often INTERPOLATED - _vec3 takes verb="created"/"changed"/"keyed" and builds
# "NOTHING was %s." - so %s has to count as a verb. 12 messages in the addon are worded that way,
# including the one in ops_lightcam._vec3 that this whole audit was written to catch; requiring a
# literal word made every one of them invisible.
GLOBAL_CLAIM = re.compile(
    r"(?:NOTHING|Nothing) was (?:[a-z]+|%s)"
    r"(?:\s+(?:to|from|on|in) (?:it|them|the (?:file|scene)))?\s*[.;]")

# Calls that reach real state. See the module docstring for why this is short.
BPY_NEW_ROOTS = ("bpy.data", "bpy.context")

# Undo is structural, not lexical: these ops mutate, then refuse, then REMOVE what they made inside
# an except handler. The write genuinely happens before the raise and the sentence is still true.
# Each entry names the file, the function and how the undo is done, so an entry cannot rot silently.
ALLOWED = {
    ("ops_material.py", "op_set_material_texture"):
        "structural undo: _made_nodes is unwound in the except handler before re-raising",
}
# It started with three. add_particles and create_collection were removed once _undone_before landed,
# because both undo on the line above the raise and no longer need excusing - and create_collection
# had the WRONG FILE on it (ops_create.py; it lives in ops_collection.py), so it had never matched
# anything and would have gone on not matching silently. Hence --check fails on a dead entry: an
# allow-list nobody can see rot in is how a real finding gets excused by a typo.


def _dotted(node):
    """`bpy.data.objects` from an Attribute chain, or None if it is not a plain dotted name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _is_mutation(node):
    """Does this statement write state a caller could observe? Returns a label or None."""
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute):
                return "%s = ..." % (_dotted(t) or ("<expr>." + t.attr))
        return None
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        if isinstance(call.func, ast.Attribute):
            dotted = _dotted(call.func)
            if dotted and call.func.attr in ("new", "link"):
                if any(dotted.startswith(r) for r in BPY_NEW_ROOTS):
                    return dotted + "()"
                if call.func.attr == "link":
                    return dotted + "()"
    return None


def _claim_text(node):
    """If this raise promises GLOBALLY that nothing changed, return the promise.

    A SCOPED PROMISE IS NOT THIS CHECK'S BUSINESS, and that is the whole of it. set_frame_range
    refuses a bad preview range with "NOTHING was changed to the preview range." after legitimately
    writing frame_start - and it is telling the truth, because it promised only about the preview
    range. Requiring the sentence to END at the verb separates the two, and removed 7 false findings
    from that one function.

    String constants are joined before matching only because a message CAN be assembled from more
    than one node. Adjacent literals are folded by the parser, so the join is defensive rather than a
    fix for anything observed - written down because the opposite is easy to assume.
    """
    if not isinstance(node, ast.Raise) or node.exc is None:
        return None
    exc = node.exc
    if not (isinstance(exc, ast.Call) and getattr(exc.func, "id", "") == "MifOpError"):
        return None
    parts = [sub.value for sub in ast.walk(exc)
             if isinstance(sub, ast.Constant) and isinstance(sub.value, str)]
    joined = " ".join(parts)
    if not any(c in joined for c in CLAIMS):
        return None
    if not GLOBAL_CLAIM.search(joined):
        return None
    return " ".join(joined.split())


# Undoing calls. A refusal that removes what it made before speaking is telling the truth, and this
# is how the addon actually does it - `tree.nodes.remove(node)` on the line above the raise.
CLEANUP = ("remove", "unlink")


def _undone_before(rstmt, owner):
    """Was the damage removed on the way to this raise?

    DETECTED RATHER THAN ALLOW-LISTED, because an allow-list of function names rots: it keeps
    excusing a function long after the undo is refactored out of it, which is the failure this whole
    audit exists to catch. op_add_group_node calls tree.nodes.remove(node) immediately above each of
    its three "NOTHING was added." raises - six findings that were all correct code.

    Only the raise's OWN branch is scanned. An undo further out - in an except handler that re-raises,
    as ops_material does - is real but not visible from here, and those stay in ALLOWED.
    """
    body = owner.get(id(rstmt))
    if not body:
        return False
    for stmt in body[:body.index(rstmt)]:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)                     and sub.func.attr in CLEANUP:
                return True
    return False


def _ends_terminal(body):
    """Does this branch always leave the function - so nothing after it can run?"""
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


def _walk_body(body, path, out, terminal, owner):
    """Tag every statement with the branch path it sits under, so exclusivity is decidable.

    `path` is a list of (node id, which-branch) pairs. Two statements can both run only if neither
    diverges from the other at a shared `if`/`try`/loop.

    `terminal` records which branches end in return/raise. assign_action writes
    `obj.animation_data.action = None` inside `if clear:` and RETURNS at the end of that branch, so
    the "no action named X. NOTHING was changed." raise 19 lines below is unreachable from it. Branch
    paths alone call that a finding, because the raise is at the function's top level and never
    diverges from the write - it is the RETURN that separates them, not the branch.
    """
    for stmt in body:
        out.append((stmt, tuple(path)))
        owner[id(stmt)] = body
        if isinstance(stmt, ast.FunctionDef):
            continue  # a nested def is a separate scope, judged on its own
        if isinstance(stmt, ast.If):
            for key, sub in (("then", stmt.body), ("else", stmt.orelse)):
                terminal[(id(stmt), key)] = _ends_terminal(sub)
                _walk_body(sub, path + [(id(stmt), key)], out, terminal, owner)
        elif isinstance(stmt, ast.Try):
            for key, sub in ([("try", stmt.body)]
                             + [("except%d" % i, h.body) for i, h in enumerate(stmt.handlers)]
                             + [("orelse", stmt.orelse), ("finally", stmt.finalbody)]):
                terminal[(id(stmt), key)] = _ends_terminal(sub)
                _walk_body(sub, path + [(id(stmt), key)], out, terminal, owner)
        elif isinstance(stmt, (ast.For, ast.While)):
            # A loop body can run then fall through to a later raise, so it is NOT exclusive with
            # code after the loop - but two different iterations are not a divergence either.
            _walk_body(stmt.body, path, out, terminal, owner)
            _walk_body(stmt.orelse, path, out, terminal, owner)
        elif isinstance(stmt, ast.With):
            _walk_body(stmt.body, path, out, terminal, owner)


def _exclusive(a, b, terminal):
    """True when the two statements cannot both run: diverging branches, or an escape between them.

    `a` is the write, `b` the raise. Two ways they never co-occur:
      - they take DIFFERENT sides of the same if/try, or
      - the write sits in a branch that always returns or raises, and the raise is outside it.
    """
    shared = 0
    for (na, ba), (nb, bb) in zip(a, b):
        if na != nb:
            break          # sequential constructs at the same depth - both can run, in order
        if ba != bb:
            return True    # opposite sides of the SAME construct - never both
        shared += 1
    # Whatever the write is nested in beyond the shared prefix, the raise is NOT inside. If any of
    # those branches always leaves the function, the raise below it is unreachable from the write.
    for key in a[shared:]:
        if terminal.get(key):
            return True
    return False


def _refusing_helpers(tree):
    """Module-level helpers that can refuse with a GLOBAL promise, and the promise they make.

    THE REASON THE AUDIT MISSED ITS OWN MOTIVATING DEFECT. op_set_light has no `raise` after
    `data.type = new_type` at all - it calls `_vec3(params, "location", ...)`, and the refusal lives
    inside _vec3. Looking only for a raise statement in the op's own body found nothing, and the
    defect that started this file went unreported by it. Measured by restoring the defect and
    re-running: 22 findings before, 22 after.

    So a CALL to one of these counts as a refusal point. The set is small and self-limiting because
    the claim has to be global: ops_common's take/take_int/take_float refuse with "'x' must be a
    number, got 'y'" and promise nothing about state, so the hundreds of ordinary parameter reads
    that follow a write are correctly silent.

    One round of transitivity - a helper that calls a refusing helper also refuses. Deeper chains
    exist in principle and none is in this addon today.
    """
    direct = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("op_"):
            continue
        for sub in ast.walk(fn):
            claim = _claim_text(sub)
            if claim:
                direct[fn.name] = claim
                break
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name in direct or fn.name.startswith("op_"):
            continue
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in direct:
                direct[fn.name] = direct[sub.func.id]
                break
    return direct


def _refusing_calls(stmt, helpers):
    """Every call inside this statement that can refuse, as (name, claim)."""
    found = []
    for sub in ast.walk(stmt):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in helpers:
            found.append((sub.func.id, helpers[sub.func.id]))
    return found


def scan_file(path):
    src = io.open(path, "rb").read().decode("utf-8")
    tree = ast.parse(src)
    name = os.path.basename(path)
    helpers = _refusing_helpers(tree)
    findings, delegating, reachable = [], [], []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        stmts, terminal, owner = [], {}, {}
        _walk_body(fn.body, [], stmts, terminal, owner)

        mutations = [(s, p, _is_mutation(s)) for s, p in stmts]
        mutations = [(s, p, m) for s, p, m in mutations if m]
        raises = [(s, p, _claim_text(s), None) for s, p in stmts]
        raises = [(s, p, c, h) for s, p, c, h in raises if c and not _undone_before(s, owner)]
        # ...and the calls that refuse on the op's behalf. See _refusing_helpers.
        for s, p in stmts:
            for helper, claim in _refusing_calls(s, helpers):
                if not _undone_before(s, owner):
                    raises.append((s, p, claim, helper))

        if raises and not mutations:
            # Refuses with a promise but writes nothing itself. If it calls a helper that writes,
            # the promise is only as good as the helper - and this audit cannot see that.
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and sub.func.id.startswith("_"):
                    delegating.append((name, fn.name, sub.func.id))
                    break

        if (name, fn.name) in ALLOWED:
            if mutations and raises:
                reachable.append((name, fn.name))
            continue
        for mstmt, mpath, label in mutations:
            for rstmt, rpath, claim, helper in raises:
                if rstmt.lineno <= mstmt.lineno or _exclusive(mpath, rpath, terminal):
                    continue
                findings.append({
                    "file": name, "func": fn.name, "wrote": label, "via": helper,
                    "writeLine": mstmt.lineno, "raiseLine": rstmt.lineno,
                    "claim": claim[:90],
                })
                break  # one finding per write is enough to send someone to the function
    return findings, delegating, reachable


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on any finding (for the release gate)")
    ap.add_argument("--show-delegating", action="store_true",
                    help="list ops whose writes happen in a helper, which this cannot follow")
    args = ap.parse_args()

    if not os.path.isdir(ADDON):
        print("addon not found at %s" % ADDON)
        return 2

    findings, delegating, reachable = [], [], []
    files = sorted(f for f in os.listdir(ADDON) if f.startswith("ops_") and f.endswith(".py"))
    for f in files:
        fo, de, re_ = scan_file(os.path.join(ADDON, f))
        findings.extend(fo)
        delegating.extend(de)
        reachable.extend(re_)

    # An allow-list entry that excuses nothing is either a typo or a fix that already landed. Either
    # way it is dead weight that quietly widens the audit's blind spot, so it is a failure.
    dead = sorted(k for k in ALLOWED if k not in reachable)
    print("audit_mutate_then_deny: %d op files, %d allow-listed, %d findings"
          % (len(files), len(ALLOWED), len(findings)))
    for key in dead:
        print("  DEAD ALLOW-LIST ENTRY: %s :: %s excuses nothing - delete it or fix the name"
              % key)
    for row in findings:
        print("")
        print("  %s :: %s" % (row["file"], row["func"]))
        print("    line %d wrote   %s" % (row["writeLine"], row["wrote"]))
        print("    line %d refuses %s%s"
              % (row["raiseLine"], ("via %s(): " % row["via"]) if row["via"] else "", row["claim"]))

    if args.show_delegating:
        print("")
        print("WRITES IN A HELPER - promise not verifiable here (%d):" % len(delegating))
        for fname, fn, helper in sorted(set(delegating)):
            print("  %-22s %-28s -> %s" % (fname, fn, helper))

    if findings:
        print("")
        print("Each of these refuses with a promise about state that the lines above it already"
              " broke.")
        print("Fix by parsing every value that CAN refuse above the first write, as ops_create's"
              " _place_values does.")
    if args.check and (findings or dead):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
