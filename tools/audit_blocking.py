"""Find handlers that BLOCK the game thread, and whether they admit to it.

The companion to audit_modals.py. "The bridge stopped answering" has exactly two causes, and that
tool covers one of them:

  * a MODAL DIALOG spins its own loop            -> audit_modals.py
  * an UNBOUNDED WAIT occupies the ticker        -> this file

The second is the nastier of the two and had no tool at all. Handlers run inline in the HTTP ticker,
so a handler that waits is holding the very ticker that would have to advance whatever it is waiting
on. There is no dialog to click and no other thread to answer: it is self-deadlocking by
construction, and from the client it looks exactly like a crash.

WHAT IS BEING ENFORCED. Not "never block" - some of these are legitimate and bounded, and
FlushRenderingCommands before reading pixels is simply correct. The rule this project already follows
is weaker and more useful:

    A handler that can stall the bridge must SAY SO, in its own comment block.

02_GOTCHAS.md section 8 keeps a table of declared blocking hazards, and "declared" there means the
endpoint's own comment states it. That convention is worth something only if something checks it,
which is what this does. An undeclared blocker is the finding - not because it is necessarily wrong,
but because the next person to read that handler has no way to know.

LIMITATIONS, stated rather than hidden:
  * Enclosing-handler detection is lexical. A blocking call in a shared helper is attributed to the
    helper, not to the endpoints that reach it.
  * "Declared" is a keyword test over the handler's own text. A handler that discusses the hazard in
    words this does not know will read as undeclared. That errs toward false alarms, which is the
    right direction here.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harvest_param_table import blank_comments_and_strings   # the ONE shared scrubber

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "Source", "MifBridge", "Private")

# (call, what it waits on, whether it is inherently bounded)
BLOCKERS = [
    ("FlushAsyncLoading", "every pending async load, engine-wide - not just this call's", False),
    ("FlushLevelStreaming", "streaming to settle; cascades into FlushAsyncLoading (World.cpp:4533)", False),
    ("WaitForCompletion", "an asset-registry or task graph job with no deadline of its own", False),
    ("ScanPathsSynchronous", "a full synchronous scan of the paths given - unbounded on a mount root", False),
    ("WaitUntilTasksComplete", "task-graph work; pumps the named-thread queue while it waits", False),
    ("MakeDialog", "a Slate progress window - NOT cancelled by the unattended guard, which excludes "
                   "slow-task windows explicitly (SlateApplication.cpp:1990)", False),
    ("FPlatformProcess::Sleep", "nothing at all - it just stops the ticker for the duration", False),
    # Bounded, and listed so the report shows the whole picture rather than only the alarming half.
    ("FlushRenderingCommands", "one game/render-thread sync; bounded, tens to hundreds of ms", True),
]

# Words that count as the handler admitting to it.
DECLARED = ("block", "stall", "synchronous", "declared", "hazard", "bounded", "freeze",
            "hang", "wait", "slow", "self-managed", "deadlock")

HANDLER = re.compile(r"^\s*void\s+(H_\w+)\s*\(")


def enclosing_handler(lines, idx):
    """(name, start_index) of the H_ function containing line idx, or (None, None)."""
    for i in range(idx, -1, -1):
        m = HANDLER.match(lines[i])
        if m:
            return m.group(1), i
        # A closing brace at column 0 means we left the previous function without finding one.
        if lines[i].startswith("}") and i < idx - 1:
            return None, None
    return None, None


def is_code(line):
    s = line.strip()
    if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
        return False
    return True


def declared_near(lines, start, idx):
    """Does the handler (or the comment block above it) admit to blocking?

    The window is the handler's own text up to the call, plus 25 lines of comment above the
    signature, which is where this codebase puts its endpoint contracts.
    """
    top = max(0, start - 25)
    window = " ".join(lines[top:idx + 1]).lower()
    return any(w in window for w in DECLARED)


def main():
    if not os.path.isdir(SRC):
        print("source not found at %s" % SRC)
        return 2

    undeclared, declared, bounded = [], [], []
    for fname in sorted(os.listdir(SRC)):
        if not fname.endswith(".cpp"):
            continue
        with open(os.path.join(SRC, fname), encoding="utf-8", errors="replace") as f:
            text = f.read()
        lines = text.splitlines()
        # MATCH AGAINST SCRUBBED TEXT, ATTRIBUTE FROM THE ORIGINAL.
        #
        # is_code() drops comment lines, which is not enough: MifBridgeDescribe.cpp:297 is a
        # generated notes-table entry, and the note reads "FPhysicsAssetUtils::CreateFromSkeletalMesh
        # puts up an FScopedSlowTask MakeDialog, and a modal deadlocks the bridge". That is prose
        # explaining why autoFit is NOT offered - the exact opposite of a blocking call - and it kept
        # this tool at exit 1 with one UNDECLARED finding, which is the state in which a genuinely
        # new blocker would have been invisible.
        #
        # The scrubber blanks comments AND string literals while preserving line count, so `probe`
        # lines up with `lines` and attribution still reads the real source.
        probe_lines = blank_comments_and_strings(text).splitlines()
        for i, line in enumerate(lines):
            if not is_code(line):
                continue
            probe = probe_lines[i] if i < len(probe_lines) else line
            for call, why, is_bounded in BLOCKERS:
                if call not in probe:
                    continue
                name, start = enclosing_handler(lines, i)
                where = "%s:%d" % (fname, i + 1)
                label = name or "(shared helper - attributed to no single endpoint)"
                if is_bounded:
                    bounded.append((where, label, call))
                elif name and declared_near(lines, start, i):
                    declared.append((where, label, call))
                else:
                    undeclared.append((where, label, call, why))

    print("=" * 78)
    print("BLOCKING CALLS IN HANDLERS")
    print("=" * 78)
    for where, label, call in bounded:
        print("  bounded    %-34s %-28s %s" % (where, call, label))
    for where, label, call in declared:
        print("  declared   %-34s %-28s %s" % (where, call, label))
    for where, label, call, why in undeclared:
        print("  UNDECLARED %-34s %-28s %s" % (where, call, label))
        print("             waits on %s" % why)

    print()
    print("=" * 78)
    print("bounded %d   declared %d   UNDECLARED %d"
          % (len(bounded), len(declared), len(undeclared)))
    if undeclared:
        print()
        print("An undeclared blocker is not automatically a bug - but a handler that can stall the")
        print("bridge and does not say so leaves the next reader no way to find out. State it in the")
        print("handler's comment block, and add it to the table in 02_GOTCHAS.md section 8.")
    print("=" * 78)
    return 1 if undeclared else 0


if __name__ == "__main__":
    sys.exit(main())
