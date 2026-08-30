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

--dll defaults to this plugin's own binary, so signal 3 runs unless you point it elsewhere. Pass
--since with the epoch seconds from JUST BEFORE the build to make it prove the binary relinked;
without it the mtime is not checked and the BUILD OK line says so rather than implying otherwise.

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


# The binary this plugin builds. Defaulted rather than left to the caller because it being optional
# is what broke: `if dll:` meant the mtime signal - one of the three the docstring above calls
# independent - did nothing at all unless someone remembered --dll. On 2026-08-30 that reported
# BUILD OK for an invocation whose project path was malformed, so UnrealBuildTool never compiled
# anything: no error-shaped line, no "Result: Failed", and the one check that would have caught it
# switched off by default. That is precisely the class of miss this file exists to prevent, so the
# check is now on unless it genuinely cannot be resolved, and saying so is not optional.
DEFAULT_DLL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "Binaries", "Win64", "UnrealEditor-MifBridge.dll")


def problems(log_path, dll=None, since=None):
    out = []
    try:
        text = io.open(log_path, encoding="utf-8", errors="replace").read()
    except Exception as exc:
        return ["could not read the log: %s" % exc], []

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

    # SIGNAL 3, and it reports what it did rather than quietly doing nothing. `ran` is returned so
    # the caller can print which checks were really performed - "BUILD OK" from one signal out of
    # three is a different claim from "BUILD OK" from all three, and conflating them is the bug.
    ran = ["error lines", '"Result: Failed"']
    if not os.path.isfile(dll):
        out.append("the expected binary does not exist: %s (a failed LINK deletes it)" % dll)
        ran.append("binary present (FAILED)")
    elif since is None:
        # Not an error - a log alone is a legitimate use - but it must not read as a full pass.
        ran.append("binary present (mtime NOT checked - pass --since <epoch> to prove it relinked)")
    elif os.path.getmtime(dll) <= float(since):
        out.append("the binary's mtime did NOT move - nothing was actually linked. The build may "
                   "have failed before compiling, or never started: check the log's last lines for "
                   "a bad argument or project path, which produce no error-shaped output at all.")
        ran.append("binary mtime (FAILED)")
    else:
        ran.append("binary mtime")
    return out, ran


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    log = args[0]
    dll = args[args.index("--dll") + 1] if "--dll" in args else DEFAULT_DLL
    since = args[args.index("--since") + 1] if "--since" in args else None

    found, ran = problems(log, dll, since)
    if not found:
        # SAY WHAT WAS CHECKED. A bare "BUILD OK" is what let a no-op build pass as a real one.
        print("BUILD OK  (checked: %s)" % "; ".join(ran))
        return 0
    print("BUILD NOT OK - %d problem(s):" % len(found))
    for f in found[:20]:
        print("  " + f)
    return 1


if __name__ == "__main__":
    sys.exit(main())
