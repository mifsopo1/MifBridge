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
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "..", "Source", "MifBridge", "Private")

VERBS = ("list_", "get_", "set_", "add_", "remove_", "create_", "delete_", "describe_", "read_",
         "write_", "rename_", "spawn_", "build_", "import_", "export_", "compile_", "resolve_",
         "analyze_", "audit_", "capture_", "render_", "duplicate_", "move_", "snap_", "pie_",
         "load_", "save_", "open_", "close_", "run_", "trigger_", "self_", "implement_", "revert_")

LITERAL = re.compile(r'TEXT\("([^"]*)"\)')
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
            for lit in LITERAL.findall(line):
                stripped = lit.strip()
                aliased = set()
                for span in ALIAS_SPAN.findall(lit):
                    aliased |= set(TOKEN.findall(span))
                for tok in TOKEN.findall(lit):
                    if not tok.startswith(VERBS) or tok in names:
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
        for tok in set(TOKEN.findall(doc)):
            if not tok.startswith(VERBS) or tok in tools or tok in params:
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


def main():
    quiet = "--quiet" in sys.argv
    names = registry()
    if not names:
        print("could not read the endpoint registry")
        return 2
    found = scan(names)
    tools = mcp_docstrings()
    found_mcp = scan_mcp(tools)
    if not found and not found_mcp:
        if not quiet:
            print("messages OK - %d endpoints and %d MCP tools, and every name either of them "
                  "advises exists" % (len(names), len(tools)))
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
        print("")
        print("Each one sends a caller somewhere that answers 'not an endpoint on this build'.")
        print("Either the name is wrong, or the endpoint is missing and the advice is a promise.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
