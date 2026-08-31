"""Prove each audit tool still FIRES, by planting a defect it claims to catch and watching it go red.

WHY THIS EXISTS. On 2026-08-31 a check was added to mcp_static_check that printed OK on every run
without ever executing - it had been wired in after main()'s `if not findings: return 0`. The finder
was correct; the wiring was dead code. Reading the diff passed. Calling the function passed. Only
running the entry point against a planted defect caught it.

That is not a one-off. This repo has already been bitten by tool ROT once before: audit_prose_
dependence found that 5 of the 7 tools it drives were intercepting ZERO reads, because the paths it
compared contained `tools/../Source` and never matched. Every one of those tools reported success
throughout. A green audit is worth exactly as much as the evidence that it can go red.

Most of these tools WERE mutation-tested when they were written - by hand, once, and never again. A
regex drifts, a corpus path changes, a refactor moves a call site, and the tool goes quietly silent
while still exiting 0. This makes that check repeatable.

HOW IT WORKS. For each entry: read the target file as BYTES, apply a textual plant, run the tool as a
SUBPROCESS the way CI runs it, require it to go red, then restore the original bytes and assert they
came back identical. A global digest of Source/ is taken before and after the whole run; if anything
under Source/ differs at the end, that is reported as an ERROR regardless of how the checks went.

WHAT "RED" MEANS. Exit code alone is not enough - a tool can exit 1 for an unrelated pre-existing
finding, which would let a broken detector pass on somebody else's failure. So an entry passes only
if the tool goes non-zero AND its output names the planted marker. Both, or it is not proof.

COVERAGE IS REPORTED, NOT ASSUMED. Tools with no plant defined are listed as NOT PROVEN. That is the
entire point: the failure this file exists to prevent is a checker that is silently absent, so a
silently absent ENTRY would repeat the bug one level up.

Usage:
    python tools/audit_detectors_fire.py            # run every plant
    python tools/audit_detectors_fire.py --list     # show coverage without touching a file
"""
import hashlib
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PRIV = os.path.join(ROOT, "Source", "MifBridge", "Private")
SERVER = os.path.join(HERE, "mcp-server", "server.py")

# Every tool that scans code and reports findings. A name here with no PLANT entry is reported as
# unproven rather than skipped quietly.
DETECTORS = sorted(f for f in os.listdir(HERE)
                   if f.endswith(".py") and (f.startswith("audit_") or f in (
                       "parity_check.py", "mcp_static_check.py", "param_reach.py"))
                   and f != "audit_report.py" and f != "audit_detectors_fire.py")


def plant_bind(text):
    """A MIF_BIND with no _post() wrapper in server.py - parity_check's CHECK 3.

    NOT a MIF_DECL without a MIF_BIND, which was this entry's first version and made parity_check
    look ASLEEP. That pair is a LINK ERROR, so the compiler owns it and the tool deliberately does
    not look - it says so in its own header. A plant aimed at something a tool has consciously
    delegated does not prove the tool is blind; it proves the plant was aimed wrong. Read the tool's
    stated contract before believing this harness's verdict about it.
    """
    m = re.search(r"\n(\t+MIF_BIND\([a-z0-9_]+\);)", text)
    if not m:
        return None
    indent = re.match(r"\n(\t+)", m.group(0)).group(1)
    return text[:m.end(1)] + "\n" + indent + "MIF_BIND(mif_probe_zz);" + text[m.end(1):]


def plant_confirm(text):
    """An endpoint that ACCEPTS confirm and never reads it - a promise it does not keep."""
    needle = 'RejectUnknownParams(In, Out, { TEXT("partitioned") },'
    if needle not in text:
        return None
    return text.replace(needle,
                        'RejectUnknownParams(In, Out, { TEXT("partitioned"), TEXT("confirm") },', 1)


def plant_unbound(text):
    """A wrapper that names a parameter its signature does not declare - the move_tree_widget shape."""
    m = re.search(r"\n(def [a-z0-9_]+\([^)]*\)[^\n]*\n)", text)
    if not m:
        return None
    # reference an undeclared name in the body's first _post call
    j = text.find("_post(", m.end(1))
    if j < 0:
        return None
    k = text.find(")", j)
    if k < 0:
        return None
    return text[:k] + ", probeKey_zz=mif_probe_zz_unbound" + text[k:]


def plant_unreachable(text):
    """A checker wired in after main()'s clean-path `return 0` - the 2026-08-31 defect itself."""
    fixed = "    lossy = lossy_bool_forwards()\n    if lossy:"
    if fixed not in text:
        return None
    return text.replace(fixed,
                        '    if not findings:\n'
                        '        print("OK  every one can be called - no unbound names")\n'
                        '        return 0\n\n'
                        '    lossy = lossy_bool_forwards()\n    if lossy:', 1)



def plant_silent_mutator(text):
    """A handler that calls a void UE API and reports ok without reading anything back.

    SetActorLabel is on audit_postconditions' SILENT_APIS list because the editor may uniquify the
    label it actually assigns, so "nothing threw" is not "the name is what you asked for". The plant
    is a whole handler rather than an edit to a real one: it has to MUTATE and have no read-back, and
    removing an existing verification would be a fragile deletion plant that breaks whenever that
    handler is refactored.
    """
    # The (\t*) matters. These handlers live inside a namespace, so every one is tab-indented; the
    # first version anchored at column zero, matched nothing, and reported the tool NOT proven -
    # the third anchor mistake in this file, and the third to look exactly like tool rot.
    m = re.search(r"\n(\t*)void\s+H_[A-Za-z0-9_]+\s*\(\s*const\s+TSharedRef<FJsonObject>&\s*In",
                  text)
    if not m:
        return None
    t = m.group(1)
    probe = ("\n"
             + t + "void H_mif_probe_zz(const TSharedRef<FJsonObject>& In, "
                   "const TSharedRef<FJsonObject>& Out)\n"
             + t + "{\n"
             + t + "\tAActor* Probe = nullptr;\n"
             + t + "\tProbe->SetActorLabel(TEXT(\"zz\"));\n"
             + t + "\tOut->SetBoolField(TEXT(\"ok\"), true);\n"
             + t + "}\n")
    return text[:m.start() + 1] + probe.lstrip("\n") + text[m.start() + 1:]


def plant_loop_write(text):
    """A per-item fact written into the ONE response object from inside a loop - last wins."""
    m = re.search(r"\n(\t+)for \([^\n]*\)\n\1\{\n", text)
    if not m:
        return None
    indent = m.group(1) + "\t"
    line = indent + 'Out->SetStringField(TEXT("probeNote_zz"), TEXT("x"));\n'
    return text[:m.end()] + line + text[m.end():]


def plant_refused_key(text):
    """A suite passing a parameter its endpoint refuses by name - T44's green-for-weeks shape."""
    needle = 'M.call("list_layers", {"limit": 400})'
    if needle not in text:
        return None
    return text.replace(needle, 'M.call("list_layers", {"limit": 400, "probeKey_zz": 1})', 1)



def plant_modal(text):
    """An unguarded call to an API that can open a MODAL DIALOG.

    A modal is worse than a crash here: handlers run inline on the game thread that answers HTTP, so
    the bridge stops responding while the editor still looks alive. audit_modals reports 0 unguarded
    today and exits 0, which is what makes "UNGUARDED" a safe marker - it cannot already be in the
    output for some other reason.
    """
    m = re.search(r"\n(\t*)void\s+H_[A-Za-z0-9_]+\s*\(\s*const\s+TSharedRef<FJsonObject>&\s*In",
                  text)
    if not m:
        return None
    t = m.group(1)
    probe = (t + "void H_mif_probe_modal_zz(const TSharedRef<FJsonObject>& In, "
                 "const TSharedRef<FJsonObject>& Out)\n"
             + t + "{\n"
             + t + "\tAssetTools.DuplicateAsset(TEXT(\"zz\"), TEXT(\"/Game/zz\"), nullptr);\n"
             + t + "}\n")
    return text[:m.start() + 1] + probe + text[m.start() + 1:]


# tool -> (target file, plant function, marker that must appear in the tool's output)
PLANTS = {
    "parity_check.py": (os.path.join(PRIV, "MifBridgeCommon.cpp"), plant_bind, "mif_probe_zz"),
    "audit_promise_flags.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_confirm, "confirm"),
    "mcp_static_check.py": (SERVER, plant_unbound, "mif_probe_zz_unbound"),
    "audit_postconditions.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_silent_mutator,
                                "mif_probe_zz"),
    "audit_loop_writes.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_loop_write,
                             "probeNote_zz"),
    "audit_suite_payloads.py": (os.path.join(HERE, "test_layers.py"), plant_refused_key,
                                "probeKey_zz"),
    "audit_modals.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_modal, "UNGUARDED"),
    # NOT "RULE 4" - that string is in the rules footer this tool prints on every red run, and the
    # already-red guard correctly refused to call that proof. The marker has to be text only a
    # FINDING can produce.
    "audit_vacuous_checks.py": (os.path.join(HERE, "mcp_static_check.py"), plant_unreachable,
                                "is only reached once main() has already returned 0"),
}

# Extra argv some tools need to report everything rather than only new-against-baseline findings.
ARGS = {"audit_vacuous_checks.py": ["--all"]}


def source_digest():
    """SHA-256 over the CONTENT of every source file under Source/.

    Content, not mtime. The first version hashed size+mtime and tripped on every run, because
    restoring a file byte-for-byte still moves its mtime - so the guard cried "Source/ changed"
    while `git status` showed it clean. A guard that fires on a correct run gets switched off.
    """
    h = hashlib.sha256()
    for base, _, names in os.walk(os.path.join(ROOT, "Source")):
        for n in sorted(names):
            if not n.endswith((".cpp", ".h", ".cs", ".inl")):
                continue
            p = os.path.join(base, n)
            try:
                h.update(os.path.relpath(p, ROOT).encode("utf-8"))
                h.update(io.open(p, "rb").read())
            except OSError:
                continue
    return h.hexdigest()


def run(tool):
    argv = [sys.executable, os.path.join(HERE, tool)] + ARGS.get(tool, [])
    r = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def prove(tool):
    """(status, detail). status is one of proven / ASLEEP / anchor-gone / already-red."""
    target, planter, marker = PLANTS[tool]
    if not os.path.isfile(target):
        return "anchor-gone", "target file missing: %s" % os.path.basename(target)

    before_rc, before_out = run(tool)
    original = io.open(target, "rb").read()
    # Normalise BEFORE planting. Every needle in this file is written with \n, and every file it
    # targets is CRLF, so planting against the raw decode silently found no anchor and reported
    # "the tool was NOT proven" - a false alarm that looks exactly like tool rot.
    text = original.decode("utf-8", "replace").replace("\r\n", "\n")
    mutated = planter(text)
    if mutated is None:
        return "anchor-gone", ("the plant's anchor is no longer in %s - the tool was NOT proven"
                               % os.path.basename(target))
    # A marker already present before planting would make the check pass for the wrong reason.
    if marker in before_out and before_rc != 0:
        return "already-red", ("%s already reports %r before planting, so this run proves nothing"
                               % (tool, marker))

    nl = "\r\n" if b"\r\n" in original else "\n"
    io.open(target, "w", encoding="utf-8", newline="").write(mutated.replace("\n", nl))
    try:
        rc, out = run(tool)
    finally:
        io.open(target, "wb").write(original)
        if io.open(target, "rb").read() != original:
            return "ASLEEP", "RESTORE FAILED on %s - fix this by hand before anything else" % target

    if rc != 0 and marker in out:
        return "proven", "went red on the planted %s" % marker
    if rc == 0:
        return "ASLEEP", "exited 0 with the defect planted - it is not looking"
    return "ASLEEP", ("went red but never named %r, so it failed on something else"
                      % marker)


def main():
    listing = "--list" in sys.argv
    covered = [t for t in DETECTORS if t in PLANTS]
    uncovered = [t for t in DETECTORS if t not in PLANTS]

    print("%d detector(s) in tools/; %d have a plant, %d do NOT"
          % (len(DETECTORS), len(covered), len(uncovered)))
    if listing:
        for t in covered:
            print("  plant   %s" % t)
        for t in uncovered:
            print("  NONE    %s" % t)
        return 0

    before = source_digest()
    asleep, notproven = [], []
    for tool in covered:
        status, detail = prove(tool)
        print("  %-26s %-12s %s" % (tool, status, detail))
        if status == "ASLEEP":
            asleep.append(tool)
        elif status != "proven":
            notproven.append(tool)

    after = source_digest()
    print("")
    if before != after:
        print("ERROR - Source/ changed across this run. A plant was not restored, or a tool wrote to")
        print("the tree. Check `git status` before trusting anything above.")
        return 2

    print("Source/ is byte-identical to before this run.")
    if uncovered:
        print("")
        print("NOT PROVEN - no plant is defined for these, so their green means nothing here:")
        for t in uncovered:
            print("  %s" % t)
        print("Add an entry to PLANTS. Listing them is deliberate: a silently missing entry would be")
        print("the same bug this file exists to catch, one level up.")
    if asleep:
        print("")
        print("ASLEEP - these did not react to a defect they claim to catch:")
        for t in asleep:
            print("  %s" % t)
        return 1
    if notproven:
        print("")
        print("INCONCLUSIVE - the plant could not be applied, so nothing was proven:")
        for t in notproven:
            print("  %s" % t)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
