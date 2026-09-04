"""Does every op that WRITES A FILE check its output path before doing the work?

WHY THIS IS A GATE AND NOT A NOTE. On 2026-09-04 an untracked file called ".exr" - the extension
alone, no name - turned up in the repo root during an unrelated commit. render_still had been writing
it on every run of the version matrix. The op took a filePath containing a NUL, rendered the frame,
and only then failed to save; with a format Blender can write to a relative path it did not fail at
all and silently wrote into the process's working directory.

Sweeping the rest found the same shape in three more: bake_texture ran the whole bake before
RuntimeError came out of image.save_render, export_mesh ran the FBX export first, export_scene caught
its own failure and pasted the traceback into the message. All four did the expensive part before
discovering they could not save, and all four answered with a bare exception in an addon where every
other refusal is a sentence.

Each was fixed by calling ops_common.check_output_path FIRST. This is the check that the next op to
write a file does the same - the alternative is remembering, and this file exists because remembering
is what failed.

WHAT IT LOOKS FOR. An op that resolves a caller-supplied path (bpy.path.abspath / os.path.abspath on
something taken from params) AND then performs a write - bpy.ops.export_*, bpy.ops.wm.save_*,
image.save*, a render with write_still - must call check_output_path somewhere above that write.

WHAT IT DELIBERATELY IGNORES. Ops that only READ a path (import_mesh, import_scene, open_file,
file_info, set_material_texture): a path that cannot be opened fails at the open, before any work,
which is the behaviour this whole check is trying to produce. Refusing them for not calling an
output-path check would be noise, and an audit that fires on correct code is one people learn to
silence.
"""
import argparse
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

# Calls that put bytes on disk at a caller-chosen path.
WRITE_MARKERS = (
    "save_render", "save_as_render", "save_as_mainfile", "save_mainfile", "save",
    "export_scene", "export_mesh", "export_anim", "write_still",
)
# ...and the operator namespaces that do it, matched on the dotted call.
WRITE_PREFIXES = ("bpy.ops.export_scene.", "bpy.ops.export_mesh.", "bpy.ops.export_anim.",
                  "bpy.ops.wm.save_")

PATH_PARAMS = ("filepath", "filePath", "file", "path", "output", "outputPath")
GUARD = "check_output_path"


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _takes_a_path(fn, src):
    """Does this op read a path-shaped parameter from the caller?"""
    seg = ast.get_source_segment(src, fn) or ""
    return any('"%s"' % p in seg for p in PATH_PARAMS)


def _writes_a_file(fn):
    """Every node where this op puts bytes on disk, as (lineno, what)."""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func) or ""
        attr = getattr(node.func, "attr", "") or ""
        # THE EXPORTERS ARE CALLED DYNAMICALLY. export_scene resolves its operator by name -
        # `getattr(getattr(bpy.ops, mod), opname)(**kwargs)` - because which exporter exists varies
        # by build, so there is no dotted bpy.ops.export_scene.gltf to match. A detector that only
        # knew the static form saw two of the five writers and would have reported the rest clean.
        if isinstance(node.func, ast.Call):
            inner = ast.dump(node.func)
            if "bpy" in inner and "ops" in inner and "getattr" in inner:
                out.append((node.lineno, "bpy.ops.<resolved at runtime>"))
                continue
        if any(dotted.startswith(p) for p in WRITE_PREFIXES):
            out.append((node.lineno, dotted))
        elif attr in WRITE_MARKERS:
            out.append((node.lineno, attr + "()"))
        else:
            for kw in node.keywords or ():
                if kw.arg == "write_still":
                    out.append((node.lineno, "render(write_still=...)"))
    return sorted(out)


def _guard_line(fn):
    """Line of the first check_output_path call, or None."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == GUARD:
            return node.lineno
    return None


def scan():
    findings, covered = [], []
    for fname in sorted(f for f in os.listdir(ADDON)
                        if f.startswith("ops_") and f.endswith(".py")):
        src = io.open(os.path.join(ADDON, fname), "rb").read().decode("utf-8")
        tree = ast.parse(src)
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("op_"):
                continue
            # READERS ARE NOT WRITERS, and the dynamic-operator match cannot tell them apart:
            # import_scene resolves its importer exactly the way export_scene resolves its
            # exporter. A path it cannot open fails at the open, before any work, which is the
            # behaviour this audit exists to produce - so naming them here would be firing on
            # correct code, and an audit that does that is one people learn to silence.
            if fn.name.startswith(("op_import_", "op_open_", "op_load_")):
                continue
            writes = _writes_a_file(fn)
            if not writes or not _takes_a_path(fn, src):
                continue
            guard = _guard_line(fn)
            first_write = writes[0][0]
            if guard is None:
                findings.append({"file": fname, "op": fn.name, "line": first_write,
                                 "what": writes[0][1], "why": "no check_output_path at all"})
            elif guard > first_write:
                findings.append({"file": fname, "op": fn.name, "line": first_write,
                                 "what": writes[0][1],
                                 "why": "checked at line %d, AFTER the write" % guard})
            else:
                covered.append((fname, fn.name))
    return findings, covered


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on any finding")
    ap.add_argument("--list", action="store_true", help="show the ops that are covered")
    args = ap.parse_args()

    findings, covered = scan()
    print("audit_output_paths: %d op(s) write a caller-named file, %d guarded, %d NOT"
          % (len(findings) + len(covered), len(covered), len(findings)))
    if args.list:
        for fname, op in covered:
            print("  ok   %-22s %s" % (fname, op))
    for row in findings:
        print("")
        print("  %s :: %s" % (row["file"], row["op"]))
        print("    line %-5d writes via %s" % (row["line"], row["what"]))
        print("    %s" % row["why"])
    if findings:
        print("")
        print("Call ops_common.check_output_path(raw, resolved, verb) BEFORE the work. An op that")
        print("renders or bakes first and then cannot save has spent the time, broken its own")
        print("refusal contract, and on some formats written a file named after the extension")
        print("alone into whatever directory the process was launched from.")
    return 1 if (args.check and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
