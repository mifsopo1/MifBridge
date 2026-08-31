"""Suites that read a NESTED field at the TOP level - the silent pass that reports success.

WHY THIS EXISTS. Three times in one session a probe or a suite asked a response for a field the
handler emits inside a sub-object, got None because the key is not at that depth, and treated the
None as an answer:

  * Blender's object_info nests its counts under 'object'
  * Blender's create_primitive does the same
  * describe_property nests the whole property row under 'property', so arrayDim - which
    MifBridgeDetails.cpp:353 emits UNCONDITIONALLY - reads as None at the top level, and that None
    was read as "this property is not a fixed-size C-array". It is: arrayDim is 8.

THE REASON IT KEEPS LANDING IS THAT THE FAILURE IS SILENT AND POSITIVE. A missing key returns None,
and a check comparing None against an expected None PASSES. So the mistake does not announce itself
in either direction: the probe concludes the wrong thing, and a suite written from that conclusion
goes green. Every other checker here asks whether a field is READ; none asks whether it is read at
the DEPTH IT IS WRITTEN.

FIVE THINGS THIS HAD TO GET RIGHT, and every one of them produced confident nonsense first. The run
that motivated each is in brackets:

  1. PER ENDPOINT, NOT PER NAME. Field names are not unique across 450 endpoints - 'active' is
     top-level on ui_scenario_status AND a per-ability field inside list_gameplay_abilities. The
     question is only ever "does THIS endpoint put THIS field at the top", so emissions are
     attributed to their enclosing H_<name> and reads to the endpoint the M.call names.

  2. A HELPER'S OUT PARAM *IS* THE RESPONSE. ResolveWidgetWorld writes worldSource into a parameter
     called OutInfo and its caller passes the response in, so worldSource is top-level with no
     'Out->' anywhere near it. Response-ness is decided by TYPE - a TShared*<FJsonObject> parameter
     - not by the name 'Out', and a handler inherits the top-level fields of the helpers it calls.

  3. THE BIND MUST BE THE WHOLE RIGHT-HAND SIDE. 'nd = M.call("get_node", ...).get("node", {})'
     binds the SUB-OBJECT - the correct form this tool argues for. [6 findings, all correct code]

  4. THE SUITE IS PARSED, NOT GREPPED. '[n.get("title") for n in ...]' reads a LOOP variable that
     happens to share a name with a response bound forty lines earlier, and
     'getter.get("nodeGuid") or getter.get("guid")' is a deliberate FALLBACK where reaching the
     second spelling means the first was absent. So this walks the ast: a name bound by a for-target
     or a comprehension is dropped from the map, a name bound to two different endpoints is dropped,
     and a read sitting in an `or` beside another read of the same object is not a finding.
     [16 findings, all correct code]

  5. SOME HANDLERS CANNOT BE READ STATICALLY AT ALL, and the honest move is to say so rather than
     guess. ui_scenario_status builds its answer in a helper and then SPLATS it -
     'for (Field : Status->Values) Out->SetField(Field.Key, Field.Value)' - so active and state are
     top-level under a RUNTIME key, with no TEXT("active") near Out anywhere. A handler that writes
     a field under a non-literal key is declared UNANALYSABLE and skipped. [3 findings, all correct]

Together those took a first run of 39 findings - every single one false - to what is reported now.

BOTH HALVES ARE COVERED, and the Blender half is the one that mattered: two of the three motivating
bugs were Blender ones, and the first version of this tool skipped every Blender suite. The addon is
python, so it is parsed the same way - the OPS dict in each ops_* module is the authoritative
endpoint -> function map, and the server does out.update(result), so an op's returned keys ARE the
response's top level. Getting that right cost two more corrections, both of the same shape as the
bug being hunted - a key that is really there, read from the wrong place:

  * KEYS ADDED AFTER THE LITERAL. op_create_primitive returns {"object": ..., "created": ...,
    "kind": ...} and then does out["name"] = obj.name and out["verts"] = ... Reading only the dict
    literal missed three TOP-LEVEL keys and produced seven confident findings against a correct
    suite, all saying "name is nested" when the source says the opposite two lines down and explains
    why. Subscript assignment, .update({...}) and .setdefault() are all tracked now.
  * .update(helper(obj)) IS FOLLOWED, NOT GIVEN UP ON. object_info does info.update(mesh_counts(obj))
    - which is exactly how its counts come to be nested, the original bug. Treating that as
    unreadable made object_info opaque, which silently emptied create_primitive's nested map and
    stopped the entire Blender path from firing. It reported CLEAN, and clean was wrong.

That second one is worth the space: the tool went green because it had stopped looking, which is the
failure mode it was written to catch, in itself. Only the mutation test caught it - the plant did not
fire, and a plant that does not fire is the finding.

DELIBERATELY NOT FLAGGED: a field the endpoint writes both ways, since the depth is then a per-call
question no static read can settle.

Usage:
    python tools/audit_nested_field_reads.py                   # findings
    python tools/audit_nested_field_reads.py --list ENDPOINT   # what one endpoint puts where

Exit codes:  0 clean   1 at least one top-level read of a nested-only field
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CPP = os.path.join(ROOT, "Source", "MifBridge", "Private")

# A function definition, captured with its parameter list so the response params can be typed.
FUNC = re.compile(r"^[ \t]*(?:[A-Za-z_][\w:<>,&*\s]*?[\s&*])?(\w+)\s*\(([^;{)]*)\)\s*(?:const\s*)?\{",
                  re.M)
# A response object is one by TYPE, not by name. Both spellings appear: handlers take TSharedRef,
# helpers that tolerate a null sink take TSharedPtr.
RESP_PARAM = re.compile(r"TShared(?:Ref|Ptr)\s*<\s*FJsonObject\s*>\s*&?\s*(\w+)")
EMIT = re.compile(r"\b(\w+)\s*->\s*Set\w*Field\s*\(\s*TEXT\(\"(\w+)\"\)")
CALLS = re.compile(r"\b([A-Z]\w+)\s*\(")
# A field written under a RUNTIME key - see point 5 above. Nothing static can follow it.
DYNAMIC_SET = re.compile(r"\b(\w+)\s*->\s*SetField\s*\(\s*(?!TEXT\()")

# A suite that talks to Blender, identified by what it IMPORTS rather than by its filename -
# blender_audit_common is the only way to reach 127.0.0.1:8792 from here, and a name-prefix rule
# would miss a suite that tests both halves.
BLENDER_HELPER = "blender_audit_common"


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def body_of(text, brace_pos):
    depth, i = 0, brace_pos
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_pos:i + 1]
        i += 1
    return text[brace_pos:]


def scan_cpp():
    """name -> {'top': set, 'nested': {field: 'file:line'}, 'calls': set, 'opaque': bool}"""
    funcs = {}
    for fname in sorted(os.listdir(CPP)):
        if not fname.endswith(".cpp"):
            continue
        text = read(os.path.join(CPP, fname))
        for m in FUNC.finditer(text):
            name, params = m.group(1), m.group(2)
            if name in ("if", "for", "while", "switch", "catch", "return"):
                continue
            resp = set(RESP_PARAM.findall(params))
            body = body_of(text, m.end() - 1)
            # Counted to the OPENING BRACE, not to the match start. A signature that wraps over
            # three lines - which the house style does often - otherwise reports every field in the
            # function three lines early, and a detector that cites the wrong line is a detector
            # people stop trusting.
            base_line = text.count("\n", 0, m.end() - 1) + 1
            rec = funcs.setdefault(name, {"top": set(), "nested": {}, "calls": set(),
                                          "opaque": False})
            for offset, line in enumerate(body.splitlines()):
                for recvr, field in EMIT.findall(line):
                    if recvr in resp:
                        rec["top"].add(field)
                    else:
                        rec["nested"].setdefault(field, "%s:%d" % (fname, base_line + offset))
            rec["calls"].update(CALLS.findall(body))
            for recvr in DYNAMIC_SET.findall(body):
                if recvr in resp:
                    rec["opaque"] = True
    return funcs


def top_fields(funcs, name, seen=None):
    """A handler's top-level fields, plus those of every helper it hands the response to."""
    seen = seen if seen is not None else set()
    if name in seen or name not in funcs:
        return set()
    seen.add(name)
    out = set(funcs[name]["top"])
    for callee in funcs[name]["calls"]:
        if callee in funcs and callee != name:
            out |= top_fields(funcs, callee, seen)
    return out


def nested_fields(funcs, name, seen=None):
    seen = seen if seen is not None else set()
    if name in seen or name not in funcs:
        return {}
    seen.add(name)
    out = dict(funcs[name]["nested"])
    for callee in funcs[name]["calls"]:
        if callee in funcs and callee != name:
            for k, v in nested_fields(funcs, callee, seen).items():
                out.setdefault(k, v)
    return out


def is_opaque(funcs, name, seen=None):
    """Opaque anywhere in the call tree means the response shape is not statically knowable."""
    seen = seen if seen is not None else set()
    if name in seen or name not in funcs:
        return False
    seen.add(name)
    if funcs[name]["opaque"]:
        return True
    return any(is_opaque(funcs, c, seen) for c in funcs[name]["calls"]
               if c in funcs and c != name)


ADDON = os.path.join(HERE, "blender-addon", "MifBlender")


def _dict_keys(node):
    """The string keys of a dict literal, or None when it is not one this tool can read."""
    if not isinstance(node, ast.Dict):
        return None
    keys = set()
    for k in node.keys:
        if k is None:                      # {**other} - the other dict's keys, unknowable here
            return None
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.add(k.value)
        else:
            return None                    # a computed key: same class as the C++ splat
    return keys


def _returned_shape(fn, by_name, seen=None):
    """(top-level keys, {key: helper that supplies its contents}) for one addon function.

    Returns (None, None) when the shape cannot be read - a computed key, a **spread, or a dict
    assembled somewhere this cannot follow. Opaque is an answer; a guess is not.
    """
    seen = seen if seen is not None else set()
    if fn is None or id(fn) in seen:
        return None, None
    seen.add(id(fn))
    top, sources = set(), {}
    # `d = {...}` then `return d` is as common here as returning the literal, so locals that hold a
    # dict literal are tracked. Anything else assigned to that name gives up on it.
    #
    # AND SO ARE KEYS ADDED AFTERWARDS. op_create_primitive builds {"object": ..., "created": ...,
    # "kind": ...} and then does out["name"] = obj.name, out["verts"] = ..., and a conditional
    # out["nameNote"] = ... - three TOP-LEVEL keys that a literal-only read cannot see. Missing them
    # produced seven confident findings against a correct suite, all of the form "name is nested"
    # when the source says the opposite two lines down and explains why ("the name is the identity
    # every op reports, not just a geometry fact"). A computed key gives up on the function, the
    # same way the C++ side treats a runtime SetField.
    locals_, extra, poisoned = {}, {}, set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    locals_[tgt.id] = node.value
                elif isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                    sl = tgt.slice
                    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                        extra.setdefault(tgt.value.id, set()).add(sl.value)
                    else:
                        poisoned.add(tgt.value.id)
        # d.update({...}) and d.setdefault("k", ...) add keys just as surely as a subscript does.
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name):
            owner = node.func.value.id
            if node.func.attr == "update" and node.args:
                more = _dict_keys(node.args[0])
                if more is None:
                    # `info.update(mesh_counts(obj))` - an update from ANOTHER addon function, which
                    # is followed rather than given up on. It is how object_info acquires its counts,
                    # and giving up here made object_info unreadable, which silently emptied
                    # create_primitive's nested map and stopped the whole Blender path from firing.
                    arg = node.args[0]
                    sub = None
                    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                        helper = by_name.get(arg.func.id)
                        if helper is not None and helper is not fn:
                            sub, _ = _returned_shape(helper, by_name, seen)
                    if sub:
                        extra.setdefault(owner, set()).update(sub)
                    else:
                        poisoned.add(owner)
                else:
                    extra.setdefault(owner, set()).update(more)
            elif node.func.attr == "setdefault" and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    extra.setdefault(owner, set()).add(a.value)
                else:
                    poisoned.add(owner)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        val, added = node.value, set()
        if isinstance(val, ast.Name):
            if val.id in poisoned:
                return None, None
            added = extra.get(val.id, set())
            val = locals_.get(val.id)
        keys = _dict_keys(val)
        if keys is None:
            return None, None
        top |= keys | added
        # A value that is a call to another addon function, or a comprehension over one, means that
        # function's own keys live UNDER this key. `{"object": object_info(x)}` is the shape that
        # started all of this.
        for k, v in zip(val.keys, val.values):
            name = k.value if isinstance(k, ast.Constant) else None
            if not isinstance(name, str):
                continue
            call = v
            if isinstance(v, (ast.ListComp, ast.GeneratorExp)):
                call = v.elt
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                helper = by_name.get(call.func.id)
                if helper is not None and helper is not fn:
                    sub, _ = _returned_shape(helper, by_name, seen)
                    if sub:
                        sources[name] = (call.func.id, sub)
    return top, sources


def scan_addon():
    """endpoint -> {'top': set, 'nested': {key: 'file:func'}, 'opaque': bool}"""
    if not os.path.isdir(ADDON):
        return {}
    by_name, endpoints, where = {}, {}, {}
    for fname in sorted(os.listdir(ADDON)):
        if not fname.endswith(".py"):
            continue
        try:
            tree = ast.parse(read(os.path.join(ADDON, fname)))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                by_name[node.name] = node
                where[node.name] = fname
            # The OPS dict is the authoritative endpoint -> function map. Guessing 'op_' + name
            # would be close and occasionally wrong, and this file is right there.
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "OPS" and isinstance(node.value, ast.Dict):
                        for k, v in zip(node.value.keys, node.value.values):
                            if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                                    and isinstance(v, ast.Name)):
                                endpoints[k.value] = v.id
    out = {}
    for ep, fname in endpoints.items():
        top, sources = _returned_shape(by_name.get(fname), by_name)
        if top is None:
            out[ep] = {"top": set(), "nested": {}, "opaque": True}
            continue
        nested = {}
        for key, (helper, subkeys) in (sources or {}).items():
            for sk in subkeys:
                nested.setdefault(sk, "%s %s() under '%s'" % (where.get(helper, "?"), helper, key))
        out[ep] = {"top": top, "nested": nested, "opaque": False}
    return out


def _is_bridge_call(node):
    """`M.call("ep", ...)` / `SC.confirm_call("ep", ...)` - returns the endpoint, or None."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in ("call", "confirm_call") or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _get_read(node):
    """`x.get("field")` where x is a bare name - returns (name, field), or None."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "get" or not node.args:
        return None
    if not isinstance(node.func.value, ast.Name):
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return (node.func.value.id, arg.value)
    return None


def responses_in(tree):
    """name -> endpoint, for names that hold a WHOLE response and are never shadowed."""
    bound, poisoned = {}, set()
    for node in ast.walk(tree):
        # A for-target or comprehension target means the name is not the response HERE, and this
        # tool cannot tell where "here" ends - so the name is given up on entirely.
        if isinstance(node, (ast.For, ast.AsyncFor)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    poisoned.add(sub.id)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    poisoned.add(sub.id)
        elif isinstance(node, ast.Assign):
            ep = _is_bridge_call(node.value)
            for tgt in node.targets:
                if not isinstance(tgt, ast.Name):
                    continue
                if ep is None:
                    poisoned.add(tgt.id)          # rebound to something that is not a response
                elif bound.get(tgt.id, ep) != ep:
                    poisoned.add(tgt.id)          # two endpoints, one name
                else:
                    bound[tgt.id] = ep
    return {k: v for k, v in bound.items() if k not in poisoned}


def or_excused(tree):
    """A read beside another read of the same object inside an `or` is a fallback, not a bug."""
    excused = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        reads = [(sub, _get_read(sub)) for sub in ast.walk(node)]
        reads = [(sub, r) for sub, r in reads if r]
        for sub, r in reads:
            if sum(1 for _, other in reads if other[0] == r[0]) > 1:
                excused.add(id(sub))
    return excused


def main():
    funcs = scan_cpp()
    bl_ops = scan_addon()

    if "--list" in sys.argv:
        ep = sys.argv[sys.argv.index("--list") + 1]
        handler = "H_" + ep
        if handler not in funcs:
            print("no handler named %s" % handler)
            return 1
        top = top_fields(funcs, handler)
        nest = nested_fields(funcs, handler)
        print("%s%s" % (ep, "   [UNANALYSABLE - writes a field under a runtime key]"
                            if is_opaque(funcs, handler) else ""))
        print("  top level (%d): %s" % (len(top), ", ".join(sorted(top)) or "-"))
        only = sorted(set(nest) - top)
        print("  nested only (%d): %s" % (len(only), ", ".join(only) or "-"))
        return 0

    print("=" * 78)
    print("NESTED-FIELD READS - a field read off a response that its handler only ever nests")
    print("=" * 78)
    handlers = [k for k in funcs if k.startswith("H_")]
    print("%d handlers parsed from %s" % (len(handlers), os.path.relpath(CPP, ROOT)))
    bl_opaque = sum(1 for v in bl_ops.values() if v["opaque"])
    print("%d Blender ops parsed from the addon (%d unreadable, reported not guessed)"
          % (len(bl_ops), bl_opaque))

    findings, skipped, unknown_ep, opaque = [], [], set(), set()
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py") or name == os.path.basename(__file__):
            continue
        text = read(os.path.join(HERE, name))
        is_blender = BLENDER_HELPER in text
        if is_blender and not bl_ops:
            skipped.append(name)
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        bound = responses_in(tree)
        if not bound:
            continue
        excused = or_excused(tree)
        lines = text.splitlines()
        for node in ast.walk(tree):
            got = _get_read(node)
            if not got or id(node) in excused:
                continue
            var, field = got
            ep = bound.get(var)
            if not ep:
                continue
            if is_blender:
                rec = bl_ops.get(ep)
                if rec is None:
                    unknown_ep.add(ep)
                    continue
                if rec["opaque"]:
                    opaque.add(ep)
                    continue
                top, nest = rec["top"], rec["nested"]
            else:
                handler = "H_" + ep
                if handler not in funcs:
                    unknown_ep.add(ep)
                    continue
                if is_opaque(funcs, handler):
                    opaque.add(ep)
                    continue
                top = top_fields(funcs, handler)
                nest = nested_fields(funcs, handler)
            if field in nest and field not in top:
                ln = getattr(node, "lineno", 0)
                snippet = lines[ln - 1].strip()[:100] if 0 < ln <= len(lines) else ""
                findings.append((name, ln, var, ep, field, nest[field], snippet))
    findings.sort()

    if skipped:
        print("skipped %d Blender suite(s) - the addon could not be parsed." % len(skipped))
    if opaque:
        print("%d endpoint(s) write a field under a RUNTIME key and cannot be read statically: %s"
              % (len(opaque), ", ".join(sorted(opaque))))
    if unknown_ep:
        print("%d endpoint(s) called by a suite have no H_ handler here (foreign or aliased): %s"
              % (len(unknown_ep), ", ".join(sorted(unknown_ep)[:6])))
    print()

    if not findings:
        print("CLEAN - every field a suite reads off a response is one that response carries at "
              "the top level.")
        return 0

    print("%d top-level read(s) of a field the handler only ever nests:" % len(findings))
    for name, ln, var, ep, field, where, snippet in findings:
        print("\n  %s:%d" % (name, ln))
        print("      %s came from %s, and %s writes \"%s\" only into a sub-object (%s)"
              % (var, ep, ep, field, where))
        print("      %s" % snippet)
        print("      This returns None whether or not the value exists, and a check comparing it")
        print("      against None PASSES. Read it through the sub-object instead.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
