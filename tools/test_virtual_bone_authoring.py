"""Virtual bone authoring: add / remove / rename, on a USkeleton.

T3301 IS THE ONE THAT MATTERS. AddNewVirtualBone DOES NOT CHECK THAT THE BONES EXIST. Skeleton.cpp
:1795-1806 rejects exactly one thing - a duplicate source/target PAIR - and then adds the entry.
Nothing asks whether either bone is in the reference skeleton. What a typo produces is therefore not
an error:

    AddNewVirtualBone returns TRUE
    the entry sits in VirtualBones forever
    list_virtual_bones reports it, because it really is there
    RebuildRefSkeleton silently skips it (ReferenceSkeleton.cpp:487-488 gates on both bones
        resolving), so it exists in NO reference skeleton and drives NO animation

A bone that appears in every listing and does nothing is worse than a refusal, because there is
nothing to notice. Both names are checked here first.

T3302 IS THE ONE THAT PROTECTS OTHER WORK. RemoveVirtualBones REPARENTS: every virtual bone whose
source was the removed one is rewired to the removed bone's own source (Skeleton.cpp:1836-1841). So
deleting one bone silently edits others. The endpoint predicts that in wouldReparent[] before asking
for confirmation - and this suite asserts the PREDICTION MATCHES THE OUTCOME, not merely that a
warning was emitted. A warning that is wrong is worse than none.

T3303 covers a void silent no-op. RenameVirtualBone (Skeleton.cpp:1868-1885) sets bModified only when
something matched and returns nothing either way, so a typo would look like success. It also checks
neither collision with a REAL bone name nor anything else; a virtual bone sharing a real bone's name
makes every by-name lookup ambiguous.

NAMING IS VERSION-SPLIT, which is why the `name` parameter is not simply passed through. The engine
names the bone itself as "VB <source>_<target>" and the out-param overload REPORTS that name rather
than accepting one. The overload that accepts a name, AddNewNamedVirtualBone, exists only on 5.6+ and
is ABSENT from 5.3 - so 5.3 adds then renames. Either way the response echoes what the skeleton
actually holds rather than what was asked for, which T3300 asserts.

COOKED IS REFUSED. The API is not editor-gated and would happily run, but a virtual bone is baked
into animation data at cook time and a cooked project's sequences cannot be rebuilt - the bone would
exist and evaluate to nothing everywhere. T3300 asserts that on the project's real shared skeleton,
which is also why this suite works on a DUPLICATE: every character here shares one rig.

CLEANS UP: the duplicated skeleton is deleted at the end. Nothing is saved.
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


def bones(path):
    return M.call("list_virtual_bones", {"path": path}).get("virtualBones") or []


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    DST = "/Game/_MifVB/SK_Test%d" % st
    made = False

    try:
        # ------------------------------------------------------------------ T3300 cooked + setup
        print("=== T3300: cooked skeletons are refused, and named ===")
        src = None
        for a in (M.call("find_assets", {"class": "Skeleton", "limit": 25}).get("assets") or []):
            if not a["path"].startswith("/Game/_Mif"):
                src = a["path"]
                break
        check("T3300 (setup) the project has a real Skeleton", bool(src), src)
        if not src:
            print("SKIPPED - no Skeleton in this project.")
            return 0

        cooked = M.raw_post("add_virtual_bone", {"skeleton": src, "source": "pelvis",
                                                 "target": "head"})
        if cooked.get("ok") is False and "COOKED" in (cooked.get("error") or ""):
            check("T3300 a cooked skeleton is refused by name", True)
            check("T3300 and the refusal explains why it would be useless, not just disallowed",
                  "evaluate to nothing" in (cooked.get("error") or ""),
                  (cooked.get("error") or "")[:200])
        else:
            print("  NOTE  this project's Skeleton is not cooked, so the cooked refusal is not")
            print("        exercised here. On an uncooked project that branch is unreachable.")

        d = M.raw_post("duplicate_asset", {"path": src, "newPath": DST})
        check("T3300 (setup) it duplicates into scratch", d.get("ok") is True, json.dumps(d)[:200])
        if not d.get("ok"):
            return 1
        made = True
        # Pick two real bones from the reference skeleton rather than assuming names.
        bl = M.call("list_bones", {"path": DST}).get("bones") or []
        names = [b.get("name") if isinstance(b, dict) else b for b in bl]
        check("T3300 (setup) the copy has a reference skeleton to work against", len(names) > 3,
              len(names))
        if len(names) < 3:
            return 1
        B1, B2, B3 = names[1], names[2], names[3]

        # ------------------------------------------------------------------ T3301 the phantom
        print("\n=== T3301: the engine will happily make a bone that does nothing ===")
        for which, payload in (("source", {"source": "NoSuchBoneAtAll", "target": B1}),
                               ("target", {"source": B1, "target": "NoSuchBoneAtAll"})):
            p = dict(payload)
            p["skeleton"] = DST
            r = M.raw_post("add_virtual_bone", p)
            check("T3301 a non-existent %s bone is refused" % which, r.get("ok") is False,
                  json.dumps(r)[:250])
            check("T3301 and the refusal names the failure mode - a PHANTOM bone",
                  "PHANTOM" in (r.get("error") or ""), (r.get("error") or "")[:200])
        check("T3301 neither refusal created anything", len(bones(DST)) == 0, len(bones(DST)))

        same = M.raw_post("add_virtual_bone", {"skeleton": DST, "source": B1, "target": B1})
        check("T3301 source == target is refused - it would be the identity transform",
              same.get("ok") is False, (same.get("error") or "")[:180])

        # ------------------------------------------------------------------ T3300b creation
        print("\n=== T3300b: creation, with the name the ENGINE chose ===")
        a = M.raw_post("add_virtual_bone", {"skeleton": DST, "source": B1, "target": B2})
        check("T3300b a virtual bone can be created", a.get("ok") is True, json.dumps(a)[:250])
        vb = a.get("virtualBone") or {}
        check("T3300b the row matches list_virtual_bones' shape - name/source/target",
              set(vb) >= {"name", "source", "target"} and vb.get("source") == B1
              and vb.get("target") == B2, json.dumps(vb))
        # THE assertion behind the version split: the engine names it, so the response must echo
        # what the skeleton holds rather than what was requested.
        listed = bones(DST)
        check("T3300b and the name it reports is the one the skeleton really holds",
              len(listed) == 1 and listed[0]["name"] == vb.get("name"),
              json.dumps(listed))
        check("T3300b which is the engine's own \"VB <source>_<target>\" form, not something asked for",
              vb.get("name", "").startswith("VB "), vb.get("name"))

        dup = M.raw_post("add_virtual_bone", {"skeleton": DST, "source": B1, "target": B2})
        check("T3300b a duplicate PAIR reports created:false rather than failing opaquely",
              dup.get("ok") is True and dup.get("created") is False and len(bones(DST)) == 1,
              json.dumps(dup)[:250])

        # ------------------------------------------------------------------ T3302 reparenting
        print("\n=== T3302: removal silently edits OTHER bones - predict it, then prove it ===")
        parent = vb["name"]
        chain = M.raw_post("add_virtual_bone", {"skeleton": DST, "source": parent, "target": B3})
        check("T3302 (setup) a second virtual bone chained off the first",
              chain.get("ok") is True, json.dumps(chain)[:250])
        child = (chain.get("virtualBone") or {}).get("name")

        warn = M.raw_post("remove_virtual_bone", {"skeleton": DST, "name": parent})
        check("T3302 removing without confirm is refused", warn.get("ok") is False,
              json.dumps(warn)[:250])
        pred = warn.get("wouldReparent") or []
        check("T3302 and it PREDICTS which other bone will be rewired, and to what",
              len(pred) == 1 and pred[0].get("name") == child
              and pred[0].get("sourceBecomes") == B1, json.dumps(pred))
        check("T3302 the refused call changed nothing", len(bones(DST)) == 2, len(bones(DST)))

        rm = M.raw_post("remove_virtual_bone", {"skeleton": DST, "name": parent, "confirm": True})
        check("T3302 with confirm it is removed", rm.get("ok") is True
              and rm.get("removedCount") == 1, json.dumps(rm)[:250])
        # THE assertion. A warning that is WRONG is worse than no warning, so the prediction is
        # compared against what actually happened rather than merely being present.
        after = bones(DST)
        survivor = next((b for b in after if b["name"] == child), None)
        check("T3302 and the prediction was CORRECT - the child really was reparented to %s" % B1,
              survivor is not None and survivor["source"] == B1
              and pred[0]["sourceBecomes"] == survivor["source"],
              "predicted %s, got %s" % (pred[0].get("sourceBecomes"),
                                        survivor and survivor.get("source")))

        gone = M.raw_post("remove_virtual_bone", {"skeleton": DST, "name": "NoSuchVB",
                                                  "confirm": True})
        check("T3302 an unknown name is refused and the real ones listed",
              gone.get("ok") is False and child in (gone.get("error") or ""),
              (gone.get("error") or "")[:200])

        # ------------------------------------------------------------------ T3303 rename
        print("\n=== T3303: rename is a void silent no-op, so it is checked first ===")
        nosuch = M.raw_post("rename_virtual_bone", {"skeleton": DST, "name": "NoSuchVB",
                                                    "newName": "VB_X"})
        check("T3303 renaming something that does not exist is refused",
              nosuch.get("ok") is False, json.dumps(nosuch)[:250])
        check("T3303 and says why it is checked here - the engine returns void and does nothing",
              "void" in (nosuch.get("error") or ""), (nosuch.get("error") or "")[:200])

        collide = M.raw_post("rename_virtual_bone", {"skeleton": DST, "name": child,
                                                     "newName": B1})
        check("T3303 renaming onto a REAL bone's name is refused", collide.get("ok") is False,
              json.dumps(collide)[:250])
        check("T3303 and explains the consequence - ambiguous by-name lookups",
              "ambiguous" in (collide.get("error") or ""), (collide.get("error") or "")[:200])

        ok = M.raw_post("rename_virtual_bone", {"skeleton": DST, "name": child,
                                                "newName": "VB_MifRenamed"})
        check("T3303 a valid rename succeeds", ok.get("ok") is True and ok.get("renamed") is True,
              json.dumps(ok)[:250])
        check("T3303 and the new name is what the skeleton reports back",
              any(b["name"] == "VB_MifRenamed" for b in bones(DST)), json.dumps(bones(DST)))
        selfsame = M.raw_post("rename_virtual_bone", {"skeleton": DST, "name": "VB_MifRenamed",
                                                      "newName": "VB_MifRenamed"})
        check("T3303 renaming to the same name is refused rather than reported as work done",
              selfsame.get("ok") is False, (selfsame.get("error") or "")[:180])
    finally:
        if made:
            r = SC.confirm_call("delete_asset", {"path": DST})
            if not r.get("ok"):
                print("        cleanup: %s" % (r.get("error") or "")[:160])
        left = [a["path"] for a in (M.call("find_assets", {"pathPrefix": "/Game/_MifVB"})
                                    .get("assets") or []) if a["path"].startswith(DST)]
        check("T3304 (cleanup) the duplicated skeleton is gone", not left, left)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  FAILED: %s\n          %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
