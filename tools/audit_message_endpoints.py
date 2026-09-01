"""Find user-facing messages that tell the caller to use an endpoint which does not exist.

WHY THIS EXISTS. MifBridge's error messages are unusually helpful - most name the endpoint you should
have called instead. That is the whole point of them, and it means a WRONG name is worse than no
advice at all: the caller follows the instruction and gets "not an endpoint on this build", having
been sent there by the bridge itself.

Found on 2026-08-27, by asking the question mechanically for the first time:

    remove_node is the real endpoint; THREE messages tell you to call delete_node
    list_tree_widgets is the real endpoint; a RejectUnknownParams hint points at list_widgets
    create_water_zone is named as the way to make a body visible and has never existed

The last one was not just a typo. It was the visible end of a real capability gap: water bodies can
be created and the AWaterZone they need in order to RENDER cannot, so create_water_body's advice had
nowhere to send anyone.

HOW IT DECIDES, and the one rule that makes it usable. An endpoint-shaped token inside a TEXT("...")
literal is a REFERENCE only when it is embedded in prose. When the token IS the entire literal it is
an identifier - a parameter alias like TEXT("save_maps"), or an entry in the forbidden-editor-command
list like TEXT("save_all") - and means nothing about endpoints. Without that distinction this check
reports eight things and five of them are noise, which is how a check gets ignored.

Deliberately conservative in two more ways: a token must start with one of the verb prefixes this
project actually uses, and it must contain an underscore. A vague word is never flagged.

BOTH HALVES OF THE INTERFACE. The endpoint messages above, and the MCP tool docstrings - which are
what an AGENT reads before choosing a tool, so a name that does not resolve there costs a wasted
call and a confusing error. The MCP side needs two exclusions of its own, both found by running it:
a tool's own PARAMETERS are snake_case and shaped exactly like tool names (removeBinding arrives as
remove_binding), and a sentence that DENIES a tool exists - 'there is deliberately no separate
run_editor_exec' - is the opposite of a dead end.

Usage:
    python tools/audit_message_endpoints.py            # report
    python tools/audit_message_endpoints.py --quiet    # exit code only: 0 clean, 1 look at it
"""
import ast
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "..", "Source", "MifBridge", "Private")

VERBS = ("list_", "get_", "set_", "add_", "remove_", "create_", "delete_", "describe_", "read_",
         "write_", "rename_", "spawn_", "build_", "import_", "export_", "compile_", "resolve_",
         "analyze_", "audit_", "capture_", "render_", "duplicate_", "move_", "snap_", "pie_",
         "load_", "save_", "open_", "close_", "run_", "trigger_", "self_", "implement_", "revert_")

# TWO patterns, because one line-anchored pattern misses the messages that matter most.
#
# The original was TEXT\("([^"]*)"\) - it required the closing `")` on the SAME LINE. C++ string
# literals concatenate across lines, and every LONG message in this module is written that way:
#
#     Fail(Out, TEXT("the asset is dirty and NOT saved - save_asset persists it. Sync markers only "
#                    "do anything inside a sync group: two sequences must share the marker NAMES"));
#
# findall returned [] for that first line. The risk is exactly inverted from where the check was
# looking: the longer and more helpful a message is, the more likely it spans lines, and the more
# likely it names an endpoint to go to next - which is the thing this tool exists to verify. Two
# identical `save_asset persists it` notes sat in MifBridgeAnimation.cpp and only the single-line
# one was ever reported.
#
# OPEN matches a TEXT( literal whether or not its closing paren is on the line. CONT matches a
# continuation line that is nothing but a quoted string, which is how the second and later fragments
# of a concatenated literal are always written here. A continuation line cannot be confused with an
# ordinary statement: a bare "..." with no call around it is not valid C++ on its own.
LITERAL = re.compile(r'TEXT\("([^"]*)"')
CONTINUATION = re.compile(r'^\s*"([^"]*)"\s*[)\];,]*\s*$')

# TWO THINGS THAT LOOK LIKE BAD ADVICE AND ARE NOT. Both surfaced the moment the scan could see
# multi-line literals, and both would have made this tool cry wolf on correct, careful text.
#
# 1. A DECLARED ABSENCE. MifBridgeAnimation.cpp tells the caller how to move and delete a socket
#    with set_property and edit_container, and finishes: "Both work today, which is why there is no
#    set_socket_transform or remove_socket endpoint." Naming a non-endpoint is the POINT of that
#    sentence. Reporting it would punish the most honest kind of message this bridge writes.
#    Matched the same way audit_absence_claims does it - by direction, on a window, rather than by
#    hunting for the name anywhere in the file.
# 2. A TOOL PATH. "tools/audit_factory_init.py --class U%s shows exactly what that factory does" is
#    a script, not an endpoint, and VERBS happens to include audit_ so the token matched. Anything
#    written as tools/<name>.py is excluded by shape.
ABSENCE = re.compile(
    r"(there (?:is|are) no\b|no such\b|does not exist\b|is not an endpoint\b|\bnever built\b)",
    re.I)
ABSENCE_WINDOW = 90
# How far an `# audit-ok:` waiver reaches. Bounded on purpose: a waiver explains the ONE branch
# written under it. Letting it run to the end of the function would allow a note about
# _check_format's glTF branch to excuse a hard-coded verb name reintroduced in its OTHER branch -
# which is precisely the defect this check was built to find.
WAIVER_LINES = 16
TOOL_PATH = re.compile(r"tools/[A-Za-z0-9_]+\.py")
TOKEN = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
# "maps (aliases: saveMaps, save_maps)" documents PARAMETER names. They are shaped exactly like
# endpoints and mean something else entirely, so the span after an 'alias' marker is excluded.
ALIAS_SPAN = re.compile(r"alias(?:es)?\s*:?\s*([^)]*)", re.IGNORECASE)


def registry():
    """Every endpoint name, from both halves of the registry."""
    names = set()
    common = io.open(os.path.join(PRIVATE, "MifBridgeCommon.cpp"), encoding="utf-8",
                     errors="replace").read()
    header = io.open(os.path.join(PRIVATE, "MifBridgeHandlers.h"), encoding="utf-8",
                     errors="replace").read()
    names |= set(re.findall(r"MIF_BIND\((\w+)\)", common))
    names |= set(re.findall(r"MIF_DECL\((\w+)\)", header))
    return names


def scan(names):
    found = {}
    for fn in sorted(os.listdir(PRIVATE)):
        if not fn.endswith(".cpp"):
            continue
        path = os.path.join(PRIVATE, fn)
        for i, line in enumerate(io.open(path, encoding="utf-8", errors="replace").read().split("\n")):
            for lit in LITERAL.findall(line) + CONTINUATION.findall(line):
                stripped = lit.strip()
                aliased = set()
                for span in ALIAS_SPAN.findall(lit):
                    aliased |= set(TOKEN.findall(span))
                # A declared absence names the missing endpoint deliberately; look BEHIND the
                # token, because the phrasing always precedes it ("there is no X", not "X there is
                # no"). The window keeps it local - a "there is no" elsewhere in a long message must
                # not license every name after it.
                absent_spans = [m.end() for m in ABSENCE.finditer(lit)]
                tool_paths = set(TOOL_PATH.findall(lit))
                for m in TOKEN.finditer(lit):
                    tok = m.group(1)
                    if not tok.startswith(VERBS) or tok in names:
                        continue
                    if any("tools/%s.py" % tok in tp for tp in tool_paths):
                        continue
                    if any(0 <= m.start() - e <= ABSENCE_WINDOW for e in absent_spans):
                        continue
                    # The token IS the literal: an alias or a command name, not advice.
                    if stripped == tok:
                        continue
                    # Named inside an 'aliases: ...' span: a parameter, not an endpoint.
                    if tok in aliased:
                        continue
                    found.setdefault(tok, []).append("%s:%d" % (fn, i + 1))
    return found


# A docstring may name a tool in order to say it does NOT exist, and several deliberately do -
# "there is deliberately no separate run_editor_exec: it would have been a third copy of the same
# UEngine::Exec call". That is the opposite of a dead end and must not be flagged.
# IGNORECASE, because the denial is usually the START of a sentence - "There is no separate X".
# Without it this matched nothing that began a sentence, which is most denials, and the first
# real one it met slipped straight through.
DENIAL = re.compile(r"(?:no separate|deliberately no|there is no|not a tool|does not exist)",
                    re.IGNORECASE)


def mcp_docstrings():
    """Every @mcp.tool function: its name, its parameters, and its docstring.

    Parsed with ast rather than matched with a regex. The docstrings are single long string literals
    full of quotes, braces and JSON examples, and a quote-aware regex for them is exactly the kind of
    thing that half-works.
    """
    path = os.path.join(HERE, "mcp-server", "server.py")
    tools = {}
    tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                args = node.args
                tools[node.name] = (
                    set(a.arg for a in list(args.args) + list(args.kwonlyargs)),
                    ast.get_docstring(node) or "")

    # AND THE SIDECAR, which is the half an agent reads most carefully. server.py keeps only the lead
    # sentence inline - 450 tool descriptions sit in the model's context on EVERY turn, which came to
    # ~72,000 tokens - and serves the FULL text from tool_help.json through mif_help. That sidecar is
    # where the traps, the engine citations and the failure modes live, and mif_help's own description
    # tells the agent to call it BEFORE using a tool it has not used before.
    #
    # It was never scanned. A wrong endpoint name is MORE costly there than inline, not less: the
    # agent has just been told to read this text precisely because it is about to do something it
    # does not know how to do. Two `save_asset persists it` notes sat in it and were found by hand.
    help_path = os.path.join(HERE, "mcp-server", "tool_help.json")
    try:
        store = json.load(io.open(help_path, encoding="utf-8", errors="replace"))
    except Exception:
        store = {}
    for key, text in store.items():
        if key.startswith("__") or not isinstance(text, str):
            continue
        params, doc = tools.get(key, (set(), ""))
        tools[key] = (params, (doc + "\\n" + text) if doc else text)
    return tools


def scan_mcp(tools):
    """The other half of the interface, and the half an AGENT actually reads.

    An MCP docstring is what the model sees before choosing a tool, so a name that does not resolve
    there costs a wasted call and a confusing error. Two exclusions, both learned by running it: a
    tool's OWN parameters are snake_case and shaped exactly like tool names (removeBinding arrives as
    remove_binding), and a sentence that DENIES the tool exists is correct rather than broken.
    """
    found = {}
    for name, (params, doc) in tools.items():
        # A SIBLING TOOL'S PARAMETER, when the docstring says whose it is. bl_list_particles is
        # described as "the verification half of bl_add_particles" and goes on to explain
        # render_type - which is bl_add_particles' parameter and Blender's own ParticleSettings
        # property, not an endpoint. The existing `tok in params` exclusion could not see it,
        # because the READ op has no parameters of its own to match against.
        #
        # Scoped to tools the docstring actually NAMES rather than to every tool: a paired
        # read/write tool discussing its partner's arguments is normal and correct, and blanket-
        # excluding every parameter of every tool would hide a genuinely dead endpoint name that
        # happened to collide with some argument somewhere.
        kin = set(params)
        for other in TOKEN.findall(doc):
            if other in tools and other != name:
                kin |= tools[other][0]
        for tok in set(TOKEN.findall(doc)):
            if not tok.startswith(VERBS) or tok in tools or tok in kin:
                continue
            sentence = ""
            for part in re.split(r"(?<=[.;])\s+", doc):
                if re.search(r"\b" + re.escape(tok) + r"\b", part):
                    sentence = part
                    break
            if DENIAL.search(sentence):
                continue
            found.setdefault(tok, []).append(name)
    return found


ADDON = os.path.join(HERE, "blender-addon", "MifBlender")
DEF_RE = re.compile(r"^def (\w+)\(")
OP_DEF = re.compile(r"^def op_(\w+)", re.M)


def blender_helper_findings():
    """A shared Blender helper whose error message blames ONE of the ops that call it.

    THE BUG THIS IS FOR, found by pushing a real Unreal-exported FBX through the round trip:
    _select_edges is called by select_edges, bevel_edges AND extrude_skirt, and its refusal said

        "bevel_edges needs a selector, and refuses to guess..."

    for whichever of the three you actually called. The refusal was correct; the name on it was not,
    and it sent you to read the wrong op's documentation.

    NARROW, because the broad version is useless here. "any op name in any message" matches five
    things in this addon and ALL FIVE are legitimate: a bmesh operation named for debugging
    (extrude_edge_only), a parameter (import_result), third-party payload keys (remove_floaters),
    an FBX kwarg (object_types). What has signal is the conjunction:

        a PRIVATE helper  +  called by two or more ops  +  naming one of its OWN callers

    An op name that is not a caller is advice - "run gen_texture to paint it" - and is left alone.
    """
    if not os.path.isdir(ADDON):
        return []
    sources = {}
    for fn in sorted(os.listdir(ADDON)):
        if fn.endswith(".py"):
            sources[fn] = io.open(os.path.join(ADDON, fn), encoding="utf-8",
                                  errors="replace").read()
    ops = set()
    for src in sources.values():
        ops |= set(OP_DEF.findall(src))

    # Which ops call which private helper. Attribution is by the enclosing def, so a helper called
    # from another helper does not count as a caller - only an op does.
    callers = {}
    for src in sources.values():
        current = None
        for line in src.split("\n"):
            m = DEF_RE.match(line)
            if m:
                current = m.group(1)
                continue
            if not current or not current.startswith("op_"):
                continue
            for helper in re.findall(r"\b(_\w+)\s*\(", line):
                callers.setdefault(helper, set()).add(current[len("op_"):])

    found = []
    for fn, src in sources.items():
        current = None
        in_doc = False
        waived = {}
        for i, line in enumerate(src.split("\n")):
            m = DEF_RE.match(line)
            if m:
                current = m.group(1)
                in_doc = False
                waived = {}
                continue
            # BACK TO MODULE LEVEL ends the function. Without this the tracker stayed on the last
            # def it saw, so the OPS = { "gen_mesh": op_gen_mesh, ... } table at the bottom of
            # ops_gen.py looked like it lived inside _first_path() and reported two hits that were
            # a dispatch table doing its job.
            if line and not line[0].isspace() and not line.startswith(")"):
                current = None
                in_doc = False
                waived = {}
                continue
            if not current or not current.startswith("_"):
                continue
            # A COMMENT is not a message. The fix for this very bug carries a comment quoting
            # "bevel_edges" while explaining it, and flagged itself on the first run.
            if line.lstrip().startswith("#"):
                # ...but a comment IS where a deliberate exception gets declared. Some branches of
                # a shared helper are reachable by exactly one caller - _check_format's glTF refusal
                # only runs when `allowed is _EXPORT_FORMATS` - so naming that caller is correct,
                # and naming the OTHER verb is the useful part of the sentence ("import_mesh DOES
                # take glTF"). The waiver lists the names it excuses rather than silencing the
                # helper, so a THIRD op's name appearing here later still gets reported.
                w = re.search(r"audit-ok:\s*([^-\n]+)", line)
                if w:
                    for nm in re.split(r"[,\s]+", w.group(1).strip()):
                        if nm:
                            waived[nm] = i + WAIVER_LINES
                continue
            # NEITHER IS A DOCSTRING, and assuming otherwise produced a false positive on
            # 2026-09-01: ops_render._apply_common's docstring reads "Settings shared by
            # set_render_settings and the overrides render_still accepts", which names BOTH
            # callers, correctly, and is documentation rather than anything a caller is ever
            # shown. The refusal inside that helper says "this op" and is caller-agnostic, so
            # there was nothing wrong with the code the report pointed at.
            #
            # It matters because a false positive is how a detector stops being read. The
            # line-wise quote scan cannot tell a docstring from a message, so docstring lines
            # are tracked and skipped explicitly.
            stripped = line.lstrip()
            if in_doc:
                if '"""' in stripped or "'''" in stripped:
                    in_doc = False
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                quote = stripped[:3]
                if stripped.count(quote) < 2:     # a one-line docstring opens AND closes here
                    in_doc = True
                continue
            mine = callers.get(current, set())
            if len(mine) < 2:
                continue                      # not shared: naming itself is fine
            # EVERY string literal, not just long ones. A 12-character minimum was the first cut
            # and it had a hole: the name reaches the message as a short format ARGUMENT just as
            # easily as it sits inside the sentence, and `% "bevel_edges"` is eleven characters.
            # Found by re-introducing the bug two ways and watching only one of them get caught.
            #
            # Short strings are safe to scan here because the scope is already narrow: we are inside
            # a PRIVATE helper that two or more ops call. The op table's own "bevel_edges": op_...
            # entries live at module level and never reach this branch.
            for text in re.findall(r'"([^"]*)"', line):
                for op in mine:
                    if i <= waived.get(op, -1):
                        continue
                    if re.search(r"\b%s\b" % re.escape(op), text):
                        found.append("%s:%d\t%s() is shared by %d ops and its message names '%s'"
                                     % (fn, i + 1, current, len(mine), op))
    return found


def main():
    quiet = "--quiet" in sys.argv
    names = registry()
    if not names:
        print("could not read the endpoint registry")
        return 2
    found = scan(names)
    tools = mcp_docstrings()
    found_mcp = scan_mcp(tools)
    found_bl = blender_helper_findings()
    if not found and not found_mcp and not found_bl:
        if not quiet:
            print("messages OK - %d endpoints, %d MCP tools and the Blender addon, and every name "
                  "any of them advises exists and belongs to the right op" % (len(names), len(tools)))
        return 0
    if not quiet:
        if found:
            print("%d name(s) advised in ENDPOINT text that are NOT endpoints:" % len(found))
            for tok, where in sorted(found.items()):
                print("  %-32s %s" % (tok, ", ".join(where[:4])))
        if found_mcp:
            print("%d name(s) cited in MCP DOCSTRINGS that are NOT tools:" % len(found_mcp))
            for tok, where in sorted(found_mcp.items()):
                print("  %-32s cited by %s" % (tok, ", ".join(sorted(set(where))[:4])))
        if found_bl:
            print("%d shared Blender helper(s) blaming ONE of their callers:" % len(found_bl))
            for f in sorted(found_bl):
                where, what = f.split("\t", 1)
                print("  %-26s %s" % (where, what))
        print("")
        print("A name that does not resolve sends the caller to 'not an endpoint on this build'.")
        print("A shared helper naming one caller sends everyone else to the wrong op's docs - the")
        print("refusal is right and the name on it is not, which is harder to notice.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
