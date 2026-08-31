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
            # Same call-not-word matching as above. Nothing triggers the bare find() here TODAY -
            # no comment near SpawnDelegateNode mentions the guard by name - but that is a fact
            # about the current text, not a property of the code, and the first instance of this
            # bug was silent. Fixed in both places so the next comment cannot resurrect it.
            scrubbed_delg = H.blank_comments_and_strings(src)
            call = re.compile(r"\bRejectUnknownParams\s*\(").search(scrubbed_delg, i)
            j = call.start() if call else -1
            if j >= 0:
                keys = {x.lower() for x in re.findall(r'TEXT\("([^"]+)"\)', _brace_block(src, j))}
                # THE HANDLER'S OWN BODY, not a 200-character window. The window was a magic number
                # and it silently excluded add_call_dispatcher, whose SpawnDelegateNode call is 656
                # characters past its `void H_` - so this block claimed in its own comment to cover
                # both dispatchers while covering one. Bounding at the next handler is exact and
                # cannot drift as either function grows.
                for ep in ("add_call_dispatcher", "add_bind_dispatcher"):
                    h = re.search(r"void\s+H_%s\s*\(" % ep, src)
                    if not h:
                        continue
                    nxt = HANDLER.search(src, h.end())
                    hbody = src[h.end(): nxt.start() if nxt else len(src)]
                    if "SpawnDelegateNode" in H.blank_comments_and_strings(hbody):
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


# --------------------------------------------------------------------------- the Blender half
#
# Added 2026-08-31. parity_check already checks server -> addon for keys the addon would REFUSE,
# which is mcp_sends_unknown's direction. Nothing checked the reverse until a cone and a torus turned
# out to be creatable only at their DEFAULT dimensions: the addon accepted radius1/radius2 and
# majorRadius/minorRadius, nothing sent them, and the op refuses size/radius for those kinds rather
# than reinterpreting them, so there was no workaround either.
#
# It goes HERE rather than into a new tool because the whole difficulty is the same difficulty: a raw
# diff says 41 of 45 ops have an unreached key, and most of those are ALIASES the server simply does
# not use - `type` for `kind`, `name` beside `object`. looks_like_alias and the baseline are what turn
# that into something readable, and duplicating them would mean maintaining the judgement twice.

BLENDER_TRANSPORT = {"_timeout", "_lock_timeout"}


def addon_accepts():
    """op -> accepted keys, from the addon's own reject_unknown sets via parity_check."""
    try:
        import parity_check as PC
    except Exception:
        return {}
    problems = []
    try:
        ops = PC.load_addon_ops(problems)
    except Exception:
        return {}
    out = {}
    for op, entry in (ops or {}).items():
        acc = entry.get("accepts")
        if acc:
            out[op] = {k.lower() for k in acc}
    return out


def blender_sends():
    """op -> keys any _blender(...) call site passes."""
    py = open(SERVER, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    out = {}
    for m in re.finditer(r'_blender\(\s*"([a-z0-9_]+)"\s*,?([^;]{0,900}?)\)\s*$', py, re.M):
        keys = {x.lower() for x in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", m.group(2) or "")}
        out.setdefault(m.group(1), set()).update(keys)
    # multi-line call sites the anchored form above misses
    for m in re.finditer(r'_blender\(\s*"([a-z0-9_]+)"\s*,([^;]{0,900}?)\n\n', py):
        keys = {x.lower() for x in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", m.group(2) or "")}
        out.setdefault(m.group(1), set()).update(keys)
    return out


ADDON_DIR = os.path.join(HERE, "blender-addon", "MifBlender")
# take(params, "object", "name", required=True) - the FIRST literal is the primary spelling and every
# later one is an alias for the same role. The addon declares these itself, on the line that reads
# the value, so this is derived from the source of truth rather than guessed at.
TAKE_CALL = re.compile(
    r"\btake(?:_bool|_int|_float|_vec)?\s*\(\s*params\s*,\s*((?:\s*[\"'][A-Za-z_]\w*[\"']\s*,?)+)")
LITERAL = re.compile(r"[\"']([A-Za-z_]\w*)[\"']")
OP_DEF = re.compile(r"^def op_(\w+)\s*\(", re.M)


def addon_alias_map():
    """op -> {alias_lower: primary_lower}, read from the addon's own take() calls.

    WHY THIS IS NOT looks_like_alias. That function folds SPELLING variants - one key containing the
    other, or differing by a name/path/id suffix - and it cannot possibly know that `name` is a second
    spelling of `object`, because the two strings have nothing in common. So every op that accepts
    both reported `name` as a lost capability, and 44 of the 46 Blender entries in the baseline were
    that one shape: add_modifier.name, uv_unwrap.name, transfer_weights.to, run_python.script,
    create_primitive.type, rename_bones.map, join_objects.sources and the rest.

    A number that is 95% noise is worse than no number - it is the one that gets quoted. Measured
    2026-08-31: the Blender half of param_reach read 46 unreachable parameters and meant about 2.
    """
    out = {}
    if not os.path.isdir(ADDON_DIR):
        return out
    for fn in sorted(os.listdir(ADDON_DIR)):
        if not fn.startswith("ops_") or not fn.endswith(".py"):
            continue
        with open(os.path.join(ADDON_DIR, fn), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        bounds = [(m.group(1), m.start()) for m in OP_DEF.finditer(text)]
        for i, (op, start) in enumerate(bounds):
            end = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
            for m in TAKE_CALL.finditer(text, start, end):
                names = [n.lower() for n in LITERAL.findall(m.group(1))]
                if len(names) < 2:
                    continue
                primary = names[0]
                for alias in names[1:]:
                    out.setdefault(op, {})[alias] = primary
    return out


def blender_unreachable():
    """Sorted 'bl:op.key' strings for addon capabilities no _blender call site can send."""
    accepts, sends = addon_accepts(), blender_sends()
    aliases = addon_alias_map()
    rows = []
    for op, keys in accepts.items():
        if op not in sends:
            continue                      # op-level parity is parity_check's job
        sent = sends[op] | BLENDER_TRANSPORT
        alias_of = aliases.get(op, {})
        for k in sorted((keys - sent) - NOISE):
            # An alias whose PRIMARY spelling is already sent is not lost capability - the caller can
            # express the call, just not in that particular wording. Only a key with no reachable
            # primary is a real gap.
            primary = alias_of.get(k)
            if primary and (primary in sent or primary in BLENDER_TRANSPORT):
                continue
            if not looks_like_alias(k, sent):
                rows.append("bl:%s.%s" % (op, k))
    return sorted(rows)


def load_baseline():
    if not os.path.exists(BASELINE):
        return set()
    return {ln.strip() for ln in open(BASELINE, encoding="utf-8")
            if ln.strip() and not ln.startswith("#")}


def main():
    found = unreachable() + blender_unreachable()
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

    ue = [r for r in found if not r.startswith("bl:")]
    bl = [r for r in found if r.startswith("bl:")]
    print("param reach: %d unreachable (baseline %d) - %d UE, %d Blender"
          % (len(found), len(base), len(ue), len(bl)))
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
