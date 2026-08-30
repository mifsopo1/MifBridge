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

TWO SCANS, because one of them was not enough and said so. The factory scan below reads
UFactory::FactoryCreateNew bodies. That is where most asset creation lives, but NOT all of it - and
the miss was not hypothetical: UUserDefinedEnum, the case that TERMINATED the editor, is created by
FEnumEditorUtils::CreateUserDefinedEnum, which is not a factory at all. The factory scan could
never have found the very bug that prompted writing it.

So the second scan reads FooUtils::CreateBar / FooEditorUtils::CreateBar helpers on the same test:
does it construct something and then do more to it? Added 2026-08-30. It found the enum's sibling
on its first run - FStructureEditorUtils::CreateUserDefinedStruct, which does SEVEN things after
its NewObject, and whose EditorData sub-object is CastChecked by every struct entry point, so null
there terminates the editor. create_asset now calls that creator rather than NewObject.

USAGE
    python tools/audit_factory_init.py            # both scans
    python tools/audit_factory_init.py --class UNiagaraSystem
    python tools/audit_factory_init.py --utils    # only the editor-utils scan
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

# Factories whose ONLY post-construct work sits behind a condition create_asset cannot
# satisfy. Kept and reported rather than silently dropped: "we looked and it does not
# apply" is a different statement from "we did not look".
CONDITIONAL_ONLY = []


# The UFactory configuration convention. A factory's caller sets these members before calling
# FactoryCreateNew; create_asset does not call the factory at all, so anything guarded by one is
# unreachable on our path. This - not brace depth - is what separates a real warning from a false
# one, and getting it wrong in the other direction dropped UMaterialInstanceConstant's
# InitResources, which sits inside a plain `if (MIC)` null check and always runs.
FACTORY_CONFIG = re.compile(r"\bInitial[A-Z]\w*|\bRootWidgetClass\b|\bParentClass\b")
IF_LINE = re.compile(r"^\s*(?:\}\s*)?else\s+if\s*\((.*)|^\s*if\s*\((.*)")


def unconditional_calls(body):
    """(reachable, unreachable) INTERESTING calls, split by whether create_asset can get to them.

    A call is UNREACHABLE for us when an enclosing condition tests a UFactory configuration member,
    because create_asset never calls the factory and so never sets one. A self null-check like
    `if (MIC)` is not such a condition, and calls under it ARE reported - that distinction is why
    brace depth alone was the wrong test, and dropping it lost UMaterialInstanceConstant's
    InitResources.

    THE GATE BINDS WHEN THE BRACE OPENS, not when the condition is read. Engine style puts `{` on
    its own line, so on the `if` line the depth has not moved yet; an earlier version registered
    the gate against the depth it expected and then pruned it at the end of that same line for
    being deeper than the current depth. Every gate died instantly and UMaterial kept warning.
    """
    reachable, blocked = [], []
    depth = 0
    gated = {}          # depth -> True when the condition opened there is one we cannot satisfy
    pending = None      # a condition seen whose brace has not opened yet

    for line in body.splitlines():
        m = IF_LINE.match(line)
        cond = (m.group(1) or m.group(2)) if m else None
        if cond is not None:
            pending = bool(FACTORY_CONFIG.search(cond))

        opened = line.count("{")
        closed = line.count("}")

        # A brace opening on this line binds the pending condition to the depth it creates.
        if opened and pending is not None:
            gated[depth + 1] = pending
            pending = None

        # Classify this line's calls against every gate at or above it, INCLUDING a same-line
        # brace-less body such as `if (InitialFoo) Bar->Init();`.
        active = any(gated.get(d) for d in range(1, depth + 1)) or bool(pending)
        for hit in INTERESTING.findall(line):
            (blocked if active else reachable).append(hit)

        depth += opened - closed
        if closed and pending is not None and not opened:
            pending = None
        for d in [k for k in gated if k > depth]:
            del gated[d]

    return sorted(set(reachable)), sorted(set(blocked))


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
                if not name.endswith(".cpp"):
                    continue
                # "Factor", not "Factory". The filter used to test `"Factory" in name`, which
                # excluded EditorFactories.cpp - the single biggest factory file in the engine,
                # holding dozens of them. Found 2026-08-30 when the editor-utils scan turned up a
                # UTextureRenderTarget2D hit that this scan should have reported first: its factory
                # calls InitAutoFormat(256,256), so a bare NewObject leaves a 0x0 render target
                # with no resource. A filename filter that silently drops the main file is worse
                # than no filter, because the report still looks complete.
                if "Factor" not in name:
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
                    calls, conditional = unconditional_calls(body)
                    if not calls:
                        # Nothing runs unconditionally. Reported at the end as a separate,
                        # explicitly weaker class rather than warned about - see the docstring.
                        if conditional:
                            CONDITIONAL_ONLY.append((cls, factory, path, line, conditional))
                        continue
                    rows.append((cls, factory, path, line, calls, body))
    return rows


# A creation helper rather than a factory. Restricted to types whose name ends in Utils /
# Utilities / Library on purpose: that is where the engine puts "the canonical way to make one of
# these", and widening it to every Create* function anywhere drowns the report in graph-node and
# widget construction that has nothing to do with assets.
UTILS_FUNC = re.compile(
    r"^\s*(?:static\s+)?(\w+)\s*\*\s*(F?\w*(?:Utils|Utilities|Library))::(Create\w*)\s*\(", re.M)


def utils_bodies(text):
    """Yield (owner::name, start_line, body) for each FooUtils::CreateBar in a file."""
    for m in UTILS_FUNC.finditer(text):
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
        yield ("%s::%s" % (m.group(2), m.group(3)),
               text[:m.start()].count("\n") + 1, text[brace:i + 1])


def scan_utils(only_class=None):
    rows = []
    for root in ENGINE_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith(".cpp"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    text = io.open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for fn, line, body in utils_bodies(text):
                    made = NEWOBJ.findall(body)
                    if not made:
                        continue
                    cls = made[0]
                    if only_class and only_class.lstrip("U") != cls.lstrip("U"):
                        continue
                    calls, conditional = unconditional_calls(body)
                    if not calls:
                        if conditional:
                            CONDITIONAL_ONLY.append((cls, fn, path, line, conditional))
                        continue
                    rows.append((cls, fn, path, line, calls, body))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--class", dest="only", default=None,
                    help="report only this asset class")
    ap.add_argument("--show-body", action="store_true",
                    help="print the factory body for each hit")
    ap.add_argument("--utils", action="store_true",
                    help="only the editor-utils scan, skipping the factory scan")
    args = ap.parse_args()

    rows = [] if args.utils else scan(args.only)
    rows.sort(key=lambda r: r[0])

    # What create_asset already handles, so the report says what is LEFT rather than what exists.
    # A class comes OFF this list only when create_asset really does the initialisation.
    # UUserDefinedStruct joined it 2026-08-30 when create_asset started calling
    # FStructureEditorUtils::CreateUserDefinedStruct instead of NewObject.
    handled = {"ULevelSequence", "UUserDefinedEnum", "UUserDefinedStruct"}

    print("Asset classes whose engine factory does work AFTER its NewObject")
    print("=" * 78)
    if not rows and not args.utils:
        print("no factories matched." if args.only else "nothing found - check ENGINE_ROOTS.")

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

    # SCAN 2. Kept separate in the output rather than merged, because the two answer different
    # questions and a merged list hides which scan found what - and the factory scan's blind spot
    # is the reason this one exists.
    print()
    print("Creation helpers that are NOT factories (FooUtils::CreateBar)")
    print("=" * 78)
    print("The factory scan above CANNOT see these. UUserDefinedEnum - the case that terminated")
    print("the editor - lives here, not in a factory, so the first scan would have missed it.")
    urows = scan_utils(args.only)
    urows.sort(key=lambda r: r[0])
    if not urows:
        print("\nno helpers matched." if args.only else "\nnothing found - check ENGINE_ROOTS.")
        return 0
    uunhandled = 0
    for cls, fn, path, line, calls, body in urows:
        mark = "  handled" if cls in handled else "  NOT HANDLED"
        if cls not in handled:
            uunhandled += 1
        print("\n%-30s %s" % (cls, mark))
        print("    %s:%d  (%s)" % (path.replace("\\", "/"), line, fn))
        print("    calls after construction: %s" % ", ".join(calls))
        if args.show_body:
            for bl in body.splitlines():
                if INTERESTING.search(bl) or "NewObject" in bl:
                    print("        %s" % bl.strip()[:110])
    print("\n" + "=" * 78)
    print("%d creation helper(s) do post-construct work; %d of those classes are NOT handled."
          % (len(urows), uunhandled))
    print()
    print("Most of these construct transient conversion-context objects rather than assets, so the")
    print("list is short on things create_asset can even be asked for. Read before acting - the")
    print("point is that nobody has to GUESS which classes to look at.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
