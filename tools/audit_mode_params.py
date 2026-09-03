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
  * MODE-PARAMS-OK, 2026-09-03, for refusals built from a TABLE - the name is assembled at runtime
    and no message literal contains it, so create_procedural_mesh went on being listed for ten
    parameters it had started refusing.
  * the full READERS list, same day, after add_socket's location/rotation/scale and
    invoke_editor_tab's probeIds were reported as mode-ignored: they go through house helpers
    (ReadVectorField, UiReadStringArray) that this scan did not recognise as reads at all.
  * HasField in presence_guarded, same day - five TryGet* spellings were listed and the plainest
    one was not, which is how set_viewport_camera came back a second time on different parameters.
  * branch depth rather than brace depth, same day, after invoke_editor_tab and create_landscape
    were listed for parameters read inside `{ FString Err; ... }` - a bare scoping block, which is
    this codebase's idiom for keeping an error string out of the enclosing scope and is not a
    branch at all.
Each pass cut noise without cutting the one row that started this - invoke_editor_tab. On
2026-09-03 the list went 23 -> 10 that way, with 9 handlers actually fixed and 8 marked
MODE-PARAMS-OK; every clearance above was checked by reading the handler, because a shorter list
that is shorter for the wrong reason is worse than the long one.
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


# EVERY FUNCTION THAT READS A NAMED FIELD OFF `In`, enumerated from Source rather than remembered:
#
#   grep -rhoE "[A-Za-z_]+\(In, TEXT\(" Source/MifBridge/Private/*.cpp | sort | uniq -c
#
# Recognising only the J* four made every helper-read parameter score as NEVER READ, which this
# file's caller cannot distinguish from "read inside a mode branch" - so add_socket's
# location/rotation/scale and invoke_editor_tab's probeIds were reported as mode-ignored while being
# read at the handler's top level on every path. Re-run that grep before trusting a clean list; a
# new helper added to Source and not added here fails in the direction that manufactures findings.
READERS = ("JStr", "JNum", "JBool", "JInt", "JArray",
           "ReadVectorField", "ReadRotatorField", "ReadScaleField", "ReadTripleField",
           "ResolveNodeField", "ParsePinSpecs", "UiReadStringArray")

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
    """Shallowest BRANCH depth at which this parameter is READ. Large number if it is never read.

    Branch depth, not brace depth. A BARE SCOPING BLOCK IS NOT A BRANCH: this codebase wraps a read
    in `{ FString ArrError; ... }` to keep an error string out of the enclosing scope, and counting
    that as nesting made invoke_editor_tab's probeIds look like it sat inside a mode branch when it
    is read on every path. Cancelling the opening brace alone is not enough - the matching close
    still decrements and the count drifts negative for the rest of the handler - so the braces are
    tracked on a stack and only the branching ones are counted.

    A line that is nothing but `{`, whose previous code line does not end a control statement
    (`)` for if/for/while/switch, or the word `else`), opens scope rather than a condition.
    """
    pattern = 'TEXT("%s")' % param
    best = 99
    stack = []          # one entry per open brace: True when it is a branch rather than bare scope
    prev_code = ""
    for line in body.splitlines():
        stripped = line.strip()
        depth = sum(1 for is_branch in stack if is_branch)
        if pattern in line and any(r in line for r in READERS):
            best = min(best, depth)
        opens = line.count("{")
        closes = line.count("}")
        bare = (stripped == "{"
                and not prev_code.endswith(")")
                and not prev_code.endswith("else"))
        for i in range(opens):
            stack.append(not (bare and i == 0))
        for _ in range(closes):
            if stack:
                stack.pop()
        if stripped:
            prev_code = stripped
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
