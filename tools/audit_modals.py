"""Find engine calls that can open a MODAL DIALOG, and whether MifBridge guards them.

Why this exists. The sweep recorded duplicate_asset as a critical crasher; it was not a crash, it was
a modal dialog. Handlers run synchronously inline on the game thread, which is the same thread the
HTTP server answers on, so a modal stops the bridge answering anything at all. From outside that is
indistinguishable from a crash - and it is worse than a crash, because the editor looks alive.

The belief that caused it was written at two call sites as a reassuring comment: "headless - no
dialog". It is wrong in a specific, general way, and that is the rule this tool encodes:

    In AssetTools and ObjectTools the "no dialog" flag suppresses the PICKER, never the VALIDATION.

IAssetTools::DuplicateAsset really does pass bWithDialog=false, but that flag only reaches the
OVERWRITE prompt at the very end. PerformDuplicateAsset calls CanCreateAsset first, which calls
FMessageDialog::Open unconditionally. ObjectTools::DeleteAssets(bShowConfirmation:false) is the same
shape - ObjectTools.cpp:2833 is not gated by that flag at all.

Two things are checked:

  1. CITATION DRIFT. Every entry names the engine file and line that proves it can prompt, and the
     expected text there. If the engine moves, this fails loudly instead of quietly describing an
     older UE. A claim about engine behaviour that nothing re-checks is exactly the add_timeline
     failure - a confident comment that reads like a tested one.

  2. UNGUARDED CALL SITES. Each call in MifBridge is reported as guarded or not. The guard is
     TGuardValue<bool>(GIsRunningUnattendedScript, true): FMessageDialog::Open shows UI only when
     !FApp::IsUnattended() && !GIsRunningUnattendedScript (MessageDialog.cpp:172) and otherwise logs
     and returns the DEFAULT - No for a YesNo, so a destructive prompt is declined rather than waited
     on.

LIMITATION, stated rather than hidden: guard detection is lexical and only looks within the enclosing
function. A call wrapped by a guard in a CALLER is reported unguarded. That errs toward false alarms,
which is the right direction for this particular bug.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "Source", "MifBridge", "Private")
ENGINE = os.environ.get("MIF_ENGINE", "D:/UE532/Engine")

GUARD = "GIsRunningUnattendedScript"

# (call to look for in MifBridge, engine file, line, text that must be at/near that line, why)
PROMPTERS = [
    ("AssetTools.DuplicateAsset",
     "Source/Developer/AssetTools/Private/AssetTools.cpp", 4294, "FMessageDialog::Open",
     "PerformDuplicateAsset -> CanCreateAsset prompts on invalid name, map clash, or existing target; "
     "bWithDialog only reaches the later overwrite prompt"),
    ("AssetTools.RenameAssets",
     "Source/Developer/AssetTools/Private/AssetTools.cpp", 4294, "FMessageDialog::Open",
     "same CanCreateAsset validation path as duplicate"),
    ("ObjectTools::DeleteAssets",
     "Source/Editor/UnrealEd/Private/ObjectTools.cpp", 2833, "FMessageDialog::Open",
     "DeleteObjects prompts when the OnAssetsCanDelete delegate vetoes - NOT gated by "
     "bShowConfirmation"),
    ("ObjectTools::DuplicateSingleObject",
     "Source/Editor/UnrealEd/Private/ObjectTools.cpp", 866, "ReplaceExistingObjectInPackage_F",
     "the destructive overwrite prompt: 'the existing object will be deleted'"),
    ("PromptUserIfExistingObject",
     "Source/Editor/UnrealEd/Private/Dialogs/Dialogs.cpp", 931, "ReplaceExistingObjectInPackage_F",
     "always prompts - there is no suppress flag on this one at all"),
]

# THE SECOND DIALOG CLASS. Everything in PROMPTERS above is FMessageDialog::Open, which
# GIsRunningUnattendedScript neutralises directly. FSuppressableWarningDialog is a DIFFERENT class on
# a DIFFERENT path - it calls GEditor->EditorAddModalWindow itself - and modelling only the first is
# how set_variable_type shipped able to hang the editor on a three-call sequence (PM-011).
#
# It has its own escape hatch, and it is a better one: ShowModal() reads [SuppressableDialogs]<Key>
# from GEditorPerProjectIni BEFORE showing anything and returns Suppressed when set, which both
# engine verify-functions treat as CONSENT. So this class can be made to PROCEED, where the
# unattended guard can only make a dialog CANCEL (= the operation is refused).
#
# Three states are therefore worth distinguishing, not two:
#   suppressed  - FMifScopedDialogSuppression: the operation happens. Best.
#   cancelled   - GIsRunningUnattendedScript: no hang, but the operation is silently refused.
#   UNGUARDED   - the bridge hangs until a human clicks.
#
# (call in MifBridge, engine file, line, text proving it prompts, ini key, why)
SUPPRESSIBLE = [
    ("ChangeMemberVariableType",
     "Source/Editor/UnrealEd/Private/Kismet2/BlueprintEditorUtils.cpp", 5035,
     "VerifyUserWantsVariableTypeChanged", "ChangeVariableType_Warning",
     "prompts whenever the variable has ANY referencing node, in this Blueprint or a loaded CHILD "
     "Blueprint - which is the normal case, not an edge one"),
    ("ChangeLocalVariableType",
     "Source/Editor/UnrealEd/Private/Kismet2/BlueprintEditorUtils.cpp", 5605,
     "VerifyUserWantsVariableTypeChanged", "ChangeVariableType_Warning",
     "the local-variable sibling of the same guard, same dialog key"),
    ("RenameMemberVariable",
     "Source/Editor/UnrealEd/Private/Kismet2/BlueprintEditorUtils.cpp", 4837,
     "VerifyUserWantsRepNotifyVariableNameChanged", "ChangeRepNotifyVariableName_Warning",
     "prompts when the variable has a RepNotify function; declining makes the engine REVERT the "
     "name, so suppressing is not obviously right here and rename_variable refuses instead"),
]

SUPPRESS_GUARD = "FMifScopedDialogSuppression"

# Engine facts the whole approach rests on. If either moves, the guard reasoning needs re-checking.
FOUNDATIONS = [
    ("Source/Runtime/Core/Private/Misc/MessageDialog.cpp", 172,
     "if (!FApp::IsUnattended() && !GIsRunningUnattendedScript)",
     "the guard works ONLY because Open() short-circuits here and returns the default"),
    ("Source/Editor/UnrealEd/Private/Dialogs/Dialogs.cpp", 832,
     "GConfig->GetBool( *ConfigSection, *IniSettingName",
     "FSuppressableWarningDialog reads the ini FIRST and returns Suppressed without showing - the "
     "whole basis for FMifScopedDialogSuppression"),
    ("Source/Runtime/Slate/Private/Framework/Application/SlateApplication.cpp", 1990,
     "GIsRunningUnattendedScript && !bSlowTaskWindow",
     "why the unattended guard also stops a suppressible dialog - AddModalWindow cancels it, so the "
     "operation is refused rather than hung"),
]


def engine_line(rel, lineno):
    p = os.path.join(ENGINE, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    return lines[lineno - 1] if 0 < lineno <= len(lines) else ""


def near(rel, lineno, needle, radius=6):
    """Citations drift by a line or two across engine patches; a small window is honest, not sloppy."""
    for ln in range(max(1, lineno - radius), lineno + radius + 1):
        s = engine_line(rel, ln)
        if s is None:
            return None
        if needle in s:
            return ln
    return False


def is_in_string_literal(line, name):
    """True when every occurrence of `name` on this line sits inside a quoted string.

    Error messages name the engine functions they are about - TEXT("ChangeLocalVariableType needs the
    generated function to exist.") is prose, not a call, and reporting it as an unguarded call site is
    noise that trains the reader to skim real findings.
    """
    for m in re.finditer(re.escape(name), line):
        if line.count('"', 0, m.start()) % 2 == 0:
            return False          # an even number of quotes before it: this one is real code
    return True


def enclosing_function_start(lines, idx):
    """Nearest preceding line that looks like a function definition at namespace indent."""
    for i in range(idx, -1, -1):
        s = lines[i]
        if re.match(r"^\t?(?:void|bool|int32|UObject\*|UEdGraph\*|AActor\*|FString|TSharedPtr)\s+\w+\(", s):
            return i
        if re.match(r"^\tvoid H_\w+\(", s):
            return i
    return max(0, idx - 60)


def main():
    print("=" * 78)
    print("CITATIONS  (a claim about the engine that nothing re-checks is just a comment)")
    print("=" * 78)
    drift = 0
    for rel, lineno, needle, why in FOUNDATIONS:
        found = near(rel, lineno, needle)
        if found is None:
            print("  SKIP  %s (engine not found at %s)" % (rel, ENGINE))
        elif found is False:
            drift += 1
            print("  DRIFT %s:%d no longer contains %r\n        %s" % (rel, lineno, needle, why))
        else:
            print("  ok    %s:%d  %s" % (rel, found, why))

    seen = set()
    for call, rel, lineno, needle, why in PROMPTERS:
        key = (rel, lineno, needle)
        if key in seen:
            continue
        seen.add(key)
        found = near(rel, lineno, needle)
        if found is None:
            print("  SKIP  %s (engine not found)" % rel)
        elif found is False:
            drift += 1
            print("  DRIFT %s:%d no longer contains %r" % (rel, lineno, needle))
        else:
            print("  ok    %s:%d  %s" % (rel, found, needle))

    for call, rel, lineno, needle, key, why in SUPPRESSIBLE:
        found = near(rel, lineno, needle)
        if found is None:
            print("  SKIP  %s (engine not found)" % rel)
        elif found is False:
            drift += 1
            print("  DRIFT %s:%d no longer contains %r" % (rel, lineno, needle))
        else:
            print("  ok    %s:%d  %s -> [%s]" % (rel, found, needle, key))

    print()
    print("=" * 78)
    print("CALL SITES IN MIFBRIDGE")
    print("=" * 78)
    unguarded = []
    guarded = 0
    for fname in sorted(os.listdir(SRC)):
        if not fname.endswith(".cpp"):
            continue
        path = os.path.join(SRC, fname)
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue          # the module documents these APIs at length; comments are not calls
            for call, rel, lineno, needle, why in PROMPTERS:
                if call not in line or is_in_string_literal(line, call):
                    continue
                start = enclosing_function_start(lines, i)
                body = "\n".join(lines[start:i])
                is_guarded = GUARD in body and "TGuardValue" in body
                if is_guarded:
                    guarded += 1
                    print("  guarded    %s:%d  %s" % (fname, i + 1, call))
                else:
                    unguarded.append((fname, i + 1, call, why))
                    print("  UNGUARDED  %s:%d  %s" % (fname, i + 1, call))
                    print("             %s" % why)
            for call, rel, lineno, needle, key, why in SUPPRESSIBLE:
                if call not in line or is_in_string_literal(line, call):
                    continue
                start = enclosing_function_start(lines, i)
                body = chr(10).join(lines[start:i])
                if SUPPRESS_GUARD in body:
                    guarded += 1
                    print("  suppressed %s:%d  %s  [%s] - proceeds" % (fname, i + 1, call, key))
                elif "RepNotifyFunc != NAME_None" in body and "Fail(" in body:
                    # Refused before the call is reached. That is the other legitimate close, and for
                    # the RepNotify dialog specifically it is the RIGHT one: declining that dialog makes
                    # the engine revert the name, so suppressing it would report a rename that did not
                    # happen.
                    guarded += 1
                    print("  refused    %s:%d  %s - the modal path is unreachable" % (fname, i + 1, call))
                elif GUARD in body and "TGuardValue" in body:
                    # Safe from hanging, but the engine treats a cancelled dialog as "no": the
                    # operation is silently refused. Worth naming rather than counting as fine.
                    guarded += 1
                    print("  cancelled  %s:%d  %s - no hang, but the operation is REFUSED"
                          % (fname, i + 1, call))
                else:
                    unguarded.append((fname, i + 1, call, why))
                    print("  UNGUARDED  %s:%d  %s   (suppressible dialog [%s])"
                          % (fname, i + 1, call, key))
                    print("             %s" % why)

    print()
    print("=" * 78)
    print("guarded %d   unguarded %d   citation drift %d" % (guarded, len(unguarded), drift))
    if unguarded:
        print()
        print("An unguarded prompter freezes the bridge, it does not fail it.")
        print("  FMessageDialog::Open        -> TGuardValue<bool>(GIsRunningUnattendedScript, true)")
        print("  FSuppressableWarningDialog  -> FMifScopedDialogSuppression(TEXT(\"<key>\"))")
        print("The second one makes the operation PROCEED; the unattended guard would only cancel it,")
        print("which turns a hang into a silent refusal. Where the condition is predictable, check it")
        print("first so the caller gets a real reason instead of a generic failure.")
    print("=" * 78)
    return 1 if (unguarded or drift) else 0


if __name__ == "__main__":
    sys.exit(main())
