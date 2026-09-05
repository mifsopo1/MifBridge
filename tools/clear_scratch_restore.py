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
import time
import shutil
import sys

# WHICH PROJECT'S MANIFEST. This was a hardcoded DDS2 path - the third tool found with that shape on
# 2026-09-05, after mifwatch and test_crash_journal, and the worst of the three: this one WRITES.
# Pointed at the wrong project it clears a restore offer belonging to a session it never touched,
# and the whole point of the file is that discarding somebody's recovery data silently is worse than
# the modal it prevents.
#
# Only reached when mifaudit cannot be imported at all. See default_manifest for why "ask the live
# editor" was the wrong question and why the answer comes from the targeted project instead.
_LAST_RESORT = "D:/DDS2SDK/Game/Saved/Autosaves/PackageRestoreData.json"


def default_manifest():
    """(path, source). The project this RUN targets - not merely whoever holds the port.

    THE FIRST VERSION OF THIS WAS STILL WRONG, and an audit found it the same night. It asked the
    live editor and otherwise fell back to a hardcoded DDS2 literal, which reads as a sensible
    ordering and is exactly backwards at the one call site that matters.

    mifaudit.launch_editor() calls clear() immediately AFTER taskkilling every editor and sleeping
    five seconds. Nothing is listening on the port by then, by construction - so live_saved_dir()
    returns None every single time and the FALLBACK is the normal path, never the exception. Aimed
    at Curfew, the sequence was: kill Curfew's editors, then clear DDS2's restore manifest. It wrote
    to a project the run had never touched, which is verbatim the failure this module was changed to
    prevent. And Curfew's own manifest went uncleared, so the modal the call exists to pre-empt
    still came up on the editor launched forty lines later.

    "Whoever holds the port" was never the right question either. mifaudit.UPROJECT already names
    the project this run targets, it is aimed by MIF_PROJECT_PATH, and launch_target() has already
    refused to proceed unless its basename equals PROJECT_MARKER. That verified name is the answer;
    asking the network for it was the mistake.
    """
    try:
        import mifaudit as M
        proj = getattr(M, "UPROJECT", "")
        if proj:
            return (os.path.join(os.path.dirname(proj), "Saved", "Autosaves",
                                 "PackageRestoreData.json"),
                    "the project this run targets (%s)" % os.path.basename(proj))
    except Exception:                     # noqa: BLE001 - no mifaudit is not a failure here
        pass
    return _LAST_RESORT, "the built-in default - mifaudit could not be asked"


# RESOLVED PER CALL, NOT FROZEN AT IMPORT. This was `MANIFEST = default_manifest()[0]` evaluated
# once when the module first loaded, and bound as the default of read_entries and clear - so a
# long-lived process could not follow a change of target, and the value depended on whether an
# editor happened to be up at import time. A path that means "the current project" must be asked
# for when it is used.
def MANIFEST():
    return default_manifest()[0]

# /Temp/ is the engine's home for the unsaved Untitled map a headless session leaves behind; it is as
# disposable as the /Game/_Mif* assets the suites create.
SCRATCH_PREFIXES = ("/Game/_Mif", "/Temp/")

# EDITOR FURNITURE THE EDITOR DIRTIES BY ITSELF, and it is here because refusing over it made this
# guard useless three times in one session.
#
# Every sweep leaves the same 22 entries in the restore list: the transform-gizmo meshes, the
# camera and crane rig meshes, the snap-grid plane, the engine sphere. Nobody authored them - they
# ship with the engine as editor-only visuals, and simply moving a gizmo in a viewport marks them
# dirty. They are not somebody's unsaved work under any reading, and there is nothing to lose by
# declining to restore one.
#
# Measured 2026-09-01: with these treated as real, the guard refused on every launch after a sweep,
# the editor sat in a modal the bridge could not answer, and the run had to be recovered by hand
# THREE times. A guard that always refuses gets forced past routinely, which is exactly how the
# forced path stops being read - so narrowing it to actual ambiguity makes the remaining refusal
# mean something.
#
# NARROW ON PURPOSE. Not all of /Engine: engine content a project has legitimately modified is real
# work. Only the editor-visual trees, listed one by one.
EDITOR_FURNITURE_PREFIXES = (
    "/Engine/EditorMeshes/",
    "/Engine/VREditor/",
    "/Engine/EngineMeshes/",
)


def read_entries(path=None):
    """Return the package path names the manifest offers to restore.

    The engine writes this file as UTF-16LE with NO byte order mark, which json.load and
    codecs 'utf-16' both refuse - the latter with "stream does not start with BOM". Decode it
    explicitly rather than guessing.
    """
    path = path or MANIFEST()
    if not os.path.isfile(path):
        return []
    raw = open(path, "rb").read()
    text = raw.decode("utf-16-le", errors="ignore") if b"\x00" in raw[:64] else raw.decode("utf-8", "ignore")
    return re.findall(r'"PackagePathName"\s*:\s*"([^"]*)"', text)


def is_scratch(name):
    return (any(name.startswith(p) for p in SCRATCH_PREFIXES)
            or any(name.startswith(p) for p in EDITOR_FURNITURE_PREFIXES))


def is_editor_furniture(name):
    """Engine editor-visual content, reported separately so the count stays honest."""
    return any(name.startswith(p) for p in EDITOR_FURNITURE_PREFIXES)


def clear(path=None, quiet=False, force=False, why=None):
    """Empty the restore list if and only if every entry is scratch.

    Returns (cleared, count, offenders). `cleared` is False both when there was nothing to do and
    when it refused - the offenders list is what tells those apart.

    FORCE, added 2026-08-30, and the reasoning is worth keeping because the default refusal is
    right and must stay right. A regression run legitimately dirties NAMED maps: the suites use
    /Game/Maps/MifWeaponTest deliberately (it is one of the very few LOOSE maps in this project, so
    it is the only thing the sublevel family can be tested against). Kill that editor and the
    manifest holds a dozen non-scratch entries, this refuses, and the next launch sits in a modal
    the bridge cannot answer - so the recovery path is blocked by the thing meant to protect it.
    The tool had no way to say "I looked, and these are mine".

    The alternative to giving it one is worse: whoever hits this hand-edits the manifest, which is
    the same discard with no backup and no record. An escape hatch that REPORTS beats a guard people
    route around.

    force does not weaken the check - it still lists every offender, still backs up first, and now
    requires `why`, which is printed. The judgement it encodes is "the caller has established the
    provenance of these entries", and the honest way to do that is the manifest's own mtime: it is
    written when the session holding those packages dies, so a manifest younger than the session you
    killed is yours. Nothing else here can know that, which is exactly why it is a parameter and not
    a heuristic.
    """
    # RESOLVE AND SAY SO. A function that deletes somebody's recovery offer must name the file it is
    # about to write, even when quiet - `quiet=True` inside a bare `except: pass` was how the wrong
    # project got written with nothing printed and nothing raised.
    path, source = (path, "given by the caller") if path else default_manifest()
    if not quiet:
        print("restore manifest: %s" % path)
        print("                  from %s" % source)
    names = read_entries(path)
    if not names:
        return False, 0, []
    offenders = [n for n in names if not is_scratch(n)]
    if offenders and not force:
        if not quiet:
            print("REFUSING to clear the restore list: %d of %d entries are NOT scratch."
                  % (len(offenders), len(names)))
            for n in sorted(set(offenders))[:10]:
                print("    %s" % n)
            print("  Those are real unsaved packages. Open the editor and answer the prompt by hand,")
            print("  or call clear(force=True, why='...') if you have established they are yours -")
            print("  the manifest's mtime tells you which session wrote it.")
        return False, len(names), offenders
    if offenders and force:
        if not why:
            raise ValueError(
                "clear(force=True) requires why= - a forced discard with no recorded reason is the "
                "silent discard this guard exists to prevent")
        if not quiet:
            print("FORCED clear over %d non-scratch entry/entries. Reason given: %s"
                  % (len(offenders), why))
            for n in sorted(set(offenders)):
                print("    discarding restore offer for  %s" % n)

    # TIMESTAMPED, because a fixed name made the backup a lie the second time it was used.
    #
    # Until 2026-09-01 this wrote ".bak-forced-clear" flat. force= stakes its entire safety
    # argument on "still backs up first" - that is the sentence that makes discarding somebody
    # else's unsaved work acceptable - and a fixed name means the SECOND forced clear silently
    # destroys the first one's evidence. It did: a forced clear today overwrote the 21718-byte
    # manifest saved on 2026-08-30, which is not recoverable.
    #
    # The neighbouring backups in this directory have carried timestamps for weeks
    # (.bak-091545, .moved-011215). Only the two written by THIS file did not, and they are the
    # two that matter most, because they are the ones taken when something is being thrown away.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = path + ("%s-%s" % (".bak-forced-clear" if offenders else ".bak-scratch-clear", stamp))
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
    # COUNTED SEPARATELY so folding editor furniture into is_scratch does not quietly inflate the
    # scratch number - the report should still say what it is actually looking at.
    furniture = [n for n in names if is_editor_furniture(n)]
    real_scratch = [n for n in scratch if not is_editor_furniture(n)]
    print("restore list holds %d package(s): %d scratch, %d engine editor furniture, %d other"
          % (len(names), len(real_scratch), len(furniture),
             len(names) - len(real_scratch) - len(furniture)))
    cleared, _, offenders = clear()
    return 2 if offenders else (0 if cleared else 1)


if __name__ == "__main__":
    sys.exit(main())
