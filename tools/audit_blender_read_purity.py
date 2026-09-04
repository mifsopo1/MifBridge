"""Does a Blender op that LOOKS like a read leave a mark on the mesh?

The UE side of this bridge has audit_read_purity.py: before/after list_dirty_packages around every
`list_`/`get_`/`describe_`/`find_` endpoint, because plenty of engine getters are GetOrCreate
underneath and a stray Modify() in a read path dirties a package as thoroughly as a real edit while
still answering ok:true. The Blender side had NO equivalent - this is that check, ported to what
Blender actually exposes.

Andre, 2026-08-27/28: "make sure our blender porting and endpoints are as indepth testing wise as our
UE side". This is one piece of closing that gap - the UE side has ~15 audit_*.py cross-cutting checks
(dead params, vacuous checks, mode params, postconditions, read purity...) against 4 files total on
the Blender side (blender_probe.py, test_blender_mesh.py, test_blender_ops.py, run_blender_suites.py),
none of which ask "does this op that is NAMED like a read actually behave like one". The mesh-roundtrip
fidelity-gate bug found the same day this was written is exactly the kind of thing that class of check
exists to catch - a claim ("this is safe/read-only") that nothing had ever actually verified.

INSTRUMENT. Blender has no list_dirty_packages equivalent reachable over this addon's socket, so the
snapshot is object_info() itself: name, verts/edges/faces/tris, boundsLocalMin/MaxBU, location/
rotation/scale, materialSlots, uvLayers, for every mesh object in the scene, taken via scene_info +
object_info before and after each candidate call. A read-like op should produce an IDENTICAL snapshot.
This does not (and cannot, since object_info reports no selection flags) prove edge/vertex SELECTION
state is untouched - select_edges's own docstring claim ("the bmesh is never written back") is about
geometry, and geometry is exactly what this measures.

CANDIDATES. Every read-shaped op that needs no object argument, plus object_info and select_edges
resolved at runtime. It was five until 2026-09-04, when the twin comparison asked why the UE arm
covers every read-prefixed endpoint and this one covered a hand-picked handful: 28 ops here are
shaped like reads and 4 were probed, so the green meant a seventh of the surface. The dozen that
need an object - describe_material, list_bones, list_modifiers and the rest - are not here yet and
are filed as their own item; they need the same runtime resolution object_info already gets. gen_status is deliberately excluded: it is a network probe to an external generation
service, out of scope the same way the gen_* family is declared out of scope elsewhere in this repo,
and purity is not the interesting question about it.

Usage:
    python tools/audit_blender_read_purity.py
Needs a live Blender with the MifBlender addon listening (relaunch: see tools/blender_probe.py's
docstring for the --background --factory-startup --python-expr invocation), AND at least one mesh
object already in the scene - import one with import_mesh first if the scene is empty.
"""
import copy
import sys

from blender_audit_common import call as _call
from blender_audit_common import HOST, PORT

# Fields object_info reports that describe GEOMETRY/TRANSFORM, i.e. what a read must not move. Deliberately
# excludes nothing structural - if ops_common.object_info grows a new geometry-shaped field, add it here
# rather than letting a silent diff pass because this list did not know to look.
_COMPARE_FIELDS = (
    "name", "type", "locationBU", "rotationEulerRad", "scale", "isIdentityTransform",
    "dimensionsBU", "boundsLocalMinBU", "boundsLocalMaxBU", "boundsLocalSizeBU", "boundsLocalSizeUU",
    "materialSlots", "uvLayers", "hasCustomSplitNormals", "verts", "edges", "faces", "tris",
)

# name -> params for each candidate that takes no object-specific argument. select_edges and
# object_info both require an explicit object name, resolved at runtime in main() against whatever is
# actually in the scene, so they are not listed here.
CANDIDATES = [
    ("ping", {}),
    ("scene_info", {}),
    ("list_objects", {}),
    # WIDENED 2026-09-04, because the UE twin covers EVERY read-prefixed endpoint and this one
    # covered five. 28 ops in this addon are shaped like reads - list_*, describe_*, *_info - and
    # four were probed, so "no read dirties the scene" was a claim about a seventh of the surface.
    # These are the ones that need no object argument, so they cost nothing to add.
    ("compositor_info", {}),
    ("file_info", {}),
    ("list_actions", {}),
    ("list_cameras", {}),
    ("list_collections", {}),
    ("list_lights", {}),
    ("list_markers", {}),
    ("list_materials", {}),
    ("list_view_layers", {}),
    ("physics_info", {}),
    ("render_info", {}),
    ("world_info", {}),
]

# select_edges needs a selector that actually resolves to something real, or it tells you nothing
# about whether a resolved selection mutates - boundaryOnly with no positional filter matches
# whatever boundary edges the target object has, same predicate mif_mesh_roundtrip falls back to.
_SELECT_EDGES_PARAMS = {"boundaryOnly": True}


def snapshot():
    """{object name -> filtered object_info} for every MESH object currently in the scene."""
    si = _call("scene_info", {})
    if not si.get("ok"):
        raise RuntimeError("scene_info failed: %s" % si.get("error"))
    names = [o["name"] for o in (si.get("objects") or []) if o.get("type") == "MESH"]
    out = {}
    for name in names:
        oi = _call("object_info", {"object": name})
        if not oi.get("ok"):
            raise RuntimeError("object_info(%s) failed: %s" % (name, oi.get("error")))
        obj = oi.get("object") or {}
        out[name] = {k: obj.get(k) for k in _COMPARE_FIELDS}
    return out


def diff_snapshots(before, after):
    """[(objectName, field, before, after)] for every field that moved, added, or vanished."""
    findings = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        if b is None:
            findings.append((name, "<object>", "absent", "PRESENT (op created it)"))
            continue
        if a is None:
            findings.append((name, "<object>", "present", "ABSENT (op deleted it)"))
            continue
        for field in _COMPARE_FIELDS:
            bv, av = b.get(field), a.get(field)
            if isinstance(bv, float) or isinstance(av, float):
                if bv is None or av is None or abs(float(bv) - float(av)) > 1e-6:
                    findings.append((name, field, bv, av))
            elif isinstance(bv, list) and isinstance(av, list) and all(
                    isinstance(x, (int, float)) for x in bv + av):
                if len(bv) != len(av) or any(abs(float(x) - float(y)) > 1e-6 for x, y in zip(bv, av)):
                    findings.append((name, field, bv, av))
            elif bv != av:
                findings.append((name, field, bv, av))
    return findings


def main():
    try:
        p = _call("ping", {}, timeout=5.0)
    except OSError as exc:
        # A SQUATTER IS NOT AN ABSENCE, and this used to report both as "start it first". On this
        # machine 8792 is held by a UE editor (docs/06 issue 15), so that advice sent the reader
        # looking for a Blender that had failed to start rather than for the process on its port.
        # blender_audit_common owns the distinction so the two audits and three suites agree.
        try:
            import blender_audit_common as _B
            _B.HOST, _B.PORT = HOST, PORT
            print("Blender backend unreachable at %s:%d (%s)." % (HOST, PORT, exc))
            return _B.skip_banner("read-purity")
        except ImportError:
            print("Blender backend unreachable at %s:%d (%s). Start it first - see this file's own "
                  "docstring for the launch command." % (HOST, PORT, exc))
            return 2
    if not p.get("ok"):
        print("ping failed: %s" % p.get("error"))
        return 2

    base = snapshot()
    if not base:
        print("scene has no mesh objects - nothing to check purity against. import_mesh one first.")
        return 2
    print("baseline: %d mesh object(s): %s" % (len(base), ", ".join(sorted(base))))

    exercised, findings = [], []
    for op, params in CANDIDATES:
        before = copy.deepcopy(base)
        r = _call(op, params, timeout=60.0)
        after = snapshot()
        if not r.get("ok"):
            print("  %-14s NOT EXERCISED - call failed: %s" % (op, r.get("error")))
            base = after  # keep the chain honest even on failure
            continue
        exercised.append(op)
        d = diff_snapshots(before, after)
        if d:
            findings.append((op, d))
        base = after  # chain forward so op N's baseline is op N-1's real post-state

    # object_info on itself: does asking about an object change what asking about it reports?
    first_obj = sorted(base)[0]
    before = copy.deepcopy(base)
    r = _call("object_info", {"object": first_obj}, timeout=30.0)
    after = snapshot()
    if r.get("ok"):
        exercised.append("object_info")
        d = diff_snapshots(before, after)
        if d:
            findings.append(("object_info", d))
    else:
        print("  %-14s NOT EXERCISED - call failed: %s" % ("object_info", r.get("error")))
    base = after if r.get("ok") else base

    # select_edges against the same real object, with a selector that actually resolves (an empty
    # match proves nothing about whether a resolved selection mutates).
    before = copy.deepcopy(base)
    r = _call("select_edges", dict(_SELECT_EDGES_PARAMS, object=first_obj), timeout=30.0)
    after = snapshot()
    if r.get("ok"):
        exercised.append("select_edges")
        matched = r.get("count")
        print("  select_edges matched %s of %s edges on '%s'" %
              (matched, r.get("totalEdges"), first_obj))
        d = diff_snapshots(before, after)
        if d:
            findings.append(("select_edges", d))
    else:
        print("  %-14s NOT EXERCISED - call failed: %s" % ("select_edges", r.get("error")))

    print("")
    # The denominator was a hardcoded 5 and stayed 5 when the candidate list grew, which is the
    # small version of every stale number this repo keeps deleting.
    print("exercised: %d of %d candidate(s) (%s)"
          % (len(exercised), len(CANDIDATES) + 2, ", ".join(exercised)))
    if findings:
        print("")
        print("READ OPS THAT MOVED THE MESH:")
        for op, d in findings:
            for name, field, bv, av in d:
                print("  %-14s %-20s %-16s %r -> %r" % (op, name, field, bv, av))
        return 1
    print("OK  every exercised op left every mesh object's geometry and transform unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
