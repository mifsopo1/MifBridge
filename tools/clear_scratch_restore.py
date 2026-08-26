"""Clear the editor's "Restore Packages" prompt - but ONLY when every entry in it is scratch.

WHY THIS EXISTS. An unattended run creates scratch assets and never saves them, which is the point.
When the editor is then killed - by a crash the sweep is hunting for, or by a rebuild - those unsaved
packages go into Saved/Autosaves/PackageRestoreData.json, and the NEXT launch opens a modal offering
to restore them. After one night that list held 448 entries.

That modal is the same outage as any other: it goes up before the bridge starts serving, the game
thread sits in it, and an automated relaunch waits out its whole timeout for an editor that is never
going to answer. The failure is silent and total, and it arrives at the worst moment - right after a
crash, when the run is already trying to recover.

WHAT IT WILL NOT DO. If a single entry is NOT a scratch path, this refuses and changes nothing. That
entry is somebody's real unsaved work - the /Game/Saved/Autosaves tree on this machine holds Andre's
DDS2Casino quest blueprints - and silently discarding a recovery offer for real work is far worse than
a modal. The check is mechanical, not a judgement call: every PackagePathName must start with a
scratch prefix.

The autosave FILES are never touched. Only the "offer to restore" list is emptied, and the previous
manifest is backed up first.
"""
import io
import json
import os
import re
import shutil
import sys

MANIFEST = "D:/DDS2SDK/Game/Saved/Autosaves/PackageRestoreData.json"

# /Temp/ is the engine's home for the unsaved Untitled map a headless session leaves behind; it is as
# disposable as the /Game/_Mif* assets the suites create.
SCRATCH_PREFIXES = ("/Game/_Mif", "/Temp/")


def read_entries(path=MANIFEST):
    """Return the package path names the manifest offers to restore.

    The engine writes this file as UTF-16LE with NO byte order mark, which json.load and
    codecs 'utf-16' both refuse - the latter with "stream does not start with BOM". Decode it
    explicitly rather than guessing.
    """
    if not os.path.isfile(path):
        return []
    raw = open(path, "rb").read()
    text = raw.decode("utf-16-le", errors="ignore") if b"\x00" in raw[:64] else raw.decode("utf-8", "ignore")
    return re.findall(r'"PackagePathName"\s*:\s*"([^"]*)"', text)


def is_scratch(name):
    return any(name.startswith(p) for p in SCRATCH_PREFIXES)


def clear(path=MANIFEST, quiet=False):
    """Empty the restore list if and only if every entry is scratch.

    Returns (cleared, count, offenders). `cleared` is False both when there was nothing to do and
    when it refused - the offenders list is what tells those apart.
    """
    names = read_entries(path)
    if not names:
        return False, 0, []
    offenders = [n for n in names if not is_scratch(n)]
    if offenders:
        if not quiet:
            print("REFUSING to clear the restore list: %d of %d entries are NOT scratch."
                  % (len(offenders), len(names)))
            for n in sorted(set(offenders))[:10]:
                print("    %s" % n)
            print("  Those are real unsaved packages. Open the editor and answer the prompt by hand.")
        return False, len(names), offenders

    bak = path + ".bak-scratch-clear"
    try:
        shutil.copy2(path, bak)
    except Exception as e:
        if not quiet:
            print("could not back up the manifest (%s) - not clearing it" % e)
        return False, len(names), []

    body = '{\r\n\t"RestoreEnabled": true,\r\n\t"Packages": [\r\n\t]\r\n}\r\n'
    open(path, "wb").write(body.encode("utf-16-le"))
    if not quiet:
        print("cleared %d scratch package(s) from the restore prompt (backup: %s)"
              % (len(names), os.path.basename(bak)))
    return True, len(names), []


def main():
    names = read_entries()
    if not names:
        print("nothing in the restore list - the editor will start without the prompt")
        return 0
    scratch = [n for n in names if is_scratch(n)]
    print("restore list holds %d package(s): %d scratch, %d other"
          % (len(names), len(scratch), len(names) - len(scratch)))
    cleared, _, offenders = clear()
    return 2 if offenders else (0 if cleared else 1)


if __name__ == "__main__":
    sys.exit(main())
