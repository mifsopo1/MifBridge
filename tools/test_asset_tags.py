"""get_asset_tags, and tag filtering on find_assets - reading the registry without loading anything.

WHAT THIS BUYS is answers about thousands of assets on a cooked project without deserialising one
of them. Blueprint parent class, texture format and dimensions, mesh LOD counts, DataTable row
struct and every custom GetAssetRegistryTags a class exposes all live in the asset registry. Nothing
is loaded, so none of the cooked crash families can be reached from here at all.

T4901 IS THE ONE THAT WOULD OTHERWISE BE SILENTLY WRONG, and it is the reason this endpoint does
extra work. FARFilter::TagsAndValues entries are OR'd by the engine, not AND'd: AssetRegistryState
walks every filter tag and appends each one's matches into a single shared array. A caller passing
two tags and expecting "both" would get the UNION and have no way to notice, because every row in it
looks plausible. find_assets therefore hands the whole set to the engine filter - an OR result is a
superset of the AND result, so it narrows the scan cheaply through the tag index - and then
re-checks every surviving row against every requested tag. This test proves the result is the
intersection by measuring each tag alone and asserting the pair is no larger than the smaller of
them, which a union could never satisfy.

T4902 IS THE HONEST LIMIT. Registry matching is exact STRING equality. Texture2D's Dimensions tag is
a formatted "1024x1024" string, so "every texture wider than 2048" is NOT a filter axis however much
it sounds like one - the survey proposed it as the flagship example and it does not work. The
endpoint refuses a numeric-looking parameter by name and points at includeTags instead.

T4903 IS THE COOKED CAVEAT, which matters more than the speed. FAssetRegistryState::FilterTags
strips tags at cook time, and an allow-list project keeps only a handful. So a small tag map on a
cooked asset means "these survived the cook", not "this asset is simple" - and the response says
which case it is in rather than leaving the reader to assume.
"""
import json
import sys

import mifaudit as M

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))


def count(**kw):
    return M.call("find_assets", dict({"limit": 1}, **kw)).get("count")


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    # ------------------------------------------------------------------ T4900 the read
    print("=== T4900: everything the registry knows, without loading the asset ===")
    # SKIP SCRATCH: T4900 reads what the REGISTRY knows about an asset without loading it, and a
    # texture test_textures imported minutes ago carries different tags from the project's own
    # cooked content - which is the content this read exists to be exercised against.
    tex = M.pick_adoptable(M.call("find_assets", {"class": "Texture2D",
                                                  "limit": 20}).get("assets")) or {}
    target = tex.get("path")
    check("T4900 (setup) a texture to read", bool(target), json.dumps(tex)[:200])
    if not target:
        return 1

    r = M.call("get_asset_tags", {"path": target})
    check("T4900 get_asset_tags succeeds", r.get("ok") is True, json.dumps(r)[:250])
    check("T4900 it reports the identity and the class",
          r.get("objectPath") == target and bool(r.get("class")), json.dumps(r)[:200])
    check("T4900 tags is a map and tagCount agrees with it",
          isinstance(r.get("tags"), dict) and r.get("tagCount") == len(r.get("tags") or {}),
          "count=%s len=%s" % (r.get("tagCount"), len(r.get("tags") or {})))
    check("T4900 a Texture2D exposes the tags you would expect",
          "Format" in (r.get("tags") or {}) and "Dimensions" in (r.get("tags") or {}),
          sorted(r.get("tags") or {})[:10])
    check("T4900 and it says plainly that nothing was loaded",
          "nothing was loaded" in (r.get("loadedNote") or "").lower(), r.get("loadedNote"))

    # An unsaved asset has no registry row - a real trap, so the refusal names it.
    missing = M.raw_post("get_asset_tags", {"path": "/Game/_Mif/NoSuchAssetAnywhere"})
    check("T4900 an unknown asset is refused, and warns that an unsaved asset has no registry row",
          missing.get("ok") is False and "never saved" in (missing.get("error") or ""),
          (missing.get("error") or "")[:220])

    # ------------------------------------------------------------------ T4901 AND vs OR
    print("\n=== T4901: two tags means BOTH, though the engine's own filter means EITHER ===")
    a_tag, a_val = "CompressionSettings", "TC_EditorIcon"
    b_tag, b_val = "LODGroup", "TEXTUREGROUP_World"
    a = count(tags={a_tag: a_val})
    b = count(tags={b_tag: b_val})
    both = count(tags={a_tag: a_val, b_tag: b_val})
    check("T4901 (setup) both tags match something on their own", a > 0 and b > 0, (a, b))
    print("        %s=%s -> %d | %s=%s -> %d | together -> %d (union would be <= %d)"
          % (a_tag, a_val, a, b_tag, b_val, b, both, a + b))
    # THE assertion. An intersection cannot exceed the smaller input; a union could not be below it.
    check("T4901 the pair is an INTERSECTION - no larger than the smaller single-tag result",
          both <= min(a, b), "both=%d min=%d" % (both, min(a, b)))
    check("T4901 and strictly smaller than the union the raw engine filter would return",
          both < a + b, "both=%d union<=%d" % (both, a + b))

    rows = M.call("find_assets", {"tags": {a_tag: a_val, b_tag: b_val},
                                  "includeTags": True, "limit": 5}).get("assets") or []
    # Counted rather than all()-ed, so the assertion states a VALUE and cannot pass vacuously on
    # an empty list - the audit is right that "every element of nothing" is a check that proves
    # nothing.
    carrying = sum(1 for row in rows
                   if (row.get("tags") or {}).get(a_tag) == a_val
                   and (row.get("tags") or {}).get(b_tag) == b_val)
    check("T4901 rows came back at all", len(rows) > 0, len(rows))
    check("T4901 and every one of them really carries BOTH tag values",
          len(rows) > 0 and carrying == len(rows),
          "%d of %d rows carry both" % (carrying, len(rows)))
    note = M.call("find_assets", {"tags": {a_tag: a_val, b_tag: b_val},
                                  "limit": 1}).get("tagMatchNote") or ""
    check("T4901 and the response explains it re-checked, rather than leaving OR to be assumed",
          "AND'd" in note and "OR" in note, note[:200])

    # ------------------------------------------------------------------ T4902 exact strings
    print("\n=== T4902: matching is exact string equality, so numbers are not a filter axis ===")
    present = M.call("find_assets", {"class": "Texture2D", "tags": {"Dimensions": None},
                                     "limit": 3})
    check("T4902 a null value means 'tag present, any value'",
          present.get("ok") is True and (present.get("count") or 0) > 0,
          json.dumps({"count": present.get("count")}))
    exact = count(tags={"Dimensions": "128x128"})
    check("T4902 an exact value matches", exact > 0, exact)
    partial = count(tags={"Dimensions": "128"})
    check("T4902 but a PARTIAL value matches nothing - there is no substring comparison",
          partial == 0, partial)
    num = M.raw_post("find_assets", {"class": "Texture2D", "minWidth": 2048})
    check("T4902 a numeric-looking parameter is refused by name", num.get("ok") is False,
          (num.get("error") or "")[:200])
    check("T4902 and the refusal explains Dimensions is a formatted string, pointing at includeTags",
          "includeTags" in (num.get("error") or ""), (num.get("error") or "")[:250])

    # ------------------------------------------------------------------ T4903 cooked
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
        print('=== T4903 SKIPPED - nothing in this project is cooked ===')
        print('  This section asserts what an endpoint REFUSES on cooked content. There is nothing cooked')
        print('  here, so the refusal cannot be provoked - which is not the same as the guard being absent.')
        print('  Where the call is a WRITE, running it unguarded would perform the write it means to see')
        print('  refused. Run against a cooked project for this half.')
    else:
        print("\n=== T4903: on cooked content this is what SURVIVED, not what exists ===")
        cooked = (M.call("find_assets", {"origin": "container", "limit": 1}).get("assets")
                  or [{}])[0].get("path")
        if cooked:
            c = M.call("get_asset_tags", {"path": cooked})
            check("T4903 a cooked asset reads fine - nothing is deserialised",
                  c.get("ok") is True, json.dumps(c)[:220])
            check("T4903 it is flagged as coming from a container",
                  c.get("origin") == "container", c.get("origin"))
            # THE assertion that stops a wrong conclusion: a short tag map on cooked content is not
            # evidence the asset is simple.
            check("T4903 and warns the tags were STRIPPED at cook, so a small map proves nothing",
                  "SURVIVED" in (c.get("cookedNote") or ""), (c.get("cookedNote") or "")[:220])
        else:
            print("  NOTE  no container-origin asset found, so the cooked caveat is unexercised here.")

        check("T4903 - the editor is still alive", M.call("self_audit", {}).get("ok") is True,
              "nothing here loads an asset, which is the whole safety argument")

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
