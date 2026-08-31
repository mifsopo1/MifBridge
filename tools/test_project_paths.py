"""project_paths - where this project actually lives on disk.

WHY IT EXISTS, and why the last test here is the only one that really matters. Several endpoints
hand back a PROJECT-RELATIVE path and gave the caller no way to resolve it: export_landscape_heightmap
returns `file`, backup_blueprint returns `backup`, trigger_cook's plan is full of them. Nothing
reported the project root, so anything wanting to read back a file an endpoint had just written had
to be told the root out of band - and test_uncovered_reads5 duly joined one against a literal
"D:/DDS2SDK/Game", a hardcoded machine path inside a tool meant to work on any project.

So P104 is the point: take a relative path from a DIFFERENT endpoint, resolve it against what this
one reports, and require the file to be there. Everything above it is necessary and none of it would
prove the endpoint does its job.

EVERY PATH IS CHECKED AGAINST THE FILESYSTEM, not just for shape. FPaths returns several of these
relative to the process working directory - the engine's Binaries folder, which no caller can guess -
so "looks like a path" is not the property under test. Absolute AND present is.
"""
import json
import os
import sys

import mifaudit as M

PASS, FAIL = [], []

DIR_KEYS = ("projectDir", "contentDir", "savedDir", "configDir",
            "pluginsDir", "intermediateDir", "logDir", "engineDir")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "\n        " + str(detail)[:300]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ P100 the read
    print("\n=== P100: it answers, and every path is ABSOLUTE ===")
    r = M.call("project_paths", {})
    check("P100 project_paths answers", r.get("ok") is True, json.dumps(r)[:250])
    check("P100 it reports the project's name and .uproject file",
          bool(r.get("projectName")) and str(r.get("projectFile", "")).endswith(".uproject"),
          json.dumps({k: r.get(k) for k in ("projectName", "projectFile")}))

    # THE DESIGN DECISION, asserted. FPaths hands back several of these relative to the process
    # working directory, which is the engine's Binaries folder - a relative answer would leave the
    # caller exactly as stuck as they were before this endpoint existed.
    rel = [k for k in DIR_KEYS + ("projectFile",)
           if r.get(k) and not os.path.isabs(str(r.get(k)))]
    check("P100 every path is absolute - a relative one would be relative to the engine's Binaries "
          "folder and leave the caller as stuck as before",
          not rel, "relative: %s" % [(k, r.get(k)) for k in rel])
    back = [k for k in DIR_KEYS + ("projectFile",) if "\\" in str(r.get(k) or "")]
    check("P100 and uses forward slashes throughout, so a caller can join without escaping",
          not back, "backslashes in: %s" % back)

    # ------------------------------------------------------------------ P101 they are real
    print("\n=== P101: the paths are on disk, not merely well-formed ===")
    missing = [k for k in DIR_KEYS if not os.path.isdir(str(r.get(k) or ""))]
    check("P101 every reported directory really exists - checked against the filesystem, because "
          "'looks like a path' is not the property this endpoint is for",
          not missing, "not directories: %s" % [(k, r.get(k)) for k in missing])
    check("P101 the .uproject file really exists",
          os.path.isfile(str(r.get("projectFile") or "")), r.get("projectFile"))

    # ------------------------------------------------------------------ P102 they agree
    print("\n=== P102: the paths agree with each other ===")
    pdir = str(r.get("projectDir") or "")
    inside = [k for k in ("contentDir", "savedDir", "configDir", "pluginsDir",
                          "intermediateDir", "logDir")
              if not str(r.get(k) or "").lower().startswith(pdir.lower())]
    check("P102 the project's own subdirectories are under projectDir",
          not inside, "outside projectDir: %s" % [(k, r.get(k)) for k in inside])
    # engineDir is deliberately NOT under projectDir - it is the installed engine, and a caller
    # that assumed otherwise would build wrong paths for anything engine-side.
    check("P102 engineDir is NOT under projectDir - it is the installed engine, and conflating the "
          "two is how a caller builds a path to nowhere",
          not str(r.get("engineDir") or "").lower().startswith(pdir.lower()),
          "engineDir=%s projectDir=%s" % (r.get("engineDir"), pdir))
    check("P102 the .uproject sits directly in projectDir",
          os.path.dirname(str(r.get("projectFile") or "")).rstrip("/").lower()
          == pdir.rstrip("/").lower(),
          "%s vs %s" % (r.get("projectFile"), pdir))

    # ------------------------------------------------------------------ P103 the guards
    print("\n=== P103: the refusals ===")
    bad = M.raw_post("project_paths", {"zzz": 1})
    check("P103 an unknown parameter is refused rather than ignored",
          bad.get("ok") is False, json.dumps(bad)[:220])
    proj = M.raw_post("project_paths", {"project": "/Game/Other"})
    check("P103 `project` is refused BY NAME - this reports the RUNNING editor's project and there "
          "is no way to ask it about another one, which is worth saying rather than ignoring",
          proj.get("ok") is False and "RUNNING" in (proj.get("error") or ""),
          (proj.get("error") or "")[:240])

    # ------------------------------------------------------------------ P104 THE POINT
    print("\n=== P104 [the point]: a relative path from ANOTHER endpoint resolves against this ===")
    # The whole reason this endpoint exists. export_landscape_heightmap returns a project-relative
    # `file`; before project_paths there was no portable way to turn that into something openable.
    exp = M.raw_post("export_landscape_heightmap", {})
    if exp.get("ok") is not True or not exp.get("file"):
        print("  NOTE  no landscape in this level, so the cross-endpoint resolution is UNEXERCISED")
        print("        rather than counted. It needs some endpoint to hand back a relative path.")
    else:
        rel_path = str(exp.get("file"))
        resolved = os.path.normpath(os.path.join(pdir, rel_path.lstrip("/\\")))
        check("P104 a project-relative path from export_landscape_heightmap resolves against "
              "projectDir to a file that is really there - which is the entire job",
              os.path.isfile(resolved),
              "file=%r + projectDir=%r -> %r (exists=%s)"
              % (rel_path, pdir, resolved, os.path.isfile(resolved)))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
