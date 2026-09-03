"""Parse changed C++ against BOTH engines without linking, and without closing anybody's editor.

WHY THIS EXISTS. Verifying a C++ change here has always meant a full build, and a full build needs
the editor closed, because a running editor holds MifBridge.dll and the link cannot replace it. So
every C++ edit made while somebody is using the editor was unverifiable until they stopped. That is
also why `register_landscape_layer` sat uncompilable on 5.7 for two days: the 5.3 build was clean,
and checking the other engine meant a full probe build nobody was going to run for one endpoint.

`cl /Zs` parses a translation unit and writes NOTHING - no object, no PCH, no touch of any DLL or
intermediate tree. It is safe beside a live editor, it takes seconds per file, and it catches the
entire class of bug gotchas 14 is about: a symbol that exists in one engine and not the other.

WHAT IT DOES NOT DO. A parse is not a link. It says nothing about unresolved externals, and it is
NOT the 5.3 or 5.7 release gate - both of those want a real build and neither should be recorded on
the strength of this. It answers one question: does this file still compile against that engine's
headers.

HOW IT WORKS, and every step of this was wrong on the first attempt:
  - It borrows a response file from a PREVIOUS build (UBT leaves one per file, or per unity blob),
    rewrites the source line to the real file, drops the output flags, and appends /Zs.
  - The module's own Private/Public dirs are PREPENDED as include paths, so headers resolve to the
    current tree rather than to whatever copy the donor build used.
  - The PCH is tried first and dropped on C1853, which is what a PCH from a different compiler build
    reports. That failure is identical for every file and looks exactly like a code error.
  - UBT's include paths are RELATIVE, so cl must run with cwd set to <engine>/Engine/Source.
  - The toolchain must match the STL that engine's build used. 14.36 against 5.7's 14.44 headers
    dies in <type_traits>; 14.44 against 5.3 does the same in the other direction.

A DONOR THAT IS MISSING IS REPORTED, NEVER SKIPPED. A checker that cannot run must not print a clean
result - that is the whole failure mode this repo keeps writing postmortems about.

  python tools/syntax_check.py                    changed-vs-HEAD .cpp, both engines
  python tools/syntax_check.py --engine 57        one engine only
  python tools/syntax_check.py MifBridgeNodes4    explicit stems
  python tools/syntax_check.py --plant            prove each engine's checker SEES a known error
"""
import argparse
import atexit
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRCDIR = os.path.join(ROOT, "Source", "MifBridge", "Private").replace("\\", "/")
PUBDIR = os.path.join(ROOT, "Source", "MifBridge", "Public").replace("\\", "/")
INTER = os.path.join(ROOT, "Intermediate", "Build", "Win64", "x64", "UnrealEditor").replace("\\", "/")

VC = "C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Tools/MSVC"

# EVERY SCRATCH FILE GOES OUTSIDE THE REPO. tools/ is tracked, and this writes both a response file
# and - for the plant - a whole .cpp. A `finally` does not survive the process being KILLED, and
# sweeps in this repo have been killed twice, so cleanup must not be the only thing standing between
# a stray file and the working tree.
SCRATCH = tempfile.mkdtemp(prefix="mif_syntax_check_")
atexit.register(shutil.rmtree, SCRATCH, True)

ENGINES = {
    # donor: a response file left by a build for THAT engine. 5.7's probe writes per-file rsps;
    # 5.3's editor build uses unity blobs, so blob 1 is borrowed purely for its flags.
    "53": {
        "label": "UE 5.3",
        "cwd": "D:/UE532/Engine/Source",
        "cl": VC + "/14.36.32532/bin/Hostx64/x64/cl.exe",
        "donor": INTER + "/DebugGame/MifBridge/Module.MifBridge.1.cpp.obj.rsp",
    },
    "57": {
        "label": "UE 5.7",
        "cwd": "C:/Program Files/Epic Games/UE_5.7/Engine/Source",
        "cl": VC + "/14.44.35207/bin/Hostx64/x64/cl.exe",
        "donor": INTER + "/Development/MifBridge/MifBridgeAnimation.cpp.obj.rsp",
    },
}

BASE_DROP = ("/Fo", "/experimental:log", "/sourceDependencies")
NOPCH_DROP = BASE_DROP + ("/Yu", "/Fp", "/Yc")
PLANT_SYMBOL = "MifSyntaxCheckPlantedSymbol"


def changed_stems():
    """.cpp files differing from HEAD - the ones a pre-commit check actually cares about."""
    try:
        out = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", "Source"],
                             capture_output=True, text=True, cwd=ROOT, timeout=60).stdout
    except OSError:
        return []
    stems = []
    for line in out.split("\n"):
        line = line.strip()
        if line.endswith(".cpp"):
            stems.append(os.path.basename(line)[:-4])
    return stems


def build_rsp(eng, stem, drop, source):
    src = io.open(eng["donor"], encoding="utf-8").read()
    out = ['/I"%s"' % SRCDIR, '/I"%s"' % PUBDIR]
    for i, ln in enumerate(src.split("\n")):
        s = ln.strip()
        if not s or any(s.startswith(d) for d in drop):
            continue
        out.append('"%s"' % source if i == 0 else s)
    out.append("/Zs")
    path = os.path.join(SCRATCH, "%s_%s.rsp" % (eng["key"], stem))
    io.open(path, "w", encoding="utf-8").write("\n".join(out))
    return path


def compile_one(eng, stem, source=None):
    """(returncode, error lines). Retries without the PCH when the PCH is from another compiler."""
    source = source or ("%s/%s.cpp" % (SRCDIR, stem))
    rc, errs, text = 1, [], ""
    for drop in (BASE_DROP, NOPCH_DROP):
        rsp = build_rsp(eng, stem, drop, source)
        p = subprocess.run([eng["cl"], "@" + rsp], capture_output=True, text=True,
                           timeout=560, cwd=eng["cwd"])
        text = p.stdout + p.stderr
        rc, errs = p.returncode, [l.strip() for l in text.split("\n") if "error " in l]
        if "C1853" in text and drop is BASE_DROP:
            continue
        break
    return rc, errs


def preflight(eng):
    """Everything that makes a clean result meaningless if absent."""
    problems = []
    if not os.path.isfile(eng["cl"]):
        problems.append("compiler missing: %s" % eng["cl"])
    if not os.path.isdir(eng["cwd"]):
        problems.append("engine source missing: %s" % eng["cwd"])
    if not os.path.isfile(eng["donor"]):
        problems.append("no donor response file - build this engine once first:\n      %s"
                        % eng["donor"])
    return problems


def plant(eng, stem):
    """A clean run is worth nothing until the checker is shown an error it MUST see.

    Counting errors is not enough. An early version of this reported success on a stale-PCH C1853
    that would have fired with no plant at all, so the error has to NAME the planted symbol.
    """
    path = "%s/%s.cpp" % (SRCDIR, stem)
    body = io.open(path, encoding="utf-8").read()
    marker = "\n}"
    if marker not in body:
        return False, "cannot plant into %s" % stem
    bad = os.path.join(SCRATCH, "planted_%s.cpp" % stem)
    io.open(bad, "w", encoding="utf-8").write(
        body + "\nstatic void MifSyntaxCheckPlant() { %s(); }\n" % PLANT_SYMBOL)
    try:
        _rc, errs = compile_one(eng, stem, bad)
    finally:
        os.remove(bad)
    mine = [e for e in errs if PLANT_SYMBOL in e]
    return bool(mine), ("saw the planted symbol" if mine else
                        "did NOT see the plant - a clean result here would mean nothing")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stems", nargs="*", help="file stems, e.g. MifBridgeNodes4. Default: changed.")
    ap.add_argument("--engine", choices=["53", "57", "both"], default="both")
    ap.add_argument("--plant", action="store_true", help="self-test each engine's checker")
    args = ap.parse_args()

    keys = ["53", "57"] if args.engine == "both" else [args.engine]
    engines = []
    for k in keys:
        e = dict(ENGINES[k]); e["key"] = k
        engines.append(e)

    stems = args.stems or changed_stems()
    if not stems and not args.plant:
        print("no changed .cpp under Source/ - nothing to check")
        return 0

    bad = 0
    for eng in engines:
        print("=== %s ===" % eng["label"])
        problems = preflight(eng)
        if problems:
            for p in problems:
                print("  CANNOT RUN: %s" % p)
            print("  NOT REPORTING CLEAN - this engine was not checked.")
            bad += 1
            continue

        if args.plant:
            ok, why = plant(eng, stems[0] if stems else "MifBridgeAnimation")
            print("  plant: %s - %s" % ("SEEN" if ok else "NOT SEEN", why))
            if not ok:
                bad += 1
            continue

        for stem in stems:
            if not os.path.isfile("%s/%s.cpp" % (SRCDIR, stem)):
                print("  %-26s no such file" % stem)
                bad += 1
                continue
            rc, errs = compile_one(eng, stem)
            print("  %-26s %s" % (stem, "clean" if not errs else "%d ERROR(S)" % len(errs)))
            for e in errs[:6]:
                print("       %s" % e[:150])
            if errs:
                bad += 1
        print("")

    print("=" * 72)
    if args.plant:
        # In plant mode NOTHING was checked - saying files parsed would be the exact false-clean
        # this tool refuses to print anywhere else.
        print("%s" % ("A CHECKER DID NOT SEE ITS PLANT - do not trust its clean results" if bad
                      else "every engine's checker saw its planted error; no files were checked"))
    else:
        print("%s" % ("SOMETHING FAILED - read it above" if bad else
                      "all checked files parse against every engine checked"))
        print("A parse is NOT a link, and this is NOT the release gate for either engine.")
    print("=" * 72)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
