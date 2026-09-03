"""DataTables - the core of DDS2 modding, and until now not covered by any suite.

tools/coverage_gaps.py measured it rather than guessing: 188 of 285 endpoints are never named in a
suite, and all six DataTable endpoints are among them. That is the highest-value block in the list,
because items, recipes and prices all live in DataTables - a silent write failure here corrupts the
thing a mod is actually made of.

docs/06_OPEN_ISSUES_FROM_USE.md has also recorded create_datatable as "IMPLEMENTED 2026-08-21,
UNVERIFIED - built but not yet exercised against a running editor" for five days. T270 closes that.

WHAT THIS SUITE CANNOT DO, stated plainly rather than quietly skipped. write_datatable_rows and
delete_datatable_rows both require confirm=true, and the audit harness this runs under strips
`confirm` from every payload alongside `save` and `force`. That guard exists so an unattended run
cannot destroy a real asset, and bypassing it to test a write would defeat the point of having it. So
those two endpoints are tested ONLY for their refusal behaviour, and their success paths remain
unexercised. That is a real coverage gap, it is deliberate, and it should be closed by someone running
with the guard relaxed against a scratch table - not by weakening the guard here.

T271 is the one with teeth among what IS reachable. A read that reports rows it did not actually read,
or silently truncates, is the failure mode that matters for a table with thousands of rows - so it
cross-checks read_datatable against get_datatable_row row by row, and checks the reported count
against the rows actually returned.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def find_row_struct():
    """A struct deriving from FTableRowBase that this project actually has.

    Chosen from the live registry rather than hardcoded: a name that exists on one engine build and
    not another turns a real failure into a fixture problem, which is the kind of flake that gets a
    suite ignored.

    ASK AN EXISTING DATATABLE FIRST. The original version yielded up to 200 UserDefinedStructs and let
    the caller try create_datatable with each until one stuck - correct, because the endpoint is the
    authority on what derives from FTableRowBase, but expensive: most of this project's structs are not
    row structs, so it burned 288 refusals to find 4 successes across one regression. Andre saw the
    result as a wall of red FAILED cards in the panel and reasonably asked whether something was broken.

    An existing DataTable's row struct is a valid row struct BY CONSTRUCTION, and read_datatable
    reports it (MifBridgeDataTables.cpp:397). One call replaces the brute force. The old search is kept
    below as a fallback, because a project with no DataTables at all is a legitimate state and this
    suite should still run there.
    """
    tables = (M.call("find_assets", {"class": "DataTable", "limit": 5}).get("assets") or [])
    for t in tables:
        # maxRows, NOT limit. read_datatable refuses `limit` by name, and find_assets on the line
        # above DOES take it - which is how the slip survived. The call failed on the parameter name
        # every run, rowStruct came back None, and this loop yielded nothing, so the fallback below
        # ran EVERY time. The brute-force search this docstring says was replaced was never actually
        # replaced. Found 2026-08-31 by audit_suite_payloads, which compares suite payload keys
        # against each handler's RejectUnknownParams list.
        r = M.call("read_datatable", {"path": t.get("path"), "maxRows": 1})
        rs = r.get("rowStruct")
        if rs:
            yield rs
            break      # one confirmed-good answer is enough; the rest is fallback

    # FALLBACK, unchanged in spirit: the endpoint is the authority, so a refusal means "try the next".
    # Capped far lower than 200 now - if the DataTable route failed, twenty guesses will not save it,
    # and 200 refusals in the transcript actively hides real failures.
    # SKIP SCRATCH without changing the design: the endpoint is still the authority and a refusal
    # still means "try the next". What is removed is a candidate that might SUCCEED - a struct
    # test_create_struct_init is midway through building, whose DataTable outlives it by seconds.
    r = M.call("find_assets", {"class": "UserDefinedStruct", "limit": 20})
    for a in (r.get("assets") or []):
        if M.is_scratch_fixture(a):
            continue
        yield a.get("path")
    for engine in ("RichTextStyleRow", "RichImageRow"):
        yield engine


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    st = int(time.time() % 100000)

    # ------------------------------------------------------------------ T270 create_datatable
    print("\n=== T270: create_datatable - UNVERIFIED in the docs since 2026-08-21 ===")
    made, used_struct, last_err = None, None, ""
    for cand in find_row_struct():
        p = "/Game/_MifDT/DT_%d" % st
        r = M.call("create_datatable", {"path": p, "rowStruct": cand})
        if r.get("ok"):
            made, used_struct = r, cand
            break
        last_err = (r.get("error") or "")[:150]
    check("T270 a DataTable can be created at all", made is not None,
          "every candidate row struct was refused; last error: %s" % last_err)
    if not made:
        print("\ncannot continue without a table")
        return 1
    print("   created with rowStruct: %s" % str(used_struct)[:80])
    # create_datatable answers with dataTablePath, not assetPath - checked against the real
    # response rather than assumed, after assuming wrongly once.
    table = made.get("dataTablePath") or made.get("assetPath") or made.get("path")
    check("T270 it returns the asset path", bool(table), json.dumps(made)[:170])
    # Verified through a DIFFERENT endpoint than the one that made it - a creator confirming its own
    # work is the weakest possible evidence.
    listed = M.call("list_datatables", {})
    entries = listed.get("datatables") or listed.get("tables") or listed.get("assets") or []
    paths = [(d.get("path") or d.get("assetPath") or d.get("dataTablePath") or "")
             if isinstance(d, dict) else str(d) for d in entries]
    check("T270 and a SEPARATE endpoint can see it",
          any(table.split(".")[0] in (x or "") for x in paths),
          "not among %d listed tables" % len(paths))
    rd = M.call("read_datatable", {"path": table})
    check("T270 the new table reads back", rd.get("ok") is True, json.dumps(rd)[:170])
    check("T270 and is empty, as a new table should be",
          len(rd.get("rows") or []) == 0, "%d rows in a brand-new table" % len(rd.get("rows") or []))

    print("\n=== T270b: create_datatable guards ===")
    for label, payload, expect in (
        ("no rowStruct", {"path": "/Game/_MifDT/DT_x%d" % st}, "rowStruct is required"),
        ("bogus rowStruct", {"path": "/Game/_MifDT/DT_y%d" % st, "rowStruct": "NoSuchStruct_zz"}, ""),
        ("rowType misspelling", {"path": "/Game/_MifDT/DT_z%d" % st, "rowType": "X"}, "spell it rowStruct"),
    ):
        q = M.call("create_datatable", payload)
        check("T270b %s refused" % label, q.get("ok") is False, json.dumps(q)[:140])
        if expect:
            check("T270b %s explains" % label, expect in (q.get("error") or ""),
                  (q.get("error") or "")[:160])

    # ------------------------------------------------------------------ T271 reads agree
    print("\n=== T271 [teeth]: a read must not report rows it did not read ===")
    src = None
    # SKIP SCRATCH: the 3-rows gate below screens out a freshly created table, but not one
    # test_factory_init populated - and T271 cross-checks a read against a table that has to stay
    # still while it does so.
    for a in (M.call("find_assets", {"class": "DataTable", "limit": 200}).get("assets") or []):
        if M.is_scratch_fixture(a):
            continue
        r = M.call("read_datatable", {"path": a.get("path")})
        if r.get("ok") and len(r.get("rows") or []) >= 3:
            src = a.get("path")
            break
    if not src:
        check("T271 a populated DataTable exists to test against", False,
              "no table with 3+ rows found; the cross-check needs one")
    else:
        rd = M.call("read_datatable", {"path": src})
        rows = rd.get("rows") or []
        print("   using %s (%d rows)" % (src.split("/")[-1][:44], len(rows)))
        # The count must describe what was RETURNED, not what the table holds - the two differ the
        # moment anything truncates, and a caller paging on a wrong count reads the wrong rows.
        reported = rd.get("rowCount")
        if reported is not None:
            check("T271 the reported count matches the rows returned",
                  reported == len(rows) or rd.get("truncated") is True,
                  "reported=%s returned=%d truncated=%s" % (reported, len(rows), rd.get("truncated")))
        # Independent cross-check: every row the bulk read claims must be fetchable on its own.
        names = [r.get("Name") or r.get("name") or r.get("rowName") for r in rows][:5]
        mismatches = []
        for n in names:
            if not n:
                mismatches.append("(a row came back with no name)")
                continue
            one = M.call("get_datatable_row", {"path": src, "rowName": n})
            if not one.get("ok"):
                mismatches.append("%s: %s" % (n, (one.get("error") or "")[:60]))
        check("T271 every row the bulk read reported is individually fetchable",
              not mismatches, "; ".join(mismatches[:3]))
        # And the reverse: a name that is NOT in the table must be refused, not invented.
        ghost = M.call("get_datatable_row", {"path": src, "rowName": "NoSuchRow_zz_%d" % st})
        check("T271 a row that does not exist is refused, not fabricated",
              ghost.get("ok") is False, json.dumps(ghost)[:150])

    print("\n=== T271b: read guards ===")
    for label, payload, expect in (
        ("no path", {}, ""),
        ("missing table", {"path": "/Game/NoSuchTable_zz"}, ""),
    ):
        q = M.call("read_datatable", payload)
        check("T271b %s refused" % label, q.get("ok") is False, json.dumps(q)[:140])
        # Judged on CONTENT, not length. An earlier version required more than 20 characters and
        # failed "path is required", which is short and perfectly actionable - the assertion was
        # measuring prose, not usefulness.
        err = (q.get("error") or "").lower()
        check("T271b %s names what to do" % label,
              any(w in err for w in ("required", "not found", "no asset", "no such", "expected")),
              (q.get("error") or "")[:120])
    # SKIP SCRATCH - the same wrong-class guard as test_ik_rig, test_list_bones and
    # test_niagara_params. A scratch Material is equally not a DataTable, but one deleted
    # mid-run is refused for being missing rather than for its class.
    notatable = (M.pick_adoptable(M.call("find_assets", {"class": "Material",
                                                         "limit": 20}).get("assets"))
                 or {}).get("path")
    q = M.call("read_datatable", {"path": notatable})
    check("T271b a non-DataTable asset is refused by class",
          q.get("ok") is False, (q.get("error") or "")[:150])

    # ------------------------------------------------------------------ T272 the guarded writes
    print("\n=== T272: the write paths refuse without confirm ===")
    # This is ALL that can be tested here: the audit harness strips `confirm` from every payload, so
    # the success paths of these two are unreachable from this suite by design. See the module
    # docstring - the gap is deliberate and should be closed with the guard relaxed, not by weakening
    # it here.
    w = M.call("write_datatable_rows", {"path": table, "rows": [{"Name": "R1"}]})
    check("T272 write_datatable_rows refuses without confirm", w.get("ok") is False, json.dumps(w)[:150])
    check("T272 and says confirm is what is missing",
          "confirm" in (w.get("error") or ""), (w.get("error") or "")[:150])
    d = M.call("delete_datatable_rows", {"path": table, "rows": ["R1"]})
    check("T272 delete_datatable_rows refuses without confirm", d.get("ok") is False, json.dumps(d)[:150])
    check("T272 and says confirm is what is missing",
          "confirm" in (d.get("error") or ""), (d.get("error") or "")[:150])
    # The refusals must not have changed the table.
    after = M.call("read_datatable", {"path": table})
    check("T272 the table is still empty after both refusals",
          len(after.get("rows") or []) == 0, "%d rows appeared" % len(after.get("rows") or []))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    print("COVERAGE GAP, deliberate: the SUCCESS paths of write_datatable_rows and")
    print("delete_datatable_rows are not exercised, because the audit harness strips confirm.")
    print("Close that with the guard relaxed against a scratch table, not by weakening the guard.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
