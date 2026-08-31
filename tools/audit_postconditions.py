"""Which mutating handlers report success WITHOUT checking that the mutation took?

This is the defect that keeps coming back in this module, three times in one week:

  * set_pin_default answered ok:true for "banana" on an int pin, because the schema's
    TrySetDefaultValue is void and silently refuses a literal it cannot parse.
  * apply_graph_patch reported 12/12 OK on a rewire where 8 destinations kept their old source,
    because "the requested link exists" was checked and "the destination looks how the caller asked"
    was not.
  * set_pin_type re-serialised the pin it had just written without comparing it to the request, so a
    node that re-derived its own types reported success while having silently reverted.

Each was found by a user, in production, after the fact. The shape is always the same: call a UE API
that CANNOT fail loudly, then report ok because nothing threw.

This scans every handler for that shape - it mutates, and nothing in its body reads the result back.
It is a heuristic and it over-reports: a handler that verifies in a helper, or one whose UE call
genuinely returns a checked bool, looks the same from here. The output is a reading list ranked by
how dangerous the silence would be, not a defect list.
"""
import os
import io
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harvest_param_table as H          # the one comment/string scrubber, shared not reimplemented

HERE = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.join(os.path.dirname(HERE), "Source", "MifBridge", "Private")

HANDLER = re.compile(r"void\s+H_([A-Za-z0-9_]+)\s*\(\s*const\s+TSharedRef<FJsonObject>&\s*In")

# UE calls that mutate and CANNOT report failure - void, or a bool nobody is obliged to read.
# These are the ones where "it did not throw" says nothing at all about whether it worked.
SILENT_APIS = [
    ("TrySetDefaultValue", "void - the schema silently refuses a literal it cannot parse for the pin type"),
    ("SetPurity", "void - no-ops when the flag already matches"),
    ("SetFolderPath", "void"),
    ("SetActorLabel", "void - the editor may uniquify the label it actually assigns"),
    ("SetMacroGraph", "void"),
    ("SetFromProperty", "void"),
    ("SetVariableType", "void"),
    ("SetPropertyValue", "void"),
    ("ImportText", "returns a pointer nobody checks; a partial parse still 'succeeds'"),
    ("SetPinDefaultValue", "void"),
    ("MakeLinkTo", "void - no schema validation at all"),
    ("SetPinType", "void"),
    ("SetMetaData", "void"),
    ("RenameNode", "void"),
]

# Evidence that the handler looked at the result rather than assuming it.
VERIFY_MARKERS = [
    "READ BACK", "VERIFY AFTER WRITE", "read back", "verify", "Verify",
    "Before", "After", "bChanged", "bDefaultChanged", "Checked(",
    "Compare", "postcondition",
    # This module's own idiom for "I checked instead of trusting the call". It appears in the
    # comments of handlers that DO verify, so leaving it out kept re-reporting fixed code.
    "rather than assume", "not assume", "rather than assumed",
]

# Mutation of the WORLD, not of the response.
#
# This list used to contain "->Set", which matches `Out->SetStringField(...)`. Every endpoint builds
# its response that way, so every read-only lister was reported as an unverified mutation and the
# MEDIUM list came out ~90 long and almost entirely noise. Response building is not a side effect.
MUTATION_HINTS = ["->Modify()", "MarkStructural", "MarkBlueprintAs", "NewObject<",
                  "AddPin", "RemovePin", "BreakPinLinks", "TryCreateConnection", "Destroy",
                  "SetActorLabel", "SetFolderPath", "TrySetDefaultValue", "ReconstructNode",
                  "SpawnActor", "DeleteAsset", "RenameAsset", "->SetFlags", "SetPurity"]

# Writes to the response object, which are never side effects on the world.
RESPONSE_WRITE = re.compile(r"\b(Out|Json|J|Row|Args|Macro|Fn|Flags)->Set")


def strip_response_writes(body):
    return RESPONSE_WRITE.sub("<response>", body)


def read_only_endpoints():
    """The module's own readOnly bucket - those endpoints cannot have a postcondition."""
    common = os.path.join(PRIV, "MifBridgeCommon.cpp")
    try:
        src = open(common, encoding="utf-8", errors="replace").read()
    except OSError:
        return set()
    # THE DEFINITION AND ITS WHOLE SET, not a window around the first mention.
    #
    # This was src.find("IsReadOnlyEndpoint") plus a +/-6000 character window of TEXT() literals.
    # Two faults compounded: the bare find matched a COMMENT 25 lines above the function (the same
    # bug fixed in param_reach the same night), so the window was centred in the wrong place - and a
    # fixed character window cannot hold a list that grows. Measured against a live editor it saw 56
    # of the 88 endpoints the bridge actually reports as readOnly, missing find_assets, compile,
    # export_asset, get_dependencies and 28 more.
    #
    # The set is a plain `static const TSet<FString> ReadOnly = { TEXT("a"), ... };` inside the
    # function, so it can be read exactly: locate the DEFINITION, then take the braced initialiser.
    m = re.search(r"\bbool\s+IsReadOnlyEndpoint\s*\(", src)
    if not m:
        return set()
    decl = re.search(r"TSet<FString>\s+\w+\s*=\s*\{", src[m.end():])
    if not decl:
        return set()
    start = m.end() + decl.end() - 1
    depth, j = 0, start
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return {x.lower() for x in re.findall(r'TEXT\("([a-z0-9_]+)"\)', src[start:j])}


def handler_bodies():
    for fn in sorted(os.listdir(PRIV)):
        if not fn.endswith(".cpp"):
            continue
        src = open(os.path.join(PRIV, fn), encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        matches = list(HANDLER.finditer(src))
        for i, m in enumerate(matches):
            # BRACE-MATCH THE BODY, do not run to the next handler. A handler that is the LAST one
            # in its file - or merely the last before a long run of helpers - absorbed everything
            # after it under the old rule. H_project_paths, added to MifBridgeCommon.cpp on
            # 2026-08-31 immediately after H_self_audit, was credited with a 154,484-character
            # "body" and duly reported for calling TrySetDefaultValue and SetActorLabel, neither of
            # which appears within a hundred lines of it. The finding it inherited had been filed
            # against self_audit for the same reason, and moved the moment a new handler took that
            # position - which is the tell that it was never about either endpoint.
            open_brace = src.find("{", m.end())
            if open_brace < 0:
                continue
            depth, j = 0, open_brace
            while j < len(src):
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            end = min(j + 1, matches[i + 1].start() if i + 1 < len(matches) else len(src))
            yield fn, m.group(1), src[m.start():end]


BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "audit_postconditions_baseline.txt")


def load_baseline():
    try:
        return set(l.strip() for l in io.open(BASELINE, encoding="utf-8")
                   if l.strip() and not l.startswith("#"))
    except Exception:
        return set()


def main():
    readonly = read_only_endpoints()
    rows = []
    skipped_readonly = 0
    for fn, name, body in handler_bodies():
        if name.lower() in readonly:
            skipped_readonly += 1
            continue
        # WHAT IT DOES comes from CODE. WHAT IT CLAIMS may come from prose. The two questions this
        # tool asks are not symmetric, and matching both against the raw text conflated them.
        #
        # Measured before the split: five handlers were judged "mutates" on the strength of a COMMENT
        # alone - remove_sublevel and save_dirty_packages each merely DISCUSS destroying (one of them
        # only via the word PrivateDestroyLevel), add_simplified_collision and add_ik_retarget_chain
        # mention ->Modify() in prose. Worse, three had a silent API attributed from prose, including
        # set_pin_default, whose only occurrence of TrySetDefaultValue is the comment at
        # MifBridgeNodes.cpp:2315 explaining the bug it no longer has. That is the founding case in
        # this file's own docstring, listed there as FIXED - so the tool was re-reporting the very
        # defect it was written to catch, and the fix had made the report WORSE by adding the comment.
        #
        # Verification stays on the raw text deliberately. Half of VERIFY_MARKERS are comment idioms
        # ("READ BACK", "rather than assume") added precisely because handlers that DO verify were
        # being re-reported. The cost of that choice is stated rather than hidden: a handler could
        # claim in a comment to verify and not do it. Mutation is the claim worth doubting, because a
        # false mutation flag sends a reader to a handler with nothing wrong with it.
        prose = strip_response_writes(body)
        code = strip_response_writes(H.blank_comments_and_strings(body))
        mutates = any(h in code for h in MUTATION_HINTS)
        if not mutates:
            continue
        verified = any(v in prose for v in VERIFY_MARKERS)
        silent = [(api, why) for api, why in SILENT_APIS if api in code]
        if silent and not verified:
            sev = "high"
        elif silent:
            sev = "low"          # uses a silent API but does check something
        elif not verified:
            sev = "medium"
        else:
            continue
        rows.append((sev, fn, name, silent, verified))

    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (order[r[0]], r[1], r[2]))

    print("Mutating handlers with no visible read-back")
    print("=" * 80)
    cur = None
    for sev, fn, name, silent, verified in rows:
        if sev != cur:
            cur = sev
            print("\n--- %s ---" % sev.upper())
            if sev == "high":
                print("    calls an API that cannot report failure, and checks nothing afterwards")
            elif sev == "medium":
                print("    mutates, but nothing in the body reads the result back")
            else:
                print("    uses a silent API but does verify something (likely fine - confirm)")
        print("  %-28s %s" % (name, fn))
        for api, why in silent:
            print("      %-22s %s" % (api, why))

    print("\n" + "=" * 80)
    print("  skipped %d endpoint(s) the module declares read-only" % skipped_readonly)
    for sev in ("high", "medium", "low"):
        print("  %-8s %d" % (sev, sum(1 for r in rows if r[0] == sev)))
    print("  total    %d" % len(rows))
    print("")
    print("Heuristic. Over-reports handlers that verify via a helper or a checked bool return.")

    # RATCHET. Ninety-four findings on a self-described over-reporting heuristic is exactly the size
    # where a genuinely NEW one is invisible - nobody re-reads ninety-four lines to spot the ninety-
    # fifth. Keyed on severity:endpoint rather than a line number: audit_loop_writes was keyed by line
    # and raised four false alarms the first time a comment was added above a site.
    #
    # Severity is part of the KEY, not metadata beside it, so a finding moving from medium to high
    # shows up as new. That transition is a regression and is the whole point of watching.
    base = load_baseline()
    keys = set("%s:%s" % (r[0], r[2]) for r in rows)
    if "--update-baseline" in sys.argv:
        with io.open(BASELINE, "w", encoding="utf-8", newline=chr(13) + chr(10)) as f:
            f.write("# Accepted postcondition findings. Regenerate deliberately with:" + chr(10))
            f.write("#   python tools/audit_postconditions.py --update-baseline" + chr(10))
            for k in sorted(keys):
                f.write(k + chr(10))
        print("baseline updated: %d entries" % len(keys))
        return 0
    fixed = sorted(base - keys)
    fresh = sorted(keys - base)
    print("")
    for k in fixed:
        print("  FIXED    %s  (drop it from the baseline)" % k)
    if fresh:
        print("")
        print("NEW since the baseline - these are the ones worth reading:")
        for k in fresh:
            print("  %s" % k)
        print("")
        print("Accept with --update-baseline once judged, and say why in the commit.")
        return 1
    print("OK  no new postcondition findings (%d known)" % len(base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
