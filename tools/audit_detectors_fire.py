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



def plant_blocker(text):
    """An unbounded wait on the game thread - audit_modals' companion failure."""
    m = re.search(r"\n(\t*)void\s+H_[A-Za-z0-9_]+\s*\(\s*const\s+TSharedRef<FJsonObject>&\s*In",
                  text)
    if not m:
        return None
    t = m.group(1)
    probe = (t + "void H_mif_probe_block_zz(const TSharedRef<FJsonObject>& In, "
                 "const TSharedRef<FJsonObject>& Out)\n"
             + t + "{\n"
             + t + "\tFPlatformProcess::Sleep(30.0f);\n"
             + t + "}\n")
    return text[:m.start() + 1] + probe + text[m.start() + 1:]


def plant_dead_param(text):
    """A name ON the accepted list that nothing reads - the blind spot in RejectUnknownParams.

    The guard refuses names it does not know, which is why this is the worse half: an accepted name
    passes by definition, so the call succeeds, reports ok, and does nothing with what was sent.
    """
    needle = 'RejectUnknownParams(In, Out, { TEXT("partitioned") },'
    if needle not in text:
        return None
    return text.replace(
        needle, 'RejectUnknownParams(In, Out, { TEXT("partitioned"), TEXT("probeDead_zz") },', 1)


def plant_undefined_name(text):
    """A name a function loads that is bound nowhere - the PORT/BRIDGE_PORT typo shape.

    py_compile cannot see this; a NameError is a runtime failure, and the function that carried the
    real one only ran when the bridge was already down. It killed a 288-run sweep at run 90.
    """
    needle = "def main():\n"
    if needle not in text:
        return None
    return text.replace(needle, needle + "    _ = MIF_PROBE_ZZ_UNDEFINED\n", 1)



def plant_unrun_assertions(text):
    """Assertions a suite DEFINES and its last recorded run never executed.

    audit_suite_reach measures ran/defined per suite, so adding definitions without a matching run
    collapses the ratio. They go inside a function nobody calls, which keeps the file valid Python -
    the tool only reads the source, but a suite left syntactically broken by a crashed run would be a
    nasty thing to leave behind.
    """
    if "def _mif_probe_zz(" in text:
        return None
    body = "".join('    check("probe %d", True)\n' % i for i in range(60))
    return text.rstrip("\n") + "\n\n\ndef _mif_probe_zz():\n" + body



def plant_bad_advice(text):
    """A message telling the caller to use an endpoint that does not exist.

    Planted into tool_help.json rather than a .cpp on purpose: this tool reads BOTH, and a tools/
    file can be planted while an editor is open, where a Source/ plant is skipped. The endpoint
    surface being scanned is the same either way.

    save_asset is the real name this tool found advised in four places on 2026-08-31 - two C++ notes
    and these two help texts - none of which exist on any build.
    """
    needle = "save_package {path} persists it"
    if needle not in text:
        return None
    return text.replace(needle, "save_asset persists it", 1)



def plant_absence_claim(text):
    """A PRESENT-tense claim that a live endpoint does not exist.

    Present tense on purpose. The same sentence in the past tense - "the read half WAS missing:
    save_package could write and nothing could see it" - is correct prose about why something was
    built, and audit_absence_claims deliberately ignores it. A plant that used the past tense would
    report the tool ASLEEP for doing exactly the right thing.
    """
    needle = '"add_actor_to_data_layer":'
    if needle not in text:
        return None
    probe = '"mif_probe_zz": "There is no save_package endpoint on this build.",'
    return text.replace(needle, probe + "\n " + needle, 1)



# ASSEMBLED AT RUNTIME, never written as one literal. audit_citations scans tools/*.py, so a
# complete `File.cpp:NNNNN` string sitting in THIS file is a dead citation in the corpus - and the
# harness reported already-red for exactly that reason on its first run, which was the tool being
# right about its own probe. Splitting it keeps the plant invisible to the checker until planted.
_PROBE_FILE = "MifBridgeCommon" + ".cpp"
_PROBE_LINE = "9" * 5
DEAD_CITATION = _PROBE_FILE + ":" + _PROBE_LINE


def plant_dead_citation(text):
    """A citation to a line that exists on no engine and in no repo file.

    Five nines rather than a plausible-but-wrong number on purpose. The check under test is "does
    this line exist anywhere", and a number that could accidentally be valid on some installed
    engine would make the plant's outcome depend on which engines this machine happens to have.
    Five digits, not six: the CITE regex takes two to five, so 999999 matched nothing at all and the first
    run of this plant reported the tool ASLEEP when the plant had never landed.
    """
    needle = "\n## "
    if needle not in text:
        return None
    return text.replace(needle,
                        "\n\nSee " + DEAD_CITATION + " for the probe.\n" + needle, 1)



def plant_unreachable_param(text):
    """Stop sending a parameter the endpoint accepts, so nothing can reach it.

    This is the whole shape param_reach exists for: an endpoint takes a key, RejectUnknownParams
    would honour it, and no MCP wrapper puts it on the wire - so the capability is real, documented
    and unusable. Removing a keyword from a _post call recreates it exactly.

    hideKnots on list_nodes because it is a plain optional bool with no aliases, so its removal
    cannot be masked by another spelling still being sent.
    """
    needle = ", hideKnots=hide_knots"
    if needle not in text:
        return None
    return text.replace(needle, "", 1)



def plant_advice_gap(text):
    """Advice telling the caller to run an operation that does not exist.

    "call X" is one of the shapes ADVICE matches, and an endpoint saying "do X first" is ASSERTING
    that X is possible - which is why this check exists at all: uv_unwrap once warned "Mark seams
    first" when nothing in the addon could set edge.use_seam. Planted in the addon rather than a
    .cpp so it runs while an editor is open; the tool reads both.
    """
    needle = 'raise MifOpError("pass \'code\' or \'file\', not both")'
    if needle not in text:
        return None
    return text.replace(
        needle,
        'raise MifOpError("pass \'code\' or \'file\', not both - call mif_probe_zz_op first")', 1)



def plant_write_only_family(text):
    """Two writers for a noun nothing reads and no response field carries.

    Both halves matter. Two writers because a single-writer family is too noisy to report; a noun
    nothing emits because the tool's whole precision comes from asking "does any response CARRY
    this thing" rather than "is there a list_<noun>" - that check took 18 candidates to 1 on the
    day it was written, and a plant using a real noun would be suppressed by it, correctly.
    """
    anchor = "\t\t\tMIF_BIND(list_game_framework_component_requests);"
    if anchor not in text:
        return None
    return text.replace(anchor, anchor
                        + "\n\t\t\tMIF_BIND(add_mif_probe_zz);"
                        + "\n\t\t\tMIF_BIND(remove_mif_probe_zz);", 1)



def plant_prose_reader(text):
    """Make a driven tool read prose again - the state audit_blocking was in this morning.

    audit_blocking matched blocker names against the RAW line, so a name inside a TEXT(...) that
    documents why a blocker is NOT used counted as one. Reverting it to `probe = line` recreates
    exactly that, and audit_prose_dependence's string pass must name it.

    Planted into a tools/ file rather than Source/, so it runs while an editor is open - and this is
    the only detector whose subject IS the other tools, which is why its plant lives in one of them.
    """
    needle = "            probe = probe_lines[i] if i < len(probe_lines) else line"
    if needle not in text:
        return None
    return text.replace(needle, "            probe = line", 1)



def plant_mode_param(text):
    """A handler that branches on a MODE and declares a parameter only one branch could use.

    The real defect this generalises: invoke_editor_tab declares `asset`, and UiResolveTabManager
    returns early for manager:"global" without ever reading it, so a caller who meant an
    asset-editor tab and forgot to set manager got a global operation under ok:true.
    """
    m = re.search(r"\n(\t*)void\s+H_[A-Za-z0-9_]+\s*\(\s*const\s+TSharedRef<FJsonObject>&\s*In",
                  text)
    if not m:
        return None
    t = m.group(1)
    probe = (t + "void H_mif_probe_mode_zz(const TSharedRef<FJsonObject>& In, "
                 "const TSharedRef<FJsonObject>& Out)\n"
             + t + "{\n"
             + t + "\tif (RejectUnknownParams(In, Out, { TEXT(\"mode\"), TEXT(\"probeOnly_zz\") },\n"
             + t + "\t\tTEXT(\"mode, probeOnly_zz\"))) { return; }\n"
             + t + "\tconst FString Mode = JStr(In, TEXT(\"mode\"));\n"
             + t + "\tif (Mode == TEXT(\"alpha\")) { return; }\n"
             + t + "}\n")
    return text[:m.start() + 1] + probe + text[m.start() + 1:]


# tool -> (target file, plant function, marker, gate)
#
# gate=True  - proof is a NON-ZERO exit AND the marker in the output. Both, because several of these
#              exit 1 on unrelated pre-existing findings, and a blind detector would otherwise pass
#              on somebody else's failure.
# gate=False - the tool is a REPORT and returns 0 whatever it finds (audit_suite_reach always does).
#              Demanding a red exit there would call it ASLEEP no matter how well it works, so proof
#              is that the marker is ABSENT before the plant and PRESENT after. Weaker evidence,
#              named as such rather than dressed up as the same thing.
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
    "audit_blocking.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_blocker,
                          "FPlatformProcess::Sleep"),
    "audit_dead_params.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_dead_param,
                             "probeDead_zz"),
    "audit_undefined_names.py": (os.path.join(HERE, "why_not.py"), plant_undefined_name,
                                 "MIF_PROBE_ZZ_UNDEFINED"),
    "audit_suite_reach.py": (os.path.join(HERE, "test_layers.py"), plant_unrun_assertions,
                             "test_layers.py", False),
    "audit_message_endpoints.py": (os.path.join(HERE, "mcp-server", "tool_help.json"),
                                   plant_bad_advice, "save_asset"),
    "audit_absence_claims.py": (os.path.join(HERE, "mcp-server", "tool_help.json"),
                                plant_absence_claim, "save_package"),
    "audit_citations.py": (os.path.join(HERE, "FEATURE_PARITY_SPEC.md"), plant_dead_citation,
                           DEAD_CITATION),
    "param_reach.py": (SERVER, plant_unreachable_param, "list_nodes.hideknots"),
    # gate=False: audit_advice_gaps exits 0 whatever it finds, deliberately - "a tool that fails the
    # build over prose would be gamed by rewording the prose, which would make the source worse".
    "audit_advice_gaps.py": (os.path.join(HERE, "blender-addon", "MifBlender", "ops_scene.py"),
                             plant_advice_gap, "mif_probe_zz_op", False),
    # gate=False: it exits 0 either way, because deciding whether a missing half is worth building
    # is a judgement call and a tool that failed the build over one would be switched off.
    "audit_family_asymmetry.py": (os.path.join(PRIV, "MifBridgeCommon.cpp"),
                                  plant_write_only_family, "add_mif_probe_zz", False),
    "audit_prose_dependence.py": (os.path.join(HERE, "audit_blocking.py"), plant_prose_reader,
                                  "audit_blocking"),
    # gate=False: it is a review list and returns 0 whatever it finds, deliberately - deciding
    # whether a declared parameter is genuinely ignored on a branch needs a person.
    "audit_mode_params.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_mode_param,
                             "probeOnly_zz", False),
    # NOT "RULE 4" - that string is in the rules footer this tool prints on every red run, and the
    # already-red guard correctly refused to call that proof. The marker has to be text only a
    # FINDING can produce.
    "audit_vacuous_checks.py": (os.path.join(HERE, "mcp_static_check.py"), plant_unreachable,
                                "is only reached once main() has already returned 0"),
}

# Detectors that drive the RUNNING editor. A planted defect in a source file cannot prove one of
# these, because what they examine is the live registry and the responses that come back - so with
# the editor down they are not merely unplanted, they are unprovable HERE. Reported as their own
# category, because "no plant written yet" and "cannot be plant-tested at all" are different facts
# and collapsing them into one NOT PROVEN list loses the one that tells you what to do next.
LIVE = {
    "audit_absence_claims.py": "checks docs against the LIVE endpoint registry",
    "audit_describe_drift.py": "compares describe_endpoint output against the handlers, live",
    "audit_read_purity.py": "calls each read endpoint and watches for a dirtied package",
    "audit_roundtrip.py": "writes then reads back through the bridge",
    "audit_blender_postconditions.py": "needs a running Blender - exits 2 SKIPPED without one",
    "audit_blender_read_purity.py": "needs a running Blender - exits 2 SKIPPED without one",
    "audit_value_discovery.py": "calls each endpoint and asks whether the values it demands are "
                                "DISCOVERABLE from another endpoint - the answer lives in the "
                                "running editor's responses, not in the source",
}

# NOT OURS TO PLANT, which is a different thing again from unproven or unprovable.
#
# audit_factory_init scans the ENGINE - UnrealEd's EditorFactories.cpp and friends - looking for
# asset classes whose factory does work after its NewObject, which create_asset's bare NewObject
# would skip. Planting for it means editing D:/UE532. That is somebody else's tree, shared by every
# project on this machine, and a plant that failed to restore would be a very bad day.
#
# Recorded rather than left in the "no plant written yet" pile, because those two states call for
# opposite actions: one is work, this is a boundary.
NOT_OURS = {
    "audit_factory_init.py": "its corpus is the ENGINE source, which this repo must not modify - "
                             "not even briefly, not even with a restore",
}

# Extra argv some tools need to report everything rather than only new-against-baseline findings.
ARGS = {"audit_vacuous_checks.py": ["--all"]}



def editor_is_running(port=8791, timeout=0.4):
    """Is an editor holding this project open right now?

    WHY THIS GUARD EXISTS. Every plant that targets Source/ writes a deliberately broken file and
    restores it about a second later. A running editor does not re-read .cpp at runtime, so that is
    normally invisible to it - but Live Coding compiles ON DEMAND, and a person pressing
    Ctrl+Alt+F11 inside that window would compile the plant. The window is short, the consequence is
    someone else's editor, and "short window" is not a safety argument. So the source plants simply
    do not run while an editor is up.

    Checked by opening the bridge port rather than by listing processes: the port is what proves an
    editor with THIS plugin loaded, and process listing has already been unreliable here - a
    UnrealEditor process was visible earlier today whose path and ports this shell could not read.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        s.close()


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
    entry = PLANTS[tool]
    target, planter, marker = entry[0], entry[1], entry[2]
    gate = entry[3] if len(entry) > 3 else True
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
    if marker in before_out and (before_rc != 0 or not gate):
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

    if not gate:
        if marker in out:
            return "proven", ("named %s after the plant and not before (report-style tool - it "
                              "always exits 0, so the exit code proves nothing)" % marker)
        return "ASLEEP", "never named %r with the defect planted - it is not looking" % marker
    if rc != 0 and marker in out:
        return "proven", "went red on the planted %s" % marker
    if rc == 0:
        return "ASLEEP", "exited 0 with the defect planted - it is not looking"
    return "ASLEEP", ("went red but never named %r, so it failed on something else"
                      % marker)


def main():
    listing = "--list" in sys.argv
    # A LIVE tool with a plant is provable WHENEVER its live dependency is actually up. Listing it
    # permanently as "cannot be proven here" would be true of the machine and false of the moment -
    # audit_absence_claims reads the running editor's registry, so with a bridge answering it is as
    # testable as any static one, and with none it exits 0 saying "could not check", which a plant
    # would misread as ASLEEP. So it is attempted only when the bridge answers, and reported as
    # skipped-for-a-reason otherwise.
    covered = [t for t in DETECTORS if t in PLANTS]
    live = [t for t in DETECTORS if t not in PLANTS and t in LIVE]
    foreign = [t for t in DETECTORS if t not in PLANTS and t in NOT_OURS]
    uncovered = [t for t in DETECTORS if t not in PLANTS and t not in LIVE and t not in NOT_OURS]

    print("%d detector(s) in tools/; %d have a plant, %d cannot be proven here, %d have neither"
          % (len(DETECTORS), len(covered), len(live), len(uncovered)))
    if listing:
        for t in covered:
            print("  plant   %s" % t)
        for t in live:
            print("  LIVE    %-32s %s" % (t, LIVE[t]))
        for t in uncovered:
            print("  NONE    %s" % t)
        return 0

    busy = editor_is_running()
    if busy:
        print("")
        print("An editor is answering on 127.0.0.1:8791. Plants that write to Source/ are SKIPPED -")
        print("they restore in about a second, but Live Coding compiles on demand and nobody should")
        print("risk compiling a planted defect into somebody's open editor. Close it, or read the")
        print("python-only results below and run the rest later.")
        print("")

    before = source_digest()
    asleep, notproven, skipped = [], [], []
    for tool in covered:
        if tool in LIVE and not busy:
            skipped.append(tool)
            print("  %-26s %-12s %s" % (tool, "skipped", "needs the bridge; it exits 0 saying "
                                                         "'could not check', not ASLEEP"))
            continue
        if busy and PLANTS[tool][0].startswith(os.path.join(ROOT, "Source")):
            skipped.append(tool)
            print("  %-26s %-12s %s" % (tool, "skipped", "editor is running - plants into Source/"))
            continue
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
    if skipped:
        print("")
        print("SKIPPED - an editor was running, so these were not attempted:")
        for t in skipped:
            print("  %s" % t)
    if live:
        print("")
        print("NOT PROVABLE HERE - each needs a live process this harness cannot plant into. A")
        print("planted source defect says nothing about them; they need the thing they measure:")
        for t in live:
            print("  %-32s %s" % (t, LIVE[t]))
    if foreign:
        print("")
        print("NOT OURS TO PLANT - the corpus belongs to somebody else:")
        for t in foreign:
            print("  %-32s %s" % (t, NOT_OURS[t]))
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
    # exit 2, not 0: a run that skipped most of its plants is not a pass, and a caller that only
    # looks at the exit code must not read "editor was open" as "everything is proven".
    return 2 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
