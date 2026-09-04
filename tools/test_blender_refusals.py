"""The Blender addon's REFUSAL contracts, tested with no Blender at all.

WHY THIS EXISTS. On 2026-09-03 the addon went from 68 ops to 100 in one session, and not one line
of it could be run: Blender was open on the machine but its addon was not listening, and 12 of the
20 Blender suites need a live backend. So the largest single addition this addon has ever had sat
entirely unverified, and "the static gates are green" was the strongest claim available.

That is a gap in the TOOLING, not an unavoidable fact. The UE half has the same shape solved -
test_payload_contract stubs requests and mcp exports and tests the transport contract offline. The
addon's refusal paths are pure parameter validation that runs BEFORE anything touches bpy, which is
exactly the discipline every op here was written to: a refusal fires before a mutation. That makes
them testable against a stub.

WHAT THIS CAN AND CANNOT PROVE, stated plainly because the distinction is the whole value:

  IT PROVES    a required key is required; a mutually exclusive pair is refused together; an
               unknown key is rejected by reject_unknown; a value outside an enum is refused; a
               type mismatch is refused. All of that is real logic and all of it was previously
               unchecked by anything.
  IT CANNOT    prove any op DOES what it says once Blender is real, WITH ONE EXCEPTION added
               2026-09-03. Every postcondition that depends on evaluation - matrices, purged
               orphans, colour spaces, motion preserved, a rendered frame - needs a live Blender
               and stays unverified until a suite runs there.
  THE EXCEPTION is B113. Collection membership is not evaluated: it is a name-keyed set of links
               and a tree of children, so a stub that models linking honestly answers the real
               question - is the object IN there afterwards, is the collection reachable from the
               scene - rather than only whether a refusal fired. Those are genuine postcondition
               checks and are labelled as such, so nobody reads a green run here as covering the
               families where the same thing is impossible.

A refusal that fires for the WRONG REASON would pass a naive version of this, so each check asserts
on the message as well as on the fact of the raise. "It raised" is not "it refused this".
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "blender-addon")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


class _Enum(object):
    def __init__(self, ids):
        self.enum_items = [types.SimpleNamespace(identifier=i) for i in ids]


class _RNA(object):
    """Just enough bl_rna.properties[...] for the enum lookups the ops do."""

    def __init__(self, enums=None, props=()):
        self._enums = enums or {}
        self._props = set(props) | set(self._enums)

    @property
    def properties(self):
        return self

    def __getitem__(self, key):
        if key in self._enums:
            return _Enum(self._enums[key])
        raise KeyError(key)

    def __contains__(self, key):
        return key in self._props


class _Obj(object):
    def __init__(self, name, kind="MESH"):
        self.name = name
        self.type = kind
        self.data = types.SimpleNamespace(name=name + "Data")
        self.pose = None
        self.constraints = []
        self.animation_data = None

    def path_resolve(self, path):
        raise ValueError("no such path %r" % path)


class _Coll(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)

    def __iter__(self):
        return iter(self.values())


class _LinkSet(object):
    """Blender's CollectionObjects / CollectionChildren: `in` tests by NAME, link raises on a dupe.

    Both details are load-bearing rather than cosmetic. ops_collection asks `obj.name in
    coll.objects`, which only works because Blender keys these by name, and it guards every link
    with a membership test because a second link() of the same object RAISES in real Blender. A stub
    that accepted a duplicate silently would let a bug through that the real API rejects loudly.
    """

    def __init__(self):
        self._d = {}

    def link(self, item):
        if item.name in self._d:
            raise RuntimeError("Object '%s' already in collection" % item.name)
        self._d[item.name] = item

    def unlink(self, item):
        self._d.pop(item.name, None)

    def __contains__(self, key):
        return (key if isinstance(key, str) else key.name) in self._d

    def __iter__(self):
        return iter(list(self._d.values()))

    def __len__(self):
        return len(self._d)


class _StubCollection(object):
    def __init__(self, name):
        self.name = name
        self.objects = _LinkSet()
        self.children = _LinkSet()
        self.hide_viewport = False
        self.hide_render = False
        self.color_tag = "NONE"


class _CollData(object):
    """bpy.data.collections - a name-keyed store with new/remove/get and iteration."""

    def __init__(self):
        self._d = {}

    def new(self, name):
        c = _StubCollection(name)
        self._d[name] = c
        return c

    def remove(self, coll):
        self._d.pop(coll.name, None)

    def get(self, name, default=None):
        return self._d.get(name, default)

    def __iter__(self):
        return iter(list(self._d.values()))

    def __len__(self):
        return len(self._d)


class _LayerColl(object):
    """A LayerCollection mirroring the real tree, with the four PER-VIEW-LAYER flags.

    Rebuilt from the collection tree on every access rather than cached, because excluding a
    collection makes Blender rebuild the layer tree and ops_collection re-fetches for exactly that
    reason. The flags are held in a dict OUTSIDE the wrapper so they survive the rebuild - which is
    what makes the read-back check meaningful instead of always seeing what was just written.
    """

    def __init__(self, coll, flags):
        self.collection = coll
        self._flags = flags.setdefault(coll.name, {"exclude": False, "hide_viewport": False,
                                                   "indirect_only": False, "holdout": False})
        self._all = flags
        self.children = [_LayerColl(c, flags) for c in coll.children]

    def __getattr__(self, key):
        if key in ("exclude", "hide_viewport", "indirect_only", "holdout"):
            return self._flags[key]
        raise AttributeError(key)

    def __setattr__(self, key, value):
        if key in ("exclude", "hide_viewport", "indirect_only", "holdout"):
            self._flags[key] = value
        else:
            object.__setattr__(self, key, value)


def install_collection_stub():
    """Give the bpy stub enough collection machinery to run ops_collection for real."""
    bpy = sys.modules["bpy"]
    bpy.data.collections = _CollData()
    root = _StubCollection("Scene Collection")
    bpy.context.scene.collection = root
    flags = {}

    class _VL(object):
        name = "ViewLayer"

        @property
        def layer_collection(self):
            return _LayerColl(root, flags)

        def update(self):
            pass

    vl = _VL()
    vl.objects = bpy.data.objects
    bpy.context.view_layer = vl
    bpy.context.scene.view_layers = _Coll({"ViewLayer": vl})
    return root


def _p(name):
    """use_pass_<name> - spelled once so the stub and the module cannot drift apart."""
    return "use_pass_" + name


def install_stub():
    """A bpy stub good enough to IMPORT the ops modules and reach their refusals."""
    bpy = types.ModuleType("bpy")
    objects = _Coll()
    objects["Cube"] = _Obj("Cube", "MESH")
    objects["Lamp"] = _Obj("Lamp", "LIGHT")
    objects["Cam"] = _Obj("Cam", "CAMERA")
    bpy.data = types.SimpleNamespace(
        objects=objects, actions=_Coll(), lights=_Coll(), cameras=_Coll(),
        materials=_Coll(), images=_Coll(), meshes=_Coll(), node_groups=_Coll(),
        textures=_Coll(), armatures=_Coll(), worlds=_Coll(), curves=_Coll(),
        collections=_Coll(), filepath="", is_dirty=False,
        # scenes, because render_animation resolves a NAMED scene through bpy.data.scenes
        # and Blender's own -S flag silently renders the wrong one on a typo.
        scenes=_Coll({"Scene": types.SimpleNamespace(name="Scene")}))
    scene = types.SimpleNamespace(
        frame_current=1, frame_start=1, frame_end=250, frame_step=1,
        frame_preview_start=1, frame_preview_end=250, use_preview_range=False,
        camera=None, world=None, objects=objects, name="Scene",
        render=types.SimpleNamespace(engine="CYCLES", fps=24, fps_base=1.0,
                                     resolution_x=1920, resolution_y=1080,
                                     resolution_percentage=100, filepath="/tmp/x",
                                     film_transparent=False,
                                     image_settings=types.SimpleNamespace(
                                         file_format="PNG", color_mode="RGBA",
                                         color_depth="8")),
        timeline_markers=_Coll())
    bpy.context = types.SimpleNamespace(scene=scene, view_layer=types.SimpleNamespace(
        name="ViewLayer", objects=objects, update=lambda: None))
    bpy.types = types.SimpleNamespace(
        Light=types.SimpleNamespace(bl_rna=_RNA({"type": ["POINT", "SUN", "SPOT", "AREA"]},
                                                ["use_shadow"])),
        Camera=types.SimpleNamespace(bl_rna=_RNA({"type": ["PERSP", "ORTHO", "PANO"],
                                                  "sensor_fit": ["AUTO", "HORIZONTAL",
                                                                 "VERTICAL"]})),
        Constraint=types.SimpleNamespace(bl_rna=_RNA({"type": ["TRACK_TO", "COPY_LOCATION",
                                                               "CHILD_OF"]})),
        FModifier=types.SimpleNamespace(bl_rna=_RNA({"type": ["CYCLES", "NOISE"]})),
        FModifierCycles=types.SimpleNamespace(bl_rna=_RNA({"mode_before": ["NONE", "REPEAT"]})),
        NlaStrip=types.SimpleNamespace(bl_rna=_RNA({"blend_type": ["REPLACE", "ADD"]})),
        Keyframe=types.SimpleNamespace(bl_rna=_RNA({"interpolation": ["CONSTANT", "LINEAR",
                                                                     "BEZIER", "BACK"],
                                                    "easing": ["AUTO", "EASE_IN"],
                                                    "handle_left_type": ["FREE", "AUTO"]})),
        FCurve=types.SimpleNamespace(bl_rna=_RNA({"extrapolation": ["CONSTANT", "LINEAR"]})),
        RenderSettings=types.SimpleNamespace(bl_rna=_RNA({"engine": ["CYCLES", "BLENDER_EEVEE"]})),
    )
    bpy.app = types.SimpleNamespace(version=(4, 4, 0), version_string="4.4.0", background=True,
                                    timers=types.SimpleNamespace(
                                        is_registered=lambda f: False,
                                        register=lambda *a, **k: None,
                                        unregister=lambda f: None))
    bpy.ops = types.SimpleNamespace()

    # bpy.path, WHICH SEVERAL MODULES USE BEFORE ANY OF THEIR OTHER REFUSALS. abspath resolves
    # Blender's "//" relative-to-the-blend prefix and otherwise leaves a path alone; without it,
    # ops_io's extension check and ops_render's frame paths raise AttributeError instead of
    # refusing, which surfaces as "raised AttributeError, not MifOpError" rather than a pass.
    def _abspath(p, **_kw):
        return os.path.abspath(p[2:]) if str(p).startswith("//") else str(p)

    bpy.path = types.SimpleNamespace(abspath=_abspath, basename=os.path.basename)
    path_mod = types.ModuleType("bpy.path")
    path_mod.abspath = _abspath
    path_mod.basename = os.path.basename
    sys.modules["bpy.path"] = path_mod
    sys.modules["bpy"] = bpy

    # SUBMODULES MUST BE REGISTERED, not just attributes. The addon's __init__ does
    # `from bpy.props import BoolProperty` and `from bpy.types import Panel`, and Python resolves
    # those through sys.modules rather than through getattr on the parent - a plain attribute gives
    # "No module named 'bpy.props'; 'bpy' is not a package", which is exactly how this failed first.
    props = types.ModuleType("bpy.props")
    for _p in ("BoolProperty", "IntProperty", "StringProperty", "FloatProperty",
               "EnumProperty", "PointerProperty", "CollectionProperty"):
        setattr(props, _p, lambda **kw: None)
    bpy.props = props
    sys.modules["bpy.props"] = props

    for _t in ("AddonPreferences", "Operator", "Panel", "PropertyGroup"):
        setattr(bpy.types, _t, type(_t, (object,), {}))
    types_mod = types.ModuleType("bpy.types")
    for _name in dir(bpy.types):
        if not _name.startswith("_"):
            setattr(types_mod, _name, getattr(bpy.types, _name))
    sys.modules["bpy.types"] = types_mod
    sys.modules["bpy.utils"] = types.ModuleType("bpy.utils")

    # THE OTHER BLENDER-ONLY MODULES. ops_mesh imports bmesh at module scope, and the wider sweep
    # in B110 has to import EVERY ops module to build the op table - so a missing stub here is not
    # a gap in one test, it stops the whole surface being swept. Stubbed as bare modules because
    # nothing is called on them before reject_unknown, which is the only thing B110 reaches.
    for _mod in ("bmesh", "bpy_extras", "gpu", "bl_math"):
        if _mod not in sys.modules:
            sys.modules[_mod] = types.ModuleType(_mod)
    bmesh = sys.modules["bmesh"]
    bmesh.ops = types.SimpleNamespace()
    bmesh.new = lambda *a, **k: None
    bmesh.from_edit_mesh = lambda *a, **k: None
    _oe = types.ModuleType("bpy_extras.object_utils")
    sys.modules["bpy_extras.object_utils"] = _oe
    sys.modules["bpy_extras"].object_utils = _oe

    mu = types.ModuleType("mathutils")

    class Vector(list):
        @property
        def length(self):
            return sum(v * v for v in self) ** 0.5

        def normalized(self):
            n = self.length or 1.0
            return Vector([v / n for v in self])

        def __sub__(self, o):
            return Vector([a - b for a, b in zip(self, o)])

        def angle(self, other):
            return 0.0

        def to_track_quat(self, *a):
            return None
    mu.Vector = Vector
    mu.Quaternion = lambda *a, **k: None
    mu.Matrix = lambda *a, **k: None
    sys.modules["mathutils"] = mu

    # mathutils.bvhtree MUST BE REGISTERED AS A SUBMODULE, not hung off mu as an attribute.
    # `import mathutils.bvhtree` resolves through sys.modules, so an attribute gives
    # "No module named 'mathutils.bvhtree'; 'mathutils' is not a package" - which is the EXACT
    # failure already written up a few lines above for bpy.props, hit a second time and for the
    # same reason. The suite's crash backstop reported it as "51 checks had already PASSED" rather
    # than a bare traceback, which is how it took a minute instead of a round.
    bvh = types.ModuleType("mathutils.bvhtree")

    class BVHTree(object):
        @staticmethod
        def FromBMesh(*a, **k):
            raise NotImplementedError("no real geometry offline - blender_version_matrix covers "
                                      "the BVH paths on four real Blenders")

        @staticmethod
        def FromObject(*a, **k):
            raise AssertionError("nothing in this addon may call BVHTree.FromObject - it ignores "
                                 "matrix_world and segfaults Blender 3.6 on a non-mesh")

    bvh.BVHTree = BVHTree
    mu.bvhtree = bvh
    sys.modules["mathutils.bvhtree"] = bvh

    if ADDON not in sys.path:
        sys.path.insert(0, ADDON)


def succeeds(fn, params):
    """Call an op expecting it to WORK, and turn any raise into a failed check, not a dead suite.

    THE SIBLING OF refuses(), and it exists because leaving it out cost a ground-truth probe. B113's
    checks called their ops directly. Planting a defect that made create_collection fail its own
    reachability postcondition then raised a MifOpError straight out of the check, which killed the
    run at that line: the suite exited 1 having reported ZERO failures, so the plant looked like it
    had not been caught when in fact it had been caught twice over.

    That is the same shape this file forbids in the ops themselves - B111 exists precisely because a
    crash instead of a refusal reports one problem and hides the rest - and a harness gets no
    exemption from its own rule. Returns (result, error) so a check can assert on either.
    """
    try:
        return fn(params), None
    except Exception as exc:                       # noqa: BLE001
        return {}, "raised %s: %s" % (type(exc).__name__, str(exc)[:110])


def refuses(fn, params, *must_contain):
    """Call an op expecting MifOpError, and require the MESSAGE to name the reason.

    Asserting only that it raised would pass on a refusal that fires for the wrong reason - and a
    wrong-reason refusal is harder to notice than no refusal, because it still looks like the guard
    working. Every check here names a phrase the correct message must contain.
    """
    from MifBlender.ops_common import MifOpError
    try:
        fn(params)
    except MifOpError as exc:
        msg = str(exc)
        missing = [w for w in must_contain if w.lower() not in msg.lower()]
        return (not missing), (msg[:150] if missing else msg[:80])
    except Exception as exc:                       # noqa: BLE001
        return False, "raised %s, not MifOpError: %s" % (type(exc).__name__, str(exc)[:90])
    return False, "did NOT refuse"


def main():
    install_stub()
    # IMPORTED ONCE, AT THE TOP. A name imported ANYWHERE inside a function is local to the WHOLE
    # function, so a `from ... import MifOpError` further down made every earlier reference an
    # UnboundLocalError - which surfaced as the suite dying mid-run rather than as a failed check.
    # It also silently invalidated a ground-truth probe: the run exited 1 because of this, not
    # because the planted defect was caught, and the exit code alone looked like proof.
    from MifBlender.ops_common import MifOpError
    try:
        from MifBlender import ops_anim, ops_lightcam, ops_constraint, ops_file
    except Exception as exc:                       # noqa: BLE001
        print("could not import the addon against the bpy stub: %s: %s"
              % (type(exc).__name__, exc))
        return 1

    print("=== B100: required keys are actually required ===")
    for label, fn, params, words in (
            ("set_light without an object", ops_lightcam.op_set_light, {}, ["object", "required"]),
            ("set_camera without an object", ops_lightcam.op_set_camera, {}, ["object", "required"]),
            ("save_file without a filepath", ops_file.op_save_file, {}, ["filepath", "required"]),
            ("open_file without a filepath", ops_file.op_open_file, {}, ["filepath", "required"]),
            ("add_constraint without a type", ops_constraint.op_add_constraint,
             {"object": "Cube"}, ["type", "required"]),
    ):
        ok, detail = refuses(fn, params, *words)
        check("B100 %s" % label, ok, detail)

    print("")
    print("=== B101: an unknown key is refused by name, never ignored ===")
    for label, fn, params in (
            ("set_light", ops_lightcam.op_set_light, {"object": "Lamp", "zzbogus": 1}),
            ("save_file", ops_file.op_save_file, {"filepath": "/tmp/x.blend", "zzbogus": 1}),
            ("list_constraints", ops_constraint.op_list_constraints,
             {"object": "Cube", "zzbogus": 1}),
    ):
        ok, detail = refuses(fn, params, "unknown param", "zzbogus")
        check("B101 %s names the offending key" % label, ok, detail)

    print("")
    print("=== B102: the wrong object TYPE is refused, with what it actually is ===")
    ok, detail = refuses(ops_lightcam.op_set_light, {"object": "Cube"}, "not a LIGHT", "MESH")
    check("B102 set_light on a mesh says it is a MESH", ok, detail)
    ok, detail = refuses(ops_lightcam.op_set_camera, {"object": "Lamp"}, "not a CAMERA", "LIGHT")
    check("B102 set_camera on a light says it is a LIGHT", ok, detail)
    ok, detail = refuses(ops_lightcam.op_set_camera_panorama, {"object": "Cube"},
                         "not a CAMERA")
    check("B102 set_camera_panorama on a mesh refuses", ok, detail)
    ok, detail = refuses(ops_lightcam.op_set_light_ies, {"object": "Cube", "filepath": "/x.ies"},
                         "not a LIGHT", "MESH")
    check("B102 set_light_ies on a mesh says it is a MESH", ok, detail)

    print("")
    print("=== B102b: set_light_ies refuses before it builds a node tree ===")
    ok, detail = refuses(ops_lightcam.op_set_light_ies, {"object": "Lamp"},
                         "filepath", "text", "clear")
    check("B102b with neither a file, inline text nor clear", ok, detail)
    ok, detail = refuses(ops_lightcam.op_set_light_ies,
                         {"object": "Lamp", "filepath": "/x.ies", "text": "IESNA"},
                         "not both")
    check("B102b filepath and text together are refused", ok, detail)
    # THE ORDER IS THE POINT. A missing file must be caught BEFORE use_nodes is set, or the light
    # is left with a half-built tree and renders black - which is why the message says so.
    ok, detail = refuses(ops_lightcam.op_set_light_ies,
                         {"object": "Lamp", "filepath": "/zz/no/such/profile.ies"},
                         "no IES file at", "before building")
    check("B102b a missing .ies is refused BEFORE the node tree is touched", ok, detail)

    print("")
    print("=== B102c: light linking, including the version it needs ===")
    ok, detail = refuses(ops_lightcam.op_set_light_linking, {"object": "Cube",
                                                             "receiverCollection": "C"},
                         "not a LIGHT")
    check("B102c set_light_linking on a mesh refuses", ok, detail)
    # The stub's objects carry no light_linking, which is exactly a pre-4.2 Blender - so this
    # exercises the version refusal itself rather than simulating it.
    ok, detail = refuses(ops_lightcam.op_set_light_linking,
                         {"object": "Lamp", "receiverCollection": "C"},
                         "no light_linking", "4.2")
    check("B102c a build without light_linking is refused BY NAME with the version, not "
          "silently ignored", ok, detail)

    print("")
    print("=== B103: a missing object is refused and the message helps ===")
    ok, detail = refuses(ops_lightcam.op_set_light, {"object": "NoSuchZz"}, "no object named")
    check("B103 set_light names the object that does not exist", ok, detail)
    ok, detail = refuses(ops_constraint.op_add_constraint,
                         {"object": "Cube", "type": "TRACK_TO", "target": "NoSuchZz"},
                         "no target object named")
    check("B103 add_constraint distinguishes a missing TARGET from a missing object", ok, detail)

    print("")
    print("=== B104: values outside this Blender's enums are refused, listing the valid ones ===")
    ok, detail = refuses(ops_lightcam.op_set_light, {"object": "Lamp", "type": "ZZBOGUS"},
                         "unknown light type", "POINT")
    check("B104 an unknown light type lists the real ones", ok, detail)
    ok, detail = refuses(ops_constraint.op_add_constraint,
                         {"object": "Cube", "type": "ZZBOGUS"},
                         "unknown constraint type", "TRACK_TO")
    check("B104 an unknown constraint type lists the real ones", ok, detail)

    print("")
    print("=== B105: mutually exclusive parameters are refused TOGETHER ===")
    ok, detail = refuses(ops_lightcam.op_set_camera,
                         {"object": "Cam", "lookAt": [0, 0, 0], "rotation": [0, 0, 0]},
                         "not both")
    check("B105 set_camera refuses lookAt with rotation", ok, detail)
    ok, detail = refuses(ops_constraint.op_add_constraint,
                         {"object": "Cube", "type": "TRACK_TO", "target": "Cube"},
                         "itself")
    check("B105 add_constraint refuses an object constrained to itself", ok, detail)

    print("")
    print("=== B106: per-type keys on the wrong type, which is the light rule ===")
    ok, detail = refuses(ops_lightcam.op_set_light,
                         {"object": "Lamp", "type": "POINT", "spotAngle": 0.5},
                         "spotAngle", "SPOT", "NOTHING was changed")
    check("B106 spotAngle on a POINT light is refused, and says nothing changed", ok, detail)
    ok, detail = refuses(ops_lightcam.op_set_light,
                         {"object": "Lamp", "type": "SPOT", "size": 2.0},
                         "size", "AREA")
    check("B106 an AREA-only key on a SPOT light is refused", ok, detail)

    print("")
    print("=== B108: the animation ops - every one added 2026-09-03 ===")
    for label, fn, params, words in (
            ("evaluate_at_frame without frames", ops_anim.op_evaluate_at_frame,
             {"object": "Cube"}, ["frames", "required"]),
            ("evaluate_at_frame with an empty list", ops_anim.op_evaluate_at_frame,
             {"object": "Cube", "frames": []}, ["non-empty"]),
            ("evaluate_at_frame with a non-number", ops_anim.op_evaluate_at_frame,
             {"object": "Cube", "frames": ["x"]}, ["must be a number"]),
            ("delete_keyframe without a dataPath", ops_anim.op_delete_keyframe,
             {"object": "Cube"}, ["dataPath", "required"]),
            ("edit_fcurve with nothing to change", ops_anim.op_edit_fcurve,
             {"object": "Cube", "dataPath": "location"}, ["nothing to change"]),
            ("edit_fcurve with an unknown interpolation", ops_anim.op_edit_fcurve,
             {"object": "Cube", "dataPath": "location", "interpolation": "ZZBOGUS"},
             ["unknown interpolation", "LINEAR"]),
            ("add_fcurve_modifier with an unknown type", ops_anim.op_add_fcurve_modifier,
             {"object": "Cube", "dataPath": "location", "type": "ZZBOGUS"},
             ["unknown fcurve modifier type"]),
            ("add_driver without a dataPath", ops_anim.op_add_driver,
             {"object": "Cube"}, ["dataPath", "required"]),
            ("add_driver on a path that does not resolve", ops_anim.op_add_driver,
             {"object": "Cube", "dataPath": "zz.bogus"},
             ["does not resolve", "evaluate to zero"]),
            ("add_nla_strip without an action", ops_anim.op_add_nla_strip,
             {"object": "Cube"}, ["action", "required"]),
            ("add_nla_strip naming an action that does not exist", ops_anim.op_add_nla_strip,
             {"object": "Cube", "action": "NoSuchZz"}, ["no action named"]),
            ("set_marker without a name", ops_anim.op_set_marker, {}, ["name", "required"]),
            ("set_marker binding a NON-camera", ops_anim.op_set_marker,
             {"name": "M", "frame": 1, "camera": "Cube"}, ["not a CAMERA"]),
            ("set_marker creating one with no frame", ops_anim.op_set_marker,
             {"name": "NoSuchMarkerZz"}, ["frame' is required"]),
            ("bake_to_keyframes with end before start", ops_anim.op_bake_to_keyframes,
             {"object": "Cube", "frameStart": 50, "frameEnd": 10}, ["before", "NOTHING was baked"]),
            ("move_keyframes with neither offset nor scale", ops_anim.op_move_keyframes,
             {"object": "Cube"}, ["offset", "scale"]),
            ("move_keyframes with BOTH offset and scale", ops_anim.op_move_keyframes,
             {"object": "Cube", "offset": 5, "scale": 2}, ["not both", "ambiguous"]),
            ("move_keyframes with a zero scale", ops_anim.op_move_keyframes,
             {"object": "Cube", "scale": 0}, ["must be positive", "collapses"]),
            ("move_keyframes with a negative scale", ops_anim.op_move_keyframes,
             {"object": "Cube", "scale": -1}, ["must be positive"]),
            ("move_keyframes with an inverted range", ops_anim.op_move_keyframes,
             {"object": "Cube", "offset": 5, "frameStart": 50, "frameEnd": 10},
             ["before", "NOTHING was moved"]),
    ):
        ok, detail = refuses(fn, params, *words)
        check("B108 %s" % label, ok, detail)

    print("")
    print("=== B109: scene and file ops ===")
    from MifBlender import ops_scene
    for label, fn, params, words in (
            ("set_custom_property without a key", ops_scene.op_set_custom_property,
             {"object": "Cube"}, ["key", "required"]),
            ("set_custom_property on an INTERNAL key", ops_scene.op_set_custom_property,
             {"object": "Cube", "key": "cycles", "value": 1}, ["internal key"]),
            ("set_custom_property with no value", ops_scene.op_set_custom_property,
             {"object": "Cube", "key": "K"}, ["value", "required"]),
            ("set_object_visibility with no flags at all", ops_scene.op_set_object_visibility,
             {"object": "Cube"}, ["no visibility flag"]),
            ("set_object_visibility on a missing object", ops_scene.op_set_object_visibility,
             {"object": "NoSuchZz", "hideRender": True}, ["no object named"]),
            ("open_file on a path that does not exist", ops_file.op_open_file,
             {"filepath": "/zz/no/such/file/anywhere.blend"}, ["no file at"]),
    ):
        ok, detail = refuses(fn, params, *words)
        check("B109 %s" % label, ok, detail)

    print("")
    print("=== B110: EVERY op refuses an unknown key - the whole surface, swept ===")
    # THE SYSTEMATIC ONE. The hand-written checks above cover the ops added 2026-09-03; the other
    # ~70 have 242 refusal paths between them that are only reachable with a live Blender, so
    # nothing checked any of them on an ordinary run.
    #
    # reject_unknown is the FIRST statement of 102 of the 103 ops (measured, not assumed), so a
    # bogus key reaches it before anything touches bpy. That makes one property checkable across
    # the entire surface at once: an op that silently ignores a misspelled parameter is the
    # "ran but did nothing" bug this addon's guard exists to prevent, and it is exactly the class
    # that let invoke_editor_tab and export_mesh's objectTypes sit wrong for months on the UE side.
    from MifBlender import server as _bl_server
    table = _bl_server._op_table()
    swept, missed = 0, []
    for name, fn in sorted(table.items()):
        swept += 1
        ok, _detail = refuses(fn, {"zzbogus_param": 1}, "unknown param", "zzbogus_param")
        if not ok:
            missed.append(name)
    check("B110 all %d registered ops reject an unknown parameter by name" % swept,
          not missed, "ops that did NOT: %s" % ", ".join(missed[:12]))
    # A SWEEP THAT FOUND NOTHING MUST PROVE IT LOOKED. Zero ops swept would satisfy the check
    # above trivially, and an op table that failed to build would look identical to a clean run.
    check("B110 and the sweep actually covered the whole table, not an empty one",
          swept >= 100, "swept %d ops" % swept)

    print("")
    print("=== B111: no op CRASHES on an object that does not exist ===")
    # THE SECOND SYSTEMATIC PROPERTY, and the one that decides what a caller sees on a typo. A
    # missing object is the single most common mistake anybody makes against this addon, and the
    # difference between a MifOpError naming what exists and an AttributeError from somewhere
    # inside bpy is the difference between a message and a traceback.
    #
    # THE ASSERTION IS ZERO CRASHES rather than a count of good messages, deliberately. Measured
    # 2026-09-03: 58 ops refuse and NAME the object, and 4 more refuse for a DIFFERENT required key
    # first - create_action wants a name, export_mesh a file, transfer_weights a source - which is
    # correct behaviour, not a gap. A count would go stale the moment an op gained a parameter;
    # "nothing throws a raw exception" stays true and stays meaningful.
    crashed = []
    checked = 0
    for name, fn in sorted(table.items()):
        checked += 1
        try:
            fn({"object": "NoSuchZzz"})
        except MifOpError:
            pass
        except Exception as exc:                   # noqa: BLE001
            crashed.append("%s (%s)" % (name, type(exc).__name__))
    check("B111 no op raises a raw exception on a missing object - %d checked" % checked,
          not crashed, "crashed: %s" % ", ".join(crashed[:10]))
    check("B111 and this swept the whole table too", checked >= 100, "checked %d" % checked)

    print("")
    print("=== B112: render_animation refuses BEFORE spawning, and render_status keeps its three "
          "answers apart ===")
    # WHY THIS ONE IS WORTH TESTING OFFLINE MORE THAN MOST. Every refusal here is protecting MINUTES
    # of somebody's machine, and the failure it guards against is not an error - it is a render that
    # runs to completion and produces the WRONG FRAMES, because the file on disk was not the scene
    # the caller was looking at. A guard that fires after the spawn is not a guard.
    import os as _os
    import tempfile as _tf
    from MifBlender import ops_render

    bpy = sys.modules["bpy"]
    saved = (bpy.data.filepath, bpy.data.is_dirty, bpy.context.scene.camera)
    try:
        bpy.data.filepath, bpy.data.is_dirty = "", False
        ok, msg = refuses(ops_render.op_render_animation, {}, "saved", "save_file")
        check("B112 an unsaved session is refused and pointed at save_file - there is no file to "
              "render out of process", ok, msg)

        real = _os.path.join(_tf.gettempdir(), "mif_b112_probe.blend")
        with open(real, "wb") as fh:
            fh.write(b"not really a blend, and nothing here opens it")

        bpy.data.filepath, bpy.data.is_dirty = real, True
        # A GUI SESSION, deliberately. bpy.data.is_dirty is ALWAYS True under blender -b and
        # never clears even after a save - measured on 4.4.0 and 5.0.1 - so the op enforces
        # this guard only when NOT in background mode, or it would refuse every headless
        # call forever. The check has to model the session where the guard applies.
        _was_bg = bpy.app.background
        bpy.app.background = False
        ok, msg = refuses(ops_render.op_render_animation, {}, "unsaved changes", "NOT the scene")
        bpy.app.background = _was_bg
        check("B112 a DIRTY session is refused - the file on disk is not the scene you are looking "
              "at, so the render would silently produce the wrong frames", ok, msg)

        bpy.data.filepath, bpy.data.is_dirty = real + ".gone", False
        ok, msg = refuses(ops_render.op_render_animation, {}, "no file is there")
        check("B112 a filepath whose file was moved or deleted since the save is refused", ok, msg)

        bpy.data.filepath, bpy.data.is_dirty = real, False
        bpy.context.scene.camera = None
        ok, msg = refuses(ops_render.op_render_animation, {}, "no camera", "set_camera")
        check("B112 no scene camera is refused BEFORE the spawn - every frame would fail",
              ok, msg)

        bpy.context.scene.camera = bpy.data.objects["Cam"]
        ok, msg = refuses(ops_render.op_render_animation, {"frameStart": 50, "frameEnd": 10},
                          "before", "renders nothing")
        check("B112 an inverted frame range is refused rather than spawning a no-op render",
              ok, msg)

        ok, msg = refuses(ops_render.op_render_animation, {"frameStep": 0}, "frameStep", "at least 1")
        check("B112 a zero frame step is refused - range() would produce an empty render", ok, msg)

        # THE THREE-ANSWER RULE, and the one that strands a caller if it breaks. An id this Blender
        # has never seen must NOT read as unfinished: a caller told "not finished" polls forever for
        # a process nobody is running. It is not an exception either - asking about a job that has
        # expired is a legitimate question with a real answer.
        # CALLED THROUGH A CATCHER, because a raising op must be a FAILED CHECK and not a dead
        # suite. Ground-truthing this block by making the unknown-job branch fall through produced a
        # TypeError that killed the run at this line - the defect WAS caught, but every check after
        # it silently never ran and no summary printed. A harness that dies on the first defect
        # reports one problem and hides the rest, which is the same shape as an op that crashes
        # instead of refusing - the thing B111 exists to forbid.
        def answers(params):
            try:
                return ops_render.op_render_status(params), None
            except Exception as exc:                          # noqa: BLE001
                return {}, "raised %s: %s" % (type(exc).__name__, str(exc)[:90])

        res, raised = answers({"jobId": "ranim_never_existed"})
        check("B112 an unknown job answers unknownJob rather than raising",
              res.get("unknownJob") is True, raised or "got %s" % res)
        check("B112 and it says explicitly that this is NOT a report of an unfinished render - "
              "the confusion that makes a caller wait forever",
              "NOT" in str(res.get("error", "")) and "unfinished" in str(res.get("error", "")),
              raised or "got %s" % res.get("error"))
        check("B112 an unknown job carries no running/exitCode fields to be misread as progress",
              "running" not in res and "framesRendered" not in res,
              raised or "got keys %s" % sorted(res))

        res, raised = answers({})
        check("B112 render_status with no jobId LISTS jobs instead of guessing which one you meant",
              isinstance(res.get("jobs"), list), raised or "got %s" % res)
    finally:
        bpy.data.filepath, bpy.data.is_dirty, bpy.context.scene.camera = saved
        try:
            _os.remove(_os.path.join(_tf.gettempdir(), "mif_b112_probe.blend"))
        except OSError:
            pass

    print("")
    print("=== B113: collections - the first family whose POSTCONDITIONS can be checked offline ===")
    # WHY THIS BLOCK IS DIFFERENT FROM EVERY OTHER ONE IN THIS FILE. The header says this suite
    # cannot prove an op DOES what it says, and for lights, cameras, constraints and animation that
    # stays true - their effects live in the depsgraph, in evaluated matrices and in rendered
    # pixels, none of which exist without a real Blender.
    #
    # COLLECTION MEMBERSHIP IS NOT LIKE THAT. It is pure data: a name-keyed set of links, a tree of
    # children, and four booleans. Nothing is evaluated, so a stub that models linking honestly can
    # answer the real question - is the object IN there afterwards, and is the collection reachable
    # from the scene - rather than only "was the refusal raised". These are the first genuine
    # postcondition checks in this file, and they are marked as such so nobody generalises from them
    # to the families where the same thing is impossible.
    print("      (membership is pure data, so these assert real outcomes - not just refusals)")
    from MifBlender import ops_collection as OC
    _bpy = sys.modules["bpy"]
    bpy_data_collections = lambda: _bpy.data.collections
    bpy_object = lambda n: _bpy.data.objects[n]

    root = install_collection_stub()

    res, err = succeeds(OC.op_create_collection, {"name": "Lit", "objects": ["Cube"]})
    check("B113 create_collection LINKS by default - an unlinked collection is invisible and "
          "renders nothing, and that is what the bare API call produces",
          res.get("inScene") is True and res.get("objectCount") == 1, err or "got %s" % res)
    check("B113 and the object is genuinely in it afterwards, measured on the collection",
          "Cube" in bpy_data_collections().get("Lit").objects, "not linked")

    ok, msg = refuses(OC.op_create_collection, {"name": "Lit"}, "already exists", "link_objects")
    check("B113 a duplicate name is refused and points at link_objects", ok, msg)

    res, err = succeeds(OC.op_create_collection, {"name": "Orphan", "link": False})
    check("B113 link:false is honoured AND reported as inScene:false with a note - the inert state "
          "named rather than left to be discovered at render time",
          res.get("inScene") is False and "NO scene" in (res.get("note") or ""),
          err or "got %s" % res)

    ok, msg = refuses(OC.op_create_collection, {"name": "Both", "parent": "Lit", "link": False},
                      "parent", "not both")
    check("B113 parent with link:false is refused - an unlinked collection has no parent", ok, msg)

    # LINKING, AND THE move:true CASE THAT HAS TO REACH THE SCENE ROOT TOO. The root collection is
    # not in bpy.data.collections, so a move that only walked that store would leave the object
    # linked at the top level while reporting it moved.
    root.objects.link(bpy_object("Lamp"))
    res, err = succeeds(OC.op_link_objects, {"collection": "Lit", "object": "Lamp", "move": True})
    check("B113 move:true unlinks from the SCENE ROOT as well - it is a collection but is not in "
          "bpy.data.collections, so a partial move would report a false success",
          "Lamp" not in root.objects and "Lamp" in bpy_data_collections().get("Lit").objects,
          err or "movedFrom=%s rootHas=%s" % (res.get("movedFrom"), "Lamp" in root.objects))

    res, err = succeeds(OC.op_link_objects, {"collection": "Lit", "object": "Lamp"})
    check("B113 linking something already there is reported as alreadyPresent, not as a new link "
          "- and does not raise, which real Blender's link() does on a duplicate",
          res.get("alreadyPresent") == ["Lamp"] and res.get("linked") == [], err or "got %s" % res)

    ok, msg = refuses(OC.op_link_objects, {"collection": "Lit"}, "objects", "required")
    check("B113 link_objects with no objects is refused rather than silently doing nothing", ok, msg)

    ok, msg = refuses(OC.op_link_objects, {"collection": "NoSuchColl", "object": "Cube"},
                      "no collection named")
    check("B113 an unknown collection is refused and the message lists what exists", ok, msg)

    # THE REFUSAL THIS MODULE EXISTS FOR. An object in zero collections is not deleted - it is in no
    # scene, invisible everywhere, and it survives the save with nothing to warn anybody.
    ok, msg = refuses(OC.op_unlink_objects, {"collection": "Lit", "object": "Cube"},
                      "NO collection", "allowOrphans")
    check("B113 unlinking the last home of an object is REFUSED - it would exist in no scene, "
          "invisible and unwarned, and survive the save", ok, msg)
    check("B113 and the refusal left the object where it was - a refusal must fire before the "
          "mutation, not partway through the list",
          "Cube" in bpy_data_collections().get("Lit").objects, "Cube was unlinked anyway")

    res, err = succeeds(OC.op_unlink_objects,
                        {"collection": "Lit", "object": "Cube", "allowOrphans": True})
    check("B113 allowOrphans permits it and NAMES what was stranded",
          res.get("nowInNoCollection") == ["Cube"] and res.get("objectCount") == 1,
          err or "got %s" % res)

    ok, msg = refuses(OC.op_unlink_objects, {"collection": "Lit", "object": "Cube"},
                      "not in", "to begin with")
    check("B113 unlinking something that was never in there is refused, not treated as a no-op",
          ok, msg)

    # VISIBILITY: four flags in two places, and the per-layer ones need a LayerCollection.
    ok, msg = refuses(OC.op_set_collection_visibility, {"collection": "Lit"}, "nothing to do")
    check("B113 set_collection_visibility with no flags is refused", ok, msg)

    ok, msg = refuses(OC.op_set_collection_visibility, {"collection": "Orphan", "exclude": True},
                      "not in view layer", "LayerCollection")
    check("B113 a per-view-layer write on a collection that is not IN the view layer is refused - "
          "there is no LayerCollection to write to, which is why excluding an orphan does nothing",
          ok, msg)

    res, err = succeeds(OC.op_set_collection_visibility,
                        {"collection": "Lit", "exclude": True, "hideRender": True})
    check("B113 a per-layer flag and a global flag land on their DIFFERENT datablocks and both "
          "read back - writing the wrong one is a silent no-op that looks like success",
          res.get("exclude") is True and res.get("hideRender") is True
          and res.get("perViewLayerWrites") == ["exclude"]
          and res.get("globalWrites") == ["hideRender"], err or "got %s" % res)
    check("B113 and excluding is called out as leaving the depsgraph, not as hiding",
          res.get("excludedFromEvaluation") is True and "depsgraph" in (res.get("note") or ""),
          "got %s" % res.get("note"))

    # DELETION, and the rehoming that stops it stranding anything.
    succeeds(OC.op_create_collection, {"name": "Doomed", "objects": ["Cam"]})
    res, err = succeeds(OC.op_delete_collection, {"collection": "Doomed"})
    check("B113 deleting a collection REHOMES the objects that would otherwise be stranded - "
          "bpy.data.collections.remove leaves them in no scene at all",
          res.get("objectsRehomed") == ["Cam"] and "Cam" in root.objects, err or "got %s" % res)
    check("B113 and it reports nothing left in no collection afterwards",
          res.get("objectsInNoCollection") == ["Cube"], "got %s" % res.get("objectsInNoCollection"))

    ok, msg = refuses(OC.op_delete_collection, {"collection": "Lit", "reparentTo": "Lit"},
                      "reparentTo names the collection being deleted")
    check("B113 reparenting into the collection being deleted is refused", ok, msg)

    res, err = succeeds(OC.op_list_collections, {})
    check("B113 list_collections names the orphans and the homeless objects - both are invisible "
          "everywhere while every other field reads perfectly",
          res.get("orphanCollections") == ["Orphan"] and "Cube" in res.get("objectsInNoCollection"),
          "got %s" % res)

    print("")
    print("=== B114: world_info's link-vs-node logic - pure graph walking, so it is checkable ===")
    # THE SECOND FAMILY WHOSE LOGIC IS REAL DATA rather than evaluation. A shader tree is nodes and
    # links; walking it needs no depsgraph and no render, so these assert what the walk actually
    # FINDS rather than only that a refusal fired.
    #
    # WHAT THEY GUARD. set_world reported `hasEnvironmentTexture` by asking whether a
    # TEX_ENVIRONMENT node existed anywhere in the tree. Its own flat-colour branch removes the
    # LINK into Background.Color and leaves the node behind, so once an HDRI had ever been set that
    # field stayed true forever - reporting an environment in play while the render used the flat
    # colour. In a node tree the effect lives on the link, and that is the whole point of this walk.
    from MifBlender import ops_world as OW

    class _S(object):
        def __init__(self, name):
            self.name = name
            self.default_value = [0.0, 0.0, 0.0, 1.0]

    class _N(object):
        def __init__(self, kind, name, inputs=()):
            self.type = kind
            self.name = name
            self.inputs = [_S(i) for i in inputs]
            self.outputs = [_S("out")]

        def sock(self, name):
            return next(s for s in self.inputs if s.name == name)

    class _L(object):
        def __init__(self, fn, tn, ts):
            self.from_node, self.to_node, self.to_socket = fn, tn, ts

    class _T(object):
        def __init__(self, nodes, links):
            self.nodes, self.links = nodes, links

    tex = _N("TEX_ENVIRONMENT", "Env")
    bg = _N("BACKGROUND", "Background", ("Color", "Strength"))
    out = _N("OUTPUT_WORLD", "World Output", ("Surface",))

    direct = _T([tex, bg, out], [_L(tex, bg, bg.sock("Color")),
                                 _L(bg, out, out.sock("Surface"))])
    check("B114 a directly linked environment texture is found",
          OW._trace_to_texture(direct, bg.sock("Color")) is tex, "not found")

    # THROUGH A MAPPING NODE - the case a naive "is it linked directly" test gets wrong, and a
    # perfectly ordinary graph: set_world itself inserts Mapping and TexCoord when given a rotation.
    mapping = _N("MAPPING", "Mapping", ("Vector", "Rotation"))
    through = _T([tex, mapping, bg, out],
                 [_L(mapping, tex, tex.inputs[0] if tex.inputs else _S("x")),
                  _L(tex, bg, bg.sock("Color")), _L(bg, out, out.sock("Surface"))])
    check("B114 and one reached THROUGH a Mapping node is still found - set_world inserts exactly "
          "that pair when given a rotation, so a direct-link test would answer no on its own output",
          OW._trace_to_texture(through, bg.sock("Color")) is tex, "not found through Mapping")

    # THE ACTUAL BUG. Node present, link gone - which is the state set_world's flat-colour branch
    # leaves behind, and the state the old field reported as an environment in play.
    unlinked = _T([tex, bg, out], [_L(bg, out, out.sock("Surface"))])
    check("B114 a texture PRESENT but not linked is correctly NOT driving - the exact state "
          "set_world leaves after replacing an HDRI with a flat colour",
          OW._trace_to_texture(unlinked, bg.sock("Color")) is None, "reported as driving")

    # A CYCLE MUST TERMINATE. Blender's UI will not build one, but this walks files the addon did
    # not author, and a reader that hangs is worse than one that gives up.
    #
    # THIS CHECK PASSES WITH THE SEEN-SET REMOVED, which was found by planting exactly that and is
    # worth stating rather than quietly leaving a check that looks like it guards more than it
    # does. Termination comes from the DEPTH LIMIT; the seen-set only stops a diamond graph being
    # re-walked once per path. The assertion - that this returns rather than hanging or blowing the
    # stack - is real and still worth holding. What it is not is a test of the seen-set.
    a = _N("MIX", "A", ("In",))
    b = _N("MIX", "B", ("In",))
    cyc = _T([a, b, bg], [_L(b, a, a.sock("In")), _L(a, b, b.sock("In")),
                          _L(a, bg, bg.sock("Color"))])
    res, err = succeeds(lambda _: OW._trace_to_texture(cyc, bg.sock("Color")), None)
    check("B114 a cyclic graph terminates instead of hanging or overflowing", err is None, err)

    # _find_background: the OTHER link-vs-node question, on the Background node itself.
    node, connected = OW._find_background(types.SimpleNamespace(node_tree=direct))
    check("B114 a Background wired to the world output reports connected",
          node is bg and connected is True, "got %s/%s" % (node, connected))

    stray = _T([bg, out], [])
    node, connected = OW._find_background(types.SimpleNamespace(node_tree=stray))
    check("B114 a Background node NOT wired to the output is found but reports connected:false - "
          "it accepts every write and changes no light",
          node is bg and connected is False, "got %s/%s" % (node, connected))

    empty = _T([out], [])
    node, connected = OW._find_background(types.SimpleNamespace(node_tree=empty))
    check("B114 a tree with no Background node at all answers None rather than raising",
          node is None and connected is False, "got %s/%s" % (node, connected))

    noout = _T([bg], [])
    check("B114 a tree with no world output answers None for its surface source",
          OW._surface_source(types.SimpleNamespace(node_tree=noout)) is None, "not None")

    # AND THE READER MUST NOT AUTHOR NODES. _background_node creates a Background and wires it when
    # the tree has none, which is right for a setter and disqualifying for a reader: an info op that
    # silently makes two nodes describes a world it just invented.
    before = len(empty.nodes)
    OW._find_background(types.SimpleNamespace(node_tree=empty))
    check("B114 _find_background is PURE - it created nothing, unlike _background_node which the "
          "setters use", len(empty.nodes) == before, "node count moved %d -> %d"
          % (before, len(empty.nodes)))

    print("")
    print("=== B115: physics_info - the cache traps and the rigid body that never simulates ===")
    # PURE DATA AGAIN. Cache state and RigidBodyWorld membership are stored values and a set
    # comparison; nothing here needs the sim to run. So these assert the DIAGNOSES, which is what
    # the op exists for - the settings themselves are the part that already read back fine and told
    # nobody anything useful.
    from MifBlender import ops_physics as OP

    def _cache(baked, start, end):
        return types.SimpleNamespace(is_baked=baked, frame_start=start, frame_end=end)

    sc = sys.modules["bpy"].context.scene
    sc.frame_start, sc.frame_end = 1, 250

    check("B115 no point cache answers None rather than a row of falsehoods",
          OP._cache_row(None, sc) is None, "not None")

    row = OP._cache_row(_cache(True, 1, 250), sc)
    check("B115 a baked cache covering the scene range is reported as covering",
          row["isBaked"] is True and row["coversSceneRange"] is True, "got %s" % row)

    # THE STALE-BAKE TRAP, and the reason this comparison is made in the op rather than left to a
    # caller: is_baked stays TRUE on a cache baked before the range was extended. It is baked, it is
    # valid, and it is short, and the frames past its end fall back to the rest state in silence.
    row = OP._cache_row(_cache(True, 1, 100), sc)
    check("B115 a cache BAKED BEFORE the range was extended is baked AND short - is_baked stays "
          "true and the frames past its end silently fall back to the rest state",
          row["isBaked"] is True and row["coversSceneRange"] is False, "got %s" % row)

    # THE INERT RIGID BODY. Every setting correct, and it hangs in the air because the sim only
    # acts on objects inside the RigidBodyWorld's collection.
    rb = types.SimpleNamespace(type="ACTIVE", mass=1.0, friction=0.5, restitution=0.0,
                               collision_shape="CONVEX_HULL", kinematic=False,
                               collision_margin=0.04, linear_damping=0.04,
                               angular_damping=0.1, enabled=True)
    cube = sys.modules["bpy"].data.objects["Cube"]
    cube.rigid_body = rb
    cube.modifiers = []
    empty_coll = types.SimpleNamespace(objects=[], name="RigidBodyWorld")
    sc.rigidbody_world = types.SimpleNamespace(
        collection=empty_coll, enabled=True, substeps_per_frame=10, solver_iterations=10,
        point_cache=_cache(True, 1, 250))

    res, err = succeeds(OP.op_physics_info, {"object": "Cube"})
    row = (res.get("objects") or [{}])[0]
    check("B115 a fully configured rigid body OUTSIDE the RigidBodyWorld collection reports "
          "inSimulation:false - every field on it reads perfectly and it never falls",
          row.get("inSimulation") is False, err or "got %s" % row)
    check("B115 and that is raised as a blocker naming the hang-in-the-air outcome, not left to "
          "be inferred from a false boolean",
          any("hangs in the air" in b for b in res.get("blockers", [])),
          err or "got %s" % res.get("blockers"))
    check("B115 readyToRender is false while a blocker stands",
          res.get("readyToRender") is False, err or "got %s" % res.get("readyToRender"))

    sc.rigidbody_world.collection = types.SimpleNamespace(objects=[cube], name="RigidBodyWorld")
    res, err = succeeds(OP.op_physics_info, {"object": "Cube"})
    row = (res.get("objects") or [{}])[0]
    check("B115 putting it in the collection flips inSimulation and clears that blocker",
          row.get("inSimulation") is True
          and not any("hangs in the air" in b for b in res.get("blockers", [])),
          err or "got %s / %s" % (row.get("inSimulation"), res.get("blockers")))

    # ACTIVE + KINEMATIC: the usual accident behind "my keyframed object will not fall".
    rb.kinematic = True
    res, err = succeeds(OP.op_physics_info, {"object": "Cube"})
    check("B115 an ACTIVE rigid body with kinematic ON is called out - it is driven by its "
          "animation, not the sim, which is why a keyframed object refuses to fall",
          any("kinematic" in b for b in res.get("blockers", [])),
          err or "got %s" % res.get("blockers"))
    rb.kinematic = False

    sc.rigidbody_world.point_cache = _cache(False, 1, 250)
    res, err = succeeds(OP.op_physics_info, {"object": "Cube"})
    check("B115 an UNBAKED cache is called out with why it matters - a late frame shows the REST "
          "state and a render of it is simply wrong",
          any("NOT baked" in b for b in res.get("blockers", [])),
          err or "got %s" % res.get("blockers"))

    sc.rigidbody_world.point_cache = _cache(True, 1, 100)
    res, err = succeeds(OP.op_physics_info, {"object": "Cube"})
    check("B115 a baked-but-short cache is a DIFFERENT blocker from an unbaked one - conflating "
          "them would tell somebody to re-bake without saying the range is the problem",
          any("does NOT cover" in b for b in res.get("blockers", []))
          and not any("NOT baked" in b for b in res.get("blockers", [])),
          err or "got %s" % res.get("blockers"))

    del cube.rigid_body
    del cube.modifiers

    print("")
    print("=== B116: the compositor - four ways to be ON and doing nothing ===")
    # THE SUBSYSTEM WAS UNREACHABLE. create_node_group could make a CompositorNodeTree, but that is
    # a node GROUP in bpy.data.node_groups; the scene's compositor is scene.node_tree, a different
    # tree nothing in the module could address. Every blocker below is a state where the tree reads
    # perfectly and the rendered file is unprocessed - which is why the read op is the point, not a
    # convenience beside the authoring ops.
    from MifBlender import ops_nodes as ON

    class _CN(object):
        def __init__(self, kind, name, mute=False):
            self.bl_idname, self.name, self.label, self.mute = kind, name, "", mute
            self.inputs, self.outputs = [], []

    class _CL(object):
        def __init__(self, fn, tn):
            self.from_node, self.to_node = fn, tn

    class _CT(object):
        def __init__(self, kind, nodes, links, name="Compositing"):
            self.bl_idname, self.nodes, self.links, self.name = kind, nodes, links, name

    # THE TERMINAL-PER-TREE-TYPE FIX. list_group_nodes looked only for NodeGroupOutput, so aimed at
    # a compositor it would have called a correctly wired tree inert.
    terms, note = ON._terminals(_CT("CompositorNodeTree", [], []))
    check("B116 a compositor tree's terminal is the Composite node, not a Group Output - the old "
          "code would have called a correctly wired compositor inert",
          "CompositorNodeComposite" in terms and "Composite node" in note, "got %s / %s" % (terms, note))
    terms, note = ON._terminals(_CT("GeometryNodeTree", [], []))
    check("B116 and a geometry tree still terminates at the Group Output, with its own wording",
          terms == ("NodeGroupOutput",) and "geometry through UNCHANGED" in note,
          "got %s / %s" % (terms, note))

    bpy = sys.modules["bpy"]
    sc = bpy.context.scene
    sc.render.use_compositing = True
    sc.render.use_sequencer = False
    sc.sequence_editor = None
    sc.use_nodes = False
    sc.node_tree = None

    # THE RESOLVER MUST REFUSE, NOT ENABLE. Turning use_nodes on from a lookup would mean a read op
    # silently switched compositing on for the whole scene.
    ok, msg = refuses(lambda _: ON._scene_tree(), None, "use_nodes is off", "set_compositing")
    check("B116 addressing the compositor while use_nodes is off is refused and points at "
          "set_compositing", ok, msg)
    check("B116 and the refusal did NOT switch compositing on behind the caller's back",
          sc.use_nodes is False, "use_nodes became %s" % sc.use_nodes)

    ok, msg = refuses(ON.op_create_node_group, {"name": ON.SCENE_COMPOSITOR}, "reserved")
    check("B116 a node group cannot be created under the reserved compositor name - a precedence "
          "rule would make which tree you addressed depend on what happened to exist", ok, msg)

    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 use_nodes off is reported as a blocker saying the render is written unprocessed",
          any("use_nodes is OFF" in b for b in res.get("blockers", [])), err or "got %s" % res)

    # THE CLASSIC. Tree on, pipeline off: the backdrop updates and the file is untouched.
    rl, comp = _CN("CompositorNodeRLayers", "Render Layers"), _CN("CompositorNodeComposite", "Composite")
    sc.use_nodes = True
    sc.node_tree = _CT("CompositorNodeTree", [rl, comp], [_CL(rl, comp)])
    sc.render.use_compositing = False
    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 a correctly wired tree with use_compositing OFF is still a blocker - two "
          "independent switches, and this is the one that is usually missed",
          any("use_compositing is OFF" in b for b in res.get("blockers", []))
          and res.get("compositorAffectsRender") is False, err or "got %s" % res.get("blockers"))

    sc.render.use_compositing = True
    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 and with both switches on and the tree wired, there are no blockers left",
          res.get("blockers") == [] and res.get("compositorAffectsRender") is True,
          err or "got %s" % res.get("blockers"))

    # A VIEWER IS NOT A COMPOSITE. This is why "it looks right in the compositor" and the file is
    # wrong - the Viewer feeds the backdrop only.
    viewer = _CN("CompositorNodeViewer", "Viewer")
    sc.node_tree = _CT("CompositorNodeTree", [rl, viewer], [_CL(rl, viewer)])
    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 a tree wired only to a VIEWER is a blocker that names the backdrop - the reason it "
          "looks right in the compositor while the saved file is wrong",
          any("Viewer" in b and "backdrop" in b for b in res.get("blockers", [])),
          err or "got %s" % res.get("blockers"))

    sc.node_tree = _CT("CompositorNodeTree", [rl, comp], [])
    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 a Composite node with nothing linked into it is a DIFFERENT blocker from having no "
          "Composite node - one needs wiring, the other needs a node",
          any("nothing is linked into it" in b for b in res.get("blockers", [])),
          err or "got %s" % res.get("blockers"))

    muted = _CN("CompositorNodeGlare", "Glare", mute=True)
    sc.node_tree = _CT("CompositorNodeTree", [rl, muted, comp], [_CL(rl, muted), _CL(muted, comp)])
    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 a MUTED node is reported - it passes its input straight through while sitting in "
          "the graph looking applied",
          res.get("mutedNodes") == ["Glare"]
          and any("MUTED" in b for b in res.get("blockers", [])), err or "got %s" % res)

    # THE VSE OVERRIDE, and the judgement that an EMPTY sequencer is not a blocker - calling the
    # normal case a problem is how a blocker list gets ignored.
    sc.node_tree = _CT("CompositorNodeTree", [rl, comp], [_CL(rl, comp)])
    sc.render.use_sequencer = True
    sc.sequence_editor = None
    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 use_sequencer on with NO strips is not a blocker - the normal case, and calling it "
          "one would train people to ignore the list",
          res.get("blockers") == [], err or "got %s" % res.get("blockers"))
    sc.sequence_editor = types.SimpleNamespace(sequences_all=[1, 2])
    res, err = succeeds(ON.op_compositor_info, {})
    check("B116 use_sequencer WITH strips is a blocker - the VSE runs after the compositor and its "
          "output is what gets written",
          any("VSE holds 2 strip" in b for b in res.get("blockers", [])),
          err or "got %s" % res.get("blockers"))
    sc.render.use_sequencer = False

    print("")
    print("=== B117: colour management validates against THIS config, not a remembered list ===")
    # THE DIFFERENCE THAT MAKES THIS WORTH TESTING. Every other enum in the addon is checked against
    # bpy.types.X.bl_rna - light type, camera type, constraint type - because those sets are fixed
    # by the build. Colour management is not: the OCIO config populates view_transform, look and
    # display_device at RUNTIME. A hard-coded list would refuse the only values that work on a
    # studio config, and Blender's own default moved from Filmic to AgX in 4.0.
    from MifBlender import ops_render as ORD

    class _Enum2(object):
        def __init__(self, ids):
            self.enum_items = [types.SimpleNamespace(identifier=i) for i in ids]

    class _Props(object):
        def __init__(self, m):
            self._m = m

        def __getitem__(self, k):
            return _Enum2(self._m[k])

    class _VS(object):
        """A view_settings that behaves like Blender's in the TWO ways that matter here.

        The look list depends on the current transform, AND ASSIGNING A TRANSFORM SILENTLY RESETS
        THE LOOK to None. The second was added after a ground-truth plant proved the read-back check
        was untestable without it: with a stub where every assignment simply sticks, deleting that
        check changed nothing and the plant went uncaught. Modelling the real coercion is what gives
        the guard something to catch - and it is the coercion the guard was written for.
        """

        def __init__(self):
            self._vt = "AgX"
            self.look = "None"
            self.exposure = 0.0
            self.gamma = 1.0
            self.use_curve_mapping = False

        @property
        def view_transform(self):
            return self._vt

        @view_transform.setter
        def view_transform(self, value):
            if value != self._vt:
                self.look = "None"        # Blender drops a look that belongs to another transform
            self._vt = value

        @property
        def bl_rna(self):
            looks = {"AgX": ["None", "AgX - Punchy", "AgX - Greyscale"],
                     "Filmic": ["None", "Filmic - High Contrast"],
                     "Standard": ["None"]}
            return types.SimpleNamespace(properties=_Props({
                "view_transform": ["Standard", "Filmic", "AgX", "StudioLookXYZ"],
                "look": looks.get(self.view_transform, ["None"])}))

    bpy = sys.modules["bpy"]
    sc = bpy.context.scene
    vs = _VS()
    sc.view_settings = vs
    sc.display_settings = types.SimpleNamespace(display_device="sRGB")
    sc.display_settings.bl_rna = types.SimpleNamespace(
        properties=_Props({"display_device": ["sRGB", "Rec.1886", "StudioDisplayXYZ"]}))
    sc.sequencer_colorspace_settings = None

    check("B117 the available set is read from the INSTANCE, so a config-specific name is offered",
          "StudioLookXYZ" in (ORD.enum_ids(vs, "view_transform") or set()),
          "got %s" % ORD.enum_ids(vs, "view_transform"))

    ok, msg = refuses(ORD.op_set_color_management, {"viewTransform": "Filmick"},
                      "not a view transform", "OCIO config actually loaded")
    check("B117 an unknown view transform is refused and the message says the list came from the "
          "loaded config rather than a remembered one", ok, msg)

    ok, msg = refuses(ORD.op_set_color_management, {}, "nothing to do")
    check("B117 a call with no settings at all is refused", ok, msg)

    # THE ORDERING RULE. "Filmic - High Contrast" is not offered while the transform is AgX, so a
    # version that validated the look against the OLD transform would refuse a legal combination.
    res, err = succeeds(ORD.op_set_color_management,
                        {"viewTransform": "Filmic", "look": "Filmic - High Contrast"})
    check("B117 a transform and a look set TOGETHER succeed - the look is validated against what "
          "the NEW transform offers, not the one in force on entry",
          res.get("viewTransform") == "Filmic" and res.get("look") == "Filmic - High Contrast",
          err or "got %s" % res)

    # AND THE CONVERSE: a look that belongs to a DIFFERENT transform must still be refused.
    ok, msg = refuses(ORD.op_set_color_management,
                      {"viewTransform": "Standard", "look": "AgX - Punchy"}, "not a look")
    check("B117 but a look the new transform does NOT offer is still refused - the ordering fix "
          "must not become an excuse to accept anything", ok, msg)

    # THE COERCION THE READ-BACK GUARD EXISTS FOR. Blender drops the look when the transform
    # changes, so an op that set the look FIRST would apply it, have it silently wiped, and return a
    # success built from the values it was handed. Order plus read-back is what makes this survive.
    #
    # WHAT PLANTING PROVED, stated because it is not what was expected. Removing the op's read-back
    # guard ALONE does not fail this - the response reads `after` from the scene either way, so it
    # stays honest. Removing the ORDERING alone fails three checks. Removing BOTH fails the same
    # three, and that is the case the guard is really for: the op would answer look:'None', ok:true,
    # to a caller who asked for something else. So this covers the outcome, and the guard by itself
    # is defence in depth rather than something a single plant can isolate.
    vs.view_transform = "AgX"
    vs.look = "AgX - Punchy"
    res, err = succeeds(ORD.op_set_color_management,
                        {"viewTransform": "Filmic", "look": "Filmic - High Contrast"})
    check("B117 the look SURVIVES a transform change in the same call - Blender wipes the look "
          "when the transform moves, so this only holds because the transform is applied first and "
          "the result is read back from the scene rather than echoed",
          res.get("look") == "Filmic - High Contrast" and vs.look == "Filmic - High Contrast",
          err or "response=%s scene=%s" % (res.get("look"), vs.look))

    ok, msg = refuses(ORD.op_set_color_management, {"displayDevice": "Rec.709"},
                      "not a display device")
    check("B117 an unknown display device is refused against the config's own list", ok, msg)

    res, err = succeeds(ORD.op_set_color_management, {"exposure": -1.5, "gamma": 1.2})
    check("B117 exposure and gamma are plain floats and are read back from the scene",
          res.get("exposure") == -1.5 and res.get("gamma") == 1.2, err or "got %s" % res)
    check("B117 and the response carries the config's available lists so a caller can recover from "
          "a refusal without guessing",
          "StudioLookXYZ" in (res.get("availableViewTransforms") or []),
          err or "got %s" % res.get("availableViewTransforms"))

    print("")
    print("=== B118: view layers and passes - what the compositor is allowed to see ===")
    # THE ASYMMETRY THIS CLOSES, facing the other way from world and physics: compositor_info could
    # READ which passes were enabled and nothing could turn one on. It matters more than a missing
    # setter usually does, because a Render Layers node only offers sockets for passes the layer
    # actually outputs - ask for a Z-depth composite with the Z pass off and there is nothing to
    # connect. The compositing ops shipped without the thing that decides what they can see.
    from MifBlender import ops_viewlayer as OV

    class _VL2(object):
        """A view layer with a READ-ONLY pass, which is the case the read-back guard exists for.

        Several use_pass_* properties are read-only under a given engine: the property exists, the
        assignment is accepted, and the value does not move. A response built from what was asked
        for would report a pass that is still off.
        """

        READONLY = "use_pass_transmission_direct"

        def __init__(self, name, use=True):
            self.name, self.use, self.samples = name, use, 0
            for p in ("combined", "z", "mist", "normal", "cryptomatte_object",
                      "transmission_direct"):
                object.__setattr__(self, _p(p), p == "combined")

        def __setattr__(self, key, value):
            if key == self.READONLY:
                return                       # accepted and ignored, exactly as Blender does
            object.__setattr__(self, key, value)

    class _VLs(object):
        def __init__(self, layers):
            self._d = {v.name: v for v in layers}

        def get(self, k, default=None):
            return self._d.get(k, default)

        def new(self, name):
            v = _VL2(name)
            self._d[name] = v
            return v

        def remove(self, v):
            self._d.pop(v.name, None)

        def __iter__(self):
            return iter(list(self._d.values()))

        def __len__(self):
            return len(self._d)

    bpy = sys.modules["bpy"]
    sc = bpy.context.scene
    main = _VL2("ViewLayer")
    sc.view_layers = _VLs([main])
    bpy.context.view_layer = main
    sc.render.engine = "CYCLES"

    check("B118 the pass set is ENUMERATED from the layer, not listed in the addon - the set "
          "differs by version and engine, so a constant would refuse passes that exist",
          set(OV._pass_names(main)) >= {"z", "mist", "cryptomatte_object"},
          "got %s" % sorted(OV._pass_names(main)))

    res, err = succeeds(OV.op_set_view_layer, {"enablePasses": ["z", "mist"]})
    check("B118 enabling passes works and is read back from the layer",
          set(res.get("enabledPasses", [])) >= {"z", "mist"}, err or "got %s" % res)

    ok, msg = refuses(OV.op_set_view_layer, {"enablePasses": ["z", "zz_not_a_pass"]},
                      "no pass named", "use_pass_ prefix")
    check("B118 an unknown pass name is refused with the available list and the prefix rule",
          ok, msg)
    check("B118 and the refusal is checked across ALL names BEFORE any write - a typo in the "
          "second name must not leave the first one changed",
          OV._pass_names(main).get("normal") is False, "normal was changed anyway")

    # THE READ-ONLY PASS. The write is accepted and the value does not move, so an op that echoed
    # the request would report a pass that is still off.
    ok, msg = refuses(OV.op_set_view_layer, {"enablePasses": ["transmission_direct"]},
                      "reads back", "read-only")
    check("B118 a pass that is READ-ONLY under this engine is caught by the read-back - the write "
          "is accepted, the value does not move, and echoing the request would report success",
          ok, msg)

    res, err = succeeds(OV.op_set_view_layer, {"passes": {"mist": False, "normal": True}})
    check("B118 the passes map works as an alternative to the two lists",
          "normal" in res.get("enabledPasses", []) and "mist" not in res.get("enabledPasses", []),
          err or "got %s" % res.get("enabledPasses"))

    ok, msg = refuses(OV.op_set_view_layer, {}, "nothing to do")
    check("B118 a call that changes nothing is refused rather than reporting success", ok, msg)

    ok, msg = refuses(OV.op_set_view_layer, {"enablePasses": "z"}, "must be a list")
    check("B118 a bare string where a list is wanted is refused by type", ok, msg)

    # use OFF: the inert shape again - everything reads perfectly and no pixel is produced.
    res, err = succeeds(OV.op_set_view_layer, {"use": False})
    check("B118 use:false is reported as a BLOCKER, not left as one boolean among many - the layer "
          "is not rendered at all while every pass on it still reads back correctly",
          res.get("renders") is False
          and any("not rendered at all" in b for b in res.get("blockers", [])),
          err or "got %s" % res)

    res, err = succeeds(OV.op_list_view_layers, {})
    check("B118 list_view_layers names the layers that are not rendering",
          res.get("notRendering") == ["ViewLayer"], err or "got %s" % res)

    # CREATE, and the copyFrom that exists because Blender's new layers start from defaults.
    main.use = True
    OV.op_set_view_layer({"enablePasses": ["z", "mist"]})
    res, err = succeeds(OV.op_create_view_layer, {"name": "FG", "copyFrom": "ViewLayer"})
    check("B118 copyFrom carries the enabled passes to the new layer - Blender's own new layers "
          "start from defaults, which is rarely what somebody splitting a shot wants",
          set(res.get("enabledPasses", [])) >= {"z", "mist"}, err or "got %s" % res)

    ok, msg = refuses(OV.op_create_view_layer, {"name": "FG"}, "already has a view layer")
    check("B118 a duplicate view layer name is refused", ok, msg)

    res, err = succeeds(OV.op_delete_view_layer, {"name": "FG"})
    check("B118 deleting one works and reports what is left",
          res.get("remaining") == ["ViewLayer"], err or "got %s" % res)

    ok, msg = refuses(OV.op_delete_view_layer, {"name": "ViewLayer"}, "only view layer",
                      "cannot be rendered")
    check("B118 deleting the LAST view layer is refused - a scene with none cannot render at all, "
          "and the API will happily let you get there", ok, msg)

    print("")
    print("=== B119: creating the object types that existing ops already DEPEND on ===")
    # FOUND BY ASKING WHAT THE ADDON CONSUMES AND CANNOT PRODUCE, the same question that turned up
    # collections. add_constraint and aim_object both take a TARGET and nothing could create an
    # Empty, which is what people overwhelmingly use as one. add_constraint accepts FOLLOW_PATH and
    # nothing could make a curve. ops_rig has twelve ops and not one creates an armature, so the
    # whole rigging family could only edit rigs somebody else had made.
    #
    # WHAT THESE CHECKS ARE FOR. Building an object from datablocks rather than through bpy.ops
    # means the failure mode is a half-made thing left behind, so every refusal here has to fire
    # BEFORE the first datablock exists. The stub has no .new() on bpy.data.curves or .armatures,
    # which makes that measurable for free: a validation that ran too late shows up as an
    # AttributeError rather than a MifOpError, and refuses() reports the difference.
    from MifBlender import ops_create as OCR

    ok, msg = refuses(OCR.op_create_empty, {"displayType": "SQUIGGLE"}, "not one Blender offers")
    check("B119 an unknown Empty display type is refused with the valid list", ok, msg)

    ok, msg = refuses(OCR.op_create_curve, {"points": [[0, 0, 0]]}, "at least 2", "no length")
    check("B119 a curve with one point is refused - nothing can follow a curve with no length",
          ok, msg)
    ok, msg = refuses(OCR.op_create_curve, {"points": [[0, 0, 0], [1, 1]]}, "must be [x,y,z]")
    check("B119 a malformed point is refused naming its index", ok, msg)
    ok, msg = refuses(OCR.op_create_curve,
                      {"points": [[0, 0, 0], [1, 0, 0]], "splineType": "CATMULL"},
                      "POLY, BEZIER or NURBS")
    check("B119 an unknown spline type is refused BEFORE the curve datablock is made - POLY and "
          "BEZIER points live in different collections, so a wrong type builds an empty spline "
          "and raises nothing", ok, msg)

    ok, msg = refuses(OCR.op_create_text, {"body": ""}, "renders nothing")
    check("B119 an empty text body is refused - such an object exists perfectly and renders "
          "nothing", ok, msg)

    # THE ARMATURE VALIDATIONS. Each of these has to fire before the mode change, because a refusal
    # partway through leaves an armature with half its bones AND Blender stuck in an editor.
    ok, msg = refuses(OCR.op_create_armature, {"bones": [{"head": [0, 0, 0], "tail": [0, 0, 1]}]},
                      "needs a 'name'")
    check("B119 a bone with no name is refused", ok, msg)
    ok, msg = refuses(OCR.op_create_armature,
                      {"bones": [{"name": "a", "head": [0, 0, 0]}]}, "'tail'", "[x,y,z]")
    check("B119 a bone missing an endpoint is refused naming which one", ok, msg)
    ok, msg = refuses(OCR.op_create_armature,
                      {"bones": [{"name": "a", "head": [0, 0, 0], "tail": [0, 0, 1]},
                                 {"name": "a", "head": [0, 0, 1], "tail": [0, 0, 2]}]},
                      "repeats the name")
    check("B119 a duplicate bone name is refused", ok, msg)
    ok, msg = refuses(OCR.op_create_armature,
                      {"bones": [{"name": "child", "head": [0, 0, 0], "tail": [0, 0, 1],
                                  "parent": "root"},
                                 {"name": "root", "head": [0, 0, 1], "tail": [0, 0, 2]}]},
                      "not defined ABOVE it", "Parents must come first")
    check("B119 a bone naming a parent defined BELOW it is refused - edit_bones resolves parents "
          "by lookup, so a forward reference is a KeyError deep inside a mode switch", ok, msg)
    ok, msg = refuses(OCR.op_create_armature, {"bones": "not a list"}, "must be a list")
    check("B119 a non-list bones parameter is refused by type", ok, msg)

    # AND ALL OF THEM BEFORE THE MODE CHANGE. Every refusal above was reported as a MifOpError,
    # which is only possible if it ran before the code that touches bpy.data.armatures.new - the
    # stub has no such method, so reaching it raises AttributeError instead.
    check("B119 every armature refusal above fired BEFORE any datablock was created - the stub has "
          "no bpy.data.armatures.new, so a validation that ran too late would have surfaced as an "
          "AttributeError rather than a refusal",
          not hasattr(sys.modules["bpy"].data.armatures, "new"),
          "the stub grew a .new(), so this check no longer proves anything")

    # THE MODE GUARD, which protects whatever somebody is editing rather than the new armature.
    _obj = sys.modules["bpy"].data.objects["Cube"]
    _obj.mode = "EDIT"
    sys.modules["bpy"].context.object = _obj
    ok, msg = refuses(OCR.op_create_armature, {}, "EDIT mode", "NOTHING was created")
    check("B119 creating an armature from EDIT mode is refused - the mode switch it needs would "
          "drop whatever is being edited", ok, msg)
    # RESTORED, and it was not at first. Leaving Cube in EDIT mode made five B123 checks
    # fail for a reason that had nothing to do with what they test - a block that mutates
    # shared stub state and does not put it back is the harness version of an op that
    # leaves Blender in edit mode, which B119 exists to forbid.
    _obj.mode = "OBJECT"
    sys.modules["bpy"].context.object = None

    ok, msg = refuses(OCR.op_create_empty, {"collection": "NoSuchColl"}, "no collection named",
                      "create_collection")
    check("B119 creating into a collection that does not exist is refused and points at "
          "create_collection", ok, msg)

    print("")
    print("=== B120: material and world shader graphs - trees that no op could address ===")
    # THE READ HALF WAS THOROUGH AND THE WRITE HALF WAS ONE NODE DEEP. describe_material reports a
    # material's whole node tree - every node, every link, every image texture and its path - while
    # set_material_properties could write only the Principled BSDF's own socket values. So the addon
    # could DESCRIBE a shader graph in detail and not add a single node to one: no mix shaders, no
    # procedural noise, no bump or normal map wired, no UV mapping node.
    #
    # A material's tree is owned by the material, a world's by the world - neither is in
    # bpy.data.node_groups - so the five authoring ops could not reach either. Same shape as the
    # compositor and the same fix: a resolver, not a second set of add/link/list ops, because
    # add_group_node is already tree-agnostic.
    from MifBlender import ops_nodes as ON2

    class _Tree3(object):
        def __init__(self, kind):
            self.bl_idname, self.nodes, self.links, self.name = kind, [], [], "Shader Nodetree"

    class _Holder(object):
        def __init__(self, name, use_nodes=True):
            self.name, self.use_nodes = name, use_nodes
            self.node_tree = _Tree3("ShaderNodeTree") if use_nodes else None

    class _Store(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

        def __iter__(self):
            return iter(list(self.values()))

    bpy = sys.modules["bpy"]
    wood = _Holder("Wood")
    flat = _Holder("Flat", use_nodes=False)
    bpy.data.materials = _Store({"Wood": wood, "Flat": flat})
    sky = _Holder("Sky")
    bpy.data.worlds = _Store({"Sky": sky})
    bpy.context.scene.world = sky

    check("B120 'material:<name>' resolves to that material's own tree, which is not in "
          "bpy.data.node_groups and was unreachable by every authoring op",
          ON2._tree("material:Wood") is wood.node_tree, "did not resolve")
    check("B120 'world:<name>' resolves the same way",
          ON2._tree("world:Sky") is sky.node_tree, "did not resolve")
    check("B120 and 'scene:world' resolves the SCENE's world without naming it",
          ON2._tree(ON2.SCENE_WORLD) is sky.node_tree, "did not resolve")

    # use_nodes OFF is the inert case again, and the resolver REFUSES rather than enabling it -
    # turning it on as a side effect of a lookup would convert a flat-colour material into a
    # node-based one, which is a different thing to render, decided by an address.
    ok, msg = refuses(lambda _: ON2._tree("material:Flat"), None, "use_nodes OFF",
                      "nothing looks at")
    check("B120 a material with use_nodes OFF is refused, not silently switched on - adding nodes "
          "to it would author something nothing looks at", ok, msg)
    check("B120 and the refusal left use_nodes alone", flat.use_nodes is False,
          "use_nodes became %s" % flat.use_nodes)

    ok, msg = refuses(lambda _: ON2._tree("material:NoSuchMat"), None, "no material named")
    check("B120 an unknown material is refused with the list of ones that exist", ok, msg)
    ok, msg = refuses(lambda _: ON2._tree("material:"), None, "names no material")
    check("B120 a bare prefix with no name is refused rather than resolving to something arbitrary",
          ok, msg)

    bpy.context.scene.world = None
    ok, msg = refuses(lambda _: ON2._tree(ON2.SCENE_WORLD), None, "has no world", "set_world")
    check("B120 'scene:world' on a scene with no world is refused and points at set_world", ok, msg)
    bpy.context.scene.world = sky

    for reserved in ("material:Wood", "world:Sky", ON2.SCENE_WORLD, ON2.SCENE_COMPOSITOR):
        ok, msg = refuses(ON2.op_create_node_group, {"name": reserved}, "reserved")
        check("B120 a node group cannot be created as '%s' - it would make which tree you addressed "
              "depend on what happened to exist" % reserved, ok, msg)

    # A NORMAL GROUP NAME MUST STILL WORK. Without this the reservation could have swallowed
    # everything and every check above would still pass.
    ok, msg = refuses(lambda _: ON2._tree("PlainGroupName"), None, "no node group named")
    check("B120 an ordinary name still goes to bpy.data.node_groups - the negative control, "
          "without which a resolver that claimed every name would pass every check above", ok, msg)

    print("")
    print("=== B121: export formats - version drift, and the keyword that differs per exporter ===")
    # MEASURED, NOT ASSUMED: import_mesh took .fbx/.gltf/.glb and export_mesh wrote .fbx and NOTHING
    # else, so glTF could come IN and not go OUT, and USD - the interchange format of every film and
    # Omniverse pipeline - was absent in both directions. FBX is what the Unreal path needs and it
    # had become the whole of what the addon could write.
    #
    # WHAT THESE CHECK is the table, because that is where the mistakes live. Blender moved its OBJ,
    # STL and PLY exporters to wm.*_export at DIFFERENT versions, and the selection keyword differs
    # for every single exporter. Both are pure lookups and neither needs Blender.
    from MifBlender import ops_io as OIO

    bpy = sys.modules["bpy"]
    bpy.ops = types.SimpleNamespace()

    def _ops(*dotted):
        """Rebuild bpy.ops so exactly these operators exist - i.e. pick a Blender version."""
        bpy.ops = types.SimpleNamespace()
        for d in dotted:
            mod, _, name = d.partition(".")
            grp = getattr(bpy.ops, mod, None)
            if grp is None:
                grp = types.SimpleNamespace()
                setattr(bpy.ops, mod, grp)
            setattr(grp, name, lambda **kw: None)

    # THE CONTAINER IS A KWARG, NOT THE EXTENSION. Both glTF spellings go to the same operator, and
    # writing .glb without export_format='GLB' produces the wrong container with the right name.
    _ops("export_scene.gltf")
    glb = OIO.resolve_exporter(".glb")
    gltf = OIO.resolve_exporter(".gltf")
    check("B121 .glb and .gltf use the SAME operator with DIFFERENT export_format - the container "
          "is decided by a kwarg, so writing .glb on the default would give the wrong file with "
          "the right name",
          glb[0] == gltf[0] == "export_scene.gltf"
          # .get(), NOT a subscript. The condition is evaluated BEFORE check() is called, so a
          # missing key raises out of the expression and KILLS the suite instead of failing this
          # line - which is exactly what the plant for it did on 2026-09-03: rc=1, zero reported
          # failures, and the defect looking uncaught. Absence must BE the failure, not a crash.
          and glb[2].get("export_format") == "GLB"
          and gltf[2].get("export_format") == "GLTF_SEPARATE",
          "got %s / %s" % (glb, gltf))

    # THE VERSION DRIFT, AND THE KEYWORD THAT MOVES WITH IT. Picking the fallback operator and
    # keeping the new operator's keyword is the exact mistake this table exists to prevent.
    _ops("wm.obj_export")
    new_obj = OIO.resolve_exporter(".obj")
    _ops("export_scene.obj")
    old_obj = OIO.resolve_exporter(".obj")
    check("B121 OBJ resolves to wm.obj_export on a 4.0+ build and export_scene.obj on an older one",
          new_obj[0] == "wm.obj_export" and old_obj[0] == "export_scene.obj",
          "got %s / %s" % (new_obj[0], old_obj[0]))
    check("B121 and THE SELECTION KEYWORD MOVES WITH IT - export_selected_objects on the new "
          "operator, use_selection on the old. Carrying one keyword to the other operator is the "
          "mistake the per-format table exists to prevent",
          new_obj[1] == "export_selected_objects" and old_obj[1] == "use_selection",
          "got %s / %s" % (new_obj[1], old_obj[1]))

    _ops("wm.obj_export", "export_scene.obj")
    both = OIO.resolve_exporter(".obj")
    check("B121 with both present the NEW one wins - candidates are tried in order, not by "
          "whichever getattr happens to answer first",
          both[0] == "wm.obj_export", "got %s" % both[0])

    # A MISSING EXPORTER IS AN ADD-ON THAT IS OFF, and must read as a sentence rather than an
    # AttributeError from somewhere inside bpy.ops.
    _ops("export_scene.gltf")
    ok, msg = refuses(lambda _: OIO.resolve_exporter(".usd"), None, "no USD exporter",
                      "Preferences > Add-ons")
    check("B121 a format whose exporter is absent - a disabled add-on - is named in a sentence "
          "with where to turn it on, not an AttributeError from inside bpy.ops", ok, msg)

    ok, msg = refuses(lambda _: OIO.resolve_exporter(".fbx"), None, "no exporter for",
                      "export_mesh")
    check("B121 .fbx is refused here and pointed at export_mesh, which owns it along with the "
          "skeletal handling the UE round trip needs", ok, msg)
    ok, msg = refuses(lambda _: OIO.resolve_exporter(".3ds"), None, "no exporter for")
    check("B121 an unknown extension is refused listing what this op does write", ok, msg)

    ok, msg = refuses(OIO.op_export_scene, {"file": "C:/tmp/x"}, "no file extension")
    check("B121 a path with no extension is refused - the format comes from it", ok, msg)

    # A FRAME RANGE ON A FORMAT THAT CARRIES NO TIME. Accepting and ignoring it is the worst
    # outcome: 'I exported an animation' and 'I exported frame 1' look identical afterwards.
    _ops("wm.obj_export")
    ok, msg = refuses(OIO.op_export_scene,
                      {"file": "C:/tmp/x.obj", "frameStart": 1, "frameEnd": 10},
                      "carries no animation", "look identical")
    check("B121 a frame range on OBJ is REFUSED rather than dropped - accepted and ignored, the "
          "result is indistinguishable from a real animation export", ok, msg)

    check("B121 and the animated set is not empty - a version that refused every frame range "
          "would pass the check above while making the feature unreachable",
          {".abc", ".usd", ".glb"} <= OIO._ANIMATED, "got %s" % sorted(OIO._ANIMATED))

    _ops("wm.alembic_export")
    ok, msg = refuses(OIO.op_export_scene,
                      {"file": "C:/tmp/x.abc", "frameStart": 50, "frameEnd": 10}, "before")
    check("B121 an inverted frame range is refused on a format that DOES carry time", ok, msg)

    ok, msg = refuses(OIO.op_export_scene,
                      {"file": "C:/tmp/x.abc", "objects": ["Cube"], "selectedOnly": True},
                      "not both")
    check("B121 naming objects AND selectedOnly is refused - one names a set, the other uses "
          "whatever happens to be selected", ok, msg)

    print("")
    print("=== B122: the query ops - refusals only, and the transform math is NOT covered ===")
    # CHOSEN BY COUNTING, not from a wishlist: of 21 run_python escapes in one day's real work, 13
    # were ray casts and 15 were reading per-face material. Those two absences accounted for nearly
    # every escape from the typed path.
    #
    # WHAT THESE DO NOT COVER, said plainly because it is the important half. The correctness story
    # of ops_query is COORDINATE SPACE - world-to-local on the way in, local-to-world out, and
    # normals through the INVERSE TRANSPOSE rather than the object matrix. None of that is checked
    # here: the offline mathutils stub has Vector and no Matrix, so the arithmetic cannot run. It
    # needs a live Blender, and until then it is unverified exactly like every other postcondition.
    from MifBlender import ops_query as OQ

    ok, msg = refuses(OQ.op_ray_cast, {"origin": [0, 0, 0]}, "direction", "required")
    check("B122 a ray with no direction and no target is refused", ok, msg)
    ok, msg = refuses(OQ.op_ray_cast, {"origin": [0, 0, 0], "direction": [0, 0, -1],
                                       "target": [1, 1, 1]}, "not both")
    check("B122 direction AND target together is refused - target IS a direction", ok, msg)
    ok, msg = refuses(OQ.op_ray_cast, {"origin": [0, 0, 0], "direction": [0, 0, 0]},
                      "zero length", "hits nothing")
    check("B122 a zero-length direction is refused rather than cast", ok, msg)
    ok, msg = refuses(OQ.op_ray_cast, {"origin": [0, 0, 0], "target": [0, 0, 0]},
                      "zero length", "target is the origin")
    check("B122 a target equal to the origin is refused, and the message says WHY it is zero "
          "length - the caller wrote a point, not a direction", ok, msg)
    ok, msg = refuses(OQ.op_ray_cast, {"origin": [0, 0], "direction": [0, 0, -1]}, "[x,y,z]")
    check("B122 a two-component origin is refused by shape", ok, msg)
    ok, msg = refuses(OQ.op_ray_cast, {"origin": [0, 0, 0], "direction": [0, 0, -1],
                                       "distance": 0}, "greater than 0")
    check("B122 a zero distance is refused - it can never hit anything", ok, msg)
    ok, msg = refuses(OQ.op_ray_cast, {"origin": [0, 0, 0], "direction": [0, 0, -1],
                                       "object": "Lamp"}, "is a LIGHT", "whole scene")
    check("B122 casting against a non-MESH is refused and points at the scene form", ok, msg)

    ok, msg = refuses(OQ.op_closest_point, {"object": "Lamp", "point": [0, 0, 0]},
                      "is a LIGHT", "surface")
    check("B122 closest_point_on_mesh on a non-MESH is refused", ok, msg)
    ok, msg = refuses(OQ.op_closest_point, {"object": "Cube"}, "'point' is required")
    check("B122 closest_point_on_mesh with no point is refused", ok, msg)

    ok, msg = refuses(OQ.op_face_info, {"object": "Lamp"}, "has no faces")
    check("B122 face_info on a non-MESH is refused", ok, msg)
    ok, msg = refuses(OQ.op_face_info, {"object": "Cube", "material": "Wood", "slot": 0},
                      "not both", "can occupy more than one slot")
    check("B122 material AND slot together is refused - a material can occupy several slots, so "
          "the two can disagree and only one could win", ok, msg)
    ok, msg = refuses(OQ.op_face_info, {"object": "Cube", "indices": "not a list"},
                      "must be a list")
    check("B122 a non-list indices is refused by type", ok, msg)
    ok, msg = refuses(OQ.op_face_info, {"object": "Cube", "limit": -1}, "cannot be negative")
    check("B122 a negative limit is refused", ok, msg)

    print("")
    print("=== B123: select_faces, bisect_plane, set_shading - refusals before any mesh work ===")
    # THE PATTERN THESE THREE SHARE. Each walks or rewrites a mesh, so each is expensive to get
    # wrong, and each is meant to be called repeatedly. Every argument is therefore validated before
    # a polygon is touched - which is also the only part of them an offline stub can reach, since
    # the geometry itself needs a live Blender.
    from MifBlender import ops_query as OQ2

    ok, msg = refuses(OQ2.op_select_faces, {"object": "Cube"}, "no criteria",
                      "face_info already does")
    check("B123 select_faces with no criteria is refused - it would return every face, which "
          "face_info already does", ok, msg)
    ok, msg = refuses(OQ2.op_select_faces, {"object": "Cube", "axis": "W"}, "axis must be one of")
    check("B123 an unknown axis is refused with the valid set", ok, msg)
    ok, msg = refuses(OQ2.op_select_faces, {"object": "Cube", "axis": "Z", "direction": [0, 0, 1]},
                      "not both", "axis IS a direction")
    check("B123 axis AND direction together is refused", ok, msg)
    ok, msg = refuses(OQ2.op_select_faces, {"object": "Cube", "axis": "Z", "angle": 0},
                      "cone HALF-angle")
    check("B123 a zero cone angle is refused, and the message says it is a HALF-angle - the "
          "difference between a hemisphere and everything", ok, msg)
    ok, msg = refuses(OQ2.op_select_faces, {"object": "Cube", "minArea": 5, "maxArea": 1},
                      "nothing can match")
    check("B123 an inverted area range is refused rather than silently matching nothing", ok, msg)
    ok, msg = refuses(OQ2.op_select_faces, {"object": "Cube", "boxMin": [0, 0, 0]},
                      "BOTH corners")
    check("B123 half a box is refused naming which corner is missing", ok, msg)
    ok, msg = refuses(OQ2.op_select_faces, {"object": "Lamp", "axis": "Z"}, "no faces to select")
    check("B123 select_faces on a non-MESH is refused", ok, msg)

    ok, msg = refuses(OQ2.op_bisect_plane, {"object": "Cube"}, "planeCo", "world space")
    check("B123 bisect_plane with no plane point is refused, and the message says WORLD space - "
          "the space mistake is silent, because a cut that missed looks like one that did nothing",
          ok, msg)
    ok, msg = refuses(OQ2.op_bisect_plane, {"object": "Cube", "planeCo": [0, 0, 0]},
                      "give the plane a direction")
    check("B123 a plane point with no axis or normal is refused", ok, msg)
    ok, msg = refuses(OQ2.op_bisect_plane, {"object": "Cube", "planeCo": [0, 0, 0], "axis": "Q"},
                      "axis must be X, Y or Z")
    check("B123 an unknown bisect axis is refused", ok, msg)
    ok, msg = refuses(OQ2.op_bisect_plane, {"object": "Cube", "planeCo": [0, 0, 0], "axis": "Z",
                                            "clearInner": True, "clearOuter": True},
                      "empty mesh", "clear_mesh")
    check("B123 clearing BOTH sides is refused - it empties the mesh, and clear_mesh is the op "
          "that means to do that", ok, msg)
    ok, msg = refuses(OQ2.op_bisect_plane, {"object": "Cube", "planeCo": [0, 0, 0],
                                            "planeNo": [0, 0, 0]}, "zero length", "no plane")
    check("B123 a zero-length plane normal is refused", ok, msg)
    ok, msg = refuses(OQ2.op_bisect_plane, {"object": "Lamp", "planeCo": [0, 0, 0], "axis": "Z"},
                      "cannot be bisected")
    check("B123 bisecting a non-MESH is refused", ok, msg)

    ok, msg = refuses(OQ2.op_set_shading, {"object": "Cube"}, "nothing to do")
    check("B123 set_shading with no settings is refused", ok, msg)
    ok, msg = refuses(OQ2.op_set_shading, {"object": "Cube", "autoSmoothAngle": 400},
                      "DEGREES", "0-180")
    check("B123 an out-of-range auto-smooth angle is refused, and the message says DEGREES - the "
          "property underneath is radians", ok, msg)
    ok, msg = refuses(OQ2.op_set_shading, {"object": "Cube", "smooth": True,
                                           "indices": "nope"}, "must be a list")
    check("B123 a non-list indices is refused by type", ok, msg)
    ok, msg = refuses(OQ2.op_set_shading, {"object": "Lamp", "smooth": True}, "no shading to set")
    check("B123 set_shading on a non-MESH is refused", ok, msg)

    print("")
    print("=== B124: the compositor on BLENDER 5.0, where scene.node_tree does not exist ===")
    # THIS BLOCK EXISTS BECAUSE THE SHIPPED CODE WAS DEAD ON 5.0 AND EVERY GATE WAS GREEN. A live
    # call to compositor_info returned "AttributeError: 'Scene' object has no attribute 'node_tree'"
    # hours after the compositor work was committed. Probing all four installs headless established:
    #
    #   3.6 / 4.2 / 4.4   scene.node_tree, embedded, gated by use_nodes (default FALSE)
    #   5.0               node_tree ABSENT; scene.compositing_node_group holds a real node group,
    #                     and use_nodes defaults TRUE while that group is still None
    #
    # and that CompositorNodeComposite is UNDEFINED on 5.0 - the compositor is a node group there,
    # so it terminates in NodeGroupOutput. B116 covers the old era; this covers the new one, because
    # a stub that only models one of them is how the whole family shipped broken.
    bpy = sys.modules["bpy"]
    sc = bpy.context.scene
    _saved_tree = getattr(sc, "node_tree", None)
    _saved_use = sc.use_nodes
    try:
        class _NG(object):
            def __init__(self, name="Compositing"):
                self.bl_idname, self.name, self.nodes, self.links = "CompositorNodeTree", name, [], []

        class _N5(object):
            def __init__(self, kind, name):
                self.bl_idname, self.name, self.label, self.mute = kind, name, "", False
                self.inputs, self.outputs = [], []

        class _L5(object):
            def __init__(self, fn, tn):
                self.from_node, self.to_node = fn, tn

        # A 5.0-SHAPED SCENE: no node_tree attribute at all, and use_nodes already True.
        del sc.node_tree
        sc.use_nodes = True
        sc.compositing_node_group = None
        sc.render.use_compositing = True

        era, attr = ON._tree.__globals__["compositor_era"](sc)
        check("B124 a scene carrying compositing_node_group is detected as the NEW era",
              era == "new" and attr == "compositing_node_group", "got %s/%s" % (era, attr))
        check("B124 and compositor_tree returns None rather than raising when no group is assigned",
              ON._tree.__globals__["compositor_tree"](sc) is None, "did not return None")

        res, err = succeeds(ON.op_compositor_info, {})
        check("B124 with no group assigned the blocker says use_nodes MEANS NOTHING on 5.0 - the "
              "trap is that the scene looks compositing-enabled out of the box",
              any("means nothing by itself" in b for b in res.get("blockers", [])),
              err or "got %s" % res.get("blockers"))
        check("B124 and the response names which API it is talking to, so a reader is never "
              "guessing which Blender the answer describes",
              "compositing_node_group" in (res.get("compositorApi") or ""),
              err or "got %s" % res.get("compositorApi"))

        # THE GUARD THAT USED TO BE ON use_nodes. On 5.0 that is True out of the box, so a
        # use_compositing blocker keyed on it would fire on every fresh scene with no compositor.
        sc.render.use_compositing = False
        res, err = succeeds(ON.op_compositor_info, {})
        check("B124 the use_compositing blocker does NOT fire when there is no tree at all - keyed "
              "on use_nodes it would have fired on every fresh 5.0 scene",
              not any("use_compositing is OFF" in b for b in res.get("blockers", [])),
              err or "got %s" % res.get("blockers"))
        sc.render.use_compositing = True

        # A CORRECTLY WIRED 5.0 TREE MUST NOT BE CALLED BROKEN. It terminates in NodeGroupOutput,
        # and the first version of this looked for CompositorNodeComposite by name and reported a
        # perfectly good compositor as writing nothing.
        ng = _NG()
        rl, go = _N5("CompositorNodeRLayers", "Render Layers"), _N5("NodeGroupOutput", "Group Output")
        ng.nodes, ng.links = [rl, go], [_L5(rl, go)]
        sc.compositing_node_group = ng
        res, err = succeeds(ON.op_compositor_info, {})
        check("B124 a tree wired Render Layers -> GROUP OUTPUT is reported as fine on 5.0 - looking "
              "for a Composite node by name called a correct compositor broken",
              res.get("blockers") == [] and res.get("compositeConnected") is True,
              err or "got %s" % res.get("blockers"))
        check("B124 and the output node types it looked for are reported, not left implicit",
              "NodeGroupOutput" in (res.get("outputNodeTypes") or []),
              err or "got %s" % res.get("outputNodeTypes"))

        ng.links = []
        res, err = succeeds(ON.op_compositor_info, {})
        check("B124 an UNLINKED group output is still a blocker, and it is named by the 5.0 term "
              "rather than by a node that does not exist on that build",
              any("Group Output node" in b for b in res.get("blockers", [])),
              err or "got %s" % res.get("blockers"))
    finally:
        # RESTORED. B119 left Cube in EDIT mode and cost five unrelated checks; a block that
        # reshapes the stub's scene owes the same duty.
        try:
            del sc.compositing_node_group
        except AttributeError:
            pass
        sc.node_tree = _saved_tree
        sc.use_nodes = _saved_use

    print("")
    print("=== B125: create_collision_hull - refusals, and why the audit is a refusal ===")
    # THE RECIPE EVERY TUTORIAL GIVES IS WRONG AND LOOKS RIGHT. bm.from_mesh(source) then
    # convex_hull then delete geom_interior gives, on Suzanne, the SAME 66 vertices as a correct
    # hull - plus 51 non-manifold edges, 4 boundary edges, Euler 16 instead of 2, 22 convexity
    # violations and an 8% inflated volume. Verified on 4.4.0 and 5.0.1 by running both recipes.
    # Nothing raises, and a check that asks "did I get a hull with a sensible vertex count" passes
    # on the wreck - which is why the op audits and REFUSES rather than warning.
    #
    # The audit itself needs real geometry and is exercised by blender_version_matrix on all four
    # builds. These are the guards that fire before any of it.
    from MifBlender import ops_mesh as OM

    ok, msg = refuses(OM.op_create_collision_hull, {"object": "Lamp"}, "MESH")
    check("B125 hulling a non-MESH is refused", ok, msg)
    ok, msg = refuses(OM.op_create_collision_hull, {}, "'object' is required")
    check("B125 with no object it is refused", ok, msg)
    ok, msg = refuses(OM.op_create_collision_hull, {"object": "Cube", "index": -1},
                      "cannot be negative")
    check("B125 a negative index is refused - it would name the hull UCX_Cube_-1", ok, msg)
    ok, msg = refuses(OM.op_create_collision_hull, {"object": "Cube", "maxVertices": 3},
                      "at least 4", "enclose a volume")
    check("B125 maxVertices below 4 is refused - fewer than four points enclose nothing, and the "
          "failure is a silent empty mesh rather than an exception", ok, msg)
    ok, msg = refuses(OM.op_create_collision_hull, {"object": "Cube", "collection": "NoSuchColl"},
                      "no collection named")
    check("B125 an unknown collection is refused before anything is built", ok, msg)

    print("")
    print("=== B126: objects_overlap - the guards, and never BVHTree.FromObject ===")
    # THE VERDICTS THEMSELVES need real geometry and are exercised by blender_version_matrix on
    # every build. What is checkable here is the guard set - and one of these guards is standing
    # between a caller and a DEAD BLENDER, not just a bad answer: BVHTree.FromObject on a non-mesh
    # SEGFAULTS 3.6 outright, no traceback and no exception. This op never calls FromObject at all,
    # which removes that failure mode rather than guarding it, and refuses non-meshes besides.
    from MifBlender import ops_query as OQ3

    ok, msg = refuses(OQ3.op_objects_overlap, {"a": "Cube"}, "'b' is required")
    check("B126 one object is refused - overlap needs a pair", ok, msg)
    ok, msg = refuses(OQ3.op_objects_overlap, {"a": "Cube", "b": "Cube"},
                      "both objects", "true and useless")
    check("B126 the same object twice is refused, and the message says why the answer would be "
          "useless rather than just declining", ok, msg)
    ok, msg = refuses(OQ3.op_objects_overlap, {"a": "Cube", "b": "Lamp"},
                      "is a LIGHT", "surface to intersect")
    check("B126 a non-MESH is refused - and this op never calls BVHTree.FromObject, which would "
          "segfault Blender 3.6 on exactly this input", ok, msg)
    ok, msg = refuses(OQ3.op_objects_overlap, {"objects": ["Cube"]}, "exactly two")
    check("B126 an objects list of the wrong length is refused", ok, msg)
    ok, msg = refuses(OQ3.op_objects_overlap, {"a": "Cube", "b": "Cam", "tolerance": -1},
                      "cannot be negative")
    check("B126 a negative tolerance is refused", ok, msg)
    ok, msg = refuses(OQ3.op_objects_overlap, {"a": "Cube", "b": "Cam", "maxSamples": 0},
                      "at least 1")
    check("B126 a zero sample cap is refused - it would test nothing and answer confidently",
          ok, msg)

    print("")
    print("=== B127: rename_object, set_vertex_color, mesh_stats ===")
    # THE RENAME GUARD IS THE ONE THAT MATTERS, and it is not a tidiness rule. Blender resolves a
    # name collision in OPPOSITE DIRECTIONS across the 3.6/4.x line - measured on all four builds:
    # with an existing 'Alpha', renaming another object to 'Alpha' makes the RENAMER take the name
    # and silently renames the INCUMBENT on 3.6, and the reverse on 4.2+. So a naive rename corrupts
    # an object the caller never mentioned, on exactly one of the builds this addon supports.
    from MifBlender import ops_mesh as OM2

    ok, msg = refuses(OM2.op_rename_object, {"object": "Cube"}, "'to' is required")
    check("B127 a rename with no new name is refused", ok, msg)
    ok, msg = refuses(OM2.op_rename_object, {"object": "Cube", "to": "   "}, "empty")
    check("B127 a blank name is refused rather than stored", ok, msg)

    ok, msg = refuses(OM2.op_set_vertex_color, {"object": "Cube", "name": "Z" * 80},
                      "3.6 silently TRUNCATES", "returns None")
    check("B127 an over-long colour attribute name is refused UP FRONT, because past the limit 3.6 "
          "truncates and 4.2+ returns None and neither raises", ok, msg)
    ok, msg = refuses(OM2.op_set_vertex_color, {"object": "Cube", "domain": "EDGE"},
                      "CORNER", "POINT")
    check("B127 an unsupported colour domain is refused", ok, msg)
    ok, msg = refuses(OM2.op_set_vertex_color, {"object": "Cube", "dataType": "HALF"},
                      "BYTE_COLOR or FLOAT_COLOR")
    check("B127 an unknown colour data type is refused", ok, msg)
    ok, msg = refuses(OM2.op_set_vertex_color, {"object": "Cube", "domain": "POINT",
                                                "faces": [0]}, "needs domain CORNER")
    check("B127 selecting faces on the POINT domain is refused - a face selects CORNERS, and a "
          "per-vertex attribute has none to select", ok, msg)
    ok, msg = refuses(OM2.op_set_vertex_color, {"object": "Cube", "color": [1, 0]},
                      "[r,g,b]")
    check("B127 a two-component colour is refused by shape", ok, msg)
    ok, msg = refuses(OM2.op_set_vertex_color, {"object": "Lamp", "color": [1, 0, 0]}, "MESH")
    check("B127 colouring a non-MESH is refused", ok, msg)

    ok, msg = refuses(OM2.op_mesh_stats, {"object": "Lamp"}, "MESH")
    check("B127 mesh_stats on a non-MESH is refused", ok, msg)
    ok, msg = refuses(OM2.op_mesh_stats, {}, "'object' is required")
    check("B127 mesh_stats with no object is refused", ok, msg)

    print("")
    print("=== B128: set_vertex_weights and add_shape_key - the last consume-only pair ===")
    # FOUND BY THE MATRIX, not by reading: three ops could never be reached because nothing in the
    # addon created a vertex group or a shape key, though five ops consumed them. Fourth time that
    # question has paid, after collections, empties and armatures.
    from MifBlender import ops_rig as OR

    ok, msg = refuses(OR.op_set_vertex_weights, {"object": "Cube"}, "'group' is required")
    check("B128 weights with no group named is refused", ok, msg)
    ok, msg = refuses(OR.op_set_vertex_weights, {"object": "Cube", "group": "G", "mode": "MULT"},
                      "mode must be one of")
    check("B128 an unknown weight mode is refused with the valid set", ok, msg)
    ok, msg = refuses(OR.op_set_vertex_weights,
                      {"object": "Cube", "group": "G", "weight": 1.0, "weights": [1.0]},
                      "not both", "cannot both win")
    check("B128 weight AND weights together is refused - one is a value for every vertex and the "
          "other a value per vertex", ok, msg)
    ok, msg = refuses(OR.op_set_vertex_weights,
                      {"object": "Cube", "group": "G", "vertices": [0, 1], "weights": [1.0]},
                      "parallel lists")
    check("B128 mismatched vertices and weights lengths are refused", ok, msg)
    ok, msg = refuses(OR.op_set_vertex_weights,
                      {"object": "Cube", "group": "G", "vertices": [0], "remove": True,
                       "weight": 1.0}, "nothing to apply to")
    check("B128 remove with a weight is refused - removal takes vertices OUT, so a weight has "
          "nothing to apply to", ok, msg)
    ok, msg = refuses(OR.op_set_vertex_weights, {"object": "Cube", "group": "Q" * 80},
                      "truncates")
    check("B128 an over-long group name is refused, because Blender truncates it on every build "
          "and the group you get is not the one you named", ok, msg)

    ok, msg = refuses(OR.op_add_shape_key, {"object": "Cube", "name": "K" * 80}, "truncates")
    check("B128 an over-long shape key name is refused", ok, msg)
    ok, msg = refuses(OR.op_add_shape_key,
                      {"object": "Cube", "sliderMin": 1.0, "sliderMax": 0.0}, "below")
    check("B128 an inverted slider range is refused", ok, msg)

    print("")
    print("=== B129: set_light_shadow - refusing what a build cannot do ===")
    # THE LAST TIER 5 ITEM, left until it could be MEASURED rather than remembered, because this is
    # the most version-unstable corner of the Blender API this addon touches. Read off bl_rna on
    # 3.6.23, 4.2.17, 4.4.0 and 5.0.1:
    #
    #   light.cycles.cast_shadow    3.6 ONLY, removed at 4.2
    #   contact shadows (4 props)   3.6 and 4.2, dropped by EEVEE Next at 4.4
    #   jitter / filter / max res    4.2 and later, absent on 3.6
    #
    # The version behaviour is exercised on the real builds by blender_version_matrix. What is
    # checkable here is the guard set.
    from MifBlender import ops_lightcam as OL

    ok, msg = refuses(OL.op_set_light_shadow, {"object": "Cam"}, "not a LIGHT")
    check("B129 shadow settings on a non-LIGHT are refused", ok, msg)
    ok, msg = refuses(OL.op_set_light_shadow, {"object": "Lamp"}, "nothing to do")
    check("B129 a call with no settings at all is refused", ok, msg)
    ok, msg = refuses(OL.op_set_light_shadow, {"object": "Lamp", "color": [1, 0]}, "[r,g,b]")
    check("B129 a two-component shadow colour is refused by shape", ok, msg)
    ok, msg = refuses(OL.op_set_light_shadow, {"object": "NoSuchLight", "enabled": True},
                      "no object named")
    check("B129 an unknown object is refused", ok, msg)
    # THE COLLECTION IS GUARDED FIRST, because all([]) is True - an empty _SHADOW_MAP would pass
    # the shape assertion below while meaning the op can map nothing at all. audit_vacuous_checks
    # caught this written the naive way, which is what that audit is for.
    check("B129 the shadow map is populated at all - all([]) is True, so the shape check below "
          "would pass on an empty table",
          len(OL._SHADOW_MAP) >= 15, "only %d entries" % len(OL._SHADOW_MAP))
    _bad_rows = {k: v for k, v in OL._SHADOW_MAP.items()
                 if not (isinstance(v, tuple) and len(v) == 3 and v[0] in ("light", "cycles")
                         and isinstance(v[1], str) and isinstance(v[2], str))}
    check("B129 every documented key maps to a real property path and an availability note - the "
          "table is what turns 'this build lacks it' into a sentence naming which builds have it",
          not _bad_rows and len(OL._SHADOW_MAP) >= 15, "malformed rows: %s" % _bad_rows)

    print("")
    print("=== B130: render_animation's scene parameter, and the guard Blender will not do ===")
    # THE KEY WAS REMOVED ON 2026-09-03 rather than shipped half-working: frame_path() was still
    # computed on bpy.context.scene while the child rendered another, so every path in the response
    # and every mtime in the postcondition would have described the wrong scene, confidently.
    #
    # BLENDER WILL NOT CATCH A TYPO. Measured on 5.0.1: `blender -b file.blend -S BadName` SILENTLY
    # renders the saved active scene - no error, no warning, exit 0. So a wrong name would render
    # the wrong scene and report success against paths taken from the right one, which is the exact
    # failure the missing output override exists to prevent. Hence the check here.
    from MifBlender import ops_render as ORD2

    ok, msg = refuses(ORD2.op_render_animation, {"scene": "NoSuchScene"},
                      "no scene named", "silently render")
    check("B130 an unknown scene is refused, and the message says Blender's own -S would have "
          "silently rendered the wrong one", ok, msg)
    check("B130 'scene' is on the accepted key list - it was removed once and this is the check "
          "that it came back",
          "scene" in ORD2._ANIM_KEYS, "keys=%s" % sorted(ORD2._ANIM_KEYS))

    print("")
    print("=== B107: a refusal that must NOT fire - the legal combination ===")
    # THE NEGATIVE CONTROL. Every check above proves something is refused; without this, a guard
    # that refused EVERYTHING would score full marks. Retyping to SPOT while setting spotAngle is
    # the case the per-type rule exists to ALLOW, and it must reach the bpy stub rather than raise.
    try:
        ops_lightcam.op_set_light({"object": "Lamp", "type": "SPOT", "spotAngle": 0.5})
        outcome = "reached the write path"
    except MifOpError as exc:
        outcome = "REFUSED: %s" % str(exc)[:110]
    except Exception as exc:                       # noqa: BLE001
        # An AttributeError here means it got past validation into the stub, which is the pass.
        outcome = "reached the write path (%s)" % type(exc).__name__
    check("B107 retyping to SPOT while setting spotAngle is ALLOWED - the negative control, "
          "without which a guard that refused everything would pass every check above",
          not outcome.startswith("REFUSED"), outcome)

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    print("=" * 72)
    print("Refusal contracts, plus the ONE family whose postconditions are pure data: B113 asserts")
    print("real collection membership and reachability, because linking is a name-keyed set with")
    print("nothing evaluated. Everywhere else the claim stands unchanged - no evaluated matrix, no")
    print("colour space, no purge count and no rendered frame is verified here, and none of it can")
    print("be without a live Blender.")
    return 1 if FAIL else 0


def run():
    """main(), but a CRASH still reports what passed and where it died.

    WHY THIS EXISTS, and it is the same lesson three times over. check() takes an already-EVALUATED
    condition, so any expression inside one that can raise - a dict subscript, an attribute on a
    None - escapes before check() is ever called and takes the interpreter with it. Three separate
    ground-truth plants on 2026-09-03 hit exactly that: the suite exited 1 having printed ZERO
    failures, which reads as "the plant was not caught" when in fact it was caught so hard the
    harness died.

    That is the same shape B111 forbids in the ops themselves - a crash instead of a refusal reports
    one problem and hides the rest - and the harness gets no exemption from its own rule.

    The individual fix is discipline (use .get(), wrap a call in succeeds()); this is the backstop,
    so a mistake in one line costs that line rather than the whole report. The exit code is still
    non-zero, because a suite that could not finish has not passed.
    """
    try:
        return main()
    except Exception:                              # noqa: BLE001
        import traceback
        print("")
        print("=" * 72)
        print("THE SUITE CRASHED - %d check(s) had already PASSED and %d FAILED before this."
              % (len(PASS), len(FAIL)))
        for name, detail in FAIL:
            print("  FAILED: %s" % name)
            print("          %s" % detail)
        print("")
        print("A crash is NOT a clean run and NOT an empty one. Everything after the traceback")
        print("below went unrun, so the checks it would have made are unknown rather than green.")
        print("=" * 72)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run())
