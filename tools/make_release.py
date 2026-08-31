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
    {"engine": "5.3.2", "status": "built and tested", "notes": "cooked editor (DDS2 SDK) - the primary target"},
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


def badge_line():
    """The line the README SHOULD carry, built from the same sources the manifest uses."""
    version, _ = plugin_version()
    return ("`v%s` &nbsp;\u00b7&nbsp; \U0001f3ae **UE 5.3 + 5.7** &nbsp;\u00b7&nbsp; "
            "\U0001f3a8 **Blender 3.6\u20135.0** &nbsp;\u00b7&nbsp; \U0001f50c **%d endpoints** "
            "&nbsp;\u00b7&nbsp; \U0001f9f0 **%d MCP tools** &nbsp;\u00b7&nbsp; "
            "\U0001f9ea **%d test suites**"
            % (version, endpoint_count(), mcp_tool_count(), suite_count()))


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
    now_dirty = [ln[3:] for ln in
                 (_git("status", "--porcelain", "--", "Source") or "").splitlines() if ln.strip()]
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
    ap.add_argument("--force", action="store_true",
                    help="package even when the badge is stale (it will ship wrong)")
    args = ap.parse_args()

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
    ok, msg = check_badge()
    if not ok:
        print("REFUSING TO PACKAGE - %s" % msg)
        if not args.force:
            return 1
        print("  --force given: packaging anyway, with a badge that is wrong.")

    # A release claiming two engines has to have compiled against both.
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

    name, _ = plugin_version()
    out = args.out or os.path.join(HERE, "dist", "MifBridge-%s.zip" % name)
    return build(out)


if __name__ == "__main__":
    sys.exit(main())
