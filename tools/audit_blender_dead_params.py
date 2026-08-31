"""Find Blender op parameters the addon ACCEPTS and nothing ever reads.

THE BLENDER ARM OF audit_dead_params.py, same question and same reasoning, different corpus. Read
that file's header for the argument; this one only records what differs.

WHY IT IS WORTH HAVING TWICE. reject_unknown is the addon's answer to the silent-parameter-ignore
class - send a name the op does not know and it refuses, loudly. The blind spot is the worse half: a
name ON the accepted list passes by definition, and if nothing then reads it the call succeeds,
reports ok, and does nothing with the thing the caller asked for. That is the same silent wrong
result the guard exists to end, arriving through the door the guard holds open.

Until 2026-08-31 the Blender half had two audit tools to the UE half's twenty, and neither asked this
question. It went unasked long enough that the answer was worth finding out.

SCOPE IS MODULE-WIDE AND DELIBERATELY PERMISSIVE, exactly as on the UE side. A key counts as read if
its literal appears anywhere in the op's own module outside the reject_unknown call - in a take()
call, in a shared resolver, in a mapping table. create_primitive is why: it accepts "fillType" and
reads it through a module-level dict, `EXTRA_KEYS = {"fillType": "fill_type", ...}`, so a scan
confined to the function body would call a working parameter dead. False positives are what kill an
audit tool's credibility, and the regression actually being guarded against - a name added to an
accept list and never wired up - appears nowhere else at all.

CASE MATTERS AND COST A WRONG ANSWER ALREADY. param_reach lowercases its keys, and a first pass at
this question compared those against the addon's camelCase source. decimate_mesh.targetTriangles and
create_primitive.fillType both read as dead and both are read perfectly well - the classifier was
matching "targettriangles" against a file that says "targetTriangles". Comparison here is
case-insensitive on both sides.

Usage:
    python tools/audit_blender_dead_params.py

Talks to nothing. Addon source only.
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

sys.path.insert(0, HERE)

OP_DEF = re.compile(r"^def op_(\w+)\s*\(", re.M)
# The reject_unknown call itself, so its own literals can be excluded from "appears elsewhere".
REJECT = re.compile(r"reject_unknown\s*\(", re.S)
LITERAL = re.compile(r"""["']([A-Za-z_][A-Za-z0-9_]*)["']""")

# Accepted and ignored ON PURPOSE, with the reason. The UE tool's header makes the same point: a
# deliberately ignored parameter is fine and the next reader has no way to tell it from an oversight
# unless it is written down.
INTENTIONAL = {}


def match_paren(text, open_idx):
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def scan():
    """[(op, key, module)] for every accepted key whose literal appears only in the accept list."""
    try:
        import parity_check as PC
    except Exception as exc:
        print("could not import parity_check (%s) - it owns the accept-list parser." % exc)
        return None
    problems = []
    ops = PC.load_addon_ops(problems) or {}
    if len(ops) < 30:
        print("SELF-CHECK FAILED: parity_check resolved only %d addon ops." % len(ops))
        return None

    dead = []
    for op, entry in sorted(ops.items()):
        accepts = entry.get("accepts")
        if not accepts:
            continue                       # unresolved accept list is parity_check's problem to say
        source = entry.get("source") or ""
        module = source.split(":")[0] if source else ""
        path = os.path.join(ADDON, module)
        if not module or not os.path.isfile(path):
            continue
        text = io.open(path, encoding="utf-8", errors="replace").read()

        # Blank every reject_unknown call so its own literals do not count as a read.
        blanked = text
        for m in REJECT.finditer(text):
            close = match_paren(text, text.index("(", m.start()))
            if close > 0:
                blanked = blanked[:m.start()] + " " * (close - m.start() + 1) + blanked[close + 1:]

        present = {n.lower() for n in LITERAL.findall(blanked)}
        for key in sorted(accepts):
            if key.lower() in present:
                continue
            if INTENTIONAL.get("%s.%s" % (op, key)):
                continue
            dead.append((op, key, module))
    return dead


def main():
    dead = scan()
    if dead is None:
        return 2
    print("addon ops with a resolved accept list: scanned")
    print("parameters accepted and never read elsewhere in their module: %d" % len(dead))
    print("")
    if not dead:
        print("OK  every accepted key appears somewhere other than the accept list.")
        print("")
        print("That is a WEAK all-clear by construction - module-wide scope will not notice a name")
        print("read by the wrong op's helper. It catches the regression it was built for: a")
        print("parameter added to an accept list and never wired up, which appears nowhere else.")
        return 0
    for op, key, module in dead:
        print("  %-28s %-24s %s" % (op, key, module))
    print("")
    print("Each of these is a name a caller can send, that passes the guard, and that nothing")
    print("reads. Wire it, remove it from the accept list, or add it to INTENTIONAL with the")
    print("reason - all three are fine and silence is not.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
