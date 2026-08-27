"""Verification for the unchecked-return sweep of 2026-08-27.

WHAT THE SWEEP WAS. Every bare-statement call in the plugin - 513 distinct names, 385 of them not
defined in our own sources - cross-referenced against BOTH engine header trees for a declaration
returning bool. 92 matched. 56 survived excluding the names where discarding the result is universal
and harmless (container mutators, JSON field setters, Modify, MarkPackageDirty). Those 56 were read.

FIVE were real. The rest were either false matches on a same-named overload that returns void, or
were already covered by a read-back - which is the STRONGER check, and the reason several bare calls
in this codebase are deliberately left bare with a comment saying so.

WHAT THIS SUITE COVERS. The one finding that can be exercised without confirm:

  T720-T723  add_struct_member / create_struct. ChangeVariableDefaultValue returns whether the value
             took, and that return was discarded - while the RenameVariable call directly above it
             was checked. It validates the string against the member's pin type and REFUSES one that
             does not parse, so default:"abc" on an int member left the member with no default at
             all, while the response reported the default that had been asked for.

             Verified by read-back rather than by the bool, because the bool ALSO comes back false
             when the value is already what was asked for - which is not a failure.

WHAT THIS SUITE DOES NOT COVER, said plainly rather than left to be assumed:

  * remove_node - a void RemoveNode, a discarded bool, and a fall-through branch that removed
    NOTHING while still reporting `removed`. It needs confirm:true, and scratch_confirm cannot
    unblock it: the endpoint is addressed purely by guid, so no payload can prove it scratch-only.
    Genuinely uncovered on the success path.
  * remove_widget_animation_track - `removedBinding` was reported from the REQUEST flag rather than
    from anything observed. Needs a widget blueprint with an animation and a bound track.
  * snap_actors_to_ground alignRefused - covered by test_snap_ground T66/T67, not here.
  * spawn_actor's mesh read-back - exercised indirectly by test_snap_ground, which spawns meshed
    actors and would fail at setup if the mesh stopped being applied.

Usage:
    python tools/test_unchecked_returns.py
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)[:240]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    stamp = int(time.time() % 100000)
    path = "/Game/_MifStruct/S_Defaults_%d" % stamp
    r = M.call("create_struct", {"path": path, "members": [{"name": "Count", "type": "int"}]})
    sp = r.get("structPath")
    if not sp:
        print("setup failed:", json.dumps(r)[:300])
        return 1
    print("struct: %s" % sp)

    print("")
    print("=== T720: a VALID default is applied, and reported without complaint ===")
    r = M.call("add_struct_member", {"struct": sp, "name": "Good", "type": "int", "default": "7"})
    check("T720 member added", r.get("ok") is True, json.dumps(r)[:240])
    check("T720 no warning for a default that parses", "warning" not in r, r.get("warning"))
    member = r.get("member") or {}
    check("T720 and the default actually took", str(member.get("default")) == "7",
          "member=%s" % json.dumps(member)[:200])

    print("")
    print("=== T721: an INVALID default is REPORTED, not silently dropped ===")
    r = M.call("add_struct_member", {"struct": sp, "name": "Bad", "type": "int", "default": "abc"})
    check("T721 the member is still ADDED, not dropped", r.get("ok") is True, json.dumps(r)[:240])
    check("T721 and the refusal is reported",
          isinstance(r.get("warning"), str) and bool(r.get("warning")),
          "no warning field - the OLD binary reported this call as fully successful: %s"
          % json.dumps(r)[:260])
    w = r.get("warning") or ""
    check("T721 the warning names the value it refused", "abc" in w, w[:200])
    check("T721 and names the member", "Bad" in w, w[:200])
    check("T721 and points at an endpoint that exists", "set_struct_member" in w, w[:200])
    member = r.get("member") or {}
    check("T721 the member does NOT carry the bad default",
          str(member.get("default", "")) != "abc", json.dumps(member)[:200])

    print("")
    print("=== T722: read it back through the ASSET, not through the response ===")
    r = M.call("list_struct_members", {"struct": sp})
    # Keyed on friendlyName, NOT name. A UUserDefinedStruct stores members under a mangled
    # name_index_guid ("Bad_6_25E2646A47C8..."), and `name` reports that. Keying on `name` found
    # nothing, which made the two assertions below it look up None and compare it against "" -
    # they PASSED, vacuously, while proving nothing at all. The two that failed loudly are the only
    # reason that was noticed.
    members = {m.get("friendlyName"): m for m in (r.get("members") or [])}
    check("T722 both members are on the struct", "Good" in members and "Bad" in members,
          sorted(members.keys()))
    check("T722 and the lookup is not vacuous", bool(members.get("Bad")),
          "no member keyed Bad - every check below this would pass without testing anything")
    check("T722 Good kept its default", str((members.get("Good") or {}).get("default")) == "7",
          json.dumps(members.get("Good"))[:200])
    # NOT `no default`. A refused ChangeVariableDefaultValue leaves the default AddVariable already
    # gave the member - for an int that is "0", not empty - so the claim worth testing is that the
    # REQUESTED value never landed, which is exactly what the fix guarantees. Written as `no default`
    # first, and the live editor said otherwise on the first run.
    bad_default = str((members.get("Bad") or {}).get("default", ""))
    check("T722 Bad did NOT take the value that was refused", bad_default != "abc",
          json.dumps(members.get("Bad"))[:200])
    check("T722 and it kept the type's own default instead", bad_default in ("", "0"),
          "default=%r - expected the int zero AddVariable set, or nothing" % bad_default)

    print("")
    print("=== T723: create_struct surfaces the SAME refusal, through warnings[] ===")
    p2 = "/Game/_MifStruct/S_Defaults2_%d" % stamp
    r = M.call("create_struct", {"path": p2,
                                 "members": [{"name": "N", "type": "int", "default": "not-a-number"}]})
    check("T723 the struct was still created", bool(r.get("structPath")), json.dumps(r)[:240])
    warns = r.get("warnings") or []
    check("T723 and the bad default appears in warnings[]",
          any("not-a-number" in str(x) for x in warns),
          "warnings=%s - the helper is shared, so both callers must surface it or the fix half-lands"
          % json.dumps(warns)[:260])

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % (f[0],))
        print("          %s" % (f[1],))
    print("scratch structs left under /Game/_MifStruct - never saved, gone on editor restart.")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
