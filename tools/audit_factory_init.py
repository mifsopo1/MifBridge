"""Which asset classes need more than a bare NewObject, and does create_asset do it?

WHY THIS EXISTS. create_asset accepts any concrete UObject class and constructs it with a bare
NewObject. That is correct for the many classes whose default state is usable, and WRONG - sometimes
fatally - for the ones the engine only ever creates through a UFactory that does further work.

It has now bitten twice:

  ULevelSequence   2026-08-28  a bare NewObject is MALFORMED - no UMovieScene sub-object, so every
                               Sequencer endpoint fails on it. Fixed by calling Initialize().
  UUserDefinedEnum 2026-08-30  a bare NewObject is FATAL - CppForm stays Regular and the first
                               operation naming an enumerator hits
                               check(CppForm == ECppForm::Namespaced) and TERMINATES the editor.
                               Fixed by SetEnums(empty, Namespaced) + SetMetaData("BlueprintType").

Twice is a pattern rather than two accidents, so this looks for the rest instead of waiting for the
third to be found the same way - by an editor dying.

HOW IT WORKS, and what it can and cannot tell you. It reads the engine's UFactory sources, finds
each FactoryCreateNew body, and reports the ones that CALL SOMETHING on the object after
constructing it. A factory that only news-and-returns needs nothing from us; one that then calls
Initialize, SetEnums, AddDefault-anything or assigns a sub-object is telling you the default state
is not a usable asset.

This is a HEURISTIC and reports candidates, not defects. It cannot tell whether a given call is
load-bearing or cosmetic - only a human reading the factory can - so it prints the actual lines and
leaves the judgement. What it does guarantee is that nobody has to guess WHICH classes to look at.

USAGE
    python tools/audit_factory_init.py            # engine factories with post-construct work
    python tools/audit_factory_init.py --class UNiagaraSystem
"""
import argparse
import io
import os
import re
import sys

ENGINE_ROOTS = [
    r"D:/UE532/Engine/Source/Editor",
    r"D:/UE532/Engine/Plugins",
]

# A bare `return NewObject<...>(...)` factory needs nothing from us. These are the shapes that say
# "the default state is not a usable asset".
INTERESTING = re.compile(
    r"->(Initialize|SetEnums|SetMetaData|SetPreviewMesh|AddDefault|Init|CreateDefault|"
    r"SetSkeleton|SetSequence|SetParent|SetSkeletalMesh|SetStaticMesh|MarkPackageDirty|"
    r"PostEditChange|SetFlags|AddSection|SetRowStruct|SetStructure|SetupDefault)")
NEWOBJ = re.compile(r"NewObject\s*<\s*([A-Z][A-Za-z0-9_]*)")


def factory_bodies(text):
    """Yield (start_line, body) for each FactoryCreateNew in a file."""
    for m in re.finditer(r"UObject\*\s+(\w+)::FactoryCreateNew\s*\(", text):
        start = m.start()
        brace = text.find("{", m.end())
        if brace < 0:
            continue
        depth, i = 0, brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield m.group(1), text[:start].count("\n") + 1, text[brace:i + 1]


def scan(only_class=None):
    rows = []
    for root in ENGINE_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith("Factory.cpp") and "Factory" not in name:
                    continue
                if not name.endswith(".cpp"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    text = io.open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for factory, line, body in factory_bodies(text):
                    made = NEWOBJ.findall(body)
                    if not made:
                        continue
                    cls = made[0]
                    if only_class and only_class.lstrip("U") != cls.lstrip("U"):
                        continue
                    calls = sorted(set(INTERESTING.findall(body)))
                    if not calls:
                        continue
                    rows.append((cls, factory, path, line, calls, body))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--class", dest="only", default=None,
                    help="report only this asset class")
    ap.add_argument("--show-body", action="store_true",
                    help="print the factory body for each hit")
    args = ap.parse_args()

    rows = scan(args.only)
    rows.sort(key=lambda r: r[0])

    # What create_asset already handles, so the report says what is LEFT rather than what exists.
    handled = {"ULevelSequence", "UUserDefinedEnum"}

    print("Asset classes whose engine factory does work AFTER its NewObject")
    print("=" * 78)
    if not rows:
        print("no factories matched." if args.only else "nothing found - check ENGINE_ROOTS.")
        return 0

    unhandled = 0
    for cls, factory, path, line, calls, body in rows:
        mark = "  handled" if cls in handled else "  NOT HANDLED"
        if cls not in handled:
            unhandled += 1
        print("\n%-28s %s" % (cls, mark))
        print("    %s:%d  (%s)" % (path.replace("\\", "/"), line, factory))
        print("    calls after construction: %s" % ", ".join(calls))
        if args.show_body:
            for bl in body.splitlines():
                if INTERESTING.search(bl) or "NewObject" in bl:
                    print("        %s" % bl.strip()[:110])

    print("\n" + "=" * 78)
    print("%d factory/factories do post-construct work; %d of those classes are NOT handled by "
          "create_asset." % (len(rows), unhandled))
    print()
    print("This is a HEURISTIC and these are CANDIDATES, not defects. A call here may be cosmetic")
    print("or may be the difference between an asset and a crash - only reading the factory says")
    print("which. What it removes is the guessing about where to look.")
    print()
    print("The two already found the hard way, for calibration:")
    print("  ULevelSequence    Initialize()                     -> malformed without it")
    print("  UUserDefinedEnum  SetEnums(.., Namespaced)         -> TERMINATES the editor without it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
