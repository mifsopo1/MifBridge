#!/usr/bin/env python3
"""Mechanical contract check between the MCP server and its two backends.

WHY THIS FILE EXISTS
====================
The UE half of MifBridge is held together by the compiler: an endpoint declared
with MIF_DECL and never bound with MIF_BIND is a link error, so that pair cannot
drift. The Blender half has no compiler. It drifted, and the flagship road-mesh
round trip shipped dead on arrival:

  * server.py called _blender("scene_info"), ("select_edges") and
    ("extrude_skirt"); none of the three existed in the addon's OPS tables, and
    mif_mesh_roundtrip DEFAULTED to the missing one.
  * the one op that did exist on both sides, bevel_edges, was sent `selector`
    (nested) and `preserveX`, neither of which is in its reject_unknown set - so
    every call the MCP was capable of making was refused.

Both are mechanically detectable. This script is the missing compiler:

  CHECK 1  op parity      the set of _blender("...") literals in the MCP server
                          == the union of the addon modules' OPS keys, BOTH ways.
  CHECK 2  param parity   every keyword each _blender("op", ...) call site sends
                          is in that op's reject_unknown() accepted set.
  CHECK 3  UE parity      MIF_BIND(...) names == _post("...") literals, minus a
                          recorded, named exemption list. Mirrors the manual
                          `comm` recipe in docs/00_ARCHITECTURE.md so the same
                          run covers both backends.

FAIL-CLOSED. Anything this script cannot statically resolve - a computed op
name, a **kwargs splat, an accepted-key set built from something fancier than a
literal - is reported as a FAILURE, not skipped. A check that quietly could not
run is the exact defect it exists to catch.

It imports nothing from either backend (no bpy, no requests, no fastmcp): it
parses the files with `ast`, so it runs anywhere, in CI or on a laptop with
neither Blender nor Unreal installed.

    python tools/parity_check.py            # exit 0 clean, 1 on any drift
    python tools/parity_check.py --verbose  # also print the resolved tables
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MCP_SERVER = os.path.join(HERE, "mcp-server", "server.py")
ADDON_DIR = os.path.join(HERE, "blender-addon", "MifBlender")
# DERIVED FROM server.py, NOT HAND-MAINTAINED — and the comment this replaces explains why.
#
# This used to be a hardcoded tuple, with a note saying: "A module missing from this tuple is
# INVISIBLE to check 1: its ops read as 'registered nowhere', and — worse — a tool that
# legitimately calls one gets reported as dead. That happened on 2026-08-15, when ops_gen.py (the
# ComfyUI generation chain) was added to the addon and this tuple was not updated, so the checker
# blamed five correct wrappers instead of itself. Cross-check against server.py's `table.update(...)`
# calls, which are the real registry."
#
# It happened a SECOND time on 2026-08-30, when ops_create.py and ops_material.py were added: nine
# correct new wrappers were reported dead, by a checker whose own comment already described the
# failure. A note telling a human to cross-check against the real registry is a worse mechanism than
# reading the real registry, so this now parses server.py's _op_table() for `table.update(ops_X.OPS)`
# and takes that list. A module added to the addon and wired into server.py is now visible here with
# no second edit, and a module NOT wired in cannot silently pass either.
def _addon_op_modules():
    src = os.path.join(ADDON_DIR, "server.py")
    try:
        text = io.open(src, encoding="utf-8").read()
    except OSError:
        return ()
    names = re.findall(r"table\.update\(\s*(ops_[A-Za-z0-9_]+)\.OPS\s*\)", text)
    # Order-preserving dedupe, so the report reads in registration order.
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n + ".py")
    return tuple(out)


ADDON_OP_MODULES = _addon_op_modules()

UE_BIND_FILE = os.path.join(ROOT, "Source", "MifBridge", "Private", "MifBridgeCommon.cpp")

# Tool-name -> op-name deviations. bl_status calls `ping` because a health probe
# reads better than a tool called bl_ping; the UE side has the same one
# deviation (compile_blueprint -> compile). Recorded, not silently tolerated.
KNOWN_OP_ALIASES = {"bl_status": "ping"}

# Addon ops with no _blender() call site, and why. An exemption is a DECISION,
# so each one carries its reason and every run prints the list - a silent
# allowlist is just drift with paperwork.
# EMPTIED 2026-08-15 on Andre's explicit instruction: run_python is now wrapped as
# bl_run_python, so removing the exemption is what puts it under the check-2 param
# parity test rather than leaving it unverified. The safety model is unchanged and
# does not live here - the addon preference `allow_run_python` still gates every
# call, and the tool's own docstring carries the warnings.
BLENDER_TOOLLESS_EXEMPTIONS = {}

# Endpoints that ship with MIF_DECL + MIF_BIND + a handler and deliberately have
# no @mcp.tool - HTTP-reachable, MCP-invisible. Recorded in
# docs/00_ARCHITECTURE.md; subtracted here so a known delta is not reported as
# fresh drift every run. Adding to this list is a decision, not a fix.
# EMPTIED 2026-08-15. These five sat here after being reported as uncovered on
# 2026-08-12 - but they were WRAPPED the same day (docs/13 records "all five are
# now wrapped, 0 uncovered") and nobody removed the exemptions. A stale exemption
# is worse than a missing one: it would have suppressed the ue-parity error if a
# wrapper were later deleted. The check below now catches this shape too.
#   was: add_component_bound_event, reparent_blueprint, retarget_variable_node,
#        set_cast_purity, set_variable_type
UE_TOOLLESS_EXEMPTIONS = set()

# _post targets registered at RUNTIME by the MifKismetReconstructor provider
# plugin (Public/MifBridgeEndpointRegistry.h). They never appear in MIF_BIND and
# their absence there is not drift.
UE_EXTERNAL_PREFIXES = ("kr_",)

# Params _blender() handles itself and never puts in the frame's params object.
# _timeout bounds the READ, _lock_timeout bounds the wait for the transport lock;
# neither reaches the addon, so neither belongs in a reject_unknown set.
BLENDER_TRANSPORT_KWARGS = {"_timeout", "_lock_timeout"}


class Problem:
    __slots__ = ("check", "message")

    def __init__(self, check: str, message: str):
        self.check = check
        self.message = message

    def __str__(self) -> str:
        return "[%s] %s" % (self.check, self.message)


# ---------------------------------------------------------------------------
# Static evaluation of the addon's accepted-key sets
# ---------------------------------------------------------------------------

class UnresolvedError(Exception):
    """A key set or op name that cannot be read statically. Always a failure."""


def _module_constants(tree: ast.Module) -> dict:
    """Module-level NAME = <expr> bindings, kept as AST NODES, not values.

    Nodes rather than literal_eval results on purpose: _EXPORT_OVERRIDES maps
    key -> (arg, type) and those tuples hold bare `str` / `bool` names, which
    literal_eval refuses. Only the KEYS matter to a param check, and the keys are
    readable straight off the Dict node.
    """
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def _const_str(node: ast.AST, where: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise UnresolvedError("%s contains a non-literal-string key (%s)"
                          % (where, ast.dump(node)[:120]))


def _eval_keyset(node: ast.AST, constants: dict, where: str) -> set:
    """Resolve an accepted-key expression to a set of strings.

    Handles exactly the shapes the addon actually uses, and refuses the rest
    rather than guessing:
        {"a", "b"}                  set literal
        {"a": ...}                  dict literal (its KEYS)
        ["a", "b"] / ("a", "b")     list/tuple literal
        _BEVEL_KEYS                 module-level name bound to any of the above
        set(_EXPORT_OVERRIDES)      set() over one of the above
        A | B                       union of any two of the above
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return (_eval_keyset(node.left, constants, where)
                | _eval_keyset(node.right, constants, where))

    if isinstance(node, ast.Name):
        if node.id not in constants:
            raise UnresolvedError(
                "%s references %s, which is not a module-level assignment this checker can "
                "find. Either make it one, or teach _eval_keyset about it - it must not "
                "be skipped." % (where, node.id))
        return _eval_keyset(constants[node.id], constants, "%s -> %s" % (where, node.id))

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set":
        if len(node.args) != 1 or node.keywords:
            raise UnresolvedError("%s calls set() in a form this checker cannot read" % where)
        return _eval_keyset(node.args[0], constants, where)

    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):
            raise UnresolvedError("%s dict-unpacks (**) into its key set" % where)
        return {_const_str(k, where) for k in node.keys}

    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return {_const_str(e, where) for e in node.elts}

    raise UnresolvedError(
        "%s builds its accepted-key set from an expression this checker cannot read "
        "statically (%s). Unresolvable == FAIL: fix the expression or the checker, do "
        "not let the check silently not run." % (where, ast.dump(node)[:160]))


def _find_reject_unknown(fn: ast.FunctionDef, constants: dict, fname: str) -> set:
    """The accepted-key set of the reject_unknown() call in this op function."""
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "reject_unknown"]
    if not calls:
        raise UnresolvedError(
            "%s.%s has no reject_unknown() call, so it accepts anything and no param "
            "check is possible. Every op must declare its keys." % (fname, fn.name))
    if len(calls) > 1:
        raise UnresolvedError("%s.%s calls reject_unknown %d times; this checker reads one"
                              % (fname, fn.name, len(calls)))
    call = calls[0]
    if len(call.args) < 2:
        raise UnresolvedError("%s.%s calls reject_unknown with %d positional args, expected 3"
                              % (fname, fn.name, len(call.args)))
    return _eval_keyset(call.args[1], constants, "%s.%s reject_unknown" % (fname, fn.name))


def load_addon_ops(problems: list) -> dict:
    """{op_name: {"accepts": set(), "source": "ops_mesh.py:op_bevel_edges"}}"""
    table = {}
    for fname in ADDON_OP_MODULES:
        path = os.path.join(ADDON_DIR, fname)
        if not os.path.isfile(path):
            problems.append(Problem("op-parity", "addon module not found: %s" % path))
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        constants = _module_constants(tree)
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

        ops_assign = None
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "OPS"):
                ops_assign = node.value
        if not isinstance(ops_assign, ast.Dict):
            problems.append(Problem(
                "op-parity", "%s has no module-level `OPS = {...}` dict literal" % fname))
            continue

        for key_node, value_node in zip(ops_assign.keys, ops_assign.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                problems.append(Problem(
                    "op-parity", "%s OPS has a non-literal key; op names must be literals" % fname))
                continue
            op = key_node.value
            if not isinstance(value_node, ast.Name):
                problems.append(Problem(
                    "op-parity", "%s OPS['%s'] is not a plain function name" % (fname, op)))
                continue
            handler = functions.get(value_node.id)
            if handler is None:
                problems.append(Problem(
                    "op-parity", "%s OPS['%s'] -> %s(), which is not defined in that module"
                                 % (fname, op, value_node.id)))
                continue
            if op in table:
                problems.append(Problem(
                    "op-parity", "op '%s' is registered twice (%s and %s) - one silently "
                                 "shadows the other when the tables are merged"
                                 % (op, table[op]["source"], fname)))
            try:
                accepts = _find_reject_unknown(handler, constants, fname)
            except UnresolvedError as exc:
                problems.append(Problem("param-parity", str(exc)))
                accepts = None
            table[op] = {"accepts": accepts, "source": "%s:%s" % (fname, value_node.id)}
    return table


# ---------------------------------------------------------------------------
# Static extraction of the MCP server's call sites
# ---------------------------------------------------------------------------

def load_mcp_calls(problems: list):
    """([(op, {kwarg names}, lineno, enclosing_def)], {_post literals})"""
    tree = ast.parse(open(MCP_SERVER, encoding="utf-8").read(), MCP_SERVER)

    # map every node to its enclosing function, for readable messages
    enclosing = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(fn):
                enclosing.setdefault(child, fn.name)

    blender_calls = []
    post_endpoints = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ("_blender", "_post"):
            continue
        where = enclosing.get(node, "<module>")
        if not node.args or not isinstance(node.args[0], ast.Constant) \
                or not isinstance(node.args[0].value, str):
            problems.append(Problem(
                "op-parity",
                "%s:%d in %s() calls %s with a non-literal endpoint name. It cannot be "
                "checked, and an unchecked call site is how the three missing ops got in."
                % (os.path.basename(MCP_SERVER), node.lineno, where, node.func.id)))
            continue
        name = node.args[0].value
        if node.func.id == "_post":
            post_endpoints.add(name)
            continue

        kwargs = set()
        for kw in node.keywords:
            if kw.arg is None:
                problems.append(Problem(
                    "param-parity",
                    "%s:%d in %s() splats **kwargs into _blender(\"%s\"). The keys cannot be "
                    "read statically, so the param check cannot run - pass them explicitly."
                    % (os.path.basename(MCP_SERVER), node.lineno, where, name)))
                kwargs = None
                break
            kwargs.add(kw.arg)
        if kwargs is None:
            continue
        blender_calls.append((name, kwargs - BLENDER_TRANSPORT_KWARGS, node.lineno, where))

    return blender_calls, post_endpoints


def load_ue_binds(problems: list):
    """The MIF_BIND name set, or None if the source is not there.

    None, not an empty set. An empty set would make check_ue_parity report every
    single _post endpoint as an orphan - 220-odd lines of confident nonsense
    produced by a check that could not find its input. It fails instead.
    """
    if not os.path.isfile(UE_BIND_FILE):
        problems.append(Problem(
            "ue-parity",
            "MIF_BIND source not found at %s, so the UE half of the parity check CANNOT "
            "RUN. Not skipped - failed. Run this from inside the plugin tree."
            % UE_BIND_FILE))
        return None
    # Skip the #define line: `#define MIF_BIND(Name) ...` matches the same regex
    # and would otherwise register a phantom endpoint called "Name". This is the
    # documented off-by-one in docs/00_ARCHITECTURE.md, handled instead of noted.
    raw = open(UE_BIND_FILE, encoding="utf-8", errors="replace").read()

    # NO BIND MAY SIT INSIDE A #if, AND TWO DOCUMENTED CLAIMS REST ON THAT.
    #
    # Measured 2026-09-03: zero of the 459 MIF_BINDs are inside any preprocessor conditional, so the
    # dispatch table is identical on every engine and in every project configuration. Two things this
    # repo asserts elsewhere are true only while that holds:
    #
    #   refresh_endpoints_snapshot.py says a DDS2 editor and the disposable probe "work equally
    #   well" for regenerating the snapshot. A bind behind MIF_WITH_NIAGARA would make the bare probe
    #   report FEWER endpoints, and refreshing from it would silently SHRINK the recorded universe
    #   that every coverage judgement is computed against.
    #
    #   the same file calls the MIF_DECL vs MIF_BIND distinction "theoretical". A conditional bind is
    #   exactly what would make it real, and nothing would say so.
    #
    # Neither breaks loudly. Both go quietly wrong in the direction of reporting less work than
    # exists, which is why this is a check rather than a comment.
    stack, conditional = [], []
    for i, ln in enumerate(raw.split("\n"), 1):
        st = ln.strip()
        if re.match(r"#\s*if", st):
            stack.append(st[:60])
        elif re.match(r"#\s*endif", st) and stack:
            stack.pop()
        if stack and "MIF_BIND(" in ln and not st.startswith(("#define", "#undef")):
            m = re.search(r"\bMIF_BIND\(([A-Za-z0-9_]+)\)", ln)
            if m:
                conditional.append("%s (line %d, inside %s)" % (m.group(1), i, stack[-1]))
    if conditional:
        problems.append(Problem(
            "ue-parity",
            "%d MIF_BIND(s) are inside a preprocessor conditional: %s. The dispatch table is no "
            "longer identical across engines and project configurations, which silently breaks two "
            "documented claims - that the disposable probe can regenerate endpoints_current.json as "
            "well as a DDS2 editor, and that the MIF_DECL/MIF_BIND distinction is theoretical. "
            "Either make the bind unconditional or update refresh_endpoints_snapshot.py's contract."
            % (len(conditional), ", ".join(conditional[:4]))))

    # Skip the #define line: `#define MIF_BIND(Name) ...` matches the same regex
    # and would otherwise register a phantom endpoint called "Name". This is the
    # documented off-by-one in docs/00_ARCHITECTURE.md, handled instead of noted.
    lines = [ln for ln in raw.split("\n")
             if not ln.lstrip().startswith(("#define", "#undef"))]
    return set(re.findall(r"\bMIF_BIND\(([A-Za-z0-9_]+)\)", "\n".join(lines)))


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def check_op_parity(addon_ops: dict, blender_calls, problems: list):
    called = {op for op, _, _, _ in blender_calls}
    registered = set(addon_ops)

    for op in sorted(called - registered):
        sites = ["%s():%d" % (where, line) for name, _, line, where in blender_calls if name == op]
        problems.append(Problem(
            "op-parity",
            "server.py calls _blender(\"%s\") but no addon OPS table registers it. The addon "
            "answers 'unknown endpoint' and the tool is dead. Call sites: %s"
            % (op, ", ".join(sites))))

    for op in sorted(registered - called - set(BLENDER_TOOLLESS_EXEMPTIONS)):
        problems.append(Problem(
            "op-parity",
            "addon op '%s' (%s) is never called by server.py - it is unreachable from MCP. "
            "Add a tool for it, drop it, or add it to BLENDER_TOOLLESS_EXEMPTIONS with a "
            "reason." % (op, addon_ops[op]["source"])))

    for op in sorted(set(BLENDER_TOOLLESS_EXEMPTIONS) - registered):
        problems.append(Problem(
            "op-parity",
            "BLENDER_TOOLLESS_EXEMPTIONS lists '%s', which no addon OPS table registers. A "
            "stale exemption hides real drift - remove it." % op))

    for op in sorted(set(BLENDER_TOOLLESS_EXEMPTIONS) & called):
        problems.append(Problem(
            "op-parity",
            "'%s' is in BLENDER_TOOLLESS_EXEMPTIONS but server.py now calls it. Remove the "
            "exemption so the param check covers it." % op))


def check_param_parity(addon_ops: dict, blender_calls, problems: list):
    for op, kwargs, line, where in blender_calls:
        entry = addon_ops.get(op)
        if entry is None:
            continue  # already reported by check_op_parity
        accepts = entry["accepts"]
        if accepts is None:
            continue  # already reported as unresolvable
        unknown = sorted(kwargs - accepts)
        if unknown:
            problems.append(Problem(
                "param-parity",
                "server.py:%d in %s() sends %s to '%s', which reject_unknown refuses. "
                "Accepted: %s" % (line, where, ", ".join(unknown), op,
                                  ", ".join(sorted(accepts)))))


def check_ue_parity(binds, post_endpoints: set, problems: list):
    if binds is None:
        return  # load_ue_binds already failed the run; do not invent findings
    external = {e for e in post_endpoints if e.startswith(UE_EXTERNAL_PREFIXES)}
    toolless = binds - post_endpoints - UE_TOOLLESS_EXEMPTIONS
    for name in sorted(toolless):
        problems.append(Problem(
            "ue-parity",
            "endpoint '%s' has a MIF_BIND but no _post() call site - HTTP-reachable, "
            "MCP-invisible. Add the @mcp.tool wrapper, or add it to "
            "UE_TOOLLESS_EXEMPTIONS with a reason." % name))
    orphan = post_endpoints - binds - external
    for name in sorted(orphan):
        problems.append(Problem(
            "ue-parity",
            "server.py calls _post(\"%s\") but no MIF_BIND registers it. The bridge answers "
            "404 / unknown endpoint." % name))
    stale = UE_TOOLLESS_EXEMPTIONS - binds
    for name in sorted(stale):
        problems.append(Problem(
            "ue-parity",
            "UE_TOOLLESS_EXEMPTIONS lists '%s', which no longer has a MIF_BIND. A stale "
            "exemption hides real drift - remove it." % name))
    # The OTHER way an exemption goes stale: the endpoint still exists AND has since
    # been given a wrapper, so the exemption is not describing reality any more. It
    # is worse than useless - it would SUPPRESS the ue-parity error if someone later
    # deleted that wrapper. The Blender half has always checked this; the UE half did
    # not, and on 2026-08-15 all five UE exemptions turned out to have been wrapped
    # back on 2026-08-12, silently, with docs/13 already saying so.
    covered = UE_TOOLLESS_EXEMPTIONS & post_endpoints
    for name in sorted(covered):
        problems.append(Problem(
            "ue-parity",
            "UE_TOOLLESS_EXEMPTIONS lists '%s', but server.py now calls _post(\"%s\"). "
            "The exemption is stale and would mask the wrapper being deleted - remove "
            "it from the list." % (name, name)))


def check_working_tree_eol():
    """Tracked text files sitting as LF on disk when .gitattributes says CRLF.

    ADVISORY, and the reason it has to be a detector rather than a fix is the interesting part.
    "93 files are LF in the working tree" sat on the open list for days as though it were a commit
    waiting to happen. IT IS NOT ONE. .gitattributes already declares `* text=auto eol=crlf` plus a
    per-extension list, core.autocrlf is true, and git stores LF in the INDEX by design - `i/lf` is
    correct and normal, not drift. So rewriting the bytes produces NO DIFF and nothing to commit,
    and hunting for the commit is time spent looking for something that cannot exist.

    What does happen is that a file WRITTEN by something other than a git checkout - an editor, a
    script, an agent's file tool - keeps whatever endings it was written with until the next
    checkout. That is a local working-tree state, it recurs whenever such a tool runs, and no
    committed file can prevent it. The only durable thing is to notice, which is this.

    The real number was 86, not 93, because some had been fixed by hand and the note went stale -
    which is its own argument for counting rather than quoting.
    """
    import subprocess as _sp2
    out = []
    try:
        r = _sp2.run(["git", "ls-files", "--eol"], capture_output=True, text=True,
                     cwd=ROOT, timeout=60)
    except Exception as e:
        return ["could not run git ls-files --eol (%s)" % e]
    bad = []
    for line in (r.stdout or "").splitlines():
        # "i/lf    w/lf    attr/text eol=crlf   path"
        if "\tw/lf" in line.replace("    ", "\t") or " w/lf" in line:
            if "eol=crlf" in line:
                bad.append(line.rsplit("\t", 1)[-1].strip() or line.split()[-1])
    if bad:
        out.append("%d tracked file(s) are LF on disk while .gitattributes says eol=crlf: %s%s. "
                   "This is a LOCAL working-tree state with NOTHING to commit - the index is LF by "
                   "design. Refresh them in place, or re-checkout."
                   % (len(bad), ", ".join(sorted(bad)[:4]),
                      "" if len(bad) <= 4 else " and %d more" % (len(bad) - 4)))
    return out


def check_hook_drift():
    """The deployed Stop hooks vs the copies in this repo.

    Andre's standing rule is "don't edit a mirror/copy file - edit the source, then sync". These hooks
    break it structurally: the version that RUNS lives in ~/.claude/hooks and the version under review
    lives here, and nothing tied them together.

    That cost something real on 2026-08-27. Andre asked for the backlog ordering to put his UI work
    ahead of MifBlender; I edited the repo copy, reported it done, and the very next hook message still
    listed MifBlender first - because the live file had never changed. Worse, the live file had a
    token-budget rework the repo copy did not, so a careless copy in either direction would have
    silently destroyed work.

    Reported, never auto-synced. Which copy is right depends on who edited what, and a check that
    guesses is a check that eventually overwrites the wrong one."""
    # Imported locally: this file does not use either at module scope, and adding a top-level import
    # for one function is how an unrelated diff shows up in a review.
    import io as _io
    import os
    live_dir = os.path.join(os.path.expanduser("~"), ".claude", "hooks")
    if not os.path.isdir(live_dir):
        return []
    problems = []
    for name in sorted(os.listdir(live_dir)):
        if not name.endswith(".js"):
            continue
        live = os.path.join(live_dir, name)
        repo = os.path.join(HERE, name)
        if not os.path.isfile(repo):
            problems.append("hook %s is DEPLOYED but has no copy in tools/ - it exists only on this "
                            "machine and is not version-controlled" % name)
            continue
        a = _io.open(live, "rb").read()
        b = _io.open(repo, "rb").read()
        if a != b:
            problems.append(
                "hook %s DIFFERS between ~/.claude/hooks (%d bytes, the one that RUNS) and tools/ "
                "(%d bytes, the one under review). Editing the repo copy changes nothing at runtime."
                % (name, len(a), len(b)))
    return problems


def check_plugin_declaration_drift():
    """Every plugin Build.cs links modules from must ALSO be declared in MifBridge.uplugin.

    These are two files that must agree, and until 2026-08-27 nothing checked that they did. Adding
    PCG to Build.cs alone stopped the editor loading entirely:

        Plugin 'MifKismetReconstructor' failed to load because module 'MifKismetReconstructor'
        could not be loaded.

    The named module is NOT the problem - it is downstream. MifBridge linked against a plugin the
    project had never enabled, the module chain failed, and the error surfaced on whatever came next
    in it. That is docs/06 issue 17, which I had written up myself and then walked into.

    The fix issue 17 records is to declare the plugin Optional+Enabled in the .uplugin so UBT enables
    it transitively. Thirteen were. The fourteenth was not, and the only thing standing between that
    and a dead editor was memory.

    Reported, not auto-fixed. Adding a plugin reference is a real decision about what MifBridge
    depends on, and a checker that edits a .uplugin on its own is one bad match away from declaring a
    dependency nobody wanted."""
    import io as _io
    import json as _json
    import os
    import re as _re

    build_cs = os.path.join(HERE, "..", "Source", "MifBridge", "MifBridge.Build.cs")
    uplugin = os.path.join(HERE, "..", "MifBridge.uplugin")
    if not (os.path.isfile(build_cs) and os.path.isfile(uplugin)):
        return []

    try:
        cs = _io.open(build_cs, encoding="utf-8", errors="replace").read()
        up = _json.loads(_io.open(uplugin, encoding="utf-8-sig").read())
    except Exception as exc:
        return ["could not read Build.cs or MifBridge.uplugin: %s" % exc]

    # AddPluginModules("MIF_WITH_X", "PluginName", ...) - the second argument is the descriptor name.
    linked = set(_re.findall(r'AddPluginModules\(\s*"[A-Z_0-9]+"\s*,\s*"([A-Za-z0-9_]+)"', cs))
    declared = set(p.get("Name") for p in (up.get("Plugins") or []))

    missing = sorted(linked - declared)
    return ["plugin %r has modules linked in Build.cs but is NOT declared in MifBridge.uplugin - "
            "the module will link and fail AT LOAD, and the error will name a different plugin "
            "(docs/06 issue 17 and 22). Add it as Optional:true, Enabled:true." % name
            for name in missing]


def check_tool_references():
    """Docs and tools that name a `tools/<x>.py` which is not on disk.

    A renamed tool leaves its old name behind in every doc that mentioned it, and the rename looks
    complete because the code still runs. audit_confirm_gates became audit_promise_flags on
    2026-08-31 and had to be chased through make_release and the runbook by hand; this is what makes
    the next one cheap.

    ADVISORY, like the idle-plugin check below it: a doc may legitimately name a tool that is
    planned, or quote an old path while explaining a rename. Reading it is the point.
    """
    import glob as _glob
    import io as _io
    import os as _os
    import re as _re

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    seen = {}
    files = (_glob.glob(_os.path.join(root, "docs", "**", "*.md"), recursive=True)
             + _glob.glob(_os.path.join(root, "tools", "*.py"))
             + [_os.path.join(root, "README.md")])
    for f in files:
        try:
            src = _io.open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in _re.finditer(r"tools/([A-Za-z0-9_\-]+\.py)", src):
            seen.setdefault(m.group(1), set()).add(_os.path.relpath(f, root).replace("\\", "/"))
    out = []
    for name in sorted(seen):
        if _os.path.isfile(_os.path.join(root, "tools", name)):
            continue
        out.append("tools/%s is named by %s and does not exist - a rename that was not chased, or a "
                   "tool that never landed. %d file(s) reference it."
                   % (name, ", ".join(sorted(seen[name]))[:90], len(seen[name])))
    return out


def check_linked_but_unused_plugins():
    """A plugin dependency that no source file uses: build cost, load risk, zero capability.

    THE OPPOSITE DIRECTION FROM THE CHECK ABOVE. That one catches a plugin linked in Build.cs and
    missing from the .uplugin, which stops the editor loading. This one catches a plugin that is
    correctly declared, correctly linked, and then never referenced by a single endpoint.

    That is not hypothetical. MifBridgeWater.cpp opens by saying so about itself:

        "The Water plugin has been LINKED since the breadth pass (MIF_WITH_WATER in Build.cs) and
         nothing has ever used it - the dependency was added and the endpoints were never written,
         which is the worst of both: build cost, no capability."

    Water is built now. NINE more were in exactly that state when this check was written, and there
    was nothing anywhere that would have said so.

    Every one is a real cost: a module to compile and link, a plugin the host project must have
    enabled, and one more way for Build.cs and the .uplugin to drift apart later (issues 17 and 22,
    both of which took the editor down).

    ADVISORY, and it does not fail the run. Deleting a dependency and building its endpoints are both
    real decisions, and neither is a checker's to make - the point is that the choice should be
    deliberate rather than forgotten.
    """
    import io as _io
    import os
    import re as _re
    import harvest_param_table as _H          # the one comment/string scrubber

    build_cs = os.path.join(HERE, "..", "Source", "MifBridge", "MifBridge.Build.cs")
    private = os.path.join(HERE, "..", "Source", "MifBridge", "Private")
    if not (os.path.isfile(build_cs) and os.path.isdir(private)):
        return []
    try:
        cs = _io.open(build_cs, encoding="utf-8", errors="replace").read()
    except Exception as exc:
        return ["could not read Build.cs: %s" % exc]

    guards = _re.findall(r'AddPluginModules\(\s*"([A-Z_0-9]+)"\s*,\s*"([A-Za-z0-9_]+)"', cs)
    used = set()
    for fn in os.listdir(private):
        if not fn.endswith((".cpp", ".h")):
            continue
        text = _io.open(os.path.join(private, fn), encoding="utf-8", errors="replace").read()
        # USED MEANS COMPILED, NOT MENTIONED. This was a bare `macro in text`, and a #if guard is
        # exactly the kind of thing files explain in prose - so writing ABOUT a guard marked it used
        # and silenced the advisory.
        #
        # The clearest case is self-refuting. MifBridgeMetasound.cpp:42 reads "...it is therefore NOT
        # the reason to keep MIF_WITH_METASOUND linked. parity_check still reports that dependency as
        # idle, correctly." It did not: that sentence is what stopped it. A comment asserting the
        # tool's behaviour changed the tool's behaviour, and read as confirmation while doing it.
        # MIF_WITH_LIVELINK was hidden the same way, by the two comments explaining its absence.
        code = _H.blank_comments_and_strings(text)
        for macro, _plugin in guards:
            if macro in code:
                used.add(macro)
    idle = sorted(plugin for macro, plugin in guards if macro not in used)
    if not idle:
        return []
    return ["%d plugin dependency(ies) are linked and NO source file COMPILES against their "
            "MIF_WITH_ guard: %s. Build cost and load risk - the state MifBridgeWater.cpp describes "
            "at the top of itself. Build endpoints for them or drop the dependency; either is fine, "
            "forgetting is not.\n"
            "    Read the file before dropping one: an unused GUARD is not always an unused "
            "CAPABILITY. MifBridgeLiveLink.cpp has no MIF_WITH_LIVELINK because every type it "
            "touches lives in LiveLinkInterface, an always-present runtime module, and the part the "
            "PLUGIN supplies is checked at runtime through IModularFeatures instead. That file works "
            "on an engine without the plugin; the dependency may still be droppable, but not for the "
            "reason this line would suggest."
            % (len(idle), ", ".join(idle))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true",
                        help="print the resolved op tables as well as the verdict")
    args = parser.parse_args()

    problems: list = []
    addon_ops = load_addon_ops(problems)
    blender_calls, post_endpoints = load_mcp_calls(problems)
    binds = load_ue_binds(problems)

    check_op_parity(addon_ops, blender_calls, problems)
    check_param_parity(addon_ops, blender_calls, problems)
    check_ue_parity(binds, post_endpoints, problems)

    if args.verbose:
        print("addon ops (%d):" % len(addon_ops))
        for op in sorted(addon_ops):
            entry = addon_ops[op]
            keys = "UNRESOLVED" if entry["accepts"] is None else "%d keys" % len(entry["accepts"])
            print("  %-16s %-34s %s" % (op, entry["source"], keys))
        print("_blender call sites (%d):" % len(blender_calls))
        for op, kwargs, line, where in sorted(blender_calls, key=lambda c: c[2]):
            print("  %-16s %s():%d  %s" % (op, where, line, ", ".join(sorted(kwargs)) or "-"))
        print("aliases: %s" % KNOWN_OP_ALIASES)
        print("_post endpoints: %d   MIF_BIND: %d" % (len(post_endpoints), len(binds)))

    # Printed on EVERY run, pass or fail. An exemption nobody sees is a silent
    # allowlist, which is the thing this script exists to replace.
    # The spec is a contract too: an item that is done but still reads as open keeps being offered
    # as the next thing to do. Four of those on 2026-08-27 alone.
    try:
        import subprocess as _sp
        import sys as _sys
        _r = _sp.run([_sys.executable, os.path.join(HERE, "spec_check.py")],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     stdin=_sp.DEVNULL, timeout=60)
        for _l in (_r.stdout or "").splitlines():
            if _l.strip() and not _l.startswith("spec OK"):
                print("SPEC: " + _l)
    except Exception as _e:
        print("SPEC: could not run spec_check.py (%s)" % _e)

    for _p in check_plugin_declaration_drift():
        print("PLUGIN DRIFT: " + _p)
    for _p in check_tool_references():
        print("TOOL REF: " + _p)
    for _p in check_linked_but_unused_plugins():
        print("PLUGIN IDLE: " + _p)
    for _p in check_hook_drift():
        print("HOOK DRIFT: " + _p)
    for _p in check_working_tree_eol():
        print("EOL DRIFT: " + _p)

    print("exemptions in force:")
    for op, reason in sorted(BLENDER_TOOLLESS_EXEMPTIONS.items()):
        print("  blender op %-14s %s" % (op, reason))
    print("  UE endpoints with no MCP tool: %s" % ", ".join(sorted(UE_TOOLLESS_EXEMPTIONS)))

    if problems:
        by_check = {}
        for problem in problems:
            by_check.setdefault(problem.check, []).append(problem)
        for check in sorted(by_check):
            print("\n%s: %d problem(s)" % (check.upper(), len(by_check[check])))
            for problem in by_check[check]:
                print("  - %s" % problem.message)
        print("\nFAIL: %d problem(s). These are contract breaks between the MCP server and a "
              "backend; a caller hits them as 'unknown endpoint' or 'unknown param(s)'."
              % len(problems))
        return 1

    print("OK  %d addon ops, %d _blender call sites, %d _post endpoints, %d MIF_BIND. "
          "No drift." % (len(addon_ops), len(blender_calls), len(post_endpoints), len(binds or ())))

    # CHECK 4: can the MCP tools SEND every parameter the UE endpoints ACCEPT?
    # The three checks above compare endpoint NAMES on the UE side and parameters only for the
    # Blender addon, so an endpoint growing a parameter the tool never exposes was invisible - which
    # is how add_bind_dispatcher's targetClass stayed unreachable long enough for a user to report
    # external dispatcher binding as a missing FEATURE. It was not missing; it was unwired.
    # Ratcheted against a baseline, because the existing backlog is mostly alias spellings and
    # failing on all of it would just get the check disabled. See tools/param_reach.py.
    try:
        import param_reach
        print()
        if param_reach.main() != 0:
            return 1
    except Exception as exc:                                  # never let this break the real checks
        print("\n(param reach check unavailable: %s)" % exc)

    # CHECK 5: the OTHER direction - does any MCP tool SEND a parameter the endpoint would REJECT?
    # Check 4 finds capability that cannot be reached, which is a loss. This finds a call that cannot
    # SUCCEED: RejectUnknownParams refuses an unrecognised key outright, so a tool sending one fails
    # 100% of the time with an error naming a key the caller never typed. Nothing else catches it -
    # check 4 is blind to it by construction, and the python suites call the bridge directly with
    # their own payloads rather than through server.py, so no test exercises the tool signatures.
    # NOT ratcheted, unlike check 4: there is no legitimate backlog here. Every hit is either a live
    # bug or a parser limitation, and the parser reports "unknown" rather than "empty" when it cannot
    # read a macro-built accept-list, so it does not manufacture the second kind.
    try:
        import mcp_sends_unknown
        print()
        if mcp_sends_unknown.main() != 0:
            return 1
    except Exception as exc:
        print("\n(mcp-sends-unknown check unavailable: %s)" % exc)

    # CHECK 6: can each MCP tool be CALLED AT ALL?
    # Checks 1-5 all compare a tool against an endpoint - names, parameters accepted, parameters
    # sent. Every one of them passes a wrapper that raises NameError before it reaches the network.
    #
    # move_tree_widget did exactly that, in shipped code, for every call it ever received:
    #
    #     return _post(..., replaceRoot=replace_root)   # replace_root was never a parameter
    #
    # It passed check 1 (the name is in all three registries), passed check 4 (it NAMES replaceRoot,
    # which is why it read as correct), and no suite touched it because the suites drive endpoints
    # over HTTP rather than calling the Python wrappers. A user found it and filed issue #1.
    #
    # NOT ratcheted. There is no legitimate backlog of functions that cannot run.
    try:
        import mcp_static_check
        print()
        if mcp_static_check.main() != 0:
            return 1
    except SystemExit as exc:                             # main() calls sys.exit via argparse
        if exc.code:
            return 1
    except Exception as exc:
        print("")
        print("(mcp static check unavailable: %s)" % exc)

    # CHECK 7: is describe_endpoint's compiled table still DERIVED from the guards?
    # THIS CHECK ALREADY EXISTED AND ALREADY WORKED. harvest_param_table.py --check compares the
    # committed table against the RejectUnknownParams literals statically and fail-closed, and it
    # reports the drift in one line. What did not exist was anything that RAN it between adding an
    # endpoint and testing one: it was wired into make_release.check_param_table and nowhere else,
    # so it fired at PACKAGING. On 2026-08-31 seven new endpoints reached a live editor and four
    # green suites without it ever being consulted.
    #
    # The cost was not theoretical, and it was worse than a wrong number. test_node_spawns passed
    # 106 checks WITHOUT exercising add_make_set, and could not say so. T330 drives whatever the
    # live registry reports as taking only cosmetic parameters; describe_endpoint answered
    # acceptedParams:NONE for an endpoint with no row, so the filter skipped it in silence and the
    # suite went green having tested one thing FEWER than the day before. Regenerating took it
    # 106 -> 109. A stale table does not just misinform a caller - it quietly shrinks the test run.
    #
    # BLOCKING, like checks 4-6 and like the packaging gate. The remedy is one command plus a
    # rebuild, and an advisory here would be read exactly the way the packaging gate was read: at
    # packaging, weeks later, by which point the suites have already gone green without it.
    try:
        import subprocess as _sp2
        print()
        _r2 = _sp2.run([sys.executable, os.path.join(HERE, "harvest_param_table.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=_sp2.DEVNULL, timeout=300)
        if _r2.returncode == 0:
            print("OK  describe_endpoint's table still matches the RejectUnknownParams guards")
        else:
            for _l in ((_r2.stdout or "") + (_r2.stderr or "")).splitlines():
                if _l.strip():
                    print("DESCRIBE TABLE: " + _l)
            print("FAIL: the describe table is stale. Run tools/harvest_param_table.py and REBUILD. "
                  "Until you do, a new endpoint is invisible to describe_endpoint - and any suite "
                  "that picks its targets by ASKING will skip it without ever saying so.")
            return 1
    except Exception as exc:
        print("\n(describe table check unavailable: %s)" % exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
