"""set_struct_member - rename / retype / re-default an existing struct member in place.

Two things are being tested, and only one of them is the feature.

T151 IS THE FEATURE. The reason this endpoint exists is that remove + re-add mints a NEW GUID and
APPENDS the member at the end - which reorders the struct, breaks every Make/Break Struct pin bound to
it, and drops that column from every row of every dependent DataTable. So the test asserts the GUID is
UNCHANGED and the order is UNCHANGED. A version that quietly did remove+re-add underneath would pass
"the name changed" and fail these.

T153 IS A CRASH GUARD. Every FStructureEditorUtils entry point CastChecked's the struct's EditorData,
which is editor-only and stripped on cook, so touching a COOKED struct is a fatal cast rather than an
error return - and every base-game DDS2 struct is cooked. The test points the endpoint at a real one
and then asserts the editor is still answering. That last assertion is the whole point of the test.
"""
import json
import sys
import time

import mifaudit as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def members(sid):
    return M.call("list_struct_members", {"struct": sid}).get("members") or []


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    stamp = int(time.time() % 100000)
    sp = "/Game/_MifStruct/S_M_%d" % stamp
    c = M.call("create_struct", {"path": sp, "members": [
        {"name": "Amount", "type": "int"},
        {"name": "Labl", "type": "string"},
        {"name": "Tail", "type": "bool"}]})
    sid = c.get("structPath") or sp
    if not c.get("ok"):
        print("setup failed:", json.dumps(c)[:200])
        return 3
    before = members(sid)
    before_names = [m.get("name") for m in before]
    print("struct:", sid[-34:], "with", len(before), "members")

    # ------------------------------------------------------------------ T150 rename
    print("\n=== T150: rename in place ===")
    r = M.call("set_struct_member", {"struct": sid, "member": "Labl", "newName": "Label"})
    print("  ", json.dumps({k: v for k, v in r.items() if k != "dependentDataTables"})[:220])
    check("T150 renamed", r.get("ok") is True and r.get("renamed") is True, json.dumps(r)[:200])
    check("T150 the response carries the member as it now is",
          (r.get("member") or {}).get("name") == "Label", json.dumps(r.get("member"))[:150])

    # ------------------------------------------------------------------ T151 the actual point
    print("\n=== T151 [the point]: GUID and ORDER survive, which remove+re-add would destroy ===")
    after = members(sid)
    # Member names carry their GUID suffix, so a preserved GUID shows up as a preserved suffix.
    old_guid = [n for n in before_names if n.startswith("Labl_")]
    new_guid = [n.split("_")[-1] for n in [m.get("name") for m in after] if "Label_" in n]
    check("T151 the GUID is unchanged",
          bool(old_guid) and bool(new_guid) and old_guid[0].endswith(new_guid[0]),
          "before=%s after=%s - a new GUID means every Make/Break Struct pin just broke"
          % (old_guid, new_guid))
    check("T151 the member did not move to the end",
          [m.get("name").split("_")[0] for m in after] == ["Amount", "Label", "Tail"],
          str([m.get("name").split("_")[0] for m in after]))

    # ------------------------------------------------------------------ T152 retype + default
    print("\n=== T152: retype and re-default ===")
    r = M.call("set_struct_member", {"struct": sid, "member": "Amount", "type": "float",
                                     "default": "2.5"})
    mem = r.get("member") or {}
    check("T152 retyped and redefaulted",
          r.get("ok") is True and r.get("retyped") is True and r.get("redefaulted") is True,
          json.dumps(r)[:200])
    check("T152 the new type reads back", mem.get("category") == "real", json.dumps(mem)[:170])
    check("T152 and the new default", (mem.get("default") or "").startswith("2.5"), mem.get("default"))
    check("T152 order still intact",
          [m.get("name").split("_")[0] for m in members(sid)] == ["Amount", "Label", "Tail"],
          str([m.get("name").split("_")[0] for m in members(sid)]))

    # ------------------------------------------------------------------ T153 the crash guard
    print("\n=== T153 [crash guard]: a COOKED struct is refused, not asserted into ===")
    cooked = None
    for a in (M.call("find_assets", {"class": "UserDefinedStruct", "pathPrefix": "/Game/",
                                     "limit": 8}).get("assets") or []):
        if "_MifStruct" not in (a.get("path") or ""):
            cooked = a.get("path")
            break
    check("T153 found a base-game struct to try", bool(cooked), cooked)
    if cooked:
        q = M.call("set_struct_member", {"struct": cooked, "member": "x", "newName": "y"})
        check("T153 refused", q.get("ok") is False, json.dumps(q)[:170])
        check("T153 and it explains that the editor data was stripped on cook",
              "COOKED" in (q.get("error") or ""), (q.get("error") or "")[:170])
    # THE assertion. FStructureEditorUtils CastChecked's stripped EditorData; if the guard had not
    # held, there would be no editor left to answer this.
    alive = M.call("self_audit", {})
    check("T153 the editor is still alive afterwards", alive.get("ok") is True,
          "a failed guard here is a fatal cast, not an error return")

    # ------------------------------------------------------------------ T154 guards
    print("\n=== T154: the ordinary guards ===")
    for name, payload, expect in (
        ("unknown member", {"struct": sid, "member": "NoSuch", "newName": "x"}, "It has:"),
        ("nothing to change", {"struct": sid, "member": "Label"}, "at least one of"),
        ("no member named", {"struct": sid, "newName": "x"}, "required"),
        ("invalid new name", {"struct": sid, "member": "Label", "newName": "not a name!"},
         "valid identifier"),
    ):
        q = M.call("set_struct_member", payload)
        check("T154 %s refused" % name, q.get("ok") is False, json.dumps(q)[:150])
        check("T154 %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:140])
    check("T154 the struct is unchanged after all those refusals",
          [m.get("name").split("_")[0] for m in members(sid)] == ["Amount", "Label", "Tail"],
          str([m.get("name").split("_")[0] for m in members(sid)]))

    # ------------------------------------------------------------------ T155 dependents
    print("\n=== T155: dependent DataTables are counted ===")
    r = M.call("set_struct_member", {"struct": sid, "member": "Tail", "default": "true"})
    check("T155 the response reports a dependent count",
          isinstance(r.get("dependentDataTableCount"), (int, float)),
          json.dumps({k: v for k, v in r.items() if k != "member"})[:200])

    M.call("delete_asset", {"path": sp})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
