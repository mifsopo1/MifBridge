#!/usr/bin/env python3
"""Package MifBridge into a versioned, verifiable release zip.

    python make_release.py                  ->  dist/MifBridge-0.4.1.zip
    python make_release.py --out X          ->  X
    python make_release.py --check <zip>    ->  verify a zip against this tree

WHY THIS EXISTS, measured rather than assumed.

MifBridge is VENDORED into D:/RoguelikeDealerGame (Curfew) rather than linked, and on 2026-08-26 the
drift was measured: 284 endpoints here against 222 there, 11 whole source files missing on the far side,
unnoticed for weeks because nothing ever compared the two. Work was being lost in both directions. A
tagged, hashed artifact plus a manifest is the cheapest thing that would have caught it - a consumer can
answer "am I current?" without reading a single line of source.

WHAT SHIPS, and why the list is derived rather than written down.

The file list comes from `git ls-files`, not from a hand-maintained array. A hand-maintained list is a
second source of truth that drifts from the first one, which is the exact failure this whole script is
about. .gitignore already excludes Binaries/, Intermediate/, Saved/ and DerivedDataCache/, so anything
git tracks is by definition source rather than build output.

Two categories are then removed on top of that, because they are tracked but are not part of a
deployable plugin: `.github/`, which is this repo's CI rather than the consumer's, and run artifacts -
any `.log`, any `.bak*` backup, and the per-run results JSON. Those are matched by KIND, not by name.
Naming them individually was the first version of this and it leaked: listing the zip afterwards found
cooked_sweep_final.log, fuzz_final.log, fuzz_verify.log and docs/06_OPEN_ISSUES_FROM_USE.md.bak-predt
all being shipped.

THE MANIFEST is the point of the exercise. It records, for each release:
  * the plugin version from the .uplugin - ONE source of truth, read, never retyped;
  * the endpoint count, taken from MIF_BIND in the C++ - the same number parity_check treats as
    authoritative, so a consumer can compare against their own self_audit;
  * a SHA-256 over the shipped file contents, so "same version" can be distinguished from
    "same version, locally modified";
  * the engine compatibility statement.

No dependencies. Does not need Unreal, an editor, or a running bridge - it is plain zipfile and hashlib,
so it can run in CI or on a machine that has never opened this project.
"""

import argparse
import hashlib
import ast
import io
import json
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UPLUGIN = os.path.join(ROOT, "MifBridge.uplugin")
BIND_FILE = os.path.join(ROOT, "Source", "MifBridge", "Private", "MifBridgeCommon.cpp")

# Tracked by git, but NOT part of a deployable plugin. Evidence of a test run and this repo's own CI.
EXCLUDE_PREFIXES = (".github/",)
# Set from --fab. A list so the walker, which is module-level, can read it without
# threading a parameter through every caller.
FAB_MODE = [False]
# Patterns rather than a list of the files that happened to exist when this was written. The first
# version of this named suite_run*.log and *_night.log specifically, and listing the zip afterwards
# showed cooked_sweep_final.log, fuzz_final.log, fuzz_verify.log and a .bak-predt backup all shipping.
# Enumerating known offenders is the same brittle shape as a hand-maintained file list - the thing the
# `git ls-files` decision above exists to avoid - so these match by KIND instead.
EXCLUDE_PATTERNS = (
    re.compile(r"\.log$"),                       # any run log, wherever it lives
    re.compile(r"\.bak(-|\.|$)"),                 # editor/backup droppings, e.g. .bak-predt
    re.compile(r"(^|/)~\$"),                     # Office lock files
    re.compile(r"^tools/suite_results\.json$"),   # results of one particular run
    re.compile(r"^tools/endpoints_current\.(json|txt)$"),
)

# WHAT A BUYER NEVER RUNS. Applied ONLY under --fab, never to the default zip.
#
# Measured rather than guessed: these four kinds are 305 of the 510 files in the 0.9.0 package, 60%
# of it. The suites need this project's own fixtures and a live editor, so a buyer cannot run them
# at all; the dev scripts are release machinery and sweeps.
#
# THE AUDITS ARE THE ARGUABLE ONE. They are the credibility story, and unlike the suites they would
# genuinely run against the shipped source. They are dropped anyway because a buyer did not purchase
# a code review of their own copy, and 49 files nobody opens reads as a repo dump. That argument
# belongs in the listing text where it persuades, not in the payload where it confuses. Reversible
# by deleting one line here.
FAB_EXCLUDE_PATTERNS = (
    re.compile(r"^tools/test_[^/]+\.py$"),        # need this repo's fixtures and a live editor
    re.compile(r"^tools/audit_[^/]+"),            # static analysis of our source, not the buyer's
    re.compile(r"^tools/probe_[^/]+\.py$"),
    re.compile(r"^tools/mif_[^/]+\.py$"),
    re.compile(r"^skills/"),                      # this repo's own agent skills
)

# KEPT UNDER --fab NO MATTER WHAT, because a pattern above would otherwise catch them and the buyer
# would lose the two things that make the plugin usable. Listed explicitly: an exclusion that
# silently removed the MCP server would produce a package that installs and does nothing.
FAB_KEEP_PREFIXES = (
    "tools/mcp-server/",      # how an agent talks to any of this
    "tools/blender-addon/",   # the optional second backend
)


# DEV-ONLY DOCS, excluded by a MARKER IN THE FILE rather than by name.
#
# Andre: "git ignore the start here as i dev the bridge while other users install or jus tmake sure we
# dont add it to the releases".
#
# Not gitignored, deliberately. docs/18_START_HERE.md exists so a session with no memory of this work
# can pick it up cold - a file whose whole purpose is surviving a lost machine is the last thing to
# leave version control. It belongs in git and out of the zip, which are different questions.
#
# Marker rather than a filename, for the same reason the patterns above match by kind: a hand-kept
# list of internal files is one forgotten entry away from shipping a roadmap to a customer. Any file
# can now opt itself out, and the next one does not need this list touched.
DEV_ONLY_MARKER = "MIFBRIDGE-DEV-ONLY"


def is_dev_only(abs_path):
    """True if the file's first 4KB carries the marker.

    First 4KB only: this runs over every tracked file, and reading a whole repo to find a comment at
    the top of five of them is waste. Anything that fails to read is NOT dev-only - a binary that
    cannot be decoded is a shipping file, and failing the other way would silently drop assets from a
    release."""
    try:
        with io.open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            return DEV_ONLY_MARKER in f.read(4096)
    except Exception:
        return False

# Engine versions this plugin is known to build against. 5.3.2 is the cooked DDS2 SDK; 5.7 is Curfew.
#
# "built" MEANS A COMPILER SAID SO. Until 2026-08-26 the 5.7 row said "built" on the strength of
# reading both engines' headers and reasoning about what would compile. It had never been compiled.
# When someone finally did, six real defects fell out in an hour - including FHttpRequestHandler
# changing from a typedef'd TFunction to a TDelegate, which no amount of reading had caught because
# the two declarations look interchangeable until a compiler disagrees.
#
# So before changing a row to "built", run the probe:
#
#     python tools/make_engine_probe.py --engine <engine root> --out <scratch> --build
#
# and check the log for "Result: Failed" - Build.bat has been observed exiting 0 on a failed build.
# A row here is a claim someone will rely on. Reading the headers is not evidence for it.
# Stated as a claim about what has actually been built, not a guess about what might work.
ENGINE_MATRIX = [
    # STATUS IS FILLED IN AT PACKAGING TIME by gate_53(), not written here. It read
    # "built and tested" as a literal string for the whole life of this file, and on 2026-09-01
    # that string was FALSE for about an hour: 0.8.0 was packaged while the last real 5.3 build
    # predated two compile fixes, and one of them - a version-guarded alias whose right-hand side a
    # blanket rename had rewritten into `using X = X;` - broke 5.3 outright while 5.7 compiled
    # clean, because 5.7 never enters that arm.
    #
    # The 5.7 row has been gated on a recorded probe since 0.7.0 shipped broken. The PRIMARY engine
    # had no such gate at all, which is the wrong way round.
    {"engine": "5.3.2", "status": None, "notes": "cooked editor (DDS2 SDK) - the primary target"},
    {"engine": "5.7", "status": "built, not deployed",
     "notes": "COMPILED against stock 5.7.4 via tools/make_engine_probe.py, most recently 2026-08-27 "
              "at 330 endpoints. NOT currently running in any project: the Curfew deployment that "
              "reached 291 endpoints was reverted to its 2026-08-24 DLL and stood down, so 'built' "
              "here means a compiler agreed, NOT that anyone has used it. That distinction is the "
              "whole reason this row exists - it said 'built' for weeks on the strength of reading "
              "headers, and when someone finally compiled it six real defects fell out in an hour. "
              "create_editable_child refuses on any stock engine: it needs a DDS2 engine-FORK header. "
              "IK Rig is ported to the 5.6+ UStruct solver model. See docs/02_GOTCHAS.md section 14 "
              "for the six API splits, and docs/06 issues 17 and 22 for the plugin-enablement defect "
              "that affects consumers who do not enable the optional plugins."},
]


def plugin_version():
    """Read VersionName from the .uplugin. One source of truth - never retyped into this script."""
    with io.open(UPLUGIN, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return str(data.get("VersionName") or "0.0.0"), int(data.get("Version") or 0)


def endpoint_count():
    """Count MIF_BIND, which is what parity_check treats as authoritative for the UE surface."""
    try:
        with io.open(BIND_FILE, "r", encoding="utf-8", errors="replace") as fh:
            return len(set(re.findall(r"MIF_BIND\(([a-z_0-9]+)\)", fh.read())))
    except OSError:
        return 0


# THE README BADGE, AND WHY PACKAGING REFUSES OVER IT
#
# README.md:7 carries `<!-- MIFBRIDGE-VERSION-LINE -->` and, until 2026-08-30, NOTHING in the repo
# read or wrote it - a marker with no reader. The line under it had drifted to "320 endpoints /
# 353 MCP tools / 75 test suites" against a real 421 / 478 / 144. That is the first thing anyone
# sees, it was wrong by a hundred endpoints, and no check anywhere would ever have said so.
#
# This does NOT rewrite the README during packaging. A build step that quietly edits tracked files
# is how you get a commit you did not write. It REFUSES instead, and `--update-badge` is the
# explicit way to fix it - the same shape as the parity gate below: the tool tells you what is
# wrong and you decide.

def mcp_tool_count():
    """@mcp.tool DECORATORS in the MCP server - the number a user of the MCP layer actually sees.

    Anchored to the start of a line rather than counted as a substring. A plain count of "@mcp.tool"
    returns 479 because server.py:3170 mentions the decorator inside a COMMENT, and 478 is what
    mcp_static_check.py finds by parsing the AST. One apart, and it would have been easy to shrug
    at - but a badge whose whole purpose is being trustworthy cannot be off by one for a silly
    reason. Being wrong by a hundred, which is where this line was, starts with being wrong by one.
    """
    try:
        with io.open(os.path.join(HERE, "mcp-server", "server.py"), "r",
                     encoding="utf-8", errors="replace") as fh:
            return len(re.findall(r"(?m)^@mcp\.tool\(\)", fh.read()))
    except OSError:
        return 0


def suite_count():
    """test_*.py files under tools/. Counted, never typed - it was typed once and went 69 stale."""
    try:
        return len([n for n in os.listdir(HERE)
                    if n.startswith("test_") and n.endswith(".py")])
    except OSError:
        return 0


def blender_op_count():
    """Ops in the addon, counted from its OPS dicts rather than remembered.

    The badge advertised UE's endpoint count and said nothing about the Blender arm - half the
    tool - while the one prose line that DID mention it read "20 ops" long after it was 68. A
    generated number cannot go stale the way a typed one does, which is the entire reason the rest
    of this line is generated.
    """
    total = 0
    root = os.path.join(ROOT, "tools", "blender-addon", "MifBlender")
    if not os.path.isdir(root):
        return 0
    for fn in sorted(os.listdir(root)):
        if not (fn.startswith("ops_") and fn.endswith(".py")):
            continue
        try:
            tree = ast.parse(io.open(os.path.join(root, fn), encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(isinstance(t, ast.Name) and t.id == "OPS" for t in node.targets) \
                    and isinstance(node.value, ast.Dict):
                total += len(node.value.keys)
    return total


def badge_line():
    """The line the README SHOULD carry, built from the same sources the manifest uses."""
    version, _ = plugin_version()
    parts = [
        "`v%s`" % version,
        "\U0001f3ae **UE 5.3 + 5.7**",
        "\U0001f3a8 **Blender 3.6\u20135.0**",
        "\U0001f50c **%d UE endpoints**" % endpoint_count(),
        "\U0001f9f1 **%d Blender ops**" % blender_op_count(),
        "\U0001f9f0 **%d MCP tools**" % mcp_tool_count(),
        "\U0001f9ea **%d test suites**" % suite_count(),
    ]
    return " &nbsp;\u00b7&nbsp; ".join(parts)


VERSION_MARKER = "<!-- MIFBRIDGE-VERSION-LINE -->"


def check_badge(update=False):
    """(ok, message). With update=True, rewrite the line under the marker instead of reporting."""
    readme = os.path.join(ROOT, "README.md")
    try:
        with io.open(readme, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return False, "could not read README.md: %s" % exc

    lines = text.split("\n")
    idx = next((i for i, l in enumerate(lines) if VERSION_MARKER in l), -1)
    if idx < 0 or idx + 1 >= len(lines):
        return False, "README.md has no %s marker to anchor the badge to" % VERSION_MARKER

    want = badge_line()
    have = lines[idx + 1]
    # Reported as plain numbers rather than by echoing the line. The badge is full of emoji and a
    # Windows console is cp1252, so printing it raises UnicodeEncodeError and the tool dies while
    # doing nothing but reporting - which is how --update-badge failed the first time it was run.
    # The numbers are what you wanted to read anyway.
    summary = ("v%s, %d endpoints, %d MCP tools, %d test suites"
               % (plugin_version()[0], endpoint_count(), mcp_tool_count(), suite_count()))
    if have.strip() == want.strip():
        return True, "badge is current: %s" % summary
    if not update:
        # Only the bolded figures, for the same encoding reason - and because the diff you care
        # about is 320-vs-421, not the surrounding markdown.
        def figures(line):
            found = re.findall(r"\*\*([^*]*\d[^*]*)\*\*", line)
            return " / ".join(found) if found else line.strip()[:80]
        return False, ("the README badge is STALE and it is the first thing anyone sees.\n"
                       "  have: %s\n  want: %s\n"
                       "  Fix it with:  python tools/make_release.py --update-badge"
                       % (figures(have), figures(want)))
    lines[idx + 1] = want
    out = "\n".join(lines)
    with io.open(readme, "w", encoding="utf-8", newline="") as fh:
        fh.write(out)
    with io.open(readme, "rb") as fh:
        raw = fh.read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with io.open(readme, "wb") as fh:
        fh.write(raw)
    return True, "badge updated: %s" % summary


# THE 5.7 GATE, keyed to the CODE rather than the calendar.
#
# 0.7.0 shipped unable to compile on UE 5.7 in any project but this one. The README said "5.7
# verified 2026-08-27 at 330 of 421 endpoints" and that was TRUE - and useless, because both
# features that broke it (PhysicsAsset authoring, collections) were written afterwards. A dated
# verification says nothing about code added later, so the gate does not ask whether a probe
# happened or whether it was recent. It asks whether the probe is NEWER THAN THE SOURCE.
#
# Running the probe build here was considered and rejected: it needs the editor closed (Live Coding
# holds the toolchain), takes about a minute, and would make packaging fail for reasons unrelated to
# packaging. Recording the verdict and checking its age gives the same guarantee without the
# coupling - and catches the case that actually shipped, which is a real probe that no longer covers
# the tree.

PROBE_RESULT = os.path.join(HERE, "engine_probe_result.json")


def _git(*args):
    try:
        return subprocess.run(["git"] + list(args), capture_output=True, text=True,
                              cwd=ROOT, timeout=30).stdout.strip()
    except Exception:
        return ""


def _porcelain_paths(out):
    """Paths out of `git status --porcelain`, WITHOUT fixed-width slicing.

    ln[3:] is the obvious reading of the format - two status columns and a space - and it is wrong
    here for a reason that is easy to miss: _git() ends in .strip(), which eats the LEADING SPACE of
    an unstaged entry (" M Source/x.cpp"). The first line then starts at "M " and ln[3:] takes one
    character too many, so the gate printed "ource/MifBridge/Private/MifBridgeStreaming.cpp" and
    sent the reader hunting a file that does not exist. Only the FIRST line is affected, which is
    what made it survive: every later line keeps its leading space and reads correctly.

    Found 2026-09-03 by running the gate against a deliberately dirty tree and READING the output,
    not by reading the slice - which had looked right in isolation, and is right in isolation.
    """
    paths = []
    for ln in (out or "").splitlines():
        if not ln.strip():
            continue
        parts = ln.split(None, 1)
        # A rename reads "R  old -> new"; keeping the whole tail beats truncating either half.
        paths.append(parts[1] if len(parts) > 1 else parts[0])
    return paths


def check_param_table():
    """(ok, message) - is describe_endpoint's compiled table still what the guards say?

    A stale table is not cosmetic. self_audit derives paramTableCoverage from it, so shipping an old
    one makes the plugin under-report its own parameter guards for the life of the release - and the
    number it produces reads as a safety problem rather than as bookkeeping. Re-derived here rather
    than trusted, for the same reason the 5.7 gate re-derives the compile.
    """
    script = os.path.join(HERE, "harvest_param_table.py")
    if not os.path.isfile(script):
        return False, "tools/harvest_param_table.py is missing - cannot verify the describe table"
    r = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()
    if r.returncode == 0:
        return True, "describe_endpoint table matches the RejectUnknownParams guards"
    return False, ("the describe_endpoint table has DRIFTED from the guards in Source - run "
                   "tools/harvest_param_table.py and REBUILD, then package. (%s)"
                   % (tail[-1] if tail else "no output"))


def check_value_discovery():
    """(ok, message) - can a caller still FIND the values every named-object parameter demands?

    Gated at packaging for the same reason the param table is: this one is cheap here and expensive
    later. A parameter naming a bone, a socket or an edit layer with nowhere to enumerate the valid
    values ships as an endpoint a caller can only use by guessing - which is exactly what
    apply_spline_to_landscape's editLayer was until 2026-08-31, and it cost a whole suite.

    Only the STATIC half blocks. audit_value_discovery --check exits 1 for an unmapped parameter,
    which is deterministic here, and for a mapping whose reader really does not return its field -
    but only when a live editor answered. A packaging box with no editor must not fail for want of
    one, because "could not check" is not "is wrong".
    """
    script = os.path.join(HERE, "audit_value_discovery.py")
    if not os.path.isfile(script):
        return False, "tools/audit_value_discovery.py is missing - cannot verify value discovery"
    r = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
    if r.returncode == 0:
        return True, "every parameter naming an engine object has a discoverable source"
    tail = [l for l in (r.stdout or "").strip().splitlines() if l.startswith("BLOCKING")]
    return False, ("a parameter demands a value nothing enumerates - run "
                   "tools/audit_value_discovery.py and map it. (%s)"
                   % (tail[-1] if tail else "see its output"))


def check_static_audits():
    """(ok, message) - do the RATCHETED source audits still pass?

    These three are baseline-ratcheted: they print their whole known set every run and exit non-zero
    only for something NEW. That makes them safe to gate - a green tree stays green, and the only
    way to turn one red is to add a finding.

    Gated because of what happened on 2026-08-31. audit_loop_writes had been failing, with 19
    findings, for an unknown length of time. Nothing depended on it, so nothing went red, and the
    one real defect among the nineteen - modify_actor_layers reporting layerCreated:true per name inside
    its loop, so it never said WHICH layer an implicit creation had invented from a typo - sat in
    plain view in a check nobody had reason to run. A ratchet outside the gate is a ratchet with
    nothing on the other end of it.

    Deliberately NOT here: coverage_gaps and audit_suite_reach, which measure how much is TESTED
    rather than whether the source is wrong, and are expected to carry a standing backlog. Gating a
    check that is meant to be non-zero teaches people to pass --force, and a gate people route
    around protects nothing.
    """
    # (tool, argv). test_fuzz_detector is a SUITE, and the only one here, because it is the only
    # one that runs entirely offline - "no editor, no bridge", per its own docstring. It regression-
    # tests the fuzzer's detectors, EMPTY_INTERP among them since 2026-08-31. Gating the suite rather
    # than fuzz_endpoints --self-test keeps one home for those cases instead of two.
    failed, ran = [], []
    #
    # audit_vacuous_checks and audit_consequence_fields JOINED 2026-08-31, on Andre's call, and both
    # need --check: without it they REPORT and exit 0, so the gate would call a red tree green.
    # They fit the criterion above exactly - ratcheted, non-zero only for something NEW - and the
    # standing worry was that a gate firing on somebody's honest new assertion is a tax. The ratchet
    # is the answer to that, and the same day it earned its place: audit_vacuous_checks caught a
    # genuinely vacuous check in test_physics_asset T2906, written to REPLACE a vacuous one, where
    # all([]) over an empty slice would have passed without examining anything.
    for tool, args in (("audit_loop_writes.py", []), ("audit_postconditions.py", []),
                       ("audit_modals.py", []), ("test_fuzz_detector.py", []),
                       ("audit_promise_flags.py", []), ("audit_suite_payloads.py", []),
                       ("audit_vacuous_checks.py", ["--check"]),
                       ("audit_consequence_fields.py", ["--check"]),
                       # The SECOND thing here that runs entirely offline. layout_graph computes
                       # node positions from what list_nodes returns, so its algorithm is pure and
                       # testable with no editor, no bridge and no session - exec ordering, the
                       # data-node case, cycle termination, column overlap and comment-box overlap.
                       # Gated because an unrun layout rots silently: nothing else would notice it
                       # started stacking nodes until somebody opened a graph and saw the mess.
                       ("layout_graph.py", ["--self-test"]),
                       # JOINED 2026-09-03, the same day it was written, and deliberately so. It
                       # exists because mifaudit.is_scratch_fixture's own comment said the thing
                       # keeping its hole shut was "a naming convention, not a check" - and when
                       # that convention was finally measured it had already been broken for some
                       # time by audit_read_purity, which leaked two editor-world actors no guard
                       # could see. Ungated it would be the exact failure written up in
                       # 02_GOTCHAS the same morning: a plant-proven detector reporting to nobody.
                       # Sits at zero findings across 32 call sites, and a deliberate exception has
                       # a `# SPAWN-LABEL-OK: <reason>` escape hatch, so this is a ratchet and not
                       # a tax on somebody's honest new spawn.
                       ("audit_spawn_labels.py", []),
                       # JOINED 2026-09-03, and this one is the whole reason that day's gotcha got
                       # written. audit_cross_endpoint_claims had `return 0` as its only exit and no
                       # gate, so for three days it printed a correct list of twelve never-compared
                       # equivalence claims to nobody. NEEDS --check: without it the tool is
                       # report-style and always exits 0, so gating the bare form would call a red
                       # tree green - the same trap audit_vacuous_checks and audit_consequence_fields
                       # carry a --check for. Differential against a baseline of two entries, both
                       # blocked on the machine and both carrying a written reason.
                       ("audit_cross_endpoint_claims.py", ["--check"]),
                       # THREE MORE JOINED 2026-09-03, after asking which detectors CAN fail and are
                       # not in this tuple. All three are static, all three run in under two seconds
                       # together, and all three sit at zero - so each is a ratchet at zero rather
                       # than a tax on anybody's new work.
                       #
                       # audit_mcp_default_sends is the one that matters most and was the most
                       # surprising omission: it found FOUR uncallable MCP tools that same day
                       # (map_legacy_input in both modes, set_struct_member, set_enum_value's
                       # bitflags mode, and set_collision, which applied a change and then reported
                       # "NOTHING was changed"), on top of the two shipped bugs found that morning.
                       # A tool that has caught six real defects in one day belongs in the gate that
                       # decides whether a release goes out.
                       ("audit_mcp_default_sends.py", []),
                       # Catches a NameError in a suite before the suite is run - which matters most
                       # for the suites that need a live editor, because there the alternative is
                       # discovering it thirty minutes into a sweep. It caught two of my own that
                       # day: a NUL byte written into a fallback, and a variable named for the wrong
                       # suite's convention.
                       ("audit_undefined_names.py", []),
                       # A parameter an endpoint ACCEPTS and never reads is the invoke_editor_tab
                       # shape one step earlier - RejectUnknownParams says yes and the handler
                       # ignores it. 2483 accepted parameters across 451 endpoints, currently zero
                       # dead.
                       ("audit_dead_params.py", []),
                       # THE BLENDER TWINS OF TWO ENTRIES ABOVE. audit_consequence_fields and
                       # audit_dead_params are gated; their Blender counterparts were not, which is
                       # the same half-finished shape as gate_53 having weaker checks than
                       # check_engine_probe - one half of a pair hardened and the other left alone.
                       # The Blender backend is a first-class half of this tool, not an accessory:
                       # parity_check exists because that half drifted once and shipped the flagship
                       # road-mesh round trip dead on arrival.
                       #
                       # Both are static - they read the addon source, not a running Blender - and
                       # together they add under a second.
                       #
                       # THE WHOLE TWIN SET WAS SWEPT 2026-09-03, so nobody has to redo it. Four
                       # audit_blender_* tools have a UE counterpart. These two were asymmetric and
                       # are fixed here. audit_read_purity and audit_blender_read_purity are BOTH
                       # ungated and both need a live backend - symmetric and correct.
                       #
                       # audit_blender_postconditions LOOKS like the remaining asymmetry - its UE
                       # twin is gated and it is not - and must stay that way. It needs a running
                       # Blender and exits 2 SKIPPED without one, so gating it would fail every
                       # release built on a machine where Blender is not open, for a reason that has
                       # nothing to do with the release. The UE twin is static; that is the whole
                       # difference. Do not "finish the pair".
                       ("audit_blender_consequence_fields.py", ["--check"]),
                       ("audit_blender_dead_params.py", []),
                       # EVERY REFUSAL'S OWN SENTENCE, gated 2026-09-04 at zero. Callers are told a
                       # refused op is a no-op; seven ops broke that and one of them - set_camera -
                       # moved the camera, its lens and its sensor while saying nothing had changed.
                       # Static on purpose: the dynamic version of this question in
                       # blender_version_matrix could not answer it, because that pass runs after
                       # the sweep has applied each payload and re-applying an idempotent set_* op
                       # changes nothing. Measured, not assumed - see the audit's own docstring.
                       ("audit_mutate_then_deny.py", ["--check"]),
                       # AND ITS OWN RULES, because both of its lists now read zero. That is the
                       # right answer and also the point at which a working rule and a dead one look
                       # identical. --selftest drives eleven synthetic ops through the same code
                       # path, each rule with a case it must catch and a case it must not.
                       ("audit_mutate_then_deny.py", ["--selftest"]),
                       # THE C++ HALF, RATCHETED rather than ungated. Its 50 sites wait on a rebuild
                       # that cannot happen while the editor holds the DLL, so a gate at zero would
                       # be one nobody can turn green - which is a gate people learn to skip. Gated
                       # at the CURRENT state instead, the same shape as audit_postconditions: the
                       # known sites are accepted as KNOWN, not as correct, and a new one fails.
                       ("audit_mutate_then_deny_ue.py", ["--check"]),
                       ("audit_mutate_then_deny_ue.py", ["--selftest"]),
                       # EVERY OP THAT WRITES A FILE CHECKS ITS PATH FIRST, gated 2026-09-04 at
                       # zero. Four of the five did the expensive work - a render, a bake, an FBX
                       # export - and only then discovered they could not save, coming back with a
                       # bare RuntimeError; render_still on some formats did not fail at all and
                       # silently wrote a file named after the extension into the working directory.
                       # That was found as an untracked ".exr" in this repo, by a stray line in
                       # `git status` rather than by any check. This is the check.
                       ("audit_output_paths.py", ["--check"]),
                       # AND THE NAME A CREATE ACTUALLY GOT, gated 2026-09-04 at zero. Blender
                       # renames on collision rather than failing, so a caller retrying a timed-out
                       # create gets "Foo.001" and believes it holds "Foo"; assign_node_group by the
                       # name it asked for then finds the wrong group. Eight ops did this, measured
                       # by calling every create op twice. The addon had already chosen the fix four
                       # times and nothing checked that the next op used it.
                       ("audit_created_name_reported.py", ["--check"]),
                       # AND NO BARE float()/int() ON CALLER INPUT, gated 2026-09-04 at zero.
                       # Python's json module parses NaN and Infinity by default, float() takes
                       # them, int() has no 32-bit bound, and Blender stores all of it - so
                       # ray_cast answered "hit": false for a NaN origin, set_bone_pose took a NaN
                       # quaternion, and set_frame_range raised a bare ValueError from inside
                       # Blender. Eleven files were fixed by hand; this stops the twelfth.
                       ("audit_unguarded_numbers.py", ["--check"]),
                       # TWO SUITES THAT NEED NO EDITOR, joined 2026-09-03. Both were found by
                       # asking which suites have NO record in suite_results.json at all - five did,
                       # and these two turned out to be static, so they had never been run for no
                       # reason other than being newer than the last sweep. Both passed first time:
                       # 12/12 and 11/11.
                       #
                       # test_release_gates guards THIS FILE. It was changed five times today -
                       # gate_53's dirty-NOW check, its fail-closed exit, _porcelain_paths, --gates
                       # learning to run the source audits, and seven new entries in this tuple -
                       # and R100 asserts README and CHANGELOG come back byte-identical, which is
                       # what makes "--gates changes nothing" a checked claim rather than a promise.
                       #
                       # test_scratch_discrimination guards mifaudit.is_scratch_fixture, the
                       # adopt-guard that three of today's defects turned on, and it exercises it
                       # with plain dicts - no bridge, no editor. Together they add under half a
                       # second. test_fuzz_detector and layout_graph are already here for the same
                       # reason: a static self-test belongs where somebody is made to run it.
                       ("test_release_gates.py", []),
                       ("test_scratch_discrimination.py", []),
                       # THE CONTRACT SIX SHIPPED BUGS TURNED ON, and nothing tested it until
                       # 2026-09-03. Both transports drop None and SEND every other falsy value, and
                       # every one of those six - sculpt_landscape, override_inherited_component,
                       # map_legacy_input, set_struct_member, set_enum_value, set_collision -
                       # depended on that single line behaving exactly so.
                       #
                       # audit_mcp_default_sends watches the WRAPPERS for concrete defaults; this
                       # pins the rule those wrappers are written against. If the filter ever
                       # dropped falsy values instead of None, every `or None` in server.py would
                       # become redundant, every row in that audit would go quiet, and the tools
                       # would start working BY ACCIDENT - until somebody needed to send a
                       # deliberate false, which mifaudit.AUTHORISING_ONLY depends on being able to.
                       ("test_payload_contract.py", []),
                       # THE BLENDER HALF OF THAT SAME IDEA, added 2026-09-03 the day it was
                       # written. test_payload_contract proves the UE transport contract with no
                       # editor; this proves the addon's REFUSAL contracts with no Blender, by
                       # stubbing bpy hard enough to import the ops modules and reach the guards -
                       # which is possible only because every op here is written so a refusal fires
                       # BEFORE anything touches bpy.
                       #
                       # It exists because the addon went 68 -> 103 ops in one session while Blender
                       # sat on the machine with its addon not listening, so 35 new ops shipped with
                       # "the static gates are green" as the strongest claim available. 56 checks,
                       # 0.17s, and two of them - B110 and B111 - sweep the WHOLE op table rather
                       # than a hand-listed subset, so a new op is covered the moment it registers
                       # instead of when somebody remembers to add a case.
                       #
                       # Ground-truthed before gating, twice, because the first attempt proved
                       # nothing: an UnboundLocalError killed the run and the rc=1 looked like the
                       # planted defect being caught. The real probe disabled op_set_light's
                       # missing-object guard and B111 named it exactly - "set_light
                       # (AttributeError)" - with B103 catching the lost message alongside.
                       #
                       # WHAT GATING THIS DOES NOT BUY: any postcondition. Nothing in it proves an
                       # op DOES what it says once Blender is real. Green here plus green in the
                       # rest of this tuple still leaves every evaluated matrix, colour space and
                       # purge count unverified, and the suite prints that in its own footer so a
                       # passing run cannot be misread as more than it is.
                       ("test_blender_refusals.py", []),
                       # THE ONE CHECK IN THIS TUPLE THAT RUNS AGAINST A REAL BLENDER, and the
                       # answer to the day test_blender_refusals could not have caught: the whole
                       # compositor family shipped DEAD on 5.0 - scene.node_tree does not exist
                       # there - with thirteen of its offline checks passing and every gate green.
                       # It was found by luck, because the live addon happened to come up and a read
                       # op returned an AttributeError.
                       #
                       # The offline suite is structurally incapable of that finding. It runs against
                       # a stub written from the same assumptions as the code, so it agrees with
                       # whatever the author believed including the wrong parts. A stub is a mirror.
                       #
                       # SAFE TO GATE because each install is launched --background --factory-startup:
                       # a throwaway process with a fresh default scene, touching no file, no running
                       # Blender and nobody's session. That is what lets the MUTATING ops be run too.
                       #
                       # AND IT IS CHEAP, which is the objection this tuple's own header raises about
                       # audit_prose_dependence at 58s. MEASURED rather than assumed: a bare headless
                       # Blender launch is 0.37s on this machine, and the full sweep - 132 ops across
                       # 3.6, 4.2, 4.4 and 5.0, 528 calls - is 2.0s. Cheaper than audit_factory_init,
                       # which is already here at 15.9s.
                       #
                       # A refusal is NOT a failure here, so this does not go red because the default
                       # scene has no armature. It goes red on a RAW exception or an unexpected
                       # divergence between builds. Exit code exercised both ways before gating: 1
                       # with a planted AttributeError, 0 clean.
                       ("blender_version_matrix.py", []),
                       # PARITY_CHECK, which was not in this tuple and is not run anywhere else in
                       # this file - checked 2026-09-03 by listing every script make_release
                       # actually executes: audit_value_discovery, harvest_param_table, and whatever
                       # is here. It is NAMED in four comments and invoked by none of them.
                       #
                       # This repo calls it "the compiler the Blender half doesn't have", and it
                       # exists because that half drifted once and shipped the flagship road-mesh
                       # round trip DEAD ON ARRIVAL. It checks three contracts in both directions -
                       # op parity, param parity, UE MIF_BIND vs _post parity - and is FAIL-CLOSED
                       # by design: anything it cannot statically resolve is a failure, not a skip.
                       #
                       # 3.4s, currently clean, so a ratchet at zero like the rest. The one thing
                       # gating it changes is that a stale EXEMPTION now blocks a release rather
                       # than sitting in a report - which is the whole point, since this file's own
                       # header says an exemption is a decision and a stale one is worse than none.
                       ("parity_check.py", []),
                       # THE OTHER TWO AUDITS THE README TELLS PEOPLE TO RUN. It names four;
                       # audit_dead_params and audit_vacuous_checks were already here and these two
                       # were not, so half the list a reader is pointed at was advisory. If a check
                       # is worth recommending in the front-door document, it is worth failing a
                       # release for.
                       #
                       # spec_check guards the spec against a claim this project makes constantly -
                       # an item ticked [x] while its own body still says it is not built. 416 items,
                       # 123ms, and directly relevant on any day items get ticked.
                       #
                       # audit_message_endpoints catches an error message advising a caller to call
                       # something that does not exist, or naming a parameter its endpoint does not
                       # accept. It also SELF-TESTS on every invocation now, so gating it gates a
                       # checker that proves its own newest arm before reporting.
                       ("spec_check.py", []),
                       ("audit_message_endpoints.py", []),
                       # THE FRONT-DOOR NUMBERS, gated because they failed FOUR TIMES in one
                       # working day and nothing said so. The architecture doc claimed 68 Blender
                       # ops against a real 140 - on a line already carrying a comment about having
                       # read "12 ops" for too long - the README cell said the same, its badge was
                       # 72 ops out, and a derived "the other 63 Blender ops" was stale in a
                       # phrasing no badge check could ever cover.
                       #
                       # NEEDS --check: bare, it reports and exits 0, the same trap its siblings
                       # carry. 0.23s.
                       #
                       # THIS IS NOT CHURN, which is the objection that keeps check_badge out of
                       # this tuple. The badge changes every time an op is added; this fires only
                       # when somebody WRITES a number into a current-state doc that disagrees with
                       # the tool computing it. With none written, adding an op leaves it green.
                       # Scoped to the docs that assert the PRESENT - the postmortems, gotchas and
                       # the spec are logs whose dated numbers are correct history.
                       ("audit_stale_counts.py", ["--check"]),
                       # A POSTMORTEM CITES THIS AS ITS PREVENTION, and until 2026-09-03 it had no
                       # non-zero exit anywhere in the file - it could not fail, so nothing made a
                       # person read the drift it found. A "Prevention" line is a claim that a class
                       # of defect is now caught; an ungated report-style tool does not deliver it.
                       #
                       # Found by checking every tool cited in a Prevention paragraph against this
                       # tuple: 11 cited, 0 fictional, 7 already gated, 2 enforced by another route
                       # (harvest_param_table via check_param_table, param_reach via parity_check),
                       # and this one genuinely advisory.
                       #
                       # NEEDS --check for the same reason its siblings do: bare, it reports and
                       # exits 0. What it guards is an engine upgrade adding a post-construct
                       # factory that create_asset would then mint silently - the exact shape that
                       # terminated the editor twice.
                       #
                       # IT IS THE MOST EXPENSIVE MEMBER HERE, 15.9s of the roughly 60 this tuple
                       # costs, because it walks the engine tree under D:/UE532. Worth stating
                       # beside the exclusion of audit_prose_dependence a few lines down, which is
                       # kept out for BEING SLOW: 58s against 15.9s is a 3.6x difference, and what
                       # the two buy is not comparable - one guards a class that has terminated the
                       # editor twice, the other asks whether a tool reads prose as evidence. The
                       # line is drawn on cost against consequence, not on cost alone.
                       #
                       # If this tuple keeps growing, split it into fast and slow rather than
                       # letting the whole thing drift past what somebody will run casually. A gate
                       # nobody runs fails the same way as a gate nobody reads.
                       ("audit_factory_init.py", ["--check"]),
                       # NOT audit_prose_dependence, and the reason is measured rather than a
                       # shrug: it runs 17 candidate tools THREE times each - plain, with comments
                       # scrubbed, with string literals scrubbed - which is 51 tool invocations and
                       # about 58 seconds. That is inherent to the question it asks, not incidental
                       # slowness somebody could tune away, so it will not become gateable later.
                       # Adding it would more than triple the cost of a question meant to be asked
                       # casually, and a gate nobody runs because it is slow fails the same way as
                       # a gate nobody reads.
                       ):
        script = os.path.join(HERE, tool)
        if not os.path.isfile(script):
            failed.append("%s is MISSING" % tool)
            continue
        r = subprocess.run([sys.executable, script] + args, capture_output=True, text=True)
        if r.returncode != 0:
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            head = next((l for l in lines if l.startswith(("NEW", "SELF-CHECK", "MISSING", "WRONG"))
                         or "misclassified" in l or "FAILED:" in l),
                        lines[-1] if lines else "no output")
            failed.append("%s%s -> %s"
                          % (tool, (" " + " ".join(args)) if args else "", head[:110]))
        else:
            # LABELLED BY WHAT WAS RUN, not by the script name. audit_mutate_then_deny is gated
            # twice - once for its findings and once for --selftest - and listing both as the bare
            # script name printed the same audit twice in the summary, which reads as a bug in the
            # list rather than two different checks.
            ran.append(tool[:-3] + (" --selftest" if "--selftest" in args else ""))
    if not failed:
        # COUNTED, NOT LISTED BY HAND. This used to name five audits and two self-tests in a literal
        # string, and by 2026-09-03 the tuple above held fourteen entries - so the success message
        # was reporting a set that had not been current for some time, in the one place a reader
        # looks to see what was actually checked. A hand-written list beside a real one is a second
        # source of truth, which is the objection this file raises about manifests elsewhere.
        return True, ("%d ratcheted source checks at baseline: %s"
                      % (len(ran), ", ".join(sorted(ran))))
    return False, ("a ratcheted source audit reports something NEW:\n    %s\n"
                   "  Read it and either fix it or accept it with that tool's --update-baseline,\n"
                   "  saying why in the commit. Do not package past it."
                   % "\n    ".join(failed))


def check_engine_probe():
    """(ok, message) - is there a passing 5.7 probe covering the current Source/?"""
    if not os.path.isfile(PROBE_RESULT):
        return False, ("no 5.7 compile has ever been recorded. 0.7.0 shipped unable to build on 5.7 "
                       "in any project but this one, which is what this gate exists to stop.\n"
                       "  Run:  python tools/make_engine_probe.py --engine "
                       "\"C:/Program Files/Epic Games/UE_5.7\" --out D:/MifProbe57gate --build")
    try:
        with io.open(PROBE_RESULT, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except Exception as exc:
        return False, "could not read %s: %s" % (PROBE_RESULT, exc)

    # INCONCLUSIVE IS NOT FAILURE, and conflating them is how a gate teaches people to --force.
    # A probe that never reached the compiler - Live Coding holding the toolchain is the usual
    # cause, and it only takes an open editor - is no evidence either way.
    if rec.get("inconclusive"):
        return False, ("the last 5.7 probe produced NO VERDICT: %s\n"
                       "  That is not a compile failure - it is a missing answer, and a release "
                       "cannot claim an engine on one."
                       % (rec.get("why") or "reason not recorded"))
    if not rec.get("succeeded"):
        return False, ("the last recorded 5.7 probe FAILED to compile (engine %s). Packaging a "
                       "release that is known not to build on a claimed engine is the exact thing "
                       "0.7.0 did." % rec.get("engine"))

    # A COMMIT MATCH IS NOT ENOUGH IF NEITHER SIDE IS THE COMMIT. This gate compared the recorded
    # Source commit against the current one and passed when they were equal - true even when the
    # probe had compiled that commit plus uncommitted edits, or when the release is about to package
    # uncommitted edits on top of a commit the probe did cover. tracked_files() ships what git
    # TRACKS, read from the WORKING TREE, so a dirty Source/ means shipping code no probe has seen
    # with the engine gate showing green.
    was_dirty = rec.get("sourceDirty") or []
    if was_dirty:
        return False, ("the recorded probe ran over a DIRTY Source/, so the commit it names is not\n"
                       "  what compiled. Commit and re-probe.\n"
                       "  Dirty at probe time: %s"
                       % ", ".join(was_dirty[:6]))
    now_dirty = _porcelain_paths(_git("status", "--porcelain", "--", "Source"))
    if now_dirty:
        return False, ("Source/ is dirty NOW, so the release would package code the probe never\n"
                       "  compiled, behind a gate reporting the matching commit as verified.\n"
                       "  Uncommitted: %s"
                       % ", ".join(now_dirty[:6]))

    probed = rec.get("sourceCommit") or ""
    current = _git("log", "-1", "--format=%H", "--", "Source")
    if not current:
        return True, "probe passed; could not read the current Source commit to compare against"
    if probed != current:
        # Is the difference actually source, or just this file moving?
        changed = _git("diff", "--name-only", probed, current, "--", "Source") if probed else "?"
        return False, ("the recorded 5.7 probe covers Source commit %s and Source is now at %s.\n"
                       "  A dated verification says NOTHING about code written after it - that is\n"
                       "  precisely how 0.7.0 shipped broken with a truthful README.\n"
                       "  Changed since the probe: %s\n"
                       "  Re-run: python tools/make_engine_probe.py --engine "
                       "\"C:/Program Files/Epic Games/UE_5.7\" --out D:/MifProbe57gate --build"
                       % (probed[:12] or "(none)", current[:12],
                          ", ".join((changed or "").split()[:6]) or "(unknown)"))
    return True, "5.7 probe passed and covers the current Source commit %s" % current[:12]



BUILD_RECORD_53 = os.path.join(HERE, "engine_build_53.json")


def record_53_build(ok, detail=""):
    """Write what the 5.3 build actually did, against the Source commit it did it to."""
    rec = {
        "engine": "5.3.2",
        "succeeded": bool(ok),
        "sourceCommit": _git("log", "-1", "--format=%H", "--", "Source") or "",
        "sourceDirty": bool((_git("status", "--porcelain", "--", "Source") or "").strip()),
        "detail": detail,
    }
    with io.open(BUILD_RECORD_53, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    return rec


def check_changelog():
    r"""Does CHANGELOG.md's top row agree with the numbers this file generates?

    WHY THIS EXISTS, and it is a different failure from the badge gate above.

    The badge was WRONG because nothing regenerated it. The changelog table was wrong because it was
    measured by hand with a slightly different regex - `MIF_DECL\((\w+)\)` unanchored, which also
    matches `#define MIF_DECL(Name) ...` and counted the macro's own parameter as an endpoint. Every
    UE column in it was one too high from 0.3.0 through 0.8.1, and the badge sat two lines away in
    another file reading the correct number the whole time.

    Nothing compared them, so they drifted in silence for six releases. That is the actual defect
    here: not either number, but that two sources of the same fact had no relationship. This gate is
    the relationship.

    Checks the FIRST data row of the table only. Historical rows describe tags and must not be
    rewritten to match today's tree - they were right about then.
    """
    path = os.path.join(ROOT, "CHANGELOG.md")
    try:
        text = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return True, "no CHANGELOG.md to check"

    rows = re.findall(r"^\|\s*\[([^\]]+)\]\([^)]*\)\s*\|[^|]*\|\s*(\d+)\s*\|\s*(\d+)\s*\|",
                      text, re.M)
    if not rows:
        return True, "CHANGELOG.md has no version table to check"

    name, ue, ops = rows[0]
    ue, ops = int(ue), int(ops)
    want_ue, want_ops = endpoint_count(), blender_op_count()
    if ue == want_ue and ops == want_ops:
        return True, "changelog top row (%s) agrees: %d endpoints, %d Blender ops" % (name, ue, ops)
    return False, ("CHANGELOG.md's top row (%s) disagrees with the tree.\n"
                   "  have: %d UE endpoints, %d Blender ops\n"
                   "  want: %d UE endpoints, %d Blender ops\n"
                   "  Historical rows are snapshots and must NOT be edited - they were right about\n"
                   "  their own tag. Fix the top row, or add an Unreleased row with these figures."
                   % (name, ue, ops, want_ue, want_ops))


def gate_53():
    """(ok, message) - the 5.3 half of the same question gate_57 asks.

    WHY THIS EXISTS. The engine matrix asserted 5.3.2 was "built and tested" as a hardcoded string,
    so it stayed true-looking through every change to Source. 5.7 has been gated on a recorded probe
    since 0.7.0 shipped broken on the strength of a truthful-but-stale README - and 5.3 is the
    PRIMARY target, so it had the weaker guarantee of the two.

    It is deliberately not a probe build: 5.3 is the engine this project develops against, so the
    ordinary incremental build IS the verification. What was missing is recording WHICH COMMIT it
    verified, which is the only part that makes a dated claim mean anything.
    """
    try:
        with io.open(BUILD_RECORD_53, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return False, ("no 5.3 build has been recorded. The matrix used to ASSERT 5.3.2 was built\n"
                       "  and tested; it now has to be shown. Build MifBridge against 5.3 and run:\n"
                       "    python tools/make_release.py --record-53")
    if not rec.get("succeeded"):
        return False, "the recorded 5.3 build FAILED: %s" % (rec.get("detail") or "(no detail)")
    if rec.get("sourceDirty"):
        return False, ("the recorded 5.3 build ran against a DIRTY Source tree, so the commit it\n"
                       "  names is not what compiled. Commit and rebuild.")
    # DIRTY NOW, not just dirty at record time. check_engine_probe has asked this since it was
    # written and gate_53 did not, which put the weaker guarantee on the PRIMARY target - the exact
    # asymmetry this function's own docstring was written to end, left half-finished. tracked_files()
    # ships what git TRACKS, read from the WORKING TREE, so uncommitted Source/ edits are packaged
    # while this gate reports the matching commit as verified.
    now_dirty = _porcelain_paths(_git("status", "--porcelain", "--", "Source"))
    if now_dirty:
        return False, ("Source/ is dirty NOW, so the release would package code the 5.3 build never\n"
                       "  compiled, behind a gate reporting the matching commit as verified.\n"
                       "  Uncommitted: %s" % ", ".join(now_dirty[:6]))

    built = rec.get("sourceCommit") or ""
    current = _git("log", "-1", "--format=%H", "--", "Source")
    # FAIL CLOSED WHEN THE QUESTION CANNOT BE ASKED. _git returns "" on any failure, and the
    # comparison below was guarded by `if current and ...` - so an unreadable git fell straight
    # through to the success return and printed "covers the current Source commit" with nothing to
    # back it. A gate that passes when it cannot see is worse than one that is absent, because it
    # reports a positive.
    if not current:
        return False, ("could not read the current Source commit - `git log -1 -- Source` returned\n"
                       "  nothing. This gate cannot compare the recorded build against a commit it\n"
                       "  cannot name, and passing on an unanswerable question is how a stale build\n"
                       "  record ships. Check the repository is intact.")
    if built != current:
        changed = _git("diff", "--name-only", built, current, "--", "Source") if built else "?"
        return False, ("the recorded 5.3 build covers Source commit %s and Source is now at %s.\n"
                       "  5.3 is the PRIMARY target and this row asserted 'built and tested' as a\n"
                       "  literal string until 2026-09-01, when that string was false.\n"
                       "  Changed since: %s"
                       % (built[:12] or "(none)", current[:12],
                          ", ".join((changed or "").split()[:6]) or "(unknown)"))
    return True, "5.3 build passed and covers the current Source commit %s" % (current or built)[:12]


def tracked_files():
    """Ship exactly what git tracks, minus the two categories above.

    Deriving this from git rather than a literal list is deliberate: a hand-written manifest is a second
    source of truth, and this script exists because two copies of one thing drifted apart.
    """
    out = subprocess.run(
        ["git", "-C", ROOT, "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout.splitlines()
    keep = []
    for rel in out:
        rel = rel.strip().replace("\\", "/")
        if not rel:
            continue
        if rel.startswith(EXCLUDE_PREFIXES):
            continue
        # search(), NOT match(). re.match anchors at the START of the string, so a pattern like
        # r"\.log$" matched only a path literally beginning with ".log" - i.e. nothing. The first
        # patterns here happened to work because they began with ^tools/, which hid the mistake until
        # the zip was listed and ten artifacts were found still shipping.
        if FAB_MODE[0] and not rel.startswith(FAB_KEEP_PREFIXES) \
                and any(p.search(rel) for p in FAB_EXCLUDE_PATTERNS):
            continue
        if any(p.search(rel) for p in EXCLUDE_PATTERNS):
            continue
        if is_dev_only(os.path.join(ROOT, rel)):
            continue
        if not os.path.isfile(os.path.join(ROOT, rel)):
            continue      # tracked but deleted in the working tree
        keep.append(rel)
    return sorted(keep)


def content_hash(rels):
    """SHA-256 over path+content of every shipped file.

    Path AND content, so a renamed file changes the hash. Sorted, so it is order-independent. This is
    what lets a consumer tell "same version" from "same version, locally modified" - the distinction
    the Curfew drift needed and did not have.
    """
    h = hashlib.sha256()
    for rel in rels:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with io.open(os.path.join(ROOT, rel), "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def build_manifest(rels):
    name, ver_int = plugin_version()
    return {
        "plugin": "MifBridge",
        "versionName": name,
        "version": ver_int,
        "endpointCount": endpoint_count(),
        "fileCount": len(rels),
        "contentSha256": content_hash(rels),
        "engineCompatibility": ENGINE_MATRIX,
        "note": (
            "endpointCount is the MIF_BIND count, the same number parity_check treats as authoritative. "
            "Compare it against your editor's self_audit to see whether your copy is current. "
            "contentSha256 covers path+content of every shipped file, so it distinguishes 'same version' "
            "from 'same version, locally modified'."
        ),
    }


def build(out_path):
    rels = tracked_files()
    if not rels:
        print("no tracked files - is this a git checkout?")
        return 1
    manifest = build_manifest(rels)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # ONE top-level directory named MifBridge, so the zip extracts straight into a project's Plugins/
    # folder. Loose-at-root would scatter files over Plugins/ itself - the same trap build_zip.py
    # documents for the Blender addon, and the same fix: verify the listing.
    top = "MifBridge"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in rels:
            z.write(os.path.join(ROOT, rel), top + "/" + rel)
        z.writestr(top + "/RELEASE_MANIFEST.json",
                   json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print("wrote %s" % out_path)
    print("  version      %s (Version %d)" % (manifest["versionName"], manifest["version"]))
    print("  endpoints    %d" % manifest["endpointCount"])
    print("  files        %d" % manifest["fileCount"])
    print("  sha256       %s" % manifest["contentSha256"][:16])
    for row in ENGINE_MATRIX:
        print("  engine %-6s %s" % (row["engine"], row["status"]))
    return 0


def check(zip_path):
    """Compare a built zip against THIS tree and say precisely how they differ."""
    if not os.path.isfile(zip_path):
        print("no such zip: %s" % zip_path)
        return 1
    with zipfile.ZipFile(zip_path) as z:
        try:
            got = json.loads(z.read("MifBridge/RELEASE_MANIFEST.json").decode("utf-8"))
        except KeyError:
            print("that zip has no RELEASE_MANIFEST.json - it was not built by this script")
            return 1

    mine = build_manifest(tracked_files())
    same_ver = got.get("versionName") == mine["versionName"]
    same_hash = got.get("contentSha256") == mine["contentSha256"]

    print("zip     : %s  (%s endpoints, sha %s)"
          % (got.get("versionName"), got.get("endpointCount"), str(got.get("contentSha256"))[:16]))
    print("this tree: %s  (%s endpoints, sha %s)"
          % (mine["versionName"], mine["endpointCount"], mine["contentSha256"][:16]))
    print("")
    if same_ver and same_hash:
        print("IDENTICAL - same version, same content.")
        return 0
    if same_ver and not same_hash:
        # The case the Curfew drift actually was, and the one a version number alone cannot catch.
        print("SAME VERSION, DIFFERENT CONTENT. One of the two has been modified locally.")
        print("A version number alone would have called these equal - which is how the vendored")
        print("Curfew copy drifted 62 endpoints behind without anything noticing.")
        return 2
    print("DIFFERENT VERSIONS. Endpoint delta: %s"
          % ((mine["endpointCount"] or 0) - (got.get("endpointCount") or 0)))
    return 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="output zip path")
    ap.add_argument("--check", metavar="ZIP", help="verify a zip against this tree instead of building")
    ap.add_argument("--update-badge", action="store_true",
                    help="rewrite README.md's version line from the real counts, then exit")
    ap.add_argument("--record-53", action="store_true",
                    help="stamp a successful 5.3 build against the current Source commit")
    ap.add_argument("--force", action="store_true",
                    help="package even when the badge is stale (it will ship wrong)")
    ap.add_argument("--fab", action="store_true",
                    help="package for a store: leave out the test suites, audits and dev scripts a "
                         "buyer never runs (305 of 510 files at 0.9.0). The default zip is "
                         "unchanged - what ships is a product decision, not a packaging default.")
    ap.add_argument("--gates", action="store_true",
                    help="report whether the recorded builds still cover Source; changes nothing")
    args = ap.parse_args()
    # WIRED HERE, and it was not on the first pass - the flag existed, the patterns existed, and
    # nothing connected them, so --fab would have produced a byte-identical zip and looked like it
    # worked. Exactly the shape of check this repo keeps deleting, committed while adding one.
    FAB_MODE[0] = bool(args.fab)

    if args.gates:
        # ASK THE GATES WITHOUT ATTEMPTING A RELEASE.
        #
        # Both engine records went stale on 2026-09-03 and neither was noticed until a release was
        # nearly attempted: nine Source commits landed, the 5.3 record was re-taken after some of
        # them, and the 5.7 probe still covered a commit from eight hours earlier. Every one of
        # those nine had been parse-checked against 5.7 with cl /Zs, which is not a link, so the
        # gate was the only thing that would have caught a shape change - and it was not being run.
        #
        # The gates themselves worked perfectly. What was missing was any way to ASK them without
        # starting a packaging run, which nobody does casually. This is that question, and it
        # deliberately reuses gate_53 and check_engine_probe rather than re-deriving the answer.
        #
        # The BADGE is deliberately not here: it is stale between releases BY DESIGN, so including
        # it would make this red almost always and teach everyone to ignore it.
        # THE SOURCE AUDITS WERE MISSING FROM THIS, and that undercut the whole point. The comment
        # above says the problem was having "no way to ASK them without starting a packaging run" -
        # and then asked the ENGINE half only, so the ratcheted source audits still had no way to be
        # asked casually. Found 2026-09-03 by timing it: the whole run took 243ms while
        # audit_undefined_names alone takes 1273ms, which is how you notice a check is not running.
        #
        # check_value_discovery is deliberately NOT here: it drives the live bridge, so it belongs to
        # packaging rather than to a question anybody can ask at any moment. Every tool in
        # check_static_audits is static and the set runs in about two seconds.
        ok53, msg53 = gate_53()
        ok57, msg57 = check_engine_probe()
        okaud, msgaud = check_static_audits()
        print("5.3 build record : %s" % ("OK  " + msg53 if ok53 else "STALE - " + msg53))
        print("5.7 probe record : %s" % ("OK  " + msg57 if ok57 else "STALE - " + msg57))
        print("source audits    : %s" % ("OK  " + msgaud if okaud else "FAILING - " + msgaud))
        if ok53 and ok57 and okaud:
            print("\nboth engine records cover the current Source commit, and every ratcheted source")
            print("audit is at its baseline.")
            return 0
        if not (ok53 and ok57):
            print("\nRebuild and re-record before releasing. A stale record does not mean the build "
                  "is")
            print("broken - it means nothing has checked it since Source moved, which is the same "
                  "thing")
            print("as far as a release claim is concerned.")
        if not okaud:
            print("\nA ratcheted source audit reports something NEW. That is not a veto on the work -")
            print("it is the one moment somebody is asked to look at it before it ships.")
        return 1

    if args.update_badge:
        ok, msg = check_badge(update=True)
        print(msg)
        return 0 if ok else 1

    if args.check:
        return check(args.check)

    # THE BADGE GATE. It refuses rather than silently rewriting a tracked file during a build - a
    # packaging step that edits the repo is how you get a commit you did not write. This is the one
    # number every reader sees first, and it had drifted a hundred endpoints with nothing anywhere
    # able to notice.
    # --record-53 RUNS BEFORE THE PACKAGING GATES, and it used to sit after them.
    #
    # Recording that a 5.3 build succeeded is BOOKKEEPING, not packaging. Sitting below check_badge
    # meant a stale badge - which is the normal state between releases, since the badge is only
    # regenerated at packaging - refused the recording too. So the sequence was: build 5.3
    # successfully, try to record it, get told the README badge is wrong, and have to do a
    # release-time chore before you could write down a fact about a build that had already happened.
    #
    # Worse, it pushed you toward --update-badge on a tree you were not releasing. The gates that
    # follow exist to stop a bad PACKAGE going out; none of them says anything about whether a
    # compiler succeeded twenty minutes ago.
    if getattr(args, "record_53", False):
        rec = record_53_build(True, "recorded by --record-53 after a successful 5.3 build")
        print("recorded 5.3 build for Source commit %s (dirty=%s)"
              % ((rec["sourceCommit"] or "(none)")[:12], rec["sourceDirty"]))
        return 0

    ok, msg = check_badge()
    if not ok:
        print("REFUSING TO PACKAGE - %s" % msg)
        if not args.force:
            return 1
        print("  --force given: packaging anyway, with a badge that is wrong.")

    # The changelog is checked SEPARATELY from the badge because they fail differently: the badge
    # goes stale because nothing rewrites it, the changelog goes wrong because somebody measured it
    # by hand. Both end up as a confident number that is not true, and until now nothing compared
    # the two - which is how they disagreed for six releases with the answer sitting in both files.
    okc, msgc = check_changelog()
    print("  %s" % msgc if okc else "")
    if not okc:
        print("REFUSING TO PACKAGE - %s" % msgc)
        if not args.force:
            return 1
        print("  --force given: packaging anyway, with a changelog that is wrong.")

    # A release claiming two engines has to have compiled against both. 5.3 first, because it is
    # the PRIMARY target and was the one with no gate at all - the matrix asserted it as a string.
    ok53, msg53 = gate_53()
    print(("5.3 gate: " + msg53) if ok53 else ("REFUSING TO PACKAGE - " + msg53))
    if not ok53 and not args.force:
        return 1
    if not ok53:
        print("  --force given: packaging anyway, without a 5.3 build covering this Source.")
    for _row in ENGINE_MATRIX:
        if _row["engine"].startswith("5.3"):
            _row["status"] = ("built and tested" if ok53 else "NOT VERIFIED for this Source commit")

    ok57, msg57 = check_engine_probe()
    print(("5.7 gate: " + msg57) if ok57 else ("REFUSING TO PACKAGE - " + msg57))
    if not ok57 and not args.force:
        return 1
    if not ok57:
        print("  --force given: packaging anyway, without a 5.7 compile covering this Source.")

    # The table is COMPILED IN, so a stale one ships and misreports the plugin's own guards for
    # the whole release. Checked here because packaging is the last point at which it is cheap.
    okpt, msgpt = check_param_table()
    print(("param table: " + msgpt) if okpt else ("REFUSING TO PACKAGE - " + msgpt))
    if not okpt and not args.force:
        return 1
    if not okpt:
        print("  --force given: packaging a stale describe_endpoint table anyway.")

    okvd, msgvd = check_value_discovery()
    print(("value discovery: " + msgvd) if okvd else ("REFUSING TO PACKAGE - " + msgvd))
    if not okvd and not args.force:
        return 1
    if not okvd:
        print("  --force given: packaging a parameter nothing can supply a value for.")

    oksa, msgsa = check_static_audits()
    print(("static audits: " + msgsa) if oksa else ("REFUSING TO PACKAGE - " + msgsa))
    if not oksa and not args.force:
        return 1
    if not oksa:
        print("  --force given: packaging with an unread source-audit finding.")

    name, _ = plugin_version()
    out = args.out or os.path.join(HERE, "dist", "MifBridge-%s.zip" % name)
    return build(out)


if __name__ == "__main__":
    sys.exit(main())
