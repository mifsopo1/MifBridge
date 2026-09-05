"""User-defined structs - add, retype, rename, remove, and the two names every member has.

THE THING THAT MAKES THIS FAMILY AWKWARD, and the reason the suite leads with it: a member you add as
"Price" is stored as `Price_2_4BB2EA0B4B9DC415FC9A60A38E16EA24`. UE mangles struct variable names with
an index and a GUID, and that mangled string is the member's real FName. So a caller who adds a member
and then lists members does not see the name they chose - they see something they never typed and
cannot guess.

That is survivable only because every member reports BOTH: `friendlyName` is what you asked for,
`name` is what it really is. T481 asserts both are present and that they correspond, because a listing
that returned only the mangled name would be technically correct and practically unusable, and one
that returned only the friendly name could not be used to address anything by FName.

The same family is where the cooked-asset hazard is worst: `docs/02_GOTCHAS.md` §6c records that a
CastChecked on a cooked UUserDefinedStruct terminates the editor outright - not an error, a dead
process. T484 hands each read a real cooked struct and checks the editor is still there afterwards.

`set_struct_member` takes `member`, not `name` - deliberately, because `name` would be ambiguous
against `newName` when renaming, and the endpoint says exactly that when you get it wrong. Asserted so
the disambiguation is not quietly dropped later.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    sp = "/Game/_MifStruct/S_%d" % st
    c = M.call("create_struct", {"path": sp})
    check("a struct is created", c.get("ok") is True, json.dumps(c)[:200])
    spath = c.get("structPath") or sp

    def members():
        return M.call("list_struct_members", {"struct": spath}).get("members") or []

    def friendly():
        return [m.get("friendlyName") for m in members()]

    # ------------------------------------------------------------------ T480 add
    print("")
    print("=== T480: members are added and readable ===")
    for n, t in (("Price", "float"), ("Label", "string"), ("Count", "int")):
        r = M.call("add_struct_member", {"struct": spath, "name": n, "type": t})
        check("T480 %s is added" % n, r.get("ok") is True, json.dumps(r)[:180])
    f = friendly()
    for n in ("Price", "Label", "Count"):
        check("T480 %s appears in the listing" % n, n in f, str(f))

    # ------------------------------------------------------------------ T481 the two names
    print("")
    print("=== T481 [the awkward bit]: every member has a mangled name AND a friendly one ===")
    price = next((m for m in members() if m.get("friendlyName") == "Price"), None)
    check("T481 the member is found by its friendly name", price is not None, str(friendly()))
    if price:
        # A listing that returned only the mangled name would be unusable; only the friendly name and
        # nothing could be addressed by FName. Both, or the family does not work.
        check("T481 the real name is the MANGLED one", price.get("name") != "Price"
              and str(price.get("name")).startswith("Price"), json.dumps(price)[:200])
        check("T481 and the friendly name is what was asked for",
              price.get("friendlyName") == "Price", json.dumps(price)[:200])
        check("T481 and it reports a type", bool(price.get("type")), json.dumps(price)[:200])

    # ------------------------------------------------------------------ T482 set/rename
    print("")
    print("=== T482: retyping and renaming a member ===")
    # `member`, not `name` - the endpoint refuses `name` as ambiguous against `newName`, and that
    # disambiguation is worth keeping.
    bad = M.call("set_struct_member", {"struct": spath, "name": "Price", "type": "int"})
    check("T482 'name' is refused as ambiguous", bad.get("ok") is False, json.dumps(bad)[:180])
    check("T482 and the refusal explains which key to use",
          "member" in (bad.get("error") or ""), (bad.get("error") or "")[:190])

    r = M.call("set_struct_member", {"struct": spath, "member": "Price", "type": "int"})
    check("T482 retyping by friendly name succeeds", r.get("ok") is True, json.dumps(r)[:200])
    if r.get("ok"):
        now = next((m for m in members() if m.get("friendlyName") == "Price"), None)
        check("T482 and the listing shows the new type",
              "int" in json.dumps(now or {}).lower(), json.dumps(now)[:200])

    r = M.call("set_struct_member", {"struct": spath, "member": "Count", "newName": "Quantity"})
    if r.get("ok"):
        f = friendly()
        check("T482 renaming a member takes", "Quantity" in f and "Count" not in f, str(f))
    else:
        check("T482 a rename that is refused says why", len(r.get("error") or "") > 15,
              (r.get("error") or "")[:180])

    # ------------------------------------------------------------------ T483 remove
    print("")
    print("=== T483: removal is confirm-gated and takes the right member ===")
    no = M.call("remove_struct_member", {"struct": spath, "name": "Label"})
    check("T483 removal refuses without confirm", no.get("ok") is False, json.dumps(no)[:180])
    check("T483 and says confirm is what is missing", "confirm" in (no.get("error") or ""),
          (no.get("error") or "")[:170])

    before = friendly()
    rm = SC.confirm_call("remove_struct_member", {"struct": spath, "name": "Label"})
    check("T483 the removal succeeds with confirm", rm.get("ok") is True, json.dumps(rm)[:200])
    after = friendly()
    check("T483 the named member is gone", "Label" not in after, str(after))
    # The failure worth catching: removing by a mangled/friendly mismatch could take the WRONG one.
    check("T483 and it took exactly one", len(after) == len(before) - 1,
          "%s -> %s" % (before, after))
    check("T483 leaving the others alone",
          all(x in after for x in before if x != "Label"), "%s -> %s" % (before, after))

    # ------------------------------------------------------------------ T484 the cooked hazard
    print("")
    # COOKED-ONLY, SKIPPED where nothing is cooked. On an uncooked project the
    # refusal this asserts never comes, so the assertion fails for the environment
    # rather than for a defect - and where the call is a write, it lands instead.
    # Section confirmed self-contained by audit_cooked_section_safety before wrapping.
    #
    # `is not False`: project_is_cooked returns None when the question could not be
    # asked, and an unanswerable question is not a No - None runs this as before.
    COOKED = M.project_is_cooked()
    if COOKED is False:
        print("")
        print('=== T484 SKIPPED - nothing in this project is cooked ===')
        print('  This section asserts what an endpoint REFUSES on cooked content. There is nothing cooked')
        print('  here, so the refusal cannot be provoked - which is not the same as the guard being absent.')
        print('  Where the call is a WRITE, running it unguarded would perform the write it means to see')
        print('  refused. Run against a cooked project for this half.')
    else:
        print("=== T484 [the hazard]: a real COOKED struct must not take the editor down ===")
        # SELECT FOR COOKEDNESS, DO NOT ASSUME IT. Skipping scratch is necessary and NOT sufficient:
        # plenty of project structs under /Game/ have intact EditorData and load perfectly well
        # (test_set_struct_member names BCE_DeveloperStruct), so a non-scratch pick can easily be an
        # UNCOOKED struct - and against one of those every read answers normally and T484 goes green
        # having probed nothing at all. find_assets' ordering is not stable either, so which it got was
        # luck that could change between runs.
        #
        # This is the fix the previous comment here described and deferred - "doing the same here is the
        # stronger fix and is filed rather than done". It is done now, mirroring test_set_struct_member:
        # one read per candidate, and only a struct that actually REFUSES as cooked is used. The probe
        # costs a read and cannot drift, because it asks the question rather than inferring it from a
        # path.
        cooked = None
        for _a in (M.call("find_assets", {"class": "UserDefinedStruct", "pathPrefix": "/Game/",
                                          "limit": 25}).get("assets") or []):
            _p = _a.get("path") or ""
            if not _p or _p.startswith(SC.SCRATCH_PREFIXES):
                continue
            _probe = M.raw_post("list_struct_members", {"struct": _p})
            if _probe.get("ok") is False and "COOKED" in (_probe.get("error") or ""):
                cooked = _p
                break
        if cooked:
            print("   using %s" % cooked)
            # gotchas 6c: a CastChecked on cooked editor data terminates the process - not an error, a dead
            # editor. The reads must answer or refuse; either is fine, dying is not.
            for ep, payload in (("list_struct_members", {"struct": cooked}),
                                ("resolve_struct", {"name": cooked.split("/")[-1].split(".")[0]})):
                r = M.call(ep, payload, timeout=60)
                check("T484 %s answers on a cooked struct" % ep, isinstance(r.get("ok"), bool),
                      json.dumps(r)[:180])
                check("T484 and the editor survived %s" % ep, M.bridge_responsive() is True,
                      "the bridge stopped answering - a CastChecked on cooked editor data is fatal")
        else:
            # RECORDED AS A SKIP, and deliberately still a passing row rather than a failure: a project
            # with no cooked UserDefinedStruct is a legitimate place to run this suite (an uncooked 5.7
            # project has none by construction), and failing there would make the suite unrunnable
            # outside DDS2. What changed is that this line now means something specific - 25 candidates
            # were ASKED and none refused as cooked - where before it only meant find_assets returned
            # nothing pickable.
            check("T484 (not exercised: no struct under /Game/ refused as COOKED, so the fatal-cast "
                  "guard has nothing to be proven against here)", True)

        # ------------------------------------------------------------------ T485 guards
        print("")
    print("=== T485: bad references are refused ===")
    q = M.call("add_struct_member", {"struct": "/Game/NoSuchStruct_zz", "name": "X", "type": "float"})
    check("T485 a struct that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("set_struct_member", {"struct": spath, "member": "NoSuchMember_zz", "type": "int"})
    check("T485 a member that does not exist is refused", q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("add_struct_member", {"struct": spath, "name": "Bad", "type": "NotAType_zz"})
    check("T485 an unknown type is refused", q.get("ok") is False, json.dumps(q)[:180])
    q = M.call("resolve_struct", {"name": "NoSuchStruct_zz_%d" % st})
    check("T485 resolving a missing struct answers found:false rather than failing",
          q.get("ok") is True and q.get("found") is False, json.dumps(q)[:180])

    print("")
    print("=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
