"""Check every `File.cpp:NNN` citation against every installed engine.

WHY THIS EXISTS. This repo cites engine source constantly - 4,300+ checkable citations across 900
distinct files - and MifBridgeEndpointRegistry.h names the cost precisely, having been burned by
seven of its own:

    "a wrong citation is the MECHANISM of the duplicate-helper bug class - the next reader jumps to
     the cited line, finds nothing, and writes a local copy."

That is not a stylistic complaint. MifKismetReconstructor's drifted `KrJBool` came from exactly that
sequence, and cost 13 parameters that silently ignored valid input until 2026-08-31.

WHAT IT CHECKS, and why it is only the cheap half. A citation is WRONG if the line does not exist -
line 3376 of a 3200-line file, no judgement required. It does NOT check that the cited line says what
the prose claims; that needs a symbol and a window, which audit_modals does for its three FOUNDATIONS
entries and which is worth generalising later. Past-EOF is decisive and free, and it is enough to
find the class this exists for.

THE TRAP THAT MAKES A NAIVE VERSION USELESS. MifBridge supports UE 5.3 through 5.7, and five engines
are installed on this machine. Checking against ONE of them reports every citation aimed at a
different version as broken. The first run of this check did exactly that: four "past EOF" citations,
every one of them CORRECT on a later engine -

    ICollectionManager.h:426        5.3 has 361 lines, 5.6/5.7 have 508
    Landscape.h:664                 5.3 has 49, 5.6 has 826, 5.7 has 806
    Level.h:1398                    5.3 has 1387, 5.6 has 1692
    LandscapeLayerInfoObject.h:140  5.3 has 78, 5.6 has 75, 5.7 has 271

So the tool reports two DIFFERENT things, and the difference is the whole point:

  DEAD      the line exists on NO installed engine and in no repo file. Real rot.
  AMBIGUOUS the line exists on some engines and not others, and the citation does not say which.
            A reader on the wrong version jumps to nothing, or - worse - to a line that exists and
            says something unrelated. The repo already has the convention that fixes it, used in
            MifBridgeNiagara.cpp: "NiagaraParameterStore.h 5.3 :527 / 5.7 :562". Name the engine, or
            cite a symbol instead of a line.

Only DEAD fails the run. AMBIGUOUS is reported and does not, because a citation aimed squarely at
the engine the author was building against is useful even unqualified, and turning 4,300 of them
into a gate on the day it ships would be a tax rather than a check.

Usage:
    python tools/audit_citations.py           # report
    python tools/audit_citations.py --all     # list every ambiguous one, not just a sample
"""
import collections
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CITE = re.compile(r"\b([A-Z][A-Za-z0-9_]+\.(?:cpp|h))\s*:\s*(\d{2,5})\b")
SKIP_DIRS = (".git", "Intermediate", "Binaries", "DerivedDataCache", "Saved")

# An engine root is anything with Engine/Source under it. Both the source fork this project builds
# against and the launcher installs count - a citation may legitimately aim at any of them.
ENGINE_ROOTS = [
    r"D:/UE532",
    r"C:/Program Files/Epic Games/UE_5.3",
    r"C:/Program Files/Epic Games/UE_5.6",
    r"C:/Program Files/Epic Games/UE_5.7",
]

# A citation that already names its engine is answering the question this tool asks.
QUALIFIED = re.compile(r"\b5\.\d\b")
QUALIFY_WINDOW = 60


def engines():
    return [(os.path.basename(r.rstrip("/\\")), os.path.join(r, "Engine", "Source"))
            for r in ENGINE_ROOTS if os.path.isdir(os.path.join(r, "Engine", "Source"))]


def index_of(root):
    idx = collections.defaultdict(list)
    for base, _d, fs in os.walk(root):
        for fn in fs:
            if fn.endswith((".cpp", ".h")):
                idx[fn].append(os.path.join(base, fn))
    return idx


_lines = {}


def nlines(path):
    if path not in _lines:
        try:
            with io.open(path, "rb") as f:
                _lines[path] = f.read().count(b"\n") + 1
        except OSError:
            _lines[path] = 0
    return _lines[path]


def repo_files():
    for base, _d, fs in os.walk(ROOT):
        if any(s in base for s in SKIP_DIRS):
            continue
        for fn in fs:
            if fn.endswith((".cpp", ".h", ".md", ".py")):
                yield os.path.join(base, fn)


def main():
    show_all = "--all" in sys.argv
    eng = engines()
    if not eng:
        print("no engine source found - cannot check citations. This exits 0 deliberately:")
        print("'could not check' is not 'is wrong'.")
        return 0

    print("engines: %s" % ", ".join(name for name, _ in eng))
    indexes = [(name, index_of(path)) for name, path in eng]
    own = index_of(ROOT)

    checked = unknown = 0
    dead, ambiguous = [], []
    for p in repo_files():
        try:
            text = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        for m in CITE.finditer(text):
            target, want = m.group(1), int(m.group(2))
            # HOMES are the sources that HAVE a file by that name; HOLDERS are the subset whose
            # copy is long enough. Ambiguity is only possible where those differ AND there was more
            # than one home to begin with.
            #
            # The first version compared holders against every installed engine and reported 867
            # false positives in one run - almost all of them repo files like MifBridgeServer.cpp,
            # which exist in the repo and in no engine, so "only on repo" was flagged as ambiguous
            # when it is the single possible resolution. A citation cannot be ambiguous between
            # places the file does not exist.
            homes, holders = [], []
            if own.get(target):
                homes.append("repo")
                if any(want <= nlines(c) for c in own[target]):
                    holders.append("repo")
            for name, idx in indexes:
                cands = idx.get(target)
                if not cands:
                    continue
                homes.append(name)
                if any(want <= nlines(c) for c in cands):
                    holders.append(name)
            if not homes:
                unknown += 1
                continue
            checked += 1
            if not holders:
                dead.append((rel, text[:m.start()].count("\n") + 1, target, want))
            elif len(homes) > 1 and len(holders) < len(homes):
                near = text[max(0, m.start() - QUALIFY_WINDOW): m.end() + QUALIFY_WINDOW]
                if not QUALIFIED.search(near):
                    ambiguous.append((rel, text[:m.start()].count("\n") + 1,
                                      target, want, ",".join(holders)))

    print("checked %d citation(s); %d name a file not present anywhere (engine module not "
          "installed, or a game file)" % (checked, unknown))
    print("")
    if dead:
        print("DEAD - the line exists on NO installed engine and in no repo file:")
        for rel, ln, target, want in dead:
            print("  %s:%d  cites %s:%d" % (rel, ln, target, want))
        print("")
    if ambiguous:
        print("AMBIGUOUS - valid on SOME engines and the citation does not say which (%d):"
              % len(ambiguous))
        for row in (ambiguous if show_all else ambiguous[:15]):
            print("  %s:%d  cites %s:%d  - only on %s" % row)
        if not show_all and len(ambiguous) > 15:
            print("  ... %d more; --all to list them" % (len(ambiguous) - 15))
        print("")
        print("  Name the engine, as MifBridgeNiagara.cpp does:")
        print("    NiagaraParameterStore.h 5.3 :527 / 5.7 :562")
        print("  or cite the SYMBOL instead of the line. Ambiguous citations do not fail this run.")
    if not dead and not ambiguous:
        print("OK  every citation resolves on at least one installed engine, and none is")
        print("    version-ambiguous without saying so.")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
