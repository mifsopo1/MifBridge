"""Which Blender ENTITY TYPES can a caller consume but not create? The half audit_family_asymmetry
could not port.

WHY THIS SHAPE AND NOT A NAMING HEURISTIC. audit_family_asymmetry finds writers with no reader, and
its mirror - consumers with no creator - is the direction that actually paid on the addon: four
times in two days, every one found by hand. Nothing could create a COLLECTION though set_light_linking
required one; nothing could create an EMPTY, a CURVE or an ARMATURE though add_constraint, aim_object
and twelve rigging ops needed them; nothing could create a VERTEX GROUP or a SHAPE KEY though five
ops consumed them.

Two attempts to port it failed and were reverted, both keyed on PARAMETER NAMES: 13 candidates then
161, nearly all false, because `collection` and `color` are indistinguishable by spelling. A parameter
name does not carry "this is a datablock". The spec item says what would:

  a parameter the op resolves through a bpy collection IS naming an entity; one it reads as a float
  is not.

So this reads the SOURCE, not the names:

  CONSUMER  a caller-supplied value used as a KEY into a bpy collection - `X.get(v)` or `X[v]`,
            where v traces back to take(params, ...) / params[...] / params.get(...).
  CREATOR   a call that ADDS to that same collection - `X.new(...)`, `X.add(...)`, `X.load(...)`,
            or a bpy.ops.*_add() operator whose name matches.

The collection is identified by its ATTRIBUTE NAME - `objects`, `vertex_groups`, `key_blocks`,
`node_groups` - so it works identically for bpy.data collections and for the per-object ones like
obj.vertex_groups, which is where two of the four hand-found cases lived.

WHAT IT DOES NOT CLAIM. A collection with a creator somewhere is not proof the creator is REACHABLE
for the case a consumer needs - create_object makes an object, but "can anything make an object of
type CURVE" is a question about arguments, not about collections. That was two of the four. This
tool finds the coarser class and says so rather than implying it found them all; see the REACH block
it prints.
"""
import ast
import glob
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon", "MifBlender")
BASELINE = os.path.join(HERE, "audit_blender_consumer_no_creator_baseline.txt")

# `params` is a plain dict; params.get("x") is not a collection lookup. Same for the response dict
# an op is building. Excluding by RECEIVER NAME rather than by type, which is not available here.
# NOT `data`. It was in this set as a response-dict name, and in a Blender addon `data` is almost
# always obj.data - the mesh or armature datablock. Excluding it made `ebs = data.edit_bones`
# invisible, so both bone creators disappeared and the tool reported bones as uncreatable.
NOT_COLLECTIONS = {"params", "out", "result", "res", "info", "kwargs", "d", "row", "payload"}
CREATE_METHODS = {"new", "add", "load", "append", "link"}
# bmesh layer accessor -> the datablock collection that creates the same entity. See collection_name.
BMESH_LAYER_ALIASES = {"uv": "uv_layers", "color": "color_attributes", "deform": "vertex_groups"}
# CREATED THROUGH ONE COLLECTION, READ THROUGH ANOTHER - Blender's duality, not this codebase's.
# A bone is made in EDIT mode through armature.edit_bones and read afterwards from armature.bones;
# they are the same entity and only one of them can create.
EQUIVALENT = {"edit_bones": "bones"}
# A modifier that IS an entity elsewhere. obj.modifiers.new(type="PARTICLE_SYSTEM") is how a
# particle system is made, and it shows up in obj.particle_systems.
MODIFIER_CREATES = {"PARTICLE_SYSTEM": "particle_systems"}
READERS = {"take", "take_bool", "take_float", "take_int"}


def own_nodes(fn):
    """Every node in this function's OWN body, not descending into nested functions.

    ast.walk descends into nested defs, and set_light_linking's get-or-create is a nested
    `_collection(name)`. So the outer op was credited with the helper's bpy.data.collections.new()
    while the matching .get() was invisible to it - its key is the helper's argument, not the
    outer's params. Result: `collections` had a creator that nothing could see consuming, and the
    ground-truth run reported zero for the exact defect this tool was built from. Each function is
    judged on its own body; the nested one is judged separately, as itself.
    """
    out = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def caller_derived(fn):
    """Local names whose value came from the caller's params, plus the nodes that ARE such reads."""
    names, nodes = set(), set()
    # A HELPER'S ARGUMENTS ARE THE CALLER'S VALUES, one call further down. set_light_linking resolves
    # its collection inside a helper that takes `name`, so nothing in that function mentions
    # `params` and the whole consumption was invisible - which is why the ground-truth run kept
    # reporting zero for the exact defect this was built from. An over-approximation on purpose:
    # for an op_* function the parameter is `params` and this adds nothing.
    argnames = [a.arg for a in fn.args.args]
    if argnames != ["params"]:
        names.update(a for a in argnames if a != "self")
    for node in own_nodes(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in READERS:
                nodes.add(id(node))
            elif (isinstance(f, ast.Attribute) and f.attr == "get"
                  and isinstance(f.value, ast.Name) and f.value.id == "params"):
                nodes.add(id(node))
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
              and node.value.id == "params"):
            nodes.add(id(node))
    # one pass is enough for this codebase's style: `name = take(params, "object")`
    for node in own_nodes(fn):
        if isinstance(node, ast.Assign) and id(node.value) in nodes:
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names, nodes


def _root(expr):
    while isinstance(expr, (ast.Attribute, ast.Subscript, ast.Call)):
        expr = expr.value if not isinstance(expr, ast.Call) else expr.func
    return expr.id if isinstance(expr, ast.Name) else None


def collection_name(expr):
    """The attribute name a collection expression ends in, or None if it is not one."""
    if not isinstance(expr, ast.Attribute):
        return None
    if isinstance(expr.value, ast.Name) and expr.value.id in NOT_COLLECTIONS:
        return None
    # bpy.ops.<namespace> is an OPERATOR namespace, not a collection. `bpy.ops.uv.unwrap()` made
    # "uv" look like an entity type nobody can create; there is no uv collection to create into.
    if isinstance(expr.value, ast.Attribute) and expr.value.attr == "ops" and _root(expr) == "bpy":
        return None
    # ONE ENTITY, TWO SPELLINGS, and it is Blender's duality rather than this codebase's. A UV layer
    # is created through the mesh datablock - `mesh.uv_layers.new(name=...)` - and read through the
    # bmesh accessor, `bm.loops.layers.uv.get(name)`. Keyed on the attribute alone those are two
    # collections, and the tool reported "uv can be consumed and never created" while
    # ops_mesh.py:1820 creates one. Anything under a `.layers` accessor is normalised to the
    # datablock collection it aliases.
    if isinstance(expr.value, ast.Attribute) and expr.value.attr == "layers":
        return BMESH_LAYER_ALIASES.get(expr.attr, expr.attr)
    # bl_rna.properties is RNA INTROSPECTION, not an entity collection - `node.bl_rna.properties`
    # enumerates a type's fields. There is nothing to create into it and it should never have been
    # a candidate.
    if isinstance(expr.value, ast.Attribute) and expr.value.attr == "bl_rna":
        return None
    return expr.attr


def scan(path):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    consumers, creators = {}, {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        op = fn.name[3:] if fn.name.startswith("op_") else fn.name
        names, nodes = caller_derived(fn)
        # LOCAL ALIASES FOR A COLLECTION. `ebs = data.edit_bones` then `ebs.new(...)` is how both
        # bone creators are written, and without this the tool reported "bones can be named and
        # created by nobody" while create_armature and add_bones both make them. Same class of miss
        # as the str() coercion: the analysis has to follow the value, not match the expression.
        alias = {}
        for st in own_nodes(fn):
            if isinstance(st, ast.Assign) and len(st.targets) == 1 \
                    and isinstance(st.targets[0], ast.Name):
                c = collection_name(st.value)
                if c:
                    alias[st.targets[0].id] = c

        def is_caller_value(k):
            # THROUGH THE COERCIONS. `bpy.data.collections.get(str(name))` is the addon's own house
            # style and the key is a Call, not a Name - so the first version answered False for it
            # and the ground-truth run reported zero, missing the collection gap entirely. str(),
            # int(), float() and .strip() do not stop a value being the caller's.
            for _ in range(4):
                if isinstance(k, ast.Call) and k.args:
                    f = k.func
                    if isinstance(f, ast.Name) and f.id in ("str", "int", "float"):
                        k = k.args[0]
                        continue
                if isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute) \
                        and k.func.attr in ("strip", "lower", "upper"):
                    k = k.func.value
                    continue
                break
            return (isinstance(k, ast.Name) and k.id in names) or id(k) in nodes

        for node in own_nodes(fn):
            # CONSUME: X.get(v) or X[v] with a caller-supplied key
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args
                    and is_caller_value(node.args[0])):
                c = collection_name(node.func.value)
                if c:
                    consumers.setdefault(c, set()).add(op)
            # A SUBSCRIPT IS NOT EVIDENCE OF A NAME. `obj.material_slots[i]` with a caller-supplied
            # value is an INDEX, and an index names nothing - it was the tool's only false positive
            # of that shape. `.get(name)` is name-based by construction, so that is the signal.
            # bpy.data.objects[name] would be missed; get_object below is how this addon spells it.
            # get_object() is the house resolver for bpy.data.objects
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "get_object" and node.args
                  and is_caller_value(node.args[0])):
                consumers.setdefault("objects", set()).add(op)
            # CREATE: X.new(...) / X.add(...) / X.load(...)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in CREATE_METHODS):
                c = collection_name(node.func.value)
                if c is None and isinstance(node.func.value, ast.Name):
                    c = alias.get(node.func.value.id)
                # A MODIFIER CREATES THE THING IT IS. `obj.modifiers.new(type="PARTICLE_SYSTEM")`
                # is the only way to make a particle system, and it lands in obj.particle_systems -
                # a third duality, after the bmesh layers and edit_bones. Read off the `type=`
                # keyword rather than assumed, so a modifier type with no entity of its own adds
                # nothing.
                if c == "modifiers":
                    for kw in node.keywords:
                        if kw.arg == "type" and isinstance(kw.value, ast.Constant):
                            c = MODIFIER_CREATES.get(kw.value.value, c)
                if c:
                    creators.setdefault(EQUIVALENT.get(c, c), set()).add(op)
    return consumers, creators


def analyse():
    consumers, creators = {}, {}
    files = sorted(glob.glob(os.path.join(ADDON, "*.py")))
    for p in files:
        c, m = scan(p)
        for k, v in c.items():
            consumers.setdefault(k, set()).update(v)
        for k, v in m.items():
            creators.setdefault(k, set()).update(v)
    # A CREATOR IS AN OP THAT EXISTS TO CREATE, not a `.new()` buried inside the consumer.
    #
    # THIS IS WHAT THE TOOL GOT WRONG AND THE GROUND TRUTH CAUGHT. Run at c867cb1^ - the commit
    # before create_collection was written - it reported ZERO, missing the very defect it was built
    # from. bpy.data.collections.new() was there, on ops_lightcam.py:1046, inside set_light_linking:
    #
    #     c = bpy.data.collections.get(str(name))
    #     if c is None:
    #         c = bpy.data.collections.new(str(name))
    #
    # That auto-create IS the broken state the spec item describes - "nothing could create a
    # collection though set_light_linking required one and could only reach its broken state". A
    # caller could not make a collection deliberately, name it, or find out it now existed.
    #
    # So an op that both consumes and creates the same collection does not count as its creator.
    # The get-or-create idiom is a consumer with a fallback, and reading it as a creator is what
    # made a real gap invisible.
    #
    # AND THE SEPARATOR IS THE OP'S PUBLIC NAME, because structure alone cannot tell the two apart.
    # create_collection also checks before it creates:
    #
    #     existing = bpy.data.collections.get(name)   # refuse a duplicate
    #     ...        bpy.data.collections.new(name)
    #
    # which is the SAME shape as set_light_linking's get-or-create and must reach the opposite
    # verdict. The difference is intent, and the only honest record of intent here is the name the
    # op is published under. That is not the heuristic the two reverted attempts died on: they
    # guessed a PARAMETER's type from its spelling, where this reads an op's own API name, and it
    # is applied only to rescue a creator - never to invent one. A private helper (leading
    # underscore) is never a creator on its own, which is what set_light_linking's _collection is.
    for coll, ops in list(creators.items()):
        real = set()
        for op in ops:
            if op in consumers.get(coll, set()):
                if op.startswith("_") or not op.split("_")[0] in ("create", "add", "new", "make"):
                    continue
            real.add(op)
        if real:
            creators[coll] = real
        else:
            del creators[coll]
    return consumers, creators, files


def main():
    check = "--check" in sys.argv
    if not os.path.isdir(ADDON):
        print("addon not found at %s - cannot judge anything" % ADDON)
        return 2
    consumers, creators, files = analyse()
    gaps = sorted(k for k in consumers if k not in creators)
    print("%-22s %-7s %s" % ("entity collection", "ops", "consumed by"))
    for k in gaps:
        ops = sorted(consumers[k])
        print("%-22s %-7d %s" % (k, len(ops), ", ".join(ops[:6]) + (" ..." if len(ops) > 6 else "")))
    print("")
    print("REACH: %d addon module(s), %d collection(s) reached by a caller-supplied key, %d of them"
          % (len(files), len(consumers), len(gaps)))
    print("       with no op that adds to the same collection.")
    print("       reached: %s" % ", ".join(sorted(consumers)))
    print("")
    print("WHAT THIS DOES NOT COVER, so the zero above is not read as more than it is: a collection")
    print("WITH a creator can still be uncreatable for the case a consumer needs - create_object")
    print("makes an object, and whether anything makes one of type CURVE is a question about")
    print("ARGUMENTS, not collections. Two of the four defects this pattern found by hand were of")
    print("that shape and this tool would not have found them.")
    if "--update-baseline" in sys.argv:
        with io.open(BASELINE, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("# Entity types a caller can NAME but not CREATE. Regenerate deliberately:\n")
            fh.write("#   python tools/audit_blender_consumer_no_creator.py --update-baseline\n")
            fh.write("# ACCEPTED AS KNOWN, NOT AS CORRECT. Each needs an addon op whose purpose is\n")
            fh.write("# creating one; until then a caller can only get one as a side effect of an\n")
            fh.write("# op that means to do something else, which is the shape set_light_linking\n")
            fh.write("# was fixed for.\n")
            for g in gaps:
                fh.write(g + "\n")
        print("")
        print("baseline updated: %d entry(ies)" % len(gaps))
        return 0
    # RATCHETED, not gated at zero. Two real gaps are open and each needs a new addon op plus a
    # Blender run to verify, so a gate at zero would be one nobody can turn green - which is a gate
    # people learn to skip. The known ones are accepted as KNOWN; a NEW entity type fails.
    known = set()
    if os.path.isfile(BASELINE):
        known = set(l.strip() for l in io.open(BASELINE, encoding="utf-8")
                    if l.strip() and not l.startswith("#"))
    fresh = [g for g in gaps if g not in known]
    stale = sorted(known - set(gaps))
    if stale:
        print("")
        print("FIXED since the baseline, remove from it: %s" % ", ".join(stale))
    if check and fresh:
        print("")
        print("FAIL: %d NEW entity type(s) can be named by a caller and created by nobody: %s"
              % (len(fresh), ", ".join(fresh)))
        print("      Either add an op that creates one, or accept it with --update-baseline.")
        return 1
    if check:
        print("")
        print("%d known gap(s), 0 new." % len(known))
    return 0


if __name__ == "__main__":
    sys.exit(main())
