"""Find parameters that are VALID but ignored depending on another parameter's value.

RejectUnknownParams catches a parameter that should not be there. It cannot catch a parameter that
SHOULD be there and is quietly unused, because the guard only knows the declared list - not that
`asset` is read on one branch and dropped on every other.

That is a real defect class, not a hypothetical. invoke_editor_tab declares `asset`, and
UiResolveTabManager returns early for the default manager:"global" without ever reading it. A caller
who meant an asset-editor tab and forgot to set manager got a global operation under ok:true. The
endpoint sweep found it by accident; nothing was looking for it.

WHAT THIS TOOL IS. A review list, not a verdict. It finds handlers that branch on a MODE parameter -
one compared against string literals - and reports the other parameters they declare, because those
are the ones that can be mode-dependent. Deciding whether each is genuinely ignored needs a human or
a careful read; a tool that guessed would produce exactly the noisy bucket the ghost detector spent
three passes escaping.

Reading the output: a handler appearing here is NOT a bug. The question to ask of each row is
"if I pass this parameter in the wrong mode, does anything tell me?"

KNOWN LIMITATION, stated rather than discovered later. Alias parameters read through a MULTI-LINE
`JStrAny(In, { TEXT("path"), TEXT("name"), ... })` are not recognised as reads, because the scan is
line-based and the literals sit on continuation lines. That is why rows like set_function_flags list
`path, functionName, name` - three spellings of one argument that IS read, on every path. Treat
alias-looking clusters as probable false positives and check the resolver before believing them.

The filters were each added because the tool accused something innocent:
  * refusal-mention, after it listed sculpt_landscape, which DOES say amount is raise/lower only;
  * brace depth, after it listed every parameter of every mode-having handler;
  * presence-guard, after it accused set_viewport_camera, whose location/rotation/lookAt sit inside
    `if (TryGetObjectField(...))` and are applied on every mode.
Each pass cut noise without cutting the one row that started this - invoke_editor_tab.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "Source", "MifBridge", "Private")

# A parameter compared against string literals is a MODE: it selects behaviour rather than carrying
# a value. `Mode.Equals(TEXT("assetEditor"))`, `if (K == TEXT("global"))`, and so on.
MODE_CMP = re.compile(r'(\w+)\s*(?:==|\.Equals\s*\()\s*TEXT\("([A-Za-z][\w.]*)"\)')

# Where a handler declares what it accepts.
DECL = re.compile(r'RejectUnknownParams\s*\(\s*In\s*,\s*Out\s*,\s*\{(.*?)\}', re.S)
PARAM = re.compile(r'TEXT\("(\w+)"\)')

HANDLER = re.compile(r'^\tvoid (H_\w+)\(', re.M)

# Everything the handler says when it refuses. A parameter named in one of these has been thought
# about; a declared parameter that appears in no refusal at all is the invoke_editor_tab shape.
FAIL_MSG = re.compile(r'Fail\s*\(\s*Out\s*,(.*?)\)\s*;', re.S)

# A REFUSAL BUILT FROM A TABLE NAMES NOTHING THIS SCAN CAN SEE, and that is a real gap rather than a
# hypothetical: create_procedural_mesh has seventeen shape-specific parameters and refuses each one
# from a loop over a { name, shapes } table - `%s is only read by shape %s`. Writing seventeen literal
# Fail() blocks the way sculpt_landscape writes two would be worse code for the same behaviour, so
# this file would go on listing ten parameters of a handler that refuses all ten.
#
# WHY A MARKER RATHER THAN A SMARTER SCAN. That was tried first and it is instructive that it failed:
# "the literal appears somewhere other than the accept-list and the accessors" cleared six handlers,
# and reading them showed three were cleared for reasons that had nothing to do with refusals - a
# range-for alias read `for (const TCHAR* Key : { TEXT("skeletonPath"), TEXT("path") })`, a
# ReadVectorField helper, and `Out->SetStringField(TEXT("label"), ...)` which writes the RESPONSE.
# Right answer, wrong reason, which does not count here. A parameter name appears in too many
# innocent places for proximity to mean anything.
#
# So the handler says so explicitly, the reason is mandatory, and the count is printed on every run
# whether or not anything is listed - the same shape as audit_fixture_adoption's ADOPTION-OK.
MODE_OK = re.compile(r'//\s*MODE-PARAMS-OK:\s*(\S.*)')


# Depth inside a handler body: 1 is the function's own braces, so a statement directly in the body
# sits at 1 and anything within an if/else/loop is deeper.
TOP_LEVEL = 1


def presence_guarded(body, param):
    """True when the handler explicitly tests whether this parameter was PASSED.

    This is the difference between "inside a branch" and "inside a MODE branch", and getting it wrong
    made the tool accuse set_viewport_camera: its location/rotation/lookAt sit inside
    `if (In->TryGetObjectField(TEXT("location"), ...))`, which tests the parameter's own presence and
    then applies it on every mode. That is deliberate handling, the opposite of a silent ignore.
    """
    # HasField IS THE PLAINEST PRESENCE GUARD THERE IS, and it was the one spelling missing. Five
    # TryGet* variants and JHasAny were listed; `if (In->HasField(TEXT("gameView")))` was not, so
    # set_viewport_camera's gameView and realtime read as mode-ignored when the handler explicitly
    # tests whether each was passed and applies it if so. That is deliberate handling - the same
    # argument that put the TryGet* forms on this list.
    for probe in ('TryGetObjectField(TEXT("%s")', 'TryGetArrayField(TEXT("%s")',
                  'TryGetStringField(TEXT("%s")', 'TryGetNumberField(TEXT("%s")',
                  'TryGetBoolField(TEXT("%s")', 'JHasAny(In, { TEXT("%s")',
                  'HasField(TEXT("%s")'):
        if (probe % param) in body:
            return True
    return False


def read_depth(body, param):
    """Shallowest brace depth at which this parameter is READ. Large number if it is never read."""
    pattern = 'TEXT("%s")' % param
    depth, best = 0, 99
    for line in body.splitlines():
        # THE HOUSE VECTOR HELPERS COUNT AS READS. Recognising only the J* accessors made
        # `ReadVectorField(In, TEXT("location"), ...)` invisible, so add_socket's location/rotation/
        # scale read as NEVER READ - which this function scores as 99, the same as a genuinely
        # unread parameter, and the caller treats "never read at top level" as "conditional".
        # They are read at the handler's top level and applied on every target; the row was a false
        # positive for three passes. Four helpers, all in Source: ReadVectorField, ReadRotatorField,
        # ReadScaleField, ReadTripleField.
        if pattern in line and ("JStr" in line or "JNum" in line or "JBool" in line
                                or "JInt" in line or "JArray" in line
                                or "ReadVectorField" in line or "ReadRotatorField" in line
                                or "ReadScaleField" in line or "ReadTripleField" in line):
            best = min(best, depth)
        depth += line.count("{") - line.count("}")
    return best


def handlers(text):
    """(name, body) for each handler in a file."""
    marks = [(m.group(1), m.start()) for m in HANDLER.finditer(text)]
    for i, (name, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        yield name, text[start:end]


def main():
    rows, cleared = [], []
    for fname in sorted(os.listdir(SRC)):
        if not fname.endswith(".cpp"):
            continue
        with open(os.path.join(SRC, fname), encoding="utf-8", errors="replace") as f:
            text = f.read()
        for name, body in handlers(text):
            decl = DECL.search(body)
            if not decl:
                continue
            declared = PARAM.findall(decl.group(1))
            if len(declared) < 2:
                continue

            # Which declared parameters are compared against string literals in this body?
            modes = {}
            for var, literal in MODE_CMP.findall(body):
                # Tie the comparison variable back to a declared parameter by name similarity:
                # `const FString ManagerIn = JStr(In, TEXT("manager"), ...)` -> ManagerIn ~ manager.
                for p in declared:
                    if p.lower() in var.lower():
                        modes.setdefault(p, set()).add(literal)
            if not modes:
                continue

            others = [p for p in declared if p not in modes]
            if not others:
                continue

            # DOES THIS HANDLER ALREADY EXPLAIN THE MODE-DEPENDENCY?
            #
            # sculpt_landscape is the good example: `amount` is raise/lower only and `targetZ` is
            # flatten only, and it says so in its refusals. A parameter whose name appears in a Fail()
            # message has almost certainly been thought about; one that appears nowhere in any refusal
            # is the invoke_editor_tab shape - declared, accepted, and silently unused on some branch.
            #
            # This is what turns an 18-row list into a short one worth reading.
            fails = " ".join(FAIL_MSG.findall(body))
            unexplained = [p for p in others if p not in fails]
            if not unexplained:
                continue

            # READ AND CLEARED, by an explicit marker with a mandatory reason. See MODE_OK.
            marked = MODE_OK.search(body)
            if marked:
                cleared.append((name[2:], marked.group(1).strip()))
                continue

            # IS IT ACTUALLY CONDITIONAL? A parameter read at the handler's top level runs on every
            # path, so it cannot be mode-ignored no matter how many modes the handler has. Only a
            # read nested INSIDE a branch can be skipped.
            #
            # Without this, the tool lists every parameter of every mode-having handler and says
            # nothing - which is a shrug dressed as a finding. Brace depth is a crude proxy for
            # "inside a branch", and crude is fine here because the output is a review list: it
            # narrows where to look, it does not decide.
            conditional = [p for p in unexplained
                           if read_depth(body, p) > TOP_LEVEL and not presence_guarded(body, p)]
            if not conditional:
                continue
            rows.append((fname, name[2:], modes, conditional, len(others)))

    print("=" * 78)
    print("HANDLERS THAT BRANCH ON A MODE PARAMETER")
    print("=" * 78)
    print("Listed below are parameters that are DECLARED but never named in any refusal message in")
    print("their own handler. That is the invoke_editor_tab shape: accepted, and silently unused on")
    print("some branch. Handlers that already explain their mode-dependency - sculpt_landscape says")
    print("amount is raise/lower only and targetZ is flatten only - are filtered out.")
    print()
    for fname, ep, modes, unexplained, total in rows:
        mode_desc = "; ".join("%s in {%s}" % (m, ", ".join(sorted(v)[:4])) for m, v in modes.items())
        print("  %-30s %s" % (ep, mode_desc))
        print("      never named in any refusal (%d of %d declared): %s"
              % (len(unexplained), total, ", ".join(unexplained)))
    print()
    print("=" * 78)
    print("%d handler(s) worth a look, %d marked MODE-PARAMS-OK. This is a REVIEW LIST, not a"
          % (len(rows), len(cleared)))
    print("defect count - a parameter can be legitimately mode-independent and simply never")
    print("mentioned in a refusal.")
    if cleared:
        print("")
        print("READ AND CLEARED - marked in the handler, with the reason given:")
        for ep, why in cleared:
            print("  %-30s %s" % (ep, why[:74]))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
