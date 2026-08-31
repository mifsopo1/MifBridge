"""ASK BEFORE YOU FILE: has this capability already been considered and declined?

    python tools/why_not.py add            # every refusal whose key or reason mentions "add"
    python tools/why_not.py niagara add    # both terms must appear somewhere in the entry
    python tools/why_not.py                # the summary, and how many decisions are recorded

WHY THIS EXISTS, from getting it wrong on 2026-08-31. I filed a backlog item saying nothing creates a
Niagara user parameter, researched the engine API, and confirmed it was buildable. Then I read the
handler and found `set_niagara_user_parameter` already REFUSES an `add` parameter by name:

    "this sets an EXISTING parameter. Adding one is not offered: a user parameter no emitter reads is
     invisible in the editor and does nothing, so creating one by typo is worse than being told the
     name is unknown"

Not a missing feature. A decision, with its reasoning attached, made before I arrived. Reading the
ENDPOINT LIST told me there was no add; reading the HANDLER would have told me there was no add ON
PURPOSE. Those are different findings and only one is worth anyone's time.

There are 870 of these notes across 349 endpoints - the fifth argument to RejectUnknownParams, where
a rejected key carries the reason it is rejected. That is a large, carefully-written record of design
decisions that was greppable only if you already knew it existed and what C++ to grep for. This makes
it a question you can ask.

WHAT IT IS NOT. Not every note is a design decision - many are simple redirects ("use path", "that is
the sublevel selector"). The output is a reading list; the decisions are the ones whose reason
explains rather than redirects, and a human can tell those apart at a glance. Same contract as the
other audits here: exit 0 always, print what a person should read.

The accepted-key parsing is IMPORTED from harvest_param_table rather than re-implemented, because
that file already solves comment scrubbing, template call sites and guards reached through a shared
helper, and a second parser would drift from it the first time either changed.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harvest_param_table as H            # one parser, not two

# The note map is a list of { TEXT("key"), TEXT("reason") } braces. Two shapes occur and the first
# version of this handled only one, which is why it silently missed the very decision that prompted
# the tool: a long reason is usually ONE TEXT() holding ADJACENT string literals -
# TEXT("a " "b " "c") - not repeated TEXT() calls. Splitting on the braces and joining every quoted
# literal in each chunk handles both, and does not care how the author wrapped the lines.
CHUNK = re.compile(r"\{(.*?)\}", re.S)
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def literals(chunk):
    return [m.group(1).replace('\\"', '"').replace("\\n", " ") for m in LITERAL.finditer(chunk)]


def entries():
    rows, _missing, _problems, _decls = H.harvest()
    out = []
    for ep, guard, _via in rows:
        for chunk in CHUNK.findall(guard.get("notes") or ""):
            parts = literals(chunk)
            if len(parts) >= 2:
                key, reason = parts[0].strip(), " ".join(p.strip() for p in parts[1:]).strip()
                reason = re.sub(r"\s+", " ", reason)
                if key and reason:
                    out.append((ep, key, reason))
    return out


def main():
    terms = [t.lower() for t in sys.argv[1:] if not t.startswith("-")]
    rows = entries()

    if not terms:
        eps = len({e for e, _, _ in rows})
        print("%d recorded decisions across %d endpoints - every parameter this bridge REFUSES,"
              % (len(rows), eps))
        print("with the reason it refuses it.")
        print()
        print("Ask before filing a gap:  python tools/why_not.py <term> [<term> ...]")
        print()
        print("Reading the endpoint list tells you a capability is absent. Reading the handler tells")
        print("you whether it is absent ON PURPOSE. This is the second question, made cheap.")
        return 0

    hits = [(ep, key, reason) for ep, key, reason in rows
            if all(t in (ep + " " + key + " " + reason).lower() for t in terms)]

    if not hits:
        print("no recorded decision mentions %s." % " + ".join(repr(t) for t in terms))
        print("That is not proof nothing was decided - the reason may be worded differently, or the")
        print("decision may live in a comment rather than a refusal. Read the handler before filing.")
        return 0

    print("%d recorded decision(s) matching %s:" % (len(hits), " + ".join(repr(t) for t in terms)))
    print()
    for ep, key, reason in sorted(hits):
        print("  %s  refuses  `%s`" % (ep, key))
        # Wrap the reason so a long one stays readable in a terminal.
        words, line = reason.split(), "      "
        for w in words:
            if len(line) + len(w) + 1 > 96:
                print(line)
                line = "      " + w
            else:
                line = (line + " " + w) if line.strip() else "      " + w
        if line.strip():
            print(line)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
