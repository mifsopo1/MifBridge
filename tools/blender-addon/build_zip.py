#!/usr/bin/env python3
"""Package MifBlender/ into an installable Blender addon zip.

    python build_zip.py            ->  dist/MifBlender.zip
    python build_zip.py --out X    ->  X

Blender's "Install from Disk" wants ONE top-level directory inside the zip whose
name matches the Python package -- MifBlender/__init__.py must land at exactly
that path. A zip with the files loose at the root, or double-nested under a
wrapper folder, installs to the wrong place and the addon never appears in the
Add-ons list. (Same trap as a Nexus mod zip, same fix: verify the listing.)

No dependencies, no Blender required -- this is plain zipfile.
"""

import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_NAME = "MifBlender"
PKG_DIR = os.path.join(HERE, PKG_NAME)
DEFAULT_OUT = os.path.join(HERE, "dist", PKG_NAME + ".zip")

# Never ship build/editor droppings into the addon.
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_EXTS = {".pyc", ".pyo", ".pyd", ".orig", ".rej"}
SKIP_PREFIXES = (".#",)


def _should_skip(filename: str) -> bool:
    if os.path.splitext(filename)[1].lower() in SKIP_EXTS:
        return True
    if filename.startswith(SKIP_PREFIXES):
        return True
    return filename.endswith("~")


def build(out_path: str) -> int:
    if not os.path.isdir(PKG_DIR):
        sys.stderr.write("error: {} not found -- run this from tools/blender-addon/\n".format(PKG_DIR))
        return 2

    init_py = os.path.join(PKG_DIR, "__init__.py")
    if not os.path.isfile(init_py):
        sys.stderr.write(
            "error: {} has no __init__.py, so Blender cannot register it as an addon. "
            "The package is incomplete -- refusing to build a zip that would install and "
            "then silently do nothing.\n".format(PKG_NAME)
        )
        return 2

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    written = []
    # Deterministic order so two builds of the same source produce the same listing.
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PKG_DIR):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if _should_skip(name):
                    continue
                abs_path = os.path.join(root, name)
                # arcname is relative to tools/blender-addon/, so every entry is
                # prefixed "MifBlender/" -- exactly one top-level dir in the zip.
                arcname = os.path.relpath(abs_path, HERE).replace(os.sep, "/")
                zf.write(abs_path, arcname)
                written.append(arcname)

    if not written:
        sys.stderr.write("error: nothing was packaged.\n")
        return 2

    # Verify the layout rather than trusting it.
    tops = {entry.split("/", 1)[0] for entry in written}
    if tops != {PKG_NAME}:
        sys.stderr.write(
            "error: zip has {} top-level entries {} -- expected exactly one, '{}'.\n".format(
                len(tops), sorted(tops), PKG_NAME))
        return 2
    if PKG_NAME + "/__init__.py" not in written:
        sys.stderr.write("error: {}/__init__.py missing from the zip.\n".format(PKG_NAME))
        return 2

    size = os.path.getsize(out_path)
    print("{}  ({} files, {:,} bytes)".format(out_path, len(written), size))
    for entry in written:
        print("  " + entry)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT, help="output zip path (default: dist/MifBlender.zip)")
    raise SystemExit(build(ap.parse_args().out))
