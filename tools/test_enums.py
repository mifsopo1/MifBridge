"""User-defined enums, and the identity a read used to throw away.

FOUND BY HUNTING, 2026-08-26. add_enum_value {"value": "Common"} correctly answered
displayName "Common", name "NewEnumerator0". list_enum_values on the same asset then answered
["NewEnumerator0", "NewEnumerator1", "NewEnumerator2"] and nothing else - so the write endpoint set
information the read endpoint discarded, and a caller had no way to map a value back to the name they
gave it. ok:true throughout, which is what made it worth finding.

For a UserDefinedEnum the authored name is ALWAYS NewEnumeratorN. The name a person chose lives in the
display name. Reporting only the former is not a formatting preference; it is returning a list of
meaningless strings.

T302 is the regression: entries[] must pair every authored name with its display name, and the two
must actually differ on a user-defined enum - otherwise the test is passing against a fixture that
cannot show the bug.

T303 guards the FIX rather than the original bug. The first version of the fix warned whenever any
display name differed from its authored name, which fires on almost every native enum too, because UE
prettifies "HitTestInvisible" into "Hit Test Invisible". A warning that fires on ESlateVisibility is
noise, and noise is how a real warning gets ignored. The condition is now the enum's CLASS.

T301 (added 2026-08-28, from docs/06_OPEN_ISSUES_FROM_USE.md section 13C) covers create_enum's OWN
`values[]` array path specifically - a genuinely different code path from T300's one-at-a-time
add_enum_value loop, and the one that actually held the bug that section documented: create_enum
pre-checked IsProperNameForUserDefinedEnumerator, which validates the AUTHORED name and - since a
UserDefinedEnum's authored names are always NewEnumeratorN - has essentially nothing to reject, so a
duplicate DISPLAY name inside values[] passed silently while SetEnumeratorDisplayName's real refusal
was discarded. Fixed in commit 9525ce5 (2026-08-26) to check that return value and warn per entry -
but nothing had ever exercised create_enum's values[] parameter with an actual duplicate in it, so
the fix shipped with zero coverage of its own code path until this test.

T305 was ALSO stale: it framed remove_enum_value's success path as a deliberate, permanent gap
because confirm-gated calls were unreachable through this harness. That stopped being true once
scratch_confirm.py landed - remove_enum_value is one of the nine endpoints it genuinely unblocks
(named by path, not guid), and test_confirm_gated.py's T345 already proves the real removal works.
Upgraded here too rather than leaving a second copy of the same corrected claim.
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

    # ------------------------------------------------------------------ T300 create + add
    print("\n=== T300: creating an enum and adding entries ===")
    path = "/Game/_MifEnum/E_%d" % st
    c = M.call("create_enum", {"path": path})
    check("T300 an enum is created", c.get("ok") is True, json.dumps(c)[:180])
    ep = c.get("enumPath")
    check("T300 it returns the enum path", bool(ep), json.dumps(c)[:150])
    check("T300 and starts empty", (c.get("values") or []) == [], json.dumps(c.get("values")))

    wanted = ["Common", "Rare", "Legendary"]
    added = []
    for v in wanted:
        a = M.call("add_enum_value", {"enum": ep, "value": v})
        added.append(a)
        check("T300 '%s' is added" % v, a.get("ok") is True, json.dumps(a)[:150])
    # The write endpoint has always been honest about the split; that is worth pinning down, because
    # the read endpoint's bug was losing exactly this.
    check("T300 add reports the display name it was given",
          [a.get("displayName") for a in added] == wanted,
          str([a.get("displayName") for a in added]))
    check("T300 and the DIFFERENT authored name the engine assigned",
          all((a.get("name") or "").startswith("NewEnumerator") for a in added),
          str([a.get("name") for a in added]))
    check("T300 indices are sequential", [a.get("index") for a in added] == [0, 1, 2],
          str([a.get("index") for a in added]))

    # ------------------------------------------------------------------ T301 create_enum's values[]
    print("\n=== T301: create_enum's OWN values[] path - a well-formed list, then a duplicate ===")
    p301 = "/Game/_MifEnum/E_%d_v" % st
    clean = M.call("create_enum", {"path": p301, "values": ["Gold", "Silver"]})
    check("T301 a well-formed values[] list succeeds", clean.get("ok") is True, json.dumps(clean)[:200])
    check("T301 no warnings when every name is genuinely unique",
          "warnings" not in clean, clean.get("warnings"))
    vals = clean.get("values") or []
    check("T301 both display names came back as given",
          [v.get("displayName") for v in vals] == ["Gold", "Silver"], vals)
    ep301 = clean.get("enumPath")
    l301 = M.call("list_enum_values", {"enum": ep301}).get("entries") or []
    check("T301 and the read-back agrees", [e.get("displayName") for e in l301] == ["Gold", "Silver"], l301)
    SC.confirm_call("delete_asset", {"path": p301})

    # THE regression from docs/06 section 13C: a values[] array with a genuine duplicate. Before commit
    # 9525ce5, create_enum's pre-check (IsProperNameForUserDefinedEnumerator) validates the AUTHORED
    # name, which for a fresh UserDefinedEnum entry is always NewEnumeratorN - so it had nothing to
    # reject, the real refusal inside SetEnumeratorDisplayName was discarded, and a caller was told
    # ok:true with no sign the second "Alpha" never actually became "Alpha".
    p301d = "/Game/_MifEnum/E_%d_dup" % st
    dup = M.call("create_enum", {"path": p301d, "values": ["Alpha", "Beta", "Alpha"]})
    check("T301 create still succeeds even with a duplicate in values[]",
          dup.get("ok") is True, json.dumps(dup)[:200])
    check("T301 and now reports a warning naming the refused entry",
          any("Alpha" in w and "2" in w for w in (dup.get("warnings") or [])),
          dup.get("warnings"))
    dvals = dup.get("values") or []
    check("T301 the duplicate entry kept its GENERATED name rather than silently claiming Alpha",
          len(dvals) == 3 and dvals[2].get("displayName") == "NewEnumerator2",
          dvals)
    check("T301 the first Alpha is unaffected", dvals[0].get("displayName") == "Alpha", dvals[0])
    # Read back independently of the write's own response - the exact discipline the original bug
    # would have defeated, since the write's response was the thing that lied.
    ldup = M.call("list_enum_values", {"enum": dup.get("enumPath")}).get("entries") or []
    check("T301 and a fresh read-back agrees, not just the write's own echo",
          len(ldup) == 3 and ldup[2].get("displayName") == "NewEnumerator2", ldup)
    SC.confirm_call("delete_asset", {"path": p301d})

    # ------------------------------------------------------------------ T302 the regression
    print("\n=== T302 [the bug]: the read must not throw the display names away ===")
    l = M.call("list_enum_values", {"enum": ep})
    check("T302 the enum reads back", l.get("ok") is True, json.dumps(l)[:170])
    # `values` keeps its old meaning - callers read it, and reshaping a working field would trade one
    # silent breakage for another.
    check("T302 values[] still holds the AUTHORED names, unchanged",
          all(v.startswith("NewEnumerator") for v in (l.get("values") or []))
          and len(l.get("values") or []) == 3, json.dumps(l.get("values")))
    entries = l.get("entries") or []
    check("T302 entries[] exists", len(entries) == 3, json.dumps(entries)[:170])
    check("T302 and pairs every authored name with its display name",
          [e.get("displayName") for e in entries] == wanted,
          str([e.get("displayName") for e in entries]))
    check("T302 each entry carries index, name, displayName and value",
          all(all(k in e for k in ("index", "name", "displayName", "value")) for e in entries),
          json.dumps(entries)[:200])
    # NON-VACUITY: if the two names were equal on this fixture, the test could not show the bug.
    check("T302 the two names genuinely DIFFER on this fixture",
          all(e.get("name") != e.get("displayName") for e in entries),
          "authored and display names are identical here, so this fixture cannot demonstrate the bug")
    check("T302 and the response says the enum is user-defined",
          l.get("userDefined") is True, l.get("userDefined"))
    check("T302 with a note telling the caller which name goes where",
          "set_pin_default" in (l.get("nameNote") or ""), (l.get("nameNote") or "")[:180])

    # ------------------------------------------------------------------ T303 the fix is not noisy
    print("\n=== T303: a NATIVE enum must not get the user-defined warning ===")
    n = M.call("list_enum_values", {"enum": "ESlateVisibility"})
    check("T303 a native enum reads", n.get("ok") is True, json.dumps(n)[:150])
    check("T303 it is reported as NOT user-defined", n.get("userDefined") is False, n.get("userDefined"))
    # UE prettifies native display names, so displayNamesDiffer is legitimately true here - which is
    # exactly why the note must be gated on the CLASS and not on that flag.
    check("T303 and gets no nameNote even though its display names differ",
          "nameNote" not in n and n.get("displayNamesDiffer") is True,
          "displayNamesDiffer=%s nameNote=%s" % (n.get("displayNamesDiffer"), "nameNote" in n))
    check("T303 its authored names are meaningful, unlike a user enum's",
          any(e.get("name") == "Visible" for e in (n.get("entries") or [])),
          json.dumps((n.get("entries") or [])[:3]))

    # ------------------------------------------------------------------ T304 guards
    print("\n=== T304: guards ===")
    for label, payload, expect in (
        ("no enum", {}, "required"),
        ("unknown enum", {"enum": "NoSuchEnum_zz"}, "not found"),
    ):
        q = M.call("list_enum_values", payload)
        check("T304 %s refused" % label, q.get("ok") is False, json.dumps(q)[:140])
        check("T304 %s explains" % label, expect in (q.get("error") or "").lower(),
              (q.get("error") or "")[:150])
    # enumName is the plugin's usual spelling and this endpoint accepts both - a drift that was fixed
    # once already, so it is pinned here.
    alias = M.call("list_enum_values", {"enumName": ep})
    check("T304 the enumName alias works", alias.get("ok") is True, json.dumps(alias)[:150])

    dup = M.call("add_enum_value", {"enum": ep, "value": "Common"})
    check("T304 a duplicate display name is refused", dup.get("ok") is False, json.dumps(dup)[:150])
    q = M.call("add_enum_value", {"enum": ep})
    check("T304 adding with no value is refused",
          q.get("ok") is False and "required" in (q.get("error") or ""), (q.get("error") or "")[:150])
    # Nothing above may have changed the enum.
    still = M.call("list_enum_values", {"enum": ep})
    check("T304 the enum is unchanged after every refusal",
          len(still.get("entries") or []) == 3, len(still.get("entries") or []))

    print("\n=== T305: removal - the refusal, then the real removal via scratch_confirm ===")
    r = M.call("remove_enum_value", {"enum": ep, "value": "Rare"})
    check("T305 remove refuses without confirm", r.get("ok") is False, json.dumps(r)[:150])
    check("T305 and says confirm is what is missing",
          "confirm" in (r.get("error") or ""), (r.get("error") or "")[:150])
    check("T305 the entry survives the refused removal",
          len((M.call("list_enum_values", {"enum": ep}).get("entries") or [])) == 3,
          "an entry disappeared on a refused call")

    # remove_enum_value is addressed by `enum`, a real asset path - one of the nine confirm-gated
    # endpoints scratch_confirm.py genuinely unblocks (see its own module docstring), already proven
    # by test_confirm_gated.py's T345. Closed here too rather than leaving this file's own claim stale.
    real = SC.confirm_call("remove_enum_value", {"enum": ep, "value": "Rare"})
    check("T305 the real removal succeeds", real.get("ok") is True, json.dumps(real)[:170])
    after = M.call("list_enum_values", {"enum": ep}).get("entries") or []
    check("T305 the removed entry is really gone", not any(e.get("displayName") == "Rare" for e in after), after)
    check("T305 and the others survive it", {"Common", "Legendary"} <= {e.get("displayName") for e in after}, after)

    SC.confirm_call("delete_asset", {"path": path})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
