"""Does any MCP tool send a parameter its endpoint would REJECT?

param_reach.py checks one direction: endpoint parameters no MCP tool can send, which costs a
capability. This checks the other direction, which costs the whole call - RejectUnknownParams refuses
an unrecognised key outright, so an @mcp.tool that sends one fails 100% of the time, for everyone,
with an error naming a key the caller never typed.

Nothing catches that today. param_reach is blind to it by construction, and a tool nobody has invoked
through MCP will never reveal it, because the python test suites call M.call() directly with their own
payloads rather than going through server.py.

Purely static: reads the _post(...) call sites in server.py and the RejectUnknownParams accept-lists in
the C++, and compares them per endpoint. Anything it reports still needs reading - a dynamically built
payload or an alias list spread across lines can look like a mismatch and not be one.
"""
import json
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp-server", "server.py")
PRIVATE = os.path.join(HERE, "..", "Source", "MifBridge", "Private")


def mcp_sends():
    r"""endpoint -> set of keys the tool passes to _post, read with ast.

    THE REGEX THIS REPLACES COULD NOT READ A CALL USED INSIDE AN EXPRESSION. It was

        _post\(\s*"(\w+)"\s*(.*?)\)\s*$   with re.S | re.M

    - non-greedy, DOTALL, anchored on a ")" at END OF LINE. A call written as
    `_post("list_nodes", ...).get("nodes")` inside a comprehension has no ")" at line end, so the
    match ran on through the following lines and swallowed them. On 2026-08-31 that reported
    list_nodes as sending `added`, `c` and `wrong` - LOCAL VARIABLE NAMES from the enclosing
    function - because `name =` matches an assignment as readily as a keyword argument.

    It had never fired before because no call site wrote _post inline; the first mif_ tool that
    read a response back inside a comprehension found it immediately. A latent bug in a checker is
    worse than a live one, because the checker is what everyone else is trusting.

    ast gets both halves right by construction: keywords belong to the CALL NODE, so a call nested
    in an expression is read exactly like a standalone one, and an assignment is not a keyword.
    **payload splats carry no name and are skipped, as before.
    """
    tree = ast.parse(open(SERVER, encoding="utf-8").read())
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_post" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        keys = {kw.arg for kw in node.keywords if kw.arg}
        keys.discard("payload")
        out.setdefault(first.value, set()).update(keys)
    return out


def cpp_accepts():
    """endpoint -> set of keys RejectUnknownParams allows, or None when it takes no list."""
    out = {}
    for fn in os.listdir(PRIVATE):
        if not fn.endswith(".cpp"):
            continue
        src = open(os.path.join(PRIVATE, fn), encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"void H_(\w+)\(", src):
            ep = m.group(1)
            # Bounded by the NEXT handler, not by a fixed window. A 4000-char window spilled past
            # short handlers into the following one and matched ITS guard, which reported connect_pins
            # as accepting a pin-resolution list it does not have. A false positive in a checker is
            # worse than no checker - it trains you to skim the output.
            nxt = src.find("\n\tvoid H_", m.end())
            body = src[m.end():nxt if nxt != -1 else len(src)]
            g = re.search(r"RejectUnknownParams\(In,\s*Out,\s*\{(.*?)\}", body, re.S)
            if not g:
                out[ep] = None          # no guard - cannot reject, so nothing to check
                continue
            keys = set(re.findall(r'TEXT\("(\w+)"\)', g.group(1)))
            # A list built from a MACRO carries no literal TEXT(...) - describe_endpoint uses
            # { MIF_DESCRIBE_OWN_KEYS }. Treating that as an EMPTY accept-list makes every key the
            # tool sends look rejected, which is how this checker's last false positive was produced.
            # Unknown is not the same as empty, so say unknown and skip it.
            out[ep] = keys if keys or not g.group(1).strip() else None
    return out


def main():
    sends, accepts = mcp_sends(), cpp_accepts()
    problems = []
    for ep, keys in sorted(sends.items()):
        acc = accepts.get(ep, "absent")
        if acc == "absent" or acc is None:
            continue
        unknown = {k for k in keys if k not in acc}
        if unknown:
            problems.append((ep, sorted(unknown), sorted(acc)))
    print("checked %d MCP tools against %d guarded endpoints\n" % (len(sends), len(accepts)))
    if not problems:
        print("no MCP tool sends a key its endpoint would reject.")
        return 0
    for ep, unknown, acc in problems:
        print("  %s" % ep)
        print("     sends but endpoint does NOT accept: %s" % ", ".join(unknown))
        print("     endpoint accepts: %s" % ", ".join(acc)[:140])
    print("\n%d suspicious. Read each before acting - a dynamically built payload or an alias list"
          % len(problems))
    print("split across lines can look like a mismatch and not be one.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
