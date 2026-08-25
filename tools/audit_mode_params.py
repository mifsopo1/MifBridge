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


def handlers(text):
    """(name, body) for each handler in a file."""
    marks = [(m.group(1), m.start()) for m in HANDLER.finditer(text)]
    for i, (name, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        yield name, text[start:end]


def main():
    rows = []
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
            rows.append((fname, name[2:], modes, unexplained, len(others)))

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
    print("%d handler(s) worth a look. This is a REVIEW LIST, not a defect count - a parameter can" % len(rows))
    print("be legitimately mode-independent and simply never mentioned in a refusal.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
