"""Does any op convert a caller's number with a bare float() or int()?

WHY THIS IS A GATE. On 2026-09-04 a caller could send NaN, Infinity or 2**40 to almost anything.
Python's json module PARSES NaN and Infinity by default, float() accepts them, int() has no 32-bit
bound, and Blender takes all of it - so the failures were silent or ugly and never actionable:

  ray_cast{origin: [nan,0,0]}        ACCEPTED, answered "hit": false. A WRONG ANSWER shaped like
                                     data - the caller reads "nothing is there" and believes it.
  set_bone_pose{quaternion:[nan,..]} accepted a rotation that is not one, exactly like the zero
                                     quaternion the code already warned about.
  uv_unwrap{uvTransform:{scale:nan}} accepted, because isinstance(v, float) is TRUE for a NaN, and
                                     every UV coordinate it touched became nan.
  set_frame_range{start: 2**40}      raw ValueError from inside Blender, escaping the refusal
                                     contract where every other refusal is a sentence.

Eleven files were fixed by hand. This is what stops the twelfth: ops_common owns take_float,
take_int, finite_float, finite_floats and finite_int, and a conversion of caller input that does not
go through one of them is the defect.

WHAT COUNTS AS CALLER INPUT is the whole precision problem, and it is decided by what the argument
is, not by what it is called. float(kb.value) reads a number BACK from Blender and is always finite
by construction; float(params["x"]) is the caller talking. The rule is therefore: an argument that
reaches an attribute of a bpy object is a read-back, anything else derived from a parameter is not.

AND A GUARD DOES NOT HAVE TO BE ONE OF THE HELPERS. create_lattice converts with int(raw) and is
correct: it range-checks 1..64 on the line above, with a message explaining that Blender clamps
outside it. A comparison against the same name earlier in the function counts, which is why that op
does not appear below and did not need changing.
"""
import argparse
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

GUARDS = ("take_float", "take_int", "finite_float", "finite_floats", "finite_int")
# The helpers themselves convert with a bare float()/int() - that IS their implementation.
EXEMPT_FUNCTIONS = set(GUARDS) | {"jsonable", "rnd"}
# Names that mean "this came from the caller".
CALLER_HINTS = ("param", "val", "raw", "value", "arg", "given", "spec", "entry", "item")


# Names that hold something Blender gave us. A number read back off one of these is finite and in
# range by construction - it is Blender's own value, not the caller's.
_BPY_ROOTS = ("bpy", "obj", "data", "sc", "scene", "mesh", "kb", "key", "node", "mat",
              "cam", "light", "world", "st", "mod", "bg", "ies", "tree", "sock", "fc", "kp")


def _reads_bpy(node):
    """Does this expression pull a value back OUT of Blender rather than from the caller?"""
    for sub in ast.walk(node):
        # getattr(mod, attr) IS an attribute read, written the way an availability table has to
        # write one. _read_modifier_attr does exactly that and then int()s the result, which is a
        # value Blender handed back and cannot be NaN or out of range.
        if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "getattr" and sub.args:
            first = sub.args[0]
            if isinstance(first, ast.Name) and first.id in _BPY_ROOTS:
                return True
        if isinstance(sub, ast.Attribute):
            root = sub
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in _BPY_ROOTS:
                return True
    return False


def _from_bpy_read(fn, node):
    """Was every name in this expression last assigned from something bpy handed us?"""
    names = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
    if not names:
        return False
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign) and _reads_bpy(sub.value):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and tgt.id in names:
                    return True
        if isinstance(sub, (ast.For,)) and _reads_bpy(sub.iter):
            if isinstance(sub.target, ast.Name) and sub.target.id in names:
                return True
    return False


def _looks_like_caller(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and any(h in sub.id.lower() for h in CALLER_HINTS):
            return True
        if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) \
                and any(h in sub.value.id.lower() for h in CALLER_HINTS):
            return True
    return False


def _guarded_name(fn, node):
    """Is the converted value already bounded by a comparison earlier in this function?

    create_lattice's `int(raw)` follows `if raw < 1 or raw > 64: raise ...`. That is a real guard
    written before the helpers existed, and reporting it would be reporting correct code.
    """
    names = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
    if not names:
        return False
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Compare) or sub.lineno >= node.lineno:
            continue
        # `raw is None` IS A PRESENCE CHECK, NOT A BOUND. Every one of these parsers starts with
        # one, so counting it excused all of them - the second hole the mutation test found in this
        # rule, after len(). A guard has to constrain the VALUE.
        if any(isinstance(o, (ast.Is, ast.IsNot)) for o in sub.ops):
            continue
        if all(isinstance(c, ast.Constant) and c.value is None for c in sub.comparators):
            continue
        # A LENGTH CHECK IS NOT A BOUND ON THE VALUE. `len(raw) < 3` says the vector has three
        # components; it says nothing about whether any of them is NaN. Counting it excused
        # ops_query's _vec entirely - the parser whose NaN origin made ray_cast answer "no hit" -
        # and the mutation test caught it: removing that guard did not make this audit fire.
        for operand in [sub.left] + list(sub.comparators):
            if any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "len"
                   for c in ast.walk(operand)):
                continue
            if any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(operand)):
                return True
    return False


def scan():
    findings = []
    for fname in sorted(f for f in os.listdir(ADDON) if f.endswith(".py")):
        src = io.open(os.path.join(ADDON, fname), "rb").read().decode("utf-8")
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name in EXEMPT_FUNCTIONS:
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "id", "") in ("float", "int") and node.args):
                    continue
                arg = node.args[0]
                # A NAME ASSIGNED FROM A bpy READ IS STILL A READ-BACK. _material_json does
                # `val = sock.default_value` and then float(val) six lines later - looking only at
                # the call site sees a bare name matching a caller hint and reports correct code.
                if _reads_bpy(arg) or _from_bpy_read(fn, arg) or not _looks_like_caller(arg):
                    continue
                if _guarded_name(fn, node):
                    continue
                findings.append({"file": fname, "func": fn.name, "line": node.lineno,
                                 "kind": node.func.id,
                                 "expr": (ast.get_source_segment(src, arg) or "?")[:40]})
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on any finding")
    args = ap.parse_args()

    findings = scan()
    print("audit_unguarded_numbers: %d bare conversion(s) of caller input" % len(findings))
    for row in findings:
        print("")
        print("  %s :: %s" % (row["file"], row["func"]))
        print("    line %-5d %s(%s)" % (row["line"], row["kind"], row["expr"]))
    if findings:
        print("")
        print("Use one of %s. A bare float() accepts NaN and" % ", ".join(GUARDS))
        print("Infinity, which Blender stores and then reads back as nan; a bare int() has no")
        print("32-bit bound, and Blender raises a ValueError from inside the assignment rather")
        print("than this addon refusing with a sentence.")
    return 1 if (args.check and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
