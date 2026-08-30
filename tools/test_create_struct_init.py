"""create_asset must not be able to produce a UUserDefinedStruct that every struct endpoint refuses.

WHY THIS EXISTS. create_asset builds any concrete UObject with a bare NewObject. For a
UUserDefinedStruct that is not enough by a wide margin: the engine's own
FStructureEditorUtils::CreateUserDefinedStruct (StructureEditorUtils.cpp:41-63) does that same
NewObject and then SEVEN more things - an EditorData sub-object, a Guid, the BlueprintType
metadata, Bind(), StaticLink(true), a Status, and one default member, because the engine does not
allow a zero-member user struct.

The load-bearing one is EditorData. Every FStructureEditorUtils entry point CastChecks it
(GetVarDesc at :648, AddVariable at :249), and CastChecked on null TERMINATES the editor. That
crash never reached a caller only because LoadUserStruct already refuses a null EditorData - a
guard written for COOKED structs, which happen to fail the same way. So the visible symptom was an
asset that looked fine in the content browser and that every struct endpoint rejected while naming
the wrong cause.

FOUND BY AUDIT, NOT BY AN EDITOR DYING, which is the point. tools/audit_factory_init.py had
admitted a blind spot - it only reads UFactory::FactoryCreateNew, so it could never have found the
UUserDefinedEnum crash, which comes from FEnumEditorUtils. Extending it to editor-utils creation
paths found this on the first pass.

THE FIX CALLS THE ENGINE RATHER THAN COPYING IT. Seven lines of engine internals replicated in a
second place is the parallel-implementation mistake and it drifts, so create_asset now constructs
this one class through FStructureEditorUtils::CreateUserDefinedStruct itself. T6210 therefore
asserts the POSTCONDITION - that the struct is actually usable - and not the presence of a note,
because a note is not a working asset.

T6212 IS A REGRESSION GUARD ON AN ERROR MESSAGE, which is unusual and deliberate. LoadUserStruct
used to blame every null EditorData on the package being cooked. That is right for a cooked struct
and a false trail for one that was simply built wrong, so the diagnosis now checks the package and
says which of the two it is. Both arms have to keep working: a wrong diagnosis sends someone
hunting a problem they do not have.

ONE BRANCH IS NOT EXERCISED and this suite says so rather than implying coverage: "not cooked and
no EditorData" is now unreachable THROUGH create_asset, because create_asset can no longer produce
that state. It stays as a defensive branch for any other route into LoadUserStruct.
"""
import json
import sys
import time

import mifaudit as M
import scratch_confirm as SC

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    if M.write_mode() == "read":
        print("SKIPPED - read mode")
        return 0

    st = int(time.time()) % 100000
    made = []
    try:
        # -------------------------------------------------- T6210 the asset must actually work
        print("=== T6210: create_asset produces a struct that struct endpoints can USE ===")
        a = M.raw_post("create_asset", {"path": "/Game/_MifStruct/S_A%d" % st,
                                        "class": "UserDefinedStruct"})
        check("T6210 the struct is created", a.get("ok") is True, json.dumps(a)[:220])
        path = a.get("assetPath")
        if path:
            made.append(path)
        if not path:
            print("cannot continue without an asset")
            return 1

        # THE assertion. Before the fix this call returned an error - and the wrong error - which
        # is the whole defect. Reading the members is the proof EditorData exists, because
        # GetVarDesc CastChecks it.
        lst = M.raw_post("list_struct_members", {"struct": path})
        check("T6210 its members can be read at all, which is the proof EditorData exists",
              lst.get("ok") is True, json.dumps(lst)[:260])
        check("T6210 and it carries the engine's one default member, not zero",
              lst.get("count") == 1, json.dumps(lst)[:260])

        # Reading is the weaker half. Writing goes through AddVariable, which CastChecks
        # EditorData at a different line - so a struct that reads but cannot be written to would
        # still be broken.
        add = M.raw_post("add_struct_member", {"struct": path, "name": "Score", "type": "int"})
        check("T6210 a member can be ADDED, which exercises a second CastChecked path",
              add.get("ok") is True, json.dumps(add)[:220])
        after = M.raw_post("list_struct_members", {"struct": path})
        check("T6210 and the new member is really there afterwards, read back from the asset",
              after.get("count") == 2
              and any((m.get("friendlyName") or m.get("name") or "").startswith("Score")
                      for m in (after.get("members") or [])),
              json.dumps(after)[:300])

        # -------------------------------------------------- T6211 say how it was built
        print("\n=== T6211: the response explains the default member rather than surprising you ===")
        note = a.get("structNote") or ""
        check("T6211 a note is present", bool(note), json.dumps(a)[:200])
        check("T6211 it says the engine's own creator was used, not a bare NewObject",
              "CreateUserDefinedStruct" in note, note[:200])
        # A caller who did not ask for a member will otherwise think something is wrong.
        check("T6211 it warns about the one default member the engine insists on",
              "default boolean member" in note, note[:250])
        check("T6211 and points at create_struct, which takes members up front",
              "create_struct" in note, note[:250])

        # -------------------------------------------------- T6212 the diagnosis must stay right
        print("\n=== T6212: a COOKED struct is still diagnosed as cooked ===")
        cooked = [x["path"] for x in
                  (M.call("find_assets", {"class": "UserDefinedStruct",
                                          "limit": 8}).get("assets") or [])
                  if not x["path"].startswith("/Game/_Mif")]
        if cooked:
            c = M.raw_post("list_struct_members", {"struct": cooked[0]})
            check("T6212 a cooked struct is refused", c.get("ok") is False, json.dumps(c)[:200])
            # The regression that matters: splitting the diagnosis must not have broken the arm
            # that was already correct.
            check("T6212 and it is still told it is COOKED, not mis-blamed on construction",
                  "COOKED" in (c.get("error") or ""), (c.get("error") or "")[:220])
            check("T6212 the cooked message does NOT mention bare NewObject - that is the OTHER "
                  "cause, and naming both would be no better than naming the wrong one",
                  "NewObject" not in (c.get("error") or ""), (c.get("error") or "")[:250])
        else:
            print("  NOTE  no cooked struct in this project, so T6212 is unexercised here.")

        print("\n  NOT EXERCISED: the 'not cooked and no EditorData' arm. create_asset can no")
        print("  longer produce that state, which was the point of the fix, so it is unreachable")
        print("  from here and stays as a defensive branch for other routes into LoadUserStruct.")

        # -------------------------------------------------- T6213 the two routes agree
        print("\n=== T6213: create_asset and create_struct produce the same kind of thing ===")
        b = M.raw_post("create_struct", {"path": "/Game/_MifStruct/S_B%d" % st})
        if b.get("ok"):
            made.append(b.get("structPath") or b.get("assetPath"))
            direct = M.raw_post("list_struct_members",
                                {"struct": b.get("structPath") or b.get("assetPath")})
            # If the two routes disagreed, one of them would be the wrong way to make a struct -
            # and callers have no way to know which.
            check("T6213 the dedicated endpoint's struct reads back the same way",
                  direct.get("ok") is True and direct.get("count") == lst.get("count"),
                  "create_struct count=%s vs create_asset count=%s"
                  % (direct.get("count"), lst.get("count")))
        else:
            print("  NOTE  create_struct declined here: %s" % (b.get("error") or "")[:120])

        check("T6213 - the editor is still alive",
              M.call("self_audit", {}).get("ok") is True,
              "a null EditorData reaching FStructureEditorUtils is a CastChecked termination")
    finally:
        for p in [m for m in made if m]:
            SC.confirm_call("delete_asset", {"path": p})

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
