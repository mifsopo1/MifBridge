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
  IT CANNOT    prove any op DOES what it says once Blender is real. Every postcondition -
               evaluated matrices, purged orphans, colour spaces, motion preserved - needs a live
               Blender and stays unverified until a suite runs there.

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
        collections=_Coll(), filepath="", is_dirty=False)
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

    if ADDON not in sys.path:
        sys.path.insert(0, ADDON)


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
    print("=== B107: a refusal that must NOT fire - the legal combination ===")
    # THE NEGATIVE CONTROL. Every check above proves something is refused; without this, a guard
    # that refused EVERYTHING would score full marks. Retyping to SPOT while setting spotAngle is
    # the case the per-type rule exists to ALLOW, and it must reach the bpy stub rather than raise.
    from MifBlender.ops_common import MifOpError
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
    print("Refusal contracts only. NOTHING here proves an op DOES what it says once Blender is")
    print("real - every postcondition needs a live backend and stays unverified until then.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
