"""Does an MCP wrapper send, by default, a key its endpoint REFUSES for being present?

WHY THIS EXISTS. On 2026-09-03 nine endpoints gained guards that refuse a parameter the chosen mode
would ignore - `radius` on a line trace, `nodeGuid` on `blueprint_watch op:list`. They were checked
against the SUITES, which build explicit payloads and send only what they mean. The MCP wrappers do
not work that way: `_post` drops None and sends everything else, so any parameter with a non-None
default goes out on EVERY call. Five tools broke the moment the guards landed:

    trace(start, end)            posted radius:50.0 and halfHeight:100.0 with shape defaulting to
                                 "line" - refused. Every default trace through the MCP.
    draw_debug(center=...)       posted radius:100.0 with shape defaulting to "point" - refused.
    blueprint_watch(op="list")   posted nodeGuid:"" and pin:"" - an empty string is still a PRESENT
                                 field, and the guard tests HasField.
    blueprint_breakpoint(...)    same.
    list_sublevels()             posted netMode:"server" with world:"editor" - refused.

NOTHING ELSE CAN SEE THIS. parity_check asks whether a wrapper sends a key the endpoint REJECTS BY
NAME, from the RejectUnknownParams accept-list. These are runtime refusals of ACCEPTED keys, which
is a different question and structurally invisible to it. The suites pass because they are not the
wrapper. It was found by reading docstrings, which is not a process.

THE MAP BELOW IS HAND-MAINTAINED, AND THAT IS THE WEAKNESS - said here rather than discovered.
Three attempts to derive it from the handlers were measured and thrown away the same hour:

  * "keys tested with HasField near a Fail" -> 49 handlers, 135 keys, and it conflated presence
    refusals with KeyNote hints and with mode VALUES (`class`, `op`, `shape`, `line`).
  * "optional wrapper params must default to None" -> 585 violations across 268 of 538 tools. That
    is a style the codebase deliberately does not follow, not a defect list.
  * "tables inside a handler carrying a MODE-PARAMS-OK marker" -> still wrong, because a KeyNote
    pair `{ TEXT("key"), TEXT("advice") }` is structurally IDENTICAL to a guard row
    `{ TEXT("param"), TEXT("modes") }` and both live in the same function.

So the list is written down. Adding a guard means adding a line here, the same discipline as adding
the MODE-PARAMS-OK marker the handler already needs. A guard added without one is unprotected, and
this file cannot tell you that - which is exactly the situation before it existed, so it is not a
regression, just a limit.

THREE QUESTIONS WERE ASKED OF THIS BOUNDARY ON 2026-09-03. Two found shipped bugs and the third
found nothing, which is worth writing down so it is not asked again from scratch:

  1. does a wrapper SEND a key the handler refuses for being present?   -> this file. Found
     sculpt_landscape (flatten and smooth unreachable, v0.3.0-v0.8.1) and five wrappers broken the
     same morning by new guards.
  2. does a wrapper DEFAULT disagree with the handler's default?        -> found
     override_inherited_component, which could not be called at all in any tagged release because
     the wrapper posted confirm:false and the endpoint honours it. Only 3 candidates in the whole
     surface and 2 were my extractor's fault; the axis is otherwise clean, so it is not automated.
  3. does a handler REQUIRE a key its wrapper cannot supply?            -> ZERO. Twelve candidates,
     twelve false: eight reach the key through an alias family the handler reads with JStrAny, and
     the rest were prose the scan misread - the real messages are "actorPaths[] is required" and
     "pathPrefix is required and must start with /". Parsing a requirement out of a refusal
     SENTENCE is reading prose as evidence, which this repo has a whole audit against. Not built.

  python tools/audit_mcp_default_sends.py           the check
  python tools/audit_mcp_default_sends.py --plant   prove it sees a wrapper that would break
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp-server", "server.py")

# {endpoint: keys the handler refuses BECAUSE THEY ARE PRESENT, whatever their value}
# Each entry is a guard written on 2026-09-03; the handler carries a MODE-PARAMS-OK comment saying
# the same thing in prose.
REFUSED_ON_PRESENCE = {
    "trace":                  {"radius", "halfExtent", "halfHeight", "drawDuration"},
    "draw_debug":             {"start", "end", "center", "radius", "extent", "text"},
    "create_procedural_mesh": {"dimensionX", "dimensionY", "dimensionZ", "steps", "radius",
                               "stepsPhi", "stepsTheta", "height", "radialSteps", "heightSteps",
                               "capped", "baseRadius", "topRadius", "majorRadius", "minorRadius",
                               "majorSteps", "minorSteps"},
    # `nodeId` LOOKS DEAD AND IS NOT. Neither wrapper posts it today, so a check for "map keys no
    # wrapper sends" reports these two rows - which is a reason to leave them alone, not to prune
    # them. nodeId is a real accepted alias for nodeGuid on both endpoints, and the handler's own
    # refusal table is exactly { nodeGuid, nodeId } (MifBridgeNodes.cpp:2869), so the row MATCHES the
    # handler rather than exceeding it. If somebody adds a node_id parameter to either wrapper, this
    # catches it on the day it lands. Checked 2026-09-03: the accept-list is
    # {op, graphId, blueprintId, path, nodeGuid, nodeId} and the other two spellings the codebase
    # uses elsewhere - `node`, `guid` - are refused outright by RejectUnknownParams here, so there
    # is no third alias missing from this row.
    "blueprint_watch":        {"nodeGuid", "nodeId", "pin"},
    "blueprint_breakpoint":   {"nodeGuid", "nodeId"},
    "start_pie":              {"oneProcess", "width", "height"},
    "list_sublevels":         {"netMode"},
    # PRE-EXISTING, and the worst of the set: the wrapper defaulted mode to "flatten" AND amount
    # to 0.0, so the tool's own default invocation posted an amount the chosen mode refuses. Not
    # caused by the 2026-09-03 guards - sculpt_landscape has refused this since it was written,
    # and is the exemplar audit_mode_params cites for how to write one. The guard was right and
    # the wrapper defeated it. Found by asking whether the new hazard had older instances.
    "sculpt_landscape":       {"amount", "targetZ"},
    "set_actor_transform":    {"scale"},
    "rename_asset":           {"path"},
    "set_function_flags":     {"pure"},
    # NOT quite "refused whatever the value" - override_inherited_component honours confirm
    # rather than ignoring it, so confirm=false is a deliberate NO and is refused while
    # confirm=true proceeds. Listed anyway, because a wrapper carrying EITHER default is wrong:
    # False could not call the tool at all (it shipped that way), and True would auto-confirm a
    # guarded write on a caller's behalf, which is worse.
    "override_inherited_component": {"confirm"},

    # THE BLENDER ARM, reached through _blender rather than _post. Both of these refuse a
    # MUTUAL EXCLUSION rather than a lone key - "pass lookAt OR rotation, not both" - so a
    # wrapper carrying a default for EITHER side makes the other unusable. Both are correct
    # today (every parameter defaults to None) and are listed so they stay that way. The tool
    # names are the wrapper names, which is what this scan matches on.
    "bl_create_camera":       {"lookAt", "rotation"},
    "bl_set_viewport_view":   {"lookFrom", "azimuth", "elevation", "distance"},

    # THE OTHER FIVE BLENDER EXCLUSIONS, added 2026-09-03. The addon raises MifOpError on seven
    # "pass X OR Y, not both" pairs and this map covered two of them - so five ops with exactly the
    # shape that produced six uncallable UE tools that same day were unwatched. All five are CORRECT
    # today (every excluded parameter defaults to None, and bl_run_python uses `or None`), and they
    # are listed for the reason the two above are: so they stay that way. A mutual exclusion is the
    # sharpest form of this class, because a default on EITHER side makes the OTHER side unusable
    # while the tool still looks fine from the side that was defaulted.
    "bl_set_keyframe":        {"location", "rotation", "scale", "dataPath"},
    "bl_create_primitive":    {"size", "radius"},
    "bl_assign_material_to_faces": {"faces", "fromSlot"},
    "bl_run_python":          {"code", "file"},
    "bl_set_world":           {"hdri", "color"},

    # ADDED 2026-09-03, and the reason they were missing IS the weakness this map's own header
    # names. A multi-agent review of the day's tree found four more endpoints of exactly the shape
    # the two shipped bugs had, and every one of them was invisible here only because nobody had
    # thought to write the row. The detector was never broken; its corpus was hand-written. When
    # one reviewer added `map_legacy_input` to a scratch copy of this map, the unmodified tool
    # named all five of its parameters immediately.
    #
    # map_legacy_input refuses a MUTUAL EXCLUSION like the two Blender rows above, and was the
    # worst case of it: an action mapping refuses `scale`, an axis mapping refuses the four
    # modifiers, and the wrapper sent all five - so BOTH modes were uncallable. Shipped 2026-08-30.
    "map_legacy_input":       {"scale", "shift", "ctrl", "alt", "cmd"},
    # bWantRename/bWantRetype/bWantDefault are HasField checks; newName="" made the rename branch
    # run on every call and the next line refuses an empty identifier.
    "set_struct_member":      {"newName", "type", "default"},
    # bHasEntry is HasField("index")||HasField("value")||...; value="" made it true always, so the
    # enum-scoped bitflags mode could never be reached.
    "set_enum_value":         {"value", "newName"},
    # THE ONE THAT LIED. Both branches are HasField-gated and the wrapper sent both keys, so a
    # profile-only call APPLIED the profile and then failed on the empty collisionEnabled with
    # "NOTHING was changed." - a false claim of that exact sentence, which is the strongest promise
    # this codebase makes.
    "set_collision":          {"profile", "collisionEnabled"},
}


def posted_keys(fn_node):
    """{python arg: posted key} and the set of args protected by an `or None`.

    THE `or None` ARM IS NOT OPTIONAL. add_variable and export_asset post
    `repNotifyFunction=rep_notify_function or None`, which turns an empty-string default into a
    dropped field - the correct fix for this whole class. A first version of this scan mapped only
    bare Names, so those params were SKIPPED rather than cleared and the clean result it printed for
    them meant nothing. That is rule 1 of audit_vacuous_checks, committed inside the script written
    to verify a fix for a different bug.
    """
    posted, protected = {}, set()
    for n in ast.walk(fn_node):
        # BOTH TRANSPORTS. _blender's own docstring says it "mirrors _post: unset (None) params are
        # dropped", so the Blender arm carries the identical hazard - and it has two ops that refuse
        # on presence, both mutual-exclusion (create_camera's lookAt-or-rotation,
        # set_viewport_view's lookFrom-or-polar). Both are safe today, checked by hand; scanning
        # only _post would have left them unguarded for the next person to break.
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("_post", "_blender")):
            continue
        for kw in n.keywords:
            v = kw.value
            if isinstance(v, ast.Name):
                posted[v.id] = kw.arg
            elif isinstance(v, ast.BoolOp) and isinstance(v.op, ast.Or):
                for x in v.values:
                    if isinstance(x, ast.Name):
                        posted[x.id] = kw.arg
                if any(isinstance(x, ast.Constant) and x.value is None for x in v.values):
                    protected |= {x.id for x in v.values if isinstance(x, ast.Name)}
    return posted, protected


def scan(source):
    """[(tool, arg, key, default_repr)] for wrappers that would post a refused key by default."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name not in REFUSED_ON_PRESENCE:
            continue
        args = node.args
        if not args.defaults:
            continue
        defaults = dict(zip([a.arg for a in args.args][-len(args.defaults):], args.defaults))
        posted, protected = posted_keys(node)
        for arg, dflt in defaults.items():
            key = posted.get(arg)
            if key not in REFUSED_ON_PRESENCE[node.name] or arg in protected:
                continue
            if isinstance(dflt, ast.Constant) and dflt.value is None:
                continue
            try:
                shown = repr(ast.literal_eval(dflt))
            except (ValueError, SyntaxError):
                shown = ast.unparse(dflt)
            bad.append((node.name, arg, key, shown))
    return bad


def main():
    if not os.path.isfile(SERVER):
        print("server.py not found at %s - the check CANNOT RUN. Not skipped, failed." % SERVER)
        return 2
    source = io.open(SERVER, encoding="utf-8", errors="replace").read()

    if "--plant" in sys.argv:
        # PLANTED IN MEMORY. This reads a file people edit; a killed run must not leave a broken
        # default behind in the real server.
        #
        # FOUR SHAPES, because one proved a quarter of it. Until 2026-09-03 this planted only a
        # STRING default on a _post wrapper - and the four bugs found that day were a float (1.0),
        # three bools (False) and an empty string, on wrappers this arm never exercised. The tool
        # scans _blender call sites too and no plant had ever touched that half.
        shapes = [
            ("string default, _post arm",
             ('def list_sublevels(world: str = "editor", net_mode: str = None)',
              'def list_sublevels(world: str = "editor", net_mode: str = "server")'),
             "list_sublevels"),
            # The exact shape of map_legacy_input's shipped bug, put back.
            ("float default, _post arm",
             ("def map_legacy_input(name: str, key: str, axis: bool = False, scale: float = None,",
              "def map_legacy_input(name: str, key: str, axis: bool = False, scale: float = 1.0,"),
             "map_legacy_input"),
            ("bool default, _post arm",
             ("shift: bool = None, ctrl: bool = None, alt: bool = None,",
              "shift: bool = False, ctrl: bool = None, alt: bool = None,"),
             "map_legacy_input"),
            # THE BLENDER ARM. bl_create_camera refuses lookAt/rotation as a mutual exclusion, so a
            # default on either makes the other unusable - the same class, a different transport.
            ("default on a _blender wrapper",
             ('def bl_create_camera(name: str = "", location: list = None, rotation: list = None,',
              'def bl_create_camera(name: str = "", location: list = None, rotation: list = "xyz",'),
             "bl_create_camera"),
        ]
        failures = []
        for label, (old, new), endpoint in shapes:
            if old not in source:
                failures.append("%s: ANCHOR NOT FOUND - the plant matched nothing, which says "
                                "nothing about the detector" % label)
                continue
            seen = [b for b in scan(source.replace(old, new, 1)) if b[0] == endpoint]
            print("PLANT  %-30s -> seen=%s" % (label, bool(seen)))
            if not seen:
                failures.append("%s: NOT SEEN" % label)

        # NEGATIVE CONTROL: the `or None` clearance. set_struct_member is in the map and posts every
        # optional key as `x or None`, so the UNPLANTED source must not flag it. Without this, a
        # detector that flagged everything would pass every plant above.
        control = [b for b in scan(source) if b[0] == "set_struct_member"]
        print("PLANT  %-30s -> flagged=%s (must be False)" % ("or-None clearance, unplanted",
                                                              bool(control)))
        if control:
            failures.append("or-None clearance: set_struct_member flagged with no plant - the "
                            "detector flags protected wrappers, so a seen plant proves nothing")

        print("")
        if failures:
            for f in failures:
                print("  %s" % f)
            print("PLANT NOT SEEN AS MINE - a clean run would mean NOTHING")
            return 1
        print("PLANT SEEN FOR THE RIGHT REASON - a clean run is worth something")
        return 0

    bad = scan(source)
    print("%d endpoint(s) refuse a key for being PRESENT; checked their MCP wrappers\n"
          % len(REFUSED_ON_PRESENCE))
    if not bad:
        print("OK  no wrapper posts a refused key by default - every one is None or `or None`,")
        print("    so _post drops it and the HANDLER's own default applies.")
        return 0
    print("%d wrapper parameter(s) would be REFUSED on a call that never mentioned them:" % len(bad))
    for tool, arg, key, shown in bad:
        print("  %-26s %-20s posts %-16s default=%s" % (tool, arg, key, shown))
    print("")
    print("Set the default to None, or post it as `x or None`. The handler carries the real")
    print("default; the wrapper must not carry it too, or every call sends it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
