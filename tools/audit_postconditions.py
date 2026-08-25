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
import re
import sys

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
    i = src.find("IsReadOnlyEndpoint")
    if i < 0:
        return set()
    # the literal list sits just above/below the predicate; take the nearest big TEXT("...") run
    window = src[max(0, i - 6000): i + 6000]
    return {m.lower() for m in re.findall(r'TEXT\("([a-z0-9_]+)"\)', window)}


def handler_bodies():
    for fn in sorted(os.listdir(PRIV)):
        if not fn.endswith(".cpp"):
            continue
        src = open(os.path.join(PRIV, fn), encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        matches = list(HANDLER.finditer(src))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
            yield fn, m.group(1), src[m.start():end]


def main():
    readonly = read_only_endpoints()
    rows = []
    skipped_readonly = 0
    for fn, name, body in handler_bodies():
        if name.lower() in readonly:
            skipped_readonly += 1
            continue
        body = strip_response_writes(body)
        mutates = any(h in body for h in MUTATION_HINTS)
        if not mutates:
            continue
        verified = any(v in body for v in VERIFY_MARKERS)
        silent = [(api, why) for api, why in SILENT_APIS if api in body]
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
    print("\nHeuristic. Over-reports handlers that verify via a helper or a checked bool return.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
