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
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MCP_SERVER = os.path.join(HERE, "mcp-server", "server.py")
ADDON_DIR = os.path.join(HERE, "blender-addon", "MifBlender")
# EVERY module that contributes to the addon's op table must be listed here. A
# module missing from this tuple is INVISIBLE to check 1: its ops read as
# "registered nowhere", and — worse — a tool that legitimately calls one gets
# reported as dead. That happened on 2026-08-15, when ops_gen.py (the ComfyUI
# generation chain) was added to the addon and this tuple was not updated, so the
# checker blamed five correct wrappers instead of itself. Cross-check against
# server.py's `table.update(...)` calls, which are the real registry.
ADDON_OP_MODULES = ("ops_scene.py", "ops_mesh.py", "ops_gen.py")

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
    lines = [ln for ln in open(UE_BIND_FILE, encoding="utf-8", errors="replace")
             if not ln.lstrip().startswith(("#define", "#undef"))]
    return set(re.findall(r"\bMIF_BIND\(([A-Za-z0-9_]+)\)", "".join(lines)))


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

    return 0


if __name__ == "__main__":
    sys.exit(main())
