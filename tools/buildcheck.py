"""Decide whether an Unreal build actually succeeded. Do not eyeball a build log.

WHY THIS IS A FILE AND NOT A GREP. Every build tonight was verified with `grep -c ": error "`, and on
2026-08-27 that reported ZERO ERRORS for a build whose log contained:

    MifBridgePCG.cpp(42): fatal error C1083: Cannot open include file: 'EditorActorSubsystem.h'

`: fatal error ` does not contain `: error `. The build had failed, five endpoints were missing from
the DLL, and the check said clean. I then spent three rounds hunting for why the DLL had not linked -
because the log had already told me, and my filter had thrown the message away.

THREE INDEPENDENT SIGNALS, because each one alone has been wrong here before:

  1. Any line matching `error <code>`, `fatal error`, or `LNK<n>`. Fatal and link errors are the two
     shapes a naive error grep misses, and both have cost time on this project.
  2. `Result: Failed` anywhere in the log. Build.bat has been observed EXITING 0 on a build printing
     exactly that, so the process exit code is not consulted at all.
  3. The expected binary's mtime moved. A build that compiles nothing and links nothing is a success
     to UBT and a no-op to everyone else - and a failed LINK deletes the DLL outright, so its absence
     is a distinct failure worth naming.

Usage:
    python tools/buildcheck.py <log> [--dll <path>] [--since <epoch-seconds>]

Exit 0 means the build really succeeded.
"""
import io
import os
import re
import sys

# Deliberately three alternatives rather than one clever pattern. "error C2065", "fatal error C1083"
# and "LNK2019" are different shapes, and the whole point of this file is that trying to catch them
# with one loose substring is what failed.
ERROR_RE = re.compile(r"(fatal error|error [A-Z]?\d+|LNK\d+)", re.IGNORECASE)

# Lines that contain an error-shaped token but are not errors. Kept SHORT and specific: a generous
# ignore list would recreate the original bug in a new place.
IGNORE_RE = re.compile(
    r"(warning|\[Upgrade\]|Suppress this message|error C4996)",  # C4996 is the deprecation warning
    re.IGNORECASE)


def problems(log_path, dll=None, since=None):
    out = []
    try:
        text = io.open(log_path, encoding="utf-8", errors="replace").read()
    except Exception as exc:
        return ["could not read the log: %s" % exc]

    seen = set()
    for line in text.split("\n"):
        if not ERROR_RE.search(line):
            continue
        if IGNORE_RE.search(line):
            continue
        key = line.strip()[:170]
        if key and key not in seen:
            seen.add(key)
            out.append(key)

    if "Result: Failed" in text:
        out.append('the log says "Result: Failed" - Build.bat has been seen exiting 0 on this')

    if dll:
        if not os.path.isfile(dll):
            out.append("the expected binary does not exist: %s (a failed LINK deletes it)" % dll)
        elif since is not None and os.path.getmtime(dll) <= float(since):
            out.append("the binary's mtime did NOT move - nothing was actually linked")
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    log = args[0]
    dll = args[args.index("--dll") + 1] if "--dll" in args else None
    since = args[args.index("--since") + 1] if "--since" in args else None

    found = problems(log, dll, since)
    if not found:
        print("BUILD OK")
        return 0
    print("BUILD NOT OK - %d problem(s):" % len(found))
    for f in found[:20]:
        print("  " + f)
    return 1


if __name__ == "__main__":
    sys.exit(main())
