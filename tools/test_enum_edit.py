"""set_enum_value - and a crash bomb create_asset had been shipping.

T6000 IS THE IMPORTANT ONE AND IT IS A REGRESSION TEST, not a feature test. Writing this endpoint
killed the editor:

    Assertion failed: CppForm == ECppForm::Namespaced
    [UserDefinedEnum.cpp:49, in GenerateFullEnumName]

create_asset made a UserDefinedEnum with a bare NewObject, add_enum_value was called on it, and the
process died. FEnumEditorUtils::CreateUserDefinedEnum - the stock "Add Enumeration" action - does
the same NewObject and then two more things (EnumEditorUtils.cpp:46-52):

    Enum->SetEnums(EmptyNames, UEnum::ECppForm::Namespaced);
    Enum->SetMetaData(TEXT("BlueprintType"), TEXT("true"));

Without the first, CppForm stays Regular and the FIRST operation that names an enumerator asserts.
The asset looked perfectly fine in the content browser until something touched it. That is exactly
the shape already recorded in create_asset for ULevelSequence ("a bare NewObject IS malformed"),
one step worse - malformed there, fatal here.

So this suite creates an enum and immediately adds three entries, then asks whether the editor is
still answering. That question IS the assertion: a failure here is a dead process, not a bad
response.

WHAT THE ENDPOINT ITSELF ADDS. Renaming is already reachable - DisplayNameMap is a plain UPROPERTY
TMap and set_property has no editability gate - so rename here is a HARDENING that adds the
duplicate check a raw property write skips. Reordering and bitflags are genuinely unreachable:
UEnum::Names is a protected non-UPROPERTY and the bitflags state is metadata rather than a property.

T6003 IS THE SCOPE RULE. bitflags belongs to the ENUM and index/value address an ENTRY; a call
carrying both is refused rather than served in some arbitrary order, because either order would
surprise half the callers.

ALSO CLOSED HERE: every enum endpoint went through a loader with no cooked check, and DisplayNameMap
SURVIVES the cook - so a user-defined enum mounted from a .pak loaded fine and every write against
it reported success and evaporated on restart. That hole existed in add_enum_value and
remove_enum_value too; the fix is in the shared loader, so all of them got it.

CLEANS UP: the scratch enum is deleted at the end.
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

    path = "/Game/_MifEnum/E_MifTest%d" % (int(time.time()) % 100000)
    made = None
    try:
        # ------------------------------------------------------------------ T6000 the crash
        print("=== T6000: a freshly created enum must survive being written to ===")
        c = M.raw_post("create_asset", {"path": path, "class": "UserDefinedEnum"})
        check("T6000 create_asset makes a UserDefinedEnum", c.get("ok") is True,
              json.dumps(c)[:220])
        made = c.get("assetPath")
        if not made:
            return 1

        for value in ("Alpha", "Beta", "Gamma"):
            r = M.raw_post("add_enum_value", {"enum": made, "value": value})
            check("T6000 add_enum_value('%s') succeeds" % value, r.get("ok") is True,
                  json.dumps(r)[:200])

        # THE assertion. Before create_asset initialised CppForm, the first of those calls
        # terminated the editor - so the process answering is the whole proof.
        alive = M.call("self_audit", {})
        check("T6000 - the editor is still alive, which is the regression this suite exists for",
              alive.get("ok") is True,
              "a bare NewObject<UUserDefinedEnum> asserts on the first enumerator named")

        # ------------------------------------------------------------------ T6001 rename
        print("\n=== T6001: renaming, with the duplicate check a raw write skips ===")
        r = M.raw_post("set_enum_value", {"enum": made, "value": "Beta", "newName": "Bravo"})
        check("T6001 an entry can be renamed by its current display name", r.get("ok") is True,
              json.dumps(r)[:250])
        check("T6001 it reports which index it hit and what was there before",
              r.get("index") == 1 and r.get("wasNamed") == "Beta",
              json.dumps({k: r.get(k) for k in ("index", "wasNamed")}))
        check("T6001 and the whole order comes back so the result is visible, not inferred",
              r.get("entries") == ["Alpha", "Bravo", "Gamma"], json.dumps(r.get("entries")))

        dup = M.raw_post("set_enum_value", {"enum": made, "index": 0, "newName": "Bravo"})
        # This is what rename-via-set_property does NOT check: two entries with the same display
        # name compile, and then every switch on the enum has two indistinguishable pins.
        check("T6001 a duplicate display name is refused - the check a raw property write skips",
              dup.get("ok") is False and "indistinguishable pins" in (dup.get("error") or ""),
              (dup.get("error") or "")[:220])

        # ------------------------------------------------------------------ T6002 reorder
        print("\n=== T6002: reordering, which nothing reflective could reach ===")
        mv = M.raw_post("set_enum_value", {"enum": made, "index": 0, "moveTo": 2})
        check("T6002 an entry can be moved", mv.get("ok") is True and mv.get("movedTo") == 2,
              json.dumps(mv)[:250])
        # Read back from the enum: the move returns void, and an off-by-one here is invisible.
        check("T6002 and the order really changed, read back from the enum",
              mv.get("entries") == ["Bravo", "Gamma", "Alpha"], json.dumps(mv.get("entries")))
        check("T6002 with a warning that indices moved but stored values did not",
              "saved an index" in (mv.get("reorderNote") or ""),
              (mv.get("reorderNote") or "")[:200])
        same = M.raw_post("set_enum_value", {"enum": made, "index": 1, "moveTo": 1})
        check("T6002 moving an entry to where it already is succeeds and says so",
              same.get("ok") is True and "nothing moved" in (same.get("note") or ""),
              (same.get("note") or "")[:180])
        bad = M.raw_post("set_enum_value", {"enum": made, "index": 0, "moveTo": 99})
        check("T6002 an out-of-range target is refused with the valid range",
              bad.get("ok") is False and "0..2" in (bad.get("error") or ""),
              (bad.get("error") or "")[:200])

        # ------------------------------------------------------------------ T6003 bitflags
        print("\n=== T6003: bitflags is metadata, and is enum-scoped ===")
        on = M.raw_post("set_enum_value", {"enum": made, "bitflags": True})
        check("T6003 bitflags can be turned on",
              on.get("ok") is True and on.get("bitflags") is True and on.get("changed") is True,
              json.dumps({k: on.get(k) for k in ("bitflags", "changed")}))
        check("T6003 with a warning that it does NOT renumber existing entries",
              "does NOT renumber" in (on.get("bitflagsNote") or ""),
              (on.get("bitflagsNote") or "")[:200])
        again = M.raw_post("set_enum_value", {"enum": made, "bitflags": True})
        check("T6003 setting it again succeeds with changed:false",
              again.get("ok") is True and again.get("changed") is False, json.dumps(again)[:200])
        off = M.raw_post("set_enum_value", {"enum": made, "bitflags": False})
        check("T6003 and it can be turned back off",
              off.get("ok") is True and off.get("bitflags") is False
              and off.get("changed") is True, json.dumps(off)[:200])

        mixed = M.raw_post("set_enum_value", {"enum": made, "bitflags": True, "index": 0})
        # THE scope rule. Either order would surprise half the callers, so neither is chosen.
        check("T6003 mixing enum-scoped bitflags with an entry-scoped index is refused",
              mixed.get("ok") is False and "pick an order" in (mixed.get("error") or ""),
              (mixed.get("error") or "")[:220])
        nothing = M.raw_post("set_enum_value", {"enum": made})
        check("T6003 a call that changes nothing is refused rather than being a silent no-op",
              nothing.get("ok") is False, (nothing.get("error") or "")[:180])

        # ------------------------------------------------------------------ T6004 addressing
        print("\n=== T6004: addressing an entry that is not there ===")
        noval = M.raw_post("set_enum_value", {"enum": made, "value": "NotAnEntry",
                                              "newName": "X"})
        check("T6004 an unknown display name is refused and the real ones listed",
              noval.get("ok") is False and "Bravo" in (noval.get("error") or ""),
              (noval.get("error") or "")[:220])
        noidx = M.raw_post("set_enum_value", {"enum": made, "index": 99, "newName": "X"})
        check("T6004 an out-of-range index names the valid range",
              noidx.get("ok") is False and "0..2" in (noidx.get("error") or ""),
              (noidx.get("error") or "")[:200])
        check("T6004 - the editor is still alive after every refusal",
              M.call("self_audit", {}).get("ok") is True, "enum edits reach engine assert paths")
    finally:
        if made:
            SC.confirm_call("delete_asset", {"path": made})
        left = [a["path"] for a in
                (M.call("find_assets", {"pathPrefix": "/Game/_MifEnum"}).get("assets") or [])
                if made and made in a["path"]]
        check("T6005 (cleanup) the scratch enum is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
