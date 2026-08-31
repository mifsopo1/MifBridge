"""Find handler text that tells the caller to do something no endpoint can do.

WHY THIS EXISTS. Twice in one night a real gap was found by reading an endpoint's own words rather
than by auditing its code:

  * uv_unwrap's ANGLE method warns "NO seams marked ... Mark seams first" - and nothing in the addon
    could set edge.use_seam. It had been offering a method its own callers could not use.
  * set_material_slots reported polygonsOutOfRange and advised "fix it by ... reassigning those
    faces". That one turned out to be reachable, but only because a DIFFERENT endpoint existed;
    reading the advice is what sent me looking.

An endpoint that says "do X first" is asserting that X is possible. When it is not, that sentence is
the gap, written down by the person closest to it, sitting in the source waiting to be read.

WHAT THIS DOES. Collect imperative advice out of Fail()/MifOpError/warning strings - the "use X",
"call X", "X first", "pass X" shapes - and report the ones naming a verb that matches no endpoint
and no addon op. Deliberately noisy-but-small: it is a READING LIST, not a defect list. Most hits
will be advice about a parameter or a UI action, which is fine and not a gap.

NOT A GATE. It exits 0 always. A tool that fails the build over prose would be gamed by rewording
the prose, which would make the source worse.
"""
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CPP = os.path.join(ROOT, "Source", "MifBridge", "Private")
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

# "use uv_unwrap", "call add_anim_notify_track first", "X creates them", "pass Y"
ADVICE = re.compile(
    r"\b(?:use|call|run|try)\s+([a-z][a-z0-9_]{4,})\b"
    r"|\b([a-z][a-z0-9_]{4,})\s+(?:creates?|makes?|adds?|enumerates?|finds?)\s+(?:them|one|it)\b"
    r"|\b([a-z][a-z0-9_]{4,})\s+first\b")

# Words that look like endpoints but are prose or engine API, not something we could provide.
IGNORE = {
    "instead", "rather", "either", "before", "after", "please", "should", "would", "could",
    "restart", "python", "engine", "editor", "blender", "unreal", "return", "returns",
    "reload", "reopen", "select", "delete", "remove", "create", "update", "change",
    "anything", "another", "something", "nothing", "someone", "somebody", "yourself",
}


def known_names():
    names = set()
    h = os.path.join(CPP, "MifBridgeHandlers.h")
    if os.path.isfile(h):
        for line in io.open(h, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*MIF_DECL\((\w+)\)", line)
            if m:
                names.add(m.group(1))
    for path in glob.glob(os.path.join(ADDON, "*.py")):
        src = io.open(path, encoding="utf-8", errors="replace").read()
        names.update(re.findall(r'^\s*"([a-z0-9_]+)":\s*op_', src, re.M))
    # MCP TOOL NAMES TOO, or advice pointing at mif_help - which is a real tool with no C++
    # endpoint behind it - reads as a gap. A scanner that cries wolf about its own documentation
    # is one nobody runs twice.
    server = os.path.join(HERE, "mcp-server", "server.py")
    if os.path.isfile(server):
        src = io.open(server, encoding="utf-8", errors="replace").read()
        names.update(re.findall(r"^def ([a-z][a-z0-9_]*)\(", src, re.M))
    # Addon module-level helpers an addon message may legitimately name.
    for path in glob.glob(os.path.join(ADDON, "*.py")):
        src = io.open(path, encoding="utf-8", errors="replace").read()
        names.update(re.findall(r"^def ([a-z][a-z0-9_]*)\(", src, re.M))
    return names


def scan():
    names = known_names()
    hits = {}
    files = glob.glob(os.path.join(CPP, "*.cpp")) + glob.glob(os.path.join(ADDON, "*.py"))
    for path in files:
        base = os.path.basename(path)
        src = io.open(path, encoding="utf-8", errors="replace").read()
        for i, line in enumerate(src.split("\n"), 1):
            # Only look inside strings - advice lives in messages, not in code.
            for lit in re.findall(r'"([^"]{12,})"', line):
                for m in ADVICE.finditer(lit):
                    word = next(g for g in m.groups() if g)
                    if word in IGNORE or word in names:
                        continue
                    # A word nobody could mistake for a verb-object endpoint name.
                    if "_" not in word:
                        continue
                    hits.setdefault(word, []).append("%s:%d" % (base, i))
    return names, hits


def main():
    names, hits = scan()
    print("known endpoints and addon ops: %d" % len(names))
    if not hits:
        print("")
        print("no advice naming an unknown operation - every 'use X' / 'X first' in a message")
        print("names something this bridge can actually do.")
        return 0
    print("")
    print("ADVICE NAMING SOMETHING THAT IS NOT AN ENDPOINT OR ADDON OP.")
    print("A READING LIST, not a defect list - most will be prose or a UI action. The ones worth")
    print("acting on are where a handler tells a caller to do something nothing can do:")
    print("")
    for word in sorted(hits, key=lambda w: -len(hits[w])):
        where = hits[word]
        print("  %-34s %d mention(s)  %s" % (word, len(where), ", ".join(sorted(set(where))[:3])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
