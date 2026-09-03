"""Texture and static-switch parameters on set_material_parameter.

The audit called this one work item with list_material_parameters, not a separate row: enumeration
surfaces parameter types the write side rejects, and a list where a third of the entries are read-only
is worse than no list.

T131 is the one that matters. A static switch changes the material's shader PERMUTATION, not just a
stored value. SetStaticSwitchParameterValueEditorOnly records the value and nothing else, so without
the UpdateStaticPermutation that follows it, the instance reports the new value through every read
path while rendering exactly as before - ok:true, a correct read-back, and no visual change. The test
asserts staticPermutationUpdated rather than trusting that the value came back right, because the
value coming back right is precisely what the broken version would also do.

Everything is done on a DUPLICATE in /Game/_MifMat. The instances that carry rich texture parameters
are real game content and are not written to.
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


def params_of(path, kind=None):
    q = M.call("list_material_parameters", dict({"path": path}, **({"types": [kind]} if kind else {})))
    return q.get("parameters") or []


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1
    stamp = int(time.time() % 100000)

    # A real instance that exposes texture parameters; duplicated so game content is never written.
    src = None
    # SKIP SCRATCH. The comment above says "a real instance" and nothing enforced it: five suites
    # mint MaterialInstanceConstants under /Game/_Mif*, and duplicating one means this suite's whole
    # T130 section measures parameter writes against another suite's half-built fixture.
    for a in (M.call("find_assets", {"class": "MaterialInstanceConstant", "pathPrefix": "/Game/",
                                     "limit": 25}).get("assets") or []):
        if M.is_scratch_fixture(a):
            continue
        if len(params_of(a.get("path"), "texture")) > 0:
            src = a.get("path")
            break
    if not src:
        print("no material instance with texture parameters found - cannot test")
        return 3
    mi = "/Game/_MifMat/MI_W_%d" % stamp
    d = M.call("duplicate_asset", {"path": src, "newPath": mi})
    if not d.get("ok"):
        print("setup failed:", json.dumps(d)[:200])
        return 3
    print("working on a duplicate of", src[-46:])

    # ------------------------------------------------------------------ T130 textures
    print("\n=== T130: a texture parameter can be written and reads back ===")
    tex = [p["name"] for p in params_of(mi, "texture")]
    check("T130 the copy exposes texture parameters", len(tex) > 0, str(tex[:3]))
    target = tex[0]
    # SKIP SCRATCH: test_textures imports a Texture2D into /Game/_MifTex and deletes it again. If
    # limit 1 lands on that one mid-run, the assignment below either fails to resolve or reads back
    # a path that no longer exists, and T130 fails for a reason that has nothing to do with
    # set_material_parameter.
    real = (M.pick_adoptable(M.call("find_assets", {"class": "Texture2D", "pathPrefix": "/Game/",
                                                    "limit": 20}).get("assets")) or {}).get("path")
    r = M.call("set_material_parameter", {"material": mi, "textures": {target: real}})
    check("T130 applied", r.get("ok") is True and r.get("texturesApplied") == 1, json.dumps(r)[:200])
    back = [p for p in params_of(mi, "texture") if p["name"] == target]
    check("T130 and the assignment is what reads back",
          bool(back) and back[0].get("value") == real,
          "%s vs %s" % (back[0].get("value") if back else None, real))

    print("\n=== T130b: an unresolvable texture is refused, not assigned as null ===")
    # A null assignment would report success and render black.
    for name, val, expect in (("missing asset", "/Game/NoSuchTex_zz", "no asset at"),
                              ("wrong asset type", src, "not a UTexture")):
        q = M.call("set_material_parameter", {"material": mi, "textures": {target: val}})
        check("T130b %s refused" % name, q.get("ok") is False, json.dumps(q)[:160])
        check("T130b %s says which" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:150])
    still = [p for p in params_of(mi, "texture") if p["name"] == target]
    check("T130b the good assignment survived the refusals",
          bool(still) and still[0].get("value") == real, still[0].get("value") if still else None)

    # ------------------------------------------------------------------ T131 static switches
    print("\n=== T131 [the trap]: a static switch updates the PERMUTATION, not just a value ===")
    # A material that actually HAS a static switch, discovered - naming one DDS2 asset made this
    # arm unrunnable on any other project. Engine content carries one, so this works on a blank
    # project: WorldPartitionSpatialHashGridPreviewMaterial has 12 scalars, 8 vectors and a switch.
    sw_parent, _swp = M.discover_material(require="staticSwitch")
    if not sw_parent:
        print("  NOTE  no material with a static switch in this project, so T131 is UNEXERCISED")
        print("        rather than counted. The permutation trap needs a switch to trap.")
        sw_parent = None
    mi2 = "/Game/_MifMat/MI_S_%d" % stamp
    c = M.call("create_material_instance", {"parent": sw_parent, "path": mi2}) if sw_parent else {}
    if c.get("ok"):
        sw = [p["name"] for p in params_of(mi2, "staticSwitch")]
        check("T131 the instance exposes a static switch", len(sw) > 0, str(sw))
        if sw:
            r = M.call("set_material_parameter", {"material": mi2, "switches": {sw[0]: True}})
            print("  ", json.dumps({k: v for k, v in r.items() if k != "permutationNote"})[:230])
            check("T131 applied", r.get("ok") is True and r.get("switchesApplied") == 1,
                  json.dumps(r)[:200])
            # The value reading back correctly is exactly what the BROKEN version would also do,
            # so the permutation flag is the real assertion.
            check("T131 the permutation was updated", r.get("staticPermutationUpdated") is True,
                  "without this the value reads back right and the material renders unchanged")
            check("T131 and it explains why that matters", "renders unchanged" in (r.get("permutationNote") or ""),
                  (r.get("permutationNote") or "")[:140])
            b = [p for p in params_of(mi2, "staticSwitch") if p["name"] == sw[0]]
            check("T131 the value reads back", bool(b) and b[0].get("value") is True,
                  b[0].get("value") if b else None)
            check("T131 and is marked as this instance's own override",
                  bool(b) and b[0].get("overriddenOnThisInstance") is True,
                  b[0].get("overriddenOnThisInstance") if b else None)
        SC.confirm_call("delete_asset", {"path": mi2})

    # ------------------------------------------------------------------ T132 guards
    print("\n=== T132: guards ===")
    for name, payload, expect in (
        ("nothing to apply", {"material": mi}, "textures"),
        ("bad association", {"material": mi, "textures": {target: real}, "association": "bogus"},
         "global, layer or blend"),
        ("non-path string value", {"material": mi, "parameter": target, "value": "notapath"},
         "neither a number nor"),
        ("switch given a non-bool", {"material": mi, "switches": {target: "yes"}}, ""),
    ):
        q = M.call("set_material_parameter", payload)
        check("T132 %s refused" % name, q.get("ok") is False, json.dumps(q)[:160])
        if expect:
            check("T132 %s explains" % name, expect in (q.get("error") or ""), (q.get("error") or "")[:150])

    print("\n=== T133: the old hints are gone (they said this was unsupported) ===")
    q = M.call("set_material_parameter", {"material": mi, "texture": "x"})
    check("T133 'texture' now points at the real key",
          "plural key is 'textures'" in (q.get("error") or ""), (q.get("error") or "")[:150])

    SC.confirm_call("delete_asset", {"path": mi})
    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s\n          %s" % f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
