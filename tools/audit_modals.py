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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harvest_param_table as H          # the one comment/string scrubber

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



# --------------------------------------------------------------------------- deferred work
# THE GUARD DOES NOT SURVIVE A DEFERRAL, and that is invisible in the source unless you look for it.
# RunEndpoint wraps every handler in TGuardValue<bool>(GIsRunningUnattendedScript, true) so a modal is
# cancelled rather than left hanging the ticker. A TGuardValue RESTORES ON SCOPE EXIT - so a handler
# that schedules its real work for a later tick and returns immediately runs that work with the guard
# already gone.
#
# Six handlers do exactly that, and they must: new_level and load_level swap the UWorld, which cannot
# happen while FTickTaskManager is iterating the level list, and the sublevel mutators defer for the
# same reason. Deferring is right; losing the guard was not noticed until a source hunt found it.
#
# MifDeferToNextTick re-arms the guard inside the lambda. This check exists so the next deferral added
# to this module cannot quietly skip it - the failure would be a modal reaching a caller who cannot
# click it, which is the worst outcome this bridge has (PM-011), and no suite can catch it because
# every one of those endpoints is on the audit harness DENY list.
DEFER_HELPER = "MifDeferToNextTick"
RAW_DEFER = "SetTimerForNextTick"


def check_deferrals():
    """Every deferral must go through the guarded helper. Returns the offending (file, line) list."""
    bad = []
    for fname in sorted(os.listdir(SRC)):
        if not fname.endswith(".cpp"):
            continue
        with open(os.path.join(SRC, fname), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            if RAW_DEFER not in line or is_in_string_literal(line, RAW_DEFER):
                continue
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            # A TRAILING comment counts too. The first version of this check flagged
            #   #include "TimerManager.h"   // SetTimerForNextTick - MifDeferToNextTick
            # because it only looked for a comment at the START of the line. A probe that reports its
            # own documentation as a defect is the kind that gets skimmed past, which is exactly how a
            # real finding gets missed.
            comment = line.find("//")
            if comment != -1 and comment < line.find(RAW_DEFER):
                continue
            # The helper's own definition is the one legitimate raw use.
            if DEFER_HELPER in chr(10).join(lines[max(0, i - 12):i]):
                continue
            bad.append((fname, i + 1, stripped[:90]))
    return bad


# Modal hazards that are NOT reached through a prompter this scan can see, because the dialog is
# opened by engine code several frames down from the call - so the only evidence at our call site is
# that the mitigation is still there. MifBridgeExport.cpp's header says of these three: "Every one is
# fatal if a later edit drops it." Until 2026-08-31 nothing checked that, which made the sentence a
# hope. The FBX exporter reaches FSlateApplication::AddModalWindow via
# FFbxExporter::FillExportOptions (FbxMainExport.cpp:218), and a modal on the game thread freezes the
# HTTP ticker this server runs on - the bridge goes down with no agent able to click OK.
#
# Note what FillExportOptions actually tests to early-return: !bShowOptionDialog ||
# GIsAutomationTesting || FApp::IsUnattended(). NOT GIsRunningUnattendedScript. So the guard the rest
# of this file is about does not help here, and neither does its reasoning.
# COUNTED, not merely present. "Is it in the file?" answers "at least one call site still has it",
# and these are per-CALL-SITE invariants: Task->Options is set at two places (678 and 699), so
# blanking either one left the check green. That was found by mutation-testing the check itself -
# blank the CODE occurrence, confirm it is reported - which is the only way to learn that a green
# checker is asleep. Two of the three were caught; this one was not, and the count is why it is now.
#
# A drop means a call site lost its gate. If a refactor legitimately merges paths, update the number
# here deliberately and say why - the same contract as every baseline in this directory.
# HOW TO FIND MORE OF THESE, because both files were found by reading rather than by pattern:
#
#     grep -rniE "INVARIANT|fatal if|load-bearing|must NOT be removed" Source/MifBridge/Private
#
# A file that declares its own invariants is telling you exactly what to check. That grep is what
# turned up MifBridgeImport.cpp saying "the two invariants that keep this endpoint from taking the
# editor down" - and then, on the file already being checked, that MifBridgeExport.cpp:425 says
# "the THREE invariants" while lines 674-678 mark FOUR lines // INVARIANT. Two of the four had been
# enforced. The prose count and the code count disagreed, and the code was right.
INVARIANTS = [
    ("MifBridgeExport.cpp", r"Task->bPrompt\s*=\s*false", 1,
     "no GWarn->YesNof overwrite dialog (UnrealExporter.cpp:339/:387) - a modal like any other, and "
     "on the same object as the two below it"),
    ("MifBridgeExport.cpp", r"Task->bWriteEmptyFiles\s*=\s*false", 1,
     "NOT a modal, and the one entry here that is not: true would clobber the real FBX with an empty "
     "buffer. Kept in this table because it is declared // INVARIANT in the same block, for the same "
     "reason, and it is one line - a second tool holding a single row would be the worse answer. If "
     "a third non-modal invariant appears, split them"),
    ("MifBridgeExport.cpp", r"Task->bAutomated\s*=\s*true", 1,
     "gate 1 - without it GetAutomatedExportOptionsFbx returns nullptr and the options modal opens"),
    ("MifBridgeExport.cpp", r"Task->Options\s*=", 2,
     "gate 2 - bAutomated alone is NOT enough; a null or wrong-typed Options falls through to the "
     "modal branch. TWO call sites build an export task and both must set it"),
    ("MifBridgeExport.cpp", r"Exporter->SetShowExportOption\s*\(\s*false\s*\)", 1,
     "belt - makes FillExportOptions early-return even if the Cast to UFbxExportOption ever fails. "
     "UExporter's constructor defaults ShowExportOption to TRUE, so omitting this is not neutral"),

    # THE IMPORT SIDE, whose header calls two of these "the two invariants that keep this endpoint
    # from taking the editor down" and the third "the single most load-bearing line in import_asset".
    # Added 2026-08-31 after the export ones, having noticed that the same sentence had been written
    # about a second file and enforced in neither. The hazard is the mirror image: interactive
    # imports raise factory option dialogs.
    ("MifBridgeImport.cpp", r"bAutomated\s*=\s*true", 1,
     "forced TRUE. UAssetToolsImpl::ImportAssetsInternal wraps the import in "
     "TGuardValue<bool>(GIsRunningUnattendedScript, ... || Params.bAutomated) at AssetTools.cpp:3045, "
     "which is what actually suppresses factory option dialogs"),
    ("MifBridgeImport.cpp", r"bAsync\s*=\s*false", 1,
     "forced FALSE. UAssetImportTask::GetObjects() BLOCKS on an async import "
     "(AssetImportTask.h:78), and this server runs handlers synchronously inside the HTTP ticker"),
    ("MifBridgeImport.cpp", r"Task->Factory\s*=", 1,
     "ALWAYS set explicitly, and the file calls this its most load-bearing line. Interchange is "
     "bypassed only when a factory is specified - IsInterchangeImportEnabled() && (SpecifiedFactory "
     "== nullptr), AssetTools.cpp:3068-3071 - so leaving it null lets a PNG or FBX route to ASYNC "
     "Interchange and span frames. Specifying it also skips NewFactory->ConfigureProperties() "
     "(AssetTools.cpp:3140), which is where a factory is allowed to raise UI"),
]


def check_invariants():
    """Each mitigation must be present in CODE. Its own file discusses all three at length.

    Matched against scrubbed source, and that is the entire difficulty. MifBridgeExport.cpp's header
    comment names every one of these three with the same spelling the code uses - so a raw `in src`
    check passes on the prose alone and would go on passing after the code was deleted, which is the
    one failure this check exists to prevent. Measured: each pattern matches once more in the raw
    file than in the code.
    """
    missing = []
    for rel, pattern, expected, why in INVARIANTS:
        path = os.path.join(SRC, rel)
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            missing.append((rel, pattern, "file not found", why))
            continue
        code = H.blank_comments_and_strings(raw)
        found = len(re.findall(pattern, code))
        if found >= expected:
            continue
        if found == 0 and re.search(pattern, raw):
            where = "IN A COMMENT ONLY - the code is gone and the prose still describes it"
        elif found == 0:
            where = "absent"
        else:
            where = "%d call site(s), expected %d - one lost its gate" % (found, expected)
        missing.append((rel, pattern, where, why))
    return missing


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
    print("DECLARED INVARIANTS - lines the source itself marks as fatal if dropped")
    print("=" * 78)
    broken = check_invariants()
    for rel, pattern, where, why in broken:
        print("  MISSING  %s   %s" % (rel, where))
        print("           %s" % why)
    if not broken:
        print("  all %d present in code - the FBX export and import gates" % len(INVARIANTS))

    print()
    print("=" * 78)
    print("DEFERRED WORK - the guard does not survive a deferral")
    print("=" * 78)
    raw = check_deferrals()
    if raw:
        for fname, ln, text in raw:
            print("  UNGUARDED DEFERRAL  %s:%d" % (fname, ln))
            print("                      %s" % text)
        print("  Route these through %s, which re-arms GIsRunningUnattendedScript" % DEFER_HELPER)
        print("  INSIDE the lambda. A TGuardValue restores on scope exit, so work scheduled for a")
        print("  later tick runs with the backstop already unwound.")
    else:
        print("  every deferral goes through %s - the guard survives to where the work runs" % DEFER_HELPER)

    print()
    print("=" * 78)
    print("guarded %d   unguarded %d   citation drift %d   unguarded deferrals %d   "
          "broken invariants %d"
          % (guarded, len(unguarded), drift, len(raw), len(broken)))
    if unguarded:
        print()
        print("An unguarded prompter freezes the bridge, it does not fail it.")
        print("  FMessageDialog::Open        -> TGuardValue<bool>(GIsRunningUnattendedScript, true)")
        print("  FSuppressableWarningDialog  -> FMifScopedDialogSuppression(TEXT(\"<key>\"))")
        print("The second one makes the operation PROCEED; the unattended guard would only cancel it,")
        print("which turns a hang into a silent refusal. Where the condition is predictable, check it")
        print("first so the caller gets a real reason instead of a generic failure.")
    print("=" * 78)
    return 1 if (unguarded or drift or raw or broken) else 0


if __name__ == "__main__":
    sys.exit(main())
