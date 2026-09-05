"""Find endpoints whose handler accepts unknown keys silently.

WHY THIS IS A FILE AND NOT A GREP, and the reason is that I ran the grep twice and it lied twice.

RejectUnknownParams is what stops `connect_pins {fromNode: ...}` from being accepted, ignored, and
answered ok:true. An endpoint without one takes any key you send and tells you nothing. So "which
endpoints have no guard" is worth knowing - and the obvious way to ask it is wrong:

    for each `void H_<name>(...)`, does its body contain RejectUnknownParams?

That reported SIX unguarded endpoints on 2026-09-01. All six were guarded. Every one of them is a
thin wrapper that delegates:

    H_connect_pins        -> DoConnect              guard lives there
    H_reconnect_pin       -> DoConnect
    H_add_variable_get    -> DoAddVariableNode
    H_add_variable_set    -> DoAddVariableNode
    H_add_bind_dispatcher -> SpawnDelegateNode<UK2Node_AddDelegate>
    H_add_call_dispatcher -> SpawnDelegateNode<UK2Node_CallDelegate>

and the source SAYS so - SpawnDelegateNode carries "ONE guard serves add_call_dispatcher AND
add_bind_dispatcher... Do NOT add a second guard in either H_ function - the key list would then
have two places to drift apart." Sharing the guard is the correct pattern here, not an omission.

The second attempt followed delegation with a regex for `Helper(In,` and still missed the last two,
because SpawnDelegateNode is a TEMPLATE and `SpawnDelegateNode<UK2Node_AddDelegate>(In, Out)` does
not match `\\w+\\(In,`. Two different false-positive sets from two different shortcuts, on a question
whose whole value is being able to trust the answer.

So this walks the call graph properly: real brace-matched bodies, and helper calls matched through
their template arguments. A handler is guarded if it guards itself OR reaches something that does.

Usage:
    python tools/audit_param_guards.py            # report
    python tools/audit_param_guards.py --quiet    # exit code only: 0 clean, 1 look at it
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "..", "Source", "MifBridge", "Private")

# A call to Foo(...) or Foo<Bar>(...). The template arguments are matched and discarded, which is
# the whole point - SpawnDelegateNode<UK2Node_AddDelegate>(In, Out) is a call to SpawnDelegateNode.
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*(?:<[^<>;{}]*>)?\s*\(")

# Any function definition, member or free, that takes the handler pair. Handlers and the helpers
# they delegate to have the same shape here, so one pattern finds both.
FUNC = re.compile(r"\b(?:void|bool)\s+(\w+)\s*(?:<[^<>;{}]*>)?\s*\([^;{}]*?FJsonObject[^;{}]*?\)\s*\{")

# Keywords that read like calls and are not. Without this every `if (`, `for (` and `switch (`
# becomes a phantom helper and the reachability walk goes everywhere.
NOT_CALLS = {"if", "for", "while", "switch", "return", "sizeof", "catch", "do", "else"}


def bodies():
    """name -> function body, brace-matched.

    Brace matching rather than "text until the next `void H_`", which was the first shortcut. A
    handler followed by a helper in the same file silently absorbed the helper's body, so guards
    were attributed to whichever function happened to sit above them.
    """
    out = {}
    for fn in sorted(os.listdir(PRIVATE)):
        if not fn.endswith(".cpp"):
            continue
        src = io.open(os.path.join(PRIVATE, fn), encoding="utf-8", errors="replace").read()
        for m in FUNC.finditer(src):
            i = src.index("{", m.end() - 1)
            depth, j = 0, i
            while j < len(src):
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.setdefault(m.group(1), "")
            out[m.group(1)] += src[i:j + 1]
    return out


def registry():
    header = io.open(os.path.join(PRIVATE, "MifBridgeHandlers.h"), encoding="utf-8",
                     errors="replace").read()
    # Skip the #define line itself. `#define MIF_DECL(Name) void H_##Name(...)` matches the same
    # pattern as a real declaration and put an endpoint called "Name" in the first report.
    header = "\n".join(l for l in header.split("\n") if not l.lstrip().startswith("#define"))
    return set(re.findall(r"MIF_DECL\((\w+)\)", header))


def audit():
    body = bodies()
    guards_itself = {n for n, b in body.items() if "RejectUnknownParams" in b}

    def reaches_guard(name, seen):
        """Does `name` guard, or call anything that does? Depth-limited by `seen`, not by hops."""
        if name in guards_itself:
            return name
        if name in seen:
            return None
        seen.add(name)
        for callee in CALL.findall(body.get(name, "")):
            if callee in NOT_CALLS or callee == name:
                continue
            if callee in body:
                hit = reaches_guard(callee, seen)
                if hit:
                    return hit
        return None

    rows = []
    for ep in sorted(registry()):
        h = "H_" + ep
        if h not in body:
            rows.append((ep, "NO HANDLER", None))
            continue
        via = reaches_guard(h, set())
        if via is None:
            rows.append((ep, "UNGUARDED", None))
        elif via != h:
            rows.append((ep, "guarded", via))
    return rows


def main():
    quiet = "--quiet" in sys.argv
    rows = audit()
    unguarded = [r for r in rows if r[1] == "UNGUARDED"]
    missing = [r for r in rows if r[1] == "NO HANDLER"]
    delegated = [r for r in rows if r[1] == "guarded"]

    if not quiet:
        print("%d endpoint(s) guard through a shared helper - correct, and the reason a naive "
              "per-handler grep reports them as holes:" % len(delegated))
        for ep, _, via in delegated:
            print("    %-28s guarded by %s" % (ep, via))
        print("")
        if missing:
            print("%d declared endpoint(s) with no H_ handler in Private/:" % len(missing))
            for ep, _, _ in missing:
                print("    %s" % ep)
            print("")
        if unguarded:
            print("%d endpoint(s) ACCEPT ANY KEY SILENTLY:" % len(unguarded))
            for ep, _, _ in unguarded:
                print("    %s" % ep)
            print("")
            print("An endpoint with no RejectUnknownParams takes a misspelled or invented key,")
            print("ignores it, and answers ok:true. That is the failure this plugin exists to refuse.")
        else:
            print("no endpoint accepts unknown keys silently.")
            # THE ADDON HALF IS COVERED ELSEWHERE, and saying so is the whole fix here. This tool
            # reads the C++ only, so the line above is a verdict about half a two-backend product -
            # but unlike the other audits swept on 2026-09-04, that half is NOT unguarded. It has a
            # static gate and two dynamic ones, and the only thing missing was a reader knowing
            # where to look.
            print("")
            print("  This covers the C++ only. The addon's equivalent is NOT unchecked:")
            print("    parity_check.py CHECK 2       static, fail-closed, and in the release gates")
            print("    test_blender_refusals B110    an unknown key is actually sent and refused")
            print("    test_blender_reject_unknown   the same property across the op surface")
    return 1 if unguarded else 0


if __name__ == "__main__":
    sys.exit(main())
