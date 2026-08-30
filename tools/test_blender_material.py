"""Material creation, inspection and per-face assignment in Blender.

WHAT THIS COVERS did not exist before 2026-08-30. The addon had exactly one material verb,
set_material_slots, which assigns NAMES to slots and deliberately does not touch material content -
its own docstring says "a material's content is Unreal's business". That is the right shape for a
round trip, where the material really does live in Unreal. It is the wrong shape for BUILDING an
asset in Blender, which is what Andre asked for. There was no material READ op of any kind.

T4101 IS THE ONE THIS WHOLE MODULE IS SHAPED AROUND. Blender RENAMED Principled BSDF inputs between
3.6 and 4.0 - "Specular" became "Specular IOR Level", "Emission" became "Emission Color",
"Transmission" became "Transmission Weight" - and this addon supports 3.6 through 5.0. Looking a
socket up by a hardcoded string does not merely raise on the wrong version: writing to a name that
does not exist there means the value lands NOWHERE, and the material is subtly wrong with nothing to
read back. So every property resolves through an alias list, and one that resolves on NO alias is
refused by name. This suite asserts the values actually READ BACK, which is the only way to tell a
write that landed from one that evaporated, and it asserts resolvedInputs reports the real socket
name so the answer is visible rather than inferred.

T4103 IS THE BLAST-RADIUS ONE. A face stores a slot INDEX, so an out-of-range assignment does not
error - it renders as some other slot. The op refuses an out-of-range index and reports `changed` as
a MEASURED count, because assigning a face to the slot it already had is a no-op and reporting the
request as the result would hide that.

RUNS AGAINST A HEADLESS BLENDER started by tools/run_blender_suites.py on a private port. It creates
and removes its own materials and objects.
"""
import json
import sys

import blender_audit_common as B

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append((name, str(detail)[:400]))
        print("  FAIL  %s\n        %s" % (name, str(detail)[:400]))



def run_python_available():
    """True when the addon will execute run_python for us.

    IT IS OFF BY DEFAULT, and a --factory-startup headless Blender - which is exactly what
    run_blender_suites.py starts - can never have it on, because it is an addon PREFERENCE. A GUI
    Blender with it enabled runs these checks; the cross-version sweep cannot. Detected rather than
    assumed, so the checks that need it are SKIPPED with a reason instead of failing on four
    versions and looking like an endpoint defect.
    """
    r = B.call("run_python", {"code": "pass"})
    return bool(r.get("ok"))

def main():
    ping = B.call("ping")
    if not ping.get("ok"):
        print("no Blender on %s:%s - start one with tools/run_blender_suites.py" % (B.HOST, B.PORT))
        return 2

    objs = []
    try:
        # ------------------------------------------------------------------ T4100 create + read
        print("=== T4100: creating a material, and the read half that never existed ===")
        m = B.call("create_material", {"name": "MifT_Mat"})
        check("T4100 a material can be created", m.get("ok") is True, json.dumps(m)[:250])
        check("T4100 with a Principled BSDF, which every property write needs",
              (m.get("principled") or {}) != {} and m.get("useNodes") is True,
              json.dumps(m.get("principled"))[:200])
        made_name = m.get("name")

        dup = B.call("create_material", {"name": "MifT_Mat"})
        check("T4100 a colliding name yields a DIFFERENT material, reported honestly",
              dup.get("ok") is True and dup.get("name") != made_name
              and "already taken" in (dup.get("nameNote") or ""),
              "%r vs %r / %s" % (made_name, dup.get("name"), dup.get("nameNote")))
        reuse = B.call("create_material", {"name": "MifT_Mat", "reuse": True})
        check("T4100 reuse:true returns the existing one instead of a numbered copy",
              reuse.get("ok") is True and reuse.get("created") is False
              and reuse.get("name") == made_name, json.dumps(reuse)[:220])

        lst = B.call("list_materials", {"nameContains": "MifT_"})
        check("T4100 list_materials finds them", lst.get("ok") is True
              and (lst.get("count") or 0) >= 2, json.dumps(lst)[:250])
        # A material with no users is not written to an FBX at all - a real pipeline trap, so the
        # read half has to surface it rather than leaving it to be discovered in Unreal.
        check("T4100 and flags unused materials, which do NOT survive an FBX export",
              made_name in (lst.get("unused") or []) and bool(lst.get("unusedNote")),
              json.dumps(lst.get("unused"))[:200])

        d = B.call("describe_material", {"material": made_name})
        check("T4100 describe_material reports the node tree and the Principled values",
              d.get("ok") is True and isinstance(d.get("principled"), dict)
              and "BSDF_PRINCIPLED" in (d.get("nodeTypes") or []), json.dumps(d)[:250])
        check("T4100 and a textures array - the file paths an Unreal import has to resolve",
              isinstance(d.get("textures"), list), json.dumps(d.get("textures"))[:150])
        missing = B.call("describe_material", {"material": "MifT_NoSuchMaterial"})
        check("T4100 an unknown material is refused and the real ones listed",
              missing.get("ok") is False and "MifT_" in (missing.get("error") or ""),
              (missing.get("error") or "")[:200])

        # ------------------------------------------------------------------ T4101 the renames
        print("\n=== T4101: Principled inputs were RENAMED between 3.6 and 4.0 ===")
        w = B.call("set_material_properties", {
            "material": made_name, "baseColor": [0.8, 0.1, 0.1], "metallic": 0.5,
            "roughness": 0.25, "specular": 0.3, "emissive": [0.0, 0.2, 0.0]})
        check("T4101 a mixed write across renamed and stable inputs succeeds",
              w.get("ok") is True, json.dumps(w)[:280])
        # THE assertion. A write to a socket that does not exist on this version lands nowhere and
        # errors nowhere; only reading the value back can tell the difference.
        p = (w.get("principled") or {})
        check("T4101 and the values READ BACK - proving they landed, not evaporated",
              abs((p.get("metallic") or 0) - 0.5) < 1e-4
              and abs((p.get("roughness") or 0) - 0.25) < 1e-4,
              json.dumps({k: p.get(k) for k in ("metallic", "roughness")}))
        check("T4101 the colour landed too, widened to RGBA with an opaque alpha",
              len(p.get("baseColor") or []) == 4
              and abs(p["baseColor"][0] - 0.8) < 1e-4 and abs(p["baseColor"][3] - 1.0) < 1e-4,
              json.dumps(p.get("baseColor")))
        # The version-dependent ones specifically: report which socket name this Blender used.
        res = w.get("resolvedInputs") or {}
        check("T4101 resolvedInputs names the REAL socket each property landed on",
              res.get("specular") in ("Specular", "Specular IOR Level")
              and res.get("emissive") in ("Emission", "Emission Color"),
              json.dumps(res))
        print("        this Blender (%s) used: specular=%r emissive=%r"
              % (w.get("blenderVersion"), res.get("specular"), res.get("emissive")))

        nothing = B.call("set_material_properties", {"material": made_name})
        check("T4101 a write with no property is refused rather than a silent no-op",
              nothing.get("ok") is False, (nothing.get("error") or "")[:180])
        badcol = B.call("set_material_properties", {"material": made_name, "baseColor": 0.5})
        check("T4101 a scalar where a colour is expected is refused",
              badcol.get("ok") is False, (badcol.get("error") or "")[:180])

        # ------------------------------------------------------------------ T4102 all-or-nothing
        print("\n=== T4102: a rename must not half-apply ===")
        before = (B.call("describe_material", {"material": made_name}).get("principled") or {})
        mixed = B.call("set_material_properties", {"material": made_name, "roughness": 0.9,
                                                   "notAProperty": 1})
        check("T4102 an unknown property name is refused", mixed.get("ok") is False,
              (mixed.get("error") or "")[:200])
        after = (B.call("describe_material", {"material": made_name}).get("principled") or {})
        # THE assertion: the GOOD half of a refused call must not have been written.
        check("T4102 and the valid property in the same call was NOT applied",
              abs((after.get("roughness") or 0) - (before.get("roughness") or 0)) < 1e-6,
              "roughness %s -> %s" % (before.get("roughness"), after.get("roughness")))

        # ------------------------------------------------------------------ T4104 review fixes
        print("\n=== T4104: three silent no-ops an adversarial review found ===")
        # 1. reuse used to return BEFORE applying inline values, while attaching a note claiming
        #    "the end state you asked for is already in place" - false exactly when a value was
        #    passed, on the idempotent-create shape a pipeline uses by default.
        B.call("set_material_properties", {"material": made_name, "baseColor": [0.1, 0.1, 0.1]})
        ru = B.call("create_material", {"name": made_name, "reuse": True,
                                        "baseColor": [1.0, 0.0, 0.0]})
        check("T4104 reuse with inline values succeeds", ru.get("ok") is True,
              json.dumps(ru)[:220])
        check("T4104 and it APPLIES them rather than discarding them",
              abs(((ru.get("principled") or {}).get("baseColor") or [0])[0] - 1.0) < 1e-4,
              json.dumps((ru.get("principled") or {}).get("baseColor")))
        check("T4104 and no longer claims the end state was already in place",
              "already in place" not in (ru.get("note") or ""), ru.get("note"))

        # 2. a LINKED socket ignores default_value entirely, so writing one changes nothing that
        #    renders while still reading back as the new value.
        #
        # BUILDING THE LINK NEEDS run_python, an addon preference that is off by default and cannot
        # be on under --factory-startup - which is what the cross-version sweep uses. Detected, so
        # these checks skip with a reason rather than failing on four versions and reading like a
        # defect in the endpoint.
        if not run_python_available():
            print("  NOT EXERCISED: the LINKED-socket refusal. Creating a node link needs")
            print("  run_python, which is off by default and unavailable under --factory-startup.")
            print("  Run against a GUI Blender with 'Allow run_python' enabled to cover it.")
        else:
            B.call("run_python", {"code":
                "import bpy\n"
                "m = bpy.data.materials[%r]\n"
                "t = m.node_tree\n"
                "n = t.nodes.new('ShaderNodeRGB')\n"
                "b = next(x for x in t.nodes if x.type == 'BSDF_PRINCIPLED')\n"
                "t.links.new(n.outputs[0], b.inputs['Base Color'])\n" % made_name})
            linked = B.call("set_material_properties", {"material": made_name,
                                                        "baseColor": [0, 1, 0]})
            check("T4104 writing a LINKED socket is refused, not silently ignored",
                  linked.get("ok") is False and "LINKED" in (linked.get("error") or ""),
                  (linked.get("error") or "")[:220])
            check("T4104 and the refusal explains that a connected input ignores its default",
                  "ignores its default" in (linked.get("error") or ""),
                  (linked.get("error") or "")[:200])

        # 3. a mesh with no polygons made assign_material_to_faces return changed:0 and no error.
        empty = B.call("create_primitive", {"kind": "circle", "name": "MifT_Empty",
                                            "fillType": "NOTHING"})
        if empty.get("ok"):
            objs.append(empty["name"])
            B.call("set_material_slots", {"object": empty["name"], "slots": [made_name],
                                          "allowResize": True})
            z = B.call("assign_material_to_faces", {"object": empty["name"], "slot": 0})
            check("T4104 a mesh with NO polygons is refused rather than reporting changed:0",
                  z.get("ok") is False and "NO polygons" in (z.get("error") or ""),
                  (z.get("error") or "")[:200])

        # ------------------------------------------------------------------ T4103 faces
        print("\n=== T4103: a face stores a slot INDEX, so a bad one renders as another slot ===")
        cube = B.call("create_primitive", {"kind": "cube", "name": "MifT_FaceCube"})
        check("T4103 (setup) a cube exists", cube.get("ok") is True, json.dumps(cube)[:200])
        objs.append(cube.get("name"))
        noslots = B.call("assign_material_to_faces", {"object": cube["name"], "slot": 0})
        check("T4103 assigning with no material slots at all is refused",
              noslots.get("ok") is False and "no material slots" in (noslots.get("error") or ""),
              (noslots.get("error") or "")[:180])

        B.call("set_material_slots", {"object": cube["name"],
                                      "slots": [made_name, "MifT_Second"], "allowResize": True})
        over = B.call("assign_material_to_faces", {"object": cube["name"], "slot": 7})
        check("T4103 an out-of-range slot is refused, not clamped",
              over.get("ok") is False and "out of range" in (over.get("error") or ""),
              (over.get("error") or "")[:200])
        badface = B.call("assign_material_to_faces", {"object": cube["name"], "slot": 1,
                                                      "faces": [0, 999]})
        check("T4103 an out-of-range FACE index is refused rather than skipped",
              badface.get("ok") is False and "999" in (badface.get("error") or ""),
              (badface.get("error") or "")[:200])

        asg = B.call("assign_material_to_faces", {"object": cube["name"], "slot": 1,
                                                  "faces": [0, 1]})
        check("T4103 assigning specific faces succeeds", asg.get("ok") is True,
              json.dumps(asg)[:250])
        check("T4103 and `changed` is MEASURED - two faces moved",
              asg.get("changed") == 2, json.dumps(asg)[:220])
        check("T4103 with a per-slot face tally, so the split is visible",
              (asg.get("facesPerSlot") or {}).get("1") == 2
              and (asg.get("facesPerSlot") or {}).get("0") == 4,
              json.dumps(asg.get("facesPerSlot")))
        again = B.call("assign_material_to_faces", {"object": cube["name"], "slot": 1,
                                                    "faces": [0, 1]})
        # Re-assigning the same faces is a no-op, and reporting the REQUEST would hide that.
        check("T4103 re-assigning the same faces reports changed:0, not 2",
              again.get("ok") is True and again.get("changed") == 0, json.dumps(again)[:220])
        allf = B.call("assign_material_to_faces", {"object": cube["name"], "slot": 0})
        check("T4103 omitting faces assigns every polygon",
              allf.get("ok") is True and allf.get("changed") == 2
              and (allf.get("facesPerSlot") or {}).get("0") == 6,
              json.dumps(allf)[:220])
    finally:
        for n in dict.fromkeys(o for o in objs if o):
            B.call("delete_object", {"object": n})
        left = B.call("list_materials", {"nameContains": "MifT_"})
        # Materials with no users are purged by Blender on file reload, not on demand - there is no
        # delete_material op, so this reports rather than asserts, and says so.
        print("        NOTE: %d MifT_ material(s) remain. There is no delete_material op - an "
              "unused material is purged on file reload, and this headless Blender is discarded "
              "at the end of the run." % (left.get("count") or 0))

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
