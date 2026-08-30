"""Creating geometry in Blender, and placing it without baking.

WHAT THIS COVERS is the half of the addon that did not exist until 2026-08-30: every mesh used to
enter through import_mesh, so the bridge could edit assets authored elsewhere and could not
originate a single vertex. Andre: "for blender i want more than round trip, i want full creation and
materialisation support".

T4001 IS THE NAME TEST, and it is the one that would otherwise produce quiet wrongness.
bpy.ops.mesh.primitive_*_add and bpy.data.materials.new NEVER fail and NEVER overwrite on a name
collision - they append .001 and carry on. So a caller who asked for "Crate" can be handed "Crate.003"
and never know, and every later op addressing "Crate" hits the wrong object. Every creation op here
echoes the name the object ACTUALLY has, and this asserts that by deliberately colliding.

T4003 IS THE ONE THAT PROVES transform_object EARNS ITS PLACE. apply_transform and set_origin both
BAKE the transform into the mesh data and leave the object at identity - which is what an export
pipeline wants and is NOT how you place a second object beside a first. The round trip papers over
the gap by asserting isIdentityTransform stays true; this asserts the opposite for the op whose whole
purpose is to move something without baking it.

T4004 CHECKS THE THING JOIN QUIETLY CHANGES. bpy.ops.object.join MERGES the material slot lists and
remaps every face's slot index, so the result's slot ORDER is neither input's order - and slot order
is exactly what decides which Unreal material lands on which face. The op reports slots before and
after; this asserts the report is real.

RUNS AGAINST A HEADLESS BLENDER started by tools/run_blender_suites.py on a private port. It creates
and deletes its own objects and never touches a GUI session's scene.
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


def names():
    return {o["name"] for o in (B.call("list_objects").get("objects") or [])}


def main():
    ping = B.call("ping")
    if not ping.get("ok"):
        print("no Blender on %s:%s - start one with tools/run_blender_suites.py" % (B.HOST, B.PORT))
        return 2

    made = []
    try:
        # ------------------------------------------------------------------ T4000 primitives
        print("=== T4000: geometry that did not come from a file ===")
        bad = B.call("create_primitive", {"kind": "dodecahedron"})
        check("T4000 an unknown kind is refused, not defaulted to a cube",
              bad.get("ok") is False, json.dumps(bad)[:250])
        check("T4000 and the refusal lists the kinds that exist",
              "cube" in (bad.get("error") or ""), (bad.get("error") or "")[:200])

        for kind, want_min in (("cube", 8), ("sphere", 100), ("cylinder", 30), ("plane", 4)):
            r = B.call("create_primitive", {"kind": kind, "name": "MifT_%s" % kind})
            check("T4000 %s is created" % kind, r.get("ok") is True, json.dumps(r)[:200])
            if r.get("ok"):
                made.append(r["name"])
                # Vert counts are MEASURED off the created mesh, so this is a real postcondition
                # rather than a claim that an operator ran.
                check("T4000 and %s has real geometry (>= %d verts)" % (kind, want_min),
                      (r.get("verts") or 0) >= want_min, r.get("verts"))

        seg = B.call("create_primitive", {"kind": "sphere", "name": "MifT_seg", "segments": 8,
                                          "ringCount": 6})
        check("T4000 per-kind parameters change the result", seg.get("ok") is True
              and 0 < (seg.get("verts") or 0) < 60, "verts=%s" % seg.get("verts"))
        if seg.get("ok"):
            made.append(seg["name"])
        wrong = B.call("create_primitive", {"kind": "cube", "segments": 8})
        check("T4000 a parameter the kind cannot use is refused, not ignored",
              wrong.get("ok") is False and "does not apply" in (wrong.get("error") or ""),
              (wrong.get("error") or "")[:200])
        both = B.call("create_primitive", {"kind": "sphere", "size": 1, "radius": 2})
        check("T4000 size and radius together are refused - they set the same dimension",
              both.get("ok") is False, (both.get("error") or "")[:180])

        # ------------------------------------------------------------------ T4001 the name
        print("\n=== T4001: Blender renames on collision and never says so ===")
        first = B.call("create_primitive", {"kind": "cube", "name": "MifT_Clash"})
        second = B.call("create_primitive", {"kind": "cube", "name": "MifT_Clash"})
        check("T4001 (setup) both creations succeed", first.get("ok") and second.get("ok"),
              json.dumps(second)[:200])
        for r in (first, second):
            if r.get("ok"):
                made.append(r["name"])
        # THE assertion: the second must NOT claim the name it was given.
        check("T4001 the second object reports the name Blender actually gave it, not the request",
              second.get("name") != "MifT_Clash" and second.get("name", "").startswith("MifT_Clash"),
              "requested MifT_Clash, reported %r" % second.get("name"))
        check("T4001 and says why, rather than leaving it to be discovered",
              "already taken" in (second.get("nameNote") or ""), second.get("nameNote"))
        live = names()
        check("T4001 both objects really exist under the reported names",
              first.get("name") in live and second.get("name") in live,
              sorted(n for n in live if n.startswith("MifT_Clash")))

        # ------------------------------------------------------------------ T4003 place, not bake
        print("\n=== T4003: placing an object WITHOUT baking it into the mesh ===")
        t = B.call("create_primitive", {"kind": "cube", "name": "MifT_Move"})
        if t.get("ok"):
            made.append(t["name"])
        obj = t["name"]
        nothing = B.call("transform_object", {"object": obj})
        check("T4003 a transform with nothing to set is refused rather than a silent no-op",
              nothing.get("ok") is False, (nothing.get("error") or "")[:180])

        mv = B.call("transform_object", {"object": obj, "location": [3, 4, 5]})
        check("T4003 an object can be moved", mv.get("ok") is True, json.dumps(mv)[:250])
        check("T4003 and it reports before AND after, so 'it moved' becomes 'it is where I asked'",
              (mv.get("after") or {}).get("location") == [3, 4, 5]
              and (mv.get("before") or {}).get("location") == [0, 0, 0],
              json.dumps({"before": mv.get("before"), "after": mv.get("after")})[:250])
        # THE assertion that distinguishes this from apply_transform: the transform is on the
        # OBJECT, so the identity flag must now be FALSE.
        # object_info the OP nests its payload under "object" (ops_scene.py:180); object_info the
        # HELPER returns it flat. Reading the wrong level cost a failing check once.
        info = (B.call("object_info", {"object": obj}).get("object") or {})
        check("T4003 the transform is on the OBJECT - isIdentityTransform is now false",
              info.get("isIdentityTransform") is False, info.get("isIdentityTransform"))

        rel = B.call("transform_object", {"object": obj, "location": [1, 1, 1], "relative": True})
        check("T4003 relative:true ADDS to the current transform rather than replacing it",
              (rel.get("after") or {}).get("location") == [4, 5, 6],
              (rel.get("after") or {}).get("location"))

        # ------------------------------------------------------------------ T4004 join
        print("\n=== T4004: join merges material slots and remaps every face ===")
        a = B.call("create_primitive", {"kind": "cube", "name": "MifT_JoinA"})
        b = B.call("create_primitive", {"kind": "cube", "name": "MifT_JoinB",
                                        "location": [5, 0, 0]})
        for r in (a, b):
            if r.get("ok"):
                made.append(r["name"])
        # allowResize is REQUIRED to grow from zero slots - set_material_slots refuses a count
        # change without it, because re-indexing leaves faces pointing past the end. Omitting it
        # here made a real endpoint refusal look like a join defect.
        B.call("set_material_slots", {"object": a["name"], "slots": ["MifT_MatA"],
                                      "allowResize": True})
        B.call("set_material_slots", {"object": b["name"], "slots": ["MifT_MatB"],
                                      "allowResize": True})
        verts_before = a.get("verts")

        self_join = B.call("join_objects", {"target": a["name"], "objects": [a["name"]]})
        check("T4004 joining an object into itself is refused", self_join.get("ok") is False,
              (self_join.get("error") or "")[:180])

        j = B.call("join_objects", {"target": a["name"], "objects": [b["name"]]})
        check("T4004 a join succeeds", j.get("ok") is True, json.dumps(j)[:250])
        check("T4004 the source is really consumed - measured, not assumed",
              j.get("consumed") == [b["name"]] and b["name"] not in names(),
              json.dumps(j.get("consumed")))
        check("T4004 and the vertex count really grew", (j.get("verts") or 0) > (verts_before or 0),
              "%s -> %s" % (verts_before, j.get("verts")))
        # THE assertion. Slot order decides which Unreal material lands on which face, and join
        # rewrites it - so the report has to be real, not decorative.
        check("T4004 the slot list before and after are both reported, and the join changed it",
              isinstance(j.get("slots"), list) and isinstance(j.get("slotsBefore"), list)
              and len(j["slots"]) > len(j["slotsBefore"]),
              json.dumps({"before": j.get("slotsBefore"), "after": j.get("slots")}))
        if b["name"] in made:
            made.remove(b["name"])

        # ------------------------------------------------------------------ T4005 separate
        print("\n=== T4005: separate, and an honest zero ===")
        sep = B.call("separate_mesh", {"object": a["name"], "mode": "loose"})
        check("T4005 the joined mesh separates back into loose parts", sep.get("ok") is True
              and (sep.get("createdCount") or 0) >= 1, json.dumps(sep)[:250])
        made.extend(sep.get("created") or [])

        solo = B.call("create_primitive", {"kind": "cube", "name": "MifT_Solo"})
        if solo.get("ok"):
            made.append(solo["name"])
        none_made = B.call("separate_mesh", {"object": solo["name"], "mode": "loose"})
        check("T4005 separating a single-part mesh succeeds with createdCount 0",
              none_made.get("ok") is True and none_made.get("createdCount") == 0,
              json.dumps(none_made)[:250])
        check("T4005 and says the zero is a measured result, not a failure",
              "measured result" in (none_made.get("note") or ""), none_made.get("note"))
        badmode = B.call("separate_mesh", {"object": solo["name"], "mode": "sideways"})
        check("T4005 an unknown mode is refused", badmode.get("ok") is False,
              (badmode.get("error") or "")[:160])
    finally:
        for n in dict.fromkeys(made):
            B.call("delete_object", {"object": n})
        left = [n for n in names() if n.startswith("MifT_")]
        check("T4006 (cleanup) every object this suite made is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
