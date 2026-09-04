"""Does every op that creates a NAMED datablock tell the caller what name it actually got?

WHY THIS IS A GATE. A bridge call can time out with the work already done - that is the whole reason
render_animation runs out of process - and the caller's only option is to retry. Blender renames on
collision rather than failing, so the second call makes "Foo.001" and returns it. On 2026-09-04,
measured by calling every create op twice with the same name, EIGHT ops returned that name with
nothing to say it was not the one requested: create_empty, create_text, create_curve, create_lattice,
create_armature, create_light, create_camera and create_node_group.

Where it bites is the call after. assign_node_group by the name it asked for finds the WRONG group,
or none - and every field in the create response agreed with the caller's belief.

THE ADDON ALREADY KNEW, four times over, which is what makes this a gate rather than a fix.
create_collection REFUSES a clash; create_primitive, create_texture, create_material and
create_action report requestedName. The pattern was chosen and then not applied to the next eight ops
written, because nothing checked. This is the check.

TWO ACCEPTABLE ANSWERS, and it takes either:
  REFUSE the clash outright, the way create_collection does. A caller that gets an error knows.
  REPORT it - requestedName plus a flag saying whether Blender changed it.

THREE SPELLINGS ARE ACCEPTED and that is not an endorsement. nameWasSuffixed (10 sites),
nameWasAdjusted (1) and nameWasTaken (1) are the same boolean under three names; a caller testing
the common one silently misses the other two. Accepting all three here keeps this audit about the
question it asks - is the caller told - rather than making it the vehicle for a response-field rename
that is a compatibility decision. See the open spec item.
"""
import argparse
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")

# bpy.data collections whose .new() takes a caller-chosen name and uniquifies it.
NAMED_COLLECTIONS = (
    "objects", "meshes", "curves", "armatures", "lattices", "lights", "cameras",
    "materials", "node_groups", "actions", "collections", "images", "textures",
    "worlds", "speakers", "texts", "particles",
)
FLAGS = ("nameWasSuffixed", "nameWasAdjusted", "nameWasTaken")
REQUESTED = "requestedName"
# The shared postcondition helper in ops_create: it reports for its five callers.
REPORTING_HELPERS = ("_created",)


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _creates_named(fn):
    """The bpy.data.<coll>.new(...) calls in this op that take a name, as (lineno, collection)."""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or getattr(node.func, "attr", "") != "new":
            continue
        dotted = _dotted(node.func) or ""
        if not dotted.startswith("bpy.data."):
            continue
        coll = dotted.split(".")[2] if dotted.count(".") >= 2 else ""
        if coll not in NAMED_COLLECTIONS:
            continue
        # A name has to actually be passed - `nodes.new("ShaderNodeMath")` names a TYPE, not an
        # object, and bpy.data.<coll>.new() with no argument cannot clash with anything.
        given = None
        if node.args:
            given = node.args[0]
        else:
            given = next((kw.value for kw in node.keywords or () if kw.arg == "name"), None)
        if given is None:
            continue
        # A STRING LITERAL IS NOT THE CALLER'S NAME. set_compositing builds
        # bpy.data.node_groups.new("Compositing", ...) with a fixed name it chose itself - there is
        # no requested name to report and nothing for the caller to be misled about.
        if isinstance(given, ast.Constant) and isinstance(given.value, str):
            continue
        # A NAME BUILT FROM A FORMAT IS NOT THE CALLER'S EITHER. set_light_ies makes its text block
        # as "%s_IES" % obj.name - the caller never chose that string, so a clash on it is the
        # addon's business rather than something the caller can be misled about.
        if isinstance(given, (ast.BinOp, ast.JoinedStr)) and any(
                isinstance(s, ast.Constant) and isinstance(s.value, str)
                for s in ast.walk(given)):
            continue
        out.append((node.lineno, coll))
    return out


def _reports(fn, src):
    """Does this op tell the caller the name it got - by reporting, refusing, or delegating?"""
    seg = ast.get_source_segment(src, fn) or ""
    if REQUESTED in seg and any(f in seg for f in FLAGS):
        return "reports"
    # requestedName ALONE is still an answer: the caller has both strings and can compare them.
    # create_material is the one op that does this, and it is why the "one boolean, three
    # spellings" spec item calls it half-reported rather than unreported.
    if REQUESTED in seg:
        return "reports requestedName only"
    # bake_texture answers with the NEW NAME rather than a boolean - "imageRenamed": image.name -
    # which tells the caller strictly more. A fifth spelling of the same idea, accepted here for the
    # same reason the other three are: this audit asks whether the caller is told, not what the
    # field is called. The naming is a separate, filed, compatibility question.
    if any(k in seg for k in ("Renamed\"", "Renamed'")):
        return "reports the new name"
    for helper in REPORTING_HELPERS:
        if helper + "(" in seg:
            return "via %s" % helper
    # create_collection's answer: refuse the clash rather than describe it.
    for node in ast.walk(fn):
        if not isinstance(node, ast.Raise):
            continue
        text = " ".join(s.value for s in ast.walk(node)
                        if isinstance(s, ast.Constant) and isinstance(s.value, str))
        low = text.lower()
        # set_material_slots words it "a name clash was silently uniquified" rather than "already
        # exists", and refuses just as hard. Matching one phrasing would have called it a finding.
        if "already exist" in low or "name clash" in low or "uniquif" in low:
            return "refuses the clash"

    # GET-OR-CREATE CANNOT CLASH. set_light_linking does bpy.data.collections.get(name) and only
    # calls .new() when there is nothing there, so a second call REUSES the first collection and
    # the caller's name always resolves. A third correct answer, and the quietest one.
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get":
            dotted = _dotted(node.func) or ""
            if dotted.startswith("bpy.data."):
                return "get-or-create"
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
            creations = _creates_named(fn)
            if not creations:
                continue
            how = _reports(fn, src)
            if how:
                covered.append((fname, fn.name, how))
            else:
                findings.append({"file": fname, "op": fn.name,
                                 "line": creations[0][0], "coll": creations[0][1]})
    return findings, covered


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on any finding")
    ap.add_argument("--list", action="store_true", help="show how each covered op answers")
    args = ap.parse_args()

    findings, covered = scan()
    print("audit_created_name_reported: %d op(s) create a named datablock, %d answer, %d do NOT"
          % (len(findings) + len(covered), len(covered), len(findings)))
    if args.list:
        for fname, op, how in covered:
            print("  ok   %-22s %-26s %s" % (fname, op, how))
    for row in findings:
        print("")
        print("  %s :: %s" % (row["file"], row["op"]))
        print("    line %-5d bpy.data.%s.new(<caller's name>)" % (row["line"], row["coll"]))
        print("    returns the name it got and never says whether that is the one asked for")
    if findings:
        print("")
        print("Either refuse the clash, as create_collection does, or report requestedName with one")
        print("of %s. A caller retrying a timed-out create otherwise" % ", ".join(FLAGS))
        print("holds a name that belongs to a different object, and finds out on the NEXT call.")
    return 1 if (args.check and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
