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

WHAT COUNTS AS A MUTATION. An attribute assignment (`obj.location = ...`), a setattr() (the only way
an availability table can write one), a `.new()` or `.link()` on a bpy.data collection, a `.remove()`
of something this op did NOT create, and a call to a helper that does any of those.

AND WHAT DOES NOT, each for a reason that cost a false finding. `bmesh.new()` builds a scratch mesh
that touches nothing until `to_mesh`. `os.remove()` on the op's own probe file is housekeeping, not
scene state. A `.remove()` of something created a few lines up is an UNDO, which is the opposite of
a mutation. Subscript targets (`out["x"] = ...`) are locals by construction - which is also why
set_custom_property, whose product is `obj[key] = value`, is one of the 16 this cannot judge.

MUTUALLY EXCLUSIVE BRANCHES ARE NOT FINDINGS. This is the check's main precision feature and the
reason it is an AST walk rather than a grep:

    if isinstance(v, (list, tuple)):
        obj.location = v
    else:
        raise MifOpError("... NOTHING was changed.")

the raise is lexically after the write and can never follow it. Every node is tagged with the
branch path it sits under, and a mutation/raise pair that diverges at any `if`, `try` or `for` is
skipped. Without this the audit reported dozens of pairs that cannot co-occur.

WHAT IT CANNOT SEE IS COUNTED ON EVERY RUN, in the REACH line, and deliberately NOT quoted here.
This paragraph has carried a hard number three times today and been wrong within the hour each time,
which is the same rot the state memory warns about. `--reach` names the ops.

An op with no recognised write is UNJUDGED, which is not the same as clean, and a gate at zero over
an unmeasured surface is the exact failure this project keeps naming - the matrix prints its own
reach beside its findings for the same reason.

FOUR HOLES HAVE BEEN CLOSED, each a different shape and each found by asking what the unjudged ops
had in common rather than by adding rules at random: a write inside a callee (`_place(obj, params)`
hid the original defect); a REMOVAL that is the op's product rather than an undo, which had left
every delete_/remove_/unlink_ op invisible; setattr(), which is how an availability table writes,
since set_light_shadow and set_material_settings never name an attribute in source; and a bpy.ops
operator, which is how transform_apply, modifier_apply and the exporters change the file.

What remains writes through a subscript on a datablock (set_custom_property's `obj[key] = value`)
or in a subprocess (render_animation). Each needs its own rule, and a rule per op is how an audit
becomes a list of special cases.
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

# WHAT THE VERB PROMISES. "NOTHING was changed" is a promise about the scene; "NOTHING was baked" is
# a promise about the bake. bake_texture switches the active UV layer, turns use_nodes on and makes
# an image before it can discover the colour config has no non-colour space to write into - and it
# says "NOTHING was baked", which is true. Holding it to a sentence it did not say would be wrong.
#
# Reported separately rather than dropped: what those ops leave behind is real (bake_texture's
# orphaned image among it), it is just a LEAK question rather than a broken promise, and the leak
# counter in blender_version_matrix is where that gets judged. Silence here would lose it.
OPERATION_VERBS = ("baked", "rendered", "written", "exported", "imported", "saved")

# THE MIRROR CLAIM. "The node WAS added as '%s'." is an instruction to go and clean something up, so
# it has to be true on every path that can reach it. A refusal saying this with nothing created
# sends the caller after an object that does not exist, under a name that belongs to nothing.
POSITIVE_CLAIM = re.compile(r"\b(?:The [a-z ]+|It|A [a-z ]+) WAS ([a-z]+)")

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


# A value named like a saved copy. `data.use_nodes = nodes_before` is a RESTORE, not a change, and
# counting it made the fix for set_light_ies report itself as a new finding.
_RESTORE_HINTS = ("before", "_prev", "prev_", "original", "orig_", "snapshot", "snap")


def _is_restore(node):
    """Is this assignment putting a saved value back?"""
    value = node.value if isinstance(node, ast.Assign) else None
    for sub in ast.walk(value) if value is not None else ():
        if isinstance(sub, ast.Name) and any(h in sub.id.lower() for h in _RESTORE_HINTS):
            return True
    return False


_CREATE_HINTS = ("new", "create", "add", "append", "load", "copy", "duplicate")


def _locally_created(fn):
    """Names bound from something that CREATES, so a later .remove() of one is an undo.

    `node = tree.nodes.new(...)` binds `node`; removing it before a refusal is putting things back.
    Removing a name that came from a lookup is not - it is the op doing its job, and 29 ops that
    remove for a living had no visible write at all while .remove() was read only as an undo.
    """
    made = set()
    for stmt in ast.walk(fn):
        if not isinstance(stmt, ast.Assign):
            continue
        for sub in ast.walk(stmt.value):
            if isinstance(sub, ast.Call):
                attr = getattr(sub.func, "attr", None) or getattr(sub.func, "id", "") or ""
                if any(h in attr.lower() for h in _CREATE_HINTS):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            made.add(tgt.id)
    return made


def _is_mutation(node, made=()):
    """Does this statement write state a caller could observe? Returns a label or None."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        # setattr() IS an attribute assignment, written the only way an availability table can write
        # one. set_light_shadow, set_material_settings and set_render_settings drive their writes
        # from a {param: (attr, types, builds)} map and never name the attribute in source, so a
        # rule that only saw `x.y = z` was blind to every op built that way - and those are exactly
        # the ops whose whole job is writing lots of properties.
        # A bpy.ops OPERATOR IS A WRITE. transform_apply, modifier_apply, save_as_mainfile,
        # open_mainfile and the exporters all change the file, and an op that drives one has no
        # attribute assignment for a rule about `x.y = z` to find. Five of the sixteen unjudged ops
        # were invisible for exactly this reason.
        dotted_call = _dotted(call.func) or ""
        if dotted_call.startswith("bpy.ops.") and dotted_call.count(".") >= 3:
            return "%s()" % dotted_call
        # frame_set MOVES THE SCENE. evaluate_at_frame steps the timeline to read a value and the
        # frame stays where it was left, so a refusal below one has changed the scene as surely as
        # any property write - and it is a method call, invisible to a rule about `x.y = z`.
        if getattr(call.func, "attr", "") == "frame_set":
            return "%s()" % (dotted_call or "frame_set")
        if isinstance(call.func, ast.Name) and call.func.id == "setattr" and len(call.args) >= 2:
            return "setattr(%s, ...)" % (_dotted(call.args[0])
                                         or getattr(call.args[0], "id", "<expr>"))
        if isinstance(call.func, ast.Attribute) and call.func.attr in CLEANUP and call.args:
            # os.remove() is housekeeping on a temp file, not a change to the scene.
            # render_animation writes a probe, deletes it, and was credited with a mutation for it.
            if (_dotted(call.func) or "").startswith(("os.", "shutil.", "pathlib.")):
                return None
            arg = call.args[0]
            # Removing something this op MADE is an undo, and _undone_before already reads it that
            # way. Removing anything else is the write the caller asked for.
            if not (isinstance(arg, ast.Name) and arg.id in made):
                return "%s() removes" % (_dotted(call.func) or call.func.attr)
            return None
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        if isinstance(node, ast.Assign) and _is_restore(node):
            return None
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

    ONLY MifOpError COUNTS, and that is complete rather than convenient: the addon has 723
    MifOpError raises against 3 RuntimeError, 2 ValueError and 1 ConnectionError, and not one of
    those six carries a "NOTHING was" promise. Measured 2026-09-04, because a refusal contract
    enforced on one exception type while another quietly makes the same promise is the shape this
    file exists to catch.
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
    hit = GLOBAL_CLAIM.search(joined)
    if not hit:
        return None
    return " ".join(joined.split())


def _is_state_claim(claim):
    """Does the promise cover scene state, or only the operation it names?

    AN INTERPOLATED VERB LANDS HERE, in the strict list. check_output_path says "NOTHING was %s."
    and takes the verb from its caller, so the word is only knowable at the call site - resolving it
    would mean following the argument, and the audit does not do whole-program flow anywhere else.
    Defaulting to the STATE list means such a finding is reported rather than filed under "not a
    failure", which is the safe direction for something that gates at zero: a scoped promise wrongly
    reported gets read and dismissed, a state promise wrongly scoped is never read at all.
    """
    hit = GLOBAL_CLAIM.search(claim or "")
    if not hit:
        return False
    verb = hit.group(0).split()[2].rstrip(".;")
    return verb not in OPERATION_VERBS


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
        # Putting a saved value back is the other honest way to keep the promise, and the one that
        # fits a scalar. set_light_ies turns use_nodes on to look for a node tree and sets it back
        # before refusing; only removals were recognised, so the fix reported itself as a finding.
        if isinstance(stmt, ast.Assign) and _is_restore(stmt):
            return True
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)                     and sub.func.attr in CLEANUP:
                return True
    return False


def _rolled_back_by_except(mpath, rpath, rollbacks):
    """Is the write undone by an `except` that cleans up and re-raises?

    The standard shape in this addon:

        try:
            ...validate against the thing just created...
        except MifOpError:
            if created:
                bpy.data.materials.remove(mat)
            raise

    Only writes INSIDE the try are covered. create_material also flips use_nodes on an EXISTING
    material above the try, and that one is a real finding: `created` is false, nothing is removed,
    and the caller's material keeps a change made under "NOTHING was changed".
    """
    for i, key in enumerate(mpath):
        if key[1] != "try" or key not in rollbacks or not rollbacks[key]:
            continue
        if rpath[:i + 1] == mpath[:i + 1]:
            return True
    return False


def _except_rolls_back(node):
    """Does any handler on this try remove something and re-raise?"""
    for h in node.handlers:
        raises = any(isinstance(s, ast.Raise) for s in ast.walk(h))
        cleans = any(isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute)
                     and s.func.attr in CLEANUP for s in ast.walk(h))
        if raises and cleans:
            return True
    return False


def _restored_by_finally(mpath, rpath, finals, label=None):
    """Is the write undone on the way out by a `finally` the raise passes through?

    export_scene deselects everything, sets the active object and moves the scene frame range, and
    every one of its refusals says "NOTHING was written" - which is true, and the scene changes are
    true too. It gets away with it honestly: the whole block is a try whose finally puts the frame
    range back and calls selection_restore(snap). Three findings, all correct code.

    Recognised by shape rather than proved: the write and the raise are inside the same try, and its
    finally either assigns the same attribute NAME or calls something *restore*. That is a heuristic
    and deliberately a narrow one - it cannot tell a complete restore from a partial one, so it says
    so here rather than pretending the check is exact.
    """
    for i, key in enumerate(mpath):
        if key[1] != "try" or key not in finals:
            continue
        # THE RAISE DOES NOT HAVE TO BE INSIDE THE TRY. Requiring that missed the commoner and
        # SAFER case: a write inside a try whose finally restores it, and a refusal further down
        # the function. There the finally has ALREADY run by the time the refusal fires - more
        # certainly restored than the inside case, where it runs during unwinding. Both are
        # restored, so the position of the raise decides nothing. bake_to_keyframes samples world
        # matrices inside such a try and refuses well below it.
        restored = finals[key]
        if restored is True:
            return True
        # MATCHED BY NAME rather than by presence. Any assignment in any finally used to suppress
        # every write inside that try; requiring the finally to touch the same attribute or call the
        # same method keeps export_scene and bake_to_keyframes silent without silencing a try whose
        # finally happens to clean up something unrelated.
        if restored and any(name in (label or "") for name in restored):
            return True
    return False


def _finally_restores(node):
    """What the finally puts back: attribute names it re-assigns and methods it calls.

    True means "calls something named *restore*", which is a whole-state restore and needs no name
    matching. Otherwise the caller checks whether the write it is judging touches one of these.
    """
    names = set()
    for stmt in node.finalbody:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store):
                names.add(sub.attr)
            if isinstance(sub, ast.Call):
                fname = getattr(sub.func, "id", None) or getattr(sub.func, "attr", "") or ""
                if "restore" in fname.lower():
                    return True
                # A RESTORING CALL, not only an assignment. bake_to_keyframes steps the timeline to
                # sample world matrices and puts it back with `sc.frame_set(started_on)` in a
                # finally - a method call, so a rule that only looked for `x.y = ...` started
                # calling correct code a finding the moment frame_set counted as a write.
                if fname:
                    names.add(fname)
    return names


def _bmesh_scratch_before(fn):
    """Line number up to which writes land on a SCRATCH bmesh, or None.

    uv_unwrap builds `bm = _bmesh.new()`, clears and sets e.seam on its edges, and refuses in the
    middle - and nothing it wrote has touched the mesh yet, because a bmesh only reaches the real
    data at bm.to_mesh(mesh). Two findings, both correct code.

    The module docstring already said bmesh.new() is scratch and does not count; that only excluded
    the CALL, not the attribute writes on the elements it hands out, which is where the seams are
    actually set. Everything before the first to_mesh in a function that makes a bmesh is treated as
    scratch - coarse, and it is why the rule is stated here rather than assumed.
    """
    makes_bmesh = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr == "new"
                      and isinstance(n.func.value, ast.Name)
                      and "bmesh" in n.func.value.id.lower()
                      for n in ast.walk(fn))
    if not makes_bmesh:
        return None
    lines = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "to_mesh"]
    return min(lines) if lines else None


def _restored_in_handlers(fn):
    """Dotted targets an except handler puts back, e.g. {"sc.render.engine"}.

    The scalar twin of _removed_names. set_render_settings switches the engine ABOVE its try because
    _apply_common has to validate `samples` against the engine now selected, and restores it in the
    handler. Nothing is removed there - a render engine is a value, not a datablock - so a rule that
    only recognises .remove() called that correct code a finding.
    """
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for h in node.handlers:
            for sub in ast.walk(h):
                if isinstance(sub, ast.Assign) and _is_restore(sub):
                    for tgt in sub.targets:
                        if isinstance(tgt, ast.Attribute):
                            dotted = _dotted(tgt)
                            if dotted:
                                out.add(dotted)
    return out


def _removed_names(fn):
    """Locals handed to a .remove() anywhere in this function.

    create_material writes mat.use_nodes ABOVE its try and rolls back inside it with
    bpy.data.materials.remove(mat) - so the write IS undone, by removing the thing written to, and
    no rule about try bodies can see that.

    ONLY REMOVALS IN AN EXCEPT HANDLER COUNT. Counting every removal in the function was too coarse
    by exactly one real finding: op_add_group_node removes its node before three of its raises and
    NOT before the _socket_value one, so matching the name anywhere excused the site that still
    needed reporting. A rollback handler is the one place a removal is unambiguously on the refusal
    path.
    """
    names = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for h in node.handlers:
            for sub in ast.walk(h):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)                         and sub.func.attr in CLEANUP and sub.args                         and isinstance(sub.args[0], ast.Name):
                    names.add(sub.args[0].id)
    return names


def _looks_like_creation(stmt):
    """Any sign at all that this statement built something. Deliberately generous - see the module
    comment on _positive_claim: this test only suppresses mirror findings, so erring wide costs a
    missed lie and erring narrow costs a false accusation."""
    # _own_nodes, NOT ast.walk. The same compound-statement mistake the main pass had: walking an
    # `if` descends into its body, so the statement `if ok: node = nodes.new(...)` looked like a
    # creation at the OUTER level and backed a claim made in the `else` arm. Caught by the
    # self-test's third mirror case, which is the whole reason it has one.
    for sub in _own_nodes(stmt):
        if isinstance(sub, ast.Call):
            attr = getattr(sub.func, "attr", None) or getattr(sub.func, "id", "") or ""
            # CONTAINED, not prefixed. op_add_group_interface builds its socket with
            # `_iface_new(...)`, and a leading underscore is enough to defeat startswith - both of
            # that op's honest "The socket WAS created." messages were reported as lies by it.
            if any(h in attr.lower() for h in _CREATE_HINTS):
                return True
        if isinstance(sub, (ast.Attribute, ast.Subscript)) and isinstance(sub.ctx, ast.Store):
            return True
    return False


def _positive_claim(node):
    """If this raise asserts something WAS created/added, return the sentence."""
    if not isinstance(node, ast.Raise) or node.exc is None:
        return None
    exc = node.exc
    if not (isinstance(exc, ast.Call) and getattr(exc.func, "id", "") == "MifOpError"):
        return None
    joined = " ".join(sub.value for sub in ast.walk(exc)
                      if isinstance(sub, ast.Constant) and isinstance(sub.value, str))
    hit = POSITIVE_CLAIM.search(joined)
    return " ".join(joined.split()) if hit else None


def _progress_names(fn):
    """Counters and flags the op updates as it works: `removed += 1`, `did = True`.

    A refusal guarded by one of these cannot follow the write that moves it. delete_keyframe removes
    in a loop and refuses `if removed == 0`; the UE audit met the same shape as `if (!bDidMutate)`
    and this is the Python spelling, with an integer instead of a bool.
    """
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, bool):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


def _guarded_by_progress(rstmt, blocks_by_stmt, progress):
    """Is this refusal behind a test of a counter or flag the op advances as it writes?"""
    guard = blocks_by_stmt.get(id(rstmt))
    if guard is None:
        return False
    for sub in ast.walk(guard):
        if isinstance(sub, ast.Name) and sub.id in progress:
            return True
    return False


def _ends_terminal(body):
    """Does this branch always leave the function - so nothing after it can run?"""
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


def _walk_body(body, path, out, terminal, owner, finals, rollbacks, guards=None, guard=None):
    """Tag every statement with the branch path it sits under, so exclusivity is decidable.

    `path` is a list of (node id, which-branch) pairs. Two statements can both run only if neither
    diverges from the other at a shared `if`/`try`/loop.

    `terminal` records which branches end in return/raise. assign_action writes
    `obj.animation_data.action = None` inside `if clear:` and RETURNS at the end of that branch, so
    the "no action named X. NOTHING was changed." raise 19 lines below is unreachable from it. Branch
    paths alone call that a finding, because the raise is at the function's top level and never
    diverges from the write - it is the RETURN that separates them, not the branch.
    """
    if guards is None:
        guards = {}
    for stmt in body:
        out.append((stmt, tuple(path)))
        owner[id(stmt)] = body
        if guard is not None:
            guards[id(stmt)] = guard
        if isinstance(stmt, ast.FunctionDef):
            continue  # a nested def is a separate scope, judged on its own
        if isinstance(stmt, ast.If):
            for key, sub in (("then", stmt.body), ("else", stmt.orelse)):
                terminal[(id(stmt), key)] = _ends_terminal(sub)
                _walk_body(sub, path + [(id(stmt), key)], out, terminal, owner, finals, rollbacks,
                           guards, stmt.test if key == "then" else guard)
        elif isinstance(stmt, ast.Try):
            finals[(id(stmt), "try")] = _finally_restores(stmt)
            rollbacks[(id(stmt), "try")] = _except_rolls_back(stmt)
            for key, sub in ([("try", stmt.body)]
                             + [("except%d" % i, h.body) for i, h in enumerate(stmt.handlers)]
                             + [("orelse", stmt.orelse), ("finally", stmt.finalbody)]):
                terminal[(id(stmt), key)] = _ends_terminal(sub)
                _walk_body(sub, path + [(id(stmt), key)], out, terminal, owner, finals, rollbacks, guards, guard)
        elif isinstance(stmt, (ast.For, ast.While)):
            # A loop body can run then fall through to a later raise, so it is NOT exclusive with
            # code after the loop - but two different iterations are not a divergence either.
            _walk_body(stmt.body, path, out, terminal, owner, finals, rollbacks, guards, guard)
            _walk_body(stmt.orelse, path, out, terminal, owner, finals, rollbacks, guards, guard)
        elif isinstance(stmt, ast.With):
            _walk_body(stmt.body, path, out, terminal, owner, finals, rollbacks, guards, guard)


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


_BODY_FIELDS = ("body", "orelse", "handlers", "finalbody")


def _own_nodes(stmt):
    """Nodes belonging to this statement itself, not to the statements nested inside it.

    An `if`'s test is its own; the statements in its body are not. Without this a try/if/for is
    credited with every call underneath it, at the compound statement's line number.
    """
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    for field, value in ast.iter_fields(stmt):
        if field in _BODY_FIELDS:
            continue
        for item in (value if isinstance(value, list) else [value]):
            if isinstance(item, ast.AST):
                for node in ast.walk(item):
                    yield node


def _mutating_call(stmt, writers):
    """Label for a call to a helper that writes, or None.

    Skips a statement that is itself already a mutation - `obj.foo = _helper()` is one write, not
    two, and reporting it twice would put the same line in the list under two names.

    THE LABEL NAMES WHAT THE HELPER WRITES, not just that it does. _restored_by_finally matches a
    finally against the write it is judging BY NAME, and bake_to_keyframes restores with
    `sc.frame_set(...)` while its write is `_sample_world()` - the two are the same property and a
    label saying only "_sample_world() writes" could not show it.
    """
    if isinstance(stmt, ast.Raise):
        return None
    for sub in _own_nodes(stmt):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in writers:
            what = writers[sub.func.id] if isinstance(writers, dict) else None
            return "%s() writes %s" % (sub.func.id, " ".join(sorted(what))) if what \
                else "%s() writes" % sub.func.id
    return None


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
    # NESTED defs count too. op_add_nla_strip's refusals live in a local _undo_and_refuse(), and a
    # walker that skips nested functions simply stops seeing them - which looks like the finding was
    # fixed. It was, but the audit would then miss a future regression in the same place, so the
    # reach has to survive the refactor that the audit itself prompted.
    candidates = [n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and not n.name.startswith("op_")]
    # ...AND THE SHARED ONES. ops_common holds the helpers every file imports - take_float,
    # take_int, finite_float, finite_int, check_axis_dict, check_output_path - and every one of them
    # can refuse. Scanning only the file's OWN functions made them invisible, and that blind spot
    # was demonstrated rather than theorised: _apply_common was given finite_int inside its commit
    # block, wrote resolution_x, refused on resolution_y, and this audit reported zero. Measured on
    # 5.0.1 - the width moved 1920 -> 123 under "NOTHING was changed".
    common = os.path.join(ADDON, "ops_common.py")
    if os.path.abspath(getattr(tree, "_mif_path", "")) != os.path.abspath(common):
        try:
            shared = ast.parse(io.open(common, "rb").read().decode("utf-8"))
        except OSError:
            shared = None
        if shared is not None:
            candidates += [n for n in ast.walk(shared)
                           if isinstance(n, ast.FunctionDef) and not n.name.startswith("op_")]
    direct = {}
    for fn in candidates:
        for sub in ast.walk(fn):
            claim = _claim_text(sub)
            if claim:
                # A helper that CLEANS UP before refusing is keeping the promise, not breaking it.
                inner, iterm, iowner = [], {}, {}
                _walk_body(fn.body, [], inner, iterm, iowner, {}, {})
                if not _undone_before(sub, iowner):
                    direct[fn.name] = claim
                break
    for fn in candidates:
        if fn.name in direct:
            continue
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in direct:
                direct[fn.name] = direct[sub.func.id]
                break
    return direct


def _cleaning_helpers(tree):
    """Functions that REMOVE something before they raise - they keep the promise, not break it."""
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        inner, iterm, iowner = [], {}, {}
        _walk_body(fn.body, [], inner, iterm, iowner, {}, {})
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Raise) and _undone_before(sub, iowner):
                out.add(fn.name)
                break
    return out


def _refusing_calls(stmt, helpers, cleaners):
    """Every call inside this statement that can refuse, as (name, claim).

    TWO WAYS A CALL REFUSES, and the second one is why the first is not enough.

    It calls a helper that raises with a promise of its own - _vec3, _pick_enum, _socket_value.

    Or it PASSES the promise in. op_add_nla_strip's local _undo_and_refuse takes the message as an
    argument and raises `MifOpError(message)`, so the helper's own body holds no string to match and
    the claim only exists at the call site. Matching argument strings catches it, and catches any
    future helper written the same way. A call to something that cleans up first is skipped: that is
    the honest version of this shape and it is what _undo_and_refuse actually does.
    """
    if isinstance(stmt, ast.Raise):
        return []          # already counted by _claim_text; do not report it twice
    found = []
    for sub in _own_nodes(stmt):
        if not isinstance(sub, ast.Call):
            continue
        callee = sub.func.id if isinstance(sub.func, ast.Name) else None
        if callee == "MifOpError" or callee in cleaners:
            continue
        if callee in helpers:
            found.append((callee, helpers[callee]))
            continue
        for arg in sub.args:
            claim = None
            for node in ast.walk(arg):
                if isinstance(node, ast.Constant) and isinstance(node.value, str)                         and GLOBAL_CLAIM.search(node.value):
                    claim = " ".join(node.value.split())
                    break
            if claim:
                found.append((callee or "<call>", claim))
                break
    return found


def scan_file(path):
    return scan_source(os.path.basename(path),
                       io.open(path, "rb").read().decode("utf-8"))


def scan_source(name, src):
    """The whole audit, given a name and source text. Split out of scan_file for --selftest."""
    tree = ast.parse(src)
    helpers = _refusing_helpers(tree)
    cleaners = _cleaning_helpers(tree)
    writers = {}
    for helper in ast.walk(tree):
        if not isinstance(helper, ast.FunctionDef) or helper.name.startswith("op_"):
            continue
        inner, iterm, iowner, ifin, iroll = [], {}, {}, {}, {}
        _walk_body(helper.body, [], inner, iterm, iowner, ifin, iroll)
        wrote = set()
        for s, _p in inner:
            label = _is_mutation(s)
            if label:
                wrote.add(label.split("(")[0].split(" =")[0].split(".")[-1])
        if wrote:
            writers[helper.name] = wrote
    findings, scoped, delegating, reachable, unbacked = [], [], [], [], []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        removed = _removed_names(fn)
        scratch_until = _bmesh_scratch_before(fn)
        put_back = _restored_in_handlers(fn)
        stmts, terminal, owner, finals, rollbacks, guards = [], {}, {}, {}, {}, {}
        _walk_body(fn.body, [], stmts, terminal, owner, finals, rollbacks, guards)

        # A CALL TO A HELPER THAT WRITES IS A WRITE. The UE twin went blind to a whole file this
        # way - eleven IKRig endpoints wrapping every write in one helper - and here it hid three:
        # op_set_keyframe delegating to _apply_interpolation, and both particle ops to
        # _apply_particle_settings. The helper is judged on its own too; this is about where the
        # CALLER puts the call relative to its own refusal.
        made = _locally_created(fn)
        progress = _progress_names(fn)
        # A WRITE IN A `finally` IS CLEANUP. Every finally in this addon exists to put something
        # back, so counting one as the op's own mutation reports the restore as the damage - which
        # is exactly what happened to bake_to_keyframes' `finally: sc.frame_set(started_on)` the
        # moment frame_set started counting as a write.
        mutations = [(s, p, _is_mutation(s, made) or _mutating_call(s, writers)) for s, p in stmts
                     if not any(key[1] == "finally" for key in p)]
        mutations = [(s, p, m) for s, p, m in mutations if m
                     and m.split(".")[0] not in removed
                     and not (scratch_until is not None and s.lineno < scratch_until)
                     and m.split(" =")[0] not in put_back]
        raises = [(s, p, _claim_text(s), None) for s, p in stmts]
        raises = [(s, p, c, h) for s, p, c, h in raises if c and not _undone_before(s, owner)]
        # ...and the calls that refuse on the op's behalf. See _refusing_helpers.
        for s, p in stmts:
            for helper, claim in _refusing_calls(s, helpers, cleaners):
                if not _undone_before(s, owner):
                    raises.append((s, p, claim, helper))

        if raises and not mutations:
            # Refuses with a promise but writes nothing itself. If it calls a helper that WRITES,
            # the promise is only as good as the helper, and this audit cannot see that.
            #
            # Only helpers that actually mutate are listed. Naming every helper called from a
            # promising op put _counts, _orphans and _op_exists on a list headed "writes in a
            # helper" - pure readers, every one, and a blind spot advertised as bigger than it is
            # is its own kind of wrong.
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and sub.func.id in writers:
                    delegating.append((name, fn.name, sub.func.id))
                    break

        if (name, fn.name) in ALLOWED:
            if mutations and raises:
                reachable.append((name, fn.name))
            continue
        # THE MIRROR PASS. A raise claiming something WAS created needs a creation above it that
        # can actually have run. Judged against the SAME mutation list, so a helper that writes
        # counts here too - otherwise every op that delegates its creation would report falsely.
        for rstmt, rpath in stmts:
            # OPS ONLY. _write_node_property says "The node WAS added." fourteen times and is
            # telling the truth every time - its CALLER added the node. A helper cannot see what
            # its caller built, so judging one against its own body reports every honest handoff
            # as a lie. Fourteen of the first eighteen findings were that.
            if not fn.name.startswith("op_"):
                continue
            claim = _positive_claim(rstmt)
            if not claim:
                continue
            backed = any(s.lineno < rstmt.lineno and not _exclusive(p, rpath, terminal)
                         and _looks_like_creation(s)
                         for s, p in stmts)
            if not backed:
                unbacked.append({"file": name, "func": fn.name, "line": rstmt.lineno,
                                 "claim": claim[:110]})

        for mstmt, mpath, label in mutations:
            for rstmt, rpath, claim, helper in raises:
                # ONE CALL IS NOT A PAIR. _apply_common both writes and refuses, so the single
                # statement `applied = _apply_common(sc, params)` was reported against itself once
                # helper calls started counting as writes.
                if rstmt is mstmt:
                    continue
                if rstmt.lineno <= mstmt.lineno or _exclusive(mpath, rpath, terminal):
                    continue
                if (_restored_by_finally(mpath, rpath, finals, label)
                        or _rolled_back_by_except(mpath, rpath, rollbacks)):
                    continue
                if _guarded_by_progress(rstmt, guards, progress):
                    continue
                (findings if _is_state_claim(claim) else scoped).append({
                    "file": name, "func": fn.name, "wrote": label, "via": helper,
                    "writeLine": mstmt.lineno, "raiseLine": rstmt.lineno,
                    "claim": claim[:90],
                })
                break  # one finding per write is enough to send someone to the function
    return findings, scoped, delegating, reachable, unbacked


# Each case is (name, should_fire, source). The PAIRS are the point: a rule that only ever suppresses
# is indistinguishable from one that suppresses everything, and both lists this audit prints now read
# zero - which is the right answer and also the moment nobody can tell a working rule from a dead one.
SELFTEST = [
    ("plain mutate then deny", True, """
def op_probe(params):
    obj.location = params["loc"]
    if bad:
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("refusal comes first", False, """
def op_probe(params):
    if bad:
        raise MifOpError("bad. NOTHING was changed.")
    obj.location = params["loc"]
"""),
    ("opposite arms of one if", False, """
def op_probe(params):
    if ok:
        obj.location = params["loc"]
    else:
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("write in a branch that returns", False, """
def op_probe(params):
    if ok:
        obj.location = params["loc"]
        return {"ok": True}
    if other:
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("removed before the raise", False, """
def op_probe(params):
    obj = bpy.data.objects.new("x", None)
    obj.location = params["loc"]
    if bad:
        bpy.data.objects.remove(obj)
        raise MifOpError("bad. NOTHING was created.")
"""),
    ("restored before the raise", False, """
def op_probe(params):
    nodes_before = data.use_nodes
    data.use_nodes = True
    if bad:
        data.use_nodes = nodes_before
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("rolled back in an except", False, """
def op_probe(params):
    mat = bpy.data.materials.new("x")
    try:
        if bad:
            raise MifOpError("bad. NOTHING was changed.")
    except MifOpError:
        bpy.data.materials.remove(mat)
        raise
"""),
    ("restored in a finally", False, """
def op_probe(params):
    frames_before = scene.frame_start
    try:
        scene.frame_start = 5
        if bad:
            raise MifOpError("bad. NOTHING was changed.")
    finally:
        scene.frame_start = frames_before
"""),
    ("scoped promise", False, """
def op_probe(params):
    scene.frame_start = 5
    if bad:
        raise MifOpError("bad. NOTHING was changed to the preview range.")
"""),
    ("removal that is the op's product", True, """
def op_probe(params):
    fc.keyframe_points.remove(kp)
    if bad:
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("removal of something this op made", False, """
def op_probe(params):
    node = tree.nodes.new("ShaderNodeMath")
    if bad:
        tree.nodes.remove(node)
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("guarded by a progress counter", False, """
def op_probe(params):
    removed = 0
    for kp in points:
        fc.keyframe_points.remove(kp)
        removed += 1
    if removed == 0:
        raise MifOpError("none matched. NOTHING was deleted.")
"""),
    ("setattr counts as a write", True, """
def op_probe(params):
    setattr(data, attr, value)
    if bad:
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("os.remove is not a scene write", False, """
def op_probe(params):
    os.remove(probe)
    if bad:
        raise MifOpError("bad. NOTHING was changed.")
"""),
    ("scratch bmesh", False, """
def op_probe(params):
    bm = bmesh.new()
    for e in bm.edges:
        e.seam = True
    if bad:
        raise MifOpError("bad. NOTHING was changed.")
    bm.to_mesh(mesh)
"""),
]


# The mirror pass, both directions. It reads zero on the real addon, so nothing else shows it works.
MIRROR_SELFTEST = [
    ("claims a node was added, and added one", False, """
def op_probe(params):
    node = tree.nodes.new("ShaderNodeMath")
    if bad:
        raise MifOpError("bad. The node WAS added as 'x'.")
"""),
    ("claims a node was added, having added none", True, """
def op_probe(params):
    node = tree.nodes.get("x")
    if bad:
        raise MifOpError("bad. The node WAS added as 'x'.")
"""),
    ("creation in the other arm does not count", True, """
def op_probe(params):
    if ok:
        node = tree.nodes.new("ShaderNodeMath")
    else:
        raise MifOpError("bad. The node WAS added as 'x'.")
"""),
]


def selftest():
    """Run each case through scan_source and report both directions."""
    bad = 0
    for name, should_fire, src in MIRROR_SELFTEST:
        _f, _s, _d, _r, unbacked = scan_source("selftest.py", src)
        fired = bool(unbacked)
        ok = fired == should_fire
        bad += 0 if ok else 1
        print("  %-4s %-38s expected %-5s got %s"
              % ("ok" if ok else "FAIL", name, should_fire, fired))
    for name, should_fire, src in SELFTEST:
        findings, scoped, _d, _r, _u = scan_source("selftest.py", src)
        fired = bool(findings)
        ok = fired == should_fire
        bad += 0 if ok else 1
        print("  %-4s %-38s expected %-5s got %s"
              % ("ok" if ok else "FAIL", name, should_fire, fired))

    # The operation-verb split, which the two lists above cannot exercise now that both read zero.
    _f, scoped, _d, _r, _u = scan_source("selftest.py", """
def op_probe(params):
    image.generated_color = (1, 0, 1, 1)
    if bad:
        raise MifOpError("bad. NOTHING was baked.")
""")
    ok = len(scoped) == 1 and not _f
    bad += 0 if ok else 1
    print("  %-4s %-38s expected %-5s got %s"
          % ("ok" if ok else "FAIL", "operation verb lands in scoped", True, ok))

    print("")
    print("selftest: %d case(s), %d failure(s)"
          % (len(SELFTEST) + len(MIRROR_SELFTEST) + 1, bad))
    return bad


def reach():
    """(promising, judged, unjudged rows) - the surface this audit can actually speak about.

    An op with no recognised write is not clean. It is invisible, and a gate at zero over an
    unmeasured surface is the failure this project keeps naming: green that means nothing.
    """
    promising, judged, blind = 0, 0, []
    for fname in sorted(f for f in os.listdir(ADDON)
                        if f.startswith("ops_") and f.endswith(".py")):
        tree = ast.parse(io.open(os.path.join(ADDON, fname), "rb").read().decode("utf-8"))
        helpers = _refusing_helpers(tree)
        writers = set()
        for helper in ast.walk(tree):
            if isinstance(helper, ast.FunctionDef) and not helper.name.startswith("op_"):
                inner, a, b, c, d = [], {}, {}, {}, {}
                _walk_body(helper.body, [], inner, a, b, c, d)
                if any(_is_mutation(s) for s, _p in inner):
                    writers.add(helper.name)
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("op_"):
                continue
            stmts, t2, o2, f2, r2 = [], {}, {}, {}, {}
            _walk_body(fn.body, [], stmts, t2, o2, f2, r2)
            # A READER HAS NOTHING TO JUDGE. list_/get_/describe_/find_ ops refuse when their
            # target is missing and write nothing at all, so counting them as UNJUDGED overstates
            # the gap with ops that are correctly clean. Same prefix set audit_read_purity uses.
            if fn.name.startswith(("op_list_", "op_get_", "op_describe_", "op_find_")):
                continue
            if not any(_claim_text(s) or _refusing_calls(s, helpers, set()) for s, _p in stmts):
                continue
            promising += 1
            if any(_is_mutation(s, _locally_created(fn)) or _mutating_call(s, writers)
                   for s, _p in stmts):
                judged += 1
            else:
                blind.append((fname, fn.name))
    return promising, judged, blind


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on any finding (for the release gate)")
    ap.add_argument("--show-delegating", action="store_true",
                    help="list ops whose writes happen in a helper, which this cannot follow")
    ap.add_argument("--reach", action="store_true",
                    help="list the ops this audit cannot judge")
    ap.add_argument("--selftest", action="store_true",
                    help="prove each precision rule can both fire and stay quiet")
    args = ap.parse_args()

    if args.selftest:
        return 1 if selftest() else 0

    if not os.path.isdir(ADDON):
        print("addon not found at %s" % ADDON)
        return 2

    findings, scoped, delegating, reachable, unbacked = [], [], [], [], []
    files = sorted(f for f in os.listdir(ADDON) if f.startswith("ops_") and f.endswith(".py"))
    for f in files:
        fo, sc_, de, re_, un = scan_file(os.path.join(ADDON, f))
        findings.extend(fo)
        scoped.extend(sc_)
        unbacked.extend(un)
        delegating.extend(de)
        reachable.extend(re_)

    # An allow-list entry that excuses nothing is either a typo or a fix that already landed. Either
    # way it is dead weight that quietly widens the audit's blind spot, so it is a failure.
    dead = sorted(k for k in ALLOWED if k not in reachable)
    print("audit_mutate_then_deny: %d op files, %d allow-listed, %d findings"
          % (len(files), len(ALLOWED), len(findings)))
    promising, judged, blind = reach()
    print("REACH - %d op(s) promise something about state; a write is visible in %d."
          % (promising, judged))
    print("        %d are UNJUDGED, not clean - they write through a subscript on a datablock or"
          % len(blind))
    print("        in a subprocess. A gate at zero needs this line beside it. --reach names them.")
    if args.reach:
        print("")
        print("UNJUDGED OPS (%d):" % len(blind))
        for fname, op in blind:
            print("  %-22s %s" % (fname, op))
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
    if scoped:
        print("")
        print("PROMISED ONLY ABOUT THE OPERATION, not the scene - %d. NOT failures: an op that says"
              % len(scoped))
        print("\"NOTHING was baked\" and then leaves an image behind has told the truth. Listed")
        print("because what they leave behind is a LEAK question, judged by the matrix, not here.")
        for row in scoped:
            print("  %-24s line %-5d %s" % (row["func"], row["writeLine"], row["wrote"]))

    if unbacked:
        print("")
        print("CLAIMS SOMETHING WAS CREATED, WITH NOTHING CREATED ABOVE IT - %d:" % len(unbacked))
        print("These send a caller to clean up an object that does not exist.")
        for row in unbacked:
            print("  %-24s line %-5d %s" % (row["func"], row["line"], row["claim"][:70]))

    if args.check and (findings or dead or unbacked):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
