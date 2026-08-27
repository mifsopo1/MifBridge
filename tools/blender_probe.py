"""Run the MifBlender addon against every installed Blender, headlessly. The Blender half of
tools/make_engine_probe.py.

WHY THIS EXISTS. The UE half of MifBridge is compiled against 5.3 AND 5.7 on every change, and the
release manifest says which. The Blender half had nothing equivalent: bl_info declared a floor of
4.4, ops_scene reported bpy.app.version back to the caller, and NOTHING anywhere checked either. The
addon's own comment was honest about it -

    # Pinned to 4.4: every io_scene_fbx and bmesh.ops default this addon relies on
    # was read from a live 4.4.0. 3.6 / 4.2 / 5.0 are NOT verified

- which is the same position the engine side was in before the 5.7 probe existed: a claim with
nothing behind it. Andre runs 4.4 and asked about upgrading, and "probably fine" is not an answer
this project gives.

WHAT IT CHECKS, in the order that matters. Each stage is cheap and gates the next:

  1. RUNS          the exe starts and reports bpy.app.version_string
  2. LEGACY FORMAT Blender still accepts a bl_info addon at all. 4.2 introduced the extensions
                   system (blender_manifest.toml) and the question "is legacy still allowed" is
                   answered by asking whether BLENDER ITSELF still ships legacy addons, not by
                   recalling release notes.
  3. IMPORTS       `import MifBlender` with the addon's parent on sys.path
  4. REGISTERS     register() then unregister(), both clean
  5. OP TABLE      the three OPS dicts merge to the expected op count
  6. FBX SURFACE   every kwarg in ops_mesh.FBX_EXPORT_ARGS / FBX_IMPORT_ARGS is still a real
                   property of the operator, and the enum VALUES it passes are still accepted.
                   This is the stage that earns the tool: the exporter's properties genuinely do
                   move between releases - use_ascii vanished in 4.4 - and a missing kwarg is a
                   TypeError at round-trip time, not at install time.
  7. BMESH         the bmesh.ops the mesh ops lean on still exist

WHAT IT DOES NOT DO, deliberately:

  * It never installs the addon into the user's Blender profile. addon_install writes into a real
    user config directory, and a probe that modifies the machine it is measuring is not a probe.
    sys.path plus import is enough to answer every question above.
  * It does not run the OP SUITE. That needs a live server on 127.0.0.1:8792, only one process can
    hold that port, and tools/test_blender_ops.py already does it properly. Run that separately,
    against one version at a time - see --serve below.
  * It never opens a .blend file and always passes --background --factory-startup.

HEADLESS SERVING, if you want the op suite too. `blender -b` runs no event loop, so the addon's
drain timer never fires - which the addon knows, and why register() does not auto-start in
background. The headless entry point is explicit:

    blender --background --factory-startup --python-expr "import sys; sys.path.insert(0, ADDON_DIR); import MifBlender; MifBlender.serve_forever()"

`--serve <version>` prints exactly that command for a version, ready to paste. It is deliberately
not run for you: it BLOCKS, it owns port 8792, and two of them at once produce garbage for both.

Usage:
    python tools/blender_probe.py                 # probe every installed version
    python tools/blender_probe.py --only 5.0      # just one
    python tools/blender_probe.py --serve 5.0     # print the headless serve command
    python tools/blender_probe.py --quiet         # exit code only: 0 all clean, 1 something is not
"""
import argparse
import glob
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(HERE, "blender-addon")
OPS_MESH = os.path.join(ADDON_DIR, "MifBlender", "ops_mesh.py")

SEARCH_GLOBS = [
    r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
    r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
    "/usr/share/blender/*/blender",
    "/Applications/Blender*.app/Contents/MacOS/Blender",
]


def installed():
    """(version-ish label, exe path) for every Blender this machine has, sorted."""
    found = []
    for pattern in SEARCH_GLOBS:
        for exe in glob.glob(pattern):
            m = re.search(r"(\d+\.\d+)", exe)
            found.append((m.group(1) if m else "?", exe))
    return sorted(set(found), key=lambda p: [int(x) for x in p[0].split(".")] if p[0] != "?" else [0])


def fbx_args_from_source():
    """The kwarg names the addon actually passes, READ FROM ITS SOURCE rather than duplicated here.

    A hand-copied list in this file would be a second source of truth that drifts from the first,
    which is the exact failure the whole MifBridge parity discipline exists to prevent. If somebody
    adds a kwarg to FBX_EXPORT_ARGS, this probe starts checking it without anyone editing this file.
    """
    src = io.open(OPS_MESH, encoding="utf-8", errors="replace").read()
    out = {}
    for name in ("FBX_EXPORT_ARGS", "FBX_IMPORT_ARGS"):
        m = re.search(name + r"\s*=\s*\{(.*?)\n\}", src, re.S)
        out[name] = re.findall(r'"([a-z_]+)"\s*:', m.group(1)) if m else []
    # use_selection is passed positionally at the call site, not in the dict.
    if "use_selection" not in out["FBX_EXPORT_ARGS"]:
        out["FBX_EXPORT_ARGS"].append("use_selection")
    return out


PROBE_TEMPLATE = r'''
import sys, traceback
import bpy
sys.path.insert(0, r"{addon}")

def say(*a):
    print("MIFPROBE " + " ".join(str(x) for x in a))

say("version", bpy.app.version_string)

# --- legacy bl_info format still accepted? ask whether BLENDER ships them ------------------
try:
    import addon_utils
    legacy = [m for m in addon_utils.modules() if getattr(m, "bl_info", None)]
    say("legacy", "ok" if legacy else "NONE", len(legacy))
except Exception as exc:
    say("legacy", "UNREADABLE", type(exc).__name__)

# --- import / register ---------------------------------------------------------------------
try:
    import MifBlender
    say("import", "ok")
except Exception:
    say("import", "FAILED")
    for line in traceback.format_exc().splitlines()[-5:]:
        say("  ", line)
    raise SystemExit(0)

try:
    MifBlender.register()
    MifBlender.unregister()
    say("register", "ok")
except Exception:
    say("register", "FAILED")
    for line in traceback.format_exc().splitlines()[-6:]:
        say("  ", line)

# --- op table ------------------------------------------------------------------------------
try:
    from MifBlender import ops_gen, ops_mesh, ops_scene
    names = set()
    for mod in (ops_gen, ops_mesh, ops_scene):
        names |= set(getattr(mod, "OPS", {{}}).keys())
    say("ops", len(names))
except Exception as exc:
    say("ops", "UNREADABLE", type(exc).__name__)

# --- the FBX surface, which is the stage that earns this tool -------------------------------
EXPORT = {export!r}
IMPORT = {import_!r}

def props(cat, name):
    try:
        return set(getattr(getattr(bpy.ops, cat), name).get_rna_type().properties.keys())
    except Exception:
        return None

for label, cat, name, wanted in (("fbxexport", "export_scene", "fbx", EXPORT),
                                 ("fbximport", "import_scene", "fbx", IMPORT)):
    have = props(cat, name)
    if have is None:
        say(label, "OPERATOR_ABSENT")
        continue
    missing = [k for k in wanted if k not in have]
    say(label, "missing" if missing else "ok", len(missing))
    for k in missing:
        say(label, "MISSING", k)

have = props("export_scene", "fbx")
if have:
    rna = bpy.ops.export_scene.fbx.get_rna_type().properties
    for prop, value in (("apply_scale_options", "FBX_SCALE_NONE"),
                        ("mesh_smooth_type", "FACE"),
                        ("colors_type", "SRGB"),
                        ("path_mode", "AUTO")):
        if prop not in have:
            continue
        try:
            items = [i.identifier for i in rna[prop].enum_items]
            say("enum", prop, value, "ok" if value in items else "GONE")
        except Exception as exc:
            say("enum", prop, "UNREADABLE")

# --- bmesh ops -----------------------------------------------------------------------------
import bmesh
for op in ("extrude_face_region", "translate", "delete", "recalc_face_normals",
           "triangulate", "dissolve_limit"):
    say("bmesh", op, "ok" if hasattr(bmesh.ops, op) else "MISSING")

say("done")
'''


def probe(version, exe, script_path):
    """Run one Blender headless and parse its MIFPROBE lines into a verdict."""
    try:
        proc = subprocess.run([exe, "--background", "--factory-startup", "--python", script_path],
                              capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"version": version, "ok": False, "lines": [], "problems": ["timed out after 300s"]}
    except Exception as exc:
        return {"version": version, "ok": False, "lines": [], "problems": ["could not run: %s" % exc]}

    lines = [l[len("MIFPROBE "):].strip()
             for l in (proc.stdout or "").splitlines() if l.startswith("MIFPROBE ")]
    problems = []
    seen = {}
    for l in lines:
        parts = l.split()
        if not parts:
            continue
        seen[parts[0]] = parts[1:] if len(parts) > 1 else []
        if "FAILED" in l or "MISSING" in l or "GONE" in l or "ABSENT" in l or "UNREADABLE" in l:
            problems.append(l)
    if "done" not in seen:
        problems.append("the probe did not reach the end - Blender exited early")
    return {"version": version, "ok": not problems, "lines": lines, "problems": problems,
            "reported": " ".join(seen.get("version", [])) or "?"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="probe just this version, e.g. 5.0")
    ap.add_argument("--serve", help="print the headless serve command for this version and exit")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    a = ap.parse_args()

    versions = installed()
    if not versions:
        print("no Blender found. Looked in:")
        for g in SEARCH_GLOBS:
            print("   " + g)
        return 2

    if a.serve:
        match = [(v, e) for v, e in versions if v == a.serve]
        if not match:
            print("no Blender %s installed. Have: %s" % (a.serve, ", ".join(v for v, _ in versions)))
            return 2
        exe = match[0][1]
        print("# BLOCKS, and owns port 8792. Run tools/test_blender_ops.py against it from another shell.")
        print('"%s" --background --factory-startup --python-expr '
              '"import sys; sys.path.insert(0, r\'%s\'); import MifBlender; MifBlender.serve_forever()"'
              % (exe, ADDON_DIR))
        return 0

    if a.only:
        versions = [(v, e) for v, e in versions if v == a.only]
        if not versions:
            return 2

    args = fbx_args_from_source()
    script = PROBE_TEMPLATE.format(addon=ADDON_DIR,
                                   export=args["FBX_EXPORT_ARGS"],
                                   import_=args["FBX_IMPORT_ARGS"])
    tmp = os.path.join(HERE, "_blender_probe_generated.py")
    io.open(tmp, "wb").write(script.replace("\n", "\r\n").encode("utf-8"))

    results = []
    try:
        for version, exe in versions:
            if not a.quiet:
                print("probing Blender %s ..." % version)
            results.append(probe(version, exe, tmp))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    bad = [r for r in results if not r["ok"]]
    if not a.quiet:
        print("")
        print("%-8s %-12s %s" % ("version", "reported", "verdict"))
        for r in results:
            print("%-8s %-12s %s" % (r["version"], r["reported"],
                                     "clean" if r["ok"] else "%d problem(s)" % len(r["problems"])))
        for r in bad:
            print("")
            print("Blender %s:" % r["version"])
            for p in r["problems"]:
                print("   " + p)
        if not bad:
            print("")
            print("Every installed Blender imports, registers, and still has every FBX kwarg and")
            print("bmesh op the addon passes. That is NOT the same as the ops working - run")
            print("tools/test_blender_ops.py against a served instance for that (--serve).")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
