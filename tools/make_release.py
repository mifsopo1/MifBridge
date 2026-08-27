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
    {"engine": "5.7", "status": "built",
     "notes": "COMPILED against stock 5.7.4 via tools/make_engine_probe.py on 2026-08-26, and run in "
              "Curfew at 291 endpoints. create_editable_child refuses - it needs a DDS2 engine-FORK "
              "header no stock Unreal has. See docs/02_GOTCHAS.md section 14 for the six API splits, "
              "and docs/06 issue 17 for a plugin-enablement defect that affects consumers who do not "
              "enable the ten optional plugins."},
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


def tracked_files():
    """Ship exactly what git tracks, minus the two categories above.

    Deriving this from git rather than a literal list is deliberate: a hand-written manifest is a second
    source of truth, and this script exists because two copies of one thing drifted apart.
    """
    out = subprocess.run(
        ["git", "-C", ROOT, "ls-files"],
        capture_output=True, text=True, check=True,
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
    args = ap.parse_args()

    if args.check:
        return check(args.check)

    name, _ = plugin_version()
    out = args.out or os.path.join(HERE, "dist", "MifBridge-%s.zip" % name)
    return build(out)


if __name__ == "__main__":
    sys.exit(main())
