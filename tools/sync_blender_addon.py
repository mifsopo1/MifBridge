"""Copy the repo addon into every installed Blender, and say what changed.

WHY THIS EXISTS, and it cost real time on 2026-08-30. There are two copies of MifBlender:

    tools/blender-addon/MifBlender/                     the SOURCE, in git
    %APPDATA%/Blender Foundation/Blender/<ver>/scripts/addons/MifBlender/    what a GUI Blender loads

The headless test harness (run_blender_suites.py) inserts the REPO on sys.path, so suites always
exercise current source. A GUI Blender loads the INSTALLED copy. So the two drift apart silently and
no suite ever goes red about it - which is exactly what happened: Andre's Blender was serving 24 ops
while the repo had 33, missing ops_rig.py entirely, and nobody noticed because every automated run
was testing the repo.

Then it happened again the same evening, in the small: three edits were made to the source, the GUI
was restarted, and the suites still passed - against the stale installed copy, because a restart
reloads the addon from where it is INSTALLED, not from where it was edited. Two of those checks
happened to pass either way, which is worse than failing.

__pycache__ IS DELETED, not overwritten. Python will import a stale .pyc for a module whose source
has changed if the timestamps line up, and a "rebuilt" addon that keeps serving old ops is a very
expensive hour.

Usage:
    python tools/sync_blender_addon.py            # sync every installed Blender
    python tools/sync_blender_addon.py --check    # report drift, change nothing (exit 1 if drift)
"""
import argparse
import filecmp
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "blender-addon", "MifBlender")
ROAMING = os.path.join(os.environ.get("APPDATA", ""), "Blender Foundation", "Blender")


def installed_dirs():
    """Every Blender config dir that exists. A version never launched has none."""
    if not os.path.isdir(ROAMING):
        return []
    out = []
    for ver in sorted(os.listdir(ROAMING)):
        base = os.path.join(ROAMING, ver, "scripts", "addons")
        if os.path.isdir(os.path.join(ROAMING, ver)):
            out.append((ver, os.path.join(base, "MifBlender")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report drift and change nothing; exit 1 if any copy is stale")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print("no source addon at %s" % SRC)
        return 2
    files = sorted(f for f in os.listdir(SRC) if f.endswith(".py"))
    print("source: %s (%d modules)" % (SRC, len(files)))

    drift = 0
    for ver, dst in installed_dirs():
        if not os.path.isdir(dst):
            if args.check:
                print("  %-5s NOT INSTALLED" % ver)
                continue
            os.makedirs(dst, exist_ok=True)

        stale = []
        for f in files:
            d = os.path.join(dst, f)
            if not os.path.exists(d) or not filecmp.cmp(os.path.join(SRC, f), d, shallow=False):
                stale.append(f)
        extra = [f for f in os.listdir(dst)
                 if f.endswith((".py", ".bak")) and f not in files]

        if args.check:
            if stale or extra:
                drift += 1
                print("  %-5s STALE: %s%s" % (ver, ", ".join(stale) or "-",
                                              "  extra: " + ", ".join(extra) if extra else ""))
            else:
                print("  %-5s up to date" % ver)
            continue

        # Bytecode FIRST: a stale .pyc for a changed source is how a "rebuilt" addon keeps
        # serving the old ops.
        pyc = os.path.join(dst, "__pycache__")
        if os.path.isdir(pyc):
            shutil.rmtree(pyc)
        for bak in glob.glob(os.path.join(dst, "*.bak")):
            os.remove(bak)
        for f in files:
            shutil.copy2(os.path.join(SRC, f), os.path.join(dst, f))
        # A module deleted from source must not survive in the install, or its ops keep answering.
        for f in extra:
            if f.endswith(".py"):
                os.remove(os.path.join(dst, f))
        print("  %-5s synced%s" % (ver, (" (%d changed)" % len(stale)) if stale else " (no change)"))

    if args.check:
        print("\n%s" % ("DRIFT in %d install(s) - run without --check" % drift if drift
                        else "every installed Blender matches the source"))
        return 1 if drift else 0
    print("\nRestart any running Blender to load the new modules - a running one keeps the copy it "
          "imported at startup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
