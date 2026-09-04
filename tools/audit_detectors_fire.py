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
                       "parity_check.py", "mcp_static_check.py", "param_reach.py",
                       # A GENERATOR, and with --check also a fail-closed detector - the same
                       # dual role param_reach.py has. Listed because parity_check's CHECK 7 now
                       # delegates to it, and a delegated check that nobody proves is exactly the
                       # gap that let a stale table reach four green suites on 2026-08-31.
                       "harvest_param_table.py"))
                   and f != "audit_report.py" and f != "audit_detectors_fire.py")


BEGIN_HARVEST = "// >>> MIF_HARVEST_BEGIN"
KEYS_ROW = re.compile(r"[ \t]*static const TCHAR\* const GMifDescKeys_\w+\[\] = .*\n")


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
    # TWO ARMS. The unconditional bind exercises CHECK 3 - a MIF_BIND with no _post wrapper - which
    # is the long-standing arm. The CONDITIONAL bind exercises the "no MIF_BIND inside a #if" check
    # added 2026-09-03, which had no plant at all: this plant added an unconditional bind and
    # therefore proved a different arm entirely, while the harness reported parity_check as proven.
    #
    # The MARKER is the #if message rather than the probe name, because both binds also trip CHECK 3
    # and a name would be reported by either arm. See the registry entry.
    #
    # Why the #if arm is worth a plant: zero of the 459 binds are conditional today, and two
    # documented claims rest on that - that the disposable probe regenerates endpoints_current.json
    # as well as a DDS2 editor, and that the MIF_DECL/MIF_BIND distinction is theoretical. Both fail
    # QUIETLY, in the direction of reporting less work than exists.
    probe = ("\n" + indent + "MIF_BIND(mif_probe_zz);"
             + "\n" + indent + "#if MIF_PROBE_ZZ_CONDITIONAL"
             + "\n" + indent + "MIF_BIND(mif_probe_cond_zz);"
             + "\n" + indent + "#endif")
    return text[:m.end(1)] + probe + text[m.end(1):]


def plant_confirm(text):
    """An endpoint that ACCEPTS confirm and never reads it - a promise it does not keep."""
    needle = 'RejectUnknownParams(In, Out, { TEXT("partitioned") },'
    if needle not in text:
        return None
    return text.replace(needle,
                        'RejectUnknownParams(In, Out, { TEXT("partitioned"), TEXT("confirm") },', 1)


def plant_default_send(text):
    """An MCP wrapper handing back a default the endpoint refuses for being present.

    THE PLANT IS THE REAL MISTAKE, and it shipped for a few hours on 2026-09-03. list_sublevels'
    wrapper declared `net_mode: str = "server"`, _post sends anything that is not None, and the
    endpoint had just started refusing netMode unless world is "pie" - so `list_sublevels()` with no
    arguments at all was refused. Restoring that one default is the whole plant.
    """
    old = 'def list_sublevels(world: str = "editor", net_mode: str = None)'
    if old not in text:
        return None
    return text.replace(old, 'def list_sublevels(world: str = "editor", net_mode: str = "server")', 1)


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



def plant_nested_field_read(text):
    """A suite reading describe_property's arrayDim off the TOP of the response.

    THE PLANT IS THE REAL MISTAKE, not an imitation of one. On 2026-08-31 a probe asked
    describe_property for arrayDim at the top level, got None because the field lives inside
    'property', and concluded that LensFlareTints is not a fixed-size C-array. It is - arrayDim is 8
    - and the wrong conclusion reached the spec before the resolver was read.

    It has to look like the suite around it. A check phrased as a probe would prove only that the
    tool spots probes: this one adopts T834's own wording and its is-None comparison, which is
    exactly the form that makes the failure silent. None is what a missing key returns, so
    `rc.get("arrayDim") is None` PASSES whether the property is a C-array or not.
    """
    anchor = '        check("T834 the property object is present", isinstance(rc.get("property"), dict), rc)'
    if anchor not in text:
        return None
    return text.replace(anchor, anchor + "\n"
                        '        check("T834 RootComponent is a single, not a fixed-size C-array",\n'
                        '              rc.get("arrayDim") is None, rc.get("arrayDim"))', 1)


def plant_contradicted_heading(text):
    """An OPEN issue heading whose own body says the defect is fixed.

    THE PLANT IS THE REAL MISTAKE. Twelve entries in docs/06 read as open on 2026-09-03 while
    something else in the same file already recorded them fixed - one of them an editor-fatal crash
    with a crash GUID, fixed for over a week, sitting where a reader triaging for danger looks first.

    The bold **Fixed and verified** is load-bearing: the detector deliberately does not accept the
    unbolded word, because this file uses "resolved" in prose about paths and "not a defect" inside
    a live entry. A plant in the loose form would be cleared, correctly, and prove nothing.
    """
    marker = "\n## 998. A planted entry whose body contradicts its own heading\n\n"
    if marker in text:
        return None
    return text + (marker + "Reported and then **Fixed and verified** 2026-01-01. Left reading as\n"
                   "open on purpose so the detector has something it must see.\n")


def plant_fixture_adoption(text):
    """A suite taking the first Blueprint it finds and naming it, with no scratch filter.

    THE PLANT IS THE REAL MISTAKE. This is the shape that made test_landscape_heightmap report
    1590uu of collision error on 2026-09-01 - it adopted a landscape another suite had left behind
    and measured against heights it never set. Blueprint is the class used here because fifty suites
    in this directory create one, so the collision clause the detector turns on has something real
    to find; a class nothing creates is correctly cleared and would prove nothing.

    Deliberately NOT scratch-scoped and NOT guarded, because those are the two ways out the detector
    is supposed to honour. If either crept in, this would be cleared and the harness would report a
    blind detector as proven.
    """
    anchor = "    st = int(time.time() % 100000)"
    if anchor not in text:
        return None
    return text.replace(anchor, anchor + "\n"
                        '    _zz = (M.call("find_assets", {"class": "Blueprint",\n'
                        '                                  "pathPrefix": "/Game/"}).get("assets")\n'
                        '           or [{}])[0].get("path")\n', 1)


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
    # THE PADDING IS THE POINT, and its absence made this report ASLEEP against a working tool.
    # declared_near() reads 25 lines ABOVE the signature, because that is where this codebase puts
    # its endpoint contracts. The first version inserted the probe immediately before H_new_level,
    # whose comment block says "blocks the game thread" - so the probe inherited a declaration it
    # never made, was classified `declared` rather than UNDECLARED, and the tool correctly exited 0.
    # Twenty-six blank lines guarantee the lookback window is empty wherever this lands.
    #
    # AND THE HANDLER'S NAME MATTERS TOO, which took a second ASLEEP verdict to notice. DECLARED
    # contains the word "block", declared_near lowercases the whole window INCLUDING the signature
    # line, and the probe was called H_mif_probe_block_zz - so it declared itself. A plant must not
    # accidentally satisfy the very predicate it is testing.
    pad = "\n" * 26
    probe = (pad
             + t + "void H_mif_probe_zzq(const TSharedRef<FJsonObject>& In, "
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
    # camelCase, NO UNDERSCORE, because the tool filters accepted keys through
    # IDENT = ^[A-Za-z][A-Za-z0-9]* before looking at them - real parameter names here are camelCase.
    # The first version used probeDead_zz, which was discarded as not-a-parameter-name before any
    # check ran, and the harness reported the tool ASLEEP when it had simply never been shown a
    # parameter. A plant has to look like the thing it imitates.
    needle = 'RejectUnknownParams(In, Out, { TEXT("partitioned") },'
    if needle not in text:
        return None
    return text.replace(
        needle, 'RejectUnknownParams(In, Out, { TEXT("partitioned"), TEXT("probeDeadZz") },', 1)


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

    EVERY CALL SITE, NOT JUST THE WRAPPER - and the line above is exactly the reasoning that was not
    enough. param_reach's tool_sends() UNIONS the keys of every `_post("<endpoint>", ...)` in
    server.py into one set per ENDPOINT, so a key still passed by any other call site stays
    "reachable" no matter what the user-facing wrapper does. Removing one of three list_nodes call
    sites planted nothing at all, and the harness reported param_reach ASLEEP for it.

    The two extra call sites are mif_layout_graph's internal reads, which pass a hardcoded
    hideKnots=False. They were added the same day this was reported ASLEEP: the plant was correct
    when written and a new tool silently invalidated it. Choosing a key with no ALIASES is not
    enough - it also has to have no SECOND CALL SITE, and nothing stops one appearing later. That
    is what must_vanish (below) is for.
    """
    wrapper, internal = ", hideKnots=hide_knots", ", hideKnots=False"
    if wrapper not in text or internal not in text:
        return None   # fail closed: reported as anchor-gone, never as a silent pass
    return text.replace(wrapper, "").replace(internal, "")



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

    TWO ARMS, because until 2026-09-03 this planted only the first and the tool is NAMED for the
    second. `probeOnly_zz` is declared and NEVER read anywhere (read_depth 99). `branchOnly_zz` is
    read ONLY inside the mode branch (read_depth 2) - the invoke_editor_tab shape itself, and the
    arm the branch-depth work of that same day rewrote. A plant that exercises the arm nobody
    touched cannot notice when the arm somebody DID touch stops firing.

    The MARKER is branchOnly_zz for that reason: it asserts the founding arm specifically. Both
    probes are planted, so the tool's output names both and a reader sees the pair.
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
             + t + "}\n"
             + t + "void H_mif_probe_branch_zz(const TSharedRef<FJsonObject>& In, "
                   "const TSharedRef<FJsonObject>& Out)\n"
             + t + "{\n"
             + t + "\tif (RejectUnknownParams(In, Out, { TEXT(\"mode\"), TEXT(\"branchOnly_zz\") },\n"
             + t + "\t\tTEXT(\"mode, branchOnly_zz\"))) { return; }\n"
             + t + "\tconst FString Mode = JStr(In, TEXT(\"mode\"));\n"
             + t + "\tif (Mode == TEXT(\"alpha\"))\n"
             + t + "\t{\n"
             + t + "\t\tconst FString V = JStr(In, TEXT(\"branchOnly_zz\"));\n"
             + t + "\t\tOut->SetStringField(TEXT(\"v\"), V);\n"
             + t + "\t}\n"
             + t + "}\n")
    return text[:m.start() + 1] + probe + text[m.start() + 1:]


def plant_factory_init_drift(text):
    """Drop one class from create_asset's FactoryInitClasses, in OUR source, not the engine's.

    audit_factory_init was filed under NOT_OURS on the grounds that its corpus is the engine tree.
    That is true of one half and false of the other: it ALSO reads this repo's own
    MifBridgeUserTypes.cpp and reports drift between the engine-derived population and the
    hand-written warning list. That half is plantable here without touching D:/UE532 at all.

    The engine half remains unplantable and that boundary is unchanged - a plant that failed to
    restore somebody else's shared engine tree would be a very bad day. What changes is that
    "cannot be proven here" was covering a check that could.

    PoseAsset because the scan genuinely finds UPoseAsset (SkeletonFactory/PoseAssetFactory both do
    post-construct work), so removing its name produces real drift rather than a no-op.
    """
    # GUARDS ITS OWN ANCHOR. Swept 2026-09-03: 33 of the 34 plant functions return None when their
    # anchor is absent, and the harness reports that as anchor-gone. This one used a bare .replace()
    # and relied entirely on the must_vanish field in its registry entry - which does catch it, but
    # puts the safety in a different file from the mistake. A plant that silently returns the text
    # UNCHANGED is the shape written up in 02_GOTCHAS the same day: the plant fails, and the failure
    # is indistinguishable from a result.
    needle = 'TEXT("PoseAsset"), '
    if needle not in text:
        return None
    return text.replace(needle, "", 1)


def plant_spawn_label(text):
    """An actor spawned into the editor world with a label the adopt-guard cannot recognise.

    THE PLANT IS THE REAL MISTAKE, twice over. audit_read_purity spawned "PureSpline_%d" and
    "PureWaterProbe_%d" and leaked both, so every run left two actors that mifaudit.is_scratch_fixture
    read as project content - adoptable by any suite hunting for a fixture. Found 2026-09-03 by
    measuring the claim in that guard's own comment that no such site existed.

    The label is Zz-prefixed rather than merely non-Mif so the marker cannot collide with a real
    label in the corpus: the harness rejects a marker that is already present before planting, and a
    plausible-looking probe name is exactly how that check gets accidentally defeated.
    """
    return text + (
        '\n\ndef _mif_spawn_label_plant():\n'
        '    M.call("spawn_actor_in_level", {"actorClass": "StaticMeshActor",\n'
        '                                    "label": "ZzSpawnProbe_1"})\n')


# tool -> (target file, plant function, marker, gate, must_vanish)
#   gate        False for report-style tools that always exit 0 - the marker is then the whole test
#   must_vanish a string that must be ABSENT from the mutated text, so a plant that
#               failed to land is reported as such instead of as a blind detector
#
# gate=True  - proof is a NON-ZERO exit AND the marker in the output. Both, because several of these
#              exit 1 on unrelated pre-existing findings, and a blind detector would otherwise pass
#              on somebody else's failure.
# gate=False - the tool is a REPORT and returns 0 whatever it finds (audit_suite_reach always does).
#              Demanding a red exit there would call it ASLEEP no matter how well it works, so proof
#              is that the marker is ABSENT before the plant and PRESENT after. Weaker evidence,
#              named as such rather than dressed up as the same thing.
def plant_missing_desc_row(text):
    """Delete a row from describe_endpoint's generated table - the 2026-08-31 defect exactly.

    Not a synthetic mangling: this is the state the file was actually in that morning, when seven
    new endpoints had guards in the source and no rows here. The consequence was not merely a
    caller reading a short list. test_node_spawns' T330 picks its targets by ASKING the live
    registry which endpoints take only cosmetic parameters, describe_endpoint answered
    acceptedParams:NONE for a row-less endpoint, and the suite skipped it in silence - passing 106
    checks while testing one thing FEWER than the day before.

    Anchored on the first generated key array rather than on a named endpoint, so renaming any one
    endpoint reports anchor-gone (which is true and visible) instead of quietly proving nothing.
    """
    begin = text.find(BEGIN_HARVEST)
    if begin < 0:
        return None
    m = KEYS_ROW.search(text, begin)
    if not m:
        return None
    return text[:m.start()] + text[m.end():]


def plant_unread_consequence_field(text):
    """Stop a suite from reading a consequence field, so the backlog grows past its baseline.

    Targets tools/ rather than Source/, which matters: the harness skips every Source/ plant while
    an editor holds the project, and a detector that can only be proven in that window is a detector
    that mostly is not.

    propertiesFailed is read in exactly one place - test_inherited_components T295 - so renaming the
    read makes the field genuinely unread rather than merely less read. The check LABEL on the same
    line still says "propertiesFailed", which is the point: a name in a label is not a read, and if
    the tool counted it this plant would prove nothing.
    """
    if '.get("propertiesFailed")' not in text:
        return None
    return text.replace('.get("propertiesFailed")', '.get("propertiesFailedZz")')


def plant_blender_dead_param(text):
    """Add a key to an addon op's reject_unknown that nothing reads.

    Targets the ADDON rather than Source/, so it runs while an editor is up - the same reason the
    consequence-field plant targets tools/. And camelCase with no underscore, because the first
    version of this question was asked with lowercased keys against a camelCase source and reported
    two working parameters as dead; a plant has to look like the thing it imitates.
    """
    needle = 'reject_unknown(params, ('
    i = text.find(needle)
    if i < 0:
        return None
    j = i + len(needle)
    return text[:j] + '"probeDeadZz", ' + text[j:]


def plant_blender_consequence_field(text):
    """Add a consequence-shaped response key to an addon op that no suite reads.

    "probeDroppedZz" rather than a plain probe name: the tool matches on the CONSEQUENCE shape, so a
    key that does not contain one of its words would be ignored - correctly - and the harness would
    call the tool asleep for declining to care about an ordinary field. A plant has to look like the
    thing it imitates, which is the lesson three earlier plants taught the hard way.
    """
    needle = '    return {"removed": removed, "removedCount": len(removed),'
    if needle not in text:
        return None
    return text.replace(needle,
                        '    return {"probeDroppedZz": 1, "removed": removed, '
                        '"removedCount": len(removed),', 1)


def plant_fatal_guard(text):
    """Add a fatal-sounding refusal naming a class nothing else guards.

    The probe name ends in "Mesh" deliberately: the tool groups BY CLASS and its CLASSISH pattern
    only recognises names ending in the UObject-ish suffixes it lists, so a bare probe token would be
    filed under "naming no class" and never printed with a marker the harness could see. Fourth time
    today a plant has had to look like the thing it imitates rather than like a probe.

    Multi-fragment on purpose too - a single-fragment literal would have been matched by the BROKEN
    version of the tool's regex, so this plant would have passed against a scanner that read a
    fraction of the source. It is written the way the module actually writes refusals.
    """
    needle = '\tvoid H_list_blueprints('
    i = text.find(needle)
    if i < 0:
        return None
    brace = text.find("{", i)
    if brace < 0:
        return None
    j = text.find("\n", brace) + 1
    return (text[:j]
            # THE FATAL PHRASE STRADDLES THE LITERAL BOUNDARY ON PURPOSE. "CRASH THE " ends one
            # literal and "EDITOR" begins the next, so the phrase exists only in the string the
            # COMPILER builds - in the source bytes it reads `CRASH THE " "EDITOR`.
            #
            # That is a regression lock, not decoration. Until 2026-09-03 the tool matched raw
            # source text and was therefore blind to every wrapped phrase, including create_asset's
            # UAnimSequence refusal - the guard for the one type that has actually terminated this
            # editor. The old plant kept its whole phrase inside the first literal, so it passed
            # just as happily against the blind version and would pass again if the joining were
            # reverted. Now it cannot.
            + '\t\tif (false) { Fail(Out, TEXT("a ProbeZzMesh would CRASH THE "\n'
              '\t\t\t"EDITOR outright, so it is refused here rather than attempted. ")); }\n'
            + text[j:])


def plant_cross_endpoint_claim(text):
    """Make a handler promise something about ANOTHER endpoint that no suite drives with it.

    Needs BOTH halves or the tool correctly ignores it: a real endpoint name, and one of the
    equivalence/completeness shapes it filters on. A bare probe token would be dropped as
    navigation, which is the tool working - so the plant carries "returns the same set" and names
    list_blueprints, and hides a unique marker inside the same sentence so the harness can see it in
    the printed quote.
    """
    needle = '\tvoid H_list_automation_tests('
    i = text.find(needle)
    if i < 0:
        return None
    brace = text.find("{", i)
    if brace < 0:
        return None
    j = text.find("\n", brace) + 1
    return (text[:j]
            + '\t\tif (false) { Fail(Out, TEXT("probeSameZz - list_blueprints returns the same set "\n'
              '\t\t\t"as this endpoint, so either will do. ")); }\n'
            + text[j:])


def plant_unreleased_acquire(text):
    """Turn a suite's `finally:` into a plain block, so its acquire is no longer released on failure.

    IMITATES THE DEFECT THAT SHIPPED rather than a synthetic one. test_pie_family.py genuinely looked
    like this until 2026-08-31: start_pie near the top of main(), a bare stop_pie at the bottom, and a
    `return 3` four lines after the start that skipped it. audit_suite_teardown was written for that
    shape and found four more instances of it the first time it ran.

    ADDS AN UNPROTECTED ACQUIRE rather than dismantling the existing try, and the first version did
    the latter. Replacing `finally:` with `if True:` leaves a `try:` with no handler of any kind,
    which is a SyntaxError - so the detector could not parse the file, skipped it, and was reported
    ASLEEP for a defect it had been prevented from reading. (It also swallowed that SyntaxError
    silently and still exited 0, which was a real bug in the detector and is now fixed.)

    A PLANT MUST LEAVE THE CORPUS READABLE. Inserting one more start_pie above the try creates
    exactly the shape this detector looks for - an acquire with no finally releasing it - while the
    file stays valid Python and everything else about it stays true.

    Targets tools/ rather than Source/, so it runs whether or not an editor is up.
    """
    needle = "    try:" + chr(10)
    if needle not in text:
        return None
    plant = ('    M.raw_post("start_pie", {})  # MIF_PROBE_ZZ_UNRELEASED_ACQUIRE' + chr(10))
    return text.replace(needle, plant + needle, 1)


def plant_removed_guard(text):
    """Strip the RejectUnknownParams out of H_disconnect_pin.

    audit_param_guards reports zero unguarded endpoints today, and "disconnect_pin" appears nowhere
    in its clean output - the six it DOES name are the ones guarding through a shared helper
    (connect_pins, reconnect_pin, add_variable_get/set, add_bind/call_dispatcher). So the endpoint
    name is a safe marker: it can only be there because the plant worked.

    Removing a guard rather than adding a hole-shaped handler, because the bug being modelled is a
    guard going away in a refactor - which is exactly how the real ones would be lost.
    """
    i = text.find("void H_disconnect_pin(")
    if i < 0:
        return None
    j = text.find("RejectUnknownParams", i)
    if j < 0:
        return None
    start = text.rindex("if", i, j)
    k = text.index("{", text.index(")", j))
    depth, q = 0, k
    while q < len(text):
        if text[q] == "{":
            depth += 1
        elif text[q] == "}":
            depth -= 1
            if depth == 0:
                break
        q += 1
    return text[:start] + "if (false) { }" + text[q + 1:]



# ---------------------------------------------------------------------------
# audit_vacuous_checks has FIVE rules and had ONE plant
# ---------------------------------------------------------------------------
# Its PLANTS entry proved rule 4, and the harness printed "proven" - true of the tool and
# false of four fifths of what the tool does. Rules 1, 2, 3 and 5 were each hand-verified once,
# on the day they were written, and nothing re-checked them afterwards. A detector that has
# LOST a rule looks exactly like one that never had it.
#
# Every plant is the SHAPE THE RULE WAS WRITTEN FROM rather than a string that happens to
# match: all() over a collection nobody checked for emptiness, presence standing in for value,
# `not <offenders>` where the list may never have been populated, a resolution count printed
# and never compared.
#
# They insert a function ABOVE `def main():` instead of editing an existing assertion, so a
# plant can never damage a real check, and each marker is an identifier that cannot occur in
# the file otherwise - which is what stops "already-red" from masking a sleeping rule.

_VACUOUS_ANCHOR = "def main():"


def _plant_before_main(text, body):
    if _VACUOUS_ANCHOR not in text:
        return None
    return text.replace(_VACUOUS_ANCHOR, body + "\n\n" + _VACUOUS_ANCHOR, 1)


def plant_vacuous_all(text):
    """RULE 1: all([]) is True, so the assertion passes when the call returned nothing."""
    return _plant_before_main(text, "\n".join((
        'def _mifplant_rule1(rows):',
        '    check("MIFPLANT1 every row has a sensible width",',
        '          all(r["width"] > 0 for r in rows), rows)',
    )))


def plant_presence_for_value(text):
    """RULE 2: a key asserted PRESENT on every row, never checked for what it holds.

    The 301-mislabelled-rows defect: `all("cooked" in b for b in rows)` passed green while a
    fifth of the rows carried the wrong VALUE.
    """
    return _plant_before_main(text, "\n".join((
        'def _mifplant_rule2(rows):',
        '    check("MIFPLANT2 every row is labelled",',
        '          all("mifPlantedLabel" in r for r in rows), rows)',
    )))


def plant_empty_counterexample(text):
    """RULE 3: `not <offenders>` is True when the offender list was never populated."""
    return _plant_before_main(text, "\n".join((
        'def _mifplant_rule3(rows):',
        '    mifPlantedOffenders = [r for r in rows if r.get("bad")]',
        '    check("MIFPLANT3 nothing is bad", not mifPlantedOffenders, mifPlantedOffenders)',
    )))


def plant_uncompared_count(text):
    """RULE 5: a resolution count printed and never compared - the select_edges defect itself.

    audit_blender_read_purity printed "matched 0 of 12" for its whole life while reporting OK,
    because its selector could not resolve against the mesh it ran on.
    """
    return _plant_before_main(text, "\n".join((
        'def _mifplant_rule5(r):',
        '    print("MIFPLANT5 selector matched %s of %s"',
        '          % (r.get("matchedCount"), r.get("totalEdges")))',
    )))

PLANTS = {
    "audit_param_guards.py": (os.path.join(PRIV, "MifBridgeNodes.cpp"), plant_removed_guard,
                             "disconnect_pin"),
    # Exits 1 on a finding, so the exit code is real proof. No must_vanish: this plant ADDS a
    # call rather than removing one, and "it is there now" is already proved by the marker.
    "audit_suite_teardown.py": (os.path.join(HERE, "test_pie_family.py"),
                                plant_unreleased_acquire, "test_pie_family.py"),
    # MARKER IS THE #if MESSAGE, not the probe name. Both planted binds lack a _post wrapper, so
    # CHECK 3 names both of them and a probe-name marker would pass without the #if arm ever
    # running. This phrase appears only in the conditional-bind problem, which is the arm that had
    # no plant until 2026-09-03.
    "parity_check.py": (os.path.join(PRIV, "MifBridgeCommon.cpp"), plant_bind,
                        "inside a preprocessor conditional"),
    "harvest_param_table.py": (os.path.join(PRIV, "MifBridgeDescribe.cpp"),
                               plant_missing_desc_row, "CONTRACT DRIFT"),
    "audit_consequence_fields.py": (os.path.join(HERE, "test_inherited_components.py"),
                                    plant_unread_consequence_field, "propertiesFailed"),
    "audit_blender_dead_params.py": (os.path.join(HERE, "blender-addon", "MifBlender", "ops_mesh.py"),
                                     plant_blender_dead_param, "probeDeadZz"),
    # REPORT-STYLE (gate False): it always exits 0, so the exit code proves nothing and the marker
    # is the whole test. Plants into Source/, so it only runs in an editor-closed window.
    "audit_editor_fatal_guards.py": (os.path.join(PRIV, "MifBridgeIntrospect.cpp"),
                                     plant_fatal_guard, "ProbeZzMesh", False),
    # Same shape, same window. Both of these were written tonight and the harness's own
    # "0 have neither" invariant is what noticed the second one had no plant.
    "audit_cross_endpoint_claims.py": (os.path.join(PRIV, "MifBridgeIntrospect.cpp"),
                                       plant_cross_endpoint_claim, "probeSameZz", False),
    "audit_blender_consequence_fields.py": (
        os.path.join(HERE, "blender-addon", "MifBlender", "ops_scene.py"),
        plant_blender_consequence_field, "probeDroppedZz"),
    "audit_promise_flags.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_confirm, "confirm"),
    "mcp_static_check.py": (SERVER, plant_unbound, "mif_probe_zz_unbound"),
    # Exits 1 on a finding, so the exit code is real proof; the marker names the wrapper. Plants
    # into server.py rather than Source/, so it still runs with an editor up.
    "audit_mcp_default_sends.py": (SERVER, plant_default_send, "list_sublevels"),
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
                             "probeDeadZz"),
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
    # must_vanish: every remaining hideKnots= must be gone. Without it, a fourth list_nodes call
    # site appearing in server.py would silently un-plant this again and blame the tool.
    "param_reach.py": (SERVER, plant_unreachable_param, "list_nodes.hideknots", True, "hideKnots="),
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
    # MARKER MOVED to branchOnly_zz 2026-09-03. probeOnly_zz proved the "declared and never read
    # anywhere" arm, which nothing had changed; branchOnly_zz proves the arm this tool is named for
    # - a parameter read ONLY inside a mode branch - which is the one the branch-depth rewrite of
    # that day actually touched. Both probes are still planted; only the assertion moved.
    "audit_mode_params.py": (os.path.join(PRIV, "MifBridgeWorld.cpp"), plant_mode_param,
                             "branchOnly_zz", False),
    # NOT "RULE 4" - that string is in the rules footer this tool prints on every red run, and the
    # already-red guard correctly refused to call that proof. The marker has to be text only a
    # FINDING can produce.
    # FIVE PLANTS, ONE PER RULE. A single entry until 2026-09-04: it proved rule 4 and let the
    # harness report "proven" for a tool that does five separate things.
    "audit_vacuous_checks.py": [
        (os.path.join(HERE, "mcp_static_check.py"), plant_unreachable,
         "is only reached once main() has already returned 0"),
        (os.path.join(HERE, "test_metasound.py"), plant_vacuous_all, "MIFPLANT1"),
        (os.path.join(HERE, "test_metasound.py"), plant_presence_for_value, "MIFPLANT2"),
        (os.path.join(HERE, "test_metasound.py"), plant_empty_counterexample, "MIFPLANT3"),
        # THE MARKER IS THE FIELD NAME, not the planted print. Rule 5 reports
        # "RULE 5 'matchedCount' is printed and never compared" - it never echoes the string being
        # printed, so a MIFPLANT5 marker made the harness call a working rule ASLEEP on its first
        # run. The plant was wrong, not the rule; the harness's own docstring warns that these two
        # are indistinguishable without care, and here it was the marker that needed the care.
        # 'matchedCount' appears nowhere else in the repo, so it cannot go already-red.
        (os.path.join(HERE, "test_metasound.py"), plant_uncompared_count,
         "RULE 5 'matchedCount'"),
    ],
    # The marker names the FILE AND THE DEPTH, not just the field: "arrayDim" alone appears in the
    # detector's own header and in the C++, so a run that merely mentioned it would prove nothing.
    # THE ONLY ENTRY THAT PLANTS INTO docs/. It is a file a person edits by hand, so the restore
    # matters more here than anywhere else - and the harness already asserts the bytes come back
    # identical, which is why this is acceptable at all. Report-style: always exits 0.
    "audit_issue_headings.py": (os.path.join(ROOT, "docs", "06_OPEN_ISSUES_FROM_USE.md"),
                                plant_contradicted_heading, "998.", False),
    # REPORT-STYLE (gate False): always exits 0, so the marker is the whole test. The marker is the
    # PLANTED SUITE'S NAME, which works only because test_pie_family is not in the report already -
    # checked, not assumed. If it ever starts appearing there for its own reasons, this entry stops
    # proving anything and needs a different target.
    "audit_fixture_adoption.py": (os.path.join(HERE, "test_pie_family.py"),
                                  plant_fixture_adoption, "test_pie_family.py", False),
    "audit_nested_field_reads.py": (os.path.join(HERE, "test_uncovered_reads2.py"),
                                    plant_nested_field_read,
                                    'writes "arrayDim" only into a sub-object'),
    # GATING (True): this one exits 1 on a finding, so both the exit code and the marker have to
    # move. The marker is the planted label itself rather than the target's filename - test_group_
    # actors already appears in this detector's UNRESOLVED list for its own reasons, and a marker
    # that is present before the plant would pass for the wrong reason.
    "audit_spawn_labels.py": (os.path.join(HERE, "test_group_actors.py"),
                              plant_spawn_label, "ZzSpawnProbe", True),
    # REPORT-STYLE (gate False): drift prints and the exit code stays 0, so the marker is the whole
    # test. The marker is a phrase from the DRIFT BLOCK rather than the dropped class name, because
    # UPoseAsset already appears in this tool's ordinary NOT HANDLED listing - a class-name marker
    # would be present before the plant and the harness would rightly reject it as already-red.
    # must_vanish proves the plant actually landed rather than silently matching nothing.
    "audit_factory_init.py": (os.path.join(PRIV, "MifBridgeUserTypes.cpp"),
                              plant_factory_init_drift, "does NOT name", False,
                              'TEXT("PoseAsset"), '),
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
# EMPTIED 2026-09-03, and the reasoning it held is preserved in plant_factory_init_drift rather
# than deleted. audit_factory_init was the only entry, filed here because it scans D:/UE532. That is
# true of half of it: it also compares the engine-derived population against create_asset's
# hand-written FactoryInitClasses in THIS repo's MifBridgeUserTypes.cpp, and that half plants
# locally with no engine edit at all. "Not ours to plant" was covering a check that was ours.
#
# The category stays rather than being removed, because the distinction it draws is real and the
# next tool that scans somebody else's tree belongs in it.
NOT_OURS = {}

# Extra argv some tools need to report everything rather than only new-against-baseline findings.
ARGS = {"audit_vacuous_checks.py": ["--all"],
        # Without --check it REPORTS and exits 0, so the harness would call it asleep for doing
        # exactly what it is meant to do outside the gate.
        "audit_consequence_fields.py": ["--check"],
        # Same reason: without --check it reports and exits 0.
        "audit_blender_consequence_fields.py": ["--check"],
        # WITHOUT --check THIS TOOL REWRITES THE TABLE. It is a generator first; the detector is
        # the --check mode. An empty ARGS entry here would have the harness regenerate the file it
        # is meant to be testing, and then report the tool asleep for finding nothing.
        "harvest_param_table.py": ["--check"]}



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


def entries_for(tool):
    """Every plant registered for one tool, as a list.

    A PLANTS value may be a single entry tuple or a LIST of them. Both shapes are supported because
    a tool with five independent rules needs five plants, and because rewriting thirty working
    entries to make one of them multi-valued would be the risky half of that change.

    The single-tuple case is detected by its first slot being a path string - entry[0] is always a
    target filename, and a list's first slot is a tuple, so the two can never be confused.
    """
    v = PLANTS[tool]
    return list(v) if isinstance(v, list) else [v]


def prove(tool, entry=None):
    """(status, detail). status is one of proven / ASLEEP / anchor-gone / already-red."""
    entry = entry if entry is not None else entries_for(tool)[0]
    target, planter, marker = entry[0], entry[1], entry[2]
    gate = entry[3] if len(entry) > 3 else True
    # OPTIONAL 5th slot: a string that must be GONE from the mutated text for the plant to have
    # landed. Optional because most plants ADD something rather than remove it, and "it is there
    # now" is already proved by the marker; this is for the removal-shaped plants, where nothing
    # else distinguishes "the plant did not land" from "the detector is blind".
    must_vanish = entry[4] if len(entry) > 4 else None
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

    # DID THE PLANT ACTUALLY LAND? This is a separate question from "did the tool notice", and
    # until it was asked separately the two were indistinguishable: a plant that changed nothing
    # the tool cares about produced a clean run, which was then reported as ASLEEP - a detector
    # accused of blindness for a defect that was never created.
    #
    # THAT HAS NOW HAPPENED TWICE IN THIS FILE. DEAD_CITATION's plant did it once ("the first run of
    # this plant reported the tool ASLEEP when the plant had never landed"), and param_reach's did
    # it again when mif_layout_graph added two new list_nodes call sites that kept the key
    # reachable. Both times the diagnosis cost more than the bug. A plant may now declare a string
    # that MUST be gone from the mutated text, and its absence is its own verdict.
    if must_vanish is not None and must_vanish in mutated:
        return "plant-did-not-land", (
            "the plant ran but %r is still present afterwards, so no defect was created. This says "
            "NOTHING about %s - do not read it as ASLEEP. Fix the plant." % (must_vanish, tool))
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
    # TWO SKIP REASONS, AND THEY ARE POLARITY-OPPOSITES, so they cannot share a bucket. They did,
    # and the summary hardcoded one of the two headers - so with the editor CLOSED the run printed
    # "SKIPPED - an editor was running", which is the exact inverse of what happened, and sent a
    # reader looking for an editor that was not there. The per-tool line printed the truth all
    # along; only the summary lied. Carrying the reason with the tool is what makes that
    # impossible rather than merely fixed.
    asleep, notproven = [], []
    skipped_no_bridge, skipped_editor_up, unlanded = [], [], []
    for tool in covered:
        if tool in LIVE and not busy:
            skipped_no_bridge.append(tool)
            print("  %-26s %-12s %s" % (tool, "skipped", "needs the bridge; it exits 0 saying "
                                                         "'could not check', not ASLEEP"))
            continue
        plants = entries_for(tool)
        if busy and plants[0][0].startswith(os.path.join(ROOT, "Source")):
            skipped_editor_up.append(tool)
            print("  %-26s %-12s %s" % (tool, "skipped", "editor is running - plants into Source/"))
            continue
        for i, entry in enumerate(plants):
            status, detail = prove(tool, entry)
            # The marker disambiguates which rule was proved. Printing the tool name alone for
            # five rows would read as one result repeated, which is the exact confusion this
            # change exists to end.
            label = tool if len(plants) == 1 else "%s [%s]" % (tool, entry[2][:22])
            print("  %-26s %-12s %s" % (label, status, detail))
            if status == "ASLEEP":
                asleep.append(label)
            elif status == "plant-did-not-land":
                unlanded.append(label)
            elif status != "proven":
                notproven.append(label)

    after = source_digest()
    print("")
    if before != after:
        print("ERROR - Source/ changed across this run. A plant was not restored, or a tool wrote to")
        print("the tree. Check `git status` before trusting anything above.")
        return 2

    print("Source/ is byte-identical to before this run.")
    if skipped_editor_up:
        print("")
        print("SKIPPED - an editor was running, so these were not attempted:")
        for t in skipped_editor_up:
            print("  %s" % t)
    if skipped_no_bridge:
        print("")
        print("SKIPPED - NO bridge was answering, so these could not be plant-tested. This is the")
        print("opposite condition to the block above; with no live editor they exit 0 saying they")
        print("could not check, which a plant would misread as ASLEEP:")
        for t in skipped_no_bridge:
            print("  %s" % t)
    if unlanded:
        print("")
        print("THE PLANT DID NOT LAND - and this says NOTHING about the detector. The mutation ran")
        print("and left the thing it was supposed to remove still in place, so no defect existed")
        print("for the tool to find. Fix the PLANT; do not read these as ASLEEP:")
        for t in unlanded:
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
    return 2 if (skipped_editor_up or skipped_no_bridge or unlanded) else 0


if __name__ == "__main__":
    sys.exit(main())
