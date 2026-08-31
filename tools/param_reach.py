"""CHECK: can the MCP tools actually SEND every parameter the UE endpoints ACCEPT?

Why this exists. A user reported that `add_bind_dispatcher` "exposes the dispatcher name but no
target class parameter", so binding `DDS2_GameMode.PlayerLoggedChanged` from another Blueprint had to
be done by hand in the editor. The symptom was real; the diagnosis was not. The C++ endpoint had
accepted `targetClass` all along - the MCP tool never passed it, so no agent driving through MCP could
express the call. The capability existed and was unreachable.

`parity_check.py` could not see it: on the UE side it compares endpoint NAMES (241 `_post` vs 229
`MIF_BIND`), and its parameter check only covers the Blender addon. A whole class of drift - endpoint
grows a parameter, tool never exposes it - was invisible.

Scanning for it turned up more of the same, including two added days earlier and never wired up:
`self_audit`'s `summaryOnly` (the compact mode built because the full response was too large to read)
and `trace_ground`'s `location` (added after top-level x/y silently ignored `location:{}`, which had
invalidated an entire terrain investigation by tracing at the world origin).

HOW IT READS BOTH SIDES
  C++  a `void H_<endpoint>(...)` body's first `RejectUnknownParams(In, Out, { TEXT("a"), ... })`
       list is the set of keys that endpoint accepts.
  py   every `_post("<endpoint>", a=..., b=...)` call site is what the tool can send.

ALIASES, AND WHY THIS RATCHETS INSTEAD OF FAILING OUTRIGHT
Most endpoints accept several spellings for one role - `add_cast` takes castTo / class / cls / to /
targetType for the single thing the tool sends as `targetClass`. There is no machine-readable alias
map, so a raw diff is mostly vocabulary noise. `looks_like_alias` folds obvious variants together,
and what survives is a CANDIDATE for a human to read.

Even filtered there is a real backlog, so failing on all of it would just get the check switched off.
Instead the accepted state is recorded in `param_reach_baseline.txt` and only ADDITIONS fail. Existing
entries stay visible on every run; a newly-unreachable parameter breaks the build the day it appears.

To accept a new entry deliberately, run with --update-baseline and put the reason in the commit
message. Three legitimate reasons an accepted key is not sent, all present in today's baseline:

  ALIAS          the tool sends another spelling of the same role (add_cast takes castTo / class /
                 cls / to / targetType for what the tool sends as targetClass).
  DELIBERATE     the endpoint offers a form the tool intentionally does not. set_material_parameter
                 accepts a singular {parameter, value} pair AND scalar/vector maps; its docstring
                 says outright "through this tool use the maps".
  REFUSAL-ONLY   the key exists so the endpoint can give a GOOD error. reset_property_to_default and
                 edit_container accept blueprintId/widgetName purely to recognise the widget-template
                 form and say "use set_property" - accepting the key is how you get a useful refusal
                 instead of a confusing one.

So a baseline entry is not a to-do. Read the endpoint before treating one as a gap - I chased all
three of the above before the docstrings said otherwise.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harvest_param_table as H          # one comment/string scrubber, not two

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PRIV = os.path.join(ROOT, "Source", "MifBridge", "Private")
SERVER = os.path.join(HERE, "mcp-server", "server.py")
BASELINE = os.path.join(HERE, "param_reach_baseline.txt")

# 'path' is documented on several endpoints as back-compat-and-ignored, so it is not a capability.
NOISE = {"in", "out", "path"}

HANDLER = re.compile(r"void\s+H_([A-Za-z0-9_]+)\s*\(")


def _brace_block(text, start):
    """Text of the {...} block beginning at or after `start`, braces balanced."""
    j = text.find("{", start)
    if j < 0:
        return ""
    depth, k = 0, j
    while k < len(text):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j:k]
        k += 1
    return ""


def endpoint_accepts():
    """endpoint -> set of accepted keys, from each handler's RejectUnknownParams list."""
    out = {}
    for fn in sorted(os.listdir(PRIV)):
        if not fn.endswith(".cpp"):
            continue
        src = open(os.path.join(PRIV, fn), encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        for m in HANDLER.finditer(src):
            nxt = HANDLER.search(src, m.end())
            body = src[m.end(): nxt.start() if nxt else len(src)]
            # THE CALL, NOT THE WORD, and not one inside a comment. This was a bare
            # body.find("RejectUnknownParams"), which matched the phrase in a COMMENT in
            # H_recipe_override_and_call_parent ("Adding a RejectUnknownParams here would be the
            # wrong fix") and then read the next brace block - that handler's list of REFUSED
            # spellings - as its accepted keys. It reported three parameters as accepted-but-
            # unreachable that the endpoint exists to reject.
            #
            # harvest_param_table already parses this correctly, scrubbing comments and strings
            # before matching \bRejectUnknownParams\s*\( - so its scrubber is reused here rather
            # than a second, weaker parser being kept alive beside it.
            scrubbed = H.blank_comments_and_strings(body)
            call = re.search(r"\bRejectUnknownParams\s*\(", scrubbed)
            if not call:
                continue
            i = call.start()
            keys = {x.lower() for x in re.findall(r'TEXT\("([^"]+)"\)', _brace_block(body, i))}
            if keys:
                out.setdefault(m.group(1), set()).update(keys)

    # Some handlers are one-line wrappers around a SHARED guard living in a template - the guard's
    # key list belongs to every endpoint that routes through it. SpawnDelegateNode is the case that
    # started all this: add_bind_dispatcher and add_call_dispatcher both delegate to it.
    delg = os.path.join(PRIV, "MifBridgeDelegates.cpp")
    if os.path.exists(delg):
        src = open(delg, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
        i = src.find("void SpawnDelegateNode")
        if i >= 0:
            j = src.find("RejectUnknownParams", i)
            if j >= 0:
                keys = {x.lower() for x in re.findall(r'TEXT\("([^"]+)"\)', _brace_block(src, j))}
                for ep in ("add_call_dispatcher", "add_bind_dispatcher"):
                    if re.search(r"void\s+H_%s[\s\S]{0,200}SpawnDelegateNode" % ep, src):
                        out.setdefault(ep, set()).update(keys)
    return out


def tool_sends():
    """endpoint -> set of keys any _post() call site passes."""
    py = open(SERVER, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    out = {}
    for m in re.finditer(r'_post\(\s*"([a-z0-9_]+)"\s*(,[\s\S]*?)?\)\s*$', py, re.M):
        args = m.group(2) or ""
        keys = {x.lower() for x in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", args)}
        keys |= {x.lower() for x in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', args)}
        out.setdefault(m.group(1), set()).update(keys)
    # multi-line _post calls the anchored pattern above misses
    for m in re.finditer(r'_post\(\s*"([a-z0-9_]+)"\s*,([^;]{0,600}?)\n\n', py):
        args = m.group(2)
        keys = {x.lower() for x in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", args)}
        keys |= {x.lower() for x in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', args)}
        out.setdefault(m.group(1), set()).update(keys)
    return out


def looks_like_alias(key, sent):
    """Fold obvious spelling variants of a role the tool already covers."""
    for s in sent:
        if key in s or s in key:
            return True
        for a, b in ((key, s), (s, key)):
            for affix in ("name", "class", "path", "id", "guid", "value", "type"):
                if a.endswith(affix) and a[: -len(affix)] and a[: -len(affix)] in b:
                    return True
    return False


def unreachable():
    """Sorted 'endpoint.key' strings for capabilities no tool call can send."""
    accepts, sends = endpoint_accepts(), tool_sends()
    rows = []
    for ep, keys in accepts.items():
        if ep not in sends:
            continue                      # name-level parity is parity_check.py's job
        sent = sends[ep]
        for k in sorted((keys - sent) - NOISE):
            if not looks_like_alias(k, sent):
                rows.append("%s.%s" % (ep, k))
    return sorted(rows)


def load_baseline():
    if not os.path.exists(BASELINE):
        return set()
    return {ln.strip() for ln in open(BASELINE, encoding="utf-8")
            if ln.strip() and not ln.startswith("#")}


def main():
    found = unreachable()
    if "--update-baseline" in sys.argv:
        with open(BASELINE, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("# Endpoint parameters no MCP tool currently sends - see param_reach.py.\n")
            f.write("# Accepted backlog. Only ADDITIONS to this list fail the check.\n")
            f.write("# Regenerate deliberately with: python tools/param_reach.py --update-baseline\n")
            for r in found:
                f.write(r + "\n")
        print("baseline updated: %d entries" % len(found))
        return 0

    base = load_baseline()
    new = [r for r in found if r not in base]
    gone = [r for r in base if r not in found]

    print("param reach: %d unreachable (baseline %d)" % (len(found), len(base)))
    for r in gone:
        print("  FIXED    %s  (now reachable - drop it from the baseline)" % r)
    if new:
        print()
        print("NEW UNREACHABLE PARAMETERS - the endpoint accepts these and no MCP tool sends them:")
        for r in new:
            print("  %s" % r)
        print()
        print("Expose them in tools/mcp-server/server.py, or accept them with")
        print("  python tools/param_reach.py --update-baseline")
        return 1
    print("OK  no newly unreachable parameters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
