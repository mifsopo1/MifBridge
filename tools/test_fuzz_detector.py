"""The ghost detector's own regression test. Runs offline - no editor, no bridge.

The fuzzer is a measuring instrument, and run 4 showed it was miscalibrated in two ways at once:

  * its ghost path stopped being a ghost DURING a run (create_blueprint at 'c' creates it, and
    endpoint_names() is sorted(), so everything after 'c' was asked about a path that existed);
  * it flagged correct empty answers, so six of seven findings were noise and the one that mattered
    was buried in them.

An instrument that reports its own artefacts is worse than no instrument, because the findings look
real. These tests pin the corrected behaviour, and the flag/skip split below is exactly the hand
triage of run 4 - audit_unused and find_assets ghosted a PREFIX and correctly found nothing, while
describe_package, get_dependencies, get_referencers, diff_properties_vs_default and invoke_editor_tab
ghosted an IDENTITY and answered about it anyway.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fz", os.path.join(HERE, "fuzz_endpoints.py"))
fz = importlib.util.module_from_spec(spec)
sys.modules["fz"] = fz
spec.loader.exec_module(fz)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    print("=== the ghost path must differ per endpoint ===")
    a, b = fz.ghost_path("duplicate_asset"), fz.ghost_path("create_blueprint")
    check("distinct per endpoint", a != b, "%s == %s" % (a, b))
    check("still a valid /Game/ package path", a.startswith("/Game/") and " " not in a, a)
    # The whole point: whatever create_blueprint creates cannot be in another endpoint's way.
    check("create_blueprint's ghost is not any other endpoint's ghost",
          b not in (fz.ghost_path(e) for e in ("duplicate_asset", "get_referencers", "describe_package")),
          b)

    print("\n=== correct empty answers are skipped, real ones still flagged ===")
    f = fz.looked_and_found_nothing
    cases = [
        # (name, ghosted keys, response, expected "this is a correct empty answer")
        ("prefix search that matched nothing",      ["pathPrefix"], {"ok": True, "assets": [], "count": 0}, True),
        ("audit over a prefix, all zeroes",         ["pathPrefix"], {"ok": True, "scanned": 0, "unreferenced": 0}, True),
        ("folder filter that matched nothing",      ["folder"],     {"ok": True, "actors": []}, True),
        ("prefix search echoing the queried path",  ["pathPrefix"], {"ok": True, "pathPrefix": "/Game/x", "assets": []}, True),
        ("identity path resolved to nothing",       ["path"],       {"ok": True, "dependencies": []}, False),
        ("identity asset, and it opened a tab",     ["asset"],      {"ok": True, "opened": True}, False),
        ("identity id with an empty node list",     ["blueprintId"], {"ok": True, "nodes": []}, False),
        ("identity mixed with a prefix",            ["path", "pathPrefix"], {"ok": True, "assets": []}, False),
        ("prefix search that DID find something",   ["pathPrefix"], {"ok": True, "assets": [{"p": 1}], "count": 1}, False),
    ]
    for name, keys, payload, want in cases:
        got = f(keys, payload)
        check(("skip:  " if want else "flag:  ") + name, got == want, "got=%s want=%s" % (got, want))

    print("\n=== an endpoint that explicitly says 'not there' is answering, not phantom-succeeding ===")
    # These payloads are the ACTUAL run-5 responses, trimmed. Three are correct answers about a
    # nonexistent thing; the fourth is a real defect and must still be flagged.
    absent = fz.reported_absent
    real = [
        ("describe_package reports existsOnDisk:false",
         {"ok": True, "package": "/Game/ghost", "existsOnDisk": False, "inRegistry": False}, True),
        ("get_dependencies reports packageExists:false",
         {"ok": True, "count": 0, "dependencies": [], "packageExists": False}, True),
        ("get_referencers reports packageExists:false",
         {"ok": True, "count": 0, "referencers": [], "packageExists": False}, True),
        ("invoke_editor_tab's enumerable:false is NOT an existence claim",
         {"ok": True, "manager": "global", "enumerable": False}, False),
    ]
    for name, payload, want in real:
        got = absent(payload)
        check(("skip:  " if want else "flag:  ") + name, got == want, "got=%s want=%s" % (got, want))
    check("a TRUE existence field is not an absence claim",
          absent({"ok": True, "exists": True}) is False, "exists:true must not suppress")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
