"""Which response fields report a CONSEQUENCE the caller did not ask for, and does any suite read one?

WHY THESE FIELDS AND NOT ALL 2000. Most response fields answer the question that was asked -
`count`, `guid`, `path`. A smaller set exists to report something the caller did NOT ask about and
cannot otherwise see: a property that did not take, an actor that could not be placed, a rollback
that could not put everything back. That is the read-back surface an agent depends on when something
goes PARTIALLY wrong, which is the case where a wrong answer costs most.

The concrete lesson is move_tree_widget. It answers a root swap with displacedRoot,
displacedSubtreeSize and a warning that the old subtree "will not render" - a displaced root does not
vanish from the asset, it stops being MOUNTED - and nothing in its ok:true would tell you. Those
three were asserted by nothing until T435.

WHY THIS IS A TOOL AND NOT A LIST. The 48 in FEATURE_PARITY_SPEC.md were counted by hand once. A
number nothing recomputes is a number that will be wrong again next month - harvest_param_table.py
says exactly that about the table it regenerates, having watched it go stale twice. It was already
drifting when this was written: the spec names propertiesFailed as asserted by nothing, and
test_inherited_components T295 asserts it.

WHAT COUNTS AS READ, and this is the whole point of the tool. A suite must contain a string whose
WHOLE VALUE is the field name. modalHazard was in the hand-counted list even though a test had
"asserted" it an hour earlier, because that test checked whether the string "modal" appeared
ANYWHERE in the response, which a note mentioning modals satisfies just as well - so a name inside a
check LABEL like "T295 propertiesFailed names the one that was bad" is not a read, and an exact-value
rule drops every label of that shape.

The first version of that rule demanded a SUBSCRIPT and was wrong within the hour: test_rollback_real
drives `resp[k] for k in ("rollbackUnresolvedPins", "rollbackLostLinks")`, so both fields read as
unasserted while a suite was asserting them. A scanner that understands one spelling of a read
manufactures the backlog it was written to measure.

NOT ALL OF THEM ARE REACHABLE, and that is an answer rather than an excuse. discardedUnsaved on
remove_sublevel needs discardUnsaved, which mifaudit's FORBIDDEN_KEYS strips from every payload on
purpose, and reaching remove_sublevel at all needs a sublevel, which needs a SAVED .umap. Anything
gated behind saving or discarding unsaved work is out of scope for an unattended suite by the
standing rules. Those live in UNREACHABLE below, with the reason, rather than sitting in the backlog
looking undone.

PARSING. Comments are blanked with harvest_param_table's shared scrubber before anything is located,
because a comment discussing a field would otherwise read as an emission - the same trap that let a
comment about MIF_WITH_METASOUND mark that dependency used in parity_check. The field NAME is then
read from the ORIGINAL text at the same offset, because the scrubber blanks string bodies too and
the name is a string. Locate on the scrubbed copy, extract from the raw one.

Usage:
    python tools/audit_consequence_fields.py            # the backlog, exit 0
    python tools/audit_consequence_fields.py --check    # exit 1 if the backlog GREW past the baseline
    python tools/audit_consequence_fields.py --baseline # re-record the count after closing some
    python tools/audit_consequence_fields.py --all      # every consequence field, covered or not

Talks to nothing. Source and suites, both static.
"""
import argparse
import ast
import glob
import io
import os
import re
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "Source", "MifBridge", "Private")

sys.path.insert(0, HERE)
import harvest_param_table as H            # the one scrubber and the one function-body walk

# --------------------------------------------------------------------------- what counts

# A field is a CONSEQUENCE field if its name says something did not happen, happened to something
# else, or happened and was undone. Curated rather than inferred: an inferred rule over 2090 field
# names would need prose to justify each call, and prose is what this repo keeps catching itself
# trusting. Every entry here is a claim that can be checked by reading the emitter.
CONSEQUENCE = re.compile(
    r"(?:^|[a-z])("
    r"[Ff]ailed|[Ff]ailure|[Dd]ropped|[Ss]kipped|[Rr]everted|[Dd]iscarded|[Dd]isplaced|"
    r"[Oo]rphaned|[Ll]eftBehind|[Ll]ost|[Uu]nresolved|[Uu]nchanged|[Rr]ejected|[Rr]emoved|"
    r"[Tt]runcated|[Cc]lamped|[Ss]ilentl|[Pp]artial|[Ss]tale|[Bb]roken|[Mm]issing|[Ii]nvalid|"
    r"[Ii]ncomplete|[Rr]emaining|StillPresent"
    r")")

# WIDENED 2026-08-31 to include REMAINS as well as WENT WRONG. "Something is still there that you
# did not ask about" is the same class of unasked-for consequence as "something failed", and the
# pattern could not see it: remove_pin's duplicatesStillPresent, remove_function's
# functionGraphsRemaining, fix_up_redirectors' remainingNote and the shader helper's
# numRemainingJobs were all invisible. Adding [Rr]emaining and StillPresent took the field count
# 64 -> 73 and surfaced FOUR unread, of which three are pre-existing gaps nobody could have seen.
# Measured before committing to it: the nine new matches are all real consequence fields, so the
# widening cost no noise.
#
# WHAT THIS PATTERN STILL CANNOT SEE, said out loud rather than left as a clean-looking number. It
# matches names that say something went WRONG or is STILL THERE. It does NOT match names that say something merely MOVED -
# `axisChanged` on set_blendspace_samples is as much an unasked-for consequence as anything in the
# list above, and no name-based rule can separate it from the dozens of `changed` fields that are the
# honest ANSWER to "did this change anything". Adding [Cc]hanged would drown the real findings in
# them. So a side effect named for the thing it moved rather than for the failure it represents is
# invisible here, and has to be found by reading the handler - which is how axisChanged was found.

# Emitted by an endpoint the standing rules put out of reach of an unattended suite. Listed with the
# reason, because "no suite asserts it" and "no suite CAN assert it" are different findings and only
# one of them is work.
UNREACHABLE = {
    "discardedUnsaved": "remove_sublevel needs discardUnsaved, which mifaudit's FORBIDDEN_KEYS "
                        "strips on purpose, and needs a sublevel, which needs a SAVED .umap",
    "discardedNote": "emitted beside discardedUnsaved, same gate",
    # EVERY ENTRY BELOW WAS ADDED AFTER READING ITS EMITTER, not after failing to think of a test.
    # The difference matters: an unreachable list is the one place where a wrong entry silently
    # shrinks the backlog, so a reason that cannot be checked against the source does not belong.
    # THIS ENTRY WAS WRONG FOR ONE DAY AND IS LEFT ANNOTATED RATHER THAN QUIETLY CORRECTED, because
    # it is the exact failure this list was warned about above. The first version said the field was
    # unreachable because a duplicate is refused by AddSample before ValidateSampleData sees it -
    # true, and not the whole story. ValidateSampleData's FIRST act is SnapSamplesToClosestGridPoint
    # (BlendSpace.cpp 5.3 :1168), which relocates samples when BOTH axes have bSnapToGrid set
    # (:2196). The handler matched survivors by position, so a MOVED sample failed the match and was
    # reported here - with a note saying it had been deleted, was not on the asset, and shared a
    # point with another, while sampleCount in the same response said it was there. Reachable, and
    # reachable wrongly. Fixed in source: a moved sample now matches on animation alone and is
    # reported in samples[] with movedByEngine. AFTER that fix this field is unreachable again, for
    # the original reason plus this one - but the entry was not entitled to be right the first time.
    "droppedByValidation": "reaching it needs a sample that is GONE, and nothing this endpoint can "
                           "send produces one: a duplicate point is refused by AddSample (it calls "
                           "IsTooCloseToExistingSamplePoint), and a sample the engine relocates by "
                           "grid-snapping is now reported as movedByEngine in samples[] rather than "
                           "as dropped. Verified live 2026-08-31 in both directions",
    "droppedNote": "emitted beside droppedByValidation, same branch",
    # PROJECT-CONDITIONAL, same as notifiesRemoved below and for the same underlying reason - no
    # scratch AnimSequence can exist here. Reached by ELIMINATION over the engine's own formula
    # rather than by failing to think of a test: bIsValid = bAnimationExists && bSampleInBounds &&
    # bSampleIsUnique (BlendSpace.cpp 5.3 :1200), and every one of the three is closed.
    #
    #   bSampleIsUnique  a duplicate point is refused by AddSample before ValidateSampleData sees
    #                    it - measured, and it is why droppedByValidation is unreachable too
    #   bSampleInBounds  out-of-bounds does not stick: AddSample EXPANDS the axis to fit rather than
    #                    refusing (0..100 -> 0..800 for one sample at x=777, measured 2026-08-31)
    #   bAnimationExists needs the sample's UAnimSequence to become null AFTER the sample is added,
    #                    which means deleting the animation - and the only animations here are real
    #                    game content, with both routes to a scratch copy closed by crash guards
    #
    # So a sample that is ON the asset and marked invalid cannot be manufactured in THIS project. In
    # an uncooked one, duplicate an anim to scratch, sample it, delete the copy.
    "invalidNote": "needs a sample present on the asset with bIsValid false, and all three inputs to "
                   "that flag are closed here - duplicates are refused by AddSample, out-of-bounds "
                   "expands the axis instead of refusing, and a null animation would need deleting "
                   "an AnimSequence, which are all real content with no scratch route (see "
                   "notifiesRemoved). Reachable in an uncooked project",

    # PROJECT-CONDITIONAL, like invalidNote above. Everywhere else "out of reach" means the
    # standing rules forbid it; this one means the ASSETS in this project put it out of reach, and it
    # would be reachable in an uncooked project - which matters, because this is a general UE5 tool
    # and Curfew (uncooked 5.7) is the other half of who it is for.
    "notifiesRemoved": "remove_anim_notify_track needs an AnimSequence with a notify track, and BOTH "
                       "routes to a scratch one are closed by crash guards added 2026-08-31: "
                       "create_asset refuses UAnimSequence (a bare NewObject leaves the sequencer "
                       "data model without its MovieScene) and duplicate_asset refuses a COOKED one "
                       "(access violation 0x28 in the post-duplicate load path). DDS2's animations "
                       "are cooked, so the only remaining target is real game content, which the "
                       "standing rules forbid dirtying. In an UNCOOKED project the duplication route "
                       "opens and this becomes ordinary work",
    # NOT the no-save rule, which was the obvious guess and was wrong. The field is emitted on
    # EVERY response including the dryRun path, so it would be reachable without saving anything -
    # but save_dirty_packages is in mifaudit's DENY list, so no suite can call it at all. Checked by
    # calling it: "denied by harness". The deny is deliberate and is not scratch_confirm's kind of
    # gate, so there is no sanctioned bypass.
    "skippedCookedOrigin": "save_dirty_packages is in mifaudit's DENY list - not callable from a "
                           "suite at all, with or without dryRun. The field itself is emitted on "
                           "every response, so this is a harness boundary rather than an endpoint "
                           "limitation",
    "broken": "break_level_instance needs a Level Instance ACTOR in the open level, and creating "
              "then breaking one modifies whatever level is loaded - the same session precondition "
              "as partialNote, and issue J's warning about actors that cannot be cleaned up applies "
              "equally. Reachable with a scratch level open",

    "droppedLines": "emitted by MifBridgePIE's log Emit helper, so reaching it needs a RUNNING PIE "
                    "session producing more output than the ring buffer holds. The PIE family is "
                    "attended-only by the standing rules and never runs in an autopilot pass",

    # ADDED 2026-08-31 AND NOT YET SUITE-COVERED, which is a DEBT recorded here rather than paid by
    # re-baselining. Both are read and asserted by V9 in verify_pending_fixes.py - including an
    # agreement check against get_node - but that file is not a test_*.py suite, so this tool does
    # not count it, and it is right not to: a one-shot verification pass is not regression coverage.
    #
    # They cannot move into a suite yet because the DLL that emits them has not been built. The
    # moment verify_pending_fixes runs green, the assertions belong in test_pins.py beside T447/T448
    # and these two entries should be DELETED rather than left to rot into permanent exemptions.
    "nodesWithOrphanedPin": "new field on set_variable_type, asserted by V9 pending a build - move "
                            "to test_pins.py and delete this entry once the build is green",
    "orphanedPinsRemaining": "new field on set_variable_type, asserted by V9 pending a build - move "
                             "to test_pins.py and delete this entry once the build is green",

    "duplicatesRemoved": "FIXED 2026-08-31 and no longer out of reach - kept here only until a "
                         "rebuild verifies it (V11). remove_pin could not remove a SAME-DIRECTION "
                         "duplicate, the case its branch exists for, because ResolvePin matches "
                         "on (NodeGuid, PinName, Direction) and returns the FIRST hit, so every "
                         "captured ref resolved to the pin being kept and Removed stayed 0 while "
                         "the response still said Kind duplicate. The trigger turned out to be "
                         "ordinary: retyping a wired variable leaves the node with two pins of one "
                         "name and direction - the new one and the engine ORPHAN holding the old "
                         "link. The loop now works on pointers, guarded by Node->Pins.Contains, "
                         "and reports duplicatesStillPresent read back from the node afterwards",
    "failedConsolidationObjects": "consolidate_assets CLOSES EVERY OPEN ASSET EDITOR to do its work, "
                                  "which is stated in its own confirm refusal. Running it against a "
                                  "session somebody is working in is not something an unattended "
                                  "suite may do, and the failure path additionally needs a "
                                  "consolidation the engine partially refuses",
    "failedNote": "emitted beside failedConsolidationObjects, same gate",

    "partialNote": "spawn_many places actors in WHATEVER LEVEL IS OPEN and issue J records that they "
                   "cannot be cleaned up, so test_spawn_many refuses to run unless the open level is "
                   "Untitled*/_Mif*. Reaching partialNote also needs a batch where SOME items fail "
                   "and some succeed. A precondition on the session rather than an impossibility - "
                   "run it with a scratch level open",

    "duplicatePinsRemoved": "belt-and-braces for a root cause that is already FIXED, and the source "
                            "says so: 'the root cause is fixed in PlaceAndInit ... but this makes "
                            "create_function self-healing if any other terminator ever behaves the "
                            "same'. The obvious way to reach it - two outputs sharing a name - does "
                            "not, because CreateUserDefinedPin runs with bUseUniqueName true and "
                            "RENAMES the second (Same -> Same1). Verified live 2026-08-31; that "
                            "rename path is asserted instead, test_idempotence T384",
    "staleNote": "needs a component request whose owning manager has gone away WITH ITS WORLD - a "
                 "teardown no unattended suite performs. staleHandles, the always-emitted count "
                 "beside it, IS asserted (test_game_framework T1408)",
    "compileFailed": "material_statistics reaches it only when a shader compile was WAITED ON and "
                     "produced no usable shader map. In a COOKED project that branch sits behind an "
                     "earlier one that always wins: a material made by create_asset has no material "
                     "resource for the editor's feature level, and the null-resource guard refuses "
                     "before GetStatistics is ever called. Measured, not assumed - a scratch "
                     "Material was created 2026-08-31 and material_statistics returned exactly that "
                     "refusal, with compileFailed absent. Reaching it needs a material that HAS a "
                     "resource and still fails, which is an uncooked project. Not a limitation of "
                     "the endpoint and not irrelevant work - it is the one editor this session may "
                     "drive",

    "verifyFailure": "reset_property_to_default emits it when a reset cannot be verified, and the "
                     "handler names two causes. BOTH MEASURED AND BOTH NARROWED. The fixed-size "
                     "C-array cause: the route is OPEN - '<prop>[N]' resolves, LensFlareTints[2] "
                     "resets with arrayDim 8 - and the per-element verify that branch does instead "
                     "of a text compare is CORRECT, so it has nothing to report (T905b). The native "
                     "setter cause: four clamped or network properties on a CDO (InitialLifeSpan "
                     "-5, NetUpdateFrequency 0, NetCullDistanceSquared -1, bHidden) all reset with "
                     "verified true, so whatever fights a reset is not ordinary clamping. T905b "
                     "READS the field, but only into a check's detail string - which is a "
                     "diagnostic, NOT an assertion, and this tool's own header says a read is not "
                     "one. Listed here so the distinction is not quietly lost",

    "truncatedReadNote": "needs a log file over 64 MB, which means writing 64 MB to disk to test a "
                         "note. truncatedRead, the bool beside it, is emitted always",
    "leftBehind": "add_timeline creates the UTimelineTemplate FIRST and fails cleanly with nothing "
                  "created when the name is taken, so the collision cannot reach this branch. What "
                  "does is the template not being re-findable by name after PlaceAndInit despite "
                  "having just been made - which the handler itself calls the one failure the "
                  "preflight cannot predict",
}

FIELD_CALL = re.compile(r"Out->Set(?:String|Number|Bool|Array|Object|StringArray)Field\s*\(")
TEXT_LIT = re.compile(r'TEXT\("([A-Za-z_][A-Za-z0-9_]*)"\)')
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A FILE OF NEEDLES IS NOT A FILE OF READS. audit_detectors_fire.py plants a defect for every
# detector, so its source contains, by construction, a literal of whatever each plant targets - here
# `.get("propertiesFailed")`. Scanning it made this tool report that field as read BY THE HARNESS,
# which meant the plant could never make it unread and the harness called this tool asleep. That is
# the third time in this repo a probe has declared itself: a MIF_BIND probe, a blocking probe whose
# marker was the word its own declaration contained, and now this. If a scanner reads the corpus,
# the corpus includes the thing doing the planting.
NOT_A_READER = {"audit_detectors_fire.py"}

# The backlog as measured on 2026-08-31, so this can be ratcheted rather than left failing forever -
# the same shape param_reach.py runs on, and for the same reason: a check that fails on the whole
# existing backlog gets switched off instead of fixed.
BASELINE_FILE = os.path.join(HERE, "consequence_fields_baseline.txt")


def emitted_fields():
    """[(field, endpoint_or_helper, file, line)] for every Out->Set*Field in a function body."""
    rows = []
    for path in sorted(glob.glob(os.path.join(SRC, "*.cpp"))):
        raw = io.open(path, encoding="utf-8", errors="replace").read()
        scrubbed = H.blank_comments_and_strings(raw)
        base = os.path.basename(path)
        for name, start, end in H.function_spans(raw, scrubbed):
            for m in FIELD_CALL.finditer(scrubbed, start, end):
                # Locate on the scrubbed copy, EXTRACT FROM THE RAW ONE - the scrubber blanks
                # string bodies, and the field name is a string.
                lit = TEXT_LIT.match(raw, m.end())
                if not lit:
                    continue
                line = raw.count("\n", 0, m.start()) + 1
                who = name[2:] if name.startswith("H_") else name
                rows.append((lit.group(1), who, base, line, name.startswith("H_")))
    return rows


def suite_reads():
    """Every field name a suite NAMES as a field, mapped to the suites that do it.

    A STRING WHOSE WHOLE VALUE IS THE FIELD NAME, which is narrower and wider than it first looks,
    and both on purpose.

    Narrower than "the name appears in the file". modalHazard was in the hand-counted backlog even
    though a test had "asserted" it an hour before, because that test looked for the substring
    "modal" anywhere in the response - which a note mentioning modals satisfies. A check LABEL like
    "T295 propertiesFailed names the one that was bad" CONTAINS the field and is not a read of it.
    Requiring the literal to equal the name exactly drops every label.

    Wider than "the name is subscripted", which was this function's first rule and was wrong within
    an hour of being written. test_rollback_real does:

        residue = {k: resp[k] for k in ("rollbackUnresolvedPins", "rollbackLostLinks") ...}

    The subscript is resp[k] - a VARIABLE - and the names live in a tuple, so a rule that demanded
    the literal inside the brackets reported both fields as read by nothing while a suite
    asserted them. Driving names from a
    tuple is ordinary Python, and a scanner that only understands one spelling of a read manufactures
    exactly the false backlog it was written to measure.

    Comments are stripped with tokenize rather than a regex, because a regex for a Python comment
    finds one inside a string literal - and these suites are full of JSON in strings. Strings are
    KEPT, unlike the C++ side: here the field name IS the string being looked for.
    """
    reads = {}
    for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))
                       + glob.glob(os.path.join(HERE, "audit_*.py"))
                       + glob.glob(os.path.join(HERE, "fuzz_*.py"))):
        if os.path.basename(path) in NOT_A_READER:
            continue
        try:
            with io.open(path, "rb") as fh:
                toks = list(tokenize.tokenize(fh.readline))
        except Exception:                      # a file this tool cannot tokenize is not a verdict
            continue
        base = os.path.basename(path)
        for t in toks:
            if t.type != tokenize.STRING:
                continue
            try:
                val = ast.literal_eval(t.string)
            except Exception:
                continue                        # f-strings and the like: not a plain field name
            if isinstance(val, str) and IDENT.match(val):
                reads.setdefault(val, set()).add(base)
    return reads


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="list every consequence field, not just gaps")
    ap.add_argument("--check", action="store_true", help="exit 1 if the backlog grew past baseline")
    ap.add_argument("--baseline", action="store_true",
                    help="write the current count as the baseline (never done by --check)")
    args = ap.parse_args()

    rows = emitted_fields()
    if len(rows) < 500:
        print("SELF-CHECK FAILED: only %d response fields found across %s."
              % (len(rows), os.path.basename(SRC)))
        print("The extraction has drifted - do not trust a clean result until this is resolved.")
        return 2

    reads = suite_reads()
    if "guid" not in reads:
        print("SELF-CHECK FAILED: no suite appears to read 'guid', which is not credible.")
        print("The suite scan is broken, so every field below would read as UNCOVERED.")
        return 2

    consequence = {}
    for field, who, base, line, is_handler in rows:
        if not CONSEQUENCE.search(field):
            continue
        consequence.setdefault(field, []).append((who, base, line, is_handler))

    covered, gaps, unreachable = [], [], []
    for field in sorted(consequence):
        if field in UNREACHABLE:
            unreachable.append(field)
        elif field in reads:
            covered.append(field)
        else:
            gaps.append(field)

    total_fields = len({r[0] for r in rows})
    print("response fields emitted        : %d distinct, %d call sites" % (total_fields, len(rows)))
    print("of those, CONSEQUENCE fields   : %d" % len(consequence))
    print("  read by a suite              : %d" % len(covered))
    print("  out of reach by the rules    : %d" % len(unreachable))
    print("  NO suite reads them          : %d" % len(gaps))
    print("")

    if args.all and covered:
        print("READ BY A SUITE:")
        for f in covered:
            print("  %-32s %s" % (f, ", ".join(sorted(reads[f])[:3])))
        print("")
    if unreachable:
        print("OUT OF REACH - not work, and not a gap:")
        for f in unreachable:
            print("  %-32s %s" % (f, UNREACHABLE[f]))
        print("")
    if gaps:
        print("NO SUITE READS THESE:")
        for f in gaps:
            sites = consequence[f]
            where = sites[0]
            extra = " (+%d more)" % (len(sites) - 1) if len(sites) > 1 else ""
            print("  %-32s %s  %s:%d%s"
                  % (f, "endpoint " + where[0] if where[3] else "helper " + where[0],
                     where[1], where[2], extra))
        print("")

    if args.baseline:
        io.open(BASELINE_FILE, "w", encoding="utf-8", newline="").write("%d\r\n" % len(gaps))
        print("baseline written: %d unread consequence field(s). Commit it." % len(gaps))
        return 0

    if args.check:
        # --check NEVER WRITES. Ratcheting automatically on a shrink sounds tidy and means the
        # threshold can move without anybody reviewing the move - including a shrink caused by a
        # suite being deleted rather than a field being covered. The number is committed, so a
        # change to it shows up in a diff like any other claim.
        prev = None
        if os.path.isfile(BASELINE_FILE):
            try:
                prev = int(io.open(BASELINE_FILE, encoding="utf-8").read().split()[0])
            except Exception:
                prev = None
        if prev is None:
            print("no baseline at %s - run with --baseline once and commit it."
                  % os.path.basename(BASELINE_FILE))
            return 2
        if len(gaps) > prev:
            print("FAIL: %d unread consequence fields, baseline %d. A field reports something the "
                  "caller did not ask for and cannot see, and nothing reads it." % (len(gaps), prev))
            for f in gaps:
                print("  unread: %s" % f)
            return 1
        if len(gaps) < prev:
            print("OK  backlog shrank %d -> %d. Re-baseline with --baseline and commit."
                  % (prev, len(gaps)))
            return 0
        print("OK  %d unread, unchanged from baseline." % len(gaps))
        return 0

    print("A mention is not a read, and a read is not an assertion - this tool only proves the")
    print("field is INDEXED somewhere. Read the check before believing it tests anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
